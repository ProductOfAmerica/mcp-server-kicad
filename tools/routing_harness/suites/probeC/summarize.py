"""Follow-up 2, probeC (geometry-misc, decisions, composite, perf): summary.json and summary.md for
designs M2, H, Hb, E and any other design in the records, from parts/*.jsonl (written in this pass
by the category scripts through rlib
v3, judged by core/judge3.py: cli = kicad-cli netlist, measured; gui = GUI-load proxy then
kicad-cli, emulated; the gui reading is not available in this repository, see
core/readings.py).

Definitions (every figure is counted from the parts files; nothing is carried over):
- call: a record of kind "main" or "seq" for one of the three tools, one per (part, variant, step,
  design). A call that serves several cases (field "also") is counted once.
- wrong write: a call whose cli or gui verdict is wrong (judge3's follow-up 2 verdict; wrong_j3 is
  follow-up 1's). A refused call that left every byte unchanged is not judged.
- false no-op: status NOOP and judge3 false_noop.
- same input: two designs' calls at the same (part, variant, step) whose in_sig (sha256 over every
  file of the design's copy before the call, raw bytes) is equal. Steps of a sequence after the
  designs' files diverged are excluded and counted.
- clean write: status OK, cli and gui wrong False, delivered True in both readings.
- fallback records (fallback_label, fallback_wptn) run on the refusing design's own pre-call state
  for every design that refused a call another design did not refuse.
Run: uv run python tools/routing_harness/suites/probeC/summarize.py  (writes summary.json and
summary.md into the run's probeC output directory)
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

HERE = hpaths.suite_out("probeC")  # the run's probeC output directory: parts/ is read, summary.* written
DESIGNS = ("M2", "H", "Hb", "E")
TOOLS = ("wire_pins_to_net", "connect_pins", "auto_place_decoupling_cap")
PAIRS = (("H", "M2"), ("M2", "H"), ("E", "M2"), ("E", "H"), ("Hb", "H"), ("H", "Hb"))
SCRIPT = str(Path(__file__).resolve())


def load():
    recs = []
    for f in sorted((HERE / "parts").glob("*.jsonl")):
        for i, line in enumerate(f.open(encoding="utf-8")):
            if line.strip():
                r = json.loads(line)
                r["_file"] = f.name
                r["_line"] = i
                recs.append(r)
    return recs


def clean(r):
    return (r.get("status") == "OK" and readings.clean(r)
            and all(r.get(f"delivered_{rd}") is True for rd in readings.READINGS))


def rid(r):
    return f"{r.get('part')} | {r.get('case')} | {r.get('variant')} | s{r.get('step')}"


def main():
    global DESIGNS
    recs = load()
    # designs are picked by name at run time (ROUTING_HARNESS_DESIGNS): follow-up 2's four first, then
    # any other design found in the records; a design with no record gets no column
    found = {r.get("design") for r in recs if r.get("kind") in ("main", "seq")
             and r.get("tool") in TOOLS} - {None}
    DESIGNS = tuple(d for d in (*DESIGNS, *sorted(found - set(DESIGNS))) if d in found)
    calls = [r for r in recs if r.get("kind") in ("main", "seq") and r.get("tool") in TOOLS
             and r.get("design") in DESIGNS]
    fbs = [r for r in recs if r.get("kind") in ("fallback_label", "fallback_wptn")]
    other = Counter(r.get("kind") for r in recs if r not in calls and r not in fbs)
    out = {"script": SCRIPT, "parts_files": sorted({r["_file"] for r in recs}),
           "n_records": len(recs), "n_calls": len(calls), "n_fallback_records": len(fbs),
           "other_record_kinds": dict(other), "designs": {}}
    by_group = defaultdict(lambda: defaultdict(Counter))
    for d in DESIGNS:
        mine = [r for r in calls if r["design"] == d]
        st = Counter(r["status"] for r in mine)
        for r in mine:
            by_group[r.get("group")][d][r["status"]] += 1
        wrong = [{"id": rid(r), "status": r["status"], "cli": r.get("cli_wrong"),
                  "gui": r.get("gui_wrong"), "cli_j3": r.get("cli_wrong_j3"),
                  "gui_j3": r.get("gui_wrong_j3"),
                  "checks": sorted({k for side in ("cli_detail", "gui_detail")
                                    for k in (r.get(side) or {})
                                    if k not in ("delivered", "delivered_power",
                                                 "delivered_ground", "wrong", "wrong_j3")}),
                  "kept": r.get("wrong_kept")}
                 for r in mine if r.get("cli_wrong") or r.get("gui_wrong")]
        j3_diff = [rid(r) for r in mine
                   if bool(r.get("cli_wrong")) != bool(r.get("cli_wrong_j3"))
                   or bool(r.get("gui_wrong")) != bool(r.get("gui_wrong_j3"))]
        fnoop = [rid(r) for r in mine if r["status"] == "NOOP" and r.get("false_noop")]
        redund = [{"id": rid(r), "clean": clean(r)} for r in mine if r.get("redundant_write")]
        errors = [{"id": rid(r), "msg": (r.get("msg_head") or "")[:200]} for r in mine
                  if r["status"] == "ERROR"]
        bytes_bad = [rid(r) for r in mine if r["status"] in ("REFUSED", "NOOP")
                     and r.get("bytes_unchanged") is False]
        judge_none = [rid(r) for r in mine if r["status"] in ("OK", "NOOP")
                      and readings.unjudged(r)]
        ok_not_delivered = [rid(r) for r in mine if r["status"] == "OK"
                            and not all(r.get(f"delivered_{rd}") for rd in readings.READINGS)]
        codes = Counter(c for r in mine if r["status"] == "REFUSED" for c in (r.get("codes") or []))
        out["designs"][d] = {
            "calls": len(mine), "status": dict(st),
            "refusal_rate": round(st.get("REFUSED", 0) / len(mine), 4) if mine else None,
            "refused_codes": dict(codes), "wrong": wrong, "wrong_differs_from_j3": j3_diff,
            "false_noop": fnoop, "redundant_write": redund, "error": errors,
            "bytes_changed_on_refusal_or_noop": bytes_bad, "judge_none": judge_none,
            "ok_not_delivered": ok_not_delivered}
    out["by_group"] = {g: {d: dict(c) for d, c in v.items()} for g, v in sorted(by_group.items())}
    # same-input refusal sets
    slots = defaultdict(dict)
    for r in calls:
        slots[(r.get("part"), r.get("variant"), r.get("kind"), r.get("step"))][r["design"]] = r
    pairs = {}
    for a, b in PAIRS:
        comparable, excluded, only = 0, 0, []
        for _k, s in slots.items():
            if a not in s or b not in s:
                continue
            ra, rb = s[a], s[b]
            if ra.get("in_sig") != rb.get("in_sig"):
                excluded += 1
                continue
            comparable += 1
            if ra["status"] == "REFUSED" and rb["status"] != "REFUSED":
                only.append({"id": rid(ra), "codes": ra.get("codes"), "other_status": rb["status"],
                             "other_clean": clean(rb), "other_wrong": bool(rb.get("cli_wrong")
                                                                           or rb.get("gui_wrong"))})
        pairs[f"{a}-only vs {b}"] = {
            "comparable_slots": comparable, "excluded_diverged": excluded, "count": len(only),
            "by_code": dict(Counter(c for x in only for c in (x["codes"] or ["(none)"]))),
            "other_wrote_clean": sum(x["other_clean"] for x in only), "items": only}
    out["same_input"] = pairs
    # E's "already connected" refusals and what M2 and H did on the same input
    conn = []
    for _k, s in slots.items():
        e = s.get("E")
        if not e or e["status"] != "REFUSED" or "connected" not in (e.get("codes") or []):
            continue
        ent = {"id": rid(e)}
        for o in ("M2", "H"):
            ro = s.get(o)
            if ro is None or ro.get("in_sig") != e.get("in_sig"):
                ent[o] = "not comparable"
            else:
                ent[o] = "clean write" if clean(ro) else ro["status"] + (
                    " wrong" if (ro.get("cli_wrong") or ro.get("gui_wrong")) else "")
        conn.append(ent)
    out["E_connected"] = {
        "count": len(conn),
        "M2_clean_write": sum(x["M2"] == "clean write" for x in conn),
        "H_clean_write": sum(x["H"] == "clean write" for x in conn),
        "items": conn}
    # fallbacks
    fb = defaultdict(Counter)
    fb_wrong = []
    for r in fbs:
        k = f"{r.get('design')} {r.get('kind')}"
        fb[k]["clean" if clean(r) else r.get("status", "?") + (
            " wrong" if (r.get("cli_wrong") or r.get("gui_wrong")) else "")] += 1
        if r.get("cli_wrong") or r.get("gui_wrong"):
            fb_wrong.append({"id": rid(r), "design": r.get("design"), "kind": r.get("kind"),
                             "kept": r.get("wrong_kept")})
    out["fallbacks"] = {k: dict(v) for k, v in sorted(fb.items())}
    out["fallback_wrong"] = fb_wrong
    (HERE / "summary.json").write_text(json.dumps(out, indent=1, default=list), encoding="utf-8")
    # summary.md
    L = ["# probeC follow-up 2 summary (geometry-misc, decisions, composite, perf)", "",
         f"Produced by `{SCRIPT}` from `parts\\*.jsonl` ({len(out['parts_files'])} files,"
         f" {len(recs)} records). Readings: {readings.LABEL}; cli is measured, gui, when taken, is"
         " emulated.", "",
         "| Design | Calls | OK | NOOP | REFUSED | ERROR | Refusal rate | Wrong (cli/gui) |"
         " False no-op | Redundant |", "|---|---|---|---|---|---|---|---|---|---|"]
    for d in DESIGNS:
        x = out["designs"][d]
        s = x["status"]
        wc = sum(1 for w in x["wrong"] if w["cli"])
        wg = sum(1 for w in x["wrong"] if w["gui"]) if "gui" in readings.READINGS else "n/a"
        L.append(f"| {d} | {x['calls']} | {s.get('OK', 0)} | {s.get('NOOP', 0)} |"
                 f" {s.get('REFUSED', 0)} | {s.get('ERROR', 0)} | {x['refusal_rate']} |"
                 f" {wc} / {wg} | {len(x['false_noop'])} | {len(x['redundant_write'])} |")
    L += ["", "## Wrong writes", ""]
    for d in DESIGNS:
        for w in out["designs"][d]["wrong"]:
            L.append(f"- {d}: {w['id']} ({w['status']}; cli {w['cli']}, gui {w['gui']};"
                     f" checks {w['checks']}; kept {w['kept']})")
    L += ["", "## Same-input refusal sets", ""]
    for k, v in pairs.items():
        L.append(f"- {k}: {v['count']} of {v['comparable_slots']} comparable slots"
                 f" ({v['excluded_diverged']} excluded as diverged); by code {v['by_code']};"
                 f" the other design wrote cleanly on {v['other_wrote_clean']}")
    e = out["E_connected"]
    L += ["", f"## E [E:connected]: {e['count']} refusals; M2 wrote cleanly on"
          f" {e['M2_clean_write']}, H on {e['H_clean_write']} (same input)", "",
          "## Fallbacks", ""]
    for k, v in out["fallbacks"].items():
        L.append(f"- {k}: {v}")
    L += ["", f"Other record kinds: {out['other_record_kinds']}"]
    (HERE / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
