"""rlib: shared runner for the probeA follow-up 2 rerun (designs M2, H, Hb, E), judged by
core/judge3.py. Copied from follow-up 1's coarse/rerun/probeA/rlib.py (M2 vs K) and extended:
- DESIGNS is M2, H, Hb, E (K dropped); reason codes [M:..] [H:..] [E:..] are parsed.
- Fallbacks (label on every drawn copy of every requested pad; connect_pins also the refusing
  design's own wire_pins_to_net) now run after EVERY refusal of every design, not only when exactly
  one design refused. The label fallback is design-independent, so it is computed once per distinct
  input tree and step and shared by every design that refused on that input.
- Each row records input_key (the hash of the design's .kicad_sch/.kicad_pro tree before the call),
  so same-input comparisons are exact; cli/gui wrong_j3 (follow-up 1's verdict) beside wrong; and a
  traceback for every ERROR (a non-ToolError exception).
- Every wrong write (wrong or wrong_j3 in either reading) and every false no-op saves the tree
  before and after the call under wrong/<category>/<variant>_s<step>_<design>[_fbl|_fbw]/.
- Speed-only caches (pi_jobs.py's netlist cache keyed on the tree bytes and GUI-proxy cache keyed
  on the input bytes) are installed here for every category.

Port (tools/routing_harness): DESIGNS comes from core/registry.py (the designs named in
hpaths.DESIGNS); records, wrong/ and work/ go to hpaths.suite_out("probeA"), under the run's
directory; the gui reading is not available in this repository (core/gui_reading.py), so records
carry gui_wrong null and _outcome counts only the readings that were taken (core/readings.py).

Follow-up 1 docstring:

A category script builds a list of Job objects and calls rlib.main(JOBS, category). Each Job
builds its fixture once (with the original probe's fixture code, copied into this directory),
copies the fixture tree byte-identically once per design, and runs its steps (tool calls) on
each design's own copy. Every non-refused call is judged with judge3.snapshot2/assess2
(kicad-cli = measured; the GUI-load proxy is not available in this repository). A REFUSED call is only checked for
unchanged bytes. Where exactly one design refuses a step, the refusing design's fallbacks run on
fresh copies of its own (unchanged) state and are judged the same way:
  fallback_label  repo schematic.add_label(N, x, y) on every drawn copy of every requested pad
                  (KiCad-true positions from m2_impl.pin_positions); connect_pins uses a fresh name
  fallback_wptn   connect_pins only: <refusing design>.wire_pins_to_net([pin1, pin2], fresh name)

Scratch: everything goes under work/ of the run's probeA output directory (TMP/TEMP are
redirected before harness and judge3 load); each job gets its own directory, removed when the job
ends.

Builder contract: build(*args) -> (root_path, edited_path) or a single path (root == edited).
Use rlib.fresh(tag) / rlib.newdir(tag) so the fixture lives under the job's directory. The tree
copied per design is the root's parent directory, so the edited file must be inside it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
import uuid as _uuid
from pathlib import Path

import hpaths  # noqa: E402  (an entry script has already put it on sys.path)

OUT = hpaths.suite_out("probeA")
WORK = OUT / "work"
SCR = WORK / "tmp"
SCR.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(SCR)
tempfile.tempdir = str(SCR)

import harness as H  # noqa: E402
import judge3 as J  # noqa: E402
import readings  # noqa: E402
import registry  # noqa: E402
from mcp_server_kicad import _cst  # noqa: E402
from mcp_server_kicad import project as PROJ  # noqa: E402
from mcp_server_kicad import schematic as SCH  # noqa: E402
from mcp_server_kicad._shared import _sym_property_cst  # noqa: E402

import e_impl as ED  # noqa: E402
import h_impl as HD  # noqa: E402
import hb_impl as HBD  # noqa: E402
import m2_impl as M2  # noqa: E402

assert HBD.MODULE.CROSS_BAN is True and HD.CROSS_BAN is False and HBD.MODULE is not HD

DESIGNS = registry.selected()  # the designs named in hpaths.DESIGNS (follow-up 3 ran Hb2 only)
TOOL = {"wptn": "wire_pins_to_net", "cp": "connect_pins"}
_CODE_RE = re.compile(r"^((?:\[[MKHE]:\w+\]\s*)+)")
_LIFT_RE = re.compile(r"\[lifts with: ([^\]]*)\]")
WRONG = OUT / "wrong"

_JOB = {"dir": None, "category": None}

# ------------------------------------------------------------------ speed-only caches
# Copied from follow-up 1's pin-identity/pi_jobs.py (which installs them again on top; harmless).
# gui_load_proxy reads only its input file; its output differs between runs only in fresh wire
# uuids, so a cached output is netlist-identical. nets_full is a function of the bytes of the
# root's directory tree; a failed export (None) is never cached.
_PROXY: dict = {}
_orig_proxy = J.gui_load_proxy


def _cached_proxy(src, dst):
    data = Path(src).read_bytes()
    k = hashlib.sha256(data).hexdigest()
    out = _PROXY.get(k)
    if out is None:
        _orig_proxy(src, dst)
        _PROXY[k] = Path(dst).read_bytes()
    else:
        Path(dst).write_bytes(out)


J.gui_load_proxy = _cached_proxy
_NETS: dict = {}
_orig_nets_full = J.nets_full


def _cached_nets_full(path):
    path = Path(path)
    h = hashlib.sha256(path.name.encode() + b"\0")
    for f in sorted(path.parent.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(path.parent)).replace("\\", "/").encode() + b"\0"
                     + hashlib.sha256(f.read_bytes()).digest())
    k = h.hexdigest()
    hit = _NETS.get(k)
    if hit is not None:
        return hit
    res = _orig_nets_full(path)
    if res is not None:
        _NETS[k] = res
    return res


J.nets_full = _cached_nets_full


# ------------------------------------------------------------------------------- fixtures


def safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(s))[:40] or "x"


def jobdir() -> Path:
    if _JOB["dir"] is None:  # outside a job (interactive tests)
        _JOB["dir"] = Path(tempfile.mkdtemp(prefix="adhoc_", dir=SCR))
    return _JOB["dir"]


def newdir(tag: str = "d") -> Path:
    """A new empty directory under the current job's scratch."""
    return Path(tempfile.mkdtemp(prefix=f"{safe(tag)}_", dir=jobdir()))


