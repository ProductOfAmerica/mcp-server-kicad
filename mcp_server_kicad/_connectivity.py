"""Where KiCad draws each pin, and what a routing write may touch.

The routing tools edit geometry, and KiCad derives connectivity from geometry, so a routing
write is only as safe as its model of the geometry KiCad will read. This module builds that
model from a parsed schematic and plans wire_pins_to_net's edit: an outward stub with a net
label at its end, or a label on the pin end, or a refusal naming the obstacle. It never writes;
schematic.py emits the nodes and writes the file once. Decision record:
docs/adr-routing-safety.md.

Coordinates are KiCad's internal units (IU, 0.0001 mm) as integers, compared exactly, the way
KiCad compares connection points. schematic.py imports this module, so it must not import
schematic.py.

The only files it reads are the other sheets of the hierarchy, for the duplicate-reference
check, and only facts are kept from them: a small cache keyed by each file's content.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from mcp_server_kicad import _cst, _shared

IU_PER_MM = 10000

#: The touch rule's margin: 0.05 mm. New geometry may come no closer than this to anything
#: other than the target pin's own connection point.
TOL = 500
TOL2 = TOL * TOL
#: A directive this close to a rule area's outline certainly attaches to it: KiCad attaches
#: within 5 IU (sch_rule_area.cpp, 9.0.8), so 4 holds whichever way coordinates round (H-C4).
CERT2 = 16

Point = tuple[int, int]
Matrix = tuple[int, int, int, int]


# ---------------------------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------------------------


def kiround(v: float) -> int:
    """KiCad's KiROUND: half away from zero."""
    return int(v + 0.5) if v >= 0 else int(v - 0.5)


def iu(text: str) -> int:
    """A coordinate atom in mm as KiCad parses it."""
    return kiround(float(text) * IU_PER_MM)


def mm(i: int) -> str:
    """Exact decimal text for an IU value, trailing zeros dropped: 977900 -> "97.79"."""
    a = abs(i)
    s = f"{a // IU_PER_MM}.{a % IU_PER_MM:04d}".rstrip("0").rstrip(".")
    return "-" + s if i < 0 else s


def pt(p: Point) -> str:
    return f"({mm(p[0])}, {mm(p[1])})"


def _d2(ax: int, ay: int, bx: int, by: int) -> int:
    return (ax - bx) ** 2 + (ay - by) ** 2


def _within(px, py, ax, ay, bx, by, r2) -> bool:
    """Squared distance from a point to a closed segment is at most r2, exact in integers."""
    dx, dy = bx - ax, by - ay
    ln2 = dx * dx + dy * dy
    ex, ey = px - ax, py - ay
    t = ex * dx + ey * dy
    if ln2 == 0 or t <= 0:
        return ex * ex + ey * ey <= r2
    if t >= ln2:
        fx, fy = px - bx, py - by
        return fx * fx + fy * fy <= r2
    cross = ex * dy - ey * dx
    return cross * cross <= r2 * ln2


def _side(ax, ay, bx, by, px, py) -> int:
    v = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    return (v > 0) - (v < 0)


# ---------------------------------------------------------------------------------------------
# Placed symbols: unit, body style, and the library sub-symbols an instance draws
# ---------------------------------------------------------------------------------------------


def _sym_unit_cst(sym) -> int:
    """Unit number of a placed symbol node.

    One when the node carries no ``(unit N)``, which is how KiCad reads it
    (``SCH_SYMBOL::Init``).
    """
    node = sym.find("unit")
    if node is None or len(node.atoms) < 2:
        return 1
    try:
        return int(node.atoms[1].text)
    except ValueError:
        return 1


def _sym_body_style_cst(sym) -> int:
    """Body style of a placed symbol node.

    KiCad 10 writes ``(body_style N)`` on every placed symbol; KiCad 9 writes
    ``(convert N)``, and only for a De Morgan alternate. Absent means 1.
    """
    node = sym.find("body_style")
    if node is None:
        node = sym.find("convert")
    if node is None or len(node.atoms) < 2:
        return 1
    try:
        return int(node.atoms[1].text)
    except ValueError:
        return 1


def _lib_unit_style(unit_node) -> tuple[int, int] | None:
    """The (unit, body style) a lib sub-symbol's name encodes, or None if none.

    KiCad names them ``NAME_<unit>_<bodyStyle>``, and NAME itself may contain
    underscores, so the two trailing fields are the ones to read.
    """
    atoms = unit_node.atoms
    if len(atoms) < 2:
        return None
    parts = atoms[1].text.rsplit("_", 2)
    if len(parts) != 3:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _instance_units(lib_sym, unit: int, body_style: int = 1):
    """The lib sub-symbols a placed instance of *unit* in *body_style* draws.

    KiCad's rule (``LIB_SYMBOL::GetPins``): a sub-symbol is drawn when its
    unit is this one or 0 and its body style is this one or 0, with 0 meaning
    "common" on both axes. So a placed ``(unit 2)`` draws units 2 and 0 and
    nothing else, and a De Morgan part placed in its normal style draws
    ``_1_1`` but not ``_1_2``. Scanning every sub-symbol instead reports a
    sibling unit's pins as this instance's own, at coordinates derived from
    this instance's origin, and an alternate style's pins a second time.

    A sub-symbol whose name encodes no unit is kept. KiCad's own parser
    refuses such a name, so only a hand-built file carries one.
    """
    kept = []
    for sub in lib_sym.find_all("symbol"):
        ids = _lib_unit_style(sub)
        if ids is None or (ids[0] in (unit, 0) and ids[1] in (body_style, 0)):
            kept.append(sub)
    return kept


def pin_matches(pin, label: str) -> bool:
    """True when a lib pin node's name or number is *label*."""
    name = pin.find("name")
    number = pin.find("number")
    return (name is not None and name.atoms[1].text == label) or (
        number is not None and number.atoms[1].text == label
    )


def not_drawn_message(reference: str, label: str, lib_sym, placed: list[tuple[int, int]]) -> str:
    """Why no symbol placed on this sheet as *reference* draws pin *label*. *placed* holds the
    (unit, body style) each placement is drawn with.

    Names the unit that draws it when that unit is not placed here, or the body style when that
    is what differs, or says the pin does not exist.
    """
    subs = lib_sym.find_all("symbol") if lib_sym is not None else []
    carriers = sorted(
        {
            ids
            for sub in subs
            if (ids := _lib_unit_style(sub)) is not None
            and any(pin_matches(pin, label) for pin in sub.find_all("pin"))
        }
    )
    if not carriers:
        return f"Pin '{label}' not found on {reference}"
    # Name the body style only where it is the thing that differs: the unit is
    # here in its other style, or it is unit 0, which every placed unit draws.
    # An unplaced unit is named as a unit, and placing it is then the remedy.
    placed_units = {u for u, _style in placed}
    styles_by_unit: dict[int, set[int]] = {}
    for u, s in carriers:
        styles_by_unit.setdefault(u, set()).add(s)

    def _carrier(u: int, styles: set[int]) -> str:
        style = "body style " + "/".join(map(str, sorted(styles)))
        if u == 0:
            return f"{style} (common to all units)"
        return f"unit {u} {style}" if u in placed_units else f"unit {u}"

    where = ", ".join(_carrier(u, styles) for u, styles in sorted(styles_by_unit.items()))
    here = ", ".join(f"unit {u}" + (f" body style {b}" if b != 1 else "") for u, b in placed)
    if any(u != 0 and u not in placed_units for u in styles_by_unit):
        return (
            f"Pin '{label}' of {reference} is on {where}, which is not placed on this sheet "
            f"({reference} here: {here}). Place that unit, or wire the pin on the sheet "
            "that holds it."
        )
    return (
        f"Pin '{label}' of {reference} is on {where}, which this sheet does not draw "
        f"({reference} here: {here}). Switch the placed symbol to that body style in KiCad."
    )


def _child_text(node, key: str, default: str = "") -> str:
    c = node.find(key)
    return c.atoms[1].text if c is not None and len(c.atoms) > 1 else default


def _property(node, key: str) -> str | None:
    for p in node.find_all("property"):
        if len(p.atoms) > 2 and p.atoms[1].text == key:
            return p.atoms[2].text
    return None


# ---------------------------------------------------------------------------------------------
# The transform, in KiCad's order
# ---------------------------------------------------------------------------------------------

#: SCH_SYMBOL::SetOrientation's matrices (x1, y1, x2, y2): x' = x1*x + y1*y, y' = x2*x + y2*y.
_ROT: dict[int, Matrix] = {
    0: (1, 0, 0, 1),
    90: (0, 1, -1, 0),
    180: (-1, 0, 0, -1),
    270: (0, -1, 1, 0),
}
_MIR: dict[str, Matrix] = {"x": (1, 0, 0, -1), "y": (-1, 0, 0, 1)}
#: A lib pin's angle names the direction from its connection point toward the body, in
#: KiCad's internal frame (Y down).
_TOWARD: dict[int, Point] = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}


class Refusal(Exception):
    """A refused pin or call: bracketed reason codes and the text after them."""

    def __init__(self, codes: str | tuple[str, ...] | list[str], text: str):
        super().__init__(text)
        self.codes = (codes,) if isinstance(codes, str) else tuple(dict.fromkeys(codes))
        self.text = text


def tags(codes) -> str:
    """ "[a] [b]" for the distinct codes, in first-seen order."""
    return " ".join(f"[{c}]" for c in dict.fromkeys(codes))


def _compose(old: Matrix, temp: Matrix) -> Matrix:
    """KiCad's SetOrientation step: apply *old*, then *temp*."""
    ox1, oy1, ox2, oy2 = old
    tx1, ty1, tx2, ty2 = temp
    return (
        ox1 * tx1 + ox2 * ty1,
        oy1 * tx1 + oy2 * ty1,
        ox1 * tx2 + ox2 * ty2,
        oy1 * tx2 + oy2 * ty2,
    )


def _apply(t: Matrix, x: int, y: int) -> Point:
    return t[0] * x + t[1] * y, t[2] * x + t[3] * y


def _angle(text: str) -> int | None:
    """An orientation atom as KiCad's parser reads it (truncated to an int), if it is one of
    the four KiCad can load."""
    try:
        a = int(float(text))
    except ValueError:
        return None
    return a if a in _ROT else None


def symbol_transform(sym, ref: str) -> tuple[Point, Matrix]:
    """Origin and transform of a placed symbol, read in file order as KiCad reads them.

    ``(at x y angle)`` resets the transform to that rotation and a later ``(mirror x|y)``
    composes onto it, so KiCad rotates first and mirrors second. A ``(mirror)`` written before
    ``(at)`` is therefore lost, exactly as KiCad's parser loses it.
    """
    t, pos = _ROT[0], (0, 0)
    for ch in sym.lists:
        if ch.head == "at":
            a = ch.atoms
            pos = (iu(a[1].text), iu(a[2].text))
            ang = _angle(a[3].text) if len(a) > 3 else 0
            if ang is None:
                raise Refusal(
                    "unloadable",
                    f"{ref} is placed at angle {a[3].text}, which KiCad cannot load (0, 90, 180"
                    " or 270 only). Remedy: rotate it to one of those in KiCad.",
                )
            t = _ROT[ang]
        elif ch.head == "mirror" and len(ch.atoms) > 1 and ch.atoms[1].text in _MIR:
            t = _compose(t, _MIR[ch.atoms[1].text])
    return pos, t


#: Outward direction in degrees as the float interface reports it: 0 right, 90 down (+Y),
#: 180 left, 270 up.
_OUT_DEG: dict[Point, int] = {(1, 0): 0, (0, 1): 90, (-1, 0): 180, (0, -1): 270}


def effective_mirror(sym) -> str | None:
    """The mirror axis KiCad applies to a placed symbol: one written after its (at)."""
    mirror = None
    for ch in sym.lists:
        if ch.head == "at":
            mirror = None
        elif ch.head == "mirror" and len(ch.atoms) > 1 and ch.atoms[1].text in _MIR:
            mirror = ch.atoms[1].text
    return mirror


