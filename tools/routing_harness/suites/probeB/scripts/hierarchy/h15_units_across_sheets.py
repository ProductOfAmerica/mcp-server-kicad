# probeB copy of pr57pt/probe-hierarchy/h15_units_across_sheets.py.
# Fixture code unchanged. The original's explicit P/M/B loop (one copy per design, the sequence of
# calls on it, then the root netlist) is replaced by rlib.run_seq (M2 and K, one copy each, every
# step judged by judge3 on the ROOT against the state before that step; pad U1:14 in two nets shows
# as pads_multi).
"""H15: one multi-unit part split across sheets. 4xxx_IEEE:4011 draws its unit-0 pins 14 (Vdd)
and 7 (Vss) in every placed unit. U1 unit 1 sits on the root, U1 unit 2 on sub-sheet C, so pad
U1:14 has one drawn copy per sheet. Each design sees only the edited sheet's copy.

Root: U1 unit 1; power +5V on R9:1 (stub end).  Child: U1 unit 2; power +5V on R31:1 (stub end).
15a root:  wire_pins_to_net([U1:14], '+5V')
15b root then child: wire_pins_to_net([U1:14], '+5V') on each sheet
15c root then child: wire_pins_to_net([U1:14], 'VDD') on each sheet (a local name)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h15_units_across_sheets", __file__)
from hlib import HERE, Log, Proj, run_seq  # noqa: E402

log = Log(HERE / "h15_units_across_sheets.out")

pj = Proj("h15")
r = pj.root
ch = pj.child("c.kicad_sch")
r.sheet("C", ch, 203.2, 25.4)
PINS = ("1", "2", "3", "7", "14")
r.symbol("4xxx_IEEE", "4011", "U1", 101.6, 101.6, "4011", pins=PINS, unit=1)
(g1, _) = r.R("R9", 50.8, 50.8)
r.wire(*g1, g1[0], round(g1[1] - 5.08, 4))
r.power("+5V", g1[0], round(g1[1] - 5.08, 4))
ch.symbol("4xxx_IEEE", "4011", ["U1"], 101.6, 101.6, "4011", pins=("5", "6", "4", "7", "14"), unit=2)
(c1, _) = ch.R(["R31"], 50.8, 50.8)
ch.wire(*c1, c1[0], round(c1[1] - 5.08, 4))
ch.power("+5V", c1[0], round(c1[1] - 5.08, 4))
pj.write()
log(f"pin 14 copies: root {rlib.M2.pin_positions(pj.root.path, 'U1').get('14')}, child {rlib.M2.pin_positions(ch.path, 'U1').get('14')}")

steps = {
    "15a_root_only_p5V": [(pj.root.fname, "+5V")],
    "15b_both_sheets_p5V": [(pj.root.fname, "+5V"), ("c.kicad_sch", "+5V")],
    "15c_both_sheets_VDD": [(pj.root.fname, "VDD"), ("c.kicad_sch", "VDD")],
}
for scen, seq in steps.items():
    run_seq(log, scen, pj.dir, pj.root.fname,
            [(f, "wire_pins_to_net", dict(pins=[{"reference": "U1", "pin": "14"}], label_text=N))
             for f, N in seq],
            ideal="pad U1:14 must end in ONE net")
log.save()
