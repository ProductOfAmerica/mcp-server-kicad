"""Rerun of probe-composite/c08_window_timing.py (CMP-01, also CMP-13) on M2 and K.

KiCad's tiny_tapeout demo (1.17 MB root sheet) copied; free_spot() (copied) picks the cap's
spot; auto_place_decoupling_cap(Device:C, C999, 100n, spot, VCC_T, GND_T, project_path).
  success  : as found
  refusal  : global FOO_CLASH at (x, y + 3.81), where C999:2 lands
Each is a judged call (rlib, M2 and K on byte-identical copies). The original measured the time
between the first and the last _atomic_write (the half-written window); every _atomic_write the
design performs is timed here too (hook on mcp_server_kicad._shared._atomic_write, which M2 and K
call through the module) and written to c08_timing.json.
"""

import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H, SCH  # noqa: E402

import cmplib as C  # noqa: E402


def free_spot(sch):
    """c08.free_spot, verbatim: a 1.27-grid point with no wire end, label or pin end within
    15 mm (coarse check)."""
    root = SCH._open_sch_cst(sch)[1]
    w, h, _ = SCH._page_size_cst(root)
    pts = []
    for tok in ("wire",):
        for n in root.find_all(tok):
            pts += SCH._wire_xys(n)
    for tok in ("label", "global_label", "hierarchical_label", "junction", "no_connect"):
        for n in root.find_all(tok):
            pts.append(SCH._node_xy(n))
    for s in root.find_all("symbol"):
        pts.append(SCH._node_xy(s))
    y = 25.4
    while y < h - 25:
        x = 25.4
        while x < w - 25:
            if all(abs(px - x) > 15 or abs(py - y) > 15 for px, py in pts):
                return x, y
            x += 12.7
        y += 12.7
    raise RuntimeError("no free spot")


SH = R._shared
REAL = SH._atomic_write
T: list = []


def aw(path, data):
    t0 = time.perf_counter()
    REAL(path, data)
    T.append((t0, time.perf_counter(), str(path)))


R.begin("composite", __file__, tag="cmp_c08_window_timing")
S = "probe-composite/c08_window_timing.py"
TIMING = []
for refuse in (False, True):
    tag = "refusal" if refuse else "success"
    fx = C.demo_fx(f"c08_{tag}")
    x, y = free_spot(fx["sch"])
    if refuse:
        H.label(fx["sch"], "FOO_CLASH", x, round(y + 3.81, 4), kind="global_label")
    kw = dict(lib_id="Device:C", reference="C999", value="100n", x=x, y=y, power_net="VCC_T",
              ground_net="GND_T", project_path=fx["pro"])
    T.clear()
    SH._atomic_write = aw
    try:
        recs = C.run_case("CMP-01", f"c08_window_timing:{tag}", "auto_place_decoupling_cap",
                          fx["sch"], kw, root=fx["root"], script=S, also=["CMP-13"],
                          note=f"spot ({x}, {y}) on the {Path(fx['sch']).stat().st_size / 1e6:.2f}"
                               " MB demo")
    finally:
        SH._atomic_write = REAL
    for dz in R.DESIGNS:  # exact copy-directory match (an "__H" substring would also match "__Hb")
        mine = [(a, b, Path(p).name) for a, b, p in T
                if any(part.endswith(f"__{dz}") for part in Path(p).parts)]
        window = (mine[-1][1] - mine[0][1]) * 1000 if len(mine) > 1 else 0.0
        row = {"variant": tag, "design": dz, "spot": [x, y], "status": recs[dz]["status"],
               "call_seconds": recs[dz]["seconds"], "writes": len(mine),
               "write_ms": [round((b - a) * 1000, 1) for a, b, _ in mine],
               "files": [n for _, _, n in mine], "window_first_to_last_ms": round(window, 1)}
        TIMING.append(row)
        print(f"  [timing] {row}", flush=True)
(R.OUT / "composite").mkdir(parents=True, exist_ok=True)
(R.OUT / "composite" / "c08_timing.json").write_text(json.dumps(TIMING, indent=1),
                                                     encoding="utf-8")
R.end()
