# probeB copy of pr57pt/probe-units-pads/u1_rnet_common_pin.py: probeB import header in front, judge() calls carry vid=/stepno=; fixture code unchanged.
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("u1_rnet_common_pin", __file__)
"""U1: unit-0 PASSIVE common pin. Device:R_Network04_Split (non-derived, footprint
Resistor_THT:R_Array_SIP5): pin 1 is the common end of all four resistors and lives in the unit-0
sub-symbol, so every placed unit draws its own copy of pad 1.

RN1 unit 1 at (101.6, 101.6) and unit 2 at (127, 101.6) (second node spliced with (unit 2) at top
level and in its instances block). Copy A of pad 1 = (101.6, 97.79), copy B = (127, 97.79), both
outward up. R1:1 is on local net COM, R2:1 on local net Q (stub + label each), R3 is free with
pin 2 at (88.9, 97.79), horizontally aligned with copy A.

Cases (each run on byte-identical copies for P, M and B; board = this repo's
update_pcb_from_schematic on the result):
  a  both copies free                 wire_pins_to_net([RN1:1], COM)
  b  copy B on Q, copy A free         wire_pins_to_net([RN1:1], COM)   (the pad is already on Q)
  b2 copy B on Q, copy A free         wire_pins_to_net([RN1:1], Q)     (legit: completes Q)
  c  copy A on COM, copy B free       wire_pins_to_net([RN1:1], COM)   (pad half on COM)
  d  both copies free                 connect_pins(RN1, 1, R3, 2)
  e  copy B on Q, copy A free         connect_pins(RN1, 1, R3, 2)
"""

from uplib import (  # noqa: F401
    IMPLS, C, W, add_unit, copies, dump, fresh, judge, nets_coded, place, probe_r, set_fp, stub,
    triplicate,
)

FP_RN = "Resistor_THT:R_Array_SIP5"
FP_R = "Resistor_SMD:R_0603_1608Metric"
REFS = ("RN1", "R1", "R2", "R3")


def build(name, copy_a=None, copy_b=None):
    p = fresh(name)
    place(p, "Device:R_Network04_Split", "RN1", 101.6, 101.6)
    add_unit(p, "RN1", 2, 127, 101.6)
    probe_r(p, "R1", 50.8, 63.5, "COM")
    probe_r(p, "R2", 76.2, 63.5, "Q")
    place(p, "Device:R", "R3", 88.9, 93.98)  # R3:1 (88.9, 90.17), R3:2 (88.9, 97.79)
    (ax, ay, _), (bx, by, _) = copies(p, "RN1", "1")
    if copy_a:
        stub(p, ax, ay, "up", copy_a)
    if copy_b:
        stub(p, bx, by, "up", copy_b)
    set_fp(p, "RN1", FP_RN)
    for r in ("R1", "R2", "R3"):
        set_fp(p, r, FP_R)
    return p


p0 = build("u1_layout")
print("RN1 drawn pins (KiCad transform):", {k: v for k, v in sorted(__import__('mine_impl').pin_positions(p0, "RN1").items())})
print("R3 drawn pins:", __import__('mine_impl').pin_positions(p0, "R3"))
print("fixture netlist:", [(nm, sorted(v)) for _c, nm, v in nets_coded(p0)])

CASES = [
    ("a", dict(), "wire", lambda q: W(q, [("RN1", "1")], "COM"), [("RN1", "1")], "COM", None),
    ("b", dict(copy_b="Q"), "wire", lambda q: W(q, [("RN1", "1")], "COM"), [("RN1", "1")], "COM", None),
    ("b2", dict(copy_b="Q"), "wire", lambda q: W(q, [("RN1", "1")], "Q"), [("RN1", "1")], "Q", None),
    ("c", dict(copy_a="COM"), "wire", lambda q: W(q, [("RN1", "1")], "COM"), [("RN1", "1")], "COM", None),
    ("d", dict(), "connect", lambda q: C(q, "RN1", "1", "R3", "2"), [], None, (("RN1", "1"), ("R3", "2"))),
    ("e", dict(copy_b="Q"), "connect", lambda q: C(q, "RN1", "1", "R3", "2"), [], None,
     (("RN1", "1"), ("R3", "2"))),
]

for tag, fx, tool, kw, targets, N, pair in CASES:
    base = build(f"u1{tag}", **fx)
    for impl, q in triplicate(base).items():
        judge(f"U1{tag} {tool} {fx or 'both copies free'}", impl, q, IMPLS[impl][tool], kw(q),
              targets=targets, N=N, pair=pair, board_refs=REFS, vid=tag)

dump("u1_rnet_common_pin.json")
