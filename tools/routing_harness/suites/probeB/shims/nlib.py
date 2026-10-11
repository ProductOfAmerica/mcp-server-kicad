"""probeB shim for probe-naming/nlib.py: Fixture/Clone, W, CP, pos and hier_project copied
(pos reads M2's KiCad transform, identical to mine_impl's), DESIGNS = M2, H, Hb and E, and step() running
ONE design's call through rlib.exec_design (judge3 on the netlist root; the two designs' results
for one (variant, step) are paired when both have run). The script copies pass vid= and stepno=."""

import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import hpaths  # noqa: E402,F401
import rlib  # noqa: E402

import harness as H  # noqa: E402
from harness import BASE, diff, nets_coded, sha  # noqa: E402,F401

import mine_impl as M  # noqa: E402,F401  (n11c's model printout uses it, read-only)
import proposal_impl as P  # noqa: E402,F401
from mcp_server_kicad import _cst  # noqa: E402,F401
from mcp_server_kicad import project as BP  # noqa: E402,F401
from mcp_server_kicad import schematic as BS  # noqa: E402

M2 = rlib.M2
Hd = rlib.H  # design H; the name H stays the harness alias the naming scripts use (fix 2026-10-03)
Hb = rlib.Hb
E = rlib.E
DESIGNS = dict(rlib.MODS)


def weak(name: str) -> bool:
    last_ = name.rsplit("/", 1)[-1]
    return last_.startswith("Net-(") or last_.startswith("unconnected-(") or name == ""


def last(name: str) -> str:
    return name.rsplit("/", 1)[-1] if name.startswith("/") else name


def pos(p, ref, num, i=0):
    x, y, _ = M2.pin_positions(p, ref)[num][i]
    return round(x, 4), round(y, 4)


class Fixture:
    def __init__(self, tag: str):
        self.tag = tag
        self.src = BASE / tag / "src"
        self.src.mkdir(parents=True)

    def path(self, name: str) -> str:
        return str(self.src / name)

    def clone(self, design: str) -> "Clone":
        dst = BASE / self.tag / design
        shutil.copytree(self.src, dst)
        return Clone(dst)


class Clone:
    def __init__(self, d: Path):
        self.d = d

    def path(self, name: str) -> str:
        return str(self.d / name)


def step(design, tag, netlist_root, files, tool, kw, targets=(), N=None, pair=None, note="",
         vid=None, stepno=1, nsteps=1):
    if vid is None:
        raise ValueError(f"step() needs vid= ({tag})")
    if not rlib.wanted(vid):
        return None
    return rlib.exec_design(design, vid=vid, step=stepno, tool=tool, kw=kw, root=netlist_root,
                            edited=kw["schematic_path"], note=tag + (f" | {note}" if note else ""),
                            nsteps=nsteps)


def show_nets(p, title="nets", focus=None):
    """Printout only in the original; returns the kicad-cli netlist when a caller uses it."""
    return []


def W(pins, N, sch, **kw):
    return dict(pins=[{"reference": r, "pin": n} for r, n in pins], label_text=N,
                schematic_path=sch, **kw)


def CP(r1, p1, r2, p2, sch):
    return dict(ref1=r1, pin1=p1, ref2=r2, pin2=p2, schematic_path=sch)


def dump(script_file):
    rlib.flush()


def hier_project(tag_dir: Path, name: str, kid: str, sheet_name: str, pins, x=101.6, y=50.8,
                 keep_parent_labels=False, keep_child_locals=True):
    BP.create_project(str(tag_dir), name)
    root = str(tag_dir / f"{name}.kicad_sch")
    pro = str(tag_dir / f"{name}.kicad_pro")
    kid_sch = str(tag_dir / kid)
    BP.create_schematic(kid_sch)
    BP.add_hierarchical_sheet(root, sheet_name, kid_sch, pins=pins, x=x, y=y, project_path=pro)
    if not keep_parent_labels:
        for i, pd in enumerate(pins):
            BS.remove_label(pd["name"], round(x - 2.54, 4), round(y + (i + 1) * 2.54, 4),
                            schematic_path=root)
    if not keep_child_locals:
        for i, pd in enumerate(pins):
            BS.remove_label(pd["name"], 27.94, round(25.4 + i * 5.08, 4), schematic_path=kid_sch)
    return root, pro, kid_sch
