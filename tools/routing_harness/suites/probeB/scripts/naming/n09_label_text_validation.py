# probeB copy of pr57pt/probe-naming/n09_label_text_validation.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N09: label_text validation (item 6). P's text names no validation; its prototype refuses an
empty label_text (I24, a builder addition). M refuses empty, '${', auto names and bus syntax.
B is the repo today, which is what P's text inherits for anything it does not mention.

R1 (101.6, 101.6) and R2 (127, 101.6) free; R5:1 carries 'D0', R6:1 carries 'D1' (plain labels).
R3:1 is wired to R4:1 (an unnamed net KiCad calls 'Net-(R3-Pad1)'); R8:1 carries 'SIG'.

  e1  wire [R1:1] -> ''            then  e2  wire [R2:1] -> ''   (two calls, separate pins)
  e3  wire [R8:1] -> ''            (R8:1 is already on 'SIG')
  v   wire [R1:1] -> 'D[0..1]'     bus-vector syntax, members D0/D1 exist as plain labels
  g   wire [R1:1] -> '{D0 D1}'     bus-group syntax
  n   wire [R1:1] -> 'Net-(R3-Pad1)'          auto name of the R3/R4 net
  u   wire [R1:1] -> 'unconnected-(R2-Pad1)'  auto name of free R2:1
  s   wire [R1:1] -> ' SIG'        leading space (does KiCad trim?)
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n09_label_text_validation", __file__)
from nlib import H, Fixture, W, dump, pos, show_nets, step  # noqa: E402


def build(fx):
    p = fx.path("n09.kicad_sch")
    H.project.create_schematic(p)
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 127, 101.6)
    for ref, x, txt in (("R5", 152.4, "D0"), ("R6", 177.8, "D1"), ("R8", 203.2, "SIG")):
        H.place(p, "Device:R", ref, x, 127)
        xx, yy = pos(p, ref, "1")
        H.wire(p, xx, yy, xx, 120.65)
        H.label(p, txt, xx, 120.65)
    H.place(p, "Device:R", "R3", 101.6, 152.4)
    H.place(p, "Device:R", "R4", 127, 152.4)
    H.wire(p, *pos(p, "R3", "1"), *pos(p, "R4", "1"))
    return p


focus = [("R1", "1"), ("R2", "1"), ("R3", "1"), ("R4", "1"), ("R5", "1"), ("R6", "1"),
         ("R8", "1")]
calls = {
    "e": [("R1", ""), ("R2", "")],
    "e3": [("R8", "")],
    "v": [("R1", "D[0..1]")],
    "g": [("R1", "{D0 D1}")],
    "n": [("R1", "Net-(R3-Pad1)")],
    "u": [("R1", "unconnected-(R2-Pad1)")],
    "s": [("R1", " SIG")],
}
for tag, seq in calls.items():
    fx = Fixture(f"n09_{tag}")
    p = build(fx)
    if tag == "e":
        show_nets(p, "fixture", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("n09.kicad_sch")
        for i, (ref, N) in enumerate(seq):
            step(d, f"N09{tag}{i + 1}: wire_pins_to_net [{ref}:1] -> {N!r}", s, [s],
                 "wire_pins_to_net", W([(ref, "1")], N, s), targets=[(ref, "1")], N=N, vid=tag, stepno=i + 1, nsteps=len(seq))
        show_nets(s, f"[{d}] {tag} after", focus=focus)

dump(__file__)
