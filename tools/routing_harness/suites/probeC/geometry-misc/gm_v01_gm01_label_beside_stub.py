"""Rerun of verify-probe-geometry-misc/v01_gm01_label_beside_stub.py on M2 and K (GM-01: a, a2, b, c,
d). The 'chk' fixture only printed pin positions (no call). Raw controls ak, a2k, dk are not re-run.
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v01_gm01_label_beside_stub.py")
W = G.W(("R1", "2"))
V = "v01_gm01_label_beside_stub:"


def fixture(name, dx, kind="label", foreign_wire=True, r4=False):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    H.place(p, "Device:R", "R3", 101.6, 76.2)
    H.wire(p, 101.6, 72.39, 101.6, 69.85)
    H.label(p, "FOO", 101.6, 69.85, kind=kind)
    q = round(76.2 - dx, 4)
    if foreign_wire:
        H.place(p, "Device:R", "R2", 63.5, 85.09)
        H.wire(p, 63.5, 81.28, q, 81.28)
    H.label(p, "FOO", q, 81.28, kind=kind)
    if r4:
        H.place(p, "Device:R", "R4", 76.2, 93.98)
    return p


p = fixture("chk", 0.0001)
print("R1 pins (KiCad order):", G.kpin(p, "R1", "1"), G.kpin(p, "R1", "2"), "| R2:1", G.kpin(p, "R2", "1"))

R.run_case("GM-01", V + "a", "wire_pins_to_net", fixture("a", 0.0001), W, script=SC,
           note="FOO on a foreign wire END, 1 IU left of the stub interior")
R.run_case("GM-01", V + "a2", "wire_pins_to_net", fixture("a2", 0.0001, foreign_wire=False), W, script=SC,
           note="lone FOO label (no wire) 1 IU left of the stub interior")
R.run_case("GM-01", V + "b", "wire_pins_to_net", fixture("b", 0.0002), W, script=SC,
           note="FOO on a foreign wire end, 2 IU left (boundary)")
R.run_case("GM-01", V + "c", "wire_pins_to_net", fixture("c", 0.0001, kind="global_label"), W, script=SC,
           note="GLOBAL FOO, 1 IU left")
R.run_case("GM-01", V + "d", "connect_pins", fixture("d", 0.0001, r4=True), G.C("R1", "2", "R4", "1"),
           script=SC, note="connect_pins R1:2 -> R4:1 straight, FOO 1 IU left of the route")
R.end()
