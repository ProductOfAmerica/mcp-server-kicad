"""Shared measurement harness for the routing pressure test.

Import it from a script that has bootstrapped hpaths (see hpaths.py):

    from harness import *

The repository under test (PT_REPO, else the checkout that holds this harness) and its tests/
directory are put on sys.path by hpaths, which this module imports.

KiCad (the kicad-cli the package resolves; run.json records its version) decides connectivity.
Everything here writes raw nodes with NO auto-junction, so the geometry is exactly what you ask for.
Gotcha: kicad-cli drops unconnected pins of a symbol with no (instances ...)
block; place() goes through place_component, which writes one (the frozen 54160c1 copy in
compat54, the code the baselines were taken with).
"""

import os
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import hpaths  # noqa: E402,F401  (repo under test and its tests/ on sys.path)

import compat54  # noqa: E402
import conftest  # noqa: E402
from mcp_server_kicad import _cst, project, schematic  # noqa: E402
from mcp_server_kicad._shared import _run_cli  # noqa: E402

BASE = Path(tempfile.mkdtemp(prefix="pt_"))


def fresh(name: str) -> str:
    """New empty schematic in its own temp dir; returns its path."""
    d = BASE / name
    d.mkdir(parents=True)
    p = str(d / f"{name}.kicad_sch")
    project.create_schematic(schematic_path=p)
    return p


def place(p, lib, ref, x, y, rot=0, mirror="", value=None):
    """place_component; snaps the origin to 1.27 mm. For power symbols pass value= (the net is
    named by Value, and value defaults to the reference here)."""
    return compat54.place_component(
        lib_id=lib, reference=ref, value=value or ref, x=x, y=y, rotation=rot,
        mirror=mirror, schematic_path=p,
    )


def pin(p, ref, num):
    """(x, y, outward_angle) as the repo computes it."""
    return schematic._get_pin_pos_cst(_cst.parse(open(p, "rb").read()).lists[0], ref, num)


def _edit(p, fn):
    tree = _cst.parse(open(p, "rb").read())
    fn(tree.lists[0])
    open(p, "wb").write(_cst.serialize(tree))


def splice(p, token: str, sexpr: bytes):
    """Insert an arbitrary node (fresh uuid if it has one) after its same-token siblings."""
    node = _cst.parse(sexpr).lists[0]
    u = node.find("uuid")
    if u is not None:
        u.atoms[1].set_text(str(uuid.uuid4()))
    _edit(p, lambda root: schematic._splice_sch_node(root, token, node))


def _n(v):
    """Exact 4-decimal formatting (KiCad IU = 0.0001 mm). The old :g format dropped digits >= 100 mm."""
    s = f"{round(v, 4):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def wire(p, x1, y1, x2, y2):
    """Raw wire, no auto-junction."""
    splice(p, "wire", f"(wire (pts (xy {_n(x1)} {_n(y1)}) (xy {_n(x2)} {_n(y2)})) "
           f"(stroke (width 0) (type default)) (uuid \"x\"))".encode())


def bus(p, x1, y1, x2, y2):
    splice(p, "bus", f"(bus (pts (xy {_n(x1)} {_n(y1)}) (xy {_n(x2)} {_n(y2)})) "
           f"(stroke (width 0) (type default)) (uuid \"x\"))".encode())


def bus_entry(p, x, y, dx=2.54, dy=2.54):
    """Bus entry with ends (x, y) and (x+dx, y+dy). The ends are symmetric: KiCad decides the
    bus side geometrically (whichever end has a bus under it)."""
    splice(p, "bus_entry", f"(bus_entry (at {_n(x)} {_n(y)}) (size {_n(dx)} {_n(dy)}) "
           f"(stroke (width 0) (type default)) (uuid \"x\"))".encode())


def junction(p, x, y):
    splice(p, "junction", f"(junction (at {_n(x)} {_n(y)}) (diameter 0) (color 0 0 0 0) "
           f"(uuid \"x\"))".encode())


def no_connect(p, x, y):
    splice(p, "no_connect", f"(no_connect (at {_n(x)} {_n(y)}) (uuid \"x\"))".encode())


def label(p, text, x, y, rot=0, kind="label", shape="input"):
    """kind: label | global_label | hierarchical_label."""
    shape_s = f"(shape {shape}) " if kind != "label" else ""
    splice(p, kind, f"({kind} \"{text}\" {shape_s}(at {_n(x)} {_n(y)} {rot}) "
           f"(effects (font (size 1.27 1.27))) (uuid \"x\"))".encode())


