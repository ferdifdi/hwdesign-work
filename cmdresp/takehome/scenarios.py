"""scenarios.py -- the FIR scenarios, their expected outputs, the reference checks and the checker.

    python scenarios.py gen            write data/<s>/in.txt, expected.txt, expected_status.json,
                                       and the scenario lists data/scenarios_{csim,cosim}.txt
    python scenarios.py model          run model.py on every scenario -> data/<s>/model_*
    python scenarios.py hand           the prompt's hand-computed examples, against model.Fir
    python scenarios.py reference      the model against np.convolve in float64, every scenario
    python scenarios.py check <stage>  compare data/<s>/<stage>_* with expected

Each scenario is one activation, written as intents.  Its expected output is computed from
the intents by a scalar float32 loop written here, separately from model.py: it keeps the
whole sample sequence since CONFIG and indexes it, where the model shifts a history register.

The *_queued error scenarios put more commands behind the failing one, which the kernel
must leave unread; they run in C simulation and co-simulation alike (prompt v2).  Word files are text, one word per
line: "<8 hex digits> <tlast 0|1>".
"""
from __future__ import annotations

import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from model import MAX_TAPS, Cmd, Err, Fir, stream_model

DATA = Path(__file__).resolve().parent / "data"
F = np.float32
np.seterr(invalid="ignore", over="ignore")   # inf and NaN taps are a scenario


def fbits(x) -> int:
    return struct.unpack("<I", struct.pack("<f", float(x)))[0]


@dataclass
class Config:
    tx: int
    taps: list[float]
    ntaps: int | None = None     # the header's ntaps (default len(taps))
    sent: int | None = None      # taps actually sent
    tlast: bool = True


@dataclass
class Process:
    tx: int
    x: list[float]
    sent: int | None = None
    tlast: bool = True


@dataclass
class Raw:
    tx: int
    cmd: int


def hdr(cmd: int, tx: int, count: int = 0) -> list[tuple[int, bool]]:
    return [((tx << 16) | cmd, False), (count, True)]


def burst(vals, sent, tlast) -> list[tuple[int, bool]]:
    n = len(vals) if sent is None else sent
    return [(fbits(v), i == n - 1 and tlast) for i, v in enumerate(vals[:n])]


def stimulus(intents, end: bool) -> list[tuple[int, bool]]:
    out: list[tuple[int, bool]] = []
    for it in intents:
        if isinstance(it, Config):
            nt = len(it.taps) if it.ntaps is None else it.ntaps
            out += hdr(Cmd.CONFIG, it.tx, nt)
            out += burst(it.taps, it.sent, it.tlast)
        elif isinstance(it, Process):
            out += hdr(Cmd.PROCESS, it.tx, len(it.x))
            out += burst(it.x, it.sent, it.tlast)
        elif isinstance(it, Raw):
            out += hdr(it.cmd, it.tx)
    if end:
        out += hdr(Cmd.END, 0)
    return out


def ref_fir(h: list, seq: list, n: int) -> np.float32:
    """y[n] by the spec: acc = h0*x[n]; acc = acc + h[k]*x[n-k]; x before CONFIG = +0.0."""
    def x(i):
        return seq[i] if i >= 0 else F(0.0)
    acc = F(h[0] * x(n))
    for k in range(1, len(h)):
        acc = F(acc + F(h[k] * x(n - k)))
    return np.frombuffer(struct.pack("<I", 0x7FC00000), F)[0] if acc != acc else acc


