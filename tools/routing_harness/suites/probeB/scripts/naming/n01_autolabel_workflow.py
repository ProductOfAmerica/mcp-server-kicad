# probeB copy of pr57pt/probe-naming/n01_autolabel_workflow.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N01: connect_pins(R1:1, R2:1) then wire_pins_to_net([R1:1], 'VCC').

Question: does P refuse the second, legitimate edit because its own connect_pins auto label
'Net-(R1-1)' named the net? What do M and B (repo main) do?

Variants for where VCC already lives on the sheet:
  v1  nowhere (VCC is a new net)
  v2  power:VCC symbol (Value 'VCC') on R3:1
  v3  local label 'VCC' on R3:1
Fixture: R1 (101.6, 101.6), R2 (127, 101.6), rot 0, so R1:1 and R2:1 are aligned at y 97.79
(straight route). R3 at (152.4, 127); its pin 1 (152.4, 123.19) gets a raw wire up to
(152.4, 120.65) and the VCC item there.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n01_autolabel_workflow", __file__)
from nlib import CP, H, Fixture, W, dump, pos, show_nets, step  # noqa: E402

for variant in ("v1", "v2", "v3"):
    fx = Fixture(f"n01_{variant}")
    p = fx.path("n01.kicad_sch")
    H.project.create_schematic(p)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 127, 101.6)
    if variant != "v1":
        H.place(p, "Device:R", "R3", 152.4, 127)
        x3, y3 = pos(p, "R3", "1")
        H.wire(p, x3, y3, x3, 120.65)
        if variant == "v2":
            H.place(p, "power:VCC", "#PWR01", x3, 120.65, value="VCC")
        else:
            H.label(p, "VCC", x3, 120.65)
    show_nets(p, f"{variant} fixture")
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("n01.kicad_sch")
        step(d, f"N01{variant} step 1: connect_pins R1:1 -> R2:1", s, [s], "connect_pins",
             CP("R1", "1", "R2", "1", s), pair=(("R1", "1"), ("R2", "1")), vid=variant, stepno=1, nsteps=2)
        step(d, f"N01{variant} step 2: wire_pins_to_net [R1:1] -> VCC", s, [s],
             "wire_pins_to_net", W([("R1", "1")], "VCC", s),
             targets=[("R1", "1"), ("R2", "1")], N="VCC",
             note="R2:1 is R1:1's net-mate after step 1 and should come along", vid=variant, stepno=2, nsteps=2)
        if variant != "v1":
            show_nets(s, f"[{d}] {variant} after both steps",
                      focus=[("R1", "1"), ("R2", "1"), ("R3", "1")])

dump(__file__)
