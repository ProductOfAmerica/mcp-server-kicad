"""Rerun of verify-probe-geometry-misc/v18_stray_junction_truncation.py on M2 and K (GM-10: j).
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

SC = G.begin(__file__, "verify-probe-geometry-misc/v18_stray_junction_truncation.py")
p = H.fresh("j")
H.place(p, "Device:R", "R1", 76.2, 76.2)
H.place(p, "Device:R", "R8", 63.5, 78.74)
G.set_at(p, "R8", 63.5, 78.7402)
H.place(p, "Device:R", "R9", 88.9, 78.74)
G.set_at(p, "R9", 88.9, 78.7402)
print("R8 pins", G.kpin(p, "R8", "1"), G.kpin(p, "R8", "2"), "R9 pins", G.kpin(p, "R9", "1"),
      G.kpin(p, "R9", "2"))
H.wire(p, 63.5, 82.5502, 88.9, 82.5502)
R.run_case("GM-10", "v18_stray_junction_truncation:j", "wire_pins_to_net", p, G.W(("R1", "2")), script=SC,
           note="foreign two-pin wire 2 IU beyond E1 (P writes a stray junction at E1)")
R.end()
