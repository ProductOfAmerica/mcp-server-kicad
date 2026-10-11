"""Follow-up 3: KiCad's own one-part signal, isolated. In scratch copies every sheet keeps only the
placed symbols that carry the reference (and the sheet symbols of the hierarchy); every other
symbol (power symbols included), wire, label, junction, no-connect flag, bus and bus entry is
removed. A pad drawn by several of the remaining symbols then lands in one net only if KiCad joins
the copies itself; "pads in several nets" means KiCad keeps the copies apart (two parts).

Per case: the new H-C1 decision; how many pads are drawn more than once (only those can show a
join); which of them KiCad lists in more than one net; and whether any of them is a hidden power_in
pin (those join by pin name in KiCad, unit or not, so they say nothing about identity); and the
kicad-cli ERC violation types (all severities) whose items name the reference.
  real   vme-wren IC14, IC20, W1 (whole project), sonde xilinx U1 (whole project)
  synth  clocks IC20: A pad 4 type changed in one variant; B pad 8 moved from unit 0 into unit 1
         in one variant; L unit 2 under another lib_id with an identical entry
  mixed  74xx:74LS00 as U1 unit 1 and 74xx:74LS04 as U1 unit 2 (different parts; share pad 3)
  dup    PI-15 m_edges/13_R1: two Device:R, both R1, both unit 1
Needs the run's prepared demo fixtures (prepare.py: fixtures/demo, the clocks sheet) and
build_probeA_fixtures.py's PI-15 fixture.
Run: uv run python tools/routing_harness/analysis/dupref/isolate_test.py [--run RUN]
         > <run>/analysis/isolate_test.out
"""
import argparse
import json
import os
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

_ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
_ap.add_argument("--run", default=hpaths.RUN, metavar="RUN",
                 help="run name or directory (default: ROUTING_HARNESS_RUN, else 'default')")
_RUN = _ap.parse_args().run
RUN_DIR = hpaths.run_dir(_RUN)
if not RUN_DIR.is_dir():
    sys.exit(f"run directory not found: {RUN_DIR}")
OUT = RUN_DIR / "analysis"
(OUT / "tmp").mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(OUT / "tmp")  # harness.BASE and the scratch land under the run
tempfile.tempdir = str(OUT / "tmp")

import harness as Hn  # noqa: E402
import h2_impl as H2  # noqa: E402
from mcp_server_kicad import _cst, _shared  # noqa: E402

DEMOS = hpaths.KICAD_DEMOS
CLI = _shared._find_kicad_cli()
if CLI is None:
    sys.exit("kicad-cli not found: install KiCad or set KICAD_CLI_PATH")
CLOCKS = RUN_DIR / "fixtures" / "demo" / "vme-wren__clocks.kicad_sch" / "clocks.kicad_sch"
PI15 = RUN_DIR / "fixtures" / "dupref" / "probeA__m_edges__13_R1" / "dup.kicad_sch"
if not CLOCKS.is_file():
    sys.exit(f"missing {CLOCKS}: run prepare.py --run {_RUN} first (it builds the demo fixtures)")
if not PI15.is_file():
    sys.exit(f"missing {PI15}: run build_probeA_fixtures.py --run {_RUN} first")
SCR = Path(tempfile.mkdtemp(prefix="isolate_", dir=OUT / "tmp"))
DROP = ("wire", "junction", "label", "global_label", "hierarchical_label", "no_connect", "bus",
        "bus_entry", "netclass_flag", "directive_label", "text", "text_box")


def isolate(d, ref):
    for f in d.rglob("*.kicad_sch"):
        tree = _cst.parse(f.read_bytes())
        root = tree.lists[0]
        for ch in list(root.lists):
            if ch.head in DROP or (ch.head == "symbol" and ref not in H2._sym_refs(ch)):
                root.remove_child(ch)
        f.write_bytes(_cst.serialize(tree))


def sheets(root_sch):
    out, todo, seen = [], [root_sch], set()
    while todo:
        f = todo.pop()
        if f.resolve() in seen or not f.exists():
            continue
        seen.add(f.resolve())
        out.append(f)
        for sh in _cst.parse(f.read_bytes()).lists[0].find_all("sheet"):
            name = _shared._sheet_file_cst(sh)
            if name:
                todo.append(f.parent / name)
    return out


