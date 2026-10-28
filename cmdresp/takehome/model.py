"""model.py -- the bit-exact Python model of the streaming FIR kernel.

The interface (results.md, Interface), on a 32-bit AXI4-Stream:

  command header, 2 words (its own burst; TLAST not checked):
    w0  [3:0] cmd_type (CONFIG=0, PROCESS=1, END=2)   [31:16] tx_id
    w1  [15:0] count: ntaps for CONFIG, nsamp for PROCESS, 0 for END   [31:16] ignored
  CONFIG: then ntaps float32 tap words h[0]..h[ntaps-1], one burst.
  PROCESS: then nsamp float32 sample words, one burst (none if nsamp = 0).
  response header, 1 word (its own burst):  [3:0] cmd_type echoed   [31:16] tx_id
  PROCESS results: nsamp float32 words, one burst.

stream_model() is a pure function of the input words that does word for word what
kernel.cpp does, returning the output words (each with its TLAST) and the final status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np

HDR_WORDS = 2
MAX_TAPS = 16


class Cmd(IntEnum):
    CONFIG = 0
    PROCESS = 1
    END = 2


class Err(IntEnum):
    NO_ERROR = 0
    UNKNOWN_CMD = 1
    BAD_NTAPS = 2
    NO_CONFIG = 3
    TAPS_TLAST_EARLY = 4
    TAPS_NO_TLAST = 5
    SAMP_TLAST_EARLY = 6
    SAMP_NO_TLAST = 7


def f2u(x) -> np.ndarray:
    return np.asarray(x, dtype=np.float32).view(np.uint32)


def u2f(u) -> np.ndarray:
    return np.asarray(u, dtype=np.uint32).view(np.float32)


NAN_BITS = 0x7FC00000      # every NaN result is written as this quiet NaN


def canonical(y: np.float32) -> np.float32:
    """IEEE leaves a NaN's sign and payload open, so the spec fixes them."""
    return np.uint32(NAN_BITS).view(np.float32) if np.isnan(y) else y


class Fir:
    """The filter state between commands: taps and the history since the last CONFIG."""

    def __init__(self, taps) -> None:
        self.h = np.asarray(taps, dtype=np.float32)
        self.hist = np.zeros(len(self.h), dtype=np.float32)    # hist[k] = x[n-k]

    def step(self, x: np.float32) -> np.float32:
        """One sample: shift it in, then acc = h0*x[n]; acc += h[k]*x[n-k], k = 1..ntaps-1."""
        self.hist[1:] = self.hist[:-1]
        self.hist[0] = x
        with np.errstate(invalid="ignore", over="ignore"):   # IEEE inf/NaN propagate
            p = (self.h * self.hist).astype(np.float32)        # each product rounded to float32
        acc = p[0]
        with np.errstate(invalid="ignore", over="ignore"):
            for k in range(1, len(p)):
                acc = np.float32(acc + p[k])                   # each sum rounded, left to right
        return canonical(acc)

    def run(self, xs) -> np.ndarray:
        return np.array([self.step(np.float32(x)) for x in xs], dtype=np.float32)


@dataclass
class Result:
    out: list[tuple[int, bool]] = field(default_factory=list)   # (word, tlast)
    halted: int = 0
    error: int = 0
    tx_id: int = 0

    def status(self) -> dict:
        return {"halted": self.halted, "error": self.error, "tx_id": self.tx_id}


def stream_model(words: list[tuple[int, bool]]) -> Result:
    """One activation.  *words* is the input stream as (word, tlast) pairs."""
    res = Result()
    pos = 0
    fir: Fir | None = None

    def halt(err: Err, tx: int) -> Result:
        res.halted, res.error, res.tx_id = 1, int(err), tx
        return res

    def take() -> tuple[int, bool]:
        nonlocal pos
        if pos >= len(words):
            raise ValueError("the stimulus ends inside a burst: the kernel would block")
        pos += 1
        return words[pos - 1]

    while True:
        if pos + HDR_WORDS > len(words):
            raise ValueError("the stimulus ends before END: the kernel would block")
        h0, h1 = (w for w, _ in words[pos:pos + HDR_WORDS])
        pos += HDR_WORDS
        cmd, tx, count = h0 & 0xF, (h0 >> 16) & 0xFFFF, h1 & 0xFFFF
        resp = (tx << 16) | cmd
        if cmd == Cmd.END:
            return res
        if cmd == Cmd.CONFIG:
            if count == 0 or count > MAX_TAPS:
                return halt(Err.BAD_NTAPS, tx)
            taps = []
            for k in range(count):
                w, last = take()
                taps.append(w)
                final = k == count - 1
                if last and not final:
                    return halt(Err.TAPS_TLAST_EARLY, tx)
                if final and not last:
                    return halt(Err.TAPS_NO_TLAST, tx)
            fir = Fir(u2f(taps))
            res.out.append((resp, True))
        elif cmd == Cmd.PROCESS:
            if fir is None:
                return halt(Err.NO_CONFIG, tx)
            res.out.append((resp, True))
            for i in range(count):
                w, last = take()
                y = fir.step(u2f([w])[0])
                final = i == count - 1
                res.out.append((int(f2u(y)), final or last))
                if last and not final:
                    return halt(Err.SAMP_TLAST_EARLY, tx)
                if final and not last:
                    return halt(Err.SAMP_NO_TLAST, tx)
        else:
            return halt(Err.UNKNOWN_CMD, tx)
