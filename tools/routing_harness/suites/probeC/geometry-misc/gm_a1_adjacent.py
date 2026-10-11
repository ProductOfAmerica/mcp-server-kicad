"""Rerun of probe-geometry-misc/a1_adjacent.py on M2 and K (GM-18, the case citing the whole script;
a1d also GM-10). Adjacent pins on a 2.54 mm pitch. a1e and a1f ran a pre-call through the design
under test; here they are two-call sequences, each design on its own evolving file.
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

SC = G.begin(__file__, "probe-geometry-misc/a1_adjacent.py")
J = [("J1", k) for k in "1234"]
V = "a1_adjacent:"
WP = "wire_pins_to_net"


def fixture(name):
    p = H.fresh(name)
    H.place(p, "Connector_Generic:Conn_01x04", "J1", 101.6, 101.6)
    return p


R.run_case("GM-18", V + "a1a", WP, fixture("a1a"), dict(pins=R.pins(*J), label_text="N"), script=SC,
           note="J1 pins 1-4 -> N, auto")
R.run_case("GM-18", V + "a1b", WP, fixture("a1b"), dict(pins=R.pins(*J), label_text="N", direction="up"),
           script=SC, note="J1 pins 1,2,3,4 -> N, direction up")
R.run_case("GM-18", V + "a1c", WP, fixture("a1c"),
           dict(pins=R.pins(*J[::-1]), label_text="N", direction="up"), script=SC,
           note="J1 pins 4,3,2,1 -> N, direction up")
R.run_case("GM-10", V + "a1d", WP, fixture("a1d"),
           dict(pins=R.pins(*J[::-1]), label_text="N", direction="down", stub_length=5.08), script=SC,
           also=["GM-18"], note="J1 pins 4,3,2,1 -> N, direction down, stub 5.08")
R.run_case("GM-18", V + "a1d2", WP, fixture("a1d2"),
           dict(pins=R.pins(*J), label_text="N", direction="down", stub_length=5.08), script=SC,
           note="J1 pins 1,2,3,4 -> N, direction down, stub 5.08")
R.run_sequence("GM-18", V + "a1e", [
    dict(tool=WP, kw=dict(pins=R.pins(("J1", "1")), label_text="A", direction="up"), label="pre J1:1 -> A up"),
    dict(tool=WP, kw=dict(pins=R.pins(("J1", "2")), label_text="B", direction="up"), label="J1:2 -> B up"),
], fixture("a1e"), script=SC, note="J1:2 -> B direction up, after J1:1 -> A direction up")
R.run_sequence("GM-18", V + "a1f", [
    dict(tool=WP, kw=dict(pins=R.pins(("J1", "1")), label_text="A"), label="pre J1:1 -> A auto"),
    dict(tool=WP, kw=dict(pins=R.pins(("J1", "2"), ("J1", "3")), label_text="B"), label="J1:2,J1:3 -> B"),
], fixture("a1f"), script=SC, note="J1:2,J1:3 -> B auto, after J1:1 -> A auto")
R.end()