def directive(p, netclass, x, y, rot=0):
    """Directive label (netclass_flag), shape harvested from royalblue54L_feather."""
    splice(p, "netclass_flag", f"(netclass_flag \"\" (length 2.54) (shape round) "
           f"(at {_n(x)} {_n(y)} {rot}) (effects (font (size 1.27 1.27)) (justify left bottom)) "
           f"(uuid \"x\") (property \"Netclass\" \"{netclass}\" (at {_n(x)} {_n(y)} 0) "
           f"(effects (font (size 1.27 1.27) (italic yes)) (justify left))))".encode())


def nets_raw(p):
    """KiCad's netlist as {raw net name: frozenset((ref, pin))}, names NOT normalised."""
    p = str(p)
    out = p + ".pt.netlist.xml"
    if os.path.exists(out):
        os.remove(out)
    r = _run_cli(["sch", "export", "netlist", "--format", "kicadxml", "--output", out, p], check=False)
    if not os.path.exists(out):
        raise RuntimeError(f"netlist export failed rc={r.returncode}: {r.stderr.strip()}")
    res = {}
    for net in ET.parse(out).getroot().iter("net"):
        res[net.get("name", "")] = frozenset(
            (n.get("ref", ""), n.get("pin", "")) for n in net.findall("node")
        )
    return res


def nets(p):
    """conftest's oracle: {name without leading '/': {(ref, pin): pintype}}."""
    return conftest.netlist_nodes(p)


def partition(n):
    """Name-blind partition of pins into nets (sets with at least one node)."""
    return {v for v in n.values() if v}


def net_of(n, node):
    return [k for k, v in n.items() if node in v]


def same_net(n, a, b):
    return any(a in v and b in v for v in n.values())


def merges(before, after):
    """Nets in AFTER that contain pins from two or more BEFORE nets.

    Returns [(after_name, [before_names...])]. Both args are nets_raw() results.
    """
    where = {node: name for name, v in before.items() for node in v}
    out = []
    for name, v in after.items():
        src = sorted({where[n] for n in v if n in where})
        if len(src) > 1:
            out.append((name, src))
    return out


def call(fn, **kw):
    """Run a tool; return its message, or 'REFUSED: ...' if it raised."""
    try:
        return fn(**kw)
    except Exception as e:  # noqa: BLE001
        return f"REFUSED: {type(e).__name__}: {e}"


def sha(p):
    import hashlib

    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]


def nets_coded(p):
    """KiCad's netlist as a list of (code, raw name, frozenset((ref, pin))).

    kicadxml prints unescaped names, so two distinct nets can share a printed name; key by node
    set or code, never by name alone.
    """
    p = str(p)
    out = p + ".pt.netlist.xml"
    if os.path.exists(out):
        os.remove(out)
    r = _run_cli(["sch", "export", "netlist", "--format", "kicadxml", "--output", out, p], check=False)
    if not os.path.exists(out):
        raise RuntimeError(f"netlist export failed rc={r.returncode}: {r.stderr.strip()}")
    res = []
    for net in ET.parse(out).getroot().iter("net"):
        res.append((net.get("code", ""), net.get("name", ""), frozenset(
            (n.get("ref", ""), n.get("pin", "")) for n in net.findall("node"))))
    return res


def diff(before, after):
    """Compare two nets_coded() results by node sets.

    Returns {"merges": [(after_name, [before_names])], "splits": [(before_name, [after_names])],
    "renames": [(before_name, after_name)] for unchanged node sets whose name changed,
    "appeared": pins absent before, "vanished": pins absent after}.
    """
    bwhere = {n: name for _, name, v in before for n in v}
    awhere = {n: name for _, name, v in after for n in v}
    merges, splits, renames = [], [], []
    for _, name, v in after:
        src = sorted({bwhere[n] for n in v if n in bwhere})
        if len(src) > 1:
            merges.append((name, src))
    for _, name, v in before:
        dst = sorted({awhere[n] for n in v if n in awhere})
        if len(dst) > 1:
            splits.append((name, dst))
    bsets = {v: name for _, name, v in before if v}
    for _, name, v in after:
        if v in bsets and bsets[v] != name:
            renames.append((bsets[v], name))
    return {
        "merges": merges, "splits": splits, "renames": renames,
        "appeared": sorted(set(awhere) - set(bwhere)), "vanished": sorted(set(bwhere) - set(awhere)),
    }
