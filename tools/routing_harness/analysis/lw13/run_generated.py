"""Run every generated layout (gen_layouts.py) through B, P and M.

Runs per layout: B/skill, P/skill(+replay), M/skill(+replay), P/adaptive, M/adaptive,
P/unbatched, M/unbatched. Results are saved incrementally to run_generated.json in the run's lw13
output directory (hpaths.suite_out("lw13")).
B is the repo's own tools at hpaths.TARGET, so the figure this chain gave the pressure test (B
shorted 16 of 36 layouts, LW-13) reproduces only with PT_REPO set to a git archive of 54160c1
(`git archive 54160c1 mcp_server_kicad tests`). Against a later checkout B is whatever that
checkout does.
usage: run_generated.py [layouts_per_ic=6]
Run: PT_REPO=<54160c1 export> uv run python tools/routing_harness/analysis/lw13/run_generated.py 6
"""

import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__, suites=("layouts_stock",))
import gen_layouts as G  # noqa: E402
import run_designs as RD  # noqa: E402

OUT = str(hpaths.suite_out("lw13") / "run_generated.json")
PLAN = (("B", "skill", False), ("P", "skill", True), ("M", "skill", True),
        ("P", "adaptive", False), ("M", "adaptive", False),
        ("P", "unbatched", False), ("M", "unbatched", False))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    ds, rej = G.layouts(n)
    print(f"{len(ds)} layouts ({rej} rejected by the reviewer checks)", flush=True)
    results = []
    t0 = time.time()
    for d in ds:
        for impl, policy, replay in PLAN:
            r = RD.run_design(d, impl, policy, replay=replay)
            r["knobs"] = d["knobs"]
            results.append(r)
        json.dump(results, open(OUT, "w", encoding="utf-8"), indent=1, default=list)
        print(f"## done {d['name']} after {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
