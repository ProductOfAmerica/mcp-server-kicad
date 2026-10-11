"""Rerun of probe-geometry-misc/a2_facing.py on M2 and K (GM-18, the case citing the whole script;
a2v762L also GM-10). Facing pins 2.54/5.08/7.62 mm apart. The '...d' rows ran a pre-call through
the design under test; here they are two-call sequences on each design's own evolving file.
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

SC = G.begin(__file__, "probe-geometry-misc/a2_facing.py")
VV = "a2_facing:"
WP = "wire_pins_to_net"


def vpair(name, gap):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 101.6, round(105.41 + gap + 3.81, 4))
    return p


def hpair(name, gap):
    p = H.fresh(name)
    H.place(p, "Device:R", "R3", 50.8, 50.8, rot=90)
    H.place(p, "Device:R", "R4", round(54.61 + gap + 3.81, 4), 50.8, rot=90)
    return p


V = [("R1", "2"), ("R2", "1")]
Hh = [("R3", "2"), ("R4", "1")]
for gap in (2.54, 5.08, 7.62):
    g = str(gap).replace(".", "")
    R.run_case("GM-18", f"{VV}a2v{g}", WP, vpair(f"a2v{g}", gap), dict(pins=R.pins(*V), label_text="N"),
               script=SC, note=f"vertical facing, gap {gap}, same net, order R1:2,R2:1")
    R.run_case("GM-18", f"{VV}a2v{g}r", WP, vpair(f"a2v{g}r", gap),
               dict(pins=R.pins(*V[::-1]), label_text="N"), script=SC,
               note=f"vertical facing, gap {gap}, same net, order R2:1,R1:2")
    R.run_sequence("GM-18", f"{VV}a2v{g}d", [
        dict(tool=WP, kw=dict(pins=R.pins(("R1", "2")), label_text="A"), label="pre R1:2 -> A"),
        dict(tool=WP, kw=dict(pins=R.pins(("R2", "1")), label_text="B"), label="R2:1 -> B"),
    ], vpair(f"a2v{g}d", gap), script=SC,
        note=f"vertical facing, gap {gap}, different nets: R1:2 -> A, then R2:1 -> B")
    R.run_case("GM-18", f"{VV}a2h{g}", WP, hpair(f"a2h{g}", gap), dict(pins=R.pins(*Hh), label_text="N"),
               script=SC, note=f"horizontal (rot 90) facing, gap {gap}, same net")

R.run_case("GM-10", VV + "a2v762L", WP, vpair("a2v762L", 7.62),
           dict(pins=R.pins(*V), label_text="N", stub_length=5.08), script=SC, also=["GM-18"],
           note="vertical facing, gap 7.62, same net, stub 5.08 (stubs overlap)")
R.run_sequence("GM-18", VV + "a2v762Ld", [
    dict(tool=WP, kw=dict(pins=R.pins(("R1", "2")), label_text="A", stub_length=5.08), label="pre R1:2 -> A"),
    dict(tool=WP, kw=dict(pins=R.pins(("R2", "1")), label_text="B", stub_length=5.08), label="R2:1 -> B"),
], vpair("a2v762Ld", 7.62), script=SC, note="vertical facing, gap 7.62, stub 5.08, different nets: A then B")
R.run_case("GM-18", VV + "a2v254S", WP, vpair("a2v254S", 2.54),
           dict(pins=R.pins(*V), label_text="N", stub_length=1.27), script=SC,
           note="vertical facing, gap 2.54, same net, stub 1.27 (stubs meet midway)")
R.run_sequence("GM-18", VV + "a2v254Sd", [
    dict(tool=WP, kw=dict(pins=R.pins(("R1", "2")), label_text="A", stub_length=1.27), label="pre R1:2 -> A"),
    dict(tool=WP, kw=dict(pins=R.pins(("R2", "1")), label_text="B", stub_length=1.27), label="R2:1 -> B"),
], vpair("a2v254Sd", 2.54), script=SC, note="vertical facing, gap 2.54, stub 1.27, different nets: A then B")
R.end()
