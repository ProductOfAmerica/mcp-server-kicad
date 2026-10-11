# probeB copy of pr57pt/probe-units-pads/x_duplicate_numbers.py: probeB import header in front, judge() calls carry vid=/stepno=; fixture code unchanged.
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("x_duplicate_numbers", __file__)
"""X: duplicate pin numbers inside ONE unit (no jumper flag; KiCad 9 cannot load jumper tokens,
digest S5). KiCad keeps the two drawn points apart but lists the pad in every net they reach
(digest S6), so one pad, two nets, and the board binds the last-sorting name.

DUP (custom, libs/dup.kicad_sym): pin "1" named A at lib (-5.08, 0), pin "1" named A2 at lib
(-5.08, -2.54), pin "2" named B at (5.08, 0). Placed at (101.6, 101.6): copy A (96.52, 101.6),
copy A2 (96.52, 104.14), both outward left. Footprint R_0603 (pads 1 and 2). R1:1 on local N,
R2:1 on local Q, R3 free with R3:2 at (88.9, 97.79).
Stock 74xx_IEEE:74278 (non-derived): pad 6 is drawn twice in unit 1, named Y4 and ~{P1}
(a library erratum: two signals share one number; pad 5 or 11 is missing). DIP-14.

Cases (P, M, B on byte-identical copies):
  X1a DUP, both copies free         wire_pins_to_net([U1:1], N)
  X1b DUP, copy A2 on Q             wire_pins_to_net([U1:1], N)   (pad already on Q)
  X1c DUP, both copies free         wire_pins_to_net([U1:A2], N)  (asked by name)
  X1d DUP, both copies free         connect_pins(U1, 1, R3, 2)
  X2a 74278, both copies free       wire_pins_to_net([U1:6], N)
  X2b 74278, both copies free       wire_pins_to_net([U1:~{P1}], N) (by name)
"""

from uplib import (
    IMPLS, C, W, copies, dump, fresh, judge, lpin, lsym, nets_coded, place, place_custom, probe_r,
    set_fp, stub, triplicate, write_lib,
)

DUP = write_lib("dup.kicad_sym", [lsym("DUP", {(1, 1): [
    lpin("1", "A", x=-5.08, y=0, ang=0),
    lpin("1", "A2", x=-5.08, y=-2.54, ang=0),
    lpin("2", "B", x=5.08, y=0, ang=180),
]})])
FP_R = "Resistor_SMD:R_0603_1608Metric"


def build_dup(name, a2=None):
    p = fresh(name)
    place_custom(p, DUP, "DUP", "U1", 101.6, 101.6)
    probe_r(p, "R1", 50.8, 63.5, "N")
    probe_r(p, "R2", 76.2, 63.5, "Q")
    place(p, "Device:R", "R3", 88.9, 93.98)
    if a2:
        x, y, _ = copies(p, "U1", "1")[1]
        stub(p, x, y, "left", a2)
    for r in ("U1", "R1", "R2", "R3"):
        set_fp(p, r, FP_R)
    return p


def build_278(name):
    p = fresh(name)
    place(p, "74xx_IEEE:74278", "U1", 101.6, 101.6)
    probe_r(p, "R1", 50.8, 63.5, "N")
    set_fp(p, "U1", "Package_DIP:DIP-14_W7.62mm")
    set_fp(p, "R1", FP_R)
    return p


p0 = build_dup("x_layout")
print("DUP copies of pad 1:", copies(p0, "U1", "1"), "| pad 2:", copies(p0, "U1", "2"))
print("DUP fixture netlist:", [(nm, sorted(v)) for _c, nm, v in nets_coded(p0)])
p1 = build_278("x_layout278")
print("74278 copies of pad 6:", copies(p1, "U1", "6"))
print("74278 fixture netlist (pad 6):", [(nm, sorted(v)) for _c, nm, v in nets_coded(p1) if ("U1", "6") in v])

REFS = ("U1", "R1", "R2", "R3")
CASES = [
    ("X1a", lambda: build_dup("x1a"), "wire", lambda q: W(q, [("U1", "1")], "N"), [("U1", "1")], "N", None),
    ("X1b", lambda: build_dup("x1b", a2="Q"), "wire", lambda q: W(q, [("U1", "1")], "N"), [("U1", "1")], "N",
     None),
    ("X1c", lambda: build_dup("x1c"), "wire", lambda q: W(q, [("U1", "A2")], "N"), [("U1", "1")], "N", None),
    ("X1d", lambda: build_dup("x1d"), "connect", lambda q: C(q, "U1", "1", "R3", "2"), [], None,
     (("U1", "1"), ("R3", "2"))),
    ("X2a", lambda: build_278("x2a"), "wire", lambda q: W(q, [("U1", "6")], "N"), [("U1", "6")], "N", None),
    ("X2b", lambda: build_278("x2b"), "wire", lambda q: W(q, [("U1", "~{P1}")], "N"), [("U1", "6")], "N",
     None),
]
for tag, mk, tool, kw, targets, N, pair in CASES:
    base = mk()
    for impl, q in triplicate(base).items():
        judge(f"{tag} {tool}", impl, q, IMPLS[impl][tool], kw(q), targets=targets, N=N, pair=pair,
              board_refs=REFS, vid=tag)

dump("x_duplicate_numbers.json")
