"""probeA rerun, category labels: probe-labels (L01-L14) and verify-probe-labels (v01-v09b)
scenarios ported to rlib Jobs and run on design M2 and design K (judge3: kicad-cli measured,
GUI-load proxy not available in this repository).

Fixture code is copied from the originals (probe-labels/plib.py and L*.py, verify-probe-labels/vl.py
and v*.py); harness.py writers (place, wire, label, directive, junction, no_connect) are used
through rlib.H, the same module the originals imported. Every fixture is built with rlib.fresh, so
it lives in the job's own directory. KiCad-only control constructions (labels written by hand, no
tool call) produce no job. The P / Pcap / Pk / M / Ml / B designs of the originals all made the
same call, so each fixture and call is one Job here, run on M2 and K.

Run:  uv run python tools/routing_harness/suites/probeA/labels/run_labels.py
          every job, records_labels.jsonl, 1 worker
      uv run python tools/routing_harness/suites/probeA/labels/run_labels.py --test L01/a,v07/A
          selected jobs into work/test_labels.jsonl
"""

from __future__ import annotations

import json
import uuid as _uuid
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)  # puts suites/probeA on sys.path, for rlib

import rlib  # noqa: E402
from rlib import H, Job, cp, wptn  # noqa: E402

CAT = "labels"
PL = "probe-labels"
VL = "verify-probe-labels"
R = "Device:R"
R1_1 = ("R1", "1")


def chk(p, ref, num, xy):
    """The fixture's hard-coded pin coordinate must be KiCad's (m2_impl.pin_positions)."""
    got = rlib.pos(p, ref, num)
    want = (round(xy[0], 4), round(xy[1], 4))
    if got != want:
        raise AssertionError(f"{ref}:{num} is at {got} by KiCad's transform, fixture assumes {want}")


# ============================================================ copied from probe-labels/plib.py
P_PIN = (101.6, 97.79)   # R1:1 end; outward up; 1x stub end E1
E1 = (101.6, 95.25)

_CLASS_TPL = {
    "bus_width": 12, "clearance": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
    "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1,
    "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
    "track_width": 0.25, "via_diameter": 0.8, "via_drill": 0.4, "wire_width": 6,
}


def pl_write_pro(p, classes=("HV", "LV")):
    """plib.write_pro: a .kicad_pro defining extra net classes, next to the schematic."""
    pro = Path(p).with_suffix(".kicad_pro")
    cls = [dict(_CLASS_TPL, name="Default", priority=2147483647)]
    cls += [dict(_CLASS_TPL, name=c, priority=i) for i, c in enumerate(classes)]
    data = {"meta": {"filename": pro.name, "version": 3},
            "net_settings": {"classes": cls, "meta": {"version": 4}, "net_colors": None,
                             "netclass_assignments": None, "netclass_patterns": []}}
    pro.write_text(json.dumps(data, indent=2))
    return str(pro)


def pl_raw(p, token, sexpr):
    while "@U" in sexpr:
        sexpr = sexpr.replace("@U", str(_uuid.uuid4()), 1)
    node = H._cst.parse(sexpr.encode()).lists[0]
    H._edit(p, lambda root: H.schematic._splice_sch_node(root, token, node))


def pl_polyline(p, pts):
    """Graphic polyline; 2 points parse as an SCH_LINE on LAYER_NOTES."""
    xy = " ".join(f"(xy {H._n(x)} {H._n(y)})" for x, y in pts)
    pl_raw(p, "polyline", f'(polyline (pts {xy}) (stroke (width 0) (type default)) (uuid "@U"))')


def pl_stub_label(p, x, y, text, dy=-2.54, kind="label"):
    H.wire(p, x, y, x, round(y + dy, 4))
    H.label(p, text, x, round(y + dy, 4), kind=kind)


def pl_base(name, n_net=True, foo_net=False):
    """R1 at (101.6,101.6) rot 0 is the target (R1:1 at (101.6,97.79), outward up).
    n_net: R9 with a stub and local label 'N'. foo_net: R8 with a stub and local label 'FOO'."""
    p = rlib.fresh(name)
    H.place(p, R, "R1", 101.6, 101.6)
    chk(p, "R1", "1", P_PIN)
    if n_net:
        H.place(p, R, "R9", 152.4, 101.6)
        chk(p, "R9", "1", (152.4, 97.79))
        pl_stub_label(p, 152.4, 97.79, "N")
    if foo_net:
        H.place(p, R, "R8", 177.8, 101.6)
        chk(p, "R8", "1", (177.8, 97.79))
        pl_stub_label(p, 177.8, 97.79, "FOO")
    return p


