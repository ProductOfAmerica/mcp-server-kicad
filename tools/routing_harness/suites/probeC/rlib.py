"""rlib v3: shared runner for the probeC follow-up 2 re-run (designs M2, H, Hb, E on probe
categories geometry-misc, composite, decisions, perf). Everything this writes lands under
hpaths.suite_out("probeC"), the run's probeC output directory.

Copied from follow-up 1's rerun/probeC/rlib.py (v2, designs M2 and K) and changed only where the
design set or the judge changed:
  - IMPLS = M2 (m2_impl), H (h_impl), Hb (hb_impl: h_impl with CROSS_BAN = True), E (e_impl).
    MODS gives the module object a hook must patch (Hb's is hb_impl.MODULE). K is gone, and with
    it the lifts-with attribution and the per-call flags.
  - Codes: ^((?:\\[[MKHE]:\\w+\\]\\s*)+) .
  - judge3 now takes status (named_merge, req_pad_multi, wrong_j3); the judge wrapper passes it.
    Records carry cli_wrong / gui_wrong (judge3's new verdict) and cli_wrong_j3 / gui_wrong_j3
    (follow-up 1's verdict). auto_place_decoupling_cap's two passes: a kind counts when both
    passes flag the same item (as v2), except req_pad_multi, which is the union of the passes
    (each pass requests only its own cap pad).
  - FALLBACKS run for every design that REFUSED a call some other design did not refuse, on that
    design's own pre-call state (follow-up 1 ran them for K-only and M2-only refusals; the steps
    are the same: fallback_label, and fallback_wptn for connect_pins with the refusing design's
    own wire_pins_to_net).
  - INPUT IDENTITY: every design call records in_sig (sha256 over its fixture directory's files,
    byte for byte, before the call) and in_sig_norm (the same with every UUID text replaced).
    A sequence step is a same-input comparison of two designs only when their in_sig agree.
  - WRONG WRITES: when a judged call or fallback is wrong in either reading, the pre-call and
    post-call fixture directories are copied to wrong/<id>/before and wrong/<id>/after.
  - run_sequence accepts fixture-action steps ({"action": fn, "label": ...}), as decisions'
    dhelp.run_sequence_ex did; fn(edited path in each design's copy) runs for every design.

JUDGING (judge3.py, unchanged; see v2 notes in follow-up 1's rlib). snap = snapshot2(root, edited,
N) once per fixture state; each design runs on its own fresh copy; res = assess2(...).
  wire_pins_to_net: N = label_text; groups = ONE set holding every requested pad.
  connect_pins: N = None; groups = [{pad1, pad2}]; rlib adds pre_satisfied / redundant_write.
  auto_place_decoupling_cap: two assess2 passes (power_net with cap pad 1, ground_net with pad 2).
  REFUSED (and ERROR) calls are not judged when no byte changed.
EXTRA CHECKS (v2, kept): extra = {cli, gui} with named_merges, pads2_requested, pads2_other.

HARNESS PORT (tools/routing_harness): IMPLS and MODS come from core/registry.py (the designs
hpaths.DESIGNS names, default X; MODS maps each to its MODULE when it has one), every path from
hpaths, and the readings from core/readings.py: a reading that was not taken is skipped by
_pair_pre and assess_cap (never judged clean, never a false no-op).
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
import uuid
from pathlib import Path

sys.dont_write_bytecode = True

import hpaths  # noqa: E402  (repo under test, core/, protos/ and adapters/ on sys.path)

OUT = hpaths.suite_out("probeC")
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

RLIB_VERSION = 3
_JUDGE_ORIG = J.judge
_LAST: list = []


def _judge_keep(before, after, groups, N=None, n_nodes=None, n_after=None, status=None):
    """judge3.judge, unchanged, keeping the netlists it compared."""
    _LAST.append((before, after))
    return _JUDGE_ORIG(before, after, groups, N, n_nodes=n_nodes, n_after=n_after, status=status)


J.judge = _judge_keep
_AUTO_RE = re.compile(r"(^|/)(Net|unconnected)-\(")


def _user_name(name) -> bool:
    return bool(name) and not _AUTO_RE.search(name)


def _extra_checks(entries, pads) -> dict:
    out = {}
    req = {tuple(p) for p in (pads or [])}
    for rd, ent in zip(("cli", "gui"), list(entries[:2]) + [None, None]):
        if ent is None or ent[0] is None or ent[1] is None:
            out[rd] = None
            continue
        before, after = ent
        bw, aw = J._index(before), J._index(after)
        bnet = {n: ix[0] for n, ix in bw.items()}
        named = []
        for _c, name, _k, v in after:
            src = sorted({bnet[n] for n in v if n in bnet})
            users = sorted({before[i][1] for i in src if _user_name(before[i][1])})
            if len(users) >= 2:
                named.append({"after": name, "before_names": users,
                              "distinct_base": len({J._strip(u) for u in users}) >= 2})
        multi = {n: sorted(after[j][1] for j in ix) for n, ix in aw.items() if len(ix) > 1}
        out[rd] = {
            "named_merges": named,
            "pads2_requested": [[n[0], n[1], nets, len(bw.get(n, [])) > 1]
                                for n, nets in sorted(multi.items()) if n in req],
            "pads2_other": [[n[0], n[1], nets, len(bw.get(n, [])) > 1]
                            for n, nets in sorted(multi.items()) if n not in req],
        }
    return out


import e_impl as E  # noqa: E402
import h_impl as Hd  # noqa: E402
import hb_impl as Hb  # noqa: E402
import m2_impl as M2  # noqa: E402
from mcp_server_kicad import _cst, _shared  # noqa: E402,F401
from mcp_server_kicad import schematic as SCH  # noqa: E402

assert Hb.MODULE.CROSS_BAN is True and Hd.CROSS_BAN is False
import readings  # noqa: E402
import registry  # noqa: E402
IMPLS = registry.selected()  # the designs this run measures (ROUTING_HARNESS_DESIGNS)
MODS = {k: getattr(m, "MODULE", m) for k, m in IMPLS.items()}  # the module a hook patches
DESIGNS = tuple(IMPLS)
TOOLS = ("wire_pins_to_net", "connect_pins", "auto_place_decoupling_cap")
_STATE = {"group": None, "script": None, "file": None, "keep": False, "n": 0, "tag": None}
_CODE_HEAD = re.compile(r"^((?:\[[MKHE]:\w+\]\s*)+)")
_UUID = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


# ----------------------------------------------------------------------------- bookkeeping


def begin(group: str, script_file: str, tag: str | None = None, keep: bool = False):
    tag = tag or Path(script_file).stem
    parts = OUT / ("parts" + os.environ.get("RLIB_OUT_SUFFIX", ""))
    parts.mkdir(parents=True, exist_ok=True)
    f = parts / f"{tag}.jsonl"
    f.write_text("", encoding="utf-8")
    _STATE.update(group=group, script=Path(script_file).name, file=f, keep=keep, n=0, tag=tag)
    print(f"# rlib v3 begin group={group} tag={tag} tmp={TMPDIR}", flush=True)


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
    rec.setdefault("part", _STATE["tag"])
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


def dir_sig(d) -> tuple[str, str]:
    """(raw, uuid-normalised) sha256 over every file of d (relative path + bytes)."""
    d = Path(d)
    hr, hn = hashlib.sha256(), hashlib.sha256()
    for f in sorted(d.rglob("*")):
        if not f.is_file():
            continue
        b = f.read_bytes()
        name = str(f.relative_to(d)).encode()
        hr.update(name + b"\0" + hashlib.sha256(b).digest())
        hn.update(name + b"\0" + hashlib.sha256(_UUID.sub(b"U", b)).digest())
    return hr.hexdigest()[:16], hn.hexdigest()[:16]


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


def code_prefix(msg: str) -> str:
    text = re.sub(r"^\w+: ", "", msg or "", count=1)
    m = _CODE_HEAD.match(text)
    return m.group(1).strip() if m else ""


def valid_name(n) -> bool:
    try:
        M2._check_net_name(n)
        return True
    except Exception:  # noqa: BLE001
        return False


def resolve_pads(edited, pin_list) -> list[tuple[str, str]]:
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


def refusers(recs: dict) -> list:
    """Designs that REFUSED while at least one other design in recs did not (every design that
    REFUSED when RLIB_FB_ALL is set: the 2026-10-03 fallback-coverage rerun)."""
    if os.environ.get("RLIB_FB_ALL"):
        return [a for a, r in recs.items() if r["status"] == "REFUSED"]
    return [a for a, r in recs.items() if r["status"] == "REFUSED"
            and any(recs[b]["status"] != "REFUSED" for b in recs if b != a)]


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
        _LAST.clear()
        if self.cap is not None:
            res = assess_cap(snap, root, edited, self.cap, status)
            res["extra"] = _extra_checks(_LAST, self.pads)
            return res
        res = assess2(snap, root, self.groups, edited_path=edited, status=status)
        res["extra"] = _extra_checks(_LAST, self.pads)
        if self.tool == "connect_pins":
            pre = _pair_pre(snap, *self.pads)
            res["pre_satisfied"] = pre
            res["redundant_write"] = bool(status == "OK" and changed and pre)
        elif "redundant_write" in res and not changed:
            res["redundant_write"] = False
        return res


_KINDS_J3 = ("bad_merges", "splits", "renames", "class_bad", "pads_multi", "vanished", "appeared")
_KINDS = _KINDS_J3 + ("named_merge", "req_pad_multi")


def assess_cap(snaps, root, edited, cap, status):
    rp = assess2(snaps["p"], root, [{(cap, "1")}], edited_path=edited, status=status)
    rg = assess2(snaps["g"], root, [{(cap, "2")}], edited_path=edited, status=status)
    res = {}
    for rd in ("cli", "gui"):
        a, b = rp.get(rd) or {}, rg.get(rd) or {}
        if rd not in readings.READINGS:  # not taken: judge3's unavailable entry, no delivered key
            res[rd] = dict(a)
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
        for k in _KINDS:
            la = [x for x in a.get(k, []) if not (k == "appeared" and x[0] == cap)]
            lb = [x for x in b.get(k, []) if not (k == "appeared" and x[0] == cap)]
            if k == "req_pad_multi":
                seen, u = set(), []
                for x in la + lb:
                    s = json.dumps(x, default=_jdefault)
                    if s not in seen:
                        seen.add(s)
                        u.append(x)
                out[k] = u
                continue
            sb = {json.dumps(x, default=_jdefault) for x in lb}
            out[k] = [x for x in la if json.dumps(x, default=_jdefault) in sb]
        out["wrong_j3"] = any(out[k] for k in _KINDS_J3)
        out["wrong"] = any(out[k] for k in _KINDS)
        out["delivered"] = bool(a.get("delivered")) and bool(b.get("delivered"))
        out["delivered_power"], out["delivered_ground"] = a.get("delivered"), b.get("delivered")
        res[rd] = out
    res["false_noop"] = status == "NOOP" and not all(
        (res.get(rd) or {}).get("delivered", True) for rd in readings.READINGS)
    res["pre_satisfied"] = False
    res["redundant_write"] = False
    return res


def _fill(rec, res):
    cli, gui = (res or {}).get("cli") or {}, (res or {}).get("gui") or {}
    rec.update(
        cli_wrong=cli.get("wrong"), gui_wrong=gui.get("wrong"),
        cli_wrong_j3=cli.get("wrong_j3", cli.get("wrong")),
        gui_wrong_j3=gui.get("wrong_j3", gui.get("wrong")),
        cli_detail=_nonempty(cli), gui_detail=_nonempty(gui),
        delivered_cli=cli.get("delivered"), delivered_gui=gui.get("delivered"),
        false_noop=(res or {}).get("false_noop"), pre_satisfied=(res or {}).get("pre_satisfied"),
        redundant_write=(res or {}).get("redundant_write"),
        extra=(res or {}).get("extra"),
    )


def _blank(rec):
    rec.update(cli_wrong=None, gui_wrong=None, cli_wrong_j3=None, gui_wrong_j3=None,
               cli_detail={}, gui_detail={}, delivered_cli=None, delivered_gui=None,
               false_noop=None, pre_satisfied=None, redundant_write=None)


def _safe(s) -> str:
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", str(s))[:90]


def keep_wrong(rec, pre_dir, post_dir):
    """Copy the pre-call and post-call fixture directories of a wrong write to wrong/<id>/."""
    if not (rec.get("cli_wrong") or rec.get("gui_wrong")):
        return
    wid = "__".join(_safe(x) for x in (rec.get("group"), rec.get("case"), rec.get("variant"),
                                       "s" + str(rec.get("step")), rec.get("kind"),
                                       rec.get("design")) if x is not None)
    dst = OUT / ("wrong" + os.environ.get("RLIB_OUT_SUFFIX", "")) / wid
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    if pre_dir is not None and Path(pre_dir).exists():
        shutil.copytree(pre_dir, dst / "before")
    shutil.copytree(post_dir, dst / "after")
    (dst / "record.json").write_text(json.dumps(rec, indent=1, default=_jdefault), encoding="utf-8")
    rec["wrong_kept"] = str(dst.relative_to(OUT))


def judged_call(fn, kw, spec, snap, root, edited, design_dir, rec, pre_dir=None):
    """Run fn on the copy in design_dir and judge it into rec. Returns (status, msg)."""
    h0 = tree_hashes(design_dir)
    rec["in_sig"], rec["in_sig_norm"] = dir_sig(design_dir)
    t0 = time.perf_counter()
    st, msg = call(fn, schematic_path=str(edited), **kw)
    dt = time.perf_counter() - t0
    h1 = tree_hashes(design_dir)
    changed = sorted(k for k in set(h0) | set(h1) if h0.get(k) != h1.get(k))
    rec.update(status=st, codes=codes_of(msg), code_prefix=code_prefix(msg), msg_head=msg[:200],
               msg=msg[:3000], seconds=round(dt, 3), files_changed=changed,
               bytes_unchanged=not changed)
    if st == "ERROR":
        rec["traceback_note"] = msg[:600]
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
    rec.setdefault("group", _STATE["group"])
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


# ----------------------------------------------------------------------------- fallbacks


def _fresh_name():
    return "FBK_" + uuid.uuid4().hex[:6].upper()


def _label_pins(edited, pad_list, name):
    msgs = []
    for ref, num in pad_list:
        try:
            pos = M2.pin_positions(str(edited), ref).get(num) or []
        except Exception as e:  # noqa: BLE001
            return "ERROR", f"pin_positions failed for {ref}:{num}: {e!r}"
        if not pos:
            return "ERROR", f"no drawn copy for {ref}:{num}"
        for x, y, _o in pos:
            st, msg = call(SCH.add_label, text=name, x=x, y=y, schematic_path=str(edited))
            msgs.append(msg)
            if st != "OK":
                return st, msg
    return "OK", "; ".join(msgs)


def fallbacks(case, variant, tool, kw, spec, pre_dir, root, edited, refusing, also=(), note="",
              step=None, kwmap=None):
    """The follow-up 1 fallbacks for a call `refusing` refused on the state in pre_dir.
    kwmap(kw, base_dir, new_dir): optional remap of kw paths into the fallback copy."""
    base_dir = Path(pre_dir)
    common = dict(case=case, variant=variant, design=refusing, also=list(also), step=step,
                  fallback_of=tool, note=note)
    jobs = []
    upads = list(dict.fromkeys(spec.pads))  # a pad listed twice gets one label per copy
    if tool == "wire_pins_to_net":
        jobs.append(("fallback_label", spec.N, upads, None))
    elif tool == "connect_pins":
        z = _fresh_name()
        jobs.append(("fallback_label", z, upads, None))
        jobs.append(("fallback_wptn", z, upads,
                     dict(pins=pins((kw["ref1"], kw["pin1"]), (kw["ref2"], kw["pin2"])),
                          label_text=z)))
    elif tool == "auto_place_decoupling_cap":
        jobs.append(("fallback_label", None, spec.pads, None))
    snaps = {}
    for kind, name, pad_list, wkw in jobs:
        d = copy_tree(base_dir, f"{base_dir.name}__fb_{kind}_{refusing}")
        r2, e2 = _rel(root, base_dir, d), _rel(edited, base_dir, d)
        rec = dict(common, tool=kind, kind=kind, N=name, pads=pad_list)
        rec["in_sig"], rec["in_sig_norm"] = dir_sig(d)
        if name is not None and not valid_name(name):
            rec.update(status="SKIPPED", codes=[], code_prefix="", msg_head=f"invalid N {name!r}",
                       msg="")
            _blank(rec)
            emit(rec)
            _line(rec)
            shutil.rmtree(d, ignore_errors=True)
            continue
        if tool == "auto_place_decoupling_cap":
            fspec, key = spec, "cap"
        else:
            fspec = Spec("wire_pins_to_net", dict(pins=pins(*pad_list), label_text=name), e2)
            fspec.groups = [set(pad_list)]
            key = name
        if key not in snaps:
            snaps[key] = fspec.snapshot(r2, e2)
        snap = snaps[key]
        h0 = tree_hashes(d)
        t0 = time.perf_counter()
        if kind == "fallback_wptn":
            st, msg = call(IMPLS[refusing].wire_pins_to_net, schematic_path=str(e2), **wkw)
        elif tool == "auto_place_decoupling_cap":
            k2 = kwmap(kw, base_dir, d) if kwmap else kw
            pk = {k: k2[k] for k in ("lib_id", "reference", "value", "x", "y")}
            pk["rotation"] = k2.get("rotation", 0)
            for k in ("symbol_lib_path", "project_path"):
                if k2.get(k):
                    pk[k] = k2[k]
            st, msg = call(SCH.place_component, schematic_path=str(e2), **pk)
            if st == "OK":
                st, m1 = _label_pins(e2, [(spec.cap, "1")], spec.power)
                msg += " | " + m1
                if st == "OK":
                    st, m2 = _label_pins(e2, [(spec.cap, "2")], spec.ground)
                    msg += " | " + m2
        else:
            st, msg = _label_pins(e2, pad_list, name)
        dt = time.perf_counter() - t0
        h1 = tree_hashes(d)
        changed = sorted(k for k in set(h0) | set(h1) if h0.get(k) != h1.get(k))
        rec.update(status=st, codes=codes_of(msg), code_prefix=code_prefix(msg),
                   msg_head=msg[:200], msg=msg[:3000], seconds=round(dt, 3),
                   files_changed=changed, bytes_unchanged=not changed)
        if st in ("REFUSED", "ERROR") and not changed:
            _blank(rec)
        else:
            try:
                res = fspec.assess(snap, r2, e2, "OK" if st == "OK" else st, bool(changed))
            except Exception as e:  # noqa: BLE001
                res = {"cli": {"wrong": None, "error": repr(e)}, "gui": {"wrong": None, "error": repr(e)}}
            _fill(rec, res)
        rec.setdefault("group", _STATE["group"])
        keep_wrong(rec, base_dir, d)
        wipe_judge_scratch()
        emit(rec)
        _line(rec)
        shutil.rmtree(d, ignore_errors=True)


# ----------------------------------------------------------------------------- the runners


def run_case(case, variant, tool, edited, kw, *, root=None, script=None, also=(), note="",
             designs=DESIGNS, fallback=True, kwmap=None, check_base=False):
    """One call on byte-identical copies of one fixture, per design; judged and recorded.
    kwmap(kw, base_dir, design_dir): remap kw paths into each copy (composite's project_path).
    check_base: record base_mutated if the base fixture changes during a design call."""
    edited, root, base_dir = _layout(edited, root)
    spec = Spec(tool, kw, edited)
    h_base = tree_hashes(base_dir)
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
        kw2 = kwmap(kw, base_dir, d) if kwmap else kw
        rec = dict(case=case, variant=variant, tool=tool, design=dz, kind="main",
                   also=list(also), N=spec.N if spec.cap is None else [spec.power, spec.ground],
                   pads=spec.pads, note=note, kw=_kwtext(kw))
        if script:
            rec["script"] = script
        judged_call(getattr(IMPLS[dz], tool), kw2, spec, snap, r2, e2, d, rec, pre_dir=base_dir)
        if check_base and tree_hashes(base_dir) != h_base:
            rec["base_mutated"] = True
            print("  !! the call modified the BASE fixture", flush=True)
        emit(rec)
        _line(rec)
        recs[dz] = rec
        shutil.rmtree(d, ignore_errors=True)
    if fallback:
        for a in refusers(recs):
            fallbacks(case, variant, tool, kw, spec, base_dir, root, edited, a, also=also,
                      note=note, kwmap=kwmap)
    return recs


def run_sequence(case, variant, steps, edited, *, root=None, script=None, also=(), note="",
                 designs=DESIGNS, fallback=True, kwmap=None, check_base=False):
    """A multi-call sequence per design on its own evolving copy, in lockstep. Steps are
    {tool, kw, edited?, label?} or fixture actions {action: fn, edited?, label?}. Each call is
    judged against the state just before it; fallbacks run on the refusing design's pre-step
    state. Returns {design: [records]}."""
    edited, root, base_dir = _layout(edited, root)
    dirs = {dz: copy_tree(base_dir, f"{base_dir.name}__seq_{dz}") for dz in designs}
    out = {dz: [] for dz in designs}
    h_base = tree_hashes(base_dir)
    print(f"== {case} | {variant} | sequence of {len(steps)} steps" + (f" | {note}" if note else ""),
          flush=True)
    for i, stp in enumerate(steps):
        if "action" in stp:
            for dz in designs:
                ed = _rel(Path(stp.get("edited") or edited).resolve(), base_dir, dirs[dz])
                res = stp["action"](str(ed))
                print(f"  [action#{i}] {dz}: {stp.get('label', '')} -> {str(res)[:150]!r}",
                      flush=True)
            continue
        tool, kw = stp["tool"], stp["kw"]
        ed_base = Path(stp.get("edited") or edited).resolve()
        recs, pres = {}, {}
        for dz in designs:
            d = dirs[dz]
            r2, e2 = _rel(root, base_dir, d), _rel(ed_base, base_dir, d)
            kw2 = kwmap(kw, base_dir, d) if kwmap else kw
            spec = Spec(tool, kw2, e2)
            pre = copy_tree(d, f"{d.name}__pre")
            pres[dz] = (pre, spec)
            snap = spec.snapshot(r2, e2)
            wipe_judge_scratch()
            rec = dict(case=case, variant=variant, tool=tool, design=dz, kind="seq", step=i,
                       step_label=stp.get("label", ""), also=list(also),
                       N=spec.N if spec.cap is None else [spec.power, spec.ground],
                       pads=spec.pads, note=note, kw=_kwtext(kw))
            if script:
                rec["script"] = script
            judged_call(getattr(IMPLS[dz], tool), kw2, spec, snap, r2, e2, d, rec, pre_dir=pre)
            if check_base and tree_hashes(base_dir) != h_base:
                rec["base_mutated"] = True
                print("  !! the call modified the BASE fixture", flush=True)
            emit(rec)
            _line(rec)
            recs[dz] = rec
            out[dz].append(rec)
        if fallback:
            for a in refusers(recs):
                pre, spec = pres[a]
                r2, e2 = _rel(root, base_dir, pre), _rel(ed_base, base_dir, pre)
                kwa = kwmap(kw, base_dir, pre) if kwmap else kw
                fallbacks(case, variant, tool, kwa, spec, pre, r2, e2, a, also=also,
                          note=note, step=i, kwmap=kwmap)
        for pre, _s in pres.values():
            shutil.rmtree(pre, ignore_errors=True)
    for d in dirs.values():
        shutil.rmtree(d, ignore_errors=True)
    return out


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
