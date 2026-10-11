"""Rerun of verify-probe-composite/v05_subsheet_names.py (CMP-05, also CMP-10) on M2 and K.

Project root: R1 with power:VCC on R1:1 and power:GND on R1:2; child sheet 'child'.
  K1 child has no power symbols; cap on the child.
  K2 child also has power:GND (on R8:2); cap on the child.
  K3 child has R7 (placed with the repo's place_component, fixture building), then the
     original's two design calls in sequence on each design's own evolving file:
     wire_pins_to_net([R7:1], 'VCC') on the child, then the cap on the child.
  K0 control: cap on the root.
build() copied verbatim. Root export judges (root= the root sheet). The original's kicad-cli ERC
on the root ran for P only; it is a P-side reading and is not repeated.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H  # noqa: E402

import cmplib as C  # noqa: E402

place = C.place


def build(tag, child_gnd=False):
    d = H.BASE / tag
    H.project.create_project(str(d), tag)
    root, pro = str(d / f"{tag}.kicad_sch"), str(d / f"{tag}.kicad_pro")
    kid = str(d / "child.kicad_sch")
    H.project.create_schematic(kid)
    H.project.add_hierarchical_sheet(parent_schematic_path=root, sheet_name="child",
                                     sheet_file=kid, pins=[], x=177.8, y=38.1, project_path=pro)
    place(root, "Device:R", "R1", 63.5, 101.6, pro=pro)          # R1:1 (63.5, 97.79)
    H.wire(root, 63.5, 97.79, 63.5, 92.71)
    place(root, "power:VCC", "#PWR01", 63.5, 92.71, value="VCC", pro=pro)
    H.wire(root, 63.5, 105.41, 63.5, 110.49)
    place(root, "power:GND", "#PWR02", 63.5, 110.49, value="GND", pro=pro)
    place(kid, "Device:R", "R8", 63.5, 63.5, pro=pro)            # R8:2 (63.5, 67.31)
    if child_gnd:
        H.wire(kid, 63.5, 67.31, 63.5, 72.39)
        place(kid, "power:GND", "#PWR03", 63.5, 72.39, value="GND", pro=pro)
    return {"root": root, "pro": pro, "kid": kid, "dir": d}


R.begin("composite", __file__, tag="cmp_v05_subsheet_names")
S = "verify-probe-composite/v05_subsheet_names.py"
for case, child_gnd, on_root, pre_wire in (("K1", False, False, False), ("K2", True, False, False),
                                           ("K3", False, False, True), ("K0", False, True, False)):
    fx = build(f"v05_{case}", child_gnd)
    target = fx["root"] if on_root else fx["kid"]
    cap = dict(lib_id="Device:C", reference="C1", value="100n", x=152.4, y=127,
               power_net="VCC", ground_net="GND", project_path=fx["pro"])
    note = ("root" if on_root else "child") + (" (child has power:GND)" if child_gnd else "") \
        + (" (R7:1 pre-wired to VCC on the child by the same design)" if pre_wire else "")
    if pre_wire:
        place(fx["kid"], "Device:R", "R7", 101.6, 63.5, pro=fx["pro"])  # R7:1 (101.6, 59.69)
        C.run_sequence("CMP-05", f"v05_subsheet_names:{case}", [
            dict(tool="wire_pins_to_net",
                 kw=dict(pins=R.pins(("R7", "1")), label_text="VCC"), label="pre-wire R7:1"),
            dict(tool="auto_place_decoupling_cap", kw=cap, label="cap"),
        ], target, root=fx["root"], script=S, also=["CMP-10"], note=note)
    else:
        C.run_case("CMP-05", f"v05_subsheet_names:{case}", "auto_place_decoupling_cap", target,
                   cap, root=fx["root"], script=S, also=["CMP-10"], note=note)
R.end()
