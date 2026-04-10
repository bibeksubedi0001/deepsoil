"""
Earthquake file formatter — parses various input formats and writes
single-column .acc files + .meta files for OpenSees.
Ported from start_again/format_earthquakes.py.
"""

import os
import re

G_ACC = 9.80665  # m/s^2


def parse_earthquake_file(filepath: str):
    """
    Auto-detect earthquake file format and return (times, accels_g, dt, npts, source_info).
    Supports:
      1. Two-column (time, accel_g) with 'NPTS DT' header
      2. Hokkaido-TU multi-component format (Gorkha-style)
      3. Simple two-column (time, accel) in g or cm/s^2
    """
    with open(filepath, "r") as f:
        raw_lines = f.readlines()

    lines = [l.rstrip() for l in raw_lines]
    fname = os.path.basename(filepath)

    # Try format 1: first line has NPTS and DT
    first_line = lines[0].strip()
    parts = first_line.split()

    if len(parts) == 2:
        try:
            npts = int(parts[0])
            dt = float(parts[1])
            data_lines = lines[1:]
            accels = []
            times = []
            for line in data_lines:
                line = line.strip()
                if not line:
                    continue
                vals = line.split()
                if len(vals) >= 2:
                    times.append(float(vals[0]))
                    accels.append(float(vals[1]))
                elif len(vals) == 1:
                    accels.append(float(vals[0]))

            if not times:
                times = [i * dt for i in range(len(accels))]

            return times, accels, dt, len(accels), fname
        except (ValueError, IndexError):
            pass

    # Try format 2: Hokkaido-TU / Gorkha multi-component
    header_count = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            header_count = i + 1
            continue
        vals = stripped.split()
        try:
            [float(v) for v in vals]
            break
        except ValueError:
            header_count = i + 1

    data_lines = lines[header_count:]
    times = []
    accels = []
    for line in data_lines:
        line = line.strip()
        if not line:
            continue
        vals = line.split()
        try:
            if len(vals) >= 2:
                times.append(float(vals[0]))
                accels.append(float(vals[1]))
            elif len(vals) == 1:
                accels.append(float(vals[0]))
        except ValueError:
            continue

    if not accels:
        raise ValueError(f"Could not parse any acceleration data from {filepath}")

    if len(times) >= 2:
        dt = times[1] - times[0]
    else:
        dt = 0.01

    if not times:
        times = [i * dt for i in range(len(accels))]

    # Detect units: if max accel > 50, likely cm/s^2; if > 2.0, likely m/s^2
    max_val = max(abs(a) for a in accels)
    if max_val > 50:
        accels = [a / (G_ACC * 100.0) for a in accels]
    elif max_val > 2.0:
        accels = [a / G_ACC for a in accels]

    return times, accels, dt, len(accels), fname


def write_acc_and_meta(accels_g, dt, npts, source_name, output_dir):
    """Write .acc (single-column g) and .meta files. Returns (acc_path, meta_path)."""
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(source_name)[0]
    base = re.sub(r'[^\w\-.]', '_', base)

    acc_path = os.path.join(output_dir, f"{base}.acc")
    meta_path = os.path.join(output_dir, f"{base}.meta")

    pga = max(abs(a) for a in accels_g)

    with open(acc_path, "w") as f:
        for a in accels_g:
            f.write(f"{a:.10e}\n")

    with open(meta_path, "w") as f:
        f.write(f"NPTS {npts}\n")
        f.write(f"DT {dt:.6f}\n")
        f.write(f"UNITS g\n")
        f.write(f"PGA {pga:.6f}\n")
        f.write(f"SOURCE {source_name}\n")

    return acc_path, meta_path


def format_earthquake(filepath: str, output_dir: str):
    """Parse an earthquake file and write .acc + .meta. Returns metadata dict."""
    times, accels_g, dt, npts, source = parse_earthquake_file(filepath)
    acc_path, meta_path = write_acc_and_meta(accels_g, dt, npts, source, output_dir)
    pga = max(abs(a) for a in accels_g)
    return {
        "acc_file": acc_path,
        "meta_file": meta_path,
        "dt": dt,
        "npts": npts,
        "pga_g": pga,
        "source": source,
        "duration": dt * (npts - 1),
    }
