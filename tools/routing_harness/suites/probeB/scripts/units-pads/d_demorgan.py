# probeB copy of pr57pt/probe-units-pads/d_demorgan.py: probeB import header in front, judge() calls carry vid=/stepno=; fixture code unchanged.
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("d_demorgan", __file__)
"""D: De Morgan alternate body style. Stock De Morgan symbols never move a pin between styles
(scan_stock: 33 symbols, the one 'difference' is AD9984AKST whose style 2 draws no pins), so a
crafted DMX (libs/dm.kicad_sym) moves them: style 1 pin 1 A at lib (-5.08, 0), pin 2 B at
(5.08, 0); style 2 pin 1 at (-5.08, 2.54), pin 2 at (5.08, 2.54), plus pin 3 C at (0, -5.08)
that only style 2 has. U1 at (101.6, 101.6). KiCad draws only the placed style (digest S4).

  D1  (convert 2)                          wire_pins_to_net([U1:1], S)    must land on (96.52, 99.06)
  D2  (convert 2), label X (with a pin) on the INACTIVE style-1 pin-1 point (96.52, 101.6)
                                           wire_pins_to_net([U1:1], S)    X must not be seen as U1:1's net
  D3  (convert 1)                          wire_pins_to_net([U1:3], S)    pin only in style 2: refuse
  D4  (convert 2), R1:1 at (106.68, 104.14) whose 2.54 stub up ends on the INACTIVE style-1 pin-2
      point (106.68, 101.6)                wire_pins_to_net([R1:1], T)    not an obstacle
  D4b (convert 1), same geometry: the stub end is now U1:2's live end   must not join U1:2
"""

from uplib import (
    IMPLS, W, copies, dump, fresh, judge, lpin, lsym, nets_coded, place, place_custom, probe_r,
    set_convert, triplicate, write_lib, label,
)

DM = write_lib("dm.kicad_sym", [lsym("DMX", {
    (1, 1): [lpin("1", "A", x=-5.08, y=0, ang=0), lpin("2", "B", x=5.08, y=0, ang=180)],
    (1, 2): [lpin("1", "A", x=-5.08, y=2.54, ang=0), lpin("2", "B", x=5.08, y=2.54, ang=180),
             lpin("3", "C", x=0, y=-5.08, ang=90)],
})])


def build(name, convert, x_label=False, r1=False):
    p = fresh(name)
    place_custom(p, DM, "DMX", "U1", 101.6, 101.6)
    if convert is not None:
        set_convert(p, "U1", convert)
    if x_label:
        label(p, "X", 96.52, 101.6)
        probe_r(p, "R9", 50.8, 63.5, "X")
    if r1:
        place(p, "Device:R", "R1", 106.68, 107.95)  # R1:1 (106.68, 104.14) outward up
    return p


for conv in (1, 2):
    q = build(f"d_layout{conv}", conv)
    print(f"(convert {conv}) U1 drawn pins:", __import__("mine_impl").pin_positions(q, "U1"))

CASES = [
    ("D1", lambda: build("d1", 2), lambda q: W(q, [("U1", "1")], "S"), [("U1", "1")], "S"),
    ("D2", lambda: build("d2", 2, x_label=True), lambda q: W(q, [("U1", "1")], "S"), [("U1", "1")], "S"),
    ("D3", lambda: build("d3", 1), lambda q: W(q, [("U1", "3")], "S"), [("U1", "3")], "S"),
    ("D4", lambda: build("d4", 2, r1=True), lambda q: W(q, [("R1", "1")], "T", direction="up"),
     [("R1", "1")], "T"),
    ("D4b", lambda: build("d4b", 1, r1=True), lambda q: W(q, [("R1", "1")], "T", direction="up"),
     [("R1", "1")], "T"),
]
for tag, mk, kw, targets, N in CASES:
    base = mk()
    print(f"\n--- {tag} fixture netlist:", [(nm, sorted(v)) for _c, nm, v in nets_coded(base)])
    for impl, q in triplicate(base).items():
        judge(f"{tag} wire", impl, q, IMPLS[impl]["wire"], kw(q), targets=targets, N=N, vid=tag)

dump("d_demorgan.json")
