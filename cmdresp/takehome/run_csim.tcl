# run_csim.tcl -- C simulation of the FIR kernel, every scenario in data/scenarios_csim.txt.
#
#   vitis-run --mode hls --tcl run_csim.tcl
#
# Part and clock as in the Waveflow stream_inband example.
set here [file dirname [file normalize [info script]]]
open_project -reset prj_fir
set_top fir
add_files $here/kernel.cpp
add_files -tb $here/tb.cpp
open_solution -reset solution1
set_part {xc7z020clg484-1}
create_clock -period 10
if {[catch {csim_design -argv "$here/data csim"} res]} {
    puts "CSIM_ERROR: $res"
    exit 1
}
puts "CSIM_DONE"
exit 0
