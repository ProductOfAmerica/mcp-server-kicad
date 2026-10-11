# probeB copy of pr57pt/probe-hierarchy/h08_cross_sheet_open.py (header swapped to the probeB shims; fixture code unchanged).
"""H08: a sub-sheet edit wires a pin to N where N is a power name, a global label or a hidden
power_in pin name that exists ONLY on another sheet. A local label N on the sub-sheet is
'/C/N', not that net (digest H4, v20 T7, v21e): the requested join silently does not happen.

Root: R9:1 on power GND; R8:1 on global label SDA; U1 (custom 'pt:HPWR': pin 1 passive, pin 2
hidden power_in named VCC) with pin 1 free.
Child c.kicad_sch (sheet C, no sheet pins): R31 free; variant 'with': the child ALSO holds a GND
power symbol, a global label SDA and a hidden-VCC part on other subgraphs (control).
Calls (schematic_path = child): wire_pins_to_net([R31:1], N) for N in GND, SDA, VCC.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h08_cross_sheet_open", __file__)
import hlib  # noqa: E402
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h08_cross_sheet_open.out")

hlib._LIB[("pt", "HPWR")] = """(symbol "pt:HPWR" (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
  (property "Reference" "U" (at 2.54 0 0) (effects (font (size 1.27 1.27))))
  (property "Value" "HPWR" (at 2.54 -2.54 0) (effects (font (size 1.27 1.27))))
  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
  (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
  (property "Description" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
  (symbol "HPWR_0_1" (rectangle (start -1.27 2.54) (end 1.27 -2.54) (stroke (width 0.254) (type default)) (fill (type none))))
  (symbol "HPWR_1_1"
    (pin passive line (at 0 3.81 270) (length 1.27) (name "A" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
    (pin power_in line (at 0 -3.81 90) (length 1.27) (hide yes) (name "VCC" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27))))))
  (embedded_fonts no))"""


def build(with_local):
    pj = Proj(f"h08_{'with' if with_local else 'only_root'}")
    r = pj.root
    ch = pj.child("c.kicad_sch")
    r.sheet("C", ch, 152.4, 25.4)
    (g1, _) = r.R("R9", 50.8, 50.8)
    r.wire(*g1, g1[0], round(g1[1] - 5.08, 4))
    r.power("GND", g1[0], round(g1[1] - 5.08, 4))
    (s1, _) = r.R("R8", 76.2, 50.8)
    r.wire(*s1, s1[0], round(s1[1] - 5.08, 4))
    r.label("SDA", s1[0], round(s1[1] - 5.08, 4), kind="global_label")
    r.symbol("pt", "HPWR", "U1", 101.6, 50.8, "HPWR", pins=("1", "2"))
    ch.R(["R31"], 50.8, 50.8)
    if with_local:
        (a1, _) = ch.R(["R32"], 76.2, 50.8)
        ch.wire(*a1, a1[0], round(a1[1] - 5.08, 4))
        ch.power("GND", a1[0], round(a1[1] - 5.08, 4))
        (b1, _) = ch.R(["R33"], 101.6, 50.8)
        ch.wire(*b1, b1[0], round(b1[1] - 5.08, 4))
        ch.label("SDA", b1[0], round(b1[1] - 5.08, 4), kind="global_label")
        ch.symbol("pt", "HPWR", ["U2"], 127, 50.8, "HPWR", pins=("1", "2"))
    pj.write()
    return pj


REF = {"GND": ("R9", "1"), "SDA": ("R8", "1"), "VCC": ("U1", "2")}
for with_local in (False, True):
    pj = build(with_local)
    log(f"\n######## child {'also holds GND/SDA/VCC items (control)' if with_local else 'holds no GND/SDA/VCC item'}")
    show(netlist(pj.root.path), title="baseline root netlist", out=log)
    for N in ("GND", "SDA", "VCC"):
        run(log, f"h08_{'with' if with_local else 'only_root'}_{N}", pj.dir, pj.root.fname,
            "c.kicad_sch", "wire_pins_to_net",
            dict(pins=[{"reference": "R31", "pin": "1"}], label_text=N),
            allowed=[("R31", "1"), REF[N]], join=(("R31", "1"), REF[N]),
            ideal=("R31:1 should end on the project's net N; a local label cannot reach it from this "
                   "sheet, so warn/refuse (or use a global/power item)") if not with_local else
            "R31:1 joins the project's N through the child's own N item (requested)",
            pin_nodes=[("R31", "1"), REF[N]])
log.save()
