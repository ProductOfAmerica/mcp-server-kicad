"""A footprint as KiCad reads it, and what KiCad's own footprint operations do to it.

``read_footprint`` reads a footprint from the lexer port's Group of a (footprint ...) list,
written in a .kicad_mod or on a board, the way the board parser does
(pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp, 9.0.8 lines, 10.0.7 in brackets):

- (at x y angle): the position through parseBoardUnits and the orientation through
  FOOTPRINT::SetOrientation, which stores Normalize180 of parseDouble; no angle atom is
  0, and a token that is not a NUMBER refuses the file (4533-4549 [5227-5243]);
  FOOTPRINT::SetOrientation (pcbnew/footprint.cpp:2641-2669 [3099-3137]) normalizes;
- (layer ...): the footprint's side, B.Cu when the file says B.Cu and F.Cu for anything
  else, since a footprint sits on one of the two (4510-4519 [5198-5207]);
- pads, through kicad_ref.pad: the number, which the parser clears for a non-plated hole
  and for a pad on no copper layer (PAD::CanHaveNumber, 5558-5563 [6584-6589]), the
  position, the absolute angle, and what a flip
  changes: the layers (the parser's wildcards expanded, kicad_ref.board_layers), the shape
  offset (drill (offset x y)), the trapezoid delta (rect_delta), the chamfered corners and
  a custom pad's primitives, in the pad's own frame. A plated through-hole pad read from
  a board also holds every copper layer of the board, which FOOTPRINT::FixUpPadsForBoard
  adds once the footprint is read (4978 [5793]; pcbnew/footprint.cpp:4167-4185
  [4834-4853], where KiCad 10 skips a footprint with a stackup of its own, a construct
  this port does not read);
- texts (the fields, written as (property ...), and the fp_text items), through
  kicad_ref.pcb_text. A (property ...) named ki_fp_filters is the
  footprint's filters and one named Footprint is dropped since KiCad 9, so neither is a
  text KiCad holds (4611-4636 [5306-5321]). In a file before format 20230620 ("PCB
  Fields", pcb_io_kicad_sexpr.h:147 [153]; FIELDS_VERSION), the format the footprint is
  read at being its board's or, in a library, its own (format_version), neither is a
  (property ...) named ki_keywords, ki_locked, ki_description, Sheetfile or Sheetname
  (either spelling), which KiCad skips or takes as the Description's text or the
  footprint's sheet (4574-4607 [5266-5299]). A legacy (fp_text reference|value ...), the
  only form such a file has for those two, is the Reference or Value field at any
  format version: parsePCB_TEXT makes a field of it (3376-3382 [3893-3899]) and the
  footprint's T_fp_text case copies that whole into its Reference or Value (4821-4850
  [5607-5640]). A field written twice, in either form, is one field: KiCad reads a later
  (property ...) into the field the earlier one made (4637-4641 [5324-5328]), which
  resets its keep-upright and its angle and takes the position, layer and justification
  the later one writes (parsePCB_TEXT_effects, 3438-3576 [3955-4093]; parseEDA_TEXT,
  505-515 [736-750]), and a later legacy fp_text replaces the field whole, so the later
  one is read and the earlier one is not. Fifteen of KiCad 9.0.8's stock footprints, its
  M.2 edge connectors, write Reference and Value twice at different places (counted
  2026-10-11). A later (property ...) that leaves out its (at), (layer) or (effects)
  keeps some of the earlier one's, and this port refuses it (ValueError).
  ``reference_and_value`` reads the two fields' texts, which a Text does not hold; an
  fp_text's content follows its type, or the bare ``locked`` after it that KiCad 6 and 7
  wrote (content_token).
  A text written without (layer) keeps the layer it was made with: F.SilkS for
  Reference and F.Fab for Value, Datasheet and Description, whatever the footprint's side
  (addMandatoryFields, below; measured: pcbnew 9.0.8 reads such a Reference on a B.Cu
  footprint on F.SilkS, unmirrored, 2026-10-11); F.Fab or B.Fab by the footprint's side
  for any other field (4644-4651 [5330-5338]), which on a board then takes
  StyleFromSettings' mirror for a back layer until an (effects) resets it; F.SilkS or
  B.SilkS by the side for an fp_text, a legacy Reference or Value included, which
  parsePCB_TEXT makes afresh (the PCB_TEXT constructor, pcbnew/pcb_text.cpp:56-76
  [61-79]);
- graphics (fp_line, fp_rect, fp_circle, fp_arc, fp_poly, fp_curve): their points in the
  footprint's own frame, as written;
- zones the footprint owns, and KiCad 10's points (PCB_POINT): in BOARD coordinates, as
  written, since format( ZONE ) writes a zone's outline through formatPolyPts without
  the footprint (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:2628-2637 at 9.0.8,
  3061-3074 at 10.0.7) where a footprint's own shapes pass it, and format( PCB_POINT )
  writes the point's position as it stands (1157-1168 at 10.0.7; parsePCB_POINT,
  pcb_io_kicad_sexpr_parser.cpp:8709-8756 at 10.0.7). A zone's layers are the names its
  (layer ...) or (layers ...) gives, the parser's wildcards expanded
  (kicad_ref.board_layers); KiCad cuts a board-level zone's layers to the board's
  enabled ones after loading, but not a footprint's zone's (1239-1249 [1510-1520]);
- the four fields every footprint holds from its constructor (addMandatoryFields,
  pcbnew/footprint.cpp:94-111 at 9.0.8; the constructor's own body, 105-117 at 10.0.7):
  Reference on F.SilkS, Value, Datasheet and Description on F.Fab, keep-upright (the
  PCB_TEXT constructor, pcbnew/pcb_text.cpp:56-76 [61-79]), centred, at the footprint's
  position and angle 0.
  One the file leaves out stays that way, turned only by the SetOrientation the
  footprint's own (at) angle calls, so it reads at Normalize( 0 + angle ), the angle as
  parsed. The file's own fields replace them.

The parser applies each token in turn, so a pad or text written before the footprint's
(at) is turned by its angle; KiCad writes (at) first, and this port refuses (ValueError)
a footprint that does not, rather than read it otherwise. It refuses a hidden fp_text
too, which KiCad 9's parser turns into a field under a name of its own making
(parsePCB_TEXT, 3419-3427 [3936-3944]).

Positions are kept footprint-relative, as the file writes them. KiCad itself holds board
positions and turns them through RotatePoint, so after a rotation off the quarter turns
the footprint-relative position it writes back can differ from the one it read by a
nanometre or two (BOARD_ITEM::GetFPRelativePosition, pcbnew/board_item.cpp:334-345
[399-410]); that rounding is not modelled here, and comparisons with pcbnew allow it.

The operations (pcbnew/footprint.cpp, identical in substance at both tags):

- SetPosition (2553-2584 [3002-3039]): every child moves with the footprint, so nothing
  footprint-relative changes; each zone and each point is moved by the same delta
  (zone->Move( delta ) and point->Move( delta ) in its loops at 10.0.7; ZONE::Move,
  pcbnew/zone.cpp:904-929 at 9.0.8, adds it to every outline and fill point), exact
  integer additions;
- SetOrientation (2641-2669 [3099-3137]): change = new - old (doubles), the orientation
  becomes Normalize180( new ), every pad and text is turned by the change (PAD::Rotate,
  PCB_TEXT::Rotate); graphics keep their footprint-relative geometry; every zone point
  and point is turned about the footprint's position by the change with the integer
  RotatePoint (ZONE::Rotate, zone.cpp:959-967 at 9.0.8, through SHAPE_LINE_CHAIN::Rotate,
  libs/kimath/src/geometry/shape_line_chain.cpp:483-490 at 9.0.8; PCB_POINT::Rotate,
  pcbnew/pcb_point.cpp:138-141 at 10.0.7; kicad_ref.trigo.rotate_point_about), whose
  rounding is the major's KiROUND;
- Rotate (2456-2479 [2898-2918]): returns at once when the angle is exactly 0, else
  SetOrientation( orientation + angle ) and then KeepUpright on every field and every
  fp_text (PCB_TEXT_T; a text box is not one);
- Flip about the footprint's own position, LEFT_RIGHT (2491-2550 [2930-2999]; what
  SetLayerAndFlip runs, 2482-2488 [2921-2927]): the footprint's layer goes to its
  opposite; the new orientation is Normalize180( -orientation ), and with the held one
  set to 0 every field and pad is flipped top to bottom about the footprint, then the
  zones and drawings (graphics and fp_text items) are, and then the footprint is Rotated
  by 180 degrees about its position, which turns every pad and text by d = ( new + 180 ) -
  new (not always exactly 180 in doubles) and runs KeepUpright. In the file's
  footprint-relative terms that is: every local y negated; a pad's angle
  Normalize( Normalize( -P ) + d ) (PAD::Flip, pcbnew/pad.cpp:1029-1093 [1504-1565], sets
  the footprint-relative angle to its negation while the footprint holds 0), its shape
  offset and trapezoid delta mirrored in y, its top and bottom chamfered corners swapped,
  its layers through BOARD::FlipLayer and its primitives flipped about the pad's own
  origin (FlipPrimitives, 1096-1111 [1568-1583]); a text's angle
  Normalize( ( 180 - T ) + d ) and then KeepUpright, its layer through FlipLayer, its
  mirror toggled only when its new layer is side specific (PCB_TEXT::Flip,
  pcbnew/pcb_text.cpp:414-431 [493-510]); a graphic's layer through FlipLayer, an arc's
  start and end swapped (EDA_SHAPE::flip, common/eda_shape.cpp:697-737 [991-1033];
  PCB_SHAPE::Flip, pcbnew/pcb_shape.cpp:492-497 [600-605]), and a rectangle becomes the
  polygon of its corners when d is not a multiple of 90 (EDA_SHAPE::rotate, 636-694
  [930-988]). A zone is mirrored top to bottom about the footprint's position, y going
  to 2 fy - y, and its layers and its fills' layers go to their opposites (ZONE::Flip,
  zone.cpp:970-993 at 9.0.8, through SHAPE_LINE_CHAIN::Mirror, shape_line_chain.cpp:
  962-974); a KiCad 10 point is mirrored the same way and its layer goes to its opposite
  (PCB_POINT::Flip, pcb_point.cpp:144-149 at 10.0.7, whatever the comment above its call
  in FOOTPRINT::Flip says); then the turn by d turns both about the position. Which
  layer is whose opposite, and which layers are side specific, is the board's
  (kicad_ref.board_layers); a 3D model is left as it is.
- placing a library footprint (FootprintLoad, then SetPosition, then Rotate or
  SetOrientation): the library footprint reads with orientation 0 at the origin.

``turn_to`` is the turn a tool's rotation asks for, the footprint turned to the angle the
rotation names: nothing at all when Normalize180 of it is the orientation the footprint
holds, else SetOrientation to it and KeepUpright, which is FOOTPRINT::Rotate by the change
wherever the orientation plus the change is that angle in doubles. The board editor's
footprint properties dialog does the same up to a whole turn in the change (it runs Rotate
by the typed angle Normalize()d less the one held, and none when the two are equal,
pcbnew/dialogs/dialog_footprint_properties.cpp:636-639 at 9.0.8), but for a footprint held
at an angle below 0 and typed again, which it turns by 360.

``canonical`` is the upright form: a keep-upright text in the form KeepUpright leaves it
(angle below 180, the justification negated if it was turned), any other text at its angle
Normalize()d. Neither KiCad's parser runs KeepUpright on load (parsePCB_TEXT_effects only
sets the flag, pcb_io_kicad_sexpr_parser.cpp:3447, 3476 and 3532 at 9.0.8, 3964, 3993 and
4049 at 10.0.7), and PCB_TEXT::GetDrawRotation folds a keep-upright text's angle into
(-90, 90] without touching its justification (pcbnew/pcb_text.cpp:181-200 at 9.0.8, 219-238
at 10.0.7), so a text and the one KeepUpright makes of it are two states KiCad holds, and a
justified one draws on the other side of its anchor. The upright form holds the two as one:
it states the laws KiCad's operations keep only up to KeepUpright (Rotate against
SetOrientation, four quarter turns or two flips from a text not yet upright), never what a
tool must write.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import NamedTuple

from kicad_ref import board_layers as BL
from kicad_ref import dsn_lexer as L
from kicad_ref import pad as PAD
from kicad_ref import pcb_text as T
from kicad_ref.eda_units import format_angle
from kicad_ref.parse_units import ParseRefusal, parse_board_units, parse_double
from kicad_ref.trigo import normalize, normalize180, rotate_point_about

Point = tuple[int, int]

#: The graphic items read, with the point lists each one carries in its own frame.
SHAPE_HEADS = (b"fp_line", b"fp_rect", b"fp_circle", b"fp_arc", b"fp_poly", b"fp_curve")
#: A custom pad's primitives (pcb_io_kicad_sexpr_parser.cpp:5426-5458 at 9.0.8).
PRIMITIVE_HEADS = (
    b"gr_line",
    b"gr_vector",
    b"gr_rect",
    b"gr_bbox",
    b"gr_arc",
    b"gr_circle",
    b"gr_curve",
    b"gr_poly",
)
_POINT_HEADS = (b"start", b"mid", b"end", b"center")
#: Properties that are no text KiCad holds (see the module docstring).
_NOT_TEXTS = (b"ki_fp_filters", b"Footprint")
#: The format from which on a footprint's (property ...) lists are fields as written, and
#: the properties that are no text in a file before it (module docstring).
FIELDS_VERSION = 20230620
_LEGACY_NOT_TEXTS = (
    b"ki_keywords",
    b"ki_locked",
    b"ki_description",
    b"Sheetfile",
    b"Sheet file",
    b"Sheetname",
    b"Sheet name",
)
#: The legacy fp_text types that are fields, with the field each is (module docstring).
LEGACY_FIELDS = {b"reference": b"Reference", b"value": b"Value"}
_H = {b"left": T.LEFT, b"right": T.RIGHT}
_V = {b"top": T.TOP, b"bottom": T.BOTTOM}


class Shape(NamedTuple):
    kind: str
    layer: str | None
    points: tuple[Point, ...]


class Pad(NamedTuple):
    number: str
    local: Point
    angle: float  # absolute on the board, [0, 360)
    layers: frozenset[str] = frozenset()  # wildcards expanded
    offset: Point = (0, 0)
    delta: Point = (0, 0)
    chamfer: frozenset[str] = frozenset()
    primitives: tuple[Shape, ...] = ()
    kind: str = "smd"


class Text(NamedTuple):
    """``key`` is ("property", name) for a field, ("fp_text", text) for any other text.
    ``default`` marks a mandatory field the file leaves out, which KiCad holds as its
    constructor made it."""

    key: tuple[str, str]
    local: Point | None  # None for a text written without (at)
    style: T.Style
    layer: str | None
    default: bool = False


class Zone(NamedTuple):
    """A zone the footprint owns, in board coordinates: the layers its (layer ...) or
    (layers ...) reads as (wildcards expanded); each (polygon (pts ...)) as its points in
    order, an (arc ...) among them giving its start, mid and end (the first polygon is
    the outline, KiCad reads any later one as a hole in it); each (filled_polygon (layer)
    (pts ...)) as (layer, points)."""

    layers: frozenset[str]
    polygons: tuple[tuple[Point, ...], ...]
    fills: tuple[tuple[str, tuple[Point, ...]], ...] = ()


class BoardPoint(NamedTuple):
    """A KiCad 10 point (PCB_POINT) the footprint owns: its position in board
    coordinates and its layer."""

    position: Point
    layer: str | None


class Footprint(NamedTuple):
    position: Point
    orientation: float  # Normalize180
    layer: str
    pads: tuple[Pad, ...]
    texts: tuple[Text, ...]
    shapes: tuple[Shape, ...]
    zones: tuple[Zone, ...] = ()
    points: tuple[BoardPoint, ...] = ()


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def head(item) -> bytes | None:
    """A list's first atom, or None."""
    if item.kind == L.GROUP and item.items and item.items[0].kind in L.ATOM_KINDS:
        return item.items[0].value
    return None


