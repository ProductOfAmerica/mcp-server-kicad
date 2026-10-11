# probeB copy of pr57pt/probe-hierarchy/h16_k10_local_power_decisions.py.
# Fixture code unchanged (files stamped 20250227, a KiCad 10 '(power local)' symbol). kicad-cli
# 9.0.8 cannot load them, so the calls are recorded with status and reason codes only: a refusal is
# checked for byte-identical files, an OK/NOOP is recorded as not judgeable (judge3 has no reading).
"""H16: KiCad 10 local power symbols. No oracle: kicad-cli 9.0.8 refuses (power local).
Root: sheet C (pin PWR); R1:1 wired to it (h01 geometry); R2:1 on local label N1.
Child: R31:1 on hier label PWR + local power +3V3L; R32 free.
Calls: a) root  wire_pins_to_net([R1:1], 'N1')     (H13 with a local power behind the port)
       b) child wire_pins_to_net([R32:1], '+3V3L')  (same-sheet local power join)
       c) child wire_pins_to_net([R31:1], 'X')      (pin on a local power net)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h16_k10_local_power_decisions", __file__)
import hlib  # noqa: E402
from hlib import HERE, Log, Proj, netlist, run  # noqa: E402

log = Log(HERE / "h16_k10_local_power_decisions.out")

stock = hlib.lib_symbol("power", "+3V3")
local = stock.replace('(symbol "power:+3V3"', '(symbol "power:+3V3L"', 1).replace("(power)", "(power local)", 1)
assert "(power local)" in local, "stock +3V3 has no bare (power) token"
hlib._LIB[("power", "+3V3L")] = local

pj = Proj("h16_k10")
r = pj.root
ch = pj.child("c.kicad_sch")
_, w = r.sheet("C", ch, 152.4, 25.4, pins=[("PWR", "input", "left", 2)])
(p1, _) = r.R("R1", 127, 40.64)
r.wire(*p1, 152.4, p1[1])
r.wire(152.4, p1[1], *w["PWR"])
(q1, _) = r.R("R2", 76.2, 50.8)
r.wire(*q1, q1[0], round(q1[1] - 5.08, 4))
r.label("N1", q1[0], round(q1[1] - 5.08, 4))
(c1, _) = ch.R(["R31"], 50.8, 50.8)
ch.wire(*c1, c1[0], 41.91)
ch.wire(c1[0], 41.91, c1[0], 38.1)
ch.label("PWR", c1[0], 41.91, kind="hierarchical_label")
ch.power("+3V3L", c1[0], 38.1, value="+3V3L")
ch.R(["R32"], 101.6, 50.8)
pj.write()
for f in (pj.root.path, ch.path):
    t = open(f, encoding="utf-8").read().replace("(version 20250114)", "(version 20250227)", 1)
    t = t.replace('(generator_version "9.0")', '(generator_version "10.0")', 1)
    open(f, "w", encoding="utf-8", newline="\n").write(t)
try:
    netlist(pj.root.path)
    log("kicad-cli 9.0.8 loaded the KiCad 10 file (unexpected)")
except RuntimeError as e:
    log(f"kicad-cli 9.0.8 cannot load it (expected): {str(e)[:200]}")

calls = [("a_root_R1_N1", pj.root.fname, "R1", "N1"), ("b_child_R32_3V3L", "c.kicad_sch", "R32", "+3V3L"),
         ("c_child_R31_X", "c.kicad_sch", "R31", "X")]
for tag, f, ref, N in calls:
    run(log, tag, pj.dir, pj.root.fname, f, "wire_pins_to_net",
        dict(pins=[{"reference": ref, "pin": "1"}], label_text=N),
        ideal="KiCad 10 project: decisions only (kicad-cli 9.0.8 cannot load it)")
log.save()
