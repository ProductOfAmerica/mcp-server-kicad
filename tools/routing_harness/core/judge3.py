"""Shared judge for the coarse-variant experiment (M vs K). One judge for every sample.

Readings:
  cli  = the netlist kicad-cli exports for the file as written. The kicad-cli is whatever
         mcp_server_kicad._shared._find_kicad_cli() resolves (run.json records its version).
  gui  = NOT AVAILABLE in this repository. It was the same project after an emulation of KiCad's
         GUI load-time cleanup was applied to every .kicad_sch, then kicad-cli; that emulation is a
         port of KiCad's GPL C++ source and is not copied here (core/gui_reading.py is a stub and
         the decision to add one is the maintainer's). Wherever a result holds a gui entry it is
         the dict {"wrong": None, "unavailable": True, "error": ...}: wrong is None (never
         False), and the dict has no "delivered" key, so it can never turn a NOOP into a false
         no-op. Results also carry "readings": the list of readings actually taken.

A write is WRONG in a reading if, compared with the same reading of the file before the call:
  bad_merges   a net joins another net outside the requested groups;
  splits       a net's nodes end up in two or more nets;
  renames      a net outside the requested groups keeps its node set but changes name
               (node sets listed twice, e.g. pad copies on two unconnected nets, are skipped);
  class_bad    a node's net class changes, unless the node is on a requested join and its new
               class is one of the classes the joined nets had before;
  pads_multi   a (ref, pin) node is listed in more nets than before (pad in two nets);
  vanished / appeared  a node disappears or appears (unloadable halves, truncation);
  unloadable   kicad-cli cannot export the file at all.
  named_merge  (follow-up 2) an after-net joins two or more before-nets that each carried a user
               name (any name that is not KiCad's auto name Net-(..)/unconnected-(.., tested on
               the raw name), even inside the requested union (closes the HIER-06 +5V-into-GND
               blind spot);
  req_pad_multi (follow-up 2) a requested pad is in two or more nets after a write; delivery
               requires each requested pad in exactly one net (closes the pad-in-two-nets blind
               spot). "wrong_j3" keeps the follow-up 1 verdict for comparison.

DELIVERY. connect_pins: both pins end in one net. wire_pins_to_net: use snapshot2/assess2. They
place a PROBE (a Connector:TestPoint far off-page with a local label N on its pin end) in a
scratch copy; a local label joins every strong item named N on its sheet (digest N01), so the
nodes sharing a net with the probe are exactly the nodes on N in that reading, whatever name
KiCad prints (two-name nets, names containing '/'). The requested merge with N's nets is
judged against that probe set too. A NOOP that is not delivered in a reading is a FALSE NO-OP.

Usage (wire_pins_to_net):
    snap = snapshot2(root, edited, N="N")
    st, msg = call(K.wire_pins_to_net, pins=[...], label_text="N", schematic_path=edited)
    res = assess2(snap, root, [ {all copies of pad 1}, ... ], edited_path=edited, status=st)
Usage (connect_pins):
    snap = snapshot2(root, edited)            # N=None
    res = assess2(snap, root, [{(r1, p1), (r2, p2)}], edited_path=edited, status=st)
res["cli"]["wrong"], res["gui"]["wrong"], res["cli"]["delivered"], res["false_noop"],
res["pre_satisfied"], res["redundant_write"] (wire_pins_to_net only).
The older snapshot/assess (name-based delivery) are kept for reference only.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import uuid as _u
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import hpaths  # noqa: E402,F401  (repo under test, core/, protos/ on sys.path)

from gui_reading import AVAILABLE as GUI_AVAILABLE  # noqa: E402
from gui_reading import REASON, GuiReadingUnavailable, gui_load_proxy  # noqa: E402,F401
from harness import _run_cli  # noqa: E402
from mcp_server_kicad import _cst as _CST  # noqa: E402
from mcp_server_kicad import schematic as _SCH  # noqa: E402
from mcp_server_kicad._shared import _sheet_file_cst  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="j3_"))

# The readings this process can take (core/readings.py computes the same tuple without this module).
READINGS = ("cli", "gui") if GUI_AVAILABLE else ("cli",)
_GUI_MISSING = {"wrong": None, "unavailable": True, "error": "gui reading not available: " + REASON}


def nets_full(path):
    """[(code, name, cls, frozenset((ref, pin)))] from kicad-cli kicadxml, or None."""
    path = str(path)
    out = str(TMP / (Path(path).stem + f".{os.getpid()}.{_u.uuid4().hex[:8]}.xml"))
    _run_cli(["sch", "export", "netlist", "--format", "kicadxml", "--output", out, path],
             check=False)
    if not os.path.exists(out):
        return None
    res = []
    for net in ET.parse(out).getroot().iter("net"):
        res.append((net.get("code", ""), net.get("name", ""), net.get("class", "") or "",
                    frozenset((n.get("ref", ""), n.get("pin", "")) for n in net.findall("node"))))
    os.remove(out)
    return res


def gui_tree_copy(root_path) -> Path:
    """Copy the root's directory tree and apply the GUI load proxy to every .kicad_sch in it.
    Raises GuiReadingUnavailable, before copying anything, when the reading is not available."""
    if not GUI_AVAILABLE:
        raise GuiReadingUnavailable(REASON)
    root_path = Path(root_path)
    dst = Path(tempfile.mkdtemp(prefix="gui_", dir=TMP))
    shutil.copytree(root_path.parent, dst / "p", dirs_exist_ok=True)
    for sch in (dst / "p").rglob("*.kicad_sch"):
        tmp = sch.with_suffix(".gui_tmp")
        gui_load_proxy(sch, tmp)
        os.replace(tmp, sch)
    return dst / "p" / root_path.name


def _strip(name: str) -> str:
    return name.rsplit("/", 1)[-1] if "/" in name else name


_AUTO = re.compile(r"(^|/)(Net|unconnected)-\(")


def _user_name(name: str) -> bool:
    """A net name a user gave (label, power symbol, hidden pin), not KiCad's auto name."""
    return bool(name) and not _AUTO.search(name)