def children(group, name: bytes) -> list:
    return [c for c in group.items if head(c) == name]


def child(group, name: bytes):
    """The first child list headed ``name``, or None."""
    found = children(group, name)
    return found[0] if found else None


def _number(token) -> float:
    if token.kind != L.NUMBER:
        raise ParseRefusal(f"need a number, got the {token.kind} {token.value!r}")
    return parse_double(token.value)


def _point(group, *, major: int) -> Point:
    for token in group.items[1:3]:
        if token.kind != L.NUMBER:
            raise ParseRefusal(f"need a number, got the {token.kind} {token.value!r}")
    return (
        parse_board_units(group.items[1].value, major=major),
        parse_board_units(group.items[2].value, major=major),
    )


def _read_pad(group, *, major: int, copper_count: int | None) -> Pad:
    at = child(group, b"at")
    if at is None:
        raise ParseRefusal(f"the pad at byte {group.start} has no (at)")
    written = _number(at.items[3]) if len(at.items) > 3 else None
    kind = group.items[2].value.decode() if len(group.items) > 2 else "smd"
    layers_node = child(group, b"layers")
    layers = (
        BL.layer_set(token.value.decode() for token in layers_node.items[1:])
        if layers_node is not None
        else frozenset()
    )
    number = group.items[1].value.decode()
    no_copper = layers_node is not None and not any(BL.is_copper(name) for name in layers)
    if kind == "np_thru_hole" or no_copper:
        # PAD::CanHaveNumber: a non-plated hole and an aperture pad (no copper layer) get
        # no number, and the parser clears one a file gives them (pcb_io_kicad_sexpr_
        # parser.cpp:5558-5563 at 9.0.8, 6584-6589 at 10.0.7; pcbnew/pad.cpp:281-292
        # [311-322]).
        number = ""
    if kind == "thru_hole" and copper_count is not None:
        layers |= BL.all_cu(copper_count)
    offset: Point = (0, 0)
    drill = child(group, b"drill")
    if drill is not None and child(drill, b"offset") is not None:
        offset = _point(child(drill, b"offset"), major=major)
    delta_node = child(group, b"rect_delta")
    delta = _point(delta_node, major=major) if delta_node is not None else (0, 0)
    chamfer_node = child(group, b"chamfer")
    chamfer = (
        frozenset(token.value.decode() for token in chamfer_node.items[1:])
        if chamfer_node is not None
        else frozenset()
    )
    primitives_node = child(group, b"primitives")
    primitives = []
    for item in primitives_node.items if primitives_node is not None else ():
        name = head(item)
        if name in PRIMITIVE_HEADS:
            primitives.append(Shape(name.decode(), None, _shape_points(item, major=major)))
    return Pad(
        number,
        _point(at, major=major),
        PAD.loaded_angle(written),
        layers,
        offset,
        delta,
        chamfer,
        tuple(primitives),
        kind,
    )


