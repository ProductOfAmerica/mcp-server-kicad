"""Judge library for the llm-workflow probe (B = the repo's own tools at hpaths.TARGET, P =
proposal_impl, M = mine_impl). The pressure test's B figures (LW-13) need PT_REPO set to a git
archive of 54160c1.

Every call is judged against kicad-cli (9.0.8 in the pressure test; measured), keyed by node sets,
never by name:
  - requested pins on the requested net after the call (OK) or already on it (NOOP);
  - merges only among the requested pins' own nets and nets already named N;
  - no splits, no vanished pins, no renames except of the requested nets;
  - no false no-op (tool says 'already on N', KiCad disagrees);
  - no partial wiring (an ambiguous pin name resolves to several pads, only some end on N);
  - refusals leave the file byte-identical.
New geometry is also checked for version-dependent hazards (measured or sourced in the digest):
junction or bus-entry end strictly inside a wire (kicad-cli 9 truncates, G04), collinear
overlap of two wires (GUI merges, kicad-cli does not, G06), a new wire passing over a foreign
pin end (looks connected, RX-19). When a file holds a truncation or overlap hazard, the pressure
test repeated the checks on a GUI-equivalent copy made by an emulation of eeschema's load-time
CleanUp. That emulation is not part of this repository (core/gui_reading.py says why), so such a
record's gui_view says the reading is not available; it is never reported clean.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import uuid as _uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import hpaths  # noqa: E402

# Every temp file of the chain (harness.BASE, WORK below, the files fresh() makes) lands under the
# run's lw13 output directory, so nothing is written outside the run directory. This must come
# before the first import of harness, which creates BASE at import.
_TMP = hpaths.suite_out("lw13") / "tmp" / f"p{os.getpid()}"
_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(_TMP)
tempfile.tempdir = str(_TMP)

import harness  # noqa: E402
from harness import _cst, _run_cli, sha  # noqa: E402

import gui_reading  # noqa: E402
import mine_impl as M  # noqa: E402
import proposal_impl as P  # noqa: E402
from mcp_server_kicad import schematic as B  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="llmwf_"))
AUTO = ("Net-(", "unconnected-(")


def strip(n: str) -> str:
    return n[1:] if n.startswith("/") else n


def strong(n: str) -> bool:
    return bool(n) and not strip(n).startswith(AUTO)


# ------------------------------------------------------------------ netlist oracle


class Unloadable(Exception):
    pass


def nets(p) -> list[tuple[str, frozenset]]:
    """kicad-cli 9.0.8 netlist as [(raw name, frozenset((ref, pin)))], one entry per net code."""
    p = str(p)
    out = p + ".llm.xml"
    if os.path.exists(out):
        os.remove(out)
    r = _run_cli(["sch", "export", "netlist", "--format", "kicadxml", "--output", out, p],
                 check=False)
    if not os.path.exists(out):
        raise Unloadable(f"kicad-cli netlist export failed rc={r.returncode}: "
                         f"{(r.stderr or '').strip()[:200]}")
    res = []
    for net in ET.parse(out).getroot().iter("net"):
        res.append((net.get("name", ""), frozenset(
            (n.get("ref", ""), n.get("pin", "")) for n in net.findall("node"))))
    return res


def ndiff(before, after):
    """Node-set diff. Nets identified by index in *before* (names can collide)."""
    bidx = {n: i for i, (_, v) in enumerate(before) for n in v}
    aidx = {n: i for i, (_, v) in enumerate(after) for n in v}
    merges, splits, renames = [], [], []
    for j, (name, v) in enumerate(after):
        src = sorted({bidx[n] for n in v if n in bidx})
        if len(src) > 1:
            merges.append((j, src))
    for i, (name, v) in enumerate(before):
        dst = sorted({aidx[n] for n in v if n in aidx})
        if len(dst) > 1:
            splits.append((i, dst))
    bsets = {v: i for i, (_, v) in enumerate(before) if v}
    for j, (name, v) in enumerate(after):
        i = bsets.get(v)
        if i is not None and before[i][0] != name:
            renames.append((i, j))
    return {"merges": merges, "splits": splits, "renames": renames, "bidx": bidx, "aidx": aidx,
            "vanished": sorted(set(bidx) - set(aidx)), "appeared": sorted(set(aidx) - set(bidx))}


# ------------------------------------------------------------------ geometry hazards


def _iu(v) -> int:
    s = float(v) * 10000.0
    return int(s + 0.5) if s >= 0 else -int(-s + 0.5)


def _on_interior(q, a, b) -> bool:
    if a == b:
        return False
    cross = (b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0])
    if cross != 0:
        return False
    dot = (q[0] - a[0]) * (b[0] - a[0]) + (q[1] - a[1]) * (b[1] - a[1])
    return 0 < dot < (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2


def _overlap(a1, b1, a2, b2) -> bool:
    """Collinear segments sharing a positive length."""
    if a1 == b1 or a2 == b2:
        return False
    for q in (a2, b2):
        if (b1[0] - a1[0]) * (q[1] - a1[1]) - (b1[1] - a1[1]) * (q[0] - a1[0]) != 0:
            return False
    key = lambda q: (q[0], q[1])  # noqa: E731
    s1, e1 = sorted((a1, b1), key=key)
    s2, e2 = sorted((a2, b2), key=key)
    lo, hi = max(s1, s2, key=key), min(e1, e2, key=key)
    return key(lo) < key(hi)


def geometry(p):
    """Items by uuid: wires/buses (a, b), point items (kind, pt), pin ends of placed symbols."""
    root = _cst.parse(open(p, "rb").read()).lists[0]
    lines, points = {}, {}
    for tok in ("wire", "bus"):
        for n in root.find_all(tok):
            xy = n.find("pts").find_all("xy")
            if len(xy) < 2:
                continue
            u = n.find("uuid").atoms[1].text
            lines[u] = (tok, (_iu(xy[0].atoms[1].text), _iu(xy[0].atoms[2].text)),
                        (_iu(xy[1].atoms[1].text), _iu(xy[1].atoms[2].text)))
    for tok in ("junction", "no_connect", "label", "global_label", "hierarchical_label",
                "netclass_flag", "directive_label"):
        for n in root.find_all(tok):
            at = n.find("at")
            u = n.find("uuid").atoms[1].text
            txt = n.atoms[1].text if tok not in ("junction", "no_connect") else ""
            points[u] = (tok, (_iu(at.atoms[1].text), _iu(at.atoms[2].text)), txt)
    for n in root.find_all("bus_entry"):
        at, sz = n.find("at"), n.find("size")
        a = (_iu(at.atoms[1].text), _iu(at.atoms[2].text))
        u = n.find("uuid").atoms[1].text
        points[u + ":a"] = ("bus_entry", a, "")
        points[u + ":b"] = ("bus_entry", (a[0] + _iu(sz.atoms[1].text), a[1] + _iu(sz.atoms[2].text)), "")
    m = M._Model(root)  # KiCad-faithful pin ends (rotate then mirror, lib_name lookup)
    pins = [((it.ref, it.num), (it.x, it.y)) for it in m.items if it.kind == "pin"]
    return lines, points, pins


def hazards(g0, g1):
    """Version-dependent or visual hazards the call's NEW geometry created."""
    lines0, points0, pins0 = g0
    lines1, points1, pins1 = g1
    new_lines = {u: v for u, v in lines1.items() if u not in lines0}
    new_points = {u: v for u, v in points1.items() if u not in points0}
    out = []
    cuts1 = [(u, v) for u, v in points1.items() if v[0] in ("junction", "bus_entry")]
    # junction / bus-entry end strictly inside a wire, where either side is new
    for u, (kind, q, _) in cuts1:
        for lu, (tok, a, b) in lines1.items():
            if _on_interior(q, a, b) and (u in new_points or lu in new_lines):
                out.append(f"TRUNCATION: {kind} at {q[0] / 1e4},{q[1] / 1e4} inside {tok} "
                           f"{a[0] / 1e4},{a[1] / 1e4}-{b[0] / 1e4},{b[1] / 1e4}"
                           f" ({'new ' + kind if u in new_points else 'new ' + tok})")
    for lu, (tok, a, b) in new_lines.items():
        for ou, (tok2, c, d) in lines1.items():
            if ou == lu or tok2 != tok:
                continue
            if _overlap(a, b, c, d):
                out.append(f"OVERLAP: new {tok} {a[0] / 1e4},{a[1] / 1e4}-{b[0] / 1e4},{b[1] / 1e4}"
                           f" collinear with {c[0] / 1e4},{c[1] / 1e4}-{d[0] / 1e4},{d[1] / 1e4}")
        for node, q in pins1:
            if _on_interior(q, a, b):
                out.append(f"PIN-ON-WIRE: new wire {a[0] / 1e4},{a[1] / 1e4}-{b[0] / 1e4},{b[1] / 1e4}"
                           f" passes over pin end {node[0]}:{node[1]}")
    # dedupe overlap pairs reported twice
    return sorted(set(out))


