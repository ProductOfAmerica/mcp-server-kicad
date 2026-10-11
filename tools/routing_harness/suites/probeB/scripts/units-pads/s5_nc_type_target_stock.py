# probeB copy of pr57pt/probe-units-pads/s5_nc_type_target_stock.py: probeB import header in front, judge() calls carry vid=/stepno=; fixture code unchanged.
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("s5_nc_type_target_stock", __file__)
"""S5: a stock no_connect-TYPE pin as the routing target. 74xGxx:74LVC1G04 (non-derived) has hidden
pin 1 'NC' of electrical type no_connect at (93.98, 99.06) when placed at (101.6, 101.6)
(sem-symbols e8d). KiCad never connects an NC-type pin (digest G08/S12), so no geometry can put
pad 1 on a net. R1:1 is on local N.

  S5a wire_pins_to_net([U1:1], N)     by number
  S5b wire_pins_to_net([U1:NC], N)    by name
"""

from uplib import IMPLS, W, copies, dump, fresh, judge, nets_coded, place, probe_r, triplicate


def build(name):
    p = fresh(name)
    place(p, "74xGxx:74LVC1G04", "U1", 101.6, 101.6)
    probe_r(p, "R1", 50.8, 63.5, "N")
    return p


p0 = build("s5_layout")
print("74LVC1G04 pad 1 copies:", copies(p0, "U1", "1"))
print("fixture netlist:", [(nm, sorted(v)) for _c, nm, v in nets_coded(p0)])
for tag, pin in (("S5a", "1"), ("S5b", "NC")):
    base = build(tag.lower())
    for impl, q in triplicate(base).items():
        judge(f"{tag} wire U1:{pin} (no_connect type) -> N", impl, q, IMPLS[impl]["wire"],
              W(q, [("U1", pin)], "N"), targets=[("U1", "1")], N="N", vid=tag)
dump("s5_nc_type_target_stock.json")
