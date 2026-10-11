# probeB copy of pr57pt/probe-units-pads/u2_ieee_power_pins.py: probeB import header in front, judge() calls carry vid=/stepno=; fixture code unchanged.
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("u2_ieee_power_pins", __file__)
"""U2: unit-0 POWER pins of a stock IEEE gate package. 74xx_IEEE:7400 (non-derived, 4 gates):
VCC (pad 14) and GND (pad 7) are visible power_in pins in the unit-0 sub-symbol, so every placed
gate draws its own copy. Footprint Package_DIP:DIP-14_W7.62mm.

U1 gate A (unit 1) at (101.6, 101.6), gate B (unit 2) at (152.4, 101.6). Pad 14 copies at
(101.6, 91.44) and (152.4, 91.44), outward up; pad 7 copies at (101.6, 111.76) and
(152.4, 111.76), outward down. A real supply: C1 with power:VCC (#PWR01, Value VCC) on C1:1 and
power:GND (#PWR02) on C1:2; power:+5V (#PWR03) on R5:1.

Cases (P, M and B on byte-identical copies; board = update_pcb_from_schematic, DIP-14):
  a   both copies free                wire_pins_to_net([U1:14], VCC, auto_pwr_flag=False)
  a2  both copies free                wire_pins_to_net([U1:14], VCC)  (default auto_pwr_flag)
  b   both copies free                wire_pins_to_net([U1:7], GND, auto_pwr_flag=False)
  c   copy A of 14 already on VCC     wire_pins_to_net([U1:14], VCC, auto_pwr_flag=False)
  d   copy B of 14 on +5V             wire_pins_to_net([U1:14], VCC, auto_pwr_flag=False)
"""

from uplib import IMPLS, W, add_unit, copies, dump, fresh, judge, nets_coded, place, set_fp, stub, wire

FP_U = "Package_DIP:DIP-14_W7.62mm"
REFS = ("U1", "C1", "R5")


def build(name, a14=None, b14=None):
    p = fresh(name)
    place(p, "74xx_IEEE:7400", "U1", 101.6, 101.6)
    add_unit(p, "U1", 2, 152.4, 101.6)
    place(p, "Device:C", "C1", 63.5, 101.6)  # C1:1 (63.5, 97.79), C1:2 (63.5, 105.41)
    place(p, "power:VCC", "#PWR01", 63.5, 93.98, value="VCC")
    wire(p, 63.5, 97.79, 63.5, 93.98)
    place(p, "power:GND", "#PWR02", 63.5, 109.22, value="GND")
    wire(p, 63.5, 105.41, 63.5, 109.22)
    place(p, "Device:R", "R5", 38.1, 101.6)  # R5:1 (38.1, 97.79)
    place(p, "power:+5V", "#PWR03", 38.1, 93.98, value="+5V")
    wire(p, 38.1, 97.79, 38.1, 93.98)
    (ax, ay, _), (bx, by, _) = copies(p, "U1", "14")
    if a14:
        stub(p, ax, ay, "up", a14)
    if b14:
        stub(p, bx, by, "up", b14)
    set_fp(p, "U1", FP_U)
    set_fp(p, "C1", "Capacitor_SMD:C_0603_1608Metric")
    set_fp(p, "R5", "Resistor_SMD:R_0603_1608Metric")
    return p


p0 = build("u2_layout")
print("U1 pad 14 copies:", copies(p0, "U1", "14"), "| pad 7 copies:", copies(p0, "U1", "7"))
print("fixture netlist:", [(nm, sorted(v)) for _c, nm, v in nets_coded(p0)])

CASES = [
    ("a", {}, lambda q: W(q, [("U1", "14")], "VCC", auto_pwr_flag=False), [("U1", "14")], "VCC"),
    ("a2", {}, lambda q: W(q, [("U1", "14")], "VCC"), [("U1", "14")], "VCC"),
    ("b", {}, lambda q: W(q, [("U1", "7")], "GND", auto_pwr_flag=False), [("U1", "7")], "GND"),
    ("c", {"a14": "VCC"}, lambda q: W(q, [("U1", "14")], "VCC", auto_pwr_flag=False), [("U1", "14")],
     "VCC"),
    ("d", {"b14": "+5V"}, lambda q: W(q, [("U1", "14")], "VCC", auto_pwr_flag=False), [("U1", "14")],
     "VCC"),
]
for tag, fx, kw, targets, N in CASES:
    base = build(f"u2{tag}", **fx)
    for impl, q in __import__("uplib").triplicate(base).items():
        judge(f"U2{tag} wire {fx or 'both copies free'} -> {N}", impl, q, IMPLS[impl]["wire"], kw(q),
              targets=targets, N=N, board_refs=REFS, vid=tag)

dump("u2_ieee_power_pins.json")