# ------------------------------------------------------------------ L01_directive_labels.py
def b_L01(key):
    if key == "a":    # lone directive on the stub path
        p = pl_base("L01a")
        H.directive(p, "HV", 101.6, 96.52)
    elif key == "b":  # lone directive exactly at the stub end E1
        p = pl_base("L01b")
        H.directive(p, "HV", *E1)
    elif key == "c":  # directive at the crossing of the stub path with a pinless wire
        p = pl_base("L01c")
        H.wire(p, 95.25, 96.52, 107.95, 96.52)
        H.directive(p, "HV", 101.6, 96.52)
    elif key == "d":  # control: the same pinless crossing wire, no directive
        p = pl_base("L01d")
        H.wire(p, 95.25, 96.52, 107.95, 96.52)
    elif key == "e":  # directive at the crossing of a foreign wire with pins (R3:1-R4:1)
        p = pl_base("L01e")
        H.place(p, R, "R3", 88.9, 100.33)
        H.place(p, R, "R4", 114.3, 100.33)
        chk(p, "R3", "1", (88.9, 96.52))
        chk(p, "R4", "1", (114.3, 96.52))
        H.wire(p, 88.9, 96.52, 114.3, 96.52)
        H.directive(p, "HV", 101.6, 96.52)
    elif key == "f":  # label 'N' on an N-net wire crossing the stub path
        p = pl_base("L01f", foo_net=True)
        H.place(p, R, "R3", 88.9, 100.33)
        H.place(p, R, "R4", 114.3, 100.33)
        H.wire(p, 88.9, 96.52, 114.3, 96.52)
        H.label(p, "N", 101.6, 96.52)
    elif key == "g":  # connect_pins, lone directive on the H-first leg
        p = pl_base("L01g", n_net=False)
        H.place(p, R, "R2", 127, 87.63)
        chk(p, "R2", "2", (127, 91.44))
        H.directive(p, "HV", 114.3, 97.79)
    else:
        raise KeyError(key)
    pl_write_pro(p)
    return p


# ------------------------------------------------------------------ L03_label_at_T.py
def b_L03(key):
    p = pl_base(f"L03{key}")
    if key == "a":
        H.place(p, R, "R5", 88.9, 99.06)
        H.place(p, R, "R6", 127, 99.06)
        chk(p, "R5", "1", (88.9, 95.25))
        chk(p, "R6", "1", (127, 95.25))
        H.wire(p, 88.9, 95.25, 127, 95.25)
        H.label(p, "N", 88.9, 95.25)
    elif key == "b":
        H.place(p, R, "R2", 127, 99.06)
        chk(p, "R2", "1", (127, 95.25))
        H.wire(p, 101.6, 97.79, 88.9, 97.79)
        H.wire(p, 88.9, 97.79, 88.9, 95.25)
        H.wire(p, 88.9, 95.25, 127, 95.25)
    else:
        raise KeyError(key)
    return p


# ------------------------------------------------------------------ L04_stack_at_stub_end.py
def b_L04(key):
    existing = {"a": "N", "b": "M"}[key]
    p = pl_base(f"L04{key}")
    H.place(p, R, "R5", 88.9, 99.06)
    H.place(p, R, "R6", 127, 99.06)
    chk(p, "R5", "1", (88.9, 95.25))
    chk(p, "R6", "1", (127, 95.25))
    H.wire(p, 88.9, 95.25, 127, 95.25)
    if existing == "M":
        H.label(p, "N", 88.9, 95.25)
    H.label(p, existing, *E1)
    return p


# ------------------------------------------------------------------ L05_stack_label_pin_merge.py
def b_L05(key):
    text = {"a": "N", "b": "FOO"}[key]
    p = pl_base(f"L05{key}")
    H.place(p, R, "R5", 88.9, 99.06)
    H.place(p, R, "R6", 127, 99.06)
    H.place(p, R, "R4", 101.6, 91.44)
    chk(p, "R4", "2", E1)
    H.wire(p, 88.9, 95.25, 127, 95.25)
    H.label(p, text, *E1)
    return p


# ------------------------------------------------------------------ L06_stack_on_target_wire.py
L06_CASES = {"a": ("N", "pin"), "b": ("N", "nc"), "c": ("N", "label"), "d": ("FOO", "pin"),
             "e": ("N", "pin+poly")}
Q6 = (114.3, 97.79)


def b_L06(key):
    text, extra = L06_CASES[key]
    p = pl_base(f"L06{key}")
    H.place(p, R, "R2", 127, 101.6)
    chk(p, "R2", "1", (127, 97.79))
    H.wire(p, 101.6, 97.79, 127, 97.79)
    H.label(p, text, *Q6)
    if extra in ("pin", "pin+poly"):
        H.place(p, R, "R4", 114.3, 101.6)
        chk(p, "R4", "1", Q6)
    if extra == "nc":
        H.no_connect(p, *Q6)
    if extra == "label":
        H.label(p, "M", *Q6)
    if extra == "pin+poly":
        pl_polyline(p, [(114.3, 93.98), Q6])
    return p


# ------------------------------------------------------------------ L07_capture_new_stub.py
L07_CASES = {
    "a": (101.6001, 96.52), "b": (101.6002, 96.52), "c": (101.6, 95.2499),
    "d": (101.6001, 95.2499), "e": (101.6, 95.2498), "f": (101.6001, 97.79),
    "g": (101.6, 97.7901),
}


def b_L07(key):
    x, y = L07_CASES[key]
    p = pl_base(f"L07{key}", foo_net=True)
    H.label(p, "FOO", x, y)
    return p


