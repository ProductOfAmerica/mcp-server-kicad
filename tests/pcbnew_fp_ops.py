"""KiCad's own footprint edits, run in pcbnew as a judge, in one batch per call.

``run(jobs)`` runs every job in one pcbnew subprocess (pcbnew_judge.run) and returns one
result per job. A job is a dict built by the helpers below:

- ``place_job``: LoadBoard( board_in ), then per item FootprintLoad( library, name ),
  SetReference, SetValue, board.Add, SetPosition, and the item's turn; SaveBoard(
  board_out ) when one is named.
- ``move_job``: LoadBoard( board_in ), then per item FindFootprintByReference,
  SetPosition, and the item's turn; SaveBoard( board_out ) when one is named.
- ``describe_job``: LoadBoard( board_in ), nothing else.
- ``load_job``: per item FootprintLoad( library, name ), nothing else.
- ``layers_job``: LoadBoard( board_in ), then per canonical layer name its opposite
  (BOARD::FlipLayer) and whether a PCB_TEXT on it is side specific
  (BOARD_ITEM::IsSideSpecific), and the board's copper layer count.
- ``exchange_job``: LoadBoard( board_in ), then per item what Update PCB from Schematic
  does to a footprint whose symbol names another footprint (kicad_ref.exchange):
  FootprintLoad( library, name ), which gives every item a fresh uuid as the board
  editor's loadFootprint does, and SetFPID; nothing more for a locked footprint, which
  the updater refuses to change; else FixUpPadsForBoard and ExchangeFootprint replayed
  step for step with every reset flag true (pcbnew/pcb_edit_frame.cpp:2346-2587 at
  9.0.8: group membership, the parent board, SetPosition, Flip LEFT_RIGHT when the
  layers differ, SetOrientation when the orientations differ, the lock, pad uuids and
  nets by number and copper side, fp_text uuids through getMatchingTextItem, the
  Reference's and Value's text and uuid, other fields' uuids by name, the footprint's
  uuid, path and sheet), KiCad 10's KeepUpright on each matched text included when
  pcbnew is 10 or later (pcb_edit_frame.cpp:2504 at 10.0.7); then SetValue of the
  item's value, as the updater's updateFootprintParameters does; SaveBoard( board_out ).
  Its uuid matching, and so the texts it takes as matched, is KiCad 9.0.8's on every major
  (KiCad 10 matches by similarity, kicad_ref.exchange).

A turn is ("rotate", d): FOOTPRINT::Rotate( position, d ); ("rotate_to", a): the turn a
tool's rotation asks for (kicad_ref.footprint.turn_to), nothing when
EDA_ANGLE::Normalize180 of a is the orientation the footprint holds, else Rotate by a
minus that orientation, which is what the editor's footprint properties dialog does for a
new angle (dialogs/dialog_footprint_properties.cpp:636-639 at 9.0.8, without its
Normalize of the typed value); ("set", a): FOOTPRINT::SetOrientation(
a ), the Python API's path; ("flip", None): FOOTPRINT::Flip( position,
FLIP_DIRECTION::LEFT_RIGHT ), what SetLayerAndFlip runs to change the side; a list of
turns, applied in order; or None. Each job's result describes every footprint it
touched (all of them for describe_job), keyed by reference (by name for load_job), as
pcbnew holds it in memory: the Reference's and the Value's text (GetReference,
GetValue), position, orientation and layer, per pad its number, board position, absolute
and footprint-relative orientation, and under "pad_layers" the canonical names of its
layers, and per text (the fields, then the fp_text items) its key, board position,
angle, keep-upright flag, justification, mirror flag and canonical layer name. Under
"pad_copper" each pad's PAD::IsOnCopperLayer and under "locked" the footprint's lock.
Angles come back as the doubles pcbnew holds, positions in nanometres.

pcbnew's Python API is used as measured on 9.0.8 (``FOOTPRINT.Rotate( VECTOR2I,
EDA_ANGLE )``, ``FOOTPRINT.Flip( VECTOR2I, FLIP_DIRECTION_LEFT_RIGHT )``,
``BOARD_ITEM.Cast()``, ``LSET.Name``); any call that fails, a board pcbnew cannot load
included, raises RuntimeError through pcbnew_judge.run, never AssertionError, so a KiCad
10 call shape that changed shows up on the KiCad 10 legs as an error to fix.
"""

from __future__ import annotations

