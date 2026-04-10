"""
Post-processing module for OpenSees site response results.
Generates figures and CSV data from OpenSees output files.

CRITICAL: OpenSees UniformExcitation causes Node recorders to output
RELATIVE accelerations (relative to the moving base frame).
To get absolute acceleration: abs = relative + input_motion
This matches start_again/postprocess_result.py logic.
"""

import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

G = 9.81

C_SURFACE = "#1f77b4"
C_BEDROCK = "#d62728"
C_PROFILE = "#2ca02c"
C_STRAIN  = "#ff7f0e"
C_HYST    = "#7f7f7f"

plt.rcParams.update({
    "figure.dpi":          150,
    "savefig.dpi":         300,
    "savefig.bbox":        "tight",
    "font.family":         "serif",
    "font.size":           11,
    "axes.titlesize":      13,
    "axes.titleweight":    "bold",
    "axes.labelsize":      12,
    "axes.linewidth":      0.8,
    "axes.grid":           True,
    "grid.alpha":          0.35,
    "grid.linewidth":      0.5,
    "grid.linestyle":      "--",
    "legend.fontsize":     10,
    "legend.framealpha":   0.9,
    "legend.edgecolor":    "0.7",
    "xtick.labelsize":     10,
    "ytick.labelsize":     10,
    "lines.linewidth":     1.2,
})


def read_time_history(filepath):
    """Read an OpenSees recorder file. Returns (time, data_2d)."""
    data = np.loadtxt(filepath)
    if data.ndim == 1:
        return None, data
    return data[:, 0], data[:, 1:]