def pad_groups(group) -> list:
    """The (pad ...) lists of a footprint, in the order read_footprint reads its pads."""
    return children(group, b"pad")


def field_name(item) -> bytes | None:
    """The field a footprint's child list makes or updates: a (property ...)'s own name,
    Reference or Value for a legacy (fp_text reference|value ...) (LEGACY_FIELDS), None for
    any other child."""
    name = head(item)
    if name == b"property" and len(item.items) > 1:
        return item.items[1].value
    if name == b"fp_text" and len(item.items) > 1:
        return LEGACY_FIELDS.get(item.items[1].value)
    return None


def format_version(group, version: int | None = None) -> int:
    """The format KiCad reads the (footprint ...) Group ``group`` at (module docstring):
    ``version`` when one is given, a board's footprint being read at its board's; else the
    group's own (version ...), a library file's; else FIELDS_VERSION."""
    if version is not None:
        return version
    node = child(group, b"version")
    if node is not None and len(node.items) > 1 and node.items[1].kind == L.NUMBER:
        return int(node.items[1].value)
    return FIELDS_VERSION


def text_groups(group, *, version: int | None = None) -> list:
    """The lists of a footprint that read as texts, in the order read_footprint reads them
    at ``version`` (format_version). A list making a field that a later one makes or
    updates again, in either form, is not among them: KiCad reads the later one into the
    same field (module docstring)."""
    at = format_version(group, version)
    found = [
        item
        for item in group.items
        if head(item) in (b"property", b"fp_text") and _text_key(item, at) is not None
    ]
    last = {field_name(item): item for item in found if field_name(item) is not None}
    return [item for item in found if field_name(item) is None or last[field_name(item)] is item]


