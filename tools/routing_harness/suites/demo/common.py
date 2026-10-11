"""Shared setup for the follow-up 2 demo rerun (designs M2, H, Hb, E). Import this module FIRST in
every script here. Copied from pr57pt/coarse/rerun/demo/common.py; K (coarse_impl) and its
lifts-with parser are gone.

On import it (1) stops Python writing bytecode anywhere (the repo and the harness tree are
read-only for a run), (2) points TMP, TEMP and tempfile.tempdir at a per-process directory under
the run's demo output directory (hpaths.suite_out("demo") / "tmp"), so judge3's and harness's
import-time mkdtemp and every kicad-cli netlist land there, then (3) imports judge3, M2 (m2_impl),
H (h_impl), Hb (hb_impl: h_impl with CROSS_BAN True), E (e_impl) and the repo's schematic module.
DESIGNS is the registry's selection (core/registry.py: ROUTING_HARNESS_DESIGNS, default X).
"""

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import re  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import hpaths  # noqa: E402

OUT = hpaths.suite_out("demo")  # every output of this suite: records, base pickles, work, logs
STOCK = hpaths.KICAD_DEMOS
PROC_TMP = OUT / "tmp" / f"p{os.getpid()}"
PROC_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(PROC_TMP)
tempfile.tempdir = str(PROC_TMP)

import judge3  # noqa: E402
from judge3 import assess2, call, snapshot, snapshot2  # noqa: E402,F401

import e_impl as E  # noqa: E402
import h_impl as H  # noqa: E402
import hb_impl as Hb  # noqa: E402
import m2_impl as M2  # noqa: E402
import registry  # noqa: E402
from mcp_server_kicad import _cst  # noqa: E402
from mcp_server_kicad import schematic as REPO_SCH  # noqa: E402

DESIGNS = registry.selected()  # follow-up 3 ran one design; the registry picks it by name
assert Hb.MODULE.CROSS_BAN is True and H.CROSS_BAN is False
CODE_RE = re.compile(r"^((?:\[[MKHE]:\w+\]\s*)+)")


def repath(src, dst):
    """pr57pt/proto/mine_realsheets.py repath, copied: every symbol's first instance path becomes
    /<this file's uuid>, any further one a dead path, so a standalone export lists every net."""
    tree = _cst.parse(Path(src).read_bytes())
    root = tree.lists[0]
    u = root.find("uuid").atoms[1].text
    for s in root.find_all("symbol"):
        inst = s.find("instances")
        if inst is None:
            continue
        paths = [pth for pj in inst.find_all("project") for pth in pj.find_all("path")]
        for i, pth in enumerate(paths):
            pth.atoms[1].set_text(f"/{u}" if i == 0 else "/00000000-0000-0000-0000-00000000dead")
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(_cst.serialize(tree))
    return str(dst)


def codes_of(msg: str) -> list:
    """Reason codes at the head of a refusal message (judge3.call prefixes 'ToolError: ')."""
    body = msg.split(": ", 1)[1] if msg.startswith(("ToolError: ", "_Refuse: ")) else msg
    m = CODE_RE.match(body)
    return re.findall(r"\[[MKHE]:(\w+)\]", m.group(1)) if m else []


def clean_tmp():
    """Delete judge3's per-call scratch (gui_* and probe_* copies) under this process's TMP."""
    for d in Path(judge3.TMP).iterdir():
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
        else:
            try:
                d.unlink()
            except OSError:
                pass


def detail(r: dict) -> dict:
    return {k: v for k, v in (r or {}).items() if v and k != "delivered_each"}
