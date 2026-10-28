// kernel.cpp -- the streaming FIR kernel (Vitis HLS).
//
// The command-response pattern: commands and the taps arrive on s_in, responses leave on
// m_out, and AXI-Lite carries only ap_ctrl and the read-only status halted / error / tx_id.
// Word layouts are in model.py and results.md; model.py is this code's bit-exact twin.
//
// State, kept only within one activation: the taps h[], their count ntaps, and the history
// hist[k] = x[n-k] of the samples since the last CONFIG.  Both arrays are registers.
#include "kernel.hpp"

namespace {

float as_float(ap_uint<32> u) {
#pragma HLS INLINE
    union { unsigned int i; float f; } c;
    c.i = (unsigned int)u;
    return c.f;
}

ap_uint<32> as_bits(float f) {
#pragma HLS INLINE
    union { unsigned int i; float f; } c;
    c.f = f;
    return c.i;
}

void put(hls::stream<word_t>& m_out, ap_uint<32> data, bool last) {
#pragma HLS INLINE
    word_t w;
    w.data = data;
    w.keep = -1;
    w.strb = -1;
    w.last = last;
    m_out.write(w);
}

}  // namespace

void fir(hls::stream<word_t>& s_in, hls::stream<word_t>& m_out,
         ap_uint<1>& halted, ap_uint<8>& error, ap_uint<16>& tx_id) {
#pragma HLS INTERFACE mode=axis port=s_in
#pragma HLS INTERFACE mode=axis port=m_out
#pragma HLS INTERFACE mode=s_axilite port=halted
#pragma HLS INTERFACE mode=s_axilite port=error
#pragma HLS INTERFACE mode=s_axilite port=tx_id
#pragma HLS INTERFACE mode=s_axilite port=return

    // Nothing persists between activations.
    halted = 0;
    error = ERR_NONE;
    tx_id = 0;
    bool configured = false;
    ap_uint<5> ntaps = 0;
    float h[MAX_TAPS];
    float hist[MAX_TAPS];
#pragma HLS ARRAY_PARTITION variable=h complete dim=1
#pragma HLS ARRAY_PARTITION variable=hist complete dim=1

cmd_loop:
    while (true) {
        // Fixed-length header, TLAST not checked.
        const ap_uint<32> w0 = s_in.read().data;
        const ap_uint<32> w1 = s_in.read().data;
        const ap_uint<4> cmd = w0(3, 0);
        const ap_uint<16> id = w0(31, 16);
        const ap_uint<16> count = w1(15, 0);
        const ap_uint<32> resp = (ap_uint<32>(id) << 16) | cmd;
        ap_uint<8> err = ERR_NONE;

        if (cmd == CMD_END) {
            return;
        } else if (cmd == CMD_CONFIG) {
            if (count == 0 || count > MAX_TAPS) {
                err = ERR_BAD_NTAPS;                 // checked before any tap is read
            } else {
            tap_loop:
                for (int k = 0; k < MAX_TAPS; ++k) {
#pragma HLS PIPELINE II=1
                    if (k < count) {
                        const word_t in = s_in.read();
                        h[k] = as_float(in.data);
                        const bool final_word = (k == count - 1);
                        if (in.last && !final_word) {
                            err = ERR_TAPS_TLAST_EARLY;
                            break;
                        }
                        if (final_word && !in.last) {
                            err = ERR_TAPS_NO_TLAST;
                        }
                    }
                }
                if (err == ERR_NONE) {
                    ntaps = count;
                    for (int k = 0; k < MAX_TAPS; ++k) {
#pragma HLS UNROLL
                        hist[k] = 0.0f;              // history since this CONFIG: +0.0
                    }
                    configured = true;
                    put(m_out, resp, true);          // after the whole tap burst is checked
                }
            }
        } else if (cmd == CMD_PROCESS) {
            if (!configured) {
                err = ERR_NO_CONFIG;
            } else {
                put(m_out, resp, true);
            samp_loop:
                for (ap_uint<17> i = 0; i < count; ++i) {
#pragma HLS PIPELINE II=1
                    const word_t in = s_in.read();
                    for (int k = MAX_TAPS - 1; k > 0; --k) {
#pragma HLS UNROLL
                        hist[k] = hist[k - 1];
                    }
                    hist[0] = as_float(in.data);
                    // acc = h0*x[n]; acc = acc + h[k]*x[n-k] for k < ntaps, left to right.
                    // Every product and sum is its own rounded float32 operation.
                    float acc = h[0] * hist[0];
                    for (int k = 1; k < MAX_TAPS; ++k) {
#pragma HLS UNROLL
                        const float p = h[k] * hist[k];
                        const float s = acc + p;
                        acc = (k < ntaps) ? s : acc;
                    }
                    // Every NaN result is the canonical quiet NaN (prompt v2): IEEE
                    // leaves its sign and payload open, and the RTL and x86 differ.
                    ap_uint<32> y = as_bits(acc);
                    if (y(30, 23) == 0xFF && y(22, 0) != 0) {
                        y = NAN_BITS;
                    }
                    const bool final_word = (i == count - 1);
                    put(m_out, y, final_word || in.last);
                    if (in.last && !final_word) {
                        err = ERR_SAMP_TLAST_EARLY;
                        break;
                    }
                    if (final_word && !in.last) {
                        err = ERR_SAMP_NO_TLAST;
                    }
                }
            }
        } else {
            err = ERR_UNKNOWN_CMD;
        }

        if (err != ERR_NONE) {
            // Any output burst started was closed above (TLAST on the last word written).
            error = err;
            tx_id = id;
            halted = 1;
            return;
        }
    }
}
