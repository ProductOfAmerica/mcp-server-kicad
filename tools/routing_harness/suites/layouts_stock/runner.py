"""Follow-up 2: designs M2, H, Hb and E on identical inputs over four samples, every call judged by
judge3 (follow-up 2 judge3: named_merge and req_pad_multi added, wrong_j3 = follow-up 1 verdict).

Copied from pr57pt/coarse/rerun/layouts_stock/runner.py (follow-up 1, M2 vs K) and generalised to
four designs.
What changed and what did not:
  - Designs: M2 = m2_impl, H = h_impl, Hb = hb_impl (h_impl with CROSS_BAN = True), E = e_impl.
  - Fixtures: every base file is built ONCE (FIXTURES/layouts_stock/bases, by build_bases) and
    copied byte-identically
    into each design's own directory. Follow-up 1 built one base per (layout, policy) task; here
    one base per layout serves both policies and all four designs.
  - Policies are follow-up 1's, unchanged, and applied identically to every design:
      skill     connect_pins refused -> wire_pins_to_net for the pair under the plan's net name;
                wire_pins_to_net refused -> continue.
      adaptive  skill plus: ambiguous pin name ('matches several pads (a, b)', the same text in
                all four designs) -> retry with those pad numbers; drop the pins the refusal
                names and retry the rest; each dropped pin refused for GEOMETRY retried alone
                with stub_length 5.08. Geometry = the pin's refused line carries the design's
                geometry-failure code: [M:touch] / [H:touch] (M2, H, Hb) or [E:blocked] (E). The
                trigger set is the one set GEOM_CODES = {touch, blocked} for every design; no
                design emits the other's code.
  - Follow-up 1's per-discordance experiments (xlift_*, xfb_label, xfb_wptn) are not run: the
    designs here have no lift switches, and the brief asks for the refusal sets, not fallbacks.
  - Same-input comparison is done in summary_build.py from the records (pre_norm = sha256 of the
    pre-call bytes with every UUID replaced), not in the task.
  - Every wrong write (cli or gui) keeps its pre and post bytes under wrong/<id>/.
  - Generated finals carry an attribution of every planned pin left off its plan net: the refused
    lines naming it (codes) and the whole-call codes of every refused call that requested it.

Samples
  generated  36 layouts of src/gen_layouts.layouts(6), seed 20261001, x {skill, adaptive} x 4
             designs; each design runs the layout's call sequence on its own evolving copy.
  stock      wire_pins_to_net on each of the 112 pins of pinsets.json (label 'VP_STOCK'),
             connect_pins on its 21 pairs; one call per item per design on a fresh copy of the
             stock fixture <sheet>.base.kicad_sch (FIXTURES/layouts_stock, made by prepare.py).
  star       20 placements: connect_pins(U1:PA1, C1:1); if OK, connect_pins(R1:1, U1:PA1); if that
             is refused, wire_pins_to_net([R1:1, U1:PA1], 'ADC').
  jct        11 junction fixtures (J1 x2, J2 x3, J2c x2, J2b x2, J6 x2), one call each.

Judging: snapshot2 before every call; assess2 after it unless the call was REFUSED and left the
bytes unchanged. wire_pins_to_net judged with N = label_text and one group per requested pad;
connect_pins with N = None and one group holding both pads.

usage: runner.py [--only gen,stock,star,jct] [--limit N] [--tag T] [--workers W] [--resume]
       --only none builds the base files (FIXTURES/layouts_stock/bases) and stops: no call is made
       and no record file is touched. check_bases.py then compares them with follow-up 1's.
Outputs (records, finals, errors, work, wrong, tmp) go to the run's layouts_stock output directory.
Run: uv run python tools/routing_harness/suites/layouts_stock/runner.py --workers 3 --resume
"""

from __future__ import annotations

import hashlib
import itertools
import json
import multiprocessing
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

HERE = Path(__file__).resolve().parent  # source directory (read only): pinsets.json
OUT = hpaths.suite_out("layouts_stock")  # every output of this suite
SCRIPT = str(Path(__file__).resolve())


def _setup_env():
    d = OUT / "tmp" / f"w{os.getpid()}"
    d.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = os.environ["TEMP"] = str(d)
    tempfile.tempdir = str(d)
    sys.dont_write_bytecode = True


_setup_env()

import harness  # noqa: E402,F401  (BASE under our TMP)
import judge3 as J  # noqa: E402  (asserts that mcp_server_kicad is the target under test)
from harness import _cst  # noqa: E402

import e_impl as E  # noqa: E402
import h_impl as H  # noqa: E402
import hb_impl as HB  # noqa: E402
import m2_impl as M2  # noqa: E402
import mine_impl as MI  # noqa: E402  (pin_numbers, as llmlib did)
import readings  # noqa: E402
import registry  # noqa: E402
from mcp_server_kicad import project as PROJ  # noqa: E402
from mcp_server_kicad import schematic as SCH  # noqa: E402

