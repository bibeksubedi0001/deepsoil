"""
1D Site Response Analysis Web Application
Flask app for running OpenSees nonlinear site response analyses.
"""

import os
import json
import re
import uuid
import threading
from flask import (Flask, render_template, request, jsonify,
                   send_from_directory)
from werkzeug.utils import secure_filename

import csv
import io

from soil_params import compute_layer_params, classify_soil
from earthquake_fmt import format_earthquake, parse_earthquake_file
from analysis import run_analysis

app = Flask(__name__)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
RUNS_DIR = os.path.join(DATA_DIR, "runs")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RUNS_DIR, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

ALLOWED_EXTENSIONS = {"txt", "acc", "csv", "l", "dat"}

run_status = {}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/live")
def live_earthquakes():
    return render_template("live.html")


@app.route("/api/compute-params", methods=["POST"])
def compute_params():
    data = request.get_json()
    if not data or "layers" not in data:
        return jsonify({"error": "No layer data provided"}), 400

    results = []
    cumulative_depth = 0.0
    for layer in data["layers"]:
        try:
            thickness = float(layer.get("thickness", 1.0))
            density = float(layer.get("mass_density", 1800))
            vs = float(layer.get("Vs", 150))
            soil_type = layer.get("soil_type", "sand").lower().strip()
            spt_n = float(layer.get("spt_n", 15))
            depth_to_mid = cumulative_depth + thickness / 2.0

            params = compute_layer_params(thickness, density, vs, soil_type,
                                          spt_n, depth_to_mid)
            cumulative_depth += thickness
            results.append(params)
        except (ValueError, KeyError) as e:
            return jsonify({"error": f"Invalid layer data: {e}"}), 400

    return jsonify({"layers": results, "total_depth": cumulative_depth})


@app.route("/api/upload-soil-csv", methods=["POST"])
def upload_soil_csv():
    """Parse a soil profile CSV (same format as opensees_input/soil_profiles/).
    Returns rows with thickness, mass_density, Vs, soil_type for the UI table."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    raw = file.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))

    layers = []
    for row in reader:
        try:
            thickness = float(row.get("thickness", 0))
            density = float(row.get("mass_density", 1800))
            vs = float(row.get("Vs", 150))
            soil_type = row.get("soil_type", "sand").strip().lower()
            if soil_type not in ("sand", "silt", "clay"):
                soil_type = "sand"
            unit_weight = round(density * 9.81 / 1000, 2)
            layers.append({
                "soil_type": soil_type,
                "thickness": round(thickness, 2),
                "mass_density": round(density),
                "unit_weight": unit_weight,
                "Vs": round(vs, 2),
            })
        except (ValueError, TypeError):
            continue

    if not layers:
        return jsonify({"error": "No valid layers found in CSV"}), 400

    # Derive borehole name from filename
    name = os.path.splitext(secure_filename(file.filename))[0]

    total_depth = sum(l["thickness"] for l in layers)
    return jsonify({
        "borehole_name": name,
        "layers": layers,
        "total_depth": round(total_depth, 2),
        "count": len(layers),
    })


@app.route("/api/upload-earthquake", methods=["POST"])
def upload_earthquake():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": f"File type not allowed. Use: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_DIR, filename)
    file.save(filepath)

    try:
        times, accels_g, dt, npts, source = parse_earthquake_file(filepath)
        pga = max(abs(a) for a in accels_g)
        duration = dt * (npts - 1)

        step = max(1, len(times) // 2000)
        preview_time = times[::step]
        preview_accel = accels_g[::step]

        return jsonify({
            "filename": filename,
            "filepath": filepath,
            "dt": round(dt, 6),
            "npts": npts,
            "pga_g": round(pga, 6),
            "duration": round(duration, 2),
            "preview": {
                "time": preview_time,
                "accel": preview_accel,
            }
        })
    except Exception as e:
        return jsonify({"error": f"Failed to parse earthquake file: {e}"}), 400


@app.route("/api/run", methods=["POST"])
def start_run():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    layers = data.get("layers")
    eq_file = data.get("earthquake_file")

    if not layers or len(layers) == 0:
        return jsonify({"error": "No soil layers provided"}), 400
    if not eq_file:
        return jsonify({"error": "No earthquake file specified"}), 400

    eq_path = os.path.join(UPLOAD_DIR, secure_filename(eq_file))
    if not os.path.exists(eq_path):
        return jsonify({"error": f"Earthquake file not found: {eq_file}"}), 400

    borehole_name = data.get("borehole_name", "").strip()
    if borehole_name:
        # Sanitise: keep alphanumeric, dash, underscore; collapse whitespace
        safe_name = re.sub(r'[^\w\-]', '_', borehole_name)
        safe_name = re.sub(r'_+', '_', safe_name).strip('_')
        if not safe_name:
            safe_name = str(uuid.uuid4())[:8]
        # Avoid overwriting a previous run with the same name
        run_id = safe_name
        counter = 2
        while run_id in run_status or os.path.exists(os.path.join(DATA_DIR, "runs", run_id)):
            run_id = f"{safe_name}_{counter}"
            counter += 1
    else:
        run_id = str(uuid.uuid4())[:8]
    run_status[run_id] = {"status": "queued", "progress": "Initializing..."}

    def run_in_background():
        run_status[run_id] = {"status": "running", "progress": "Running OpenSees analysis..."}
        try:
            result = run_analysis(run_id, layers, eq_path, DATA_DIR,
                                   borehole_name=borehole_name or run_id)
            run_status[run_id] = {
                "status": result.get("status", "error"),
                "progress": "Completed" if result.get("status") == "completed" else result.get("error", "Unknown error"),
                "result": result,
            }
        except Exception as e:
            run_status[run_id] = {"status": "error", "progress": str(e)}

    thread = threading.Thread(target=run_in_background, daemon=True)
    thread.start()

    return jsonify({"run_id": run_id, "status": "queued"})


@app.route("/api/status/<run_id>")
def get_status(run_id):
    if run_id not in run_status:
        return jsonify({"error": "Run not found"}), 404
    return jsonify(run_status[run_id])


@app.route("/api/results/<run_id>")
def get_results(run_id):
    if run_id not in run_status:
        return jsonify({"error": "Run not found"}), 404

    status = run_status[run_id]
    if status["status"] != "completed":
        return jsonify({"error": "Analysis not yet completed",
                        "status": status["status"]}), 400

    return jsonify(status.get("result", {}))


@app.route("/api/results/<run_id>/figure/<filename>")
def get_figure(run_id, filename):
    filename = secure_filename(filename)
    result_dir = os.path.join(RUNS_DIR, run_id, "output")
    if not os.path.exists(os.path.join(result_dir, filename)):
        return jsonify({"error": "Figure not found"}), 404
    return send_from_directory(result_dir, filename)


@app.route("/api/results/<run_id>/csv/<filename>")
def get_csv(run_id, filename):
    filename = secure_filename(filename)
    result_dir = os.path.join(RUNS_DIR, run_id, "output")
    if not os.path.exists(os.path.join(result_dir, filename)):
        return jsonify({"error": "CSV not found"}), 404
    return send_from_directory(result_dir, filename, as_attachment=True)


if __name__ == "__main__":
    print("=" * 60)
    print("  1D Site Response Analysis Application")
    print("  Open http://127.0.0.1:5000 in your browser")
    print("=" * 60)
    app.run(debug=True, port=5000)