def measure(case, root_sch, sheet, ref):
    drawn, hidden_pw = Counter(), set()
    for f in sheets(root_sch):
        m = H2._Model(_cst.parse(f.read_bytes()).lists[0], str(f))
        for (r, num), copies in m.pads.items():
            if r == ref:
                drawn[num] += len(copies)
                if any(c.hidden and c.etype == "power_in" for c in copies):
                    hidden_pw.add(num)
    m = H2._Model(_cst.parse(sheet.read_bytes()).lists[0], str(sheet))
    try:
        m.check_unique(ref)
        decision = "accepted"
    except H2._Refuse as e:
        decision = "refused: " + str(e).split("): ", 1)[-1].split("; KiCad", 1)[0]
    net = root_sch.with_suffix(".xml")
    r = subprocess.run([CLI, "sch", "export", "netlist", "--format", "kicadxml", "--output",
                        str(net), str(root_sch)], capture_output=True, text=True)
    out = {"case": case, "ref": ref, "new_rule": decision, "cli_rc": r.returncode}
    erc = root_sch.with_suffix(".erc.json")
    subprocess.run([CLI, "sch", "erc", "--format", "json", "--severity-all", "--output",
                    str(erc), str(root_sch)], capture_output=True)
    if erc.exists():
        types = Counter()
        for s in json.loads(erc.read_text(encoding="utf-8")).get("sheets", []):
            for v in s.get("violations", []):
                if any(i.get("description", "").startswith(f"Symbol {ref} ") for i in v.get("items", [])):
                    types[v["type"]] += 1
        out["erc_types_naming_ref"] = dict(types)
    if net.exists():
        t = ET.parse(net).getroot()
        listed = Counter(x.get("pin") for n in t.iter("net") for x in n.findall("node") if x.get("ref") == ref)
        multi = sorted(n for n, k in drawn.items() if k > 1)
        out.update({"pads": len(drawn), "pads_drawn_more_than_once": len(multi),
                    "of_those_hidden_power_in": sorted(hidden_pw & set(multi)),
                    "of_those_kept_apart_by_kicad": sorted(n for n in multi if listed[n] > 1),
                    "of_those_joined_by_kicad": sorted(n for n in multi if listed[n] == 1)})
    print(json.dumps(out), flush=True)


def lib(root, name):
    return next(s for s in root.find("lib_symbols").find_all("symbol") if s.atoms[1].text == name)


def pin(ls, num):
    for sub in ls.find_all("symbol"):
        for p in sub.find_all("pin"):
            if H2._child_text(p, "number") == num:
                return sub, p
    raise KeyError(num)


def clocks_variant(tag, edit):
    d = SCR / tag
    d.mkdir()
    f = d / "clocks.kicad_sch"
    tree = _cst.parse(CLOCKS.read_bytes())
    if edit:
        edit(tree.lists[0])
    f.write_bytes(_cst.serialize(tree))
    isolate(d, "IC20")
    measure(f"synth IC20 {tag}", f, f, "IC20")


def edit_a(root):
    pin(lib(root, "SN74LVC2G126DCT_1"), "4")[1].atoms[1].set_text("passive")


def edit_b(root):
    ls = lib(root, "SN74LVC2G126DCT_1")
    sub, p = pin(ls, "8")
    tgt = next(s for s in ls.find_all("symbol") if (H2._sch._lib_unit_style(s) or (0, 0))[0] == 1)
    tgt.insert_after(tgt.lists[-1], p.copy())
    sub.remove_child(p)


def edit_l(root):
    libs = root.find("lib_symbols")
    src = lib(root, "Standard Logic:SN74LVC2G126DCT")
    new = src.copy()
    new.atoms[1].set_text("Other:SN74LVC2G126DCT")
    libs.insert_after(src, new)
    for s in root.find_all("symbol"):
        if H2._child_text(s, "lib_id") == "Standard Logic:SN74LVC2G126DCT" and "IC20" in H2._sym_refs(s) \
                and not H2._child_text(s, "lib_name"):
            s.find("lib_id").atoms[1].set_text("Other:SN74LVC2G126DCT")


# real groups
vw = SCR / "vme-wren"
for ref, sheet in (("IC14", "fpga-hp-banks.kicad_sch"), ("IC20", "clocks.kicad_sch"),
                   ("W1", "power-supply-1.kicad_sch")):
    d = SCR / f"vme-wren-{ref}"
    shutil.copytree(DEMOS / "vme-wren", d)
    isolate(d, ref)
    measure(f"real vme-wren {ref}", d / "vme-wren.kicad_sch", d / sheet, ref)
d = SCR / "sonde"
shutil.copytree(DEMOS / "sonde xilinx", d)
isolate(d, "U1")
measure("real sonde xilinx U1", d / "sonde xilinx.kicad_sch", d / "sonde xilinx.kicad_sch", "U1")
# synthetic
clocks_variant("control", None)
clocks_variant("A_type", edit_a)
clocks_variant("B_unit", edit_b)
clocks_variant("L_libid", edit_l)
# mixed parts
p = Path(Hn.fresh("mixed_iso"))
Hn.place(str(p), "74xx:74LS00", "U1", 101.6, 101.6)
Hn.place(str(p), "74xx:74LS04", "U2", 152.4, 101.6)
tree = _cst.parse(p.read_bytes())
for s in tree.lists[0].find_all("symbol"):
    if H2._child_text(s, "lib_id") == "74xx:74LS04":
        for prop in s.find_all("property"):
            if prop.atoms[1].text == "Reference":
                prop.atoms[2].set_text("U1")
        s.find("unit").atoms[1].set_text("2")
        for path in H2._inst_paths(s):
            path.find("reference").atoms[1].set_text("U1")
            path.find("unit").atoms[1].set_text("2")
p.write_bytes(_cst.serialize(tree))
measure("mixed 74LS00 u1 + 74LS04 u2 as U1", p, p, "U1")
# true duplicate
d = SCR / "pi15"
shutil.copytree(PI15.parent, d)
isolate(d, "R1")
measure("dup PI-15 R1 (two Device:R, unit 1)", d / "dup.kicad_sch", d / "dup.kicad_sch", "R1")
shutil.rmtree(SCR, ignore_errors=True)
