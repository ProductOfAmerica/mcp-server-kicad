"""probeB runner (follow-up 2): re-run the probe scenarios of buses, hierarchy, naming and
units-pads on designs M2 (coarse/m2_impl.py), H (coarse/h_impl.py), Hb (coarse/hb_impl.py: H with
CROSS_BAN = True) and E (coarse/e_impl.py), judged by coarse/judge3.py (follow-up 2 version: adds
named_merge and req_pad_multi; wrong_j3 keeps the follow-up 1 verdict). Copied from
rerun/probeB/rlib.py; changes: four designs, every REFUSED call (any design) gets the follow-up 1
fallbacks (label fallback cached per byte-identical input), pre_hash per call for same-input
comparisons, wrong writes keep before/after fixture files under wrong/.

Import this module FIRST: it redirects TMP/TEMP to a per-process directory under
<run>/probeB/tmp before harness and judge3 are imported (both make temp dirs at import), and it
turns off bytecode writing so nothing is written next to the imported originals.

Per call (one design):
  - the design runs on its own byte-identical copy of the fixture (or, in per-design scripts, on its
    own clone, as the original script did);
  - REFUSED or ERROR with every file byte-identical: no judge needed (judged = "bytes_only");
  - otherwise judge3.snapshot2 of the state BEFORE the call (cached by content hash, so the two
    designs share it when their pre-states are byte-identical), then judge3.assess2 on the result;
  - NOOP with every file byte-identical: the after-readings are the before-readings, so the judge is
    judge3.judge(before, before, ...) on the snapshot (judged = "snapshot_reuse"). This also keeps
    kicad-cli's known nondeterminism on shorted rails (probe-hierarchy/repeat_export.out) out of
    files nobody changed.
When every design has a result for a (script, variant, step), the set is finalised: one record
per design per case naming that variant, plus fallbacks for every refusal.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.dont_write_bytecode = True
import hpaths  # noqa: E402

# SRC is read-only (scripts/). The custom libraries some scripts write (uplib.write_lib, n14) are
# outputs. Every write goes under OUT: rec, rec_partial, wrong, logs, tmp, libs, runs.
SRC = Path(__file__).resolve().parent
OUT = hpaths.suite_out("probeB")
PROC_TMP = OUT / "tmp" / f"p{os.getpid()}"
PROC_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(PROC_TMP)
tempfile.tempdir = str(PROC_TMP)

import harness  # noqa: E402,F401  (BASE under PROC_TMP)
import judge3  # noqa: E402

import gui_reading  # noqa: E402
import readings  # noqa: E402

import casemap  # noqa: E402
import e_impl as E  # noqa: E402
import h_impl as H  # noqa: E402
import hb_impl as Hb  # noqa: E402
import m2_impl as M2  # noqa: E402
from mcp_server_kicad import _cst  # noqa: E402
from mcp_server_kicad import _shared  # noqa: E402
from mcp_server_kicad import schematic as REPO  # noqa: E402

import registry  # noqa: E402  (the design is chosen by name at run time: ROUTING_HARNESS_DESIGNS)
MODS = registry.selected()
assert H.CROSS_BAN is False and Hb.MODULE.CROSS_BAN is True and Hb.MODULE is not H
DESIGNS = tuple(MODS)
REC_DIR = OUT / "rec"
KEEP_DIR = OUT / "wrong"
REC_DIR.mkdir(exist_ok=True)
KEEP_DIR.mkdir(exist_ok=True)
FB_CACHE: dict = {}

STATE = {"script": None, "tag": None, "rerun": None, "n": 0}
SNAP: dict = {}
PENDING: dict = {}
_CODE_RE = re.compile(r"^((?:\[[MKHE]:\w+\]\s*)+)")


def _no_verdict(rd):
    """The entry of a reading with no verdict: not taken at all (core/gui_reading.py), or its
    before-state would not load."""
    if rd not in readings.READINGS:
        return {"wrong": None, "unavailable": True,
                "error": "gui reading not available: " + gui_reading.REASON}
    return {"wrong": None, "note": "before unloadable"}


def _cleanup():
    shutil.rmtree(PROC_TMP, ignore_errors=True)


atexit.register(_cleanup)


def set_script(sid, rerun_file, shard=None):
    if sid not in casemap.SCRIPTS:
        raise KeyError(sid)
    STATE["script"] = sid
    STATE["tag"] = sid if shard is None else f"{sid}__{shard}"
    STATE["rerun"] = str(Path(rerun_file).resolve())


def wanted(vid) -> bool:
    return bool(casemap.cases_for(STATE["script"], vid))


# ------------------------------------------------------------------------------------ files


def _uid() -> str:
    STATE["n"] += 1
    return f"{STATE['n']:05d}"


def new_tmp(kind: str) -> Path:
    d = PROC_TMP / kind / _uid()
    d.parent.mkdir(parents=True, exist_ok=True)
    return d


def copy_tree(src, dst) -> Path:
    shutil.copytree(src, dst)
    return Path(dst)


def _sch_files(d):
    return sorted(p for p in Path(d).rglob("*.kicad_sch") if p.is_file())


def file_hashes(d) -> dict:
    d = Path(d)
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in _sch_files(d)}


def content_hash(d) -> str:
    d = Path(d)
    h = hashlib.sha256()
    files = _sch_files(d) + sorted(p for p in d.rglob("*.kicad_pro") if p.is_file())
    for p in files:
        h.update(p.relative_to(d).as_posix().encode())
        h.update(b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def clean_judge_tmp():
    for c in Path(judge3.TMP).iterdir():
        if c.is_dir():
            shutil.rmtree(c, ignore_errors=True)
        else:
            try:
                c.unlink()
            except OSError:
                pass


def keep_copy(ddir, label, before=None):
    """Keep the project files of a flagged call: <wrong>/<label>/after (the result) and, when
    given, <wrong>/<label>/before (the fixture the call ran on)."""
    dst = KEEP_DIR / re.sub(r"[^\w.-]+", "_", label)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    for sub, src in (("after", ddir), ("before", before)):
        if src is None:
            continue
        for p in list(_sch_files(src)) + list(Path(src).rglob("*.kicad_pro")):
            q = dst / sub / p.relative_to(src)
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, q)
    return str(dst)


# ----------------------------------------------------------------------------------- groups


def _instance_refs(root, edited, ref):
    """Per-instance references of the placed symbol(s) with Reference property `ref` in `edited`,
    restricted to the instance paths that are live in the project rooted at `root`."""
    try:
        live = set(judge3.instance_paths(root, edited))
        rt = _cst.parse(Path(edited).read_bytes()).lists[0]
    except Exception:  # noqa: BLE001
        return [ref]
    out = []
    for s in rt.find_all("symbol"):
        if _shared._sym_property_cst(s, "Reference") != ref:
            continue
        inst = s.find("instances")
        if inst is None:
            continue
        for proj in inst.find_all("project"):
            for path in proj.find_all("path"):
                if len(path.atoms) > 1 and path.atoms[1].text in live:
                    r = path.find("reference")
                    if r is not None and len(r.atoms) > 1:
                        out.append(r.atoms[1].text)
    return sorted(set(out)) or [ref]


def _resolve_num(edited, ref, ptxt):
    try:
        root = _cst.parse(Path(edited).read_bytes()).lists[0]
        m = M2._Model(root)
        num, _copies = m.resolve(ref, str(ptxt))
        return num
    except Exception:  # noqa: BLE001
        return str(ptxt)


def compute_groups(tool, kw, root, edited):
    """(groups, pads): groups as judge3 wants them; pads = [(Reference, number)] requested."""
    if tool == "wire_pins_to_net":
        pads, seen = [], set()
        for pd in kw.get("pins") or []:
            if not isinstance(pd, dict):
                continue
            ref, ptxt = pd.get("reference"), pd.get("pin")
            num = _resolve_num(edited, ref, ptxt)
            if (ref, num) in seen:
                continue
            seen.add((ref, num))
            pads.append((ref, num))
        groups = [{(r, num) for r in _instance_refs(root, edited, ref)} for ref, num in pads]
        return groups, pads
    if tool == "connect_pins":
        a = (kw["ref1"], _resolve_num(edited, kw["ref1"], kw["pin1"]))
        b = (kw["ref2"], _resolve_num(edited, kw["ref2"], kw["pin2"]))
        g = {(r, a[1]) for r in _instance_refs(root, edited, a[0])}
        g |= {(r, b[1]) for r in _instance_refs(root, edited, b[0])}
        return [g], [a, b]
    return [], []


# ------------------------------------------------------------------------------------ judge


def get_snapshot(src_dir, root_rel, edited_rel, N):
    """judge3.snapshot2, composed from its two parts so the N-independent part (judge3.snapshot of
    the root) is computed once per pre-state and shared by every N: snapshot2 is exactly
    snapshot(root) plus, when N is given, onN = probe_on_N(root, edited, N), plus 'N'."""
    h = content_hash(src_dir)
    key = (h, root_rel, edited_rel, N)
    if key not in SNAP:
        src_dir = Path(src_dir)
        bkey = (h, root_rel, "__base__")
        if bkey not in SNAP:
            SNAP[bkey] = judge3.snapshot(src_dir / root_rel)
            clean_judge_tmp()
        snap = dict(SNAP[bkey])
        if N is not None:
            snap["onN"] = judge3.probe_on_N(src_dir / root_rel, src_dir / edited_rel, N)
        snap["N"] = N
        SNAP[key] = snap
        clean_judge_tmp()
    return SNAP[key]


def _reuse(snap, groups, N, st):
    """assess2 for a call that left every file byte-identical: after == before by construction."""
    res = {}
    for rd in ("cli", "gui"):
        b = snap.get(rd)
        if b is None:
            res[rd] = _no_verdict(rd)
            continue
        if N is not None:
            onn = (snap.get("onN") or {}).get(rd)
            res[rd] = judge3.judge(b, b, groups, None, n_nodes=onn, n_after=onn or set())
        else:
            res[rd] = judge3.judge(b, b, groups, None)
    res["false_noop"] = st == "NOOP" and not all(
        r.get("delivered", True) for r in (res["cli"], res.get("gui") or {}) if r)
    if N is not None:
        pre = [all(set(g) <= (((snap.get("onN") or {}).get(rd)) or set()) for g in groups)
               for rd in readings.READINGS]
        res["pre_satisfied"] = all(pre)
        res["redundant_write"] = st == "OK" and res["pre_satisfied"]
    return res


def _judge_cap(snap, root, edited, kw, st):
    ref = kw["reference"]
    refs = _instance_refs(root, edited, ref)
    g1 = {(r, "1") for r in refs}
    g2 = {(r, "2") for r in refs}
    res = judge3.assess2(snap, root, [g1], edited_path=edited, status=st)
    after_g = judge3.probe_on_N(root, edited, kw["ground_net"])
    for rd in ("cli", "gui"):
        r = res.get(rd) or {}
        if r.get("wrong") is None:
            continue
        if "appeared" in r:
            r["appeared"] = [n for n in r["appeared"] if n[0] not in refs]
            r["wrong_j3"] = bool(r.get("bad_merges") or r.get("splits") or r.get("renames")
                                 or r.get("class_bad") or r.get("pads_multi")
                                 or r.get("vanished") or r.get("appeared"))
            r["wrong"] = bool(r["wrong_j3"] or r.get("named_merge") or r.get("req_pad_multi"))
        dg = g2 <= (after_g.get(rd) or set())
        r["delivered_power"] = r.get("delivered")
        r["delivered_ground"] = dg
        r["delivered"] = bool(r.get("delivered")) and dg
    res["false_noop"] = False
    res["cap_refs"] = refs
    # Read-back (h11e's question, not a judge3 verdict): does the new symbol carry an instance
    # entry for every live instance path of the edited sheet?
    try:
        live = judge3.instance_paths(root, edited)
        rt = _cst.parse(Path(edited).read_bytes()).lists[0]
        covered = set()
        for s in rt.find_all("symbol"):
            if _shared._sym_property_cst(s, "Reference") != ref:
                continue
            inst = s.find("instances")
            for proj in (inst.find_all("project") if inst is not None else []):
                for path in proj.find_all("path"):
                    covered.add(path.atoms[1].text)
        res["cap_instances"] = {"live": sorted(live), "missing": sorted(set(live) - covered)}
    except Exception as e:  # noqa: BLE001
        res["cap_instances"] = {"error": repr(e)}
    return res


def judge_call(st, changed, tool, kw, groups, root, edited, pre_src, root_rel, edited_rel):
    if st in ("REFUSED", "ERROR") and not changed:
        return None, "bytes_only"
    N = kw.get("label_text") if tool == "wire_pins_to_net" else (
        kw.get("power_net") if tool == "auto_place_decoupling_cap" else None)
    snap = get_snapshot(pre_src, root_rel, edited_rel, N)
    if snap.get("cli") is None and snap.get("gui") is None:
        return {"cli": _no_verdict("cli"), "gui": _no_verdict("gui")}, "not_judgeable"
    if tool == "auto_place_decoupling_cap" and st == "OK":
        res = _judge_cap(snap, root, edited, kw, st)
        judged = "assess2_cap"
    elif not changed and st == "NOOP":
        res = _reuse(snap, groups, N, st)
        judged = "snapshot_reuse"
    else:
        res = judge3.assess2(snap, root, groups, edited_path=edited, status=st)
        judged = "assess2"
    for rd in ("cli", "gui"):  # judge3.judge tests `after is None` first; a reading that could
        if snap.get(rd) is None:  # not load BEFORE the call has no verdict either way
            res[rd] = _no_verdict(rd)
    # Context for the report, not a verdict: the cli net names of every group node before the
    # call (from the snapshot) and, for a write, after it (one extra kicad-cli export).
    try:
        nodes = {n for g in groups for n in g}
        if tool == "auto_place_decoupling_cap":
            nodes |= {(r, p) for r in (res.get("cap_refs") or []) for p in ("1", "2")}
        res["net_names"] = {"before": _names_of(snap.get("cli"), nodes)}
        if changed:
            res["net_names"]["after"] = _names_of(judge3.nets_full(root), nodes)
    except Exception as e:  # noqa: BLE001
        res["net_names"] = {"error": repr(e)}
    clean_judge_tmp()
    return res, judged


def _names_of(nets, nodes):
    if nets is None:
        return None
    out = {}
    for _c, name, _k, v in nets:
        for n in v & nodes:
            out.setdefault(f"{n[0]}:{n[1]}", []).append(name)
    return out


def _strip_msg(msg: str) -> str:
    for pre in ("ToolError: ", "_Refuse: "):
        if msg.startswith(pre):
            return msg[len(pre):]
    return msg


def codes_of(msg: str) -> list:
    m = _CODE_RE.match(_strip_msg(msg))
    return re.findall(r"\[[MKHE]:(\w+)\]", m.group(1)) if m else []


def _code_prefix(msg: str) -> str:
    m = _CODE_RE.match(_strip_msg(msg))
    return m.group(1).strip() if m else ""


def _detail(r):
    if not r:
        return {}
    return {k: v for k, v in r.items() if v and k not in ("delivered_each",)}


# ---------------------------------------------------------------------------------- designs


def _call(fn, **kw):
    """judge3.call's status/message, plus the traceback text of an ERROR (non-ToolError)."""
    try:
        msg = fn(**kw)
    except Exception as e:  # noqa: BLE001
        cls = type(e).__name__
        if cls in ("ToolError", "_Refuse"):
            return "REFUSED", f"{cls}: {e}", None
        return "ERROR", f"{cls}: {e}", traceback.format_exc()
    text = str(msg)
    if text.lower().startswith("no change"):
        return "NOOP", text, None
    return "OK", text, None


