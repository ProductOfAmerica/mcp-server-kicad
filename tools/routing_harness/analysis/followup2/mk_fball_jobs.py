"""Job list for the probeC fallback-coverage rerun (RLIB_FB_ALL=1, RLIB_OUT_SUFFIX=_fball): the 27
scripts holding an H connect_pins refusal, or an E connect_pins refusal coded only [E:blocked], whose
fallbacks did not run in the main pass. Outputs go to <subdir>/<stem>.fball.out under the run's
probeC output directory. Writes RUN_DIR/meta/jobs_probeC_fball.json (ROUTING_HARNESS_RUN names the
run), in core/seqrun.py's job format: run it with
  uv run python tools/routing_harness/core/seqrun.py <that file> 3
after the main pass, then analysis/followup2/agg_fallback.py reads its parts_fball/ records."""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

C = hpaths.SUITES / "probeC"
PARTS = ["dec_d01", "dec_d02", "dec_d04", "dec_d07", "dec_d10", "dec_d14", "dec_v01", "dec_v02",
         "dec_v06", "dec_v09", "dec_v10", "gm_c1_connect_label_near", "gm_g1_diag_cross",
         "gm_g3_diag_through_pin", "gm_i1_pin_on_wire", "gm_m1_near_miss_names",
         "gm_o1_label_near_stub", "gm_v01_gm01_label_beside_stub",
         "gm_v05_gm05_connect_auto_label", "gm_v06_gm06_name_model_capture",
         "gm_v11_gm11_m_tolerance", "gm_v12_gm12_line_through_pin", "perf_probe_connect",
         "perf_probe_stock_C", "perf_vp_stock_A", "perf_vp_stock_C", "perf_vp_synthetic"]
PERF = {"perf_probe_connect": ["run_probe_connect.py"], "perf_probe_stock_C": ["run_probe_stock.py", "C"],
        "perf_vp_stock_A": ["run_vp_stock.py", "A"], "perf_vp_stock_C": ["run_vp_stock.py", "C"],
        "perf_vp_synthetic": ["run_vp_synthetic.py"]}
env = {"RLIB_FB_ALL": "1", "RLIB_OUT_SUFFIX": "_fball"}
jobs = []
for p in PARTS:
    if p in PERF:
        sub, args = "perf", PERF[p]
    elif p.startswith("dec_"):
        sub, args = "decisions", [p + ".py"]
    else:
        sub, args = "geometry-misc", [p + ".py"]
    assert (C / sub / args[0]).exists(), (sub, args)
    jobs.append({"cwd": f"suites/probeC/{sub}", "args": args, "out": f"probeC/{sub}/{p}.fball.out",
                 "env": env})
dst = hpaths.RUN_DIR / "meta" / "jobs_probeC_fball.json"
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(json.dumps(jobs, indent=1), encoding="utf-8")
print(len(jobs), "jobs ->", dst)
