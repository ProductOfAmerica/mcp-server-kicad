# probeB copy of pr57pt/probe-hierarchy/h06_child_hier_label.py (header swapped to the probeB shims; fixture code unchanged).
"""H06 (digest H7): wire_pins_to_net in a CHILD file (schematic_path = chan.kicad_sch) for a pin
whose subgraph carries hierarchical label H; single- and multi-instance child.

Root: S1 (and, multi, S2) use chan.kicad_sch, each with sheet pin H on the left edge.
  R1:1 -> S1.H with local label BUS_A on the wire; R2:1 -> S2.H with local label BUS_B.
Child chan.kicad_sch (refs R1x for S1, R2x for S2):
  RA (R11/R21) pin 1 -> (20.32, 97.79) = hierarchical label H (wire leaves sideways)
  RD (R14/R24) pin 1 = global label G (stub end); RE (R15/R25) pin 1 = power +5V (stub end)
  RF (R16/R26) pin 1 free.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h06_child_hier_label", __file__)
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h06_child_hier_label.out")


def build(multi):
    pj = Proj(f"h06_{'multi' if multi else 'single'}")
    r = pj.root
    ch = pj.child("chan.kicad_sch")
    sheets = [("S1", 50.8, "R1", "BUS_A")] + ([("S2", 101.6, "R2", "BUS_B")] if multi else [])
    for name, y, ref, lab in sheets:
        _, w = r.sheet(name, ch, 127, y, pins=[("H", "input", "left", 2)])
        (p1, _) = r.R(ref, 101.6, y + 12.7)
        r.wire(*p1, p1[0], w["H"][1])
        r.wire(p1[0], w["H"][1], *w["H"])
        r.label(lab, round(p1[0] + 10.16, 4), w["H"][1])  # on the horizontal wire interior
    refs = (lambda s: [f"R1{s}", f"R2{s}"]) if multi else (lambda s: [f"R1{s}"])
    (a1, _) = ch.R(refs("1"), 25.4, 101.6)
    ch.wire(*a1, 20.32, a1[1])
    ch.label("H", 20.32, a1[1], kind="hierarchical_label")
    (d1, _) = ch.R(refs("4"), 63.5, 101.6)
    ch.wire(*d1, 58.42, d1[1])
    ch.label("G", 58.42, d1[1], kind="global_label")
    (e1, _) = ch.R(refs("5"), 76.2, 101.6)
    ch.wire(*e1, 71.12, e1[1])
    if multi:
        ch.power("+5V", 71.12, e1[1], refs=["#PWR001", "#PWR002"])
    else:
        ch.power("+5V", 71.12, e1[1])
    ch.R(refs("6"), 88.9, 101.6)
    pj.write()
    return pj


for multi in (False, True):
    pj = build(multi)
    tag = "multi" if multi else "single"
    log(f"\n######## {tag}-instance child")
    show(netlist(pj.root.path), title="baseline root netlist", out=log)
    nodes = [("R1", "1"), ("R11", "1"), ("R14", "1"), ("R15", "1"), ("R16", "1")]
    if multi:
        nodes += [("R2", "1"), ("R21", "1"), ("R24", "1"), ("R25", "1"), ("R26", "1")]
    ra = [("R11", "1")] + ([("R21", "1")] if multi else [])
    rf = [("R16", "1")] + ([("R26", "1")] if multi else [])
    calls = [
        ("R11", "G", ra + [("R14", "1")], "refuse: H subgraph is named; a label G shorts every instance's parent net into G"),
        ("R11", "+5V", ra + [("R15", "1")], "refuse (as above, power)"),
        ("R11", "H", ra, "no-op: already on H"),
        ("R11", "FRESH", ra, "netlist-neutral (child label under a strong parent name); refusal is conservative"),
        ("R16", "G", rf + [("R14", "1")], "allow: RF joins global G in every instance (requested)"),
        ("R16", "H", rf + ra, "allow: RF joins its own instance's H chain (per instance)"),
    ]
    if multi:
        calls.append(("R21", "H", [("R21", "1")], "R21 is instance S2's reference of RA: a no-op"))
    for ref, N, allowed, ideal in calls:
        run(log, f"h06_{tag}_{ref}_{N.replace('+', 'p')}", pj.dir, pj.root.fname, "chan.kicad_sch",
            "wire_pins_to_net", dict(pins=[{"reference": ref, "pin": "1"}], label_text=N),
            allowed=allowed, ideal=ideal, pin_nodes=nodes)
log.save()
