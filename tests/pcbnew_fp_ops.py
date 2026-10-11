"""KiCad's own footprint edits, run in pcbnew as a judge, in one batch per call.

``run(jobs)`` runs every job in one pcbnew subprocess (pcbnew_judge.run) and returns one
result per job. A job is a dict built by the helpers below:

- ``place_job``: LoadBoard( board_in ), then per item FootprintLoad( library, name ),
  SetReference, SetValue, board.Add, SetPosition, and the item's turn; SaveBoard(
  board_out ) when one is named.
- ``move_job``: LoadBoard( board_in ), then per item FindFootprintByReference,
  SetPosition, and the item's turn; SaveBoard( board_out ) when one is named.
- ``describe_job``: LoadBoard( board_in ), nothing else.

A turn is ("rotate", d): FOOTPRINT::Rotate( position, d ); ("rotate_to", a): Rotate by
a minus the orientation the footprint holds, which is what the editor's footprint
properties dialog does for a new angle (dialogs/dialog_footprint_properties.cpp:636-639 at
9.0.8, without its Normalize of the typed value); ("set", a): FOOTPRINT::SetOrientation(
a ), the Python API's path; or None. Each job's result describes every footprint it
touched (all of them for describe_job), keyed by reference, as pcbnew holds it in
memory: position and orientation, and per pad its number, board position, absolute and
footprint-relative orientation, and per text (the fields, then the fp_text items) its key,
board position, angle, keep-upright flag, justification, mirror flag and canonical layer
name. Angles come back as the doubles pcbnew holds, positions in nanometres.

pcbnew's Python API is used as measured on 9.0.8 (``FOOTPRINT.Rotate( VECTOR2I,
EDA_ANGLE )``, ``BOARD_ITEM.Cast()``, ``LSET.Name``); any call that fails, a board pcbnew
cannot load included, raises RuntimeError through pcbnew_judge.run, never AssertionError,
so a KiCad 10 call shape that changed shows up on the KiCad 10 legs as an error to fix.
"""

from __future__ import annotations

import pcbnew_judge as PJ

_SCRIPT = r"""
import pcbnew


def angle(value):
    return pcbnew.EDA_ANGLE(float(value), pcbnew.DEGREES_T)


def layer_name(layer):
    return pcbnew.LSET.Name(layer)


def text_record(text, kind):
    pos = text.GetTextPos()
    key = ["property", text.GetName()] if kind == "field" else ["fp_text", text.GetText()]
    return [
        key,
        [pos.x, pos.y],
        text.GetTextAngle().AsDegrees(),
        bool(text.IsKeepUpright()),
        int(text.GetHorizJustify()),
        int(text.GetVertJustify()),
        bool(text.IsMirrored()),
        layer_name(text.GetLayer()),
    ]


def describe(fp):
    pads = []
    for pad in fp.Pads():
        pos = pad.GetPosition()
        pads.append(
            [
                pad.GetNumber(),
                [pos.x, pos.y],
                pad.GetOrientation().AsDegrees(),
                pad.GetFPRelativeOrientation().AsDegrees(),
            ]
        )
    texts = [text_record(field, "field") for field in fp.GetFields()]
    for item in fp.GraphicalItems():
        item = item.Cast()
        if item.GetClass() == "PCB_TEXT":
            texts.append(text_record(item, "text"))
    pos = fp.GetPosition()
    return {
        "position": [pos.x, pos.y],
        "orientation": fp.GetOrientation().AsDegrees(),
        "layer": layer_name(fp.GetLayer()),
        "pads": pads,
        "texts": texts,
    }


def turn(fp, how):
    if how is None:
        return
    kind, value = how
    if kind == "rotate":
        fp.Rotate(fp.GetPosition(), angle(value))
    elif kind == "rotate_to":
        fp.Rotate(fp.GetPosition(), angle(float(value) - fp.GetOrientation().AsDegrees()))
    elif kind == "set":
        fp.SetOrientation(angle(value))
    else:
        raise ValueError(f"no such turn {kind!r}")


def load(path):
    board = pcbnew.LoadBoard(path)
    if board is None:
        raise RuntimeError(f"pcbnew cannot load {path}")
    return board


def save(board, path):
    if path and not pcbnew.SaveBoard(path, board):
        raise RuntimeError(f"pcbnew could not save {path}")


result = []
for job in payload["jobs"]:
    out = {}
    if job["op"] == "place":
        board = load(job["board_in"])
        for item in job["items"]:
            fp = pcbnew.FootprintLoad(item["library"], item["name"])
            if fp is None:
                raise RuntimeError(f"FootprintLoad found no {item['name']} in {item['library']}")
            fp.SetReference(item["ref"])
            fp.SetValue(item["value"])
            board.Add(fp)
            fp.SetPosition(pcbnew.VECTOR2I(int(item["x"]), int(item["y"])))
            turn(fp, item["turn"])
            out[item["ref"]] = describe(fp)
        save(board, job["board_out"])
    elif job["op"] == "move":
        board = load(job["board_in"])
        for item in job["items"]:
            fp = board.FindFootprintByReference(item["ref"])
            if fp is None:
                raise RuntimeError(f"no footprint {item['ref']} on {job['board_in']}")
            fp.SetPosition(pcbnew.VECTOR2I(int(item["x"]), int(item["y"])))
            turn(fp, item["turn"])
            out[item["ref"]] = describe(fp)
        save(board, job["board_out"])
    elif job["op"] == "describe":
        board = load(job["board_in"])
        for fp in board.GetFootprints():
            out[fp.GetReference()] = describe(fp)
    elif job["op"] == "call":
        getattr(pcbnew, job["name"])()
    else:
        raise ValueError(f"no such job {job['op']!r}")
    result.append(out)
"""


def place_item(library, name: str, ref: str, at_nm: tuple[int, int], turn=None, value="V") -> dict:
    """One footprint for place_job: ``library`` a .pretty directory, ``turn`` as in the
    module docstring."""
    return {
        "library": str(library),
        "name": name,
        "ref": ref,
        "value": value,
        "x": at_nm[0],
        "y": at_nm[1],
        "turn": list(turn) if turn else None,
    }


def move_item(ref: str, at_nm: tuple[int, int], turn=None) -> dict:
    """One footprint for move_job."""
    return {"ref": ref, "x": at_nm[0], "y": at_nm[1], "turn": list(turn) if turn else None}


def place_job(board_in, items: list[dict], board_out=None) -> dict:
    return {
        "op": "place",
        "board_in": str(board_in),
        "board_out": str(board_out) if board_out else None,
        "items": items,
    }


def move_job(board_in, items: list[dict], board_out=None) -> dict:
    return {
        "op": "move",
        "board_in": str(board_in),
        "board_out": str(board_out) if board_out else None,
        "items": items,
    }


def describe_job(board_in) -> dict:
    return {"op": "describe", "board_in": str(board_in)}


def call_job(name: str) -> dict:
    """A job that calls ``pcbnew.<name>()``: for checking that a failed call raises."""
    return {"op": "call", "name": name}


def run(jobs: list[dict]) -> list[dict]:
    """Run ``jobs`` in one pcbnew subprocess; one result per job (module docstring)."""
    found = PJ.run(_SCRIPT, {"jobs": jobs})
    if not isinstance(found, list) or len(found) != len(jobs):
        raise RuntimeError(f"pcbnew answered {len(found) if isinstance(found, list) else found!r}")
    return found
