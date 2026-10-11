# probeB copy of pr57pt/probe-naming/n06_hidden_power_628128.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N06: hidden power_in pins (item 1) on the demo symbol interf_u:628128 (hidden power_in VCC=32 at
lib (0, 27.94), GND=16 at lib (0, -27.94), unit 0, extracted to verify-sem-naming/vhp.kicad_sym).

U1 at (101.6, 101.6): hidden VCC end (101.6, 73.66), hidden GND end (101.6, 129.54).
R1 at (101.6, 63.5): R1:2 (101.6, 67.31) is wired down to U1's hidden VCC end, so R1:2 is on VCC.
R7:1 carries local 'VSS', R8:1 carries local 'SIG'; R2 free.

  a  [U1:16] -> GND        target is the hidden pin, already on its own name
  b  [U1:16] -> VSS        hidden pin to another name (R7 on VSS)
  c  [U1:'GND'] -> GND     hidden pin addressed by NAME
  d1 [R1:2] -> SIG         hidden VCC pin on the target's component (R8 on SIG)
  d2 [R1:2] -> VCC         ... to its own name
  e  [R2:1] -> VCC         label_text is the hidden pin's name elsewhere on the sheet
  f  [R2:1] -> Vcc         case variant of that name
  g  connect_pins R1:2 (VCC via hidden pin) -> R8:1 (SIG)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n06_hidden_power_628128", __file__)
from nlib import BS, CP, H, Fixture, W, dump, pos, show_nets, step  # noqa: E402

VHP = str(hpaths.FIXTURES / "probeB" / "vhp.kicad_sym")
if not Path(VHP).is_file():
    raise SystemExit(f"missing prepared input {VHP}: run tools/routing_harness/prepare.py first")


def build(fx):
    p = fx.path("n06.kicad_sch")
    H.project.create_schematic(p)
    BS.place_component(lib_id="VHP:628128", reference="U1", value="628128", x=101.6, y=101.6,
                       symbol_lib_path=VHP, schematic_path=p)
    hp = {n: pos(p, "U1", n) for n in ("32", "16")}
    assert hp == {"32": (101.6, 73.66), "16": (101.6, 129.54)}, hp
    H.place(p, "Device:R", "R1", 101.6, 63.5)
    H.wire(p, *pos(p, "R1", "2"), *hp["32"])
    H.place(p, "Device:R", "R2", 152.4, 101.6)
    for ref, x, txt in (("R7", 177.8, "VSS"), ("R8", 203.2, "SIG")):
        H.place(p, "Device:R", ref, x, 127)
        xx, yy = pos(p, ref, "1")
        H.wire(p, xx, yy, xx, 120.65)
        H.label(p, txt, xx, 120.65)
    return p


cases = [
    ("a", "wire_pins_to_net", [("U1", "16")], "GND"),
    ("b", "wire_pins_to_net", [("U1", "16")], "VSS"),
    ("c", "wire_pins_to_net", [("U1", "GND")], "GND"),
    ("d1", "wire_pins_to_net", [("R1", "2")], "SIG"),
    ("d2", "wire_pins_to_net", [("R1", "2")], "VCC"),
    ("e", "wire_pins_to_net", [("R2", "1")], "VCC"),
    ("f", "wire_pins_to_net", [("R2", "1")], "Vcc"),
    ("g", "connect_pins", None, None),
]
focus = [("U1", "16"), ("U1", "32"), ("R1", "2"), ("R2", "1"), ("R7", "1"), ("R8", "1")]
for tag, tool, pins, N in cases:
    fx = Fixture(f"n06_{tag}")
    p = build(fx)
    if tag == "a":
        show_nets(p, "fixture", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("n06.kicad_sch")
        if tool == "wire_pins_to_net":
            tg = [(r, "16" if n == "GND" else n) for r, n in pins]
            step(d, f"N06{tag}: wire_pins_to_net {pins} -> {N}", s, [s], tool, W(pins, N, s),
                 targets=tg, N=N, vid=tag)
        else:
            step(d, "N06g: connect_pins R1:2 (VCC via hidden pin) -> R8:1 (SIG)", s, [s], tool,
                 CP("R1", "2", "R8", "1", s), pair=(("R1", "2"), ("R8", "1")), vid=tag)
        show_nets(s, f"[{d}] {tag} after", focus=focus)

dump(__file__)
