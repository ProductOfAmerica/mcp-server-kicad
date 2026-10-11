# probeB copy of pr57pt/probe-naming/n11c_frontpanel_vcc.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N11c: why are the hidden VCC pins of front-panel-io's 74LVC2G17GV parts on net 'P1V8' in
kicad-cli? Compare P's and M's component names for those pins, then run wire_pins_to_net on one
of them with 'P1V8' and with 'VCC' (sub-sheet exported alone: named nets are kept)."""
import sys, shutil
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n11c_frontpanel_vcc", __file__)
from nlib import BASE, M, P, W, _cst, dump, nets_coded, step
src = str(hpaths.KICAD_DEMOS / "vme-wren" / "front-panel-io.kicad_sch")
root = _cst.parse(open(src, "rb").read()).lists[0]
pm, mm = P._Model(root), M._Model(root)
mm.ensure()
for i, k in enumerate(pm.kind):
    d = pm.data[i]
    if k == "pin" and d["ref"] in ("IC2",) and d["number"] in ("5", "2"):
        info = pm.comp(i)
        print("P", d["ref"], d["number"], d["pt"], "names", sorted(info["names"]),
              "pins", len(info["pins"]))
for it in mm.items:
    if it.kind == "pin" and it.ref == "IC2" and it.num in ("5", "2"):
        n0 = mm.names_of("P0", it); nc = mm.names_of("C", it)
        print("M", it.ref, it.num, (it.x, it.y), "P0 names", sorted(n0.known), "C names", sorted(nc.known),
              "members", len(mm.members("P0", it)))
        at = [x.kind + (":" + (x.ref or "") + ":" + (x.num or "") if x.kind == "pin" else ":" + str(x.text))
              for x in mm._pts.get((it.x, it.y), []) if x is not it]
        print("   items at that point:", at)
for N in ("P1V8", "VCC"):
    for dsg in rlib.DESIGNS:
        dd = BASE / f"n11c_{N}" / dsg
        dd.mkdir(parents=True)
        s = str(dd / "fp.kicad_sch")
        shutil.copy(src, s)
        step(dsg, f"N11c front-panel-io: wire_pins_to_net [IC2:5] -> {N}", s, [s], "wire_pins_to_net",
             W([("IC2", "5")], N, s), targets=[("IC2", "5")], N=N, vid=N)
dump(__file__)
