"""probeB shim for probe-hierarchy/hlib.py: the builder (Sch, Proj, lib_symbol) copied unchanged,
RUNS redirected under this process's probeB temp dir, and run()/run_seq() replaced by rlib (designs
M2, H, Hb and E, judge3 on the ROOT). Only variants casemap names for the current script run."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

sys.dont_write_bytecode = True
import hpaths  # noqa: E402
import rlib  # noqa: E402

HERE = rlib.OUT / "logs"
RUNS = rlib.PROC_TMP / "hruns"
RUNS.mkdir(parents=True, exist_ok=True)

from mcp_server_kicad import schematic as B  # noqa: E402,F401
from mcp_server_kicad._shared import _run_cli  # noqa: E402

M2 = rlib.M2
H = rlib.H
Hb = rlib.Hb
E = rlib.E
M = M2  # scripts that read pin positions through M get M2's (same transform as mine_impl)
DESIGNS = dict(rlib.MODS)

KROOT = hpaths.KICAD_ROOT
SYMDIR = hpaths.KICAD_SYMBOLS
DEMOS = hpaths.KICAD_DEMOS


def U() -> str:
    return str(uuid.uuid4())


def n(v) -> str:
    s = f"{round(float(v), 4):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _close(text, start):
    depth, i, in_str = 0, start, False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced")


_LIB: dict = {}


def lib_symbol(lib, name):
    key = (lib, name)
    if key not in _LIB:
        text = (SYMDIR / f"{lib}.kicad_sym").read_text(encoding="utf-8")
        m = re.search(r'^\t\(symbol "' + re.escape(name) + r'"\s*$', text, re.M)
        s = m.start() + 1
        body = text[s:_close(text, s)]
        assert "(extends" not in body, (lib, name)
        _LIB[key] = body.replace(f'(symbol "{name}"', f'(symbol "{lib}:{name}"', 1)
    return _LIB[key]


EFF = "(effects (font (size 1.27 1.27)))"
EFFH = "(effects (font (size 1.27 1.27)) (hide yes))"


class Sch:
    def __init__(self, proj, fname, is_root=False):
        self.proj, self.fname, self.is_root = proj, fname, is_root
        self.path = str(proj.dir / fname)
        self.uuid = U()
        self.items: list = []
        self.libs: dict = {}
        self.inst_paths = [f"/{self.uuid}"] if is_root else []

    def wire(self, x1, y1, x2, y2):
        self.items.append(("wire", f"(wire (pts (xy {n(x1)} {n(y1)}) (xy {n(x2)} {n(y2)})) "
                                   f"(stroke (width 0) (type default)) (uuid \"{U()}\"))"))

    def junction(self, x, y):
        self.items.append(("junction", f"(junction (at {n(x)} {n(y)}) (diameter 0) "
                                       f"(color 0 0 0 0) (uuid \"{U()}\"))"))

    def no_connect(self, x, y):
        self.items.append(("no_connect", f"(no_connect (at {n(x)} {n(y)}) (uuid \"{U()}\"))"))

    def label(self, text, x, y, kind="label", rot=0, shape="input"):
        if kind == "label":
            t = (f"(label \"{text}\" (at {n(x)} {n(y)} {rot}) "
                 f"(effects (font (size 1.27 1.27)) (justify left bottom)) (uuid \"{U()}\"))")
        elif kind == "global_label":
            t = (f"(global_label \"{text}\" (shape {shape}) (at {n(x)} {n(y)} {rot}) "
                 f"(fields_autoplaced yes) (effects (font (size 1.27 1.27)) (justify left)) "
                 f"(uuid \"{U()}\") (property \"Intersheetrefs\" \"${{INTERSHEET_REFS}}\" "
                 f"(at {n(x)} {n(y)} 0) (effects (font (size 1.27 1.27)) (justify left) (hide yes))))")
        elif kind == "hierarchical_label":
            t = (f"(hierarchical_label \"{text}\" (shape {shape}) (at {n(x)} {n(y)} {rot}) "
                 f"(effects (font (size 1.27 1.27)) (justify left)) (uuid \"{U()}\"))")
        else:
            raise ValueError(kind)
        self.items.append((kind, t))

    def symbol(self, lib, name, refs, x, y, value, rot=0, pins=("1",), unit=1):
        lib_id = f"{lib}:{name}"
        self.libs[lib_id] = lib_symbol(lib, name)
        if isinstance(refs, str):
            refs = [refs]
        assert len(refs) == len(self.inst_paths), (refs, self.inst_paths, self.fname)
        hide_ref = refs[0].startswith("#")
        pins_t = " ".join(f"(pin \"{p}\" (uuid \"{U()}\"))" for p in pins)
        inst = " ".join(f"(path \"{pp}\" (reference \"{r}\") (unit {unit}))"
                        for pp, r in zip(self.inst_paths, refs))
        t = (f"(symbol (lib_id \"{lib_id}\") (at {n(x)} {n(y)} {rot}) (unit {unit}) "
             f"(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid \"{U()}\") "
             f"(property \"Reference\" \"{refs[0]}\" (at {n(x + 2.54)} {n(y)} 0) {EFFH if hide_ref else EFF}) "
             f"(property \"Value\" \"{value}\" (at {n(x + 2.54)} {n(y + 2.54)} 0) {EFF}) "
             f"(property \"Footprint\" \"\" (at {n(x)} {n(y)} 0) {EFFH}) "
             f"(property \"Datasheet\" \"\" (at {n(x)} {n(y)} 0) {EFFH}) "
             f"(property \"Description\" \"\" (at {n(x)} {n(y)} 0) {EFFH}) "
             f"{pins_t} (instances (project \"{self.proj.name}\" {inst})))")
        self.items.append(("symbol", t))

    def R(self, refs, x, y):
        self.symbol("Device", "R", refs, x, y, "R", pins=("1", "2"))
        return (x, round(y - 3.81, 4)), (x, round(y + 3.81, 4))

    def power(self, name, x, y, value=None, refs=None):
        if refs is None:
            refs = []
            for _ in self.inst_paths:
                self.proj.npwr += 1
                refs.append(f"#PWR{self.proj.npwr:03d}")
        self.symbol("power", name, refs, x, y, value or name)
        return (x, y)

    def sheet(self, sheet_name, child, x, y, w=30.48, h=20.32, pins=()):
        su = U()
        where, pin_t = {}, []
        for name, shape, side, k in pins:
            px = x if side == "left" else round(x + w, 4)
            py = round(y + k * 2.54, 4)
            ang, just = (180, "left") if side == "left" else (0, "right")
            where[name] = (px, py)
            pin_t.append(f"(pin \"{name}\" {shape} (at {n(px)} {n(py)} {ang}) (uuid \"{U()}\") "
                         f"(effects (font (size 1.27 1.27)) (justify {just})))")
        inst = []
        for pp in self.inst_paths:
            self.proj.page += 1
            inst.append(f"(path \"{pp}\" (page \"{self.proj.page}\"))")
        t = (f"(sheet (at {n(x)} {n(y)}) (size {n(w)} {n(h)}) (exclude_from_sim no) (in_bom yes) "
             f"(on_board yes) (dnp no) (fields_autoplaced yes) (stroke (width 0.1524) (type solid)) "
             f"(fill (color 0 0 0 0.0000)) (uuid \"{su}\") "
             f"(property \"Sheetname\" \"{sheet_name}\" (at {n(x)} {n(y - 0.7116)} 0) "
             f"(effects (font (size 1.27 1.27)) (justify left bottom))) "
             f"(property \"Sheetfile\" \"{child.fname}\" (at {n(x)} {n(y + h + 0.5846)} 0) "
             f"(effects (font (size 1.27 1.27)) (justify left top))) "
             + " ".join(pin_t)
             + f" (instances (project \"{self.proj.name}\" {' '.join(inst)})))")
        self.items.append(("sheet", t))
        child.inst_paths += [f"{pp}/{su}" for pp in self.inst_paths]
        return su, where

    def text(self):
        order = ["junction", "no_connect", "wire", "label", "global_label", "hierarchical_label",
                 "symbol", "sheet"]
        body = [f"(kicad_sch (version 20250114) (generator \"eeschema\") (generator_version \"9.0\") "
                f"(uuid \"{self.uuid}\") (paper \"A4\")",
                "(lib_symbols " + "\n".join(self.libs.values()) + ")"]
        for tok in order:
            body += [t for k, t in self.items if k == tok]
        if self.is_root:
            body.append("(sheet_instances (path \"/\" (page \"1\")))")
        body.append("(embedded_fonts no))")
        return "\n".join(body) + "\n"


class Proj:
    def __init__(self, name, text_vars=None):
        self.name = name
        self.dir = RUNS / name / "orig"
        if self.dir.parent.exists():
            shutil.rmtree(self.dir.parent)
        self.dir.mkdir(parents=True)
        self.text_vars = text_vars
        self.page = 1
        self.npwr = 0
        self.root = Sch(self, f"{name}.kicad_sch", is_root=True)
        self.files = [self.root]

    def child(self, fname):
        c = Sch(self, fname)
        self.files.append(c)
        return c

    def write(self):
        pro = {"meta": {"filename": f"{self.name}.kicad_pro", "version": 1}}
        if self.text_vars:
            pro["text_variables"] = self.text_vars
        (self.dir / f"{self.name}.kicad_pro").write_text(json.dumps(pro, indent=2) + "\n")
        for f in self.files:
            Path(f.path).write_text(f.text(), encoding="utf-8", newline="\n")
        return self.root.path


# ---------------------------------------------------------------------------- oracle (info)


def netlist(sch_path):
    sch_path = str(sch_path)
    out = sch_path + ".probe.netlist.xml"
    if os.path.exists(out):
        os.remove(out)
    r = _run_cli(["sch", "export", "netlist", "--format", "kicadxml", "--output", out, sch_path],
                 check=False)
    if not os.path.exists(out):
        raise RuntimeError(f"netlist export failed rc={r.returncode}: {r.stderr.strip()}")
    res = []
    for net in ET.parse(out).getroot().iter("net"):
        res.append((int(net.get("code", "0")), net.get("name", ""),
                    frozenset((d.get("ref", ""), d.get("pin", "")) for d in net.findall("node"))))
    os.remove(out)
    return res


def where(nl):
    return {nd: (c, nm) for c, nm, v in nl for nd in v}


def show(nl, refs=None, title="", out=print):
    return None


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


class Log:
    def __init__(self, path):
        self.path = Path(path)
        self.records: list = []

    def __call__(self, s=""):
        print(s)

    def save(self):
        rlib.flush()


# ---------------------------------------------------------------------------- runner


def run(log, scen, orig_dir, root_name, rel_sch, tool, kw, *, allowed=(), join=None,
        ideal="", designs=None, pin_nodes=(), note=""):
    rlib.run_pair(vid=scen, src_dir=orig_dir, root_rel=root_name, edited_rel=rel_sch, tool=tool,
                  kw=kw, note=(ideal or note))


def run_seq(log, scen, orig_dir, root_name, steps, *, designs=None, pin_nodes=(), ideal=""):
    rlib.run_seq(vid=scen, src_dir=orig_dir, root_rel=root_name, steps=steps, note=ideal)