def _check_repeated_fields(group, version: int) -> None:
    """Refuse (ValueError) a field written twice, in either form, whose last list is a
    (property ...) leaving out its (at), (layer) or (effects): KiCad then keeps what the
    earlier one set for it, which this port does not model."""
    names = [field_name(item) for item in group.items]
    for item in text_groups(group, version=version):
        if head(item) != b"property" or names.count(field_name(item)) < 2:
            continue
        missing = [n.decode() for n in (b"at", b"layer", b"effects") if child(item, n) is None]
        if missing:
            raise ValueError(
                f"the repeated field {item.items[1].value.decode()!r} leaves out"
                f" {', '.join(missing)}: not modelled"
            )


def reference_and_value(group) -> tuple[str, str]:
    """The texts of the Reference and Value fields KiCad holds once it has read the
    (footprint ...) Group ``group`` (module docstring): every list making or updating one,
    a (property "Reference"|"Value" ...) or a legacy (fp_text reference|value ...), read
    in turn, a later one's text replacing an earlier one's; a legacy fp_text's text with
    every %V replaced by ${VALUE} and then every %R by ${REFERENCE}, as parsePCB_TEXT
    replaces them (3411-3412 [3928-3929]); "" for a field written neither way, the text
    addMandatoryFields leaves it. The format version changes neither: the rules a file
    before 20230620 has for its properties name other properties (4574-4607
    [5266-5299]). Raises ParseRefusal for a list with no text atom, which KiCad refuses."""
    texts = {b"Reference": "", b"Value": ""}
    for item in group.items:
        name = field_name(item)
        if name not in texts:
            continue
        token = content_token(item)
        if token is None or token.kind not in L.ATOM_KINDS:
            raise ParseRefusal(f"the {name.decode()} at byte {item.start} has no text")
        text = token.value.decode()
        if head(item) == b"fp_text":
            text = text.replace("%V", "${VALUE}").replace("%R", "${REFERENCE}")
        texts[name] = text
    return texts[b"Reference"], texts[b"Value"]