def has_canon_hazard(p) -> bool:
    lines, points, _ = geometry(p)
    for kind, q, _ in points.values():
        if kind in ("junction", "bus_entry"):
            if any(_on_interior(q, a, b) for _, a, b in lines.values()):
                return True
    ls = list(lines.values())
    for i, (t1, a1, b1) in enumerate(ls):
        for t2, a2, b2 in ls[i + 1:]:
            if t1 == t2 and _overlap(a1, b1, a2, b2):
                return True
    return False


# ------------------------------------------------------------------ pin references


def pin_numbers(p, ref, ptxt):
    """What an LLM's 'ref:pin' denotes: the exact number if some pin has it, else every pin
    number whose primary name matches (all units). Uses KiCad's lib_name resolution."""
    root = _cst.parse(open(p, "rb").read()).lists[0]
    m = M._Model(root)
    syms = [s for s in m.syms if s.ref == ref and s.lib is not None]
    nums, byname = set(), set()
    for s in syms:
        for sub in s.lib.find_all("symbol"):
            for pn in sub.find_all("pin"):
                num = pn.find("number").atoms[1].text
                nam = pn.find("name").atoms[1].text
                nums.add(num)
                if nam == ptxt:
                    byname.add(num)
    if ptxt in nums:
        return [ptxt]
    return sorted(byname)


