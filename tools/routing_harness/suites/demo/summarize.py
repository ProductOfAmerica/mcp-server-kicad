"""Summarise records.jsonl (run_sweeps.py) and modeldiff2.json (modeldiff2.py; optional, its
producer is not part of this repository) into summary.json, summary.md and fallback.md. Every
count comes from those two files only (follow-up 2 pass). Reads and writes the run's demo output
directory (hpaths.suite_out("demo")).

Readings: cli = kicad-cli on the file as written (measured; the run's version is in run.json);
gui = judge3's GUI-cleanup emulation then kicad-cli (emulated; not available in this repository,
see core/gui_reading.py). A reading that was not taken is skipped: never counted clean, never
counted unjudged, and its columns show null. The summary header says which readings were taken.
"clean delivered" = status OK, wrong False and delivered True in every reading taken (judge3:
every requested pad in exactly one net, together).
Run: uv run python tools/routing_harness/suites/demo/summarize.py [--json-only]
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
OUT = hpaths.suite_out("demo")  # the run's outputs: records in, summaries out
SCRIPT = str(HERE / "summarize.py")
RUN = str(HERE / "run_sweeps.py")
KINDS = ("bad_merges", "splits", "renames", "class_bad", "pads_multi", "vanished", "appeared",
         "unloadable", "named_merge", "req_pad_multi")
D4 = ("M2", "H", "Hb", "E")
MAIN = ("demo_wptn", "demo_connect")
E_PIN_STATE = {"connected", "nc_flag", "nc_type", "implicit", "rule_area", "cross_sheet",
               "dup_ref", "copies", "units", "jumper", "sheets"}
CAUSES = json.loads((OUT / "causes.json").read_text(encoding="utf-8")) \
    if (OUT / "causes.json").exists() else {}

recs = [json.loads(x) for x in (OUT / "records.jsonl").read_text(encoding="utf-8").splitlines()]
md = json.loads((OUT / "modeldiff2.json").read_text(encoding="utf-8")) \
    if (OUT / "modeldiff2.json").exists() else None
# designs are picked by name at run time (ROUTING_HARNESS_DESIGNS): list follow-up 2's four first,
# then any other design found in the records ("repo" labels the add_label recoveries, not a design)
D4 += tuple(sorted({r["design"] for r in recs} - {"repo", *D4}))


def rid(r):
    return f"{r['sample']} | {r['group']} | {r['case']} | {r['design']} | {r.get('call', '')}"


def ok_both(r):
    return all(bool(r.get(f"delivered_{rd}")) for rd in readings.READINGS)


def clean_both(r):
    return readings.clean(r)


def good(r):
    return r is not None and r["status"] == "OK" and clean_both(r) and ok_both(r)


def wrong(r):
    return bool(readings.wrong(r))


def outcome(r):
    if r is None:
        return "not run"
    if r["status"] == "REFUSED":
        return "REFUSED " + " ".join(r.get("codes") or [])
    if r["status"] not in ("OK", "NOOP", "PARTIAL"):
        return r["status"]
    w = "WRONG" if wrong(r) else "clean" if clean_both(r) else "UNJUDGED"
    d = "delivered" if ok_both(r) else "NOT delivered (cli %s, gui %s)" % (
        r.get("delivered_cli"), r.get("delivered_gui"))
    return f"{r['status']} {w} {d}"


def brief(r, extra=()):
    out = {"id": rid(r), **{k: r.get(k) for k in (
        "status", "codes", "kind", "cli_wrong", "gui_wrong", "cli_wrong_j3", "gui_wrong_j3",
        "delivered_cli", "delivered_gui", "false_noop", "kept", *extra)}}
    for rd in ("cli", "gui"):
        det = r.get(f"{rd}_detail") or {}
        out[f"{rd}_fired"] = {k: v for k, v in det.items() if k in KINDS or k == "error"}
    if rid(r) in CAUSES:
        out["cause"] = CAUSES[rid(r)]
    return out


def stats(rs):
    st = Counter(r["status"] for r in rs)
    n = len(rs)
    s = {"calls": n, "OK": st["OK"], "NOOP": st["NOOP"], "REFUSED": st["REFUSED"],
         "ERROR": st["ERROR"], "other_status": {k: v for k, v in st.items()
                                                if k not in ("OK", "NOOP", "REFUSED", "ERROR")},
         "refusal_rate": round(st["REFUSED"] / n, 4) if n else None,
         "REFUSED_by_code": dict(Counter(" ".join(r["codes"]) or "(none)" for r in rs
                                         if r["status"] == "REFUSED").most_common()),
         "OK_by_kind": dict(Counter(r["kind"] for r in rs if r["status"] == "OK").most_common())}
    judged = [r for r in rs if r["status"] != "REFUSED" or r.get("bytes_changed")]
    for rd in ("cli", "gui"):
        if rd not in readings.READINGS:  # not taken: null, never 0 and never unjudged
            s[f"wrong_{rd}"] = s[f"wrong_{rd}_by_kind"] = None
            s[f"unjudged_{rd}"] = s[f"wrong_ne_wrong_j3_{rd}"] = None
            continue
        kinds = Counter()
        for r in judged:
            if r.get(f"{rd}_wrong"):
                for k in KINDS:
                    if (r.get(f"{rd}_detail") or {}).get(k):
                        kinds[k] += 1
        s[f"wrong_{rd}"] = sum(1 for r in judged if r.get(f"{rd}_wrong"))
        s[f"wrong_{rd}_by_kind"] = dict(kinds)
        s[f"unjudged_{rd}"] = sum(1 for r in judged if r.get(f"{rd}_wrong") is None)
        s[f"wrong_ne_wrong_j3_{rd}"] = sum(1 for r in judged
                                           if r.get(f"{rd}_wrong") != r.get(f"{rd}_wrong_j3"))
    s["false_noops"] = sum(1 for r in rs if r.get("false_noop"))
    s["ok_not_delivered_either"] = sum(1 for r in rs if r["status"] == "OK" and not ok_both(r))
    red = [r for r in rs if r.get("redundant_write")]
    s["redundant_writes"] = len(red)
    s["redundant_writes_clean"] = sum(1 for r in red if clean_both(r))
    s["byte_rule_violations"] = sum(1 for r in rs if not r["bytes_ok"])
    s["other_files_written"] = sum(1 for r in rs if r.get("other_files_written"))
    s["ok_clean_delivered"] = sum(1 for r in rs if good(r))
    return s


by = defaultdict(dict)  # (sample, group, case) -> design -> record (main and fallback samples)
for r in recs:
    if r["sample"] in ("demo_wptn", "demo_connect", "fallback"):
        by[(r["sample"], r["group"], r["case"])][r["design"]] = r


def only(sample, refuser, other):
    rows = [(k, v) for k, v in by.items() if k[0] == sample and refuser in v and other in v
            and v[refuser]["status"] == "REFUSED" and v[other]["status"] != "REFUSED"]
    return {"count": len(rows),
            "by_code": dict(Counter(" ".join(v[refuser]["codes"]) for _k, v in rows).most_common()),
            "per_sheet": dict(Counter(k[1] for k, _v in rows).most_common()),
            f"{other}_outcome": dict(Counter(outcome(v[other]) for _k, v in rows).most_common()),
            "ids": [f"{k[1]} | {k[2]} | {' '.join(v[refuser]['codes'])} | {other}: "
                    f"{outcome(v[other])}" for k, v in rows]}


S = {"provenance": {"records": str(OUT / "records.jsonl"), "producer": RUN, "summarizer": SCRIPT,
                    "modeldiff": "modeldiff2.py (not part of this repository; modeldiff2.json is"
                                 " read from the output directory when present)",
                    "judge": str(hpaths.CORE / "judge3.py"),
                    "readings": readings.LABEL},
     "samples": {}, "fallback160": {}, "same_input_refusals": {}}
for s in ("demo_wptn", "demo_connect", "fallback", "recov_b", "recov_c", "e_split"):
    ds = sorted({r["design"] for r in recs if r["sample"] == s},
                key=lambda d: D4.index(d) if d in D4 else 9)
    S["samples"][s] = {d: stats([r for r in recs if r["sample"] == s and r["design"] == d])
                       for d in ds}
S["fallback160"] = {d: stats([r for r in recs if r["sample"] == "fallback" and r["design"] == d
                              and r.get("m2_refused")]) for d in D4}
for s in MAIN:
    S["same_input_refusals"][s] = {
        "H_only_vs_M2": only(s, "H", "M2"), "M2_only_vs_H": only(s, "M2", "H"),
        "E_only_vs_M2": only(s, "E", "M2"), "E_only_vs_H": only(s, "E", "H"),
        "Hb_only_vs_H": only(s, "Hb", "H"), "H_only_vs_Hb": only(s, "H", "Hb")}
    pairs = Counter()
    for k, v in by.items():
        if k[0] == s:
            pairs[" / ".join(f"{d} {v[d]['status'] if d in v else '-'}" for d in D4)] += 1
    S["samples"][s]["status_tuples"] = dict(pairs.most_common())

# 4. E's [E:connected] refusals in the main samples
ec = {}
for s in MAIN:
    rows = [v for k, v in by.items() if k[0] == s and "E" in v and v["E"]["status"] == "REFUSED"
            and "connected" in v["E"]["codes"]]
    ec[s] = {"E_connected_refusals": len(rows),
             "M2_clean_delivered": sum(1 for v in rows if good(v.get("M2"))),
             "H_clean_delivered": sum(1 for v in rows if good(v.get("H"))),
             "Hb_clean_delivered": sum(1 for v in rows if good(v.get("Hb"))),
             "M2_or_H_clean_delivered": sum(1 for v in rows if good(v.get("M2")) or good(v.get("H"))),
             "M2_outcomes": dict(Counter(outcome(v.get("M2")) for v in rows).most_common()),
             "H_outcomes": dict(Counter(outcome(v.get("H")) for v in rows).most_common())}
S["E_connected"] = ec

judged_any = [r for r in recs if r["status"] != "REFUSED" or r.get("bytes_changed")]
S["wrong_writes"] = [brief(r) for r in judged_any if wrong(r)]
S["wrong_ne_wrong_j3"] = [brief(r) for r in judged_any
                          if r.get("cli_wrong") != r.get("cli_wrong_j3")
                          or r.get("gui_wrong") != r.get("gui_wrong_j3")]
S["false_noops"] = [brief(r) for r in recs if r.get("false_noop")]
S["ok_not_delivered"] = [brief(r) for r in recs if r["status"] == "OK" and not ok_both(r)]
S["redundant_writes"] = [brief(r, ("redundant_write", "pre_satisfied")) for r in recs
                         if r.get("redundant_write")]
S["byte_rule_violations"] = [brief(r, ("bytes_changed",)) for r in recs if not r["bytes_ok"]]
S["errors"] = [brief(r) | {"msg": r["msg"][:600]} for r in recs if r["status"] == "ERROR"]
S["unjudged"] = [brief(r) for r in judged_any if readings.unjudged(r)]
S["hash_pins_vanished_or_appeared"] = [
    brief(r) for r in judged_any
    if any(n[0].startswith("#") for rd in ("cli", "gui")
           for k in ("vanished", "appeared") for n in ((r.get(f"{rd}_detail") or {}).get(k) or []))]

# ------------------------------------------------------------------ measurements A, B, C
conn = {(r["group"], r["case"]): {} for r in recs if r["sample"] == "demo_connect"}
for r in recs:
    if r["sample"] == "demo_connect":
        conn[(r["group"], r["case"])][r["design"]] = r
fbk = {(r["group"], r["case"], r["design"]): r for r in recs if r["sample"] == "fallback"}
rb = defaultdict(list)
for r in recs:
    if r["sample"] == "recov_b":
        rb[(r["group"], r["case"], r["design"])].append(r)
rc = {(r["group"], r["case"]): r for r in recs if r["sample"] == "recov_c"}
es = {(r["group"], r["case"]): r for r in recs if r["sample"] == "e_split"}


def recovery(d, cond):
    rows = [(k, v) for k, v in conn.items() if cond(v)]
    per = defaultdict(Counter)
    cases, bad = [], []
    for k, v in rows:
        code = " ".join(v[d]["codes"])
        a = fbk.get((*k, d))
        bs = rb.get((*k, d), [])
        c = rc.get(k)
        ra, rbb, rcc = good(a), any(good(x) for x in bs), good(c)
        p = per[code]
        p["pairs"] += 1
        p["a"] += ra
        p["b_applicable"] += bool(bs)
        p["b"] += rbb
        p["c"] += rcc
        p["any"] += ra or rbb or rcc
        p["none"] += not (ra or rbb or rcc)
        for x in [a, *bs, c]:
            if x is not None and wrong(x):
                bad.append(brief(x))
        cases.append({"sheet": k[0], "case": k[1], "code": code, "a": outcome(a),
                      "b": [f"{x['call']}: {outcome(x)}" for x in bs] or "n/a (no user-named net)",
                      "c": outcome(c)})
    tot = Counter()
    for p in per.values():
        tot.update(p)
    return {"by_code": {k: dict(v) for k, v in per.items()}, "total": dict(tot),
            "wrong_recoveries": bad, "cases": cases}


S["A_H_recovery"] = recovery("H", lambda v: v.get("H", {}).get("status") == "REFUSED")
S["C_Hb_only_recovery"] = recovery(
    "Hb", lambda v: v.get("Hb", {}).get("status") == "REFUSED"
    and v.get("H", {}).get("status") != "REFUSED")
brows = []
for k, v in conn.items():
    if v.get("E", {}).get("status") != "REFUSED":
        continue
    codes = v["E"]["codes"]
    free = not (set(codes) & E_PIN_STATE)
    one = fbk.get((*k, "E"))
    two = es.get(k)
    brows.append({"sheet": k[0], "case": k[1], "connect_codes": codes, "both_free": free,
                  "one_call": outcome(one), "one_call_good": good(one),
                  "one_call_codes": one["codes"] if one else None,
                  "split": outcome(two) if two else "not run (one call did not refuse)",
                  "split_parts": [f"{p['pin']}: {p['status']} {' '.join(p['codes'])}"
                                  for p in two["parts"]] if two else None,
                  "split_good": good(two) if two else None,
                  "wrong": bool((one and wrong(one)) or (two and wrong(two)))})
S["B_E_claim"] = {
    "pairs_E_refused": len(brows),
    "both_free": sum(r["both_free"] for r in brows),
    "both_free_one_call_good": sum(r["both_free"] and r["one_call_good"] for r in brows),
    "both_free_counterexamples": [r for r in brows if r["both_free"] and not r["one_call_good"]],
    "not_free": sum(not r["both_free"] for r in brows),
    "not_free_one_call": dict(Counter(r["one_call"] for r in brows if not r["both_free"])),
    "not_free_split": dict(Counter(r["split"] for r in brows if not r["both_free"])),
    "by_connect_code": dict(Counter(f"{' '.join(r['connect_codes'])} -> one call: "
                                    f"{r['one_call']}; split: {r['split']}"
                                    for r in brows).most_common()),
    "any_wrong": [r for r in brows if r["wrong"]], "rows": brows}
if md:
    S["modeldiff"] = md["totals"]
    S["modeldiff_findings"] = {
        dn: [{"sheet": r["sheet"], "reading": side, "check": k, "items": r[dn][side][k]}
             for r in md["rows"] if "skipped" not in r and "skipped" not in r[dn]
             for side in ("cli", "gui") for k in ("narrow_split", "coarse_miss")
             if r[dn][side][k]] for dn in ("H", "M2")}
    S["modeldiff_skipped"] = [{"sheet": r["sheet"], "why": r.get("skipped")}
                              for r in md["rows"] if "skipped" in r] + \
        [{"sheet": r["sheet"], "design": dn, "why": r[dn]["skipped"]} for r in md["rows"]
         if "skipped" not in r for dn in ("H", "M2") if "skipped" in r[dn]]
(OUT / "summary.json").write_text(json.dumps(S, indent=1, default=list), encoding="utf-8")
if "--json-only" in sys.argv:
    sys.exit(0)


# ------------------------------------------------------------------------------------ markdown


def tbl(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def j(x):
    return json.dumps(x, default=list)


L = ["# " + ", ".join(d for d in D4 if any(r["design"] == d for r in recs))
     + " on KiCad 9.0.8 demo sheets (follow-up 2)", "",
     f"All figures: `records.jsonl` ({len(recs)} call records) from run_sweeps.py, and"
     " `modeldiff2.json` from modeldiff2.py (when present), both from one run; tables built by"
     " summarize.py (this file and summary.json). Readings: " + readings.LABEL + ". cli = kicad-cli"
     " (measured); gui = GUI-cleanup emulation then kicad-cli (emulated; not available in this"
     " repository). Every non-refused call was judged in the readings taken; a column for a"
     " reading that was not taken shows null.", ""]
WRONGS = S["wrong_writes"]
L += [f"## Wrong writes and false no-ops (all samples): {len(WRONGS)} wrong, "
      f"{len(S['false_noops'])} false no-ops", ""]
for e in WRONGS + S["false_noops"]:
    L.append(f"- {e['id']}: cli {e['cli_wrong']} gui {e['gui_wrong']} (j3 cli {e['cli_wrong_j3']}"
             f" gui {e['gui_wrong_j3']}); fired cli {j(e['cli_fired'])[:220]}, gui"
             f" {j(e['gui_fired'])[:220]}; cause: {e.get('cause', 'see fallback.md / not yet')}")
L.append("")
keys = ["calls", "OK", "NOOP", "REFUSED", "ERROR", "refusal_rate", "wrong_cli", "wrong_gui",
        "wrong_ne_wrong_j3_cli", "wrong_ne_wrong_j3_gui", "false_noops", "ok_not_delivered_either",
        "redundant_writes", "redundant_writes_clean", "byte_rule_violations",
        "other_files_written", "ok_clean_delivered"]
for s, title in (("demo_wptn", "wire_pins_to_net, 718 pins"),
                 ("demo_connect", "connect_pins, 244 pairs"),
                 ("fallback", "fallback: wire_pins_to_net([a, b], 'FALLBK') on every pair any"
                              " design's connect_pins refused")):
    ds = [d for d in D4 if d in S["samples"][s]]
    L += [f"## {s} ({title})", "", tbl(["metric", *ds], [[k, *(j(S["samples"][s][d][k])
                                                              for d in ds)] for k in keys]), ""]
    for d in ds:
        L.append(f"- {d} refused by code: {j(S['samples'][s][d]['REFUSED_by_code'])}")
    L.append("")
L += ["## fallback160 (the subset M2's connect_pins refused, follow-up 1's sample)", "",
      tbl(["metric", *D4], [[k, *(j(S["fallback160"][d][k]) for d in D4)] for k in keys]), ""]
for d in D4:
    L.append(f"- {d} refused by code: {j(S['fallback160'][d]['REFUSED_by_code'])}")
L += ["", "## Same-input refusal sets (refuser refused, other did not)", ""]
for s in MAIN:
    for name, o in S["same_input_refusals"][s].items():
        L.append(f"- {s} {name}: {o['count']}; by code {j(o['by_code'])}; other:"
                 f" {j([v for k, v in o.items() if k.endswith('_outcome')][0])}")
L += ["", "## E [E:connected] refusals (main samples)", ""]
for s in MAIN:
    e = ec[s]
    L.append(f"- {s}: {e['E_connected_refusals']} refusals; M2 wrote clean+delivered on"
             f" {e['M2_clean_delivered']}, H on {e['H_clean_delivered']}, either on"
             f" {e['M2_or_H_clean_delivered']}")
L += ["", "## Errors, unjudged, byte-rule, '#' pins", "",
      f"- ERROR records: {len(S['errors'])}", *[f"  - {e['id']}: {e['msg'][:300]}"
                                               for e in S["errors"]],
      f"- unjudged (a reading returned None): {len(S['unjudged'])}",
      f"- wrong != wrong_j3: {len(S['wrong_ne_wrong_j3'])}",
      *[f"  - {e['id']}" for e in S["wrong_ne_wrong_j3"]],
      f"- byte-rule violations: {len(S['byte_rule_violations'])}",
      f"- OK not delivered: {len(S['ok_not_delivered'])}",
      *[f"  - {e['id']}: {e['delivered_cli']}/{e['delivered_gui']}" for e in S["ok_not_delivered"]],
      f"- redundant writes: {len(S['redundant_writes'])}",
      f"- '#' pins vanished/appeared (DEC-23 style judge limit): "
      f"{len(S['hash_pins_vanished_or_appeared'])}", ""]
if md:
    L += ["## Model differential (modeldiff2.py, 93 demo sheets, standalone copies)", "",
          "H narrow = H's C; H coarse = H's coarse refusal model. M2 coarse = M2's P closed under"
          " same-text names (ports and '${' counted as refused); 'no exemption' counts nothing as"
          " refused.", ""]
    rows = []
    for dn in ("H", "M2"):
        t = md["totals"][dn]
        rows.append([dn, t["sheets"], t["cli_narrow_split"], t["gui_narrow_split"],
                     t["cli_narrow_name"], t["gui_narrow_name"], t["cli_coarse_miss"],
                     t["gui_coarse_miss"], t["cli_coarse_miss_noexempt"],
                     t["gui_coarse_miss_noexempt"],
                     f"{t['cli_nets_overjoined']}/{t['cli_nets_with_pins']}",
                     f"{t['gui_nets_overjoined']}/{t['gui_nets_with_pins']}"])
    L += [tbl(["design", "sheets", "narrow split cli", "gui", "narrow name cli", "gui",
               "coarse miss cli", "gui", "no-exemption miss cli", "gui", "over-joined cli",
               "gui"], rows), ""]
    for dn in ("H", "M2"):
        L.append(f"- {dn} narrow-name kinds: {j(md['totals'][dn]['narrow_name_kinds'])}")
        for f in S["modeldiff_findings"][dn][:10]:
            L.append(f"  - {j(f)[:400]}")
    L.append(f"- skipped: {j(S['modeldiff_skipped'])}")
(OUT / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")

# ------------------------------------------------------------------------------ fallback.md
F = ["# Recovery after a connect_pins refusal (measurements A, B, C)", "",
     "Source: records.jsonl (run_sweeps.py samples fallback, recov_b, recov_c, e_split), built"
     " by summarize.py. Each recovery ran on a fresh copy of the before-state and was judged by"
     " judge3 in both readings. 'recovers' = status OK, wrong False in cli and gui, delivered in"
     " both (both pins on one net, each in exactly one net).", ""]
for key, title in (("A_H_recovery", "A. H connect_pins refusals"),
                   ("C_Hb_only_recovery", "C. Hb refusals H did not make")):
    R = S[key]
    F += [f"## {title}: {R['total'].get('pairs', 0)} pairs", "",
          "(a) H.wire_pins_to_net([p1, p2], 'FALLBK'); (b) wire_pins_to_net([other pin], name of"
          " the named pin's cli net), both directions, only where a pin's net is user-named;"
          " (c) repo add_label('FALLBK') at every drawn copy of both pin ends.", ""]
    rows = [[c, p["pairs"], p["a"], f"{p['b']} (of {p['b_applicable']} with a named net)",
             p["c"], p["any"], p["none"]] for c, p in R["by_code"].items()]
    t = R["total"]
    if t:
        rows.append(["total", t["pairs"], t["a"], f"{t['b']} (of {t['b_applicable']})", t["c"],
                     t["any"], t["none"]])
    F += [tbl(["refusal code", "pairs", "(a)", "(b)", "(c)", "any", "none"], rows), "",
          f"Wrong recoveries: {len(R['wrong_recoveries'])}"]
    for e in R["wrong_recoveries"]:
        F.append(f"- {e['id']}: fired cli {j(e['cli_fired'])[:300]} gui {j(e['gui_fired'])[:300]}"
                 f"; cause: {e.get('cause', '(see summary)')}")
    F += ["", "Per pair:", "", tbl(["sheet", "case", "code", "(a)", "(b)", "(c)"],
                                  [[c["sheet"], c["case"], c["code"], c["a"], j(c["b"]), c["c"]]
                                   for c in R["cases"]]), ""]
B = S["B_E_claim"]
F += ["## B. E's suggested fallback (wire_pins_to_net with one shared name)", "",
      f"E connect_pins refusals: {B['pairs_E_refused']}. Both pins free per E (no pin-state code"
      f" {sorted(E_PIN_STATE)}): {B['both_free']}, of which the one call recovered on"
      f" {B['both_free_one_call_good']}. Not free: {B['not_free']}.", "",
      f"- not free, one call: {j(B['not_free_one_call'])}",
      f"- not free, two single-pin calls (only where the one call refused): {j(B['not_free_split'])}",
      f"- any wrong write among these: {len(B['any_wrong'])}", "",
      "By connect_pins code:", ""]
F += [f"- {k}: {v}" for k, v in B["by_connect_code"].items()]
F += ["", f"Counterexamples on free pins: {len(B['both_free_counterexamples'])}", ""]
for r in B["both_free_counterexamples"]:
    F.append(f"- {r['sheet']} {r['case']} connect {r['connect_codes']}: one call {r['one_call']};"
             f" split {r['split']} {j(r['split_parts'])}")
(OUT / "fallback.md").write_text("\n".join(F) + "\n", encoding="utf-8")
print("\n".join(L))
