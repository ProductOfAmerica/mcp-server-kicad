"""Rerun of verify-probe-decisions/v03_hier_N_is_pin.py on M2 and K (DEC-03 h3/h4, DEC-04 h1/h2,
DEC-05 h5 and h5 batch).

Same hierarchy as v02 (sheet 'sub', sheet pin PA, parent label removed except in h5). R7:1 free or
(h2, h3) on a parent local label PA; R8:1 free.
  h1 plain, no other PA   h2 plain, R7:1 on PA   h3 GND, R7:1 on PA   h4 GND, no other PA
  h5 plain, the parent auto label PA kept: wire([R1:1],'PA') and batch wire([R1:1, R8:1],'PA')
Controls not re-run: the h4 latent-alias truth (a raw PA label on R8:1 after P's write) and the
h5 truth (a raw local PA on R1:1).
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_v03")
S = "verify-probe-decisions/v03_hier_N_is_pin.py; rerun decisions/dec_v03.py"
RES = "Device:R"


def build(name, gnd, r7_label, keep):
    root, pro, kid, stub = D.v_hierarchy(name, keep_parent_label=keep)
    D.v_place(root, RES, "R1", stub[0], round(stub[1] + 3.81, 4), project_path=pro)
    D.v_place(kid, RES, "R11", 50.8, 38.1, project_path=pro)
    D.wire(kid, 27.94, 25.4, 50.8, 25.4)
    D.wire(kid, 50.8, 25.4, 50.8, 34.29)
    if gnd:
        D.v_place(kid, "power:GND", "#PWR01", 50.8, 25.4, value="GND", project_path=pro)
    D.v_place(root, RES, "R7", 152.4, 101.6, project_path=pro)
    D.v_place(root, RES, "R8", 177.8, 101.6, project_path=pro)
    if r7_label:
        D.v_stubbed(root, "R7", "1", "PA")
    return root, kid


V = {"h1": (False, False, False), "h2": (False, True, False), "h3": (True, True, False),
     "h4": (True, False, False), "h5": (False, False, True)}
CASE = {"h1": "DEC-04", "h2": "DEC-04", "h3": "DEC-03", "h4": "DEC-03", "h5": "DEC-05"}
for v, (gnd, r7l, keep) in V.items():
    root, kid = build(f"v03_{v}", gnd, r7l, keep)
    R.run_case(CASE[v], f"v03_hier_N_is_pin:{v}", "wire_pins_to_net", root,
               W([("R1", "1")], "PA"), root=root, script=S)
    if v == "h5":
        R.run_case(CASE[v], f"v03_hier_N_is_pin:{v}:batch", "wire_pins_to_net", root,
                   W([("R1", "1"), ("R8", "1")], "PA"), root=root, script=S)
R.end()
