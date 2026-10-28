"""model.py -- the bit-exact Python model of the clamp-and-count kernel.

The interface (see results.md, Interface), on a 32-bit AXI4-Stream:

  command header, 4 words (its own burst; TLAST not checked):
    w0  [3:0] cmd_type (CONFIG=0, DATA=1, COUNT=2, END=3)   [31:16] tx_id
    w1  nsamp (uint32; DATA only, else 0)
    w2  lo (float32 bits; CONFIG only, else 0)
    w3  hi (float32 bits; CONFIG only, else 0)
  response header, 1 word (its own burst):
    [3:0] cmd_type echoed   [31:16] tx_id
  DATA results: nsamp float32 words, one burst.  COUNT: one uint32 word, one burst.

The model is a pure function of the input words: it does word for word what kernel.cpp
does, and returns the output words (each with its TLAST) and the final status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np

HDR_WORDS = 4


class Cmd(IntEnum):
    CONFIG = 0
    DATA = 1
    COUNT = 2
    END = 3


class Err(IntEnum):
    NO_ERROR = 0
    NO_CONFIG = 1      # DATA or COUNT before any CONFIG
    BAD_BOUNDS = 2     # CONFIG with lo > hi, or lo or hi NaN
    UNKNOWN_CMD = 3    # cmd_type not one of the four
    TLAST_EARLY = 4    # sample burst's TLAST before sample nsamp
    NO_TLAST = 5       # last sample word without TLAST


def f2u(x) -> np.ndarray:
    return np.asarray(x, dtype=np.float32).view(np.uint32)


def u2f(u) -> np.ndarray:
    return np.asarray(u, dtype=np.uint32).view(np.float32)


def clamp(x, lo: np.float32, hi: np.float32) -> tuple[np.ndarray, int]:
    """t = (x < lo) ? lo : x;  y = (t > hi) ? hi : t;  and how many samples were clipped."""
    x = np.asarray(x, dtype=np.float32)
    lo, hi = np.float32(lo), np.float32(hi)
    below = x < lo
    t = np.where(below, lo, x).astype(np.float32)
    above = t > hi
    y = np.where(above, hi, t).astype(np.float32)
    return y, int(np.count_nonzero(below | above))


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
    configured = False
    lo = hi = np.float32(0)
    count = 0

    def halt(err: Err, tx: int) -> Result:
        res.halted, res.error, res.tx_id = 1, int(err), tx
        return res

    while True:
        if pos + HDR_WORDS > len(words):
            raise ValueError("stimulus ends before END: the kernel would block")
        h = [w for w, _ in words[pos:pos + HDR_WORDS]]
        pos += HDR_WORDS
        cmd, tx = h[0] & 0xF, (h[0] >> 16) & 0xFFFF
        resp = (tx << 16) | cmd
        if cmd == Cmd.END:
            return res
        if cmd == Cmd.CONFIG:
            nlo, nhi = u2f(h[2]).item(), u2f(h[3]).item()
            if np.isnan(nlo) or np.isnan(nhi) or nlo > nhi:
                return halt(Err.BAD_BOUNDS, tx)
            lo, hi, count, configured = np.float32(nlo), np.float32(nhi), 0, True
            res.out.append((resp, True))
        elif cmd == Cmd.DATA:
            if not configured:
                return halt(Err.NO_CONFIG, tx)
            res.out.append((resp, True))
            nsamp = h[1]
            for i in range(nsamp):
                if pos >= len(words):
                    raise ValueError("stimulus ends inside a sample burst: the kernel would block")
                w, last = words[pos]
                pos += 1
                y, c = clamp(u2f([w]), lo, hi)
                count = (count + c) & 0xFFFFFFFF
                final = i == nsamp - 1
                res.out.append((int(f2u(y)[0]), final or last))
                if last and not final:
                    return halt(Err.TLAST_EARLY, tx)
                if final and not last:
                    return halt(Err.NO_TLAST, tx)
        elif cmd == Cmd.COUNT:
            if not configured:
                return halt(Err.NO_CONFIG, tx)
            res.out.append((resp, True))
            res.out.append((count, True))
        else:
            return halt(Err.UNKNOWN_CMD, tx)
