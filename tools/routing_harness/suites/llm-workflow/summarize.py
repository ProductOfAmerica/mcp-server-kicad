"""Follow-up 2, llm-workflow batch: merge parts/*.jsonl into records.jsonl and compute every figure
of summary.json (printed to stdout). Reads only this suite's output directory of the run, and the plan
block of lw_designs.py (its source directory); records.jsonl and summary.json are written beside parts/.
Run: uv run python tools/routing_harness/suites/llm-workflow/summarize.py (ROUTING_HARNESS_RUN names the run)

Same input: two records (of two designs) are the same input when they share part, case, variant,
tool, kw and input_key (uuid-blanked digest of every file before the call). A labelled call whose
pre-call state differs between two designs is not a same-input pair (counted separately).
First attempt: attempt == "first" (a plan's own call; retries, fallbacks and replays excluded).
Planned pins off their net: lw_designs_<plan>_final.json, final_check of follow-up 1 unchanged
(pins_not_on_plan_net); "strict" adds, for nets the plan wires with wire_pins_to_net, the pins of
the largest component when that component does not carry the plan's name (the follow-up 1 metric
counts one component of a net as on-net even when nothing labelled it).
"""

import json
import re
from collections import Counter, defaultdict
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import readings  # noqa: E402

HERE = Path(__file__).resolve().parent  # source directory: lw_designs.py
OUT = hpaths.suite_out("llm-workflow")  # the run's parts/, finals and summaries
DES = ("M2", "H", "Hb", "E", "Hb2", "X")  # every design a batch can hold; the summary covers those present
CHECKS = ("bad_merges", "splits", "renames", "class_bad", "pads_multi", "vanished", "appeared",
          "named_merge", "req_pad_multi", "unloadable")
REFLINE = re.compile(r"^- REFUSED ((?:\[[MKHE]:\w+\]\s*)+)(\S+?:\S+?): (.*)$")