def exec_design(design, *, vid, step, tool, kw, root, edited, pre_src=None, note="", nsteps=1,
                fn=None):
    """Run one design's call on its own copy (root/edited inside it), judge, and register it."""
    root, edited = Path(root), Path(edited)
    ddir = root.parent
    root_rel = root.relative_to(ddir).as_posix()
    edited_rel = edited.resolve().relative_to(ddir.resolve()).as_posix()
    N = kw.get("label_text") if tool == "wire_pins_to_net" else (
        kw.get("power_net") if tool == "auto_place_decoupling_cap" else None)
    pre_copy = None
    if pre_src is None:
        pre_copy = copy_tree(ddir, new_tmp("pre"))
        pre_src = pre_copy
    groups, pads = compute_groups(tool, kw, root, edited)
    pre_hash = content_hash(ddir)
    h0 = file_hashes(ddir)
    t0 = time.perf_counter()
    st, msg, tb = _call(fn or getattr(MODS[design], tool), **kw)
    dt = time.perf_counter() - t0
    h1 = file_hashes(ddir)
    changed = sorted(k for k in set(h0) | set(h1) if h0.get(k) != h1.get(k))
    try:
        res, judged = judge_call(st, changed, tool, kw, groups, root, edited, pre_src, root_rel,
                                 edited_rel)
    except Exception as e:  # noqa: BLE001
        res, judged = None, f"judge_error: {type(e).__name__}: {e}"
        traceback.print_exc()
    stash = None
    if st in ("REFUSED", "ERROR") and not changed and tool in ("wire_pins_to_net", "connect_pins"):
        stash = copy_tree(ddir, new_tmp("stash"))
    out = {
        "design": design, "status": st, "msg": msg, "codes": codes_of(msg), "seconds": round(dt, 3),
        "files_changed": changed, "bytes_ok": (st == "OK") == bool(changed) or (
            st == "ERROR" and not changed),
        "judged": judged, "res": res, "groups": [sorted(g) for g in groups], "pads": pads,
        "N": N, "tool": tool, "kw": {k: v for k, v in kw.items() if k not in ("schematic_path",
                                                                           "project_path")},
        "root_rel": root_rel, "edited_rel": edited_rel, "stash": str(stash) if stash else None,
        "note": note, "step": step, "nsteps": nsteps, "pre_hash": pre_hash,
        "traceback": tb,
    }
    if res and (_wrong(res, "cli") or _wrong(res, "gui") or res.get("false_noop")):
        out["kept"] = keep_copy(ddir, f"{STATE['tag']}__{vid}__{step}__{design}", before=pre_src)
    if pre_copy is not None:
        shutil.rmtree(pre_copy, ignore_errors=True)
    print(f"[{STATE['tag']}] {vid}#{step} {design} {tool}: {st} {out['codes']} "
          f"judged={judged} cli={_flag(res, 'cli')} gui={_flag(res, 'gui')} "
          f"dlv={_dl(res, 'cli')}/{_dl(res, 'gui')} fn={res.get('false_noop') if res else None}"
          f" | {_strip_msg(msg)[:160]!r}", flush=True)
    key = (STATE["script"], vid, step)
    PENDING.setdefault(key, {})[design] = out
    if all(d in PENDING[key] for d in DESIGNS):
        finalize(key)
    return out


