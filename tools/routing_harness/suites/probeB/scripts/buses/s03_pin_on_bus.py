# probeB copy of pr57pt/probe-buses/s03_pin_on_bus.py (header swapped to the probeB shims; fixture code unchanged).
"""S03: target pins whose net involves a bus (P, M and baseline B on copies of one fixture).

s03a   R1:1 sits on a bus ENDPOINT; the bus runs to R2:1 (unlabelled). KiCad: one unnamed net.
s03b   s03a plus a plain label 'SIG' on the bus interior; wire R1:1 to 'NEWNET'.
s03b2  s03b; wire R1:1 to 'SIG' (KiCad: already on SIG).
s03c   a bus passes THROUGH R1:1 (pin on the bus interior); bus carries 'SIG' and ends on R2:1.
s03d   V2f shape: R36:1 is both a bus endpoint (bus to R37:1, plain label 'SIG' on it) and a wire
       endpoint (wire to R38:1). Target R38:1, which the bus reaches only through R36:1.
s03e   target R3:1's wire ends at a junction ON a pre-split bus (BUS-12); the bus carries 'SIG'
       and ends on R2:1. kicad-cli 9 joins the wire to the bus subgraph (measured in BUS-12); the
       KiCad 9 GUI's cleanup is inferred to delete such a junction (digest BUS-12/verify note).
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s03_pin_on_bus", __file__)
from bl import *  # noqa: F401,F403
from bl import (bus, check_pin, dump, fresh, junction, label, pins_arg, place, scenario,
                show_nets, wire)

R1P1 = ("R1", "1")


def two_pin_bus(name, sig=False):
    p = fresh(name)
    place(p, "Device:R", "R1", 101.6, 101.6)
    place(p, "Device:R", "R2", 127, 101.6)
    check_pin(p, "R1", "1", (101.6, 97.79))
    check_pin(p, "R2", "1", (127.0, 97.79))
    bus(p, 101.6, 97.79, 127, 97.79)
    if sig:
        label(p, "SIG", 114.3, 97.79)
    return p


p = two_pin_bus("s03a")
scenario("s03a", "R1:1 on a bus ENDPOINT, bus to R2:1, unlabelled (one unnamed net in KiCad)", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="NEWNET")),
         targets=[R1P1], N="NEWNET", expect="OK",
         note="correct outcome: R1 and R2 on NEWNET (R2 comes along; same KiCad net)")

p = two_pin_bus("s03b", sig=True)
scenario("s03b", "s03a + plain label SIG on the bus; wire R1:1 to NEWNET", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="NEWNET")),
         targets=[R1P1], N="NEWNET", expect="REFUSED")

p = two_pin_bus("s03b2", sig=True)
scenario("s03b2", "s03b; wire R1:1 to SIG (already on SIG in KiCad)", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="SIG")),
         targets=[R1P1], N="SIG", expect="NOOP")

p = fresh("s03c")
place(p, "Device:R", "R1", 101.6, 101.6)
place(p, "Device:R", "R2", 127, 101.6)
check_pin(p, "R1", "1", (101.6, 97.79))
bus(p, 88.9, 97.79, 127, 97.79)                     # passes through R1:1, ends on R2:1
label(p, "SIG", 114.3, 97.79)
scenario("s03c", "bus passes THROUGH R1:1 (pin on bus interior, not connected in KiCad)", p,
         ("wire_pins_to_net", dict(pins=pins_arg(R1P1), label_text="NEWNET")),
         targets=[R1P1], N="NEWNET", expect=None,
         note="netlist-safe edit exists (a wire end on a bus interior joins nothing, BUS-1), but the"
              " pin visibly sits on a bus; a refusal is defensible")

p = fresh("s03d")
place(p, "Device:R", "R36", 101.6, 101.6)
place(p, "Device:R", "R37", 127, 101.6)
place(p, "Device:R", "R38", 88.9, 101.6)
check_pin(p, "R36", "1", (101.6, 97.79))
check_pin(p, "R37", "1", (127.0, 97.79))
check_pin(p, "R38", "1", (88.9, 97.79))
bus(p, 101.6, 97.79, 127, 97.79)
label(p, "SIG", 114.3, 97.79)
wire(p, 101.6, 97.79, 88.9, 97.79)
print("s03d fixture nets:")
show_nets(p, [("R36", "1"), ("R37", "1"), ("R38", "1")])
scenario("s03d", "R38:1 reaches bus 'SIG' only through R36:1 (bus end + wire end), wire R38:1 to NEWNET",
         p, ("wire_pins_to_net", dict(pins=pins_arg(("R38", "1")), label_text="NEWNET")),
         targets=[("R38", "1")], N="NEWNET", expect="REFUSED")

p = fresh("s03e")
place(p, "Device:R", "R2", 127, 100.33)
check_pin(p, "R2", "1", (127.0, 96.52))
bus(p, 88.9, 96.52, 101.6, 96.52)
bus(p, 101.6, 96.52, 127, 96.52)                    # pre-split at J = (101.6, 96.52); ends on R2:1
label(p, "SIG", 114.3, 96.52)
junction(p, 101.6, 96.52)
place(p, "Device:R", "R3", 88.9, 104.14)
check_pin(p, "R3", "1", (88.9, 100.33))
wire(p, 88.9, 100.33, 101.6, 100.33)
wire(p, 101.6, 100.33, 101.6, 96.52)                # ends at J
print("s03e fixture nets:")
show_nets(p, [("R2", "1"), ("R3", "1")])
scenario("s03e", "R3:1's wire ends at a junction on a pre-split bus carrying SIG; wire R3:1 to NEWNET",
         p, ("wire_pins_to_net", dict(pins=pins_arg(("R3", "1")), label_text="NEWNET")),
         targets=[("R3", "1")], N="NEWNET", expect="REFUSED",
         note="kicad-cli 9 reading; the 9 GUI is inferred to delete the junction (2 bus + 1 wire exits)")

dump("s03_pin_on_bus")