def fresh(tag: str = "fx") -> str:
    """New empty schematic (repo create_schematic) in its own directory; returns its path."""
    p = newdir(tag) / f"{safe(tag)}.kicad_sch"
    PROJ.create_schematic(schematic_path=str(p))
    return str(p)


def pos(path, ref, num, i=0):
    """KiCad-true pin end (mm) of copy i (m2_impl.pin_positions)."""
    x, y, _o = M2.pin_positions(str(path), ref)[str(num)][i]
    return round(x, 4), round(y, 4)


def outward(path, ref, num, i=0):
    return M2.pin_positions(str(path), ref)[str(num)][i][2]


# ------------------------------------------------------------------------------- steps


class Job:
    """One scenario: a fixture and one or more tool calls run on each design's own copy.

    cases: list of case ids (the first is the primary; the others get duplicate records).
    variant: unique id within the category, e.g. 'J1/raw_pin_first'.
    origin: original script and sub-case, e.g. 'probe-junctions/J1_own_wire.py raw_pin_first'.
    source: 'probe' or 'verify'.
    """

    def __init__(self, cases, variant, origin, source, build, args=(), steps=(), note=""):
        self.cases = [cases] if isinstance(cases, str) else list(cases)
        self.variant, self.origin, self.source = variant, origin, source
        self.build, self.args, self.steps, self.note = build, tuple(args), list(steps), note


def _pins(pins):
    out = []
    for p in pins:
        if isinstance(p, dict):
            out.append(p)
        elif isinstance(p, (tuple, list)) and len(p) == 2:
            out.append({"reference": p[0], "pin": p[1]})
        else:
            out.append(p)  # deliberately malformed (validation probes)
    return out


