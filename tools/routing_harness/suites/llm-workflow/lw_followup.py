"""Rerun of probe-llm-workflow/followup_probe.py (F1, F2, F3) and
verify-probe-llm-workflow/v_lw02_autolabel.py (G1, G2) on M2, H, Hb and E (follow-up 2 copy of the follow-up 1 script).

followup_probe base: U1 STM32G030F6Px (101.6, 101.6), R1 (139.7, 86.36) rot 90, TP1 (152.4, 63.5),
R2 (152.4, 109.22) rot 90, J1 Conn_01x02 (177.8, 109.22) rot 180, C1 (139.7, 124.46).
  F1 connect_pins(U1:PA0, R1:1); wire_pins_to_net([TP1:1, R1:1], 'LED_DRV')
  F2 connect_pins(U1:PA0, R1:1); wire_pins_to_net([U1:PA0], 'LED_DRV')
  F3 connect_pins(J1:1, R2:1); connect_pins(U1:PA1, C1:1); connect_pins(R2:1, U1:PA1)
v_lw02 base: U1 (101.6, 101.6), R1 (144.78, 95.25), TP1 (157.48, 71.12).
  G1 connect_pins(U1:PA0, R1:1); wire_pins_to_net([TP1:1, R1:1], 'LED_DRV')
  G2 connect_pins(U1:PA0, R1:1); wire_pins_to_net([U1:PA0], 'LED_DRV')
  G3 (P only in the original: the recovery P's refusal text points at) is not run.
No branching: each design runs the fixed calls on its own evolving file.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H  # noqa: E402

import lwlib as LW  # noqa: E402

R.begin(LW.CAT, __file__, tag="lw_followup" + "".join("_" + a for a in sys.argv[1:]))


def fu_base(tag):
    p = H.fresh(tag)
    H.place(p, "MCU_ST_STM32G0:STM32G030F6Px", "U1", 101.6, 101.6, value="STM32G030")
    H.place(p, "Device:R", "R1", 139.7, 86.36, rot=90, value="330")
    H.place(p, "Connector:TestPoint", "TP1", 152.4, 63.5, value="TP")
    H.place(p, "Device:R", "R2", 152.4, 109.22, rot=90, value="1k")
    H.place(p, "Connector_Generic:Conn_01x02", "J1", 177.8, 109.22, rot=180, value="IN")
    H.place(p, "Device:C", "C1", 139.7, 124.46, value="100n")
    return p


def conn(a, b):
    return ("connect_pins", dict(ref1=a[0], pin1=a[1], ref2=b[0], pin2=b[1]),
            f"connect {a[0]}:{a[1]}->{b[0]}:{b[1]}")


def wire(pins, N):
    return ("wire_pins_to_net", dict(pins=LW.pins_kw(pins), label_text=N), f"wire {N} {pins}")


def run(case, also, scen, base, steps, script):
    LW.header(scen)
    seqs = {}
    for dz in R.DESIGNS:
        s = LW.Seq(case, scen, dz, base, also=also, script=script)
        for tool, kw, label in steps:
            s.call(tool, kw, label)
        seqs[dz] = s
    LW.close_all(seqs)


SC = {
    "F1": ("LW-02", ["LW-08"], [conn(("U1", "PA0"), ("R1", "1")),
                                wire([("TP1", "1"), ("R1", "1")], "LED_DRV")]),
    "F2": ("LW-02", [], [conn(("U1", "PA0"), ("R1", "1")), wire([("U1", "PA0")], "LED_DRV")]),
    "F3": ("LW-01", ["LW-02"], [conn(("J1", "1"), ("R2", "1")), conn(("U1", "PA1"), ("C1", "1")),
                                conn(("R2", "1"), ("U1", "PA1"))]),
}
for sc, (case, also, steps) in SC.items():
    run(case, also, f"followup_probe:{sc}", fu_base(f"fu_{sc}"), steps,
        LW.PROBE + "followup_probe.py")


def g_base(tag):
    p = H.fresh(tag)
    H.place(p, "MCU_ST_STM32G0:STM32G030F6Px", "U1", 101.6, 101.6, value="STM32G030")
    H.place(p, "Device:R", "R1", 144.78, 95.25, value="330")
    H.place(p, "Connector:TestPoint", "TP1", 157.48, 71.12, value="TP")
    return p


GC = {
    "G1": ("LW-02", ["LW-08"], [conn(("U1", "PA0"), ("R1", "1")),
                                wire([("TP1", "1"), ("R1", "1")], "LED_DRV")]),
    "G2": ("LW-02", [], [conn(("U1", "PA0"), ("R1", "1")), wire([("U1", "PA0")], "LED_DRV")]),
}
for g, (case, also, steps) in GC.items():
    run(case, also, f"v_lw02_autolabel:{g}", g_base(f"v02_{g}"), steps,
        LW.VERIFY + "v_lw02_autolabel.py")
R.end()
