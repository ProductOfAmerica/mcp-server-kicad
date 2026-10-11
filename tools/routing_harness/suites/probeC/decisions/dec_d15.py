"""Rerun of probe-decisions/d15_N_is_an_auto_name.py on M2 and K (DEC-19).

Fixture copied from d15: R1:1-R2:1 wired (KiCad prints Net-(R1-Pad1)); R7 free.
  a wire([R7:1],'Net-(R1-Pad1)')   b wire([R1:1],'Net-(R1-Pad1)')
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402

R.begin("decisions", __file__, tag="dec_d15")
S = "probe-decisions/d15_N_is_an_auto_name.py; rerun decisions/dec_d15.py"

p = D.fresh("d15")
D.hplace(p, "Device:R", "R1", 101.6, 101.6)
D.hplace(p, "Device:R", "R2", 127, 101.6)
D.hplace(p, "Device:R", "R7", 177.8, 101.6)
D.wire(p, *D.pos(p, "R1", "1"), *D.pos(p, "R2", "1"))
for tag, tgt in (("a", ("R7", "1")), ("b", ("R1", "1"))):
    R.run_case("DEC-19", f"d15_N_is_an_auto_name:{tag}", "wire_pins_to_net", p,
               W([tgt], "Net-(R1-Pad1)"), script=S)
R.end()
