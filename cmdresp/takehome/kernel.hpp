// kernel.hpp -- the streaming FIR kernel's interface.
#pragma once

#include <ap_axi_sdata.h>
#include <ap_int.h>
#include <hls_stream.h>

typedef ap_axiu<32, 0, 0, 0> word_t;

static const int HDR_WORDS = 2;
static const int MAX_TAPS = 16;
static const unsigned int NAN_BITS = 0x7FC00000;   // every NaN result

// cmd_type, header word 0 bits [3:0]
enum { CMD_CONFIG = 0, CMD_PROCESS = 1, CMD_END = 2 };

// The error register
enum {
    ERR_NONE = 0,
    ERR_UNKNOWN_CMD = 1,       // cmd_type not CONFIG, PROCESS or END
    ERR_BAD_NTAPS = 2,         // CONFIG with ntaps = 0 or ntaps > MAX_TAPS
    ERR_NO_CONFIG = 3,         // PROCESS before any CONFIG
    ERR_TAPS_TLAST_EARLY = 4,  // tap burst's TLAST before tap ntaps
    ERR_TAPS_NO_TLAST = 5,     // last tap without TLAST
    ERR_SAMP_TLAST_EARLY = 6,  // sample burst's TLAST before sample nsamp
    ERR_SAMP_NO_TLAST = 7,     // last sample without TLAST
};

void fir(hls::stream<word_t>& s_in, hls::stream<word_t>& m_out,
         ap_uint<1>& halted, ap_uint<8>& error, ap_uint<16>& tx_id);
