"""Rerun of probe-geometry-misc/o3_offgrid_part.py on M2 and K (GM-02: o3a2 and the unlisted o3a, o3c,
o3d; GM-11: o3b). Off-grid foreign parts near the stub or the pin end; fixture copied.
Raw controls (o3a2 raw, o3c raw) are not re-run (no design call).
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

SC = G.begin(__file__, "probe-geometry-misc/o3_offgrid_part.py")
W = G.W(("R1", "1"))
V = "o3_offgrid_part:"


def base(name):
    p = H.fresh(name)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    return p


p = base("o3a")
H.place(p, "Device:R", "R2", 101.6, 91.44)
G.set_at(p, "R2", 101.6, 91.4399)
print("o3a R2 pins:", G.kpos(p, "R2", "1"), G.kpos(p, "R2", "2"))
R.run_case("GM-02", V + "o3a", "wire_pins_to_net", p, W, script=SC,
           note="foreign pin end R2:2 1 IU beyond the stub end")

p = base("o3a2")
H.place(p, "Device:R", "R2", 101.6, 91.44)
G.set_at(p, "R2", 101.6, 91.4399)
H.place(p, "Device:R", "R3", 127, 99.06)  # R3:1 at (127, 95.25)
H.wire(p, 101.6, 95.2499, 127, 95.2499)
H.wire(p, 127, 95.2499, 127, 95.25)
R.run_case("GM-02", V + "o3a2", "wire_pins_to_net", p, W, script=SC,
           note="R2:2 1 IU beyond the stub end, with its own wire leaving sideways")

p = base("o3b")
H.place(p, "Device:R", "R2", 105.41, 97.79, rot=90)
G.set_at(p, "R2", 105.46, 97.79)
print("o3b R2 pins:", G.kpos(p, "R2", "1"), G.kpos(p, "R2", "2"))
R.run_case("GM-11", V + "o3b", "wire_pins_to_net", p, W, script=SC,
           note="foreign pin end R2:1 0.05 mm right of the target pin end")

p = base("o3c")
H.place(p, "Device:R", "R2", 105.41, 96.52, rot=90)
G.set_at(p, "R2", 105.41, 97.155)
print("o3c R2 pins:", G.kpos(p, "R2", "1"), G.kpos(p, "R2", "2"))
R.run_case("GM-02", V + "o3c", "wire_pins_to_net", p, W, script=SC,
           note="foreign pin end R2:1 exactly on the stub interior (half-grid point)")

p = base("o3d")
H.place(p, "Device:R", "R2", 106.68, 96.52, rot=90)
G.set_at(p, "R2", 106.045, 96.52)
print("o3d R2 pins:", G.kpos(p, "R2", "1"), G.kpos(p, "R2", "2"))
R.run_case("GM-02", V + "o3d", "wire_pins_to_net", p, W, script=SC,
           note="foreign pin end R2:1 0.635 mm beside the stub interior")
R.end()
