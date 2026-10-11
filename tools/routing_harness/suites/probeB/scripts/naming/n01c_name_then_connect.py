# probeB copy of pr57pt/probe-naming/n01c_name_then_connect.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N01c: the reverse order of N01. wire_pins_to_net([R1:1], 'VCC') first, then
connect_pins(R1:1, R2:1). P adds no auto label to a named net (builder I26a), so this order should
work in P where N01's order is refused. Same fixture as N01 v1.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n01c_name_then_connect", __file__)
from nlib import CP, H, Fixture, W, dump, show_nets, step  # noqa: E402

fx = Fixture("n01c")
p = fx.path("n01c.kicad_sch")
H.project.create_schematic(p)
H.place(p, "Device:R", "R1", 101.6, 101.6)
H.place(p, "Device:R", "R2", 127, 101.6)
for d in rlib.DESIGNS:
    c = fx.clone(d)
    s = c.path("n01c.kicad_sch")
    step(d, "N01c step 1: wire_pins_to_net [R1:1] -> VCC", s, [s], "wire_pins_to_net",
         W([("R1", "1")], "VCC", s), targets=[("R1", "1")], N="VCC", vid="n01c", stepno=1, nsteps=2)
    step(d, "N01c step 2: connect_pins R1:1 -> R2:1", s, [s], "connect_pins",
         CP("R1", "1", "R2", "1", s), pair=(("R1", "1"), ("R2", "1")), vid="n01c", stepno=2, nsteps=2)
    show_nets(s, f"[{d}] after", focus=[("R1", "1"), ("R2", "1")])

dump(__file__)