# ------------------------------------------------------------------ L08_capture_model.py
L08_CASES = {"a": (114.3, 97.7901), "b": (127.0001, 97.79), "c": (114.3, 97.7902),
             "d": (127.0001, 97.7901)}


def b_L08(key):
    x, y = L08_CASES[key]
    p = pl_base(f"L08{key}", foo_net=False)
    H.place(p, R, "R2", 127, 101.6)
    chk(p, "R2", "1", (127, 97.79))
    H.wire(p, 101.6, 97.79, 127, 97.79)
    H.label(p, "FOO", x, y)
    return p


# ------------------------------------------------------------------ L09_graphic_polylines.py
def b_L09(key):
    if key == "a":
        p = pl_base("L09a")
        pl_polyline(p, [(95.25, 97.79), (107.95, 97.79)])
    elif key == "b":
        p = pl_base("L09b", foo_net=True)
        pl_polyline(p, [E1, (177.8, 95.25)])
    elif key == "c":
        p = pl_base("L09c")
        H.place(p, R, "R7", 139.7, 101.6)
        pl_stub_label(p, 139.7, 97.79, "BLK")
        H.label(p, "BLK", *E1)
        H.place(p, R, "R4", 114.3, 101.6)
        chk(p, "R4", "1", (114.3, 97.79))
        H.wire(p, 101.6001, 97.79, 114.3, 97.79)
        pl_polyline(p, [(101.6, 99.06), (88.9, 99.06)])
        pl_polyline(p, [(95.25, 97.79), (101.6, 97.79)])
    else:
        raise KeyError(key)
    return p


# ------------------------------------------------------------------ L10_label_on_pin_fallback.py
def _blk(p):
    H.place(p, R, "R7", 139.7, 101.6)
    chk(p, "R7", "1", (139.7, 97.79))
    pl_stub_label(p, 139.7, 97.79, "BLK")
    H.label(p, "BLK", *E1)


def b_L10(key):
    if key == "a":
        p = pl_base("L10a")
        _blk(p)
    elif key == "b":
        p = pl_base("L10b")
        H.place(p, R, "R3", 88.9, 101.6)
        H.place(p, R, "R4", 114.3, 101.6)
        H.wire(p, 88.9, 97.79, 114.3, 97.79)
    elif key == "c":
        p = pl_base("L10c")
        H.place(p, R, "R3", 88.9, 101.6)
        H.place(p, R, "R4", 114.3, 101.6)
        H.place(p, R, "R2", 101.6, 87.63)
        H.wire(p, 88.9, 97.79, 114.3, 97.79)
        H.wire(p, 101.6, 91.44, 101.6, 99.06)
    elif key == "d":
        p = pl_base("L10d", foo_net=True)
        _blk(p)
        H.label(p, "FOO", 101.6001, 97.79)
    elif key == "e":
        p = pl_base("L10e")
        _blk(p)
        H.place(p, R, "R3", 88.9, 101.6)
        H.place(p, R, "R4", 114.3, 101.6)
        chk(p, "R3", "1", (88.9, 97.79))
        H.wire(p, 101.6, 97.79, 88.9, 97.79)
        H.wire(p, 101.6001, 97.79, 114.3, 97.79)
    elif key == "f":
        p = pl_base("L10f", n_net=False)
        H.place(p, R, "R2", 101.6, 87.63)
        chk(p, "R2", "2", (101.6, 91.44))
        H.place(p, R, "R4", 114.3, 101.6)
        H.wire(p, 101.6001, 97.79, 114.3, 97.79)
    else:
        raise KeyError(key)
    return p


# ------------------------------------------------------------------ L14_autolabel_then_wire.py
def b_L14():
    p = pl_base("L14")
    H.place(p, R, "R2", 101.6, 87.63)
    chk(p, "R2", "2", (101.6, 91.44))
    return p


# ------------------------------------------------------------------ L02_label_at_crossing_model.py
L02_CASES = {"a": ("label", (114.3, 97.79)), "b": ("global_label", (114.3, 97.79)),
             "c": ("hierarchical_label", (114.3, 97.79)), "d": ("label", (114.3001, 97.7901)),
             "e": ("label", (114.3, 97.7901))}


def b_L02(key):
    kind, (x, y) = L02_CASES[key]
    p = pl_base(f"L02{key}")
    H.place(p, R, "R2", 127, 101.6)
    H.place(p, R, "R3", 114.3, 86.36)
    H.place(p, R, "R4", 114.3, 109.22)
    chk(p, "R2", "1", (127, 97.79))
    chk(p, "R3", "2", (114.3, 90.17))
    chk(p, "R4", "1", (114.3, 105.41))
    H.wire(p, 101.6, 97.79, 127, 97.79)
    H.wire(p, 114.3, 90.17, 114.3, 105.41)
    H.label(p, "FOO", x, y, kind=kind)
    return p


