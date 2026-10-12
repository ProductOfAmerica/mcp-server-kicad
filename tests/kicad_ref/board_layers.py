"""A board's layer table as KiCad reads it, and the layer pairs KiCad's flip uses.

KiCad holds one descriptor per board layer (struct LAYER, pcbnew/board.h:181-232 at
9.0.8): its canonical name, its type, the user name the file gives it, and its opposite,
the layer an item on it lands on when the footprint carrying it is flipped. 9.0.8 lines,
10.0.7 in brackets; the bodies cited are identical at both tags unless a difference is
named.

Reading a board (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp):

- parseLayer (1630-1684 [1899-1953]): a row is (ordinal "Name" type ["user name"]); the
  type goes through LAYER::ParseType (pcbnew/board.cpp:782-792 [892-910]), so signal,
  power, mixed, jumper, auxiliary, front and back are types and "user" is undefined;
  from format 20200922 on, the user name is the fourth atom and empty without one.
- parseLayers (1958-2092 [2265-2423]): the copper rows first, then every other row by
  its canonical name; each row goes to BOARD::SetLayerDescr, which reruns
  recalcOpposites (board.cpp:656-661 [760-765]), and only after the last row is the
  board's copper count set.
- A layer the table leaves out keeps the BOARD constructor's descriptor (board.cpp:
  101-111 [120-130]): copper typed signal, User.N auxiliary, everything else
  undefined, no user name.

The pairs, BOARD::recalcOpposites (board.cpp:795-853 [913-970]): every layer's opposite
is first ::FlipLayer( layer, copper count ) (common/layer_id.cpp:169-212 [173-218]),
which swaps the F and B copper, silk, adhesive, mask, paste, courtyard and fab layers
and leaves every other non-copper layer alone. Then, walking User.1, User.2, ... in turn:

- pass 1 pairs a user layer typed front or back with the first later one of the other
  type whose user name, after its first '.', is the same text, which must not be empty;
- pass 2 pairs User.N typed front with User.N+1 typed back when neither is paired yet
  and both user names differ from the canonical names. A row written without a user
  name reads as an empty one, which differs, so two consecutive unnamed front and back
  layers pair (measured: pcbnew 9.0.8 pairs User.1 front with User.2 back written
  without user names, and does not pair them when their user names are written equal
  to the canonical names, 2026-10-11).

Inner copper differs between the majors. KiCad 9.0.8's SetCopperLayerCount does not
rerun recalcOpposites (board.cpp:869-872), so the last run is the one the last
SetLayerDescr made, while the count was still the new board's, and ::FlipLayer leaves
every inner layer where it is (measured: In1.Cu to In5.Cu map to themselves on boards of
2, 4 and 6 copper layers, pcbnew 9.0.8, 2026-10-11). KiCad 10.0.7's reruns it with the
board's count (board.cpp:986-990), and its ::FlipLayer mirrors In<k>.Cu through the inner
stack, In<n-1-k>.Cu on a board of n copper layers, clamped to In1.Cu and In<n-2>.Cu.

BOARD::FlipLayer (board.cpp:856-860 [973-977]) returns a layer's opposite.
BOARD_ITEM::IsSideSpecific (pcbnew/board_item.cpp:156-170 [224-238]): an item on a layer
of LSET::SideSpecificMask (common/lset.cpp:719-723 [736-740]: the F and B silk, mask,
adhesive, paste, courtyard and fab layers and every copper layer), or on a layer whose
type is front or back (BOARD::GetLayerType, board.cpp:734-750 [844-860]).

An item's layer list may use the parser's wildcards (init, 115-123 [120-128]): *.Cu for
all 32 copper layers, *In.Cu, F&B.Cu, *.Adhes, *.Paste, *.Mask, *.SilkS, *.Fab, *.CrtYd.
KiCad's writer spells a pad on every copper layer of its board *.Cu (formatLayers,
written_pad_layers below), so a pad holding the board's copper layers and no others
reads back on all 32.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from kicad_ref import dsn_lexer as L

#: PCB_LAYER_ID of the fixed layers (include/layer_ids.h:64-169 at 9.0.8, the same
#: numbering at 10.0.7): copper even, In<k>.Cu at 2k + 2, User.N at 37 + 2N.
_FIXED = {
    "F.Cu": 0,
    "B.Cu": 2,
    "F.Mask": 1,
    "B.Mask": 3,
    "F.SilkS": 5,
    "B.SilkS": 7,
    "F.Adhes": 9,
    "B.Adhes": 11,
    "F.Paste": 13,
    "B.Paste": 15,
    "Dwgs.User": 17,
    "Cmts.User": 19,
    "Eco1.User": 21,
    "Eco2.User": 23,
    "Edge.Cuts": 25,
    "Margin": 27,
    "B.CrtYd": 29,
    "F.CrtYd": 31,
    "B.Fab": 33,
    "F.Fab": 35,
    "Rescue": 37,
}
_NAMES = {v: k for k, v in _FIXED.items()}
IN1_CU = 4
USER_1 = 39
LAYER_COUNT = 128
MAX_CU_LAYERS = 32
_INNER = re.compile(r"In([1-9][0-9]?)\.Cu")
_USER = re.compile(r"User\.([1-9][0-9]?)")

#: ::FlipLayer's fixed pairs.
_STANDARD_PAIRS = {
    "F.Cu": "B.Cu",
    "F.SilkS": "B.SilkS",
    "F.Adhes": "B.Adhes",
    "F.Mask": "B.Mask",
    "F.Paste": "B.Paste",
    "F.CrtYd": "B.CrtYd",
    "F.Fab": "B.Fab",
}
_STANDARD_PAIRS.update({b: f for f, b in list(_STANDARD_PAIRS.items())})

#: LSET::BackTechMask and FrontTechMask (common/lset.cpp:631-649 at 9.0.8, 648-666 at
#: 10.0.7).
TECH_LAYERS = frozenset(
    f"{side}.{kind}"
    for side in "FB"
    for kind in ("SilkS", "Mask", "Adhes", "Paste", "CrtYd", "Fab")
)

LT_SIGNAL, LT_POWER, LT_MIXED, LT_JUMPER, LT_AUX, LT_FRONT, LT_BACK, LT_UNDEFINED = (
    "signal",
    "power",
    "mixed",
    "jumper",
    "auxiliary",
    "front",
    "back",
    "undefined",
)
_TYPES = {LT_SIGNAL, LT_POWER, LT_MIXED, LT_JUMPER, LT_AUX, LT_FRONT, LT_BACK}


def layer_id(name: str) -> int:
    """The PCB_LAYER_ID of a canonical layer name; ValueError for any other text."""
    if name in _FIXED:
        return _FIXED[name]
    inner = _INNER.fullmatch(name)
    if inner and 1 <= int(inner.group(1)) <= 30:
        return IN1_CU + 2 * (int(inner.group(1)) - 1)
    user = _USER.fullmatch(name)
    if user and 1 <= int(user.group(1)) <= 45:
        return USER_1 + 2 * (int(user.group(1)) - 1)
    raise ValueError(f"{name!r} is no canonical layer name")


def layer_name(lid: int) -> str:
    """The canonical name of a PCB_LAYER_ID (LSET::Name)."""
    if lid in _NAMES:
        return _NAMES[lid]
    if lid % 2 == 0 and IN1_CU <= lid < 64:
        return f"In{(lid - IN1_CU) // 2 + 1}.Cu"
    if lid % 2 == 1 and USER_1 <= lid < LAYER_COUNT:
        return f"User.{(lid - USER_1) // 2 + 1}"
    raise ValueError(f"no layer {lid}")


def is_copper(name: str) -> bool:
    """IsCopperLayer: an even PCB_LAYER_ID."""
    return layer_id(name) % 2 == 0


def all_cu(count: int = MAX_CU_LAYERS) -> frozenset[str]:
    """LSET::AllCuMask( count ): F.Cu, In1.Cu to In<count - 2>.Cu and B.Cu."""
    return frozenset(["F.Cu", "B.Cu", *(f"In{k}.Cu" for k in range(1, count - 1))])


#: The parser's wildcard layer names and the sets they read as.
WILDCARDS = {
    "*.Cu": all_cu(),
    "*In.Cu": all_cu() - {"F.Cu", "B.Cu"},
    "F&B.Cu": frozenset({"F.Cu", "B.Cu"}),
    "*.Adhes": frozenset({"F.Adhes", "B.Adhes"}),
    "*.Paste": frozenset({"F.Paste", "B.Paste"}),
    "*.Mask": frozenset({"F.Mask", "B.Mask"}),
    "*.SilkS": frozenset({"F.SilkS", "B.SilkS"}),
    "*.Fab": frozenset({"F.Fab", "B.Fab"}),
    "*.CrtYd": frozenset({"F.CrtYd", "B.CrtYd"}),
}


def layer_set(names) -> frozenset[str]:
    """The layers a list of layer names reads as (wildcards expanded); ValueError for a
    name that is neither canonical nor a wildcard."""
    out: set[str] = set()
    for name in names:
        if name in WILDCARDS:
            out |= WILDCARDS[name]
        else:
            layer_id(name)
            out.add(name)
    return frozenset(out)


def written_pad_layers(layers: frozenset[str], copper_count: int) -> frozenset[str]:
    """The layers a pad holding ``layers`` reads as once KiCad has written it on a board of
    ``copper_count`` copper layers. A pad's layers go through formatLayers unenumerated
    (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:1545 at 9.0.8, 1791 at 10.0.7),
    which writes *.Cu for a pad on every copper layer of the board, clearing every copper
    bit, and for a pad on F.Cu and B.Cu and no other copper layer of the board, clearing
    those two and naming any other copper layer it holds after (1345-1429 at 9.0.8,
    1550-1632 at 10.0.7); the parser reads *.Cu as all 32 copper layers (WILDCARDS).
    Either way the pad reads back on all 32 and on the rest of its layers. Every other
    layer list reads back as it was: a layer named one by one, or a wildcard pair whose
    two layers the pad holds."""
    board = all_cu(copper_count)
    on_board = layers & board
    if on_board == board or on_board == WILDCARDS["F&B.Cu"]:
        return layers | WILDCARDS["*.Cu"]
    return layers


class Row(NamedTuple):
    name: str  # canonical
    type: str
    user_name: str  # the fourth atom, "" without one


class LayerTable(NamedTuple):
    rows: tuple[Row, ...]

    @property
    def copper_count(self) -> int:
        return sum(1 for row in self.rows if is_copper(row.name))


def read_layer_table(board) -> LayerTable:
    """The (layers ...) table of a board, the lexer port's Group of a (kicad_pcb ...)
    list. Refuses (ValueError) a board older than format 20200922, whose rows hold no
    user name, and a row whose name is not canonical."""
    version = next(
        (int(c.items[1].value) for c in board.items if _head(c) == b"version"),
        None,
    )
    if version is None or version < 20200922:
        raise ValueError(f"a board at format {version} is not modelled")
    table = next((c for c in board.items if _head(c) == b"layers"), None)
    if table is None:
        raise ValueError("the board has no (layers ...) table")
    rows = []
    for row in table.items[1:]:
        if row.kind != L.GROUP or len(row.items) < 3:
            raise ValueError(f"a layer row at byte {row.start} is not (ordinal name type)")
        name = row.items[1].value.decode()
        layer_id(name)
        kind = row.items[2].value.decode()
        rest = row.items[3:]
        user = rest[0].value.decode() if rest and rest[0].kind == L.STRING else ""
        rows.append(Row(name, kind if kind in _TYPES else LT_UNDEFINED, user))
    return LayerTable(tuple(rows))


def _head(item) -> bytes | None:
    if item.kind == L.GROUP and item.items and item.items[0].kind in L.ATOM_KINDS:
        return item.items[0].value
    return None


def _descriptors(table: LayerTable) -> dict[int, tuple[str, str]]:
    """(type, user name) per PCB_LAYER_ID, the constructor's defaults under the table's
    rows."""
    out = {}
    for lid in range(LAYER_COUNT):
        if lid % 2 == 0 and lid < 64:
            kind = LT_SIGNAL
        elif lid >= USER_1 and lid % 2 == 1:
            kind = LT_AUX
        else:
            kind = LT_UNDEFINED
        out[lid] = (kind, "")
    for row in table.rows:
        out[layer_id(row.name)] = (row.type, row.user_name)
    return out


def flip_layer_standalone(name: str, copper_count: int, *, major: int) -> str:
    """::FlipLayer( layer, copper count ) of KiCad ``major``."""
    if name in _STANDARD_PAIRS:
        return _STANDARD_PAIRS[name]
    lid = layer_id(name)
    if lid % 2 == 1 or copper_count < 4:
        return name
    inner = (lid - IN1_CU) // 2
    if major >= 10:
        flipped = min(max(copper_count - 3 - inner, 0), copper_count - 3)
        return layer_name(IN1_CU + 2 * flipped)
    fliplayer = min(max(copper_count - 2 - (lid - IN1_CU), 0), 2)
    return layer_name(fliplayer)


def opposites(table: LayerTable, *, major: int) -> dict[str, str]:
    """Every layer's opposite as KiCad ``major`` holds it after reading the board
    (module docstring): recalcOpposites with the copper count of its last run."""
    count = table.copper_count if major >= 10 else 2
    desc = _descriptors(table)
    opposite = {
        lid: layer_id(flip_layer_standalone(layer_name(lid), count, major=major))
        for lid in range(LAYER_COUNT)
        if lid < 64 or lid % 2 == 1
    }

    def principal(lid: int) -> str:
        user = desc[lid][1]
        return user.split(".", 1)[1] if "." in user else ""

    for lid in range(USER_1, LAYER_COUNT, 2):
        if opposite[lid] != lid or desc[lid][0] not in (LT_FRONT, LT_BACK):
            continue
        for other in range(lid + 2, LAYER_COUNT, 2):
            if opposite[other] != other or desc[other][0] not in (LT_FRONT, LT_BACK):
                continue
            if desc[lid][0] == desc[other][0]:
                continue
            candidate = principal(other)
            if candidate and candidate == principal(lid):
                opposite[lid], opposite[other] = other, lid
                break
    for lid in range(USER_1, LAYER_COUNT - 2, 2):
        following = lid + 2
        if opposite[lid] != lid or opposite[following] != following:
            continue
        if desc[lid][0] != LT_FRONT or desc[following][0] != LT_BACK:
            continue
        if desc[lid][1] != layer_name(lid) and desc[following][1] != layer_name(following):
            opposite[lid], opposite[following] = following, lid
    return {layer_name(lid): layer_name(other) for lid, other in opposite.items()}


def flip_layer(table: LayerTable, name: str, *, major: int) -> str:
    """BOARD::FlipLayer of KiCad ``major`` on the board whose table is ``table``."""
    return opposites(table, major=major)[name]


def is_side_specific(table: LayerTable, name: str) -> bool:
    """BOARD_ITEM::IsSideSpecific of an item on the layer ``name``."""
    if name in TECH_LAYERS or is_copper(name):
        return True
    return _descriptors(table)[layer_id(name)][0] in (LT_FRONT, LT_BACK)