def expected(intents) -> tuple[list[tuple[int, bool]], dict]:
    out: list[tuple[int, bool]] = []
    h: list | None = None
    seq: list = []

    def halt(err, tx):
        return out, {"halted": 1, "error": int(err), "tx_id": tx}

    for it in intents:
        if isinstance(it, Raw):
            return halt(Err.UNKNOWN_CMD, it.tx)
        if isinstance(it, Config):
            nt = len(it.taps) if it.ntaps is None else it.ntaps
            if nt == 0 or nt > MAX_TAPS:
                return halt(Err.BAD_NTAPS, it.tx)
            sent = nt if it.sent is None else it.sent
            if sent < nt:
                return halt(Err.TAPS_TLAST_EARLY, it.tx)
            if not it.tlast:
                return halt(Err.TAPS_NO_TLAST, it.tx)
            h, seq = [F(v) for v in it.taps], []
            out.append(((it.tx << 16) | Cmd.CONFIG, True))
        else:
            if h is None:
                return halt(Err.NO_CONFIG, it.tx)
            out.append(((it.tx << 16) | Cmd.PROCESS, True))
            n_in = len(it.x) if it.sent is None else it.sent
            for i in range(n_in):
                seq.append(F(it.x[i]))
                out.append((fbits(ref_fir(h, seq, len(seq) - 1)), i == n_in - 1))
            if n_in < len(it.x):
                return halt(Err.SAMP_TLAST_EARLY, it.tx)
            if it.x and not it.tlast:
                return halt(Err.SAMP_NO_TLAST, it.tx)
    return out, {"halted": 0, "error": 0, "tx_id": 0}


def scenarios() -> dict[str, tuple[list, bool, bool]]:
    """name -> (intents, END appended, runs in cosim)."""
    rng = np.random.default_rng(7)

    def x(n):
        return [float(v) for v in rng.uniform(-2, 2, n).astype(F)]

    def t(n):
        return [float(v) for v in rng.uniform(-1, 1, n).astype(F)]

    s40 = x(40)
    t7 = t(7)
    t16 = t(16)
    sc: dict[str, tuple[list, bool, bool]] = {
        "hand_a": ([Config(1, [1, 2, 3]), Process(2, [1, 0, 0, 0, 1, 1]), Process(3, [2]),
                    Config(4, [0.5]), Process(5, [4])], True, True),
        "hand_order": ([Config(6, [1, 1, 1]), Process(7, [1, 1e8, -1e8])], True, True),
        "ntaps1": ([Config(10, [0.75]), Process(11, x(20))], True, True),
        "ntaps16": ([Config(12, t16), Process(13, x(50))], True, True),
        "short_cross": ([Config(14, t(16)), Process(15, x(5)), Process(16, x(3)),
                         Process(17, x(20))], True, True),
        "split_one": ([Config(20, t7), Process(21, s40)], True, True),
        "split_many": ([Config(22, t7), Process(23, s40[:13]), Process(24, s40[13:14]),
                        Process(25, []), Process(26, s40[14:])], True, True),
        "reconfig": ([Config(30, t(4)), Process(31, x(10)), Config(32, t(11)),
                      Process(33, x(10)), Process(34, x(5))], True, True),
        "zero_len": ([Config(35, t(3)), Process(36, []), Process(37, x(4)), Process(38, [])],
                     True, True),
        "signed_zero": ([Config(39, [-1.0, -2.0]), Process(40, [0.0, -0.0, 0.0])], True, True),
        "nonfinite_taps": ([Config(41, [1.0, float("inf")]), Process(42, [1.0, 2.0, -1.0])],
                           True, True),
        # A NaN sample with its sign bit set: every result it reaches is the canonical NaN.
        "nan_sample": ([Config(43, [0.5, -1.0, 2.0]), Process(44, [1.0, -float("nan"), 2.0, 3.0, 4.0])],
                       True, True),
    }
    for n in (0, 1, 256, 512):
        sc[f"timing_p{n}"] = ([Config(100 + n % 97, t16), Process(200 + n % 97, x(n))], True, True)

    fresh = [Config(90, t(4)), Process(91, x(8))]
    errors = {
        "err_unknown": [Config(50, [1.0]), Process(51, x(3)), Raw(52, 7)],
        "err_bad_ntaps0": [Config(53, [], ntaps=0)],
        "err_bad_ntaps17": [Config(54, t(17))],
        "err_no_config": [Process(55, x(4))],
        "err_no_config_zero": [Process(56, [])],
        "err_taps_early": [Config(57, t(5), sent=3)],
        "err_taps_notlast": [Config(58, t(5), tlast=False)],
        "err_samp_early": [Config(59, t(3)), Process(60, x(5)), Process(61, x(10), sent=6)],
        "err_samp_notlast": [Config(62, t(3)), Process(63, x(6), tlast=False)],
    }
    queued = [Config(70, [1.0]), Process(71, x(4))]
    for name, intents in errors.items():
        sc[name] = (intents, False, True)
        sc[name + "_queued"] = (intents + queued, True, True)
        sc[name + "_fresh"] = (fresh, True, True)
    return sc


