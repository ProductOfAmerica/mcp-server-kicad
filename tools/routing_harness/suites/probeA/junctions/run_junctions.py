"""Junctions category of the probeA rerun: design M2 vs design K, judged by judge3 through rlib.

Ports every scenario of the junction cases (coarse/caseidx/junctions.json): the probe scripts in
probe-junctions (J1 to J7, J9) and the verifier scripts in verify-probe-junctions named by the
cases (V1 to V9b; V0 is a calibration with no tool call, V10 belongs to another category).
Fixture code is copied from the originals (pj.py, vj.py and each script) with every output path
moved into rlib's per-job directory. True pin ends come from m2_impl.pin_positions (KiCad's
transform), never from the repo's _get_pin_pos_cst.

Setup calls that only construct a fixture stay in the builders exactly as the originals made
them (raw wires, repo add_wires / connect_pins as compat54's frozen 54160c1 copies,
set_component_property, and the P / M / B
connect_pins that J1, J3, V4 and V4c use as "how the first wire was made"; P = proto's
proposal_impl, M = proto's mine_impl, both imported read-only). The calls the originals judged
on P / M / B become rlib steps run on M2 and K. Sequences the design under test makes itself
(J9, V8) are multi-step jobs. Added variants (marked in their note): J3/V4/V4c "own_*", where the
first connection is made by the design under test (step 1) before the judged second call, the
analog of the originals' P_fwd / P_rev / M_connect fixtures for M2 and K.

Hand-edit controls (L, L2, V7b) and the board readings of J2d / V2d are not tool calls of a
design and are listed in not_rerun.json.

Run: uv run python tools/routing_harness/suites/probeA/junctions/run_junctions.py [workers] [--test]
     [variant-substring ...]
"""

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)  # puts suites/probeA on sys.path, for rlib

import rlib  # noqa: E402
from rlib import H, Job, cp, wptn  # noqa: E402

import mine_impl as M  # noqa: E402  (fixture setup only)
import proposal_impl as P  # noqa: E402  (fixture setup only)
import compat54  # noqa: E402  (frozen 54160c1 add_wires and connect_pins: fixture setup)

B = H.schematic  # repo main tools (fixture setup only)
SCRIPT = str(Path(__file__).resolve())
PJ = "probe-junctions"
VJ = "verify-probe-junctions"


# ------------------------------------------------------------------ integers (pj.py, vj.py)


def iu(v) -> int:
    s = float(v) * 10000.0
    return int(s + 0.5) if s >= 0 else -int(-s + 0.5)


def mm(i: int) -> str:
    a = abs(i)
    s = f"{a // 10000}.{a % 10000:04d}".rstrip("0").rstrip(".")
    return ("-" + s) if i < 0 else (s or "0")


def pin_xy(p, ref, num):
    """KiCad-true pin end in IU (m2_impl.pin_positions), first copy."""
    x, y = rlib.pos(p, ref, num)
    return iu(x), iu(y)


# ------------------------------------------------------------------ writers (pj.py raw_wire)


def j_wire(p, a, b):
    """pj.raw_wire: raw wire between IU points, no auto-junction (harness.wire)."""
    H.wire(p, a[0] / 1e4, a[1] / 1e4, b[0] / 1e4, b[1] / 1e4)


def j_label(p, text, q, rot=0):
    H.label(p, text, q[0] / 1e4, q[1] / 1e4, rot=rot)


# ------------------------------------------------------------------ writers (vj.py, same text)


def v_wire(p, a, b):
    H.splice(p, "wire", f"(wire (pts (xy {mm(a[0])} {mm(a[1])}) (xy {mm(b[0])} {mm(b[1])})) "
             f"(stroke (width 0) (type default)) (uuid \"x\"))".encode())


def v_label(p, text, q, rot=0):
    H.splice(p, "label", f"(label \"{text}\" (at {mm(q[0])} {mm(q[1])} {rot}) "
             f"(effects (font (size 1.27 1.27)) (justify left bottom)) (uuid \"x\"))".encode())


def v_junction(p, q):
    H.splice(p, "junction", f"(junction (at {mm(q[0])} {mm(q[1])}) (diameter 0) (color 0 0 0 0) "
             f"(uuid \"x\"))".encode())


def v_place(p, lib, ref, x, y, rot=0, value=None):
    return H.place(p, lib, ref, x, y, rot=rot, value=value)


