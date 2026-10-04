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
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

IU_PER_MM = 10000

#: The touch rule's margin: 0.05 mm. New geometry may come no closer than this to anything
#: other than the target pin's own connection point.
TOL = 500
TOL2 = TOL * TOL

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


def not_drawn_message(reference: str, label: str, lib_sym, targets) -> str:
    """Why no symbol placed on this sheet as *reference* draws pin *label*.

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
    placed_units = {_sym_unit_cst(t) for t in targets}
    styles_by_unit: dict[int, set[int]] = {}
    for u, s in carriers:
        styles_by_unit.setdefault(u, set()).add(s)

    def _carrier(u: int, styles: set[int]) -> str:
        style = "body style " + "/".join(map(str, sorted(styles)))
        if u == 0:
            return f"{style} (common to all units)"
        return f"unit {u} {style}" if u in placed_units else f"unit {u}"

    where = ", ".join(_carrier(u, styles) for u, styles in sorted(styles_by_unit.items()))
    placed = ", ".join(
        f"unit {_sym_unit_cst(t)}"
        + (f" body style {_sym_body_style_cst(t)}" if _sym_body_style_cst(t) != 1 else "")
        for t in targets
    )
    if any(u != 0 and u not in placed_units for u in styles_by_unit):
        return (
            f"Pin '{label}' of {reference} is on {where}, which is not placed on this sheet "
            f"({reference} here: {placed}). Place that unit, or wire the pin on the sheet "
            "that holds it."
        )
    return (
        f"Pin '{label}' of {reference} is on {where}, which this sheet does not draw "
        f"({reference} here: {placed}). Switch the placed symbol to that body style in KiCad."
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
        self.codes = (codes,) if isinstance(codes, str) else tuple(codes)
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
    KiCad cannot load.
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
_LABEL_KINDS = {
    "label": "label",
    "global_label": "global label",
    "hierarchical_label": "hierarchical label",
    "netclass_flag": "directive label",
    "directive_label": "directive label",
}


class Sym:
    __slots__ = ("node", "ref", "value", "key", "lib", "derived", "units", "style", "is_power")

    def __init__(self, node, ref: str, value: str, key: str, lib):
        self.node, self.ref, self.value, self.key, self.lib = node, ref, value, key, lib
        self.derived = lib is not None and lib.find("extends") is not None
        self.units: list[int] = []
        self.style = 1
        self.is_power = False


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
        "alt_names",
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
        # Names only the possible view counts: a pin whose placed alternate leaves it unclear
        # which name KiCad gives the net.
        self.alt_names: tuple[str, ...] = ()


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
            return f"power symbol {it.ref} (Value {it.sym.value!r}) pin at {pt((it.x, it.y))}"
        extra = [w for w, f in (("hidden", it.hidden), ("no-connect type", it.nc)) if f]
        tail = f" ({', '.join(extra)})" if extra else ""
        return f"{it.ref}:{it.num} pin end at {pt((it.x, it.y))}{tail}"
    if k == "label":
        return f"{it.sub} '{it.text}' at {pt((it.x, it.y))}"
    if k == "junction":
        return f"junction at {pt((it.x, it.y))}"
    if k == "nc":
        return f"no-connect flag at {pt((it.x, it.y))}"
    if k == "sheetpin":
        return f"sheet pin '{it.text}' of sheet '{it.sub}' at {pt((it.x, it.y))}"
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


def _all_names(it: Item):
    if it.name is not None:
        yield it.name
    yield from it.alt_names


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
    """The possible view: a union-find, and per component the names [(item, name)]."""

    __slots__ = ("uf", "names")

    def __init__(self, uf: _UF, names: dict):
        self.uf, self.names = uf, names

    def names_of(self, it: Item) -> list[tuple[Item, str]]:
        return self.names.get(self.uf.find(it.id), [])


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

    def __init__(self, root):
        self.root = root
        self.items: list[Item] = []
        self.syms: list[Sym] = []
        self.pads: dict[tuple[str, str], list[Item]] = {}
        self.libs: dict = {}
        self._dirty = True
        self._coarse: _Coarse | None = None
        libs = root.find("lib_symbols")
        for ls in libs.find_all("symbol") if libs is not None else ():
            if len(ls.atoms) > 1:
                self.libs.setdefault(ls.atoms[1].text, ls)
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
            for a, b in zip(xys, xys[1:]):
                self.add(Item(h, *a, *b))
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
            it.name = None if h in ("netclass_flag", "directive_label") else it.text
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
        ref = _property(node, "Reference") or "?"
        value = _property(node, "Value") or ""
        # KiCad resolves lib_name when present, else lib_id, by exact name, with no fallback.
        key = _child_text(node, "lib_name") or _child_text(node, "lib_id")
        s = Sym(node, ref, value, key, self.libs.get(key))
        self.syms.append(s)
        if s.lib is None or s.derived:
            return
        pos, t = symbol_transform(node, ref)
        s.units = [_sym_unit_cst(node)]
        s.style = _sym_body_style_cst(node)
        s.is_power = s.lib.find("power") is not None
        alts = {}
        for p in node.find_all("pin"):
            a = p.find("alternate")
            if a is not None and len(a.atoms) > 1 and len(p.atoms) > 1:
                alts[p.atoms[1].text] = a.atoms[1].text
        seen: set[int] = set()
        for u in s.units:
            for sub in _instance_units(s.lib, u, s.style):
                if id(sub) in seen:
                    continue
                seen.add(id(sub))
                for p in sub.find_all("pin"):
                    self._take_pin(s, p, pos, t, alts)

    def _take_pin(self, s: Sym, p, pos: Point, t: Matrix, alts: dict) -> None:
        if p.find("at") is None:
            return
        x, y, out = pin_end(pos, t, p, s.ref)
        it = self.add(Item("pin", x, y))
        it.sym, it.ref, it.out = s, s.ref, out
        it.num = _child_text(p, "number")
        self.pads.setdefault((s.ref, it.num), []).append(it)
        it.pname = _child_text(p, "name")
        primary = p.atoms[1].text if len(p.atoms) > 1 else "unspecified"
        it.etype = primary
        alt = alts.get(it.num)
        if alt:
            for a in p.find_all("alternate"):
                if len(a.atoms) > 2 and a.atoms[1].text == alt:
                    it.etype = a.atoms[2].text
                    break
        it.hidden = _pin_hidden(p)
        it.nc = it.etype == "no_connect"
        # KiCad's rule (sch_pin.cpp): a power_in pin names its net when it is hidden, by its own
        # name, or when it sits on a power symbol, by the symbol's Value. PWR_FLAG's pin is
        # power_out and a visible power_in pin on an ordinary part names nothing.
        if s.is_power:
            if it.etype == "power_in":
                it.name = s.value
            elif primary == "power_in":
                it.alt_names = (s.value,)
        elif it.hidden:
            if not alt:
                it.name = it.pname if it.etype == "power_in" else None
            else:
                # Whether KiCad names the net by the primary or the alternate name is not
                # established, so neither counts as certain and both count as possible.
                cands = []
                if it.etype == "power_in":
                    cands += [it.pname, alt]
                if primary == "power_in":
                    cands.append(it.pname)
                it.alt_names = tuple(n for n in dict.fromkeys(cands) if n is not None)

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
            return not it.nc
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
        for copies in self.pads.values():
            for it in copies[1:]:
                uf.union(copies[0].id, it.id)
        first: dict[str, int] = {}
        for it in self.items:  # same-text names join, transitively
            for name in _all_names(it):
                uf.union(first.setdefault(name, it.id), it.id)
        names: dict[int, list[tuple[Item, str]]] = {}
        for it in self.items:
            for name in _all_names(it):
                names.setdefault(uf.find(it.id), []).append((it, name))
        return _Coarse(uf, names)

    def members(self, it: Item) -> list[Item]:
        """Pins on the possible component of *it*."""
        cm = self.coarse()
        r = cm.uf.find(it.id)
        return [q for q in self.items if q.kind == "pin" and cm.uf.find(q.id) == r]

    # -- lookup ---------------------------------------------------------------------------
    def find_pin(self, ref: str, label: str) -> Item:
        """The pin a reference and a pin name or number mean: the first pin, in file order,
        whose name or number matches, on the first placed symbol that draws one."""
        syms = [s for s in self.syms if s.ref == ref]
        if not syms:
            raise Refusal(
                "resolve",
                f"Component {ref} not found on this sheet. Use list_schematic_components to see"
                " what is placed.",
            )
        for s in syms:
            if s.lib is None or s.derived:
                what = "is derived and was never flattened" if s.derived else "is not in"
                raise Refusal(
                    "derived",
                    f"{ref} uses library symbol '{s.key}', which {what} this file's lib_symbols,"
                    " so its pins are unknown here. Remedy: re-place it with place_component"
                    " from a non-derived library symbol.",
                )
            for it in self.items:
                if it.kind == "pin" and it.sym is s and (it.num == label or it.pname == label):
                    return it
        raise Refusal("resolve", not_drawn_message(ref, label, syms[0].lib, [s.node for s in syms]))

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
        for ln in self._lines():
            if not _within(P[0], P[1], ln.x, ln.y, ln.x2, ln.y2, TOL2):
                continue
            if ln.kind in ends and P in ((ln.x, ln.y), (ln.x2, ln.y2)):
                continue
            return f"{_desc(ln)} passes through or within 0.05 mm of the pin end {pt(P)}"
        return None

    def rule2(self, E: Point) -> str | None:
        """Nothing at or near a new endpoint that is not a pin end."""
        for x, y, it in self._point_entries():
            if _d2(x, y, *E) <= TOL2:
                return f"{_pdesc(x, y, it)} at or within 0.05 mm of {pt(E)}"
        for ln in self._lines():
            if _within(E[0], E[1], ln.x, ln.y, ln.x2, ln.y2, TOL2):
                return f"{_desc(ln)} passes at or within 0.05 mm of {pt(E)}"
        return None

    def rule3(self, A: Point, B: Point, pin_ends: set) -> str | None:
        """A new wire's interior meets nothing: no point item near it, no collinear overlap
        with a wire or bus, and no crossing of any line."""
        ends = {A, B} & pin_ends
        for x, y, it in self._point_entries():
            if (x, y) in ends:
                continue  # exact coincidence at the pin end was judged by rule 1
            if _within(x, y, A[0], A[1], B[0], B[1], TOL2):
                return (
                    f"{_pdesc(x, y, it)} lies on or within 0.05 mm of the new wire {pt(A)}-{pt(B)}"
                )
        for ln in self._lines(("wire", "bus")):
            if _overlap_axis(A, B, ln):
                return f"{_desc(ln)} overlaps the new wire {pt(A)}-{pt(B)} collinearly"
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


def _overlap_axis(A: Point, B: Point, ln: Item) -> bool:
    """An axis-aligned new segment A-B overlapping *ln* collinearly over a positive length
    (within the margin across)."""
    if A[1] == B[1]:
        if ln.y != ln.y2 or abs(ln.y - A[1]) > TOL:
            return False
        lo, hi = sorted((A[0], B[0]))
        llo, lhi = sorted((ln.x, ln.x2))
    elif A[0] == B[0]:
        if ln.x != ln.x2 or abs(ln.x - A[0]) > TOL:
            return False
        lo, hi = sorted((A[1], B[1]))
        llo, lhi = sorted((ln.y, ln.y2))
    else:
        return False
    return min(hi, lhi) - max(lo, llo) > 0


# ---------------------------------------------------------------------------------------------
# wire_pins_to_net's plan
# ---------------------------------------------------------------------------------------------

DIRECTIONS: dict[str, Point] = {"right": (1, 0), "left": (-1, 0), "up": (0, -1), "down": (0, 1)}
_DIR_NAME = {v: k for k, v in DIRECTIONS.items()}
#: Label rotation for a stub direction: right 0, up 90, left 180, down 270.
LABEL_ROT: dict[Point, int] = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}

#: KiCad's 50 mil schematic grid, in IU.
GRID = 12700
#: Names KiCad gives unnamed nets. A label spelled like one collides with them.
_AUTO_NAME = re.compile(r"^(Net|unconnected)-\(")
_BUS_RANGE = re.compile(r"\[[^\]]*\.\.[^\]]*\]")


def check_args(net, direction, stub_length) -> int:
    """Validate wire_pins_to_net's arguments before any file is read. Returns the stub in IU.

    Each rule is a measured way a bad argument wrote a wrong file (pressure-test-report.md,
    problem P12): an empty name joins every such call into one net, a name with a stray space
    or a leading "/" is a different net from the one meant, an auto-looking name collides with
    KiCad's own, bus syntax writes a bus label on a wire, and an off-grid stub ends a hair from
    a grid item.
    """

    def bad(text: str) -> Refusal:
        return Refusal("validation", text)

    if not isinstance(net, str) or not net:
        raise bad("label_text must be a non-empty net name.")
    if net != net.strip():
        raise bad(
            f"label_text {net!r} has leading or trailing whitespace, which KiCad keeps as part"
            f" of the name, so it would make a net apart from {net.strip()!r}. Pass the name"
            " without it."
        )
    if net.startswith("/"):
        raise bad(
            f"label_text {net!r} starts with '/', which is the sheet path KiCad prints before a"
            " local net's name, not part of the name; as label text it makes a different net."
            " Pass the name without it."
        )
    if "${" in net:
        raise bad(
            f"label_text {net!r} contains a text variable ('${{'): its value, and so the net it"
            " would join, cannot be known here. Pass the literal net name."
        )
    if _AUTO_NAME.match(net):
        raise bad(
            f"label_text {net!r} looks like a name KiCad generates for an unnamed net, and KiCad"
            " renames or splits nets that collide with one. Choose a real net name."
        )
    if _BUS_RANGE.search(net) or any(
        c == "{" and (i == 0 or net[i - 1] not in "_^~") for i, c in enumerate(net)
    ):
        raise bad(
            f"label_text {net!r} is bus syntax, and a bus label on a wire is a bus/net conflict."
            " Choose a plain net name."
        )
    if direction != "auto" and direction not in DIRECTIONS:
        raise bad(f"direction must be one of auto, left, right, up, down; got {direction!r}.")
    if isinstance(stub_length, bool) or not isinstance(stub_length, (int, float)):
        raise bad(f"stub_length must be a number of mm; got {stub_length!r}.")
    if not math.isfinite(stub_length) or stub_length <= 0:
        raise bad(f"stub_length must be a length greater than 0 mm; got {stub_length!r}.")
    L = kiround(float(stub_length) * IU_PER_MM)
    if L % GRID:
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

    @property
    def refused(self) -> bool:
        return bool(self.codes)

    def refuse(self, codes, text: str) -> None:
        codes = [codes] if isinstance(codes, str) else list(codes)
        self.codes += codes
        self.lines.append(f"{tags(codes)} {text}")

    def refusal(self) -> str:
        return (
            f"{tags(self.codes)} wire_pins_to_net refused the whole call; nothing was"
            " written.\n- " + "\n- ".join(self.lines)
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
        parts.append(f"{text!r} via {'; '.join(_desc(it) for it in its[:2])}{more}")
    if len(by_text) > 4:
        parts.append(f"{len(by_text) - 4} more names")
    return ", ".join(parts)


def _names_refusal(other: list[tuple[Item, str]], net: str) -> Refusal:
    text = (
        f"its net possibly carries {_names_text(other)}; putting it on '{net}' could merge that"
        f" net with '{net}', and two named nets are never joined here. Remedy: if the pin belongs"
        " on that net, pass that name as label_text; otherwise stop and report."
    )
    auto = [(it, t) for it, t in other if it.kind == "label" and _AUTO_NAME.match(t)]
    for it, t in auto[:1]:
        text += (
            f" {t!r} looks like the label connect_pins writes; to name this net yourself, remove"
            f" it with remove_label({t!r}, {mm(it.x)}, {mm(it.y)}) and call again."
        )
    return Refusal("names", text)


def _wire_copy(m: Model, plan: WirePlan, c: Item, net: str, fixed: Point | None, L: int) -> str:
    """Geometry for one drawn pin: the outward stub, else a label on the pin end."""
    d = fixed or c.out
    if d not in LABEL_ROT:
        raise Refusal(
            "touch",
            f"pin end {pt((c.x, c.y))} has no axis-aligned outward direction. Remedy: pass"
            " direction explicitly.",
        )
    P = (c.x, c.y)
    E = (c.x + d[0] * L, c.y + d[1] * L)
    dname = _DIR_NAME[d]
    why_stub = m.touch([(P, c)], [E], [(P, E)])
    if why_stub is None:
        wire = m.add(Item("wire", *P, *E))
        wire.new = True
        lab = m.add(Item("label", *E))
        lab.sub, lab.text, lab.name, lab.new = "label", net, net, True
        plan.wires.append((P, E))
        plan.labels.append((E, LABEL_ROT[d]))
        return f"stub {dname} {mm(L)} mm from {pt(P)} to {pt(E)}, label '{net}' at its end"
    why_label = m.rule1(P, c, ("wire",), ("wire",))
    if why_label is None:
        lab = m.add(Item("label", *P))
        lab.sub, lab.text, lab.name, lab.new = "label", net, net, True
        plan.labels.append((P, LABEL_ROT[d]))
        return f"label '{net}' on the pin end {pt(P)} (stub {dname} blocked: {why_stub})"
    raise Refusal(
        "touch",
        f"stub {dname} blocked: {why_stub}; label on the pin blocked: {why_label}. Remedy:"
        " move the part or the obstacle, or pass another direction; otherwise stop and report.",
    )


def plan_wire_pins(root, pins: list, net: str, direction: str, stub_length: float) -> WirePlan:
    """Decide wire_pins_to_net's edit on a parsed schematic root. Writes nothing.

    In order: arguments; every pin resolved to a drawn pin; per pad, a no-op when the narrow
    view already puts it on *net*, else a refusal when the possible view carries another name;
    then geometry for the rest, each new item joining the model before the next pin.
    """
    plan = WirePlan(net)
    try:
        L = check_args(net, direction, stub_length)
        m = Model(root)
    except Refusal as e:
        plan.refuse(e.codes, e.text)
        return plan
    fixed = None if direction == "auto" else DIRECTIONS[direction]

    targets: list[tuple[str, Item]] = []
    seen: dict[tuple, str] = {}
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
        tag = f"{ref}:{label}"
        try:
            c = m.find_pin(ref, label)
        except Refusal as e:
            plan.refuse(e.codes, f"{tag}: {e.text}")
            continue
        key = (c.ref, c.num)
        if key in seen:
            plan.lines.append(f"{tag}: same pad as {seen[key]}; counted once.")
            continue
        seen[key] = tag
        targets.append((tag, c))

    requested = {(c.ref, c.num) for _, c in targets}
    mates: set[str] = set()
    ready: list[tuple[str, Item, Item | None]] = []
    for tag, c in targets:
        mates |= {
            f"{q.ref}:{q.num}"
            for q in m.members(c)
            if q.ref and not q.ref.startswith("#") and (q.ref, q.num) not in requested
        }
        on = m.narrow_names(c).get(net)
        if on:
            plan.lines.append(f"{tag}: already on '{net}' via {_desc(on[0])}; unchanged")
            continue
        named = m.coarse().names_of(c)
        other = [e for e in named if e[1] != net]
        if other:
            e = _names_refusal(other, net)
            plan.refuse(e.codes, f"{tag}: {e.text}")
            continue
        hint = next((it for it, t in named if t == net), None)
        ready.append((tag, c, hint))

    joins = [_desc(it) for it in m.items if it.name == net and not it.new]
    for tag, c, hint in ready:
        try:
            text = _wire_copy(m, plan, c, net, fixed, L)
        except Refusal as e:
            plan.refuse(e.codes, f"{tag}: {e.text}")
            continue
        if hint is not None:
            text += (
                f" (it possibly reached '{net}' already, via {_desc(hint)}, which not every"
                " KiCad reader joins; wired explicitly)"
            )
        plan.lines.append(f"{tag}: {text}")
        plan.wired += 1

    if joins:
        plan.notes.append(f"'{net}' joins on this sheet: " + "; ".join(joins[:6]) + ".")
    else:
        plan.notes.append(
            f"Warning: nothing on this sheet carried '{net}', so this is a new local net here."
            f" A power symbol or global label named '{net}' on another sheet does not join it;"
            " to reach one, place a power symbol (add_power_symbol) or a global label"
            " (add_global_label) instead."
        )
    ports = [_desc(it) for it in m.items if it.kind == "sheetpin" and it.text == net]
    if ports:
        plan.notes.append(
            "Note: " + "; ".join(ports) + " is a hierarchy port, not a name: KiCad joins it to a"
            f" label '{net}' only when something on this sheet already carries '{net}', so do"
            " not rely on the name to reach it; wire to it explicitly if that was the intent."
        )
    if mates:
        plan.notes.append(
            "Already connected to a wired pin, so now also on it: " + ", ".join(sorted(mates))
        )
    return plan