def write_words(path: Path, words) -> None:
    path.write_text("".join(f"{w & 0xFFFFFFFF:08x} {int(t)}\n" for w, t in words))


def read_words(path: Path) -> list[tuple[int, bool]]:
    return [(int(a, 16), b == "1") for a, b in (l.split() for l in path.read_text().splitlines() if l)]


def names(stage: str) -> list[str]:
    lst = "cosim" if stage == "cosim" else "csim"
    return (DATA / f"scenarios_{lst}.txt").read_text().split()


def gen() -> None:
    all_names, cosim_names = [], []
    for name, (intents, end, cosim) in scenarios().items():
        d = DATA / name
        d.mkdir(parents=True, exist_ok=True)
        write_words(d / "in.txt", stimulus(intents, end))
        exp, st = expected(intents)
        write_words(d / "expected.txt", exp)
        (d / "expected_status.json").write_text(json.dumps(st) + "\n")
        all_names.append(name)
        if cosim:
            cosim_names.append(name)
    (DATA / "scenarios_csim.txt").write_text("\n".join(all_names) + "\n")
    (DATA / "scenarios_cosim.txt").write_text("\n".join(cosim_names) + "\n")
    print(f"wrote {len(all_names)} scenarios ({len(cosim_names)} in cosim)")


def run_model() -> None:
    for name in names("model"):
        d = DATA / name
        r = stream_model(read_words(d / "in.txt"))
        write_words(d / "model_out.txt", r.out)
        (d / "model_status.json").write_text(json.dumps(r.status()) + "\n")


def results_only(words, counts) -> list[int]:
    """The result words of CONFIG then PROCESS commands of *counts* samples, headers dropped."""
    ws = [w for w, _ in words]
    out, pos = [], 1                 # word 0: CONFIG's response header
    for n in counts:
        out += ws[pos + 1:pos + 1 + n]
        pos += 1 + n
    return out if pos == len(ws) else []


def check(stage: str) -> int:
    nfail = 0
    print(f"{'scenario':26s} {stage:6s} status")
    for name in names(stage):
        d = DATA / name
        probs = []
        want = read_words(d / "expected.txt")
        st_want = json.loads((d / "expected_status.json").read_text())
        try:
            got = read_words(d / f"{stage}_out.txt")
            st_got = json.loads((d / f"{stage}_status.json").read_text())
        except FileNotFoundError as e:
            probs, got, st_got = [f"missing {e.filename}"], [], {}
        if not probs:
            if got != want:
                k = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b),
                         min(len(got), len(want)))
                detail = f" (got {got[k][0]:08x}, expected {want[k][0]:08x})" if k < min(len(got), len(want)) else ""
                probs.append(f"{len(got)} words, expected {len(want)}; first difference at word {k}{detail}")
            if st_got != st_want:
                probs.append(f"status {st_got}, expected {st_want}")
            if st_want["halted"] and got and not got[-1][1]:
                probs.append("last output word before the error has no TLAST")
        nfail += bool(probs)
        print(f"{name:26s} {'FAIL' if probs else 'PASS':6s} {json.dumps(st_got)} {'; '.join(probs)}")
    # The split invariant: the same 40 samples, as one PROCESS or as four, give the same results.
    try:
        one = results_only(read_words(DATA / "split_one" / f"{stage}_out.txt"), [40])
        many = results_only(read_words(DATA / "split_many" / f"{stage}_out.txt"), [13, 1, 0, 26])
        ok = one == many and len(one) == 40
    except FileNotFoundError:
        ok = False
    nfail += not ok
    print(f"{'split_one == split_many':26s} {'PASS' if ok else 'FAIL':6s} (40 results, compared word for word)")
    print(f"{stage}: {nfail} failing check(s)")
    return nfail


