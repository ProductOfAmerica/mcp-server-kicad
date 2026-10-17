"""How pcbnew reads a board's nets, run in pcbnew as a judge.

``read( board )`` loads the board with pcbnew (LoadBoard, the path the editor and kicad-cli
take) and returns, as pcbnew holds it in memory:

- ``items``: the net of every track (segment, arc and via), zone and copper shape (a
  PCB_SHAPE) by uuid, as [net code, net name]; an item on no net has the name "" (net 0, or
  no net at all for a number no net has: code -1);
- ``pads``: the net of every pad, by footprint reference and pad number, as [code, name];
- ``nets``: the nets the board holds, as sorted [code, name] pairs, net 0 included;
- ``count``: BOARD::GetNetCount().

pcbnew logs "Invalid net ID" with wxLogError, which a GUI log target would show in a dialog
nobody is there to answer. On Windows an unanswerable one kills the process (return code
0xC000041D, measured 2026-10-17 on pcbnew 9.0.8), so the log is sent to stderr wherever the
wxApp prelude would have imported wx (a display, or Windows and macOS, which always have one).
Any failure, a board pcbnew cannot load included, raises RuntimeError through
pcbnew_judge.run, never AssertionError.

pcbnew's Python API is used as measured on 9.0.8; a call whose shape changed on a later
pcbnew shows on the KiCad 10 legs as an error to fix.
"""

from __future__ import annotations

from pathlib import Path

import pcbnew_judge as PJ

_SCRIPT = r"""
import os
import sys
import pcbnew

display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
if sys.platform in ("win32", "darwin") or display:
    import wx

    wx.Log.SetActiveTarget(wx.LogStderr())

board = pcbnew.LoadBoard(payload["board"])
if board is None:
    raise RuntimeError("pcbnew cannot load " + payload["board"])
items = {}
for track in board.GetTracks():
    items[track.m_Uuid.AsString()] = [track.GetNetCode(), track.GetNetname()]
for zone in board.Zones():
    items[zone.m_Uuid.AsString()] = [zone.GetNetCode(), zone.GetNetname()]
for drawing in board.GetDrawings():
    shape = drawing.Cast()
    if hasattr(shape, "GetNetCode"):
        items[shape.m_Uuid.AsString()] = [shape.GetNetCode(), shape.GetNetname()]
pads = {}
for fp in board.GetFootprints():
    pads[fp.GetReference()] = {
        pad.GetNumber(): [pad.GetNetCode(), pad.GetNetname()] for pad in fp.Pads()
    }
nets = sorted([int(code), net.GetNetname()] for code, net in board.GetNetsByNetcode().items())
result = {"items": items, "pads": pads, "nets": nets, "count": board.GetNetCount()}
"""


def read(board: str | Path) -> dict:
    """What pcbnew reads of ``board``'s nets (the module docstring)."""
    return PJ.run(_SCRIPT, {"board": str(board)})
