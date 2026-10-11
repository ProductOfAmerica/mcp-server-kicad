"""Rerun of probe-composite/c02_rollback_exact.py (CMP-12) on M2 and K.

Triggers (c02.add_trigger, copied): T1 pin 2 lands on global FOO that also names R9:1; T2 a
1-pin part (Connector:TestPoint) so pin 2 does not exist; T3 pin 1 lands on the end of a wire
carrying global FOO. Each in loose / root+pro / sub+pro / sub with project_path omitted.
auto_place_decoupling_cap(C1, 100n, (127, 101.6), VCC, GND). Judged by rlib (a refusal is judged
by byte identity of every file in the project directory).
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402

import cmplib as C  # noqa: E402

R.begin("composite", __file__, tag="cmp_c02_rollback_exact")
S = "probe-composite/c02_rollback_exact.py"
for trig in ("T1", "T2", "T3"):
    for cfg in ("loose", "root", "sub", "subnp"):
        fx = C.CONFIGS[cfg](f"c02_{trig}_{cfg}")
        tkw = C.add_trigger(fx, trig)
        kw = dict(reference="C1", value="100n", x=127, y=101.6, power_net="VCC",
                  ground_net="GND", project_path=fx["project_path"], **tkw)
        C.run_case("CMP-12", f"c02_rollback_exact:{trig}_{cfg}", "auto_place_decoupling_cap",
                   fx["sch"], kw, root=fx["root"], script=S, note=f"cfg {fx['cfg']}")
R.end()
