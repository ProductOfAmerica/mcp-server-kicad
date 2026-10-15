"""Rerun on M2, H, Hb and E (follow-up 2 copy of the follow-up 1 script) of the hierarchical llm-workflow scenarios, judged on the ROOT netlist:
  hier_probe.py   LW-10 (also LW-15). Project 'hier': root hier.kicad_sch with U1 STM32G030F6Px and
                  C1; sheet 'Sensor' (sensor.kicad_sch) made by add_hierarchical_sheet with pins SDA,
                  SCL, +3V3, GND at (177.8, 50.8); child BME280 U2, C2, pull-ups R1, R2. Calls: the
                  four child batches (edited = child), then the four root batches. H2: before the
                  root SDA batch, the repo's add_wires hooks U1's PB7/PB8 pin to the SDA sheet-pin
                  stub end (177.8-2.54, 50.8+2.54) (a fixture step inside the sequence).
  v_lw10_port.py  LW-10. Project 'hp': sheet 'IO' (io.kicad_sch) with pins SDA, SCL at
                  (190.5, 63.5); U1 on the root, R1 on the child. K1: wire_pins_to_net([U1:PB7/PB8],
                  'SDA'). K2: first add_wires from U1's pin to the parent's local SDA label, then the
                  same call.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H  # noqa: E402

import lwlib as LW  # noqa: E402
import compat54  # noqa: E402  (frozen 54160c1 add_wires and connect_pins: fixture setup)
from mcp_server_kicad import project  # noqa: E402

R.begin(LW.CAT, __file__, tag="lw_hier" + "".join("_" + a for a in sys.argv[1:]))
B = H.schematic
which = set(sys.argv[1:]) or {"hier", "v10"}

# ------------------------------------------------------------------ hier_probe.py
CHILD_CALLS = [("+3V3", [("U2", "8"), ("U2", "6"), ("U2", "2"), ("C2", "1"), ("R1", "1"), ("R2", "1")]),
               ("GND", [("U2", "1"), ("U2", "7"), ("U2", "5"), ("C2", "2")]),
               ("SDA", [("U2", "3"), ("R1", "2")]),
               ("SCL", [("U2", "4"), ("R2", "2")])]
ROOT_CALLS = [("+3V3", [("U1", "VDD"), ("C1", "1")]), ("GND", [("U1", "VSS"), ("C1", "2")]),
              ("SDA", [("U1", "PB7/PB8")]), ("SCL", [("U1", "PB9/PC14")])]


def build_hier(tag):
    d = LW.fixture_dir(f"hier_{tag}")
    project.create_project(str(d), "hier")
    root, child, pro = d / "hier.kicad_sch", d / "sensor.kicad_sch", d / "hier.kicad_pro"
    project.create_schematic(str(child))
    project.add_hierarchical_sheet(
        parent_schematic_path=str(root), sheet_name="Sensor", sheet_file=str(child),
        pins=[{"name": "SDA", "direction": "bidirectional"}, {"name": "SCL", "direction": "input"},
              {"name": "+3V3", "direction": "passive"}, {"name": "GND", "direction": "passive"}],
        x=177.8, y=50.8, project_path=str(pro))
    for sch, lib, ref, val, x, y in (
            (root, "MCU_ST_STM32G0:STM32G030F6Px", "U1", "STM32G030", 101.6, 101.6),
            (root, "Device:C", "C1", "100n", 101.6, 63.5),
            (child, "Sensor:BME280", "U2", "BME280", 101.6, 101.6),
            (child, "Device:C", "C2", "100n", 76.2, 101.6),
            (child, "Device:R", "R1", "4k7", 127.0, 76.2),
            (child, "Device:R", "R2", "4k7", 139.7, 76.2)):
        B.place_component(lib_id=lib, reference=ref, value=val, x=x, y=y,
                          schematic_path=str(sch), project_path=str(pro))
    return root, child, pro


if "hier" in which:
    for variant in ("H1", "H2"):
        root, child, pro = build_hier(variant)
        LW.header(f"hier_probe {variant}")
        seqs = {}
        for dz in R.DESIGNS:
            s = LW.Seq("LW-10", f"hier_probe:{variant}", dz, root, base_edited=child, also=["LW-15"],
                       script=LW.PROBE + "hier_probe.py")
            for N, pins in CHILD_CALLS:
                s.call("wire_pins_to_net", dict(pins=LW.pins_kw(pins), label_text=N), f"child {N}",
                       edited=child)
            for N, pins in ROOT_CALLS:
                if variant == "H2" and N == "SDA":
                    rr = s.map(root)
                    num = LW.pin_numbers(str(rr), "U1", "PB7/PB8")[0]
                    px, py, _ = R.M2.pin_positions(str(rr), "U1")[num][0]
                    sx, sy = 177.8 - 2.54, 50.8 + 2.54
                    msg = compat54.add_wires(wires=[{"x1": px, "y1": py, "x2": px, "y2": sy},
                                             {"x1": px, "y1": sy, "x2": sx, "y2": sy}],
                                      schematic_path=str(rr))
                    print(f"  [{dz}] add_wires U1:PB7/PB8 -> sheet stub: {msg}", flush=True)
                s.call("wire_pins_to_net", dict(pins=LW.pins_kw(pins), label_text=N), f"root {N}",
                       edited=root)
            seqs[dz] = s
        LW.close_all(seqs)

# ------------------------------------------------------------------ v_lw10_port.py


def build_hp(tag):
    d = LW.fixture_dir(f"hp_{tag}")
    project.create_project(str(d), "hp")
    root, child, pro = d / "hp.kicad_sch", d / "io.kicad_sch", d / "hp.kicad_pro"
    project.create_schematic(str(child))
    project.add_hierarchical_sheet(parent_schematic_path=str(root), sheet_name="IO", sheet_file=str(child),
                                   pins=[{"name": "SDA", "direction": "bidirectional"},
                                         {"name": "SCL", "direction": "input"}],
                                   x=190.5, y=63.5, project_path=str(pro))
    B.place_component(lib_id="MCU_ST_STM32G0:STM32G030F6Px", reference="U1", value="STM32G030",
                      x=101.6, y=101.6, schematic_path=str(root), project_path=str(pro))
    B.place_component(lib_id="Device:R", reference="R1", value="4k7", x=101.6, y=101.6,
                      schematic_path=str(child), project_path=str(pro))
    return root, child, pro


if "v10" in which:
    for variant in ("K1", "K2"):
        root, child, pro = build_hp(variant)
        from mcp_server_kicad import _cst

        rt = _cst.parse(Path(root).read_bytes()).lists[0]
        labels = [(n.atoms[1].text, n.find("at").atoms[1].text, n.find("at").atoms[2].text)
                  for n in rt.find_all("label")]
        px, py, _ = R.M2.pin_positions(str(root), "U1")["1"][0]
        sda = [lbl for lbl in labels if lbl[0] == "SDA"][0]
        lx, ly = float(sda[1]), float(sda[2])
        if variant == "K2":
            compat54.add_wires(wires=[{"x1": px, "y1": py, "x2": px, "y2": ly},
                               {"x1": px, "y1": ly, "x2": lx, "y2": ly}], schematic_path=str(root))
        R.run_case("LW-10", f"v_lw10_port:{variant}", "wire_pins_to_net", root,
                   dict(pins=LW.pins_kw([("U1", "PB7/PB8")]), label_text="SDA"), root=root,
                   script=LW.VERIFY + "v_lw10_port.py",
                   note="K2: U1:PB7/PB8 hooked to the SDA stub by add_wires first" if variant == "K2" else
                   "K1 control")
R.end()
