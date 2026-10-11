"""Follow-up 3: what KiCad itself does with every shared reference H's dup_ref fired on.

For each group (project or fixture, reference):
  rule      the new H-C1 decision (h2_impl's _one_part on the group's records, whole hierarchy)
            and the old one (h_impl's check_unique), plus each member's library entry, unit,
            Value and Footprint, and whether the pad maps agree;
  netlist   kicad-cli kicadxml of the root: how many <comp> entries carry the reference,
            and every pad of it listed in more than one net (a pad KiCad could not make one);
  erc       kicad-cli ERC (all severities) of the root: every violation naming the
            reference, by type, apart from the per-pin drive and library-configuration types
            that do not speak to identity (listed as excluded).
Projects are copied from KiCad's demos into a scratch directory first; nothing is written to
them. The four PI-15 groups read the fixtures build_probeA_fixtures.py wrote for the same run.
Run: uv run python tools/routing_harness/analysis/dupref/oracle.py [--run RUN]
         > <run>/analysis/oracle.out  (writes <run>/analysis/oracle.json)
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
import h_impl as H1  # noqa: E402
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

DEMOS = hpaths.KICAD_DEMOS
CLI = _shared._find_kicad_cli()
if CLI is None:
    sys.exit("kicad-cli not found: install KiCad or set KICAD_CLI_PATH")
FIX = RUN_DIR / "fixtures" / "dupref"
if not FIX.is_dir():
    sys.exit(f"missing {FIX}: run build_probeA_fixtures.py --run {_RUN} first (it builds the PI-15 fixtures)")
SCR = Path(tempfile.mkdtemp(prefix="oracle_", dir=OUT / "tmp"))
EXCLUDED = {"pin_not_connected", "pin_not_driven", "power_pin_not_driven", "lib_symbol_issues",
            "footprint_link_issues", "lib_symbol_mismatch", "unconnected_wire_endpoint",
            "label_dangling", "footprint_filter", "endpoint_off_grid", "wire_dangling",
            "global_label_dangling", "four_way_junction", "label_multiple_wires",
            "simulation_model_issue", "single_global_label", "isolated_pin_label"}


def project(name):
    dst = SCR / name.replace(" ", "_")
    if not dst.exists():
        shutil.copytree(DEMOS / name, dst)
    return dst


GROUPS = []
vw = project("vme-wren")
GROUPS += [("vme-wren project", vw / "vme-wren.kicad_sch", vw / "fpga-hp-banks.kicad_sch", "IC14"),
           ("vme-wren project", vw / "vme-wren.kicad_sch", vw / "clocks.kicad_sch", "IC20"),
           ("vme-wren project", vw / "vme-wren.kicad_sch", vw / "power-supply-1.kicad_sch", "W1")]
sx = project("sonde xilinx")
GROUPS += [("sonde xilinx project", sx / "sonde xilinx.kicad_sch", sx / "sonde xilinx.kicad_sch", "U1")]
for v, ref in (("m_edges__13_R1", "R1"), ("m_edges__13_RQ", "R?"), ("v10_dupref__C1", "C1"),
               ("v10_dupref__CQ", "C?")):
    d = FIX / f"probeA__{v}"
    GROUPS.append((f"PI-15 {v}", d / "dup.kicad_sch", d / "dup.kicad_sch", ref))


def rule(sheet, ref):
    root = _cst.parse(sheet.read_bytes()).lists[0]
    m2 = H2._Model(root, str(sheet))
    try:
        m2.check_unique(ref)
        new = "accepted"
    except H2._Refuse as e:
        new = "refused: " + str(e).split("): ", 1)[-1].split("; KiCad", 1)[0]
    m1 = H1._Model(_cst.parse(sheet.read_bytes()).lists[0], str(sheet))
    try:
        m1.check_unique(ref)
        old = "accepted"
    except H1._Refuse:
        old = "refused"
    recs = [r for r in m2._mine + m2._hier_records() if ref in r[0]]
    members = []
    for refs, key, ls, units in recs:
        members.append({"lib": key, "units": units, "pads": len(H2._pad_map(ls)) if ls is not None else None})
    maps = {tuple(sorted(H2._pad_map(ls).items())) for _, _, ls, _ in recs if ls is not None}
    return {"old": old, "new": new, "members": members, "pad_maps_agree": len(maps) == 1}


def props(root_sch, ref):
    """Value and Footprint of every placed symbol carrying ref anywhere in the hierarchy."""
    out, todo, seen = [], [root_sch], set()
    while todo:
        f = todo.pop()
        if f.resolve() in seen or not f.exists():
            continue
        seen.add(f.resolve())
        root = _cst.parse(f.read_bytes()).lists[0]
        for s in root.find_all("symbol"):
            if ref in H2._sym_refs(s):
                out.append((f.name, _shared._sym_property_cst(s, "Value"),
                            _shared._sym_property_cst(s, "Footprint")))
        for sh in root.find_all("sheet"):
            name = _shared._sheet_file_cst(sh)
            if name:
                todo.append(f.parent / name)
    return sorted(set(out))


def kicad(root_sch, ref):
    out = SCR / f"{root_sch.stem}.{ref.replace('?', 'Q')}"
    net, erc = out.with_suffix(".xml"), out.with_suffix(".json")
    subprocess.run([CLI, "sch", "export", "netlist", "--format", "kicadxml", "--output",
                    str(net), str(root_sch)], capture_output=True)
    subprocess.run([CLI, "sch", "erc", "--format", "json", "--severity-all", "--output",
                    str(erc), str(root_sch)], capture_output=True)
    t = ET.parse(net).getroot()
    comps = [c for c in t.iter("comp") if c.get("ref") == ref]
    pad_nets = Counter()
    for n in t.iter("net"):
        for x in n.findall("node"):
            if x.get("ref") == ref:
                pad_nets[x.get("pin")] += 1
    d = json.loads(erc.read_text(encoding="utf-8"))
    viol = Counter()
    excluded = Counter()
    for s in d.get("sheets", []):
        for v in s.get("violations", []):
            if any(i.get("description", "").startswith(f"Symbol {ref} ") for i in v.get("items", [])):
                (excluded if v["type"] in EXCLUDED else viol)[v["type"]] += 1
    return {"netlist_comp_entries": len(comps),
            "pads_in_several_nets": sorted(p for p, k in pad_nets.items() if k > 1),
            "erc_identity_relevant": dict(viol), "erc_excluded": dict(excluded)}


res = []
for label, root_sch, sheet, ref in GROUPS:
    ent = {"group": label, "sheet": sheet.name, "ref": ref, **rule(sheet, ref),
           "props": props(root_sch, ref), **kicad(root_sch, ref)}
    res.append(ent)
    print(json.dumps(ent))
(OUT / "oracle.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
shutil.rmtree(SCR, ignore_errors=True)