def transform_mm(
    px: float, py: float, pin_angle: float, cx: float, cy: float, angle: float, mirror
) -> tuple[float, float, int]:
    """A lib pin's sheet position (mm) and outward angle, for callers holding plain numbers.

    The same integer transform as symbol_transform and pin_end. Raises ValueError for an angle
    KiCad cannot load. Only schematic._get_pin_pos, the kiutils twin of the pin reads, calls it:
    tests compare that twin with the reads the tools make, which go through pin_point_mm.
    """
    rot, pang = _angle(str(angle)), _angle(str(pin_angle))
    if rot is None or pang is None:
        bad = angle if rot is None else pin_angle
        raise ValueError(f"angle {bad} cannot be loaded by KiCad, which accepts 0, 90, 180 or 270")
    t = _ROT[rot]
    if mirror in _MIR:
        t = _compose(t, _MIR[mirror])
    dx, dy = _apply(t, kiround(px * IU_PER_MM), -kiround(py * IU_PER_MM))
    tx, ty = _TOWARD[pang]
    x = (kiround(cx * IU_PER_MM) + dx) / IU_PER_MM
    y = (kiround(cy * IU_PER_MM) + dy) / IU_PER_MM
    return x, y, _OUT_DEG[_apply(t, -tx, -ty)]


def pin_point_mm(sym, pin, ref: str) -> tuple[float, float, int]:
    """Sheet position (mm) and outward angle of lib *pin* drawn by placed symbol *sym*."""
    pos, t = symbol_transform(sym, ref)
    x, y, out = pin_end(pos, t, pin, ref)
    return x / IU_PER_MM, y / IU_PER_MM, _OUT_DEG[out]


def pin_end(pos: Point, t: Matrix, pin, ref: str) -> tuple[int, int, Point]:
    """Connection point and outward unit vector of a lib pin under a symbol's transform."""
    at = pin.find("at")
    lx, ly = iu(at.atoms[1].text), iu(at.atoms[2].text)
    ang = _angle(at.atoms[3].text) if len(at.atoms) > 3 else 0
    if ang is None:
        raise Refusal(
            "unloadable",
            f"{ref} has a pin at angle {at.atoms[3].text}, which KiCad cannot load. Remedy: fix"
            " that library pin's angle.",
        )
    dx, dy = _apply(t, lx, -ly)  # a library's Y axis points up; the sheet's points down
    tx, ty = _TOWARD[ang]
    return pos[0] + dx, pos[1] + dy, _apply(t, -tx, -ty)


# ---------------------------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------------------------

#: Lines a label or a point can sit on.
_LINES = ("wire", "bus", "gline")
#: Every connectable line kind, bus entries included.
_SEGS = ("wire", "bus", "be", "gline")
#: Items the model does not handle, in the order they are reported (H-C12): what it is, why
#: this tool cannot judge it, and what to do instead.
_UNHANDLED = {
    "bus": ("a bus", "bus members are not modelled", "keep this net's wiring off the bus"),
    "bus_entry": (
        "a bus entry",
        "bus members are not modelled",
        "keep this net's wiring off the bus entry",
    ),
    "sheet_pin": (
        "a sheet pin",
        "its net is named across the hierarchy, which this tool does not follow",
        "draw this connection in KiCad, where the child sheet is visible",
    ),
    "text_var": (
        "text with a text variable",
        "its value is unknown here",
        "replace the text variable with the literal text",
    ),
    "jumper": (
        "a symbol with jumper pins",
        "KiCad 10 joins pins inside it",
        "keep this net off that symbol",
    ),
    "unit0_unplaced": (
        "a pad also drawn by a unit not placed on this sheet",
        "any such unit placed on another sheet draws a copy of the pad there, which this sheet"
        " cannot see",
        "place every unit that draws that pad on this sheet, or wire it where they are",
    ),
    "units_disagree": (
        "a symbol whose instance entries disagree on its unit",
        "a reused sheet draws different pins per instance",
        "give every instance of the sheet the same unit for it",
    ),
}

_LABEL_KINDS = {
    "label": "label",
    "global_label": "global label",
    "hierarchical_label": "hierarchical label",
    "netclass_flag": "directive label",
    "directive_label": "directive label",
}


class Sym:
    __slots__ = (
        "node",
        "ref",
        "refs",
        "value",
        "key",
        "lib",
        "derived",
        "units",
        "style",
        "is_power",
        "jumper",
        "lib_units",
        "pad_units",
    )

    def __init__(self, node, ref: str, value: str, key: str, lib, refs=frozenset()):
        self.node, self.ref, self.value, self.key, self.lib = node, ref, value, key, lib
        # Every reference it answers to: KiCad's at this sheet's live paths, or, when those are
        # unknown, every one its entries and property give it. ref is the one shown.
        self.refs: frozenset[str] = refs or frozenset({ref})
        self.derived = lib is not None and lib.find("extends") is not None
        self.units: list[int] = []
        self.style = 1
        self.is_power = False
        self.jumper = False
        self.lib_units: set[int] = {1}
        self.pad_units: dict[str, set[int]] = {}

    @property
    def amb(self) -> bool:
        """Its instance entries disagree on its unit."""
        return len(self.units) > 1


#: KiCad's text escapes and what UnescapeString reads them as (9.0.8 and 10.0.6
#: string_utils.cpp).
_ESCAPES = {
    "dblquote": '"',
    "quote": "'",
    "lt": "<",
    "gt": ">",
    "backslash": "\\",
    "slash": "/",
    "bar": "|",
    "comma": ",",
    "colon": ":",
    "space": " ",
    "dollar": "$",
    "tab": "\t",
    "return": "\n",
    "brace": "{",
}


def unescape(text: str) -> str:
    """Text as KiCad shows it: its UnescapeString (9.0.8 and 10.0.6 string_utils.cpp), which
    every shown text goes through. {slash} reads as '/', {dblquote} as '"' and so on; a brace
    group after $, ~, ^ or _ is markup and kept, as is an unknown or unterminated one."""
    if len(text) <= 2:
        return text
    out: list[str] = []
    ch = prev = ""
    i, n = 0, len(text)
    while i < n:
        prev, ch = ch, text[i]
        if ch != "{":
            out.append(ch)
            i += 1
            continue
        depth, token, i = 1, [], i + 1
        while i < n:
            ch = text[i]
            depth += (ch == "{") - (ch == "}")
            if depth <= 0:
                break
            token.append(ch)
            i += 1
        inner = "".join(token)
        if depth > 0:  # unterminated
            out.append("{" + unescape(inner))
        elif prev != "" and prev in "$~^_":
            out.append("{" + unescape(inner) + "}")
        elif inner in _ESCAPES:
            out.append(_ESCAPES[inner])
        else:
            out.append("{" + unescape(inner) + "}")
        i += 1
    return "".join(out)


def _netname_escape(text: str) -> str:
    """KiCad's EscapeString(text, CTX_NETNAME): '/' becomes {slash}, line breaks go."""
    return text.replace("/", "{slash}").replace("\n", "").replace("\r", "")


def netname(text: str) -> str:
    """The net name KiCad gives a label, or a power symbol by its Value:
    EscapeString(UnescapeString(text), CTX_NETNAME) (9.0.8 and 10.0.6 connection_graph.cpp
    GetNameForDriver, sch_pin.cpp GetDefaultNetName). So 'A/B' and 'A{slash}B' are one net,
    which KiCad shows as A/B."""
    return _netname_escape(unescape(text))


class Item:
    """One connectable thing: a point item (x, y) or a line (x, y)-(x2, y2)."""

    __slots__ = (
        "id",
        "kind",
        "x",
        "y",
        "x2",
        "y2",
        "sub",
        "text",
        "name",
        "ref",
        "num",
        "pname",
        "etype",
        "hidden",
        "nc",
        "out",
        "sym",
        "new",
        "tv",
        "cls",
        "w",
    )

    def __init__(self, kind: str, x: int, y: int, x2: int | None = None, y2: int | None = None):
        self.id = -1
        self.kind, self.x, self.y = kind, x, y
        self.x2 = x if x2 is None else x2
        self.y2 = y if y2 is None else y2
        self.sub: str | None = None  # label kind, or a sheet pin's sheet name
        self.text: str | None = None  # label or sheet pin text
        self.name: str | None = None  # the net name it carries by KiCad's rule, if any
        self.ref: str | None = None
        self.num: str | None = None
        self.pname: str | None = None
        self.etype: str | None = None
        self.hidden = self.nc = self.new = False
        self.out: Point | None = None
        self.sym: Sym | None = None
        self.tv = False  # text holding a text variable ("${"), whose value is unknown here
        self.cls: tuple[str, ...] = ()  # a label's non-empty Netclass field values (H-C2)
        self.w = 0  # a wire's or bus's stroke width as written; 0 is the default


def _conn_points(it: Item):
    if it.kind in ("wire", "bus", "be"):
        return ((it.x, it.y), (it.x2, it.y2))
    if it.kind == "gline":
        return ()
    return ((it.x, it.y),)


def _desc(it: Item) -> str:
    k = it.kind
    if k == "pin":
        if it.sym is not None and it.sym.is_power:
            value = unescape(it.sym.value)
            return f"power symbol {it.ref} (Value {value!r}) pin at {pt((it.x, it.y))}"
        extra = [w for w, f in (("hidden", it.hidden), ("no-connect type", it.nc)) if f]
        tail = f" ({', '.join(extra)})" if extra else ""
        return f"{it.ref}:{it.num} pin end at {pt((it.x, it.y))}{tail}"
    if k == "label":
        return f"{it.sub} '{unescape(it.text or '')}' at {pt((it.x, it.y))}"
    if k == "junction":
        return f"junction at {pt((it.x, it.y))}"
    if k == "nc":
        return f"no-connect flag at {pt((it.x, it.y))}"
    if k == "sheetpin":
        return f"sheet pin '{unescape(it.text or '')}' of sheet '{it.sub}' at {pt((it.x, it.y))}"
    if k == "be":
        return f"bus entry {pt((it.x, it.y))}-{pt((it.x2, it.y2))}"
    name = {"wire": "wire", "bus": "bus", "gline": "graphic line"}[k]
    return f"{'new ' if it.new else ''}{name} {pt((it.x, it.y))}-{pt((it.x2, it.y2))}"


def _pdesc(x: int, y: int, it: Item) -> str:
    if it.kind in ("wire", "bus", "be"):
        return f"end {pt((x, y))} of {_desc(it)}"
    return _desc(it)


def _xys(node) -> list[Point]:
    pts = node.find("pts")
    if pts is None:
        return []
    return [(iu(p.atoms[1].text), iu(p.atoms[2].text)) for p in pts.find_all("xy")]


def _pin_hidden(p) -> bool:
    if any(a.text == "hide" for a in p.atoms[3:]):
        return True
    h = p.find("hide")
    return h is not None and (len(h.atoms) < 2 or h.atoms[1].text == "yes")


#: Line index cell for the possible view, IU (2.54 mm).
_CELL = 25400


def _pad_order(num: str):
    """Pad numbers in natural order: 2 before 10."""
    return (0, int(num), "") if num.isdigit() else (1, 0, num)


class _UF:
    __slots__ = ("p",)

    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        p = self.p
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


class _Coarse:
    """The possible view: a union-find, and per component its names [(item, name)] and its
    unhandled items [(code, item)]."""

    __slots__ = ("uf", "names", "bad")

    def __init__(self, uf: _UF, names: dict, bad: dict):
        self.uf, self.names, self.bad = uf, names, bad

    def names_of(self, it: Item) -> list[tuple[Item, str]]:
        return self.names.get(self.uf.find(it.id), [])

    def bad_of(self, it: Item) -> list[tuple[str, Item]]:
        return self.bad.get(self.uf.find(it.id), [])


