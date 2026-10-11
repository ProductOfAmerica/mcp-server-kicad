# probeB copy of pr57pt/probe-naming/n07_power_value_case.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N07: power symbols are named by the placed Value (digest N11), case-sensitively (N01, v22a).

R1:1 (101.6, 97.79), auto stub up to (101.6, 95.25).
  a1 R1:1 wired up to power:GND (Value 'GND'); R7 on power:GND with Value 'gnd' (a second rail).
     wire [R1:1] -> 'GND'   (already on it: no-op)
  a2 same fixture, wire [R1:1] -> 'gnd'   (case variant = the other rail: must refuse)
  b  R1:1 on power:GND placed with Value 'gnd'; R7 on power:GND Value 'GND'.  wire -> 'GND'
  c1 R1:1 on power:VCC placed with Value '+5V'; R7 on power:VCC Value 'VCC'. wire -> 'VCC'
  c2 same fixture, wire -> '+5V'
  d  a free power:VCC (Value 'VCC') sits exactly at R1:1's 1x stub end; wire [R1:1] -> 'VCC'
  e  a free power:VCC with Value 'Vcc' sits at R1:1's 1x stub end; R7 on power VCC Value 'VCC';
     wire [R1:1] -> 'VCC'
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n07_power_value_case", __file__)
from nlib import H, Fixture, W, dump, pos, show_nets, step  # noqa: E402


def build(fx, tag):
    p = fx.path("n07.kicad_sch")
    H.project.create_schematic(p)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R7", 152.4, 127)
    x1, y1 = pos(p, "R1", "1")
    x7, y7 = pos(p, "R7", "1")
    H.wire(p, x7, y7, x7, 120.65)
    if tag in ("a1", "a2", "b"):
        own, other = ("GND", "gnd") if tag != "b" else ("gnd", "GND")
        H.wire(p, x1, y1, 93.98, y1)  # left: P's up-stub must not overlap it
        H.place(p, "power:GND", "#PWR01", 93.98, y1, value=own)
        H.place(p, "power:GND", "#PWR02", x7, 120.65, value=other)
    elif tag in ("c1", "c2"):
        H.wire(p, x1, y1, 93.98, y1)
        H.place(p, "power:VCC", "#PWR01", 93.98, y1, value="+5V")
        H.place(p, "power:VCC", "#PWR02", x7, 120.65, value="VCC")
    elif tag == "d":
        H.place(p, "power:VCC", "#PWR01", x1, 95.25, value="VCC")
        H.place(p, "power:VCC", "#PWR02", x7, 120.65, value="VCC")
    elif tag == "e":
        H.place(p, "power:VCC", "#PWR01", x1, 95.25, value="Vcc")
        H.place(p, "power:VCC", "#PWR02", x7, 120.65, value="VCC")
    return p


cases = {"a1": "GND", "a2": "gnd", "b": "GND", "c1": "VCC", "c2": "+5V", "d": "VCC", "e": "VCC"}
focus = [("R1", "1"), ("R7", "1")]
for tag, N in cases.items():
    fx = Fixture(f"n07_{tag}")
    p = build(fx, tag)
    show_nets(p, f"{tag} fixture", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("n07.kicad_sch")
        step(d, f"N07{tag}: wire_pins_to_net [R1:1] -> {N}", s, [s], "wire_pins_to_net",
             W([("R1", "1")], N, s), targets=[("R1", "1")], N=N, vid=tag)
        show_nets(s, f"[{d}] {tag} after", focus=focus)

dump(__file__)
