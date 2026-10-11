# probeB copy of pr57pt/probe-units-pads/h_units_two_sheets.py.
# Fixture code unchanged (sem-hierarchy/hb builder, RUNS redirected under probeB's temp dir). The
# per-design loop runs H1 (root edit) then H2 (child edit) on one copy per design, both judged by
# judge3 on the ROOT. Not run: the original's board check (update_pcb_from_schematic).
"""H: one package, two units on two SHEETS. 74xx_IEEE:7400 U1: gate A (unit 1) on the root,
gate B (unit 2) on child sheet 'Sub'. Pad 14 (VCC) and pad 7 (GND) are unit-0 pins.
  H1  root: wire_pins_to_net([U1:14], VCC, auto_pwr_flag=False)
  H2  after H1, child: wire_pins_to_net([U1:14], VCC, auto_pwr_flag=False) on sub.kicad_sch
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h_units_two_sheets", __file__)

import os  # noqa: E402
import shutil  # noqa: E402

import hb  # noqa: E402

from uplib import IMPLS, W, _edit, dump, judge, schematic, set_fp, wire  # noqa: E402

hb.RUNS = hb.Path(str(rlib.PROC_TMP / "uruns"))


def build(name):
    pj = hb.Proj(name)
    child = pj.child("sub.kicad_sch")
    su, _ = pj.sheet(pj.root, "Sub", "sub.kicad_sch", 152.4, 50.8)
    pj.sym(pj.root, "74xx_IEEE:7400", "U1", 101.6, 101.6)
    pj.sym(child, "74xx_IEEE:7400", ["U1"], 101.6, 101.6, paths=[f"/{pj.root_uuid}/{su}"])

    def unit2(root):
        s = schematic._find_syms_cst(root, "U1")[0]
        s.find("unit").atoms[1].set_text("2")
        for path in s.find("instances").find("project").find_all("path"):
            path.find("unit").atoms[1].set_text("2")

    _edit(child, unit2)
    pj.sym(pj.root, "Device:C", "C1", 63.5, 101.6)
    pj.sym(pj.root, "power:VCC", "#PWR01", 63.5, 93.98, value="VCC")
    wire(pj.root, 63.5, 97.79, 63.5, 93.98)
    pj.sym(pj.root, "power:GND", "#PWR02", 63.5, 109.22, value="GND")
    wire(pj.root, 63.5, 105.41, 63.5, 109.22)
    for f in (pj.root, child):
        set_fp(f, "U1", "Package_DIP:DIP-14_W7.62mm")
    set_fp(pj.root, "C1", "Capacitor_SMD:C_0603_1608Metric")
    return pj, child


pj, child = build("h_base")
for impl in rlib.DESIGNS:
    d = os.path.join(str(rlib.PROC_TMP), "uruns", f"h_{impl}")
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(str(pj.dir), d)
    root = os.path.join(d, "h_base.kicad_sch")
    sub = os.path.join(d, "sub.kicad_sch")
    fn = IMPLS[impl]["wire"]
    judge("H1 root sheet: U1:14 -> VCC", impl, root, fn,
          W(root, [("U1", "14")], "VCC", auto_pwr_flag=False), targets=[("U1", "14")], N="VCC",
          files=[root, sub], vid="H", stepno=1, nsteps=2)
    judge("H2 child sheet: U1:14 -> VCC (local label on the child)", impl, root, fn,
          W(sub, [("U1", "14")], "VCC", auto_pwr_flag=False), targets=[("U1", "14")], N="VCC",
          files=[root, sub], vid="H", stepno=2, nsteps=2)
dump("h_units_two_sheets.json")
