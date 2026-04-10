"""
Soil parameter computation for OpenSees 1D Site Response Analysis.
Logic ported from start_again/borehole_to_opensees.py (verified accurate).
"""

import math

G_ACCEL = 9.81
ATM_PA = 101325.0
POISSON_NU = 0.3
GAMMA_WATER = 9.81  # kN/m^3


def classify_soil(description: str) -> str:
    desc = description.lower().strip()
    if desc in ("sand", "gravel"):
        return "sand"
    if desc == "silt":
        return "silt"
    if desc == "clay":
        return "clay"
    if "clay" in desc:
        return "clay"
    if "silt" in desc:
        return "silt"
    if any(kw in desc for kw in ("sand", "gravel", "boulder", "cobble")):
        return "sand"
    return "sand"


def calc_pdmy_args(rho, Vs, N_value, depth_mid):
    """8 PressureDependMultiYield args for Sand/Gravel."""
    G0 = rho * Vs ** 2
    K_bulk = 2.0 * G0 * (1.0 + POISSON_NU) / (3.0 * (1.0 - 2.0 * POISSON_NU))

    if N_value is None or (isinstance(N_value, float) and math.isnan(N_value)) or N_value <= 0:
        phi = 30.0
    elif N_value < 10:
        phi = 28.0
    elif N_value < 30:
        phi = 32.0
    elif N_value < 50:
        phi = 36.0
    else:
        phi = 40.0

    peak_strain = 0.1
    p_ref = ATM_PA
    press_coeff = 0.5
    pt_ang = max(phi - 3.0, 20.0)

    return (f"{rho:.1f}, {G0:.1f}, {K_bulk:.1f}, {phi:.1f}, "
            f"{peak_strain}, {p_ref:.1f}, {press_coeff}, {pt_ang:.1f}")


def calc_hyst_args(rho, Vs):
    """4 Hysteretic model args for Silt."""
    K0 = rho * Vs ** 2
    Fy = 0.01 * K0
    return f"{K0:.1f}, {Fy:.1f}, 0.2, 0.2"


def calc_clay_args(rho, Vs, depth_mid):
    """5 Clay model args using effective stress framework."""
    gamma = rho * G_ACCEL / 1000.0  # kN/m^3

    sigma_v_eff = (gamma - GAMMA_WATER) * depth_mid * 1000.0  # Pa
    sigma_v_eff = max(sigma_v_eff, 1000.0)

    K0_coeff = 0.577  # 1 - sin(25 deg)
    p_ref = sigma_v_eff * (1.0 + 2.0 * K0_coeff) / 3.0

    if Vs < 150:
        e0, lambda_c = 1.1, 0.25
    elif Vs < 200:
        e0, lambda_c = 0.85, 0.18
    else:
        e0, lambda_c = 0.6, 0.12

    kappa = lambda_c / 5.0
    xi = 1.0

    return f"{p_ref:.1f}, {e0:.2f}, {lambda_c}, {kappa:.3f}, {xi}"


def compute_layer_params(thickness, density, vs, soil_type,
                         spt_n=15, depth_to_mid=5.0):
    result = {
        "thickness": round(float(thickness), 3),
        "mass_density": round(float(density), 2),
        "Vs": round(float(vs), 2),
        "soil_type": soil_type,
        "pdmy_args": "",
        "hyst_args": "",
        "clay_args": "",
    }

    if soil_type == "sand":
        result["pdmy_args"] = calc_pdmy_args(density, vs, spt_n, depth_to_mid)
    elif soil_type == "silt":
        result["hyst_args"] = calc_hyst_args(density, vs)
    elif soil_type == "clay":
        result["clay_args"] = calc_clay_args(density, vs, depth_to_mid)

    return result


def build_soil_csv(layers: list) -> str:
    header = "thickness,mass_density,Vs,soil_type,pdmy_args,hyst_args,clay_args"
    lines = [header]

    cumulative_depth = 0.0
    for layer in layers:
        thickness = float(layer["thickness"])
        density = float(layer["mass_density"])
        vs = float(layer["Vs"])
        soil_type = layer["soil_type"].lower().strip()
        spt_n = float(layer.get("spt_n", 15))
        depth_to_mid = cumulative_depth + thickness / 2.0

        params = compute_layer_params(thickness, density, vs, soil_type,
                                      spt_n, depth_to_mid)
        cumulative_depth += thickness

        pdmy = f'"{params["pdmy_args"]}"' if params["pdmy_args"] else ""
        hyst = f'"{params["hyst_args"]}"' if params["hyst_args"] else ""
        clay = f'"{params["clay_args"]}"' if params["clay_args"] else ""

        line = (f'{params["thickness"]},{params["mass_density"]},'
                f'{params["Vs"]},{params["soil_type"]},{pdmy},{hyst},{clay}')
        lines.append(line)

    return "\n".join(lines) + "\n"
