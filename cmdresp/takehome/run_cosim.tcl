# run_cosim.tcl -- C synthesis, then one RTL co-simulation run over every scenario in
# data/scenarios_cosim.txt.  Copies the reports it is cited by into reports/.
#
#   vitis-run --mode hls --tcl run_cosim.tcl
set here [file dirname [file normalize [info script]]]
open_project -reset prj_fir_cosim
set_top fir
add_files $here/kernel.cpp
add_files -tb $here/tb.cpp
open_solution -reset solution1
set_part {xc7z020clg484-1}
create_clock -period 10
if {[catch {csynth_design} res]} {
    puts "CSYNTH_ERROR: $res"
    exit 1
}
set sol [file join [pwd] prj_fir_cosim solution1]
file mkdir $here/reports
file copy -force $sol/syn/report/fir_csynth.rpt $here/reports/fir_csynth.rpt
file copy -force $sol/syn/report/csynth.xml $here/reports/csynth.xml
if {[catch {cosim_design -argv "$here/data cosim"} res]} {
    puts "COSIM_ERROR: $res"
    exit 1
}
file copy -force $sol/sim/report/fir_cosim.rpt $here/reports/fir_cosim.rpt
foreach f [glob -nocomplain $sol/sim/verilog/*.transaction.xml $sol/sim/verilog/*.performance.result*.xml] {
    file copy -force $f $here/reports/
}
puts "COSIM_DONE"
exit 0
