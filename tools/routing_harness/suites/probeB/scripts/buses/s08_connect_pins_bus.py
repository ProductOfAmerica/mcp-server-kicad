# probeB copy of pr57pt/probe-buses/s08_connect_pins_bus.py (header swapped to the probeB shims; fixture code unchanged).
"""S08: connect_pins routes against buses and bus entries. Designs: P (prototype), Pt (P with the
touch rule as written: only plain crossings of WIRES are exempt), M, and baseline B.

Pins: R1:1 (101.6, 97.79) and R2:2 (139.7, 85.09) unless stated. Candidate routes:
  H-first: (101.6, 97.79) -> (139.7, 97.79) -> (139.7, 85.09)
  V-first: (101.6, 97.79) -> (101.6, 85.09) -> (139.7, 85.09)
A vertical bus at x=120.65 crosses both horizontal legs.

s08a  both routes cross the bus at clean points (an on-bus label OTHER sits between them,
      R3 carries OTHER elsewhere)                                         expected OK
s08b  H-first crosses the bus exactly at a member entry's BUS-side end (bus split there, member
      D0 -> R5); V-first crosses cleanly                                  expected OK (V-first)
s08c  H-first crosses the bus exactly at an on-bus net label OTHER; V-first clean
                                                                          expected OK (V-first)
s08d  H-first as s08b, V-first crosses at an on-bus label OTHER: no clean candidate
                                                                          expected REFUSED
s08e  aligned pins R1:1 / R4:1 (139.7, 97.79): the straight route crosses the bus cleanly
                                                                          expected OK
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s08_connect_pins_bus", __file__)
from bl import *  # noqa: F401,F403
from bl import (bus, bus_entry, check_pin, dump, fresh, label, place, scenario, show_nets, wire)

BX = 120.65


def base(name, splits=(), labels=(), entry_at=None):
    p = fresh(name)
    place(p, "Device:R", "R1", 101.6, 101.6)
    check_pin(p, "R1", "1", (101.6, 97.79))
    place(p, "Device:R", "R2", 139.7, 81.28)
    check_pin(p, "R2", "2", (139.7, 85.09))
    ys = [76.2, *sorted(splits), 111.76]
    for a, b in zip(ys, ys[1:]):
        bus(p, BX, a, BX, b)
    label(p, "D[0..3]", BX, 78.74, rot=90)
    for y in labels:
        label(p, "OTHER", BX, y, rot=90)
    if labels:
        place(p, "Device:R", "R3", 160.02, 101.6)
        check_pin(p, "R3", "1", (160.02, 97.79))
        wire(p, 160.02, 97.79, 160.02, 95.25)
        label(p, "OTHER", 160.02, 95.25, rot=90)
    if entry_at is not None:
        bus_entry(p, BX, entry_at, 2.54, 2.54)           # wire side (123.19, entry_at + 2.54)
        wy = round(entry_at + 2.54, 4)
        place(p, "Device:R", "R5", 128.27, round(wy + 3.81, 4))
        check_pin(p, "R5", "1", (128.27, wy))
        wire(p, 123.19, wy, 128.27, wy)
        label(p, "D0", 125.73, wy)
    return p


PAIR = (("R1", "1"), ("R2", "2"))
CALL = ("connect_pins", dict(ref1="R1", pin1="1", ref2="R2", pin2="2"))
DES = ("P", "Pt", "M", "B")

p = base("s08_probe", splits=(97.79,), labels=(85.09,), entry_at=97.79)
print("fixture nets (s08d shape):")
show_nets(p)

scenario("s08a", "both L routes cross the bus at clean points", base("s08a", labels=(91.44,)),
         CALL, pair=PAIR, expect="OK", designs=DES)
scenario("s08b", "H-first crosses the bus at a member entry's bus-side end; V-first clean",
         base("s08b", splits=(97.79,), entry_at=97.79), CALL, pair=PAIR, expect="OK", designs=DES)
scenario("s08c", "H-first crosses the bus at an on-bus label OTHER; V-first clean",
         base("s08c", labels=(97.79,)), CALL, pair=PAIR, expect="OK", designs=DES)
scenario("s08d", "H-first at an entry bus-side end, V-first at an on-bus label: no clean candidate",
         base("s08d", splits=(97.79,), labels=(85.09,), entry_at=97.79), CALL, pair=PAIR,
         expect="REFUSED", designs=DES)
p = base("s08e", labels=(91.44,))
place(p, "Device:R", "R4", 139.7, 101.6)
check_pin(p, "R4", "1", (139.7, 97.79))
scenario("s08e", "aligned pins; the straight route crosses the bus cleanly", p,
         ("connect_pins", dict(ref1="R1", pin1="1", ref2="R4", pin2="1")),
         pair=(("R1", "1"), ("R4", "1")), expect="OK", designs=DES)

dump("s08_connect_pins_bus")