# ------------------------------------------------------------------ L12_stack_nc_at_stub_end.py
def b_L12(key):
    with_label = {"a": True, "b": False}[key]
    p = pl_base(f"L12{key}")
    H.place(p, R, "R5", 88.9, 99.06)
    H.place(p, R, "R6", 127, 99.06)
    chk(p, "R5", "1", (88.9, 95.25))
    chk(p, "R6", "1", (127, 95.25))
    H.wire(p, 88.9, 95.25, 127, 95.25)
    if with_label:
        H.label(p, "N", *E1)
    else:
        H.label(p, "N", 88.9, 95.25)
    H.no_connect(p, *E1)
    return p


# ------------------------------------------------------------------ L13_wire_near_new_label.py
def b_L13(key):
    y = {"a": 95.2501, "b": 95.2499}[key]
    p = pl_base(f"L13{key}")
    H.place(p, R, "R3", 88.9, 99.06)
    H.place(p, R, "R4", 114.3, 99.06)
    chk(p, "R3", "1", (88.9, 95.25))
    chk(p, "R4", "1", (114.3, 95.25))
    H.wire(p, 88.9, 95.25, 88.9, y)
    H.wire(p, 88.9, y, 114.3, y)
    H.wire(p, 114.3, y, 114.3, 95.25)
    return p


# ------------------------------------------------------------------ L15_autolabel_chain.py
def b_L15():
    p = pl_base("L15", n_net=False)
    H.place(p, R, "R2", 101.6, 87.63)
    H.place(p, R, "R3", 127, 101.6)
    H.place(p, R, "R4", 127, 87.63)
    chk(p, "R2", "2", (101.6, 91.44))
    chk(p, "R3", "1", (127, 97.79))
    chk(p, "R4", "2", (127, 91.44))
    return p


# ============================================================ copied from verify-probe-labels/vl.py
def v_fmt(iuv: int) -> str:
    a = abs(iuv)
    s = f"{a // 10000}.{a % 10000:04d}".rstrip("0").rstrip(".")
    return ("-" + s) if iuv < 0 else s


def v_node(p, token, sexpr):
    while "@U" in sexpr:
        sexpr = sexpr.replace("@U", str(_uuid.uuid4()), 1)
    n = H._cst.parse(sexpr.encode()).lists[0]
    H._edit(p, lambda root: H.schematic._splice_sch_node(root, token, n))


def v_w(p, x1, y1, x2, y2):
    v_node(p, "wire", f"(wire (pts (xy {v_fmt(x1)} {v_fmt(y1)}) (xy {v_fmt(x2)} {v_fmt(y2)})) "
                      f"(stroke (width 0) (type default)) (uuid \"@U\"))")


def v_lab(p, text, x, y, kind="label", rot=0):
    shape = "" if kind == "label" else "(shape input) "
    v_node(p, kind, f"({kind} \"{text}\" {shape}(at {v_fmt(x)} {v_fmt(y)} {rot}) "
                    f"(effects (font (size 1.27 1.27))) (uuid \"@U\"))")


def v_ncflag(p, x, y):
    v_node(p, "no_connect", f"(no_connect (at {v_fmt(x)} {v_fmt(y)}) (uuid \"@U\"))")


def v_netclass(p, cls, x, y, rot=0):
    v_node(p, "netclass_flag",
           f"(netclass_flag \"\" (length 2.54) (shape round) (at {v_fmt(x)} {v_fmt(y)} {rot}) "
           f"(effects (font (size 1.27 1.27)) (justify left bottom)) (uuid \"@U\") "
           f"(property \"Netclass\" \"{cls}\" (at {v_fmt(x)} {v_fmt(y)} 0) "
           f"(effects (font (size 1.27 1.27) (italic yes)) (justify left))))")


def v_gline(p, x1, y1, x2, y2):
    v_node(p, "polyline", f"(polyline (pts (xy {v_fmt(x1)} {v_fmt(y1)}) (xy {v_fmt(x2)} {v_fmt(y2)})) "
                          f"(stroke (width 0) (type default)) (uuid \"@U\"))")


def v_part(p, ref, x, y, rot=0, lib="Device:R"):
    """place_component (snaps to 1.27 mm). x, y in IU."""
    H.place(p, lib, ref, x / 1e4, y / 1e4, rot=rot)


def v_project_with_classes(p, classes=("HV",)):
    base = {"bus_width": 12, "clearance": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
            "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3,
            "microvia_drill": 0.1, "pcb_color": "rgba(0, 0, 0, 0.000)",
            "schematic_color": "rgba(0, 0, 0, 0.000)", "track_width": 0.25,
            "via_diameter": 0.8, "via_drill": 0.4, "wire_width": 6}
    cl = [dict(base, name="Default", priority=2147483647)]
    cl += [dict(base, name=c, priority=i) for i, c in enumerate(classes)]
    pro = Path(p).with_suffix(".kicad_pro")
    pro.write_text(json.dumps({"meta": {"filename": pro.name, "version": 3},
                               "net_settings": {"classes": cl, "meta": {"version": 4},
                                                "net_colors": None,
                                                "netclass_assignments": None,
                                                "netclass_patterns": []}}, indent=2))


VP = (635000, 723900)    # R1:1 end of the verifier layout (63.5, 72.39), outward up
VE1 = (635000, 698500)   # its 1x stub end (63.5, 69.85)
VQ = (762000, 723900)    # (76.2, 72.39), interior point of W1 in v04


