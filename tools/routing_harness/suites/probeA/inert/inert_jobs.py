"""probeA rerun, category 'inert': every scenario of caseidx/inert.json on design M2 and design K.

Fixtures are rebuilt with the original fixture code (copied into ifx.py) from
  probe-inert/s01_netclass.py .. s08_nc_on_dangling.py   (source 'probe')
  verify-probe-inert/v01_netclass.py .. v07_misc.py        (source 'verify')
The calls the originals made on P, M and B become rlib steps run on M2 and K and judged by
core/judge3.py (kicad-cli = measured; the GUI-load proxy is not available in this repository).
Variants that only exist for P (Plit, P1iu) share their fixture with the plain variant, so they are the same job.

Run: uv run python tools/routing_harness/suites/probeA/inert/inert_jobs.py [workers] [only...]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import rlib  # noqa: E402  (first: redirects TMP/TEMP)
from rlib import Job, cp, wptn  # noqa: E402

import ifx as F  # noqa: E402

R = "Device:R"
SCRIPT = str(Path(__file__).resolve())
PI = "probe-inert"
VI = "verify-probe-inert"
W_SIG = [("R1", "1")]


def W():
    return wptn(W_SIG, "SIG")


def CP12():
    return cp(("R1", "1"), ("R2", "1"))


# ------------------------------------------------------------------ probe-inert/s01_netclass.py


def s01_base(name, sig=True):
    p = F.mk(name)
    F.place(p, R, "R1", 101.6, 101.6)
    F.place(p, R, "R2", 127, 101.6)
    if sig:
        F.wire(p, 127, 97.79, 127, 95.25)
        F.label(p, "SIG", 127, 95.25)
    F.add_pro(p)
    return p


def b_s01(v):
    if v == "1a":
        p = s01_base("i01a")
        F.wire(p, 101.6, 95.25, 111.76, 95.25)
        F.directive(p, "HV", 111.76, 95.25)
    elif v == "1b":
        p = s01_base("i01b")
        F.wire(p, 101.6, 95.25, 111.76, 95.25)
        F.directive(p, "HV", 106.68, 95.25)
    elif v == "1c":
        p = s01_base("i01c")
        F.directive(p, "HV", 101.6, 95.25)
    elif v == "1d":
        p = s01_base("i01d")
        F.directive(p, "HV", 101.6, 96.52)
    elif v == "1e":
        p = s01_base("i01e", sig=False)
        F.directive(p, "HV", 114.3, 97.79)
    elif v in ("1f-lr", "1f-rl"):
        a, b = ((95.25, 97.79), (107.95, 97.79)) if v == "1f-lr" else ((107.95, 97.79), (95.25, 97.79))
        p = s01_base(f"i01f_{v[-2:]}")
        F.wire(p, a[0], a[1], b[0], b[1])
        F.directive(p, "HV", 107.95, 97.79)
    elif v == "1g":
        p = s01_base("i01g")
        F.wire(p, 101.6, 95.25, 111.76, 95.25)
    else:
        raise KeyError(v)
    return p


# ------------------------------------------------------------------ probe-inert/s02_latent_t.py


def b_s02(v):
    if v == "2a":
        p = F.mk("i02a")
        F.place(p, R, "R1", 101.6, 101.6)
        F.place(p, R, "R2", 111.76, 113.03)
        F.wire(p, 111.76, 109.22, 111.76, 87.63)
        F.label(p, "FOO", 111.76, 87.63)
        F.wire(p, 101.6, 95.25, 111.76, 95.25)
        F.add_pro(p)
    elif v == "2b":
        p = F.mk("i02b")
        F.place(p, R, "R1", 101.6, 101.6)
        F.place(p, R, "R3", 106.68, 91.44)
        F.place(p, R, "R4", 127, 101.6)
        F.wire(p, 106.68, 87.63, 127, 87.63)
        F.wire(p, 127, 87.63, 127, 97.79)
        F.label(p, "FOO", 127, 87.63)
        F.wire(p, 101.6, 95.25, 111.76, 95.25)
        F.add_pro(p)
    else:
        raise KeyError(v)
    return p


# ------------------------------------------------------------------ probe-inert/s03_label_crossing.py


def b_s03(v):
    p = F.mk({"3a": "i03a", "3b": "i03b", "3c": "i03c", "ctrl": "i03ctrl"}[v])
    F.place(p, R, "R1", 101.6, 101.6)
    F.place(p, R, "R2", 109.22, 101.6)
    F.wire(p, 109.22, 97.79, 109.22, 87.63)     # F
    F.wire(p, 101.6, 95.25, 116.84, 95.25)      # D, crosses F at X
    F.add_pro(p)
    if v == "3a":
        F.label(p, "LBL", 109.22, 95.25)
    elif v == "3b":
        F.directive(p, "HV", 109.22, 95.25)
    elif v == "3c":
        F.raw_label(p, "LBL", "109.2201", "95.2501")
    elif v != "ctrl":
        raise KeyError(v)
    return p


# ------------------------------------------------------------------ probe-inert/s04_lone_items.py


def b_s04(v):
    if v == "4a":
        p = s01_base("i04a")
        F.junction(p, 101.6, 96.52)
    elif v == "4b":
        p = s01_base("i04b")
        F.junction(p, 101.6, 95.25)
    elif v == "4c":
        p = s01_base("i04c", sig=False)
        F.junction(p, 114.3, 97.79)
    elif v == "4d":
        p = s01_base("i04d")
        F.wire(p, 95.25, 95.25, 107.95, 95.25)
    elif v in ("4d2-lr", "4d2-rl"):
        a, b = ((95.25, 95.25), (107.95, 95.25)) if v == "4d2-lr" else ((107.95, 95.25), (95.25, 95.25))
        p = s01_base(f"i04d2_{v[-2:]}")
        F.wire(p, a[0], a[1], b[0], b[1])
        F.directive(p, "HV", 107.95, 95.25)
    elif v == "4e1":
        p = s01_base("i04e1")
        F.poly(p, [(95.25, 97.79), (107.95, 97.79)])
    elif v == "4e2":
        p = s01_base("i04e2")
        F.poly(p, [(95.25, 95.25), (107.95, 95.25)])
    elif v == "4e3":
        p = s01_base("i04e3")
        F.text(p, "SIG2", 101.6, 95.25)
    elif v == "4f":
        p = s01_base("i04f")
        F.rule_area(p, [(96.52, 88.9), (106.68, 88.9), (106.68, 96.52), (96.52, 96.52)])
        F.directive(p, "HV", 104.14, 88.9)
    else:
        raise KeyError(v)
    return p


# ------------------------------------------------------------------ probe-inert/s05_hidden_nc.py


def b_s05(v):
    lib = F.hn_lib()
    ux, uy = {"5a": (86.36, 95.25), "5b": (106.68, 87.63), "5c": (111.76, 102.87),
              "5d": (111.76, 105.41)}[v]
    p = F.mk(f"i0{v}")
    F.place(p, R, "R1", 101.6, 101.6)
    rlib.SCH.place_component(lib_id="pt:HN", reference="U1", value="HN", x=ux, y=uy,
                             symbol_lib_path=lib, schematic_path=p)
    if v == "5a":
        F.wire(p, 101.6, 95.25, 93.98, 95.25)
    elif v in ("5b", "5c"):
        F.wire(p, 101.6, 95.25, 111.76, 95.25)
    else:
        F.wire(p, 101.6, 97.79, 111.76, 97.79)
    F.add_pro(p)
    return p


# ------------------------------------------------------------------ probe-inert/s06_model_gaps.py


def b_s06(v):
    if v == "6a":
        p = F.mk("i06a")
        F.place(p, R, "R1", 101.6, 101.6)
        F.place(p, R, "R2", 119.38, 101.6)
        F.wire(p, 101.6, 95.25, 111.76, 95.25)             # D
        F.wire(p, 106.68, 95.25, 119.38, 95.25)            # F, overlaps D
        F.wire(p, 119.38, 95.25, 119.38, 97.79)            # F2 to R2:1
        F.label(p, "FOO", 119.38, 95.25)
        F.add_pro(p)
    elif v in ("6b-lr", "6b-rl"):
        a, b = ((101.6, 95.25), (111.76, 95.25)) if v == "6b-lr" else ((111.76, 95.25), (101.6, 95.25))
        p = F.mk(f"i06b_{v[-2:]}")
        F.place(p, R, "R1", 101.6, 101.6)
        F.place(p, R, "R2", 109.22, 85.09)
        F.wire(p, a[0], a[1], b[0], b[1])
        F.bus_entry(p, 106.68, 95.25, 2.54, -2.54)
        F.wire(p, 109.22, 92.71, 109.22, 88.9)
        F.label(p, "FOO", 109.22, 92.71)
        F.add_pro(p)
    else:
        raise KeyError(v)
    return p


# ------------------------------------------------------------------ probe-inert/s07_bus_directive.py


def b_s07(v):
    p = F.mk(f"i07{v[-1]}")
    F.place(p, R, "R1", 101.6, 101.6)
    F.place(p, R, "R2", 109.22, 101.6)
    F.place(p, R, "R3", 127, 101.6)
    F.wire(p, 127, 97.79, 127, 95.25)
    F.label(p, "SIG", 127, 95.25)
    F.bus(p, 109.22, 87.63, 109.22, 97.79)
    F.wire(p, 101.6, 95.25, 116.84, 95.25)
    if v == "7a":
        F.directive(p, "HV", 109.22, 95.25)
    elif v != "7b":
        raise KeyError(v)
    F.add_pro(p)
    return p


# ------------------------------------------------------------------ probe-inert/s08_nc_on_dangling.py


def b_s08(v):
    p = F.mk("i08")
    F.place(p, R, "R1", 101.6, 101.6)
    F.place(p, R, "R2", 127, 101.6)
    F.wire(p, 127, 97.79, 127, 95.25)
    F.label(p, "SIG", 127, 95.25)
    F.wire(p, 101.6, 95.25, 111.76, 95.25)
    F.no_connect(p, 111.76, 95.25)
    F.add_pro(p)
    return p


# ------------------------------------------------------------------ verify: common bases


def v_base(case, sig=True, rot=0, pro=True):
    """v01/v04/v05/v07 base: R1 (76.2,76.2), R2 (96.52,76.2), SIG on R2:1."""
    p = F.v_new_sheet(case)
    F.v_place(p, R, "R1", 76.2, 76.2, rot=rot)
    F.v_place(p, R, "R2", 96.52, 76.2)
    if sig:
        F.v_wire(p, 96.52, 72.39, 96.52, 69.85)
        F.v_label(p, "SIG", 96.52, 69.85)
    if pro:
        F.v_write_pro(p)
    return p


# ------------------------------------------------------------------ verify-probe-inert/v01_netclass.py


def b_v01(v):
    if v == "a":
        p = v_base("v01a")
        F.v_wire(p, 76.2, 69.85, 66.04, 69.85)
        F.v_netclass_flag(p, "HV", 66.04, 69.85)
    elif v == "b":
        p = v_base("v01b")
        F.v_wire(p, 76.2, 69.85, 66.04, 69.85)
        F.v_netclass_flag(p, "HV", 71.12, 69.85)
    elif v == "c":
        p = v_base("v01c")
        F.v_netclass_flag(p, "HV", 76.2, 69.85)
    elif v == "d":
        p = v_base("v01d")
        F.v_netclass_flag(p, "HV", 76.2, 71.12)
    elif v == "e":
        p = v_base("v01e", sig=False)
        F.v_netclass_flag(p, "HV", 86.36, 72.39)
    elif v == "f":
        p = v_base("v01f", rot=90)
        (px, py, outd), = rlib.M2.pin_positions(p, "R1")["1"]  # KiCad transform
        ex = px + (2.54 if outd == "right" else -2.54 if outd == "left" else 0)
        ey = py + (2.54 if outd == "down" else -2.54 if outd == "up" else 0)
        F.v_wire(p, round(ex, 4), round(ey, 4), round(ex, 4), round(ey - 7.62, 4))
        F.v_netclass_flag(p, "HV", round(ex, 4), round(ey - 7.62, 4))
    elif v == "g":
        p = v_base("v01g")
        F.v_wire(p, 76.2, 69.85, 66.04, 69.85)
    else:
        raise KeyError(v)
    return p


# ------------------------------------------------------------------ verify-probe-inert/v02_latent.py


def b_v02(v):
    p = v_base(f"v02{v}", pro=False)
    if v == "T":
        F.v_place(p, R, "R3", 66.04, 86.36)
        F.v_wire(p, 66.04, 82.55, 66.04, 62.23)
        F.v_label(p, "FOO", 66.04, 62.23)
        F.v_wire(p, 76.2, 69.85, 66.04, 69.85)
    elif v == "X":
        F.v_place(p, R, "R3", 68.58, 66.04)
        F.v_wire(p, 68.58, 62.23, 68.58, 59.69)
        F.v_label(p, "FOO", 68.58, 59.69)
        F.v_wire(p, 76.2, 69.85, 60.96, 69.85)
    elif v == "H":
        lib = F.vh_lib()
        rlib.SCH.place_component(lib_id="vlib:VH", reference="U1", value="VH", x=71.12, y=64.77,
                                 symbol_lib_path=lib, schematic_path=p)
        F.v_wire(p, 76.2, 69.85, 60.96, 69.85)
    else:
        raise KeyError(v)
    F.v_write_pro(p)
    return p


# ------------------------------------------------------------------ verify-probe-inert/v04_junctions.py


def b_v04(v):
    if v == "J1":
        p = v_base("v04J1", pro=False)
        F.v_junction(p, 76.2, 71.12)
    elif v == "J2":
        p = v_base("v04J2", sig=False, pro=False)
        F.v_junction(p, 86.36, 72.39)
    elif v == "J3":
        p = v_base("v04J3", pro=False)
        F.v_wire(p, 66.04, 69.85, 86.36, 69.85)
    elif v in ("J4-lr", "J4-rl"):
        a, b = ((66.04, 69.85), (86.36, 69.85)) if v == "J4-lr" else ((86.36, 69.85), (66.04, 69.85))
        p = v_base(f"v04J4{v[-2:]}", pro=False)
        F.v_wire(p, a[0], a[1], b[0], b[1])
        F.v_netclass_flag(p, "HV", 86.36, 69.85)
    elif v in ("J5-lr", "J5-rl"):
        a, b = ((66.04, 69.85), (86.36, 69.85)) if v == "J5-lr" else ((86.36, 69.85), (66.04, 69.85))
        p = F.v_new_sheet(f"v04J5{v[-2:]}")
        F.v_place(p, R, "R1", 76.2, 76.2)
        F.v_place(p, R, "R2", 86.36, 76.2)
        F.v_wire(p, a[0], a[1], b[0], b[1])                 # W
        F.v_label(p, "SIG", 66.04, 69.85)                  # SIG at W's left end
        F.v_wire(p, 86.36, 69.85, 86.36, 72.39)            # W's right end down to R2:1
    elif v in ("J7-lr", "J7-rl"):
        a, b = ((68.58, 72.39), (83.82, 72.39)) if v == "J7-lr" else ((83.82, 72.39), (68.58, 72.39))
        p = v_base(f"v04J7{v[-2:]}", pro=False)
        F.v_wire(p, a[0], a[1], b[0], b[1])
        F.v_netclass_flag(p, "HV", 83.82, 72.39)
    else:
        raise KeyError(v)
    F.v_write_pro(p)
    return p


# ------------------------------------------------------------------ verify-probe-inert/v05_rule_area.py


def b_v05(v):
    p = v_base(f"v05{v}")
    if v in ("RA1", "RA4"):
        F.v_rule_area(p, [(71.12, 60.96), (81.28, 60.96), (81.28, 71.12), (71.12, 71.12)])
        F.v_netclass_flag(p, "HV", 78.74, 60.96)
    elif v == "RA2":
        F.v_rule_area(p, [(71.12, 70.485), (81.28, 70.485), (81.28, 71.755), (71.12, 71.755)])
        F.v_netclass_flag(p, "HV", 71.12, 71.12)
    elif v == "RA3":
        F.v_rule_area(p, [(71.12, 60.96), (81.28, 60.96), (81.28, 71.12), (71.12, 71.12)])
    else:
        raise KeyError(v)
    return p


# ------------------------------------------------------------------ verify-probe-inert/v06_model_gaps.py


def v06_cross(case):
    p = F.v_new_sheet(case)
    F.v_place(p, R, "R1", 76.2, 76.2)
    F.v_place(p, R, "R3", 83.82, 76.2)          # R3:1 = (83.82,72.39)
    F.v_wire(p, 83.82, 72.39, 83.82, 62.23)     # F
    F.v_wire(p, 76.2, 69.85, 91.44, 69.85)      # D
    return p


def b_v06(v):
    if v == "OV":
        p = F.v_new_sheet("v06OV")
        F.v_place(p, R, "R1", 76.2, 76.2)
        F.v_place(p, R, "R3", 93.98, 76.2)
        F.v_wire(p, 76.2, 69.85, 86.36, 69.85)
        F.v_wire(p, 81.28, 69.85, 93.98, 69.85)
        F.v_wire(p, 93.98, 69.85, 93.98, 72.39)
        F.v_label(p, "FOO", 93.98, 69.85)
    elif v in ("BE-lr", "BE-rl"):
        a, b = ((76.2, 69.85), (86.36, 69.85)) if v == "BE-lr" else ((86.36, 69.85), (76.2, 69.85))
        p = F.v_new_sheet(f"v06BE{v[-2:]}")
        F.v_place(p, R, "R1", 76.2, 76.2)
        F.v_place(p, R, "R3", 83.82, 59.69)
        F.v_wire(p, a[0], a[1], b[0], b[1])
        F.v_bus_entry(p, 81.28, 69.85, 2.54, -2.54)
        F.v_wire(p, 83.82, 67.31, 83.82, 63.5)
        F.v_label(p, "FOO", 83.82, 67.31)
    elif v in ("BD", "BD-ctl"):
        p = F.v_new_sheet("v06BD" if v == "BD" else "v06BDc")
        F.v_place(p, R, "R1", 76.2, 76.2)
        F.v_place(p, R, "R2", 96.52, 76.2)
        F.v_wire(p, 96.52, 72.39, 96.52, 69.85)
        F.v_label(p, "SIG", 96.52, 69.85)
        F.v_place(p, R, "R3", 83.82, 76.2)
        F.v_bus(p, 83.82, 62.23, 83.82, 72.39)
        F.v_wire(p, 76.2, 69.85, 91.44, 69.85)
        if v == "BD":
            F.v_netclass_flag(p, "HV", 83.82, 69.85)
    elif v == "L1":
        p = v06_cross("v06L1")
        F.v_label(p, "LBL", "83.8201", "69.8501")
    elif v == "L0":
        p = v06_cross("v06L0")
        F.v_label(p, "LBL", 83.82, 69.85)
    elif v == "DX":
        p = v06_cross("v06DX")
        F.v_netclass_flag(p, "HV", 83.82, 69.85)
    else:
        raise KeyError(v)
    F.v_write_pro(p)
    return p


# ------------------------------------------------------------------ verify-probe-inert/v07_misc.py


def b_v07(v):
    if v == "NC":
        p = v_base("v07NC")
        F.v_wire(p, 76.2, 69.85, 86.36, 69.85)
        F.v_no_connect(p, 86.36, 69.85)
    elif v == "PL1":
        p = v_base("v07PL1")
        F.v_polyline(p, [(68.58, 72.39), (83.82, 72.39)])
    elif v == "PL2":
        p = v_base("v07PL2")
        F.v_polyline(p, [(68.58, 69.85), (83.82, 69.85)])
    elif v in ("AL", "AL-remedy"):
        p = v_base("v07AL", sig=False)
        F.v_netclass_flag(p, "HV", 86.36, 72.39)
    elif v == "NT":
        p = v_base("v07NT")
        lib = F.vh_lib()
        rlib.SCH.place_component(lib_id="vlib:VH", reference="U1", value="VH", x=60.96, y=72.39,
                                 symbol_lib_path=lib, schematic_path=p)
        F.v_wire(p, 76.2, 72.39, 66.04, 72.39)
    else:
        raise KeyError(v)
    return p


# ------------------------------------------------------------------ jobs

CASE_ORDER = [f"INERT-{i:02d}" for i in range(1, 17)]


def J(stem, sub, cases, builder, steps, source, script, sub_desc=None):
    cases = sorted(cases, key=CASE_ORDER.index)
    d = PI if source == "probe" else VI
    return Job(cases, f"{stem}/{sub}", f"{d}/{script} {sub_desc or sub}", source, builder,
               args=(sub,), steps=steps)


JOBS = [
    # s01_netclass.py
    J("s01_netclass", "1a", ["INERT-01"], b_s01, [W()], "probe", "s01_netclass.py"),
    J("s01_netclass", "1b", ["INERT-01"], b_s01, [W()], "probe", "s01_netclass.py"),
    J("s01_netclass", "1c", ["INERT-01"], b_s01, [W()], "probe", "s01_netclass.py"),
    J("s01_netclass", "1d", ["INERT-01"], b_s01, [W()], "probe", "s01_netclass.py"),
    J("s01_netclass", "1e", ["INERT-01", "INERT-14"], b_s01, [CP12()], "probe", "s01_netclass.py"),
    J("s01_netclass", "1f-lr", ["INERT-01", "INERT-06"], b_s01, [W()], "probe", "s01_netclass.py"),
    J("s01_netclass", "1f-rl", ["INERT-01", "INERT-06"], b_s01, [W()], "probe", "s01_netclass.py"),
    J("s01_netclass", "1g", ["INERT-16"], b_s01, [W()], "probe", "s01_netclass.py"),
    # s02_latent_t.py
    J("s02_latent_t", "2a", ["INERT-02"], b_s02, [W()], "probe", "s02_latent_t.py"),
    J("s02_latent_t", "2b", ["INERT-03"], b_s02, [W()], "probe", "s02_latent_t.py"),
    # s03_label_crossing.py (3b: the Plit variant is P-only; same fixture)
    J("s03_label_crossing", "3a", ["INERT-16"], b_s03, [W()], "probe", "s03_label_crossing.py"),
    J("s03_label_crossing", "3b", ["INERT-11", "INERT-16"], b_s03, [W()], "probe",
      "s03_label_crossing.py"),
    J("s03_label_crossing", "3c", ["INERT-10"], b_s03, [W()], "probe", "s03_label_crossing.py"),
    J("s03_label_crossing", "ctrl", ["INERT-16"], b_s03, [W()], "probe", "s03_label_crossing.py",
      "control"),
    # s04_lone_items.py
    J("s04_lone_items", "4a", ["INERT-04"], b_s04, [W()], "probe", "s04_lone_items.py"),
    J("s04_lone_items", "4b", ["INERT-16"], b_s04, [W()], "probe", "s04_lone_items.py"),
    J("s04_lone_items", "4c", ["INERT-04", "INERT-14"], b_s04, [CP12()], "probe",
      "s04_lone_items.py"),
    J("s04_lone_items", "4d", ["INERT-06"], b_s04, [W()], "probe", "s04_lone_items.py"),
    J("s04_lone_items", "4d2-lr", ["INERT-01", "INERT-06"], b_s04, [W()], "probe",
      "s04_lone_items.py"),
    J("s04_lone_items", "4d2-rl", ["INERT-01", "INERT-06"], b_s04, [W()], "probe",
      "s04_lone_items.py"),
    J("s04_lone_items", "4e1", ["INERT-13"], b_s04, [W()], "probe", "s04_lone_items.py"),
    J("s04_lone_items", "4e2", ["INERT-13", "INERT-16"], b_s04, [W()], "probe", "s04_lone_items.py"),
    J("s04_lone_items", "4e3", ["INERT-13", "INERT-16"], b_s04, [W()], "probe", "s04_lone_items.py"),
    J("s04_lone_items", "4f", ["INERT-05"], b_s04, [W()], "probe", "s04_lone_items.py"),
    # s05_hidden_nc.py
    J("s05_hidden_nc", "5a", ["INERT-16"], b_s05, [W()], "probe", "s05_hidden_nc.py"),
    J("s05_hidden_nc", "5b", ["INERT-03"], b_s05, [W()], "probe", "s05_hidden_nc.py"),
    J("s05_hidden_nc", "5c", ["INERT-15", "INERT-16"], b_s05, [W()], "probe", "s05_hidden_nc.py"),
    J("s05_hidden_nc", "5d", ["INERT-15"], b_s05, [W()], "probe", "s05_hidden_nc.py"),
    # s06_model_gaps.py
    J("s06_model_gaps", "6a", ["INERT-07"], b_s06, [W()], "probe", "s06_model_gaps.py"),
    J("s06_model_gaps", "6b-lr", ["INERT-08"], b_s06, [W()], "probe", "s06_model_gaps.py"),
    J("s06_model_gaps", "6b-rl", ["INERT-08"], b_s06, [W()], "probe", "s06_model_gaps.py"),
    # s07_bus_directive.py
    J("s07_bus_directive", "7a", ["INERT-09"], b_s07, [W()], "probe", "s07_bus_directive.py"),
    J("s07_bus_directive", "7b", ["INERT-09", "INERT-16"], b_s07, [W()], "probe",
      "s07_bus_directive.py"),
    # s08_nc_on_dangling.py
    J("s08_nc_on_dangling", "8", ["INERT-12"], b_s08, [W()], "probe", "s08_nc_on_dangling.py"),
    # v01_netclass.py
    *[J("v01_netclass", v, ["INERT-01"] + (["INERT-14"] if v == "e" else []), b_v01,
        [CP12() if v == "e" else W()], "verify", "v01_netclass.py") for v in "abcdefg"],
    # v02_latent.py
    J("v02_latent", "T", ["INERT-02"], b_v02, [W()], "verify", "v02_latent.py"),
    J("v02_latent", "X", ["INERT-03"], b_v02, [W()], "verify", "v02_latent.py"),
    J("v02_latent", "H", ["INERT-03"], b_v02, [W()], "verify", "v02_latent.py"),
    # v04_junctions.py (J5-lr/J5-rl are the docstring's J5/J6: no case names them; INERT-06)
    J("v04_junctions", "J1", ["INERT-04"], b_v04, [W()], "verify", "v04_junctions.py"),
    J("v04_junctions", "J2", ["INERT-04", "INERT-14"], b_v04, [CP12()], "verify",
      "v04_junctions.py"),
    J("v04_junctions", "J3", ["INERT-06"], b_v04, [W()], "verify", "v04_junctions.py"),
    *[J("v04_junctions", v, ["INERT-06"], b_v04, [W()], "verify", "v04_junctions.py")
      for v in ("J4-lr", "J4-rl", "J5-lr", "J5-rl", "J7-lr", "J7-rl")],
    # v05_rule_area.py
    *[J("v05_rule_area", v, ["INERT-05"], b_v05, [W()], "verify", "v05_rule_area.py")
      for v in ("RA1", "RA2", "RA3")],
    J("v05_rule_area", "RA4", ["INERT-05"], b_v05, [wptn(W_SIG, "SIG", direction="left")],
      "verify", "v05_rule_area.py"),
    # v06_model_gaps.py (L1: P1iu, DX: Plit are P-only variants of the same fixture)
    J("v06_model_gaps", "OV", ["INERT-07"], b_v06, [W()], "verify", "v06_model_gaps.py"),
    J("v06_model_gaps", "BE-lr", ["INERT-08"], b_v06, [W()], "verify", "v06_model_gaps.py"),
    J("v06_model_gaps", "BE-rl", ["INERT-08"], b_v06, [W()], "verify", "v06_model_gaps.py"),
    J("v06_model_gaps", "BD", ["INERT-09"], b_v06, [W()], "verify", "v06_model_gaps.py"),
    J("v06_model_gaps", "BD-ctl", ["INERT-09"], b_v06, [W()], "verify", "v06_model_gaps.py"),
    J("v06_model_gaps", "L1", ["INERT-10"], b_v06, [W()], "verify", "v06_model_gaps.py"),
    J("v06_model_gaps", "L0", ["INERT-10"], b_v06, [W()], "verify", "v06_model_gaps.py"),
    J("v06_model_gaps", "DX", ["INERT-11"], b_v06, [W()], "verify", "v06_model_gaps.py"),
    # v07_misc.py (PLx is hand-built geometry, no tool call: not_rerun.json)
    J("v07_misc", "NC", ["INERT-12"], b_v07, [W()], "verify", "v07_misc.py"),
    J("v07_misc", "PL1", ["INERT-13"], b_v07, [W()], "verify", "v07_misc.py"),
    J("v07_misc", "PL2", ["INERT-13"], b_v07, [W()], "verify", "v07_misc.py"),
    J("v07_misc", "AL", ["INERT-14"], b_v07, [CP12()], "verify", "v07_misc.py"),
    # groups override: both requested pads in ONE group. With rlib's one-set-per-pad default,
    # judge3 counts the requested join of R1:1's and R2:1's nets (through NETX) as a bad merge,
    # because a merge must fall inside a single group's allowed nets.
    J("v07_misc", "AL-remedy", ["INERT-14"], b_v07,
      [wptn([("R1", "1"), ("R2", "1")], "NETX", groups=[{("R1", "1"), ("R2", "1")}])],
      "verify", "v07_misc.py"),
    J("v07_misc", "NT", ["INERT-15"], b_v07, [W()], "verify", "v07_misc.py"),
]

_ids = [j.variant for j in JOBS]
assert len(_ids) == len(set(_ids)), "duplicate variant ids"

if __name__ == "__main__":
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    only = sys.argv[2:] or None
    out = None
    if only and only[0].startswith("test:"):
        out = rlib.WORK / "test_inert.jsonl"
        only = [only[0][5:], *only[1:]]
    rlib.main(JOBS, "inert", SCRIPT, out=out, workers=workers, only=only)
