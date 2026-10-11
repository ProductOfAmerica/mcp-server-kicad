# Copied from pr57pt/sem-hierarchy/hb.py; RUNS moved under the run's probeB output directory.
"""Hierarchy builder for the sem-hierarchy slice.

Builds small multi-sheet projects with exact control over UUIDs and per-instance
symbol references, then lets kicad-cli 9.0.8 decide connectivity on the ROOT.

Symbols are placed with the repo's place_component (via harness.place) so the
lib_symbols are verbatim stock-library copies; their (instances ...) block is then
rewritten to KiCad's native per-instance form: (path "/<root>/<sheet>" (reference ..)).
Sheets are written in KiCad 9's native shape (harvested from the multichannel demo).
"""

import json
import sys
import uuid as _uuid
from pathlib import Path

import hpaths  # noqa: E402  (core/harness.py is on sys.path through hpaths)
from harness import (  # noqa: E402,F401
    _cst, _edit, _n, junction, label, merges, nets_raw, pin, place, project, schematic, wire,
)

RUNS = hpaths.suite_out("probeB") / "runs"


def U():
    return str(_uuid.uuid4())


def rsplice(p, token, text):
    """Splice a raw node, keeping every uuid exactly as written."""
    node = _cst.parse(text.encode()).lists[0]
    _edit(p, lambda root: schematic._splice_sch_node(root, token, node))


def file_uuid(p):
    return _cst.parse(open(p, "rb").read()).lists[0].find("uuid").atoms[1].text


class Proj:
    def __init__(self, name, text_vars=None):
        self.name = name
        self.dir = RUNS / name
        if self.dir.exists():
            import shutil

            shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True)
        pro = {"meta": {"filename": f"{name}.kicad_pro", "version": 1}}
        if text_vars:
            pro["text_variables"] = text_vars
        (self.dir / f"{name}.kicad_pro").write_text(json.dumps(pro, indent=2) + "\n")
        self.root = str(self.dir / f"{name}.kicad_sch")
        project.create_schematic(schematic_path=self.root)
        self.root_uuid = file_uuid(self.root)
        self.page = 1

    def child(self, fname):
        p = str(self.dir / fname)
        project.create_schematic(schematic_path=p)
        return p

    def sheet(self, parent, sheet_name, fname, x, y, w=30.48, h=20.32, pins=(), parent_path=None):
        """Add a sheet symbol. pins: [(name, shape, side, k)] -> pin at edge, y + k*2.54.

        Returns (sheet_uuid, {pin_name: (px, py)}).
        """
        su = U()
        self.page += 1
        ppath = parent_path or f"/{self.root_uuid}"
        pin_nodes, where = [], {}
        for name, shape, side, k in pins:
            px = x if side == "left" else x + w
            py = round(y + k * 2.54, 4)
            ang = 180 if side == "left" else 0
            just = "left" if side == "left" else "right"
            where[name] = (px, py)
            pin_nodes.append(
                f'(pin "{name}" {shape} (at {_n(px)} {_n(py)} {ang}) (uuid "{U()}")'
                f" (effects (font (size 1.27 1.27)) (justify {just})))"
            )
        text = (
            f"(sheet (at {_n(x)} {_n(y)}) (size {_n(w)} {_n(h)}) (exclude_from_sim no) (in_bom yes)"
            f" (on_board yes) (dnp no) (fields_autoplaced yes)"
            f" (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0000))"
            f' (uuid "{su}")'
            f' (property "Sheetname" "{sheet_name}" (at {_n(x)} {_n(y - 0.7116)} 0)'
            f" (effects (font (size 1.27 1.27)) (justify left bottom)))"
            f' (property "Sheetfile" "{fname}" (at {_n(x)} {_n(y + h + 0.5846)} 0)'
            f" (effects (font (size 1.27 1.27)) (justify left top)))"
            + " ".join(pin_nodes)
            + f' (instances (project "{self.name}" (path "{ppath}" (page "{self.page}")))))'
        )
        rsplice(parent, "sheet", text)
        return su, where

    def sym(self, p, lib, refs, x, y, rot=0, value=None, paths=None):
        """Place a symbol; refs is one ref (root) or a list paired with paths (child instances)."""
        if isinstance(refs, str):
            refs = [refs]
        place(p, lib, refs[0], x, y, rot=rot, value=value)
        if paths is None:
            paths = [f"/{self.root_uuid}"]
        assert len(paths) == len(refs), (paths, refs)
        body = " ".join(
            f'(path "{pp}" (reference "{r}") (unit 1))' for pp, r in zip(paths, refs)
        )
        new = _cst.parse(f'(instances (project "{self.name}" {body}))'.encode()).lists[0]

        def fix(root):
            for s in root.find_all("symbol"):
                if schematic._sym_property_cst(s, "Reference") == refs[0]:
                    inst = s.find("instances")
                    s.insert_after(inst, new)
                    s.remove_child(inst)
                    return
            raise KeyError(refs[0])

        _edit(p, fix)

    def nets(self):
        return nets_raw(self.root)


def nets_coded(p):
    """[(code, name, frozenset(nodes))] straight from kicadxml, keyed by CODE.

    kicadxml writes UnescapeString(name), so two distinct nets can share a printed
    name (e.g. '/Sub{slash}X' and '/Sub/X' both print '/Sub/X'); nets_raw() keys by
    name and silently collapses them.
    """
    import os
    import xml.etree.ElementTree as ET

    from harness import _run_cli

    p = str(p)
    out = p + ".coded.netlist.xml"
    if os.path.exists(out):
        os.remove(out)
    r = _run_cli(["sch", "export", "netlist", "--format", "kicadxml", "--output", out, p], check=False)
    if not os.path.exists(out):
        raise RuntimeError(f"netlist export failed rc={r.returncode}: {r.stderr.strip()}")
    res = []
    for net in ET.parse(out).getroot().iter("net"):
        res.append((net.get("code"), net.get("name", ""),
                    frozenset((nd.get("ref", ""), nd.get("pin", "")) for nd in net.findall("node"))))
    return res


def same_c(coded, a, b):
    return any(a in v and b in v for _, _, v in coded)


def show(n, refs=None, title=""):
    """Print nets touching any of refs (all nets if refs is None)."""
    if title:
        print(f"--- {title}")
    for name in sorted(n):
        nodes = sorted(n[name])
        if refs is None or any(r in refs for r, _ in nodes):
            print(f"    {name!r:40} {nodes}")


def net_of(n, node):
    return [k for k, v in n.items() if node in v]


def same(n, a, b):
    return any(a in v and b in v for v in n.values())