def _wrong(res, rd):
    r = (res or {}).get(rd) or {}
    return bool(r.get("wrong"))


def _flag(res, rd):
    if res is None:
        return "-"
    r = res.get(rd) or {}
    w = r.get("wrong")
    return "n/a" if w is None else ("WRONG" if w else "clean")


def _dl(res, rd):
    if res is None:
        return "-"
    r = res.get(rd) or {}
    d = r.get("delivered")
    return "?" if d is None else ("Y" if d else "N")


def run_pair(*, vid, src_dir, root_rel, edited_rel, tool, kw, note="", project_rel=None):
    """Every design, each on its own byte-identical copy of src_dir (one call each)."""
    if not wanted(vid):
        return
    src_dir = Path(src_dir)
    for d in DESIGNS:
        dd = copy_tree(src_dir, new_tmp("calls"))
        kw2 = dict(kw)
        kw2["schematic_path"] = str(dd / edited_rel)
        if kw2.get("project_path"):
            kw2["project_path"] = str(dd / (project_rel or Path(kw2["project_path"]).name))
        try:
            exec_design(d, vid=vid, step=1, tool=tool, kw=kw2, root=dd / root_rel,
                        edited=dd / edited_rel, pre_src=src_dir, note=note)
        finally:
            shutil.rmtree(dd, ignore_errors=True)


