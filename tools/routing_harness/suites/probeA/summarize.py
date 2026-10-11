"""Follow-up 2, probeA: merge records_<category>.jsonl into records.jsonl and write summary.json /
summary.md for designs M2, H, Hb, E and any other design in the records (pin-identity, junctions,
inert, labels).

Every count comes from the records files in the run's probeA output directory, written in this
pass by the category scripts (pin-identity/pi_jobs.py, junctions/run_junctions.py,
inert/inert_jobs.py, labels/run_labels.py) through rlib.py and judged by core/judge3.py
snapshot2/assess2. [cli] = kicad-cli netlist of the written file (measured); [gui] = judge3's
GUI-load proxy applied first, then kicad-cli (emulated). The gui reading is not available in this
repository: the counts below cover only the readings that were taken (core/readings.py), and the
header says which.

Scope flags (as follow-up 1's summarize.py):
  primary           one record per (job, step, design); other cases of a shared job are duplicates.
  extra             jobs added beyond the caseidx scenarios (labels L02/L12/L13/L15; junctions
                    '/own_' variants). Main tables are strict (non-extra) primary records.
  extra_membership  a labels record whose case came from the probe's evidence citations.
Same-input comparisons use rlib's input_key (hash of the design's tree before the call); a pair at
the same (job, step) whose inputs differ (an earlier step went differently) is not compared.
causes.json (hand-written after inspecting each saved wrong/ tree) maps "variant|step|design" to
a cause line; it supplies text only, never a count.
Run: uv run python tools/routing_harness/suites/probeA/summarize.py
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

OUT = hpaths.suite_out("probeA")
CASEIDX = hpaths.CASEIDX
RDS = readings.READINGS
CATS = ["pin-identity", "junctions", "inert", "labels"]
DESIGNS = ["M2", "H", "Hb", "E"]
PAIRS = [("H", "M2"), ("M2", "H"), ("E", "M2"), ("E", "H"), ("Hb", "H")]
KINDS = [("bad_merges", "merges"), ("splits", "splits"), ("renames", "renames"),
         ("class_bad", "class"), ("pads_multi", "pads_multi"), ("vanished", "vanished"),
         ("appeared", "appeared"), ("unloadable", "unloadable"), ("named_merge", "named_merge"),
         ("req_pad_multi", "req_pad_multi")]
SRC = "summarize.py -> summary.json"


def load():
    recs = []
    for c in CATS:
        f = OUT / f"records_{c}.jsonl"
        if not f.exists():
            print("MISSING", f)
            continue
        for line in open(f, encoding="utf-8"):
            if line.strip():
                recs.append(json.loads(line))
    return recs


def flag(recs):
    for r in recs:
        v = r.get("variant") or ""
        r["extra"] = bool((r["group"] == "labels" and v.startswith(("L02/", "L12/", "L13/", "L15/")))
                          or (r["group"] == "junctions" and "/own_" in v))
        m = re.search(r"extra case membership \[([^\]]*)\]", r.get("note") or "")
        r["extra_membership"] = bool(m) and r["case"] in re.findall(r"'([^']+)'", m.group(1))
    by = defaultdict(list)
    for r in recs:
        by[(r["group"], r["variant"], r.get("step"), r.get("design"))].append(r)
    for rs in by.values():
        if any(r.get("primary") and r["extra_membership"] for r in rs):
            for r in rs:
                r["primary"] = False
            for r in rs:
                if not r["extra_membership"]:
                    r["primary"] = True
                    break


def kinds_of(detail):
    return [short for key, short in KINDS if detail.get(key)]


def wrong(r):
    return r["status"] not in ("REFUSED", "HARNESS_ERROR") and bool(r.get("cli_wrong") or r.get("gui_wrong"))


def clean_delivered(r):
    return (r is not None and r["status"] == "OK" and readings.clean(r)
            and all(bool(r.get(f"delivered_{rd}")) for rd in RDS))


def outcome(r):
    if r is None:
        return "missing"
    st = r["status"]
    if st == "REFUSED":
        return "REFUSED"
    if wrong(r):
        return f"{st} WRONG"
    if r.get("false_noop"):
        return f"{st} FALSE-NOOP"
    if st == "OK" and clean_delivered(r):
        return "OK clean delivered"
    if st == "NOOP" and all(r.get(f"delivered_{rd}") for rd in RDS):
        return "NOOP delivered"
    return f"{st} clean not-delivered" if st == "OK" else st


def key(r):
    return (r["group"], r["variant"], r.get("step"))


def design_stats(rs):
    st = Counter(r["status"] for r in rs)
    by_code = Counter()
    for r in rs:
        if r["status"] == "REFUSED":
            by_code.update(r.get("codes") or ["(none)"])
    nw = {rd: 0 for rd in RDS}
    wk = {rd: Counter() for rd in RDS}
    for r in rs:
        if r["status"] in ("REFUSED", "HARNESS_ERROR"):
            continue
        for rd in RDS:
            if r.get(f"{rd}_wrong"):
                nw[rd] += 1
                wk[rd].update(kinds_of(r.get(f"{rd}_detail") or {}))
    ok = [r for r in rs if r["status"] == "OK"]
    red = [r for r in rs if r.get("redundant_write")]
    s1 = [r for r in rs if r.get("step") == 1]
    return {
        "calls": len(rs), "status": dict(st), "refused_by_code": dict(by_code),
        "refusal_rate": round(st.get("REFUSED", 0) / len(rs), 4) if rs else None,
        "step1_calls": len(s1), "step1_refused": sum(1 for r in s1 if r["status"] == "REFUSED"),
        "wrong_calls": nw, "wrong_by_kind": {k: dict(v) for k, v in wk.items()},
        "wrong_j3_calls": {rd: sum(1 for r in rs if r["status"] not in ("REFUSED", "HARNESS_ERROR")
                                   and r.get(f"{rd}_wrong_j3")) for rd in RDS},
        "wrong_ne_j3": sum(1 for r in rs for rd in RDS
                           if r["status"] not in ("REFUSED", "HARNESS_ERROR")
                           and bool(r.get(f"{rd}_wrong")) != bool(r.get(f"{rd}_wrong_j3"))),
        "false_noop": sum(1 for r in rs if r.get("false_noop")),
        "ok_not_delivered": {rd: sum(1 for r in ok if r.get(f"delivered_{rd}") is False) for rd in RDS},
        "redundant_writes": len(red),
        "redundant_clean": sum(1 for r in red if readings.clean(r)),
        "bytes_not_ok": sum(1 for r in rs if r.get("bytes_ok") is False),
        "fallback_label_after_refusal": dict(Counter((r.get("fallback_label") or {}).get("outcome")
                                                     for r in rs if r["status"] == "REFUSED")),
        "fallback_wptn_after_cp_refusal": dict(Counter((r.get("fallback_wptn") or {}).get("outcome")
                                                       for r in rs if r["status"] == "REFUSED"
                                                       and r["tool"] == "connect_pins")),
    }


def pair_sets(prim):
    idx = {(key(r), r["design"]): r for r in prim}
    out = {}
    for a, b in PAIRS:
        rows, diff_input = [], 0
        for r in prim:
            if r["design"] != a or r["status"] != "REFUSED":
                continue
            o = idx.get((key(r), b))
            if o is None:
                continue
            if o.get("input_key") != r.get("input_key"):
                diff_input += 1
                continue
            if o["status"] != "REFUSED":
                rows.append((r, o))
        out[f"{a}_only_vs_{b}"] = {
            "count": len(rows), "skipped_different_input": diff_input,
            "by_code": dict(Counter(" ".join(r.get("codes") or ["(none)"]) for r, _ in rows)),
            "by_category": dict(Counter(r["group"] for r, _ in rows)),
            f"{b}_outcome": dict(Counter(outcome(o) for _, o in rows)),
            "fallback_label": dict(Counter((r.get("fallback_label") or {}).get("outcome") for r, _ in rows)),
            "ids": [{"group": r["group"], "case": r["case"], "variant": r["variant"], "step": r.get("step"),
                     "tool": r["tool"], "codes": r.get("codes"), f"{b}": outcome(o)} for r, o in rows],
        }
    return out


def e_connected(prim):
    idx = {(key(r), r["design"]): r for r in prim}
    rows = [r for r in prim if r["design"] == "E" and r["status"] == "REFUSED" and "connected" in (r.get("codes") or [])]
    res = {"count": len(rows), "only_code_connected": sum(1 for r in rows if r.get("codes") == ["connected"])}
    for dn in ("M2", "H", "Hb"):
        same = [(r, idx.get((key(r), dn))) for r in rows]
        same = [(r, o) for r, o in same if o is not None and o.get("input_key") == r.get("input_key")]
        res[f"{dn}_same_input"] = len(same)
        res[f"{dn}_clean_write"] = sum(1 for _, o in same if clean_delivered(o))
        res[f"{dn}_outcome"] = dict(Counter(outcome(o) for _, o in same))
    res["M2_and_H_clean_write"] = sum(1 for r in rows if clean_delivered(idx.get((key(r), "M2")))
                                      and clean_delivered(idx.get((key(r), "H")))
                                      and idx[(key(r), "M2")].get("input_key") == r.get("input_key")
                                      and idx[(key(r), "H")].get("input_key") == r.get("input_key"))
    res["fallback_label"] = dict(Counter((r.get("fallback_label") or {}).get("outcome") for r in rows))
    res["ids"] = [{"group": r["group"], "case": r["case"], "variant": r["variant"], "step": r.get("step"),
                   "codes": r.get("codes"), "M2": outcome(idx.get((key(r), "M2"))),
                   "H": outcome(idx.get((key(r), "H")))} for r in rows]
    return res


def hb_vs_h(prim):
    idx = {(key(r), r["design"]): r for r in prim}
    diffs = []
    for r in prim:
        if r["design"] != "Hb":
            continue
        h = idx.get((key(r), "H"))
        if h is None:
            continue
        sig = lambda x: (x["status"], tuple(x.get("codes") or []), bool(x.get("cli_wrong")),  # noqa: E731
                         bool(x.get("gui_wrong")), bool(x.get("delivered_cli")), bool(x.get("delivered_gui")),
                         bool(x.get("false_noop")))
        if sig(r) != sig(h) or r.get("files_changed") != h.get("files_changed"):
            diffs.append({"group": r["group"], "case": r["case"], "variant": r["variant"], "step": r.get("step"),
                          "same_input": r.get("input_key") == h.get("input_key"),
                          "H": outcome(h) + (f" {h.get('codes')}" if h.get("codes") else ""),
                          "Hb": outcome(r) + (f" {r.get('codes')}" if r.get("codes") else ""),
                          "Hb_fallback_label": (r.get("fallback_label") or {}).get("outcome")})
    return diffs


def wrong_row(r, causes):
    return {"group": r["group"], "case": r["case"], "variant": r["variant"], "step": r.get("step"),
            "design": r["design"], "tool": r["tool"], "status": r["status"], "extra": r["extra"],
            "args": r.get("args"),
            "cli": kinds_of(r.get("cli_detail") or {}) if r.get("cli_wrong") else [],
            "gui": kinds_of(r.get("gui_detail") or {}) if r.get("gui_wrong") else [],
            "cli_wrong_j3": r.get("cli_wrong_j3"), "gui_wrong_j3": r.get("gui_wrong_j3"),
            "cli_detail": {k: v for k, v in (r.get("cli_detail") or {}).items() if k not in ("delivered",)},
            "gui_detail": {k: v for k, v in (r.get("gui_detail") or {}).items() if k not in ("delivered",)},
            "delivered": [r.get("delivered_cli"), r.get("delivered_gui")], "saved": r.get("saved"),
            "cause": causes.get(f"{r['variant']}|{r.get('step')}|{r['design']}") or causes.get(r["variant"]),
            "msg_head": (r.get("msg_head") or "")[:200], "script": r.get("script"), "origin": r.get("origin")}


def main():
    global DESIGNS
    print("readings:", readings.LABEL)
    recs = load()
    # designs are picked by name at run time (ROUTING_HARNESS_DESIGNS): follow-up 2's four first, then
    # any other design found in the records; a design with no record gets no column
    found = {r.get("design") for r in recs} - {None}
    DESIGNS = [d for d in (*DESIGNS, *sorted(found - set(DESIGNS))) if d in found]
    flag(recs)
    with open(OUT / "records.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, default=str) + "\n")
    cf = OUT / "causes.json"
    causes = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else {}
    prim = [r for r in recs if r.get("primary") and r["status"] != "HARNESS_ERROR"]
    herr = [r for r in recs if r["status"] == "HARNESS_ERROR"]
    strict = [r for r in prim if not r["extra"]]
    extras = [r for r in prim if r["extra"]]
    s = {"readings": readings.LABEL,
         "source": "records_<category>.jsonl in this directory, merged into records.jsonl",
         "records_total": len(recs), "records_primary": len(prim), "records_primary_strict": len(strict),
         "records_primary_extra": len(extras), "harness_errors": len(herr),
         "harness_error_list": [{"variant": r["variant"], "case": r["case"],
                                 "error": (r.get("error") or "").strip().splitlines()[-1:]} for r in herr]}
    for scope, rs in (("strict", strict), ("extra", extras), ("all", prim)):
        blk = {}
        for c in CATS + ["ALL"]:
            rc = [r for r in rs if c == "ALL" or r["group"] == c]
            if not rc:
                continue
            blk[c] = {"jobs": len({(r["group"], r["variant"]) for r in rc}),
                      **{dn: design_stats([r for r in rc if r["design"] == dn]) for dn in DESIGNS}}
        s[scope] = blk
    s["pairs_all"] = pair_sets(prim)
    s["pairs_strict"] = {k: {kk: vv for kk, vv in v.items() if kk != "ids"} for k, v in pair_sets(strict).items()}
    s["E_connected_all"] = e_connected(prim)
    s["Hb_vs_H_diffs_all"] = hb_vs_h(prim)
    s["wrong_writes"] = [wrong_row(r, causes) for r in prim if wrong(r)]
    s["wrong_ne_wrong_j3"] = [{"variant": r["variant"], "step": r.get("step"), "design": r["design"],
                               "reading": rd, "wrong": r.get(f"{rd}_wrong"), "wrong_j3": r.get(f"{rd}_wrong_j3"),
                               "kinds": kinds_of(r.get(f"{rd}_detail") or {})}
                              for r in prim for rd in RDS
                              if r["status"] not in ("REFUSED", "HARNESS_ERROR")
                              and bool(r.get(f"{rd}_wrong")) != bool(r.get(f"{rd}_wrong_j3"))]
    s["false_noops"] = [{"group": r["group"], "case": r["case"], "variant": r["variant"], "step": r.get("step"),
                         "design": r["design"], "delivered": [r.get("delivered_cli"), r.get("delivered_gui")],
                         "saved": r.get("saved"), "msg_head": (r.get("msg_head") or "")[:200]}
                        for r in prim if r.get("false_noop")]
    s["redundant_writes"] = [{"group": r["group"], "case": r["case"], "variant": r["variant"], "step": r.get("step"),
                              "design": r["design"], "clean": readings.clean(r),
                              "extra": r["extra"]} for r in prim if r.get("redundant_write")]
    s["ok_not_delivered"] = [{"variant": r["variant"], "step": r.get("step"), "design": r["design"],
                              "delivered": [r.get("delivered_cli"), r.get("delivered_gui")]}
                             for r in prim if r["status"] == "OK" and (r.get("delivered_cli") is False
                                                                       or r.get("delivered_gui") is False)]
    s["errors"] = [{"group": r["group"], "case": r["case"], "variant": r["variant"], "step": r.get("step"),
                    "design": r["design"], "msg_head": r.get("msg_head"),
                    "traceback_tail": (r.get("traceback") or "").strip().splitlines()[-4:]}
                   for r in recs if r.get("status") == "ERROR" and r.get("primary")]
    s["fallback_errors"] = [{"variant": r["variant"], "design": r["design"], "fb": fb,
                             "traceback_tail": (r[fb].get("traceback") or "").strip().splitlines()[-4:]}
                            for r in prim for fb in ("fallback_label", "fallback_wptn")
                            if (r.get(fb) or {}).get("status") == "ERROR"]
    s["fallback_wrong"] = [{"variant": r["variant"], "step": r.get("step"), "design": r["design"], "fb": fb,
                            "kinds": sorted(set(kinds_of(r[fb].get("cli_detail") or {}))
                                            | set(kinds_of(r[fb].get("gui_detail") or {}))),
                            "codes_of_refusal": r.get("codes"), "saved": r[fb].get("saved")}
                           for r in prim for fb in ("fallback_label", "fallback_wptn")
                           if (r.get(fb) or {}).get("outcome") == "wrong"]
    s["dec23_like"] = [{"variant": r["variant"], "design": r["design"]} for r in prim
                       for rd in RDS
                       if any(str(n[0]).startswith("#") for k in ("vanished", "appeared")
                              for n in (r.get(f"{rd}_detail") or {}).get(k, []))]
    focus = {}
    for case in ("PI-15", "PI-17", "INERT-05"):
        rows = [r for r in recs if r.get("case") == case and r["status"] != "HARNESS_ERROR"]
        focus[case] = [{"variant": r["variant"], "step": r.get("step"), "design": r["design"],
                        "outcome": outcome(r), "codes": r.get("codes"),
                        "kinds_cli": kinds_of(r.get("cli_detail") or {}) if r.get("cli_wrong") else [],
                        "fallback_label": (r.get("fallback_label") or {}).get("outcome")} for r in rows]
    s["focus_cases"] = focus
    nr = []
    for c in CATS:
        f = OUT / c / "not_rerun.json"
        if f.exists():
            nr += [{"group": c, **e} for e in json.loads(f.read_text(encoding="utf-8"))]
    s["not_rerun"] = nr
    have = {(r["group"], r["case"]) for r in recs if not r["extra_membership"] and not r["extra"]}
    s["cases_without_strict_records"] = [f"{c}:{case['id']}" for c in CATS
                                         for case in json.loads((CASEIDX / f"{c}.json").read_text(encoding="utf-8"))
                                         if (c, case["id"]) not in have]
    (OUT / "summary.json").write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
    print("wrote", OUT / "records.jsonl", OUT / "summary.json")
    _print_brief(s)


def _fc(d):
    return ", ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda t: (-t[1], str(t[0])))) or "0"


def _print_brief(s):
    for scope in ("strict", "extra", "all"):
        for c, e in s[scope].items():
            for dn in DESIGNS:
                d = e[dn]
                print(scope, c, dn, d["calls"], d["status"], "wrong", d["wrong_calls"], "j3", d["wrong_j3_calls"],
                      "fnoop", d["false_noop"], "red", d["redundant_writes"], d["redundant_clean"],
                      "notdlv", d["ok_not_delivered"], "bytes", d["bytes_not_ok"])
    for k, v in s["pairs_all"].items():
        print(k, v["count"], "skip", v["skipped_different_input"], _fc(v["by_code"]), "|", v[[x for x in v if x.endswith("_outcome")][0]])
    print("E_connected", {k: v for k, v in s["E_connected_all"].items() if k != "ids"})
    print("wrong", len(s["wrong_writes"]), "fnoop", len(s["false_noops"]), "errors", len(s["errors"]),
          "wrong!=j3", len(s["wrong_ne_wrong_j3"]), "hb diffs", len(s["Hb_vs_H_diffs_all"]))


if __name__ == "__main__":
    main()
