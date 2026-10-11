"""Follow-up 2: M2, H, Hb, E on KiCad 9.0.8 demo sheets, every call judged by judge3.

Copied from pr57pt/coarse/rerun/demo/run_sweeps.py (follow-up 1) and extended to four designs.
Fixture building is unchanged: each sheet is repathed once into the run's fixtures directory
(FIXTURES/demo) and every call works on its own byte-identical copy of that file (each design on
its own copy, one fresh copy per call).

Samples (one JSON line per call in records.jsonl):
  demo_wptn     10 demo sheets; every pin kicad-cli puts on a net named unconnected-* or Net-* (no
                '#' reference) gets one wire_pins_to_net call to PT<i> per design.
  demo_connect  the 244 pairs of pr57pt/proto/mine_connect_sweep2.json (baselines/
                demo_connect_pairs.json), connect_pins(a, b) per design.
  fallback      every pair at least one design's connect_pins refused: EVERY design's
                wire_pins_to_net([a, b], 'FALLBK') on a fresh copy (identical inputs for all four).
                The follow-up 1 "160-pair" sample is the subset M2 refused (field m2_refused).
  recov_b       (measurement A/C) for every pair H refused (and, for Hb, every pair Hb refused that
                H did not): when a's cli net carries a user name X (not Net-(/unconnected-(, one
                leading '/' stripped), that design's wire_pins_to_net([b], X); symmetric for b.
  recov_c       (measurement A/C) same pairs: the repo's add_label('FALLBK', x, y) at every drawn
                copy of a and of b (m2_impl.pin_positions, KiCad's transform), one call per label.
  e_split       (measurement B) every pair E's connect_pins refused whose one-call E fallback
                refused: E.wire_pins_to_net([a], 'FALLBK') then E.wire_pins_to_net([b], 'FALLBK')
                on one fresh copy, the final file judged.

Judging (judge3.assess2, cli measured, gui emulated): one before-snapshot per sheet (taken from the
fixture in this pass, task_prepare), shared by every call on that sheet. wire_pins_to_net-shaped
calls are judged with N (probe on N before, taken once per call and N); connect_pins with N=None.
groups: one group holding every requested pad. A REFUSED call only gets the byte check.
Every wrong or false-no-op call keeps its before and after files under wrong/ (in the run's demo
output directory).
usage: run_sweeps.py [wptn] [connect] [--workers=12] [--limit=N] [--prepare-only]
       (default both; finished tasks in records.jsonl are skipped)
       --prepare-only: prepare every sheet (fixtures, base readings, prepare.json), log the
       comparisons with follow-up 1 (same fixture bytes for the 11 sheets, same candidate list for
       the 10 wptn sheets) and exit 1 if any is false; no call is made.
ROUTING_HARNESS_FU2=1 restores follow-up 2's two rules in task_conn: the fallback sample only for the
pairs some design's connect_pins refused, and the recov_b and recov_c recoveries after an H (or
Hb-only) refusal. Default (follow-up 3): the fallback for every pair, no recoveries.
Run: uv run python tools/routing_harness/suites/demo/run_sweeps.py wptn --limit=10 --workers=4
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

import common  # noqa: E402,F401  (must be first: bytecode off, per-process TMP)

import concurrent.futures as cf
import hashlib
import json
import multiprocessing
import os
import pickle
import re
import shutil
import time
import traceback

from common import (DESIGNS, M2, OUT, REPO_SCH, STOCK, assess2, call, clean_tmp, codes_of,
                    detail, judge3, repath)

SCRIPT = str(Path(__file__).resolve())
REC = OUT / "records.jsonl"
FIX = hpaths.FIXTURES / "demo"
BASE = OUT / "base"
WORK = OUT / "work"
WRONG = OUT / "wrong"
LOG = OUT / "run_sweeps.log"
# follow-up 1, read only: sha256 of each fixture and its candidate pins (baselines/derive.py)
FU1 = hpaths.BASELINES / "demo_followup1.json"
FU2 = hpaths.FU2  # ROUTING_HARNESS_FU2=1: follow-up 2's fallback and recovery rules in task_conn
WPTN_SHEETS = [
    "vme-wren/front-panel-io.kicad_sch",
    "vme-wren/fpga-hp-banks.kicad_sch",
    "vme-wren/power-supply-1.kicad_sch",
    "vme-wren/clocks.kicad_sch",
    "complex_hierarchy/ampli_ht.kicad_sch",
    "tiny_tapeout/rp2040.kicad_sch",
    "kit-dev-coldfire-xilinx_5213/in_out_conn.kicad_sch",
    "video/rams.kicad_sch",
    "interf_u/interf_u.kicad_sch",
    "stickhub/StickHub.kicad_sch",
]
CONN_SRC = hpaths.BASELINES / "demo_connect_pairs.json"
FB_NAME = "FALLBK"
ORDER = tuple(DESIGNS)
AUTO = ("Net-(", "unconnected-(")


def tag_of(rel):
    return rel.replace("/", "__")


def fixture(rel) -> Path:
    return FIX / tag_of(rel) / Path(rel).name


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def fu1_baseline() -> dict:
    """baselines/demo_followup1.json: {sheet: {"sha256": fixture bytes, "cand": candidate pins}}."""
    return json.loads(FU1.read_text(encoding="utf-8"))


_BASE: dict = {}


def base_snap(rel) -> dict:
    # pickle is safe here: the only pickles read are the ones task_prepare wrote under BASE.
    if rel not in _BASE:
        _BASE[rel] = pickle.loads((BASE / f"{tag_of(rel)}.pkl").read_bytes())
    return _BASE[rel]


# ------------------------------------------------------------------------------------ pieces


def task_prepare(rel):
    """Repath the sheet into its own fixture directory and take its cli/gui base readings."""
    dst = fixture(rel)
    if dst.parent.exists():
        shutil.rmtree(dst.parent)
    repath(STOCK / rel, dst)
    base = judge3.snapshot(dst)
    again = judge3.nets_full(dst)
    BASE.mkdir(parents=True, exist_ok=True)
    (BASE / f"{tag_of(rel)}.pkl").write_bytes(pickle.dumps(base))
    cand = []
    if base.get("cli"):
        cand = [node for _c, name, _k, v in base["cli"]
                if name.startswith(("unconnected-", "Net-"))
                for node in sorted(v) if not node[0].startswith("#")]
    extra = sorted(p.name for p in dst.parent.iterdir() if p != dst)
    fu1 = fu1_baseline().get(rel)
    fb_names = sorted({nm for rd in ("cli", "gui") for _c, nm, _k, _v in (base.get(rd) or [])
                       if FB_NAME in nm})
    clean_tmp()
    return {"rel": rel, "cand": cand, "cli_ok": base.get("cli") is not None,
            "gui_ok": base.get("gui") is not None, "gui_error": base.get("gui_error"),
            "cli_deterministic": again == base.get("cli"), "fixture_dir_extra": extra,
            "sha256": sha(dst),
            "same_bytes_as_followup1": fu1 is not None and fu1["sha256"] == sha(dst),
            "nets_containing_FALLBK": fb_names}


def run_call(fn, kwargs, src: Path, d: Path):
    d.mkdir(parents=True, exist_ok=True)
    q = d / src.name
    shutil.copyfile(src, q)
    h0 = sha(q)
    t0 = time.perf_counter()
    st, msg = call(fn, schematic_path=str(q), **kwargs)
    dt = time.perf_counter() - t0
    others = sorted(p.name for p in d.iterdir() if p != q)
    return {"q": q, "st": st, "msg": msg, "changed": sha(q) != h0, "dt": dt, "others": others}


def judged(snap, q, groups, st) -> dict:
    res = assess2(snap, q, groups, edited_path=q, status=st)
    cli, gui = res.get("cli") or {}, res.get("gui") or {}
    return {"cli_wrong": cli.get("wrong"), "gui_wrong": gui.get("wrong"),
            "cli_wrong_j3": cli.get("wrong_j3"), "gui_wrong_j3": gui.get("wrong_j3"),
            "cli_detail": detail(cli), "gui_detail": detail(gui),
            "delivered_cli": cli.get("delivered"), "delivered_gui": gui.get("delivered"),
            "false_noop": res.get("false_noop"), "pre_satisfied": res.get("pre_satisfied"),
            "redundant_write": res.get("redundant_write")}


def wkind(st, msg):
    if st != "OK":
        return st
    if msg.startswith("Connected "):
        return "OK-" + msg.split("(", 1)[1].split(":", 1)[0]
    stub = re.search(r"stub \w+ [\d.]+ mm from", msg) is not None
    lop = "on the pin end" in msg
    return "OK-mixed" if stub and lop else "OK-stub" if stub else "OK-label-on-pin" if lop else "OK-?"


def record(sample, rel, case, tool, design, c, j=None, **extra) -> dict:
    st, msg, changed = c["st"], c["msg"], c["changed"]
    rec = {"sample": sample, "group": rel, "case": case, "tool": tool, "design": design,
           "status": st, "codes": codes_of(msg) if st == "REFUSED" else [],
           "msg_head": msg[:400], "cli_wrong": None, "gui_wrong": None, "cli_wrong_j3": None,
           "gui_wrong_j3": None, "cli_detail": None, "gui_detail": None, "delivered_cli": None,
           "delivered_gui": None, "false_noop": None, "pre_satisfied": None,
           "redundant_write": None, "script": SCRIPT, "kind": wkind(st, msg),
           "bytes_changed": changed, "bytes_ok": changed if st == "OK" else not changed,
           "other_files_written": c["others"], "seconds": round(c["dt"], 3), "msg": msg}
    if j:
        rec.update(j)
    rec.update(extra)
    return rec


def keep_if_bad(rec, src: Path, q: Path):
    """Keep the before and after files of every wrong write or false no-op under wrong\\."""
    if rec.get("cli_wrong") or rec.get("gui_wrong") or rec.get("false_noop"):
        name = f"{rec['sample']}__{tag_of(rec['group'])}__{rec['case']}__{rec['design']}"
        name = re.sub(r"[^\w.\-]+", "_", name)
        d = WRONG / name
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, d / ("before_" + src.name))
        shutil.copyfile(q, d / ("after_" + src.name))
        rec["kept"] = str(d)


class Snaps:
    """Before-snapshots of one sheet: the shared base readings plus a probe on N per N."""

    def __init__(self, rel, src):
        self.rel, self.src, self.by = rel, src, {}

    def get(self, N):
        if N not in self.by:
            s = dict(base_snap(self.rel))
            if N is not None:
                s["onN"] = judge3.probe_on_N(self.src, self.src, N)
            s["N"] = N
            self.by[N] = s
        return self.by[N]


# ------------------------------------------------------------------------------------- tasks


def task_wptn(rel, idx, ref, num):
    t0 = time.perf_counter()
    N = f"PT{idx:03d}"
    src = fixture(rel)
    wd = WORK / f"p{os.getpid()}" / f"w_{tag_of(rel)}_{idx}"
    if wd.exists():
        shutil.rmtree(wd)
    kw = {"pins": [{"reference": ref, "pin": num}], "label_text": N}
    snaps = Snaps(rel, src)
    groups = [{(ref, num)}]
    out = []
    for d in ORDER:
        c = run_call(DESIGNS[d].wire_pins_to_net, kw, src, wd / d)
        j = judged(snaps.get(N), c["q"], groups, c["st"]) if c["st"] != "REFUSED" else None
        r = record("demo_wptn", rel, f"{ref}:{num}", "wire_pins_to_net", d, c, j, label_text=N,
                   call=f"wire_pins_to_net([{ref}:{num}], {N})")
        keep_if_bad(r, src, c["q"])
        out.append(r)
    shutil.rmtree(wd, ignore_errors=True)
    clean_tmp()
    for r in out:
        r["task_seconds"] = round(time.perf_counter() - t0, 1)
    return out


def net_name(nets, node):
    """The user name of node's net in a reading, or None (auto name, or not listed)."""
    for _c, name, _k, v in nets or []:
        if node in v:
            if name.startswith(AUTO) or not name:
                return None
            return name[1:] if name.startswith("/") else name
    return None