def run_seq(*, vid, src_dir, root_rel, steps, note=""):
    """Every design, each on one copy of src_dir; steps [(edited_rel, tool, kw)] in order."""
    if not wanted(vid):
        return
    src_dir = Path(src_dir)
    for d in DESIGNS:
        dd = copy_tree(src_dir, new_tmp("calls"))
        try:
            for i, (rel, tool, kw) in enumerate(steps, 1):
                kw2 = dict(kw)
                kw2["schematic_path"] = str(dd / rel)
                exec_design(d, vid=vid, step=i, tool=tool, kw=kw2, root=dd / root_rel,
                            edited=dd / rel, pre_src=src_dir if i == 1 else None, note=note,
                            nsteps=len(steps))
        finally:
            shutil.rmtree(dd, ignore_errors=True)


# -------------------------------------------------------------------------------- fallbacks


def _fresh_name(d) -> str:
    texts = [p.read_bytes() for p in _sch_files(d)]
    i = 1
    while True:
        name = f"FBNET{i}"
        if not any(name.encode() in t for t in texts):
            return name
        i += 1


def _fb_judge(stash, root_rel, edited_rel, fbdir, N, groups, st, changed):
    if st in ("REFUSED", "ERROR") and not changed:
        return None, "bytes_only"
    snap = get_snapshot(stash, root_rel, edited_rel, N)
    if snap.get("cli") is None and snap.get("gui") is None:
        return {"cli": _no_verdict("cli"), "gui": _no_verdict("gui")}, "not_judgeable"
    res = judge3.assess2(snap, Path(fbdir) / root_rel, groups,
                         edited_path=Path(fbdir) / edited_rel, status=st)
    for rd in ("cli", "gui"):
        if snap.get(rd) is None:
            res[rd] = _no_verdict(rd)
    clean_judge_tmp()
    return res, "assess2"


