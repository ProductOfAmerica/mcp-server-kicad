"""CMP-05 / CMP-10 reading the judge does not make: does the cap placed on a sub-sheet reach the
PROJECT's VCC/GND (the root's power symbols, on R1:1 / R1:2), and does the message say the
nets are sheet-local? The originals (c06, v05) measured exactly this from a kicad-cli root export.

For c06 K1/K2/K0 and v05 K1/K2/K3/K0 (fixtures copied from c06_subsheet_power_names.py and
v05_subsheet_names.py of this rerun), each design runs the same call(s) on its own copy (kw paths
remapped into the copy); kicad-cli 9.0.8 exports the root (judge3.nets_full); recorded: C1:1 with
R1:1, C1:2 with R1:2, the cap pins' net names, and whether the message mentions 'local'.
Writes rails_check.json; no records (the judged calls are in cmp_c06_* / cmp_v05_* parts).
"""

import json
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import J  # noqa: E402

import cmplib as C  # noqa: E402
from rlib import H, SCH  # noqa: E402


# build functions copied verbatim from c06_subsheet_power_names.py / v05_subsheet_names.py
# (those scripts run their calls at import, so they are not imported)
def build_c06(tag, child_gnd):
    d = H.BASE / tag
    H.project.create_project(str(d), tag)
    root, pro = str(d / f"{tag}.kicad_sch"), str(d / f"{tag}.kicad_pro")
    kid = str(d / "kid.kicad_sch")
    H.project.create_schematic(kid)
    H.project.add_hierarchical_sheet(parent_schematic_path=root, sheet_name="kid", sheet_file=kid,
                                     pins=[], x=152.4, y=25.4, project_path=pro)
    pc = SCH.place_component
    pc(lib_id="Device:R", reference="R1", value="1k", x=50.8, y=101.6, schematic_path=root,
       project_path=pro)                                   # R1:1 (50.8, 97.79), R1:2 (50.8, 105.41)
    H.wire(root, 50.8, 97.79, 50.8, 92.71)
    pc(lib_id="power:VCC", reference="#PWR01", value="VCC", x=50.8, y=92.71, schematic_path=root,
       project_path=pro)
    H.wire(root, 50.8, 105.41, 50.8, 110.49)
    pc(lib_id="power:GND", reference="#PWR02", value="GND", x=50.8, y=110.49,
       schematic_path=root, project_path=pro)
    pc(lib_id="Device:R", reference="R8", value="1k", x=50.8, y=50.8, schematic_path=kid,
       project_path=pro)                                   # R8:2 at (50.8, 54.61)
    if child_gnd:
        H.wire(kid, 50.8, 54.61, 50.8, 59.69)
        pc(lib_id="power:GND", reference="#PWR03", value="GND", x=50.8, y=59.69,
           schematic_path=kid, project_path=pro)
    return {"root": root, "pro": pro, "kid": kid}




def build_v05(tag, child_gnd=False):
    d = H.BASE / tag
    H.project.create_project(str(d), tag)
    root, pro = str(d / f"{tag}.kicad_sch"), str(d / f"{tag}.kicad_pro")
    kid = str(d / "child.kicad_sch")
    H.project.create_schematic(kid)
    H.project.add_hierarchical_sheet(parent_schematic_path=root, sheet_name="child",
                                     sheet_file=kid, pins=[], x=177.8, y=38.1, project_path=pro)
    C.place(root, "Device:R", "R1", 63.5, 101.6, pro=pro)          # R1:1 (63.5, 97.79)
    H.wire(root, 63.5, 97.79, 63.5, 92.71)
    C.place(root, "power:VCC", "#PWR01", 63.5, 92.71, value="VCC", pro=pro)
    H.wire(root, 63.5, 105.41, 63.5, 110.49)
    C.place(root, "power:GND", "#PWR02", 63.5, 110.49, value="GND", pro=pro)
    C.place(kid, "Device:R", "R8", 63.5, 63.5, pro=pro)            # R8:2 (63.5, 67.31)
    if child_gnd:
        H.wire(kid, 63.5, 67.31, 63.5, 72.39)
        C.place(kid, "power:GND", "#PWR03", 63.5, 72.39, value="GND", pro=pro)
    return {"root": root, "pro": pro, "kid": kid, "dir": d}




