"""Manager re-check of two pressure-test claims, independent of the worker's harness.

Claim 1 reads the target's own pin position (schematic._get_pin_pos_cst), so it reports the tree the
harness resolves: this checkout, or the tree PT_REPO names. A 54160c1 export (git archive 54160c1
mcp_server_kicad tests) reports 4 of the 12 rotation and mirror orientations WRONG, and a tree with
slice 1's pin fix reports all 12 ok (measured 2026-10-10 with KiCad 9.0.8, at 54160c1 and at
8750f08; re-run to confirm). Claim 2 reads only kicad-cli (the same output at both).
Run: uv run python tools/routing_harness/analysis/manager/mgr_verify.py"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
from wptn_harness import fresh, pin, raw_wire, junction, nets, same_net, net_name
from mcp_server_kicad import schematic

print("\n=== Claim 1: the repo's pin position is wrong for rotation 90/270 with a mirror")
for rot in (0, 90, 180, 270):
    for mir in ("", "x", "y"):
        p = fresh(f"m{rot}{mir}")
        schematic.place_component(lib_id="Device:R", reference="R1", value="R1", x=101.6, y=101.6,
                                  rotation=rot, mirror=mir, schematic_path=p)
        x, y, _ = pin(p, "R1", "1")
        schematic.add_label("PROBE", x, y, schematic_path=p)
        n = nets(p)
        on = sorted(pn for (r, pn) in n.get("PROBE", {}) if r == "R1") if "PROBE" in n else []
        verdict = "ok" if on == ["1"] else "WRONG"
        print(f"rot={rot:3} mirror={mir or '-'}: label at repo pin1 ({x},{y}) lands on R1 pins {on}  {verdict}")

print("\n=== Claim 2: kicad-cli 9 keeps only the start half of a wire cut by a junction on its interior")
# Same crossing as my R8, but the vertical wire is drawn TOWARD the pin, so R1:1 sits on the far half.
for toward in (False, True):
    p = fresh(f"j{toward}")
    schematic.place_component(lib_id="Device:R", reference="R1", value="R1", x=101.6, y=101.6, schematic_path=p)
    schematic.place_component(lib_id="Device:R", reference="R2", value="R2", x=88.9, y=92.71, schematic_path=p)
    x1, y1, _ = pin(p, "R1", "1"); x2, y2, _ = pin(p, "R2", "1")
    if toward:
        raw_wire(p, x1, 76.2, x1, y1)
    else:
        raw_wire(p, x1, y1, x1, 76.2)
    raw_wire(p, x2, y2, 114.3, y2)
    junction(p, 101.6, 88.9)
    print(f"vertical wire drawn {'toward' if toward else 'from'} R1:1: R1:1 joined to R2:1 = "
          f"{same_net(nets(p), ('R1', '1'), ('R2', '1'))}")
