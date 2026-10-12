"""KiCad's footprint exchange, as Update PCB from Schematic runs it, ported as an oracle.

When a symbol's footprint changes, the netlist updater replaces the board footprint
(BOARD_NETLIST_UPDATER::replaceFootprint, pcbnew/netlist_reader/
board_netlist_updater.cpp:300-380 at 9.0.8, 313-393 at 10.0.7): a locked footprint is
never replaced unless the user overrides locks, which the dialog leaves off, and the
updater says "Could not change ... (footprint is locked)" (353-363 [366-376]); it still
updates that footprint's value, fields and pads from the symbol (1366-1383 at 9.0.8;
10.0.7 keeps it as the base footprint and updates it, 2246-2315). Otherwise it loads the
library footprint, gives its plated through-hole pads every copper layer of the board
(FOOTPRINT::FixUpPadsForBoard, pcbnew/footprint.cpp:4167-4185 [4834-4853]) and calls
PCB_EDIT_FRAME::ExchangeFootprint with every reset flag at its default, true
(pcbnew/pcb_edit_frame.cpp:2346-2587 [2597-3068]; the defaults in pcb_edit_frame.h:
595-599 [546-555]):

- the library footprint is placed at the old one's position (PlaceFootprint, which only
  calls SetPosition, pcbnew/load_select_footprint.cpp:426-427), flipped by
  FOOTPRINT::Flip about that position in the default direction, LEFT_RIGHT, when its
  layer differs from the old one's, and set to the old orientation by
  FOOTPRINT::SetOrientation when its own differs (2366-2372 [2624-2630]): so it keeps
  the old footprint's position, side and orientation, its pads and texts turned and
  flipped with it (kicad_ref.footprint's set_position, flip and set_orientation);
- every text's layer, visibility, effects and position are reset to the library's
  (processTextItem, 2257-2295 [2474-2525]). KiCad 9.0.8 turns no text upright there, so
  after SetOrientation a keep-upright text can stand at 180 or more (the flip before it
  ends in a KeepUpright of its own); KiCad 10.0.7's processTextItem runs KeepUpright on
  the text it is handed (2504), whether or not the footprint turned: the Reference, the
  Value, each field paired with an old one (the Datasheet and the Description always,
  which both footprints hold and PCB_FIELD::Similarity pairs by id,
  pcbnew/pcb_field.cpp:325-347 at 10.0.7) and each fp_text paired with an old one.
  ``exchange`` for major 10 turns upright the texts ``matched_texts`` names, which pairs
  user fields by name and fp_texts by getMatchingTextItem, KiCad 9.0.8's rules, where
  KiCad 10.0.7 pairs both by similarity (below);
- KiCad 9.0.8 carries uuids by name and number (``carried_ids``): the footprint's own
  (2569), the Reference's and the Value's (processTextItem copies its source's uuid,
  2294), every other field's to the library field of the same name (FOOTPRINT::
  GetFieldByName, an exact match, footprint.cpp:643-655; 2484-2509), a mandatory field
  the library file leaves out included, since the new footprint holds it all the same
  (addMandatoryFields, footprint.cpp:94-111), every fp_text's to the library text
  getMatchingTextItem picks (2298-2343: the same text; then on the same layer; then at
  the same footprint-relative position; then the first candidate; a later old text
  matching the same library text overwrites an earlier one's uuid), and every numbered
  pad's to the first old pad of that number whose IsOnCopperLayer agrees (FindPadByNumber
  walks the pads in order, footprint.cpp:2007-2024; pcb_edit_frame.cpp:2376-2417). A pad
  without a number takes none: to KiCad's parser that is every non-plated hole and every
  pad on no copper layer (kicad_ref.footprint), so a pad with a number is always on
  copper and the copper test passes for each candidate. Group membership follows the
  footprint (2358-2362), so a group that listed the old footprint's uuid lists the new
  one. KiCad 10.0.7 matches pads, drawings, zones, points, groups and fields by
  similarity instead (matchItemsBySimilarity, 2527-2594): each old item to at most one
  new one, the pairs taken greedily by score, and a fresh uuid (ResetUuid, 2675, 2708,
  2845) for every new item left unpaired. That matching is not ported. What it makes
  certain is (``carried_ids`` with ``major`` 10): the footprint's, the Reference's and
  the Value's uuids carry (2634-2636), the Datasheet's and the Description's too, and a
  pad whose number appears once in each footprint takes the old pad's uuid, since a pair
  of one number scores PAD::Similarity plus 2 (2552), over 2, and any other pair at most
  1 (pcbnew/pad.cpp:3187-3208); no uuid goes to two items. User fields and fp_texts pair
  by scores of their own (PCB_FIELD::Similarity: 1 for one name; EDA_TEXT::Similarity,
  common/eda_text.cpp:1262-1275: the Levenshtein similarity of the two texts, times 0.9
  for other attributes and 0.9 for another place), so where each side holds one field of
  a name and the fp_texts' texts are far apart, KiCad 10 pairs them as KiCad 9's rules
  do; an fp_text whose text the other side lacks can still pair with one of another text
  ("KEEP" with "${REFERENCE}", Levenshtein similarity 1/6), which neither ``exchange``
  nor ``carried_ids`` models.

PAD::IsOnCopperLayer (pcbnew/pad.cpp:1129-1180 at 9.0.8): a pad on any copper layer,
except a non-plated hole whose circle or oval is no larger than its drill and not offset,
which has no annular ring and so no copper.

A footprint's lock is read by parseMaybeAbsentBool( true ) (pcbnew/pcb_io/kicad_sexpr/
pcb_io_kicad_sexpr_parser.cpp:239-266 at 9.0.8, 265-293 at 10.0.7; the footprint's
case T_locked, 4502-4504 [5190-5192]): a bare ``locked`` after the library id, an empty
(locked), (locked yes) and (locked true) lock it; (locked no) and (locked false) do not.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

from typing import NamedTuple

from kicad_ref import board_layers as BL
from kicad_ref import dsn_lexer as L
from kicad_ref import footprint as F
from kicad_ref import pcb_text as T
from kicad_ref.parse_units import parse_board_units


def is_locked(group) -> bool:
    """Whether KiCad reads the (footprint ...) Group ``group`` as locked (module
    docstring); ValueError for a lock word KiCad refuses."""
    locked = False
    for item in group.items[2:]:
        if item.kind == L.SYMBOL and item.value == b"locked":
            locked = True
        elif F.head(item) == b"locked":
            words = item.items[1:]
            if not words:
                locked = True
            elif words[0].value in (b"yes", b"true"):
                locked = True
            elif words[0].value in (b"no", b"false"):
                locked = False
            else:
                raise ValueError(f"(locked {words[0].value!r}) is no bool KiCad reads")
    return locked


def exchange(
    old: F.Footprint, library: F.Footprint, table: BL.LayerTable, *, major: int
) -> F.Footprint:
    """The footprint ExchangeFootprint makes of ``library`` (read at orientation 0 at the
    origin, its through-hole pads holding the board's copper) in place of ``old``, on the
    board whose layer table is ``table``, as KiCad ``major`` turns and rounds it (module
    docstring): KiCad 10's KeepUpright on matched_texts included."""
    if library.orientation != 0:
        raise ValueError(f"a library footprint reads at orientation 0, not {library.orientation}")
    placed = F.set_position(library, old.position)
    if placed.layer != old.layer:
        placed = F.board_flip(placed, table, major=major)
    if placed.orientation != old.orientation:
        placed = F.set_orientation(placed, old.orientation, major=major)
    if major >= 10:
        upright = matched_texts(old, placed)
        placed = placed._replace(
            texts=tuple(
                t._replace(style=T.keep_upright(t.style)) if k in upright else t
                for k, t in enumerate(placed.texts)
            )
        )
    return placed


def matched_texts(old: F.Footprint, new: F.Footprint) -> frozenset[int]:
    """The indices of ``new.texts`` the exchange hands an old text (processTextItem), by
    KiCad 9.0.8's pairing (module docstring): every field an old one of the same name
    pairs with, the Reference, the Value, the Datasheet and the Description among them,
    which every footprint holds; every fp_text getMatchingTextItem picks for an old one."""
    names = {t.key for t in old.texts if t.key[0] == "property"}
    found = {k for k, t in enumerate(new.texts) if t.key[0] == "property" and t.key in names}
    candidates = [(k, t) for k, t in enumerate(new.texts) if t.key[0] == "fp_text"]
    for old_text in old.texts:
        if old_text.key[0] == "fp_text":
            k = _matching_text(old_text, candidates)
            if k is not None:
                found.add(k)
    return frozenset(found)


class Ids(NamedTuple):
    """The uuids a footprint's file gives: its own, each pad's (in kicad_ref.footprint's
    pad order) and each text's (in its text order, the fields and fp_text items the file
    writes), None where the file writes none."""

    footprint: str | None
    pads: tuple[str | None, ...]
    texts: tuple[str | None, ...]


def _uuid(group) -> str | None:
    """The text of a list's (uuid ...) or legacy (tstamp ...), which KiCad reads alike."""
    for item in group.items:
        if F.head(item) in (b"uuid", b"tstamp") and len(item.items) > 1:
            return item.items[1].value.decode()
    return None


def read_ids(group) -> Ids:
    """The uuids of the (footprint ...) Group ``group`` (Ids)."""
    return Ids(
        _uuid(group),
        tuple(_uuid(pad) for pad in F.pad_groups(group)),
        tuple(_uuid(text) for text in F.text_groups(group)),
    )


def _size(group, name: bytes) -> tuple[int, int] | None:
    node = F.child(group, name)
    if node is None:
        return None
    numbers = [t.value for t in node.items[1:] if t.kind == L.NUMBER]
    if not numbers:
        return None
    x = parse_board_units(numbers[0], major=10)
    return x, parse_board_units(numbers[1], major=10) if len(numbers) > 1 else x


def is_on_copper(pad_group, copper_count: int) -> bool:
    """PAD::IsOnCopperLayer of a (pad ...) Group on a board of ``copper_count`` copper
    layers, once FixUpPadsForBoard has given a plated through hole the board's copper
    (module docstring)."""
    kind = pad_group.items[2].value if len(pad_group.items) > 2 else b"smd"
    shape = pad_group.items[3].value if len(pad_group.items) > 3 else b""
    layers_node = F.child(pad_group, b"layers")
    layers = (
        BL.layer_set(token.value.decode() for token in layers_node.items[1:])
        if layers_node is not None
        else frozenset()
    )
    if kind == b"thru_hole":
        layers |= BL.all_cu(copper_count)
    if kind == b"np_thru_hole":
        size, drill = _size(pad_group, b"size"), F.child(pad_group, b"drill")
        hole = None
        offset = (0, 0)
        if drill is not None:
            numbers = [t.value for t in drill.items[1:] if t.kind == L.NUMBER]
            if numbers:
                dx = parse_board_units(numbers[0], major=10)
                hole = (dx, parse_board_units(numbers[1], major=10) if len(numbers) > 1 else dx)
            if F.child(drill, b"offset") is not None:
                offset = _size(drill, b"offset") or (0, 0)
        if size is not None and hole is not None and offset == (0, 0):
            if shape == b"circle" and size[0] <= hole[0]:
                return False
            if shape == b"oval" and size[0] <= hole[0] and size[1] <= hole[1]:
                return False
    return any(BL.is_copper(name) for name in layers)


def _matching_text(old: F.Text, candidates: list[tuple[int, F.Text]]) -> int | None:
    """getMatchingTextItem: the index of the library fp_text ``old``'s uuid goes to."""
    same = [(k, t) for k, t in candidates if t.key[1] == old.key[1]]
    if not same:
        return None
    if len(same) == 1:
        return same[0][0]
    on_layer = [(k, t) for k, t in same if t.layer == old.layer]
    if len(on_layer) == 1:
        return on_layer[0][0]
    at_place = [(k, t) for k, t in (on_layer or same) if t.local == old.local]
    if at_place:
        return at_place[0][0]
    if on_layer:
        return on_layer[0][0]
    return same[0][0]


#: A new item that keeps its own uuid, whatever the library gave it: no old item's.
OWN = ""
#: The fields every footprint holds, whose uuids KiCad 10.0.7 always carries.
_MANDATORY = frozenset(("property", name) for name, _ in F.MANDATORY_FIELDS)


def carried_ids(
    old_group,
    old: F.Footprint,
    new_group,
    new: F.Footprint,
    copper_count: int,
    *,
    major: int = 9,
) -> Ids:
    """The uuid each item of the exchanged footprint ``new`` (exchange's result; its
    library Group ``new_group``) carries from ``old`` (read from ``old_group``) by KiCad
    9.0.8's rules (module docstring): its pads aligned as read_ids aligns them, its texts
    as ``new.texts`` holds them (a mandatory field the library leaves out last); for each,
    the old item's uuid; OWN where it carries none; None where it carries the uuid of an
    old item the old file writes none for, which KiCad made up when it read the board.
    With ``major`` 10 or more, what KiCad 10.0.7's matching makes certain (module
    docstring), None for every other item, which that matching may pair or not."""
    if major >= 10:
        return _carried_ids_10(old_group, old, new)
    old_ids = read_ids(old_group)
    pads: list[str | None] = []
    old_pads = [
        (pad.number, is_on_copper(group, copper_count), uuid)
        for pad, group, uuid in zip(old.pads, F.pad_groups(old_group), old_ids.pads)
    ]
    for pad, group in zip(new.pads, F.pad_groups(new_group)):
        found = OWN
        if pad.number:
            copper = is_on_copper(group, copper_count)
            for number, old_copper, uuid in old_pads:
                if number == pad.number and old_copper == copper:
                    found = uuid
                    break
        pads.append(found)
    unwritten = (None,) * (len(old.texts) - len(old_ids.texts))
    old_texts = list(zip(old.texts, (*old_ids.texts, *unwritten)))
    texts: list[str | None] = [OWN] * len(new.texts)
    for k, text in enumerate(new.texts):
        if text.key[0] != "property":
            continue
        for old_text, uuid in old_texts:
            if old_text.key == text.key:
                texts[k] = uuid
                break
    candidates = [(k, t) for k, t in enumerate(new.texts) if t.key[0] == "fp_text"]
    for old_text, uuid in old_texts:
        if old_text.key[0] != "fp_text":
            continue
        k = _matching_text(old_text, candidates)
        if k is not None:
            texts[k] = uuid
    return Ids(old_ids.footprint, tuple(pads), tuple(texts))


def _carried_ids_10(old_group, old: F.Footprint, new: F.Footprint) -> Ids:
    """carried_ids for KiCad 10.0.7: the footprint's uuid; the old pad's for a pad whose
    number each footprint holds once; the old field's for the Reference, the Value, the
    Datasheet and the Description; None for every other item."""
    old_ids = read_ids(old_group)
    old_numbers = [p.number for p in old.pads]
    new_numbers = [p.number for p in new.pads]
    pads: list[str | None] = []
    for pad in new.pads:
        n = pad.number
        once = bool(n) and old_numbers.count(n) == new_numbers.count(n) == 1
        pads.append(old_ids.pads[old_numbers.index(n)] if once else None)
    unwritten = (None,) * (len(old.texts) - len(old_ids.texts))
    fields = {
        text.key: uuid
        for text, uuid in zip(old.texts, (*old_ids.texts, *unwritten))
        if text.key in _MANDATORY
    }
    texts = tuple(fields.get(text.key) for text in new.texts)
    return Ids(old_ids.footprint, tuple(pads), texts)
