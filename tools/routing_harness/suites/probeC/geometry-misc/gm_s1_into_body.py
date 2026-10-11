"""Rerun of probe-geometry-misc/s1_into_body.py on M2 and K (GM-16). Explicit direction into the part's
own body; fixture copied.
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

SC = G.begin(__file__, "probe-geometry-misc/s1_into_body.py")

for tag, L in (("s1a", 2.54), ("s1b", 7.62), ("s1c", 3.81)):
    p = H.fresh(tag)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    R.run_case("GM-16", f"s1_into_body:{tag}", "wire_pins_to_net", p,
               G.W(("R1", "1"), direction="down", stub_length=L), script=SC,
               note=f"R1:1 direction='down' (into the body), stub_length {L}")

p = H.fresh("s1d")
H.place(p, "Amplifier_Operational:LM2904", "U1", 101.6, 101.6)
try:
    print("s1d U1 pins:", {k: G.kpos(p, "U1", k) for k in ("1", "2", "3")})
    ok = True
except Exception as e:  # noqa: BLE001  (the original skipped s1d when the pin peek failed)
    print("s1d skipped:", type(e).__name__, e)
    ok = False
if ok:
    R.run_case("GM-16", "s1_into_body:s1d", "wire_pins_to_net", p,
               G.W(("U1", "2"), direction="right", stub_length=10.16), script=SC,
               note="U1:2 (IN-) direction='right' into the opamp body, stub 10.16")
R.end()
