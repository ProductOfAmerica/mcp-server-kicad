"""rlib (follow-up 2, llm-workflow batch): shared runner for designs M2, H, Hb and E.

Copied from pr57pt/coarse/rerun/probeC/rlib.py (follow-up 1) and changed only where follow-up 2 needs it:
  - everything lands under the run's llm-workflow directory (OUT = hpaths.suite_out("llm-workflow"));
  - designs M2 (m2_impl), H (h_impl), Hb (hb_impl: h_impl with CROSS_BAN True), E (e_impl);
    K (coarse_impl) is gone, with its lifts-with parsing and attribution flags;
  - judge3 is called as it is now (it takes status, and itself reports named_merge,
    req_pad_multi and wrong_j3), so follow-up 1's judge wrapper and "extra" checks are deleted;
  - the repo-tool fallbacks (fallback_label / fallback_wptn) are deleted: the follow-up 2 brief
    reports only the designs' own calls;
  - each record carries the uuid-blanked digest of the pre-call state (input_key) so that "same
    input" across designs can be tested exactly, and the pre and post files of every wrong write
    are kept under OUT/wrong/.

JUDGING (judge3.py, unchanged). snap = snapshot2(root, edited, N); res = assess2(snap, root,
groups, edited_path=edited, status=st).
  wire_pins_to_net: N = label_text; groups = ONE set holding every requested pad (ref, number)
      (judge3 now takes the union itself, so this gives the same verdicts as per-pad sets).
  connect_pins: N = None; groups = [{(ref1, num1), (ref2, num2)}]; rlib adds pre_satisfied (both
      pads in one before-net in cli AND gui) and redundant_write = OK and a file changed and
      pre_satisfied.
  auto_place_decoupling_cap: two assess2 passes, (N=power_net, {(cap, "1")}) and
      (N=ground_net, {(cap, "2")}); the cap's own pads are removed from "appeared"; each judge3
      kind of follow-up 1 is kept only when BOTH passes flag the same item (wrong_j3, as
      follow-up 1); named_merge likewise (it does not depend on the groups, so both passes see the
      same list); req_pad_multi is the union of the two passes (each pass requests one cap pad).
      wrong = wrong_j3 or named_merge or req_pad_multi. delivered = both passes.
  REFUSED (and ERROR) calls with no file changed are not judged (bytes_unchanged).

READINGS (this repository). The gui reading is not available here (core/gui_reading.py), so the gui
entry of an assessment is judge3's marker dict (wrong None, unavailable True, no "delivered" key).
A reading that was not taken is skipped, never read as clean: pre_satisfied of connect_pins is
computed over readings.READINGS, and the cap's two passes carry the marker through.
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
from pathlib import Path

import hpaths  # noqa: E402  (an entry script has already put it on sys.path)
import readings  # noqa: E402

sys.dont_write_bytecode = True

OUT = hpaths.suite_out("llm-workflow")
_TAG = Path(sys.argv[0]).stem if sys.argv and sys.argv[0] else "interactive"
TMPDIR = OUT / "tmp" / f"{_TAG}_{os.getpid()}"
TMPDIR.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(TMPDIR)
tempfile.tempdir = str(TMPDIR)

import harness as H  # noqa: E402

H.BASE = TMPDIR / "fx"
H.BASE.mkdir(parents=True, exist_ok=True)

import judge3 as J  # noqa: E402
from judge3 import assess2, call, snapshot2  # noqa: E402,F401

RLIB_VERSION = "fu2"

import e_impl as EI  # noqa: E402
import h_impl as HI  # noqa: E402
import hb_impl as HB  # noqa: E402
import m2_impl as M2  # noqa: E402
from mcp_server_kicad import schematic as SCH  # noqa: E402

assert HI.CROSS_BAN is False and HB.MODULE.CROSS_BAN is True and HB.MODULE is not HI
import registry  # noqa: E402

IMPLS = registry.selected()  # the designs this run judges: ROUTING_HARNESS_DESIGNS (default X)
DESIGNS = tuple(IMPLS)
TOOLS = ("wire_pins_to_net", "connect_pins", "auto_place_decoupling_cap")
_STATE = {"group": None, "script": None, "file": None, "keep": False, "n": 0, "wrong": 0}
_CODE_HEAD = re.compile(r"^((?:\[[MKHE]:\w+\]\s*)+)")
WRONG_DIR = OUT / "wrong"


# ----------------------------------------------------------------------------- bookkeeping


def begin(group: str, script_file: str, tag: str | None = None, keep: bool = False):
    """Start a script: records go to parts/<tag>.jsonl (truncated)."""
    tag = tag or Path(script_file).stem
    (OUT / "parts").mkdir(parents=True, exist_ok=True)
    f = OUT / "parts" / f"{tag}.jsonl"
    f.write_text("", encoding="utf-8")
    _STATE.update(group=group, script=Path(script_file).name, file=f, keep=keep, n=0, tag=tag)
    print(f"# rlib begin group={group} tag={tag} tmp={TMPDIR}", flush=True)


def end():
    print(f"# rlib end: {_STATE['n']} records -> {_STATE['file']}", flush=True)


@atexit.register
def _cleanup():
    if not _STATE.get("keep"):
        shutil.rmtree(TMPDIR, ignore_errors=True)


def emit(rec: dict):
    rec.setdefault("rlib", RLIB_VERSION)
    rec.setdefault("sample", "probe")
    rec.setdefault("group", _STATE["group"])
    rec.setdefault("script", _STATE["script"])
    rec.setdefault("part", _STATE.get("tag"))
    with open(_STATE["file"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=_jdefault) + "\n")
    _STATE["n"] += 1


def _jdefault(o):
    if isinstance(o, (set, frozenset, tuple)):
        return sorted(o, key=str) if isinstance(o, (set, frozenset)) else list(o)
    if isinstance(o, Path):
        return str(o)
    return repr(o)


def wipe_judge_scratch():
    for ch in Path(J.TMP).iterdir():
        if ch.is_dir():
            shutil.rmtree(ch, ignore_errors=True)
        else:
            try:
                ch.unlink()
            except OSError:
                pass


# ----------------------------------------------------------------------------- small helpers


def pins(*pairs):
    return [{"reference": r, "pin": str(n)} for r, n in pairs]


def tree_hashes(d) -> dict:
    d = Path(d)
    return {str(f.relative_to(d)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(d.rglob("*")) if f.is_file()}


_UUID_RE = re.compile(rb'\(uuid "[^"]*"\)')


def norm_state(d) -> str:
    """Digest of every file under d with uuid values blanked (two states equal under it differ
    only in uuids, which no reading of connectivity depends on; ctlib._norm's normalisation)."""
    h = hashlib.sha256()
    for f in sorted(Path(d).rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(d)).encode())
            h.update(_UUID_RE.sub(b'(uuid "")', f.read_bytes()))
    return h.hexdigest()


