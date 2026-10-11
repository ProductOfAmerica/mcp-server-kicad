"""In-process crash reruns for M2 and K: probe-composite/c04_crash_windows.py (CMP-01, also
CMP-09, CMP-13), the c04_child.py method: the child runs one call and os._exit(9)s right after
its K-th completed _atomic_write.

For each configuration (cplib loose / root+pro / sub+pro; cap C1 at (127, 101.6), VCC/GND) and
design, a dry run (writes logged, no exit) counts the call's writes; then every K short of the
last write is killed (the original's rule), the on-disk state is recorded and judged, and the
same call is re-issued (retry). The original's refusal path (trigger T1, P's restores) is dry-run
for M2 and K too: a call that writes nothing has no write to die after.
Dry-run counts are written to crash_windows_dry.json.
"""

import json
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402

import cmplib as C  # noqa: E402

R.begin("composite", __file__, tag="cmp_crash_windows")
S = "probe-composite/c04_crash_windows.py"
DRY = []
for path_kind, add in (("success", None), ("refusal", C.add_t1)):
    for cfg in ("loose", "root", "sub"):
        tag = f"c04_{'ref_' if add else ''}{cfg}"
        fx = C.CONFIGS[cfg](tag)
        if add:
            add(fx)
        kw = C.cap_kw_c(fx)
        exp = C.Exp(fx, kw)
        totals = {}
        for dz in R.DESIGNS:
            d, e2, r2, kw2 = exp.design_copy(dz, "_dry")
            res = C.drive(dz, e2, kw2, mode="log")
            totals[dz] = len(res["child_log"])
            DRY.append({"path": path_kind, "cfg": fx["cfg"], "design": dz,
                        "writes": res["child_log"], "status": res["status"],
                        "msg": res["msg"][:200]})
            print(f"## {dz} {fx['cfg']} {path_kind}: {totals[dz]} writes {res['child_log']}"
                  f" -> {res['status']}", flush=True)
            shutil.rmtree(d, ignore_errors=True)
        for k in range(1, max(totals.values())):
            dzs = tuple(dz for dz in R.DESIGNS if k < totals[dz])
            C.run_experiment("CMP-01", f"c04_crash_windows:{path_kind}_{cfg}_k{k}", S, fx, kw,
                             also=["CMP-09", "CMP-13"], mode=f"exit:{k}", retry=True,
                             desc=f"os._exit right after write {k}", designs=dzs)
(R.OUT / "composite").mkdir(parents=True, exist_ok=True)
(R.OUT / "composite" / "crash_windows_dry.json").write_text(json.dumps(DRY, indent=1),
                                                            encoding="utf-8")
R.end()
