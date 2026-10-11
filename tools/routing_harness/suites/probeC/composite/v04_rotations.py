"""Rerun of verify-probe-composite/v04_rotations.py (CMP-04) on M2 and K.

Device:C and Device:C_Small at rotation 0/90/180/270 on a loose sheet, plus rotation 180 on a
sub-sheet; auto_place_decoupling_cap(C1, 100n, (152.4, 127), VCC, GND, project_path=fx pro).
Fixtures are vlib's (copied in cmplib). Judged by rlib; the original's geometric check (stub or
label inside the symbol, wire collinear with a pin line, transform cross-check) is repeated per
design on a scratch copy and written to v04_geometry.json.
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

R.begin("composite", __file__, tag="cmp_v04_rotations")
S = "verify-probe-composite/v04_rotations.py"
runs = [("loose", lib, rot) for lib in ("Device:C", "Device:C_Small") for rot in (0, 90, 180, 270)]
runs += [("sub", lib, 180) for lib in ("Device:C", "Device:C_Small")]
GEO = []
for cfg, lib, rot in runs:
    tag = f"{cfg}_{lib.split(':')[1]}_{rot}"
    fx = C.FX[cfg](f"v04_{tag}")
    kw = C.cap_kw_v(fx, lib=lib, rotation=rot)
    C.run_case("CMP-04", f"v04_rotations:{tag}", "auto_place_decoupling_cap", fx["sch"], kw,
               root=fx["root"], script=S, note=f"cfg {fx['cfg']}")
    for dz in R.DESIGNS:
        g = C.geom_check(dz, Path(fx["dir"]), fx["sch"], kw)
        g["variant"] = f"v04_rotations:{tag}"
        GEO.append(g)
        print(f"  [geo] {dz} {g['status']} inside={g.get('inside')} xcheck="
              f"{g.get('transform_crosscheck')}", flush=True)
(R.OUT / "composite").mkdir(parents=True, exist_ok=True)
(R.OUT / "composite" / "v04_geometry.json").write_text(json.dumps(GEO, indent=1),
                                                       encoding="utf-8")
R.end()
