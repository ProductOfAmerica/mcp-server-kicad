# probeB copy of pr57pt/probe-units-pads/s_stacked.py: probeB import header in front, judge() calls carry vid=/stepno=; fixture code unchanged.
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s_stacked", __file__)
"""S: stacked pins of one symbol (several pins of one placed unit ending at one point). KiCad joins
stacked members with no wire, except a no_connect-type member, which never joins; a hidden power_in
member names the point's net by its pin name, globally (digest S7, v14).

Custom symbols (libs/stk.kicad_sym), all placed at (101.6, 101.6):
  STK   pin 1 A passive + pin 4 A hidden passive at lib (-5.08, 0) -> (96.52, 101.6); pin 2 B right.
  STKH  pin 1 S passive + pin 5 T5 hidden POWER_IN at (-5.08, 0); pin 2 B right.
  STKNC pin 3 C passive + pin 5 NC hidden NO_CONNECT at lib (0, 5.08) -> (101.6, 96.52), outward
        up; pin 2 B right.
Stock: Amplifier_Current:INA240A1D (non-derived): pin 2 GND visible power_in + pin 4 GND hidden
passive stacked (the typical stock pattern: 4494 stacked groups, none with an NC or hidden
power_in member, scan_stock.out). SOIC-8.

  S1a STK   wire_pins_to_net([U1:4], N)            hidden member asked: both pins must land on N
  S1b STK, pin 1 already on A      wire_pins_to_net([U1:4], N)   must refuse (stack is on A)
  S1c STK   wire_pins_to_net([U1:1, U1:4], N)      one call, both members
  S2a STKH  wire_pins_to_net([U1:1], S)            stack already named T5: must refuse
  S2b STKH  wire_pins_to_net([U1:1], T5)           no-op
  S3a STKNC wire_pins_to_net([U1:3], C)            KiCad: pin 3 joins C, pin 5 stays alone (legit)
  S3b STKNC wire_pins_to_net([U1:5], C)            NC-type member asked: cannot be put on C
  S3c STKNC connect_pins(U1, 3, R3, 1)             legit in KiCad
  S4a INA240A1D wire_pins_to_net([U1:2], GND)      stacked hidden passive pad 4 comes along
  S4b INA240A1D wire_pins_to_net([U1:GND], GND)    asked by the shared NAME
"""

from uplib import (
    IMPLS, C, W, copies, dump, fresh, judge, lpin, lsym, nets_coded, place, place_custom, probe_r,
    set_fp, stub, triplicate, write_lib,
)

LIB = write_lib("stk.kicad_sym", [
    lsym("STK", {(1, 1): [lpin("1", "A", x=-5.08, y=0, ang=0), lpin("4", "A", x=-5.08, y=0, ang=0, hidden=True),
                          lpin("2", "B", x=5.08, y=0, ang=180)]}),
    lsym("STKH", {(1, 1): [lpin("1", "S", x=-5.08, y=0, ang=0),
                           lpin("5", "T5", typ="power_in", x=-5.08, y=0, ang=0, hidden=True),
                           lpin("2", "B", x=5.08, y=0, ang=180)]}),
    lsym("STKNC", {(1, 1): [lpin("3", "C", x=0, y=5.08, ang=270),
                            lpin("5", "NC", typ="no_connect", x=0, y=5.08, ang=270, hidden=True),
                            lpin("2", "B", x=5.08, y=0, ang=180)]}),
])


def build(name, sym, pre=None):
    p = fresh(name)
    place_custom(p, LIB, sym, "U1", 101.6, 101.6)
    probe_r(p, "R1", 50.8, 63.5, "N")
    place(p, "Device:R", "R3", 114.3, 92.71)  # R3:1 (114.3, 88.9) up, R3:2 (114.3, 96.52) down
    if pre:
        num, text = pre
        x, y, d = copies(p, "U1", num)[0]
        stub(p, x, y, d, text)
    return p


def build_ina(name):
    p = fresh(name)
    place(p, "Amplifier_Current:INA240A1D", "U1", 101.6, 101.6)
    place(p, "Device:C", "C1", 63.5, 101.6)
    place(p, "power:GND", "#PWR02", 63.5, 109.22, value="GND")
    __import__("uplib").wire(p, 63.5, 105.41, 63.5, 109.22)
    set_fp(p, "U1", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    set_fp(p, "C1", "Capacitor_SMD:C_0603_1608Metric")
    return p


for sym in ("STK", "STKH", "STKNC"):
    q = build(f"s_layout_{sym}", sym)
    print(f"{sym} drawn pins:", __import__("mine_impl").pin_positions(q, "U1"))
    print(f"{sym} fixture netlist:", [(nm, sorted(v)) for _c, nm, v in nets_coded(q) if any(r == "U1" for r, _ in v)])
q = build_ina("s_layout_ina")
pp = __import__("mine_impl").pin_positions(q, "U1")
print("INA240A1D pads 2/4:", pp.get("2"), pp.get("4"))
print("INA240A1D netlist (pads 2/4):", [(nm, sorted(v)) for _c, nm, v in nets_coded(q) if ("U1", "2") in v or ("U1", "4") in v])

CASES = [
    ("S1a", lambda: build("s1a", "STK"), "wire", lambda q: W(q, [("U1", "4")], "N"), [("U1", "4"), ("U1", "1")], "N", None),
    ("S1b", lambda: build("s1b", "STK", pre=("1", "A")), "wire", lambda q: W(q, [("U1", "4")], "N"), [("U1", "4")], "N", None),
    ("S1c", lambda: build("s1c", "STK"), "wire", lambda q: W(q, [("U1", "1"), ("U1", "4")], "N"),
     [("U1", "1"), ("U1", "4")], "N", None),
    ("S2a", lambda: build("s2a", "STKH"), "wire", lambda q: W(q, [("U1", "1")], "S"), [("U1", "1")], "S", None),
    ("S2b", lambda: build("s2b", "STKH"), "wire", lambda q: W(q, [("U1", "1")], "T5"), [("U1", "1")], "T5", None),
    ("S3a", lambda: build("s3a", "STKNC"), "wire", lambda q: W(q, [("U1", "3")], "C"), [("U1", "3")], "C", None),
    ("S3b", lambda: build("s3b", "STKNC"), "wire", lambda q: W(q, [("U1", "5")], "C"), [("U1", "5")], "C", None),
    ("S3c", lambda: build("s3c", "STKNC"), "connect", lambda q: C(q, "U1", "3", "R3", "1"), [], None,
     (("U1", "3"), ("R3", "1"))),
    ("S4a", lambda: build_ina("s4a"), "wire", lambda q: W(q, [("U1", "2")], "GND", auto_pwr_flag=False),
     [("U1", "2"), ("U1", "4")], "GND", None),
    ("S4b", lambda: build_ina("s4b"), "wire", lambda q: W(q, [("U1", "GND")], "GND", auto_pwr_flag=False),
     [("U1", "2"), ("U1", "4")], "GND", None),
]
for tag, mk, tool, kw, targets, N, pair in CASES:
    base = mk()
    for impl, q in triplicate(base).items():
        judge(f"{tag} {tool}", impl, q, IMPLS[impl][tool], kw(q), targets=targets, N=N, pair=pair,
              board_refs=("U1", "C1") if tag.startswith("S4") else None, vid=tag)

dump("s_stacked.json")
