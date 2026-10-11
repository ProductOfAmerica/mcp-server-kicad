"""Rerun of verify-probe-composite/v01_placement_contacts.py (S1, S2: CMP-02; S3, S3r, S3n,
S4: CMP-03; S5, S6: CMP-08) on M2 and K.

build() is copied verbatim. auto_place_decoupling_cap(Device:C, C1, 100n, (152.4, 127), VCC,
GND), no project_path, as the original. The original's second reading (a copy split at every
interior junction) is judge3's GUI-load emulation here.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H  # noqa: E402

import cmplib as C  # noqa: E402

place = C.place
label, wire = H.label, H.wire


def build(sc, tag):
    p = H.fresh(tag)
    place(p, "Device:R", "R90", 38.1, 38.1)
    if sc == "S1":
        place(p, "Device:R", "R5", 152.4, 127)
    elif sc == "S2":
        place(p, "Device:R", "R5", 127, 127)                 # R5:1 at (127, 123.19)
        wire(p, 127, 123.19, 152.4, 123.19)
    elif sc in ("S3", "S3r", "S3n"):
        place(p, "power:VCC", "#PWR01", 101.6, 120.65, value="VCC")
        place(p, "Device:R", "R1", 114.3, 124.46)            # R1:1 at (114.3, 120.65)
        place(p, "Device:R", "R2", 190.5, 124.46)            # R2:1 at (190.5, 120.65)
        wire(p, 101.6, 120.65, 114.3, 120.65)
        if sc == "S3r":
            wire(p, 190.5, 120.65, 114.3, 120.65)
        else:
            wire(p, 114.3, 120.65, 190.5, 120.65)
        if sc == "S3n":
            wire(p, 190.5, 120.65, 203.2, 120.65)
            place(p, "power:VCC", "#PWR02", 203.2, 120.65, value="VCC")
    elif sc == "S4":
        place(p, "power:VCC", "#PWR01", 101.6, 123.19, value="VCC")
        place(p, "Device:R", "R2", 190.5, 127)               # R2:1 at (190.5, 123.19)
        wire(p, 101.6, 123.19, 190.5, 123.19)
    elif sc == "S5":
        place(p, "power:GND", "#PWR02", 152.4, 130.81, value="GND")
        place(p, "Device:R", "R3", 127, 127)                 # R3:2 at (127, 130.81)
        wire(p, 127, 130.81, 127, 135.89)
        place(p, "power:GND", "#PWR03", 127, 135.89, value="GND")
    elif sc == "S6":
        place(p, "Device:R", "R3", 127, 127)                 # R3:2 at (127, 130.81)
        wire(p, 127, 130.81, 152.4, 130.81)
        label(p, "GND", 139.7, 130.81)
    return p


R.begin("composite", __file__, tag="cmp_v01_placement_contacts")
S = "verify-probe-composite/v01_placement_contacts.py"
CASE = {"S1": "CMP-02", "S2": "CMP-02", "S3": "CMP-03", "S3r": "CMP-03", "S3n": "CMP-03",
        "S4": "CMP-03", "S5": "CMP-08", "S6": "CMP-08"}
KW = dict(lib_id="Device:C", reference="C1", value="100n", x=152.4, y=127, power_net="VCC",
          ground_net="GND")
for sc in ("S1", "S2", "S3", "S3r", "S3n", "S4", "S5", "S6"):
    p = build(sc, f"v01_{sc}")
    C.run_case(CASE[sc], f"v01_placement_contacts:{sc}", "auto_place_decoupling_cap", p, KW,
               script=S)
R.end()
