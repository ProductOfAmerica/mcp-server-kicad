"""Slice 1 gate: the adapter's refusal lines in the harness's grammar (after the first rerun4
pass showed the adaptive policies never fired: they parse '- REFUSED [H:code] REF:PIN: reason').
Run: uv run python tools/routing_harness/analysis/slice1/smoke_x2.py > smoke_x2.out
"""

import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__, suites=("layouts_stock",))

TMP = hpaths.suite_out("slice1") / "tmp" / f"smoke_x2_{os.getpid()}"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(TMP)
tempfile.tempdir = str(TMP)
import harness as Hn  # noqa: E402
import judge3 as J  # noqa: E402
import x_impl as X  # noqa: E402
from runner import refused_lines  # noqa: E402

hpaths.assert_target(X._REPO.__file__)  # the adapter wraps the target's package, not another checkout's
p = Path(Hn.fresh("smoke_x2"))
Hn.place(str(p), "Device:R", "R1", 101.6, 101.6)
Hn.place(str(p), "Device:R", "R2", 152.4, 101.6)
Hn.wire(str(p), 95, 97.79, 110, 97.79)  # through R1:1's end: [touch]
st, msg = J.call(X.wire_pins_to_net, pins=[{"reference": "R1", "pin": "1"}, {"reference": "R2", "pin": "1"}],
                 label_text="N", schematic_path=str(p))
print(st, "|", msg.splitlines()[1][:140] if len(msg.splitlines()) > 1 else msg)
lines = refused_lines(msg.split(": ", 1)[1])
print("parsed:", lines)
assert st == "REFUSED" and lines and lines[0][0] == "R1:1" and lines[0][2] == ["touch"]

text = X._recode(
    "[resolve] wire_pins_to_net refused the whole call; nothing was written.\n"
    "- [resolve] U1:COM: pin name 'COM' on U1 matches several pads that are not drawn at one"
    " point (pad 3 at (96.52, 101.6); pad 5 at (106.68, 101.6)). Remedy: pass the pad number."
    "\n- [touch] U1:VSS (pad 23): stub down blocked: x"
)
print(text)
parsed = refused_lines(text)
print("parsed:", parsed)
assert parsed[0][0] == "U1:COM" and "matches several pads (3, 5)" in parsed[0][1]
assert parsed[1][0] == "U1:VSS" and parsed[1][1].startswith("(pad 23) ")
print("smoke2 ok")