from pathlib import Path

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
        "reference": fp.GetReference(),
        "value": fp.GetValue(),
        "position": [pos.x, pos.y],
        "orientation": fp.GetOrientation().AsDegrees(),
        "layer": layer_name(fp.GetLayer()),
        "pads": pads,
        "pad_layers": [
            sorted(layer_name(layer) for layer in pad.GetLayerSet().Seq()) for pad in fp.Pads()
        ],
        "pad_copper": [bool(pad.IsOnCopperLayer()) for pad in fp.Pads()],
        "locked": bool(fp.IsLocked()),
        "texts": texts,
    }


KICAD10 = int(pcbnew.GetMajorMinorVersion().split(".")[0]) >= 10
KEEP = []


def set_uuid(item, source):
    if hasattr(item, "SetUuid"):
        item.SetUuid(source.m_Uuid)
    else:
        item.m_Uuid.Clone(source.m_Uuid)


def field_named(fp, name):
    # FOOTPRINT::GetFieldByName at 9.0.8, GetField( name ) at 10.0.7 (footprint.h:830)
    if hasattr(fp, "HasFieldByName"):
        return fp.GetFieldByName(name) if fp.HasFieldByName(name) else None
    return fp.GetField(name) if fp.HasField(name) else None


def text_items(fp):
    return [i.Cast() for i in fp.GraphicalItems() if i.GetClass() == "PCB_TEXT"]


def matching_text(old, new_fp):
    # getMatchingTextItem, pcb_edit_frame.cpp:2298-2343 at 9.0.8
    candidates = [t for t in text_items(new_fp) if t.GetText() == old.GetText()]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    same_layer = [t for t in candidates if t.GetLayer() == old.GetLayer()]
    if len(same_layer) == 1:
        return same_layer[0]
    rel = old.GetFPRelativePosition()
    at_place = [
        t
        for t in (same_layer or candidates)
        if t.GetFPRelativePosition().x == rel.x and t.GetFPRelativePosition().y == rel.y
    ]
    if at_place:
        return at_place[0]
    return same_layer[0] if same_layer else candidates[0]


def process_text(src, dest, reset_text):
    # processTextItem with the layer, effects (and at 10.0.7 position) resets true
    if not reset_text:
        dest.SetText(src.GetText())
    if KICAD10:
        dest.KeepUpright()
    dest.SetLocked(src.IsLocked())
    set_uuid(dest, src)


