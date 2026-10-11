"""External second-writer and hold reruns for M2 and K: verify-probe-composite/
v03_restore_semantics.py (L1-L3: CMP-07; R1, R2: CMP-01, also CMP-06).

vlib fixtures; global FOO on R90:1 and where C1:2 lands, so the cap call refuses (pin 2).
auto_place_decoupling_cap(C1, 100n, (152.4, 127), VCC, GND, project_path) in a child process.
  L1  loose: the second writer edits the SHEET on the sheet's change 2 (P's pin-1 write).
  L2  sub-sheet: the second writer edits the ROOT on the root's change 1 (P's index write).
  L3  sub-sheet: the second writer edits the ROOT 0.3 s after the child is ready (the
      original's own adaptation for M, which writes nothing on refusal).
      M2 and K write nothing on refusal either, so L1's and L2's triggers can never come; they
      also fire 0.3 s after ready (L3's rule) if the trigger has not come by then, so the edit
      still lands during the call. Which rule fired is recorded.
  R1  sub-sheet: the CHILD sheet held 3 s from its change 2 (P's pin-1 write).
  R2  sub-sheet: the ROOT held 3 s from its change 1 (P's index write).
      Run with the original triggers (no fallback): M2 and K never produce them.
  L1_success_extra (NOT an original variant, flagged extra): loose sheet WITHOUT the FOO
      labels, so the call succeeds; the second writer edits the sheet 0.3 s after ready,
      between the design's read and its write. Same hazard class as CMP-07, on the success path.
The second writer appends a text node with its own tmp + os.replace (v03.external_edit, copied).
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402

import cmplib as C  # noqa: E402

R.begin("composite", __file__, tag="cmp_restore_semantics")
S = "verify-probe-composite/v03_restore_semantics.py"
X = C.run_experiment

fx = C.FX["loose"]("v03_L1")
C.foo_at_pin2(fx)
X("CMP-07", "v03_restore_semantics:L1", S, fx, C.cap_kw_v(fx), action="edit", watch_key="sch",
  k=2, target_keys=["sch"], delay_s=0.3, check_mark=True,
  desc="second writer edits the sheet on its change 2, else 0.3 s after ready")

fx = C.FX["sub"]("v03_L2")
C.foo_at_pin2(fx)
X("CMP-07", "v03_restore_semantics:L2", S, fx, C.cap_kw_v(fx), action="edit", watch_key="root",
  k=1, target_keys=["root"], delay_s=0.3, check_mark=True,
  desc="second writer edits the ROOT on its change 1, else 0.3 s after ready")

fx = C.FX["sub"]("v03_L3")
C.foo_at_pin2(fx)
X("CMP-07", "v03_restore_semantics:L3", S, fx, C.cap_kw_v(fx), action="edit",
  target_keys=["root"], delay_s=0.3, check_mark=True,
  desc="second writer edits the ROOT 0.3 s after ready")

fx = C.FX["sub"]("v03_R1")
C.foo_at_pin2(fx)
X("CMP-01", "v03_restore_semantics:R1", S, fx, C.cap_kw_v(fx), also=["CMP-06"], action="hold",
  watch_key="sch", k=2, target_keys=["sch"], secs=3.0,
  desc="child sheet held 3 s from its change 2")

fx = C.FX["sub"]("v03_R2")
C.foo_at_pin2(fx)
X("CMP-01", "v03_restore_semantics:R2", S, fx, C.cap_kw_v(fx), also=["CMP-06"], action="hold",
  watch_key="root", k=1, target_keys=["root"], secs=3.0,
  desc="ROOT held 3 s from its change 1")

fx = C.FX["loose"]("v03_L1_success_extra")
X("CMP-07", "v03_restore_semantics:L1_success_extra", S, fx, C.cap_kw_v(fx), action="edit",
  target_keys=["sch"], delay_s=0.3, check_mark=True, extra={"extra_variant": True},
  desc="EXTRA: success path, second writer edits the sheet 0.3 s after ready")
R.end()