def _fb_label(r, N, pads, groups):
    key = (content_hash(r["stash"]), r["root_rel"], r["edited_rel"], N, tuple(map(tuple, pads)))
    if key not in FB_CACHE:
        FB_CACHE[key] = _fb_label_run(r, N, pads, groups)
    return dict(FB_CACHE[key])


def _fb_label_run(r, N, pads, groups):
    stash = Path(r["stash"])
    fbdir = copy_tree(stash, new_tmp("fb"))
    edited = fbdir / r["edited_rel"]
    h0 = file_hashes(fbdir)
    placed, st, msg = [], "OK", ""
    try:
        for ref, num in pads:
            pts = M2.pin_positions(str(edited), ref).get(num, [])
            if not pts:
                raise RuntimeError(f"no drawn copy of {ref}:{num} on this sheet")
            for x, y, _o in pts:
                REPO.add_label(text=N, x=x, y=y, schematic_path=str(edited))
                placed.append((x, y))
        msg = f"add_label '{N}' at {placed}"
    except Exception as e:  # noqa: BLE001
        st = "REFUSED" if type(e).__name__ == "ToolError" else "ERROR"
        msg = f"{type(e).__name__}: {e} (placed before the failure: {placed})"
    h1 = file_hashes(fbdir)
    changed = sorted(k for k in set(h0) | set(h1) if h0.get(k) != h1.get(k))
    try:
        res, judged = _fb_judge(stash, r["root_rel"], r["edited_rel"], fbdir, N, groups, st,
                                changed)
    except Exception as e:  # noqa: BLE001
        res, judged = None, f"judge_error: {type(e).__name__}: {e}"
    out = {"fallback": "fallback_label", "tool": "add_label", "status": st, "msg": msg, "N": N,
           "files_changed": changed, "judged": judged, "res": res}
    if res and (_wrong(res, "cli") or _wrong(res, "gui")):
        out["kept"] = keep_copy(fbdir, f"{STATE['tag']}__fb_label__{_uid()}", before=stash)
    shutil.rmtree(fbdir, ignore_errors=True)
    return out


