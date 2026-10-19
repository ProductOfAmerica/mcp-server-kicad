"""A schematic sheet block's pins, minimum size and autoplaced fields, ported as an oracle.

Read from KiCad's source at 9.0.8 (eeschema/sch_sheet.cpp, sch_sheet_pin.cpp, sch_label.cpp,
sch_text.cpp, common/eda_text.cpp, common/gr_text.cpp). Not read at 10.0.7 except where a test
says so.

- Load rule. The parser calls SCH_SHEET_PIN::SetPosition, which is ConstrainOnEdge( pos, true )
  (sch_sheet_pin.h:194-196): the nearest of the sheet's four edge segments (top, right, bottom,
  left; the first wins a tie) becomes the side, the coordinate across it is set to the edge and
  the one along it is clamped to the edge (sch_sheet_pin.cpp:158-223). Then the (at) angle
  picks the side again through SetSide (0 right, 90 top, 180 left, 270 bottom; parser
  2342-2425), which moves the pin onto that edge across it and does not clamp
  (sch_sheet_pin.cpp:114-149).
- Resize keeps the side: ConstrainOnEdge( position, false ) (sch_sheet.cpp:1023-1037).
- A pin's box along its edge is textHeight + penWidth + margin, centred on the pin with
  C integer halving (SCH_HIERLABEL::GetBodyBoundingBox, sch_label.cpp:2155-2205); the pen is
  the text thickness when above 1 IU, else width/8 (bold width/5), clamped to a quarter of
  the smaller size (eda_text.cpp:458-476, gr_text.cpp:36-95); the margin is
  KiROUND( text_offset_ratio * size.y ) (sch_text.cpp:267-279).
- GetMinWidth( true ) and GetMinHeight( true ): the pins' boxes along the edge, bumped outward
  to the next 50 mil line (bumpToNextGrid, sch_sheet.cpp:525-544), at least 500 mils wide and
  150 high (sch_sheet.cpp:547-612, sch_sheet.h:39-40).
- AutoplaceFields (sch_sheet.cpp:665-706).

test_hierarchy_writers.py checks the load rule and AutoplaceFields against the sheets KiCad
ships. This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

import math
from typing import NamedTuple

from kicad_ref import dsn_lexer as L

IU_PER_MM = 10_000
IU_PER_MIL = 254
GRID = 50 * IU_PER_MIL
MIN_WIDTH = 500 * IU_PER_MIL
MIN_HEIGHT = 150 * IU_PER_MIL
DEFAULT_PEN = 6 * IU_PER_MIL
DEFAULT_TEXT_OFFSET_RATIO = 0.15
ANGLE_OF_SIDE = {"right": 0, "top": 90, "left": 180, "bottom": 270}
SIDE_OF_ANGLE: dict[float, str] = {v: k for k, v in ANGLE_OF_SIDE.items()}


def kiround(v: float) -> int:
    """KiROUND: half away from zero."""
    return int(math.floor(abs(v) + 0.5)) * (1 if v >= 0 else -1)


def iu(token: bytes | str) -> int:
    return kiround(float(token) * IU_PER_MM)


class Pin(NamedTuple):
    name: str
    side: str
    x: int
    y: int
    size: tuple[int, int]
    bold: bool
    justify: tuple[str, ...]  # the (justify ...) atoms as written


class Sheet(NamedTuple):
    x: int
    y: int
    w: int
    h: int
    pen: int  # the border width, 0 when it is unset
    autoplaced: bool
    name_size: tuple[int, int]
    file_size: tuple[int, int]
    pins: tuple[Pin, ...]


def _head(g) -> bytes | None:
    if g.kind == L.GROUP and g.items and g.items[0].kind in L.ATOM_KINDS:
        return g.items[0].value
    return None


def _child(g, head: bytes):
    return next((c for c in g.items[1:] if _head(c) == head), None)


def _font_size(effects) -> tuple[int, int]:
    font = _child(effects, b"font") if effects is not None else None
    size = _child(font, b"size") if font is not None else None
    if size is None:
        return 12700, 12700
    return iu(size.items[2].value), iu(size.items[1].value)  # (size HEIGHT WIDTH) -> (W, H)


def _bold(effects) -> bool:
    font = _child(effects, b"font") if effects is not None else None
    return font is not None and any(
        c.kind in L.ATOM_KINDS and c.value == b"bold" for c in font.items[1:]
    )


def _justify(effects) -> tuple[str, ...]:
    j = _child(effects, b"justify") if effects is not None else None
    return tuple(c.value.decode() for c in j.items[1:]) if j is not None else ()


def _property_size(sheet, name: bytes) -> tuple[int, int]:
    for p in sheet.items[1:]:
        if _head(p) == b"property" and p.items[1].value == name:
            return _font_size(_child(p, b"effects"))
    return 12700, 12700


def nearest_side(x: int, y: int, w: int, h: int, px: int, py: int) -> str:
    """The side whose edge segment is nearest the point; the first of top, right, bottom,
    left wins a tie."""
    segments = (
        ("top", (x, y, x + w, y)),
        ("right", (x + w, y, x + w, y + h)),
        ("bottom", (x + w, y + h, x, y + h)),
        ("left", (x, y + h, x, y)),
    )
    best, best_d = "", None
    for side, (ax, ay, bx, by) in segments:
        cx = min(max(px, min(ax, bx)), max(ax, bx))
        cy = min(max(py, min(ay, by)), max(ay, by))
        d = (px - cx) ** 2 + (py - cy) ** 2
        if best_d is None or d < best_d:
            best, best_d = side, d
    return best


def constrain(x: int, y: int, w: int, h: int, side: str, px: int, py: int) -> tuple[int, int]:
    """ConstrainOnEdge on a given side: onto the edge across it, clamped into it along it."""
    if side in ("left", "right"):
        return (x if side == "left" else x + w), min(max(py, y), y + h)
    return min(max(px, x), x + w), (y if side == "top" else y + h)


def load_pin(
    x: int, y: int, w: int, h: int, px: int, py: int, angle: float
) -> tuple[str, int, int]:
    """(side, x, y) of a pin written at (px, py, angle), as KiCad's parser leaves it."""
    if angle not in SIDE_OF_ANGLE:
        raise ValueError(f"KiCad's parser refuses a sheet pin angle of {angle}")
    first = nearest_side(x, y, w, h, px, py)
    cx, cy = constrain(x, y, w, h, first, px, py)
    side = SIDE_OF_ANGLE[angle]
    if side in ("left", "right"):
        cx = x if side == "left" else x + w
    else:
        cy = y if side == "top" else y + h
    return side, cx, cy


