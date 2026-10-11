"""Rerun of verify-probe-geometry-misc/v10_gm10_junctions_overlaps.py on M2 and K (GM-10: a, a2, b, c).
The 'peek' fixture only printed pin positions (no call).
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v10_gm10_junctions_overlaps.py")
J = [("J1", k) for k in "1234"]
V = "v10_gm10_junctions_overlaps:"
WP = "wire_pins_to_net"

p = H.fresh("peek")
H.place(p, "Connector_Generic:Conn_01x04", "J1", 127, 63.5)
print("J1 pins:", {k: G.kpin(p, "J1", k) for k in "1234"})

for tag, order in (("a", J[::-1]), ("a2", J)):
    p = H.fresh(tag)
    H.place(p, "Connector_Generic:Conn_01x04", "J1", 127, 63.5)
    R.run_case("GM-10", V + tag, WP, p,
               dict(pins=R.pins(*order), label_text="N", direction="down", stub_length=5.08), script=SC,
               note=f"J1 pins {','.join(k for _, k in order)} -> N, down, stub 5.08")

p = H.fresh("b")
H.place(p, "Device:R", "R1", 76.2, 76.2)
H.place(p, "Device:R", "R2", 76.2, 91.44)  # R2:1 at (76.2, 87.63)
print("R2:1", G.kpin(p, "R2", "1"))
R.run_case("GM-10", V + "b", WP, p,
           dict(pins=R.pins(("R1", "2"), ("R2", "1")), label_text="N", stub_length=5.08), script=SC,
           note="facing pins 7.62 apart, same net, stub 5.08")

p = H.fresh("c")
H.place(p, "Device:R", "R1", 76.2, 76.2)
H.place(p, "Device:R", "R3", 101.6, 76.2)
H.wire(p, 101.6, 72.39, 101.6, 69.85)
H.label(p, "FOO", 101.6, 69.85)
H.wire(p, 71.12, 82.5502, 81.28, 82.5502)
H.label(p, "FOO", 71.12, 82.5502)
R.run_case("GM-10", V + "c", WP, p, G.W(("R1", "2")), script=SC,
           note="FOO wire 2 IU beyond E1 (stray junction case)")
R.end()