assert HB.MODULE.CROSS_BAN is True and H.CROSS_BAN is False and HB.MODULE is not H
DES = registry.selected()  # follow-up 3 ran one design; the registry picks it by name
DESIGNS = tuple(DES)
FN = {"wire": "wire_pins_to_net", "connect": "connect_pins"}
GEOM_CODES = {"touch", "blocked"}
WORK = OUT / "work"
FIXT = hpaths.FIXTURES / "layouts_stock"  # the stock fixtures, made by prepare.py
BASES = FIXT / "bases"
WRONG = OUT / "wrong"
UUID_RE = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
CODE_HEAD = re.compile(r"^((?:\[[MKHE]:\w+\]\s*)+)")


# ------------------------------------------------------------------------------------ helpers


def norm_hash(b: bytes) -> str:
    return hashlib.sha256(UUID_RE.sub(b"U", b)).hexdigest()[:20]


def rmtree(p):
    for _ in range(5):
        try:
            shutil.rmtree(p)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.2)
    shutil.rmtree(p, ignore_errors=True)


def clean_j3():
    for ch in Path(J.TMP).iterdir():
        if ch.is_dir():
            rmtree(ch)
        else:
            try:
                ch.unlink()
            except OSError:
                pass


def pin_model(path):
    return MI._Model(_cst.parse(Path(path).read_bytes()).lists[0])


def pin_numbers(path, ref, ptxt, m=None):
    """llmlib.pin_numbers: the exact number if some pin has it, else every pin number whose
    primary name matches (all units). KiCad's lib_name resolution via mine_impl's model."""
    m = m if m is not None else pin_model(path)
    nums, byname = set(), set()
    for s in (s for s in m.syms if s.ref == ref and s.lib is not None):
        for sub in s.lib.find_all("symbol"):
            for pn in sub.find_all("pin"):
                num = pn.find("number").atoms[1].text
                nums.add(num)
                if pn.find("name").atoms[1].text == ptxt:
                    byname.add(num)
    if ptxt in nums:
        return [ptxt]
    return sorted(byname)


def group_of(path, pins):
    """ONE set of every requested pad's (ref, number) node: connect_pins' group."""
    g = set()
    m = pin_model(path)
    for r, x in pins:
        g |= {(r, n) for n in pin_numbers(path, r, x, m)}
    return [g]


def pad_groups(path, pins):
    """wire_pins_to_net: one set per requested pad holding that pad's (ref, number) node."""
    m = pin_model(path)
    out = []
    for r, x in pins:
        for n in pin_numbers(path, r, x, m):
            if {(r, n)} not in out:
                out.append({(r, n)})
    return out or [set()]


def groups_for(kind, path, pins):
    return pad_groups(path, pins) if kind == "wire" else group_of(path, pins)


def strip_cls(msg: str) -> str:
    for pre in ("ToolError: ", "_Refuse: "):
        if msg.startswith(pre):
            return msg[len(pre):]
    return msg


def code_prefix(text: str) -> str:
    m = CODE_HEAD.match(text)
    return m.group(1).strip() if m else ""


def parse_codes(text: str) -> list:
    m = CODE_HEAD.match(text)
    return re.findall(r"\[[MKHE]:(\w+)\]", m.group(1)) if m else []


def refused_lines(text: str) -> list:
    """[(tag, reason, codes)] for every '- REFUSED [X:code]... ref:pin: reason' line."""
    out = []
    for line in text.splitlines():
        m = re.match(r"- REFUSED ((?:\[[MKHE]:\w+\]\s*)+)(\S+?:\S+?): (.*)", line)
        if m:
            out.append((m.group(2), m.group(3), re.findall(r"\[[MKHE]:(\w+)\]", m.group(1))))
    return out


def pre_sat(snap, groups):
    on = snap.get("onN") or {}
    return all(all(set(g) <= (on.get(rd) or set()) for g in groups) for rd in readings.READINGS)


def _detail(r):
    return {k: v for k, v in (r or {}).items()
            if v and k not in ("wrong", "wrong_j3", "delivered", "delivered_each")}


def call_text(kind, kw):
    if kind == "wire":
        extra = "".join(f", {k}={v}" for k, v in kw.items() if k not in ("pins", "label_text", "schematic_path"))
        return (f"wire_pins_to_net([{', '.join(p['reference'] + ':' + str(p['pin']) for p in kw['pins'])}],"
                f" '{kw['label_text']}'{extra})")
    return f"connect_pins({kw['ref1']}:{kw['pin1']}, {kw['ref2']}:{kw['pin2']})"


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:90] + "_" + hashlib.sha1(s.encode()).hexdigest()[:8]