def add_wire(p, a, b):
    """Repo add_wires from IU a to IU b (compat54's frozen 54160c1 copy: the auto-junction code
    the originals used)."""
    compat54.add_wires([{"x1": a[0] / 1e4, "y1": a[1] / 1e4, "x2": b[0] / 1e4, "y2": b[1] / 1e4}],
                       schematic_path=p)


# ------------------------------------------------------------------ probe builders (J*)


def b_J1(variant):
    p = rlib.fresh(variant)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 101.6, 81.28)
    a, b = pin_xy(p, "R1", "1"), pin_xy(p, "R2", "2")
    if variant == "raw_pin_first":
        j_wire(p, a, b)
    elif variant == "raw_far_first":
        j_wire(p, b, a)
    elif variant == "B_add_wires":
        add_wire(p, a, b)
    elif variant == "M_connect":
        M.connect_pins("R1", "1", "R2", "2", schematic_path=p)
    elif variant == "P_connect":
        P.connect_pins("R1", "1", "R2", "2", schematic_path=p)
    elif variant == "B_connect":
        compat54.connect_pins("R1", "1", "R2", "2", schematic_path=p)
    else:
        raise ValueError(variant)
    return p


def b_J2(variant):
    p = rlib.fresh(variant)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R3", 88.9, 91.44)
    H.place(p, "Device:R", "R4", 114.3, 91.44)
    r3, r4 = pin_xy(p, "R3", "2"), pin_xy(p, "R4", "2")
    if variant == "a_label_at_start":
        j_wire(p, r3, r4)
        j_label(p, "N", r3)
    elif variant == "b_label_at_end":
        j_wire(p, r4, r3)
        j_label(p, "N", r3)
    elif variant == "c_label_far_end":
        j_wire(p, r3, r4)
        j_label(p, "N", r4)
    else:
        raise ValueError(variant)
    return p


def b_J2b(variant):
    p = rlib.fresh(variant)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 114.3, 101.6)
    a, r2 = pin_xy(p, "R1", "1"), pin_xy(p, "R2", "1")
    free = (888900, a[1])
    if variant == "a_label_end_first":
        j_wire(p, free, r2)
    elif variant == "b_pin_end_first":
        j_wire(p, r2, free)
    else:
        raise ValueError(variant)
    j_label(p, "N", free)
    return p


def b_J2c(variant, footprints=False):
    """J2c (and J2d with footprints=True: J2c fixture a plus a footprint on every part)."""
    p = rlib.fresh(variant)
    H.place(p, "Device:R", "R10", 76.2, 88.9, rot=90)
    H.place(p, "Device:R", "R11", 152.4, 88.9, rot=90)
    for i, x in enumerate((101.6, 114.3, 127.0), start=1):
        H.place(p, "Device:C", f"C{i}", x, 95.25)
    a, b = pin_xy(p, "R10", "2"), pin_xy(p, "R11", "1")
    if variant == "b_far_end_first":
        j_wire(p, b, a)
    else:
        j_wire(p, a, b)
    j_label(p, "VCC", a)
    if footprints:
        for ref in ("R10", "R11"):
            B.set_component_property(reference=ref, key="Footprint",
                                     value="Resistor_SMD:R_0603_1608Metric", schematic_path=p)
        for i in (1, 2, 3):
            B.set_component_property(reference=f"C{i}", key="Footprint",
                                     value="Capacitor_SMD:C_0603_1608Metric", schematic_path=p)
    return p


def b_J3(variant):
    p = rlib.fresh(variant)
    H.place(p, "Device:R", "R1", 101.6, 81.28)
    H.place(p, "Device:R", "R2", 101.6, 121.92)
    H.place(p, "Device:R", "R3", 121.92, 101.6)
    a, b, c = pin_xy(p, "R1", "2"), pin_xy(p, "R2", "1"), pin_xy(p, "R3", "1")
    corner = (a[0], c[1])
    if variant == "raw_down":
        j_wire(p, a, b)
    elif variant == "raw_up":
        j_wire(p, b, a)
    elif variant == "presplit":
        j_wire(p, a, corner)
        j_wire(p, corner, b)
    elif variant == "P_connect":
        P.connect_pins("R1", "2", "R2", "1", schematic_path=p)
    elif variant == "M_connect":
        M.connect_pins("R1", "2", "R2", "1", schematic_path=p)
    elif variant == "B_connect":
        compat54.connect_pins("R1", "2", "R2", "1", schematic_path=p)
    elif variant == "own_connect":
        pass  # added variant: step 1 is the design's own connect_pins(R1:2, R2:1)
    else:
        raise ValueError(variant)
    return p