def wptn(pins, N, groups=None, **kw):
    """wire_pins_to_net step. pins: [(ref, pin)] or raw dicts. kw: direction, stub_length..."""
    return {"kind": "wptn", "pins": _pins(pins), "N": N, "kw": kw, "groups": groups}


def cp(a, b, groups=None):
    """connect_pins step: a = (ref1, pin1), b = (ref2, pin2)."""
    return {"kind": "cp", "a": tuple(a), "b": tuple(b), "groups": groups}


# ------------------------------------------------------------------------------- helpers


def tree_files(d: Path) -> dict:
    d = Path(d)
    out = {}
    for f in sorted(d.rglob("*")):
        if f.is_file():
            out[str(f.relative_to(d)).replace("\\", "/")] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def tree_key(d: Path) -> str:
    h = hashlib.sha256()
    for k, v in tree_files(d).items():
        if k.endswith((".kicad_sch", ".kicad_pro")):
            h.update(k.encode() + b"\0" + v.encode() + b"\n")
    return h.hexdigest()


def codes_of(msg: str) -> list:
    text = re.sub(r"^(ToolError|_Refuse): ", "", msg or "")
    m = _CODE_RE.match(text)
    return re.findall(r"\[[MKHE]:(\w+)\]", m.group(1)) if m else []


def prefix_of(msg: str) -> str:
    """The raw bracketed code prefix of a message ('' when none)."""
    text = re.sub(r"^(ToolError|_Refuse): ", "", msg or "")
    m = _CODE_RE.match(text)
    return m.group(1).strip() if m else ""


def lifts_of(msg: str) -> list:
    return _LIFT_RE.findall(msg or "")


def _resolve_num(edited, ref, ptxt):
    try:
        _t, root, *_ = SCH._open_sch_cst(str(edited))
        m = M2._Model(root)
        num, _copies = m.resolve(ref, str(ptxt))
        return num
    except Exception:  # noqa: BLE001
        return str(ptxt)


def _inst_refs(edited, ref) -> set:
    """Per-instance references of every placed symbol whose Reference property is ref."""
    out = {ref}
    try:
        root = _cst.parse(Path(edited).read_bytes()).lists[0]
        for s in root.find_all("symbol"):
            if _sym_property_cst(s, "Reference") != ref:
                continue
            inst = s.find("instances")
            if inst is None:
                continue
            for proj in inst.find_all("project"):
                for path in proj.find_all("path"):
                    r = path.find("reference")
                    if r is not None and len(r.atoms) > 1:
                        out.add(r.atoms[1].text)
    except Exception:  # noqa: BLE001
        pass
    return out


def _before_nodes(snap) -> set:
    nodes = set()
    for rd in ("cli", "gui"):
        for _c, _n, _k, v in snap.get(rd) or []:
            nodes |= set(v)
    return nodes


def _pad_nodes(edited, ref, ptxt, before):
    num = _resolve_num(edited, ref, ptxt)
    cand = {(r, num) for r in _inst_refs(edited, ref)}
    present = {n for n in cand if n in before}
    return present or {(ref, num)}


def default_groups(step, edited, snap):
    before = _before_nodes(snap)
    if step["kind"] == "wptn":
        gs = []
        for p in step["pins"]:
            if isinstance(p, dict) and isinstance(p.get("reference"), str) and "pin" in p:
                gs.append(_pad_nodes(edited, p["reference"], p["pin"], before))
        return gs
    a = _pad_nodes(edited, step["a"][0], step["a"][1], before)
    b = _pad_nodes(edited, step["b"][0], step["b"][1], before)
    return [a | b]


def _reading(res, rd):
    r = (res or {}).get(rd) or {}
    return r


def _detail(r):
    return {k: v for k, v in r.items() if v and k not in ("delivered_each",)}