def _index(nets):
    where = {}
    for i, (_c, _n, _k, v) in enumerate(nets):
        for node in v:
            where.setdefault(node, []).append(i)
    return where


def judge(before, after, groups, N=None, n_nodes=None, n_after=None, status=None):
    if after is None:
        return {"wrong": True, "unloadable": True, "delivered": False}
    if before is None:
        return {"wrong": None, "note": "before unloadable"}
    bw, aw = _index(before), _index(after)
    bnet = {n: ix[0] for n, ix in bw.items()}
    anet = {n: ix[0] for n, ix in aw.items()}
    allowed = []
    for g in groups:
        s = {bnet[n] for n in g if n in bnet}
        if N is not None:
            s |= {i for i, (_c, nm, _k, _v) in enumerate(before) if _strip(nm) == N}
        if n_nodes:
            s |= {bnet[n] for n in n_nodes if n in bnet}
        allowed.append(frozenset(s))
    if (N is not None or n_nodes is not None or n_after is not None) and len(allowed) > 1:
        # wire_pins_to_net joins EVERY requested pad and N into one net: the requested merge is
        # the union of all groups (fix 2026-10-02; per-group sets flagged that join as foreign).
        allowed = [frozenset().union(*allowed)]
    targets = set().union(*allowed) if allowed else set()
    bad_merges = []
    for _c, name, _k, v in after:
        src = frozenset(bnet[n] for n in v if n in bnet)
        if len(src) > 1 and not any(src <= a for a in allowed):
            bad_merges.append((name, sorted(before[i][1] for i in src)))
    splits = []
    for i, (_c, name, _k, v) in enumerate(before):
        dst = {anet[n] for n in v if n in anet}
        if len(dst) > 1:
            splits.append((name, sorted(after[j][1] for j in dst)))
    renames = []
    bcount = Counter(v for _c, _n, _k, v in before if v)
    acount = Counter(v for _c, _n, _k, v in after if v)
    bsets = {v: (i, name) for i, (_c, name, _k, v) in enumerate(before) if v and bcount[v] == 1}
    for _c, name, _k, v in after:
        if acount[v] != 1:
            continue
        if v in bsets and bsets[v][1] != name and bsets[v][0] not in targets:
            renames.append((bsets[v][1], name))
    class_bad = []
    for node, j in anet.items():
        if node not in bnet:
            continue
        i = bnet[node]
        bc, ac = before[i][2], after[j][2]
        if bc == ac:
            continue
        ok = any(i in a and ac in {before[k][2] for k in a} for a in allowed)
        if not ok:
            class_bad.append((node, bc, ac))
    pads_multi = sorted(n for n, ix in aw.items() if len(ix) > len(bw.get(n, [None])))
    vanished = sorted(set(bnet) - set(anet))
    appeared = sorted(set(anet) - set(bnet))
    delivered = []
    if n_after is not None:
        delivered = [set(g) <= n_after for g in groups]
    else:
        for g in groups:
            nets_of_g = {anet.get(n) for n in g}
            ok = len(nets_of_g) == 1 and None not in nets_of_g
            if ok and N is not None:
                j = next(iter(nets_of_g))
                nm = after[j][1]
                ok = _strip(nm) == N or any(
                    _strip(before[bnet[n]][1]) == N for n in after[j][3] if n in bnet)
            delivered.append(ok)
    wrong_j3 = bool(bad_merges or splits or renames or class_bad or pads_multi or vanished
                    or appeared)
    # Follow-up 2 additions (the two judge3-blind classes of follow-up 1):
    # named_merge: one after-net holds two or more before-nets that carried DIFFERENT user names
    #   (auto names Net-(...)/unconnected-(...) excluded, sheet path stripped). The requested
    #   union does not excuse it: joining +5V into GND is never what "put pin on N" means.
    # Fix 2026-10-03: the auto-name test runs on the RAW name and user-named nets are counted, not
    # their path-stripped texts. Stripping first cut "unconnected-(U1-PB7/PB8-Pad1)" to
    # "PB8-Pad1)", which then counted as a user name (false positive, found in the llm-workflow
    # batch), and collapsed "/A/X" with "/B/X" (false negative).
    named_merge = []
    for _c, name, _k, v in after:
        src = {bnet[n] for n in v if n in bnet}
        if len(src) > 1:
            users = [before[i][1] for i in sorted(src) if _user_name(before[i][1])]
            if len(users) > 1:
                named_merge.append((name, sorted(users)))
    # req_pad_multi: a requested (ref, pin) node is listed in two or more nets after the call.
    #   Delivery now requires every requested node in exactly one net, so a NOOP on such a pad is a
    #   false no-op; a write that leaves one is wrong (status OK, or status None and the netlist
    #   changed).
    req = set().union(*[set(g) for g in groups]) if groups else set()
    req_pad_multi = sorted(n for n in req if len(aw.get(n, [])) > 1)
    changed = sorted((x[1], x[3]) for x in before) != sorted((x[1], x[3]) for x in after)
    wrote = status == "OK" or (status is None and changed)
    delivered = [d and all(len(aw.get(n, [])) == 1 for n in g) for d, g in zip(delivered, groups)]
    wrong = bool(wrong_j3 or named_merge or (wrote and req_pad_multi))
    return {"wrong": wrong, "wrong_j3": wrong_j3, "bad_merges": bad_merges, "splits": splits,
            "renames": renames, "class_bad": class_bad, "pads_multi": pads_multi,
            "vanished": vanished, "appeared": appeared, "named_merge": named_merge,
            "req_pad_multi": req_pad_multi if wrote else [],
            "req_pad_multi_seen": req_pad_multi,
            "delivered": all(delivered), "delivered_each": delivered}


