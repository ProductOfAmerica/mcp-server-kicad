"""Rerun of probe-decisions/d05_noop_via_global_power_hier.py on M2 and K (DEC-22).

Fixtures copied from d05; each has a pin on a LOCAL label N on the same sheet (R5 or R12).
  5a root: R1:1 on global label VCC          5b root: R1:1 wired to power:VCC
  5c root: R1:1 wired to a hidden power_in pin named VCC (custom HPIN)
  5d root: R1:1 on a hierarchical label HR
  5e child: R11:1 on global label VCC        5f child: R11:1 wired to power:VCC
  5g child: R11:1 on the child's hierarchical label HN (sheet pin HN on the parent)
Call: wire_pins_to_net([target], N). The raw-label truths are controls, not re-run.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import H, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d05")
S = "probe-decisions/d05_noop_via_global_power_hier.py; rerun decisions/dec_d05.py"
RES = "Device:R"
lib = D.d_write_custom_lib(H.BASE / "d05_lib.kicad_sym")


def flat(name, build, N):
    p = D.fresh(name)
    D.hplace(p, RES, "R1", 101.6, 101.6)
    D.hplace(p, RES, "R5", 152.4, 101.6)
    build(p)
    D.d_stub_label(p, "R5", "1", N)
    return p, p, ("R1", "1")


def b5a(p):
    D.d_stub_label(p, "R1", "1", "VCC", kind="global_label")


def b5b(p):
    D.hplace(p, "power:VCC", "#PWR01", 101.6, 92.71, value="VCC")
    D.wire(p, *D.pos(p, "#PWR01", "1"), *D.pos(p, "R1", "1"))


def b5c(p):
    D.d_place_custom(p, lib, "HPIN", "U2", 76.2, 76.2)     # U2:2 hidden VCC at (83.82, 76.2)
    x, y = D.pos(p, "U2", "2")
    rx, ry = D.pos(p, "R1", "1")
    D.wire(p, x, y, rx, y)
    D.wire(p, rx, y, rx, ry)


def b5d(p):
    D.d_stub_label(p, "R1", "1", "HR", kind="hierarchical_label")


def hier(name, build, N, sheet_pins=()):
    d = H.BASE / name
    D.project.create_project(str(d), "hp")
    root, pro, kid = str(d / "hp.kicad_sch"), str(d / "hp.kicad_pro"), str(d / "kid.kicad_sch")
    D.project.create_schematic(kid)
    D.project.add_hierarchical_sheet(root, "kid", kid, pins=list(sheet_pins), x=101.6, y=50.8,
                                     project_path=pro)
    for r, x in (("R11", 101.6), ("R12", 152.4)):
        D.schematic.place_component(lib_id="Device:R", reference=r, value=r, x=x, y=101.6,
                                    schematic_path=kid, project_path=pro)
    D.schematic.place_component(lib_id="Device:R", reference="R1", value="R1", x=99.06, y=57.15,
                                schematic_path=root, project_path=pro)
    build(kid, pro)
    D.d_stub_label(kid, "R12", "1", N)
    return kid, root, ("R11", "1")


def b5e(kid, pro):
    D.d_stub_label(kid, "R11", "1", "VCC", kind="global_label")


def b5f(kid, pro):
    D.schematic.place_component(lib_id="power:VCC", reference="#PWR01", value="VCC", x=101.6,
                                y=92.71, schematic_path=kid, project_path=pro)
    D.wire(kid, *D.pos(kid, "#PWR01", "1"), *D.pos(kid, "R11", "1"))


def b5g(kid, pro):
    D.d_remove_nodes(kid, "label", lambda n: n.atoms[1].text == "HN")
    x, y = D.pos(kid, "R11", "1")
    D.wire(kid, 27.94, 25.4, x, 25.4)
    D.wire(kid, x, 25.4, x, y)


CASES = [
    ("5a", lambda: flat("d05a", b5a, "VCC"), "VCC"),
    ("5b", lambda: flat("d05b", b5b, "VCC"), "VCC"),
    ("5c", lambda: flat("d05c", b5c, "VCC"), "VCC"),
    ("5d", lambda: flat("d05d", b5d, "HR"), "HR"),
    ("5e", lambda: hier("d05e", b5e, "VCC"), "VCC"),
    ("5f", lambda: hier("d05f", b5f, "VCC"), "VCC"),
    ("5g", lambda: hier("d05g", b5g, "HN", sheet_pins=[{"name": "HN", "direction": "input"}]), "HN"),
]
for tag, build, N in CASES:
    edited, root, tgt = build()
    R.run_case("DEC-22", f"d05_noop_via_global_power_hier:{tag}", "wire_pins_to_net", edited,
               W([tgt], N), root=root, script=S)
R.end()