def judged(res):
    cli, gui = _reading(res, "cli"), _reading(res, "gui")
    return {
        "cli_wrong": cli.get("wrong"), "gui_wrong": gui.get("wrong"),
        "cli_wrong_j3": cli.get("wrong_j3"), "gui_wrong_j3": gui.get("wrong_j3"),
        "cli_detail": _detail(cli), "gui_detail": _detail(gui),
        "delivered_cli": cli.get("delivered"), "delivered_gui": gui.get("delivered"),
        "false_noop": (res or {}).get("false_noop"),
        "pre_satisfied": (res or {}).get("pre_satisfied"),
        "redundant_write": (res or {}).get("redundant_write"),
    }


def _empty_judge():
    return {"cli_wrong": None, "gui_wrong": None, "cli_wrong_j3": None, "gui_wrong_j3": None,
            "cli_detail": {}, "gui_detail": {},
            "delivered_cli": None, "delivered_gui": None, "false_noop": None,
            "pre_satisfied": None, "redundant_write": None}


def _copytree(src: Path, tag: str) -> Path:
    dst = newdir(tag) / src.name
    shutil.copytree(src, dst)
    return dst


def _relocate(base_dir: Path, new_dir: Path, path: str) -> Path:
    return new_dir / Path(path).resolve().relative_to(base_dir.resolve())


def _snap(cache, d: Path, root: Path, edited: Path, N):
    key = (tree_key(d), N)
    if key not in cache:
        cache[key] = J.snapshot2(str(root), str(edited), N=N or None)
    return cache[key]


def _fresh_name(tag="FB"):
    return f"{tag}_{_uuid.uuid4().hex[:6].upper()}"


_LAST_TB = {"tb": None}


def _call(fn, **kw):
    """judge3.call's exact classification (OK / NOOP / REFUSED / ERROR), plus the traceback of an
    ERROR kept in _LAST_TB."""
    _LAST_TB["tb"] = None
    try:
        msg = fn(**kw)
    except Exception as e:  # noqa: BLE001
        cls = type(e).__name__
        if cls in ("ToolError", "_Refuse"):
            return "REFUSED", f"{cls}: {e}"
        _LAST_TB["tb"] = traceback.format_exc()[-4000:]
        return "ERROR", f"{cls}: {e}"
    text = str(msg)
    if text.lower().startswith("no change"):
        return "NOOP", text
    return "OK", text


def _call_step(mod, step, edited):
    if step["kind"] == "wptn":
        return _call(mod.wire_pins_to_net, pins=step["pins"], label_text=step["N"],
                     schematic_path=str(edited), **step["kw"])
    (r1, p1), (r2, p2) = step["a"], step["b"]
    return _call(mod.connect_pins, ref1=r1, pin1=p1, ref2=r2, pin2=p2, schematic_path=str(edited))


def _tree_bytes(d: Path) -> dict:
    return {str(f.relative_to(d)): f.read_bytes() for f in sorted(Path(d).rglob("*")) if f.is_file()}


def _dump_tree(files: dict, dst: Path):
    for rel, data in files.items():
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def save_wrong(tag: str, before: dict, after_dir: Path, info: dict):
    """Keep the fixture tree before and after a wrong write / false no-op under wrong/."""
    dst = WRONG / safe(_JOB.get("category") or "x") / tag
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    _dump_tree(before, dst / "before")
    _dump_tree(_tree_bytes(after_dir), dst / "after")
    (dst / "info.json").write_text(json.dumps(info, indent=1, default=str), encoding="utf-8")
    return str(dst)


def _is_wrong(r) -> bool:
    return bool(r.get("cli_wrong") or r.get("gui_wrong") or r.get("cli_wrong_j3")
                or r.get("gui_wrong_j3") or r.get("false_noop"))


def _requested(step):
    if step["kind"] == "wptn":
        return [(p["reference"], str(p["pin"])) for p in step["pins"]
                if isinstance(p, dict) and isinstance(p.get("reference"), str) and "pin" in p]
    return [(step["a"][0], str(step["a"][1])), (step["b"][0], str(step["b"][1]))]