def read_sheet(group) -> Sheet:
    """A lexed (sheet ...) group, its pins as KiCad loads them."""
    at, size = _child(group, b"at"), _child(group, b"size")
    x, y = iu(at.items[1].value), iu(at.items[2].value)
    w, h = iu(size.items[1].value), iu(size.items[2].value)
    stroke = _child(group, b"stroke")
    width = _child(stroke, b"width") if stroke is not None else None
    pins = []
    for p in group.items[1:]:
        if _head(p) != b"pin":
            continue
        pat = _child(p, b"at")
        effects = _child(p, b"effects")
        side, px, py = load_pin(
            x, y, w, h, iu(pat.items[1].value), iu(pat.items[2].value), float(pat.items[3].value)
        )
        pins.append(
            Pin(
                p.items[1].value.decode(),
                side,
                px,
                py,
                _font_size(effects),
                _bold(effects),
                _justify(effects),
            )
        )
    auto = _child(group, b"fields_autoplaced")
    return Sheet(
        x,
        y,
        w,
        h,
        iu(width.items[1].value) if width is not None else 0,
        auto is not None and not any(c.value == b"no" for c in auto.items[1:] if c.kind != L.GROUP),
        _property_size(group, b"Sheetname"),
        _property_size(group, b"Sheetfile"),
        tuple(pins),
    )


