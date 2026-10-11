"""Time H on vme-wren/front-panel-io (stock demo copy, writes stubbed): one wire_pins_to_net call
with the whole project beside it, the hierarchy scan alone (_hier_records), and the same call on
the sheet copied alone (no .kicad_pro, so the scan sees only this sheet). Output: stdout (perf_h.out in
the pressure test); the timings are machine-dependent. Scratch files go under the run's crossban/tmp.
Run: uv run python tools/routing_harness/analysis/crossban/perf_h.py"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

HB = hpaths.suite_out("crossban")
TMP = HB / "tmp" / f"p{os.getpid()}"
TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TMP)
import h_impl as HI  # noqa: E402
from mcp_server_kicad import schematic  # noqa: E402

HI._shared._atomic_write = lambda path, data: None
src = Path(hpaths.KICAD_DEMOS) / "vme-wren"
d = Path(tempfile.mkdtemp(prefix="perf_")) / "vme-wren"
shutil.copytree(src, d)
alone = d.parent / "alone" / "front-panel-io.kicad_sch"
alone.parent.mkdir()
shutil.copy2(d / "front-panel-io.kicad_sch", alone)
for tag, sch in (("project", d / "front-panel-io.kicad_sch"), ("alone", alone)):
    kw = dict(pins=[{"reference": "R151", "pin": "2"}], label_text="XBANNET", schematic_path=str(sch))
    t = time.perf_counter()
    try:
        out = HI.wire_pins_to_net(**kw).split("\n")[0]
    except Exception as e:  # noqa: BLE001
        out = str(e).split("\n")[0][:100]
    print(f"{tag}: wire_pins_to_net {time.perf_counter() - t:.2f} s -> {out}")
    _, root, *_ = schematic._open_sch_cst(str(sch))
    m = HI._Model(root, str(sch))
    t = time.perf_counter()
    recs = m._hier_records()
    print(f"{tag}: hierarchy scan alone {time.perf_counter() - t:.2f} s, {len(recs)} symbols on other sheets")
shutil.rmtree(d.parent, ignore_errors=True)