def v_base(name, n_net=True):
    """R1 at (63.5,76.2); N net on R9 (stub up + local label N)."""
    p = rlib.fresh(name)
    v_part(p, "R1", 635000, 762000)
    chk(p, "R1", "1", (63.5, 72.39))
    if n_net:
        v_part(p, "R9", 1397000, 762000)
        chk(p, "R9", "1", (139.7, 72.39))
        v_w(p, 1397000, 723900, 1397000, 698500)
        v_lab(p, "N", 1397000, 698500)
    return p


# ------------------------------------------------------------------ v01_directive.py
def b_v01(kind):
    p = v_base(f"v01{kind}", n_net=(kind != "g"))
    if kind == "a":
        v_netclass(p, "HV", 635000, 711200)
    elif kind == "b":
        v_netclass(p, "HV", *VE1)
    elif kind == "c":
        v_w(p, 571500, 711200, 698500, 711200)
        v_netclass(p, "HV", 635000, 711200)
    elif kind == "c2":
        v_w(p, 635000, 698500, 698500, 698500)
        v_netclass(p, "HV", 698500, 698500)
    elif kind == "d":
        v_w(p, 571500, 711200, 698500, 711200)
    elif kind == "e":
        v_part(p, "R3", 508000, 749300)   # R3:1 at (50.8,71.12)
        v_part(p, "R4", 762000, 749300)   # R4:1 at (76.2,71.12)
        chk(p, "R3", "1", (50.8, 71.12))
        chk(p, "R4", "1", (76.2, 71.12))
        v_w(p, 508000, 711200, 762000, 711200)
        v_netclass(p, "HV", 635000, 711200)
    elif kind == "g":
        v_part(p, "R2", 889000, 635000)   # R2:2 at (88.9,67.31)
        chk(p, "R2", "2", (88.9, 67.31))
        v_netclass(p, "HV", 762000, 723900)
    else:
        raise KeyError(kind)
    v_project_with_classes(p, ("HV",))
    return p


# ------------------------------------------------------------------ v02_junction_split.py
def b_v02(kind):
    p = v_base(f"v02{kind}")
    if kind in ("a", "a_rev"):
        v_part(p, "R5", 508000, 736600)   # R5:1 (50.8,69.85)
        v_part(p, "R6", 762000, 736600)   # R6:1 (76.2,69.85)
        chk(p, "R5", "1", (50.8, 69.85))
        chk(p, "R6", "1", (76.2, 69.85))
        if kind == "a":
            v_w(p, 508000, 698500, 762000, 698500)
        else:
            v_w(p, 762000, 698500, 508000, 698500)
        v_lab(p, "N", 508000, 698500)
    elif kind == "b":
        v_part(p, "R2", 762000, 736600)   # R2:1 (76.2,69.85)
        chk(p, "R2", "1", (76.2, 69.85))
        v_w(p, *VP, 558800, 723900)
        v_w(p, 558800, 723900, 558800, 698500)
        v_w(p, 558800, 698500, 762000, 698500)
    elif kind in ("c", "c_rev", "d"):
        v_part(p, "R2", 635000, 584200)   # R2:2 (63.5,62.23)
        chk(p, "R2", "2", (63.5, 62.23))
        if kind == "d":  # setup with the repo's own add_wires, as the original did
            H.schematic.add_wires(wires=[dict(x1=63.5, y1=72.39, x2=63.5, y2=62.23)],
                                  schematic_path=p)
        elif kind == "c":
            v_w(p, *VP, 635000, 622300)
        else:
            v_w(p, 635000, 622300, *VP)
    else:
        raise KeyError(kind)
    return p


# ------------------------------------------------------------------ v03_stack_pin_merge.py
def b_v03(kind):
    p = v_base(f"v03{kind}")
    v_part(p, "R5", 508000, 736600)
    v_part(p, "R6", 762000, 736600)
    v_part(p, "R4", 635000, 660400)   # R4:2 (63.5,69.85)
    chk(p, "R4", "2", (63.5, 69.85))
    if kind == "a_rev":
        v_w(p, 762000, 698500, 508000, 698500)
    else:
        v_w(p, 508000, 698500, 762000, 698500)
    v_lab(p, "FOO" if kind == "b" else "N", *VE1)
    return p


# ------------------------------------------------------------------ v04_stack_own_wire.py
def b_v04(kind):
    p = v_base(f"v04{kind}")
    v_part(p, "R2", 889000, 762000)
    chk(p, "R2", "1", (88.9, 72.39))
    v_w(p, 635000, 723900, 889000, 723900)
    v_lab(p, "FOO" if kind == "d" else "N", *VQ)
    if kind in ("a", "d", "e"):
        v_part(p, "R4", 762000, 762000)   # R4:1 at Q
        chk(p, "R4", "1", (76.2, 72.39))
    if kind == "b":
        v_ncflag(p, *VQ)
    if kind == "c":
        v_lab(p, "M", *VQ)
    if kind == "e":
        v_gline(p, 762000, 673100, *VQ)
    if kind == "f":
        v_netclass(p, "HV", *VQ)
        v_project_with_classes(p, ("HV",))
    return p


