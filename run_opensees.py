#!/usr/bin/env python3
"""
Wrapper to run OpenSees TCL scripts via openseespy.
Used on Linux/Docker where standalone OpenSees binary is unavailable.
Usage: python run_opensees.py <tcl_script> <arg1> <arg2> ...
"""
import sys
import os


def run_tcl(tcl_script, args):
    import openseespy.opensees as ops

    # Set argc/argv TCL variables so the script can read them
    ops.eval(f"set argc {len(args)}")
    argv_list = " ".join(f"{{{a}}}" for a in args)
    ops.eval(f"set argv [list {argv_list}]")

    # Source the TCL script
    tcl_script_abs = os.path.abspath(tcl_script).replace("\\", "/")
    ops.eval(f"source {{{tcl_script_abs}}}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_opensees.py <script.tcl> [args...]")
        sys.exit(1)
    run_tcl(sys.argv[1], sys.argv[2:])
