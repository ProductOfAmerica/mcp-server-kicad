"""Derive, scrub and copy the data files of the routing harness from the pressure test's scratch
directories. Provenance and the way to re-derive them: run it again while the scratch directories
still exist (they are not durable); `--check` compares what it derives with the committed files.

usage: derive.py --pt <scratch root "pr57pt"> --work <scratch root "pr57work"> [--check]

It reads only those two roots (read only) and writes only under tools/routing_harness: it knows no
machine path, the roots are arguments. Standard library only. Output is deterministic:
json.dumps(obj, indent=1, ensure_ascii=False), LF, a final newline; sorted only where noted.
Before anything is written it scans every output for the machine and user names it must not carry
and for em and en dashes, and fails (writing nothing) if one is found.

Derived, under baselines/ (the identity expectations of prepare.py, and gate.py's inputs):
  demo_followup1.json          {sheet: {"sha256": sha256 of follow-up 1's repathed fixture,
                               "cand": follow-up 1's candidate pin list}} for its 11 demo sheets
  demo_connect_pairs.json      {sheet: [{"a": "REF:PIN", "b": "REF:PIN"}, ...]}, the 244 pairs of
                               the connect_pins sweep, sheet order and row order kept
  demo_m2_refused_pairs.json   sorted [group, case] pairs whose connect_pins design M2 refused in
                               follow-up 2 (the 160 pairs of the fallback sample)
  layouts_followup1_bases.json {"gen/<group>" | "star/<group[:6]>" | "jct/<case id>": pre_norm} for
                               the 67 layout bases (the selection of layouts_stock/check_bases.py)
  fixtures_expected.json       sections:
                                 layouts_stock       {name: sha256} of the three stock bases
                                 probeB_vhp          sha256 of probeB's vhp.kicad_sym
                                 probeC_perf         {"probe/<file>" | "verify/<file>":
                                                     {"sha256": ...} for the pure copies or
                                                     {"norm": ...} for files that hold fresh UUIDs}
                                 probeC_perf_report  the part of perf's build_report.json that
                                                     states positions: probe/<sheet>/aug,
                                                     probe/k200/placed, verify/<sheet>/<tag>/x0
                               "norm" is sha256 of the bytes with every UUID replaced by U, first
                               20 hex digits (layouts_stock/check_bases.py's rule)
Scrubbed:
  caseidx/*.json               machine paths in string values become pr57pt/<dir>/<file> labels
  baselines/gate_*.json        "root" becomes "scratch:coarse/<rerunN>"
  reports/HANDOFF.md           the scratch root becomes %TEMP%\\pr57pt, KiCad's demos <KiCad 9.0
                               install>\\share\\kicad\\demos
  reports/pressure-test-report.md  the "Not run: codex-spec" line, which described a local tool
                               configuration, becomes a neutral line; nothing else changes
Repaired:
  reports/coarse-variant-report.md  one path whose backslash-n became a line break when the report
                               was written is restored; nothing else changes
Copied byte for byte:
  reports/{hybrid,dupref}-report.md, reports/routing-proposal-v2.md
"""

import argparse
import getpass
import hashlib
import json
import re
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent

CASEIDX = ("buses", "composite", "decisions", "geometry-misc", "hierarchy", "inert", "junctions",
           "labels", "llm-workflow", "naming", "perf", "pin-identity", "units-pads")
GATES = ("gate_rerun3_Hb2", "gate_x_final2", "gate_x_final3", "gate_rerun2_Hb")
REPORTS = ("hybrid-report.md", "dupref-report.md", "routing-proposal-v2.md")
LAYOUT_STOCK = ("ampli_ht", "fpga-hp-banks", "tinytapeout-demo")
UUID_RE = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

def _account_name_rule():
    """The name of the account running this, as a whole word, or None. It is read at run time so
    that no file has to spell a user name for the scan to look for it."""
    names = set()
    for get in (getpass.getuser, lambda: Path.home().name):
        try:
            names.add(get())
        except Exception:  # noqa: BLE001  (no name known: the path patterns below still apply)
            pass
    names = sorted(n for n in names if n and len(n) >= 3)
    if not names:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, names)) + r")(?![A-Za-z0-9])",
                      re.IGNORECASE)


