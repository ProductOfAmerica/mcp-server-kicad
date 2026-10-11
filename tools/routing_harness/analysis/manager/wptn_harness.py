"""Ground truth for wire_pins_to_net: KiCad's own netlist decides what is connected.

Runs against the target tree: this checkout, or the tree PT_REPO names (hpaths puts it and its tests/
on sys.path). Run: uv run python tools/routing_harness/analysis/manager/wptn_harness.py"""
import sys, os, tempfile, uuid
from pathlib import Path
sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

REPO = str(hpaths.TARGET)  # the repo under test (on sys.path, with its tests/, through hpaths)
import conftest
from mcp_server_kicad import _cst, schematic, project

TMP = hpaths.suite_out("manager") / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
BASE = Path(tempfile.mkdtemp(prefix="wptn", dir=TMP))

def fresh(name):
    d = BASE / name; d.mkdir()
    p = str(d / f"{name}.kicad_sch"); project.create_schematic(schematic_path=p); return p

def place(p, lib, ref, x, y, rot=0):
    schematic.place_component(lib_id=lib, reference=ref, value=ref, x=x, y=y, rotation=rot, schematic_path=p)

def pin(p, ref, num):
    root = _cst.parse(open(p, "rb").read()).lists[0]
    return schematic._get_pin_pos_cst(root, ref, num)

def raw_wire(p, x1, y1, x2, y2):
    """A wire with no auto-junction, exactly as given."""
    tree = _cst.parse(open(p, "rb").read()); root = tree.lists[0]
    schematic._splice_wire(root, x1, y1, x2, y2); open(p, "wb").write(_cst.serialize(tree))

def junction(p, x, y):
    tree = _cst.parse(open(p, "rb").read()); root = tree.lists[0]
    node = schematic._JUNCTION_TPL.copy(); schematic._fill_at(node, x, y)
    node.find("uuid").atoms[1].set_text(str(uuid.uuid4())); schematic._splice_sch_node(root, "junction", node)
    open(p, "wb").write(_cst.serialize(tree))

def nets(p):
    return conftest.netlist_nodes(p)

def same_net(n, a, b):
    return any(a in v and b in v for v in n.values())

def net_name(n, node):
    return [k for k, v in n.items() if node in v]

print("=== KiCad connection rules (kicad-cli 9.0.8)")
# R2: a label on a wire's interior
p = fresh("r2"); place(p, "Device:R", "R1", 101.6, 101.6); x, y, _ = pin(p, "R1", "1")
raw_wire(p, x, y, x, 88.9); raw_wire(p, x, 88.9, 127, 88.9); schematic.add_label("MID", 114.3, 88.9, schematic_path=p)
print("R2 label on wire interior connects:", ("R1", "1") in nets(p).get("MID", {}))
# R3: a wire's end on another wire's interior, no junction
p = fresh("r3"); place(p, "Device:R", "R1", 101.6, 101.6); place(p, "Device:R", "R2", 127, 92.71)
x1, y1, _ = pin(p, "R1", "1"); x2, y2, _ = pin(p, "R2", "1")
raw_wire(p, 88.9, 88.9, 127, 88.9); raw_wire(p, x1, y1, x1, 88.9)
print(f"   R2 pin1 at ({x2},{y2}) is the far end of the horizontal wire")
print("R3 wire end on wire interior (no junction) connects:", same_net(nets(p), ("R1", "1"), ("R2", "1")))
# R4: a pin end on a wire's interior
p = fresh("r4"); place(p, "Device:R", "R1", 101.6, 101.6); place(p, "Device:R", "R2", 88.9, 101.6)
x1, y1, _ = pin(p, "R1", "1"); x2, y2, _ = pin(p, "R2", "1")
raw_wire(p, x2, y2, 114.3, y2)  # passes through R1 pin 1 at (101.6, 97.79)
print("R4 pin end on wire interior connects:", same_net(nets(p), ("R1", "1"), ("R2", "1")))
# R5 / R8: crossing wires, without and with a junction
for with_j in (False, True):
    p = fresh(f"r5{with_j}"); place(p, "Device:R", "R1", 101.6, 101.6); place(p, "Device:R", "R2", 88.9, 92.71)
    x1, y1, _ = pin(p, "R1", "1"); x2, y2, _ = pin(p, "R2", "1")
    raw_wire(p, x1, y1, x1, 76.2); raw_wire(p, x2, y2, 114.3, y2)  # cross at (101.6, 88.9)
    if with_j: junction(p, 101.6, 88.9)
    print(f"R{'8' if with_j else '5'} crossing wires {'with' if with_j else 'without'} junction connect:",
          same_net(nets(p), ("R1", "1"), ("R2", "1")))
