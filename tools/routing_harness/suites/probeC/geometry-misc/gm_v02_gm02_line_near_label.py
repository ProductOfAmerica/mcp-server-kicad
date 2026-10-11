"""Rerun of verify-probe-geometry-misc/v02_gm02_line_near_label.py on M2 and K (GM-02: a, b, c, d, e2,
e3, f). Raw controls (a raw, d raw) are not re-run (no design call).
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v02_gm02_line_near_label.py")
W = G.W(("R1", "2"))
V = "v02_gm02_line_near_label:"
WP = "wire_pins_to_net"


def base(name):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 76.2, 76.2)
    H.place(p, "Device:R", "R3", 101.6, 76.2)
    H.wire(p, 101.6, 72.39, 101.6, 69.85)
    H.label(p, "FOO", 101.6, 69.85)
    return p


p = base("a")
H.wire(p, 71.12, 82.5501, 81.28, 82.5501)
H.label(p, "FOO", 71.12, 82.5501)
R.run_case("GM-02", V + "a", WP, p, W, script=SC, note="FOO wire 1 IU beyond E1 (no contact with the stub)")

p = base("b")
H.wire(p, 71.12, 82.5499, 81.28, 82.5499)
H.label(p, "FOO", 71.12, 82.5499)
R.run_case("GM-02", V + "b", WP, p, W, script=SC, note="FOO wire 1 IU before E1 (proper crossing of the stub)")

p = base("c")
H.wire(p, 76.2001, 81.28, 76.2001, 86.36)
H.label(p, "FOO", 76.2001, 86.36)
R.run_case("GM-02", V + "c", WP, p, W, script=SC, note="FOO wire parallel, 1 IU right of the stub, spanning E1")

p = base("d")
H.place(p, "Device:R", "R5", 88.9, 86.36)  # R5:1 at (88.9, 82.55)
H.wire(p, 76.2001, 82.55, 88.9, 82.55)
print("R5:1", G.kpin(p, "R5", "1"))
R.run_case("GM-02", V + "d", WP, p, W, script=SC, note="unnamed foreign wire END 1 IU right of E1")

for tag, sh in (("e2", 0.0002), ("e3", 0.0003)):
    p = base(tag)
    H.wire(p, round(73.66 + sh, 4), 85.09, round(78.74 + sh, 4), 80.01)
    H.label(p, "FOO", round(73.66 + sh, 4), 85.09)
    R.run_case("GM-02", V + tag, WP, p, W, script=SC,
               note=f"45-degree FOO wire shifted {round(sh * 1e4)} IU: E1 at d^2 = {(sh * 1e4) ** 2 / 2:g}")

p = base("f")
H.place(p, "Device:R", "R6", 88.9, 86.36)  # R6:1 at (88.9, 82.55)
H.wire(p, 68.58, 82.55, 88.9, 82.55)       # unnamed foreign net through E1
H.wire(p, 71.12, 85.0901, 81.28, 85.0901)  # FOO wire 1 IU beyond E2
H.label(p, "FOO", 71.12, 85.0901)
R.run_case("GM-02", V + "f", WP, p, W, script=SC, note="foreign wire through E1, FOO wire 1 IU beyond E2")
R.end()