def snapshot(root_path, gui=True):
    """Before-readings of a project (root path). Name-based; prefer snapshot2."""
    snap = {"cli": nets_full(root_path)}
    if gui and not GUI_AVAILABLE:
        snap["gui"] = None
        snap["gui_error"] = _GUI_MISSING["error"]
    elif gui:
        try:
            snap["gui"] = nets_full(gui_tree_copy(root_path))
        except Exception as e:  # noqa: BLE001
            snap["gui"] = None
            snap["gui_error"] = repr(e)
    return snap


def assess(snap, root_path, groups, N=None, status=None, gui=True):
    """Name-based delivery (kept for reference; prefer assess2)."""
    res = {"cli": judge(snap.get("cli"), nets_full(root_path), groups, N)}
    if gui and not GUI_AVAILABLE:
        res["gui"] = dict(_GUI_MISSING)
    elif gui:
        try:
            res["gui"] = judge(snap.get("gui"), nets_full(gui_tree_copy(root_path)), groups, N)
        except Exception as e:  # noqa: BLE001
            res["gui"] = {"wrong": None, "error": repr(e)}
    res["false_noop"] = status == "NOOP" and not all(
        r.get("delivered", True) for r in (res["cli"], res.get("gui") or {}) if r)
    res["readings"] = list(READINGS if gui else ("cli",))
    return res


