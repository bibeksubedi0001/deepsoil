"""
OpenSees analysis runner — creates soil profile CSV, formats earthquake,
invokes OpenSees, and post-processes results.
"""

import os
import subprocess
import shutil

from soil_params import build_soil_csv
from earthquake_fmt import format_earthquake
from postprocess import generate_all_outputs


def find_opensees():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_dir = os.path.dirname(os.path.abspath(__file__))
    # Windows local
    local_bin = os.path.join(base, "OpenSees3.8.0", "bin", "OpenSees.exe")
    if os.path.exists(local_bin):
        return local_bin
    # Linux binary bundled in app dir (Docker)
    linux_bin = os.path.join(app_dir, "opensees_bin", "OpenSees")
    if os.path.exists(linux_bin):
        return linux_bin
    # Docker installed location
    docker_bin = "/opt/opensees/bin/OpenSees"
    if os.path.exists(docker_bin):
        return docker_bin
    if shutil.which("opensees"):
        return shutil.which("opensees")
    if shutil.which("OpenSees"):
        return shutil.which("OpenSees")
    return None


def get_tcl_script():
    app_dir = os.path.dirname(os.path.abspath(__file__))
    base = os.path.dirname(app_dir)
    # Check app directory first (for deployment)
    app_tcl = os.path.join(app_dir, "SiteResponse1D.tcl")
    if os.path.exists(app_tcl):
        return app_tcl
    tcl_path = os.path.join(base, "SiteResponse1D.tcl")
    if os.path.exists(tcl_path):
        return tcl_path
    alt = os.path.join(base, "start_again", "SiteResponse1D.tcl")
    if os.path.exists(alt):
        return alt
    return None


def run_analysis(run_id: str, soil_layers: list, earthquake_path: str,
                 data_dir: str, borehole_name: str = ""):
    """
    Execute full analysis pipeline:
      1. Generate soil CSV from layer data
      2. Format earthquake file
      3. Run OpenSees
      4. Post-process results (with relative->absolute correction)
    """
    run_dir = os.path.join(data_dir, "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)

    result = {"run_id": run_id, "status": "running", "steps": []}

    # Step 1: Generate soil CSV
    try:
        soil_csv_content = build_soil_csv(soil_layers)
        soil_csv_path = os.path.join(run_dir, "soil_profile.csv")
        with open(soil_csv_path, "w") as f:
            f.write(soil_csv_content)
        result["steps"].append({"step": "soil_profile", "status": "ok",
                                "file": soil_csv_path})
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"Soil profile generation failed: {e}"
        return result

    # Step 2: Format earthquake
    try:
        eq_info = format_earthquake(earthquake_path, run_dir)
        result["steps"].append({"step": "earthquake", "status": "ok",
                                "info": eq_info})
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"Earthquake formatting failed: {e}"
        return result

    # Step 3: Run OpenSees
    opensees_exe = find_opensees()
    tcl_script = get_tcl_script()

    if not opensees_exe:
        result["status"] = "error"
        result["error"] = ("OpenSees executable not found. "
                           "Place OpenSees.exe in OpenSees3.8.0/bin/ or add to PATH.")
        return result

    if not tcl_script:
        result["status"] = "error"
        result["error"] = "SiteResponse1D.tcl not found"
        return result

    output_dir = os.path.join(run_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    acc_file = eq_info["acc_file"]
    meta_file = eq_info["meta_file"]
    dt = eq_info["dt"]
    npts = eq_info["npts"]

    cmd = [
        opensees_exe,
        tcl_script,
        soil_csv_path,
        acc_file,
        str(dt),
        str(npts),
        output_dir,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=os.path.dirname(tcl_script),
        )
        result["steps"].append({
            "step": "opensees",
            "status": "ok" if proc.returncode == 0 else "warning",
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-2000:] if proc.stdout else "",
            "stderr_tail": proc.stderr[-1000:] if proc.stderr else "",
        })

        with open(os.path.join(run_dir, "opensees_stdout.txt"), "w") as f:
            f.write(proc.stdout or "")
        with open(os.path.join(run_dir, "opensees_stderr.txt"), "w") as f:
            f.write(proc.stderr or "")

    except subprocess.TimeoutExpired:
        result["status"] = "error"
        result["error"] = "OpenSees analysis timed out (>10 minutes)"
        return result
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"OpenSees execution failed: {e}"
        return result

    # Step 4: Post-process with relative->absolute correction
    try:
        label = borehole_name if borehole_name else f"Run {run_id}"
        pp_result = generate_all_outputs(output_dir, acc_file, meta_file, label)
        if "error" in pp_result:
            result["status"] = "error"
            result["error"] = pp_result["error"]
            return result
        result["results"] = pp_result
        result["steps"].append({"step": "postprocess", "status": "ok"})
        result["status"] = "completed"
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"Post-processing failed: {e}"
        return result

    return result
