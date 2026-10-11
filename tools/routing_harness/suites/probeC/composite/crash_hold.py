"""External-actor reruns for M2 and K: verify-probe-composite/v02_crash_hold.py (CMP-01, CMP-09)
and the hold rows of probe-composite/c03_restore_failure.py (A1..A7; CMP-01, CMP-09, CMP-13).

Each call runs in a child process (cmp_child.py, no hooks); this process busy-polls the files
and kills the child, or holds plain read handles, as the originals did. Configurations, k values,
hold lengths and fixtures are the originals'.

v02 rows (vlib fixtures, cap C1 at (152.4, 127), VCC/GND, project_path = the fixture's):
  C1        loose, no actor (the poller alone);                         no retry (as original)
  K_cfg_kN  kill on the N-th observed change of the sheet: loose N=1,2; root N=1,2,3; sub N=1
            (B and P used those N; v02 ran M at N=1 only; M2/K run every N; a change that never
            comes means the call completes and nothing is killed)
  KR_cfg    P's refusal path (global FOO where C1:2 lands): loose N=2, root N=3
  H_cfg     a 3 s hold on the sheet from its first change: loose, root
  HR_loose  refusal path, 3 s hold from change 2
  HS_sub    sub-sheet, ROOT held 4 s from when the child is ready
  W_*       KiCad's tiny_tapeout demo (1.17 MB), C999 at (25.4, 25.4), VCC_W/GND_W: success
            and refusal (global FOO_W where C999:2 lands), no actor: the write window
  WK        the demo, kill on change 1 (success path);                  no retry (as original)
c03 hold rows (cplib fixtures, cap C1 at (127, 101.6), VCC/GND):
  A3        loose, sheet held 3 s from before the call (= c03 A1 for P; same experiment)
  A2_loose  loose, 3 s hold from the sheet's first change (P's "after the placement"; c03 A4,
            the same experiment for B, merged)
  A2_sub    sub-sheet, the child held 3 s from its first change
  A6        sub-sheet, ROOT held 3 s from before the call (= c03 A5 for P; merged)
  A7        loose, T1 refusal; c03 held the sheet as P's restore began. M2 and K have no restore;
            the hold is armed on the sheet's first change, which a refusal never makes
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

R.begin("composite", __file__, tag="cmp_crash_hold")
S2 = "verify-probe-composite/v02_crash_hold.py"
S3 = "probe-composite/c03_restore_failure.py"
X = C.run_experiment

# ---------------------------------------------------------------- v02
fx = C.FX["loose"]("v02_C1")
X("CMP-01", "v02_crash_hold:C1", S2, fx, C.cap_kw_v(fx), desc="C1 control, no actor")

for cfg, ks in (("loose", (1, 2)), ("root", (1, 2, 3)), ("sub", (1,))):
    for k in ks:
        fx = C.FX[cfg](f"v02_K_{cfg}_{k}")
        X("CMP-01", f"v02_crash_hold:K_{cfg}_k{k}", S2, fx, C.cap_kw_v(fx),
          also=["CMP-09"] if cfg == "sub" else [], action="kill", watch_key="sch", k=k,
          retry=True, desc=f"kill on the sheet's change {k}")

for cfg, k in (("loose", 2), ("root", 3)):
    fx = C.FX[cfg](f"v02_KR_{cfg}")
    C.foo_at_pin2(fx)
    X("CMP-01", f"v02_crash_hold:KR_{cfg}", S2, fx, C.cap_kw_v(fx), action="kill",
      watch_key="sch", k=k, retry=True,
      desc=f"refusal path (global FOO where C1:2 lands), kill on change {k}")

for cfg in ("loose", "root"):
    fx = C.FX[cfg](f"v02_H_{cfg}")
    X("CMP-01", f"v02_crash_hold:H_{cfg}", S2, fx, C.cap_kw_v(fx), action="hold",
      watch_key="sch", k=1, target_keys=["sch"], secs=3.0, retry=True,
      desc="3 s hold on the sheet from its first change")

fx = C.FX["loose"]("v02_HR_loose")
C.foo_at_pin2(fx)
X("CMP-01", "v02_crash_hold:HR_loose", S2, fx, C.cap_kw_v(fx), action="hold", watch_key="sch",
  k=2, target_keys=["sch"], secs=3.0, retry=True,
  desc="refusal path, 3 s hold on the sheet from its change 2")

fx = C.FX["sub"]("v02_HS")
X("CMP-01", "v02_crash_hold:HS_sub", S2, fx, C.cap_kw_v(fx), also=["CMP-09"], hold_start=True,
  target_keys=["root"], secs=4.0, retry=True, desc="ROOT held 4 s from when the child is ready")

fx = C.demo_fx("v02_W_success")
X("CMP-01", "v02_crash_hold:W_success", S2, fx,
  C.cap_kw_v(fx, x=25.4, y=25.4, pn="VCC_W", gn="GND_W", ref="C999"),
  desc="tiny_tapeout demo, success path, no actor (write window)")

fx = C.demo_fx("v02_W_refusal")
H.label(fx["sch"], "FOO_W", 25.4, 29.21, kind="global_label")
X("CMP-01", "v02_crash_hold:W_refusal", S2, fx,
  C.cap_kw_v(fx, x=25.4, y=25.4, pn="VCC_W", gn="GND_W", ref="C999"),
  desc="tiny_tapeout demo, refusal path (global FOO_W where C999:2 lands), no actor")

fx = C.demo_fx("v02_WK")
X("CMP-01", "v02_crash_hold:WK", S2, fx,
  C.cap_kw_v(fx, x=25.4, y=25.4, pn="VCC_W", gn="GND_W", ref="C999"), action="kill",
  watch_key="sch", k=1, desc="tiny_tapeout demo, kill on change 1")

# ---------------------------------------------------------------- c03 hold rows
fx = C.CONFIGS["loose"]("c03_A3")
X("CMP-13", "c03_restore_failure:A3", S3, fx, C.cap_kw_c(fx), hold_start=True,
  target_keys=["sch"], secs=3.0, desc="sheet held 3 s from before the call",
  note="c03 A1 (P) is the same experiment; merged")

for cfg in ("loose", "sub"):
    fx = C.CONFIGS[cfg](f"c03_A2_{cfg}")
    X("CMP-01", f"c03_restore_failure:A2_{cfg}", S3, fx, C.cap_kw_c(fx), action="hold",
      watch_key="sch", k=1, target_keys=["sch"], secs=3.0,
      desc="3 s hold on the edited sheet from its first change (P: right after the placement)",
      note="c03 A4 (B, loose) is the same experiment; merged" if cfg == "loose" else "")

fx = C.CONFIGS["sub"]("c03_A6")
X("CMP-09", "c03_restore_failure:A6", S3, fx, C.cap_kw_c(fx), hold_start=True,
  target_keys=["root"], secs=3.0, desc="sub-sheet, ROOT held 3 s from before the call",
  note="c03 A5 (P) is the same experiment; merged")

fx = C.CONFIGS["loose"]("c03_A7")
C.add_t1(fx)
X("CMP-01", "c03_restore_failure:A7", S3, fx, C.cap_kw_c(fx), action="hold", watch_key="sch",
  k=1, target_keys=["sch"], secs=3.0,
  desc="T1 refusal; hold armed on the sheet's first change (P: as its restore began)")
R.end()
