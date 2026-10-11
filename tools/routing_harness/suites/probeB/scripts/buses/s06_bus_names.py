# probeB copy of pr57pt/probe-buses/s06_bus_names.py (header swapped to the probeB shims; fixture code unchanged).
"""S06: label_text that is bus syntax, or a bus member name. P (as written and as prototyped) does
not validate label_text beyond non-empty; M refuses bus syntax (5.4 step 1).

Fixture: bus 'D[0..3]' split at its entries; members W0 'D0' -> R0:1, W1 'D1' -> R1:1 (S04
layout); a free resistor R5 off to the right; s06d adds a second bus segment labelled 'D[0..3]'
drawn pin-to-pin between R6:1 and R7:1 (pins on bus endpoints, BUS-2).

s06a  wire_pins_to_net(R5:1, 'D[0..3]')  (a bus label on a plain wire stub)
s06b  wire_pins_to_net(R5:1, 'D0')       (member name: joins /D0 by name, the requested join)
s06c  wire_pins_to_net(R5:1, 'D[0..3]') on a sheet with no bus at all (control)
s06d  wire_pins_to_net(R5:1, 'D[0..3]') with R6:1/R7:1 on a bus labelled 'D[0..3]'
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s06_bus_names", __file__)
from bl import *  # noqa: F401,F403
from bl import (bus, bus_entry, check_pin, dump, fresh, label, pins_arg, place, scenario,
                show_nets, wire)

PINS = {"R0": (132.08, 91.44), "R1": (119.38, 96.52)}
YS = (88.9, 93.98)


def fixture(name, with_bus=True, pin_bus=False):
    p = fresh(name)
    if with_bus:
        pts = [81.28, *YS, 101.6]
        for a, b in zip(pts, pts[1:]):
            bus(p, 76.2, a, 76.2, b)
        label(p, "D[0..3]", 76.2, 83.82, rot=90)
        for i, (ref, (x, y)) in enumerate(PINS.items()):
            place(p, "Device:R", ref, x, round(y + 3.81, 4))
            check_pin(p, ref, "1", (x, y))
            bus_entry(p, 76.2, YS[i], 2.54, 2.54)
            wire(p, 78.74, y, x, y)
            label(p, f"D{i}", 83.82, y)
    place(p, "Device:R", "R5", 152.4, 101.6)
    check_pin(p, "R5", "1", (152.4, 97.79))
    if pin_bus:
        place(p, "Device:R", "R6", 101.6, 127)
        place(p, "Device:R", "R7", 127, 127)
        check_pin(p, "R6", "1", (101.6, 123.19))
        check_pin(p, "R7", "1", (127.0, 123.19))
        bus(p, 101.6, 123.19, 127, 123.19)
        label(p, "D[0..3]", 114.3, 123.19)
    return p


W = "wire_pins_to_net"
R5P1 = ("R5", "1")
p = fixture("s06_probe", pin_bus=True)
print("fixture nets (s06d):")
show_nets(p)

scenario("s06a", "R5:1 -> 'D[0..3]' (bus syntax) next to bus D[0..3] with members D0/D1", fixture("s06a"),
         (W, dict(pins=pins_arg(R5P1), label_text="D[0..3]")), targets=[R5P1], N="D[0..3]",
         expect="REFUSED", note="expect REFUSED: a bus label on a wire is a bus_to_net_conflict (BUS-16)")
scenario("s06b", "R5:1 -> 'D0' (member name)", fixture("s06b"),
         (W, dict(pins=pins_arg(R5P1), label_text="D0")), targets=[R5P1], N="D0", expect="OK")
scenario("s06c", "R5:1 -> 'D[0..3]' with no bus on the sheet", fixture("s06c", with_bus=False),
         (W, dict(pins=pins_arg(R5P1), label_text="D[0..3]")), targets=[R5P1], N="D[0..3]",
         expect="REFUSED")
scenario("s06d", "R5:1 -> 'D[0..3]' with R6:1/R7:1 on a pin-to-pin bus labelled D[0..3]",
         fixture("s06d", pin_bus=True), (W, dict(pins=pins_arg(R5P1), label_text="D[0..3]")),
         targets=[R5P1], N="D[0..3]", expect="REFUSED")

dump("s06_bus_names")
