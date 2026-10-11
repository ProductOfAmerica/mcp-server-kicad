"""Rerun of verify-probe-decisions/v01_autolabel.py on M2 and K (DEC-01 W1-W3, DEC-21 W4).

Fixture copied from v01: R1:1 (63.5, 72.39), R2:1 (88.9, 72.39), R5:1 on SIG (upward stub), R3 and
R4 rotated 180.
  W1 connect(R1:1, R2:1); wire([R1:1], 'SIG')
  W2 connect(R1:1, R2:1); connect(R1:1, R5:1)
  W3 connect(R1:1, R2:1); connect(R3:1, R4:1); connect(R2:1, R3:1)
  T  (truth after P's connect plus raw labels): not re-run, a control
  W4 legacy label from the repo's connect_pins (fixture state); then wire([R1:1],'SIG'),
     wire([R5:1],'Net-(R1-1)'), connect(R1:1, R5:1); then the remedy remove_label('Net-(R1-1)')
     (the repo's tool, applied to each design's copy as a fixture action) and wire([R1:1],'SIG').
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

R.begin("decisions", __file__, tag="dec_v01")
S = "verify-probe-decisions/v01_autolabel.py; rerun decisions/dec_v01.py"
RES = "Device:R"

base = D.v_new("v01")
D.v_place(base, RES, "R1", 63.5, 76.2)
D.v_place(base, RES, "R2", 88.9, 76.2)
D.v_place(base, RES, "R5", 38.1, 76.2)
D.v_place(base, RES, "R3", 88.9, 50.8, rot=180)
D.v_place(base, RES, "R4", 114.3, 50.8, rot=180)
D.v_stubbed(base, "R5", "1", "SIG")
print("pins:", {r: D.pos(base, r, "1") for r in ("R1", "R2", "R3", "R4", "R5")})

R.run_sequence("DEC-01", "v01_autolabel:W1", [
    cstep("R1", "1", "R2", "1", "connect(R1:1, R2:1)"),
    wstep([("R1", "1")], "SIG", "wire([R1:1], 'SIG')"),
], base, script=S)
R.run_sequence("DEC-01", "v01_autolabel:W2", [
    cstep("R1", "1", "R2", "1", "connect(R1:1, R2:1)"),
    cstep("R1", "1", "R5", "1", "connect(R1:1, R5:1)"),
], base, script=S)
R.run_sequence("DEC-01", "v01_autolabel:W3", [
    cstep("R1", "1", "R2", "1", "connect(R1:1, R2:1)"),
    cstep("R3", "1", "R4", "1", "connect(R3:1, R4:1)"),
    cstep("R2", "1", "R3", "1", "connect(R2:1, R3:1)"),
], base, script=S)

w4dir = R.copy_tree(Path(base).parent, "v_v01_W4")
w4 = str(w4dir / Path(base).name)
print("fixture: B.connect_pins(R1:1, R2:1):",
      D.schematic.connect_pins(ref1="R1", pin1="1", ref2="R2", pin2="1", schematic_path=w4))


def remedy(path):
    return D.schematic.remove_label(text="Net-(R1-1)", schematic_path=path)


D.run_sequence_ex("DEC-21", "v01_autolabel:W4", [
    wstep([("R1", "1")], "SIG", "wire([R1:1], 'SIG')"),
    wstep([("R5", "1")], "Net-(R1-1)", "wire([R5:1], 'Net-(R1-1)')"),
    cstep("R1", "1", "R5", "1", "connect(R1:1, R5:1)"),
    dict(action=remedy, label="remove_label('Net-(R1-1)') (the repo's tool; fixture action)"),
    wstep([("R1", "1")], "SIG", "wire([R1:1], 'SIG') after remove_label"),
], w4, script=S)
R.end()
