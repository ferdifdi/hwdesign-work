# Streaming FIR filter: results

Built from `prompt.md` version 1.  The kernel `fir` is a float32 FIR filter
of up to 16 taps, built with Vitis HLS 2026.1 for `xc7z020clg484-1` at a
10 ns clock.

**Status after run 1**: every scenario passes in the model and in C
simulation.  In co-simulation, 32 of the 33 scenarios pass and
`nonfinite_taps` FAILS: the RTL writes a NaN with a different sign bit (see
Open Items).  The clock and throughput targets pass.

## Interface

One 32-bit AXI4-Stream in (`s_in`) and one out (`m_out`), one float32 per
word.  AXI-Lite (`s_axi_control`) carries `ap_ctrl_hs` and three read-only
status registers.

**Command header**: 2 words, its own burst.  The kernel reads exactly 2
words and does not check their TLAST.

| Word | Bits | Field |
| --- | --- | --- |
| 0 | [3:0] | `cmd_type`: CONFIG = 0, PROCESS = 1, END = 2 |
| 0 | [31:16] | `tx_id` |
| 1 | [15:0] | `count`: `ntaps` for CONFIG, `nsamp` for PROCESS, 0 for END |
| 1 | [31:16] | ignored (sent as 0) |

**Data bursts on s_in**

| Command | After its header |
| --- | --- |
| CONFIG | `ntaps` words, h[0] .. h[ntaps-1] as float32, TLAST on the last |
| PROCESS | `nsamp` words x as float32, TLAST on the last; nothing if `nsamp` = 0 |
| END | nothing |

**Responses on m_out**: each burst is closed with TLAST.

| Command | Response |
| --- | --- |
| CONFIG | response header (1 word), written after the tap burst has been read and checked |
| PROCESS | response header (1 word), then `nsamp` float32 results as one burst |
| END | nothing |

Response header: bits [3:0] echo `cmd_type`, bits [31:16] echo `tx_id`.

**Status registers**

| Register | Offset | Width | Meaning |
| --- | --- | --- | --- |
| `ap_ctrl` | 0x00 | | ap_start, ap_done, ap_idle, ap_ready |
| `halted` | 0x10 | 1 | 1 if the activation ended on an error |
| `error` | 0x20 | 8 | the error code below |
| `tx_id` | 0x30 | 16 | the `tx_id` of the failing command |

The kernel clears all three at the start of every activation.  The offsets
are those Vitis assigns, listed in the synthesized driver header, copied to
`reports/xfir_hw.h`.

**Error codes**

| Code | Name | Written for the failing command |
| --- | --- | --- |
| 0 | NO_ERROR | |
| 1 | UNKNOWN_CMD | nothing |
| 2 | BAD_NTAPS (ntaps = 0 or > 16) | nothing; no tap is read |
| 3 | NO_CONFIG (PROCESS before any CONFIG) | nothing |
| 4 | TAPS_TLAST_EARLY | nothing |
| 5 | TAPS_NO_TLAST | nothing |
| 6 | SAMP_TLAST_EARLY | response header, then the result of every sample read, the last with TLAST |
| 7 | SAMP_NO_TLAST | response header, then all `nsamp` results, the last with TLAST |

## Design

`kernel.cpp` is a single function with one command loop (`cmd_loop`).  Each
pass reads a 2-word header and dispatches on it:

- **CONFIG**: checks `ntaps`, then runs `tap_loop` (pipelined, II = 1, up to
  16 iterations).  It reads each tap into `h[]` and checks TLAST on each
  one.  On success it sets `ntaps`, clears the history to +0.0 and writes
  the response header.
- **PROCESS**: writes the response header, then runs `samp_loop` (pipelined,
  II = 1, depth 80).  Each iteration reads one sample, shifts it into
  `hist[0]` (with `hist[k]` = x[n-k]), and evaluates the filter as a chain of
  rounded float32 operations: `acc = h[0]*hist[0]`, then for k = 1..15,
  `acc = (k < ntaps) ? acc + h[k]*hist[k] : acc`.  It writes one result per
  sample and checks TLAST on each input word.
- **END** returns.  Anything else is an error.

**State.**  `h[16]` and `hist[16]` are fully partitioned into registers.
`ntaps` and a `configured` flag are kept alongside them.  All of them are
locals of one activation, so nothing survives between activations.  The
history persists across PROCESS commands because it lives outside
`cmd_loop`'s per-command code, and CONFIG clears it.

**Throughput.**  The 16 multiplies and 15 additions are fully unrolled into
one combinational chain per sample.  The chain lies inside an iteration, so
the only loop-carried state is the shift register, and that allows II = 1.
The fixed left-to-right order the spec requires makes the pipeline deep: 15
dependent float additions give a depth of 80 cycles.  That costs latency
but not throughput.  Every product is computed, but those for k ≥ ntaps
are not added.

## Targets

