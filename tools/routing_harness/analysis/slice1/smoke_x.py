"""Slice 1 gate: smoke-test design X under the harness interpreter before a full run.
Checks the package the harness resolves is the target's (under hpaths.TARGET), that x_impl wraps
that same package, and that one write, one refusal and one validation failure come back in the
shapes judge3.call expects.
Run: uv run python tools/routing_harness/analysis/slice1/smoke_x.py > smoke_x.out
"""

import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

TMP = hpaths.suite_out("slice1") / "tmp" / f"smoke_x_{os.getpid()}"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(TMP)
tempfile.tempdir = str(TMP)
import harness as Hn  # noqa: E402
import judge3 as J  # noqa: E402
import x_impl as X  # noqa: E402

import mcp_server_kicad  # noqa: E402

print("target package:", Path(mcp_server_kicad.__file__).parent)
print("x_impl package:", Path(X._REPO.__file__).parent)
hpaths.assert_target(mcp_server_kicad.__file__)
hpaths.assert_target(X._REPO.__file__)

d = Path(tempfile.mkdtemp(prefix="smx_"))
p = Path(Hn.fresh("smoke_x"))
Hn.place(str(p), "Device:R", "R1", 101.6, 101.6)
Hn.place(str(p), "Device:R", "R2", 152.4, 101.6)
st, msg = J.call(X.wire_pins_to_net, pins=[{"reference": "R1", "pin": "1"}], label_text="N",
                 schematic_path=str(p))
print("write:", st, "|", msg.splitlines()[0])
assert st == "OK"
st, msg = J.call(X.wire_pins_to_net, pins=[{"reference": "R1", "pin": "1"}], label_text="M",
                 schematic_path=str(p))
print("refusal:", st, "|", msg.splitlines()[0][:120])
assert st == "REFUSED" and "[H:names]" in msg
st, msg = J.call(X.wire_pins_to_net, pins=[{"reference": "R2", "pin": "1"}], label_text=" N",
                 auto_pwr_flag=False, schematic_path=str(p))
print("validation:", st, "|", msg.splitlines()[0][:120])
assert st == "REFUSED" and "[H:validation]" in msg
st, msg = J.call(X.wire_pins_to_net, pins=[{"reference": "R1", "pin": "1"}], label_text="N",
                 schematic_path=str(p))
print("no-op:", st, "|", msg.splitlines()[0][:120])
assert st == "NOOP"
print("smoke ok")