def b_J4(direction):
    p = rlib.fresh(direction)
    H.place(p, "Device:R", "R1", 101.6, 81.28)
    H.place(p, "Device:R", "R2", 101.6, 121.92)
    H.place(p, "Device:R", "R3", 121.92, 101.6)
    H.place(p, "Device:R", "R4", 127.0, 118.11, rot=90)
    a, b, c = pin_xy(p, "R1", "2"), pin_xy(p, "R2", "1"), pin_xy(p, "R3", "1")
    tee = (a[0], c[1])
    s, e = (a, b) if direction == "down" else (b, a)
    add_wire(p, s, e)
    add_wire(p, c, tee)
    j_label(p, "N", a)
    return p


J5J = (1016000, 965200)


def b_J5(variant):
    p = rlib.fresh(variant)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    if variant != "a_lone":
        net = "N" if variant == "b_tee_N" else "OTHER"
        H.place(p, "Device:R", "R6", 127.0, 96.52, rot=90)  # R6:1 at (123.19, 96.52)
        r6 = pin_xy(p, "R6", "1")
        j_wire(p, (889000, J5J[1]), J5J)  # left arm, label at its free end
        j_wire(p, J5J, r6)  # right arm to R6:1
        H.label(p, net, 88.9, 96.52)
    H.junction(p, J5J[0] / 1e4, J5J[1] / 1e4)
    return p


def b_J6(variant):
    p = rlib.fresh(variant)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 127.0, 97.79, rot=90)
    H.place(p, "Device:R", "R5", 76.2, 97.79, rot=90)
    b, r5 = pin_xy(p, "R2", "1"), pin_xy(p, "R5", "2")
    if variant == "a_from_R2":
        j_wire(p, b, r5)
    elif variant == "b_from_R5":
        j_wire(p, r5, b)
    else:
        raise ValueError(variant)
    return p


def b_J7(variant):
    p = rlib.fresh(variant)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 101.6, 81.28)
    H.place(p, "Device:R", "R3", 88.9, 97.79, rot=90)
    a, r2, r3 = pin_xy(p, "R1", "1"), pin_xy(p, "R2", "2"), pin_xy(p, "R3", "2")
    if variant == "a_up_from_pin":
        j_wire(p, a, r2)
    elif variant == "b_up_to_pin":
        j_wire(p, r2, a)
    else:
        raise ValueError(variant)
    j_wire(p, a, r3)
    H.junction(p, a[0] / 1e4, a[1] / 1e4)
    return p


def b_J9():
    p = rlib.fresh("base")
    for i, x in enumerate((101.6, 127.0, 152.4, 177.8), start=1):
        H.place(p, "Device:R", f"R{i}", x, 101.6)
    return p


# ------------------------------------------------------------------ verifier builders (V*)


def b_V1(variant):
    p = rlib.fresh(variant)
    v_place(p, "Device:R", "R1", 101.6, 101.6)
    v_place(p, "Device:R", "R2", 101.6, 78.74)
    a, b = pin_xy(p, "R1", "1"), pin_xy(p, "R2", "2")
    if variant in ("pin_first", "stub3"):
        v_wire(p, a, b)
    elif variant == "far_first":
        v_wire(p, b, a)
    elif variant == "addwires":
        add_wire(p, a, b)
    else:
        raise ValueError(variant)
    return p


def b_V2(variant):
    p = rlib.fresh(variant)
    v_place(p, "Device:R", "R1", 127.0, 127.0)
    v_place(p, "Device:R", "R3", 109.22, 116.84)
    v_place(p, "Device:R", "R4", 149.86, 116.84)
    r3, r4 = pin_xy(p, "R3", "2"), pin_xy(p, "R4", "2")
    if variant in ("a", "c", "d"):
        v_wire(p, r3, r4)
    elif variant == "b":
        v_wire(p, r4, r3)
    else:
        raise ValueError(variant)
    v_label(p, "N", r3)
    if variant == "c":
        v_label(p, "N", r4)
    return p