def _fb_wptn(r, N, kw, groups, mod, modname):
    stash = Path(r["stash"])
    fbdir = copy_tree(stash, new_tmp("fb"))
    edited = fbdir / r["edited_rel"]
    h0 = file_hashes(fbdir)
    pins = [{"reference": kw["ref1"], "pin": kw["pin1"]}, {"reference": kw["ref2"], "pin": kw["pin2"]}]
    st, msg = judge3.call(mod.wire_pins_to_net, pins=pins, label_text=N, schematic_path=str(edited))
    h1 = file_hashes(fbdir)
    changed = sorted(k for k in set(h0) | set(h1) if h0.get(k) != h1.get(k))
    try:
        if st == "NOOP" and not changed:
            snap = get_snapshot(stash, r["root_rel"], r["edited_rel"], N)
            res, judged = _reuse(snap, groups, N, st), "snapshot_reuse"
        else:
            res, judged = _fb_judge(stash, r["root_rel"], r["edited_rel"], fbdir, N, groups, st,
                                    changed)
    except Exception as e:  # noqa: BLE001
        res, judged = None, f"judge_error: {type(e).__name__}: {e}"
    out = {"fallback": "fallback_wptn", "tool": f"{modname}.wire_pins_to_net", "status": st,
           "msg": msg, "codes": codes_of(msg), "N": N, "files_changed": changed, "judged": judged,
           "res": res}
    if res and (_wrong(res, "cli") or _wrong(res, "gui")):
        out["kept"] = keep_copy(fbdir, f"{STATE['tag']}__fb_wptn_{modname}__{_uid()}",
                                before=stash)
    shutil.rmtree(fbdir, ignore_errors=True)
    return out


