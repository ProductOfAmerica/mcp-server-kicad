# probeB copy of pr57pt/probe-buses/s07_member_leak.py (header swapped to the probeB shims; fixture code unchanged).
"""S07: bus member names leak across hierarchy (digest BUS-6), with P, M and B as the tools.

Root 'top' holds sheet 'kid' (file kid.kicad_sch) whose sheet pin is a bus ('Q[0..3]', or the
group '{SDA SCL}' in s07c). No bus is drawn on the parent at the pin unless stated. The child has
the matching hierarchical label on a bus split at its entries, and member wires labelled 'Q2'
(-> R82) or 'SDA'/'SCL' (-> R83/R84). Parent R81 is free. Judged on the ROOT export.

s07a  parent: wire_pins_to_net(R81:1, 'Q2')   (no 'Q2' anywhere on the parent sheet)
s07b  parent: wire_pins_to_net(R81:1, 'Q3')   (no Q3 net in the child either)
s07c  parent: wire_pins_to_net(R81:1, 'SDA')  (group sheet pin '{SDA SCL}')
s07d  child:  wire_pins_to_net(R85:1, 'Q1')   (child has no 'Q1'; parent has a bus from the sheet
              pin with an entry to a wire labelled 'Q1' -> R1)
"""
import sys
import uuid as _uuid
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s07_member_leak", __file__)
from bl import *  # noqa: F401,F403
from bl import (PROJ, _cst, bus, bus_entry, check_pin, dump, fresh, label, pins_arg, place,
                scenario, show_nets, wire)
from harness import schematic as sch  # noqa: E402


def root_uuid(p):
    return _cst.parse(open(p, "rb").read()).lists[0].find("uuid").atoms[1].text


def build(name, pin_name="Q[0..3]", members=(("Q2", "R82"),), parent_bus_member=None,
          child_free=False):
    p = fresh(name)
    d = Path(p).parent
    ru = root_uuid(p)
    su = str(_uuid.uuid4())
    sheet = f'''(sheet
	(at 127 63.5)
	(size 25.4 15.24)
	(exclude_from_sim no)
	(in_bom yes)
	(on_board yes)
	(dnp no)
	(stroke
		(width 0)
		(type solid)
	)
	(fill
		(color 0 0 0 0.0000)
	)
	(uuid "{su}")
	(property "Sheetname" "kid"
		(at 127 62.7184 0)
		(effects (font (size 1.27 1.27)) (justify left bottom))
	)
	(property "Sheetfile" "kid.kicad_sch"
		(at 127 79.3246 0)
		(effects (font (size 1.27 1.27)) (justify left top))
	)
	(pin "{pin_name}" input
		(at 127 68.58 180)
		(uuid "{_uuid.uuid4()}")
		(effects (font (size 1.27 1.27)) (justify left))
	)
	(instances
		(project "{name}"
			(path "/{ru}"
				(page "2")
			)
		)
	)
)'''
    node = _cst.parse(sheet.encode()).lists[0]
    tree = _cst.parse(open(p, "rb").read())
    sch._splice_sch_node(tree.lists[0], "sheet", node)
    open(p, "wb").write(_cst.serialize(tree))
    place(p, "Device:R", "R81", 88.9, 101.6)
    check_pin(p, "R81", "1", (88.9, 97.79))
    if parent_bus_member:
        # parent bus from the sheet pin, split at an entry; entry wire labelled -> R1
        mname = parent_bus_member
        bus(p, 127, 68.58, 114.3, 68.58)
        bus(p, 114.3, 68.58, 101.6, 68.58)
        bus_entry(p, 114.3, 68.58, -2.54, 2.54)       # wire side (111.76, 71.12)
        place(p, "Device:R", "R1", 111.76, 78.74)
        check_pin(p, "R1", "1", (111.76, 74.93))
        wire(p, 111.76, 71.12, 111.76, 74.93)
        label(p, mname, 111.76, 73.66, rot=90)
    kid = str(d / "kid.kicad_sch")
    PROJ.create_schematic(schematic_path=kid)
    label(kid, pin_name, 50.8, 50.8, rot=180, kind="hierarchical_label", shape="input")
    xs = [63.5 + 12.7 * i for i in range(len(members))]
    pts = [50.8, *xs, xs[-1] + 12.7]
    for a, b in zip(pts, pts[1:]):
        bus(kid, a, 50.8, b, 50.8)
    for x, (mname, ref) in zip(xs, members):
        bus_entry(kid, x, 50.8, 2.54, 2.54)            # wire side (x+2.54, 53.34)
        place(kid, "Device:R", ref, x + 2.54, 64.77)
        check_pin(kid, ref, "1", (round(x + 2.54, 4), 60.96))
        wire(kid, x + 2.54, 53.34, x + 2.54, 60.96)
        label(kid, mname, x + 2.54, 57.15, rot=90)
    if child_free:
        place(kid, "Device:R", "R85", 101.6, 101.6)
        check_pin(kid, "R85", "1", (101.6, 97.79))
    t = open(kid, encoding="utf-8").read()
    ku = root_uuid(kid)
    t2 = t.replace('(project "kid"', f'(project "{name}"').replace(f'(path "/{ku}"', f'(path "/{ru}/{su}"')
    assert t2 != t and f"/{ru}/{su}" in t2
    open(kid, "w", encoding="utf-8", newline="").write(t2)
    return p


p = build("s07_probe", parent_bus_member="Q1", child_free=True)
print("fixture nets (root export, s07d shape):")
show_nets(p)

W = "wire_pins_to_net"
R81 = ("R81", "1")
scenario("s07a", "parent R81:1 -> 'Q2'; sheet pin 'Q[0..3]'; child member Q2 -> R82", build("s07a"),
         (W, dict(pins=pins_arg(R81), label_text="Q2")), targets=[R81], N="Q2", expect=None,
         note="no 'Q2' item exists on the parent; the caller sees a free name")
scenario("s07b", "parent R81:1 -> 'Q3'; sheet pin 'Q[0..3]'; child has no Q3", build("s07b"),
         (W, dict(pins=pins_arg(R81), label_text="Q3")), targets=[R81], N="Q3", expect=None)
scenario("s07c", "parent R81:1 -> 'SDA'; group sheet pin '{SDA SCL}'; child SDA -> R83, SCL -> R84",
         build("s07c", pin_name="{SDA SCL}", members=(("SDA", "R83"), ("SCL", "R84"))),
         (W, dict(pins=pins_arg(R81), label_text="SDA")), targets=[R81], N="SDA", expect=None)
p = build("s07d", parent_bus_member="Q1", child_free=True)
scenario("s07d", "child R85:1 -> 'Q1'; child hier bus 'Q[0..3]'; parent entry wire 'Q1' -> R1", p,
         (W, dict(pins=pins_arg(("R85", "1")), label_text="Q1")), edit_rel="kid.kicad_sch",
         root_rel=Path(p).name, targets=[("R85", "1")], N="Q1", expect="OK",
         n_alias=("/kid/Q1",),
         note="control: a local label Q1 on sheet kid is printed /kid/Q1, so /kid/Q1 IS net N here")

dump("s07_member_leak")
