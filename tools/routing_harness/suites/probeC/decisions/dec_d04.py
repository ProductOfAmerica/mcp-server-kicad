"""Rerun of probe-decisions/d04_power_pins_as_targets.py on M2 and K (DEC-23 a-e, DEC-15 f-h; i is
cited by no case and is assigned to DEC-15, the alternates case).

Fixture copied from d04 (one sheet): #PWR01 GND wired to R1:1; #PWR02 VCC free (target); #PWR03 VCC
wired to R2:1; U2 HPIN pin 2 hidden power_in 'VCC'; U3 HALT pin 2 hidden power_in 'VCC' with the
passive alternate ALTP selected; U4 HALT2 pin 2 hidden passive 'VCC' with the power_in alternate
PWRALT selected; R5:1 on local SIG; R6 free.
Calls (fresh copy each): a wire([#PWR02:1],'VCC')  b wire([#PWR02:1],'GND')  c connect(#PWR02:1,
R6:1)  d wire([U2:2],'VCC')  e wire([U2:2],'SIG')  f wire([U3:2],'SIG')  g wire([U3:2],'VCC')
h wire([U4:2],'SIG')  i wire([U4:2],'VCC').
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import C, H, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d04")
S = "probe-decisions/d04_power_pins_as_targets.py; rerun decisions/dec_d04.py"
RES = "Device:R"

lib = D.d_write_custom_lib(H.BASE / "d04_lib.kicad_sym")
p = D.fresh("d04")
D.hplace(p, RES, "R1", 50.8, 50.8)
D.hplace(p, "power:GND", "#PWR01", 50.8, 38.1, value="GND", rot=180)
D.hplace(p, "power:VCC", "#PWR02", 76.2, 38.1, value="VCC")
D.hplace(p, RES, "R2", 101.6, 50.8)
D.hplace(p, "power:VCC", "#PWR03", 101.6, 38.1, value="VCC")
for ref, r in (("#PWR01", "R1"), ("#PWR03", "R2")):
    (px, py) = D.pos(p, ref, "1")
    (rx, ry) = D.pos(p, r, "1")
    D.wire(p, px, py, rx, ry)
D.d_place_custom(p, lib, "HPIN", "U2", 101.6, 101.6)
D.d_place_custom(p, lib, "HALT", "U3", 152.4, 101.6)
D.d_place_custom(p, lib, "HALT2", "U4", 152.4, 127)
D.d_set_alternate(p, "U3", "2", "ALTP")
D.d_set_alternate(p, "U4", "2", "PWRALT")
D.hplace(p, RES, "R5", 50.8, 127)
D.d_stub_label(p, "R5", "1", "SIG")
D.hplace(p, RES, "R6", 127, 50.8)
print("pins:", {r: D.pos(p, r, "1") for r in ("#PWR01", "#PWR02", "#PWR03")},
      "U2:2", D.pos(p, "U2", "2"), "U3:2", D.pos(p, "U3", "2"), "U4:2", D.pos(p, "U4", "2"),
      "R6:1", D.pos(p, "R6", "1"))

CALLS = [
    ("a", "DEC-23", "wire_pins_to_net", W([("#PWR02", "1")], "VCC")),
    ("b", "DEC-23", "wire_pins_to_net", W([("#PWR02", "1")], "GND")),
    ("c", "DEC-23", "connect_pins", C("#PWR02", "1", "R6", "1")),
    ("d", "DEC-23", "wire_pins_to_net", W([("U2", "2")], "VCC")),
    ("e", "DEC-23", "wire_pins_to_net", W([("U2", "2")], "SIG")),
    ("f", "DEC-15", "wire_pins_to_net", W([("U3", "2")], "SIG")),
    ("g", "DEC-15", "wire_pins_to_net", W([("U3", "2")], "VCC")),
    ("h", "DEC-15", "wire_pins_to_net", W([("U4", "2")], "SIG")),
    ("i", "DEC-15", "wire_pins_to_net", W([("U4", "2")], "VCC")),
]
for tag, case, tool, kw in CALLS:
    note = "variant i is cited by no case; run as part of d04" if tag == "i" else ""
    R.run_case(case, f"d04_power_pins_as_targets:{tag}", tool, p, kw, script=S, note=note)
R.end()