def exchange(board, existing, new):
    # PCB_EDIT_FRAME::ExchangeFootprint, pcb_edit_frame.cpp:2346-2587 at 9.0.8
    group = existing.GetParentGroup()
    if group:
        group.RemoveItem(existing)
        group.AddItem(new)
    new.SetParent(board)
    new.SetPosition(existing.GetPosition())
    if new.GetLayer() != existing.GetLayer():
        new.Flip(new.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    if new.GetOrientation().AsDegrees() != existing.GetOrientation().AsDegrees():
        new.SetOrientation(existing.GetOrientation())
    new.SetLocked(existing.IsLocked())
    for pad in new.Pads():
        if not pad.GetNumber():
            pad.SetNetCode(0)
            continue
        old, last = None, None
        while True:
            old = existing.FindPadByNumber(pad.GetNumber(), last)
            if old is None or pad.IsOnCopperLayer() == old.IsOnCopperLayer():
                break
            last = old
        if old is not None:
            set_uuid(pad, old)
        if pad.IsOnCopperLayer() and old is not None:
            pad.SetNetCode(old.GetNetCode())
        else:
            pad.SetNetCode(0)
    for old_text in text_items(existing):
        match = matching_text(old_text, new)
        if match is not None:
            process_text(old_text, match, True)
    process_text(existing.Reference(), new.Reference(), False)
    value_is_fpid = existing.GetValue() == str(existing.GetFPID().GetLibItemName())
    process_text(existing.Value(), new.Value(), value_is_fpid)
    for old_field in existing.GetFields():
        if old_field.IsReference() or old_field.IsValue():
            continue
        new_field = field_named(new, old_field.GetName())
        if new_field is not None:
            process_text(old_field, new_field, True)
    set_uuid(new, existing)
    new.SetPath(existing.GetPath())
    new.SetSheetfile(existing.GetSheetfile())
    new.SetSheetname(existing.GetSheetname())
    new.SetFilters(existing.GetFilters())
    board.Remove(existing)
    # KiCad keeps the removed footprint in its undo list; freeing it here corrupted later
    # SWIG calls in pcbnew 9.0.8, so it is kept alive instead.
    existing.thisown = 0
    KEEP.append(existing)
    board.Add(new)


def turn(fp, how):
    if how is None:
        return
    if how and isinstance(how[0], list):
        for step in how:
            turn(fp, step)
        return
    kind, value = how
    if kind == "rotate":
        fp.Rotate(fp.GetPosition(), angle(value))
    elif kind == "rotate_to":
        held = fp.GetOrientation().AsDegrees()
        if angle(value).Normalize180().AsDegrees() != held:
            fp.Rotate(fp.GetPosition(), angle(float(value) - held))
    elif kind == "set":
        fp.SetOrientation(angle(value))
    elif kind == "flip":
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
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
    elif job["op"] == "load":
        for item in job["items"]:
            fp = pcbnew.FootprintLoad(item["library"], item["name"])
            if fp is None:
                raise RuntimeError(f"FootprintLoad found no {item['name']} in {item['library']}")
            out[item["name"]] = describe(fp)
    elif job["op"] == "exchange":
        board = load(job["board_in"])
        for item in job["items"]:
            existing = board.FindFootprintByReference(item["ref"])
            if existing is None:
                raise RuntimeError(f"no footprint {item['ref']} on {job['board_in']}")
            new = pcbnew.FootprintLoad(item["library"], item["name"])
            if new is None:
                raise RuntimeError(f"FootprintLoad found no {item['name']} in {item['library']}")
            new.SetFPID(pcbnew.LIB_ID(item["nickname"], item["name"]))
            if existing.IsLocked():
                out[item["ref"]] = describe(existing)
                continue
            new.FixUpPadsForBoard(board)
            exchange(board, existing, new)
            new.SetValue(item["value"])
            out[item["ref"]] = describe(new)
        save(board, job["board_out"])
    elif job["op"] == "layers":
        board = load(job["board_in"])
        out["copper_count"] = board.GetCopperLayerCount()
        for name in job["names"]:
            layer = board.GetLayerID(name)
            if layer < 0:
                raise RuntimeError(f"pcbnew knows no layer {name!r}")
            text = pcbnew.PCB_TEXT(board)
            text.SetLayer(layer)
            board.Add(text)
            out[name] = [layer_name(board.FlipLayer(layer)), bool(text.IsSideSpecific())]
    elif job["op"] == "call":
        getattr(pcbnew, job["name"])()
    else:
        raise ValueError(f"no such job {job['op']!r}")
    result.append(out)
"""


def _turn(turn):
    """A turn or a sequence of turns (module docstring) in the job's JSON shape."""
    if not turn:
        return None
    if isinstance(turn[0], (list, tuple)):
        return [list(step) for step in turn]
    return list(turn)


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
        "turn": _turn(turn),
    }


def move_item(ref: str, at_nm: tuple[int, int], turn=None) -> dict:
    """One footprint for move_job."""
    return {"ref": ref, "x": at_nm[0], "y": at_nm[1], "turn": _turn(turn)}


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


def load_job(footprints) -> dict:
    """A job loading each (library, name) of ``footprints``, ``library`` a .pretty
    directory, by FootprintLoad and nothing else: each footprint as KiCad reads its library
    file, keyed by name."""
    return {
        "op": "load",
        "items": [{"library": str(library), "name": name} for library, name in footprints],
    }


def exchange_item(ref: str, library, name: str, value: str = "V") -> dict:
    """One footprint for exchange_job: footprint ``ref`` replaced by ``name`` from the
    .pretty directory ``library``, whose stem is its nickname, and given ``value``."""
    return {
        "ref": ref,
        "library": str(library),
        "nickname": Path(library).stem,
        "name": name,
        "value": value,
    }


def exchange_job(board_in, items: list[dict], board_out) -> dict:
    return {
        "op": "exchange",
        "board_in": str(board_in),
        "board_out": str(board_out),
        "items": items,
    }


def layers_job(board_in, names) -> dict:
    """A job reporting each named layer's opposite and side specificity on a board."""
    return {"op": "layers", "board_in": str(board_in), "names": list(names)}


def call_job(name: str) -> dict:
    """A job that calls ``pcbnew.<name>()``: for checking that a failed call raises."""
    return {"op": "call", "name": name}


def run(jobs: list[dict]) -> list[dict]:
    """Run ``jobs`` in one pcbnew subprocess; one result per job (module docstring)."""
    found = PJ.run(_SCRIPT, {"jobs": jobs})
    if not isinstance(found, list) or len(found) != len(jobs):
        raise RuntimeError(f"pcbnew answered {len(found) if isinstance(found, list) else found!r}")
    return found