# ------------------------------------------------------------------ calling the tools


def _fn(impl, kind):
    mod = {"B": B, "P": P, "M": M}[impl]
    return {"wire": mod.wire_pins_to_net, "connect": mod.connect_pins,
            "decap": mod.auto_place_decoupling_cap}[kind]


def invoke(impl, kind, kw):
    t0 = time.perf_counter()
    try:
        msg = _fn(impl, kind)(**kw)
        st = "NOOP" if str(msg).startswith("No change") else "OK"
    except ToolError as e:
        st, msg = "REFUSED", str(e)
    except ValueError as e:  # B's connect_pins leaks ValueError on a bad pin (file intact)
        st, msg = "REFUSED", f"ValueError: {e}"
    except Exception as e:  # noqa: BLE001
        st, msg = "ERROR", f"{type(e).__name__}: {e}"
    return st, str(msg), time.perf_counter() - t0


def judge_view(before, after, req_sets, N_list, allowed_extra_nodes=()):
    """Problems in one view. req_sets: [(label, [nodes], N or None)] ; N_list: names whose
    existing nets may join. For connect: N None and the label's nodes must end on one net."""
    d = ndiff(before, after)
    bidx = d["bidx"]
    allowed = set()
    for _lab, nodes, _N in req_sets:
        for n in nodes:
            if n in bidx:
                allowed.add(bidx[n])
    for i, (name, v) in enumerate(before):
        if any(strip(name) == N for N in N_list if N):
            allowed.add(i)
    probs = []
    bad = [(j, src) for j, src in d["merges"] if not set(src) <= allowed]
    for j, src in bad:
        probs.append("UNINTENDED MERGE " + after[j][0] + " <= " +
                     " + ".join(f"{before[i][0]}{sorted(before[i][1])[:4]}" for i in src))
    for i, dst in d["splits"]:
        probs.append(f"SPLIT {before[i][0]}{sorted(before[i][1])[:6]} -> " +
                     " | ".join(f"{after[j][0]}{sorted(after[j][1])[:4]}" for j in dst))
    req_idx = {bidx[n] for _l, nodes, _N in req_sets for n in nodes if n in bidx}
    for i, j in d["renames"]:
        if i not in req_idx:
            probs.append(f"UNINTENDED RENAME {before[i][0]} -> {after[j][0]}")
    van = [n for n in d["vanished"]]
    app = [n for n in d["appeared"] if n not in allowed_extra_nodes]
    if van:
        probs.append(f"PINS VANISHED {van[:6]}")
    if app:
        probs.append(f"PINS APPEARED {app[:6]}")
    # a pin that was on a strongly named net and is no longer on a net of that name
    anames = {n: strip(name) for name, v in after for n in v}
    for i, (name, v) in enumerate(before):
        if not strong(name):
            continue
        left = sorted(n for n in v if n in anames and anames[n] != strip(name)
                      and not (i in allowed and any(anames[n] == N for N in N_list if N)))
        if left:
            probs.append(f"PIN LEFT NET {name}: {left[:6]}")
    # merged two strongly named nets that were distinct before (P's rule and M's invariant)
    for j, src in d["merges"]:
        names = {strip(before[i][0]) for i in src if strong(before[i][0])}
        if len(names) > 1:
            probs.append(f"NAMED NETS MERGED {sorted(names)} -> {after[j][0]}")
    return probs, d


