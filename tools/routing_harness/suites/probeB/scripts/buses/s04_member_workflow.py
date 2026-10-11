# probeB copy of pr57pt/probe-buses/s04_member_workflow.py (header swapped to the probeB shims; fixture code unchanged).
"""S04: the ordinary bus workflow. A bus 'D[0..3]' (split at every entry, as KiCad saves it) with
four entries; member wires W0 (label D0) -> R0:1, W1 (label D1) -> R1:1, W2 (unlabelled) -> R2:1,
W3 (unlabelled) -> R3:1. KiCad: R0 on /D0, R1 on /D1, R2 and R3 each alone (BUS-5: an entry gives
a wire no identity). Every member wire enters its pin end horizontally; pins point up.

s04a  wire_pins_to_net(R2:1, 'D2')        expected OK (name the unlabelled member)
s04b  wire_pins_to_net(R0:1, 'D0')        expected NOOP (already on D0)
s04c  wire_pins_to_net(R0:1, 'D1')        expected REFUSED (would merge D0 and D1)
s04d  wire_pins_to_net([R2:1, R3:1], 'X') expected OK (two unnamed nets join X)
s04e  connect_pins(R2:1, R9:1)            expected OK (R9 free)
s04f  connect_pins(R2:1, R3:1)            expected OK (two unnamed member nets)
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s04_member_workflow", __file__)
from bl import *  # noqa: F401,F403
from bl import (bus, bus_entry, check_pin, dump, fresh, label, pins_arg, place, scenario,
                show_nets, wire)

PINS = {"R0": (132.08, 91.44), "R1": (119.38, 96.52), "R2": (106.68, 101.6), "R3": (93.98, 106.68)}
YS = (88.9, 93.98, 99.06, 104.14)


def fixture(name):
    p = fresh(name)
    pts = [81.28, *YS, 111.76]
    for a, b in zip(pts, pts[1:]):
        bus(p, 76.2, a, 76.2, b)
    label(p, "D[0..3]", 76.2, 83.82, rot=90)
    for i, (ref, (x, y)) in enumerate(PINS.items()):
        place(p, "Device:R", ref, x, round(y + 3.81, 4))
        check_pin(p, ref, "1", (x, y))
        bus_entry(p, 76.2, YS[i], 2.54, 2.54)
        wire(p, 78.74, y, x, y)
        if i < 2:
            label(p, f"D{i}", 83.82, y)
    place(p, "Device:R", "R9", 152.4, 118.11)
    check_pin(p, "R9", "1", (152.4, 114.3))
    return p


p = fixture("s04_probe")
print("fixture nets:")
show_nets(p, [(r, "1") for r in (*PINS, "R9")])

W = "wire_pins_to_net"
scenario("s04a", "name the unlabelled member: R2:1 -> D2", fixture("s04a"),
         (W, dict(pins=pins_arg(("R2", "1")), label_text="D2")),
         targets=[("R2", "1")], N="D2", expect="OK")
scenario("s04b", "R0:1 -> D0 (already on D0)", fixture("s04b"),
         (W, dict(pins=pins_arg(("R0", "1")), label_text="D0")),
         targets=[("R0", "1")], N="D0", expect="NOOP")
scenario("s04c", "R0:1 -> D1 (R0 is on D0)", fixture("s04c"),
         (W, dict(pins=pins_arg(("R0", "1")), label_text="D1")),
         targets=[("R0", "1")], N="D1", expect="REFUSED")
scenario("s04d", "[R2:1, R3:1] -> X (two unnamed member nets)", fixture("s04d"),
         (W, dict(pins=pins_arg(("R2", "1"), ("R3", "1")), label_text="X")),
         targets=[("R2", "1"), ("R3", "1")], N="X", expect="OK")
scenario("s04e", "connect_pins(R2:1, R9:1), R9 free", fixture("s04e"),
         ("connect_pins", dict(ref1="R2", pin1="1", ref2="R9", pin2="1")),
         pair=(("R2", "1"), ("R9", "1")), expect="OK")
scenario("s04f", "connect_pins(R2:1, R3:1), two unnamed member nets", fixture("s04f"),
         ("connect_pins", dict(ref1="R2", pin1="1", ref2="R3", pin2="1")),
         pair=(("R2", "1"), ("R3", "1")), expect="OK")

dump("s04_member_workflow")