def add_labels(src: Path, pads, N, d: Path):
    """The repo's add_label(N, x, y) at every drawn copy of every pad, one call per label."""
    d.mkdir(parents=True, exist_ok=True)
    q = d / src.name
    shutil.copyfile(src, q)
    h0 = sha(q)
    pts = [(ref, num, x, y) for ref, num in pads
           for x, y, _o in M2.pin_positions(str(q), ref).get(num, [])]
    st, msgs = ("OK" if pts else "NOTHING"), []
    t0 = time.perf_counter()
    for _ref, _num, x, y in pts:
        s, m = call(REPO_SCH.add_label, text=N, x=x, y=y, schematic_path=str(q))
        msgs.append(m)
        if s != "OK":
            st = s
            break
    others = sorted(p.name for p in d.iterdir() if p != q)
    return {"q": q, "st": st, "msg": " | ".join(msgs), "changed": sha(q) != h0,
            "dt": time.perf_counter() - t0, "others": others, "points": pts}


def task_conn(rel, idx, a, b):
    t0 = time.perf_counter()
    src = fixture(rel)
    wd = WORK / f"p{os.getpid()}" / f"c_{tag_of(rel)}_{idx}"
    if wd.exists():
        shutil.rmtree(wd)
    case = f"{a[0]}:{a[1]}->{b[0]}:{b[1]}"
    A, B = tuple(a), tuple(b)
    groups = [{A, B}]
    snaps = Snaps(rel, src)
    out = []
    st = {}
    kw = {"ref1": a[0], "pin1": a[1], "ref2": b[0], "pin2": b[1]}
    for d in ORDER:
        c = run_call(DESIGNS[d].connect_pins, kw, src, wd / d)
        st[d] = c["st"]
        j = judged(snaps.get(None), c["q"], groups, c["st"]) if c["st"] != "REFUSED" else None
        r = record("demo_connect", rel, case, "connect_pins", d, c, j,
                   call=f"connect_pins({case})")
        keep_if_bad(r, src, c["q"])
        out.append(r)
    refused_by = [d for d in ORDER if st[d] == "REFUSED"]
    if FU2 and not refused_by:  # follow-up 2 only; follow-up 3 (default) runs the fallback on every pair
        shutil.rmtree(wd, ignore_errors=True)
        clean_tmp()
        for r in out:
            r["task_seconds"] = round(time.perf_counter() - t0, 1)
        return out
    conn_codes = {r["design"]: r["codes"] for r in out}
    pins2 = [{"reference": a[0], "pin": a[1]}, {"reference": b[0], "pin": b[1]}]
    fb = {}
    for d in ORDER:  # the fallback sample: every design, identical input
        c = run_call(DESIGNS[d].wire_pins_to_net, {"pins": pins2, "label_text": FB_NAME}, src,
                     wd / f"fb_{d}")
        j = judged(snaps.get(FB_NAME), c["q"], groups, c["st"]) if c["st"] != "REFUSED" else None
        r = record("fallback", rel, case, "wire_pins_to_net", d, c, j, label_text=FB_NAME,
                   call=f"wire_pins_to_net([{case.replace('->', ', ')}], {FB_NAME})",
                   refused_by=refused_by, m2_refused="M2" in refused_by,
                   own_connect_status=st[d], own_connect_codes=conn_codes[d])
        keep_if_bad(r, src, c["q"])
        fb[d] = r
        out.append(r)
    # Measurements A and C: recoveries after an H (or Hb-only) connect_pins refusal.
    recov = []  # follow-up 3: recovery measurements were follow-up 2's
    if FU2:
        recov = [d for d in ("H", "Hb")
                 if d in st and st[d] == "REFUSED" and (d == "H" or st.get("H") != "REFUSED")]
    if recov:
        cli = base_snap(rel).get("cli")
        names = {A: net_name(cli, A), B: net_name(cli, B)}
        for d in recov:
            for named, other in ((A, B), (B, A)):
                X = names[named]
                if X is None:
                    continue
                kwb = {"pins": [{"reference": other[0], "pin": other[1]}], "label_text": X}
                c = run_call(DESIGNS[d].wire_pins_to_net, kwb, src,
                             wd / re.sub(r"[^\w.\-]+", "_", f"rb_{d}_{named[0]}_{named[1]}"))
                j = judged(snaps.get(X), c["q"], groups, c["st"]) if c["st"] != "REFUSED" else None
                r = record("recov_b", rel, case, "wire_pins_to_net", d, c, j, label_text=X,
                           call=f"wire_pins_to_net([{other[0]}:{other[1]}], {X!r})",
                           named_pin=f"{named[0]}:{named[1]}", recovers_for=d,
                           connect_codes=conn_codes[d])
                keep_if_bad(r, src, c["q"])
                out.append(r)
        c = add_labels(src, [A, B], FB_NAME, wd / "rc")
        j = judged(snaps.get(FB_NAME), c["q"], groups, "OK" if c["st"] == "OK" else c["st"]) \
            if c["st"] != "REFUSED" or c["changed"] else None
        r = record("recov_c", rel, case, "schematic.add_label", "repo", c, j, label_text=FB_NAME,
                   call=f"add_label({FB_NAME!r}) at {len(c['points'])} pin end(s)",
                   points=c["points"], recovers_for=recov,
                   connect_codes={d: conn_codes[d] for d in recov})
        keep_if_bad(r, src, c["q"])
        out.append(r)
    # Measurement B: E's two single-pin calls when E's one-call fallback refused.
    if "E" in st and st["E"] == "REFUSED" and fb["E"]["status"] == "REFUSED":
        d = wd / "es"
        d.mkdir(parents=True, exist_ok=True)
        q = d / src.name
        shutil.copyfile(src, q)
        h0 = sha(q)
        parts = []
        t1 = time.perf_counter()
        for p in pins2:
            s, m = call(DESIGNS["E"].wire_pins_to_net, pins=[p], label_text=FB_NAME,
                        schematic_path=str(q))
            parts.append({"pin": f"{p['reference']}:{p['pin']}", "status": s,
                          "codes": codes_of(m) if s == "REFUSED" else [], "msg_head": m[:300]})
        changed = sha(q) != h0
        sts = [p["status"] for p in parts]
        if all(s == "OK" for s in sts):
            total = "OK"
        elif changed:
            total = "PARTIAL"
        else:
            total = "REFUSED" if "REFUSED" in sts else "/".join(sts)
        c = {"q": q, "st": total, "msg": " || ".join(p["msg_head"] for p in parts),
             "changed": changed, "dt": time.perf_counter() - t1,
             "others": sorted(p.name for p in d.iterdir() if p != q)}
        j = judged(snaps.get(FB_NAME), q, groups, "OK" if changed else total) \
            if changed or total != "REFUSED" else None
        r = record("e_split", rel, case, "wire_pins_to_net x2", "E", c, j, label_text=FB_NAME,
                   call=f"wire_pins_to_net([{a[0]}:{a[1]}], {FB_NAME}); "
                        f"wire_pins_to_net([{b[0]}:{b[1]}], {FB_NAME})",
                   parts=parts, connect_codes=conn_codes["E"])
        r["codes"] = sorted({x for p in parts for x in p["codes"]})
        r["bytes_ok"] = changed if total in ("OK", "PARTIAL") else not changed
        keep_if_bad(r, src, q)
        out.append(r)
    shutil.rmtree(wd, ignore_errors=True)
    clean_tmp()
    for r in out:
        r["task_seconds"] = round(time.perf_counter() - t0, 1)
    return out