def copy_tree(src_dir, name: str) -> Path:
    src_dir = Path(src_dir)
    dst = src_dir.parent / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src_dir, dst)
    return dst


def codes_of(msg: str) -> list[str]:
    text = re.sub(r"^\w+: ", "", msg or "", count=1)
    m = _CODE_HEAD.match(text)
    return re.findall(r"\[[MKHE]:(\w+)\]", m.group(1)) if m else []


def valid_name(n) -> bool:
    try:
        M2._check_net_name(n)
        return True
    except Exception:  # noqa: BLE001
        return False


def resolve_pads(edited, pin_list) -> list[tuple[str, str]]:
    """(ref, pad number) per requested pin via m2_impl's resolver (raw text if it refuses)."""
    out = []
    try:
        _, root, *_ = SCH._open_sch_cst(str(edited))
        m = M2._Model(root)
    except Exception:  # noqa: BLE001
        m = None
    for pd in pin_list:
        if not isinstance(pd, dict):
            continue
        ref, ptxt = pd.get("reference"), str(pd.get("pin"))
        num = ptxt
        if m is not None:
            try:
                num, _copies = m.resolve(ref, ptxt)
            except Exception:  # noqa: BLE001
                num = ptxt
        out.append((ref, num))
    return out


def _nonempty(d):
    return {k: v for k, v in (d or {}).items() if v and k != "delivered_each"}


