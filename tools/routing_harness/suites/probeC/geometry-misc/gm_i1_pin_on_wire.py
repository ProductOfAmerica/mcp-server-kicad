"""Rerun of probe-geometry-misc/i1_pin_on_wire.py on M2 and K (GM-19: i1a, i1f; GM-10: i1b (also
GM-12), i1d (also GM-11); GM-09: i1c, i1c2; GM-12: i1e). The target pin end on or next to a foreign
wire; fixture copied. Raw controls (i1c raw x2, i1e raw) are not re-run (no design call).
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

SC = G.begin(__file__, "probe-geometry-misc/i1_pin_on_wire.py")
W = G.W(("R1", "1"))
V = "i1_pin_on_wire:"


def base(name):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    return p


def foo_r2(p):
    H.place(p, "Device:R", "R2", 127, 101.6)
    H.wire(p, 127, 97.79, 127, 95.25)
    H.label(p, "FOO", 127, 95.25)


p = base("i1a")
H.place(p, "Device:R", "R3", 88.9, 101.6)
H.place(p, "Device:R", "R4", 114.3, 101.6)
H.wire(p, 88.9, 97.79, 114.3, 97.79)
R.run_case("GM-19", V + "i1a", "wire_pins_to_net", p, W, script=SC,
           note="foreign wire R3:1-R4:1 through the target pin end")

p = base("i1b")
H.wire(p, 95.25, 97.79, 107.95, 97.79)
R.run_case("GM-10", V + "i1b", "wire_pins_to_net", p, W, script=SC, also=["GM-12"],
           note="inert dangling wire through the target pin end")

for tag, a, b in (("i1c", 95.25, 107.95), ("i1c2", 107.95, 95.25)):
    p = base(tag)
    H.wire(p, a, 97.79, b, 97.79)
    H.label(p, "FOO", 97.79, 97.7899)
    foo_r2(p)
    R.run_case("GM-09", V + tag, "wire_pins_to_net", p, W, script=SC,
               note=f"'inert' wire through P carrying FOO via a label 1 IU above it (drawn {a}->{b})")

p = base("i1d")
H.place(p, "Device:R", "R3", 88.9, 101.6)
H.wire(p, 88.9, 97.79, 88.9, 97.7899)
H.wire(p, 88.9, 97.7899, 114.3, 97.7899)
H.label(p, "FOO", 114.3, 97.7899)
foo_r2(p)
R.run_case("GM-10", V + "i1d", "wire_pins_to_net", p, W, script=SC, also=["GM-11"],
           note="foreign FOO wire passing 1 IU above the target pin end")

p = base("i1e")
H.bus(p, 95.25, 97.79, 107.95, 97.79)
H.label(p, "D[0..3]", 95.25, 97.79)
R.run_case("GM-12", V + "i1e", "wire_pins_to_net", p, W, script=SC,
           note="foreign bus through the target pin end")

p = base("i1f")
H.place(p, "Device:R", "R3", 88.9, 101.6)
H.place(p, "Device:R", "R4", 114.3, 101.6)
H.wire(p, 88.9, 97.79, 114.3, 97.79)
H.place(p, "Device:R", "R5", 101.6, 88.9)  # R5:2 at (101.6, 92.71)
R.run_case("GM-19", V + "i1f", "connect_pins", p, G.C("R1", "1", "R5", "2"), script=SC,
           note="connect_pins R1:1 -> R5:2 straight, R1:1 on the R3-R4 wire")
R.end()
