# probeB copy of pr57pt/probe-buses/s01_stub_vs_bus.py (header swapped to the probeB shims; fixture code unchanged).
"""S01: wire_pins_to_net stub geometry against a bus (P, M and baseline B on copies of one fixture).

s01a  1x stub end lands on a bus INTERIOR; another net ('OTHER') already sits on that bus
      through a stub that ends on the bus with its label there (the shape the repo's current tool
      writes, BUS-9).
s01b  1x stub end lands on a bus ENDPOINT; the bus's other endpoint is R2:1 and a plain label
      'OTHER' sits on the bus.
s01c  1x stub crosses a bus interior at a clean point (bus labelled 'D[0..3]', R2's OTHER stub
      also ends on the bus).
s01d  1x stub crosses the bus exactly at an on-bus net label 'OTHER' (R2 carries OTHER off-bus).
s01e  same as s01d but the label at the crossing is the bus label 'D[0..3]'.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s01_stub_vs_bus", __file__)
from bl import *  # noqa: F401,F403
from bl import bus, check_pin, dump, fresh, label, pins_arg, place, scenario, wire

R1P1 = ("R1", "1")


def base(name):
    p = fresh(name)
    place(p, "Device:R", "R1", 101.6, 101.6)
    check_pin(p, "R1", "1", (101.6, 97.79))
    return p


# s01a: stub end on bus interior
p = base("s01a")
bus(p, 88.9, 95.25, 114.3, 95.25)
place(p, "Device:R", "R2", 109.22, 101.6)
check_pin(p, "R2", "1", (109.22, 97.79))
wire(p, 109.22, 97.79, 109.22, 95.25)
label(p, "OTHER", 109.22, 95.25, rot=90)
scenario("s01a", "1x stub end on a bus interior; R2's OTHER stub+label already ends on that bus", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="N1")),
         targets=[R1P1], N="N1", expect="OK")

# s01b: stub end on bus endpoint
p = base("s01b")
bus(p, 101.6, 95.25, 127, 95.25)
place(p, "Device:R", "R2", 127, 99.06)
check_pin(p, "R2", "1", (127.0, 95.25))
label(p, "OTHER", 114.3, 95.25)
scenario("s01b", "1x stub end on a bus ENDPOINT; bus runs to R2:1 and carries plain label OTHER", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="N2")),
         targets=[R1P1], N="N2", expect="OK")

# s01c: clean crossing
p = base("s01c")
bus(p, 88.9, 96.52, 114.3, 96.52)
label(p, "D[0..3]", 91.44, 96.52)
place(p, "Device:R", "R2", 109.22, 101.6)
wire(p, 109.22, 97.79, 109.22, 96.52)
label(p, "OTHER", 109.22, 96.52, rot=90)
scenario("s01c", "1x stub crosses a bus interior at a clean point", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="N3")),
         targets=[R1P1], N="N3", expect="OK",
         note="P's text exempts only plain crossings of WIRES; proposal_impl I14 extends it to buses")

# s01d: crossing exactly at an on-bus net label
p = base("s01d")
bus(p, 88.9, 96.52, 114.3, 96.52)
label(p, "OTHER", 101.6, 96.52)
place(p, "Device:R", "R2", 127, 101.6)
wire(p, 127, 97.79, 127, 92.71)
label(p, "OTHER", 127, 92.71, rot=90)
scenario("s01d", "1x stub crosses the bus exactly at an on-bus net label OTHER (R2 on OTHER)", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="N4")),
         targets=[R1P1], N="N4", expect="OK")

# s01e: crossing exactly at the bus label
p = base("s01e")
bus(p, 88.9, 96.52, 114.3, 96.52)
label(p, "D[0..3]", 101.6, 96.52)
place(p, "Device:R", "R2", 109.22, 101.6)
wire(p, 109.22, 97.79, 109.22, 96.52)
label(p, "OTHER", 109.22, 96.52, rot=90)
scenario("s01e", "1x stub crosses the bus exactly at its bus label D[0..3]", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="N5")),
         targets=[R1P1], N="N5", expect="OK")

dump("s01_stub_vs_bus")
