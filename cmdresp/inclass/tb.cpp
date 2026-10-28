// tb.cpp -- the C++ testbench: one kernel activation per scenario, on fresh streams.
//
// Usage: tb <data_dir> <stage>
//
// For each scenario in <data_dir>/scenarios.txt:
//   1. fill the status registers with garbage, so a kernel that does not clear them fails;
//   2. play <scenario>/in.txt into a fresh input stream and call the kernel once;
//   3. record the output to <scenario>/<stage>_out.txt and the status to <stage>_status.json;
//   4. drop whatever the kernel left unread: the host resetting the stream path.
#include "kernel.hpp"

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: tb <data_dir> <stage>\n");
        return 2;
    }
    const std::string root = argv[1], stage = argv[2];
    std::ifstream list(root + "/scenarios.txt");
    std::string name;
    int run = 0;
    while (list >> name) {
        const std::string dir = root + "/" + name;
        hls::stream<word_t> s_in, m_out;
        std::ifstream in(dir + "/in.txt");
        std::string hex;
        int last;
        while (in >> hex >> last) {
            word_t w;
            w.data = (unsigned int)std::stoul(hex, nullptr, 16);
            w.keep = -1;
            w.strb = -1;
            w.last = last;
            s_in.write(w);
        }

        ap_uint<1> halted = 1;
        ap_uint<8> error = 0xEE;
        ap_uint<16> tx_id = 0xFFFF;
        clampcount(s_in, m_out, halted, error, tx_id);

        std::ofstream out(dir + "/" + stage + "_out.txt");
        int nout = 0;
        while (!m_out.empty()) {
            word_t w = m_out.read();
            char buf[32];
            std::snprintf(buf, sizeof buf, "%08x %d\n", (unsigned int)w.data, (int)w.last);
            out << buf;
            ++nout;
        }
        std::ofstream st(dir + "/" + stage + "_status.json");
        st << "{\"halted\": " << (int)halted << ", \"error\": " << (int)error
           << ", \"tx_id\": " << (int)tx_id << "}\n";

        int dropped = 0;
        while (!s_in.empty()) { s_in.read(); ++dropped; }
        std::printf("scenario %-24s halted=%d error=%d tx_id=%d out_words=%d unread_words=%d\n",
                    name.c_str(), (int)halted, (int)error, (int)tx_id, nout, dropped);
        ++run;
    }
    if (run == 0) {
        std::fprintf(stderr, "no scenario ran (data_dir=%s)\n", root.c_str());
        return 1;
    }
    return 0;
}
