# Streaming FIR filter

Build a Vitis HLS kernel in this folder following the Waveflow stream_inband
example (https://sdrangan.github.io/waveflow/docs/examples/stream_inband/) and
the command-response pattern.

## Operation
A float32 FIR filter whose taps are sent in a CONFIG command, applied by every
PROCESS command after it:

    y[n] = h[0]*x[n] + h[1]*x[n-1] + ... + h[ntaps-1]*x[n-ntaps+1]

- **Arithmetic order, bit-exact.**  Each product is rounded to float32, and
  the sum is accumulated left to right in k, each addition rounded to float32:
  `acc = h[0]*x[n]; for k = 1 .. ntaps-1: acc = acc + h[k]*x[n-k]`.  No fused
  multiply-add, and no reordering into a tree.  Exactly ntaps products enter
  the sum: taps beyond ntaps are not added, not even as zero.
- **History.**  `n` counts the samples of all PROCESS commands since the most
  recent CONFIG, in order.  So the history carries over from one PROCESS
  command to the next: splitting a sample sequence across several PROCESS
  commands gives the same outputs as sending it in one.  A sample from before
  that CONFIG (`n-k < 0`) is +0.0, and its product is included in the sum like
  any other.
- **Reconfiguration.**  A second CONFIG in the same activation replaces the
  taps and clears the history to +0.0.
- Taps are not checked: inf or NaN taps propagate under IEEE rules.  IEEE
  does not fix a NaN's sign or payload, so every NaN result is written as the
  canonical quiet NaN 0x7FC00000, in the model and the kernel alike.
- Nothing persists between activations: no taps, no history, no status.

## Requirements
- Data: IEEE-754 float32 taps, samples and results.  One 32-bit AXI4-Stream
  in, one out.  A sample or result is one word.
- Sizes: 1 ≤ ntaps ≤ 16 (MAX_TAPS = 16).  0 ≤ nsamp ≤ 65535 per PROCESS
  command.
- Throughput target: the PROCESS sample loop accepts one input sample per
  clock in steady state (II = 1), measured in co-simulation as the cycles from
  a 256-sample PROCESS command's first sample word in to its last result out.
  Pass if that is at most 256 + the loop's pipeline depth + 10.
- Latency: no target; report the first-sample-in to first-result-out cycles.

## Commands and parameters
- **CONFIG**: a header carrying `tx_id` and `ntaps`, then a burst of ntaps tap
  words h[0] .. h[ntaps-1], the last with TLAST.  `ntaps` is checked before
  any tap is read.  Response: a response header, sent after the whole tap
  burst has been read and checked.
- **PROCESS**: a header carrying `tx_id` and `nsamp`, then a burst of nsamp
  samples, the last with TLAST, unless nsamp = 0, in which case no burst.
  Response: a response header echoing `tx_id`, sent before the samples are
  processed, then nsamp results as one burst (none if nsamp = 0).
- **END**: header only.  No response; the kernel returns.

Every header is a fixed number of words and its own burst.  The kernel does
not check TLAST on a header.  ntaps and nsamp are 16-bit fields.  If they
share a word with unused bits, those bits are ignored, not an error.

## Errors
Errors go in AXI-Lite status registers (halted, error code, tx_id).  On an
error the kernel closes any output burst it started with TLAST and returns
without reading more input.  Nothing persists between activations: the status
registers are cleared at the start of each one.

The errors, with the output written for the failing command:

| Error | Condition | Written for the failing command |
| --- | --- | --- |
| UNKNOWN_CMD | header's command type is not CONFIG, PROCESS or END | nothing |
| BAD_NTAPS | CONFIG with ntaps = 0 or ntaps > 16 | nothing; no tap is read |
| NO_CONFIG | PROCESS before any CONFIG in this activation (also with nsamp = 0) | nothing |
| TAPS_TLAST_EARLY | the tap burst's TLAST comes before tap ntaps | nothing |
| TAPS_NO_TLAST | the last tap has no TLAST | nothing |
| SAMP_TLAST_EARLY | the sample burst's TLAST comes before sample nsamp | response header, then the result of every sample read, the last with TLAST |
| SAMP_NO_TLAST | the last sample has no TLAST | response header, then all nsamp results, the last with TLAST |

Each command type has one header check, so no two errors apply at once.  A
burst error is found while reading, after the header check has passed.

## Verification
- **Hand-computed examples** for the Python model, checked first:
  - taps [1, 2, 3], PROCESS x = [1, 0, 0, 0, 1, 1] gives y = [1, 2, 3, 0, 1, 3];
  - then a second PROCESS x = [2] gives y = [7] (2·1 + 1·2 + 1·3, history carried);
  - then CONFIG taps [0.5] and PROCESS [4] gives [2] (history cleared, new taps);
  - taps [1, 1, 1], x = [1, 1e8, -1e8] gives y = [1, 1e8, 1] exactly.  The
    last output is (-1e8 + 1e8) + 1 = 1 in the specified order.  Summed from
    the oldest sample first it would be (1 + 1e8) - 1e8 = 0, because
    1e8 + 1 rounds to 1e8 in float32.  The order is part of the spec.
- **Independent reference**: the expected output of every scenario is
  computed from its intent by a scalar float32 loop in the order above,
  written separately from the model.  Also compare the model with NumPy
  `np.convolve` in float64 over the samples since the last CONFIG, to a
  relative tolerance of 1e-5 where the outputs are not near cancellation.
- **Scenarios**: ntaps = 1 and ntaps = 16; a PROCESS shorter than ntaps, so
  the history crosses a command boundary; the same 40 samples sent as one
  PROCESS and as PROCESS commands of 13, 1, 0 and 26 samples, which must give
  identical outputs; a second CONFIG mid-activation with a different ntaps;
  nsamp = 0; a 256-sample PROCESS with 16 taps, for the throughput; and one
  activation per error, each followed by a fresh activation that must pass.
  Each error activation, in C simulation and co-simulation alike, also has
  commands queued behind the failing one, which must stay unread.

Compare the model, C simulation and co-simulation bit for bit.  Run
co-simulation once: one testbench run that walks through every scenario in
order, calling the kernel once per activation with fresh streams, including
each error activation and the fresh activation after it.

## results.md
Write results.md with these sections:
- Interface: the command and response layouts, how values pack into words,
  the status registers and the error codes.
- Design: the loop structure, where state is kept, and how the throughput
  target is reached.
- Targets: each target above with the required value, the measured value,
  PASS/FAIL and the file it came from (co-simulation for timing; synthesis
  for clock, II and resources).  Always report the latency and throughput
  from co-simulation and the estimated clock and resources from synthesis,
  writing "no target" where this prompt sets none.
- Verification: the model's check against the hand-computed examples and the
  reference; then one row per scenario saying whether the model, C simulation
  and co-simulation agree, and for each error scenario the status it set and
  whether the fresh activation after it passed. Name the source file of each.
- Assumptions: every place this prompt was silent or ambiguous and what you
  chose, apart from interface details already given under Interface.
- Open Items: questions you could not settle, and anything that failed or was
  not done, or "None".
- Run History: one row per run, with the prompt version, what was built or
  changed, and what passed, so that a later run can continue from here.

Every number and PASS/FAIL in Targets and Verification names the file it came
from (a log, a comparison output or a synthesis report), and that file stays in
this folder to be committed: a reviewer opens them to check results.md.

## Revision history
| Version | Change | Why |
| --- | --- | --- |
| 1 | First version | |
| 2 | Every NaN result is the canonical quiet NaN 0x7FC00000 | Run 1: `nonfinite_taps` failed cosim. The RTL wrote 0x7FC00000 and x86 0xFFC00000 for inf × 0 (Open Item 1, Assumption 8) |
| 2 | Unused bits beside ntaps/nsamp are ignored, not an error | Run 1 had to choose (Assumption 1); I kept its choice |
| 2 | Error activations with commands queued behind them run in cosim too | Run 1 ran them in csim only, believing cosim could not leave input unread; its own cosim showed it can (Assumption 7) |
