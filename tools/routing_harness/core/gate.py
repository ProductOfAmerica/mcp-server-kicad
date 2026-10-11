"""The gate (wrong writes, false no-ops), refusal rates and pins left off their net for one design,
computed from one run's records with the hybrid report's definitions:
  probeA   strict = primary records not in the extra jobs (labels L02/L12/L13/L15, junctions
           '/own_' variants); gate over every primary record
  probeB   one call per (run_id, design); first attempt = all but step 2 of u1f_nc_on_spare_copy
  probeC   kind main/seq records of the three tools
  llm      attempt == "first" for the rate; gate over every call; planned pins from
           lw_designs_*_final.json
  layouts  first_attempt records per bucket; planned pins from finals.jsonl
  demo     samples demo_wptn, demo_connect, fallback; fallback160 = the 160 pairs M2's connect_pins
           refused in follow-up 2 (baselines/demo_m2_refused_pairs.json)
usage: gate.py <run name or run directory> <design> [--json out.json]

The run directory is WORK/runs/<run name> (hpaths.run_dir) and must hold run.json, which says which
readings the run took. Without the GUI reading (core/gui_reading.py) wrong_gui is null, not 0, and
unjudged counts only the cli reading; gui_not_taken counts the OK and NOOP records whose gui reading
was not taken. Each sample also lists its offenders (record ids), because a count cannot show which
case is the one DEC-23 or probeB's h16 call. A sample with no records for the design (its batch
did not run, wrote nothing, ran other designs, or every job crashed) is reported as
{"missing": ...}, never as an empty gate whose absent counts could read as 0. A crashed probeA job
writes a HARNESS_ERROR record that names no design: the missing message counts them, and probeA's
error offenders list them.
Every other key keeps its name and meaning, so a new JSON compares field by field with
baselines/gate_*.json ("root" there is the scratch directory the figures came from).
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import json  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402

TOOLS = ("wire_pins_to_net", "connect_pins", "auto_place_decoupling_cap")
ID_FIELDS = ("sample", "group", "case", "variant", "step", "run_id", "design")
NOT_TAKEN = ('gui reading: not taken (wrong_gui is null, not 0); the "0 wrong in both readings" '
             "criterion cannot be evaluated by this run")


def jl(p):
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]


class NoRecords(Exception):
    """A sample holds no records for the design."""


def need(rs, where, D):
    if not rs:
        raise NoRecords(f"no {D} records in {where}")
    return rs


def gate_counts(rs, gui_taken):
    out = Counter()
    for r in rs:
        out["calls"] += 1
        out[r.get("status")] += 1
        out["wrong_cli"] += bool(r.get("cli_wrong"))
        out["wrong_gui"] += bool(r.get("gui_wrong")) if gui_taken else 0
        if r.get("status") == "NOOP" and r.get("false_noop"):
            out["false_noop"] += 1
        if r.get("status") in ("OK", "NOOP") and (
                r.get("cli_wrong") is None or (gui_taken and r.get("gui_wrong") is None)):
            out["unjudged"] += 1
        if not gui_taken and r.get("status") in ("OK", "NOOP") and r.get("gui_wrong") is None:
            out["gui_not_taken"] += 1
        if "dup_ref" in (r.get("codes") or []):
            out["dup_ref"] += 1
    out = dict(out)
    if not gui_taken:
        out["wrong_gui"] = None
    return out


def rid(r):
    """The record's id, else its present identifying fields joined by '|'."""
    if r.get("id"):
        return str(r["id"])
    return "|".join(str(r[k]) for k in ID_FIELDS if r.get(k) not in (None, ""))


def offenders(rs, gui_taken):
    """Record ids per class: false no-ops, unjudged calls, ERROR and HARNESS_ERROR statuses, and
    wrong writes (the classes the gate's pass criteria name, which a count cannot identify)."""
    out = {"false_noop": [], "unjudged": [], "error": [], "wrong": []}
    for r in rs:
        if r.get("status") == "NOOP" and r.get("false_noop"):
            out["false_noop"].append(rid(r))
        if r.get("status") in ("OK", "NOOP") and (
                r.get("cli_wrong") is None or (gui_taken and r.get("gui_wrong") is None)):
            out["unjudged"].append(rid(r))
        if r.get("status") in ("ERROR", "HARNESS_ERROR"):
            out["error"].append(rid(r))
        if r.get("cli_wrong") or (gui_taken and r.get("gui_wrong")):
            out["wrong"].append(rid(r))
    return out


def rate(rs):
    n = len(rs)
    k = sum(r.get("status") == "REFUSED" for r in rs)
    return {"n": n, "refused": k, "pct": round(100 * k / n, 1) if n else None}


def probeA(root, D, gui_taken):
    recs = []
    for f in sorted((root / "probeA").glob("records_*.jsonl")):
        recs += [r for r in jl(f) if r.get("primary")]
    crashed = [r for r in recs if r.get("status") == "HARNESS_ERROR"]
    mine = [r for r in recs if r.get("design") == D]
    if not mine:
        raise NoRecords(f"no {D} records in probeA/records_*.jsonl"
                        + (f"; {len(crashed)} HARNESS_ERROR records (crashed jobs)" if crashed else ""))
    for r in mine:
        v = r.get("variant") or ""
        r["_extra"] = (r["group"] == "labels" and v.startswith(("L02/", "L12/", "L13/", "L15/"))) \
            or (r["group"] == "junctions" and "/own_" in v)
    off = offenders(mine, gui_taken)
    off["error"] += [rid(r) for r in crashed if r.get("design") is None]
    return {"gate": gate_counts(mine, gui_taken), "rate_strict": rate([r for r in mine if not r["_extra"]]),
            "harness_errors": len(crashed), "offenders": off}