def run_fallbacks(r):
    """Follow-up 1's fallbacks for a refusal (r = the refusing design's result)."""
    if not r.get("stash"):
        return {"note": "no stash (files changed or tool without fallback)"}
    tool = r["tool"]
    groups = [set(map(tuple, g)) for g in r["groups"]]
    out = {}
    if tool == "wire_pins_to_net":
        out["fallback_label"] = _fb_label(r, r["N"], r["pads"], groups)
    elif tool == "connect_pins":
        F = _fresh_name(r["stash"])
        out["fallback_label"] = _fb_label(r, F, r["pads"], groups)
        mod = MODS[r["design"]]
        out["fallback_wptn"] = _fb_wptn(r, F, r["kw"], groups, mod, r["design"])
    for v in out.values():
        print(f"    fallback {v['fallback']} for {r['design']}: {v['status']} judged={v['judged']}"
              f" cli={_flag(v['res'], 'cli')} gui={_flag(v['res'], 'gui')}"
              f" dlv={_dl(v['res'], 'cli')}/{_dl(v['res'], 'gui')} | {v['msg'][:120]!r}",
              flush=True)
    return out


# ---------------------------------------------------------------------------------- records


def _short_res(res):
    if res is None:
        return {"cli_wrong": None, "gui_wrong": None, "cli_wrong_j3": None, "gui_wrong_j3": None,
                "delivered_cli": None, "delivered_gui": None}
    c, g = res.get("cli") or {}, res.get("gui") or {}
    return {"cli_wrong": c.get("wrong"), "gui_wrong": g.get("wrong"),
            "cli_wrong_j3": c.get("wrong_j3"), "gui_wrong_j3": g.get("wrong_j3"),
            "delivered_cli": c.get("delivered"), "delivered_gui": g.get("delivered")}


def _record(r, case, other):
    res = r["res"]
    sid = STATE["script"]
    meta = casemap.SCRIPTS[sid]
    rec = {
        "record_type": "call", "sample": "probe", "group": meta["cat"], "case": case,
        "variant": r["vid"], "step": r["step"], "nsteps": r["nsteps"], "tool": r["tool"],
        "design": r["design"], "status": r["status"], "codes": r["codes"],
        "code_prefix": _code_prefix(r["msg"]),
        "msg_head": _strip_msg(r["msg"])[:200],
    }
    if res is None:
        bo = False if r["judged"] == "bytes_only" else None
        rec.update({"cli_wrong": bo, "gui_wrong": bo, "cli_wrong_j3": bo, "gui_wrong_j3": bo,
                    "cli_detail": {}, "gui_detail": {}, "delivered_cli": None,
                    "delivered_gui": None, "false_noop": None, "pre_satisfied": None,
                    "redundant_write": None})
    else:
        c, g = res.get("cli") or {}, res.get("gui") or {}
        rec.update({"cli_wrong": c.get("wrong"), "gui_wrong": g.get("wrong"),
                    "cli_wrong_j3": c.get("wrong_j3"), "gui_wrong_j3": g.get("wrong_j3"),
                    "cli_detail": _detail(c), "gui_detail": _detail(g),
                    "delivered_cli": c.get("delivered"), "delivered_gui": g.get("delivered"),
                    "false_noop": res.get("false_noop"), "pre_satisfied": res.get("pre_satisfied"),
                    "redundant_write": res.get("redundant_write")})
    rec.update({
        "script": meta["orig"], "rerun_script": STATE["rerun"], "run_id": f"{sid}:{r['vid']}:{r['step']}",
        "judged": r["judged"], "files_changed": r["files_changed"], "bytes_ok": r["bytes_ok"],
        "N": r["N"], "groups": r["groups"], "other_design_status": other,
        "pre_hash": r["pre_hash"], "traceback": r.get("traceback"),
        "seconds": r["seconds"], "note": r["note"], "kept": r.get("kept"), "kw": r["kw"],
    })
    if res is not None and "net_names" in res:
        rec["net_names"] = res["net_names"]
    if res is not None and "cap_instances" in res:
        rec["cap_instances"] = res["cap_instances"]
        rec["cap_refs"] = res.get("cap_refs")
    if r.get("fallbacks") is not None:
        rec["fallbacks"] = {k: dict(status=v["status"], judged=v["judged"],
                                    codes=v.get("codes", []), **_short_res(v["res"]))
                            for k, v in (r.get("fallbacks") or {}).items() if isinstance(v, dict)}
    return rec


