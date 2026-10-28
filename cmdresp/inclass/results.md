# Clamp-and-count accelerator: results

Built from `prompt.md` (version 1) through C simulation.  No synthesis or
co-simulation, as the prompt asked.

**Result: every scenario passes.**  The Python model and C simulation both
match the expected output computed from each scenario's intent: 20 of 20
scenarios, bit for bit, TLAST for TLAST, with the final status checked too.

## How it was built

| File | Contents |
| --- | --- |
| `model.py` | The bit-exact model: `clamp()` and `stream_model()`, a pure function from input words to output words and status |
| `scenarios.py` | The scenarios as intents, the stimulus, the expected output (from a separate scalar reference), the hand checks and the checker |
| `kernel.hpp`, `kernel.cpp` | The HLS kernel `clampcount` |
| `tb.cpp` | The C++ testbench: one activation per scenario on fresh streams, status poisoned before each call |
| `run.tcl` | Vitis HLS C simulation (`vitis-run --mode hls --tcl run.tcl`) |
| `logs/hand_check.txt`, `logs/model_check.txt`, `logs/csim_check.txt`, `logs/csim_vitis.txt` | Evidence |
| `data/<scenario>/` | `in.txt`, `expected.txt`, `expected_status.json`, and the `model_*` and `csim_*` outputs |

The Waveflow `stream_inband` example (`poly.py`, `scenarios.py`,
`poly_body_impl.tpp`, `poly_tb.cpp`, `run.tcl` and the docs pages `index.md`,
`protocol.md`, `error_path.md`) was read from a clone of the Waveflow
repository, not through the MCP server: the server on this machine runs an
older Waveflow install without the knowledge base, and installing the new
clone was not permitted in this session.  So the example's **structure** is
followed: a pure model, expected outputs from intents, a testbench that
poisons the status and runs one activation per scenario, and the TLAST rules.
Waveflow's **code generation** (schemas and serializers) is not used.  The
header and response words are packed by hand, in `model.py` and `kernel.cpp`,
and the two are checked against each other by every scenario.

Vitis HLS **2026.1** was used: the version installed here, not the course's
2025.1.  The part is `xc7z020clg484-1` and the clock 10 ns, both from the
example.

## Interface

One 32-bit AXI4-Stream in (`s_in`) and one out (`m_out`).  AXI-Lite carries
`ap_ctrl` and three read-only status registers: `halted` (1 bit), `error`
(8 bits) and `tx_id` (16 bits).

**Command header**: 4 words, one burst.  The kernel reads exactly 4 words
and does not check their TLAST.

| Word | Contents |
| --- | --- |
| 0 | bits [3:0] `cmd_type`: CONFIG = 0, DATA = 1, COUNT = 2, END = 3; bits [31:16] `tx_id` |
| 1 | `nsamp`, uint32 (DATA only; 0 otherwise) |
| 2 | `lo`, float32 bits (CONFIG only; 0 otherwise) |
| 3 | `hi`, float32 bits (CONFIG only; 0 otherwise) |

**Responses**: every burst is closed with TLAST.

| Command | out_stream |
| --- | --- |
| CONFIG | response header (1 word) |
| DATA | response header · `nsamp` float32 results (one burst; none if `nsamp` = 0) |
| COUNT | response header · count, as uint32 (a 1-word burst) |
| END | nothing |

Response header word: bits [3:0] echo `cmd_type`, bits [31:16] echo `tx_id`.

**Error codes**

| Code | Name | When | Written for the failing command |
| --- | --- | --- | --- |
| 0 | NO_ERROR | | |
| 1 | NO_CONFIG | DATA or COUNT before any CONFIG | nothing |
| 2 | BAD_BOUNDS | CONFIG with lo > hi, or lo or hi NaN | nothing |
| 3 | UNKNOWN_CMD | `cmd_type` not 0–3 | nothing |
| 4 | TLAST_EARLY | the sample burst's TLAST before sample `nsamp` | header and the results of the samples read, the last with TLAST |
| 5 | NO_TLAST | the last sample has no TLAST | header and all `nsamp` results, the last with TLAST |

## Scenarios

The source for every row is `logs/model_check.txt` (model) and
`logs/csim_check.txt` (C simulation), each comparing against
`data/<scenario>/expected.txt` and `expected_status.json`.  Every error
scenario queues more commands behind the failing one.  The testbench log
(`logs/csim_vitis.txt`) shows them left unread (`unread_words` > 0).

