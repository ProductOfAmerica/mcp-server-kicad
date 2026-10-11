"""Rerun of probe-geometry-misc/a3_perpendicular.py on M2 and K (GM-10: a3a, a3b, also GM-18;
GM-17: a3c, also GM-18; GM-18: a3d). Perpendicular stubs of nearby pins. a3c and a3d ran a pre-call
through the design under test; here they are two-call sequences.
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

SC = G.begin(__file__, "probe-geometry-misc/a3_perpendicular.py")
V = "a3_perpendicular:"
WP = "wire_pins_to_net"


def fixture(name):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 106.68, 95.25, rot=90)
    return p


q = fixture("a3peek")
print("R1:1", G.kpos(q, "R1", "1"), "R2:1", G.kpos(q, "R2", "1"), "R2:2", G.kpos(q, "R2", "2"))
X = [("R2", "1"), ("R1", "1")]
R.run_case("GM-10", V + "a3a", WP, fixture("a3a"), dict(pins=R.pins(*X), label_text="N"), script=SC,
           also=["GM-18"], note="order R2:1, R1:1 -> N (R1's stub end on R2's new stub interior)")
R.run_case("GM-10", V + "a3b", WP, fixture("a3b"), dict(pins=R.pins(*X[::-1]), label_text="N"), script=SC,
           also=["GM-18"], note="order R1:1, R2:1 -> N (R2's stub passes over R1's stub end)")
R.run_sequence("GM-17", V + "a3c", [
    dict(tool=WP, kw=dict(pins=R.pins(("R2", "1")), label_text="A"), label="pre R2:1 -> A"),
    dict(tool=WP, kw=dict(pins=R.pins(("R1", "1")), label_text="B"), label="R1:1 -> B"),
], fixture("a3c"), script=SC, also=["GM-18"], note="R2:1 -> A, then R1:1 -> B")
R.run_sequence("GM-18", V + "a3d", [
    dict(tool=WP, kw=dict(pins=R.pins(("R1", "1")), label_text="A"), label="pre R1:1 -> A"),
    dict(tool=WP, kw=dict(pins=R.pins(("R2", "1")), label_text="B"), label="R2:1 -> B"),
], fixture("a3d"), script=SC, note="R1:1 -> A, then R2:1 -> B")
R.end()