def call(fn, **kw):
    """(status, message): OK, NOOP (message starts 'No change'), REFUSED, ERROR."""
    try:
        msg = fn(**kw)
    except Exception as e:  # noqa: BLE001
        cls = type(e).__name__
        return ("REFUSED" if cls in ("ToolError", "_Refuse") else "ERROR"), f"{cls}: {e}"
    text = str(msg)
    if text.lower().startswith("no change"):
        return "NOOP", text
    return "OK", text


# ------------------------------------------------------------------ probe-based "is it on N"


def _uuid_of(root):
    u = root.find("uuid")
    return u.atoms[1].text if u is not None else ""


def instance_paths(root_file, target_file):
    """Every hierarchical instance path ('/root/sheet/...') under which target_file is used."""
    root_file, target_file = Path(root_file).resolve(), Path(target_file).resolve()
    out = []

    def walk(f, path, depth=0):
        if depth > 8 or not f.exists():
            return
        if f.resolve() == target_file:
            out.append(path)
        r = _CST.parse(f.read_bytes()).lists[0]
        for sh in r.find_all("sheet"):
            name = _sheet_file_cst(sh)
            if not name:
                continue
            child = f.parent / name
            if not child.exists():
                child = root_file.parent / name
            su = sh.find("uuid")
            walk(child, f"{path}/{su.atoms[1].text if su is not None else ''}", depth + 1)

    walk(root_file, "/" + _uuid_of(_CST.parse(root_file.read_bytes()).lists[0]))
    return out or ["/" + _uuid_of(_CST.parse(target_file.read_bytes()).lists[0])]


def _add_probe(croot, cedit, N):
    tree = _CST.parse(Path(cedit).read_bytes())
    root = tree.lists[0]
    if _SCH._find_lib_symbol_cst(root, "Connector:TestPoint") is None:
        if not _SCH._copy_system_lib_symbol_cst(root, "Connector", "TestPoint"):
            raise RuntimeError("Connector:TestPoint not found in the system library")
    lib = _SCH._find_lib_symbol_cst(root, "Connector:TestPoint")
    node = _SCH._SYMBOL_TPL.copy()
    node.find("lib_name").atoms[1].set_text(lib.atoms[1].text)
    node.find("lib_id").atoms[1].set_text("Connector:TestPoint")
    _SCH._fill_at(node, -200.0, -200.0, 0)
    node.find("uuid").atoms[1].set_text(str(_u.uuid4()))
    props = node.find_all("property")
    props[0].atoms[2].set_text("TP900")
    props[1].atoms[2].set_text("TP")
    inst = node.find("instances")
    pin = _SCH._PIN_REF_TPL.copy()
    pin.find("uuid").atoms[1].set_text(str(_u.uuid4()))
    node.insert_before(inst, pin)
    proj = inst.find("project")
    proj.atoms[1].set_text(Path(croot).stem)
    first = proj.find("path")
    refs = []
    for k, ip in enumerate(instance_paths(croot, cedit)):
        ref = f"TP9{k:02d}"
        refs.append(ref)
        pnode = first if k == 0 else first.copy()
        pnode.atoms[1].set_text(ip)
        pnode.find("reference").atoms[1].set_text(ref)
        if k > 0:
            proj.insert_after(proj.find_all("path")[-1], pnode)
    _SCH._splice_sch_node(root, "symbol", node)
    Path(cedit).write_bytes(_CST.serialize(tree))
    import mine_impl as _MI

    x, y, _o = _MI.pin_positions(str(cedit), "TP900")["1"][0]
    tree = _CST.parse(Path(cedit).read_bytes())
    root = tree.lists[0]
    lab = _SCH._LABEL_TPL.copy()
    lab.atoms[1].set_text(N)
    _SCH._fill_at(lab, x, y, 0)
    lab.find("uuid").atoms[1].set_text(str(_u.uuid4()))
    _SCH._splice_sch_node(root, "label", lab)
    Path(cedit).write_bytes(_CST.serialize(tree))
    return refs