| Scenario | What it tests | Model | C sim | Status (halted, error, tx_id) |
| --- | --- | --- | --- | --- |
| hand | The worked example from the prompt, through the whole stream | PASS | PASS | 0, 0, 0 |
| multi_len | DATA with 100, 7, 1 and 0 samples, then COUNT | PASS | PASS | 0, 0, 0 |
| reconfig | A second CONFIG resets the count and changes the bounds | PASS | PASS | 0, 0, 0 |
| count_accum | COUNT across three DATA commands; COUNT twice (no reset) | PASS | PASS | 0, 0, 0 |
| lo_eq_hi | lo = hi = 0.25; samples include +inf and -inf | PASS | PASS | 0, 0, 0 |
| zero_only | DATA with nsamp = 0, then COUNT | PASS | PASS | 0, 0, 0 |
| err_no_config | DATA first | PASS | PASS | 1, 1, 50 |
| err_no_config_fresh | Fresh activation | PASS | PASS | 0, 0, 0 |
| err_count_no_config | COUNT first | PASS | PASS | 1, 1, 55 |
| err_count_no_config_fresh | Fresh activation | PASS | PASS | 0, 0, 0 |
| err_bad_bounds | CONFIG(2, 1) after a good CONFIG and DATA | PASS | PASS | 1, 2, 62 |
| err_bad_bounds_fresh | Fresh activation | PASS | PASS | 0, 0, 0 |
| err_nan_bounds | CONFIG(NaN, 1) | PASS | PASS | 1, 2, 65 |
| err_nan_bounds_fresh | Fresh activation | PASS | PASS | 0, 0, 0 |
| err_unknown | `cmd_type` = 9 | PASS | PASS | 1, 3, 71 |
| err_unknown_fresh | Fresh activation | PASS | PASS | 0, 0, 0 |
| err_early_tlast | 10 samples announced, 6 sent | PASS | PASS | 1, 4, 77 |
| err_early_tlast_fresh | Fresh activation | PASS | PASS | 0, 0, 0 |
| err_no_tlast | 6 samples, no TLAST on the last | PASS | PASS | 1, 5, 81 |
| err_no_tlast_fresh | Fresh activation | PASS | PASS | 0, 0, 0 |

**Hand checks** of `model.clamp` (`logs/hand_check.txt`): all three of the
prompt's worked examples PASS.

**Independence.**  The expected output comes from a scalar Python reference
in `scenarios.py`.  It counts a sample as clipped when `x < lo or x > hi`, a
different test from the model's `x < lo or t > hi`.  The status poisoning in
`tb.cpp` (`halted = 1, error = 0xEE, tx_id = 0xFFFF`) would catch a kernel
that does not clear its status.

## Where the prompt was silent or ambiguous, and what was chosen

1. **Header layout and command codes**: a fixed 4-word header for every
   command, with unused words sent as 0, and codes CONFIG = 0, DATA = 1,
   COUNT = 2, END = 3.
2. **Width and limit of `nsamp`**: uint32, a full word, with no upper limit
   checked.  There are no buffers, so no limit is needed.
3. **Response header contents**: `tx_id` plus the echoed `cmd_type`, so the
   host can tell a CONFIG response from a COUNT response.
4. **When CONFIG's response is sent**: only after the bounds are checked.  A
   bad CONFIG writes nothing.
5. **COUNT's framing**: the header and the count are two separate bursts,
   each with TLAST, not one 2-word burst.
6. **Count overflow**: it wraps modulo 2^32.  The prompt gave no limit.
7. **Infinite bounds**: allowed.  Only NaN and lo > hi are invalid, so
   CONFIG(-inf, +inf) is a pass-through.
8. **Signed zeros in the bounds**: CONFIG(+0.0, -0.0) is accepted, because
   +0.0 > -0.0 is false in IEEE comparison.  The output then follows the
   formula exactly.
9. **Error precedence**: each command has only one header check, so no two
   errors compete.  A DATA with `nsamp` = 0 before CONFIG is still NO_CONFIG.
10. **Unknown command, which `tx_id`**: taken from bits [31:16] of word 0, as
    for every command.
11. **NaN samples**: passed through and not counted as clipped.  This follows
    from the prompt's formula, but it was never stated outright.
12. **A TLAST inside a header**: not checked, as the prompt said.  A host that
    sends a short header therefore has the following words read as the rest
    of it, silently.
13. **The ap_ctrl protocol**: `ap_ctrl_hs` on AXI-Lite, the Vitis default for
    `s_axilite port=return`.
14. **Tool and flow differences**: Vitis 2026.1, not 2025.1.  Waveflow's
    codegen and MCP server were not used (see *How it was built*).
