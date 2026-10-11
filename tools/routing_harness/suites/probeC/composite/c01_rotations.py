"""Rerun of probe-composite/c01_rotations.py (CMP-04, also CMP-13) on M2 and K.

Device:C and Device:C_Small x rotation 0/90/180/270 x {loose, root+pro, sub+pro, sub with
project_path omitted}; auto_place_decoupling_cap(C1, 100n, (127, 101.6), VCC, GND). Fixtures are
cplib's (copied in cmplib). Judged by rlib (judge3, two cap passes). The geometric check the
original made (stub or label inside the symbol, new wire collinear with a cap pin line) is
repeated on a scratch copy per design and written to c01_geometry.json.
"""

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402

import cmplib as C  # noqa: E402

R.begin("composite", __file__, tag="cmp_c01_rotations")
S = "probe-composite/c01_rotations.py"
GEO = []
for cfg in ("loose", "root", "sub", "subnp"):
    for lib in ("Device:C", "Device:C_Small"):
        for rot in (0, 90, 180, 270):
            tag = f"{cfg}_{lib.split(':')[1]}_{rot}"
            fx = C.CONFIGS[cfg](f"c01_{tag}")
            kw = C.cap_kw_c(fx, lib_id=lib, rotation=rot)
            C.run_case("CMP-04", f"c01_rotations:{tag}", "auto_place_decoupling_cap", fx["sch"],
                       kw, root=fx["root"], script=S, also=["CMP-13"], note=f"cfg {fx['cfg']}")
            for dz in R.DESIGNS:
                g = C.geom_check(dz, Path(fx["dir"]), fx["sch"], kw)
                g["variant"] = f"c01_rotations:{tag}"
                GEO.append(g)
                print(f"  [geo] {dz} {g['status']} inside={g.get('inside')} xcheck="
                      f"{g.get('transform_crosscheck')}", flush=True)
(R.OUT / "composite").mkdir(parents=True, exist_ok=True)
(R.OUT / "composite" / "c01_geometry.json").write_text(json.dumps(GEO, indent=1),
                                                       encoding="utf-8")
R.end()
