# probeB copy of pr57pt/probe-buses/s05_shared_entry_point.py (header swapped to the probeB shims; fixture code unchanged).
"""S05: two bus entries leave ONE bus point (the '<' shape KiCad's own demos use, e.g.
kit-dev-coldfire-xilinx_5213/in_out_conn and video/bus_pci). KiCad never joins two bus-wire entries
(9.0.8 sch_bus_entry.cpp:597-599) and, where a bus passes, an entry joins only bus-layer items
(9.0.8 connection_graph.cpp:1344-1355), so the two member wires stay separate nets.
P's rule 1 lists bus-entry ends among the points that "share a point" and so joins them; M's
over-approximate model (5.2) joins every exact coincidence and every item on a bus.

Bus x=76.2 split at the shared point S=(76.2, 93.98); entry EU from S up-right to (78.74, 91.44),
entry ED from S down-right to (78.74, 96.52).

s05a  WU labelled D1 -> R1:1; WD unlabelled -> R2:1.  wire_pins_to_net(R2:1, 'D2')  expected OK
s05b  same fixture.                                    wire_pins_to_net(R2:1, 'D1')  expected OK
s05c  same fixture.                                    connect_pins(R2:1, R1:1)      expected OK
s05d  both members unlabelled; WU routed so that R2:1's 1x stub end lands on WU's interior
      (WU's last segment drawn from R1 toward the bus so kicad-cli's junction truncation keeps
      R1's side).                                      wire_pins_to_net(R2:1, 'N')   expected OK
s05u  s05a with the bus label removed (M's literal 5.4 no-op reads the over-approximate model).
                                                       wire_pins_to_net(R2:1, 'D1')  expected OK
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s05_shared_entry_point", __file__)
from bl import *  # noqa: F401,F403
from bl import (bus, bus_entry, check_pin, dump, fresh, label, pins_arg, place, scenario,
                show_nets, wire)


def fixture(name, bus_label=True, d1=True, routed=False):
    p = fresh(name)
    bus(p, 76.2, 81.28, 76.2, 93.98)
    bus(p, 76.2, 93.98, 76.2, 106.68)
    if bus_label:
        label(p, "D[0..3]", 76.2, 86.36, rot=90)
    bus_entry(p, 76.2, 93.98, 2.54, -2.54)            # EU -> (78.74, 91.44)
    bus_entry(p, 76.2, 93.98, 2.54, 2.54)             # ED -> (78.74, 96.52)
    if not routed:
        place(p, "Device:R", "R1", 119.38, 95.25)
        check_pin(p, "R1", "1", (119.38, 91.44))
        wire(p, 78.74, 91.44, 119.38, 91.44)
        if d1:
            label(p, "D1", 83.82, 91.44)
    else:
        place(p, "Device:R", "R1", 119.38, 97.79)
        check_pin(p, "R1", "1", (119.38, 93.98))
        wire(p, 78.74, 91.44, 88.9, 91.44)
        wire(p, 88.9, 91.44, 88.9, 93.98)
        wire(p, 119.38, 93.98, 88.9, 93.98)           # drawn from R1 toward the bus
    place(p, "Device:R", "R2", 106.68, 100.33)
    check_pin(p, "R2", "1", (106.68, 96.52))
    wire(p, 78.74, 96.52, 106.68, 96.52)
    return p


p = fixture("s05_probe")
print("fixture nets (s05a/b/c):")
show_nets(p, [("R1", "1"), ("R2", "1")])
p = fixture("s05_probe_d", d1=False, routed=True)
print("fixture nets (s05d):")
show_nets(p, [("R1", "1"), ("R2", "1")])

W = "wire_pins_to_net"
R2P1 = ("R2", "1")
scenario("s05a", "'<' entries at one bus point; unlabelled member R2:1 -> D2", fixture("s05a"),
         (W, dict(pins=pins_arg(R2P1), label_text="D2")), targets=[R2P1], N="D2", expect="OK")
scenario("s05b", "same; R2:1 -> D1 (the OTHER member's name; R2 is not on D1 in KiCad)",
         fixture("s05b"), (W, dict(pins=pins_arg(R2P1), label_text="D1")),
         targets=[R2P1], N="D1", expect="OK")
scenario("s05c", "same; connect_pins(R2:1, R1:1) (KiCad: separate nets, one named D1)",
         fixture("s05c"), ("connect_pins", dict(ref1="R2", pin1="1", ref2="R1", pin2="1")),
         pair=(R2P1, ("R1", "1")), expect="OK")
scenario("s05d", "both members unlabelled; R2:1's 1x stub end lands on the other member wire",
         fixture("s05d", d1=False, routed=True), (W, dict(pins=pins_arg(R2P1), label_text="N")),
         targets=[R2P1], N="N", expect="OK")
scenario("s05u", "s05b without the bus label", fixture("s05u", bus_label=False),
         (W, dict(pins=pins_arg(R2P1), label_text="D1")), targets=[R2P1], N="D1", expect="OK")

dump("s05_shared_entry_point")
