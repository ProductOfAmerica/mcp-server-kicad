# probeB copy of pr57pt/probe-units-pads/h3_units_two_sheets_plus5v.py.
# Fixture code unchanged (hb builder, RUNS redirected under probeB's temp dir). Each design wires
# U1:14 to '+5V' on the root, then on the child, on one copy per design; both steps judged by
# judge3 on the ROOT. Not run: the original's board check (update_pcb_from_schematic).
"""H3: as H but the supply is power:+5V (Value +5V) on the root, and both sheets are wired with
label_text '+5V'. On the child a LOCAL label '+5V' makes '/Sub/+5V', which does not reach the
root's global '+5V'."""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h3_units_two_sheets_plus5v", __file__)

import os  # noqa: E402
import shutil  # noqa: E402

import hb  # noqa: E402

from uplib import IMPLS, W, _edit, dump, judge, schematic, set_fp, wire  # noqa: E402

hb.RUNS = hb.Path(str(rlib.PROC_TMP / "uruns"))

pj = hb.Proj("h3_base")
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
pj.sym(pj.root, "power:+5V", "#PWR01", 63.5, 93.98, value="+5V")
wire(pj.root, 63.5, 97.79, 63.5, 93.98)
for f in (pj.root, child):
    set_fp(f, "U1", "Package_DIP:DIP-14_W7.62mm")
set_fp(pj.root, "C1", "Capacitor_SMD:C_0603_1608Metric")

for impl in rlib.DESIGNS:
    d = os.path.join(str(rlib.PROC_TMP), "uruns", f"h3_{impl}")
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(str(pj.dir), d)
    root = os.path.join(d, "h3_base.kicad_sch")
    sub = os.path.join(d, "sub.kicad_sch")
    fn = IMPLS[impl]["wire"]
    for i, (step, f) in enumerate((("root", root), ("child", sub)), 1):
        judge(f"H3 wire U1:14 -> '+5V' on the {step} sheet", impl, root, fn,
              W(f, [("U1", "14")], "+5V", auto_pwr_flag=False), targets=[("U1", "14")], N="+5V",
              files=[root, sub], vid="H3", stepno=i, nsteps=2)
dump("h3")