# What no committed file may carry: machine paths, paths into a home directory's configuration, the
# running account's name, and em and en dashes. The path patterns are spelled with character classes
# and escapes so that this file does not carry them itself.
_ACCOUNT = _account_name_rule()
FORBIDDEN = tuple(re.compile(p, re.IGNORECASE) for p in
                  (r"C:[\\/]+Use[r]s", r"/c/Use[r]s", r"App[D]ata", r"~[\\/]+[.][A-Za-z]",
                   r"/home/[A-Za-z0-9._-]+/", r"/Use[r]s/[A-Za-z0-9._-]+/", r"\$HOME[\\/]",
                   r"%USERPROFIL[E]%", r"\$env:USERPROFIL[E]")) + (
    (_ACCOUNT,) if _ACCOUNT is not None else ()) + (
    re.compile("\u2014"), re.compile("\u2013"))


def dumps(obj) -> bytes:
    return (json.dumps(obj, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def norm(b: bytes) -> str:
    return hashlib.sha256(UUID_RE.sub(b"U", b)).hexdigest()[:20]


def jl(path: Path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


# ------------------------------------------------------------------------------------ derived


def derive_demo_followup1(pt: Path) -> bytes:
    base = pt / "coarse" / "rerun" / "demo"
    prep = json.loads((base / "prepare.json").read_text(encoding="utf-8"))
    out = {}
    for rel, v in prep.items():
        tag = rel.replace("/", "__")
        fx = base / "fixtures" / tag / Path(rel).name
        out[rel] = {"sha256": sha(fx.read_bytes()), "cand": v["cand"]}
    assert len(out) == 11, f"follow-up 1 demo sheets: {len(out)}, expected 11"
    return dumps(out)


def derive_demo_connect_pairs(pt: Path) -> bytes:
    conn = json.loads((pt / "proto" / "mine_connect_sweep2.json").read_text(encoding="utf-8"))
    out = {rel: [{"a": r["a"], "b": r["b"]} for r in rows] for rel, rows in conn.items()}
    n = sum(len(v) for v in out.values())
    assert n == 244, f"connect pairs: {n}, expected 244"
    return dumps(out)


def derive_demo_m2_refused_pairs(pt: Path) -> bytes:
    recs = jl(pt / "coarse" / "rerun2" / "demo" / "records.jsonl")
    pairs = {(r["group"], r["case"]) for r in recs
             if r["sample"] == "demo_connect" and r["design"] == "M2" and r["status"] == "REFUSED"}
    assert len(pairs) == 160, f"M2 refused pairs: {len(pairs)}, expected 160"
    return dumps(sorted([g, c] for g, c in pairs))


def derive_layouts_bases(pt: Path) -> bytes:
    fu1 = {}
    for r in jl(pt / "coarse" / "rerun" / "layouts_stock" / "records.jsonl"):
        if r["design"] == "M2" and r.get("call_index") == 1 and r.get("first_attempt") \
                and "experiment" not in r:
            if r["sample"] == "generated":
                k = f"gen/{r['group']}"
            elif r["sample"] == "star":
                k = f"star/{r['group'][:6]}"
            elif r["sample"] == "jct":
                k = f"jct/{r['case'].split(' ')[0]}"
            else:
                continue
            fu1.setdefault(k, set()).add(r["pre_norm"])
    bad = {k: sorted(v) for k, v in fu1.items() if len(v) != 1}
    assert not bad, f"layout bases with more than one hash: {bad}"
    assert len(fu1) == 67, f"layout bases: {len(fu1)}, expected 67"
    return dumps({k: next(iter(v)) for k, v in fu1.items()})


def _walk_report(x, path, bad, counts):
    """Every boolean must be true and every 'missing' list empty (check_build_report.py)."""
    if isinstance(x, dict):
        for k, v in x.items():
            _walk_report(v, f"{path}.{k}", bad, counts)
    elif isinstance(x, bool):
        counts["booleans"] += 1
        if not x:
            bad.append(path)
    elif isinstance(x, list) and path.endswith(".missing") and x:
        bad.append(f"{path}={x}")


def derive_fixtures_expected(pt: Path):
    r3 = pt / "coarse" / "rerun3"
    out = {"layouts_stock": {}, "probeB_vhp": None, "probeC_perf": {}, "probeC_perf_report": {}}
    for name in LAYOUT_STOCK:
        out["layouts_stock"][name] = sha((r3 / "layouts_stock" / "fixtures" / f"{name}.base.kicad_sch").read_bytes())
    out["probeB_vhp"] = sha((pt / "verify-sem-naming" / "vhp.kicad_sym").read_bytes())
    fx = r3 / "probeC" / "perf" / "fixtures"
    for sub in ("probe", "verify"):
        for f in sorted((fx / sub).iterdir()):
            stem = f.name.removesuffix(".kicad_sch")
            tag = stem.rsplit(".", 1)[1] if "." in stem else ""
            data = f.read_bytes()
            if tag in ("", "base"):
                out["probeC_perf"][f"{sub}/{f.name}"] = {"sha256": sha(data)}
            elif tag in ("aug", "k200", "free", "free200"):
                out["probeC_perf"][f"{sub}/{f.name}"] = {"norm": norm(data)}
            else:
                raise AssertionError(f"unexpected perf fixture {sub}/{f.name}")
    assert len(out["probeC_perf"]) == 18, f"perf fixtures: {len(out['probeC_perf'])}, expected 18"
    rep = json.loads((fx / "build_report.json").read_text(encoding="utf-8"))
    bad, counts = [], {"booleans": 0}
    _walk_report(rep, "report", bad, counts)
    assert not bad, f"build_report.json has false flags or non-empty missing lists: {bad}"
    pos = {"probe": {}, "verify": {}}
    for sheet, v in rep["probe"].items():
        pos["probe"][sheet] = {"placed": v["placed"]} if sheet == "k200" else {"aug": v["aug"]}
    for sheet, v in rep["verify"].items():
        pos["verify"][sheet] = {tag: {"x0": v[tag]["x0"]} for tag in ("free", "free200") if tag in v}
    out["probeC_perf_report"] = pos
    return dumps(out), counts["booleans"]


# ------------------------------------------------------------------------------------ scrubbed

_PATH = re.compile(
    r"(?:[A-Za-z]:|/[A-Za-z])[\\/]+(?:[^\s\\/;,()\"']+[\\/]+)*?(?P<root>pr57pt|pr57work)"
    r"(?P<rest>(?:[\\/]+[A-Za-z0-9_.+\-]+)*[\\/]*)")


def _label(m):
    return m.group("root") + m.group("rest").replace("\\", "/")


def scrub_strings(x):
    """Every string value: a path token that starts at a drive (or /c/) and runs through the
    scratch root becomes the label pr57pt/<dir>/<file> (or pr57work/...) with / separators."""
    if isinstance(x, str):
        return _PATH.sub(_label, x)
    if isinstance(x, list):
        return [scrub_strings(v) for v in x]
    if isinstance(x, dict):
        return {k: scrub_strings(v) for k, v in x.items()}
    return x


def scrub_caseidx(pt: Path, name: str) -> bytes:
    data = json.loads((pt / "coarse" / "caseidx" / f"{name}.json").read_text(encoding="utf-8"))
    return dumps(scrub_strings(data))


def scrub_gate(pt: Path, name: str) -> bytes:
    data = json.loads((pt / "coarse" / "dupref" / f"{name}.json").read_text(encoding="utf-8"))
    data["root"] = "scratch:coarse/" + re.split(r"[\\/]", data["root"].rstrip("\\/"))[-1]
    return dumps(data)


_USER = r"C:[\\/]+Use[r]s[\\/]+[^\\/]+[\\/]+App[D]ata[\\/]+Local[\\/]+"  # any account's profile
_TEMP_ROOT = re.compile(_USER + r"Temp[\\/]+pr57pt")
_KICAD_DEMOS = re.compile(_USER + r"Programs[\\/]+KiCad[\\/]+9\.0[\\/]+share[\\/]+kicad[\\/]+demos")


def scrub_handoff(pt: Path) -> bytes:
    """The scratch root becomes %TEMP%\\pr57pt (in the slash style it had) and KiCad's demos
    directory becomes <KiCad 9.0 install>\\share\\kicad\\demos; nothing else changes."""
    t = (pt / "HANDOFF.md").read_bytes().decode("utf-8")
    t = _TEMP_ROOT.sub(lambda m: "%TEMP%\\pr57pt" if "\\" in m.group(0) else "%TEMP%/pr57pt", t)
    t = _KICAD_DEMOS.sub(lambda m: "<KiCad 9.0 install>\\share\\kicad\\demos", t)
    return t.replace("\r\n", "\n").encode("utf-8")


def scrub_pressure_test(work: Path) -> bytes:
    """The one line saying why codex-spec was not run described the author's local tool
    configuration; it becomes a neutral line. Every other byte is the scratch original's."""
    t = (work / "pressure-test-report.md").read_bytes().decode("utf-8")
    hits = re.findall(r"(?m)^- \*\*Not run:\*\* codex-spec\..*$", t)
    assert len(hits) == 1, f"pressure-test-report.md: {len(hits)} codex-spec lines, expected 1"
    t = t.replace(hits[0], "- **Not run:** codex-spec, an external spec review that could not run in "
                  "this environment.")
    return t.encode("utf-8")


def repair_coarse_variant(work: Path) -> bytes:
    """The report's writer turned the backslash-n in rerun\\probeC\\notes into a line break;
    the path is restored. Every other byte is the scratch original's."""
    t = (work / "coarse-variant-report.md").read_bytes()
    broken = b"`rerun\\probeC\notes\\llm-workflow.json`"
    n = t.count(broken)
    assert n == 1, f"coarse-variant-report.md: {n} broken paths, expected 1"
    return t.replace(broken, b"`rerun\\probeC\\notes\\llm-workflow.json`")


# ---------------------------------------------------------------------------------------- main


def build(pt: Path, work: Path):
    out, notes = {}, []
    out["baselines/demo_followup1.json"] = derive_demo_followup1(pt)
    out["baselines/demo_connect_pairs.json"] = derive_demo_connect_pairs(pt)
    out["baselines/demo_m2_refused_pairs.json"] = derive_demo_m2_refused_pairs(pt)
    out["baselines/layouts_followup1_bases.json"] = derive_layouts_bases(pt)
    fe, flags = derive_fixtures_expected(pt)
    out["baselines/fixtures_expected.json"] = fe
    notes.append(f"build_report.json: {flags} boolean flags, all true, no missing pins")
    for g in GATES:
        out[f"baselines/{g}.json"] = scrub_gate(pt, g)
    for c in CASEIDX:
        out[f"caseidx/{c}.json"] = scrub_caseidx(pt, c)
    out["reports/HANDOFF.md"] = scrub_handoff(pt)
    out["reports/pressure-test-report.md"] = scrub_pressure_test(work)
    out["reports/coarse-variant-report.md"] = repair_coarse_variant(work)
    for r in REPORTS:
        out[f"reports/{r}"] = (work / r).read_bytes()
    return out, notes


def problems(out) -> list:
    found = []
    for rel, data in out.items():
        text = data.decode("utf-8")
        for rx in FORBIDDEN:
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                found.append(f"{rel}:{line}: {m.group(0)!r}")
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pt", required=True, type=Path, help='scratch root "pr57pt"')
    ap.add_argument("--work", required=True, type=Path, help='scratch root "pr57work"')
    ap.add_argument("--check", action="store_true", help="compare with the committed files, write nothing")
    a = ap.parse_args(argv)
    out, notes = build(a.pt, a.work)
    bad = problems(out)
    if bad:
        print("derive: forbidden strings in the derived output, nothing written:", file=sys.stderr)
        for b in bad:
            print("  " + b, file=sys.stderr)
        return 2
    if a.check:
        differ = [rel for rel, data in out.items()
                  if not (HARNESS / rel).is_file() or (HARNESS / rel).read_bytes() != data]
        for rel in differ:
            print(f"differs: {rel}")
        print(f"derive --check: {len(out) - len(differ)} of {len(out)} files equal")
        return 1 if differ else 0
    for rel, data in out.items():
        dst = HARNESS / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        print(f"wrote {rel} ({len(data)} bytes)")
    again = {rel: (HARNESS / rel).read_bytes() for rel in out}
    assert again == out, "a file read back differs from what was derived"
    assert not problems(again), "forbidden strings in the written files"
    for n in notes:
        print(n)
    print(f"derive: {len(out)} files, none carries a machine name or a dash")
    return 0


if __name__ == "__main__":
    sys.exit(main())
