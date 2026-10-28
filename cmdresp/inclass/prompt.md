# Clamp-and-count accelerator

Build a Vitis HLS kernel in this folder following the Waveflow stream_inband
example (the streaming polynomial) and the course command-response pattern.
Where the two differ, follow the pattern: the clamp bounds are sent in a
CONFIG command on the input stream, never written over AXI-Lite.  AXI-Lite
carries only ap_start/ap_done and the read-only status registers halted,
error and tx_id.  One 32-bit AXI4-Stream in, one out; every command header
and response header is its own burst closed with TLAST.

Write a bit-exact Python model (NumPy float32) and the HLS kernel, run the
model and C simulation on every scenario below, and check that they agree
bit for bit with an expected output computed from each scenario's intent.
Stop once C simulation passes: no synthesis or co-simulation.

## Operation
All values are IEEE-754 float32.  Commands:

- CONFIG(lo, hi): sets the bounds and resets the clipped-sample count to 0.
  A second CONFIG in the same activation replaces both bounds and resets
  the count again.  Response: a response header only.
- DATA(nsamp, then nsamp samples): for each sample x, in order,
  `t = (x < lo) ? lo : x;  y = (t > hi) ? hi : t;`
  exactly these comparisons, so a NaN sample passes through unchanged and
  -0.0 is not clipped by lo = +0.0.  A sample is *clipped* if `x < lo` or
  `t > hi`; each clipped sample adds 1 to the count.  Response: a response
  header (echoing tx_id), then nsamp results as one burst.  nsamp = 0 is
  valid: a response header and no sample bursts either way.
- COUNT: response header, then one word: the number of samples clipped since
  the most recent CONFIG, as uint32.  COUNT does not reset the count.
- END: no response; the kernel returns.

Nothing persists between activations: no bounds, no count, no status.

## Errors
On any error the kernel sets halted = 1, error = its code and tx_id = the
failing command's tx_id, closes any output burst it started (TLAST on the
last word written) and returns without reading more input.  Codes:

1. DATA or COUNT before any CONFIG in this activation: nothing is written
   for that command.
2. CONFIG with lo > hi, or lo or hi NaN: nothing is written for it.
3. Unknown command type: nothing is written for it.
4. A DATA sample burst whose TLAST comes before sample nsamp: the kernel
   writes the result of every sample it read, the last with TLAST, then halts.
5. A DATA sample burst whose last sample has no TLAST: the kernel writes all
   nsamp results (the last with TLAST), then halts.

Headers have a fixed length and their TLAST is not checked.  The status is
cleared at the start of every activation.

## Checks
- Check the Python clamp against these hand-computed cases first, with
  lo = -1.0, hi = 2.0: x = [-3, -1, 0.5, 2, 5, NaN, -0.0] gives
  y = [-1, -1, 0.5, 2, 2, NaN, -0.0] and a count of 2; then a second DATA
  [10, -10] gives [2, -1] and a COUNT of 4; then CONFIG(0, 0) and COUNT gives 0.
- Scenarios: several DATA commands of different lengths (including 1 and 0);
  a second CONFIG mid-activation, showing new bounds and a reset count;
  COUNT across several DATA commands; lo == hi; every error above, each with
  more commands queued behind the failing one, each followed by a fresh
  activation (fresh streams) that must pass.
- The testbench fills the status registers with garbage before each call, so
  a kernel that forgets to clear them fails.

## results.md
Write results.md with: the interface you chose (command and response
layouts, error codes); a table of every scenario with model and C simulation
PASS/FAIL and the status each error set; and a list of every place this
prompt was silent or ambiguous, and what you chose.
