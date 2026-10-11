# probeB copy of pr57pt/probe-units-pads/c_coincident.py: probeB import header in front, judge() calls carry vid=/stepno=; fixture code unchanged.
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("c_coincident", __file__)
"""C: coincident pin ends of two parts. KiCad joins coincident pin ends (digest G07), except that a
no_connect-type pin never joins (G08/S12).

Base: R1 at (101.6, 101.6): R1:1 (101.6, 97.79) up, R1:2 (101.6, 105.41) down.
      R2 at (101.6, 109.22): R2:1 (101.6, 105.41) = R1:2, R2:2 (101.6, 113.03).
      R3 free at (127, 101.6); R9:1 on local A (a named net with a real pin).

  C1  separate calls: wire_pins_to_net([R1:2], A) then, on that call's own output,
      wire_pins_to_net([R2:1], B)                      the second must refuse (R2:1 is on A)
  C2  one call: wire_pins_to_net([R1:2, R2:1], A)       second pin is already on A after the first
  C3  connect_pins(R1, 2, R2, 1)                       already connected: no-op
  C4  NCP U2 (pin 1 no_connect type) placed so U2:1 = R1:2; R2 absent.
      wire_pins_to_net([R1:2], A)                      legit: KiCad joins R1:2 to A, U2:1 stays alone
  C5  one call, two nets: auto_place_decoupling_cap(Device:C, C1 at (101.6, 133.35)) with R4 at
      (101.6, 125.73) and R5 at (101.6, 140.97): C1:1 lands on R4:2 (129.54) and C1:2 on R5:1
      (137.16); R4:2 and R5:1 are free, R4:1 is on SIG.  power VCC, ground GND.
  C5b same, but R4:2 already carries label OUT (named): must refuse with every file intact.
"""

import os

from uplib import (
    IMPLS, W, C, copies, dump, fresh, judge, label, lpin, lsym, nets_coded, place, place_custom,
    probe_r, sha, stub, triplicate, write_lib,
)

NCP = write_lib("ncp.kicad_sym", [lsym("NCP", {(1, 1): [
    lpin("1", "NC", typ="no_connect", x=-5.08, y=0, ang=0),
    lpin("2", "B", x=5.08, y=0, ang=180)]})])


def base(name, r2=True, ncp=False):
    p = fresh(name)
    place(p, "Device:R", "R1", 101.6, 101.6)
    if r2:
        place(p, "Device:R", "R2", 101.6, 109.22)
    place(p, "Device:R", "R3", 127, 101.6)
    probe_r(p, "R9", 50.8, 63.5, "A")
    if ncp:
        place_custom(p, NCP, "NCP", "U2", 106.68, 105.41)
    return p


def cap_base(name, named_out=False):
    p = fresh(name)
    place(p, "Device:R", "R4", 101.6, 125.73)  # R4:1 (101.6, 121.92), R4:2 (101.6, 129.54)
    place(p, "Device:R", "R5", 101.6, 140.97)  # R5:1 (101.6, 137.16), R5:2 (101.6, 144.78)
    stub(p, 101.6, 121.92, "up", "SIG")
    probe_r(p, "R9", 50.8, 63.5, "SIG")
    if named_out:
        label(p, "OUT", 101.6, 129.54)
        probe_r(p, "R8", 76.2, 63.5, "OUT")
    return p


q = base("c_layout")
print("R1/R2 pins:", __import__("mine_impl").pin_positions(q, "R1"), __import__("mine_impl").pin_positions(q, "R2"))
print("fixture netlist:", [(nm, sorted(v)) for _c, nm, v in nets_coded(q)])

# C1: two sequential calls on each implementation's own file
b = base("c1")
for impl, q in triplicate(b).items():
    judge("C1 call 1: R1:2 -> A", impl, q, IMPLS[impl]["wire"], W(q, [("R1", "2")], "A"),
          targets=[("R1", "2")], N="A", vid="C1", stepno=1, nsteps=2)
    judge("C1 call 2: R2:1 -> B (R2:1 is on A now)", impl, q, IMPLS[impl]["wire"],
          W(q, [("R2", "1")], "B"), targets=[("R2", "1")], N="B", vid="C1", stepno=2, nsteps=2)

b = base("c2")
for impl, q in triplicate(b).items():
    judge("C2 one call [R1:2, R2:1] -> A", impl, q, IMPLS[impl]["wire"],
          W(q, [("R1", "2"), ("R2", "1")], "A"), targets=[("R1", "2"), ("R2", "1")], N="A", vid="C2")

b = base("c3")
for impl, q in triplicate(b).items():
    judge("C3 connect_pins(R1:2, R2:1) coincident", impl, q, IMPLS[impl]["connect"],
          C(q, "R1", "2", "R2", "1"), pair=(("R1", "2"), ("R2", "1")), vid="C3")

b = base("c4", r2=False, ncp=True)
print("\nC4 U2 pins:", __import__("mine_impl").pin_positions(b, "U2"))
print("C4 fixture netlist:", [(nm, sorted(v)) for _c, nm, v in nets_coded(b)])
for impl, q in triplicate(b).items():
    judge("C4 R1:2 -> A with U2's NC-type pin end on R1:2", impl, q, IMPLS[impl]["wire"],
          W(q, [("R1", "2")], "A"), targets=[("R1", "2")], N="A", vid="C4")

for tag, named in (("C5", False), ("C5b", True)):
    b = cap_base(tag.lower(), named_out=named)
    print(f"\n{tag} fixture netlist:", [(nm, sorted(v)) for _c, nm, v in nets_coded(b)])
    for impl, q in triplicate(b).items():
        kw = dict(lib_id="Device:C", reference="C1", value="100n", x=101.6, y=133.35,
                  power_net="VCC", ground_net="GND", schematic_path=q)
        judge(f"{tag} auto_place_decoupling_cap C1 on R4:2/R5:1", impl, q, IMPLS[impl]["cap"], kw,
              targets=[("C1", "1")], N=None, extra_allowed=("/VCC", "/GND"), vid=tag)
        after = {nm: sorted(v) for _c, nm, v in nets_coded(q)}
        print(f"    nets after: { {k: v for k, v in after.items() if any(r in ('R4', 'R5', 'C1') for r, _ in v)} }")

dump("c_coincident.json")