def hand() -> int:
    ok_all = True

    def show(label, got, want):
        nonlocal ok_all
        ok = np.array_equal(np.asarray(got, F).view(np.uint32), np.asarray(want, F).view(np.uint32))
        ok_all &= ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}: got {list(map(float, got))}")

    f = Fir([1, 2, 3])
    show("taps [1,2,3], x=[1,0,0,0,1,1] -> [1,2,3,0,1,3]", f.run([1, 0, 0, 0, 1, 1]), [1, 2, 3, 0, 1, 3])
    show("then x=[2] -> [7] (history carried)", f.run([2]), [7])
    f = Fir([0.5])
    show("CONFIG [0.5], x=[4] -> [2] (history cleared)", f.run([4]), [2])
    f = Fir([1, 1, 1])
    show("taps [1,1,1], x=[1,1e8,-1e8] -> [1,1e8,1]", f.run([1, 1e8, -1e8]), [1, 1e8, 1])
    oldest_first = F(F(F(1) + F(1e8)) + F(-1e8))
    print(f"note  the same sum oldest-first gives {float(oldest_first)}: the order matters")
    return 0 if ok_all else 1


def reference() -> int:
    """The model against np.convolve in float64, over the samples since each CONFIG.

    Tolerance: |model - ref| <= 1e-5 * sum_k |h_k x_{n-k}| (+ 1e-30): relative to the size of
    the terms, so that cancellation (hand_order) does not fail a correct float32 result.
    Non-finite outputs must match in kind (NaN or the same infinity).
    """
    nfail = 0
    for name, (intents, _, _) in scenarios().items():
        if name == "nonfinite_taps":
            # np.convolve leaves out the products with the zeros before CONFIG, which the spec
            # includes: here inf * (+0.0) = NaN.  The bit-exact expected output covers it.
            print(f"{name:26s} SKIP  np.convolve omits the pre-CONFIG products (inf*0 = NaN)")
            continue
        h, seq, worst, bad = None, [], 0.0, 0
        exp_words = read_words(DATA / name / "model_out.txt")
        results = iter([w for w, _ in exp_words])
        for it in intents:
            if isinstance(it, Raw):
                break
            if isinstance(it, Config):
                nt = len(it.taps) if it.ntaps is None else it.ntaps
                if nt == 0 or nt > MAX_TAPS or (it.sent or nt) < nt or not it.tlast:
                    break
                h, seq = np.array(it.taps, np.float64), []
                next(results)
                continue
            if h is None:
                break
            next(results)
            n_in = len(it.x) if it.sent is None else it.sent
            for i in range(n_in):
                seq.append(float(F(it.x[i])))
                xs = np.array(seq, np.float64)
                n = len(xs) - 1
                ref = float(np.convolve(h, xs)[n])
                scale = float(np.convolve(np.abs(h), np.abs(xs))[n])
                got = float(np.uint32(next(results)).view(F))
                if np.isfinite(ref) or np.isfinite(got):
                    err = abs(got - ref) / (scale + 1e-30)
                    worst = max(worst, err)
                    bad += not err <= 1e-5
                else:
                    bad += not (np.isnan(ref) == np.isnan(got))
            if n_in < len(it.x) or (it.x and not it.tlast):
                break
        nfail += bad > 0
        print(f"{name:26s} {'PASS' if bad == 0 else 'FAIL'}  worst scaled error {worst:.2e}")
    print(f"reference: {nfail} failing scenario(s)")
    return nfail


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
    elif cmd == "reference":
        sys.exit(1 if reference() else 0)
