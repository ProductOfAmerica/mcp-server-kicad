# probeB copy of pr57pt/probe-units-pads/r_reused_sheet_units.py.
# Fixture code unchanged (hb builder, RUNS redirected under probeB's temp dir). The original ran the
# call through a shim so its judge could export the ROOT; here judge() takes the root and the
# edited child separately (judge3 on the ROOT, per-instance references of U1 in the groups).
"""R: one child sheet used twice (S1, S2), drawing a DIFFERENT unit of one shared package per
instance: chan.kicad_sch places U1 (74xx_IEEE:7400) once, with instance entries
  /<root>/<S1>  reference U1  unit 1   /<root>/<S2>  reference U1  unit 2
  R1  chan: wire_pins_to_net([U1:14], VCC)   legit: pad 14 joins VCC in both instances
  R2  chan: wire_pins_to_net([U1:1], N)      pad 1 exists only in instance S1; in S2 the same
                                               point is pad 4
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("r_reused_sheet_units", __file__)

import os  # noqa: E402
import shutil  # noqa: E402

import hb  # noqa: E402

from uplib import IMPLS, W, _edit, dump, judge, schematic, set_fp, wire  # noqa: E402

hb.RUNS = hb.Path(str(rlib.PROC_TMP / "uruns"))

pj = hb.Proj("r_base")
chan = pj.child("chan.kicad_sch")
s1, _ = pj.sheet(pj.root, "S1", "chan.kicad_sch", 152.4, 50.8)
s2, _ = pj.sheet(pj.root, "S2", "chan.kicad_sch", 152.4, 101.6)
paths = [f"/{pj.root_uuid}/{s1}", f"/{pj.root_uuid}/{s2}"]
pj.sym(chan, "74xx_IEEE:7400", ["U1", "U1"], 101.6, 101.6, paths=paths)
pj.sym(chan, "power:VCC", ["#PWR01", "#PWR02"], 63.5, 76.2, value="VCC", paths=paths)


def second_unit(root):
    s = schematic._find_syms_cst(root, "U1")[0]
    ps = s.find("instances").find("project").find_all("path")
    ps[1].find("unit").atoms[1].set_text("2")


_edit(chan, second_unit)
pj.sym(pj.root, "Device:C", "C9", 63.5, 101.6)
pj.sym(pj.root, "power:VCC", "#PWR09", 63.5, 93.98, value="VCC")
wire(pj.root, 63.5, 97.79, 63.5, 93.98)
set_fp(chan, "U1", "Package_DIP:DIP-14_W7.62mm")
set_fp(pj.root, "C9", "Capacitor_SMD:C_0603_1608Metric")

for tag, pin, N in (("R1", "14", "VCC"), ("R2", "1", "N")):
    for impl in rlib.DESIGNS:
        d = os.path.join(str(rlib.PROC_TMP), "uruns", f"r_{tag}_{impl}")
        if os.path.exists(d):
            shutil.rmtree(d)
        shutil.copytree(str(pj.dir), d)
        root = os.path.join(d, "r_base.kicad_sch")
        ch = os.path.join(d, "chan.kicad_sch")
        judge(f"{tag} chan: U1:{pin} -> {N}", impl, root, IMPLS[impl]["wire"], W(ch, [("U1", pin)], N),
              targets=[("U1", pin)] + ([("U1", "4")] if pin == "1" else []), N=N, files=[root, ch],
              vid=tag)
dump("r_reused_sheet_units.json")
