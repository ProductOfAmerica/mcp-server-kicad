# probeB copy of pr57pt/probe-hierarchy/h07_duplicate_sheet_names.py (header swapped to the probeB shims; fixture code unchanged).
"""H07 (digest H5/H10 verifier): sibling sheets sharing one Sheetname alias local net names, because
KiCad 9 builds names from the human-readable sheet path and keys net codes by name.

(a) two child FILES a.kicad_sch and b.kicad_sch under sheets both named 'S' (control: 'SA'/'SB').
    b holds R21:1 on local label L. Call in a.kicad_sch: wire_pins_to_net([R11:1], 'L').
(b) one child file chan.kicad_sch used twice, both sheets named 'S' (control 'S1'/'S2').
    Call in chan.kicad_sch: wire_pins_to_net([R16:1], 'NEW') for a free pin (refs R16/R26).
The root has no wiring to the sheets (no sheet pins).
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("h07_duplicate_sheet_names", __file__)
from hlib import HERE, Log, Proj, netlist, run, show  # noqa: E402

log = Log(HERE / "h07_duplicate_sheet_names.out")


def build_files(dup):
    pj = Proj(f"h07a_{'dup' if dup else 'ctl'}")
    r = pj.root
    a = pj.child("a.kicad_sch")
    b = pj.child("b.kicad_sch")
    r.sheet("S" if dup else "SA", a, 127, 50.8)
    r.sheet("S" if dup else "SB", b, 127, 101.6)
    a.R(["R11"], 50.8, 50.8)
    (q1, _) = b.R(["R21"], 50.8, 50.8)
    b.wire(*q1, q1[0], round(q1[1] - 5.08, 4))
    b.label("L", q1[0], round(q1[1] - 5.08, 4))
    pj.write()
    return pj


def build_instances(dup):
    pj = Proj(f"h07b_{'dup' if dup else 'ctl'}")
    r = pj.root
    ch = pj.child("chan.kicad_sch")
    r.sheet("S" if dup else "S1", ch, 127, 50.8)
    r.sheet("S" if dup else "S2", ch, 127, 101.6)
    ch.R(["R16", "R26"], 50.8, 50.8)
    pj.write()
    return pj


for dup in (False, True):
    pj = build_files(dup)
    log(f"\n######## (a) two child files, sheet names {'S/S (duplicate)' if dup else 'SA/SB (control)'}")
    show(netlist(pj.root.path), title="baseline root netlist", out=log)
    run(log, f"h07a_{'dup' if dup else 'ctl'}", pj.dir, pj.root.fname, "a.kicad_sch",
        "wire_pins_to_net", dict(pins=[{"reference": "R11", "pin": "1"}], label_text="L"),
        allowed=[("R11", "1")],
        ideal="L is a new local name on a.kicad_sch: no merge with b.kicad_sch's L",
        pin_nodes=[("R11", "1"), ("R21", "1")])
for dup in (False, True):
    pj = build_instances(dup)
    log(f"\n######## (b) one child file, two instances named {'S/S (duplicate)' if dup else 'S1/S2 (control)'}")
    show(netlist(pj.root.path), title="baseline root netlist", out=log)
    run(log, f"h07b_{'dup' if dup else 'ctl'}", pj.dir, pj.root.fname, "chan.kicad_sch",
        "wire_pins_to_net", dict(pins=[{"reference": "R16", "pin": "1"}], label_text="NEW"),
        allowed=[("R16", "1")],
        ideal="a local label gives one net per instance; R16 and R26 must stay apart",
        pin_nodes=[("R16", "1"), ("R26", "1")])
log.save()
