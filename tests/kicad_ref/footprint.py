"""A footprint as KiCad reads it, and what KiCad's own footprint operations do to it.

``read_footprint`` reads a footprint from the lexer port's Group of a (footprint ...) list,
written in a .kicad_mod or on a board, the way the board parser does
(pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp, 9.0.8 lines, 10.0.7 in brackets):

- (at x y angle): the position through parseBoardUnits and the orientation through
  FOOTPRINT::SetOrientation, which stores Normalize180 of parseDouble; no angle atom is
  0, and a token that is not a NUMBER refuses the file (4533-4549 [5227-5243]);
  FOOTPRINT::SetOrientation (pcbnew/footprint.cpp:2641-2669 [3099-3137]) normalizes;
- (layer ...): the footprint's own layer, read as written here (KiCad reads anything but
  B.Cu as F.Cu; the side is not this port's subject yet);
- pads, through kicad_ref.pad; texts (the fields, written as (property ...), and the
  fp_text items), through kicad_ref.pcb_text. A (property ...) named ki_fp_filters is the
  footprint's filters and one named Footprint is dropped since KiCad 9, so neither is a
  text KiCad holds (4611-4636 [5306-5321]); a legacy (fp_text reference|value ...) is the
  Reference or Value field. A field name written twice is one field: KiCad reads the later
  (property ...) into the field the earlier one made (4637-4641 [5324-5328]), which resets
  its keep-upright and its angle and takes the position, layer and justification the later
  one writes (parsePCB_TEXT_effects, 3438-3576 [3955-4093]; parseEDA_TEXT, 505-515
  [736-750]), so the later one is read and the earlier one is not. Fifteen of KiCad
  9.0.8's stock footprints, its M.2 edge connectors, write Reference and Value twice at
  different places (counted 2026-10-11). A later one that leaves out its (at), (layer)
  or (effects) keeps some of the earlier one's, and this port refuses it (ValueError);
- graphics (fp_line, fp_rect, fp_circle, fp_arc, fp_poly, fp_curve): their points in the
  footprint's own frame, as written;
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
  footprint-relative changes;
- SetOrientation (2641-2669 [3099-3137]): change = new - old (doubles), the orientation
  becomes Normalize180( new ), every pad and text is turned by the change (PAD::Rotate,
  PCB_TEXT::Rotate); graphics keep their footprint-relative geometry;
- Rotate (2456-2479 [2898-2918]): returns at once when the angle is exactly 0, else
  SetOrientation( orientation + angle ) and then KeepUpright on every field and every
  fp_text (PCB_TEXT_T; a text box is not one);
- placing a library footprint (FootprintLoad, then SetPosition, then Rotate or
  SetOrientation): the library footprint reads with orientation 0 at the origin.

``canonical`` is the comparison form of the test ladder's rule D2: a keep-upright text in
the form KeepUpright leaves it (angle below 180, the justification negated if it was
turned), any other text at its angle Normalize()d. It accepts both of KiCad's paths, Rotate
and SetOrientation, as one.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

from typing import NamedTuple

from kicad_ref import dsn_lexer as L
from kicad_ref import pad as PAD
from kicad_ref import pcb_text as T
from kicad_ref.eda_units import format_angle
from kicad_ref.parse_units import ParseRefusal, parse_board_units, parse_double
from kicad_ref.trigo import normalize, normalize180

Point = tuple[int, int]

#: The graphic items read, with the point lists each one carries in its own frame.
SHAPE_HEADS = (b"fp_line", b"fp_rect", b"fp_circle", b"fp_arc", b"fp_poly", b"fp_curve")
_POINT_HEADS = (b"start", b"mid", b"end", b"center")
#: Properties that are no text KiCad holds (see the module docstring).
_NOT_TEXTS = (b"ki_fp_filters", b"Footprint")
_H = {b"left": T.LEFT, b"right": T.RIGHT}
_V = {b"top": T.TOP, b"bottom": T.BOTTOM}


class Pad(NamedTuple):
    number: str
    local: Point
    angle: float  # absolute on the board, [0, 360)


class Text(NamedTuple):
    """``key`` is ("property", name) for a field, ("fp_text", text) for any other text.
    ``default`` marks a mandatory field the file leaves out, which KiCad holds as its
    constructor made it."""

    key: tuple[str, str]
    local: Point | None  # None for a text written without (at)
    style: T.Style
    layer: str | None
    default: bool = False


class Shape(NamedTuple):
    kind: str
    layer: str | None
    points: tuple[Point, ...]


class Footprint(NamedTuple):
    position: Point
    orientation: float  # Normalize180
    layer: str
    pads: tuple[Pad, ...]
    texts: tuple[Text, ...]
    shapes: tuple[Shape, ...]


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


def _read_pad(group, *, major: int) -> Pad:
    at = child(group, b"at")
    if at is None:
        raise ParseRefusal(f"the pad at byte {group.start} has no (at)")
    written = _number(at.items[3]) if len(at.items) > 3 else None
    return Pad(group.items[1].value.decode(), _point(at, major=major), PAD.loaded_angle(written))


def pad_groups(group) -> list:
    """The (pad ...) lists of a footprint, in the order read_footprint reads its pads."""
    return children(group, b"pad")


def text_groups(group) -> list:
    """The lists of a footprint that read as texts, in the order read_footprint reads them.
    A (property ...) whose name a later one repeats is not among them: KiCad reads the
    later one into the same field (module docstring)."""
    found = [
        item
        for item in group.items
        if head(item) in (b"property", b"fp_text") and _text_key(item) is not None
    ]
    last = {item.items[1].value: item for item in found if head(item) == b"property"}
    return [
        item for item in found if head(item) != b"property" or last[item.items[1].value] is item
    ]


def _check_repeated_fields(group) -> None:
    """Refuse (ValueError) a repeated field name whose last (property ...) leaves out its
    (at), (layer) or (effects): KiCad then keeps what the earlier one set for it, which
    this port does not model."""
    names = [item.items[1].value for item in group.items if head(item) == b"property"]
    for item in text_groups(group):
        if head(item) != b"property" or names.count(item.items[1].value) < 2:
            continue
        missing = [n.decode() for n in (b"at", b"layer", b"effects") if child(item, n) is None]
        if missing:
            raise ValueError(
                f"the repeated field {item.items[1].value.decode()!r} leaves out"
                f" {', '.join(missing)}: not modelled"
            )


def _text_key(group) -> tuple[str, str] | None:
    name = head(group)
    if name == b"property":
        if group.items[1].value in _NOT_TEXTS:
            return None
        return ("property", group.items[1].value.decode())
    kind, text = group.items[1].value, group.items[2].value.decode()
    if kind == b"reference":
        return ("property", "Reference")
    if kind == b"value":
        return ("property", "Value")
    return ("fp_text", text)


def _read_text(group, orientation: float, *, major: int) -> Text | None:
    key = _text_key(group)
    if key is None:
        return None
    local = None
    written = 0.0
    keep_upright = True
    h = v = T.CENTER
    mirror = False
    layer = None
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
        elif name == b"layer":
            layer = item.items[1].value.decode()
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


def read_footprint(group, *, major: int) -> Footprint:
    """The footprint KiCad ``major`` reads from a (footprint ...) Group (module docstring).
    Raises ParseRefusal for a token KiCad refuses."""
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
    layer = layer_node.items[1].value.decode() if layer_node is not None else "F.Cu"
    pads = tuple(_read_pad(g, major=major) for g in pad_groups(group))
    _check_repeated_fields(group)
    texts = []
    for item in text_groups(group):
        text = _read_text(item, orientation, major=major)
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
    return Footprint(position, orientation, layer, pads, tuple(texts), tuple(shapes))


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------


def set_position(fp: Footprint, position: Point) -> Footprint:
    """FOOTPRINT::SetPosition: nothing footprint-relative changes."""
    return fp._replace(position=position)


def set_orientation(fp: Footprint, degrees: float) -> Footprint:
    """FOOTPRINT::SetOrientation: every pad and text turned by the change, no KeepUpright."""
    change = degrees - fp.orientation
    return fp._replace(
        orientation=normalize180(degrees),
        pads=tuple(p._replace(angle=PAD.rotate(p.angle, change)) for p in fp.pads),
        texts=tuple(t._replace(style=T.rotate(t.style, change)) for t in fp.texts),
    )


def keep_upright(fp: Footprint) -> Footprint:
    """KeepUpright on every text, as FOOTPRINT::Rotate runs it after SetOrientation."""
    return fp._replace(texts=tuple(t._replace(style=T.keep_upright(t.style)) for t in fp.texts))


def rotate(fp: Footprint, degrees: float) -> Footprint:
    """FOOTPRINT::Rotate about the footprint's own position."""
    if degrees == 0:
        return fp
    return keep_upright(set_orientation(fp, fp.orientation + degrees))


