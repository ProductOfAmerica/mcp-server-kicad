"""Follow-up 3: rebuild the probeA fixtures where H's dup_ref fired (PI-15, the true duplicates)
with probeA's own job builders (suites/probeA rlib.Job, the code that made the batch's records), and
copy each fixture tree to <run>/fixtures/dupref/probeA__<variant>/ for the ERC oracle and the rule
check.
Run: uv run python tools/routing_harness/analysis/dupref/build_probeA_fixtures.py [--run RUN]
         > <run>/analysis/build_probeA_fixtures.out
"""
import argparse
import json
import shutil
import tempfile
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__, suites=("probeA",))
sys.path.insert(0, str(hpaths.SUITES / "probeA" / "pin-identity"))  # pi_jobs

_ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
_ap.add_argument("--run", default=hpaths.RUN, metavar="RUN",
                 help="run name or directory (default: ROUTING_HARNESS_RUN, else 'default')")
RUN_DIR = hpaths.run_dir(_ap.parse_args().run)
if not RUN_DIR.is_dir():
    sys.exit(f"run directory not found: {RUN_DIR}")
OUT = RUN_DIR / "fixtures" / "dupref"

import rlib as R  # noqa: E402

import pi_jobs  # noqa: E402

VARIANTS = ["m_edges/13_R1", "m_edges/13_RQ", "v10_dupref/C1", "v10_dupref/CQ"]
JOBS = {j.variant: j for j in pi_jobs.JOBS}
J = R.J
for v in VARIANTS:
    job = JOBS[v]
    jd = Path(tempfile.mkdtemp(prefix="dr_", dir=R.SCR))
    R._JOB["dir"], R._JOB["category"] = jd, "pin-identity"
    hb, jt = R.H.BASE, J.TMP
    R.H.BASE = jd / "h"
    R.H.BASE.mkdir()
    J.TMP = jd / "j3"
    J.TMP.mkdir()
    try:
        built = job.build(*job.args)
        root, edited = (built if isinstance(built, tuple) else (built, built))
        root, edited = Path(root), Path(edited)
        dst = OUT / ("probeA__" + v.replace("/", "__"))
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(root.parent, dst)
        step = job.steps[0]
        info = {"variant": v, "cases": job.cases, "root": root.name,
                "edited": str(edited.resolve().relative_to(root.parent.resolve())),
                "step": {k: step[k] for k in ("kind", "pins", "N", "a", "b") if k in step}}
        (dst / "_fixture.json").write_text(json.dumps(info, indent=1, default=str), encoding="utf-8")
        print(json.dumps(info, default=str))
    finally:
        R.H.BASE, J.TMP = hb, jt
        R._JOB["dir"] = None
        shutil.rmtree(jd, ignore_errors=True)
