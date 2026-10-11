"""Rerun of verify-probe-decisions/v02_hier.py on M2 and K (DEC-02 C and C+, DEC-03 GND/GLOBAL A/B,
DEC-04 plain A/B).

Fixture copied from v02: sheet 'sub' at (127, 63.5), sheet pin PA; the repo's parent stub label PA
removed; R1:1 on that stub end. Child: hier label PA + stub + local label PA at (27.94, 25.4), a
wire on to (50.8, 25.4) and down to R11:1. Child variants plain | GND (power:GND at the corner) |
GLOBAL (global label GEXT at the corner). Parent: R5:1 (76.2, 66.04) on N7; R6:1 free (rot 180).
Calls: A wire([R1:1],'N7')  B connect(R1:1, R5:1)  C connect(R1:1, R6:1).
C+: v02 ran, for P only in plain and GND, wire([R6:1],'X') on the file C left. Here C and C+ run as
one two-step sequence per design in plain and GND (each design on its own output); GLOBAL C runs
alone, as in the original.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import C, R, W, cstep, wstep  # noqa: E402

R.begin("decisions", __file__, tag="dec_v02")
S = "verify-probe-decisions/v02_hier.py; rerun decisions/dec_v02.py"
RES = "Device:R"


def build(variant):
    root, pro, kid, stub = D.v_hierarchy(f"v02_{variant}")
    D.v_place(root, RES, "R1", stub[0], round(stub[1] + 3.81, 4), project_path=pro)
    D.v_place(kid, RES, "R11", 50.8, 38.1, project_path=pro)
    D.wire(kid, 27.94, 25.4, 50.8, 25.4)
    D.wire(kid, 50.8, 25.4, 50.8, 34.29)
    if variant == "GND":
        D.v_place(kid, "power:GND", "#PWR01", 50.8, 25.4, value="GND", project_path=pro)
    elif variant == "GLOBAL":
        D.label(kid, "GEXT", 50.8, 25.4, kind="global_label")
    D.v_place(root, RES, "R5", 76.2, 69.85, project_path=pro)
    D.v_stubbed(root, "R5", "1", "N7")
    D.v_place(root, RES, "R6", 124.46, 50.8, rot=180, project_path=pro)
    return root, kid


CASE_AB = {"plain": "DEC-04", "GND": "DEC-03", "GLOBAL": "DEC-03"}
for variant in ("plain", "GND", "GLOBAL"):
    root, kid = build(variant)
    print(f"variant {variant}: pins", {r: D.pos(root, r, "1") for r in ("R1", "R5", "R6")},
          "R11:1", D.pos(kid, "R11", "1"))
    R.run_case(CASE_AB[variant], f"v02_hier:{variant}:A", "wire_pins_to_net", root,
               W([("R1", "1")], "N7"), root=root, script=S)
    R.run_case(CASE_AB[variant], f"v02_hier:{variant}:B", "connect_pins", root,
               C("R1", "1", "R5", "1"), root=root, script=S)
    if variant in ("plain", "GND"):
        R.run_sequence("DEC-02", f"v02_hier:{variant}:C", [
            cstep("R1", "1", "R6", "1", "C connect(R1:1, R6:1)"),
            wstep([("R6", "1")], "X", "C+ then wire([R6:1],'X') (original: P only)"),
        ], root, root=root, script=S)
    else:
        R.run_case("DEC-02", f"v02_hier:{variant}:C", "connect_pins", root,
                   C("R1", "1", "R6", "1"), root=root, script=S)
R.end()
