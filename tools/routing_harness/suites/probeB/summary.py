# Copied from pr57pt/coarse/rerun2/probeB/summary.py (follow-up 2); paths, designs and readings changed.
"""Follow-up 2, probeB: merge rec/*.jsonl into records.jsonl (one line per call, the cases a call
serves listed together) and compute summary.json. summary.md is written by hand from
summary.json and summary.out (this script's printout); every figure there comes from here.

Definitions (all from the records, nothing carried over):
- call = (run_id, design), record_type "call". Fallback runs are separate lines (record_type
  "fallback") and are not counted as calls.
- wrong write = a call with status OK whose cli (or gui) verdict is wrong. "wrong" is judge3's
  follow-up 2 verdict; "wrong_j3" the follow-up 1 verdict.
- false no-op = a NOOP whose judge says not delivered in a reading (judge3 false_noop).
- first attempt = every scripted call except the u1f remedy retries (step 2 of u1f_nc_on_spare_copy,
  run only after a no-connect refusal and the repo's remove_no_connect).
- same input = the two calls have the same run_id and the same pre_hash (sha256 over every
  .kicad_sch/.kicad_pro of the call's copy before the call).
- clean write = status OK, wrong False and delivered True in every reading taken (cli, and gui
  when that reading is available: core/readings.py).
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import readings  # noqa: E402

OUT = hpaths.suite_out("probeB")  # rec/*.jsonl is read here; records.jsonl and summary.json are written here
DESIGNS = tuple(hpaths.DESIGNS)  # the run's designs; main() adds any other design found in the records
CHECKS = ("unloadable", "bad_merges", "splits", "renames", "class_bad", "pads_multi", "vanished",
          "appeared", "named_merge", "req_pad_multi")


def load():
    calls, fbs = {}, {}
    for f in sorted((OUT / "rec").glob("*.jsonl")):
        for line in f.open(encoding="utf-8"):
            r = json.loads(line)
            if r["record_type"] == "call":
                k = (r["run_id"], r["design"])
                if k in calls:
                    calls[k]["cases"].append(r["case"])
                    continue
                r["cases"] = [r.pop("case")]
                r["job"] = f.stem
                calls[k] = r
            else:
                k = (r["run_id"], r["for_design"], r["fallback"])
                if k in fbs:
                    fbs[k]["cases"].append(r["case"])
                    continue
                r["cases"] = [r.pop("case")]
                fbs[k] = r
    for r in list(calls.values()) + list(fbs.values()):
        r["cases"] = sorted(set(r["cases"]))
    return calls, fbs


def fired(r, rd):
    d = r.get(f"{rd}_detail") or {}
    return [c for c in CHECKS if d.get(c)]


def clean_write(r):
    return (r and r["status"] == "OK" and readings.clean(r)
            and all(r.get(f"delivered_{rd}") is True for rd in readings.READINGS))


def first_attempt(r):
    return not (r["run_id"].startswith("u1f_nc_on_spare_copy:") and r["step"] == 2)


def brief(r):
    kw = r.get("kw") or {}
    if r["tool"] == "wire_pins_to_net":
        pins = ",".join(f"{p.get('reference')}:{p.get('pin')}" for p in kw.get("pins") or []
                        if isinstance(p, dict))
        return f"wire_pins_to_net([{pins}], {kw.get('label_text')!r})"
    if r["tool"] == "connect_pins":
        return f"connect_pins({kw.get('ref1')}:{kw.get('pin1')}, {kw.get('ref2')}:{kw.get('pin2')})"
    if r["tool"] == "auto_place_decoupling_cap":
        return (f"auto_place_decoupling_cap({kw.get('reference')}, {kw.get('power_net')!r}, "
                f"{kw.get('ground_net')!r})")
    return r["tool"]


def main():
    global DESIGNS
    calls, fbs = load()
    DESIGNS += tuple(sorted({d for _rid, d in calls} - set(DESIGNS)))
    by_run = defaultdict(dict)
    for (rid, d), r in calls.items():
        by_run[rid][d] = r
    out = {"definitions": __doc__, "designs": DESIGNS, "readings": readings.LABEL}
    # records.jsonl
    with open(OUT / "records.jsonl", "w", encoding="utf-8") as f:
        for k in sorted(calls):
            f.write(json.dumps(calls[k], ensure_ascii=False) + "\n")
        for k in sorted(fbs):
            f.write(json.dumps(fbs[k], ensure_ascii=False) + "\n")
    out["n_call_lines"], out["n_fallback_lines"] = len(calls), len(fbs)
    out["n_cases"] = len({c for r in calls.values() for c in r["cases"]})
    # 1. status counts
    st = {d: Counter() for d in DESIGNS}
    st_cat = defaultdict(Counter)
    for r in calls.values():
        st[r["design"]][r["status"]] += 1
        st[r["design"]]["calls"] += 1
        st_cat[(r["group"], r["design"])][r["status"]] += 1
        st_cat[(r["group"], r["design"])]["calls"] += 1
    out["status"] = {d: dict(st[d]) for d in DESIGNS}
    out["status_by_category"] = {f"{g}|{d}": dict(c) for (g, d), c in sorted(st_cat.items())}
    # incomplete runs (a design missing for a run_id)
    out["runs_missing_a_design"] = sorted(rid for rid, m in by_run.items()
                                          if set(m) != set(DESIGNS))
    # 2. wrong writes, false no-ops, redundant writes, judge-version differences
    wrong, fnoop, redund, notdel, j3diff, wrongnonok = ([] for _ in range(6))
    for (rid, d), r in sorted(calls.items()):
        cw, gw = r.get("cli_wrong"), r.get("gui_wrong")
        item = {"run_id": rid, "design": d, "cases": r["cases"], "call": brief(r),
                "status": r["status"], "cli_wrong": cw, "gui_wrong": gw,
                "cli_wrong_j3": r.get("cli_wrong_j3"), "gui_wrong_j3": r.get("gui_wrong_j3"),
                "cli_checks": fired(r, "cli"), "gui_checks": fired(r, "gui"),
                "cli_detail": {k: v for k, v in (r.get("cli_detail") or {}).items() if k in CHECKS},
                "gui_detail": {k: v for k, v in (r.get("gui_detail") or {}).items() if k in CHECKS},
                "delivered": [r.get("delivered_cli"), r.get("delivered_gui")],
                "kept": r.get("kept"), "pre_hash": r.get("pre_hash")}
        if (cw or gw) and r["status"] == "OK":
            wrong.append(item)
        elif cw or gw:
            wrongnonok.append(item)
        if r.get("false_noop"):
            fnoop.append(item)
        if r.get("redundant_write"):
            redund.append(dict(item, clean=not (cw or gw)))
        if r["status"] == "OK" and (r.get("delivered_cli") is False or r.get("delivered_gui") is False):
            notdel.append(item)
        for rd in ("cli", "gui"):
            a, b = r.get(f"{rd}_wrong"), r.get(f"{rd}_wrong_j3")
            if a is not None and b is not None and a != b:
                j3diff.append(dict(item, reading=rd))
    out["wrong_writes"], out["wrong_not_ok"] = wrong, wrongnonok
    out["false_noops"], out["redundant_writes"] = fnoop, redund
    out["ok_not_delivered"], out["wrong_ne_wrong_j3"] = notdel, j3diff
    # DEC-23 style: a fired check that only involves '#'-references (power symbols)
    dec23 = []
    for it in wrong + wrongnonok:
        for rd in ("cli", "gui"):
            for k in ("vanished", "appeared", "pads_multi"):
                v = it[f"{rd}_detail"].get(k) or []
                if v and all(str(n[0]).startswith("#") for n in v):
                    dec23.append((it["run_id"], it["design"], rd, k))
    out["dec23_style"] = dec23
    # fallbacks
    fbc = Counter()
    for r in fbs.values():
        v = ("refused" if r["status"] == "REFUSED" else r["status"].lower() if r["status"] != "OK"
             else ("wrong" if readings.wrong(r) else
                   "clean_delivered" if all(r.get(f"delivered_{rd}") for rd in readings.READINGS)
                   else "clean_not_delivered"))
        fbc[(r["for_design"], r["fallback"], v)] += 1
    out["fallback_outcomes"] = {f"{a}|{b}|{c}": n for (a, b, c), n in sorted(fbc.items())}
    out["fallback_wrong"] = [{"run_id": r["run_id"], "for_design": r["for_design"],
                              "fallback": r["fallback"], "cases": r["cases"],
                              "cli": fired(r, "cli"), "gui": fired(r, "gui"), "kept": r.get("kept")}
                             for r in fbs.values() if r.get("cli_wrong") or r.get("gui_wrong")]
    # 3. first-attempt refusal rate and same-input refusal sets
    fa = {d: Counter() for d in DESIGNS}
    for r in calls.values():
        if first_attempt(r):
            fa[r["design"]]["calls"] += 1
            fa[r["design"]]["refused"] += r["status"] == "REFUSED"
    out["first_attempt"] = {d: dict(fa[d], rate=round(fa[d]["refused"] / fa[d]["calls"], 4)
                                    if fa[d]["calls"] else None) for d in DESIGNS}

    def only(a, b):
        ids, codes, other, cats = [], Counter(), Counter(), Counter()
        compared = 0
        for rid, m in sorted(by_run.items()):
            ra, rb = m.get(a), m.get(b)
            if not ra or not rb or ra["pre_hash"] != rb["pre_hash"]:
                continue
            if not (first_attempt(ra) and first_attempt(rb)):
                continue
            compared += 1
            if ra["status"] == "REFUSED" and rb["status"] != "REFUSED":
                ids.append(rid)
                codes.update(set(ra["codes"]) or {"(none)"})
                cats[ra["group"]] += 1
                ob = rb["status"]
                if ob == "OK":
                    ob = "OK clean" if clean_write(rb) else (
                        "OK wrong" if (rb.get("cli_wrong") or rb.get("gui_wrong"))
                        else "OK not delivered")
                other[ob] += 1
        return {"compared_same_input": compared, "n": len(ids), "codes": dict(codes.most_common()),
                "by_category": dict(cats), "other_design_outcome": dict(other), "ids": ids}

    pairs = [("H", "M2"), ("M2", "H"), ("E", "M2"), ("E", "H"), ("M2", "E"), ("H", "E"),
             ("Hb", "H"), ("H", "Hb")]
    out["refusal_sets"] = {f"{a}-only vs {b}": only(a, b) for a, b in pairs}
    # Hb vs H: every same-input difference (status, codes, verdict)
    hbd = []
    for rid, m in sorted(by_run.items()):
        h, hb = m.get("H"), m.get("Hb")
        if not h or not hb or h["pre_hash"] != hb["pre_hash"]:
            continue
        keys = ("status", "codes", "cli_wrong", "gui_wrong", "delivered_cli", "delivered_gui",
                "files_changed")
        if any(h.get(k) != hb.get(k) for k in keys) or (
                h["status"] == "OK" and h["msg_head"] != hb["msg_head"]):
            hbd.append({"run_id": rid, "H": {k: h.get(k) for k in keys},
                        "Hb": {k: hb.get(k) for k in keys}, "H_msg": h["msg_head"][:160],
                        "Hb_msg": hb["msg_head"][:160]})
    out["hb_vs_h_differences"] = hbd
    # 4. E [E:connected]
    conn = [r for r in calls.values() if r["design"] == "E" and r["status"] == "REFUSED"
            and "connected" in r["codes"]]
    e4 = {"n": len(conn), "M2_clean_write_same_input": [], "H_clean_write_same_input": [],
          "M2_outcome": Counter(), "H_outcome": Counter()}
    for r in conn:
        for d in ("M2", "H"):
            o = by_run[r["run_id"]].get(d)
            if not o or o["pre_hash"] != r["pre_hash"]:
                e4[f"{d}_outcome"]["no same-input record"] += 1
                continue
            if clean_write(o):
                e4[f"{d}_clean_write_same_input"].append(r["run_id"])
                e4[f"{d}_outcome"]["OK clean"] += 1
            elif o["status"] == "OK":
                e4[f"{d}_outcome"]["OK wrong or not delivered"] += 1
            else:
                e4[f"{d}_outcome"][o["status"]] += 1
    e4["only_connected_code"] = sum(1 for r in conn if set(r["codes"]) == {"connected"})
    e4["by_category"] = dict(Counter(r["group"] for r in conn))
    e4["M2_outcome"], e4["H_outcome"] = dict(e4["M2_outcome"]), dict(e4["H_outcome"])
    e4["M2_clean_n"] = len(e4["M2_clean_write_same_input"])
    e4["H_clean_n"] = len(e4["H_clean_write_same_input"])
    out["e_connected"] = e4
    # 5. errors
    out["errors"] = [{"run_id": r["run_id"], "design": r["design"], "call": brief(r),
                      "msg": r["msg_head"], "traceback": r.get("traceback")}
                     for r in calls.values() if r["status"] == "ERROR"]
    out["judge_errors"] = [{"run_id": r["run_id"], "design": r["design"], "judged": r["judged"]}
                           for r in calls.values() if str(r.get("judged", "")).startswith(
                               "judge_error")]
    out["bytes_not_ok"] = [{"run_id": r["run_id"], "design": r["design"], "status": r["status"],
                            "files_changed": r["files_changed"]}
                           for r in calls.values() if not r.get("bytes_ok", True)]
    # calibration cases
    cal = {}
    for case in ("HIER-06", "HIER-14", "UP-05"):
        rows = []
        for (rid, d), r in sorted(calls.items()):
            if case in r["cases"]:
                rows.append({"run_id": rid, "design": d, "status": r["status"], "codes": r["codes"],
                             "cli_wrong": r.get("cli_wrong"), "gui_wrong": r.get("gui_wrong"),
                             "cli_wrong_j3": r.get("cli_wrong_j3"),
                             "gui_wrong_j3": r.get("gui_wrong_j3"),
                             "cli_checks": fired(r, "cli"), "gui_checks": fired(r, "gui"),
                             "delivered": [r.get("delivered_cli"), r.get("delivered_gui")]})
        cal[case] = rows
    out["calibration"] = cal
    # codes per design
    out["refused_codes"] = {d: dict(Counter(c for r in calls.values() if r["design"] == d
                                            and r["status"] == "REFUSED" for c in r["codes"])
                                    .most_common()) for d in DESIGNS}
    out["refused_codes_by_category"] = {
        f"{g}|{d}": dict(Counter(c for r in calls.values() if r["design"] == d and r["group"] == g
                                 and r["status"] == "REFUSED" for c in r["codes"]).most_common())
        for g in sorted({r["group"] for r in calls.values()}) for d in DESIGNS}
    (OUT / "summary.json").write_text(json.dumps(out, indent=1, default=list, ensure_ascii=False),
                                      encoding="utf-8")
    # printout for the hand-written summary.md
    p = print
    p(readings.LABEL)
    p(f"call lines {len(calls)}, fallback lines {len(fbs)}, cases {out['n_cases']}")
    p("runs missing a design:", len(out["runs_missing_a_design"]), out["runs_missing_a_design"][:20])
    for d in DESIGNS:
        p(d, out["status"][d], "first-attempt", out["first_attempt"][d])
    for k, v in out["status_by_category"].items():
        p("  ", k, v)
    p("refused codes:", json.dumps(out["refused_codes"]))
    for k, v in out["refused_codes_by_category"].items():
        p("  ", k, v)
    p(f"\nWRONG WRITES {len(wrong)}")
    for it in wrong:
        p(" ", it["design"], it["run_id"], it["cases"], it["call"], "cli", it["cli_wrong"],
          it["cli_checks"], "gui", it["gui_wrong"], it["gui_checks"], "j3", it["cli_wrong_j3"],
          it["gui_wrong_j3"], "dlv", it["delivered"])
        p("     cli:", json.dumps(it["cli_detail"])[:400])
    p(f"wrong not OK {len(wrongnonok)}", [(i['design'], i['run_id'], i['status']) for i in wrongnonok])
    p(f"FALSE NOOPS {len(fnoop)}")
    for it in fnoop:
        p(" ", it["design"], it["run_id"], it["cases"], it["call"], it["delivered"],
          it["cli_checks"])
    p(f"redundant writes {len(redund)}:", [(i['design'], i['run_id'], i['clean']) for i in redund])
    p(f"OK not delivered {len(notdel)}:", [(i['design'], i['run_id']) for i in notdel])
    p(f"wrong != wrong_j3 {len(j3diff)}:", [(i['design'], i['run_id'], i['reading']) for i in j3diff])
    p("dec23-style:", dec23)
    p("fallback outcomes:", json.dumps(out["fallback_outcomes"], indent=0))
    p("fallback wrong:", [(x["run_id"], x["for_design"], x["fallback"], x["cli"]) for x in
                          out["fallback_wrong"]])
    for k, v in out["refusal_sets"].items():
        p(k, {kk: vv for kk, vv in v.items() if kk != "ids"})
        if v["n"] <= 40:
            p("    ", v["ids"])
    p(f"Hb vs H differences {len(hbd)}")
    for x in hbd:
        p("  ", x["run_id"], x["H"]["status"], x["H"]["codes"], "->", x["Hb"]["status"],
          x["Hb"]["codes"], "|", x["Hb_msg"][:120])
    p("E connected:", {k: v for k, v in e4.items() if not k.endswith("same_input")})
    p("  M2 clean:", e4["M2_clean_write_same_input"])
    p("  H clean:", e4["H_clean_write_same_input"])
    p(f"ERRORS {len(out['errors'])}")
    for x in out["errors"]:
        p(" ", x["design"], x["run_id"], x["call"], x["msg"][:200])
        p("   ", (x["traceback"] or "").strip().splitlines()[-3:])
    p("judge errors:", out["judge_errors"])
    p("bytes not ok:", out["bytes_not_ok"])
    p("\nCALIBRATION")
    for case, rows in cal.items():
        for x in rows:
            p(" ", case, x["design"], x["run_id"], x["status"], x["codes"], "cli", x["cli_wrong"],
              x["cli_checks"], "j3", x["cli_wrong_j3"], "gui", x["gui_wrong"], x["gui_checks"],
              "j3", x["gui_wrong_j3"], "dlv", x["delivered"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
