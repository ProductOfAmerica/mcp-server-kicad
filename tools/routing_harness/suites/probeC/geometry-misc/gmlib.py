"""Fixture helpers for the geometry-misc rerun, copied from probe-geometry-misc/gm.py and
verify-probe-geometry-misc/vlib.py (fixture building only: no setup(), no judging, no design).
Import rlib before this module (it redirects TMP/TEMP and sets sys.dont_write_bytecode).
"""

from pathlib import Path

import rlib as R
from rlib import H

_cst = H._cst
GROUP = "geometry-misc"


def begin(script_file, orig):
    """R.begin with tag gm_<stem>; returns the record 'script' string (rerun <- original)."""
    stem = Path(script_file).stem
    R.begin(GROUP, script_file, tag=stem)
    return f"geometry-misc/{Path(script_file).name} <- {orig}"


def n(v: float) -> str:
    return H._n(v)


def set_at(p, ref, x, y, rot=None):
    """Move a placed symbol's origin exactly (place() snaps to 1.27 mm). Copied from gm.py."""
    tree = _cst.parse(open(p, "rb").read())
    root = tree.lists[0]
    for sym in root.find_all("symbol"):
        if sym.find("lib_id") is None:
            continue
        refp = [pr for pr in sym.find_all("property") if pr.atoms[1].text == "Reference"]
        if refp and refp[0].atoms[2].text == ref:
            at = sym.find("at")
            at.atoms[1].set_text(n(x))
            at.atoms[2].set_text(n(y))
            if rot is not None:
                at.atoms[3].set_text(str(rot))
            open(p, "wb").write(_cst.serialize(tree))
            return
    raise KeyError(ref)


def kpos(p, ref, num, i=0):
    """KiCad's pin end (mm) and outward name (m2_impl's transform, the same as mine_impl's)."""
    x, y, out = R.M2.pin_positions(p, ref)[num][i]
    return round(x, 4), round(y, 4), out


def kpin(p, ref, num):
    return R.M2.pin_positions(p, ref)[num]


def iu(v) -> int:
    s = float(v) * 10000.0
    return int(s + 0.5) if s >= 0 else -int(-s + 0.5)


def W(*pairs, N="N", **kw):
    return dict(pins=R.pins(*pairs), label_text=N, **kw)


def C(r1, p1, r2, p2):
    return dict(ref1=r1, pin1=p1, ref2=r2, pin2=p2)
