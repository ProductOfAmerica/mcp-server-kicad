"""Check behind the judge_limit note on d04:a: does kicad-cli 9.0.8's kicadxml netlist list nodes
for '#' references (power symbols)? Builds the d04 power part of the fixture (power:GND wired to
R1:1, power:VCC #PWR02 free, power:VCC #PWR03 wired to R2:1) and prints every node per net.
Records nothing (no design call).
Run: uv run python tools/routing_harness/suites/probeC/decisions/dec_check_pwr.py > dec_check_pwr.out
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R  # noqa: E402

p = D.fresh("pwrcheck")
D.hplace(p, "Device:R", "R1", 50.8, 50.8)
D.hplace(p, "power:GND", "#PWR01", 50.8, 38.1, value="GND", rot=180)
D.hplace(p, "power:VCC", "#PWR02", 76.2, 38.1, value="VCC")
D.hplace(p, "Device:R", "R2", 101.6, 50.8)
D.hplace(p, "power:VCC", "#PWR03", 101.6, 38.1, value="VCC")
for ref, r in (("#PWR01", "R1"), ("#PWR03", "R2")):
    D.wire(p, *D.pos(p, ref, "1"), *D.pos(p, r, "1"))
nets = R.J.nets_full(p)
for code, name, cls, nodes in nets:
    print(f"net {code} {name!r} class={cls!r}: {sorted(nodes)}")
hashed = sorted({n for _c, _n, _k, v in nets for n in v if n[0].startswith("#")})
print("nodes with a '#' reference:", hashed or "none")