def b_V2c(variant, footprints=False):
    """V2c (and V2d with footprints=True: V2c power_fwd plus a footprint on every part)."""
    p = rlib.fresh(variant)
    v_place(p, "Device:R", "R10", 63.5, 76.2, rot=90)
    v_place(p, "Device:R", "R11", 139.7, 76.2, rot=90)
    v_place(p, "Device:C", "C1", 88.9, 82.55)
    v_place(p, "Device:C", "C2", 114.3, 82.55)
    a, b = pin_xy(p, "R10", "2"), pin_xy(p, "R11", "1")
    if variant == "label_rev":
        v_wire(p, b, a)
    elif variant in ("label_fwd", "power_fwd"):
        v_wire(p, a, b)
    else:
        raise ValueError(variant)
    if variant.startswith("power"):
        v_place(p, "power:VCC", "#PWR01", a[0] / 1e4, a[1] / 1e4, value="VCC")
    else:
        v_label(p, "VCC", a)
    if footprints:
        for ref, fp in (("R10", "Resistor_SMD:R_0603_1608Metric"),
                        ("R11", "Resistor_SMD:R_0603_1608Metric"),
                        ("C1", "Capacitor_SMD:C_0603_1608Metric"),
                        ("C2", "Capacitor_SMD:C_0603_1608Metric")):
            B.set_component_property(reference=ref, key="Footprint", value=fp, schematic_path=p)
    return p


V3F = (1092200, 1231900)


def b_V3(variant):
    p = rlib.fresh(variant)
    v_place(p, "Device:R", "R1", 127.0, 127.0)
    v_place(p, "Device:R", "R2", 144.78, 127.0)
    r2 = pin_xy(p, "R2", "1")
    if variant == "from_pin":
        v_wire(p, r2, V3F)
    elif variant in ("from_label", "foreign_from_label"):
        v_wire(p, V3F, r2)
    else:
        raise ValueError(variant)
    v_label(p, "OTHER" if variant.startswith("foreign") else "N", V3F)
    return p


def b_V4(variant):
    """V4, V4b and V4c share this geometry and these first-connection fixtures."""
    p = rlib.fresh(variant)
    v_place(p, "Device:R", "R1", 76.2, 63.5)
    v_place(p, "Device:R", "R2", 76.2, 106.68)
    v_place(p, "Device:R", "R3", 101.6, 83.82)
    a, b, c = pin_xy(p, "R1", "2"), pin_xy(p, "R2", "1"), pin_xy(p, "R3", "1")
    corner = (a[0], c[1])
    if variant == "raw_down":
        v_wire(p, a, b)
    elif variant == "raw_up":
        v_wire(p, b, a)
    elif variant == "presplit":
        v_wire(p, a, corner)
        v_wire(p, corner, b)
    elif variant == "P_fwd":
        P.connect_pins("R1", "2", "R2", "1", schematic_path=p)
    elif variant == "P_rev":
        P.connect_pins("R2", "1", "R1", "2", schematic_path=p)
    elif variant == "B_fwd":
        compat54.connect_pins("R1", "2", "R2", "1", schematic_path=p)
    elif variant in ("own_fwd", "own_rev"):
        pass  # added variant: step 1 is the design's own first connection
    else:
        raise ValueError(variant)
    return p


def b_V5(variant):
    p = rlib.fresh(variant)
    v_place(p, "Device:R", "R1", 127.0, 127.0)
    v_place(p, "Device:R", "R2", 152.4, 123.19, rot=90)
    v_place(p, "Device:R", "R5", 101.6, 123.19, rot=90)
    b, r5 = pin_xy(p, "R2", "1"), pin_xy(p, "R5", "2")
    if variant == "from_R2":
        v_wire(p, b, r5)
    elif variant == "from_R5":
        v_wire(p, r5, b)
    else:
        raise ValueError(variant)
    return p


def b_V6(variant):
    p = rlib.fresh(variant)
    v_place(p, "Device:R", "R1", 127.0, 127.0)
    J = (1270000, 1193800) if variant == "lone_mid2" else (1270000, 1219200)
    if variant.startswith("tee"):
        v_place(p, "Device:R", "R6", 143.51, 121.92, rot=90)
        r6 = pin_xy(p, "R6", "1")
        v_wire(p, (1143000, J[1]), J)
        v_wire(p, J, r6)
        v_label(p, "N" if variant == "tee_N" else "OTHER", (1143000, J[1]))
    elif variant not in ("lone", "lone_mid2"):
        raise ValueError(variant)
    v_junction(p, J)
    return p


def b_V7(direction):
    p = rlib.fresh(direction)
    v_place(p, "Device:R", "R1", 76.2, 63.5)
    v_place(p, "Device:R", "R2", 76.2, 106.68)
    v_place(p, "Device:R", "R3", 101.6, 83.82)
    a, b, c = pin_xy(p, "R1", "2"), pin_xy(p, "R2", "1"), pin_xy(p, "R3", "1")
    t = (a[0], c[1])
    s, e = (a, b) if direction == "down" else (b, a)
    add_wire(p, s, e)
    add_wire(p, c, t)
    v_label(p, "N", a)
    return p


