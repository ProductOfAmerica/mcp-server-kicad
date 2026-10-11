"""Rerun of probe-geometry-misc/m1_near_miss_names.py on M2 and K (GM-06: m1a, m1b (the case's
control, a design call), m1c, m1e, m1f; GM-13: m1d). Labels KiCad attaches to the target's own
wire by its 1 IU capture; fixture copied.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402  (first: redirects TMP/TEMP, no .pyc writes)

from rlib import H  # noqa: E402
import gmlib as G  # noqa: E402

SC = G.begin(__file__, "probe-geometry-misc/m1_near_miss_names.py")
W = G.W(("R1", "1"))
V = "m1_near_miss_names:"


def base(name, r3y=101.6, n_holder=True):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R3", 127, r3y)
    H.wire(p, 127, round(r3y - 3.81, 4), 127, round(r3y - 6.35, 4))
    H.label(p, "FOO", 127, round(r3y - 6.35, 4))
    if n_holder:  # a second holder of N, so a FOO/N join shows as a merge in the netlist
        H.place(p, "Device:R", "R6", 139.7, 101.6)
        H.wire(p, 139.7, 97.79, 139.7, 95.25)
        H.label(p, "N", 139.7, 95.25)
    return p


for tag, dx in (("m1a", 0.0002), ("m1b", 0.0003)):
    p = base(tag)
    H.wire(p, 101.6, 97.79, 96.52, 92.71)
    H.label(p, "FOO", round(99.06 + dx, 4), 95.25)
    R.run_case("GM-06", V + tag, "wire_pins_to_net", p, W, script=SC,
               note=f"own 45-degree wire, FOO {round(dx * 1e4)} IU off it; wire R1:1 to N")

p = base("m1c")
H.place(p, "Device:R", "R5", 114.3, 101.6)
H.wire(p, 101.6, 97.79, 114.3, 97.79)
H.label(p, "FOO", 107.95, 97.7899)
R.run_case("GM-06", V + "m1c", "wire_pins_to_net", p, W, script=SC,
           note="own horizontal wire R1:1-R5:1, FOO 1 IU above it; wire R1:1 to N")

p = base("m1d")
H.wire(p, 101.6, 97.79, 96.52, 92.71)
H.label(p, "N", 99.0602, 95.25)
R.run_case("GM-13", V + "m1d", "wire_pins_to_net", p, W, script=SC,
           note="own 45-degree wire, the requested name N 2 IU off it (already on N in KiCad)")

p = base("m1e", r3y=127, n_holder=False)
H.wire(p, 101.6, 97.79, 96.52, 92.71)
H.label(p, "FOO", 99.0602, 95.25)
H.place(p, "Device:R", "R4", 152.4, 101.6)
H.wire(p, 152.4, 97.79, 152.4, 95.25)
H.label(p, "BAR", 152.4, 95.25)
R.run_case("GM-06", V + "m1e", "connect_pins", p, G.C("R1", "1", "R4", "1"), script=SC,
           note="connect_pins R1:1 (on FOO via a captured label) -> R4:1 (on BAR)")

# f: an any-angle (slope 2) own wire, label FOO at exact squared distance 3.2 IU^2 from it
p = base("m1f")
H.wire(p, 101.6, 97.79, 102.87, 95.25)
H.label(p, "FOO", 102.2352, 96.52)
R.run_case("GM-06", V + "m1f", "wire_pins_to_net", p, W, script=SC,
           note="own slope-2 wire, FOO at squared distance 3.2 IU^2 (attached in KiCad); wire R1:1 to N")
R.end()