def content_token(item):
    """The token holding a text list's content, or None: its third item, or its fourth
    after the bare ``locked`` KiCad 6 and 7 wrote after an fp_text's type, which the
    parser skips (parsePCB_TEXT, 3400-3405 [3917-3922]); a quoted "locked" is content."""
    k = 2
    if head(item) == b"fp_text" and len(item.items) > 3:
        third = item.items[2]
        if third.kind == L.SYMBOL and third.value == b"locked":
            k = 3
    return item.items[k] if len(item.items) > k else None


def _text_key(group, version: int) -> tuple[str, str] | None:
    name = head(group)
    if name == b"property":
        if group.items[1].value in _NOT_TEXTS:
            return None
        if version < FIELDS_VERSION and group.items[1].value in _LEGACY_NOT_TEXTS:
            return None
        return ("property", group.items[1].value.decode())
    token = content_token(group)
    if token is None or token.kind not in L.ATOM_KINDS:
        raise ParseRefusal(f"the fp_text at byte {group.start} has no text")
    kind, text = group.items[1].value, token.value.decode()
    if kind == b"reference":
        return ("property", "Reference")
    if kind == b"value":
        return ("property", "Value")
    return ("fp_text", text)


def _default_layer(group, key: tuple[str, str], *, back: bool) -> str:
    """The layer a text written without (layer) keeps (module docstring)."""
    for name, layer in MANDATORY_FIELDS:
        if key == ("property", name) and head(group) == b"property":
            return layer
    if head(group) == b"property":
        return "B.Fab" if back else "F.Fab"
    return "B.SilkS" if back else "F.SilkS"


def _read_text(group, orientation: float, *, major: int, back: bool, on_board: bool, version: int):
    key = _text_key(group, version)
    if key is None:
        return None
    local = None
    written = 0.0
    keep_upright = True
    h = v = T.CENTER
    layer = None
    for item in group.items:
        if head(item) == b"layer":
            layer = item.items[1].value.decode()
    if layer is None:
        layer = _default_layer(group, key, back=back)
    # A user field new to a footprint on a board takes StyleFromSettings, whose mirror is
    # that of a back layer (4648-4651 [5335-5338]; pcbnew/pcb_text.cpp:307-318 [370-386]),
    # until an (effects) resets it (parseEDA_TEXT).
    mirror = head(group) == b"property" and key not in _MANDATORY_KEYS and on_board and back
    for item in group.items:
        name = head(item)
        if name == b"at":
            local = _point(item, major=major)
            rest = item.items[3:]
            if rest and rest[0].kind == L.NUMBER:
                written = parse_double(rest[0].value)
                rest = rest[1:]
            if rest and rest[0].kind == L.SYMBOL and rest[0].value == b"unlocked":
                keep_upright = False
        elif name == b"unlocked":
            word = item.items[1].value
            if word not in (b"yes", b"no"):
                raise ParseRefusal(f"(unlocked {word!r}) is no bool KiCad reads")
            keep_upright = word == b"no"
        elif name == b"effects":
            h = v = T.CENTER
            mirror = False
            for words in children(item, b"justify"):
                for word in words.items[1:]:
                    if word.value in _H:
                        h = _H[word.value]
                    elif word.value in _V:
                        v = _V[word.value]
                    elif word.value == b"mirror":
                        mirror = True
    if key[0] == "fp_text" and is_hidden(group):
        raise ValueError(f"the hidden fp_text {key[1]!r} becomes a field KiCad names itself")
    style = T.Style(T.loaded_angle(written, orientation), keep_upright, h, v, mirror)
    return Text(key, local, style, layer)


