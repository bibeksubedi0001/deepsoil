"""
1D Non-Linear Site Response Analysis — OpenSeesPy version.
Direct Python translation of SiteResponse1D.tcl for deployment
where the standalone OpenSees binary is unavailable.

Usage:
    python run_opensees.py <soil_csv> <motion_acc> <dt> <npts> <outdir>
"""

import sys
import os
import csv
import math

import openseespy.opensees as ops


def main(soil_csv, motion_acc, dt_str, npts_str, output_dir):
    dt = float(dt_str)
    npts = int(npts_str)

    print(f"openseespy version: {ops.version()}")
    os.makedirs(output_dir, exist_ok=True)
    ops.wipe()

    # ─── 1. CONSTANTS ────────────────────────────────────────
    g = 9.81
    col_width = 1.0
    rock_Vs = 760.0
    rock_den = 2200.0
    damp_ratio = 0.02
    nu = 0.3

    # ─── 2. READ SOIL CSV ────────────────────────────────────
    print("\nReading soil profile...")
    layers = []
    with open(soil_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            thick = float(row["thickness"])
            if thick <= 0.001:
                continue
            layers.append({
                "thickness": thick,
                "density": float(row["mass_density"]),
                "Vs": float(row["Vs"]),
                "soil_type": row["soil_type"],
                "pdmy_args": row.get("pdmy_args", ""),
                "hyst_args": row.get("hyst_args", ""),
                "clay_args": row.get("clay_args", ""),
            })

    num_layers = len(layers)
    total_depth = sum(l["thickness"] for l in layers)
    sum_h_vs = sum(l["thickness"] / l["Vs"] for l in layers)
    Vs_avg = total_depth / sum_h_vs

    print(f"  Layers     : {num_layers}")
    print(f"  Total depth: {total_depth} m")
    print(f"  Vs30 (avg) : {Vs_avg:.1f} m/s")

    # ─── 3. ELEMENT DISCRETIZATION ───────────────────────────
    f_max = 25.0
    elems = []

    for i, lay in enumerate(layers):
        h = lay["thickness"]
        vs = lay["Vs"]
        h_max = vs / (10.0 * f_max)
        h_max = min(h_max, 1.5)
        h_max = max(h_max, 0.25)
        n_sub = max(1, math.ceil(h / h_max))
        sub_h = h / n_sub
        for _ in range(n_sub):
            elems.append({
                "thickness": sub_h,
                "density": lay["density"],
                "Vs": lay["Vs"],
                "soil_type": lay["soil_type"],
                "pdmy_args": lay["pdmy_args"],
                "hyst_args": lay["hyst_args"],
                "clay_args": lay["clay_args"],
                "mat_tag": i + 1,
            })

    total_elems = len(elems)
    print(f"  Elements   : {total_elems} (after sub-discretization)")

    # ─── 4. BUILD MODEL ──────────────────────────────────────
    ops.model("BasicBuilder", "-ndm", 2, "-ndf", 2)
    print("\nBuilding mesh...")

    # 4a. Create Nodes
    z = 0.0
    for j in range(total_elems + 1):
        nL = 2 * j + 1
        nR = 2 * j + 2
        ops.node(nL, 0.0, z)
        ops.node(nR, col_width, z)
        if j < total_elems:
            z += elems[j]["thickness"]

    surf_nL = 2 * total_elems + 1
    surf_nR = 2 * total_elems + 2
    base_nL = 1
    base_nR = 2
    print(f"  Nodes: {2 * (total_elems + 1)}")

    # 4b. Boundary Conditions
    ops.fix(base_nL, 0, 1)
    ops.fix(base_nR, 0, 1)

    dash_node = 2 * (total_elems + 1) + 1
    ops.node(dash_node, 0.0, 0.0)
    ops.fix(dash_node, 1, 1)

    for j in range(total_elems + 1):
        nL = 2 * j + 1
        nR = 2 * j + 2
        ops.equalDOF(nL, nR, 1, 2)

    # 4c. Define Materials
    print("\nDefining materials...")
    for i, lay in enumerate(layers):
        mat_tag = i + 1
        stype = lay["soil_type"]
        vs = lay["Vs"]
        dens = lay["density"]

        if stype == "sand":
            args = [s.strip() for s in lay["pdmy_args"].split(",")]
            rho = float(args[0])
            G0 = float(args[1])
            K_bulk = float(args[2])
            phi = float(args[3])
            peak_str = float(args[4])
            p_ref = float(args[5])
            press_coef = float(args[6])
            pt_ang = float(args[7])
            ops.nDMaterial("PressureDependMultiYield", mat_tag, 2,
                           rho, G0, K_bulk, phi, peak_str, p_ref, press_coef, pt_ang,
                           0.21, 0.0, 0.0,
                           0.0, 0.0, 0.0,
                           0)

        elif stype == "silt":
            args = [s.strip() for s in lay["hyst_args"].split(",")]
            K0 = float(args[0])
            Fy = float(args[1])
            G0 = K0
            K_bulk = 2.0 * G0 * (1.0 + nu) / (3.0 * (1.0 - 2.0 * nu))
            cohesion = Fy / 1.732
            peak_str = 0.1
            ops.nDMaterial("PressureIndependMultiYield", mat_tag, 2,
                           dens, G0, K_bulk, cohesion, peak_str,
                           0.0, 101325.0, 0.0, 0)

        elif stype == "clay":
            args = [s.strip() for s in lay["clay_args"].split(",")]
            p_ref_clay = float(args[0])
            e0 = float(args[1])
            lambda_c = float(args[2])
            kappa = float(args[3])
            xi_ocr = float(args[4])
            G0 = dens * vs * vs
            K_bulk = 2.0 * G0 * (1.0 + nu) / (3.0 * (1.0 - 2.0 * nu))
            sigma_v = p_ref_clay * 3.0 / (1.0 + 2.0 * 0.577)
            Su = 0.25 * sigma_v * xi_ocr
            su_min = max(G0 / 500.0, 10000.0)
            if Su < su_min:
                Su = su_min
            cohesion = Su
            peak_str = 0.1
            ops.nDMaterial("PressureIndependMultiYield", mat_tag, 2,
                           dens, G0, K_bulk, cohesion, peak_str,
                           0.0, 101325.0, 0.0, 0)

        print(f"  Mat {mat_tag}: {stype} (Vs={vs:.0f} m/s, rho={dens:.0f} kg/m3)")

    # 4d. Create Elements
    print("\nCreating elements...")
    for j in range(total_elems):
        ele_tag = j + 1
        n1 = 2 * j + 1
        n2 = 2 * j + 2
        n3 = 2 * (j + 1) + 2
        n4 = 2 * (j + 1) + 1
        mat_t = elems[j]["mat_tag"]
        dens = elems[j]["density"]
        ops.element("quad", ele_tag, n1, n2, n3, n4,
                     col_width, "PlaneStrain", mat_t,
                     0.0, dens, 0.0, -dens * g)

    print(f"  Created {total_elems} quad elements")

    # 4e. Dashpot
    dash_coeff = rock_den * rock_Vs * col_width
    dash_mat_tag = num_layers + 1
    ops.uniaxialMaterial("Viscous", dash_mat_tag, dash_coeff, 1.0)
    dash_ele_tag = total_elems + 1
    ops.element("zeroLength", dash_ele_tag, dash_node, base_nL,
                "-mat", dash_mat_tag, "-dir", 1)

    # ─── 5. RECORDERS ────────────────────────────────────────
    print("\nSetting up recorders...")
    node_list = [2 * j + 1 for j in range(total_elems + 1)]
    ele_list = [j + 1 for j in range(total_elems)]

    ops.recorder("Node", "-file", os.path.join(output_dir, "acc_surface.out"),
                 "-time", "-node", surf_nL, "-dof", 1, "accel")
    ops.recorder("Node", "-file", os.path.join(output_dir, "acc_base.out"),
                 "-time", "-node", base_nL, "-dof", 1, "accel")
    ops.recorder("Node", "-file", os.path.join(output_dir, "acc_all_nodes.out"),
                 "-time", "-node", *node_list, "-dof", 1, "accel")
    ops.recorder("Node", "-file", os.path.join(output_dir, "disp_surface.out"),
                 "-time", "-node", surf_nL, "-dof", 1, "disp")
    ops.recorder("Node", "-file", os.path.join(output_dir, "disp_all_nodes.out"),
                 "-time", "-node", *node_list, "-dof", 1, "disp")
    ops.recorder("Element", "-file", os.path.join(output_dir, "stress.out"),
                 "-time", "-ele", *ele_list, "material", 1, "stress")
    ops.recorder("Element", "-file", os.path.join(output_dir, "strain.out"),
                 "-time", "-ele", *ele_list, "material", 1, "strain")

    # ─── 6. GRAVITY (Elastic) ────────────────────────────────
    print("\n--- Gravity Analysis (Elastic Stage) ---")
    for i in range(num_layers):
        ops.updateMaterialStage("-material", i + 1, "-stage", 0)

    ops.constraints("Transformation")
    ops.test("NormDispIncr", 1.0e-5, 40, 0)
    ops.algorithm("Newton")
    ops.numberer("RCM")
    ops.system("ProfileSPD")
    ops.integrator("Newmark", 0.5, 0.25)
    ops.analysis("Transient")

    grav_ok = ops.analyze(10, 5.0e2)
    if grav_ok != 0:
        ops.algorithm("KrylovNewton")
        grav_ok = ops.analyze(50, 5.0e2)

    print(f"  Elastic gravity: {'OK' if grav_ok == 0 else 'FAILED'}")

    # ─── 7. SWITCH TO PLASTIC ────────────────────────────────
    print("\n--- Updating Material Stage to Plastic ---")
    for i in range(num_layers):
        ops.updateMaterialStage("-material", i + 1, "-stage", 1)

    ops.algorithm("Newton")
    plastic_ok = ops.analyze(10, 5.0e2)
    if plastic_ok != 0:
        ops.algorithm("KrylovNewton")
        plastic_ok = ops.analyze(50, 5.0e2)

    print(f"  Plastic gravity: {'OK' if plastic_ok == 0 else 'FAILED'}")

    # Reset
    ops.setTime(0.0)
    ops.wipeAnalysis()
    ops.remove("recorders")

    for j in range(total_elems + 1):
        nL = 2 * j + 1
        nR = 2 * j + 2
        ops.setNodeDisp(nL, 1, 0.0)
        ops.setNodeDisp(nL, 2, 0.0)
        ops.setNodeDisp(nR, 1, 0.0)
        ops.setNodeDisp(nR, 2, 0.0)
    ops.setNodeDisp(dash_node, 1, 0.0)
    ops.setNodeDisp(dash_node, 2, 0.0)

    # ─── 8. RAYLEIGH DAMPING ─────────────────────────────────
    f1 = Vs_avg / (4.0 * total_depth)
    f2 = 5.0 * f1
    omega1 = 2.0 * math.pi * f1
    omega2 = 2.0 * math.pi * f2
    a0 = 2.0 * damp_ratio * omega1 * omega2 / (omega1 + omega2)
    a1 = 2.0 * damp_ratio / (omega1 + omega2)
    ops.rayleigh(a0, a1, 0.0, 0.0)

    # ─── 9. DYNAMIC ANALYSIS ─────────────────────────────────
    print("\n--- Dynamic Analysis ---")

    # Re-setup recorders
    ops.recorder("Node", "-file", os.path.join(output_dir, "acc_surface.out"),
                 "-time", "-node", surf_nL, "-dof", 1, "accel")
    ops.recorder("Node", "-file", os.path.join(output_dir, "acc_base.out"),
                 "-time", "-node", base_nL, "-dof", 1, "accel")
    ops.recorder("Node", "-file", os.path.join(output_dir, "acc_all_nodes.out"),
                 "-time", "-node", *node_list, "-dof", 1, "accel")
    ops.recorder("Node", "-file", os.path.join(output_dir, "disp_surface.out"),
                 "-time", "-node", surf_nL, "-dof", 1, "disp")
    ops.recorder("Node", "-file", os.path.join(output_dir, "disp_all_nodes.out"),
                 "-time", "-node", *node_list, "-dof", 1, "disp")
    ops.recorder("Element", "-file", os.path.join(output_dir, "stress.out"),
                 "-time", "-ele", *ele_list, "material", 1, "stress")
    ops.recorder("Element", "-file", os.path.join(output_dir, "strain.out"),
                 "-time", "-ele", *ele_list, "material", 1, "strain")

    # Input motion
    motion_path = os.path.abspath(motion_acc).replace("\\", "/")
    ops.timeSeries("Path", 1, "-dt", dt, "-filePath", motion_path,
                   "-factor", g)
    ops.pattern("UniformExcitation", 1, 1, "-accel", 1)

    total_time = npts * dt
    print(f"  Duration: {total_time:.1f} s, dt = {dt} s")

    # Analysis setup
    ops.constraints("Transformation")
    ops.test("NormDispIncr", 1.0e-3, 50, 0)
    ops.algorithm("KrylovNewton")
    ops.numberer("RCM")
    ops.system("ProfileSPD")
    ops.integrator("Newmark", 0.5, 0.25)
    ops.analysis("Transient")

    max_dt = 0.005
    analysis_dt = min(dt, max_dt)

    current_time = 0.0
    ok = 0
    step_count = 0
    fail_count = 0

    print(f"  Running dynamic analysis...")

    while current_time < total_time and ok == 0:
        ok = ops.analyze(1, analysis_dt)

        if ok != 0:
            ok = ops.analyze(1, analysis_dt / 2.0)
        if ok != 0:
            ok = ops.analyze(2, analysis_dt / 4.0)
        if ok != 0:
            ops.algorithm("ModifiedNewton")
            ok = ops.analyze(2, analysis_dt / 4.0)
            ops.algorithm("KrylovNewton")
        if ok != 0:
            ops.algorithm("NewtonLineSearch", 0.8)
            ok = ops.analyze(4, analysis_dt / 8.0)
            ops.algorithm("KrylovNewton")
        if ok != 0:
            ops.test("NormDispIncr", 5.0e-3, 60, 0)
            ops.algorithm("BFGS")
            ok = ops.analyze(8, analysis_dt / 16.0)
            ops.test("NormDispIncr", 1.0e-3, 50, 0)
            ops.algorithm("KrylovNewton")
        if ok != 0:
            ops.test("NormDispIncr", 1.0e-2, 80, 0)
            ops.algorithm("BFGS")
            ok = ops.analyze(16, analysis_dt / 32.0)
            ops.test("NormDispIncr", 1.0e-3, 50, 0)
            ops.algorithm("KrylovNewton")
        if ok != 0:
            fail_count += 1
            if fail_count > 50:
                print(f"    FATAL: Too many failures ({fail_count}). Aborting.")
                break
            ops.test("NormDispIncr", 1.0e-1, 10, 0)
            ops.analyze(1, analysis_dt)
            ops.test("NormDispIncr", 1.0e-3, 50, 0)
            ops.algorithm("KrylovNewton")
            ok = 0

        current_time = ops.getTime()
        step_count += 1

        prog_interval = max(1, npts // 10)
        if step_count % prog_interval == 0:
            pct = 100.0 * current_time / total_time
            print(f"    t = {current_time:.1f} s  ({pct:.0f}%)")

    print(f"\n  Analysis complete. Steps: {step_count}, Failures: {fail_count}")

    # ─── 10. WRITE METADATA ──────────────────────────────────
    meta_path = os.path.join(output_dir, "model_info.txt")
    with open(meta_path, "w") as mf:
        mf.write("# 1D Site Response Analysis - Model Information\n")
        mf.write(f"soil_profile {soil_csv}\n")
        mf.write(f"motion_file {motion_acc}\n")
        mf.write(f"motion_dt {dt}\n")
        mf.write(f"motion_npts {npts}\n")
        mf.write(f"num_layers {num_layers}\n")
        mf.write(f"num_elements {total_elems}\n")
        mf.write(f"total_depth {total_depth}\n")
        mf.write(f"Vs_avg {Vs_avg:.2f}\n")
        mf.write(f"damping_ratio {damp_ratio}\n")
        mf.write(f"f1 {f1:.4f}\n")
        mf.write(f"f2 {f2:.4f}\n")
        mf.write(f"col_width {col_width}\n")
        mf.write(f"rock_Vs {rock_Vs}\n")
        mf.write(f"rock_density {rock_den}\n")
        mf.write(f"surface_nodeL {surf_nL}\n")
        mf.write(f"base_nodeL {base_nL}\n")
        mf.write("#\n# Node level elevations (left-column nodes):\n")
        zz = 0.0
        for j in range(total_elems + 1):
            nL = 2 * j + 1
            mf.write(f"node_elev {nL} {zz:.4f}\n")
            if j < total_elems:
                zz += elems[j]["thickness"]
        mf.write("#\n# Element -> layer mapping:\n")
        for j in range(total_elems):
            e_tag = j + 1
            m_tag = elems[j]["mat_tag"]
            st = elems[j]["soil_type"]
            mf.write(f"elem_info {e_tag} mat={m_tag} type={st} "
                      f"Vs={elems[j]['Vs']} thick={elems[j]['thickness']}\n")

    print(f"\nDONE — Results in {output_dir}")

    ops.record()
    ops.wipe()


if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("Usage: python run_opensees.py <soil_csv> <motion_acc> <dt> <npts> <outdir>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