def _pair_pre(snap, a, b):
    ok = []
    for rd in readings.READINGS:  # a reading that was not taken is skipped
        nets = snap.get(rd)
        if nets is None:
            return None
        na = {i for i, (_c, _n, _k, v) in enumerate(nets) if a in v}
        nb = {i for i, (_c, _n, _k, v) in enumerate(nets) if b in v}
        ok.append(bool(na & nb))
    return all(ok)


# ----------------------------------------------------------------------------- judging


def _layout(edited, root):
    edited = Path(edited).resolve()
    root = Path(root).resolve() if root else edited
    base_dir = root.parent
    edited.relative_to(base_dir)
    return edited, root, base_dir


def _rel(path, base_dir, new_dir):
    return Path(new_dir) / Path(path).resolve().relative_to(Path(base_dir).resolve())


class Spec:
    """What a call asks for, for judging: N, groups, and the cap's two passes."""

    def __init__(self, tool, kw, edited):
        self.tool, self.kw = tool, kw
        self.cap = None
        if tool == "wire_pins_to_net":
            self.N = kw.get("label_text")
            pl = kw.get("pins") or []
            self.pads = list(dict.fromkeys(resolve_pads(edited, pl if isinstance(pl, list) else [])))
            self.groups = [set(self.pads)]
            if not valid_name(self.N):
                self.N = None
        elif tool == "connect_pins":
            self.N = None
            a = resolve_pads(edited, pins((kw["ref1"], kw["pin1"])))[0]
            b = resolve_pads(edited, pins((kw["ref2"], kw["pin2"])))[0]
            self.pads = [a, b]
            self.groups = [{a, b}]
        elif tool == "auto_place_decoupling_cap":
            ref = kw["reference"]
            self.cap = ref
            self.pads = [(ref, "1"), (ref, "2")]
            self.N = None
            self.power, self.ground = kw["power_net"], kw["ground_net"]
            self.groups = None
        else:
            raise ValueError(tool)

    def snapshot(self, root, edited):
        if self.cap is not None:
            sp = snapshot2(root, edited, N=self.power if valid_name(self.power) else None)
            sg = snapshot2(root, edited, N=self.ground if valid_name(self.ground) else None)
            return {"p": sp, "g": sg}
        return snapshot2(root, edited, N=self.N)

    def assess(self, snap, root, edited, status, changed):
        if self.cap is not None:
            return assess_cap(snap, root, edited, self.cap, status)
        res = assess2(snap, root, self.groups, edited_path=edited, status=status)
        if self.tool == "connect_pins":
            pre = _pair_pre(snap, *self.pads)
            res["pre_satisfied"] = pre
            res["redundant_write"] = bool(status == "OK" and changed and pre)
        elif "redundant_write" in res and not changed:
            res["redundant_write"] = False
        return res


_KINDS = ("bad_merges", "splits", "renames", "class_bad", "pads_multi", "vanished", "appeared")


