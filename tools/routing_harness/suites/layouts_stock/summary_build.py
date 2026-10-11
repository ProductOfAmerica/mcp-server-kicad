"""Summarise runner.py's records.jsonl / finals.jsonl / errors.jsonl and counterfactual.py's
records_cf.jsonl / replay_cf.jsonl (optional: counterfactual.py is not part of this repository) into
summary.json and summary_auto.md. Every count comes from those files only (no figure carried over
from follow-up 1). Reads and writes the run's layouts_stock output directory
(hpaths.suite_out("layouts_stock")).

Readings: cli = kicad-cli netlist (measured; the run's version is in run.json); gui = the GUI
load-cleanup emulation then kicad-cli (emulated; not available in this repository, see
core/gui_reading.py). A reading that was not taken is skipped: never counted clean, never counted
unjudged, and shown as None or n/a. Statuses, codes and byte checks are measured.
usage: summary_build.py [--tag T]   (reads records_T.jsonl etc. when a tag is given)
Run: uv run python tools/routing_harness/suites/layouts_stock/summary_build.py
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import json  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402

import readings  # noqa: E402

HERE = Path(__file__).resolve().parent  # source directory (read only)
OUT = hpaths.suite_out("layouts_stock")  # the run's outputs: records in, summaries out
NOT_TAKEN = tuple(rd for rd in ("cli", "gui") if rd not in readings.READINGS)
TAG = sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else ""
SFX = f"_{TAG}" if TAG else ""
DESIGNS = ("M2", "H", "Hb", "E")
PAIRS = (("H", "M2"), ("E", "M2"), ("E", "H"), ("Hb", "H"))
CHECKS = ("bad_merges", "splits", "renames", "class_bad", "pads_multi", "vanished", "appeared",
          "unloadable", "named_merge", "req_pad_multi", "note", "error")
F_R, F_F, F_E = (OUT / f"{n}{SFX}.jsonl" for n in ("records", "finals", "errors"))
F_CF, F_RP = OUT / f"records_cf{SFX}.jsonl", OUT / f"replay_cf{SFX}.jsonl"


def load(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


recs, fins, errs, cf, rp = load(F_R), load(F_F), load(F_E), load(F_CF), load(F_RP)
# designs are picked by name at run time (ROUTING_HARNESS_DESIGNS): follow-up 2's four first, then
# any other design found in the records; a design with no record gets no column
DESIGNS = tuple(d for d in (*DESIGNS, *sorted({r["design"] for r in recs + cf} - set(DESIGNS)))
                if any(r["design"] == d for r in recs + cf))


def bucket(r):
    s = r["sample"]
    if s in ("generated", "generated_cf"):
        return f"{s}/{r['policy']}"
    if s == "stock":
        return "stock/pins" if r["kind"] == "wire" else "stock/pairs"
    return s


def checks(r, side):
    d = r.get(f"{side}_detail") or {}
    return [k for k in CHECKS if d.get(k)]


def rid(r):
    return f"{r['sample']}|{r['group']}|{r.get('policy')}|{r['design']}|{r['case']}"


def clean_write(r):
    return (r is not None and r["status"] == "OK"
            and all(r.get(f"{rd}_wrong") is False and r.get(f"delivered_{rd}") is True
                    for rd in readings.READINGS))


def stats(rs):
    s = Counter()
    code_first = Counter()
    for r in rs:
        s["calls"] += 1
        s[r["status"]] += 1
        if r.get("first_attempt"):
            s["first"] += 1
            s[f"first_{r['status']}"] += 1
            if r["status"] == "REFUSED":
                for c in dict.fromkeys(r["codes"]) or ["(none)"]:
                    code_first[c] += 1
        for side in ("cli", "gui"):
            s[f"{side}_wrong"] += bool(r.get(f"{side}_wrong"))
            s[f"{side}_wrong_j3"] += bool(r.get(f"{side}_wrong_j3"))
        s["false_noop"] += bool(r.get("false_noop"))
        if r.get("redundant_write"):
            s["redundant_write"] += 1
            s["redundant_write_wrong"] += bool(r.get("cli_wrong") or r.get("gui_wrong"))
        s["refused_bytes_changed"] += r["status"] == "REFUSED" and r.get("bytes_unchanged") is False
        s["judge_error"] += bool(r.get("judge_error"))
        s["ok_undelivered"] += r["status"] == "OK" and (r.get("delivered_cli") is False or r.get("delivered_gui") is False)
    out = dict(s)
    out["first_refusal_pct"] = round(100 * s["first_REFUSED"] / s["first"], 1) if s["first"] else None
    out["refused_by_code_first"] = dict(code_first.most_common())
    for side in NOT_TAKEN:  # a reading that was not taken is None, not 0
        out[f"{side}_wrong"] = out[f"{side}_wrong_j3"] = None
    return out


S = {"inputs": {"records": len(recs), "finals": len(fins), "task_errors": len(errs), "cf_records": len(cf),
                "cf_replay_tasks": len(rp)},
     "readings": readings.LABEL,
     "scripts": {"calls": str(HERE / "runner.py"),
                 "cf": "counterfactual.py (scratch, not part of this repository)",
                 "summary": str(HERE / "summary_build.py")}}

# 1. per design per bucket
buckets = sorted({bucket(r) for r in recs})
S["per_bucket"] = {b: {d: stats([r for r in recs if bucket(r) == b and r["design"] == d]) for d in DESIGNS}
                   for b in buckets + ["ALL"] if b}
for d in DESIGNS:
    S["per_bucket"]["ALL"][d] = stats([r for r in recs if r["design"] == d])

# 2. wrong writes, false no-ops, redundant writes, wrong != wrong_j3, errors (main and cf)


def listing(rs):
    out = {"wrong": [], "false_noop": [], "redundant": [], "wrong_ne_j3": [], "errors": [],
           "judge_errors": [], "refused_bytes_changed": []}
    for r in rs:
        if r.get("cli_wrong") or r.get("gui_wrong"):
            out["wrong"].append({"id": r["id"], "design": r["design"], "case": rid(r), "call": r.get("call"),
                                 "status": r["status"], "cli": checks(r, "cli") if r.get("cli_wrong") else [],
                                 "gui": checks(r, "gui") if r.get("gui_wrong") else [],
                                 "cli_detail": r.get("cli_detail"), "gui_detail": r.get("gui_detail"),
                                 "wrong_j3": [r.get("cli_wrong_j3"), r.get("gui_wrong_j3")],
                                 "fixture_dir": r.get("wrong_dir"), "msg_head": r["msg_head"][:300]})
        if r.get("false_noop"):
            out["false_noop"].append({"id": r["id"], "design": r["design"], "case": rid(r), "call": r.get("call"),
                                      "delivered": [r.get("delivered_cli"), r.get("delivered_gui")],
                                      "detail": [r.get("cli_detail"), r.get("gui_detail")],
                                      "fixture_dir": r.get("wrong_dir"), "msg_head": r["msg_head"][:300]})
        if r.get("redundant_write"):
            out["redundant"].append({"id": r["id"], "design": r["design"], "case": rid(r),
                                     "clean": readings.clean(r)})
        for side in ("cli", "gui"):
            if r.get(f"{side}_wrong") is not None and bool(r.get(f"{side}_wrong")) != bool(r.get(f"{side}_wrong_j3")):
                out["wrong_ne_j3"].append({"id": r["id"], "reading": side, "wrong": r.get(f"{side}_wrong"),
                                           "wrong_j3": r.get(f"{side}_wrong_j3"), "checks": checks(r, side)})
        if r["status"] == "ERROR":
            out["errors"].append({"id": r["id"], "call": r.get("call"), "msg": r["msg_head"][:300],
                                  "traceback_tail": (r.get("traceback") or "")[-800:]})
        if r.get("judge_error"):
            out["judge_errors"].append({"id": r["id"], "tail": r["judge_error"][-500:]})
        if r["status"] == "REFUSED" and r.get("bytes_unchanged") is False:
            out["refused_bytes_changed"].append(r["id"])
    return out


S["listing"] = listing(recs)
S["listing_cf"] = listing(cf)
S["task_errors"] = errs
S["cf_replay_errors"] = [t for t in rp if t.get("error")]

# 3. same-input refusal sets


def key(r):
    # Fix 2026-10-03: stock, star and jct items share group/policy/call_index across cases (one
    # call per item on a fresh copy), so without the case the dict kept one item per sheet (stock
    # showed "pairs 3"). The case is unique per first attempt in every sample (checked: 0
    # duplicates in 3084 first attempts), and first-attempt call_index agrees across designs.
    return (bucket(r), r["group"], r.get("policy"), r.get("call_index"), r.get("case"))


def same_input(rs, require_same_bytes=True):
    firsts = defaultdict(dict)
    for r in rs:
        if r.get("first_attempt"):
            firsts[key(r)][r["design"]] = r
    out = {}
    for a, b in PAIRS:
        per = defaultdict(lambda: {"first_attempt_pairs": 0, "identical_input": 0, f"{a}_only": [],
                                   f"{b}_only": [], "both_refused": 0})
        for k, dd in firsts.items():
            if a not in dd or b not in dd:
                continue
            x = per[k[0]]
            x["first_attempt_pairs"] += 1
            ra, rb = dd[a], dd[b]
            if require_same_bytes and ra["pre_norm"] != rb["pre_norm"]:
                continue
            x["identical_input"] += 1
            if ra["status"] == "REFUSED" and rb["status"] == "REFUSED":
                x["both_refused"] += 1
            elif ra["status"] == "REFUSED":
                x[f"{a}_only"].append((ra, rb))
            elif rb["status"] == "REFUSED":
                x[f"{b}_only"].append((rb, ra))
        res = {}
        for bk, x in sorted(per.items()):
            row = {k: v for k, v in x.items() if not isinstance(v, list)}
            for side in (a, b):
                got = x[f"{side}_only"]
                row[f"{side}_only"] = len(got)
                row[f"{side}_only_by_code"] = dict(Counter(
                    c for ref, _o in got for c in dict.fromkeys(ref["codes"])).most_common())
                row[f"{side}_only_other_wrote_clean"] = sum(clean_write(o) for _r, o in got)
                row[f"{side}_only_ids"] = [
                    f"{ref['id']} | {ref['case']} | {ref['codes']} | other {o['status']}"
                    + (" clean" if clean_write(o) else " not-clean" if o["status"] == "OK" else "")
                    for ref, o in got]
            res[bk] = row
        out[f"{a}_vs_{b}"] = res
    return out


S["same_input_identical_bytes"] = same_input(recs, True)
S["same_input_cf"] = same_input(cf, False)
# cf consistency: the four probes of one step saw the same bytes; replay M2 vs runner M2; probe M2 vs replay
cf_steps = defaultdict(set)
for r in cf:
    cf_steps[key(r)].add(r["pre_norm"])
m2main = {(r["group"], r["policy"], r["case"]): r["status"] for r in recs
          if r["sample"] == "generated" and r["design"] == "M2"}
agree = Counter()
for t in rp:
    name, pol = t["task"].rsplit(" ", 1)
    for x in t["replay"]:
        agree["same" if m2main.get((name, pol, x["case"])) == x["status"] else
              f"differs (runner {m2main.get((name, pol, x['case']))} vs replay {x['status']})"] += 1
S["cf_checks"] = {"steps": len(cf_steps), "steps_with_one_pre_state": sum(len(v) == 1 for v in cf_steps.values()),
                  "replay_M2_vs_runner_M2": dict(agree),
                  "probe_M2_vs_replay_M2": dict(Counter(
                      "same" if r["status"] == r.get("replay_m2_status") else f"{r['status']} vs {r.get('replay_m2_status')}"
                      for r in cf if r["design"] == "M2"))}
S["cf_per_design"] = {b: {d: stats([r for r in cf if bucket(r) == b and r["design"] == d]) for d in DESIGNS}
                      for b in sorted({bucket(r) for r in cf})}

# 4. E [E:connected] refusals and what M2 / H did on the same input


def e_connected(rs, same_bytes):
    firsts = defaultdict(dict)
    for r in rs:
        if r.get("first_attempt"):
            firsts[key(r)][r["design"]] = r
    out = {"E_connected_first_attempts": 0}
    for other in ("M2", "H"):
        out[f"comparable_with_{other}"] = 0
        out[f"{other}_clean_write"] = 0
        out[f"{other}_status"] = Counter()
        out[f"{other}_clean_ids"] = []
    for k, dd in firsts.items():
        e = dd.get("E")
        if not e or "connected" not in e["codes"]:
            continue
        out["E_connected_first_attempts"] += 1
        for other in ("M2", "H"):
            o = dd.get(other)
            if o is None or (same_bytes and o["pre_norm"] != e["pre_norm"]):
                continue
            out[f"comparable_with_{other}"] += 1
            out[f"{other}_status"][o["status"]] += 1
            if clean_write(o):
                out[f"{other}_clean_write"] += 1
                out[f"{other}_clean_ids"].append(e["id"] + " | " + e["case"])
    for other in ("M2", "H"):
        out[f"{other}_status"] = dict(out[f"{other}_status"])
    return out


S["E_connected"] = {
    "all_E_calls_refused_connected": sum(1 for r in recs if r["design"] == "E" and "connected" in r["codes"]),
    "per_bucket_identical_bytes": {b: e_connected([r for r in recs if bucket(r) == b], True) for b in buckets},
    "cf": {b: e_connected([r for r in cf if bucket(r) == b], False) for b in sorted({bucket(r) for r in cf})}}

# planned pins left off their plan net (generated)
off = {}
for f in fins:
    a = off.setdefault(f"{f['policy']}|{f['design']}", {"layouts": 0, "cli": Counter(), "gui": Counter()})
    a["layouts"] += 1
    for rd in ("cli", "gui"):
        if rd in NOT_TAKEN:  # skipped: never counted unjudged
            continue
        x = f["final"].get(rd) or {}
        if "planned_pins" not in x:
            a[rd]["unjudged"] += 1
            continue
        a[rd]["planned"] += x["planned_pins"]
        a[rd]["off"] += x["pins_not_on_plan_net"]
        a[rd]["layouts_with_off"] += x["pins_not_on_plan_net"] > 0
        a[rd]["layouts_with_short"] += bool(x["shorts"])
for a in off.values():
    for rd in ("cli", "gui"):
        a[rd] = dict(a[rd])
S["planned_pins_off_net"] = dict(sorted(off.items()))
S["final_shorts"] = [f"{f['group']} {f['policy']} {f['design']} {rd}: {s}" for f in fins
                     for rd in ("cli", "gui") for s in (f["final"].get(rd) or {}).get("shorts", [])]


def stop_codes(item):
    """The codes that stopped one off-net pin: refused lines naming it, else whole-call codes."""
    line = [c for x in item["calls"] for c in x["pin_line_codes"]]
    call = [c for x in item["calls"] if x["status"] == "REFUSED" for c in x["call_codes"]]
    return list(dict.fromkeys(line)), list(dict.fromkeys(call))


S["off_net_pins"] = {}
for f in fins:
    for it in f.get("off_net", []):
        line, call = stop_codes(it)
        S["off_net_pins"].setdefault(f"{f['policy']}|{f['design']}", []).append(
            {"layout": f["group"], "net": it["net"], "pin": it["pin"], "readings": it["readings"],
             "pin_line_codes": line, "call_codes": call,
             "calls": [f"{x['case']}: {x['status']} {x['call_codes']} line {x['pin_line_codes']}" for x in it["calls"]]})
S["off_net_by_code"] = {}
for k, items in S["off_net_pins"].items():
    c = Counter()
    for it in items:
        codes = it["pin_line_codes"] or [f"(no own line) {x}" for x in it["call_codes"]] or ["(never refused)"]
        for x in dict.fromkeys(codes):
            c[x] += 1
    S["off_net_by_code"][k] = dict(c.most_common())

# Hb vs H: every record where the statuses or codes differ (main, matched by call position)
hb_diff = []
hm = {(r["sample"], r["group"], r.get("policy"), r["case"]): r for r in recs if r["design"] == "H"}
for r in recs:
    if r["design"] != "Hb":
        continue
    h = hm.get((r["sample"], r["group"], r.get("policy"), r["case"]))
    if h is None or (h["status"], h["codes"]) != (r["status"], r["codes"]) or \
            (h.get("cli_wrong"), h.get("gui_wrong")) != (r.get("cli_wrong"), r.get("gui_wrong")):
        hb_diff.append(f"{rid(r)}: H {h['status'] + str(h['codes']) if h else 'no such call'} / "
                       f"Hb {r['status']}{r['codes']} same_input={h is not None and h['pre_norm'] == r['pre_norm']}")
S["Hb_vs_H_record_differences"] = hb_diff

# star and junction outcomes
S["star_jct"] = {}
for r in recs:
    if r["sample"] in ("star", "jct"):
        S["star_jct"].setdefault(f"{r['sample']} {r['group'] if r['sample'] == 'jct' else r['group'][:6]} {r['case']}", {})[r["design"]] = (
            f"{r['status']}{r['codes'] or ''}"
            + (" d" + "".join("-" if rd in NOT_TAKEN else str(int(bool(r.get(f"delivered_{rd}"))))
                              for rd in ("cli", "gui")) if r["status"] in ("OK", "NOOP") else "")
            + (" WRONG" if r.get("cli_wrong") or r.get("gui_wrong") else ""))

(OUT / f"summary{SFX}.json").write_text(json.dumps(S, indent=1, default=list), encoding="utf-8")

# ------------------------------------------------------------------------------- markdown
md = []
w = md.append
w(f"# Follow-up 2, layouts_stock batch: {', '.join(DESIGNS)} on identical inputs\n")
w(f"Calls and judging: `runner.py` -> `records{SFX}.jsonl`, `finals{SFX}.jsonl`; same-state replay: "
  f"`counterfactual.py` -> `records_cf{SFX}.jsonl`; this file and `summary{SFX}.json`: `summary_build.py`. "
  f"All in this directory. Readings: {readings.LABEL}. cli = kicad-cli netlist (measured); gui = GUI "
  "CleanUp emulation (emulated; not available in this repository). A column for a reading that was "
  "not taken shows None or n/a.\n")
w(f"Inputs: {S['inputs']}\n")
L, LC = S["listing"], S["listing_cf"]
w("## Wrong writes, false no-ops, errors\n")
for name, lst in (("main", L), ("cf", LC)):
    w(f"- {name}: wrong {len(lst['wrong'])}, false no-op {len(lst['false_noop'])}, ERROR {len(lst['errors'])}, "
      f"judge errors {len(lst['judge_errors'])}, refused with bytes changed {len(lst['refused_bytes_changed'])}, "
      f"wrong != wrong_j3 {len(lst['wrong_ne_j3'])}")
    for x in lst["wrong"]:
        w(f"  - WRONG {x['case']} `{x['call']}` {x['status']} cli {x['cli']} gui {x['gui']} j3 {x['wrong_j3']} ({x['fixture_dir']})")
    for x in lst["false_noop"]:
        w(f"  - FALSE NO-OP {x['case']} `{x['call']}` delivered {x['delivered']}")
    for x in lst["errors"]:
        w(f"  - ERROR {x['id']} `{x['call']}`: {x['msg'][:200]}")
    for x in lst["wrong_ne_j3"]:
        w(f"  - wrong != wrong_j3: {x}")
w(f"- task errors: {len(errs)}; cf replay errors: {len(S['cf_replay_errors'])}\n")
w("## Per design per sample (main run)\n")
cols = ("calls", "OK", "NOOP", "REFUSED", "ERROR", "first", "first_REFUSED", "first_refusal_pct",
        "cli_wrong", "gui_wrong", "false_noop", "redundant_write", "redundant_write_wrong", "ok_undelivered")
w("| bucket | design | " + " | ".join(cols) + " |")
w("|" + "---|" * (len(cols) + 2))
for b, dd in S["per_bucket"].items():
    for d, st in dd.items():
        w(f"| {b} | {d} | " + " | ".join(str(st.get(c, 0)) for c in cols) + " |")
w("\nFirst-attempt refusals by code (main):\n")
for b, dd in S["per_bucket"].items():
    if b == "ALL":
        continue
    w(f"- {b}: " + "; ".join(f"{d} {dd[d]['refused_by_code_first']}" for d in DESIGNS if dd[d]["refused_by_code_first"]))
w("\n## Planned pins left off their plan net (generated, end of layout)\n")
w("| policy/design | layouts | cli off/planned | gui off/planned | layouts with off (cli/gui) | layouts with short (cli/gui) |")
w("|---|---|---|---|---|---|")
for k, a in S["planned_pins_off_net"].items():
    c, g = a["cli"], a["gui"]
    if "gui" in NOT_TAKEN:
        g = {"off": "n/a", "planned": "n/a", "layouts_with_off": "n/a", "layouts_with_short": "n/a"}
    w(f"| {k} | {a['layouts']} | {c.get('off', 0)}/{c.get('planned', 0)} | {g.get('off', 0)}/{g.get('planned', 0)} |"
      f" {c.get('layouts_with_off', 0)}/{g.get('layouts_with_off', 0)} | {c.get('layouts_with_short', 0)}/{g.get('layouts_with_short', 0)} |")
w(f"\nOff-net pins by stopping code: {S['off_net_by_code']}\n")
w(f"Shorts at the end of a layout: {len(S['final_shorts'])}" + "".join(f"\n- {s}" for s in S["final_shorts"]) + "\n")
for k in ("adaptive|H", "adaptive|E"):
    items = S["off_net_pins"].get(k, [])
    w(f"### Off-net under {k}: {len(items)} pins\n")
    for it in items:
        w(f"- {it['layout']} {it['net']} {it['pin']} ({'/'.join(it['readings'])}): line {it['pin_line_codes']} call {it['call_codes']}")
    w("")
w("## Same-input refusal sets (first attempts)\n")
for title, src in (("identical bytes (main run)", S["same_input_identical_bytes"]),
                   ("counterfactual (all four on M2's state)", S["same_input_cf"])):
    w(f"### {title}\n")
    for pair, per in src.items():
        a, b = pair.split("_vs_")
        for bk, row in per.items():
            w(f"- {pair} {bk}: pairs {row['first_attempt_pairs']}, identical {row['identical_input']}, both refused "
              f"{row['both_refused']}, {a}-only {row[f'{a}_only']} {row[f'{a}_only_by_code']}, {b}-only "
              f"{row[f'{b}_only']} {row[f'{b}_only_by_code']}")
    w("")
w(f"cf checks: {S['cf_checks']}\n")
w("## E [E:connected] refusals (first attempts)\n")
w(f"- all E calls refused with [E:connected] (main, any attempt): {S['E_connected']['all_E_calls_refused_connected']}")
for title, src in (("identical bytes", S["E_connected"]["per_bucket_identical_bytes"]), ("cf", S["E_connected"]["cf"])):
    for bk, x in src.items():
        if x["E_connected_first_attempts"]:
            w(f"- {title} {bk}: E connected {x['E_connected_first_attempts']}; vs M2 comparable "
              f"{x['comparable_with_M2']}, M2 clean write {x['M2_clean_write']} {x['M2_status']}; vs H comparable "
              f"{x['comparable_with_H']}, H clean write {x['H_clean_write']} {x['H_status']}")
w(f"\n## Hb vs H: {len(hb_diff)} records differ\n")
for x in hb_diff[:25]:
    w(f"- {x}")
w("\n## Star and junction fixtures (status, delivered cli/gui)\n")
for k, v in S["star_jct"].items():
    w(f"- {k}: " + ", ".join(f"{d} {v.get(d)}" for d in DESIGNS))
(OUT / f"summary_auto{SFX}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
print("\n".join(md))
