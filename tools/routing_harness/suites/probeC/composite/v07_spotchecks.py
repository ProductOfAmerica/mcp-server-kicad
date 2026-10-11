"""Rerun of verify-probe-composite/v07_spotchecks.py OK-1 (CMP-01) on M2 and K.

OK-1: the ordinary refusal path, nothing interfering: a pin-1 refusal (global FOO where C1:1
lands, FOO also on R90:1) and a pin-2 refusal (global FOO where C1:2 lands), on loose / root+pro
/ sub+pro. auto_place_decoupling_cap(C1, 100n, (152.4, 127), VCC, GND, project_path). rlib
records the files changed (the original counted writes; a refusal must change no file).
OK-2 (kicad-cli ignores a 20250114 root's symbol_instances) calls no design: a control, not re-run.
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

R.begin("composite", __file__, tag="cmp_v07_spotchecks")
S = "verify-probe-composite/v07_spotchecks.py"
for cfg in ("loose", "root", "sub"):
    for which, y_off in (("pin1", -3.81), ("pin2", 3.81)):
        fx = C.FX[cfg](f"v07_ok1_{cfg}_{which[3]}")
        s = fx["sch"]
        H.label(s, "FOO", 38.1, 34.29, kind="global_label")             # FOO also on R90:1
        H.label(s, "FOO", 152.4, round(127 + y_off, 2), kind="global_label")
        C.run_case("CMP-01", f"v07_spotchecks:OK1_{cfg}_{which}", "auto_place_decoupling_cap", s,
                   C.cap_kw_v(fx), root=fx["root"], script=S,
                   note=f"cfg {fx['cfg']}, {which} refusal expected")
R.end()
