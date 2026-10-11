"""DEC-23 judge-limit check (follow-up 2). The probeC DEC-23 records flag M2/H/Hb's NOOP on
wire_pins_to_net([#PWR02:1], 'VCC') as a false no-op because no netlist lists a '#' node. This
rebuilds the DEC-23 power part of the fixture with probeC's own helpers (dec_d04.py: #PWR02 VCC
free, #PWR03 VCC wired to R2:1, #PWR01 GND wired to R1:1), then:
  1. exports the netlist as is (kicad-cli 9.0.8): which '#' nodes are listed at all;
  2. renames every '#PWRnn' to 'PWRnn' in a scratch copy and exports again (measured result: a
     power symbol stays out of the netlist without the '#', so this does not expose the node);
  3. places a free resistor R7 in a scratch copy and wires R7:1 to #PWR02's pin end: the net R7:1
     lands on is the net of the connection point #PWR02:1.
If R7:1 shares the net of R2:1 named VCC, #PWR02:1 is on VCC and the NOOP was right.
Run: uv run python tools/routing_harness/analysis/followup2/dec23_check.py
The report is also written to <run>/analysis/dec23_check.out (ROUTING_HARNESS_RUN names the run).
"""
import re
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__, suites=("probeC",))
sys.path.insert(0, str(hpaths.SUITES / "probeC" / "decisions"))
import dhelp as D  # noqa: E402  (imports probeC rlib, which redirects TMP under the run's probeC/tmp)
from dhelp import R  # noqa: E402

_REPORT = hpaths.RUN_DIR / "analysis" / "dec23_check.out"
_REPORT.parent.mkdir(parents=True, exist_ok=True)
_STDOUT, _FH = sys.stdout, open(_REPORT, "w", encoding="utf-8")


class _Tee:
    """stdout, also copied to the run's analysis/dec23_check.out."""

    def write(self, s):
        _STDOUT.write(s)
        _FH.write(s)
        return len(s)

    def flush(self):
        _STDOUT.flush()
        _FH.flush()


sys.stdout = _Tee()

RES = "Device:R"
p = D.fresh("d23check")
D.hplace(p, RES, "R1", 50.8, 50.8)
D.hplace(p, "power:GND", "#PWR01", 50.8, 38.1, value="GND", rot=180)
D.hplace(p, "power:VCC", "#PWR02", 76.2, 38.1, value="VCC")
D.hplace(p, RES, "R2", 101.6, 50.8)
D.hplace(p, "power:VCC", "#PWR03", 101.6, 38.1, value="VCC")
for ref, r in (("#PWR01", "R1"), ("#PWR03", "R2")):
    (px, py) = D.pos(p, ref, "1")
    (rx, ry) = D.pos(p, r, "1")
    D.wire(p, px, py, rx, ry)
nets = R.J.nets_full(p)
print("1. as built: nets with any '#' node:",
      [(n, sorted(v)) for _c, n, _k, v in nets if any(x[0].startswith("#") for x in v)])
print("   nets:", [(n, sorted(v)) for _c, n, _k, v in nets])
q = Path(p).with_name("renamed.kicad_sch")
b = Path(p).read_bytes()
b2, k = re.subn(rb'"#PWR(\d\d)"', rb'"PWR\1"', b)
q.write_bytes(b2)
print(f"2. renamed {k} quoted '#PWRnn' atoms in a scratch copy")
nets2 = R.J.nets_full(q)
for _c, n, _k, v in nets2:
    if any(x[0].startswith("PWR") for x in v):
        print(f"   net {n!r}: {sorted(v)}")
on = [n for _c, n, _k, v in nets2 if ("PWR02", "1") in v]
r2 = [n for _c, n, _k, v in nets2 if ("R2", "1") in v]
print(f"   PWR02:1 is on {on}; R2:1 is on {r2}; same net: {bool(on) and on == r2}")
w = Path(p).with_name("probe_r7.kicad_sch")
shutil.copyfile(p, w)
D.hplace(str(w), RES, "R7", 76.2, 63.5)
(px, py) = D.pos(str(w), "#PWR02", "1")
(rx, ry) = D.pos(str(w), "R7", "1")
D.wire(str(w), px, py, rx, ry)
nets3 = R.J.nets_full(w)
on7 = [n for _c, n, _k, v in nets3 if ("R7", "1") in v]
r2b = [n for _c, n, _k, v in nets3 if ("R2", "1") in v]
print(f"3. #PWR02:1 at {(px, py)} wired to R7:1 at {(rx, ry)}: R7:1 is on {on7}; R2:1 is on {r2b};"
      f" same net: {bool(on7) and on7 == r2b}")
shutil.rmtree(Path(p).parent, ignore_errors=True)