def parse_model_info(result_dir):
    info = {}
    node_elevs = {}
    elem_info = []
    fpath = os.path.join(result_dir, "model_info.txt")
    if not os.path.exists(fpath):
        return info, node_elevs, elem_info
    with open(fpath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            key = parts[0]
            if key == "node_elev":
                node_elevs[int(parts[1])] = float(parts[2])
            elif key == "elem_info":
                tag = int(parts[1])
                props = {}
                for p in parts[2:]:
                    k, v = p.split("=")
                    props[k] = v
                props["tag"] = tag
                elem_info.append(props)
            else:
                val = parts[1] if len(parts) > 1 else ""
                try:
                    val = float(val)
                except ValueError:
                    pass
                info[key] = val
    return info, node_elevs, elem_info


def load_input_motion(acc_file, meta_file):
    """Load input motion from .acc and .meta files.
    Returns (t_array, accel_m_s2, pga_g, dt)."""
    acc_g = np.loadtxt(acc_file)  # single-column, in g

    # Parse meta
    meta = {}
    with open(meta_file) as f:
        for line in f:
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                meta[parts[0]] = parts[1]

    dt = float(meta.get("DT", 0.01))
    pga_g = float(meta.get("PGA", np.max(np.abs(acc_g))))

    t = np.arange(len(acc_g)) * dt
    return t, acc_g * G, pga_g, dt


def compute_response_spectrum(accel, dt, damping=0.05, periods=None):
    """Newmark average-acceleration SDOF response spectrum.
    Total-displacement formulation (verified correct)."""
    if periods is None:
        periods = np.sort(np.unique(np.concatenate([
            np.arange(0.01, 0.10, 0.005),
            np.arange(0.10, 0.50, 0.01),
            np.arange(0.50, 1.00, 0.02),
            np.arange(1.00, 3.00, 0.05),
            np.arange(3.00, 10.01, 0.1),
        ])))

    n = len(accel)
    nT = len(periods)
    gamma, beta = 0.5, 0.25
    omega = 2.0 * np.pi / np.maximum(periods, 1e-12)
    k_vec = omega ** 2
    c_vec = 2.0 * damping * omega

    a1 = 1.0 / (beta * dt**2)
    a4 = gamma / (beta * dt)
    k_eff = k_vec + a1 + a4 * c_vec

    b1 = a1
    b2 = 1.0 / (beta * dt)
    b3 = 1.0 / (2.0 * beta) - 1.0
    b4 = a4
    b5 = gamma / beta - 1.0
    b6 = dt * (gamma / (2.0 * beta) - 1.0)

    u = np.zeros(nT)
    v = np.zeros(nT)
    a_r = np.zeros(nT)
    peak = np.zeros(nT)

    for j in range(1, n):
        p_eff = -accel[j] \
              + (b1 * u + b2 * v + b3 * a_r) \
              + c_vec * (b4 * u + b5 * v + b6 * a_r)
        u_new = p_eff / k_eff
        a_new = b1 * (u_new - u) - b2 * v - b3 * a_r
        v_new = v + dt * ((1.0 - gamma) * a_r + gamma * a_new)
        u[:] = u_new
        v[:] = v_new
        a_r[:] = a_new
        tot = np.abs(a_r + accel[j])
        peak = np.maximum(peak, tot)

    Sa = np.where(np.isfinite(peak), peak, np.max(np.abs(accel)))
    return periods, Sa


def generate_all_outputs(result_dir, acc_file, meta_file, label="Analysis"):
    """
    Full post-processing pipeline.

    CRITICAL: Converts relative recorder output to absolute acceleration
    by adding the input motion time series. Without this, PGA and
    amplification ratios are completely wrong.

    Parameters
    ----------
    result_dir : str - path to OpenSees output directory
    acc_file   : str - path to the input .acc file (single-column, g)
    meta_file  : str - path to the input .meta file
    label      : str - label for figure titles
    """
    acc_surf_file = os.path.join(result_dir, "acc_surface.out")
    acc_base_file = os.path.join(result_dir, "acc_base.out")

    if not os.path.exists(acc_surf_file):
        return {"error": f"No acc_surface.out found in {result_dir}"}

    info, node_elevs, elem_info = parse_model_info(result_dir)

    # Read RELATIVE accelerations from OpenSees recorders
    time_s, acc_surf_rel = read_time_history(acc_surf_file)
    if acc_surf_rel.ndim > 1:
        acc_surf_rel = acc_surf_rel[:, 0]

    _, acc_base_rel = read_time_history(acc_base_file)
    if acc_base_rel.ndim > 1:
        acc_base_rel = acc_base_rel[:, 0]

    dt = float(time_s[1] - time_s[0]) if (time_s is not None and len(time_s) > 1) else 0.01

    # Load input motion and convert relative -> ABSOLUTE acceleration
    t_input, input_acc_ms2, input_pga_g, input_dt = load_input_motion(acc_file, meta_file)

    # Interpolate input motion to recorder time steps
    input_at_rec = np.interp(time_s, t_input, input_acc_ms2)

    # ABSOLUTE = RELATIVE + INPUT
    acc_surf = acc_surf_rel + input_at_rec
    acc_base = acc_base_rel + input_at_rec

    # PGA from absolute acceleration
    pga_surf = np.max(np.abs(acc_surf))

    # For amplification: use INPUT outcrop PGA, not base node PGA
    # With compliant dashpot base, base node PGA != input outcrop PGA
    pga_input = input_pga_g * G  # convert g to m/s^2
    pga_amp = pga_surf / pga_input if pga_input > 0 else float("inf")

    # Response spectra: surface uses absolute acceleration,
    # bedrock uses the raw input motion directly
    periods, Sa_surf = compute_response_spectrum(acc_surf, dt)
    _, Sa_base = compute_response_spectrum(input_acc_ms2, input_dt)
    amp_factor = np.where(Sa_base > 1e-12, Sa_surf / Sa_base, 1.0)

    # Save CSVs
    np.savetxt(os.path.join(result_dir, "response_spectrum_surface.csv"),
               np.column_stack([periods, Sa_surf / G]),
               header="Period(s),Sa_surface(g)", fmt="%.6f", delimiter=",", comments="")
    np.savetxt(os.path.join(result_dir, "response_spectrum_base.csv"),
               np.column_stack([periods, Sa_base / G]),
               header="Period(s),Sa_base(g)", fmt="%.6f", delimiter=",", comments="")
    np.savetxt(os.path.join(result_dir, "amplification_factor.csv"),
               np.column_stack([periods, amp_factor]),
               header="Period(s),Amp_Factor", fmt="%.6f", delimiter=",", comments="")

    summary = {
        "pga_surface_g": round(float(pga_surf / G), 6),
        "pga_input_g": round(float(input_pga_g), 6),
        "pga_amplification": round(float(pga_amp), 4),
        "max_Sa_surface_g": round(float(np.max(Sa_surf) / G), 6),
        "period_max_Sa": round(float(periods[np.argmax(Sa_surf)]), 4),
        "total_depth": float(info.get("total_depth", 0)),
        "Vs_avg": float(info.get("Vs_avg", 0)),
        "num_layers": int(info.get("num_layers", 0)),
        "num_elements": int(info.get("num_elements", 0)),
    }

    figures = []

    # Figure 1: Acceleration time histories (absolute)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5.5), sharex=True)
    ax1.plot(time_s, acc_surf / G, color=C_SURFACE, linewidth=0.35, label="Surface", rasterized=True)
    ax1.set_ylabel("Acceleration (g)")
    ax1.set_title(f"Acceleration Time History \u2014 {label}")
    ax1.legend(loc="upper right")
    ax1.annotate(f"PGA = {pga_surf/G:.4f} g", xy=(0.02, 0.92), xycoords="axes fraction",
                 fontsize=9, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="0.7"))
    ax2.plot(time_s, acc_base / G, color=C_BEDROCK, linewidth=0.35, label="Bedrock", rasterized=True)
    ax2.set_ylabel("Acceleration (g)")
    ax2.set_xlabel("Time (s)")
    ax2.legend(loc="upper right")
    ax2.annotate(f"PGA input = {input_pga_g:.4f} g", xy=(0.02, 0.92), xycoords="axes fraction",
                 fontsize=9, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="0.7"))
    plt.tight_layout(h_pad=0.4)
    fig1_path = os.path.join(result_dir, "Fig1_Acceleration_TimeHistory.png")
    fig.savefig(fig1_path)
    plt.close(fig)
    figures.append(fig1_path)

    # Figure 2: Response spectra
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.plot(periods, Sa_surf / G, color=C_SURFACE, linewidth=1.4, label="Surface", zorder=3)
    ax.plot(periods, Sa_base / G, color=C_BEDROCK, linewidth=1.2, linestyle="--", label="Input Motion", zorder=2)
    ax.fill_between(periods, Sa_base / G, Sa_surf / G,
                    where=(Sa_surf > Sa_base), color=C_SURFACE, alpha=0.08, interpolate=True)
    ax.set_xscale("log")
    ax.set_xlabel("Period (s)")
    ax.set_ylabel("Spectral Acceleration, Sa (g)")
    ax.set_title(f"Response Spectra (5% Damping) \u2014 {label}")
    ax.legend(loc="upper right")
    ax.set_xlim(0.01, 10.0)
    ax.set_xticks([0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10])
    ax.get_xaxis().set_major_formatter(ticker.ScalarFormatter())
    plt.tight_layout()
    fig2_path = os.path.join(result_dir, "Fig2_ResponseSpectra.png")
    fig.savefig(fig2_path)
    plt.close(fig)
    figures.append(fig2_path)

    # Figure 3: Depth profiles (absolute acceleration)
    total_depth = info.get("total_depth", 0)
    if total_depth == 0 and node_elevs:
        total_depth = max(node_elevs.values())

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 7), sharey=True)

    acc_all_file = os.path.join(result_dir, "acc_all_nodes.out")
    if os.path.exists(acc_all_file) and node_elevs:
        raw = np.loadtxt(acc_all_file)
        rec_time = raw[:, 0]
        acc_all_rel = raw[:, 1:]

        # Add input motion to get ABSOLUTE acceleration at each depth
        input_at_depth = np.interp(rec_time, t_input, input_acc_ms2)

        if acc_all_rel.ndim == 1:
            acc_all_rel = acc_all_rel.reshape(-1, 1)
        sorted_nodes = sorted(node_elevs.items(), key=lambda x: x[1])
        pga_depths, pga_values = [], []
        for col_idx in range(min(len(sorted_nodes), acc_all_rel.shape[1])):
            nTag, elev = sorted_nodes[col_idx]
            depth = total_depth - elev
            abs_acc = acc_all_rel[:, col_idx] + input_at_depth
            pga_g = np.max(np.abs(abs_acc)) / G
            pga_depths.append(depth)
            pga_values.append(pga_g)
        ax1.plot(pga_values, pga_depths, "o-", color=C_PROFILE, markersize=3,
                 linewidth=1.2, markerfacecolor="white", markeredgewidth=0.8)

    ax1.set_xlabel("Peak Ground Acceleration (g)")
    ax1.set_ylabel("Depth (m)")
    ax1.set_title("PGA Depth Profile")
    ax1.invert_yaxis()
    if total_depth:
        ax1.set_ylim(total_depth, 0)

    strain_file = os.path.join(result_dir, "strain.out")
    if os.path.exists(strain_file) and elem_info:
        _, strain_all = read_time_history(strain_file)
        if strain_all.ndim == 1:
            strain_all = strain_all.reshape(-1, 1)
        strain_depths, strain_vals = [], []
        for ei in range(len(elem_info)):
            col_xy = ei * 3 + 2
            if col_xy >= strain_all.shape[1]:
                break
            gamma_xy = strain_all[:, col_xy]
            max_strain_pct = np.max(np.abs(gamma_xy)) * 100.0
            e_thick = float(elem_info[ei].get("thick", 1.0))
            cum = sum(float(elem_info[k].get("thick", 1.0)) for k in range(ei)) + e_thick / 2.0
            depth = total_depth - cum
            strain_depths.append(depth)
            strain_vals.append(max_strain_pct)
        ax2.plot(strain_vals, strain_depths, "s-", color=C_STRAIN, markersize=3,
                 linewidth=1.2, markerfacecolor="white", markeredgewidth=0.8)

    ax2.set_xlabel("Maximum Shear Strain (%)")
    ax2.set_title("Max Shear Strain Depth Profile")
    ax2.invert_yaxis()
    if total_depth:
        ax2.set_ylim(total_depth, 0)

    fig.suptitle(f"Depth Profiles \u2014 {label}", fontsize=13, fontweight="bold", y=1.01)
    plt.tight_layout()
    fig3_path = os.path.join(result_dir, "Fig3_DepthProfiles.png")
    fig.savefig(fig3_path)
    plt.close(fig)
    figures.append(fig3_path)

    # Figure 4: Hysteresis
    stress_file = os.path.join(result_dir, "stress.out")
    if os.path.exists(stress_file) and os.path.exists(strain_file) and elem_info:
        _, stress_all = read_time_history(stress_file)
        _, strain_all2 = read_time_history(strain_file)
        if stress_all.ndim == 1:
            stress_all = stress_all.reshape(-1, 1)
        if strain_all2.ndim == 1:
            strain_all2 = strain_all2.reshape(-1, 1)

        n_elems = len(elem_info)
        idx_top = n_elems - 1
        idx_mid = n_elems // 2
        idx_bot = 0

        fig, ax = plt.subplots(figsize=(8, 6))

        # Background: surface and base elements
        for ei, lbl, c, a in [(idx_top, "Near-surface element", C_SURFACE, 0.25),
                               (idx_bot, "Base element", C_BEDROCK, 0.25)]:
            col_stress_xy = ei * 5 + 3
            col_strain_xy = ei * 3 + 2
            if col_stress_xy < stress_all.shape[1] and col_strain_xy < strain_all2.shape[1]:
                tau = stress_all[:, col_stress_xy] / 1000.0
                gam = strain_all2[:, col_strain_xy] * 100.0
                ax.plot(gam, tau, color=c, alpha=a, linewidth=0.4, label=lbl, rasterized=True)

        # Primary: mid-depth element
        col_stress_xy = idx_mid * 5 + 3
        col_strain_xy = idx_mid * 3 + 2
        if col_stress_xy < stress_all.shape[1] and col_strain_xy < strain_all2.shape[1]:
            tau = stress_all[:, col_stress_xy] / 1000.0
            gam = strain_all2[:, col_strain_xy] * 100.0
            mid_e = elem_info[idx_mid]
            mid_lbl = f"Mid-depth (Elem {mid_e['tag']}, {mid_e.get('type','?')})"
            ax.plot(gam, tau, color=C_HYST, linewidth=0.6, label=mid_lbl, rasterized=True, zorder=3)

        ax.set_xlabel("Shear Strain, \u03b3 (%)")
        ax.set_ylabel("Shear Stress, \u03c4 (kPa)")
        ax.set_title(f"Stress\u2013Strain Hysteresis \u2014 {label}")
        ax.legend(loc="upper left", fontsize=9)
        ax.axhline(0, color="k", linewidth=0.4)
        ax.axvline(0, color="k", linewidth=0.4)
        plt.tight_layout()
        fig4_path = os.path.join(result_dir, "Fig4_Hysteresis.png")
        fig.savefig(fig4_path)
        plt.close(fig)
        figures.append(fig4_path)

    # Downsampled data for interactive web charts
    n_total = len(time_s)
    step = max(1, n_total // 2000)
    time_hist = {
        "time": time_s[::step].tolist(),
        "acc_surface": (acc_surf[::step] / G).tolist(),
        "acc_base": (acc_base[::step] / G).tolist(),
    }

    spectra_data = {
        "periods": periods.tolist(),
        "Sa_surface": (Sa_surf / G).tolist(),
        "Sa_base": (Sa_base / G).tolist(),
        "amplification": amp_factor.tolist(),
    }

    summary["figures"] = [os.path.basename(f) for f in figures]
    summary["time_history"] = time_hist
    summary["spectra"] = spectra_data

    # ── Animation data: displacement + accel at sampled depths ──
    #    Used by the frontend "Shaking Simulation" tab.
    #    disp_all_nodes.out has relative displacements (already correct for animation
    #    since we want lateral motion relative to stationary viewer).
    anim_data = None
    disp_all_file = os.path.join(result_dir, "disp_all_nodes.out")
    if os.path.exists(acc_all_file) and os.path.exists(disp_all_file) and node_elevs:
        try:
            disp_raw = np.loadtxt(disp_all_file)
            disp_time = disp_raw[:, 0]
            disp_all = disp_raw[:, 1:]
            if disp_all.ndim == 1:
                disp_all = disp_all.reshape(-1, 1)

            # Re-read acc for animation (already loaded above in depth-profile block)
            acc_raw = np.loadtxt(acc_all_file)
            acc_rec_time = acc_raw[:, 0]
            acc_all_raw = acc_raw[:, 1:]
            if acc_all_raw.ndim == 1:
                acc_all_raw = acc_all_raw.reshape(-1, 1)
            acc_input_interp = np.interp(acc_rec_time, t_input, input_acc_ms2)

            sorted_nodes = sorted(node_elevs.items(), key=lambda x: x[1])
            n_nodes = min(len(sorted_nodes), disp_all.shape[1], acc_all_raw.shape[1])

            # Pick ~8 representative nodes (evenly spaced) for smooth animation
            if n_nodes <= 10:
                sample_idx = list(range(n_nodes))
            else:
                sample_idx = [int(round(i * (n_nodes - 1) / 9)) for i in range(10)]
                sample_idx = sorted(set(sample_idx))

            # Downsample time to ~800 frames for smooth 30fps playback
            n_t = len(disp_time)
            t_step = max(1, n_t // 800)

            anim_times = disp_time[::t_step].tolist()
            anim_nodes = []
            for ci in sample_idx:
                nTag, elev = sorted_nodes[ci]
                depth = total_depth - elev
                d = disp_all[::t_step, ci]                  # relative disp (m)
                a = (acc_all_raw[::t_step, ci] + acc_input_interp[::t_step]) / G  # abs accel (g)
                anim_nodes.append({
                    "depth": round(float(depth), 2),
                    "disp": np.round(d, 6).tolist(),         # meters
                    "accel": np.round(a, 5).tolist(),        # g
                })

            # Build layer geometry from elem_info
            anim_layers = []
            cum = 0.0
            for ei in elem_info:
                thick = float(ei.get("thick", 1.0))
                stype = ei.get("type", "sand").lower()
                anim_layers.append({
                    "top": round(cum, 2),
                    "bot": round(cum + thick, 2),
                    "type": stype,
                    "thick": round(thick, 2),
                })
                cum += thick

            anim_data = {
                "times": anim_times,
                "nodes": anim_nodes,
                "layers": anim_layers,
                "total_depth": float(total_depth),
                "dt_anim": round(float(disp_time[t_step] - disp_time[0]), 5) if n_t > t_step else 0.01,
            }
        except Exception:
            anim_data = None

    if anim_data:
        summary["animation"] = anim_data

    return summary