def b_V8():
    p = rlib.fresh("base")
    for i, x in enumerate((76.2, 101.6, 127.0, 152.4), start=1):
        v_place(p, "Device:R", f"R{i}", x, 88.9)
    return p


def b_V8b():
    p = rlib.fresh("base")
    v_place(p, "Device:R", "R1", 76.2, 88.9)
    v_place(p, "Device:R", "R2", 101.6, 88.9)
    compat54.connect_pins(ref1="R1", pin1="1", ref2="R2", pin2="1", schematic_path=p)  # repo, setup
    return p


def b_V9(variant):
    """V9 and V9b share this geometry."""
    p = rlib.fresh(variant)
    v_place(p, "Device:R", "R1", 127.0, 127.0)
    v_place(p, "Device:R", "R2", 127.0, 104.14)
    v_place(p, "Device:R", "R3", 109.22, 123.19, rot=90)
    a, r2, r3 = pin_xy(p, "R1", "1"), pin_xy(p, "R2", "2"), pin_xy(p, "R3", "2")
    if variant == "up_from_pin":
        v_wire(p, a, r2)
    elif variant == "up_to_pin":
        v_wire(p, r2, a)
    elif variant != "side_only":
        raise ValueError(variant)
    v_wire(p, a, r3)
    v_junction(p, a)
    return p


# ------------------------------------------------------------------ jobs

CASES = {  # script stem -> cases whose scenario includes it, in caseidx order
    "J1_own_wire": ["JCT-P1", "JCT-M6"],
    "J2_stub_end_on_N_wire": ["JCT-P2", "JCT-M6"],
    "J2b_pin_on_N_wire": ["JCT-P3", "JCT-M4", "JCT-M6"],
    "J2c_cap_row_on_rail": ["JCT-P2", "JCT-M6"],
    "J2d_pcb_consequence": ["JCT-P2", "JCT-M6"],
    "J3_d3_connect": ["JCT-P4", "JCT-M2", "JCT-M6"],
    "J4_preexisting_junctions": ["JCT-P7", "JCT-M1", "JCT-M5", "JCT-M6"],
    "J5_stub_through_junction": ["JCT-P6", "JCT-M6"],
    "J6_connect_pin_on_own_wire": ["JCT-P5", "JCT-M4", "JCT-M6"],
    "J7_pin_at_presplit_junction": ["JCT-M3", "JCT-M6"],
    "J9_side_autolabel": ["JCT-P8"],
    "V1_own_wire": ["JCT-P1"],
    "V2_stub_end_on_N": ["JCT-P2"],
    "V2c_rail": ["JCT-P2"],
    "V2d_board": ["JCT-P2"],
    "V3_pin_on_N_wire": ["JCT-P3", "JCT-M4"],
    "V4_d3": ["JCT-P4", "JCT-M2"],
    "V4b_m_fallback": ["JCT-M2"],
    "V4c_second_call_swapped": ["JCT-P4", "JCT-M2"],
    "V5_connect_pin_on_own_wire": ["JCT-P5", "JCT-M4"],
    "V6_stub_through_junction": ["JCT-P6"],
    "V7_preexisting_junctions": ["JCT-P7", "JCT-M1", "JCT-M5"],
    "V8_autolabel": ["JCT-P8"],
    "V8b_repo_autolabel_then_m": ["JCT-P8"],
    "V9_pin_at_presplit_junction": ["JCT-M3"],
    "V9b_m_direction_workaround": ["JCT-M3"],
}
ADDED = "added variant: the first connection is made by the design under test (step 1), the analog" \
        " of the original's P/M/B-made first-connection fixture"

JOBS: list = []


def job(stem, sub, build, args, steps, note="", cases=None):
    """Append one Job. GROUPS OVERRIDE: a wire_pins_to_net step with two or more pads is judged
    with ONE group holding every requested pad. With rlib's default (one group per pad) judge3
    allows each pad's net to merge only with N's nets, so the merge the call is asked to make
    (all listed pads onto N) is flagged as a bad merge whenever two listed pads were on different
    nets before. Every pin here is a pad number on a single sheet whose per-instance reference is
    the Reference property, so the nodes are exactly (ref, pin)."""
    src = "probe" if stem.startswith("J") else "verify"
    d = PJ if src == "probe" else VJ
    for st in steps:
        if st["kind"] == "wptn" and st["groups"] is None and len(st["pins"]) > 1:
            st["groups"] = [{(p["reference"], str(p["pin"])) for p in st["pins"]}]
    JOBS.append(Job(cases or CASES[stem], f"{stem}/{sub}", f"{d}/{stem}.py {sub}", src, build,
                    args=args, steps=steps, note=note))