def probeB(root, D, gui_taken):
    calls = {}
    for f in sorted((root / "probeB" / "rec").glob("*.jsonl")):
        for r in jl(f):
            if r.get("record_type") == "call" and r.get("design") == D:
                calls.setdefault(r["run_id"], r)
    rs = need(list(calls.values()), "probeB/rec/*.jsonl", D)
    first = [r for r in rs if not (r["run_id"].startswith("u1f_nc_on_spare_copy:") and r.get("step") == 2)]
    return {"gate": gate_counts(rs, gui_taken), "rate_first": rate(first),
            "offenders": offenders(rs, gui_taken)}


def probeC(root, D, gui_taken):
    rs = []
    for f in sorted((root / "probeC" / "parts").glob("*.jsonl")):
        rs += [r for r in jl(f) if r.get("kind") in ("main", "seq") and r.get("tool") in TOOLS
               and r.get("design") == D]
    need(rs, "probeC/parts/*.jsonl", D)
    return {"gate": gate_counts(rs, gui_taken), "rate": rate(rs), "offenders": offenders(rs, gui_taken)}


def llm(root, D, gui_taken):
    rs = []
    for f in sorted((root / "llm-workflow" / "parts").glob("*.jsonl")):
        rs += [r for r in jl(f) if r.get("design") == D]
    need(rs, "llm-workflow/parts/*.jsonl", D)
    off = defaultdict(Counter)
    for f in sorted((root / "llm-workflow").glob("lw_designs_*_final.json")):
        for x in json.loads(f.read_text(encoding="utf-8")):
            c = (x.get("final") or {}).get("cli") or {}
            if x.get("impl") == D and c.get("planned_pins") is not None:
                off[x["policy"]]["planned"] += c["planned_pins"]
                off[x["policy"]]["off"] += c["pins_not_on_plan_net"]
    return {"gate": gate_counts(rs, gui_taken), "rate_first": rate([r for r in rs if r.get("attempt") == "first"]),
            "planned_off": {k: dict(v) for k, v in off.items()}, "offenders": offenders(rs, gui_taken)}


def layouts(root, D, gui_taken):
    rs = need([r for r in jl(root / "layouts_stock" / "records.jsonl") if r.get("design") == D],
              "layouts_stock/records.jsonl", D)

    def bucket(r):
        if r["sample"] == "generated":
            return f"generated/{r['policy']}"
        if r["sample"] == "stock":
            return "stock/pins" if r["kind"] == "wire" else "stock/pairs"
        return r["sample"]
    rates = {}
    for b in sorted({bucket(r) for r in rs}):
        rates[b] = rate([r for r in rs if bucket(r) == b and r.get("first_attempt")])
    off = defaultdict(Counter)
    for f in jl(root / "layouts_stock" / "finals.jsonl"):
        if f.get("design") != D:
            continue
        c = (f.get("final") or {}).get("cli") or {}
        if "planned_pins" in c:
            off[f["policy"]]["planned"] += c["planned_pins"]
            off[f["policy"]]["off"] += c["pins_not_on_plan_net"]
            off[f["policy"]]["layouts_with_off"] += c["pins_not_on_plan_net"] > 0
    return {"gate": gate_counts(rs, gui_taken), "rates_first": rates,
            "planned_off": {k: dict(v) for k, v in off.items()}, "offenders": offenders(rs, gui_taken)}


def demo(root, D, gui_taken):
    rs = need([r for r in jl(root / "demo" / "records.jsonl") if r.get("design") == D],
              "demo/records.jsonl", D)
    pairs = json.loads((hpaths.BASELINES / "demo_m2_refused_pairs.json").read_text(encoding="utf-8"))
    m2_refused = {(g, c) for g, c in pairs}
    by = defaultdict(list)
    for r in rs:
        by[r["sample"]].append(r)
    fb = by.get("fallback", [])
    gated = [r for r in rs if r["sample"] in ("demo_wptn", "demo_connect", "fallback")]
    return {"gate": gate_counts(gated, gui_taken),
            "rate_wptn": rate(by.get("demo_wptn", [])), "rate_connect": rate(by.get("demo_connect", [])),
            "rate_fallback_all": rate(fb),
            "rate_fallback160": rate([r for r in fb if (r["group"], r["case"]) in m2_refused]),
            "offenders": offenders(gated, gui_taken)}


def main():
    args = [a for a in sys.argv[1:]]
    json_out = None
    if "--json" in args:
        i = args.index("--json")
        json_out = args[i + 1]
        del args[i:i + 2]
    if len(args) != 2:
        sys.exit("usage: gate.py <run name or run directory> <design> [--json out.json]")
    root, D = hpaths.run_dir(args[0]), args[1]
    run_json = root / "run.json"
    if not run_json.is_file():
        sys.exit(f"gate: {run_json} not found: the gate needs the run's run.json (written by run.py), "
                 "which says which readings the run took")
    run = json.loads(run_json.read_text(encoding="utf-8"))
    gui_taken = run.get("gui_reading") == "available"
    if not gui_taken:
        print(NOT_TAKEN)
    out = {"root": root.name, "design": D, "readings": ["cli", "gui"] if gui_taken else ["cli"]}
    for name, fn in (("probeA", probeA), ("probeB", probeB), ("probeC", probeC), ("llm", llm),
                     ("layouts", layouts), ("demo", demo)):
        try:
            out[name] = fn(root, D, gui_taken)
        except FileNotFoundError as e:
            missing = Path(e.filename) if e.filename else None
            try:
                what = missing.relative_to(root).as_posix() if missing else str(e)
            except ValueError:
                what = missing.name
            out[name] = {"missing": what}
        except NoRecords as e:
            out[name] = {"missing": str(e)}
    print(json.dumps(out, indent=1))
    if json_out:
        Path(json_out).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
