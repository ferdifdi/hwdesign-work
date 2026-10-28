"""scenarios.py -- the scenarios, their expected outputs, the hand checks and the checker.

    python scenarios.py gen            write data/<scenario>/in.txt and expected*.txt/json
    python scenarios.py model          run model.py on every scenario -> data/<s>/model_*
    python scenarios.py check <stage>  compare data/<s>/<stage>_* against expected
    python scenarios.py hand           the hand-computed checks of the clamp

Each scenario is one activation, written as intents (CONFIG these bounds, DATA these
samples, ...).  The expected output is computed from the intents with a scalar reference
written separately from model.py (Python's own comparisons, a different clipped test), so the
model and the kernel cannot pass by sharing a mistake.

Word files are text, one word per line: "<8 hex digits> <tlast 0|1>".
"""
from __future__ import annotations

import json
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from model import Cmd, Err, clamp, stream_model

DATA = Path(__file__).resolve().parent / "data"


def fbits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def bitsf(u: int) -> float:
    return struct.unpack("<f", struct.pack("<I", u))[0]


@dataclass
class Config:
    tx: int
    lo: float
    hi: float


@dataclass
class Data:
    tx: int
    x: list[float]
    sent: int | None = None     # samples actually sent (early TLAST if < len(x))
    tlast: bool = True          # TLAST on the last word sent


@dataclass
class Count:
    tx: int


@dataclass
class Raw:                      # a header with an arbitrary cmd_type
    tx: int
    cmd: int


def hdr(cmd: int, tx: int, nsamp: int = 0, lo: float = 0.0, hi: float = 0.0) -> list[tuple[int, bool]]:
    w = [(tx << 16) | cmd, nsamp, fbits(lo) if cmd == Cmd.CONFIG else 0,
         fbits(hi) if cmd == Cmd.CONFIG else 0]
    return [(v, i == 3) for i, v in enumerate(w)]


def stimulus(intents, end: bool = True) -> list[tuple[int, bool]]:
    out: list[tuple[int, bool]] = []
    for it in intents:
        if isinstance(it, Config):
            out += hdr(Cmd.CONFIG, it.tx, lo=it.lo, hi=it.hi)
        elif isinstance(it, Data):
            out += hdr(Cmd.DATA, it.tx, nsamp=len(it.x))
            n = len(it.x) if it.sent is None else it.sent
            out += [(fbits(v), i == n - 1 and it.tlast) for i, v in enumerate(it.x[:n])]
        elif isinstance(it, Count):
            out += hdr(Cmd.COUNT, it.tx)
        elif isinstance(it, Raw):
            out += hdr(it.cmd, it.tx)
    if end:
        out += hdr(Cmd.END, 0)
    return out


def ref_clamp(x: float, lo: float, hi: float) -> float:
    """The scalar reference: Python comparisons on float32 values (NaN compares false)."""
    t = lo if lo > x else x
    return hi if t > hi else t


def expected(intents) -> tuple[list[tuple[int, bool]], dict]:
    out: list[tuple[int, bool]] = []
    cfg = None
    count = 0

    def halt(err, tx):
        return out, {"halted": 1, "error": int(err), "tx_id": tx}

    for it in intents:
        if isinstance(it, Config):
            if math.isnan(it.lo) or math.isnan(it.hi) or it.lo > it.hi:
                return halt(Err.BAD_BOUNDS, it.tx)
            cfg, count = (bitsf(fbits(it.lo)), bitsf(fbits(it.hi))), 0
            out.append(((it.tx << 16) | Cmd.CONFIG, True))
        elif isinstance(it, Data):
            if cfg is None:
                return halt(Err.NO_CONFIG, it.tx)
            out.append(((it.tx << 16) | Cmd.DATA, True))
            lo, hi = cfg
            n = len(it.x) if it.sent is None else it.sent
            for i, v in enumerate(it.x[:n]):
                v = bitsf(fbits(v))
                y = ref_clamp(v, lo, hi)
                count += int(v < lo or v > hi)      # clipped: outside [lo, hi]
                out.append((fbits(y), i == n - 1))
            if n < len(it.x):
                return halt(Err.TLAST_EARLY, it.tx)
            if it.x and not it.tlast:
                return halt(Err.NO_TLAST, it.tx)
        elif isinstance(it, Count):
            if cfg is None:
                return halt(Err.NO_CONFIG, it.tx)
            out.append(((it.tx << 16) | Cmd.COUNT, True))
            out.append((count, True))
        elif isinstance(it, Raw):
            return halt(Err.UNKNOWN_CMD, it.tx)
    return out, {"halted": 0, "error": 0, "tx_id": 0}