# ------------------------------------------------------------------ v05_capture.py
V05_POS = {"s1": (635001, 711200), "s2": (635002, 711200), "w1": (762000, 723901),
           "w2": (762000, 723902), "x1": (762001, 723901)}


def b_v05(kind):
    p = v_base(f"v05{kind}")
    v_part(p, "R8", 1651000, 762000)
    v_w(p, 1651000, 723900, 1651000, 698500)
    v_lab(p, "FOO", 1651000, 698500)
    if kind in ("w1", "w2", "x1"):
        v_part(p, "R2", 889000, 762000)
        chk(p, "R2", "1", (88.9, 72.39))
        v_w(p, 635000, 723900, 889000, 723900)
    if kind == "x1":
        v_part(p, "R3", 762000, 609600)   # R3:2 (76.2,64.77)
        v_part(p, "R4", 762000, 838200)   # R4:1 (76.2,80.01)
        chk(p, "R3", "2", (76.2, 64.77))
        chk(p, "R4", "1", (76.2, 80.01))
        v_w(p, 762000, 647700, 762000, 800100)
    v_lab(p, "FOO", *V05_POS[kind])
    return p


# ------------------------------------------------------------------ v06_label_on_pin.py
def b_v06(kind):
    """Tool-level trials: Ta = geometry Ka, Tbus = geometry Kbus, both without the label on P."""
    geo = {"Ta": "Ka", "Tbus": "Kbus"}[kind]
    p = v_base(f"v06{kind}")
    v_part(p, "R3", 508000, 762000)   # R3:1 (50.8,72.39)
    v_part(p, "R4", 762000, 762000)   # R4:1 (76.2,72.39)
    chk(p, "R3", "1", (50.8, 72.39))
    chk(p, "R4", "1", (76.2, 72.39))
    if geo == "Ka":
        v_w(p, 508000, 723900, 762000, 723900)
    if geo == "Kbus":
        v_part(p, "R2", 635000, 584200)   # R2:2 (63.5,62.23)
        chk(p, "R2", "2", (63.5, 62.23))
        v_w(p, 635000, 622300, *VP)       # own wire ends at P
        v_part(p, "R8", 1651000, 762000)
        v_w(p, 1651000, 723900, 1651000, 698500)
        v_lab(p, "FOO", 1651000, 698500)
        v_node(p, "bus", "(bus (pts (xy 50.8 72.39) (xy 76.2 72.39)) "
                         "(stroke (width 0) (type default)) (uuid \"@U\"))")
        v_lab(p, "FOO", 508000, 723900)
    return p


# ------------------------------------------------------------------ v07_autolabel.py
def b_v07(seq):
    p = rlib.fresh(f"v07{seq}")
    v_part(p, "R1", 635000, 762000)
    v_part(p, "R2", 635000, 584200)
    v_part(p, "R3", 1016000, 762000)
    v_part(p, "R4", 1016000, 584200)
    chk(p, "R1", "1", (63.5, 72.39))
    chk(p, "R2", "2", (63.5, 62.23))
    chk(p, "R3", "1", (101.6, 72.39))
    chk(p, "R4", "2", (101.6, 62.23))
    v_part(p, "R9", 1397000, 762000)
    v_w(p, 1397000, 723900, 1397000, 698500)
    v_lab(p, "N", 1397000, 698500)
    return p


# ------------------------------------------------------------------ v08_m_tolerance_and_glines.py
def b_v08(kind):
    p = v_base(f"v08{kind}")
    if kind == "t1":
        v_part(p, "R7", 1143000, 762000)
        v_w(p, 1143000, 723900, 1143000, 698500)
        v_lab(p, "BLK", 1143000, 698500)
        v_lab(p, "BLK", *VE1)
        v_part(p, "R8", 1651000, 762000)
        v_w(p, 1651000, 723900, 1651000, 698500)
        v_lab(p, "FOO", 1651000, 698500)
        v_lab(p, "FOO", 635001, 723900)
    elif kind == "t2":
        v_part(p, "R2", 635000, 584200)
        chk(p, "R2", "2", (63.5, 62.23))
        v_part(p, "R4", 762000, 762000)       # R4:1 (76.2,72.39)
        chk(p, "R4", "1", (76.2, 72.39))
        v_w(p, 635001, 723900, 762000, 723900)
    elif kind == "g1":
        v_gline(p, 571500, 723900, 698500, 723900)
    elif kind == "g2":
        v_part(p, "R8", 1651000, 762000)
        v_w(p, 1651000, 723900, 1651000, 698500)
        v_lab(p, "FOO", 1651000, 698500)
        v_gline(p, *VE1, 1651000, 698500)
    else:
        raise KeyError(kind)
    return p


# ============================================================ jobs
def _o(d, script, sub):
    return f"{d}/{script} {sub}"


W1 = wptn([R1_1], "N")
JOBS: list = []


def add(cases, variant, origin, source, build, args, steps, note=""):
    JOBS.append(Job(cases, variant, origin, source, build, args=args, steps=steps, note=note))