def is_hidden(group) -> bool:
    """Whether a text is hidden: (hide), (hide yes), or a bare hide inside (effects)."""
    for item in group.items:
        if head(item) == b"hide":
            return len(item.items) < 2 or item.items[1].value == b"yes"
        if head(item) == b"effects":
            for part in item.items[1:]:
                if part.kind == L.SYMBOL and part.value == b"hide":
                    return True
                if head(part) == b"hide":
                    return len(part.items) < 2 or part.items[1].value == b"yes"
    return False


def _shape_points(group, *, major: int) -> tuple[Point, ...]:
    points: list[Point] = []
    for item in group.items:
        name = head(item)
        if name in _POINT_HEADS:
            points.append(_point(item, major=major))
        elif name == b"pts":
            for entry in item.items:
                if head(entry) == b"xy":
                    points.append(_point(entry, major=major))
                elif head(entry) == b"arc":
                    for part in entry.items[1:]:
                        if head(part) in _POINT_HEADS:
                            points.append(_point(part, major=major))
    return tuple(points)


#: The fields FOOTPRINT::addMandatoryFields creates, with their layers.
MANDATORY_FIELDS = (
    ("Reference", "F.SilkS"),
    ("Value", "F.Fab"),
    ("Datasheet", "F.Fab"),
    ("Description", "F.Fab"),
)
_MANDATORY_KEYS = frozenset(("property", name) for name, _ in MANDATORY_FIELDS)


def read_footprint(
    group, *, major: int, copper_count: int | None = None, version: int | None = None
) -> Footprint:
    """The footprint KiCad ``major`` reads from a (footprint ...) Group (module docstring).
    ``copper_count`` is the copper layer count of the board the footprint is read from,
    None for a library file; ``version`` that board's format (format_version). Raises
    ParseRefusal for a token KiCad refuses."""
    version = format_version(group, version)
    at = child(group, b"at")
    position: Point = (0, 0)
    orientation = 0.0
    turned = None  # the angle the (at) gives SetOrientation, as parsed
    if at is not None:
        position = _point(at, major=major)
        if len(at.items) > 3:
            turned = _number(at.items[3])
            orientation = normalize180(turned)
        before = [head(item) for item in group.items if item.start < at.start]
        if b"pad" in before or b"property" in before or b"fp_text" in before:
            raise ValueError("a pad or text precedes the footprint's (at): not modelled")
    layer_node = child(group, b"layer")
    back = layer_node is not None and layer_node.items[1].value == b"B.Cu"
    layer = "B.Cu" if back else "F.Cu"
    pads = tuple(_read_pad(g, major=major, copper_count=copper_count) for g in pad_groups(group))
    _check_repeated_fields(group, version)
    texts = []
    for item in text_groups(group, version=version):
        text = _read_text(
            item,
            orientation,
            major=major,
            back=back,
            on_board=copper_count is not None,
            version=version,
        )
        if text is not None:
            texts.append(text)
    keys = {t.key for t in texts}
    for name, field_layer in MANDATORY_FIELDS:
        if ("property", name) not in keys:
            angle = 0.0 if turned is None else normalize(0.0 + turned)
            style = T.Style(angle, True, T.CENTER, T.CENTER, False)
            texts.append(Text(("property", name), (0, 0), style, field_layer, True))
    shapes = []
    for item in group.items:
        name = head(item)
        if name in SHAPE_HEADS:
            layer_of = child(item, b"layer")
            shapes.append(
                Shape(
                    name.decode(),
                    layer_of.items[1].value.decode() if layer_of is not None else None,
                    _shape_points(item, major=major),
                )
            )
    zones = tuple(_read_zone(item, major=major) for item in children(group, b"zone"))
    points = tuple(_read_point(item, major=major) for item in children(group, b"point"))
    return Footprint(position, orientation, layer, pads, tuple(texts), tuple(shapes), zones, points)


def _layer_word(group) -> str | None:
    """The layer name of a (layer "name") list, or None."""
    return group.items[1].value.decode() if group is not None and len(group.items) > 1 else None


def _read_zone(group, *, major: int) -> Zone:
    """A footprint's (zone ...), its points in board coordinates as written."""
    names = [
        token.value.decode()
        for node in (*children(group, b"layer"), *children(group, b"layers"))
        for token in node.items[1:]
    ]
    polygons = tuple(_shape_points(item, major=major) for item in children(group, b"polygon"))
    fills = tuple(
        (_layer_word(child(item, b"layer")) or "", _shape_points(item, major=major))
        for item in children(group, b"filled_polygon")
    )
    return Zone(BL.layer_set(names), polygons, fills)


def _read_point(group, *, major: int) -> BoardPoint:
    """A footprint's KiCad 10 (point (at x y) (size s) (layer "name") (uuid ...))."""
    at = child(group, b"at")
    if at is None:
        raise ParseRefusal(f"the point at byte {group.start} has no (at)")
    return BoardPoint(_point(at, major=major), _layer_word(child(group, b"layer")))


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------


