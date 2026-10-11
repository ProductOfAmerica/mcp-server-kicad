# probeB copy of pr57pt/probe-naming/n06b_hidden_power_demo_files.py: header swapped to the probeB shims, the design loop is M2/K, step() calls carry vid=/stepno=; fixture code unchanged.
"""N06b: hidden power_in pins on KiCad's own demo files (copied; KiCad-authored geometry).

interf_u: U5 = interf_u:628128 (hidden VCC 32 / GND 16, unit 0); U9 = 4003APG120 with 14 hidden
power pins in unit 1 (8 named GND, 6 named VCC).
sonde xilinx: U1 is placed as four units, each through a different lib entry (74LS125_1,
74LS125_6, 74LS125_7, sonde_xilinx:74LS125), and every entry draws hidden VCC 14 / GND 7 in its
unit 0, so pad U1:7 has four drawn copies.
"""

import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402
rlib.set_script("n06b_hidden_power_demo_files", __file__)
from nlib import BASE, W, dump, show_nets, step  # noqa: E402

DEMOS = hpaths.KICAD_DEMOS

cases = [
    ("interf_u", "interf_u.kicad_sch", [("U5", "16")], "VSS", [("U5", "16")]),
    ("interf_u", "interf_u.kicad_sch", [("U5", "16")], "GND", [("U5", "16")]),
    ("interf_u", "interf_u.kicad_sch", [("U9", "C4")], "GND", [("U9", "C4")]),
    ("interf_u", "interf_u.kicad_sch", [("U9", "GND")], "GND", [("U9", "C4")]),
    ("interf_u", "interf_u.kicad_sch", [("U9", "GND")], "VSS", [("U9", "C4")]),
    ("sonde xilinx", "sonde xilinx.kicad_sch", [("U1", "7")], "GND", [("U1", "7")]),
    ("sonde xilinx", "sonde xilinx.kicad_sch", [("U1", "7")], "VSS", [("U1", "7")]),
]
for i, (demo, sch, pins, N, targets) in enumerate(cases):
    for d in rlib.DESIGNS:
        dst = BASE / f"n06b_{i}" / d
        shutil.copytree(DEMOS / demo, dst)
        s = str(dst / sch)
        if i == 0 and d == "P":
            show_nets(s, f"{demo} fixture (U5 / U9 power nets)", focus=[("U5", "16"), ("U5", "32")])
        step(d, f"N06b {demo}: wire_pins_to_net {pins} -> {N}", s, [s], "wire_pins_to_net",
             W(pins, N, s), targets=targets, N=N, vid=str(i))

dump(__file__)
