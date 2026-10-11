# probeB variant built from pr57pt/probe-buses/c05_partB_fullproject.py.
# c05 itself makes no tool call: it re-reads s09 Part B's pairs from the FULL project export (root).
# Here the same Part B requests (wire pin A to B's member name, connect_pins(A, B)) run on the
# sub-sheet inside a full copy of each demo project, judged on the project ROOT. The pairs are
# c05's; label_text for 'wire A to B' is s09's (B's net name from the standalone sub-sheet export,
# leading '/' stripped), recomputed here the same way.
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib  # noqa: E402

rlib.set_script("c05_partB_fullproject", __file__)

import os  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from bl import nets_coded, strip  # noqa: E402

DEMOS = hpaths.KICAD_DEMOS
for proj, root, sub, pairs in (
    ("kit-dev-coldfire-xilinx_5213", "kit-dev-coldfire-xilinx_5213.kicad_sch", "in_out_conn.kicad_sch",
     [(("MCU_PORT201", "10"), ("MCU_PORT201", "14")), (("MCU_PORT201", "37"), ("MCU_PORT201", "33")),
      (("MCU_PORT201", "39"), ("MCU_PORT201", "35"))]),
    ("video", "video.kicad_sch", "bus_pci.kicad_sch",
     [(("U11", "118"), ("U11", "120")), (("U11", "116"), ("U11", "119"))]),
):
    d = Path(tempfile.mkdtemp(prefix="c05_")) / proj
    shutil.copytree(DEMOS / proj, d)
    sd = Path(tempfile.mkdtemp(prefix="c05s_"))
    shutil.copy2(DEMOS / proj / sub, sd / sub)
    alone = nets_coded(str(sd / sub))
    where_alone = {n: name for _, name, v in alone for n in v}
    full = nets_coded(str(d / root))
    os.remove(str(d / root) + ".pt.netlist.xml")
    where_full = {n: name for _, name, v in full for n in v}
    for a, b in pairs:
        N = strip(where_alone[b])
        note = (f"{a[0]}:{a[1]} on {where_full.get(a)!r}, {b[0]}:{b[1]} on {where_full.get(b)!r}"
                f" (full-project root export); N from the standalone export {where_alone[b]!r}")
        tag = f"c05:{proj}:{a[0]}:{a[1]}-{b[0]}:{b[1]}"
        rlib.run_pair(vid=f"{tag}:wire_A_to_B", src_dir=d, root_rel=root, edited_rel=sub,
                      tool="wire_pins_to_net",
                      kw=dict(pins=[{"reference": a[0], "pin": a[1]}], label_text=N), note=note)
        rlib.run_pair(vid=f"{tag}:connect_A_B", src_dir=d, root_rel=root, edited_rel=sub,
                      tool="connect_pins", kw=dict(ref1=a[0], pin1=a[1], ref2=b[0], pin2=b[1]),
                      note=note)
rlib.flush()