def fallback_label(base_dir, root, edited, step, snap, groups, tag=None):
    """add_label(N, x, y) on every drawn copy of every requested pad, on a fresh copy."""
    d = _copytree(base_dir, "fbl")
    r, e = _relocate(base_dir, d, root), _relocate(base_dir, d, edited)
    N = step["N"] if step["kind"] == "wptn" else _fresh_name("FBL")
    h0 = tree_files(d)
    placed, msgs = [], []
    try:
        for ref, ptxt in _requested(step):
            num = _resolve_num(e, ref, ptxt)
            pp = M2.pin_positions(str(e), ref).get(num, [])
            if not pp:
                raise RuntimeError(f"no drawn copy of {ref}:{num}")
            for x, y, _o in pp:
                msgs.append(SCH.add_label(N, round(x, 4), round(y, 4), schematic_path=str(e)))
                placed.append((ref, num, round(x, 4), round(y, 4)))
        st, msg = "OK", " | ".join(str(m) for m in msgs)
    except Exception as ex:  # noqa: BLE001
        st, msg = "REFUSED", f"{type(ex).__name__}: {ex}"
    out = {"status": st, "label": N, "placed": placed, "msg_head": msg[:200]}
    if st == "OK":
        res = J.assess2(snap, str(r), groups, edited_path=str(e), status="OK")
        out.update(judged(res))
    else:
        out["bytes_unchanged"] = tree_files(d) == h0
    out["outcome"] = _outcome(out)
    if tag and _is_wrong(out):
        out["saved"] = save_wrong(tag + "_fbl", _tree_bytes(base_dir), d, out)
    return out


def fallback_wptn(mod, base_dir, root, edited, step, snap, groups, tag=None):
    """connect_pins fallback: mod.wire_pins_to_net([pin1, pin2], fresh name) on a fresh copy.
    The snapshot passed in has N=None, so this fallback's judging uses no probe (as follow-up 1)."""
    d = _copytree(base_dir, "fbw")
    r, e = _relocate(base_dir, d, root), _relocate(base_dir, d, edited)
    N = _fresh_name("FBW")
    h0 = tree_files(d)
    pins = [{"reference": step["a"][0], "pin": step["a"][1]},
            {"reference": step["b"][0], "pin": step["b"][1]}]
    st, msg = _call(mod.wire_pins_to_net, pins=pins, label_text=N, schematic_path=str(e))
    out = {"status": st, "label": N, "codes": codes_of(msg), "msg_head": msg[:200]}
    if st == "ERROR":
        out["traceback"] = _LAST_TB["tb"]
    if st == "REFUSED":
        out["bytes_unchanged"] = tree_files(d) == h0
    else:
        res = J.assess2(snap, str(r), groups, edited_path=str(e), status=st)
        out.update(judged(res))
    out["outcome"] = _outcome(out)
    if tag and _is_wrong(out):
        out["saved"] = save_wrong(tag + "_fbw", _tree_bytes(base_dir), d, out)
    return out


def _outcome(fb):
    st = fb.get("status")
    if st == "REFUSED":
        return "refused"
    if st == "ERROR":
        return "error"
    if readings.wrong(fb):
        return "wrong"
    if readings.unjudged(fb):
        return "unjudged"
    if all(fb.get(f"delivered_{rd}") for rd in readings.READINGS):
        return "clean_delivered"
    return "clean_not_delivered"


# ------------------------------------------------------------------------------- the runner