def req_status(after, req_sets):
    """Per request: (ok, detail)."""
    aw = {}
    for j, (name, v) in enumerate(after):
        for n in v:
            aw.setdefault(n, []).append(j)
    out = []
    for lab, nodes, N in req_sets:
        if N is not None:
            off = [n for n in nodes if not any(strip(after[j][0]) == N for j in aw.get(n, []))]
            out.append((lab, not off, f"not on {N}: {off}" if off else ""))
        else:
            js = [set(aw.get(n, [])) for n in nodes]
            joined = bool(js) and bool(set.intersection(*js))
            out.append((lab, joined, "" if joined else f"not joined: {nodes}"))
    return out


class Run:
    """One design executed with one implementation, judged call by call."""

    def __init__(self, path, impl, log, judge=None):
        self.p = str(path)
        self.impl = impl
        self.log = log
        self.records = []
        self.dead = False
        self.judge = str(judge) if judge else None  # export this file (a root) instead of p
        try:
            self.cur = nets(self.judge or self.p)
        except Unloadable as e:
            self.dead = True
            self.cur = None
            log(f"  [{impl}] start file unloadable: {e}")

    def step(self, tag, kind, kw, reqs, N_list, extra_nodes=(), note=""):
        """reqs: [(label, ref, pin_text, N)] for wire/decap; [(label, (r1, p1), (r2, p2))] for
        connect. Returns the record."""
        p = self.p
        rec = {"tag": tag, "impl": self.impl, "kind": kind, "kw": {k: v for k, v in kw.items()
                                                                     if k != "schematic_path"},
               "note": note}
        h0 = sha(p)
        g0 = geometry(p)
        snap = WORK / f"snap_{_uuid.uuid4().hex[:8]}"
        snap.mkdir()
        snap_p = snap / Path(p).name
        shutil.copy2(p, snap_p)
        st, msg, dt = invoke(self.impl, kind, kw)
        h1 = sha(p)
        rec.update(status=st, msg=msg, seconds=round(dt, 3), changed=h0 != h1,
                   before_file=str(snap_p))
        # requested node sets (resolved on the file AFTER the call, so decap's new part exists)
        req_sets = []
        if kind == "connect":
            lab, (r1, p1), (r2, p2) = reqs[0]
            n1 = [(r1, x) for x in pin_numbers(p, r1, p1)]
            n2 = [(r2, x) for x in pin_numbers(p, r2, p2)]
            req_sets.append((lab, n1 + n2, None))
        else:
            for lab, ref, ptxt, N in reqs:
                req_sets.append((lab, [(ref, x) for x in pin_numbers(p, ref, ptxt)], N))
        rec["requested"] = [(lab, nodes, N) for lab, nodes, N in req_sets]
        probs = []
        if st in ("REFUSED", "NOOP", "ERROR") and h0 != h1:
            probs.append(f"FILE CHANGED ON {st}")
        if st == "OK" and h0 == h1:
            probs.append("OK BUT FILE UNCHANGED")
        if self.dead:
            rec.update(problems=probs + ["(file already unloadable; not judged)"], kicad=None)
            self.records.append(rec)
            return rec
        before = self.cur
        try:
            after = nets(self.judge or p) if h0 != h1 else before
        except Unloadable as e:
            probs.append(f"UNLOADABLE AFTER CALL: {e}")
            self.dead = True
            rec.update(problems=probs, kicad=None)
            self.records.append(rec)
            self.log(f"  [{self.impl}] {tag}: {st} -> FILE NO LONGER LOADS: {e}")
            return rec
        vprobs, d = judge_view(before, after, req_sets, N_list, extra_nodes)
        probs += vprobs
        rs = req_status(after, req_sets)
        if st in ("OK", "NOOP"):
            for (lab, ok, det), (_l, nodes, N) in zip(rs, req_sets):
                if ok:
                    continue
                if st == "NOOP":
                    probs.append(f"FALSE NO-OP {lab}: {det}")
                    continue
                on = [n for n in nodes if N is not None and
                      any(strip(nm) == N for nm, v in after if n in v)]
                kind_ = "PARTIAL (ambiguous name, only some pads wired)" if on else "REQUEST NOT MET"
                probs.append(f"{kind_} {lab}: {det}")
        # wrong pin: a requested pin not on N while another pin of the same ref newly is
        if st == "OK":
            all_req = {n for _l, ns, _N in req_sets for n in ns}
            bnet = {n: v for _bn, v in before for n in v}
            for lab, nodes, N in req_sets:
                if N is None:
                    continue
                off = [n for n in nodes if not any(strip(nm) == N for nm, v in after if n in v)]
                if not off:
                    continue
                refs = {n[0] for n in nodes}
                own = {m for n in all_req for m in bnet.get(n, ())}
                newly = [n for nm, v in after if strip(nm) == N for n in v
                         if n[0] in refs and n not in all_req and n not in own and
                         not any(strip(bn) == N and n in bv for bn, bv in before)]
                if newly:
                    probs.append(f"WRONG PIN {lab}: asked {nodes}, KiCad put {newly} on {N}")
        g1 = geometry(p) if h0 != h1 else g0
        hz = hazards(g0, g1) if h0 != h1 else []
        rec["hazards"] = hz
        gui_probs = []
        if self.judge is None and h0 != h1 and (has_canon_hazard(p) or has_canon_hazard(str(snap_p))):
            # Where the pressure test judged a GUI-equivalent copy; that emulation is not here.
            gui_probs = [f"gui reading not available: {gui_reading.REASON}"]
        rec["gui_view"] = gui_probs
        rec["problems"] = probs
        rec["req"] = rs
        rec["merges"] = [(after[j][0], [before[i][0] for i in src]) for j, src in d["merges"]]
        rec["renames"] = [(before[i][0], after[j][0]) for i, j in d["renames"]]
        self.cur = after
        self.records.append(rec)
        flag = "CLEAN" if not probs else "PROBLEMS: " + " | ".join(probs)
        hzs = (" HAZARDS: " + " | ".join(hz)) if hz else ""
        gv = (" GUI-VIEW: " + " | ".join(gui_probs)) if gui_probs else ""
        self.log(f"  [{self.impl}] {tag}: {st} ({dt:.2f}s) -> {flag}{hzs}{gv}")
        if st != "OK":
            self.log(f"        msg: {msg[:600]}")
        return rec