def _pad_units(lib) -> dict[str, set[int]]:
    """{pad number: units that draw it} of a library symbol; 0 is common to every unit, and a
    sub-symbol whose name encodes no unit counts as 0."""
    out: dict[str, set[int]] = {}
    for sub in lib.find_all("symbol"):
        ids = _lib_unit_style(sub)
        unit = ids[0] if ids else 0
        for pin in sub.find_all("pin"):
            out.setdefault(_child_text(pin, "number"), set()).add(unit)
    return out


def _pad_map(lib) -> dict[str, tuple[frozenset[int], frozenset[str]]]:
    """{pad number: (units that draw it, electrical types)} of a library symbol (H-C1). Unit 0
    is common to every unit, a sub-symbol whose name encodes no unit counts as 0, and body
    styles fold into their unit."""
    out: dict[str, tuple[set[int], set[str]]] = {}
    for sub in lib.find_all("symbol"):
        ids = _lib_unit_style(sub)
        unit = ids[0] if ids else 0
        for pin in sub.find_all("pin"):
            units, types = out.setdefault(_child_text(pin, "number"), (set(), set()))
            units.add(unit)
            types.add(pin.atoms[1].text if len(pin.atoms) > 1 else "unspecified")
    return {n: (frozenset(u), frozenset(t)) for n, (u, t) in out.items()}


@dataclass(frozen=True)
class SymRecord:
    """What one placed symbol brings to the reference and unit rules (H-C1, design 4.6)."""

    prop_ref: str  # its Reference property
    entries: tuple[tuple[str, str, int], ...]  # (path, reference, unit) per instance entry
    top_unit: int  # its top-level (unit)
    uuid: str
    key: str  # the library symbol it names (lib_name, else lib_id)
    pads: dict | None  # its definition's pad map; None when unresolved or derived
    derived: bool

    def at(self, path: str) -> tuple[str, int]:
        """KiCad's reference and unit for this symbol's instance at sheet path *path*.

        The entry for that path wins, the last one when several share it; else the first entry,
        which KiCad's parser makes the symbol's own reference and unit as it loads it; else the
        Reference property and the top-level unit (9.0.8: sch_symbol.cpp GetRef,
        GetUnitSelection, AddHierarchicalReference).
        """
        hit = None
        for p, ref, unit in self.entries:
            if p == path:
                hit = (ref, unit)
        if hit is not None:
            return hit
        if self.entries:
            return self.entries[0][1], self.entries[0][2]
        return self.prop_ref, self.top_unit

    def every_ref(self, extra=()) -> frozenset[str]:
        """Every reference it could carry, for when its live paths cannot be known."""
        return frozenset(
            {self.prop_ref, *(r for _p, r, _u in self.entries), *(r for r, _ in extra)}
        )

    def every_unit(self, extra=()) -> tuple[int, ...]:
        """Every unit it could be, for when its live paths cannot be known (I02)."""
        units = {u for _p, _r, u in self.entries} | {u for _r, u in extra}
        return tuple(sorted(units)) or (self.top_unit,)


def _instance_entries(node) -> tuple[tuple[str, str, int], ...]:
    """(path, reference, unit) of a placed symbol's instance entries, every project, in file
    order. A missing reference reads as the Reference property, a missing unit as 1 (the
    default of KiCad's SCH_SYMBOL_INSTANCE)."""
    prop = _property(node, "Reference") or "?"
    out = []
    inst = node.find("instances")
    for proj in inst.find_all("project") if inst is not None else ():
        for path in proj.find_all("path"):
            if len(path.atoms) < 2:
                continue
            try:
                unit = int(_child_text(path, "unit", "1"))
            except ValueError:
                unit = 1
            out.append((path.atoms[1].text, _child_text(path, "reference") or prop, unit))
    return tuple(out)


def _record(node, libs: dict, maps: dict) -> SymRecord:
    """*maps* memoises pad maps per library name across the symbols of one file."""
    key = lib_key(node)
    lib = libs.get(key)
    derived = lib is not None and lib.find("extends") is not None
    pads = None
    if lib is not None and not derived:
        pads = maps.get(key)
        if pads is None:
            pads = maps[key] = _pad_map(lib)
    return SymRecord(
        _property(node, "Reference") or "?",
        _instance_entries(node),
        _sym_unit_cst(node),
        _child_text(node, "uuid"),
        key,
        pads,
        derived,
    )


def _lib_name(text: str) -> str:
    """A library symbol name as KiCad's parser keeps it: {slash} reads as '/' in lib_symbols
    names, lib_name and lib_id alike (9.0.8 and 10.0.6 sch_io_kicad_sexpr_parser.cpp)."""
    return text.replace("{slash}", "/")


def lib_key(node) -> str:
    """The lib_symbols name KiCad draws placed symbol *node* from: its lib_name when present,
    else its lib_id, matched exactly with no fallback (9.0.8 sch_screen.cpp
    UpdateLocalLibSymbolLinks, sch_symbol.cpp GetSchSymbolLibraryName)."""
    return _lib_name(_child_text(node, "lib_name") or _child_text(node, "lib_id"))


def _lib_entries(root):
    libs = root.find("lib_symbols")
    for ls in libs.find_all("symbol") if libs is not None else ():
        if len(ls.atoms) > 1:
            yield _lib_name(ls.atoms[1].text), ls


def lib_index(root) -> dict:
    """The sheet's lib_symbols entries by name, for lookups by lib_key. Of two entries with one
    name the later wins, as SCH_SCREEN::AddLibSymbol replaces the earlier (9.0.8, 10.0.6)."""
    return dict(_lib_entries(root))


def lib_repeats(root) -> dict[str, int]:
    """Names lib_symbols holds more than once, with how many times."""
    counts = Counter(name for name, _ls in _lib_entries(root))
    return {name: n for name, n in counts.items() if n > 1}


@dataclass(frozen=True)
class SheetFacts:
    """What the hierarchy rules need from one sheet file, never the tree itself."""

    uuid: str
    version: int
    records: tuple[SymRecord, ...]  # its placed symbols, in file order
    children: tuple[tuple[str, str], ...]  # (sheet block uuid, file name) per child sheet
    legacy: tuple[tuple[str, str, int], ...]  # a KiCad 6 root's (symbol_instances) table


def sheet_facts(root, records: tuple[SymRecord, ...] | None = None) -> SheetFacts:
    """The facts of a parsed sheet; *records* when the caller has them already."""
    if records is None:
        libs, maps = lib_index(root), {}
        records = tuple(_record(s, libs, maps) for s in root.find_all("symbol"))
    try:
        version = int(_child_text(root, "version", "0"))
    except ValueError:
        version = 0
    children = tuple(
        (_child_text(sh, "uuid"), n)
        for sh in root.find_all("sheet")
        if (n := _shared._sheet_file_cst(sh))
    )
    legacy = tuple(
        (path.atoms[1].text, _child_text(path, "reference"), int(_child_text(path, "unit", "1")))
        for table in root.find_all("symbol_instances")
        for path in table.find_all("path")
        if len(path.atoms) > 1 and _child_text(path, "unit", "1").isdigit()
    )
    return SheetFacts(_child_text(root, "uuid"), version, records, children, legacy)


#: Facts of the sheet files read for the hierarchy, by a blake2b digest of their bytes: keyed by
#: content, so an edited file is never served stale; bounded, least recently used out; locked,
#: because the server may run tools on worker threads.
_FACTS: OrderedDict[bytes, SheetFacts] = OrderedDict()
_FACTS_MAX = 512
_FACTS_LOCK = threading.Lock()


def _file_facts(data: bytes) -> SheetFacts:
    digest = hashlib.blake2b(data, digest_size=16).digest()
    with _FACTS_LOCK:
        hit = _FACTS.get(digest)
        if hit is not None:
            _FACTS.move_to_end(digest)
            return hit
    facts = sheet_facts(_cst.parse(data).lists[0])
    with _FACTS_LOCK:
        _FACTS[digest] = facts
        while len(_FACTS) > _FACTS_MAX:
            _FACTS.popitem(last=False)
    return facts


#: Below this version KiCad keeps instance data in the root's (symbol_instances) table, not on
#: the symbols (9.0.8 eeschema_helpers.cpp).
_ON_SYMBOL_VERSION = 20221002
#: Sheet instances walked before giving up on live paths: reuse inside reuse multiplies them.
_MAX_INSTANCES = 4096
#: Directories above this sheet's own that are searched for its project file.
_PROJECT_LEVELS = 3


def _atom(text: bytes) -> bytes:
    """A regex for one s-expression atom with this text, quoted or bare, the way _cst.TOKEN
    reads it: a bare atom runs to whitespace, a parenthesis, a quote or the end."""
    e = re.escape(text)
    return rb'(?:"' + e + rb'"|' + e + rb'(?=[\s()"]|\Z))'


def _head(*words: bytes) -> bytes:
    """A regex for the start of a list whose leading atoms are *words*, any whitespace between."""
    return rb"\(\s*" + rb"\s*".join(_atom(w) for w in words)


#: A sheet block or a KiCad 6 instance table somewhere in the bytes. A match inside a quoted
#: string is a false positive, which only costs a parse.
_STRUCTURE = re.compile(_head(b"sheet") + b"|" + _head(b"symbol_instances"))


def refs_pattern(refs) -> re.Pattern | None:
    """A regex matching every place a sheet's bytes can give a placed symbol one of *refs*: an
    instance entry's or a KiCad 6 table's (reference R), or the (property Reference R). A file
    it does not match holds no symbol carrying them, so it need not be parsed. None when a
    reference holds a character the file would escape (quote, backslash, TAB, LF, CR), or is
    empty: then every sheet is read."""
    texts = []
    for r in sorted(refs):
        if not r or any(c in r for c in '"\\\t\n\r'):
            return None
        texts.append(r.encode("utf-8", "surrogateescape"))
    if not texts:
        return None
    ref = b"(?:" + b"|".join(_atom(t) for t in texts) + b")"
    return re.compile(
        _head(b"reference")
        + rb"\s*"
        + ref
        + b"|"
        + _head(b"property", b"Reference")
        + rb"\s*"
        + ref
    )


@dataclass
class OtherSheet:
    """Another sheet file of the hierarchy. Its bytes are kept, unparsed, until a reference the
    call asks about may be on it."""

    name: str
    paths: tuple[str, ...]  # its instance paths, when the live paths are known
    data: bytes | None
    facts: SheetFacts | None

    @property
    def records(self) -> tuple[SymRecord, ...]:
        return self.facts.records if self.facts is not None else ()


@dataclass
class Hierarchy:
    """This sheet's place in its project, as far as the reference and unit rules need it.

    With *live* set, references and units are read as KiCad reads them, at the instance paths
    the project's hierarchy gives each sheet. Without it (no project found, this sheet not
    reached from it, a KiCad 6 root, or too many instances), every entry of every symbol counts,
    as the prototype counted them, and a KiCad 6 root's table counts too.
    """

    live: tuple[str, ...] = ()  # this sheet's instance paths
    others: list[OtherSheet] = field(default_factory=list)
    legacy: dict[str, list[tuple[str, int]]] = field(default_factory=dict)  # by symbol uuid
    error: Refusal | None = None


def _project_roots(path: Path) -> list[Path]:
    """Root schematics that may own this sheet, nearest first: the .kicad_sch named after the
    only .kicad_pro in its directory, then in each of the _PROJECT_LEVELS directories above."""
    out, d = [], path.parent
    for _ in range(_PROJECT_LEVELS + 1):
        pros = list(d.glob("*.kicad_pro"))
        if len(pros) == 1 and pros[0].with_suffix(".kicad_sch").is_file():
            out.append(pros[0].with_suffix(".kicad_sch"))
        if d.parent == d:
            break
        d = d.parent
    return out


#: An unparsed sheet: no sheet block and no instance table, so it adds no path and no
#: reference of its own until its symbols are needed.
_LEAF = SheetFacts("", _ON_SYMBOL_VERSION, (), (), ())