def run_job(job: Job, script: str, category: str) -> list:
    jd = Path(tempfile.mkdtemp(prefix="job_", dir=SCR))
    _JOB["dir"] = jd
    _JOB["category"] = category
    hbase, jtmp = H.BASE, J.TMP
    H.BASE = jd / "h"
    H.BASE.mkdir()
    J.TMP = jd / "j3"
    J.TMP.mkdir()
    recs: list = []

    def rec_base(case, primary):
        return {"sample": "probe", "group": category, "case": case, "primary": primary,
                "variant": job.variant, "source": job.source, "origin": job.origin,
                "script": script, "note": job.note}

    try:
        built = job.build(*job.args)
        root, edited = (built if isinstance(built, tuple) else (built, built))
        root, edited = Path(root), Path(edited)
        base_dir = root.parent
        state = {}
        for dn in DESIGNS:
            d = _copytree(base_dir, dn)
            state[dn] = (d, _relocate(base_dir, d, root), _relocate(base_dir, d, edited))
        cache: dict = {}
        fbl_cache: dict = {}
        n = len(job.steps)
        for k, step in enumerate(job.steps, 1):
            rows = {}
            snaps = {}
            for dn, mod in DESIGNS.items():
                d, r, e = state[dn]
                N = step["N"] if step["kind"] == "wptn" else None
                ikey = tree_key(d)
                snap = _snap(cache, d, r, e, N)
                snaps[dn] = snap
                groups = step["groups"] if step["groups"] is not None else default_groups(step, e, snap)
                h0 = tree_files(d)
                before = _tree_bytes(d)
                t0 = time.perf_counter()
                st, msg = _call_step(mod, step, e)
                dt = time.perf_counter() - t0
                tb = _LAST_TB["tb"]
                h1 = tree_files(d)
                changed = sorted(f for f in set(h0) | set(h1) if h0.get(f) != h1.get(f))
                row = {"step": k, "nsteps": n, "tool": TOOL[step["kind"]], "design": dn,
                       "args": _args(step), "groups": [sorted(map(list, g)) for g in groups],
                       "input_key": ikey, "status": st, "codes": codes_of(msg),
                       "code_prefix": prefix_of(msg), "msg_head": msg[:200], "msg": msg[:4000],
                       "files_changed": changed, "seconds": round(dt, 3)}
                if st == "ERROR":
                    row["traceback"] = tb
                if st == "REFUSED":
                    row.update(_empty_judge())
                    row["bytes_ok"] = not changed
                else:
                    res = J.assess2(snap, str(r), groups, edited_path=str(e), status=st)
                    row.update(judged(res))
                    row["bytes_ok"] = (bool(changed) == (st == "OK")) if st != "ERROR" else None
                    if _is_wrong(row):
                        row["saved"] = save_wrong(f"{safe(job.variant)}_s{k}_{dn}", before, d,
                                                  {"variant": job.variant, "cases": job.cases,
                                                   "step": k, "design": dn, "args": row["args"],
                                                   "status": st, "msg": msg[:4000],
                                                   "cli_detail": row["cli_detail"],
                                                   "gui_detail": row["gui_detail"],
                                                   "root": str(r.relative_to(d)),
                                                   "edited": str(e.relative_to(d))})
                row["_groups"] = groups
                rows[dn] = row
            refused = [dn for dn, row in rows.items() if row["status"] == "REFUSED"]
            for dn in refused:
                d, r, e = state[dn]
                row = rows[dn]
                row["refusers_this_step"] = refused
                fk = (k, row["input_key"])
                if fk not in fbl_cache:
                    fbl_cache[fk] = fallback_label(d, r, e, step, snaps[dn], row["_groups"],
                                                   tag=f"{safe(job.variant)}_s{k}_{dn}")
                    fbl_cache[fk]["computed_on"] = dn
                row["fallback_label"] = fbl_cache[fk]
                if step["kind"] == "cp":
                    row["fallback_wptn"] = fallback_wptn(DESIGNS[dn], d, r, e, step, snaps[dn],
                                                         row["_groups"],
                                                         tag=f"{safe(job.variant)}_s{k}_{dn}")
            for dn in DESIGNS:
                row = rows[dn]
                row.pop("_groups", None)
                for i, case in enumerate(job.cases):
                    recs.append({**rec_base(case, i == 0), **row})
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        for i, case in enumerate(job.cases):
            recs.append({**rec_base(case, i == 0), "status": "HARNESS_ERROR", "design": None,
                         "error": tb[-3000:]})
    finally:
        H.BASE, J.TMP = hbase, jtmp
        _JOB["dir"] = None
        shutil.rmtree(jd, ignore_errors=True)
    return recs