OUT = []


def check(script, case, fx, target, kw, pre=None):
    base = Path(fx["root"]).parent
    for dz in R.DESIGNS:
        d = C.copy_dir(base, base.name + f"__rail_{dz}")
        e2, r2 = C.rel(target, base, d), C.rel(fx["root"], base, d)
        msgs = []
        if pre:
            st0, m0 = R.call(R.IMPLS[dz].wire_pins_to_net, schematic_path=str(C.rel(pre[0], base, d)),
                             **pre[1])
            msgs.append(f"pre-wire {st0}: {m0}")
        st, msg = R.call(R.IMPLS[dz].auto_place_decoupling_cap, schematic_path=str(e2),
                         **C.remap(kw, base, d))
        msgs.append(f"cap {st}: {msg}")
        nets = J.nets_full(r2) or []

        def net_of(node):
            return [(nm, sorted(f"{a}:{b}" for a, b in v)) for _c, nm, _k, v in nets if node in v]

        row = {"script": script, "case": case, "design": dz, "status": st,
               "C1:1": net_of(("C1", "1")), "C1:2": net_of(("C1", "2")),
               "C1:1 with R1:1 (project VCC)": any(("C1", "1") in v and ("R1", "1") in v
                                                    for *_x, v in nets),
               "C1:2 with R1:2 (project GND)": any(("C1", "2") in v and ("R1", "2") in v
                                                    for *_x, v in nets),
               "message_mentions_local": "local" in msg.lower(), "messages": msgs}
        OUT.append(row)
        print(f"{script} {case} {dz}: {st} | C1:1 {row['C1:1']} | C1:2 {row['C1:2']} | project VCC"
              f" {row['C1:1 with R1:1 (project VCC)']} GND {row['C1:2 with R1:2 (project GND)']}"
              f" | 'local' in msg {row['message_mentions_local']}", flush=True)
        shutil.rmtree(d, ignore_errors=True)


R.begin("composite-aux", __file__, tag="cmp_rails_check_aux")
for case, child_gnd, on_root in (("K1", False, False), ("K2", True, False), ("K0", False, True)):
    fx = build_c06(f"rc06_{case}", child_gnd)
    kw = dict(lib_id="Device:C", reference="C1", value="100n", x=127, y=101.6, power_net="VCC",
              ground_net="GND", project_path=fx["pro"])
    check("c06_subsheet_power_names", case, fx, fx["root"] if on_root else fx["kid"], kw)
for case, child_gnd, on_root, pre_wire in (("K1", False, False, False), ("K2", True, False, False),
                                           ("K3", False, False, True), ("K0", False, True, False)):
    fx = build_v05(f"rv05_{case}", child_gnd)
    kw = dict(lib_id="Device:C", reference="C1", value="100n", x=152.4, y=127,
              power_net="VCC", ground_net="GND", project_path=fx["pro"])
    pre = None
    if pre_wire:
        C.place(fx["kid"], "Device:R", "R7", 101.6, 63.5, pro=fx["pro"])
        pre = (fx["kid"], dict(pins=R.pins(("R7", "1")), label_text="VCC"))
    check("v05_subsheet_names", case, fx, fx["root"] if on_root else fx["kid"], kw, pre)
(R.OUT / "composite").mkdir(parents=True, exist_ok=True)
(R.OUT / "composite" / "rails_check.json").write_text(json.dumps(OUT, indent=1),
                                                      encoding="utf-8")
(R.OUT / "parts" / "cmp_rails_check_aux.jsonl").unlink(missing_ok=True)
R.end()
