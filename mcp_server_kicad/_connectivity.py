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


# ---------------------------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------------------------


class Model:
    """One sheet's connectable items, with pins placed where KiCad draws them."""

    def __init__(self, root):
        self.root = root
        self.items: list[Item] = []
        self.syms: list[Sym] = []
        self.libs: dict = {}
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
        it.pname = _child_text(p, "name")
        it.etype = p.atoms[1].text if len(p.atoms) > 1 else "unspecified"
        alt = alts.get(it.num)
        if alt:
            for a in p.find_all("alternate"):
                if len(a.atoms) > 2 and a.atoms[1].text == alt:
                    it.etype = a.atoms[2].text
                    break
        it.hidden = _pin_hidden(p)
        it.nc = it.etype == "no_connect"

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


@dataclass
class WirePlan:
    """What wire_pins_to_net will add, or why it refuses."""

    net: str
    wires: list[tuple[Point, Point]] = field(default_factory=list)
    labels: list[tuple[Point, int]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
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
        return f"Wired {self.wired} pins to '{self.net}'.\n- " + "\n- ".join(self.lines)


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
    """Decide wire_pins_to_net's edit on a parsed schematic root. Writes nothing."""
    plan = WirePlan(net)
    try:
        m = Model(root)
    except Refusal as e:
        plan.refuse(e.codes, e.text)
        return plan
    fixed = None if direction == "auto" else DIRECTIONS[direction]
    L = kiround(float(stub_length) * IU_PER_MM)
    for pd in pins:
        ref, label = pd["reference"], str(pd["pin"])
        tag = f"{ref}:{label}"
        try:
            c = m.find_pin(ref, label)
            text = _wire_copy(m, plan, c, net, fixed, L)
        except Refusal as e:
            plan.refuse(e.codes, f"{tag}: {e.text}")
            continue
        plan.lines.append(f"{tag}: {text}")
        plan.wired += 1
    return plan
