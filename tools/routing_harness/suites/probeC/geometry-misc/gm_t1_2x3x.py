"""Rerun of probe-geometry-misc/t1_2x3x.py on M2 and K (GM-02: t1d, also GM-10 and GM-17; GM-17:
t1a, t1b, t1c, t1e, t1f, the case citing the whole script). P's 2x/3x stub scenarios; fixture copied.
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

SC = G.begin(__file__, "probe-geometry-misc/t1_2x3x.py")
W = G.W(("R1", "1"))
V = "t1_2x3x:"


def crossing(p, ref, y, x_pin=114.3):
    """Foreign wire from x=96.52 to a resistor pin at (x_pin, y)."""
    H.place(p, "Device:R", ref, x_pin, round(y + 3.81, 4))
    H.wire(p, 96.52, y, x_pin, y)


p = H.fresh("t1a")
H.place(p, "Device:R", "R1", 101.6, 101.6)
crossing(p, "R2", 95.25)
R.run_case("GM-17", V + "t1a", "wire_pins_to_net", p, W, script=SC, note="foreign wire through E1")

p = H.fresh("t1b")
H.place(p, "Device:R", "R1", 101.6, 101.6)
crossing(p, "R2", 95.25)
crossing(p, "R4", 92.71, x_pin=119.38)
R.run_case("GM-17", V + "t1b", "wire_pins_to_net", p, W, script=SC,
           note="foreign wires through E1 and E2")

p = H.fresh("t1c")
H.place(p, "Device:R", "R1", 101.6, 101.6)
crossing(p, "R2", 95.25)
crossing(p, "R4", 92.71, x_pin=119.38)
crossing(p, "R6", 90.17, x_pin=124.46)
R.run_case("GM-17", V + "t1c", "wire_pins_to_net", p, W, script=SC,
           note="foreign wires through E1, E2 and E3")

p = H.fresh("t1d")
H.place(p, "Device:R", "R1", 101.6, 101.6)
crossing(p, "R2", 95.25)
H.wire(p, 96.52, 92.7099, 106.68, 92.7099)  # 1 IU beyond E2
H.label(p, "FOO", 106.68, 92.7099)
H.place(p, "Device:R", "R3", 132.08, 101.6)
H.wire(p, 132.08, 97.79, 132.08, 95.25)
H.label(p, "FOO", 132.08, 95.25)
R.run_case("GM-02", V + "t1d", "wire_pins_to_net", p, W, script=SC, also=["GM-10", "GM-17"],
           note="foreign wire through E1, FOO wire 1 IU beyond E2")

p = H.fresh("t1e")
H.place(p, "Device:R", "R1", 101.6, 101.6)
H.bus(p, 96.52, 95.25, 106.68, 95.25)
H.label(p, "D[0..3]", 96.52, 95.25)
R.run_case("GM-17", V + "t1e", "wire_pins_to_net", p, W, script=SC, note="foreign bus through E1")

p = H.fresh("t1f")
H.place(p, "Device:R", "R1", 101.6, 101.6)
crossing(p, "R2", 95.25)
H.place(p, "Device:R", "R5", 101.6, 92.71, rot=90)  # body centred on E2, pins at x 97.79/105.41
R.run_case("GM-17", V + "t1f", "wire_pins_to_net", p, W, script=SC,
           note="foreign wire through E1; R5's body centred on E2")
R.end()