def pin_justify(side: str) -> tuple[str, ...]:
    """The horizontal justify SetSide's spin gives a pin on a side (sch_sheet_pin.cpp:114-149,
    SetSpinStyle at sch_label.cpp:2116-2120): left and bottom pins print ``left``, right and
    top pins print ``right``."""
    return {"left": ("left",), "right": ("right",), "top": ("right",), "bottom": ("left",)}[side]


def pen_of(size: tuple[int, int], bold: bool) -> int:
    pen = kiround(size[0] / 5.0) if bold else kiround(size[0] / 8.0)
    return min(pen, kiround(min(size) * 0.25))


def pin_span(pin: Pin, ratio: float) -> tuple[int, int]:
    """(low, high) of the pin's box along its edge, in IU."""
    height = pin.size[1] + pen_of(pin.size, pin.bold) + kiround(ratio * pin.size[1])
    low = (pin.y if pin.side in ("left", "right") else pin.x) - height // 2
    return low, low + height


def _bump(value: int, direction: int) -> int:
    base = int(value / GRID)  # C integer division truncates toward zero
    excess = abs(value - base * GRID)
    if direction > 0:
        return (base + 1) * GRID
    return base * GRID if excess > 0 else (base - 1) * GRID


def min_height(sheet: Sheet, ratio: float = DEFAULT_TEXT_OFFSET_RATIO) -> int:
    """GetMinHeight( true )."""
    top, bottom = sheet.y + sheet.h, sheet.y
    for pin in sheet.pins:
        if pin.side in ("left", "right"):
            lo, hi = pin_span(pin, ratio)
            top, bottom = min(top, lo), max(bottom, hi)
    top, bottom = _bump(top, -1), _bump(bottom, 1)
    need = 0 if top >= bottom else bottom - sheet.y
    return max(need, MIN_HEIGHT)


def min_width(sheet: Sheet, ratio: float = DEFAULT_TEXT_OFFSET_RATIO) -> int:
    """GetMinWidth( true )."""
    left, right = sheet.x + sheet.w, sheet.x
    for pin in sheet.pins:
        if pin.side in ("top", "bottom"):
            lo, hi = pin_span(pin, ratio)
            left, right = min(left, lo), max(right, hi)
    left, right = _bump(left, -1), _bump(right, 1)
    need = 0 if left >= right else right - sheet.x
    return max(need, MIN_WIDTH)


class Fields(NamedTuple):
    name_at: tuple[int, int, int]
    name_justify: tuple[str, ...]
    file_at: tuple[int, int, int]
    file_justify: tuple[str, ...]


def autoplace(sheet: Sheet, default_pen: int = DEFAULT_PEN) -> Fields:
    """SCH_SHEET::AutoplaceFields: (x, y, angle) and the justify of both fields."""
    pen = sheet.pen if sheet.pen > 0 else default_pen
    border = kiround(pen / 2.0) + 4
    vertical = bool(sheet.pins) and all(p.side in ("top", "bottom") for p in sheet.pins)
    name_margin = border + kiround(max(sheet.name_size) * 0.5)
    file_margin = border + kiround(max(sheet.file_size) * 0.4)
    if vertical:
        return Fields(
            (sheet.x - name_margin, sheet.y + sheet.h, 90),
            ("left", "bottom"),
            (sheet.x + sheet.w + file_margin, sheet.y + sheet.h, 90),
            ("left", "top"),
        )
    return Fields(
        (sheet.x, sheet.y - name_margin, 0),
        ("left", "bottom"),
        (sheet.x, sheet.y + sheet.h + file_margin, 0),
        ("left", "top"),
    )


def field_at(group, name: bytes) -> tuple[int, int, int]:
    """(x, y, angle) of a sheet's property, as written."""
    for p in group.items[1:]:
        if _head(p) == b"property" and p.items[1].value == name:
            at = _child(p, b"at")
            return iu(at.items[1].value), iu(at.items[2].value), int(float(at.items[3].value))
    raise KeyError(name)


def field_justify(group, name: bytes) -> tuple[str, ...]:
    for p in group.items[1:]:
        if _head(p) == b"property" and p.items[1].value == name:
            return _justify(_child(p, b"effects"))
    raise KeyError(name)