# ---- probe-labels
for k in "abcdef":
    add(["LBL-01"], f"L01/{k}", _o(PL, "L01_directive_labels.py", k), "probe", b_L01, (k,), [W1])
add(["LBL-01"], "L01/g", _o(PL, "L01_directive_labels.py", "g"), "probe", b_L01, ("g",),
    [cp(R1_1, ("R2", "2"))])
for k in "ab":
    add(["LBL-02"], f"L03/{k}", _o(PL, "L03_label_at_T.py", k), "probe", b_L03, (k,), [W1])
for k in "ab":
    add(["LBL-15"], f"L04/{k}", _o(PL, "L04_stack_at_stub_end.py", k), "probe", b_L04, (k,), [W1])
for k in "ab":
    add(["LBL-03"], f"L05/{k}", _o(PL, "L05_stack_label_pin_merge.py", k), "probe", b_L05, (k,),
        [W1])
for k in "abcde":
    add(["LBL-04", "LBL-05", "LBL-11", "LBL-14"], f"L06/{k}",
        _o(PL, "L06_stack_on_target_wire.py", k), "probe", b_L06, (k,), [W1])
for k in "abcdefg":
    add(["LBL-06"], f"L07/{k}", _o(PL, "L07_capture_new_stub.py", k), "probe", b_L07, (k,), [W1])
for k in "abcd":
    add(["LBL-07", "LBL-13"], f"L08/{k}", _o(PL, "L08_capture_model.py", k), "probe", b_L08, (k,),
        [W1])
for k in "abc":
    add(["LBL-12"], f"L09/{k}", _o(PL, "L09_graphic_polylines.py", k), "probe", b_L09, (k,), [W1])
for k in "abcde":
    add(["LBL-10"], f"L10/{k}", _o(PL, "L10_label_on_pin_fallback.py", k), "probe", b_L10, (k,),
        [W1])
add(["LBL-10"], "L10/f", _o(PL, "L10_label_on_pin_fallback.py", "f"), "probe", b_L10, ("f",),
    [cp(R1_1, ("R2", "2"))])
add(["LBL-09"], "L14/seq", _o(PL, "L14_autolabel_then_wire.py", "steps 1-2"), "probe", b_L14, (),
    [cp(R1_1, ("R2", "2")), wptn([R1_1], "N")],
    note="sequence on one file per design: connect_pins(R1:1,R2:2) then wire_pins_to_net([R1:1],'N')")

# ---- verify-probe-labels
for k in ("a", "b", "c", "c2", "d", "e"):
    add(["LBL-01"], f"v01/{k}", _o(VL, "v01_directive.py", k), "verify", b_v01, (k,), [W1])
add(["LBL-01"], "v01/g", _o(VL, "v01_directive.py", "g"), "verify", b_v01, ("g",),
    [cp(R1_1, ("R2", "2"))])
for k in ("a", "a_rev", "b", "c", "c_rev", "d"):
    add(["LBL-02"], f"v02/{k}", _o(VL, "v02_junction_split.py", k), "verify", b_v02, (k,), [W1])
for k in ("a", "a_rev", "b"):
    cases = ["LBL-02", "LBL-03"] + (["LBL-05"] if k == "b" else [])
    add(cases, f"v03/{k}", _o(VL, "v03_stack_pin_merge.py", k), "verify", b_v03, (k,), [W1])
V04_CASES = {"a": ["LBL-04", "LBL-11"], "b": ["LBL-04", "LBL-11"], "c": ["LBL-04", "LBL-14"],
             "d": ["LBL-04", "LBL-05", "LBL-11"], "e": ["LBL-04", "LBL-11"],
             "f": ["LBL-04", "LBL-11"]}
V04_ALIAS = {"a": " (= v09b_literal_m_pin_nc.py a: same fixture and call)",
             "b": " (= v09b_literal_m_pin_nc.py b: same fixture and call)",
             "c": " (= v09_literal_m_noop.py c: same fixture and call)",
             "f": " (= v09_literal_m_noop.py f: same fixture and call)"}
for k in "abcdef":
    add(V04_CASES[k], f"v04/{k}", _o(VL, "v04_stack_own_wire.py", k) + V04_ALIAS.get(k, ""),
        "verify", b_v04, (k,), [W1])
V05_CASES = {"s1": ["LBL-06"], "s2": ["LBL-06"], "w1": ["LBL-07"], "w2": ["LBL-07", "LBL-13"],
             "x1": ["LBL-07"]}
for k in ("s1", "s2", "w1", "w2", "x1"):
    add(V05_CASES[k], f"v05/{k}", _o(VL, "v05_capture.py", k), "verify", b_v05, (k,), [W1])
for k in ("Ta", "Tbus"):
    add(["LBL-08"], f"v06/{k}", _o(VL, "v06_label_on_pin.py", k), "verify", b_v06, (k,), [W1])
add(["LBL-09"], "v07/A", _o(VL, "v07_autolabel.py", "sequence A"), "verify", b_v07, ("A",),
    [cp(R1_1, ("R2", "2")), wptn([("R2", "2")], "N")],
    note="connect_pins(R1:1,R2:2) then wire_pins_to_net([R2:2],'N') on one file per design")