| Target | Required | Measured | Result | Source |
| --- | --- | --- | --- | --- |
| Clock | estimated < 10 ns | 9.244 ns (uncertainty 2.70 ns) | PASS | `reports/fir_csynth.rpt`, Timing |
| samp_loop II | 1 | 1 (depth 80) | PASS | `reports/fir_Pipeline_samp_loop_csynth.rpt`, Loop |
| tap_loop II | no target | 1 (depth 3) | — | `reports/fir_Pipeline_tap_loop_csynth.rpt` |
| Throughput: 256 samples, first sample in to last result out | ≤ 256 + 80 + 10 = 346 cycles | 338 cycles | PASS | `logs/timing.txt`, from `reports/fir.performance.result.transaction.xml` |
| Steady-state rate | no separate target | 1.000 cycle/sample | — | `logs/timing.txt` |
| Latency, first sample in to first result out | no target | 83 cycles | — | `logs/timing.txt` |
| Whole-run cosim latency | no target | min 5, avg 152, max 628 cycles per activation; 5470 in total | — | `reports/fir_cosim.rpt` |
| DSP | no target | 78 of 220 (35 %) | — | `reports/fir_csynth.rpt`, Utilization |
| FF | no target | 14,047 of 106,400 (13 %) | — | `reports/fir_csynth.rpt` |
| LUT | no target | 14,569 of 53,200 (27 %) | — | `reports/fir_csynth.rpt` |
| BRAM | no target | 0 | — | `reports/fir_csynth.rpt` |

The synthesis log (`logs/cosim_vitis.txt`) includes the warning
HLS 200-871, "estimated clock period exceeds the target … effective delay
budget 7.3 ns".  As the assignment directs, this is the clock-uncertainty
margin and not a FAIL: 9.244 ns is under 10 ns.

**How the timing is measured** (`timing.py`).  Co-simulation reports each
activation's cycles from ap_start to ap_done.  Four timing activations are
identical (CONFIG with 16 taps, one PROCESS, END) apart from `nsamp` = 0, 1,
256 or 512.  Then:

- span = L(256) − L(0), the cycles the 256 samples add (372 − 34 = 338);
- rate = (L(512) − L(256)) / 256 = 1.000;
- latency = L(1) − L(0) = 117 − 34 = 83.

## Verification

**The model's independent checks**

| Check | Result | Source |
| --- | --- | --- |
| Hand example: taps [1,2,3], x = [1,0,0,0,1,1] → [1,2,3,0,1,3] | PASS | `logs/hand_check.txt` |
| Then x = [2] → [7] (history carried) | PASS | `logs/hand_check.txt` |
| CONFIG [0.5], x = [4] → [2] (history cleared) | PASS | `logs/hand_check.txt` |
| Taps [1,1,1], x = [1, 1e8, −1e8] → [1, 1e8, 1]; oldest-first order would give 0 | PASS | `logs/hand_check.txt` |
| `np.convolve` (float64) on every scenario, tolerance 1e-5 · Σ\|h_k x_{n−k}\| | PASS (41 scenarios checked; `nonfinite_taps` skipped, see below) | `logs/reference_check.txt` |
| The expected output of every scenario, from a separate scalar float32 loop in `scenarios.py` | the model matches on all 42 | `logs/model_check.txt` |

`nonfinite_taps` is skipped in the float64 reference.  `np.convolve` leaves
out the products with the zeros before CONFIG, but the spec includes them,
and inf × (+0.0) = NaN.

**Scenarios.**  The model and C simulation results are in
`logs/model_check.txt` and `logs/csim_check.txt`; co-simulation's is in
`logs/cosim_check.txt`.  Each compares `data/<s>/<stage>_out.txt` and
`<stage>_status.json` with `data/<s>/expected.txt` and
`expected_status.json`.

