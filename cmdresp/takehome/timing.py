"""timing.py -- the throughput and latency targets, from the co-simulation transaction report.

    python timing.py > logs/timing.txt

Co-simulation runs the scenarios in data/scenarios_cosim.txt in order, one kernel call
(transaction) each, and reports/fir.performance.result.transaction.xml lists each call's
latency (ap_start to ap_done) in cycles.  The four timing activations are identical except
for the PROCESS length (CONFIG with 16 taps, PROCESS nsamp, END), so the differences between
them isolate the samples:

  span(256)  = L(p256) - L(p0)          cycles the 256 samples add: first sample word in to
                                        last result out, plus at most the 1-2 cycles of overlap
                                        with the PROCESS header and END
  rate       = (L(p512) - L(p256)) / 256  cycles per sample in steady state (II)
  latency    = L(p1) - L(p0)            first sample in to first result out (one sample)
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
# samp_loop's iteration latency (its pipeline depth), from the synthesis report.
DEPTH = int(re.search(r"\|- samp_loop\s*\|[^|]*\|[^|]*\|\s*(\d+)\|",
                      (HERE / "reports" / "fir_Pipeline_samp_loop_csynth.rpt").read_text()).group(1))

names = (HERE / "data" / "scenarios_cosim.txt").read_text().split()
rows = re.findall(r"transaction\s+(\d+):\s+(\d+)\s+(\d+|x)",
                  (HERE / "reports" / "fir.performance.result.transaction.xml").read_text())
lat = {names[int(i)]: int(l) for i, l, _ in rows}
assert len(rows) == len(names), f"{len(rows)} transactions, {len(names)} cosim scenarios"

for n in (0, 1, 256, 512):
    print(f"L(timing_p{n:<3d}) = {lat[f'timing_p{n}']} cycles")
span = lat["timing_p256"] - lat["timing_p0"]
rate = (lat["timing_p512"] - lat["timing_p256"]) / 256
latency = lat["timing_p1"] - lat["timing_p0"]
limit = 256 + DEPTH + 10
print(f"span(256 samples) = {span} cycles; target <= 256 + depth {DEPTH} + 10 = {limit}: "
      f"{'PASS' if span <= limit else 'FAIL'}")
print(f"steady-state rate = {rate:.3f} cycles/sample (II)")
print(f"latency, first sample in to first result out = {latency} cycles (no target)")
