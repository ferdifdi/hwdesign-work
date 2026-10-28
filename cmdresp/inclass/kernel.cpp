// kernel.cpp -- the clamp-and-count kernel (Vitis HLS).
//
// The command-response pattern: commands and the clamp bounds arrive on s_in; responses
// leave on m_out; AXI-Lite carries only ap_ctrl and the read-only status halted / error /
// tx_id.  Word layouts are in model.py and results.md; model.py is this code's bit-exact twin.
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

void clampcount(hls::stream<word_t>& s_in, hls::stream<word_t>& m_out,
                ap_uint<1>& halted, ap_uint<8>& error, ap_uint<16>& tx_id) {
#pragma HLS INTERFACE mode=axis port=s_in
#pragma HLS INTERFACE mode=axis port=m_out
#pragma HLS INTERFACE mode=s_axilite port=halted
#pragma HLS INTERFACE mode=s_axilite port=error
#pragma HLS INTERFACE mode=s_axilite port=tx_id
#pragma HLS INTERFACE mode=s_axilite port=return

    // Nothing persists between activations: status, bounds and count all start clear.
    halted = 0;
    error = ERR_NONE;
    tx_id = 0;
    bool configured = false;
    float lo = 0.0f, hi = 0.0f;
    ap_uint<32> count = 0;

cmd_loop:
    while (true) {
        // Fixed-length header: four words, TLAST not checked.
        ap_uint<32> h[HDR_WORDS];
        for (int k = 0; k < HDR_WORDS; ++k) {
            h[k] = s_in.read().data;
        }
        const ap_uint<4> cmd = h[0](3, 0);
        const ap_uint<16> id = h[0](31, 16);
        const ap_uint<32> resp = (ap_uint<32>(id) << 16) | cmd;
        ap_uint<8> err = ERR_NONE;

        if (cmd == CMD_END) {
            return;
        } else if (cmd == CMD_CONFIG) {
            const float nlo = as_float(h[2]);
            const float nhi = as_float(h[3]);
            if (nlo != nlo || nhi != nhi || nlo > nhi) {
                err = ERR_BAD_BOUNDS;
            } else {
                lo = nlo;
                hi = nhi;
                count = 0;
                configured = true;
                put(m_out, resp, true);
            }
        } else if (cmd == CMD_DATA) {
            if (!configured) {
                err = ERR_NO_CONFIG;
            } else {
                put(m_out, resp, true);
                const ap_uint<32> nsamp = h[1];
            samp_loop:
                for (ap_uint<33> i = 0; i < nsamp; ++i) {
#pragma HLS PIPELINE II=1
                    const word_t in = s_in.read();
                    const float x = as_float(in.data);
                    const bool below = x < lo;
                    const float t = below ? lo : x;
                    const bool above = t > hi;
                    const float y = above ? hi : t;
                    count += (below || above) ? 1 : 0;
                    const bool final_word = (i == nsamp - 1);
                    const bool in_last = in.last;
                    put(m_out, as_bits(y), final_word || in_last);
                    if (in_last && !final_word) {
                        err = ERR_TLAST_EARLY;
                        break;
                    }
                    if (final_word && !in_last) {
                        err = ERR_NO_TLAST;
                    }
                }
            }
        } else if (cmd == CMD_COUNT) {
            if (!configured) {
                err = ERR_NO_CONFIG;
            } else {
                put(m_out, resp, true);
                put(m_out, count, true);
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
