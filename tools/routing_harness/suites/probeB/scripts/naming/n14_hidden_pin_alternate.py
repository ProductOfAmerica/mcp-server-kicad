# probeB copy of pr57pt/probe-naming/n14_hidden_pin_alternate.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N14: hidden pins whose electrical type comes from a selected alternate.

KiCad 9.0.8: SCH_PIN::GetType() returns the alternate's type when one is selected
(sch_pin.cpp:279-297); IsGlobalPower() = GetType()==power_in && (hidden || power symbol)
(sch_pin.cpp:323-327); a non-power symbol's global pin is named m_libPin->GetName(), the primary
lib name (sch_pin.cpp:1621-1637). P's builder ignores alternates (I02); M uses them (I08).

Custom symbol ALTP (unit 1): pin 1 passive visible (left); pin 2 HIDDEN passive 'X' with alternate
'VCC' (power_in); pin 3 HIDDEN power_in 'Y' with alternate 'IO' (bidirectional).
U1 selects alternate VCC on pin 2 and IO on pin 3. R7:1 carries local 'X', R8:1 carries local 'N',
R9:1 carries local 'Y'.
  a  wire [U1:2] -> 'N'   (KiCad: U1:2 is a global 'X' pin, so this merges X and N)
  b  wire [U1:3] -> 'Y'   (KiCad: U1:3 is bidirectional, unnamed, so it is NOT already on Y)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n14_hidden_pin_alternate", __file__)
from nlib import BS, H, Fixture, W, _cst, dump, pos, show_nets, step  # noqa: E402

LIB = rlib.OUT / "libs" / "altp.kicad_sym"  # written below, like uplib.write_lib (OUT, never the source tree)
LIB.parent.mkdir(exist_ok=True)


def _pin(etype, x, y, ang, name, num, hide=False, alt=""):
    h = "\n\t\t\t\t(hide yes)" if hide else ""
    return (f"\t\t\t(pin {etype} line\n\t\t\t\t(at {x} {y} {ang})\n\t\t\t\t(length 2.54){h}\n"
            f"\t\t\t\t(name \"{name}\" (effects (font (size 1.27 1.27))))\n"
            f"\t\t\t\t(number \"{num}\" (effects (font (size 1.27 1.27))))\n{alt}\t\t\t)\n")


LIB.write_text(
    "(kicad_symbol_lib\n\t(version 20241209)\n\t(generator \"pt\")\n"
    "\t(symbol \"ALTP\"\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n"
    "\t\t(property \"Reference\" \"U\" (at 0 7.62 0) (effects (font (size 1.27 1.27))))\n"
    "\t\t(property \"Value\" \"ALTP\" (at 0 -7.62 0) (effects (font (size 1.27 1.27))))\n"
    "\t\t(property \"Footprint\" \"\" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n"
    "\t\t(property \"Datasheet\" \"\" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n"
    "\t\t(symbol \"ALTP_1_1\"\n"
    + _pin("passive", -7.62, 0, 0, "A", "1")
    + _pin("passive", 7.62, 2.54, 180, "X", "2", hide=True,
           alt="\t\t\t\t(alternate \"VCC\" power_in line)\n")
    + _pin("power_in", 7.62, -2.54, 180, "Y", "3", hide=True,
           alt="\t\t\t\t(alternate \"IO\" bidirectional line)\n")
    + "\t\t)\n\t)\n)\n",
    encoding="utf-8",
)


def select_alt(p, ref, num, alt):
    tree = _cst.parse(open(p, "rb").read())
    root = tree.lists[0]
    sym = [s for s in root.find_all("symbol") if BS._sym_property_cst(s, "Reference") == ref][0]
    pin = [q for q in sym.find_all("pin") if q.atoms[1].text == num][0]
    pin.append_child(_cst.parse(f'(alternate "{alt}")'.encode()).lists[0], b" ")
    open(p, "wb").write(_cst.serialize(tree))


def build(fx):
    p = fx.path("n14.kicad_sch")
    H.project.create_schematic(p)
    BS.place_component(lib_id="ALTP", reference="U1", value="ALTP", x=101.6, y=101.6,
                       symbol_lib_path=str(LIB), schematic_path=p)
    select_alt(p, "U1", "2", "VCC")
    select_alt(p, "U1", "3", "IO")
    for ref, x, txt in (("R7", 152.4, "X"), ("R8", 177.8, "N"), ("R9", 203.2, "Y")):
        H.place(p, "Device:R", ref, x, 127)
        xx, yy = pos(p, ref, "1")
        H.wire(p, xx, yy, xx, 120.65)
        H.label(p, txt, xx, 120.65)
    return p


focus = [("U1", "2"), ("U1", "3"), ("R7", "1"), ("R8", "1"), ("R9", "1")]
for tag, pin_, N in (("a", "2", "N"), ("b", "3", "Y")):
    fx = Fixture(f"n14_{tag}")
    p = build(fx)
    show_nets(p, f"{tag} fixture", focus=focus)
    print("    U1 pins (KiCad transform):", {k: v for k, v in
                                               __import__("nlib").M.pin_positions(p, "U1").items()})
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("n14.kicad_sch")
        step(d, f"N14{tag}: wire_pins_to_net [U1:{pin_}] -> {N}", s, [s], "wire_pins_to_net",
             W([("U1", pin_)], N, s), targets=[("U1", pin_)], N=N, vid=tag)
        show_nets(s, f"[{d}] {tag} after", focus=focus)

dump(__file__)
