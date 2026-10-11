# probeB copy of pr57pt/probe-naming/n08_text_variables.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N08: text variables (item 5). KiCad joins on RESOLVED text (digest N12, v16a); the project file
hp.kicad_pro defines VAR = 'SIG'. '${#}' is KiCad's built-in page number ('1' on the root).

R1:1 (101.6, 97.79) auto stub up; R1:1's own wire runs left to (93.98, 97.79) where a label may sit.
R2:1 (127, 97.79) wire up to (127, 92.71) with a label. R7:1 carries a label too.

  t1  R1:1 net: label '${VAR}';   R2:1 net: label 'SIG'.   wire [R1:1] -> 'SIG'   (already on it)
  t2  R1:1 free;                  R2:1 net: label 'SIG'.   wire [R1:1] -> '${VAR}' (= SIG, join R2)
  t3  R1:1 net: label 'SIG';                               wire [R1:1] -> '${VAR}' (already on it)
  t4  R1:1 net: label '${VAR}';   R2:1 net: label 'SIG'.   connect_pins(R1:1, R2:1) (already joined)
  t5  R1:1 free;                  R7:1 net: label 'N1'.    wire [R1:1] -> 'N${#}' (= N1, join R7)
  t6  R1:1 net: label 'N${#}';    R7:1 net: label 'N1'.    wire [R1:1] -> 'N1'    (already on it)
"""

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n08_text_variables", __file__)
from nlib import BP, CP, H, Fixture, W, dump, pos, show_nets, step  # noqa: E402


def build(fx, tag):
    BP.create_project(str(fx.src), "hp")
    pro = Path(fx.src / "hp.kicad_pro")
    data = json.loads(pro.read_text(encoding="utf-8"))
    data["text_variables"] = {"VAR": "SIG"}
    pro.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    p = str(fx.src / "hp.kicad_sch")
    H.place(p, "Device:R", "R1", 101.6, 101.6)
    H.place(p, "Device:R", "R2", 127, 101.6)
    H.place(p, "Device:R", "R7", 152.4, 127)
    x1, y1 = pos(p, "R1", "1")
    x2, y2 = pos(p, "R2", "1")
    x7, y7 = pos(p, "R7", "1")
    own = {"t1": "${VAR}", "t3": "SIG", "t4": "${VAR}", "t6": "N${#}"}.get(tag)
    if own:
        H.wire(p, x1, y1, 93.98, y1)
        H.label(p, own, 93.98, y1)
    if tag in ("t1", "t2", "t4"):
        H.wire(p, x2, y2, x2, 92.71)
        H.label(p, "SIG", x2, 92.71)
    if tag in ("t5", "t6"):
        H.wire(p, x7, y7, x7, 120.65)
        H.label(p, "N1", x7, 120.65)
    return p


cases = {"t1": "SIG", "t2": "${VAR}", "t3": "${VAR}", "t4": None, "t5": "N${#}", "t6": "N1"}
focus = [("R1", "1"), ("R2", "1"), ("R7", "1")]
for tag, N in cases.items():
    fx = Fixture(f"n08_{tag}")
    p = build(fx, tag)
    show_nets(p, f"{tag} fixture", focus=focus)
    for d in rlib.DESIGNS:
        c = fx.clone(d)
        s = c.path("hp.kicad_sch")
        if N is None:
            step(d, f"N08{tag}: connect_pins R1:1 ('${{VAR}}') -> R2:1 ('SIG')", s, [s],
                 "connect_pins", CP("R1", "1", "R2", "1", s), pair=(("R1", "1"), ("R2", "1")), vid=tag)
        else:
            # the judge compares resolved names, so pass the resolved N
            res = {"${VAR}": "SIG", "N${#}": "N1"}.get(N, N)
            step(d, f"N08{tag}: wire_pins_to_net [R1:1] -> {N!r} (resolves to {res!r})", s, [s],
                 "wire_pins_to_net", W([("R1", "1")], N, s), targets=[("R1", "1")], N=res, vid=tag)
        show_nets(s, f"[{d}] {tag} after", focus=focus)

dump(__file__)