def _board_children(
    fp: Footprint, move: Callable[[Point], Point], opposite: Mapping[str, str] | None = None
) -> dict:
    """``fp``'s zones and points with ``move`` applied to every board point they hold and,
    with ``opposite``, every layer taken to its opposite: _replace arguments."""

    def flipped(layer):
        return opposite[layer] if opposite is not None and layer else layer

    zones = tuple(
        zone._replace(
            layers=frozenset(flipped(layer) for layer in zone.layers),
            polygons=tuple(tuple(move(p) for p in polygon) for polygon in zone.polygons),
            fills=tuple((flipped(layer), tuple(move(p) for p in pts)) for layer, pts in zone.fills),
        )
        for zone in fp.zones
    )
    points = tuple(BoardPoint(move(point.position), flipped(point.layer)) for point in fp.points)
    return {"zones": zones, "points": points}


def _turned_about(centre: Point, degrees: float, major: int) -> Callable[[Point], Point]:
    """The integer RotatePoint about ``centre`` by ``degrees`` (kicad_ref.trigo)."""
    return lambda p: rotate_point_about(p[0], p[1], centre[0], centre[1], degrees, major=major)


def set_position(fp: Footprint, position: Point) -> Footprint:
    """FOOTPRINT::SetPosition: nothing footprint-relative changes; zones and points move
    by the delta."""
    dx, dy = position[0] - fp.position[0], position[1] - fp.position[1]
    return fp._replace(position=position, **_board_children(fp, lambda p: (p[0] + dx, p[1] + dy)))


def set_orientation(fp: Footprint, degrees: float, *, major: int = 10) -> Footprint:
    """FOOTPRINT::SetOrientation: every pad and text turned by the change, no KeepUpright;
    zones and points turned about the position by the change, rounded as KiCad
    ``major`` rounds."""
    change = degrees - fp.orientation
    return fp._replace(
        orientation=normalize180(degrees),
        pads=tuple(p._replace(angle=PAD.rotate(p.angle, change)) for p in fp.pads),
        texts=tuple(t._replace(style=T.rotate(t.style, change)) for t in fp.texts),
        **_board_children(fp, _turned_about(fp.position, change, major)),
    )


def keep_upright(fp: Footprint) -> Footprint:
    """KeepUpright on every text, as FOOTPRINT::Rotate runs it after SetOrientation."""
    return fp._replace(texts=tuple(t._replace(style=T.keep_upright(t.style)) for t in fp.texts))


def rotate(fp: Footprint, degrees: float, *, major: int = 10) -> Footprint:
    """FOOTPRINT::Rotate about the footprint's own position."""
    if degrees == 0:
        return fp
    return keep_upright(set_orientation(fp, fp.orientation + degrees, major=major))


def turn_to(fp: Footprint, degrees: float, *, major: int = 10) -> Footprint:
    """``fp`` turned to the angle ``degrees`` names, as a tool's rotation asks (module
    docstring): unchanged when Normalize180( degrees ) is its orientation, else
    SetOrientation( degrees ) and KeepUpright."""
    if normalize180(degrees) == fp.orientation:
        return fp
    return keep_upright(set_orientation(fp, degrees, major=major))


def is_cardinal(degrees: float) -> bool:
    """EDA_ANGLE::IsCardinal (libs/kimath/src/geometry/eda_angle.cpp:40-49 at 9.0.8, the
    same at 10.0.7): a multiple of 90 degrees, tested by stepping into [0, 90)."""
    test = degrees
    while test < 0.0:
        test += 90.0
    while test >= 90.0:
        test -= 90.0
    return test == 0.0


def _mirror_y(point: Point) -> Point:
    return (point[0], -point[1])


def flip_shape(shape: Shape, opposite: Mapping[str, str] | None) -> Shape:
    """EDA_SHAPE::flip top to bottom about the frame's origin (PCB_SHAPE::Flip also takes
    the layer to its opposite): every y negated, an arc's start and end swapped."""
    points = tuple(_mirror_y(p) for p in shape.points)
    if shape.kind in ("fp_arc", "gr_arc"):
        points = (points[2], points[1], points[0])
    layer = shape.layer
    if layer is not None and opposite is not None:
        layer = opposite[layer]
    return shape._replace(layer=layer, points=points)


def _rotated_shape(shape: Shape, change: float) -> Shape:
    """What EDA_SHAPE::rotate by ``change`` does to a graphic's footprint-relative form
    when its footprint turns with it: nothing, except that a rectangle turned by an angle
    that is not a multiple of 90 becomes the polygon of its corners."""
    if shape.kind == "fp_rect" and not is_cardinal(change):
        (sx, sy), (ex, ey) = shape.points
        return shape._replace(kind="fp_poly", points=((sx, sy), (ex, sy), (ex, ey), (sx, ey)))
    return shape


