"""Rerun of probe-composite/c05_placement_contacts.py (S1, S2: CMP-02; S3, S3r, S4: CMP-03;
S5, S6: CMP-08; S7: CMP-12) on M2 and K. c05b_rail_readers.py (CMP-03) builds S3 and S4 with the
same build() and makes the same call; its second reading (a copy split at interior junctions) is
what judge3's GUI-load emulation reads, so c05b is covered by the S3 and S4 rows here.

build() is copied verbatim. auto_place_decoupling_cap(Device:C, C1, 100n, (127, 101.6), VCC,
GND), no project_path, as the original.
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
    if sc == "S1":
        place(p, "Device:R", "R5", 127, 101.6)
        place(p, "Device:R", "R6", 50.8, 50.8)
    elif sc == "S2":
        place(p, "Device:R", "R5", 101.6, 101.6)       # R5:1 at (101.6, 97.79)
        wire(p, 101.6, 97.79, 127, 97.79)               # free end where C1:1 lands
    elif sc in ("S3", "S3r"):
        place(p, "power:VCC", "#PWR01", 76.2, 95.25, value="VCC")
        place(p, "Device:R", "R1", 88.9, 99.06)         # R1:1 at (88.9, 95.25)
        place(p, "Device:R", "R2", 165.1, 99.06)        # R2:1 at (165.1, 95.25)
        if sc == "S3":
            wire(p, 76.2, 95.25, 88.9, 95.25)
            wire(p, 88.9, 95.25, 165.1, 95.25)
        else:
            wire(p, 165.1, 95.25, 88.9, 95.25)
            wire(p, 88.9, 95.25, 76.2, 95.25)
    elif sc == "S4":
        place(p, "power:VCC", "#PWR01", 76.2, 97.79, value="VCC")
        place(p, "Device:R", "R2", 165.1, 101.6)        # R2:1 at (165.1, 97.79)
        wire(p, 76.2, 97.79, 165.1, 97.79)              # C1:1 will land on its interior
    elif sc == "S5":
        place(p, "power:GND", "#PWR02", 127, 105.41, value="GND")
        place(p, "Device:R", "R3", 76.2, 101.6)         # R3:2 at (76.2, 105.41)
        wire(p, 76.2, 105.41, 76.2, 110.49)
        place(p, "power:GND", "#PWR03", 76.2, 110.49, value="GND")
    elif sc == "S6":
        place(p, "Device:R", "R3", 76.2, 101.6)         # R3:2 at (76.2, 105.41)
        wire(p, 76.2, 105.41, 127, 105.41)              # free end where C1:2 lands
        label(p, "GND", 101.6, 105.41)                  # on the wire's interior
    elif sc == "S7":
        place(p, "power:GND", "#PWR01", 76.2, 97.79, value="GND")
        place(p, "Device:R", "R2", 165.1, 101.6)        # R2:1 at (165.1, 97.79)
        wire(p, 76.2, 97.79, 165.1, 97.79)              # C1:1 will land on its interior
    return p


R.begin("composite", __file__, tag="cmp_c05_placement_contacts")
S = "probe-composite/c05_placement_contacts.py"
CASE = {"S1": "CMP-02", "S2": "CMP-02", "S3": "CMP-03", "S3r": "CMP-03", "S4": "CMP-03",
        "S5": "CMP-08", "S6": "CMP-08", "S7": "CMP-12"}
KW = dict(lib_id="Device:C", reference="C1", value="100n", x=127, y=101.6, power_net="VCC",
          ground_net="GND")
for sc in ("S1", "S2", "S3", "S3r", "S4", "S5", "S6", "S7"):
    p = build(sc, f"c05_{sc}")
    note = "c05b_rail_readers.py makes this same call on this same build()" \
        if sc in ("S3", "S4") else ""
    C.run_case(CASE[sc], f"c05_placement_contacts:{sc}", "auto_place_decoupling_cap", p, KW,
               script=S, note=note)
R.end()
