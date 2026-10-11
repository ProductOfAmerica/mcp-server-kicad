"""probeB shim for probe-buses/bl.py: the same fixture helpers (copied), with scenario() running
designs M2, H, Hb and E through rlib (judge3) instead of P, Pt, M and B. scenario() runs only the
variants casemap names for the current script."""

import sys

sys.dont_write_bytecode = True
import hpaths  # noqa: E402,F401
import rlib  # noqa: E402  (first: redirects TMP before harness/judge3)

import harness as H  # noqa: E402
from harness import (  # noqa: E402,F401
    _cst, bus, bus_entry, diff, fresh, junction, label, nets_coded, no_connect, pin, place, sha,
    splice, wire,
)

import mine_impl as M  # noqa: E402,F401  (fixture-selection code of s09 uses it, read-only)
import proposal_impl as P  # noqa: E402,F401  (s09/s12 use its _iu and _Model to pick targets)
from mcp_server_kicad import project as PROJ  # noqa: E402,F401
from mcp_server_kicad import schematic as B  # noqa: E402,F401

M2 = rlib.M2
H = rlib.H
Hb = rlib.Hb
E = rlib.E


def strip(n: str) -> str:
    return n[1:] if n.startswith("/") else n


def is_auto(n: str) -> bool:
    s = n.rsplit("/", 1)[-1] if n.startswith("/") else n
    return n.startswith(("Net-(", "unconnected-(")) or s.startswith(("Net-(", "unconnected-("))


def pins_arg(*rp):
    return [{"reference": r, "pin": n} for r, n in rp]


def _iu(v) -> int:
    s = float(v) * 10000.0
    return int(s + 0.5) if s >= 0 else -int(-s + 0.5)


def check_pin(p, ref, num, want):
    """Assert the repo's pin position and M2's KiCad-transform position (as bl.check_pin did with M,
    whose transform M2 keeps unchanged)."""
    got = pin(p, ref, num)[:2]
    got = (round(got[0], 4), round(got[1], 4))
    assert got == want, (ref, num, got, want)
    mp = M2.pin_positions(p, ref)[num][0][:2]
    assert (round(mp[0], 4), round(mp[1], 4)) == want, ("M2 disagrees", ref, num, mp, want)
    return got


def show_nets(p, watch=None):
    """Fixture printout only in the original; skipped here (no judge input)."""
    return None


def dump(name):
    rlib.flush()


def scenario(sid, title, fixture_path, call, edit_rel=None, root_rel=None, targets=(), N=None,
             pair=None, expect=None, designs=None, note="", show_before=True, n_alias=(),
             new_refs=(), extra=None):
    from pathlib import Path

    src_dir = Path(fixture_path).parent
    edit_rel = edit_rel or Path(fixture_path).name
    root_rel = root_rel or edit_rel
    fn_name, kwargs = call
    rlib.run_pair(vid=sid, src_dir=src_dir, root_rel=str(root_rel), edited_rel=str(edit_rel),
                  tool=fn_name, kw=kwargs, note=title + (f" | expect {expect}" if expect else ""))