| Scenario | What it tests | Model | C sim | Cosim | Status (halted, error, tx_id) |
| --- | --- | --- | --- | --- | --- |
| hand_a | The hand examples, in one stream | PASS | PASS | PASS | 0,0,0 |
| hand_order | Order of summation | PASS | PASS | PASS | 0,0,0 |
| ntaps1 | ntaps = 1 | PASS | PASS | PASS | 0,0,0 |
| ntaps16 | ntaps = 16, 50 samples | PASS | PASS | PASS | 0,0,0 |
| short_cross | PROCESS of 5 and 3 < 16 taps: history across commands | PASS | PASS | PASS | 0,0,0 |
| split_one / split_many | 40 samples as 1 PROCESS, and as 13+1+0+26 | PASS | PASS | PASS | 0,0,0 |
| split_one == split_many | The 40 results are identical | PASS | PASS | PASS | |
| reconfig | Second CONFIG, 4 → 11 taps, history cleared | PASS | PASS | PASS | 0,0,0 |
| zero_len | nsamp = 0 around a PROCESS | PASS | PASS | PASS | 0,0,0 |
| signed_zero | Taps [−1,−2] on ±0.0: −0.0 results | PASS | PASS | PASS | 0,0,0 |
| nonfinite_taps | Taps [1, inf]: NaN and inf results | PASS | PASS | **FAIL** | 0,0,0 |
| timing_p0 / p1 / p256 / p512 | 16 taps; nsamp 0, 1, 256, 512 | PASS | PASS | PASS | 0,0,0 |
| err_unknown | cmd_type 7 after a CONFIG and PROCESS | PASS | PASS | PASS | 1,1,52 |
| err_bad_ntaps0 | ntaps = 0 | PASS | PASS | PASS | 1,2,53 |
| err_bad_ntaps17 | ntaps = 17, its 17 taps sent | PASS | PASS | PASS | 1,2,54 |
| err_no_config | PROCESS first | PASS | PASS | PASS | 1,3,55 |
| err_no_config_zero | PROCESS with nsamp = 0 first | PASS | PASS | PASS | 1,3,56 |
| err_taps_early | 5 taps announced, 3 sent | PASS | PASS | PASS | 1,4,57 |
| err_taps_notlast | 5 taps, no TLAST | PASS | PASS | PASS | 1,5,58 |
| err_samp_early | 10 samples announced, 6 sent | PASS | PASS | PASS | 1,6,61 |
| err_samp_notlast | 6 samples, no TLAST | PASS | PASS | PASS | 1,7,63 |
| err_*_queued (9) | Each error above with CONFIG + PROCESS + END queued behind it; must stay unread | PASS | PASS | not run | same as above |
| err_*_fresh (9) | The fresh activation after each error | PASS | PASS | PASS | 0,0,0 |

The fresh activations all pass.  The co-simulation checker also verifies
that every error scenario's last output word has TLAST, and none failed.
Co-simulation ran once: `run_cosim.tcl`, a single `cosim_design` call
walking the 33 scenarios in `data/scenarios_cosim.txt`, one kernel call per
activation.  The tests left unread input in the queued scenarios: the
testbench log (`logs/csim_vitis.txt`, `unread_words`) shows the queued words
still in the stream after each error.

## Assumptions

1. **The `count` field is 16 bits**, in word 1 bits [15:0].  The upper 16
   bits are ignored, not an error.
2. **Taps from a failed CONFIG** may be partly written into `h[]`, but the
   kernel halts at once, so no result ever uses them.
3. **A BAD_NTAPS header for ntaps > 16** is still followed, in the test, by
   the 17 taps a host would send.  The kernel leaves them unread.
4. **The throughput measurement** uses differences of whole-activation
   latencies, not a waveform: L(p256) − L(p0).  This can include 1–2 cycles
   where the samples overlap the PROCESS header or END, so it is an upper
   bound on the span the prompt defines.
5. **The latency** is measured the same way, as L(p1) − L(p0).
6. **The pipeline depth in the throughput limit** is the synthesized
   `samp_loop` depth, 80, from `reports/fir_Pipeline_samp_loop_csynth.rpt`.
7. **The `_queued` error variants run in C simulation only.**  I believed
   co-simulation could not leave input unread.  Run 1 showed otherwise:
   `err_bad_ntaps17` and `err_no_config` left 17 and 4 words unread in
   co-simulation and passed.
8. **NaN bit patterns**: the prompt requires bit-exact results and says inf
   and NaN taps "propagate under IEEE rules", but IEEE does not fix a NaN's
   sign or payload.  The model computes them as x86 does, which gives
   0xFFC00000 for inf × 0.  This choice fails co-simulation (Open Items).
9. **The fresh activation** after each error is the same 4-tap CONFIG and
   8-sample PROCESS every time.
10. **`ap_ctrl_hs`** on AXI-Lite (the Vitis default for
    `s_axilite port=return`).
11. **The flow**: Vitis HLS 2026.1, not 2025.1.  The Waveflow
    `stream_inband` example was read from a clone of the Waveflow repository
    and followed in structure, but its code generator and MCP server were
    not used: the installed Waveflow is too old, and installing the clone
    was not permitted in this session.  The header words are packed by hand
    in `model.py`, `scenarios.py` and `kernel.cpp`, and checked against each
    other by every scenario.

## Open Items

1. **`nonfinite_taps` fails co-simulation.**  inf × (+0.0) is NaN.  The RTL
   writes 0x7FC00000, the x86 model and C simulation 0xFFC00000
   (`logs/cosim_check.txt`).  The prompt must say how a NaN result is
   written, or that any NaN matches any NaN.
2. **Vitis warning HLS 200-887** reports a 10.921 ns combinational path in
   `samp_loop`, from the input-stream read
   (`logs/cosim_vitis.txt`).  The final estimate is 9.244 ns, so the target
   passes as the assignment defines it.  Implementation (place and route)
   was not run.

## Run History

| Run | Prompt version | Built or changed | Passed |
| --- | --- | --- | --- |
| 1 | 1 | Model, scenarios, kernel, testbench, csim, csynth and cosim, from scratch | Hand checks, reference, model 42/42, csim 42/42; cosim 32/33 (`nonfinite_taps` FAIL); clock, II and throughput PASS |
