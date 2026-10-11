"""Rerun of verify-probe-geometry-misc/v13_gm13_m_capture_models.py on M2 and K (GM-13: g13, x2 asking
for N, x2 asking for FOO). The original's 'M-literal emulation' (mine_impl with _copy_state
monkeypatched) is the same call on the same fixture as g13, so for M2/K it is that variant; it is not
run twice. x2b made no call in the original.
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v13_gm13_m_capture_models.py")
V = "v13_gm13_m_capture_models:"
WP = "wire_pins_to_net"


def base(name):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    H.place(p, "Device:R", "R3", 101.6, 76.2)
    H.wire(p, 101.6, 72.39, 101.6, 69.85)
    H.label(p, "FOO", 101.6, 69.85)
    H.place(p, "Device:R", "R6", 114.3, 76.2)
    H.wire(p, 114.3, 72.39, 114.3, 69.85)
    H.label(p, "N", 114.3, 69.85)
    return p


p = base("g13")
H.wire(p, 76.2, 80.01, 81.28, 85.09)
H.label(p, "N", 78.7402, 82.55)
R.run_case("GM-13", V + "g13", WP, p, G.W(("R1", "2")), script=SC,
           note="R1:2 already on N via a label 2 IU off its own 45-degree wire")

p = base("x2")
H.place(p, "Device:R", "R5", 88.9, 76.2)
H.wire(p, 76.2, 80.01, 88.9, 80.01)
H.label(p, "FOO", 82.55, 80.0098)
for N in ("N", "FOO"):
    R.run_case("GM-13", V + f"x2_{N}", WP, p, G.W(("R1", "2"), N=N), script=SC,
               note=f"FOO 2 IU above the own horizontal wire (not attached); ask for '{N}'")
R.end()
