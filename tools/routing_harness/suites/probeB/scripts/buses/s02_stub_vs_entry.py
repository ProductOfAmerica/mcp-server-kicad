# probeB copy of pr57pt/probe-buses/s02_stub_vs_entry.py (header swapped to the probeB shims; fixture code unchanged).
"""S02: wire_pins_to_net stubs passing through bus-entry ends (both ends are symmetric in the file;
KiCad decides the bus side geometrically). P, M and baseline B on copies of one fixture.

s02a  1x stub interior passes the WIRE-side end of a foreign member entry (D0, R2), N = 'N6'.
s02b  1x stub crosses a pre-split bus exactly at a foreign entry's BUS-side end, N = 'N7'.
s02c  as s02a but N = 'D0' (the entry's own member net, so P's touch rule admits it).
s02d  as s02b but N = 'D0'.
s02e  1x stub crosses an entry's diagonal BODY (not an end): inert in KiCad (BUS-11).
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s02_stub_vs_entry", __file__)
from bl import *  # noqa: F401,F403
from bl import bus, bus_entry, check_pin, dump, fresh, label, pins_arg, place, scenario, wire

R1P1 = ("R1", "1")


def wire_side_fixture(name):
    p = fresh(name)
    place(p, "Device:R", "R1", 101.6, 101.6)
    check_pin(p, "R1", "1", (101.6, 97.79))
    # vertical bus x=99.06, split at the entry's bus point (99.06, 93.98)
    bus(p, 99.06, 81.28, 99.06, 93.98)
    bus(p, 99.06, 93.98, 99.06, 106.68)
    label(p, "D[0..3]", 99.06, 86.36, rot=90)
    bus_entry(p, 99.06, 93.98, 2.54, 2.54)            # wire side (101.6, 96.52): on R1's stub path
    place(p, "Device:R", "R2", 114.3, 104.14)
    check_pin(p, "R2", "1", (114.3, 100.33))
    wire(p, 101.6, 96.52, 114.3, 96.52)
    wire(p, 114.3, 96.52, 114.3, 100.33)
    label(p, "D0", 109.22, 96.52)
    return p


def bus_side_fixture(name):
    p = fresh(name)
    place(p, "Device:R", "R1", 101.6, 101.6)
    check_pin(p, "R1", "1", (101.6, 97.79))
    # horizontal bus y=96.52 crossing R1's stub path, split at (101.6, 96.52) = entry bus-side end
    bus(p, 88.9, 96.52, 101.6, 96.52)
    bus(p, 101.6, 96.52, 114.3, 96.52)
    label(p, "D[0..3]", 91.44, 96.52)
    bus_entry(p, 101.6, 96.52, 2.54, -2.54)           # wire side (104.14, 93.98)
    place(p, "Device:R", "R2", 116.84, 87.63)
    check_pin(p, "R2", "2", (116.84, 91.44))
    wire(p, 104.14, 93.98, 116.84, 93.98)
    wire(p, 116.84, 93.98, 116.84, 91.44)
    label(p, "D0", 109.22, 93.98)
    return p


p = wire_side_fixture("s02a")
scenario("s02a", "stub interior passes a foreign entry's WIRE-side end (member D0 -> R2)", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="N6")),
         targets=[R1P1], N="N6", expect="OK")

p = bus_side_fixture("s02b")
scenario("s02b", "stub crosses a pre-split bus exactly at a foreign entry's BUS-side end", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="N7")),
         targets=[R1P1], N="N7", expect="OK")

p = wire_side_fixture("s02c")
scenario("s02c", "as s02a with N = 'D0' (the entry's own member net)", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="D0")),
         targets=[R1P1], N="D0", expect="OK")

p = bus_side_fixture("s02d")
scenario("s02d", "as s02b with N = 'D0'", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="D0")),
         targets=[R1P1], N="D0", expect="OK")

# s02e: crossing an entry's diagonal body
p = fresh("s02e")
place(p, "Device:R", "R1", 101.6, 101.6)
check_pin(p, "R1", "1", (101.6, 97.79))
bus(p, 102.87, 85.09, 102.87, 95.25)
bus(p, 102.87, 95.25, 102.87, 110.49)
label(p, "D[0..3]", 102.87, 88.9, rot=90)
bus_entry(p, 100.33, 97.79, 2.54, -2.54)              # ends (100.33, 97.79) and (102.87, 95.25)
place(p, "Device:R", "R2", 93.98, 104.14)
check_pin(p, "R2", "1", (93.98, 100.33))
wire(p, 100.33, 97.79, 93.98, 97.79)
wire(p, 93.98, 97.79, 93.98, 100.33)
label(p, "D0", 96.52, 97.79)
scenario("s02e", "stub crosses an entry's diagonal body at (101.6, 96.52), not an end", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="N8")),
         targets=[R1P1], N="N8", expect="OK")

dump("s02_stub_vs_entry")