def wR1N():
    return wptn([("R1", "1")], "N")


# J1: existing straight wire from R1:1 outward; wire_pins_to_net([R1:1], 'N')
for v in ("raw_pin_first", "raw_far_first", "B_add_wires", "M_connect", "P_connect", "B_connect"):
    job("J1_own_wire", v, b_J1, (v,), [wR1N()])

# J2: stub end on the interior of a wire on N
for v in ("a_label_at_start", "b_label_at_end", "c_label_far_end"):
    job("J2_stub_end_on_N_wire", v, b_J2, (v,), [wR1N()])

# J2b: pin end on the interior of N's wire (hand edit L not re-run)
for v in ("a_label_end_first", "b_pin_end_first"):
    job("J2b_pin_on_N_wire", v, b_J2b, (v,), [wR1N()])

# J2c: three caps under a VCC rail, one call
CAPS3 = [("C1", "1"), ("C2", "1"), ("C3", "1")]
for v in ("a_label_end_first", "b_far_end_first"):
    job("J2c_cap_row_on_rail", v, b_J2c, (v,), [wptn(CAPS3, "VCC")])

# J2d: J2c fixture a with footprints; schematic call only (board reading not re-run)
job("J2d_pcb_consequence", "rail", b_J2c, ("a_label_end_first", True), [wptn(CAPS3, "VCC")],
    note="schematic call only; the board reading (update_pcb_from_schematic) is not re-run")

# J3: D3 geometry, second connect_pins judged (first connection = fixture)
for v in ("raw_down", "raw_up", "presplit", "P_connect", "M_connect", "B_connect"):
    for second in (("R2", "1"), ("R1", "2")):
        job("J3_d3_connect", f"{v}/second_{second[0]}_{second[1]}", b_J3, (v,),
            [cp(("R3", "1"), second)])
for second in (("R2", "1"), ("R1", "2")):
    job("J3_d3_connect", f"own_connect/second_{second[0]}_{second[1]}", b_J3, ("own_connect",),
        [cp(("R1", "2"), ("R2", "1")), cp(("R3", "1"), second)], note=ADDED,
        cases=["JCT-P4", "JCT-M2"])

# J4: pre-existing junction on an unsplit wire (repo add_wires)
def j4q():
    return [
        ("Q1", wptn([("R2", "1")], "N")),
        ("Q2", wptn([("R2", "1")], "OTHER")),
        ("Q3", wptn([("R3", "1")], "N")),
        ("Q4", wptn([("R1", "2")], "N")),
        ("Q5", cp(("R2", "1"), ("R3", "1"))),
        ("Q6", cp(("R4", "1"), ("R2", "1"))),
    ]


for d in ("down", "up"):
    for q, st in j4q():
        job("J4_preexisting_junctions", f"{d}/{q}", b_J4, (d,), [st])

# J5: stub interior through an existing junction
for v in ("a_lone", "b_tee_N", "c_tee_OTHER"):
    job("J5_stub_through_junction", v, b_J5, (v,), [wR1N()])

# J6: connect_pins with R1:1 on the interior of R2:1's own wire, and M's suggested fallback
for v in ("a_from_R2", "b_from_R5"):
    job("J6_connect_pin_on_own_wire", f"{v}/connect", b_J6, (v,), [cp(("R1", "1"), ("R2", "1"))])
    job("J6_connect_pin_on_own_wire", f"{v}/wptn_X", b_J6, (v,),
        [wptn([("R1", "1"), ("R2", "1")], "X")],
        note="the original's 'M suggested fallback' call, run as its own job")

# J7: target pin end carries KiCad's pre-split tee (hand edit L not re-run)
for v in ("a_up_from_pin", "b_up_to_pin"):
    job("J7_pin_at_presplit_junction", v, b_J7, (v,), [wR1N()])

# J9: sequences by the design under test
job("J9_side_autolabel", "a", b_J9, (),
    [cp(("R1", "1"), ("R2", "1")), wptn([("R1", "1"), ("R2", "1")], "SIG")])