def _real(p: Path) -> Path:
    """*p* resolved, or made absolute when it cannot be: resolving a symlink loop raises
    (RuntimeError before Python 3.13, OSError after). No reader can open such a file, KiCad
    included, so it then counts as a missing sheet."""
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        return p.absolute()


def hierarchy(path: str | Path, here: SheetFacts) -> Hierarchy:
    """Walk this sheet's project for the reference and unit rules (H-C1, design 4.6).

    A sheet block's file name resolves against its parent's directory, then the root's. This
    sheet's facts come from the tree being edited, never from disk. A sheet is parsed only when
    its bytes can hold a sheet block or an instance table; the rest wait in OtherSheet until a
    reference check needs them (Model.prepare). A missing sheet file is skipped, as KiCad loads
    it empty; one that cannot be read or parsed makes every reference check refuse, since what
    it holds is unknown.
    """
    path = Path(path)
    me = _real(path)
    cache: dict[Path, tuple[bytes | None, SheetFacts] | None] = {me: (None, here)}
    bad: list[str] = []

    def facts(f: Path) -> SheetFacts | None:
        rf = _real(f)
        if rf not in cache:
            try:
                if not f.is_file():
                    cache[rf] = None
                else:
                    data = f.read_bytes()
                    if _STRUCTURE.search(data):
                        cache[rf] = (None, _file_facts(data))
                    else:
                        cache[rf] = (data, _LEAF)
            except Exception:  # noqa: BLE001 - any failure leaves the sheet unknown
                cache[rf] = None
                bad.append(f.name)
        hit = cache[rf]
        return hit[1] if hit is not None else None

    def other(rf: Path, paths: tuple[str, ...]) -> OtherSheet:
        data, ff = cache[rf] or (None, _LEAF)
        return OtherSheet(rf.name, paths, data, None if data is not None else ff)

    def child_of(f: Path, base: Path, name: str) -> Path:
        cands = [f.parent / name, base / name]
        return next((c for c in cands if c.exists()), cands[0])

    roots = _project_roots(path)
    for root in roots:
        top = facts(root)
        if top is None or top is _LEAF or top.version < _ON_SYMBOL_VERSION:
            continue
        found: dict[Path, list[str]] = {}
        stack: list[tuple[Path, str, tuple[Path, ...]]] = [(root, f"/{top.uuid}", (_real(root),))]
        n = 0
        while stack and n < _MAX_INSTANCES:
            f, kpath, chain = stack.pop()
            ff = facts(f)
            if ff is None:
                continue
            found.setdefault(chain[-1], []).append(kpath)
            n += 1
            for su, name in ff.children:
                c = child_of(f, root.parent, name)
                rc = _real(c)
                if rc not in chain:  # KiCad refuses a sheet that contains itself
                    stack.append((c, f"{kpath}/{su}", (*chain, rc)))
        if stack or me not in found:
            continue
        error = _unreadable(bad)
        if error is not None:
            break
        others = [other(rf, tuple(ps)) for rf, ps in found.items() if rf != me]
        return Hierarchy(tuple(found[me]), others)
    seen: set[Path] = set()
    todo = [(path, path.parent)] + [(r, r.parent) for r in roots]
    others, legacy = [], {}
    while todo:
        f, base = todo.pop()
        rf = _real(f)
        if rf in seen:
            continue
        seen.add(rf)
        ff = facts(f)
        if ff is None:
            continue
        if rf != me:
            others.append(other(rf, ()))
        if ff.version < _ON_SYMBOL_VERSION:
            for p, ref, unit in ff.legacy:
                legacy.setdefault(p.rsplit("/", 1)[-1], []).append((ref, unit))
        todo += [(child_of(f, base, name), base) for _su, name in ff.children]
    return Hierarchy((), others, legacy, _unreadable(bad))


def _legacy(facts: SheetFacts) -> dict[str, list[tuple[str, int]]]:
    """A KiCad 6 sheet's own table, by symbol uuid, for a sheet read alone."""
    out: dict[str, list[tuple[str, int]]] = {}
    if facts.version < _ON_SYMBOL_VERSION:
        for p, ref, unit in facts.legacy:
            out.setdefault(p.rsplit("/", 1)[-1], []).append((ref, unit))
    return out


def _unreadable(bad: list[str]) -> Refusal | None:
    if not bad:
        return None
    return Refusal(
        "dup_ref",
        f"sheet {bad[0]} of this hierarchy could not be read, so the reference cannot be checked"
        " for uniqueness. Remedy: fix or remove that sheet; otherwise stop and report.",
    )


def _one_part(group: list[tuple[SymRecord, tuple[int, ...]]]) -> str | None:
    """None when the placed symbols sharing a reference, each with the units it places, are one
    part, else why not (H-C1): each places one distinct unit, every definition resolves and is
    not derived, and the definitions agree on which units draw each pad and with which electrical
    types."""
    units = [u for _r, u in group]
    if not all(len(u) == 1 for u in units) or len({u[0] for u in units}) != len(units):
        return "they do not each place a distinct unit"
    for r, _u in group:
        if r.pads is None:
            return f"library symbol '{r.key}' {'is derived' if r.derived else 'does not resolve'}"
    first = group[0][0]
    assert first.pads is not None
    for r, _u in group[1:]:
        assert r.pads is not None
        if r.pads == first.pads:
            continue
        differ = (set(r.pads) ^ set(first.pads)) or {
            n for n in r.pads if r.pads[n] != first.pads[n]
        }
        num = min(differ, key=_pad_order)

        def show(pads: dict) -> str:
            if num not in pads:
                return "no such pad"
            u, t = pads[num]
            return f"units {','.join(map(str, sorted(u)))} as {'/'.join(sorted(t))}"

        return (
            f"their library definitions disagree on pad {num} ('{first.key}': {show(first.pads)};"
            f" '{r.key}': {show(r.pads)})"
        )
    return None


def _edges(poly: list[Point]):
    return zip(poly, poly[1:] + poly[:1])


def _in_poly(x: int, y: int, poly: list[Point]) -> bool:
    """Even-odd point in polygon, exact in integers. A point on the outline may fall either
    way; every caller tests the outline too."""
    inside = False
    for (ax, ay), (bx, by) in _edges(poly):
        if (ay > y) != (by > y):
            lhs, rhs = (x - ax) * (by - ay), (y - ay) * (bx - ax)
            if (lhs < rhs) if by > ay else (lhs > rhs):
                inside = not inside
    return inside


def _near_poly(x: int, y: int, poly: list[Point], r2: int) -> bool:
    """Inside the polygon or within sqrt(r2) of its outline."""
    return _in_poly(x, y, poly) or any(_within(x, y, *a, *b, r2) for a, b in _edges(poly))


def _seg_poly(ax: int, ay: int, bx: int, by: int, poly: list[Point], r2: int) -> bool:
    """A segment inside the polygon, crossing its outline, or within sqrt(r2) of it."""
    if _near_poly(ax, ay, poly, r2) or _near_poly(bx, by, poly, r2):
        return True
    for (cx, cy), (dx, dy) in _edges(poly):
        if _within(cx, cy, ax, ay, bx, by, r2):
            return True
        if (
            _side(cx, cy, dx, dy, ax, ay) * _side(cx, cy, dx, dy, bx, by) < 0
            and _side(ax, ay, bx, by, cx, cy) * _side(ax, ay, bx, by, dx, dy) < 0
        ):
            return True
    return False


def _overlaps(lines: list[Item]) -> list[tuple[Item, Item]]:
    """Pairs of same-layer lines (wire with wire, bus with bus) overlapping collinearly."""
    out = []
    for layer in ("wire", "bus"):
        hz: dict[int, list] = {}
        vt: dict[int, list] = {}
        dg = []
        for ln in lines:
            if ln.kind != layer or (ln.x == ln.x2 and ln.y == ln.y2):
                continue
            if ln.y == ln.y2:
                hz.setdefault(ln.y, []).append((min(ln.x, ln.x2), max(ln.x, ln.x2), ln))
            elif ln.x == ln.x2:
                vt.setdefault(ln.x, []).append((min(ln.y, ln.y2), max(ln.y, ln.y2), ln))
            else:
                dg.append(ln)
        for groups in (hz, vt):
            for lst in groups.values():
                lst.sort(key=lambda r: (r[0], r[1]))
                for i in range(len(lst)):
                    for j in range(i + 1, len(lst)):
                        if lst[j][0] >= lst[i][1]:
                            break
                        out.append((lst[i][2], lst[j][2]))
        for i, a in enumerate(dg):
            ax, ay = a.x2 - a.x, a.y2 - a.y
            for b in dg[i + 1 :]:
                bx, by = b.x2 - b.x, b.y2 - b.y
                if ax * by - ay * bx != 0 or (b.x - a.x) * ay - (b.y - a.y) * ax != 0:
                    continue
                l2 = ax * ax + ay * ay
                t1 = (b.x - a.x) * ax + (b.y - a.y) * ay
                t2 = (b.x2 - a.x) * ax + (b.y2 - a.y) * ay
                if min(l2, max(t1, t2)) - max(0, min(t1, t2)) > 0:
                    out.append((a, b))
    return out


# ---------------------------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------------------------


