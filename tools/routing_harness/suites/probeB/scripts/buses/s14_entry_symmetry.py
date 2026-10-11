# probeB copy of pr57pt/probe-buses/s14_entry_symmetry.py (header swapped to the probeB shims; fixture code unchanged).
"""S14: bus-entry ends are symmetric in the file (KiCad demos put (at) on the WIRE side 61% of the
time, BUS-3). Re-run S02a/S02b and the S05b shared-point case with the reversed encoding ((at) on
the wire side, negative size), and add a connect_pins H-first route through a foreign member
entry's WIRE-side end.

s14a  = s02a, entry written (at wire-side) (size -2.54 -2.54)        expected OK (label on pin)
s14b  = s02b, entry written (at wire-side) (size -2.54 2.54)         expected OK
s14c  = s05b ('<' shared point), both entries written from the wire side; R2:1 -> 'D1'
                                                                     expected OK (KiCad: R2 not on D1)
s14d  connect_pins(R1:1, R2:2): H-first leg passes a member entry's WIRE-side end (member D0 -> R5);
      V-first is clean                                               expected OK (V-first)
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s14_entry_symmetry", __file__)
from bl import (bus, bus_entry, check_pin, dump, fresh, label, pins_arg, place, scenario,  # noqa: E402
                show_nets, wire)

R1P1 = ("R1", "1")

# s14a: s02a with the reversed encoding
p = fresh("s14a")
place(p, "Device:R", "R1", 101.6, 101.6)
check_pin(p, "R1", "1", (101.6, 97.79))
bus(p, 99.06, 81.28, 99.06, 93.98)
bus(p, 99.06, 93.98, 99.06, 106.68)
label(p, "D[0..3]", 99.06, 86.36, rot=90)
bus_entry(p, 101.6, 96.52, -2.54, -2.54)              # ends (101.6, 96.52) [wire] and (99.06, 93.98) [bus]
place(p, "Device:R", "R2", 114.3, 104.14)
wire(p, 101.6, 96.52, 114.3, 96.52)
wire(p, 114.3, 96.52, 114.3, 100.33)
label(p, "D0", 109.22, 96.52)
scenario("s14a", "s02a reversed entry encoding: stub passes a foreign entry's wire-side end", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="N6")),
         targets=[R1P1], N="N6", expect="OK")

# s14b: s02b with the reversed encoding
p = fresh("s14b")
place(p, "Device:R", "R1", 101.6, 101.6)
check_pin(p, "R1", "1", (101.6, 97.79))
bus(p, 88.9, 96.52, 101.6, 96.52)
bus(p, 101.6, 96.52, 114.3, 96.52)
label(p, "D[0..3]", 91.44, 96.52)
bus_entry(p, 104.14, 93.98, -2.54, 2.54)              # ends (104.14, 93.98) [wire] and (101.6, 96.52) [bus]
place(p, "Device:R", "R2", 116.84, 87.63)
wire(p, 104.14, 93.98, 116.84, 93.98)
wire(p, 116.84, 93.98, 116.84, 91.44)
label(p, "D0", 109.22, 93.98)
scenario("s14b", "s02b reversed entry encoding: stub crosses the bus at a foreign entry's bus-side end", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="N7")),
         targets=[R1P1], N="N7", expect="OK")

# s14c: s05b with both entries written from the wire side
p = fresh("s14c")
bus(p, 76.2, 81.28, 76.2, 93.98)
bus(p, 76.2, 93.98, 76.2, 106.68)
label(p, "D[0..3]", 76.2, 86.36, rot=90)
bus_entry(p, 78.74, 91.44, -2.54, 2.54)               # EU, (at) on the wire side
bus_entry(p, 78.74, 96.52, -2.54, -2.54)              # ED, (at) on the wire side
place(p, "Device:R", "R1", 119.38, 95.25)
check_pin(p, "R1", "1", (119.38, 91.44))
wire(p, 78.74, 91.44, 119.38, 91.44)
label(p, "D1", 83.82, 91.44)
place(p, "Device:R", "R2", 106.68, 100.33)
check_pin(p, "R2", "1", (106.68, 96.52))
wire(p, 78.74, 96.52, 106.68, 96.52)
scenario("s14c", "s05b reversed entry encoding: '<' shared point, R2:1 -> D1", p,
         ("wire_pins_to_net", dict(pins=pins_arg(("R2", "1")), label_text="D1")),
         targets=[("R2", "1")], N="D1", expect="OK")

# s14d: connect_pins H-first through a member entry's wire-side end
p = fresh("s14d")
place(p, "Device:R", "R1", 101.6, 101.6)
check_pin(p, "R1", "1", (101.6, 97.79))
place(p, "Device:R", "R2", 139.7, 81.28)
check_pin(p, "R2", "2", (139.7, 85.09))
bus(p, 118.11, 76.2, 118.11, 95.25)
bus(p, 118.11, 95.25, 118.11, 111.76)
label(p, "D[0..3]", 118.11, 78.74, rot=90)
bus_entry(p, 118.11, 95.25, 2.54, 2.54)               # wire side (120.65, 97.79): on the H-first leg
place(p, "Device:R", "R5", 128.27, 105.41)
check_pin(p, "R5", "1", (128.27, 101.6))
wire(p, 120.65, 97.79, 120.65, 101.6)
wire(p, 120.65, 101.6, 128.27, 101.6)
label(p, "D0", 124.46, 101.6)
print("s14d fixture nets:")
show_nets(p, [("R1", "1"), ("R2", "2"), ("R5", "1")])
scenario("s14d", "connect_pins: H-first leg passes a member entry's wire-side end; V-first clean", p,
         ("connect_pins", dict(ref1="R1", pin1="1", ref2="R2", pin2="2")),
         pair=(("R1", "1"), ("R2", "2")), expect="OK", designs=("P", "Pt", "M", "B"))

dump("s14_entry_symmetry")
