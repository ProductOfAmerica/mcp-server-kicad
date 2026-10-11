# probeB copy of pr57pt/probe-hierarchy/h11_decoupling_cap_subsheet.py (header swapped to the probeB shims; fixture code unchanged).
"""H11: auto_place_decoupling_cap on a sub-sheet (schematic_path = child, project_path = .kicad_pro).

11a single-instance child with NO +5V/GND items; the root holds power +5V and GND symbols.
    power_net='+5V', ground_net='GND'. Does the cap reach the rails?
11b same child but it holds power +5V and GND symbols on other parts (control: should join).
11c two-instance child (S1, S2) with +5V/GND symbols: is the cap placed and wired in BOTH
    instances of the root netlist?
11d refusal path: a lone local label OTHER sits exactly where C1 pin 1 lands. Every file (child
    AND root) must stay byte-identical.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h11_decoupling_cap_subsheet", __file__)
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h11_decoupling_cap_subsheet.out")


def build(tag, child_power, multi=False, blocker=False):
    pj = Proj(f"h11_{tag}")
    r = pj.root
    ch = pj.child("c.kicad_sch")
    r.sheet("S1" if multi else "C", ch, 152.4, 25.4)
    if multi:
        r.sheet("S2", ch, 152.4, 76.2)
    (a1, a2) = r.R("R9", 50.8, 50.8)
    r.wire(*a1, a1[0], round(a1[1] - 5.08, 4))
    r.power("+5V", a1[0], round(a1[1] - 5.08, 4))
    r.wire(*a2, a2[0], round(a2[1] + 5.08, 4))
    r.power("GND", a2[0], round(a2[1] + 5.08, 4))
    refs = (lambda b: [f"{b}1", f"{b}2"]) if multi else (lambda b: [f"{b}1"])
    if child_power:
        (b1, b2) = ch.R(refs("R3"), 101.6, 76.2)
        ch.wire(*b1, b1[0], round(b1[1] - 5.08, 4))
        ch.wire(*b2, b2[0], round(b2[1] + 5.08, 4))
        if multi:
            ch.power("+5V", b1[0], round(b1[1] - 5.08, 4), refs=["#PWR011", "#PWR012"])
            ch.power("GND", b2[0], round(b2[1] + 5.08, 4), refs=["#PWR013", "#PWR014"])
        else:
            ch.power("+5V", b1[0], round(b1[1] - 5.08, 4))
            ch.power("GND", b2[0], round(b2[1] + 5.08, 4))
    if blocker:
        ch.label("OTHER", 50.8, 72.39)
    pj.write()
    return pj


CAP = dict(lib_id="Device:C", reference="C1", value="100n", x=50.8, y=76.2, power_net="+5V",
           ground_net="GND")
cases = [
    ("11a_empty_child", dict(child_power=False), "the cap's pins should end on the project's +5V and GND"),
    ("11b_child_has_power", dict(child_power=True), "C1:1 on +5V, C1:2 on GND (requested)"),
    ("11c_multi_instance", dict(child_power=True, multi=True), "the cap in both instances, each on +5V/GND"),
    ("11d_refusal_rollback", dict(child_power=True, blocker=True), "refuse; child and root byte-identical"),
]
for tag, kw, ideal in cases:
    pj = build(tag, **kw)
    log(f"\n######## {tag}")
    show(netlist(pj.root.path), title="baseline root netlist", out=log)
    pro = str(Path(pj.dir) / f"{pj.name}.kicad_pro")
    run(log, f"h11_{tag}_run", pj.dir, pj.root.fname, "c.kicad_sch", "auto_place_decoupling_cap",
        dict(CAP, project_path=pro), allowed=[("R9", "1"), ("R9", "2")], ideal=ideal,
        pin_nodes=[("R9", "1"), ("R9", "2"), ("C1", "1"), ("C1", "2"), ("C2", "1"), ("C2", "2")])
log.save()
