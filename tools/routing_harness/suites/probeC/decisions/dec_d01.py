"""Rerun of probe-decisions/d01_autolabel_trap.py on M2 and K (DEC-01 W1-W3, DEC-21 W4).

Fixture copied from d01: R1:1, R2:1, R5:1 (on local label SIG), R3:1, R4:1.
  W1 connect(R1:1, R2:1); wire([R1:1], 'SIG')
  W2 connect(R1:1, R2:1); connect(R1:1, R5:1)
  W3 connect(R1:1, R2:1); connect(R3:1, R4:1); connect(R2:1, R3:1)
  W4 legacy file: the repo's connect_pins(R1:1, R2:1) wrote its automatic label (fixture state);
     then the calls M made: wire([R1:1],'SIG'); wire([R5:1],'Net-(R1-1)'); connect(R1:1, R5:1)
Each workflow runs per design on its own evolving copy (rlib.run_sequence).
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, cstep, wstep  # noqa: E402
from pathlib import Path  # noqa: E402

R.begin("decisions", __file__, tag="dec_d01")
S = "probe-decisions/d01_autolabel_trap.py; rerun decisions/dec_d01.py"
RES = "Device:R"

base = D.fresh("d01")
D.hplace(base, RES, "R1", 101.6, 101.6)
D.hplace(base, RES, "R2", 127, 101.6)
D.hplace(base, RES, "R5", 76.2, 101.6)
D.hplace(base, RES, "R3", 152.4, 127)
D.hplace(base, RES, "R4", 177.8, 127)
D.d_stub_label(base, "R5", "1", "SIG")

R.run_sequence("DEC-01", "d01_autolabel_trap:W1", [
    cstep("R1", "1", "R2", "1", "W1.1 connect(R1:1, R2:1)"),
    wstep([("R1", "1")], "SIG", "W1.2 wire([R1:1], 'SIG')"),
], base, script=S)
R.run_sequence("DEC-01", "d01_autolabel_trap:W2", [
    cstep("R1", "1", "R2", "1", "W2.1 connect(R1:1, R2:1)"),
    cstep("R1", "1", "R5", "1", "W2.2 connect(R1:1, R5:1) (R5:1 on SIG)"),
], base, script=S)
R.run_sequence("DEC-01", "d01_autolabel_trap:W3", [
    cstep("R1", "1", "R2", "1", "W3.1 connect(R1:1, R2:1)"),
    cstep("R3", "1", "R4", "1", "W3.2 connect(R3:1, R4:1)"),
    cstep("R2", "1", "R3", "1", "W3.3 connect(R2:1, R3:1)"),
], base, script=S)

# W4: the legacy automatic label written by the repo's own connect_pins is fixture state
w4dir = R.copy_tree(Path(base).parent, "d01_W4")
w4 = str(w4dir / Path(base).name)
print("fixture W4.1 B.connect_pins(R1:1, R2:1):",
      D.schematic.connect_pins(ref1="R1", pin1="1", ref2="R2", pin2="1", schematic_path=w4))
R.run_sequence("DEC-21", "d01_autolabel_trap:W4", [
    wstep([("R1", "1")], "SIG", "W4.2 wire([R1:1], 'SIG')"),
    wstep([("R5", "1")], "Net-(R1-1)", "W4.3 wire([R5:1], 'Net-(R1-1)')"),
    cstep("R1", "1", "R5", "1", "W4.4 connect(R1:1, R5:1)"),
], w4, script=S)
R.end()