def _args(step):
    if step["kind"] == "wptn":
        a = {"pins": step["pins"], "label_text": step["N"]}
        a.update(step["kw"])
        return a
    return {"ref1": step["a"][0], "pin1": step["a"][1], "ref2": step["b"][0], "pin2": step["b"][1]}


def _flag(r, rd):
    w = r.get(f"{rd}_wrong")
    return "W" if w else "c" if w is False else "?"


def _short(r):
    s = f"{r.get('design')}:{r.get('status')}"
    if r.get("codes"):
        s += "[" + ",".join(r["codes"]) + "]"
    if r.get("status") in ("OK", "NOOP", "ERROR"):
        # only the readings that were taken: a reading that was not taken is not shown as "?" or "N"
        s += "".join(f" {rd}={_flag(r, rd)}" for rd in readings.READINGS)
        s += " dlv=" + "/".join("Y" if r.get(f"delivered_{rd}") else "N" for rd in readings.READINGS)
        if r.get("false_noop"):
            s += " FALSE-NOOP"
        if r.get("redundant_write"):
            s += " REDUNDANT"
    if r.get("bytes_ok") is False:
        s += " BYTES!"
    for fb, ab in (("fallback_label", "fbl"), ("fallback_wptn", "fbw")):
        if r.get(fb):
            s += f" {ab}={r[fb]['outcome']}"
    if r.get("status") == "ERROR":
        s += " " + (r.get("msg_head") or "")[:120]
    if r.get("status") == "HARNESS_ERROR":
        s += " " + r.get("error", "").strip().splitlines()[-1][:200]
    return s


def main(jobs, category, script, out=None, workers=1, only=None):
    """Run jobs (optionally only those whose variant contains one of `only`), append records to
    out (default records_<category>.jsonl in OUT), print one line per step."""
    out = Path(out or OUT / f"records_{category}.jsonl")
    sel = [j for j in jobs if not only or any(o in j.variant for o in only)]
    if out.exists() and not os.environ.get("RLIB_FRESH"):
        # Follow-up 2 resume (runs were killed from outside): keep the records of every job that
        # is complete in out (len(DESIGNS) x cases x steps records, no HARNESS_ERROR), drop the
        # rest (a partial block or a harness error) and run only the jobs not kept.
        want = {j.variant: len(DESIGNS) * len(j.cases) * len(j.steps) for j in jobs}
        by: dict = {}
        for line in open(out, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            by.setdefault(r.get("variant"), []).append(r)
        keep = {v for v, rs in by.items() if v in want and len(rs) == want[v]
                and not any(r.get("status") == "HARNESS_ERROR" for r in rs)}
        with open(out, "w", encoding="utf-8") as f:
            for v in keep:
                for r in by[v]:
                    f.write(json.dumps(r, default=str) + "\n")
        dropped = sorted(v for v in by if v not in keep)
        print(f"resume: kept {len(keep)} complete jobs, dropped {len(dropped)} incomplete: {dropped}",
              flush=True)
        sel = [j for j in sel if j.variant not in keep]
    print(f"{category}: {len(sel)} jobs, {workers} workers -> {out}", flush=True)
    t0 = time.perf_counter()

    def emit(recs):
        with open(out, "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, default=str) + "\n")
        prim = [r for r in recs if r.get("primary")]
        steps = {}
        for r in prim:
            steps.setdefault(r.get("step"), []).append(_short(r))
        v = recs[0]["variant"] if recs else "?"
        for k, cells in sorted(steps.items(), key=lambda t: (t[0] is None, t[0] or 0)):
            print(f"  {v} [{','.join(recs[0]['case'] for _ in [0])}] step {k}: " + " | ".join(cells),
                  flush=True)

    if workers <= 1:
        for j in sel:
            emit(run_job(j, script, category))
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(run_job, j, script, category) for j in sel]
            for f in as_completed(futs):
                emit(f.result())
    print(f"done in {time.perf_counter() - t0:.0f}s", flush=True)