def assess_cap(snaps, root, edited, cap, status):
    rp = assess2(snaps["p"], root, [{(cap, "1")}], edited_path=edited, status=status)
    rg = assess2(snaps["g"], root, [{(cap, "2")}], edited_path=edited, status=status)
    res = {}
    for rd in ("cli", "gui"):
        a, b = rp.get(rd) or {}, rg.get(rd) or {}
        if a.get("unavailable") or b.get("unavailable"):
            # the reading was not taken: carry judge3's marker through (no verdict, no "delivered" key)
            res[rd] = a if a.get("unavailable") else b
            continue
        if a.get("wrong") is None or b.get("wrong") is None:
            res[rd] = {"wrong": None, "wrong_j3": None,
                       "note": a.get("note") or b.get("note") or a.get("error") or b.get("error"),
                       "unloadable": a.get("unloadable") or b.get("unloadable"),
                       "delivered": bool(a.get("delivered")) and bool(b.get("delivered"))}
            if a.get("unloadable") or b.get("unloadable"):
                res[rd]["wrong"] = res[rd]["wrong_j3"] = True
            continue
        out = {}
        for k in _KINDS + ("named_merge",):
            la = [x for x in a.get(k, []) if not (k == "appeared" and x[0] == cap)]
            lb = [x for x in b.get(k, []) if not (k == "appeared" and x[0] == cap)]
            sb = {json.dumps(x, default=_jdefault) for x in lb}
            out[k] = [x for x in la if json.dumps(x, default=_jdefault) in sb]
        out["req_pad_multi"] = sorted(set(map(tuple, a.get("req_pad_multi", [])))
                                      | set(map(tuple, b.get("req_pad_multi", []))))
        out["wrong_j3"] = any(out[k] for k in _KINDS)
        out["wrong"] = bool(out["wrong_j3"] or out["named_merge"] or out["req_pad_multi"])
        out["delivered"] = bool(a.get("delivered")) and bool(b.get("delivered"))
        out["delivered_power"], out["delivered_ground"] = a.get("delivered"), b.get("delivered")
        res[rd] = out
    res["false_noop"] = status == "NOOP" and not all(
        (res.get(rd) or {}).get("delivered", True) for rd in ("cli", "gui"))
    res["pre_satisfied"] = False
    res["redundant_write"] = False
    return res


def _fill(rec, res):
    cli, gui = (res or {}).get("cli") or {}, (res or {}).get("gui") or {}
    rec.update(
        cli_wrong=cli.get("wrong"), gui_wrong=gui.get("wrong"),
        cli_wrong_j3=cli.get("wrong_j3"), gui_wrong_j3=gui.get("wrong_j3"),
        cli_detail=_nonempty(cli), gui_detail=_nonempty(gui),
        delivered_cli=cli.get("delivered"), delivered_gui=gui.get("delivered"),
        false_noop=(res or {}).get("false_noop"), pre_satisfied=(res or {}).get("pre_satisfied"),
        redundant_write=(res or {}).get("redundant_write"),
    )


def _blank(rec):
    rec.update(cli_wrong=None, gui_wrong=None, cli_wrong_j3=None, gui_wrong_j3=None, cli_detail={},
               gui_detail={}, delivered_cli=None, delivered_gui=None, false_noop=None,
               pre_satisfied=None, redundant_write=None)


def _slug(s, n=60):
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", s)[:n]


def keep_wrong(rec, pre_dir, post_dir):
    """Copy the pre-call and post-call fixture of a wrong write under OUT/wrong/."""
    _STATE["wrong"] += 1
    name = (f"{_STATE.get('tag')}_{_STATE['wrong']:03d}_{rec['design']}_{rec['case']}_"
            f"{_slug(rec['variant'])}")
    dst = WRONG_DIR / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(pre_dir, dst / "pre")
    shutil.copytree(post_dir, dst / "post")
    rec["wrong_dir"] = str(dst)


def judged_call(fn, kw, spec, snap, root, edited, design_dir, rec, pre_dir=None):
    """Run fn on the copy in design_dir and judge it into rec. Returns (status, msg)."""
    h0 = tree_hashes(design_dir)
    t0 = time.perf_counter()
    st, msg = call(fn, schematic_path=str(edited), **kw)
    dt = time.perf_counter() - t0
    h1 = tree_hashes(design_dir)
    changed = sorted(k for k in set(h0) | set(h1) if h0.get(k) != h1.get(k))
    rec.update(status=st, codes=codes_of(msg), msg_head=msg[:200], msg=msg[:3000],
               seconds=round(dt, 3), files_changed=changed, bytes_unchanged=not changed)
    if st in ("REFUSED", "ERROR") and not changed:
        _blank(rec)
    else:
        try:
            res = spec.assess(snap, root, edited, st, bool(changed))
        except Exception as e:  # noqa: BLE001
            res = {"cli": {"wrong": None, "error": repr(e)}, "gui": {"wrong": None, "error": repr(e)}}
        _fill(rec, res)
        if st in ("REFUSED", "ERROR"):
            rec["note"] = (rec.get("note", "") + " REFUSED/ERROR BUT FILES CHANGED").strip()
        if (rec.get("cli_wrong") or rec.get("gui_wrong")) and pre_dir is not None:
            keep_wrong(rec, pre_dir, design_dir)
    wipe_judge_scratch()
    return st, msg


