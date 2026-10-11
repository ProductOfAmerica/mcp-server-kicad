"""Rerun of probe-geometry-misc/s3_nongrid_stub.py on M2 and K (GM-03: s3a-s3e; s3b, s3c also GM-10
(P's stray junction); s3e also GM-17). The case calls s3a and s3e controls: they are design calls,
so they run. The raw control 's3d raw' is not re-run (no design call). Fixture copied.
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

SC = G.begin(__file__, "probe-geometry-misc/s3_nongrid_stub.py")
V = "s3_nongrid_stub:"


def W(L):
    return G.W(("R1", "1"), stub_length=L)


def foo_r3(p):
    H.place(p, "Device:R", "R3", 127, 101.6)
    H.wire(p, 127, 97.79, 127, 95.25)
    H.label(p, "FOO", 127, 95.25)


p = H.fresh("s3a")
H.place(p, "Device:R", "R1", 101.6, 101.6)
R.run_case("GM-03", V + "s3a", "wire_pins_to_net", p, W(2.0), script=SC,
           note="stub_length 2.0, nothing around (case control)")

for tag, L in (("s3b", 2.5399), ("s3c", 2.5401)):
    p = H.fresh(tag)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.wire(p, 96.52, 95.25, 106.68, 95.25)
    H.label(p, "FOO", 106.68, 95.25)
    foo_r3(p)
    R.run_case("GM-03", V + tag, "wire_pins_to_net", p, W(L), script=SC, also=["GM-10"],
               note=f"stub_length {L}, foreign FOO wire crossing the axis at y=95.25")

p = H.fresh("s3d")
H.place(p, "Device:R", "R1", 101.6, 101.6)
H.wire(p, 101.6, 95.25, 114.3, 95.25)
H.label(p, "FOO", 101.6, 95.25)
foo_r3(p)
R.run_case("GM-03", V + "s3d", "wire_pins_to_net", p, W(2.5399), script=SC,
           note="stub_length 2.5399, foreign wire end + FOO label at (101.6, 95.25)")

p = H.fresh("s3e")
H.place(p, "Device:R", "R1", 101.6, 101.6)
H.place(p, "Device:R", "R2", 114.3, 99.06)
G.set_at(p, "R2", 114.3, 99.695)  # R2:1 at (114.3, 95.885): a half-grid y
H.wire(p, 96.52, 95.885, 114.3, 95.885)        # crosses the axis exactly at the 1x end
R.run_case("GM-03", V + "s3e", "wire_pins_to_net", p, W(1.905), script=SC, also=["GM-17"],
           note="stub_length 1.905: 1x end on a foreign wire at a half-grid point (case control)")
R.end()
