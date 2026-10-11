# probeB copy of pr57pt/probe-buses/s13_global_bus_member.py (header swapped to the probeB shims; fixture code unchanged).
"""S13: member name of a bus LABEL on the edited sheet (not a sheet pin). Root 'top' and child 'kid'
(no sheet pins). Root: bus carrying GLOBAL label 'GB[0..3]', split at an entry whose member wire is
labelled 'GB1' -> R1. Child: a bus carrying global label 'GB[0..3]' (no entries) and a free R2.
Edit the CHILD: wire_pins_to_net(R2:1, 'GB1'). The child has no item named 'GB1'.
Control s13c: the same without the child's bus label (a local 'GB1' on kid should stay /kid/GB1).
Judged on the root export.
"""
import sys
import uuid as _uuid
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s13_global_bus_member", __file__)
from bl import (PROJ, _cst, bus, bus_entry, check_pin, dump, fresh, label, pins_arg, place,  # noqa: E402
                scenario, show_nets, wire)
from harness import schematic as sch  # noqa: E402


def root_uuid(p):
    return _cst.parse(open(p, "rb").read()).lists[0].find("uuid").atoms[1].text


def build(name, child_bus_label=True):
    p = fresh(name)
    d = Path(p).parent
    ru = root_uuid(p)
    su = str(_uuid.uuid4())
    sheet = f'''(sheet (at 152.4 63.5) (size 25.4 15.24) (exclude_from_sim no) (in_bom yes) (on_board yes)
 (dnp no) (stroke (width 0) (type solid)) (fill (color 0 0 0 0.0000)) (uuid "{su}")
 (property "Sheetname" "kid" (at 152.4 62.7184 0) (effects (font (size 1.27 1.27)) (justify left bottom)))
 (property "Sheetfile" "kid.kicad_sch" (at 152.4 79.3246 0) (effects (font (size 1.27 1.27)) (justify left top)))
 (instances (project "{name}" (path "/{ru}" (page "2")))))'''
    node = _cst.parse(sheet.encode()).lists[0]
    tree = _cst.parse(open(p, "rb").read())
    sch._splice_sch_node(tree.lists[0], "sheet", node)
    open(p, "wb").write(_cst.serialize(tree))
    # root: bus with global label GB[0..3], entry, member GB1 -> R1
    bus(p, 76.2, 81.28, 76.2, 93.98)
    bus(p, 76.2, 93.98, 76.2, 106.68)
    label(p, "GB[0..3]", 76.2, 86.36, rot=90, kind="global_label")
    bus_entry(p, 76.2, 93.98, 2.54, 2.54)
    place(p, "Device:R", "R1", 106.68, 100.33)
    check_pin(p, "R1", "1", (106.68, 96.52))
    wire(p, 78.74, 96.52, 106.68, 96.52)
    label(p, "GB1", 88.9, 96.52)
    kid = str(d / "kid.kicad_sch")
    PROJ.create_schematic(schematic_path=kid)
    if child_bus_label:
        bus(kid, 50.8, 50.8, 76.2, 50.8)
        label(kid, "GB[0..3]", 55.88, 50.8, kind="global_label")
    place(kid, "Device:R", "R2", 101.6, 101.6)
    check_pin(kid, "R2", "1", (101.6, 97.79))
    t = open(kid, encoding="utf-8").read()
    ku = root_uuid(kid)
    t2 = t.replace('(project "kid"', f'(project "{name}"').replace(f'(path "/{ku}"', f'(path "/{ru}/{su}"')
    open(kid, "w", encoding="utf-8", newline="").write(t2)
    return p


p = build("s13_probe")
print("fixture nets (root export):")
show_nets(p, [("R1", "1"), ("R2", "1")])
for sid, cbl in (("s13a", True), ("s13c", False)):
    p = build(sid, child_bus_label=cbl)
    scenario(sid, f"child R2:1 -> 'GB1'; child {'has' if cbl else 'has NO'} global bus label GB[0..3];"
             " root member GB1 -> R1", p,
             ("wire_pins_to_net", dict(pins=pins_arg(("R2", "1")), label_text="GB1")),
             edit_rel="kid.kicad_sch", root_rel=Path(p).name, targets=[("R2", "1")], N="GB1",
             expect=None, n_alias=("/kid/GB1",),
             note="a local label GB1 on kid prints as /kid/GB1; any other destination is a leak")
dump("s13_global_bus_member")