def load():
    recs = []
    with open(OUT / "records.jsonl", "w", encoding="utf-8") as out:
        for f in sorted((OUT / "parts").glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    recs.append(r)
                    out.write(json.dumps(r) + "\n")
    return recs


def rid(r):
    return f"{r['part']} | {r['case']} | {r['variant']}"


def ikey(r):
    return (r["part"], r["case"], r["variant"], r["tool"], r.get("kw"), r.get("input_key"))


def lkey(r):
    return (r["part"], r["case"], r["variant"], r["tool"], r.get("kw"))


def fired(r, side):
    d = r.get(f"{side}_detail") or {}
    return [k for k in CHECKS if d.get(k)]


def wrong_entry(r):
    return {"id": rid(r), "design": r["design"], "call": f"{r['tool']}({r.get('kw')})",
            "status": r["status"], "cli_wrong": r["cli_wrong"], "gui_wrong": r["gui_wrong"],
            "cli_wrong_j3": r.get("cli_wrong_j3"), "gui_wrong_j3": r.get("gui_wrong_j3"),
            "cli_checks": fired(r, "cli"), "gui_checks": fired(r, "gui"),
            "cli_detail": {k: (r.get("cli_detail") or {}).get(k) for k in fired(r, "cli")},
            "gui_detail": {k: (r.get("gui_detail") or {}).get(k) for k in fired(r, "gui")},
            "wrong_dir": r.get("wrong_dir")}


def main():
    recs = load()
    by = defaultdict(list)
    for r in recs:
        by[r["design"]].append(r)
    S = {"records": len(recs), "per_design": {}, "same_input": {}, "e_connected": {},
         "errors": [], "wrong_ne_j3": [], "dec23_like": [], "hb_vs_h": {}, "plans": {}}
    # uniqueness of the same-input key per design
    dupk = Counter((r["design"],) + ikey(r) for r in recs)
    S["dup_keys"] = [list(k) for k, n in dupk.items() if n > 1]
    for dz in DES:
        if dz not in by:
            continue
        rs = by[dz]
        st = Counter(r["status"] for r in rs)
        first = [r for r in rs if r.get("attempt") == "first"]
        fr = sum(r["status"] == "REFUSED" for r in first)
        wr_cli = [wrong_entry(r) for r in rs if r.get("cli_wrong")]
        wr_gui = [wrong_entry(r) for r in rs if r.get("gui_wrong")]
        wrong_any = [wrong_entry(r) for r in rs if r.get("cli_wrong") or r.get("gui_wrong")]
        fnoop = [{"id": rid(r), "call": f"{r['tool']}({r.get('kw')})",
                  "delivered": [r.get("delivered_cli"), r.get("delivered_gui")]}
                 for r in rs if r.get("false_noop")]
        red = [{"id": rid(r), "call": f"{r['tool']}({r.get('kw')})",
                "clean": readings.clean(r)}
               for r in rs if r.get("redundant_write")]
        changed_refusal = [rid(r) for r in rs if r["status"] in ("REFUSED", "ERROR")
                           and not r.get("bytes_unchanged", True)]
        ok_not_dlv = [rid(r) for r in rs if r["status"] == "OK"
                      and (r.get("delivered_cli") is False or r.get("delivered_gui") is False)]
        S["per_design"][dz] = {
            "calls": len(rs), "status": dict(st),
            "first_attempts": len(first), "first_refused": fr,
            "first_refusal_rate": round(fr / len(first), 4) if first else None,
            "first_refused_codes": dict(Counter(c for r in first if r["status"] == "REFUSED"
                                                for c in dict.fromkeys(r["codes"]))),
            "attempts": dict(Counter(r.get("attempt") for r in rs)),
            "wrong_cli": len(wr_cli), "wrong_gui": len(wr_gui) if readings.GUI_AVAILABLE else None,
            "wrong": wrong_any,
            "false_noops": fnoop, "redundant_writes": red,
            "redundant_clean": sum(x["clean"] for x in red),
            "refused_or_error_but_changed": changed_refusal,
            "ok_not_delivered": ok_not_dlv,
        }
        for r in rs:
            if r["status"] == "ERROR":
                S["errors"].append({"id": rid(r), "design": dz, "msg": r.get("msg", "")[:600]})
            for side in ("cli", "gui"):
                w, j = r.get(f"{side}_wrong"), r.get(f"{side}_wrong_j3")
                if w is not None and bool(w) != bool(j):
                    S["wrong_ne_j3"].append({"id": rid(r), "design": dz, "side": side,
                                             "wrong": w, "wrong_j3": j,
                                             "checks": fired(r, side)})
                d = r.get(f"{side}_detail") or {}
                pw = [n for k in ("vanished", "appeared") for n in (d.get(k) or [])
                      if str(n[0]).startswith("#")]
                if pw:
                    S["dec23_like"].append({"id": rid(r), "design": dz, "side": side, "nodes": pw})
    # same-input refusal sets
    idx = {(r["design"],) + ikey(r): r for r in recs}
    lab = defaultdict(dict)
    for r in recs:
        lab[lkey(r)][r["design"]] = r

    def only(a, b):
        same, diff_input, out = 0, 0, []
        for lk, d in lab.items():
            if a not in d or b not in d:
                continue
            ra, rb = d[a], d[b]
            if ra.get("input_key") != rb.get("input_key"):
                diff_input += 1
                continue
            same += 1
            if ra["status"] == "REFUSED" and rb["status"] != "REFUSED":
                out.append({"id": rid(ra), "codes": ra["codes"], "other_status": rb["status"],
                            "other_clean": rb["status"] == "OK" and readings.clean(rb),
                            "attempt": ra.get("attempt")})
        return {"pairs_same_input": same, "pairs_diverged_input": diff_input, "n": len(out),
                "by_code": dict(Counter(c for x in out for c in dict.fromkeys(x["codes"]))),
                "ids": out}

    for a, b in (("H", "M2"), ("M2", "H"), ("E", "M2"), ("E", "H"), ("Hb", "H"), ("H", "Hb"),
                 ("M2", "E"), ("H", "E")):
        S["same_input"][f"{a}-only vs {b}"] = only(a, b)
    # E [E:connected]
    conn = [r for r in by["E"] if r["status"] == "REFUSED" and "connected" in r["codes"]]
    rows = []
    for r in conn:
        row = {"id": rid(r), "attempt": r.get("attempt"), "codes": r["codes"]}
        for o in ("M2", "H"):
            ro = idx.get((o,) + ikey(r))
            if ro is None:
                row[o] = "no same-input record"
            else:
                clean = (ro["status"] == "OK" and readings.clean(ro)
                         and all(ro.get(f"delivered_{rd}") for rd in readings.READINGS))
                row[o] = "clean-write" if clean else ro["status"] + (
                    " wrong" if (ro.get("cli_wrong") or ro.get("gui_wrong")) else "")
        rows.append(row)
    S["e_connected"] = {
        "n": len(conn), "first_attempt": sum(r.get("attempt") == "first" for r in conn),
        "M2_clean_same_input": sum(x["M2"] == "clean-write" for x in rows),
        "H_clean_same_input": sum(x["H"] == "clean-write" for x in rows),
        "M2_no_same_input": sum(x["M2"] == "no same-input record" for x in rows),
        "H_no_same_input": sum(x["H"] == "no same-input record" for x in rows),
        "M2_outcomes": dict(Counter(x["M2"] for x in rows)),
        "H_outcomes": dict(Counter(x["H"] for x in rows)), "rows": rows}
    # Hb vs H: same-input records whose outcome differs
    hb = []
    for lk, d in lab.items():
        if "H" in d and "Hb" in d and d["H"].get("input_key") == d["Hb"].get("input_key"):
            a, b = d["H"], d["Hb"]
            fa = (a["status"], tuple(a["codes"]), a.get("cli_wrong"), a.get("gui_wrong"),
                  a.get("delivered_cli"), a.get("delivered_gui"))
            fb = (b["status"], tuple(b["codes"]), b.get("cli_wrong"), b.get("gui_wrong"),
                  b.get("delivered_cli"), b.get("delivered_gui"))
            if fa != fb:
                hb.append({"id": rid(a), "H": list(fa), "Hb": list(fb),
                           "Hb_msg": b.get("msg_head", "")[:200]})
    S["hb_vs_h"] = {"differ_same_input": hb, "n": len(hb)}
    # plans: planned pins off their net
    for f in sorted(OUT.glob("lw_designs_*_final.json")):
        for x in json.loads(f.read_text(encoding="utf-8")):
            if "final" not in x:
                S["plans"].setdefault(x["design"], {})[x["policy"]] = {"build_error": x.get("build_error")}
                continue
            fc = x["final"]
            ent = {"calls": x["calls"]}
            for side in ("cli", "gui"):
                c = fc.get(side) or {}
                ent[side] = {k: c.get(k) for k in ("planned_pins", "pins_not_on_plan_net",
                                                   "unloadable", "shorts", "misnamed", "error")
                             if k in c}
                ent[side]["incomplete"] = c.get("incomplete")
            ent["pinmap"] = fc.get("pinmap")
            if "erc_rc" in fc:
                ent["erc_rc"] = fc["erc_rc"]
            S["plans"].setdefault(x["design"], {}).setdefault(x["policy"], {})[x["impl"]] = ent
    plan_blockers(S, recs)
    (OUT / "summary.json").write_text(json.dumps(S, indent=1, default=list), encoding="utf-8")
    report(S)


def load_plans():
    """The plan dicts D1, D2, D3, D1PS, D1DER, executed from lw_designs.py's plan block only."""
    src = (HERE / "lw_designs.py").read_text(encoding="utf-8")
    block = src[src.index("D1 = {"):src.index("ALSO = {")]
    ns = {}
    exec(block, ns)  # noqa: S102 (plain dict literals)
    return {ns[k]["name"]: ns[k] for k in ("D1", "D2", "D3", "D1PS", "D1DER")}


def plan_blockers(S, recs):
    """Per plan/policy/design: off-net pins (follow-up 1 metric plus strict), each with the
    refusal codes that named it in that design's own sequence (replay excluded)."""
    plans = load_plans()
    seq = defaultdict(list)
    for r in recs:
        if r["part"].startswith("lw_designs") and r.get("attempt") != "replay":
            seq[(r.get("scenario"), r["design"])].append(r)
    for pname, pols in S["plans"].items():
        plan = plans[pname]
        for pol, des in pols.items():
            if not isinstance(des, dict) or "build_error" in des:
                continue
            for dz, ent in des.items():
                pm = ent.get("pinmap") or {}
                wire_nets = {c[1] for c in plan["calls"] if c[0] == "wire"} | \
                    {n for c in plan["calls"] if c[0] == "decap" for n in (c[6], c[7])}
                netpads = defaultdict(set)
                for c in plan["calls"]:
                    if c[0] == "wire":
                        prs = [(c[1], p) for p in c[2]]
                    elif c[0] == "connect":
                        prs = [(c[1], c[2]), (c[1], c[3])]
                    else:
                        prs = [(c[6], (c[1], "1")), (c[7], (c[1], "2"))]
                    for net, (rf, tx) in prs:
                        nums = pm.get(f"{rf}:{tx}")
                        for n in (nums if isinstance(nums, list) else []):
                            netpads[net].add((rf, n))
                # tag -> pad
                tagpad = {}
                for t, nums in pm.items():
                    rf = t.split(":", 1)[0]
                    for n in (nums if isinstance(nums, list) else []):
                        tagpad.setdefault(t, set()).add((rf, n))
                hist = defaultdict(list)
                for r in seq[(f"run_designs:{pname}:{pol}", dz)]:
                    if r["status"] != "REFUSED":
                        continue
                    lines = [REFLINE.match(x) for x in (r.get("msg") or "").splitlines()]
                    lines = [m for m in lines if m]
                    if lines and r["tool"] == "wire_pins_to_net":
                        for m in lines:
                            t = m.group(2)
                            rf = t.split(":", 1)[0]
                            pads = tagpad.get(t) or {(rf, t.split(":", 1)[1])}
                            for pd in pads:
                                hist[pd].append({"call": r["step_label"],
                                                 "codes": re.findall(r"\[[MKHE]:(\w+)\]", m.group(1))})
                    else:
                        for pd in r.get("pads") or []:
                            hist[tuple(pd)].append({"call": r["step_label"], "codes": r["codes"]})
                for side in ("cli", "gui"):
                    c = ent.get(side) or {}
                    inc = c.get("incomplete")
                    if inc is None:
                        continue
                    off = {tuple(p) for v in inc.values() for p in v}
                    strict = set(off)
                    for net, nm in (c.get("misnamed") or {}).items():
                        if net in wire_nets:
                            strict |= netpads[net] - off
                    c["strict_off"] = len(strict)
                    c["off_pins"] = [{"pad": list(p), "net": next((n for n, s in netpads.items()
                                                                   if p in s), None),
                                      "strict_only": p not in off,
                                      "refusals": hist.get(p, [])} for p in sorted(strict)]


def report(S):
    P = print
    P(f"readings: {readings.LABEL}")
    P(f"records: {S['records']}; duplicate same-input keys: {len(S['dup_keys'])}")
    for dz, d in S["per_design"].items():
        P(f"\n## {dz}: calls {d['calls']} status {d['status']} attempts {d['attempts']}")
        P(f"  first attempts {d['first_attempts']} refused {d['first_refused']} "
          f"rate {d['first_refusal_rate']} codes {d['first_refused_codes']}")
        P(f"  wrong cli {d['wrong_cli']} gui {'n/a' if d['wrong_gui'] is None else d['wrong_gui']}; "
          f"false no-ops {len(d['false_noops'])}; "
          f"redundant {len(d['redundant_writes'])} (clean {d['redundant_clean']}); "
          f"refused/error but changed {d['refused_or_error_but_changed']}; "
          f"OK not delivered {len(d['ok_not_delivered'])}")
        for w in d["wrong"]:
            P(f"   WRONG {w['id']} {w['call'][:120]} cli={w['cli_wrong']}/{w['cli_checks']} "
              f"gui={w['gui_wrong']}/{w['gui_checks']} j3={w['cli_wrong_j3']}/{w['gui_wrong_j3']}")
            P(f"         {json.dumps(w['cli_detail'])[:400]} | {json.dumps(w['gui_detail'])[:400]}")
            P(f"         {w['wrong_dir']}")
        for x in d["false_noops"]:
            P(f"   FALSE-NOOP {x}")
        for x in d["ok_not_delivered"]:
            P(f"   OK-NOT-DELIVERED {x}")
    P("\n## same-input refusal sets")
    for k, v in S["same_input"].items():
        P(f"  {k}: {v['n']} (same-input pairs {v['pairs_same_input']}, diverged {v['pairs_diverged_input']})"
          f" by code {v['by_code']}")
    e = S["e_connected"]
    P(f"\n## E connected: {e['n']} (first attempt {e['first_attempt']}); M2 clean same input "
      f"{e['M2_clean_same_input']}, H clean {e['H_clean_same_input']}; M2 {e['M2_outcomes']}; H {e['H_outcomes']}")
    P(f"\n## Hb vs H differing same-input records: {S['hb_vs_h']['n']}")
    for x in S["hb_vs_h"]["differ_same_input"]:
        P(f"   {x['id']} H={x['H']} Hb={x['Hb']}")
    P(f"\n## errors: {len(S['errors'])}")
    for x in S["errors"]:
        P(f"   {x}")
    P(f"\n## wrong != wrong_j3: {len(S['wrong_ne_j3'])}")
    for x in S["wrong_ne_j3"]:
        P(f"   {x}")
    P(f"\n## DEC-23-like (#-ref nodes vanished/appeared): {len(S['dec23_like'])}")
    for x in S["dec23_like"]:
        P(f"   {x}")
    P("\n## plans: planned pins off their net (cli / gui; strict in brackets)")
    for pname, pols in S["plans"].items():
        for pol, des in pols.items():
            for dz, ent in des.items():
                if not isinstance(ent, dict) or "cli" not in ent:
                    P(f"  {pname}/{pol}/{dz}: {ent}")
                    continue
                c, g = ent["cli"], ent["gui"]
                P(f"  {pname}/{pol}/{dz}: calls {ent['calls']} planned {c.get('planned_pins')} off "
                  f"{c.get('pins_not_on_plan_net')} [{c.get('strict_off')}] / {g.get('pins_not_on_plan_net')} "
                  f"[{g.get('strict_off')}] unloadable {c.get('unloadable')} shorts {c.get('shorts')}"
                  f"{' erc_rc ' + str(ent['erc_rc']) if 'erc_rc' in ent else ''}")
                for x in c.get("off_pins") or []:
                    P(f"      off {x['pad']} net {x['net']}{' (strict only)' if x['strict_only'] else ''}: "
                      f"{[(h['call'], h['codes']) for h in x['refusals']]}")


if __name__ == "__main__":
    main()
