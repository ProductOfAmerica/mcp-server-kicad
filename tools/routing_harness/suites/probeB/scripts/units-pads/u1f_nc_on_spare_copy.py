# probeB copy of pr57pt/probe-units-pads/u1f_nc_on_spare_copy.py: probeB import header in front, judge() calls carry vid=/stepno=; fixture code unchanged.
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("u1f_nc_on_spare_copy", __file__)
"""U1f: a no-connect FLAG on the spare copy of a unit-0 pin (digest S2: users put one there to silence
pin_not_connected; it does not clear different_unit_net). Same R_Network04_Split fixture as U1:
copy A (101.6, 97.79), copy B (127, 97.79), R1:1 on local COM.

  f1  NC flag on copy B, copy A free   wire_pins_to_net([RN1:1], COM)
  f2  NC flag on copy A, copy B free   wire_pins_to_net([RN1:1], COM)
For M's refusal in f1 the remedy it names is remove_no_connect; that is the repo's tool (M's 5.1
says the pad-identity change also fixes it), so the remedy is tried as the repo ships it.
"""

from uplib import (
    IMPLS, W, add_unit, copies, dump, fresh, judge, no_connect, place, probe_r, run, schematic,
    set_fp, sha, triplicate,
)


def build(name, nc_on):
    p = fresh(name)
    place(p, "Device:R_Network04_Split", "RN1", 101.6, 101.6)
    add_unit(p, "RN1", 2, 127, 101.6)
    probe_r(p, "R1", 50.8, 63.5, "COM")
    (ax, ay, _), (bx, by, _) = copies(p, "RN1", "1")
    no_connect(p, *((bx, by) if nc_on == "B" else (ax, ay)))
    set_fp(p, "RN1", "Resistor_THT:R_Array_SIP5")
    set_fp(p, "R1", "Resistor_SMD:R_0603_1608Metric")
    return p


for tag, nc_on in (("f1", "B"), ("f2", "A")):
    base = build(f"u1{tag}", nc_on)
    for impl, q in triplicate(base).items():
        rec = judge(f"U1{tag} NC flag on copy {nc_on}: RN1:1 -> COM", impl, q, IMPLS[impl]["wire"],
                    W(q, [("RN1", "1")], "COM"), targets=[("RN1", "1")], N="COM", board_refs=("RN1", "R1"), vid=tag, stepno=1)
        if rec is not None and rec["status"] == "REFUSED" and "no-connect" in rec["msg"]:
            h = sha(q)
            st, msg = run(schematic.remove_no_connect, dict(reference="RN1", pin_name="1", schematic_path=q))
            print(f"    M's named remedy, repo remove_no_connect(RN1, 1): {st}: {msg[:200]} | file changed: {sha(q) != h}")
            judge(f"U1{tag} retry after repo remove_no_connect(RN1, 1) ({st}, file changed: {sha(q) != h})", impl, q, IMPLS[impl]["wire"], W(q, [("RN1", "1")], "COM"), targets=[("RN1", "1")], N="COM", vid=tag, stepno=2, nsteps=2)

dump("u1f_nc_on_spare_copy.json")