def flip(
    fp: Footprint,
    opposite: Mapping[str, str],
    side_specific: Callable[[str], bool],
    *,
    major: int = 10,
) -> Footprint:
    """FOOTPRINT::Flip about the footprint's own position, LEFT_RIGHT (module docstring).
    ``opposite`` maps every layer to its opposite and ``side_specific`` says which layers
    are, both the board's (kicad_ref.board_layers.opposites and is_side_specific); the
    turn of zones and points rounds as KiCad ``major`` rounds."""
    new = normalize180(-fp.orientation)
    change = (new + 180.0) - new
    cy = fp.position[1]
    mirrored = fp._replace(**_board_children(fp, lambda p: (p[0], 2 * cy - p[1]), opposite))
    board = _board_children(mirrored, _turned_about(fp.position, change, major))
    pads = []
    for p in fp.pads:
        pads.append(
            p._replace(
                local=_mirror_y(p.local),
                angle=PAD.rotate(PAD.flip_angle(p.angle), change),
                layers=frozenset(opposite[layer] for layer in p.layers),
                offset=_mirror_y(p.offset),
                delta=_mirror_y(p.delta),
                chamfer=PAD.flip_chamfer(p.chamfer),
                primitives=tuple(flip_shape(s, None) for s in p.primitives),
            )
        )
    texts = []
    for t in fp.texts:
        layer = opposite[t.layer] if t.layer is not None else None
        style = T.flip(t.style, side_specific(layer) if layer is not None else False)
        texts.append(
            t._replace(
                local=None if t.local is None else _mirror_y(t.local),
                style=T.keep_upright(T.rotate(style, change)),
                layer=layer,
            )
        )
    shapes = tuple(_rotated_shape(flip_shape(s, opposite), change) for s in fp.shapes)
    return fp._replace(
        orientation=normalize180(new + 180.0),
        layer=opposite[fp.layer],
        pads=tuple(pads),
        texts=tuple(texts),
        shapes=shapes,
        **board,
    )


def board_flip(fp: Footprint, table: BL.LayerTable, *, major: int) -> Footprint:
    """``flip`` on the board whose layer table is ``table``, as KiCad ``major`` reads it."""
    opposite = BL.opposites(table, major=major)
    return flip(fp, opposite, lambda layer: BL.is_side_specific(table, layer), major=major)


def place(
    library: Footprint,
    position: Point,
    degrees: float,
    *,
    semantics: str,
    flipped: Callable[[Footprint], Footprint] | None = None,
    major: int = 10,
) -> Footprint:
    """A library footprint (orientation 0) placed at ``position`` and turned to ``degrees``
    by Rotate by the change (``semantics="rotate"``, the editor's rotate command), by
    SetOrientation (``"set"``, the Python API) or by turn_to (``"turn"``, the turn a
    tool's rotation asks for). With ``flipped`` (a flip of the board, such as board_flip
    bound to its table) the footprint is flipped after SetPosition and before the turn,
    which then turns it from the orientation the flip left (SetLayerAndFlip( B_Cu ), then
    the turn to ``degrees``). Zones and points turn as KiCad ``major`` rounds."""
    if library.orientation != 0:
        raise ValueError(f"a library footprint reads at orientation 0, not {library.orientation}")
    placed = set_position(library, position)
    if flipped is not None:
        placed = flipped(placed)
    if semantics == "rotate":
        return rotate(placed, degrees - placed.orientation, major=major)
    if semantics == "set":
        return set_orientation(placed, degrees, major=major)
    if semantics == "turn":
        return turn_to(placed, degrees, major=major)
    raise ValueError(f"no such semantics {semantics!r}")


# ---------------------------------------------------------------------------
# writing, and the upright form
# ---------------------------------------------------------------------------


def fp_angle_token(orientation: float) -> str | None:
    """The footprint's (at) angle atom format( FOOTPRINT ) writes, None when it writes none
    (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:1127-1131 at 9.0.8, 1231-1235 at
    10.0.7): left out when the orientation IsZero(), else FormatAngle."""
    return None if orientation == 0 else format_angle(orientation)


def written(fp: Footprint, *, copper_count: int | None = None) -> Footprint:
    """``fp`` as KiCad reads back the tokens it writes for it: every angle through its
    writer's spelling and the parser's arithmetic again (a text's through the orientation
    read back). KiCad writes every field, so none is a default any more. With
    ``copper_count``, the copper layer count of the board KiCad writes it on, every pad's
    layers as its writer spells them (kicad_ref.board_layers.written_pad_layers)."""
    token = fp_angle_token(fp.orientation)
    orientation = 0.0 if token is None else normalize180(parse_double(token))
    pads = []
    for p in fp.pads:
        pad_token = PAD.angle_token(p.angle)
        layers = p.layers
        if copper_count is not None:
            layers = BL.written_pad_layers(layers, copper_count)
        pads.append(
            p._replace(
                angle=PAD.loaded_angle(None if pad_token is None else parse_double(pad_token)),
                layers=layers,
            )
        )
    texts = []
    for t in fp.texts:
        angle = T.loaded_angle(parse_double(T.angle_token(t.style)), orientation)
        texts.append(t._replace(style=t.style._replace(angle=angle), default=False))
    return fp._replace(orientation=orientation, pads=tuple(pads), texts=tuple(texts))


def canonical(fp: Footprint) -> Footprint:
    """The upright form (module docstring): every keep-upright text as KeepUpright leaves
    it, every other text at its angle Normalize()d. It holds two states KiCad keeps apart
    as one, so it states only laws that hold up to KeepUpright, never what a tool must
    write."""
    texts = []
    for t in fp.texts:
        if t.style.keep_upright:
            style = T.keep_upright(t.style)
        else:
            style = t.style._replace(angle=normalize(t.style.angle))
        texts.append(t._replace(style=style))
    return fp._replace(texts=tuple(texts))