class Model:
    """One sheet's connectable items, with pins placed where KiCad draws them."""

    def __init__(self, root, path: str | Path | None = None):
        self.root = root
        self.path = path  # the sheet's file, for the hierarchy; None reads this sheet alone
        self.items: list[Item] = []
        self.syms: list[Sym] = []
        self.pads: dict[tuple[str, str], list[Item]] = {}
        self.placed: dict[str, set[int]] = {}  # units this sheet places per reference
        self.jumpers: list[list[Item]] = []
        self.areas: list[list[Point]] = []  # rule area outlines
        self.libs: dict = lib_index(root)
        self.lib_repeats = lib_repeats(root)
        self._dirty = True
        self._coarse: _Coarse | None = None
        maps: dict = {}
        self.records = {id(n): _record(n, self.libs, maps) for n in root.find_all("symbol")}
        #: The path kicad-cli gives this sheet when it reads it alone, as its own root.
        self.own_path = f"/{_child_text(root, 'uuid')}"
        here = sheet_facts(root, tuple(self.records.values()))
        self.hier = hierarchy(path, here) if path is not None else Hierarchy(legacy=_legacy(here))
        self._scanned: set[str] = set()  # references the other sheets were read for
        self._unique: set[str] = set()  # references already found to name one part
        for ch in root.lists:
            self._take(ch)

    def add(self, it: Item) -> Item:
        """Index an item. Items a call adds go through here too, so the next pin of the same
        call is checked against them (H-C15)."""
        it.id = len(self.items)
        self.items.append(it)
        self._dirty = True
        return it

    def _take(self, ch) -> None:
        h = ch.head
        if h in ("wire", "bus", "polyline"):
            xys = _xys(ch)
            if h == "polyline":
                # A 2-point graphic polyline loads as a line a label can attach to; three or
                # more points load as a graphic shape and connect nothing.
                if len(xys) == 2:
                    self.add(Item("gline", *xys[0], *xys[1]))
                return
            stroke = ch.find("stroke")
            width = iu(_child_text(stroke, "width", "0")) if stroke is not None else 0
            for a, b in zip(xys, xys[1:]):
                self.add(Item(h, *a, *b)).w = max(width, 0)
        elif h == "rule_area":
            poly = ch.find("polyline")
            xys = _xys(poly) if poly is not None else []
            if xys:
                self.areas.append(xys)
        elif h == "bus_entry":
            at, size = ch.find("at"), ch.find("size")
            x, y = iu(at.atoms[1].text), iu(at.atoms[2].text)
            dx, dy = (
                (iu(size.atoms[1].text), iu(size.atoms[2].text)) if size is not None else (0, 0)
            )
            self.add(Item("be", x, y, x + dx, y + dy))
        elif h in ("junction", "no_connect"):
            at = ch.find("at")
            self.add(
                Item(
                    "junction" if h == "junction" else "nc",
                    iu(at.atoms[1].text),
                    iu(at.atoms[2].text),
                )
            )
        elif h in _LABEL_KINDS:
            at = ch.find("at")
            it = self.add(Item("label", iu(at.atoms[1].text), iu(at.atoms[2].text)))
            it.sub = _LABEL_KINDS[h]
            it.text = ch.atoms[1].text if len(ch.atoms) > 1 else ""
            it.name = None if h in ("netclass_flag", "directive_label") else netname(it.text)
            # Every property but Intersheetrefs can feed the net's name or class (H-C8).
            props = [
                (q.atoms[1].text, q.atoms[2].text)
                for q in ch.find_all("property")
                if len(q.atoms) > 2 and q.atoms[1].text != "Intersheetrefs"
            ]
            it.tv = any("${" in unescape(v) for v in (it.text, *(v for _k, v in props)))
            it.cls = tuple(v for k, v in props if k == "Netclass" and v)
        elif h == "sheet":
            sheet_name = _property(ch, "Sheetname") or _property(ch, "Sheet name") or "?"
            for p in ch.find_all("pin"):
                at = p.find("at")
                if at is None:
                    continue
                it = self.add(Item("sheetpin", iu(at.atoms[1].text), iu(at.atoms[2].text)))
                it.sub = sheet_name
                it.text = p.atoms[1].text if len(p.atoms) > 1 else ""
        elif h == "symbol":
            self._take_symbol(ch)

    def _take_symbol(self, node) -> None:
        # A part is known by the reference KiCad reads for it, which the Reference property need
        # not be: KiCad sets the property from the first instance entry, possibly one another
        # project left behind (smps-com in KiCad's demos carries R2 on eight resistors).
        rec, h = self.records[id(node)], self.hier
        if h.live:
            ref, refs = rec.at(h.live[0])[0], frozenset(rec.at(p)[0] for p in h.live)
        else:
            ref = rec.at(self.own_path)[0]
            refs = rec.every_ref(h.legacy.get(rec.uuid, ())) | {ref}
        value = _property(node, "Value") or ""
        key = lib_key(node)
        s = Sym(node, ref, value, key, self.libs.get(key), refs)
        self.syms.append(s)
        if s.lib is None or s.derived:
            return
        pos, t = symbol_transform(node, ref)
        if h.live:
            s.units = sorted({rec.at(p)[1] for p in h.live})
        else:
            s.units = list(rec.every_unit(h.legacy.get(rec.uuid, ())))
        s.style = _sym_body_style_cst(node)
        s.is_power = s.lib.find("power") is not None
        s.lib_units = {
            ids[0] for sub in s.lib.find_all("symbol") if (ids := _lib_unit_style(sub)) and ids[0]
        } or {1}
        s.pad_units = _pad_units(s.lib)
        for r in s.refs:
            self.placed.setdefault(r, set()).update(s.units)
        alts = {}
        for p in node.find_all("pin"):
            a = p.find("alternate")
            if a is not None and len(a.atoms) > 1 and len(p.atoms) > 1:
                alts[p.atoms[1].text] = a.atoms[1].text
        seen: set[int] = set()
        pins: list[Item] = []
        for u in s.units:
            for sub in _instance_units(s.lib, u, s.style):
                if id(sub) in seen:
                    continue
                seen.add(id(sub))
                for p in sub.find_all("pin"):
                    it = self._take_pin(s, p, pos, t, alts)
                    if it is not None:
                        pins.append(it)
        # KiCad 10 jumpers join pins inside one symbol (H-C9).
        if _child_text(s.lib, "duplicate_pin_numbers_are_jumpers") == "yes":
            s.jumper = True
            by_num: dict[str, list[Item]] = {}
            for it in pins:
                by_num.setdefault(it.num or "", []).append(it)
            self.jumpers += [g for g in by_num.values() if len(g) > 1]
        groups = s.lib.find("jumper_pin_groups")
        if groups is not None:
            s.jumper = s.jumper or bool(groups.lists)
            for g in groups.lists:
                nums = {a.text for a in g.atoms}
                members = [it for it in pins if it.num in nums]
                if len(members) > 1:
                    self.jumpers.append(members)

    def _take_pin(self, s: Sym, p, pos: Point, t: Matrix, alts: dict) -> Item | None:
        if p.find("at") is None:
            return None
        x, y, out = pin_end(pos, t, p, s.ref)
        it = self.add(Item("pin", x, y))
        it.sym, it.ref, it.out = s, s.ref, out
        it.num = _child_text(p, "number")
        for r in s.refs:
            self.pads.setdefault((r, it.num), []).append(it)
        it.pname = _child_text(p, "name")
        it.etype = p.atoms[1].text if len(p.atoms) > 1 else "unspecified"
        # A placed alternate sets the pin's type, unless KiCad's SetAlt drops it: one spelled
        # like the pin's own name, or one the library pin does not define.
        alt = alts.get(it.num)
        if alt and alt != it.pname:
            for a in p.find_all("alternate"):
                if len(a.atoms) > 2 and a.atoms[1].text == alt:
                    it.etype = a.atoms[2].text
                    break
        it.hidden = _pin_hidden(p)
        it.nc = it.etype == "no_connect"
        # KiCad's rule (sch_pin.cpp IsGlobalPower and GetDefaultNetName, 9.0.8 and 10.0.6): a
        # power_in pin names its net when it sits on a power symbol, by the symbol's Value, or
        # when it is hidden, by the library pin's own name, never an alternate's. PWR_FLAG's pin
        # is power_out and a visible power_in pin on an ordinary part names nothing.
        if it.etype == "power_in" and (s.is_power or it.hidden):
            it.name = netname(s.value) if s.is_power else _netname_escape(it.pname or "")
        it.tv = it.name is not None and "${" in it.name
        return it

    # -- the narrow view: joins every KiCad reader makes ----------------------------------
    def ensure(self) -> None:
        if self._dirty:
            self._build()

    def _near(self, x: int, y: int, kinds) -> list[Item]:
        """Lines passing exactly through (x, y)."""
        out = []
        for lo, hi, ln in self._H.get(y, ()):
            if ln.kind in kinds and lo <= x <= hi:
                out.append(ln)
        for lo, hi, ln in self._V.get(x, ()):
            if ln.kind in kinds and lo <= y <= hi and ln not in out:
                out.append(ln)
        for ln in self._D:
            if ln.kind in kinds and _within(x, y, ln.x, ln.y, ln.x2, ln.y2, 0):
                out.append(ln)
        return out

    def _certain(self, it: Item) -> bool:
        k = it.kind
        if k == "wire":
            return it.id not in self._unreliable
        if k == "pin":
            return not it.nc and not (it.sym is not None and it.sym.amb)
        if k == "label":
            return it.name is not None
        return k == "junction"

    def _lone_wire(self, it: Item) -> Item | None:
        """The one reliable wire whose interior a named label sits on alone, if any."""
        if len(self._pts[(it.x, it.y)]) != 1:
            return None
        lines = self._near(it.x, it.y, _LINES)
        if len(lines) == 1 and lines[0].kind == "wire" and lines[0].id not in self._unreliable:
            return lines[0]
        return None

    def _build(self) -> None:
        lines = [it for it in self.items if it.kind in _LINES]
        H: dict[int, list] = {}
        V: dict[int, list] = {}
        D: list[Item] = []
        for ln in lines:
            if ln.y == ln.y2:
                H.setdefault(ln.y, []).append((min(ln.x, ln.x2), max(ln.x, ln.x2), ln))
            elif ln.x == ln.x2:
                V.setdefault(ln.x, []).append((min(ln.y, ln.y2), max(ln.y, ln.y2), ln))
            else:
                D.append(ln)
        self._H, self._V, self._D = H, V, D
        pts: dict[Point, list[Item]] = {}
        for it in self.items:
            for xy in _conn_points(it):
                pts.setdefault(xy, []).append(it)
        self._pts = pts
        # A wire with a junction or a bus-entry end on its interior is cut there by kicad-cli 9
        # and joined by the GUI; a collinear overlap is merged by the GUI on load and not by
        # kicad-cli. Readers disagree about both, so the narrow view uses neither.
        unreliable: set[int] = set()
        for it in self.items:
            ends = ((it.x, it.y),) if it.kind == "junction" else _conn_points(it)
            if it.kind not in ("junction", "be"):
                continue
            for x, y in ends:
                for ln in self._near(x, y, ("wire", "bus")):
                    if (x, y) not in ((ln.x, ln.y), (ln.x2, ln.y2)):
                        unreliable.add(ln.id)
        for a, b in _overlaps(lines):
            unreliable.update((a.id, b.id))
        self._unreliable = unreliable
        uf = _UF(len(self.items))
        for its in pts.values():
            cond = [it for it in its if self._certain(it)]
            for it in cond[1:]:
                uf.union(cond[0].id, it.id)
        for it in self.items:
            if it.kind == "label" and it.name is not None and (w := self._lone_wire(it)):
                uf.union(it.id, w.id)
        self._C = uf
        self._cnames: dict[int, dict[str, list[Item]]] = {}
        for it in self.items:
            if it.name is not None and "${" not in it.name:
                self._cnames.setdefault(uf.find(it.id), {}).setdefault(it.name, []).append(it)
        self._coarse = None
        self._poss: dict[int, set[str]] = {}
        self._cert: dict[int, set[str]] | None = None
        self._dirty = False

    def narrow_names(self, it: Item) -> dict[str, list[Item]]:
        """{name: [items]} on the narrow component of *it*."""
        self.ensure()
        return self._cnames.get(self._C.find(it.id), {})

    # -- the possible view: every join any reader might make ------------------------------
    def coarse(self) -> _Coarse:
        self.ensure()
        if self._coarse is None:
            self._coarse = self._build_coarse()
        return self._coarse

    def _build_coarse(self) -> _Coarse:
        uf = _UF(len(self.items))
        pts: list[tuple[int, int, Item]] = []
        for it in self.items:
            if it.kind in _SEGS:
                pts += [(it.x, it.y, it), (it.x2, it.y2, it)]
            else:
                pts.append((it.x, it.y, it))
        grid: dict[Point, list] = {}
        for q in pts:
            grid.setdefault((q[0] // TOL, q[1] // TOL), []).append(q)
        for (cx, cy), cell in grid.items():  # two points within the margin
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for u, v, b in grid.get((cx + dx, cy + dy), ()):
                        for x, y, a in cell:
                            if a.id < b.id and _d2(x, y, u, v) <= TOL2:
                                uf.union(a.id, b.id)
        cells: dict[Point, list[Item]] = {}
        for ln in self.items:
            if ln.kind in _SEGS and (ln.x, ln.y) != (ln.x2, ln.y2):
                for gx in range(
                    (min(ln.x, ln.x2) - TOL) // _CELL, (max(ln.x, ln.x2) + TOL) // _CELL + 1
                ):
                    for gy in range(
                        (min(ln.y, ln.y2) - TOL) // _CELL, (max(ln.y, ln.y2) + TOL) // _CELL + 1
                    ):
                        cells.setdefault((gx, gy), []).append(ln)
        for x, y, a in pts:  # a point within the margin of a line
            for ln in cells.get((x // _CELL, y // _CELL), ()):
                if ln is not a and _within(x, y, ln.x, ln.y, ln.x2, ln.y2, TOL2):
                    uf.union(a.id, ln.id)
        for group in [*self.pads.values(), *self.jumpers]:
            for it in group[1:]:
                uf.union(group[0].id, it.id)
        first: dict[str, int] = {}
        for it in self.items:  # same-text names join, transitively
            if it.name is not None:
                uf.union(first.setdefault(it.name, it.id), it.id)
        names: dict[int, list[tuple[Item, str]]] = {}
        bad: dict[int, list[tuple[str, Item]]] = {}
        for it in self.items:
            r = uf.find(it.id)
            if it.name is not None:
                names.setdefault(r, []).append((it, it.name))
            for code in self._unhandled(it):
                bad.setdefault(r, []).append((code, it))
        return _Coarse(uf, names, bad)

    def _unhandled(self, it: Item) -> list[str]:
        k = it.kind
        if k in ("bus", "be", "sheetpin"):
            return [{"bus": "bus", "be": "bus_entry", "sheetpin": "sheet_pin"}[k]]
        out = ["text_var"] if it.tv else []
        if k == "pin" and it.sym is not None:
            s = it.sym
            if s.jumper:
                out.append("jumper")
            drawn_by = s.pad_units.get(it.num or "", set())
            needed = s.lib_units if 0 in drawn_by else drawn_by
            if any(not needed <= self.placed.get(r, set()) for r in s.refs):
                out.append("unit0_unplaced")
            if s.amb:
                out.append("units_disagree")
        return out

    def check_loadable(self) -> None:
        """A derived, unresolved or twice-defined symbol anywhere on the sheet refuses the whole
        call: its pins are unknown, and KiCad stacks an unresolved symbol's pins at its origin
        (PI-17)."""
        for s in self.syms:
            if s.key in self.lib_repeats:
                # KiCad draws the last entry; which one the file meant is unknown.
                raise Refusal(
                    "derived",
                    f"{s.ref} uses library symbol '{s.key}', and this file's lib_symbols holds"
                    f" {self.lib_repeats[s.key]} entries named '{s.key}', as a hand edit or a"
                    " merge can leave. KiCad draws the last of them, but which one the file"
                    " meant is unknown, so its pins are too. Remedy: open and save the sheet in"
                    " KiCad, which keeps only the entry it draws; otherwise stop and report.",
                )
            if s.derived:
                what = "is derived and was never flattened into this file"
                remedy = "with place_component from a non-derived library symbol"
            elif s.lib is None:
                what = "is not in this file's lib_symbols"
                remedy = "with place_component, which copies its library symbol into the file"
            else:
                continue
            raise Refusal(
                "derived",
                f"{s.ref} uses library symbol '{s.key}', which {what}, so its pins are unknown"
                f" here. Remedy: re-place {s.ref} {remedy}; otherwise stop and report.",
            )

    def references_of(self, ref: str) -> set[str]:
        """Every reference the symbols placed here as *ref* carry: at this sheet's live paths,
        or every entry's when those are not known."""
        return set().union(*(s.refs for s in self.syms if ref in s.refs))

    def prepare(self, refs) -> None:
        """Parse every other sheet of the hierarchy whose bytes can hold a symbol carrying one
        of the references *refs*' symbols here carry; one pass over the bytes for all of them.
        The rest stay unparsed: no symbol on them can share those references."""
        wanted = set().union(set(), *(self.references_of(r) for r in refs)) - self._scanned
        if not wanted or self.hier.error is not None:
            return
        # A KiCad 6 root's table gives references by symbol uuid, which a sheet's own bytes
        # need not spell, so then every sheet is read.
        pattern = None if self.hier.legacy else refs_pattern(wanted)
        for sheet in self.hier.others:
            if sheet.data is None or (pattern is not None and not pattern.search(sheet.data)):
                continue
            try:
                sheet.facts = _file_facts(sheet.data)
            except Exception:  # noqa: BLE001 - any failure leaves the sheet unknown
                self.hier.error = _unreadable([sheet.name])
                return
            sheet.data = None
        self._scanned |= wanted

    def check_unique(self, ref: str) -> None:
        """Refuse [dup_ref] unless every placed symbol in the hierarchy carrying one of the
        references *ref*'s symbols here carry is a distinct unit of one part (H-C1, design
        4.6). KiCad's netlist knows a component by its reference, so same-numbered pads of
        symbols sharing one become one pad.

        At live paths each reference is checked on its own, so a reused sheet annotated R1 and
        R101 is two parts, and one whose instances both say R1 is refused.
        """
        if ref in self._unique:
            return
        self.prepare([ref])
        h = self.hier
        if h.error is not None:
            raise h.error
        mine = [self.records[id(s.node)] for s in self.syms]
        targets = [rec for rec, s in zip(mine, self.syms) if ref in s.refs]
        if h.live:
            wanted = {rec.at(p)[0] for rec in targets for p in h.live}
            placed = [(None, rec, p) for rec in mine for p in h.live]
            placed += [(o.name, rec, p) for o in h.others for rec in o.records for p in o.paths]
            for r in sorted(wanted):
                group = [(f, rec, (rec.at(p)[1],), p) for f, rec, p in placed if rec.at(p)[0] == r]
                self._refuse_unless_one_part(ref, r, group)
        else:
            extra = h.legacy.get
            refs = frozenset().union(*(rec.every_ref(extra(rec.uuid, ())) for rec in targets))
            group = [
                (f, rec, rec.every_unit(extra(rec.uuid, ())), None)
                for f, rec in [(None, rec) for rec in mine]
                + [(o.name, rec) for o in h.others for rec in o.records]
                if rec.every_ref(extra(rec.uuid, ())) & refs
            ]
            self._refuse_unless_one_part(ref, None, group)
        self._unique.add(ref)

    def _refuse_unless_one_part(self, ref: str, shared: str | None, group: list) -> None:
        """*group*: (file or None for this sheet, record, units, instance path or None)."""
        if len(group) <= 1:
            return
        why = _one_part([(rec, units) for _f, rec, units, _p in group])
        if why is None:
            return
        twice = {(f, id(rec)) for f, rec, _u, _p in group}
        many = len(twice) < len(group)

        def where(f, rec, units, p) -> str:
            shown = shared or "/".join(sorted(rec.every_ref(self.hier.legacy.get(rec.uuid, ()))))
            at = f" at {p}" if many and p else ""
            unit = ",".join(map(str, units))
            return f"{shown} ({rec.key}, unit {unit}, on {f or 'this sheet'}{at})"

        shown = "; ".join(where(*g) for g in group[:6])
        more = f" and {len(group) - 6} more" if len(group) > 6 else ""
        name = shared or ref
        raise Refusal(
            "dup_ref",
            f"reference {name} is carried by {len(group)} placed symbols in this hierarchy that"
            f" are not distinct units of one part ({shown}{more}): {why}; KiCad joins their pads"
            " by reference. Remedy: give each part its own reference (annotate_schematic numbers"
            " the ones ending in '?'), then call again; otherwise stop and report.",
        )

    def members(self, it: Item) -> list[Item]:
        """Pins on the possible component of *it*."""
        cm = self.coarse()
        r = cm.uf.find(it.id)
        return [q for q in self.items if q.kind == "pin" and cm.uf.find(q.id) == r]

    # -- resolution -----------------------------------------------------------------------
    def resolve(self, ref: str, label: str) -> list[tuple[str, list[Item]]]:
        """The pads a reference and a pin number or name mean, each with every copy this sheet
        draws (design 4.5, 4.8).

        An exact pad number wins. A name matching one pad is that pad. A name matching several
        is one target only when, on every placed symbol here, the matching pads it draws sit at
        one point and every matching pad is drawn by the same placed symbols (stacked pins);
        otherwise it is refused with the pads listed.
        """
        syms = [s for s in self.syms if ref in s.refs]
        if not syms:
            raise Refusal(
                "resolve",
                f"Component {ref} not found on this sheet. Use list_schematic_components to see"
                " what is placed.",
            )
        for s in syms:
            if s.amb:
                raise Refusal(
                    "units_disagree",
                    f"{ref}'s instance entries disagree on its unit"
                    f" ({', '.join(map(str, s.units))}): a reused sheet draws different units"
                    " per instance, so this sheet has no single pin geometry. Remedy: give every"
                    f" instance the same unit for {ref}, or wire it on a sheet used once.",
                )
        self.check_unique(ref)
        libs: list = []
        for s in syms:
            if all(s.lib is not lib for lib in libs):
                libs.append(s.lib)
        # Every pad number, and the numbers each pin name is drawn under, over every unit of every
        # definition: one number can be drawn twice under two names (KiCad's 74278, pad 6).
        numbers: set[str] = set()
        named: dict[str, set[str]] = {}
        for lib in libs:
            for sub in lib.find_all("symbol"):
                for pin in sub.find_all("pin"):
                    num = _child_text(pin, "number")
                    numbers.add(num)
                    named.setdefault(_child_text(pin, "name"), set()).add(num)
        placed = [(u, s.style) for s in syms for u in s.units]  # as KiCad draws them
        if label in numbers:
            nums = [label]
        else:
            nums = sorted(named.get(label, ()), key=_pad_order)
            if not nums:
                raise Refusal("resolve", not_drawn_message(ref, label, syms[0].lib, placed))
            if len(nums) > 1 and not self._stacked(ref, nums):
                where = "; ".join(
                    f"pad {n} at " + ", ".join(pt((c.x, c.y)) for c in self.pads.get((ref, n), []))
                    if self.pads.get((ref, n))
                    else f"pad {n} not drawn here"
                    for n in nums
                )
                raise Refusal(
                    "resolve",
                    f"pin name '{label}' on {ref} matches several pads that are not drawn at one"
                    f" point ({where}). Remedy: pass the pad number of each pin you mean.",
                )
        out = []
        for n in nums:
            copies = self.pads.get((ref, n), [])
            if not copies:
                raise Refusal("resolve", not_drawn_message(ref, n, syms[0].lib, placed))
            out.append((n, copies))
        return out

    def _stacked(self, ref: str, nums: list[str]) -> bool:
        """Every placed symbol here that draws one of these pads draws all of them, at one
        point."""
        per_sym: dict[int, dict[str, set[Point]]] = {}
        for n in nums:
            for c in self.pads.get((ref, n), []):
                per_sym.setdefault(id(c.sym), {}).setdefault(n, set()).add((c.x, c.y))
        return bool(per_sym) and all(
            set(drawn) == set(nums) and len(set().union(*drawn.values())) == 1
            for drawn in per_sym.values()
        )

    # -- net classes (H-C2 to H-C5) -------------------------------------------------------
    def _area_classes(self, r2: int) -> list[tuple[list[Point], set[str]]]:
        """[(outline, classes of the directives within sqrt(r2) of it)] per rule area."""
        flags = [it for it in self.items if it.kind == "label" and it.name is None and it.cls]
        return [
            (
                poly,
                {
                    c
                    for d in flags
                    for c in d.cls
                    if any(_within(d.x, d.y, *a, *b, r2) for a, b in _edges(poly))
                },
            )
            for poly in self.areas
        ]

    def _touches(self, it: Item, poly: list[Point]) -> bool:
        """KiCad collides a wire or bus with a rule area as a segment of its stroke width, and
        a point item by its point (sch_rule_area.cpp, 9.0.8), here widened by the margin."""
        if it.kind in _SEGS:
            return _seg_poly(it.x, it.y, it.x2, it.y2, poly, (TOL + it.w // 2) ** 2)
        return _near_poly(it.x, it.y, poly, TOL2)

    def poss_classes(self, its: list[Item]) -> set[str]:
        """H-C3: the classes these items possibly carry, from labels and touched rule areas."""
        out = {c for it in its for c in it.cls}
        for poly, cls in self._area_classes(TOL2):
            if cls and any(self._touches(it, poly) for it in its):
                out |= cls
        return out

    def _comp_poss(self, it: Item) -> set[str]:
        cm = self.coarse()
        r = cm.uf.find(it.id)
        if r not in self._poss:
            self._poss[r] = self.poss_classes([x for x in self.items if cm.uf.find(x.id) == r])
        return self._poss[r]

    def cert_classes(self, roots: set[int]) -> set[str]:
        """H-C4: the classes the narrow components with these roots certainly carry: their
        named labels' fields; directives they certainly hold (at a certain conductor, or alone
        on one reliable wire's interior); and rule areas that certainly hold both a directive
        and one of their items. A class holding a text variable is never certain."""
        self.ensure()
        if self._cert is None:
            cert: dict[int, set[str]] = {}
            for it in self.items:
                if it.kind != "label" or not it.cls:
                    continue
                if it.name is not None:
                    tgt: Item | None = it
                else:
                    at = [x for x in self._pts[(it.x, it.y)] if self._certain(x)]
                    tgt = at[0] if at else self._lone_wire(it)
                if tgt is not None:
                    cert.setdefault(self._C.find(tgt.id), set()).update(it.cls)
            for poly, cls in self._area_classes(CERT2):
                for it in self.items if cls else ():
                    if (it.kind == "pin" and it.hidden) or it.kind == "junction":
                        continue
                    if not self._certain(it):
                        continue
                    if any(_near_poly(x, y, poly, 0) for x, y in _conn_points(it)):
                        cert.setdefault(self._C.find(it.id), set()).update(cls)
            self._cert = cert
        out: set[str] = set()
        for r in roots:
            out |= self._cert.get(r, set())
        return {c for c in out if "${" not in c}

    def class_block(
        self, pins: list[Item], pts: list[Point], segs: list[tuple[Point, Point]], net: str
    ) -> str | None:
        """H-C5 for one candidate geometry: None when it passes, else why not.

        It fires when the pin's possible net or the new geometry possibly carries a class, and
        passes only when every part being joined (the pin's net and, when this sheet carries
        *net*, that net) certainly carries one class set and possibly nothing else, and the new
        geometry possibly carries nothing outside it. A class only *net*'s side carries does not
        fire it: KiCad then gives the pin that class, which is the join asked for.
        """
        if not self.areas and not any(it.cls for it in self.items):
            return None
        self.ensure()
        parts = [
            (self._comp_poss(q), self.cert_classes({self._C.find(q.id)}), _desc(q)) for q in pins
        ]
        new = [Item("wire", *a, *b) for a, b in segs] + [Item("label", *q) for q in pts]
        g = self.poss_classes(new)
        g |= {
            c
            for it in self.items
            if it.cls
            for c in it.cls
            if any(_within(it.x, it.y, n.x, n.y, n.x2, n.y2, TOL2) for n in new)
        }
        touched = set().union(g, *(ps for ps, _c, _d in parts))
        if not touched:
            return None
        named = [it for it in self.items if it.name == net]
        if named:
            parts.append(
                (
                    self._comp_poss(named[0]),
                    self.cert_classes({self._C.find(it.id) for it in named}),
                    f"net '{net}'",
                )
            )
        want = parts[0][1]
        if all(ps == want and cs == want for ps, cs, _d in parts) and g <= want:
            return None
        shown = "; ".join(
            f"{d} possibly {sorted(ps)}, certainly {sorted(cs)}" for ps, cs, d in parts
        )
        return (
            f"net classes {sorted(touched)} are touched and not certainly the same on every part"
            f" being joined ({shown}; the new geometry possibly {sorted(g)})"
        )

    # -- the touch rule -------------------------------------------------------------------
    def _point_entries(self):
        for it in self.items:
            for x, y in _conn_points(it):
                yield x, y, it

    def _lines(self, kinds=_LINES):
        return (it for it in self.items if it.kind in kinds)

    def rule1(
        self,
        P: Point,
        tgt: Item,
        at_p=("wire", "junction", "label", "sheetpin"),
        ends=("wire", "gline"),
    ) -> str | None:
        """Nothing but the target's own connections at or near its pin end P.

        Exactly at P a candidate may meet other (not no-connect) pin ends and the kinds in
        *at_p*; a line may end exactly at P only if its kind is in *ends*. Anything else within
        the margin, or any line passing through, blocks it.
        """
        for x, y, it in self._point_entries():
            dd = _d2(x, y, *P)
            if dd > TOL2 or it is tgt:
                continue
            if dd == 0 and ((it.kind == "pin" and not it.nc) or it.kind in at_p):
                continue
            where = "at" if dd == 0 else "within 0.05 mm of"
            return f"{_pdesc(x, y, it)} {where} the pin end {pt(P)}"
        for ln in self._lines(_SEGS):
            if not _within(P[0], P[1], ln.x, ln.y, ln.x2, ln.y2, TOL2):
                continue
            if ln.kind in ends and P in ((ln.x, ln.y), (ln.x2, ln.y2)):
                continue
            return f"{_desc(ln)} passes through or within 0.05 mm of the pin end {pt(P)}"
        return None

    def rule2(self, E: Point) -> str | None:
        """Nothing at or near a new endpoint that is not a pin end: no point item, and no line
        of any kind, a bus entry's body included, since the possible view joins all of them."""
        for x, y, it in self._point_entries():
            if _d2(x, y, *E) <= TOL2:
                return f"{_pdesc(x, y, it)} at or within 0.05 mm of {pt(E)}"
        for ln in self._lines(_SEGS):
            if _within(E[0], E[1], ln.x, ln.y, ln.x2, ln.y2, TOL2):
                return f"{_desc(ln)} passes at or within 0.05 mm of {pt(E)}"
        return None

    def rule3(self, A: Point, B: Point, pin_ends: set) -> str | None:
        """A new wire's interior meets nothing: no point item near it, a graphic line's ends
        included (the possible view joins them, though they are not connection points), and no
        crossing of any line.

        A collinear overlap needs no check of its own. Two overlapping segments put an end of
        one on the other: an existing line's end on the new wire is a point item caught below,
        and a new end on an existing line is caught by rules 1 and 2, which run first."""
        ends = {A, B} & pin_ends
        for x, y, it in self._point_entries():
            if (x, y) in ends:
                continue  # exact coincidence at the pin end was judged by rule 1
            if _within(x, y, A[0], A[1], B[0], B[1], TOL2):
                return (
                    f"{_pdesc(x, y, it)} lies on or within 0.05 mm of the new wire {pt(A)}-{pt(B)}"
                )
        for ln in self._lines(("gline",)):
            for x, y in ((ln.x, ln.y), (ln.x2, ln.y2)):
                if (x, y) not in ends and _within(x, y, A[0], A[1], B[0], B[1], TOL2):
                    return (
                        f"end {pt((x, y))} of {_desc(ln)} lies on or within 0.05 mm of the new"
                        f" wire {pt(A)}-{pt(B)}"
                    )
        # The crossing ban: no reader joins a plain crossing, but a later label, junction or
        # wire end put on it by another tool joins both lines (docs/adr-routing-safety.md).
        for ln in self._lines(_SEGS):
            if (
                _side(ln.x, ln.y, ln.x2, ln.y2, *A) * _side(ln.x, ln.y, ln.x2, ln.y2, *B) < 0
                and _side(*A, *B, ln.x, ln.y) * _side(*A, *B, ln.x2, ln.y2) < 0
            ):
                return f"{_desc(ln)} crosses the new wire {pt(A)}-{pt(B)}"
        return None

    def touch(
        self, pins: list[tuple[Point, Item]], free: list[Point], segments: list[tuple[Point, Point]]
    ) -> str | None:
        """None when the new geometry touches only the target pins' own points, else the
        first obstacle."""
        for P, tgt in pins:
            why = self.rule1(P, tgt)
            if why:
                return why
        for E in free:
            why = self.rule2(E)
            if why:
                return why
        pin_ends = {P for P, _ in pins}
        for A, B in segments:
            why = self.rule3(A, B, pin_ends)
            if why:
                return why
        return None


# ---------------------------------------------------------------------------------------------
# wire_pins_to_net's plan
# ---------------------------------------------------------------------------------------------

DIRECTIONS: dict[str, Point] = {"right": (1, 0), "left": (-1, 0), "up": (0, -1), "down": (0, 1)}
_DIR_NAME = {v: k for k, v in DIRECTIONS.items()}
#: Label rotation for a stub direction: right 0, up 90, left 180, down 270.
LABEL_ROT: dict[Point, int] = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}

#: KiCad's 50 mil schematic grid, in IU.
GRID = 12700
#: The longest stub accepted, in mm: a stub is a short lead off its pin, and the bound keeps
#: every coordinate written far inside what KiCad can store.
MAX_STUB_MM = 1000
#: Names KiCad gives unnamed nets. A label spelled like one collides with them.
_AUTO_NAME = re.compile(r"^(Net|unconnected)-\(")
_BUS_RANGE = re.compile(r"\[[^\]]*\.\.[^\]]*\]")


def check_args(net, direction, stub_length, param: str = "label_text") -> int:
    """Validate wire_pins_to_net's arguments before any file is read. Returns the stub in IU.
    *param* names the net argument in the messages, for callers that call it something else.

    Each rule is a measured way a bad argument wrote a wrong file (pressure-test-report.md,
    problem P12): an empty name joins every such call into one net, a name with a stray space
    or a leading "/" is a different net from the one meant, an auto-looking name collides with
    KiCad's own, bus syntax writes a bus label on a wire, and an off-grid stub ends a hair from
    a grid item. A name is judged as KiCad reads it, its escapes such as {slash} decoded.
    """

    def bad(text: str) -> Refusal:
        return Refusal("validation", text)

    if not isinstance(net, str) or not unescape(net):
        raise bad(f"{param} must be a non-empty net name.")
    shown = unescape(net)
    if shown != shown.strip():
        raise bad(
            f"{param} {net!r} has leading or trailing whitespace, which KiCad keeps as part"
            f" of the name, so it would make a net apart from {shown.strip()!r}. Pass the name"
            " without it."
        )
    if shown.startswith("/"):
        raise bad(
            f"{param} {net!r} starts with '/', which is the sheet path KiCad prints before a"
            " local net's name, not part of the name; as label text it makes a different net."
            " Pass the name without it."
        )
    if "${" in shown:
        raise bad(
            f"{param} {net!r} contains a text variable ('${{'): its value, and so the net it"
            " would join, cannot be known here. Pass the literal net name."
        )
    if _AUTO_NAME.match(shown):
        raise bad(
            f"{param} {net!r} looks like a name KiCad generates for an unnamed net, and KiCad"
            " renames or splits nets that collide with one. Choose a real net name."
        )
    if _BUS_RANGE.search(shown) or any(
        c == "{" and (i == 0 or shown[i - 1] not in "_^~") for i, c in enumerate(shown)
    ):
        raise bad(
            f"{param} {net!r} is bus syntax, and a bus label on a wire is a bus/net conflict."
            " Choose a plain net name."
        )
    if direction != "auto" and direction not in DIRECTIONS:
        raise bad(f"direction must be one of auto, left, right, up, down; got {direction!r}.")
    if isinstance(stub_length, bool) or not isinstance(stub_length, (int, float)):
        raise bad(f"stub_length must be a number of mm; got {stub_length!r}.")
    if (isinstance(stub_length, float) and not math.isfinite(stub_length)) or stub_length <= 0:
        raise bad(f"stub_length must be a length greater than 0 mm; got {stub_length!r}.")
    if stub_length > MAX_STUB_MM:
        raise bad(
            f"stub_length must be at most {MAX_STUB_MM} mm, a short lead off the pin; got"
            f" {stub_length!r}."
        )
    L = kiround(float(stub_length) * IU_PER_MM)
    if L == 0 or L % GRID:
        raise bad(
            f"stub_length {stub_length!r} mm is not a multiple of 1.27 mm, the 50 mil grid KiCad"
            " schematics use, and an off-grid stub end can land a hair from a grid item and"
            " join it. Use 1.27, 2.54, 3.81 and so on."
        )
    return L


@dataclass
class WirePlan:
    """What wire_pins_to_net will add, or why it refuses."""

    net: str
    wires: list[tuple[Point, Point]] = field(default_factory=list)
    labels: list[tuple[Point, int]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    wired: int = 0
    planned: list[int] = field(default_factory=list)  # lines describing geometry to be written

    @property
    def refused(self) -> bool:
        return bool(self.codes)

    def refuse(self, codes, text: str) -> None:
        codes = [codes] if isinstance(codes, str) else list(codes)
        self.codes += codes
        self.lines.append(f"{tags(codes)} {text}")

    def refusal(self) -> str:
        lines = [
            f"{line} (planned only; not written)" if i in self.planned else line
            for i, line in enumerate(self.lines)
        ]
        return (
            f"{tags(self.codes)} wire_pins_to_net refused the whole call; nothing was"
            " written.\n- " + "\n- ".join(lines)
        )

    def success(self) -> str:
        head = f"Wired {self.wired} pins to '{self.net}'."
        return "\n".join([head, *(f"- {line}" for line in self.lines), *self.notes])

    def no_change(self) -> str:
        head = f"No change: every pin is already on '{self.net}'."
        return "\n".join([head, *(f"- {line}" for line in self.lines)])


def _names_text(entries: list[tuple[Item, str]]) -> str:
    """Name entries [(item, text)] grouped by text, two carriers each."""
    by_text: dict[str, list[Item]] = {}
    for it, text in entries:
        by_text.setdefault(text, []).append(it)
    parts = []
    for text, its in list(by_text.items())[:4]:
        more = f" and {len(its) - 2} more" if len(its) > 2 else ""
        parts.append(f"{unescape(text)!r} via {'; '.join(_desc(it) for it in its[:2])}{more}")
    if len(by_text) > 4:
        parts.append(f"{len(by_text) - 4} more names")
    return ", ".join(parts)


def _pad_conflict(m: Model, copies: list[Item]) -> Refusal | None:
    """The refusals that come after the no-op and before names (H-C11, H-C12): a no-connect
    type pin, a no-connect flag on a copy, and anything unhandled on the possible net."""
    for c in copies:
        if c.nc:
            return Refusal(
                "nc_type",
                "it is a no-connect type pin, which KiCad never connects to anything. Remedy:"
                " pick another pin.",
            )
        for x, y, it in m._point_entries():
            if it.kind == "nc" and _d2(x, y, c.x, c.y) <= TOL2:
                return Refusal(
                    "nc_flag",
                    f"it has a no-connect flag at {pt((x, y))}. Remedy: if the pin should be"
                    " connected, remove the flag with remove_no_connect first.",
                )
    bad = m.coarse().bad_of(copies[0])
    if not bad:
        return None
    codes = [c for c in _UNHANDLED if any(code == c for code, _ in bad)]
    shown = "; ".join(
        f"{_UNHANDLED[c][0]} ({_desc(next(it for k, it in bad if k == c))}): {_UNHANDLED[c][1]}"
        for c in codes
    )
    remedy = "; ".join(_UNHANDLED[c][2] for c in codes)
    return Refusal(
        codes,
        f"its net possibly reaches {shown}. Remedy: {remedy}; otherwise stop and report."
        " add_label and add_wires check none of this, so they are not a safe way around it.",
    )


def _names_refusal(other: list[tuple[Item, str]], net: str, param: str) -> Refusal:
    remedies = []
    auto = [it for it, t in other if it.kind == "label" and _AUTO_NAME.match(unescape(t))]
    for it in auto[:1]:  # validation refuses its name, so removing it is the way through
        t = it.text or ""
        remedies.append(
            f"{unescape(t)!r} looks like the label connect_pins writes; to name this net"
            f" yourself, remove it with remove_label({t!r}, {mm(it.x)}, {mm(it.y)}) and call"
            " again"
        )
    if any(not _AUTO_NAME.match(unescape(t)) for _it, t in other):
        remedies.append(f"if the pin belongs on that net, pass that name as {param}")
    return Refusal(
        "names",
        f"its net possibly carries {_names_text(other)}; putting it on '{net}' could merge that"
        f" net with '{net}', and two named nets are never joined here. Remedy: "
        + "; ".join(remedies)
        + "; otherwise stop and report.",
    )


def _wire_copy(
    m: Model, plan: WirePlan, c: Item, net: str, fixed: Point | None, L: int, turns: bool
) -> str:
    """Geometry for one drawn pin: the outward stub, else a label on the pin end. *turns* says
    the caller can pass another direction."""
    d = fixed or c.out
    if d not in LABEL_ROT:
        raise Refusal(
            "touch",
            f"pin end {pt((c.x, c.y))} has no axis-aligned outward direction. Remedy: pass"
            " direction explicitly.",
        )
    key = netname(net)
    P = (c.x, c.y)
    E = (c.x + d[0] * L, c.y + d[1] * L)
    dname = _DIR_NAME[d]
    why_stub = m.touch([(P, c)], [E], [(P, E)])
    cls_stub = None if why_stub else m.class_block([c], [E], [(P, E)], key)
    if why_stub is None and cls_stub is None:
        wire = m.add(Item("wire", *P, *E))
        wire.new = True
        lab = m.add(Item("label", *E))
        lab.sub, lab.text, lab.name, lab.new = "label", net, key, True
        plan.wires.append((P, E))
        plan.labels.append((E, LABEL_ROT[d]))
        return f"stub {dname} {mm(L)} mm from {pt(P)} to {pt(E)}, label '{net}' at its end"
    why_label = m.rule1(P, c, ("wire",), ("wire",))
    cls_label = None if why_label else m.class_block([c], [P], [], key)
    if why_label is None and cls_label is None:
        lab = m.add(Item("label", *P))
        lab.sub, lab.text, lab.name, lab.new = "label", net, key, True
        plan.labels.append((P, LABEL_ROT[d]))
        return (
            f"label '{net}' on the pin end {pt(P)} (stub {dname} blocked: {why_stub or cls_stub})"
        )
    raise Refusal(
        ["touch" if why else "netclass" for why in (why_stub, why_label)],
        f"stub {dname} blocked: {why_stub or cls_stub}; label on the pin blocked:"
        f" {why_label or cls_label}. Remedy: move the part or the obstacle"
        + (", or pass another direction" if turns else "")
        + "; otherwise stop and report.",
    )


def plan_wire_pins(
    root,
    pins: list,
    net: str,
    direction: str,
    stub_length: float,
    path: str | None = None,
    param: str = "label_text",
    turns: bool = True,
) -> WirePlan:
    """Decide wire_pins_to_net's edit on a parsed schematic root. Writes nothing.

    *path* is the sheet's file, which places it in its hierarchy for the duplicate-reference
    check; without it only this sheet is checked. *param* names the net argument in remedies,
    and *turns* says whether the caller can pass another direction, for callers whose own
    parameters differ.

    In order: arguments; every pin resolved to a drawn pin; per pad, a no-op when the narrow
    view already puts it on *net*, else a refusal when the possible view carries another name;
    then geometry for the rest, each new item joining the model before the next pin.
    """
    plan = WirePlan(net)
    try:
        L = check_args(net, direction, stub_length, param=param)
        m = Model(root, path)
        m.check_loadable()
    except Refusal as e:
        plan.refuse(e.codes, e.text)
        return plan
    fixed = None if direction == "auto" else DIRECTIONS[direction]
    key = netname(net)  # the name KiCad reads from a label holding *net*

    targets: list[tuple[str, list[Item]]] = []
    seen: dict[tuple, str] = {}
    m.prepare(
        {
            pd["reference"]
            for pd in pins
            if isinstance(pd, dict) and isinstance(pd.get("reference"), str)
        }
    )
    for pd in pins:
        if (
            not isinstance(pd, dict)
            or not isinstance(pd.get("reference"), str)
            or isinstance(pd.get("pin"), bool)
            or not isinstance(pd.get("pin"), (str, int))
        ):
            plan.refuse("validation", f"{pd!r}: each pin must be {{'reference': str, 'pin': str}}.")
            continue
        ref, label = pd["reference"], str(pd["pin"])
        try:
            pads = m.resolve(ref, label)
        except Refusal as e:
            plan.refuse(e.codes, f"{ref}:{label}: {e.text}")
            continue
        for num, copies in pads:
            tag = f"{ref}:{label}" if len(pads) == 1 else f"{ref}:{label} (pad {num})"
            if (ref, num) in seen:
                plan.lines.append(f"{tag}: same pad as {seen[(ref, num)]}; counted once.")
                continue
            seen[(ref, num)] = tag
            targets.append((tag, copies))

    def where(copies: list[Item], c: Item) -> str:
        return f"copy at {pt((c.x, c.y))}: " if len(copies) > 1 else ""

    requested = {(copies[0].ref, copies[0].num) for _, copies in targets}
    mates: set[str] = set()
    ready: list[tuple[str, list[Item], Item | None]] = []
    for tag, copies in targets:
        mates |= {
            f"{q.ref}:{q.num}"
            for q in m.members(copies[0])
            if q.ref and not q.ref.startswith("#") and (q.ref, q.num) not in requested
        }
        on = [m.narrow_names(c).get(key) for c in copies]
        if all(on):
            plan.lines.append(
                f"{tag}: "
                + "; ".join(
                    f"{where(copies, c)}already on '{net}' via {_desc(via[0])}; unchanged"
                    for c, via in zip(copies, on)
                    if via
                )
            )
            continue
        why = _pad_conflict(m, copies)
        if why:
            plan.refuse(why.codes, f"{tag}: {why.text}")
            continue
        named = m.coarse().names_of(copies[0])  # every copy of a pad is one possible component
        other = [e for e in named if e[1] != key]
        if other:
            e = _names_refusal(other, net, param)
            plan.refuse(e.codes, f"{tag}: {e.text}")
            continue
        ready.append((tag, copies, next((it for it, t in named if t == key), None)))

    joins = [_desc(it) for it in m.items if it.name == key and not it.new]
    for tag, copies, hint in ready:
        parts = []
        drew = False
        try:
            for c in copies:
                via = m.narrow_names(c).get(key)  # an earlier copy or pad may have joined it
                if via:
                    new = ", added by this call" if via[0].new else ""
                    parts.append(f"{where(copies, c)}already on '{net}' via {_desc(via[0])}{new}")
                    continue
                parts.append(where(copies, c) + _wire_copy(m, plan, c, net, fixed, L, turns))
                drew = True
        except Refusal as e:
            plan.refuse(e.codes, f"{tag}: {where(copies, c)}{e.text}")
            continue
        text = "; ".join(parts)
        if hint is not None:
            text += (
                f" (it possibly reached '{net}' already, via {_desc(hint)}, which not every"
                " KiCad reader joins; wired explicitly)"
            )
        plan.planned.append(len(plan.lines))
        plan.lines.append(f"{tag}: {text}")
        plan.wired += drew

    if joins:
        plan.notes.append(f"'{net}' joins on this sheet: " + "; ".join(joins[:6]) + ".")
    else:
        plan.notes.append(
            f"Warning: nothing on this sheet carried '{net}', so this is a new local net here."
            f" A power symbol or global label named '{net}' on another sheet does not join it;"
            " to reach one, place a power symbol (add_power_symbol) or a global label"
            " (add_global_label) instead."
        )
    ports = [_desc(it) for it in m.items if it.kind == "sheetpin" and netname(it.text or "") == key]
    if ports:
        plan.notes.append(
            "Note: " + "; ".join(ports) + f" is a hierarchy port named '{net}' too. A label of"
            " that name does not reliably connect to it, so wire to it explicitly if that was"
            " the intent."
        )
    if mates:
        plan.notes.append(
            "Already connected to a wired pin, so now also on it: " + ", ".join(sorted(mates))
        )
    live = m.hier.live
    if len(live) > 1 and plan.wired and not plan.refused:
        refs = sorted(
            {
                (m.records[id(cs[0].sym.node)].at(p)[0], cs[0].num or "")
                for _tag, cs, _hint in ready
                if cs[0].sym is not None
                for p in live
            },
            key=lambda rn: (rn[0], _pad_order(rn[1])),
        )
        plan.notes.append(
            f"Note: this sheet is used {len(live)} times in its project, so this wiring is in"
            f" every instance: {', '.join(f'{r}:{n}' for r, n in refs)}."
        )
    return plan