def place(library: Footprint, position: Point, degrees: float, *, semantics: str) -> Footprint:
    """A library footprint (orientation 0) placed at ``position`` and turned to ``degrees``
    by Rotate (``semantics="rotate"``, the editor's rotate command and properties dialog)
    or by SetOrientation (``"set"``, the Python API)."""
    if library.orientation != 0:
        raise ValueError(f"a library footprint reads at orientation 0, not {library.orientation}")
    placed = set_position(library, position)
    if semantics == "rotate":
        return rotate(placed, degrees)
    if semantics == "set":
        return set_orientation(placed, degrees)
    raise ValueError(f"no such semantics {semantics!r}")


# ---------------------------------------------------------------------------
# writing, and the comparison form
# ---------------------------------------------------------------------------


def fp_angle_token(orientation: float) -> str | None:
    """The footprint's (at) angle atom format( FOOTPRINT ) writes, None when it writes none
    (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:1127-1131 at 9.0.8, 1231-1235 at
    10.0.7): left out when the orientation IsZero(), else FormatAngle."""
    return None if orientation == 0 else format_angle(orientation)


def written(fp: Footprint) -> Footprint:
    """``fp`` as KiCad reads back the tokens it writes for it: every angle through its
    writer's spelling and the parser's arithmetic again (a text's through the orientation
    read back). KiCad writes every field, so none is a default any more."""
    token = fp_angle_token(fp.orientation)
    orientation = 0.0 if token is None else normalize180(parse_double(token))
    pads = []
    for p in fp.pads:
        pad_token = PAD.angle_token(p.angle)
        pads.append(
            p._replace(
                angle=PAD.loaded_angle(None if pad_token is None else parse_double(pad_token))
            )
        )
    texts = []
    for t in fp.texts:
        angle = T.loaded_angle(parse_double(T.angle_token(t.style)), orientation)
        texts.append(t._replace(style=t.style._replace(angle=angle), default=False))
    return fp._replace(orientation=orientation, pads=tuple(pads), texts=tuple(texts))


def canonical(fp: Footprint) -> Footprint:
    """The comparison form (rule D2, module docstring)."""
    texts = []
    for t in fp.texts:
        if t.style.keep_upright:
            style = T.keep_upright(t.style)
        else:
            style = t.style._replace(angle=normalize(t.style.angle))
        texts.append(t._replace(style=style))
    return fp._replace(texts=tuple(texts))