job("J9_side_autolabel", "b", b_J9, (),
    [cp(("R1", "2"), ("R2", "2")), cp(("R3", "2"), ("R4", "2")), cp(("R2", "2"), ("R3", "2"))])

# V1: own wire 15.24 mm
for v in ("pin_first", "far_first", "addwires", "stub3"):
    kw = {"stub_length": 5.08} if v == "stub3" else {}
    job("V1_own_wire", v, b_V1, (v,), [wptn([("R1", "1")], "N", **kw)])

# V2: stub end on N's wire interior (d: stub 1.27 control)
for v in ("a", "b", "c", "d"):
    kw = {"stub_length": 1.27} if v == "d" else {}
    job("V2_stub_end_on_N", v, b_V2, (v,), [wptn([("R1", "1")], "N", **kw)])

# V2c: two caps under a VCC rail
CAPS2 = [("C1", "1"), ("C2", "1")]
for v in ("label_fwd", "label_rev", "power_fwd"):
    job("V2c_rail", v, b_V2c, (v,), [wptn(CAPS2, "VCC")])

# V2d: V2c power_fwd with footprints; schematic call only
job("V2d_board", "rail", b_V2c, ("power_fwd", True), [wptn(CAPS2, "VCC")],
    note="schematic call only; the board reading (update_pcb_from_schematic) is not re-run")

# V3: pin end on N's wire interior (hand edits L and the foreign fixture not re-run)
for v in ("from_label", "from_pin"):
    job("V3_pin_on_N_wire", v, b_V3, (v,), [wR1N()])

# V4: D3 geometry, second call judged; plus the design's own first connection
for v in ("raw_down", "raw_up", "presplit", "P_fwd", "P_rev", "B_fwd"):
    for second in (("R2", "1"), ("R1", "2")):
        job("V4_d3", f"{v}/second_{second[0]}_{second[1]}", b_V4, (v,), [cp(("R3", "1"), second)])
for v, first in (("own_fwd", (("R1", "2"), ("R2", "1"))), ("own_rev", (("R2", "1"), ("R1", "2")))):
    for second in (("R2", "1"), ("R1", "2")):
        job("V4_d3", f"{v}/second_{second[0]}_{second[1]}", b_V4, (v,),
            [cp(first[0], first[1]), cp(("R3", "1"), second)], note=ADDED)

# V4b: M's suggested fallback on V4's geometry
for v in ("raw_down", "raw_up"):
    job("V4b_m_fallback", v, b_V4, (v,), [wptn([("R3", "1"), ("R2", "1")], "X")])

# V4c: second call with swapped arguments
for v in ("raw_down", "P_fwd", "B_fwd", "raw_up"):
    job("V4c_second_call_swapped", v, b_V4, (v,), [cp(("R2", "1"), ("R3", "1"))])
job("V4c_second_call_swapped", "own_fwd", b_V4, ("own_fwd",),
    [cp(("R1", "2"), ("R2", "1")), cp(("R2", "1"), ("R3", "1"))], note=ADDED)

# V5: connect_pins with R1:1 on R2:1's own wire, and the wire_pins_to_net fallback (L2 not re-run)
for v in ("from_R2", "from_R5"):
    job("V5_connect_pin_on_own_wire", f"{v}/connect", b_V5, (v,), [cp(("R1", "1"), ("R2", "1"))])
    job("V5_connect_pin_on_own_wire", f"{v}/wptn_X", b_V5, (v,),
        [wptn([("R1", "1"), ("R2", "1")], "X")])

# V6: stub through a junction
for v in ("lone", "lone_mid2", "tee_N", "tee_OTHER"):
    kw = {"stub_length": 5.08} if v == "lone_mid2" else {}
    job("V6_stub_through_junction", v, b_V6, (v,), [wptn([("R1", "1")], "N", **kw)])

# V7: pre-existing junction on an unsplit wire (literal-M model query not re-run)
for d in ("down", "up"):
    job("V7_preexisting_junctions", f"{d}/Q1", b_V7, (d,), [wptn([("R2", "1")], "N")])
    job("V7_preexisting_junctions", f"{d}/Q3", b_V7, (d,), [wptn([("R3", "1")], "N")])
    job("V7_preexisting_junctions", f"{d}/Q5", b_V7, (d,), [cp(("R2", "1"), ("R3", "1"))])

