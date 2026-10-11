"""Follow-up 3: the pad-map branch of the new H-C1, on synthetic disagreements. The real samples
never reached it (every shared reference there agreed on its pad map), so this edits one library
variant of vme-wren clocks' IC20 (SN74LVC2G126DCT_1, unit 1 of 2; the other unit uses
'Standard Logic:SN74LVC2G126DCT') in scratch copies and records, per variant:
  - h2_impl's decision for IC20 (Model.check_unique) and its stated reason;
  - kicad-cli's netlist: <comp> entries for IC20 and IC20 pads listed in more than one net.
Variants: A pad 4 (GND) electrical type power_in -> passive in the _1 entry;
          B pad 8 (VCC, drawn by unit 0) moved into unit 1 in the _1 entry;
          C control: the file unchanged.
Needs the run's prepared demo fixtures (prepare.py: fixtures/demo, the clocks sheet).
Run: uv run python tools/routing_harness/analysis/dupref/disagree_test.py [--run RUN]
         > <run>/analysis/disagree_test.out
"""
import argparse
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import h2_impl as H2  # noqa: E402
from mcp_server_kicad import _cst, _shared  # noqa: E402

_ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
_ap.add_argument("--run", default=hpaths.RUN, metavar="RUN",
                 help="run name or directory (default: ROUTING_HARNESS_RUN, else 'default')")
_RUN = _ap.parse_args().run
RUN_DIR = hpaths.run_dir(_RUN)
if not RUN_DIR.is_dir():
    sys.exit(f"run directory not found: {RUN_DIR}")
OUT = RUN_DIR / "analysis"
(OUT / "tmp").mkdir(parents=True, exist_ok=True)
CLI = _shared._find_kicad_cli()
if CLI is None:
    sys.exit("kicad-cli not found: install KiCad or set KICAD_CLI_PATH")
SRC = RUN_DIR / "fixtures" / "demo" / "vme-wren__clocks.kicad_sch" / "clocks.kicad_sch"
if not SRC.is_file():
    sys.exit(f"missing {SRC}: run prepare.py --run {_RUN} first (it builds the demo fixtures)")
SCR = Path(tempfile.mkdtemp(prefix="disagree_", dir=OUT / "tmp"))


def lib(root, name):
    return next(s for s in root.find("lib_symbols").find_all("symbol") if s.atoms[1].text == name)


def pin(ls, num):
    for sub in ls.find_all("symbol"):
        for p in sub.find_all("pin"):
            if H2._child_text(p, "number") == num:
                return sub, p
    raise KeyError(num)


def variant(tag, edit):
    d = SCR / tag
    d.mkdir()
    f = d / "clocks.kicad_sch"
    tree = _cst.parse(SRC.read_bytes())
    root = tree.lists[0]
    note = edit(root) if edit else "unchanged"
    f.write_bytes(_cst.serialize(tree))
    m = H2._Model(_cst.parse(f.read_bytes()).lists[0], str(f))
    try:
        m.check_unique("IC20")
        decision = "accepted"
    except H2._Refuse as e:
        decision = "refused: " + str(e).split("): ", 1)[-1].split("; KiCad", 1)[0]
    net = d / "n.xml"
    r = subprocess.run([CLI, "sch", "export", "netlist", "--format", "kicadxml", "--output",
                        str(net), str(f)], capture_output=True, text=True)
    out = {"variant": tag, "edit": note, "h2": decision, "cli_rc": r.returncode}
    if net.exists():
        t = ET.parse(net).getroot()
        pads = Counter(x.get("pin") for n in t.iter("net") for x in n.findall("node") if x.get("ref") == "IC20")
        out["comp_entries"] = sum(1 for c in t.iter("comp") if c.get("ref") == "IC20")
        out["pads_in_several_nets"] = sorted(p for p, k in pads.items() if k > 1)
        out["pads_listed"] = sorted(pads)
    print(json.dumps(out))


def edit_a(root):
    _sub, p = pin(lib(root, "SN74LVC2G126DCT_1"), "4")
    old = p.atoms[1].text
    p.atoms[1].set_text("passive")
    return f"pad 4 type {old} -> passive in SN74LVC2G126DCT_1"


def edit_b(root):
    ls = lib(root, "SN74LVC2G126DCT_1")
    sub, p = pin(ls, "8")
    ids = H2._sch._lib_unit_style(sub)
    target = next(s for s in ls.find_all("symbol") if (H2._sch._lib_unit_style(s) or (0, 0))[0] == 1)
    if ids and ids[0] == 1:
        return f"pad 8 already in unit 1 ({sub.atoms[1].text}); no edit"
    target.insert_after(target.lists[-1], p.copy())
    sub.remove_child(p)
    return f"pad 8 moved from {sub.atoms[1].text} (unit {ids[0] if ids else '?'}) to {target.atoms[1].text}"


variant("C_control", None)
variant("A_type", edit_a)
variant("B_unit", edit_b)
shutil.rmtree(SCR, ignore_errors=True)
