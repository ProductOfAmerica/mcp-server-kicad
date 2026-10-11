"""Rerun of verify-probe-decisions/v15_alternates.py on M2 and K (DEC-15).

Fixture copied from v15: U2 VHP pin 2 hidden power_in 'VDD'; U3 VALTP pin 2 hidden power_in 'VDD'
with the passive alternate PASV selected; U4 VALTW pin 2 hidden passive 'VDD' with the power_in
alternate PWRIN selected; R5:1 on SIG. Calls: f wire([U3:2],'SIG')  g wire([U3:2],'VDD')
h wire([U4:2],'SIG')  i wire([U4:2],'VDD'). The two raw-label truths are controls, not re-run.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import H, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_v15")
S = "verify-probe-decisions/v15_alternates.py; rerun decisions/dec_v15.py"
RES = "Device:R"

lib = D.v_write_lib(H.BASE / "v15.kicad_sym")
p = D.v_new("v15")
D.v_place_lib(p, lib, "VHP", "U2", 63.5, 88.9)
D.v_place_lib(p, lib, "VALTP", "U3", 114.3, 63.5)
D.v_place_lib(p, lib, "VALTW", "U4", 114.3, 88.9)
D.v_set_alt(p, "U3", "2", "PASV")
D.v_set_alt(p, "U4", "2", "PWRIN")
D.v_place(p, RES, "R5", 165.1, 101.6)
D.v_stubbed(p, "R5", "1", "SIG")
print("U3:2", D.pos(p, "U3", "2"), "U4:2", D.pos(p, "U4", "2"))
for tag, tgt, N in (("f", ("U3", "2"), "SIG"), ("g", ("U3", "2"), "VDD"), ("h", ("U4", "2"), "SIG"),
                    ("i", ("U4", "2"), "VDD")):
    R.run_case("DEC-15", f"v15_alternates:{tag}", "wire_pins_to_net", p, W([tgt], N), script=S)
R.end()
