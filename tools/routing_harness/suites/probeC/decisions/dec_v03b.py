"""Rerun of verify-probe-decisions/v03b_erc_merge.py on M2 and K (DEC-03).

Fixture copied from v03b: the v02 GND hierarchy without R6 (sheet 'sub', sheet pin PA, parent label
removed, R1:1 on the stub end, child GND on the PA net, parent R5:1 on N7). Call:
wire([R1:1],'N7'). The original compared kicad-cli ERC before and after P's write; here ERC rows
are printed for any design that writes (none expected: the call reaches a sheet pin).
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import dhelp as D  # noqa: E402  (imports rlib first)
from dhelp import R, W  # noqa: E402
from pathlib import Path  # noqa: E402

R.begin("decisions", __file__, tag="dec_v03b")
S = "verify-probe-decisions/v03b_erc_merge.py; rerun decisions/dec_v03b.py"
RES = "Device:R"


def erc(path):
    out = str(path) + ".erc.json"
    if os.path.exists(out):
        os.remove(out)
    D.H._run_cli(["sch", "erc", "--format", "json", "--severity-all", "--output", out, str(path)],
                 check=False)
    data = json.load(open(out, encoding="utf-8"))
    os.remove(out)
    rows = Counter()
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            items = "; ".join(i.get("description", "") for i in v.get("items", []))
            rows[f"{sheet.get('path')} {v.get('severity')}:{v.get('type')}: {items}"] += 1
    return rows


root, pro, kid, stub = D.v_hierarchy("v03b")
D.v_place(root, RES, "R1", stub[0], round(stub[1] + 3.81, 4), project_path=pro)
D.v_place(kid, RES, "R11", 50.8, 38.1, project_path=pro)
D.wire(kid, 27.94, 25.4, 50.8, 25.4)
D.wire(kid, 50.8, 25.4, 50.8, 34.29)
D.v_place(kid, "power:GND", "#PWR01", 50.8, 25.4, value="GND", project_path=pro)
D.v_place(root, RES, "R5", 76.2, 69.85, project_path=pro)
D.v_stubbed(root, "R5", "1", "N7")
kw = W([("R1", "1")], "N7")
recs = R.run_case("DEC-03", "v03b_erc_merge:A", "wire_pins_to_net", root, kw, root=root, script=S)
for dz, rec in recs.items():
    if rec["status"] == "OK":
        d = R.copy_tree(Path(root).parent, f"{Path(root).parent.name}__erc_{dz}")
        r2 = d / Path(root).name
        before = erc(r2)
        R.call(getattr(R.IMPLS[dz], "wire_pins_to_net"), schematic_path=str(r2), **kw)
        after = erc(r2)
        print(f"  ERC {dz}: only before {sorted(set(before) - set(after))}")
        print(f"  ERC {dz}: only after  {sorted(set(after) - set(before))}")
    else:
        print(f"  ERC {dz}: not run (status {rec['status']}, nothing written)")
R.end()
