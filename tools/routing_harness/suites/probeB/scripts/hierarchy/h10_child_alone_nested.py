# probeB copy of pr57pt/probe-hierarchy/h10_child_alone_nested.py.
# Header swapped to the probeB shims; fixture code and the three calls unchanged. Dropped: the
# trailing read-back of PWR_FLAG instance paths per design (M2 and K never place a PWR_FLAG, I15).
"""H10: edits to a CHILD file passed alone (schematic_path = chan.kicad_sch), where chan is used by
two sheets (S1, S2) and itself holds a sheet 'GC' (gchild.kicad_sch) with sheet pin P.

gchild: R41:1 on hier label P and power +5V (so every instance of RA's net is +5V).
chan:   RA (R11/R21) pin 1 -> (20.32, 97.79) -> sheet pin P of GC [ends on it]
        RB (R12/R22) pin 1 on local label N1;  RC (R13/R23) pin 1 on power GND
        U5 (R-like custom 'pt:VPIN', refs U15/U25): pin 1 VISIBLE power_in 'VIN', pin 2 passive
Calls (child): 10a wire_pins_to_net([R11:1], 'N1')     -> nested H13, multiplied per instance
               10b wire_pins_to_net([R11:1], 'GND')
               10c wire_pins_to_net([U15:1], 'VDD')    -> P/B auto PWR_FLAG in a 2-instance child
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h10_child_alone_nested", __file__)
import hlib  # noqa: E402
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h10_child_alone_nested.out")

hlib._LIB[("pt", "VPIN")] = """(symbol "pt:VPIN" (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
  (property "Reference" "U" (at 2.54 0 0) (effects (font (size 1.27 1.27))))
  (property "Value" "VPIN" (at 2.54 -2.54 0) (effects (font (size 1.27 1.27))))
  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
  (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
  (property "Description" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
  (symbol "VPIN_0_1" (rectangle (start -1.27 2.54) (end 1.27 -2.54) (stroke (width 0.254) (type default)) (fill (type none))))
  (symbol "VPIN_1_1"
    (pin power_in line (at 0 3.81 270) (length 1.27) (name "VIN" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
    (pin passive line (at 0 -3.81 90) (length 1.27) (name "B" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27))))))
  (embedded_fonts no))"""

pj = Proj("h10_nested")
r = pj.root
ch = pj.child("chan.kicad_sch")
gc = pj.child("gchild.kicad_sch")
r.sheet("S1", ch, 127, 50.8)
r.sheet("S2", ch, 127, 101.6)
_, w = ch.sheet("GC", gc, 50.8, 63.5, pins=[("P", "input", "left", 2)])
(a1, _) = ch.R(["R11", "R21"], 25.4, 101.6)
ch.wire(*a1, 20.32, a1[1])
ch.wire(20.32, a1[1], 20.32, w["P"][1])
ch.wire(20.32, w["P"][1], *w["P"])
(b1, _) = ch.R(["R12", "R22"], 101.6, 101.6)
ch.wire(*b1, 96.52, b1[1])
ch.label("N1", 96.52, b1[1])
(c1, _) = ch.R(["R13", "R23"], 127, 101.6)
ch.wire(*c1, 121.92, c1[1])
ch.power("GND", 121.92, c1[1], refs=["#PWR001", "#PWR002"])
ch.symbol("pt", "VPIN", ["U15", "U25"], 152.4, 101.6, "VPIN", pins=("1", "2"))
(g1, _) = gc.R(["R41", "R42"], 50.8, 50.8)
gc.wire(*g1, g1[0], 41.91)
gc.wire(g1[0], 41.91, g1[0], 38.1)
gc.label("P", g1[0], 41.91, kind="hierarchical_label")
gc.power("+5V", g1[0], 38.1, refs=["#PWR003", "#PWR004"])
pj.write()
NODES = [("R11", "1"), ("R21", "1"), ("R41", "1"), ("R12", "1"), ("R22", "1"), ("R13", "1")]
run(log, "h10a_nested_N1", pj.dir, pj.root.fname, "chan.kicad_sch", "wire_pins_to_net",
    dict(pins=[{"reference": "R11", "pin": "1"}], label_text="N1"),
    allowed=[("R11", "1"), ("R21", "1"), ("R12", "1"), ("R22", "1")],
    ideal="refuse: RA:1 is on +5V through the nested sheet pin; N1 would join both instances' N1 into +5V",
    pin_nodes=NODES)
run(log, "h10b_nested_GND", pj.dir, pj.root.fname, "chan.kicad_sch", "wire_pins_to_net",
    dict(pins=[{"reference": "R11", "pin": "1"}], label_text="GND"),
    allowed=[("R11", "1"), ("R21", "1"), ("R13", "1")],
    ideal="refuse: +5V/GND short", pin_nodes=NODES)
run(log, "h10c_pwrflag_multi", pj.dir, pj.root.fname, "chan.kicad_sch", "wire_pins_to_net",
    dict(pins=[{"reference": "U15", "pin": "1"}], label_text="VDD", auto_pwr_flag=True),
    allowed=[("U15", "1"), ("U25", "1")],
    ideal="U15:1 and U25:1 on /S1/VDD and /S2/VDD; any PWR_FLAG must load in both instances",
    pin_nodes=[("U15", "1"), ("U25", "1")])
log.save()
