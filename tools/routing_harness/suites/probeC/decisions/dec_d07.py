"""Rerun of probe-decisions/d07_nc_type_pin.py on M2 and K (DEC-09 a/b, DEC-10 c, DEC-17 c0).

Fixtures copied from d07 (custom NCPART: pin 2 is no_connect type):
  p   U1:2 (109.22, 101.6) NC-type; R5:1 on local N; R6:1 free, in line with U1:2
  q   R1:2 and R2:1 both wired to U1:2's end; R5:1 on N2 (c0: R1:2's outward stub runs along
      its own wire)
  s   R1:1 wired sideways and R2:1 wired up to U1:2's end; R5:1 on N2 (c: clean stub)
Calls: a wire([U1:2],'N')  b connect(U1:2, R6:1)  c0 wire([R1:2],'N2')  c wire([R1:1],'N2').
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import C, H, R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d07")
S = "probe-decisions/d07_nc_type_pin.py; rerun decisions/dec_d07.py"
RES = "Device:R"
lib = D.d_write_custom_lib(H.BASE / "d07_lib.kicad_sym")

p = D.fresh("d07")
D.d_place_custom(p, lib, "NCPART", "U1", 101.6, 101.6)    # U1:2 (109.22, 101.6), type no_connect
D.hplace(p, RES, "R5", 177.8, 101.6)
D.d_stub_label(p, "R5", "1", "N")
D.hplace(p, RES, "R6", 127, 105.41)                       # R6:1 (127, 101.6), in line with U1:2

q = D.fresh("d07c")
D.d_place_custom(q, lib, "NCPART", "U1", 101.6, 101.6)
D.hplace(q, RES, "R1", 109.22, 76.2)                      # R1:2 (109.22, 80.01)
D.hplace(q, RES, "R2", 109.22, 127)                       # R2:1 (109.22, 123.19)
D.wire(q, *D.pos(q, "R1", "2"), *D.pos(q, "U1", "2"))      # R1:2 -> U1:2
D.wire(q, *D.pos(q, "R2", "1"), *D.pos(q, "U1", "2"))      # R2:1 -> U1:2
D.hplace(q, RES, "R5", 177.8, 101.6)
D.d_stub_label(q, "R5", "1", "N2")

s = D.fresh("d07c2")
D.d_place_custom(s, lib, "NCPART", "U1", 101.6, 101.6)
D.hplace(s, RES, "R1", 119.38, 105.41)                    # R1:1 (119.38, 101.6), outward up
D.hplace(s, RES, "R2", 109.22, 127)                       # R2:1 (109.22, 123.19)
D.wire(s, *D.pos(s, "R1", "1"), *D.pos(s, "U1", "2"))      # horizontal R1:1 -> U1:2
D.wire(s, *D.pos(s, "R2", "1"), *D.pos(s, "U1", "2"))      # vertical R2:1 -> U1:2
D.hplace(s, RES, "R5", 177.8, 101.6)
D.d_stub_label(s, "R5", "1", "N2")
print("U1:2", D.pos(p, "U1", "2"), "R6:1", D.pos(p, "R6", "1"))

R.run_case("DEC-09", "d07_nc_type_pin:a", "wire_pins_to_net", p, W([("U1", "2")], "N"), script=S)
R.run_case("DEC-09", "d07_nc_type_pin:b", "connect_pins", p, C("U1", "2", "R6", "1"), script=S)
R.run_case("DEC-17", "d07_nc_type_pin:c0", "wire_pins_to_net", q, W([("R1", "2")], "N2"), script=S)
R.run_case("DEC-10", "d07_nc_type_pin:c", "wire_pins_to_net", s, W([("R1", "1")], "N2"), script=S)
R.end()