def keep_wrong(rec, b0, b1, kw):
    d = WRONG / _safe(rec["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "pre.kicad_sch").write_bytes(b0)
    (d / "post.kicad_sch").write_bytes(b1)
    (d / "meta.json").write_text(json.dumps(
        {k: rec.get(k) for k in ("id", "design", "sample", "group", "policy", "case", "call",
                                 "status", "msg", "cli_detail", "gui_detail")},
        indent=1, default=list), encoding="utf-8")
    rec["wrong_dir"] = str(d)


def judged(fn, kw, path, N, groups, meta, snap=None, kind=None):
    """Run fn(**kw) on the single-sheet file at path, judged by judge3. Returns (record, snap)."""
    p = Path(path)
    b0 = p.read_bytes()
    if snap is None:
        snap = J.snapshot2(str(p), str(p), N=N)
    tb = []

    def wrapped(**k):
        try:
            return fn(**k)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ not in ("ToolError", "_Refuse"):
                tb.append(traceback.format_exc())
            raise

    t0 = time.perf_counter()
    st, msg = J.call(wrapped, **kw)
    dt = time.perf_counter() - t0
    b1 = p.read_bytes()
    changed = b0 != b1
    text = strip_cls(msg)
    rec = dict(meta)
    rec.update(call=call_text(kind or meta.get("kind"), kw), status=st,
               code_prefix=code_prefix(text) if st == "REFUSED" else "",
               codes=parse_codes(text) if st == "REFUSED" else [], msg_head=text[:400],
               msg=text, seconds=round(dt, 3), file_changed=changed, script=SCRIPT,
               pre_norm=norm_hash(b0), N=N, groups=[sorted(g) for g in groups])
    if tb:
        rec["traceback"] = tb[0][-3000:]
    if st == "REFUSED":
        rec["bytes_unchanged"] = not changed
    if st == "REFUSED" and not changed:
        rec.update(cli_wrong=None, gui_wrong=None, cli_wrong_j3=None, gui_wrong_j3=None,
                   cli_detail=None, gui_detail=None, delivered_cli=None, delivered_gui=None,
                   false_noop=None,
                   pre_satisfied=pre_sat(snap, groups) if N is not None else None,
                   redundant_write=None)
    else:
        try:
            res = J.assess2(snap, str(p), groups, edited_path=str(p), status=st)
        except Exception:  # noqa: BLE001  (e.g. a written file the CST cannot parse)
            res = {"cli": {"wrong": None}, "gui": {"wrong": None}}
            rec["judge_error"] = traceback.format_exc()[-3000:]
        cli, gui = res.get("cli") or {}, res.get("gui") or {}
        rec.update(cli_wrong=cli.get("wrong"), gui_wrong=gui.get("wrong"),
                   cli_wrong_j3=cli.get("wrong_j3"), gui_wrong_j3=gui.get("wrong_j3"),
                   cli_detail=_detail(cli), gui_detail=_detail(gui),
                   delivered_cli=cli.get("delivered"), delivered_gui=gui.get("delivered"),
                   false_noop=res.get("false_noop"), pre_satisfied=res.get("pre_satisfied"),
                   redundant_write=res.get("redundant_write"))
        if rec["cli_wrong"] or rec["gui_wrong"] or rec["false_noop"] or rec.get("judge_error"):
            keep_wrong(rec, b0, b1, kw)
    clean_j3()
    return rec, snap


def new_file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ----------------------------------------------------------------------------- sequences


class Seq:
    """One design's run of a call sequence on its own evolving file."""

    def __init__(self, design, path, sample, group, policy):
        self.design, self.p = design, Path(path)
        self.sample, self.group, self.policy = sample, group, policy
        self.records = []

    def _meta(self, tag, kind, first, idx):
        v = "first"
        if "[fallback wire" in tag:
            v = "skill_fallback"
        elif "[retry" in tag:
            k = ("retry_numbers" if "[retry: numbers]" in tag else
                 "retry_without" if "[retry without" in tag else "retry_stub")
            v = ("fallback_" + k) if "[fallback]" in tag else k
        return {"sample": self.sample, "group": self.group, "case": tag, "variant": v,
                "tool": FN[kind], "kind": kind, "design": self.design, "policy": self.policy,
                "first_attempt": first, "call_index": idx,
                "id": f"{self.sample}|{self.group}|{self.policy}|{self.design}|{len(self.records):03d}"}

    def wire(self, tag, N, pins, extra=None, first=False, idx=None):
        kw = {"pins": [{"reference": r, "pin": x} for r, x in pins], "label_text": N}
        if extra:
            kw.update(extra)
        groups = pad_groups(self.p, pins)
        rec, _ = judged(getattr(DES[self.design], FN["wire"]), dict(kw, schematic_path=str(self.p)),
                        self.p, N, groups, self._meta(tag, "wire", first, idx), kind="wire")
        rec["pins"] = [list(x) for x in pins]
        self.records.append(rec)
        return rec

    def connect(self, tag, a, b, first=False, idx=None):
        kw = {"ref1": a[0], "pin1": a[1], "ref2": b[0], "pin2": b[1]}
        groups = group_of(self.p, [a, b])
        rec, _ = judged(getattr(DES[self.design], FN["connect"]), dict(kw, schematic_path=str(self.p)),
                        self.p, None, groups, self._meta(tag, "connect", first, idx), kind="connect")
        rec["pins"] = [list(a), list(b)]
        self.records.append(rec)
        return rec


def adaptive(run: Seq, tag, N, pins, rec):
    """run_designs.adaptive on coded messages (follow-up 1), geometry = GEOM_CODES."""
    if rec["status"] != "REFUSED":
        return
    tags = refused_lines(rec["msg"])
    expanded, changed = [], False
    for r, x in pins:
        hit = next((reason for t, reason, _c in tags if t == f"{r}:{x}"), None)
        m = re.search(r"matches several pads \(([^)]*)\)", hit or "")
        if m:
            expanded += [(r, n.strip()) for n in m.group(1).split(",")]
            changed = True
        else:
            expanded.append((r, x))
    if changed:
        rec2 = run.wire(tag + " [retry: numbers]", N, expanded)
        if rec2["status"] != "REFUSED":
            return
        pins, tags = expanded, refused_lines(rec2["msg"])
    bad = {t: codes for t, _reason, codes in tags}
    keep = [(r, x) for r, x in pins if f"{r}:{x}" not in bad]
    dropped = [(r, x) for r, x in pins if f"{r}:{x}" in bad]
    if keep and dropped:
        run.wire(tag + f" [retry without {', '.join(t for t in bad)}]", N, keep)
    for r, x in dropped:
        if GEOM_CODES & set(bad[f"{r}:{x}"]):
            run.wire(tag + f" [retry {r}:{x} stub 5.08]", N, [(r, x)], {"stub_length": 5.08})


def run_plan(run: Seq, calls, policy):
    for i, c in enumerate(calls, 1):
        if c[0] == "wire":
            tag = f"#{i} wire {c[1]}"
            rec = run.wire(tag, c[1], [tuple(x) for x in c[2]], first=True, idx=i)
            if policy == "adaptive":
                adaptive(run, tag, c[1], [tuple(x) for x in c[2]], rec)
        elif c[0] == "connect":
            name, (r1, p1), (r2, p2) = c[1], c[2], c[3]
            tag = f"#{i} connect {r1}:{p1}->{r2}:{p2}"
            rec = run.connect(tag, (r1, p1), (r2, p2), first=True, idx=i)
            if rec["status"] == "REFUSED":
                rec2 = run.wire(tag + f" [fallback wire {name}]", name, [(r1, p1), (r2, p2)])
                if policy == "adaptive":
                    adaptive(run, tag + " [fallback]", name, [(r1, p1), (r2, p2)], rec2)


def strip_slash(n: str) -> str:
    return n[1:] if n.startswith("/") else n


def intended(calls, p):
    """run_designs.intended: plan net -> node set (pin names resolved as llmlib.pin_numbers)."""
    nets = {}
    for c in calls:
        if c[0] == "wire":
            nets.setdefault(c[1], []).extend(tuple(x) for x in c[2])
        elif c[0] == "connect":
            nets.setdefault(c[1], []).extend([tuple(c[2]), tuple(c[3])])
    m = pin_model(p)
    return {name: {(r, n) for r, x in pins for n in pin_numbers(p, r, x, m)}
            for name, pins in nets.items()}


def final_check(want, got):
    """run_designs.final_check on one reading's netlist got = [(name, frozenset)]."""
    where = {}
    for j, (_name, v) in enumerate(got):
        for n in v:
            where[n] = j
    owner = {}
    for name, nodes in want.items():
        for n in nodes:
            owner.setdefault(n, set()).add(name)
    incomplete, misnamed = {}, {}
    for name, nodes in want.items():
        comps = {}
        for n in nodes:
            comps.setdefault(where.get(n), []).append(n)
        if len(comps) > 1:
            biggest = max(comps.values(), key=len)
            incomplete[name] = sorted(n for v in comps.values() if v is not biggest for n in v)
        j = max(comps, key=lambda k: len(comps[k]))
        if j is not None and strip_slash(got[j][0]) != name and not name.startswith(("LED_", "UART_TX")):
            misnamed[name] = got[j][0]
    shorts = []
    for name, v in got:
        plan_nets, stray = set(), []
        for n in v:
            if n in owner:
                plan_nets |= owner[n]
            else:
                stray.append(n)
        if len(plan_nets) > 1:
            shorts.append(f"{name}: plan nets {sorted(plan_nets)} joined")
        if plan_nets and stray:
            shorts.append(f"{name}: unplanned pins {sorted(stray)} joined plan net {sorted(plan_nets)}")
        if not plan_nets and len(stray) > 1:
            shorts.append(f"{name}: unplanned pins joined {sorted(stray)}")
    return {"planned_pins": sum(len(v) for v in want.values()),
            "pins_not_on_plan_net": sum(len(v) for v in incomplete.values()),
            "incomplete": {k: [list(x) for x in v] for k, v in incomplete.items()},
            "shorts": shorts, "misnamed": misnamed}


def finals(calls, p):
    out = {}
    want = intended(calls, p)
    for rd in ("cli", "gui"):
        try:
            nets = J.nets_full(p) if rd == "cli" else J.nets_full(J.gui_tree_copy(p))
        except Exception as e:  # noqa: BLE001
            out[rd] = {"error": repr(e)}
            continue
        out[rd] = ({"unloadable": True} if nets is None
                   else final_check(want, [(n, v) for _c, n, _k, v in nets]))
    clean_j3()
    return out


def attribute(fin, records, base):
    """For every planned pin left off its plan net (either reading): the calls that requested
    it, with their status, whole-call codes and the codes of the refused line naming it."""
    m = pin_model(base)
    cache = {}

    def nums(r, x):
        if (r, x) not in cache:
            cache[(r, x)] = set(pin_numbers(base, r, x, m))
        return cache[(r, x)]

    off = {}
    for rd in ("cli", "gui"):
        for net, nodes in ((fin.get(rd) or {}).get("incomplete") or {}).items():
            for r, n in nodes:
                off.setdefault((net, r, n), []).append(rd)
    out = []
    for (net, r, n), rds in sorted(off.items()):
        hist = []
        for rec in records:
            req = [x for rr, x in rec["pins"] if rr == r and n in nums(rr, x)]
            if not req:
                continue
            own = [c for t, _reason, codes in refused_lines(rec["msg"]) for c in codes
                   if t in {f"{r}:{x}" for x in req}] if rec["status"] == "REFUSED" else []
            hist.append({"case": rec["case"], "status": rec["status"], "call_codes": rec["codes"],
                         "pin_line_codes": own, "delivered": [rec.get("delivered_cli"), rec.get("delivered_gui")]})
        out.append({"net": net, "pin": f"{r}:{n}", "readings": rds, "calls": hist})
    return out


# ------------------------------------------------------------------------------- base files


def _place(p, lib, ref, x, y, rot=0, value=None):
    SCH.place_component(lib_id=lib, reference=ref, value=value or ref, x=x, y=y, rotation=rot,
                        schematic_path=str(p))


def _pin_xy(p, ref, num):
    x, y, _ = MI.pin_positions(str(p), ref)[num][0]
    return MI._iu(x), MI._iu(y)


def _raw_wire(p, a, b):
    harness.wire(str(p), a[0] / 1e4, a[1] / 1e4, b[0] / 1e4, b[1] / 1e4)


def build_gen(d, p):
    PROJ.create_schematic(schematic_path=str(p))
    if tuple(d.get("page", (297, 210))) == (420, 297):
        SCH.set_page_size(size="A3", schematic_path=str(p))
    for lib, ref, val, x, y, rot, mir in d["parts"]:
        SCH.place_component(lib_id=lib, reference=ref, value=val, x=x, y=y, rotation=rot,
                            mirror=mir, schematic_path=str(p))


def star_combos():
    """measure_star.py's placements (gen_layouts.bbox/overlaps, copied to src/)."""
    import gen_layouts as G

    combos = []
    for dxr, dyr, dxc, rr in itertools.product((12.7, 20.32), (-7.62, 0.0, 10.16), (7.62, 15.24),
                                                (0, 90)):
        r_xy = (127 + dxr, 88.9 + dyr)
        c_xy = (127 + dxc, 88.9 + 12.7)
        boxes = [G.bbox("MCU_ST_STM32G0:STM32G030F6Px", 101.6, 101.6, 0),
                 G.bbox("Device:R", *r_xy, rr), G.bbox("Device:C", *c_xy, 0)]
        if any(G.overlaps(a, b, 0.0) for i, a in enumerate(boxes) for b in boxes[i + 1:]):
            continue
        combos.append((r_xy, rr, c_xy))
    return combos


def build_star(combo, p):
    r_xy, rr, c_xy = combo
    PROJ.create_schematic(schematic_path=str(p))
    _place(p, "MCU_ST_STM32G0:STM32G030F6Px", "U1", 101.6, 101.6, value="STM32G030")
    _place(p, "Device:R", "R1", *r_xy, rot=rr, value="1k")
    _place(p, "Device:C", "C1", *c_xy, value="100n")


def jct_fixtures():
    out = []
    for v in ("pin_first", "far_first"):
        out.append(("J1", v))
    for v in ("a_label_at_start", "b_label_at_end", "c_label_far_end"):
        out.append(("J2", v))
    for v in ("a_label_end_first", "b_far_end_first"):
        out.append(("J2c", v))
    for v in ("a_label_end_first", "b_pin_end_first"):
        out.append(("J2b", v))
    for v in ("a_from_R2", "b_from_R5"):
        out.append(("J6", v))
    return out


def build_jct(fam, v, p):
    """measure_jct.py's fixtures, as follow-up 1 rebuilt them. Returns (kind, call)."""
    if p is not None:
        PROJ.create_schematic(schematic_path=str(p))
    if fam == "J1":
        if p is not None:
            _place(p, "Device:R", "R1", 101.6, 101.6)
            _place(p, "Device:R", "R2", 101.6, 86.36)
            a, b = _pin_xy(p, "R1", "1"), _pin_xy(p, "R2", "2")
            _raw_wire(p, a, b) if v == "pin_first" else _raw_wire(p, b, a)
        return "wire", {"N": "N", "pins": [("R1", "1")]}
    if fam == "J2":
        if p is not None:
            _place(p, "Device:R", "R1", 101.6, 101.6)
            _place(p, "Device:R", "R3", 88.9, 91.44)
            _place(p, "Device:R", "R4", 114.3, 91.44)
            r3, r4 = _pin_xy(p, "R3", "2"), _pin_xy(p, "R4", "2")
            if v == "a_label_at_start":
                _raw_wire(p, r3, r4)
                harness.label(str(p), "N", r3[0] / 1e4, r3[1] / 1e4)
            elif v == "b_label_at_end":
                _raw_wire(p, r4, r3)
                harness.label(str(p), "N", r3[0] / 1e4, r3[1] / 1e4)
            else:
                _raw_wire(p, r3, r4)
                harness.label(str(p), "N", r4[0] / 1e4, r4[1] / 1e4)
        return "wire", {"N": "N", "pins": [("R1", "1")]}
    if fam == "J2c":
        if p is not None:
            _place(p, "Device:R", "R10", 76.2, 88.9, rot=90)
            _place(p, "Device:R", "R11", 152.4, 88.9, rot=90)
            for i, x in enumerate((101.6, 114.3, 127.0), start=1):
                _place(p, "Device:C", f"C{i}", x, 95.25)
            a, b = _pin_xy(p, "R10", "2"), _pin_xy(p, "R11", "1")
            _raw_wire(p, a, b) if v == "a_label_end_first" else _raw_wire(p, b, a)
            harness.label(str(p), "VCC", a[0] / 1e4, a[1] / 1e4)
        return "wire", {"N": "VCC", "pins": [(f"C{i}", "1") for i in (1, 2, 3)]}
    if fam == "J2b":
        if p is not None:
            _place(p, "Device:R", "R1", 101.6, 101.6)
            _place(p, "Device:R", "R2", 114.3, 101.6)
            a, r2 = _pin_xy(p, "R1", "1"), _pin_xy(p, "R2", "1")
            free = (888900, a[1])
            _raw_wire(p, free, r2) if v == "a_label_end_first" else _raw_wire(p, r2, free)
            harness.label(str(p), "N", free[0] / 1e4, free[1] / 1e4)
        return "wire", {"N": "N", "pins": [("R1", "1")]}
    if fam == "J6":
        if p is not None:
            _place(p, "Device:R", "R1", 101.6, 101.6)
            _place(p, "Device:R", "R2", 127.0, 97.79, rot=90)
            _place(p, "Device:R", "R5", 76.2, 97.79, rot=90)
            b, r5 = _pin_xy(p, "R2", "1"), _pin_xy(p, "R5", "2")
            _raw_wire(p, b, r5) if v == "a_from_R2" else _raw_wire(p, r5, b)
        return "connect", {"N": None, "pins": [("R1", "1"), ("R2", "1")]}
    raise KeyError(fam)


def gen_layouts():
    import gen_layouts as G

    ds, _rej = G.layouts(6)
    assert len(ds) == 36, len(ds)
    return ds


def base_path(sample, name):
    return BASES / sample / name / f"{name}.kicad_sch"


def build_bases():
    """Build every generated, star and junction base file once (kept; reused on rerun)."""
    built = 0
    todo = [("gen", d["name"], (lambda d: lambda p: build_gen(d, p))(d)) for d in gen_layouts()]
    todo += [("star", f"star{k:02d}", (lambda c: lambda p: build_star(c, p))(c))
             for k, c in enumerate(star_combos())]
    todo += [("jct", f"{f}_{v}", (lambda f, v: lambda p: build_jct(f, v, p))(f, v))
             for f, v in jct_fixtures()]
    for sample, name, fn in todo:
        dst = base_path(sample, name)
        if dst.exists():
            continue
        tmp = WORK / "build" / name / dst.name
        rmtree(tmp.parent)
        tmp.parent.mkdir(parents=True)
        fn(tmp)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp, dst)
        rmtree(tmp.parent)
        built += 1
    return built, len(todo)


# ------------------------------------------------------------------------------------- tasks


def task_generated(d, policy, design):
    name = d["name"]
    base = base_path("gen", name)
    tdir = WORK / "gen" / f"{name}_{policy}_{design}"
    rmtree(tdir)
    p = new_file(tdir / design / base.name, base.read_bytes())
    run = Seq(design, p, "generated", name, policy)
    run_plan(run, d["calls"], policy)
    fin = finals(d["calls"], str(p))
    fins = [{"sample": "generated", "group": name, "policy": policy, "design": design,
             "knobs": d["knobs"], "final": fin, "off_net": attribute(fin, run.records, base),
             "base_norm": norm_hash(base.read_bytes()), "script": SCRIPT}]
    rmtree(tdir)
    return run.records, fins


_SNAP: dict = {}


def stock_snap(sheet, N):
    key = (sheet, N)
    if key not in _SNAP:
        q = new_file(WORK / f"snap_{os.getpid()}" / sheet / f"{sheet}.base.kicad_sch",
                     (FIXT / f"{sheet}.base.kicad_sch").read_bytes())
        _SNAP[key] = J.snapshot2(str(q), str(q), N=N)
        rmtree(q.parent)
        clean_j3()
    return _SNAP[key]


def task_stock(item):
    sheet = item["sheet"]
    src = FIXT / f"{sheet}.base.kicad_sch"
    pre = src.read_bytes()
    fname = src.name
    if "pin" in item:
        kind, pins = "wire", [(item["ref"], item["pin"])]
        case = f"{item['ref']}:{item['pin']}"
        N = "VP_STOCK"
        kw = {"pins": [{"reference": item["ref"], "pin": item["pin"]}], "label_text": N}
        extra = {"in110": item["in110"], "in60": item["in60"]}
    else:
        kind, pins = "connect", [tuple(item["a"]), tuple(item["b"])]
        case = f"{item['a'][0]}:{item['a'][1]}->{item['b'][0]}:{item['b'][1]}"
        N = None
        kw = {"ref1": item["a"][0], "pin1": item["a"][1], "ref2": item["b"][0], "pin2": item["b"][1]}
        extra = {"dist_mm": item["dist_mm"]}
    tdir = WORK / "stock" / f"{sheet}_{re.sub(r'[^A-Za-z0-9_-]', '_', case)}"
    rmtree(tdir)
    groups = groups_for(kind, src, pins)
    recs = []
    for design in DESIGNS:
        q = new_file(tdir / design / fname, pre)
        meta = {"sample": "stock", "group": sheet, "case": case, "variant": "first",
                "tool": FN[kind], "kind": kind, "design": design, "policy": "single",
                "first_attempt": True, "call_index": 1,
                "id": f"stock|{sheet}|{case}|{design}", **extra}
        rec, _ = judged(getattr(DES[design], FN[kind]), dict(kw, schematic_path=str(q)), q, N,
                        groups, meta, snap=stock_snap(sheet, N), kind=kind)
        rec["pins"] = [list(x) for x in pins]
        recs.append(rec)
        rmtree(q.parent)
    rmtree(tdir)
    return recs, []


def task_star(k):
    name = f"star{k:02d}"
    base = base_path("star", name)
    bb = base.read_bytes()
    combo = star_combos()[k]
    group = f"{name} R1 at {combo[0]} rot{combo[1]}, C1 at {combo[2]}"
    tdir = WORK / "star" / name
    rmtree(tdir)
    recs = []
    for design in DESIGNS:
        p = new_file(tdir / design / base.name, bb)
        run = Seq(design, p, "star", group, "skill")
        r1 = run.connect("connect U1:PA1->C1:1", ("U1", "PA1"), ("C1", "1"), first=True, idx=1)
        if r1["status"] == "OK":
            r2 = run.connect("connect R1:1->U1:PA1", ("R1", "1"), ("U1", "PA1"), first=True, idx=2)
            if r2["status"] == "REFUSED":
                run.wire("connect R1:1->U1:PA1 [fallback wire ADC]", "ADC", [("R1", "1"), ("U1", "PA1")])
        recs += run.records
    rmtree(tdir)
    return recs, []


def task_jct(fam, v):
    name = f"{fam}_{v}"
    base = base_path("jct", name)
    bb = base.read_bytes()
    kind, call = build_jct(fam, v, None)
    tdir = WORK / "jct" / name
    rmtree(tdir)
    recs = []
    for design in DESIGNS:
        p = new_file(tdir / design / base.name, bb)
        run = Seq(design, p, "jct", fam, "single")
        if kind == "wire":
            run.wire(f"{name} wire {call['N']}", call["N"], call["pins"], first=True, idx=1)
        else:
            run.connect(f"{name} connect R1:1->R2:1", *call["pins"], first=True, idx=1)
        recs += run.records
    rmtree(tdir)
    return recs, []


def dispatch(task):
    kind, args = task
    t0 = time.time()
    try:
        fn = {"gen": task_generated, "stock": task_stock, "star": task_star, "jct": task_jct}[kind]
        recs, fins = fn(*args)
        return {"task": _label(task), "recs": recs, "fins": fins, "error": None,
                "seconds": round(time.time() - t0, 1)}
    except Exception:  # noqa: BLE001
        return {"task": _label(task), "recs": [], "fins": [], "error": traceback.format_exc(),
                "seconds": round(time.time() - t0, 1)}


def _label(task):
    kind, args = task
    if kind == "gen":
        return f"gen {args[0]['name']} {args[1]} {args[2]}"
    if kind == "stock":
        it = args[0]
        return f"stock {it['sheet']} " + (f"{it['ref']}:{it['pin']}" if "pin" in it else
                                          f"{it['a']}->{it['b']}")
    if kind == "star":
        return f"star {args[0]}"
    return f"jct {args[0]} {args[1]}"


def _rec_label(r):
    """The _label of the task that wrote record r (for --resume)."""
    s = r["sample"]
    if s == "generated":
        return f"gen {r['group']} {r['policy']} {r['design']}"
    if s == "stock":
        if r["kind"] == "wire":
            return f"stock {r['group']} {r['case']}"
        a, b = r["case"].split("->")
        return f"stock {r['group']} {a.split(':')}->{b.split(':')}"
    if s == "star":
        return f"star {int(r['group'][4:6])}"
    fam, v = r["group"], r["case"].split(" ", 1)[0][len(r["group"]) + 1:]
    return f"jct {fam} {v}"


def build_tasks(only, limit):
    tasks = []
    if "gen" in only:
        ds = gen_layouts()
        for d in ds[:limit] if limit else ds:
            for policy in ("skill", "adaptive"):
                for design in DESIGNS:
                    tasks.append(("gen", (d, policy, design)))
    if "stock" in only:
        ps = json.loads((HERE / "pinsets.json").read_text())
        items = ps["pins"] + ps["pairs"]
        if limit:
            items = ps["pins"][:limit] + ps["pairs"][:limit]
        tasks += [("stock", (it,)) for it in items]
    if "star" in only:
        n = len(star_combos())
        assert n == 20, n
        tasks += [("star", (k,)) for k in range(n)][: limit or None]
    if "jct" in only:
        tasks += [("jct", fv) for fv in jct_fixtures()][: limit or None]
    return tasks


def main():
    import argparse
    from concurrent.futures import ProcessPoolExecutor, as_completed

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="gen,stock,star,jct")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--resume", action="store_true",
                    help="keep existing outputs and skip tasks whose records are already there")
    a = ap.parse_args()
    only = set(a.only.split(","))
    sfx = f"_{a.tag}" if a.tag else ""
    out_r = OUT / f"records{sfx}.jsonl"
    out_f = OUT / f"finals{sfx}.jsonl"
    out_e = OUT / f"errors{sfx}.jsonl"
    built, total = build_bases()
    print(f"bases: {built} built, {total - built} reused", flush=True)
    if only == {"none"}:  # bases only: stop before any record file is touched
        return
    tasks = build_tasks(only, a.limit)
    missing = sorted({t[1][0]["sheet"] for t in tasks if t[0] == "stock"
                      and not (FIXT / f"{t[1][0]['sheet']}.base.kicad_sch").exists()})
    if missing:
        sys.exit(f"stock fixture(s) {missing} not found under {FIXT}: run "
                 "tools/routing_harness/prepare.py first (it copies them from the installed "
                 "KiCad's demos)")
    if a.resume and out_r.exists():
        done_labels = {_rec_label(json.loads(x)) for x in out_r.read_text(encoding="utf-8").splitlines() if x.strip()}
        before = len(tasks)
        tasks = [t for t in tasks if _label(t) not in done_labels]
        print(f"resume: {before - len(tasks)} tasks already recorded, {len(tasks)} to run", flush=True)
        if out_e.exists():
            out_e.replace(out_e.with_suffix(f".{int(time.time())}.jsonl"))
    else:
        for f in (out_r, out_f, out_e):
            if f.exists():
                f.unlink()
    print(f"{len(tasks)} tasks, {a.workers} workers", flush=True)
    t0 = time.time()
    done = 0
    # spawn on every platform: each worker re-imports this script, so _setup_env gives it its own TMP
    with ProcessPoolExecutor(max_workers=a.workers,
                             mp_context=multiprocessing.get_context("spawn")) as ex:
        futs = [ex.submit(dispatch, t) for t in tasks]
        for fu in as_completed(futs):
            r = fu.result()
            done += 1
            with open(out_r, "a", encoding="utf-8") as fh:
                for rec in r["recs"]:
                    fh.write(json.dumps(rec, default=list) + "\n")
            with open(out_f, "a", encoding="utf-8") as fh:
                for fin in r["fins"]:
                    fh.write(json.dumps(fin, default=list) + "\n")
            if r["error"]:
                with open(out_e, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"task": r["task"], "error": r["error"]}) + "\n")
            st = {}
            for rec in r["recs"]:
                st[rec["status"]] = st.get(rec["status"], 0) + 1
            print(f"[{done}/{len(tasks)} {time.time() - t0:6.0f}s] {r['task']}: {len(r['recs'])} recs"
                  f" {st} in {r['seconds']}s{' ERROR' if r['error'] else ''}", flush=True)
    print(f"done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
