// kernel.hpp -- the clamp-and-count kernel's interface.
#pragma once

#include <ap_axi_sdata.h>
#include <ap_int.h>
#include <hls_stream.h>

typedef ap_axiu<32, 0, 0, 0> word_t;

static const int HDR_WORDS = 4;

// cmd_type, header word 0 bits [3:0]
enum { CMD_CONFIG = 0, CMD_DATA = 1, CMD_COUNT = 2, CMD_END = 3 };

// The error register
enum {
    ERR_NONE = 0,
    ERR_NO_CONFIG = 1,     // DATA or COUNT before any CONFIG
    ERR_BAD_BOUNDS = 2,    // CONFIG with lo > hi, or lo or hi NaN
    ERR_UNKNOWN_CMD = 3,   // cmd_type not CONFIG, DATA, COUNT or END
    ERR_TLAST_EARLY = 4,   // sample burst's TLAST before sample nsamp
    ERR_NO_TLAST = 5,      // last sample without TLAST
};

void clampcount(hls::stream<word_t>& s_in, hls::stream<word_t>& m_out,
                ap_uint<1>& halted, ap_uint<8>& error, ap_uint<16>& tx_id);
