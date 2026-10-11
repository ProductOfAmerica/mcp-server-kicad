"""Rerun of verify-probe-decisions/v12_erc_pads.py on M2 and K (DEC-11 ii, DEC-12 i).

Fixture copied from v12: the v10 RN1 geometry without R7 (unit A (63.5, 76.2), unit B (88.9, 76.2),
R5:1 on N, R6:1 on X). i: both copies free; ii: copy B on X. Call: wire([RN1:1],'N').
The original printed kicad-cli ERC rows mentioning RN1 before and after P's write; here they are
printed for every design that writes.
"""
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402
from pathlib import Path  # noqa: E402

R.begin("decisions", __file__, tag="dec_v12")
S = "verify-probe-decisions/v12_erc_pads.py; rerun decisions/dec_v12.py"
RES = "Device:R"


def erc(path):
    out = str(path) + ".erc.json"
    if os.path.exists(out):
        os.remove(out)
    D.H._run_cli(["sch", "erc", "--format", "json", "--severity-all", "--output", out, str(path)],
                 check=False)
    data = json.load(open(out, encoding="utf-8"))
    os.remove(out)
    rows = []
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            items = "; ".join(i.get("description", "") for i in v.get("items", []))
            if "RN1" in items:
                rows.append(f"{v.get('severity')}:{v.get('type')}: {items}")
    return rows


def fixture(name, b=None):
    p = D.v_new(name)
    D.v_place(p, "Device:R_Network03_Split", "RN1", 63.5, 76.2)
    D.v_add_unit(p, "RN1", 2, 88.9, 76.2)
    D.v_place(p, RES, "R5", 139.7, 101.6)
    D.v_stubbed(p, "R5", "1", "N")
    D.v_place(p, RES, "R6", 165.1, 101.6)
    D.v_stubbed(p, "R6", "1", "X")
    if b:
        D.v_stubbed(p, "RN1", "1", b, i=1)
    return p


for tag, b, case, also in (("i", None, "DEC-12", ["DEC-11"]), ("ii", "X", "DEC-11", [])):
    p = fixture(f"v12{tag}", b)
    kw = W([("RN1", "1")], "N")
    recs = R.run_case(case, f"v12_erc_pads:{tag}", "wire_pins_to_net", p, kw, script=S, also=also)
    for dz, rec in recs.items():
        if rec["status"] == "OK":
            d = R.copy_tree(Path(p).parent, f"{Path(p).parent.name}__erc_{dz}")
            q = d / Path(p).name
            before = erc(q)
            R.call(getattr(R.IMPLS[dz], "wire_pins_to_net"), schematic_path=str(q), **kw)
            after = erc(q)
            print(f"  ERC {dz} {tag}: RN1 rows before {before}")
            print(f"  ERC {dz} {tag}: RN1 rows after  {after}")
        else:
            print(f"  ERC {dz} {tag}: not run (status {rec['status']}, nothing written)")
R.end()