add(["LBL-09"], "v07/B", _o(VL, "v07_autolabel.py", "sequence B"), "verify", b_v07, ("B",),
    [cp(R1_1, ("R2", "2")), cp(("R3", "1"), ("R4", "2")), cp(R1_1, ("R3", "1"))],
    note="connect_pins(R1:1,R2:2); connect_pins(R3:1,R4:2); connect_pins(R1:1,R3:1)")
add(["LBL-10"], "v08/t1", _o(VL, "v08_m_tolerance_and_glines.py", "t1"), "verify", b_v08, ("t1",),
    [W1])
add(["LBL-06", "LBL-10"], "v08/t2", _o(VL, "v08_m_tolerance_and_glines.py", "t2"), "verify", b_v08,
    ("t2",), [cp(R1_1, ("R2", "2"))])
add(["LBL-12"], "v08/g1", _o(VL, "v08_m_tolerance_and_glines.py", "g1"), "verify", b_v08, ("g1",),
    [W1])
add(["LBL-12"], "v08/g2", _o(VL, "v08_m_tolerance_and_glines.py", "g2"), "verify", b_v08, ("g2",),
    [W1], note="the verifier's spot check of L09b's geometry (LBL-12 evidence); v08 is named by"
    " LBL-10 (t1, t2) and LBL-12 (g1), and g2 is a tool call that script runs")

# ---- evidence-cited probe scenarios. caseidx keeps one scenario_script per case; the original
# probe evidence (phase2-attack.json result 'labels', cases[i].evidence) cites more sub-cases, and
# LBL-06's and LBL-07's verifier_method name L13, L10e, L09c and L02d. These scripts are not in any
# caseidx scenario_script; they are run (new jobs) or tagged (extra case memberships) here so each
# case's evidence is covered. Filter on these variants to restrict to the strict caseidx scenarios.
EVID = "evidence-cited (phase2-attack.json labels evidence), not in caseidx scenario_script"
for k in "abcde":
    add(["LBL-07"] + (["LBL-15"] if k == "a" else []), f"L02/{k}",
        _o(PL, "L02_label_at_crossing_model.py", k), "probe", b_L02, (k,), [W1], note=EVID)
for k in "ab":
    add(["LBL-15"], f"L12/{k}", _o(PL, "L12_stack_nc_at_stub_end.py", k), "probe", b_L12, (k,),
        [W1], note=EVID)
for k in "ab":
    add(["LBL-06"], f"L13/{k}", _o(PL, "L13_wire_near_new_label.py", k), "probe", b_L13, (k,),
        [W1], note=EVID)
add(["LBL-09"], "L15/seq", _o(PL, "L15_autolabel_chain.py", "steps 1-3"), "probe", b_L15, (),
    [cp(R1_1, ("R2", "2")), cp(("R3", "1"), ("R4", "2")), cp(("R2", "2"), ("R3", "1"))],
    note=EVID + "; connect_pins(R1:1,R2:2); connect_pins(R3:1,R4:2); connect_pins(R2:2,R3:1)")
# extra memberships of variants already in the list (evidence citations)
EXTRA = {"L04/a": ["LBL-02"], "L04/b": ["LBL-02"], "L05/a": ["LBL-02"],
         "L09/c": ["LBL-06"], "L10/e": ["LBL-06", "LBL-08"], "L10/f": ["LBL-06"],
         "L10/b": ["LBL-08"], "L07/f": ["LBL-10"], "L07/g": ["LBL-10"], "L10/c": ["LBL-15"]}
ORDER = [f"LBL-{i:02d}" for i in range(1, 16)]
for j in JOBS:
    if j.variant in EXTRA:
        j.cases = sorted(set(j.cases) | set(EXTRA[j.variant]), key=ORDER.index)
        j.note = (j.note + "; " if j.note else "") + \
            f"extra case membership {EXTRA[j.variant]} from the probe evidence citations"
    else:
        j.cases = sorted(set(j.cases), key=ORDER.index)

assert len({j.variant for j in JOBS}) == len(JOBS), "variant ids must be unique"
assert set(EXTRA) <= {j.variant for j in JOBS}


def _select(names):
    """Exact variant ids (rlib.main's own filter matches substrings: 'v02/a' in 'v02/a_rev')."""
    if not names:
        return JOBS
    sel = [j for j in JOBS if j.variant in set(names)]
    missing = set(names) - {j.variant for j in sel}
    if missing:
        raise SystemExit(f"unknown variants: {sorted(missing)}")
    return sel


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--test":
        sel = _select(args[1].split(",") if len(args) > 1 else None)
        rlib.main(sel, CAT, __file__, out=rlib.WORK / "test_labels.jsonl")
    elif args and args[0] == "--list":
        for j in JOBS:
            print(j.variant, j.cases, j.source, len(j.steps), j.origin)
        print(len(JOBS), "jobs")
    else:
        import os  # follow-up 2: worker count from the environment (default 1, as follow-up 1)

        rlib.main(_select(args[0].split(",") if args else None), CAT, __file__,
                  workers=int(os.environ.get("LABELS_WORKERS", "1")))