def _fb_record(r, fb, case):
    sid = STATE["script"]
    meta = casemap.SCRIPTS[sid]
    res = fb["res"]
    c, g = (res or {}).get("cli") or {}, (res or {}).get("gui") or {}
    return {
        "record_type": "fallback", "sample": "probe", "group": meta["cat"], "case": case,
        "variant": r["vid"], "step": r["step"], "for_design": r["design"],
        "fallback": fb["fallback"], "tool": fb["tool"], "design": fb["fallback"],
        "status": fb["status"], "codes": fb.get("codes", []), "msg_head": _strip_msg(fb["msg"])[:200],
        "N": fb["N"], "cli_wrong": c.get("wrong") if res else (False if fb["judged"] == "bytes_only"
                                                              else None),
        "gui_wrong": g.get("wrong") if res else (False if fb["judged"] == "bytes_only" else None),
        "cli_wrong_j3": c.get("wrong_j3"), "gui_wrong_j3": g.get("wrong_j3"),
        "cli_detail": _detail(c), "gui_detail": _detail(g), "delivered_cli": c.get("delivered"),
        "delivered_gui": g.get("delivered"), "false_noop": (res or {}).get("false_noop"),
        "pre_satisfied": (res or {}).get("pre_satisfied"),
        "redundant_write": (res or {}).get("redundant_write"), "script": meta["orig"],
        "rerun_script": STATE["rerun"], "run_id": f"{sid}:{r['vid']}:{r['step']}",
        "judged": fb["judged"], "files_changed": fb["files_changed"], "kept": fb.get("kept"),
        "pre_hash": r["pre_hash"],
    }


def _write(recs):
    path = REC_DIR / f"{os.environ.get('PROBEB_JOB') or STATE['tag']}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        for rec in recs:
            f.write(json.dumps(rec, default=_jd, ensure_ascii=False) + "\n")


def _jd(o):
    if isinstance(o, (set, frozenset, tuple)):
        return sorted(o, key=str) if isinstance(o, (set, frozenset)) else list(o)
    return str(o)


def finalize(key):
    sid, vid, step = key
    rs = PENDING.pop(key)
    for r in rs.values():
        r["vid"] = vid
    for a in rs.values():
        if a["status"] == "REFUSED":
            try:
                a["fallbacks"] = run_fallbacks(a)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                a["fallbacks"] = {"error": f"{type(e).__name__}: {e}"}
    cases = casemap.cases_for(sid, vid)
    recs = []
    for case in cases:
        for d in DESIGNS:
            r = rs.get(d)
            if r is None:
                continue
            other = {o: rs[o]["status"] for o in DESIGNS if o != d and o in rs}
            recs.append(_record(r, case, other))
            for fb in (r.get("fallbacks") or {}).values():
                if isinstance(fb, dict) and "fallback" in fb:
                    recs.append(_fb_record(r, fb, case))
    _write(recs)
    for r in rs.values():
        if r.get("stash"):
            shutil.rmtree(r["stash"], ignore_errors=True)


def flush():
    """Finalise any variant some design never reached (records the designs that ran)."""
    for key in list(PENDING):
        finalize(key)