def _nodes_with(nets, refs):
    if nets is None:
        return None
    out = set()
    for _c, _n, _k, v in nets:
        if any((r, "1") in v for r in refs):
            out |= {n for n in v if not n[0].startswith("TP9")}
    return out


def probe_on_N(root_path, edited_path, N, gui=True):
    """{'cli': nodes on N, 'gui': nodes on N} for the project as it is on disk now."""
    root_path = Path(root_path)
    edited_path = Path(edited_path or root_path)
    rel = edited_path.resolve().relative_to(root_path.parent.resolve())
    dst = Path(tempfile.mkdtemp(prefix="probe_", dir=TMP))
    shutil.copytree(root_path.parent, dst / "p", dirs_exist_ok=True)
    croot, cedit = dst / "p" / root_path.name, dst / "p" / rel
    refs = _add_probe(croot, cedit, N)
    res = {"cli": _nodes_with(nets_full(croot), refs)}
    if gui and not GUI_AVAILABLE:
        res["gui"] = None
        res["gui_error"] = _GUI_MISSING["error"]
    elif gui:
        try:
            res["gui"] = _nodes_with(nets_full(gui_tree_copy(croot)), refs)
        except Exception as e:  # noqa: BLE001
            res["gui"] = None
            res["gui_error"] = repr(e)
    return res


def snapshot2(root_path, edited_path=None, N=None, gui=True):
    """snapshot() plus, when N is given, the nodes on N before the call (probe)."""
    snap = snapshot(root_path, gui=gui)
    if N is not None:
        snap["onN"] = probe_on_N(root_path, edited_path, N, gui=gui)
    snap["N"] = N
    return snap


def assess2(snap, root_path, groups, edited_path=None, status=None, gui=True):
    """Probe-based assessment. Adds pre_satisfied (request already met before the call in both
    readings) and redundant_write (an OK write although pre_satisfied) for wire_pins_to_net."""
    N = snap.get("N")
    after_onN = probe_on_N(root_path, edited_path, N, gui=gui) if N is not None else {}
    res = {}
    for rd in (("cli", "gui") if gui else ("cli",)):
        if rd == "gui" and not GUI_AVAILABLE:
            res[rd] = dict(_GUI_MISSING)
            continue
        try:
            after = nets_full(root_path) if rd == "cli" else nets_full(gui_tree_copy(root_path))
        except Exception as e:  # noqa: BLE001
            res[rd] = {"wrong": None, "error": repr(e)}
            continue
        if N is not None:
            res[rd] = judge(snap.get(rd), after, groups, None,
                            n_nodes=(snap.get("onN") or {}).get(rd),
                            n_after=after_onN.get(rd) or set(), status=status)
        else:
            res[rd] = judge(snap.get(rd), after, groups, None, status=status)
    res["false_noop"] = status == "NOOP" and not all(
        r.get("delivered", True) for r in (res["cli"], res.get("gui") or {}) if r)
    if N is not None:
        pre = [all(set(g) <= (((snap.get("onN") or {}).get(rd)) or set()) for g in groups)
               for rd in (READINGS if gui else ("cli",))]
        res["pre_satisfied"] = all(pre)
        res["redundant_write"] = status == "OK" and res["pre_satisfied"]
    res["readings"] = list(READINGS if gui else ("cli",))
    return res


# Nothing judges unless the package under test is the one hpaths selected (PT_REPO, else the
# checkout holding this harness): fail loudly if anything resolved another tree.
hpaths.assert_target(_CST.__file__)