def _line(rec):
    def yn(v):
        return "?" if v is None else ("Y" if v else "N")

    if rec["status"] in ("OK", "NOOP") or not rec.get("bytes_unchanged", True):
        tail = (f" cli={'WRONG' if rec['cli_wrong'] else 'clean' if rec['cli_wrong'] is False else 'n/a'}"
                f" gui={'WRONG' if rec['gui_wrong'] else 'clean' if rec['gui_wrong'] is False else 'n/a'}"
                f" dlv={yn(rec['delivered_cli'])}/{yn(rec['delivered_gui'])}"
                f"{' FALSE-NOOP' if rec.get('false_noop') else ''}"
                f"{' REDUNDANT' if rec.get('redundant_write') else ''}")
    else:
        tail = f" bytes_unchanged={rec.get('bytes_unchanged')}"
    head = f"[{rec.get('kind')}{'#' + str(rec['step']) if rec.get('step') is not None else ''}]"
    print(f"  {head} {rec['design']:>2} {rec['tool']}: {rec['status']} {' '.join(rec['codes'])}"
          f"{tail} | {rec['msg_head'][:150]!r}", flush=True)
    for side in ("cli", "gui"):
        if rec.get(f"{side}_wrong"):
            print(f"      {side} detail: {rec[f'{side}_detail']}", flush=True)


# ----------------------------------------------------------------------------- the runner


def run_case(case, variant, tool, edited, kw, *, root=None, script=None, also=(), note="",
             designs=DESIGNS, attempt="first"):
    """One call on byte-identical copies of one fixture, per design; judged and recorded."""
    edited, root, base_dir = _layout(edited, root)
    spec = Spec(tool, kw, edited)
    h_base = tree_hashes(base_dir)
    key = norm_state(base_dir)
    snap = spec.snapshot(root, edited)
    if tree_hashes(base_dir) != h_base:
        print("  !! snapshot changed the base fixture", flush=True)
    wipe_judge_scratch()
    print(f"== {case} | {variant} | {tool}({_kwtext(kw)})" + (f" | {note}" if note else ""),
          flush=True)
    recs = {}
    for dz in designs:
        d = copy_tree(base_dir, f"{base_dir.name}__{dz}")
        r2, e2 = _rel(root, base_dir, d), _rel(edited, base_dir, d)
        rec = dict(case=case, variant=variant, tool=tool, design=dz, kind="main",
                   also=list(also), N=spec.N if spec.cap is None else [spec.power, spec.ground],
                   pads=spec.pads, note=note, kw=_kwtext(kw), fixture=base_dir.name,
                   input_key=key, attempt=attempt)
        if script:
            rec["script"] = script
        judged_call(getattr(IMPLS[dz], tool), kw, spec, snap, r2, e2, d, rec, pre_dir=base_dir)
        emit(rec)
        _line(rec)
        recs[dz] = rec
        shutil.rmtree(d, ignore_errors=True)
    return recs


def _kwtext(kw):
    parts = []
    for k, v in kw.items():
        if k == "pins" and isinstance(v, list):
            v = "[" + ", ".join(f"{p.get('reference')}:{p.get('pin')}" if isinstance(p, dict)
                                else repr(p) for p in v) + "]"
            parts.append(f"pins={v}")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)
