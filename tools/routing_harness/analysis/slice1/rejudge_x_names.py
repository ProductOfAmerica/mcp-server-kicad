"""Slice 1 gate: re-judge design X's writes the harness flagged after asking for a pin by NAME.

The harness judge keys a request by the text passed (("U1", "GND")), while kicad-cli lists pads
by number, so a write for a name never matches a node: the pin's own net reads as a bad merge or
a rename and nothing reads as delivered. Here each requested (ref, name) is mapped to the pad
numbers whose pin carries that name (or number), read from the library symbol in the before
file, independently of the code under test, and the before and after files are judged again
with the repo's netlist judge (kicad-cli reading).
Needs a target that has tests/netlist_oracle.py (the repo from slice 1 on, not a 54160c1 export).
Run (RUN is a run name under work/runs or a path, default: the current run):
    uv run python tools/routing_harness/analysis/slice1/rejudge_x_names.py --run RUN > rejudge_x_names.out
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
from netlist_oracle import judge, nets  # noqa: E402

from mcp_server_kicad import _cst  # noqa: E402

_ap = argparse.ArgumentParser(description="Re-judge design X's by-name writes (slice 1 gate).")
_ap.add_argument("--run", default=hpaths.RUN,
                 help="run holding design X's records: a name under work/runs or a path (default: the current run)")
PASS1 = hpaths.run_dir(_ap.parse_args().run)  # the run whose records are re-judged
if not PASS1.is_dir():
    sys.exit(f"run directory not found: {PASS1}")


def root_file(d: Path, files: list) -> Path:
    pros = list(d.glob("*.kicad_pro"))
    if pros and pros[0].with_suffix(".kicad_sch").exists():
        return pros[0].with_suffix(".kicad_sch")
    sch = list(d.glob("*.kicad_sch"))
    if len(sch) == 1:
        return sch[0]
    (one,) = [d / f for f in files]
    return one


def pads_of(d: Path, ref: str, label: str) -> set:
    """Pad numbers of every pin named or numbered *label* on the symbols placed as *ref*."""
    out = set()
    for f in d.glob("*.kicad_sch"):
        root = _cst.parse(f.read_bytes()).lists[0]
        libs = {s.atoms[1].text: s for s in root.find("lib_symbols").find_all("symbol")}
        for s in root.find_all("symbol"):
            refs = {q.atoms[2].text for q in s.find_all("property")
                    if len(q.atoms) > 2 and q.atoms[1].text == "Reference"}
            inst = s.find("instances")
            for proj in inst.find_all("project") if inst is not None else ():
                for p in proj.find_all("path"):
                    r = p.find("reference")
                    if r is not None:
                        refs.add(r.atoms[1].text)
            if ref not in refs:
                continue
            key_node = s.find("lib_name")
            key = (key_node if key_node is not None else s.find("lib_id")).atoms[1].text
            for sub in libs[key].find_all("symbol"):
                for pin in sub.find_all("pin"):
                    num, name = pin.find("number").atoms[1].text, pin.find("name").atoms[1].text
                    if label in (num, name):
                        out.add(num)
    return out


def requested(r: dict) -> tuple[list, str]:
    kw = r["kw"]
    if isinstance(kw, dict):
        return [(p["reference"], str(p["pin"])) for p in kw["pins"]], kw["label_text"]
    pins = re.search(r"pins=\[([^\]]*)\]", kw).group(1)
    n = re.search(r"label_text='([^']*)'", kw).group(1)
    return [tuple(x.strip().split(":", 1)) for x in pins.split(",")], n


found = []
for rec_file in list(PASS1.glob("probeB/rec/*.jsonl")) + list(PASS1.glob("llm-workflow/parts/*.jsonl")):
    for line in open(rec_file, encoding="utf-8"):
        r = json.loads(line)
        if r.get("design") == "X" and r.get("cli_wrong") and r.get("tool") == "wire_pins_to_net":
            found.append(r)
print(len(found), "wrong wire_pins_to_net records of X in", PASS1.name)
for r in found:
    kept = Path(r.get("kept") or r.get("wrong_dir"))
    if not kept.exists():  # the run directory moved since the record was written: re-root the path below runs/<run>/
        kept = PASS1.joinpath(*kept.parts[max(i for i, x in enumerate(kept.parts) if x == "runs") + 2 :])
    before, after = (kept / "before", kept / "after") if (kept / "before").is_dir() else (kept / "pre", kept / "post")
    pins, n = requested(r)
    group = {(ref, pad) for ref, lab in pins for pad in pads_of(before, ref, lab)}
    names = {(ref, lab) for ref, lab in pins if not lab.isdigit()}
    files = r.get("files_changed") or []
    v = judge(nets(root_file(before, files)), nets(root_file(after, files)), [group], n)
    print(f"  requested {pins}")
    print(f"  pads      {sorted(group)}")
    print(f"  harness   {r.get('pads') or r.get('groups')}")
    print(f"{r.get('case')} {r.get('variant', '')[:60]}: names {sorted(names)} -> {len(group)} pads;"
          f" wrong={v.wrong} delivered={v.delivered} {v.problems() or ''}")