# -------------------------------------------------------------------------------------- main


def log(text):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text, flush=True)


def check_identity(prep, sheets) -> int:
    """--prepare-only: log the comparisons with follow-up 1 and return the exit code, 1 if any is
    false: same fixture bytes for every sheet, same candidate list for the wptn sheets, and a
    readable cli base reading for every sheet (the gui reading is not required: it may be
    unavailable)."""
    fu1 = fu1_baseline()
    bad = []
    for rel in sheets:
        if prep[rel]["same_bytes_as_followup1"] is not True:
            bad.append(f"{rel}: fixture bytes differ from follow-up 1")
        if not prep[rel]["cli_ok"]:
            bad.append(f"{rel}: kicad-cli could not read the base file")
    for rel in WPTN_SHEETS:
        cand = prep[rel]["cand"]
        same = [list(x) for x in cand] == [list(x) for x in fu1[rel]["cand"]]
        log(f"candidates {rel}: {len(cand)} here, same list as follow-up 1 {same}")
        if not same:
            bad.append(f"{rel}: candidate pins differ from follow-up 1")
    for b in bad:
        log(f"IDENTITY FAILURE {b}")
    log(f"identity with follow-up 1: {len(sheets)} sheets checked, {len(bad)} failure(s)")
    return 1 if bad else 0


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    which = set(args) or {"wptn", "connect"}
    workers = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--workers=")), 12))
    limit = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--limit=")), 0))
    prepare_only = "--prepare-only" in sys.argv[1:]
    conn = json.load(open(CONN_SRC, encoding="utf-8"))
    done = set()
    if REC.exists():
        for line in REC.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r["sample"] in ("demo_wptn", "demo_connect"):
                done.add((r["sample"], r["group"], r["case"]))
    sheets = list(WPTN_SHEETS) + [s for s in conn if s not in WPTN_SHEETS]
    t0 = time.perf_counter()
    # spawn on every platform: each worker re-imports this script, so common.py gives it its own TMP
    with cf.ProcessPoolExecutor(max_workers=workers,
                                mp_context=multiprocessing.get_context("spawn")) as ex:
        prep = {}
        if not prepare_only and (OUT / "prepare.json").exists() and all(
                (BASE / f"{tag_of(s)}.pkl").exists() for s in sheets):
            prep = json.loads((OUT / "prepare.json").read_text(encoding="utf-8"))
            log("prepare.json and base readings exist; reused (built earlier in this pass)")
        else:
            for r in ex.map(task_prepare, sheets):
                prep[r["rel"]] = r
                log(f"prepared {r['rel']}: {len(r['cand'])} candidate pins, cli_ok {r['cli_ok']},"
                    f" gui_ok {r['gui_ok']} {r['gui_error'] or ''}, cli deterministic"
                    f" {r['cli_deterministic']}, other files in fixture dir"
                    f" {r['fixture_dir_extra']}, same bytes as follow-up 1"
                    f" {r['same_bytes_as_followup1']}, nets containing FALLBK"
                    f" {r['nets_containing_FALLBK']}")
            (OUT / "prepare.json").write_text(json.dumps(prep, indent=1, default=list),
                                              encoding="utf-8")
        if prepare_only:
            return check_identity(prep, sheets)
        tasks = []
        if "wptn" in which:
            fu1 = fu1_baseline()
            for rel in WPTN_SHEETS:
                cand = prep[rel]["cand"]
                log(f"candidates {rel}: {len(cand)} here, same list as follow-up 1"
                    f" {[list(x) for x in cand] == [list(x) for x in fu1[rel]['cand']]}")
                for i, (ref, num) in enumerate(cand):
                    if ("demo_wptn", rel, f"{ref}:{num}") not in done:
                        tasks.append(("wptn", rel, i, ref, num))
        if "connect" in which:
            for rel, rows in conn.items():
                for i, r in enumerate(rows):
                    a, b = r["a"].split(":", 1), r["b"].split(":", 1)
                    if ("demo_connect", rel, f"{r['a']}->{r['b']}") not in done:
                        tasks.append(("conn", rel, i, a, b))
        size = {rel: (STOCK / rel).stat().st_size for rel in sheets}
        tasks.sort(key=lambda t: -size[t[1]])
        if limit:
            tasks = tasks[:limit]
        log(f"{len(tasks)} tasks to run ({len(done)} already recorded), {workers} workers")
        futs = {}
        for t in tasks:
            fn = task_wptn if t[0] == "wptn" else task_conn
            futs[ex.submit(fn, *t[1:])] = t
        n = 0
        for f in cf.as_completed(futs):
            t = futs[f]
            n += 1
            try:
                recs = f.result()
            except Exception:  # noqa: BLE001
                log(f"TASK FAILED {t}: {traceback.format_exc()}")
                continue
            with open(REC, "a", encoding="utf-8") as fh:
                for r in recs:
                    fh.write(json.dumps(r, default=list) + "\n")
            if n % 25 == 0 or n == len(tasks):
                log(f"{n}/{len(tasks)} tasks done, {time.perf_counter() - t0:.0f}s")
    log(f"finished in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
