# probeB copy of pr57pt/probe-buses/s10_multi_and_cap.py (header swapped to the probeB shims; fixture code unchanged).
"""S10: remaining bus cases for the third routing tool and for multi-pin calls.

s10a  '<' shared entry point (S05 geometry), both member wires unlabelled:
      wire_pins_to_net([R1:1, R2:1], 'N')  expected OK with BOTH pins on N.
s10b  auto_place_decoupling_cap: C1 placed so that pin 1 lands exactly on a bus ENDPOINT; the bus
      runs to R9:1 and carries plain label 'SIG' (BUS-2: pins on bus ends are one net).
      expected REFUSED with every file intact (placing C1 there puts C1:1 on SIG; wiring it to
      VCC would merge VCC and SIG).
s10c  auto_place_decoupling_cap: C1's pin-1 'up' stub end would land on a bus interior that
      already carries R9's OTHER stub+label (BUS-9 shape).                 expected OK
s10d  S01c (1x stub crosses a bus at a clean point) with P as written (Pt): only plain WIRE
      crossings are exempt in P's text.                                      expected OK
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s10_multi_and_cap", __file__)
from bl import *  # noqa: F401,F403
from bl import (bus, bus_entry, check_pin, dump, fresh, label, pins_arg, place, scenario,
                show_nets, strip, wire)


def shared(name):
    p = fresh(name)
    bus(p, 76.2, 81.28, 76.2, 93.98)
    bus(p, 76.2, 93.98, 76.2, 106.68)
    label(p, "D[0..3]", 76.2, 86.36, rot=90)
    bus_entry(p, 76.2, 93.98, 2.54, -2.54)            # EU -> (78.74, 91.44)
    bus_entry(p, 76.2, 93.98, 2.54, 2.54)             # ED -> (78.74, 96.52)
    place(p, "Device:R", "R1", 119.38, 95.25)
    check_pin(p, "R1", "1", (119.38, 91.44))
    wire(p, 78.74, 91.44, 119.38, 91.44)
    place(p, "Device:R", "R2", 106.68, 100.33)
    check_pin(p, "R2", "1", (106.68, 96.52))
    wire(p, 78.74, 96.52, 106.68, 96.52)
    return p


scenario("s10a", "'<' shared entry point, both members unlabelled: [R1:1, R2:1] -> N in one call",
         shared("s10a"), ("wire_pins_to_net", dict(pins=pins_arg(("R1", "1"), ("R2", "1")), label_text="N")),
         targets=[("R1", "1"), ("R2", "1")], N="N", expect="OK")

CAP = dict(lib_id="Device:C", reference="C1", value="100n", x=127, y=101.6, power_net="VCC",
           ground_net="GND")

p = fresh("s10b")
place(p, "Device:R", "R9", 101.6, 101.6)
check_pin(p, "R9", "1", (101.6, 97.79))
bus(p, 101.6, 97.79, 127, 97.79)                     # ends where C1:1 will land (127, 97.79)
label(p, "SIG", 114.3, 97.79)
print("s10b fixture nets:")
show_nets(p, [("R9", "1")])
def cap_on(after):
    where = {n: name for _, name, v in after for n in v}
    bad = []
    for pin_, want in ((("C1", "1"), "VCC"), (("C1", "2"), "GND")):
        if strip(where.get(pin_, "")) != want:
            bad.append(f"C1:{pin_[1]} on {where.get(pin_)!r}, wanted {want!r}")
    return [f"CAP NOT AS REQUESTED: {bad}"] if bad else []


scenario("s10b", "auto_place_decoupling_cap: C1:1 would land on a bus endpoint of net SIG", p,
         ("auto_place_decoupling_cap", CAP), targets=[], expect="REFUSED", new_refs=("C1",),
         extra=cap_on,
         note="the netlist judge sees the whole call; C1 pins do not exist before, so 'appeared' is"
              " expected on success")

p = fresh("s10c")
place(p, "Device:R", "R9", 114.3, 101.6)
check_pin(p, "R9", "1", (114.3, 97.79))
bus(p, 101.6, 95.25, 139.7, 95.25)                   # C1:1 at (127, 97.79); 'up' 1x end (127, 95.25)
wire(p, 114.3, 97.79, 114.3, 95.25)
label(p, "OTHER", 114.3, 95.25, rot=90)
print("s10c fixture nets:")
show_nets(p, [("R9", "1")])
scenario("s10c", "auto_place_decoupling_cap: C1 pin-1 'up' stub end on a bus carrying OTHER", p,
         ("auto_place_decoupling_cap", CAP), targets=[], expect="OK", new_refs=("C1",),
         extra=cap_on)

p = fresh("s10d")
place(p, "Device:R", "R1", 101.6, 101.6)
check_pin(p, "R1", "1", (101.6, 97.79))
bus(p, 88.9, 96.52, 114.3, 96.52)
label(p, "D[0..3]", 91.44, 96.52)
place(p, "Device:R", "R2", 109.22, 101.6)
wire(p, 109.22, 97.79, 109.22, 96.52)
label(p, "OTHER", 109.22, 96.52, rot=90)
scenario("s10d", "S01c with P as written: 1x stub crosses a bus at a clean point", p,
         ("wire_pins_to_net", dict(pins=pins_arg(("R1", "1")), label_text="N3")),
         targets=[("R1", "1")], N="N3", expect="OK", designs=("P", "Pt", "M"))

dump("s10_multi_and_cap")