def scenarios() -> dict[str, tuple[list, bool]]:
    """name -> (intents, END appended).  Every error scenario queues more commands behind
    the failing one, and is followed by a <name>_fresh activation that must pass."""
    rng = np.random.default_rng(5)

    def x(n):
        return [float(v) for v in rng.uniform(-4, 4, n).astype(np.float32)]

    nan, inf = float("nan"), float("inf")
    fresh = [Config(90, -1.0, 1.0), Data(91, x(8)), Count(92)]
    sc = {
        "hand": ([Config(1, -1.0, 2.0), Data(2, [-3, -1, 0.5, 2, 5, nan, -0.0]), Count(3),
                  Data(4, [10, -10]), Count(5), Config(6, 0.0, 0.0), Count(7)], True),
        "multi_len": ([Config(10, -1.5, 1.5), Data(11, x(100)), Data(12, x(7)), Data(13, x(1)),
                       Data(14, []), Count(15)], True),
        "reconfig": ([Config(20, -1.0, 1.0), Data(21, x(16)), Count(22), Config(23, 0.0, 3.0),
                      Count(24), Data(25, x(16)), Count(26)], True),
        "count_accum": ([Config(30, -0.5, 0.5), Data(31, x(10)), Count(32), Data(33, x(10)),
                         Data(34, x(10)), Count(35), Count(36)], True),
        "lo_eq_hi": ([Config(40, 0.25, 0.25), Data(41, x(9) + [0.25, inf, -inf]), Count(42)], True),
        "zero_only": ([Config(45, -1.0, 1.0), Data(46, []), Count(47)], True),
        "err_no_config": ([Data(50, x(4)), Config(51, -1, 1), Data(52, x(4))], True),
        "err_count_no_config": ([Count(55), Config(56, -1, 1)], True),
        "err_bad_bounds": ([Config(60, -1, 1), Data(61, x(3)), Config(62, 2.0, 1.0),
                            Data(63, x(3))], True),
        "err_nan_bounds": ([Config(65, nan, 1.0), Data(66, x(3))], True),
        "err_unknown": ([Config(70, -1, 1), Raw(71, 9), Data(72, x(3))], True),
        "err_early_tlast": ([Config(75, -1, 1), Data(76, x(5)), Data(77, x(10), sent=6),
                             Data(78, x(4))], True),
        "err_no_tlast": ([Config(80, -1, 1), Data(81, x(6), tlast=False), Data(82, x(4))], True),
    }
    out = {}
    for name, v in sc.items():
        out[name] = v
        if name.startswith("err_"):
            out[name + "_fresh"] = (fresh, True)
    return out


def write_words(path: Path, words) -> None:
    path.write_text("".join(f"{w & 0xFFFFFFFF:08x} {int(t)}\n" for w, t in words))


def read_words(path: Path) -> list[tuple[int, bool]]:
    return [(int(a, 16), b == "1") for a, b in (l.split() for l in path.read_text().splitlines() if l)]


def gen() -> None:
    names = []
    for name, (intents, end) in scenarios().items():
        d = DATA / name
        d.mkdir(parents=True, exist_ok=True)
        write_words(d / "in.txt", stimulus(intents, end))
        exp, st = expected(intents)
        write_words(d / "expected.txt", exp)
        (d / "expected_status.json").write_text(json.dumps(st) + "\n")
        names.append(name)
    (DATA / "scenarios.txt").write_text("\n".join(names) + "\n")
    print(f"wrote {len(names)} scenarios")


def run_model() -> None:
    for name in (DATA / "scenarios.txt").read_text().split():
        d = DATA / name
        r = stream_model(read_words(d / "in.txt"))
        write_words(d / "model_out.txt", r.out)
        (d / "model_status.json").write_text(json.dumps(r.status()) + "\n")


def check(stage: str) -> int:
    nfail = 0
    print(f"{'scenario':24s} {stage:6s} status")
    for name in (DATA / "scenarios.txt").read_text().split():
        d = DATA / name
        probs = []
        want, st_want = read_words(d / "expected.txt"), json.loads((d / "expected_status.json").read_text())
        try:
            got = read_words(d / f"{stage}_out.txt")
            st_got = json.loads((d / f"{stage}_status.json").read_text())
        except FileNotFoundError as e:
            probs, got, st_got = [f"missing {e.filename}"], [], {}
        if not probs:
            if got != want:
                k = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
                probs.append(f"{len(got)} words, expected {len(want)}; first difference at word {k}")
            if st_got != st_want:
                probs.append(f"status {st_got}, expected {st_want}")
            if st_want["halted"] and got and not got[-1][1]:
                probs.append("last output word written before the error has no TLAST")
        nfail += bool(probs)
        print(f"{name:24s} {'FAIL' if probs else 'PASS':6s} {json.dumps(st_got)} {'; '.join(probs)}")
    print(f"{stage}: {nfail} failing scenario(s)")
    return nfail


def hand() -> int:
    """The worked example from prompt.md, checked against model.clamp directly."""
    nan = float("nan")
    y, c = clamp([-3, -1, 0.5, 2, 5, nan, -0.0], -1.0, 2.0)
    want = np.array([-1, -1, 0.5, 2, 2, nan, -0.0], np.float32)
    ok1 = np.array_equal(y.view(np.uint32), want.view(np.uint32)) and c == 2
    y2, c2 = clamp([10, -10], -1.0, 2.0)
    ok2 = list(y2) == [2.0, -1.0] and c + c2 == 4
    _, c3 = clamp([], 0.0, 0.0)
    ok3 = c3 == 0
    for label, ok in (("x=[-3,-1,0.5,2,5,NaN,-0.0] -> [-1,-1,0.5,2,2,NaN,-0.0], count 2", ok1),
                      ("then [10,-10] -> [2,-1], count 4", ok2),
                      ("CONFIG(0,0), COUNT -> 0", ok3)):
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    return 0 if ok1 and ok2 and ok3 else 1


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "gen":
        gen()
    elif cmd == "model":
        run_model()
    elif cmd == "check":
        sys.exit(1 if check(sys.argv[2]) else 0)
    elif cmd == "hand":
        sys.exit(hand())