# V8: sequences by the design under test
job("V8_autolabel", "a", b_V8, (), [cp(("R1", "1"), ("R2", "1")), wptn([("R1", "1")], "SIG")])
job("V8_autolabel", "a2", b_V8, (),
    [cp(("R1", "1"), ("R2", "1")), wptn([("R1", "1"), ("R2", "1")], "SIG")])
job("V8_autolabel", "b", b_V8, (),
    [cp(("R1", "2"), ("R2", "2")), cp(("R4", "2"), ("R3", "2")), cp(("R2", "2"), ("R3", "2"))])
job("V8_autolabel", "c", b_V8, (), [cp(("R1", "1"), ("R2", "1")), cp(("R2", "1"), ("R3", "1"))])

# V8b: net made by the repo's connect_pins (setup), then naming it
job("V8b_repo_autolabel_then_m", "base", b_V8b, (), [wptn([("R1", "1"), ("R2", "1")], "SIG")])

# V9: pre-split tee at the pin (hand edit L not re-run)
for v in ("up_from_pin", "up_to_pin", "side_only"):
    job("V9_pin_at_presplit_junction", v, b_V9, (v,), [wR1N()])

# V9b: explicit direction='right'
for v in ("up_from_pin", "up_to_pin"):
    job("V9b_m_direction_workaround", v, b_V9, (v,), [wptn([("R1", "1")], "N", direction="right")])


# ------------------------------------------------------------------ not re-run

NOT_RERUN = [
    {"case": "JCT-P2", "origin": f"{PJ}/J2d_pcb_consequence.py board reading",
     "reason": "update_pcb_from_schematic board pad-net reading; judge3 has no board reading. The"
               " schematic call itself runs as job J2d_pcb_consequence/rail."},
    {"case": "JCT-P2", "origin": f"{VJ}/V2d_board.py board reading",
     "reason": "update_pcb_from_schematic board pad-net reading; judge3 has no board reading. The"
               " schematic call itself runs as job V2d_board/rail."},
    {"case": "JCT-P3", "origin": f"{PJ}/J2b_pin_on_N_wire.py L (a_label_end_first, b_pin_end_first)",
     "reason": "hand-edit control (a label N placed on R1:1), not a tool call of a design"},
    {"case": "JCT-P3", "origin": f"{VJ}/V3_pin_on_N_wire.py L (from_label, from_pin)",
     "reason": "hand-edit control (a label N placed on R1:1), not a tool call of a design"},
    {"case": "JCT-P3", "origin": f"{VJ}/V3_pin_on_N_wire.py foreign_from_label",
     "reason": "the original runs only the hand edit L on this fixture; no design call to port"},
    {"case": "JCT-P5", "origin": f"{VJ}/V5_connect_pin_on_own_wire.py L2 (from_R2, from_R5)",
     "reason": "hand-edit control (labels X on R1:1 and R2:1), not a tool call of a design"},
    {"case": "JCT-P7", "origin": f"{VJ}/V7_preexisting_junctions.py literal_m (down, up)",
     "reason": "direct query of mine_impl's P0/C model state, not a tool call"},
    {"case": "JCT-P7", "origin": f"{VJ}/V7b_label_fix.py (down R2:1, up R2:1, up R3:1)",
     "reason": "hand edits only (a label N placed on the pin), no design call"},
    {"case": "JCT-M3", "origin": f"{PJ}/J7_pin_at_presplit_junction.py L (a_up_from_pin, b_up_to_pin)",
     "reason": "hand-edit control (a label N placed on R1:1), not a tool call of a design"},
    {"case": "JCT-M3", "origin": f"{VJ}/V9_pin_at_presplit_junction.py L (3 fixtures)",
     "reason": "hand-edit control (a label N placed on R1:1), not a tool call of a design"},
    {"case": "JCT-P1", "origin": f"{VJ}/V0_calibrate.py",
     "reason": "calibration of the verifier's own readings; no tool call"},
]


if __name__ == "__main__":
    nr = rlib.OUT / "junctions" / "not_rerun.json"
    nr.parent.mkdir(parents=True, exist_ok=True)
    nr.write_text(json.dumps(NOT_RERUN, indent=1), encoding="utf-8")
    argv = sys.argv[1:]
    test = "--test" in argv
    argv = [a for a in argv if a != "--test"]
    workers = int(argv[0]) if argv and argv[0].isdigit() else 1
    only = [a for a in argv if not a.isdigit()] or None
    out = rlib.WORK / "test_junctions.jsonl" if test else None
    rlib.main(JOBS, "junctions", SCRIPT, out=out, workers=workers, only=only)
