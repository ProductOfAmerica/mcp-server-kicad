"""Design E for the routing pressure test: write only into empty space.

Scratch prototype, not repo code. It imports the repo (PT_REPO, default the harness's checkout)
read-only and copies the helpers it needs from coarse_impl.py (design K); nothing is imported from
coarse_impl or m2_impl. Every refusal raises mcp ToolError before any write, so a refused call
leaves every file byte-identical; a successful call writes each file it changes exactly once
through the repo's _atomic_write. No junction is ever written.

E has NO connectivity model of existing wiring. It only asks whether a place is empty: an item is
within r = 500 IU (0.05 mm) of a point or segment when their closed-segment distance is at most r
(point items are zero-length segments). Items: wire and bus segments, bus entries, junctions,
labels of every kind (directive labels included), no-connect flags, sheet pins, graphic polyline
segments, and every pin end of every placed symbol (hidden and no_connect type included).

DECISIONS
  wire_pins_to_net, per requested pad (every copy of (reference, number) on this sheet):
    refuse when the pad has a copy on another sheet, its symbol is duplicated (except distinct
    units of one library symbol), its instances disagree on the unit, or it is a jumper symbol;
    per copy: refuse a no_connect type pin, an implicit name other than N, a rule area at the
    pin end, a no-connect flag within r; this tool's own shape for N is a no-op; anything else
    within r is "already connected"; a free copy gets an outward stub with a local label N at
    its end, else the label on the pin end itself, else it is refused.
  connect_pins: both pins single-copy, free, not both implicitly named; straight route when
    aligned, else L horizontal-first, then vertical-first, through a clear corridor; no label.
  auto_place_decoupling_cap: place in memory, both new pin ends must land free, then E's
    wire_pins_to_net rules for pin 1 (power_net) and pin 2 (ground_net); one write per file.
  A derived or unresolved library symbol anywhere on the sheet refuses the whole call.

REASON CODES. A refusal message starts with the bracketed code of every distinct reason in it, in
order of first occurrence; each per-pad line of wire_pins_to_net repeats its own code.
  [E:validation]  an argument is invalid, the file cannot be read, or the repo rejects a
                  place_component input (also the default for any uncoded ToolError)
  [E:unloadable]  a placed symbol or library pin at an angle KiCad cannot load
  [E:derived]     a derived or unresolved library symbol on the sheet
  [E:resolve]     reference or pin not found, a pin name matching several pads, no copy drawn here
  [E:units]       the target symbol's instance entries disagree on its unit
  [E:dup_ref]     the reference is duplicated on this sheet (not distinct units of one symbol)
  [E:jumper]      the target symbol has KiCad 10 jumper pins
  [E:cross_sheet] a copy of the pad on another sheet
  [E:sheets]      another sheet of the project could not be read for that check
  [E:nc_type]     the pin's electrical type is no_connect
  [E:implicit]    the pin names its own net, not N (connect_pins: both pins are named)
  [E:rule_area]   the pin end is inside, or within r of, a rule area
  [E:nc_flag]     a no-connect flag within r of the pin end
  [E:connected]   anything else within r of the pin end that is not this tool's own shape
  [E:blocked]     no clear stub and no clear pin end for a label (connect_pins: no clear route)
  [E:copies]      connect_pins on a pad with several drawn copies
  [E:landing]     auto_place_decoupling_cap: a new pin end lands within r of an item

Exported API (same shapes as the repo tools):
    wire_pins_to_net(pins, label_text, direction="auto", stub_length=2.54, auto_pwr_flag=True,
                     schematic_path=SCH_PATH)
    connect_pins(ref1, pin1, ref2, pin2, schematic_path=SCH_PATH)
    auto_place_decoupling_cap(lib_id, reference, value, x, y, power_net, ground_net, rotation=0,
                              symbol_lib_path="", schematic_path=SCH_PATH, project_path="")
Probe helper: pin_positions(schematic_path, reference) -> {number: [(x_mm, y_mm, outward)]}.

CHOICES THE SPEC LEFT OPEN (the more conservative option unless the entry says otherwise)
- E-C1 Distance is exact integer arithmetic on closed segments, inclusive (<= r). Two segments
  that cross are at distance 0. A wire or polyline is one segment per consecutive point pair; a
  one-point one is a point.
- E-C2 A bus entry is an item along its whole body, not only at its two ends (the spec lists the
  ends). Polylines of any point count are items on every segment (K kept 2-point ones only).
- E-C3 Not items: text, text boxes, rectangles, circles, arcs, beziers, images, tables, sheet
  bodies (only their pins) and symbol bodies (only pin ends): KiCad connects none of them
  (inference from KiCad's connectable item types, not measured here).
- E-C4 Pin ends: every pin of every unit any instance entry lists (a reused sheet with disagreeing
  units contributes every listed unit), every body style rule as K: KiCad's own transform,
  lib_name else lib_id resolution without fallback, alternates set the electrical type.
- E-C5 Rule areas: each child of (rule_area ...) holding (pts ...) is a closed polygon. A point or
  segment is "in" an area when an end lies inside it (even-odd ray cast) or it comes within r of
  any edge, the closing edge included. A rule_area with no point list is ignored (KiCad 9 writes
  rule areas as polylines; other shapes not measured).
- E-C6 Freedom excludes the other pins of the same pad wherever they are drawn. The stub excludes
  only the target pin and same-pad pins exactly at its pin end, so a copy within r but off the
  pin end blocks the stub and the label goes on the pin end.
- E-C7 Stub lengths: only 1x is tried. The spec's 2x and 3x stubs can never succeed where 1x
  failed: the 1x segment is part of the longer one, so any item within r of it is within r of
  them, and the same holds for rule areas. Trying them would be dead code.
- E-C8 Own shapes, exact. A: the only item within r of the pin end (same-pad pins aside) is a
  local label (not global, hierarchical or directive) whose raw text is exactly N, exactly on
  the pin end. B: the only such item is a wire with one end exactly on the pin end and a distinct
  other end E, and the only item within r of that wire (itself and same-pad pins aside) is a
  local label exactly N exactly at E. Any wire direction and length qualifies, not only the
  call's own stub. Everything else within r refuses [E:connected], including a second label,
  a junction, or a label N that is global or hierarchical.
- E-C9 Per copy the order is nc_type, implicit, rule_area, nc_flag, then own shape or
  connected: a rule area or a no-connect flag refuses even beside this tool's own shape (the tool
  never writes its shape there).
- E-C10 An implicit name equal to N does not make the pin a no-op: it is wired like any free
  pin. The self-test measured both such writes (a hidden power_in pin 'VCC', a +5V power
  symbol) as redundant but clean: a local label N already reached the pin's implicit net N in
  both readings. A no-op here would rest on that two-fixture measurement, so the redundant
  label stays. A pin name '' or '~' is a name like any other, so such a hidden power_in pin
  refuses for every N.
- E-C11 References: a placed symbol answers to its Reference property and to every per-instance
  (reference ...) of its instances block. Duplicates: every placed symbol on the sheet answering
  to the requested reference must resolve to one library symbol with pairwise disjoint unit sets,
  else [E:dup_ref]. [E:units] when any of them has more than one listed unit.
- E-C12 Other sheets: seeds are every .kicad_sch in the edited file's directory plus the
  .kicad_sch beside each .kicad_pro in the nearest directory upward (the file's own first) that
  holds one; then every file reachable from a seed through (sheet ...) Sheetfile entries, each
  tried relative to the referencing file and to that project directory. Missing files are
  skipped; an unparseable one refuses [E:sheets]; the edited file itself is never "another
  sheet" (another instance of a reused sheet is the same file). A copy exists elsewhere when a
  placed symbol there answers to any of the target symbols' references and draws a pin with that
  number for any unit it lists, or cannot be resolved there. An unrelated .kicad_pro found
  upward only adds refusals.
- E-C13 Jumper symbols (jumper_pin_groups with a group, or duplicate_pin_numbers_are_jumpers
  yes): a target on one refuses [E:jumper]; its internal joins are connectivity E does not model.
  Not in the spec.
- E-C14 Copies of one pad drawn at exactly the same point are one connection point, wired once.
- E-C15 Multi-pad calls: every pad is classified against the file as read; then stubs and pin
  labels are laid pad by pad, copy by copy, each checked against the file plus everything this
  call has added. All refusals of both phases are reported together; any refusal writes nothing.
- E-C16 connect_pins: [E:copies] for a multi-copy pad, [E:cross_sheet] for a copy elsewhere,
  [E:implicit] when BOTH pins carry implicit names (equal or not); one implicit name is allowed,
  since the other pin is free and so only brings its own single-pin net. Both pins must be free;
  there is no no-op shape, so a repeat call refuses [E:connected]. The same pad on both sides
  is "No change". Each route segment must clear every item by r except the two target pins
  (anywhere along the route) and stay out of rule areas.
- E-C17 auto_place_decoupling_cap: K's placement (validation, library copy-in, derived refusal,
  instances, root symbol_instances entry), then [E:landing] unless each new pin end is clear of
  every item but the new symbol's same-pad pins and out of rule areas, then pin 1 and pin 2 in
  one plan (pin 2 sees pin 1's stub and label). power_net == ground_net is not refused (K's
  choice). auto_pwr_flag is accepted everywhere and ignored: E never places a PWR_FLAG.
- E-C18 Label rotation follows the stub direction (right 0, up 90, left 180, down 270); a label
  on the pin end takes the rotation of the stub it replaces. Page bounds are not checked (as K).
"""

import functools
import math
import os
import re
import sys
from pathlib import Path

import hpaths  # noqa: E402,F401  (repo under test on sys.path)

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

import mcp_server_kicad._cst as _cst  # noqa: E402
from mcp_server_kicad import _shared  # noqa: E402
from mcp_server_kicad import schematic as _sch  # noqa: E402

SCH_PATH = _sch.SCH_PATH
R = 500  # the margin r, IU (0.05 mm)
R2 = R * R

_ROT = {0: (1, 0, 0, 1), 90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}
_MIR = {"x": (1, 0, 0, -1), "y": (-1, 0, 0, 1)}
_TOWARD = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}  # sch_pin.cpp:608-615
_DIRS = {"right": (1, 0), "left": (-1, 0), "up": (0, -1), "down": (0, 1)}
_DIR_NAME = {v: k for k, v in _DIRS.items()}
_LABEL_ROT = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}
_LABELS = ("label", "global_label", "hierarchical_label", "netclass_flag", "directive_label")
_KIND = {"wire": "wire", "bus": "bus", "polyline": "graphic line", "bus_entry": "bus entry",
         "junction": "junction", "no_connect": "no-connect flag"}
_AUTO_NAME = re.compile(r"^(Net|unconnected)-\(")
_BUS_RANGE = re.compile(r"\[[^\]]*\.\.[^\]]*\]")
_CHEAD = "connect_pins refused; nothing was written."


class _Refuse(Exception):
    """A per-pad refusal with its reason code; collected and reported, never escapes."""

    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


def _codes(codes) -> str:
    return " ".join(f"[E:{c}]" for c in dict.fromkeys(codes))


def _err(code, text) -> ToolError:
    return ToolError(f"{_codes([code])} {text}")


def _coded(fn):
    """A ToolError from a repo helper the tool calls gets the [validation] code."""

    @functools.wraps(fn)
    def run(*args, **kw):
        try:
            return fn(*args, **kw)
        except ToolError as e:
            if str(e).startswith("[E:"):
                raise
            raise _err("validation", str(e)) from None

    return run


# ------------------------------------------------------------------------- numbers, geometry


def _kiround(v: float) -> int:
    return int(v + 0.5) if v >= 0 else int(v - 0.5)


def _iu(text) -> int:
    return _kiround(float(text) * 10000.0)


def _mm(i: int) -> str:
    a = abs(i)
    s = f"{a // 10000}.{a % 10000:04d}".rstrip("0").rstrip(".")
    return "-" + s if i < 0 else s


def _pt(x: int, y: int) -> str:
    return f"({_mm(x)}, {_mm(y)})"


def _within(p, a, b) -> bool:
    """Squared distance from point p to closed segment ab <= R2, exact in integers."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ex, ey = p[0] - a[0], p[1] - a[1]
    ln2, t = dx * dx + dy * dy, ex * dx + ey * dy
    if ln2 == 0 or t <= 0:
        return ex * ex + ey * ey <= R2
    if t >= ln2:
        return (p[0] - b[0]) ** 2 + (p[1] - b[1]) ** 2 <= R2
    cross = ex * dy - ey * dx
    return cross * cross <= R2 * ln2


def _side(a, b, p) -> int:
    v = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    return (v > 0) - (v < 0)


def _near(s, t) -> bool:
    """Closed segments s and t are within R of each other (E-C1)."""
    (a, b), (c, d) = s, t
    if _side(c, d, a) * _side(c, d, b) < 0 and _side(a, b, c) * _side(a, b, d) < 0:
        return True
    return _within(a, c, d) or _within(b, c, d) or _within(c, a, b) or _within(d, a, b)


def _inside(p, poly) -> bool:
    ins = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < x1 + (p[1] - y1) * (x2 - x1) / (y2 - y1):
            ins = not ins
    return ins


# --------------------------------------------------------------------------------- transform


def _compose(old, temp):
    """new = old * temp, exactly as SCH_SYMBOL::SetOrientation (9.0 sch_symbol.cpp:1906-1913)."""
    ox1, oy1, ox2, oy2 = old
    tx1, ty1, tx2, ty2 = temp
    return (ox1 * tx1 + ox2 * ty1, oy1 * tx1 + oy2 * ty1, ox1 * tx2 + ox2 * ty2,
            oy1 * tx2 + oy2 * ty2)


def _apply(t, x, y):
    """TRANSFORM::TransformCoordinate (9.0 transform.cpp:44-47)."""
    return t[0] * x + t[1] * y, t[2] * x + t[3] * y


def _sym_transform(sym, ref):
    t, pos = _ROT[0], (0, 0)
    for ch in sym.lists:
        if ch.head == "at":
            a = ch.atoms
            pos = (_iu(a[1].text), _iu(a[2].text))
            ang = int(float(a[3].text)) if len(a) > 3 else 0
            if ang not in _ROT:
                raise _err("unloadable", f"{ref} is placed at angle {a[3].text}, which KiCad"
                           " cannot load (0, 90, 180 or 270 only). Nothing was written.")
            t = _ROT[ang]
        elif ch.head == "mirror" and len(ch.atoms) > 1 and ch.atoms[1].text in _MIR:
            t = _compose(t, _MIR[ch.atoms[1].text])
    return pos, t


def _child_text(node, key, default=""):
    c = node.find(key)
    return c.atoms[1].text if c is not None and len(c.atoms) > 1 else default


def _instance_paths(sym):
    inst = sym.find("instances")
    return [p for proj in inst.find_all("project") for p in proj.find_all("path")] \
        if inst is not None else []


def _units(sym) -> list[int]:
    units: set[int] = set()
    for path in _instance_paths(sym):
        try:
            units.add(int(_child_text(path, "unit")))
        except ValueError:
            pass
    return sorted(units or {_sch._sym_unit_cst(sym)})


def _pin_hidden(p) -> bool:
    if any(a.text == "hide" for a in p.atoms[3:]):
        return True
    h = p.find("hide")
    return h is not None and (len(h.atoms) < 2 or h.atoms[1].text == "yes")


# ------------------------------------------------------------------------------------- items


class _It:
    """One item: a segment a-b (a point when a == b) plus kind-specific attributes."""

    def __init__(self, kind, a, b=None, **kw):
        self.kind, self.a, self.b = kind, a, b or a
        self.box = (min(a[0], self.b[0]) - R, max(a[0], self.b[0]) + R,
                    min(a[1], self.b[1]) - R, max(a[1], self.b[1]) + R)
        self.__dict__.update(kw)


class _Sym:
    def __init__(self, node, ref, refs, value, key, lib):
        self.node, self.ref, self.refs, self.value, self.key, self.lib = \
            node, ref, refs, value, key, lib
        self.derived = lib is not None and lib.find("extends") is not None
        self.units, self.pins, self.is_power, self.jumper = [], [], False, False


def _desc(it) -> str:
    k = it.kind
    if k == "pin":
        extra = [w for w, f in (("hidden", it.hidden), ("power symbol", it.sym.is_power),
                                ("no-connect type", it.etype == "no_connect")) if f]
        return f"{it.ref}:{it.num} pin end at {_pt(*it.a)}" + (f" ({', '.join(extra)})" if extra
                                                               else "")
    if k == "label":
        return f"{it.sub} '{it.text}' at {_pt(*it.a)}"
    if k == "sheetpin":
        return f"sheet pin '{it.text}' at {_pt(*it.a)}"
    new = "new " if getattr(it, "new", False) else ""
    if it.a == it.b:
        return f"{new}{_KIND[k]} at {_pt(*it.a)}"
    return f"{new}{_KIND[k]} {_pt(*it.a)}-{_pt(*it.b)}"


def _implicit(c):
    """The name the pin itself gives its net (KiCad: power_in, hidden or on a power symbol)."""
    if c.etype == "power_in" and (c.hidden or c.sym.is_power):
        return c.sym.value if c.sym.is_power else c.pname
    return None


class _Model:
    """Per-sheet item list and rule areas; no connectivity."""

    def __init__(self, root):
        self.items: list[_It] = []
        self.syms: list[_Sym] = []
        self.areas: list[tuple[list, list]] = []
        self.libs: dict = {}
        libs = root.find("lib_symbols")
        for ls in libs.find_all("symbol") if libs is not None else []:
            if len(ls.atoms) > 1:
                self.libs.setdefault(ls.atoms[1].text, ls)
        for ch in root.lists:
            self._take(ch)

    def add(self, kind, a, b=None, **kw) -> _It:
        it = _It(kind, a, b, **kw)
        self.items.append(it)
        return it

    def _take(self, ch):
        h = ch.head
        if h in ("wire", "bus", "polyline"):
            pts = ch.find("pts")
            xys = [(_iu(p.atoms[1].text), _iu(p.atoms[2].text)) for p in pts.find_all("xy")] \
                if pts is not None else []
            for a, b in list(zip(xys, xys[1:])) or [(p, p) for p in xys]:
                self.add(h, a, b)
        elif h in ("bus_entry", "junction", "no_connect") or h in _LABELS:
            at = ch.find("at")
            a = (_iu(at.atoms[1].text), _iu(at.atoms[2].text))
            if h == "bus_entry":
                size = ch.find("size")
                d = (_iu(size.atoms[1].text), _iu(size.atoms[2].text)) if size is not None \
                    else (0, 0)
                self.add(h, a, (a[0] + d[0], a[1] + d[1]))
            elif h in _LABELS:
                self.add("label", a, sub=h, text=ch.atoms[1].text if len(ch.atoms) > 1 else "")
            else:
                self.add(h, a)
        elif h == "sheet":
            for p in ch.find_all("pin"):
                at = p.find("at")
                if at is not None:
                    self.add("sheetpin", (_iu(at.atoms[1].text), _iu(at.atoms[2].text)),
                             text=p.atoms[1].text if len(p.atoms) > 1 else "")
        elif h == "rule_area":  # E-C5
            for shape in ch.lists:
                pts = shape.find("pts")
                if pts is not None:
                    poly = [(_iu(p.atoms[1].text), _iu(p.atoms[2].text))
                            for p in pts.find_all("xy")]
                    self.areas.append((poly, list(zip(poly, poly[1:] + poly[:1]))))
        elif h == "symbol":
            self._take_symbol(ch)

    def _take_symbol(self, node):
        ref = _shared._sym_property_cst(node, "Reference") or "?"
        refs = {ref} | {_child_text(p, "reference") for p in _instance_paths(node)} - {""}
        key = _child_text(node, "lib_name") or _child_text(node, "lib_id")  # no fallback
        ls = self.libs.get(key)
        s = _Sym(node, ref, refs, _shared._sym_property_cst(node, "Value") or "", key, ls)
        self.syms.append(s)
        if ls is None or s.derived:
            return
        pos, t = _sym_transform(node, ref)
        s.units = _units(node)
        s.is_power = ls.find("power") is not None
        groups = ls.find("jumper_pin_groups")
        s.jumper = bool(groups is not None and groups.lists) or \
            _child_text(ls, "duplicate_pin_numbers_are_jumpers") == "yes"
        alts = {p.atoms[1].text: _child_text(p, "alternate") for p in node.find_all("pin")
                if len(p.atoms) > 1 and p.find("alternate") is not None}
        style, seen = _sch._sym_body_style_cst(node), set()
        for u in s.units:
            for sub in _sch._instance_units(ls, u, style):
                if id(sub) not in seen:
                    seen.add(id(sub))
                    for p in sub.find_all("pin"):
                        self._take_pin(s, p, pos, t, alts)

    def _take_pin(self, s, p, pos, t, alts):
        at = p.find("at")
        if at is None:
            return
        ang = int(float(at.atoms[3].text)) if len(at.atoms) > 3 else 0
        if ang not in _TOWARD:
            raise _err("unloadable", f"{s.ref} has a pin at angle {ang}, which KiCad cannot load."
                       " Nothing was written. Remedy: fix that library pin's angle.")
        dx, dy = _apply(t, _iu(at.atoms[1].text), -_iu(at.atoms[2].text))  # lib Y inverted
        out = _apply(t, -_TOWARD[ang][0], -_TOWARD[ang][1])
        etype = p.atoms[1].text if len(p.atoms) > 1 else "unspecified"
        num = _child_text(p, "number")
        for a in p.find_all("alternate"):
            if alts.get(num) and len(a.atoms) > 2 and a.atoms[1].text == alts[num]:
                etype = a.atoms[2].text
        s.pins.append(self.add("pin", (pos[0] + dx, pos[1] + dy), ref=s.ref, num=num, out=out,
                               etype=etype, hidden=_pin_hidden(p), pname=_child_text(p, "name"),
                               sym=s))

    def check_loadable(self):
        for s in self.syms:
            if s.derived or s.lib is None:
                what = ("is derived ((extends ...)) and was never flattened into this file"
                        if s.derived else "this file's lib_symbols does not hold")
                raise _err("derived", f"{s.ref} uses library symbol '{s.key}', which {what}, so"
                           " its pins are unknown here and nothing was written. Remedy: re-place"
                           f" {s.ref} with place_component from a non-derived library symbol.")

    def hits(self, seg, skip=()):
        """Items within R of seg (a point when both ends are equal), skipping `skip`."""
        (ax, ay), (bx, by) = seg
        x0, x1, y0, y1 = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
        for it in self.items:
            b = it.box
            if b[1] >= x0 and b[0] <= x1 and b[3] >= y0 and b[2] <= y1 and it not in skip \
                    and _near(seg, (it.a, it.b)):
                yield it

    def in_area(self, seg) -> bool:
        return any(_inside(seg[0], poly) or _inside(seg[1], poly) or
                   any(_near(seg, e) for e in edges) for poly, edges in self.areas)

    def blocker(self, seg, skip):
        it = next(self.hits(seg, skip), None)
        return _desc(it) if it else ("a rule area" if self.in_area(seg) else None)

    def resolve(self, ref: str, ptxt: str):
        """(number, copies, references) for ref:ptxt, or raise _Refuse."""
        syms = [s for s in self.syms if ref in s.refs]
        if not syms:
            raise _Refuse("resolve", f"{ref} is not placed on this sheet. Use"
                          " list_schematic_components to see what is placed.")
        units = [u for s in syms for u in s.units]
        if len(syms) > 1 and (len({s.key for s in syms}) > 1 or len(units) != len(set(units))):
            raise _Refuse("dup_ref", f"{len(syms)} placed symbols answer to {ref} ("
                          + ", ".join(f"'{s.key}' unit {s.units}" for s in syms) + "), not distinct"
                          " units of one library symbol, so the netlist merges their pads."
                          " Remedy: annotate them with distinct references first.")
        for s in syms:
            if len(s.units) > 1:
                raise _Refuse("units", f"{ref}'s instance entries disagree on its unit"
                              f" ({', '.join(map(str, s.units))}); this sheet has no single pin"
                              f" geometry. Remedy: give every instance the same unit for {ref}.")
            if s.jumper:
                raise _Refuse("jumper", f"{ref} has jumper pins (KiCad 10), which join pins inside"
                              " the symbol; E does not model them. Remedy: wire it by hand.")
        allpins = [(_child_text(p, "number"), _child_text(p, "name"), _sch._lib_unit_style(sub))
                   for ls in {id(s.lib): s.lib for s in syms}.values()
                   for sub in ls.find_all("symbol") for p in sub.find_all("pin")]
        num = ptxt
        if ptxt not in {n for n, _, _ in allpins}:
            cands = sorted({n for n, nm, _ in allpins if nm == ptxt})
            if len(cands) != 1:
                raise _Refuse("resolve", f"{ref} has no pin numbered or named '{ptxt}'. Use"
                              " get_pin_positions to list its pins." if not cands else
                              f"pin name '{ptxt}' on {ref} matches several pads"
                              f" ({', '.join(cands)}); pass the pin number.")
            num = cands[0]
        copies = [p for s in syms for p in s.pins if p.num == num]
        if not copies:
            where = ", ".join(sorted({f"unit {i[0]} body style {i[1]}" for n, _, i in allpins
                                      if n == num and i})) or "no unit"
            raise _Refuse("resolve", f"pin {num} of {ref} is drawn by {where}, which this sheet"
                          " does not place. Place that unit, or wire the pin where it is drawn.")
        return num, copies, set().union(*(s.refs for s in syms))


# ------------------------------------------------------------------------------ the decisions


def _elsewhere(path, pads) -> dict:
    """{tag: [where]} for pads [(tag, num, copies, refs)] with a copy on another sheet (E-C12)."""
    if not pads:
        return {}
    me = Path(path).resolve()
    home = next((d for d in me.parents if any(d.glob("*.kicad_pro"))), me.parent)
    todo = [*me.parent.glob("*.kicad_sch"),
            *(q.with_suffix(".kicad_sch") for q in home.glob("*.kicad_pro"))]
    seen, out = set(), {}
    while todo:
        f = Path(todo.pop()).resolve()
        if f in seen or not f.is_file():
            continue
        seen.add(f)
        try:
            root = _cst.parse(f.read_bytes()).lists[0]
        except Exception as e:  # noqa: BLE001
            raise _err("sheets", f"cannot read {f} ({e}) to look for copies of the pad on other"
                       " sheets; nothing was written. Remedy: fix or remove that file.") from None
        for sh in root.find_all("sheet"):
            name = _shared._sheet_file_cst(sh)
            if name:
                todo += [f.parent / name, home / name]
        if f == me:
            continue
        for s in _Model(root).syms:
            for tag, num, _c, refs in pads:
                if s.refs & refs and (s.lib is None or s.derived or
                                      any(p.num == num for p in s.pins)):
                    out.setdefault(tag, []).append(f"{s.ref} in {f.name}")
    return out


def _own_label(it, N, P) -> bool:
    return it.kind == "label" and it.sub == "label" and it.text == N and it.a == P


def _state(m, at, same, N):
    """None when the pin end of copies `at` is free, a text when it already holds this tool's own
    shape for N (E-C8), else raise _Refuse (E-C9). N None: connect_pins (no names, no shapes)."""
    P = at[0].a
    for c in at:
        if c.etype == "no_connect":
            raise _Refuse("nc_type", f"pin end {_pt(*P)} is a no-connect type pin; KiCad never"
                          " connects it. Remedy: pick another pin.")
        name = _implicit(c)
        if N is not None and name is not None and name != N:
            raise _Refuse("implicit", f"the pin at {_pt(*P)} names its own net '{name}' ("
                          + ("its power symbol's Value" if c.sym.is_power else
                             "a hidden power_in pin's name") + f"); wiring it to '{N}' would"
                          f" merge '{name}' and '{N}'. Remedy: use '{name}'.")
    if m.in_area((P, P)):
        raise _Refuse("rule_area", f"pin end {_pt(*P)} is inside or within 0.05 mm of a rule"
                      " area, whose net class a new connection could spread. Remedy: wire it by"
                      " hand.")
    near = list(m.hits((P, P), same))
    if not near:
        return None
    nc = next((it for it in near if it.kind == "no_connect"), None)
    if nc:
        raise _Refuse("nc_flag", f"it has a no-connect flag at {_pt(*nc.a)}. Remove it with"
                      " remove_no_connect first if the pin should be connected.")
    it = near[0]
    if len(near) == 1 and N is not None:
        if _own_label(it, N, P):
            return f"label '{N}' already on the pin end {_pt(*P)}; unchanged"
        if it.kind == "wire" and P in (it.a, it.b) and it.a != it.b:
            E = it.b if it.a == P else it.a
            rest = list(m.hits((it.a, it.b), same | {it}))
            if len(rest) == 1 and _own_label(rest[0], N, E):
                return f"wire {_pt(*P)}-{_pt(*E)} with label '{N}' at its end already; unchanged"
    raise _Refuse("connected", f"pin end {_pt(*P)} is already connected: {_desc(it)} is within"
                  " 0.05 mm" + (f" (and {len(near) - 1} more item(s))" if len(near) > 1 else "")
                  + ". E only writes into empty space. Remedy: label that net yourself, or"
                  " remove what is there first.")


class _Plan:
    def __init__(self, model):
        self.m = model
        self.nodes: list = []
        self.lines: list[str] = []
        self.codes: list[str] = []
        self.wired = 0

    def ok(self, text):
        self.lines.append(text)

    def refuse(self, code, text):
        self.codes.append(code)
        self.lines.append(f"REFUSED {_codes([code])} {text}")

    def head(self, tool):
        return f"{_codes(self.codes)} {tool} refused the whole call; nothing was written.\n- "

    def add_wire(self, A, B):
        self.m.add("wire", A, B, new=True)
        node = _sch._WIRE_TPL.copy()
        for xy, (x, y) in zip(node.find("pts").find_all("xy"), (A, B)):
            xy.atoms[1].set_text(_mm(x))
            xy.atoms[2].set_text(_mm(y))
        node.find("uuid").atoms[1].set_text(_shared._gen_uuid())
        self.nodes.append(("wire", node))

    def add_label(self, P, text, rot):
        self.m.add("label", P, sub="label", text=text, new=True)
        node = _sch._LABEL_TPL.copy()
        node.atoms[1].set_text(text)
        at = node.find("at")
        at.atoms[1].set_text(_mm(P[0]))
        at.atoms[2].set_text(_mm(P[1]))
        at.atoms[3].set_text(str(rot))
        node.find("uuid").atoms[1].set_text(_shared._gen_uuid())
        self.nodes.append(("label", node))

    def write(self, tree, root, path):
        for token, node in self.nodes:
            _sch._splice_sch_node(root, token, node)
        _shared._atomic_write(path, _cst.serialize(tree))


def _wire(plan, path, pins, N, direction, L):
    """E's wire_pins_to_net decisions for one net; geometry goes into plan (E-C15)."""
    m = plan.m
    pads, seen = [], {}
    for pd in pins:
        if not isinstance(pd, dict) or not isinstance(pd.get("reference"), str) \
                or not isinstance(pd.get("pin"), (str, int)) or isinstance(pd.get("pin"), bool):
            plan.refuse("validation", f"{pd!r}: each pin must be {{'reference': str, 'pin': str}}.")
            continue
        tag = f"{pd['reference']}:{pd['pin']}"
        try:
            num, copies, refs = m.resolve(pd["reference"], str(pd["pin"]))
        except _Refuse as e:
            plan.refuse(e.code, f"{tag}: {e}")
            continue
        key = (pd["reference"], num)
        if key in seen:
            plan.ok(f"{tag}: same pad as {seen[key]}; counted once.")
            continue
        seen[key] = tag
        pads.append((tag, num, copies, refs))
    far = _elsewhere(path, pads)
    work = []
    for tag, num, copies, _refs in pads:
        if tag in far:
            plan.refuse("cross_sheet", f"{tag}: pad {num} also has a copy on another sheet ("
                        + ", ".join(far[tag]) + "), which this call cannot see or wire. Remedy:"
                        " label every copy yourself, on every sheet, with the same global name.")
            continue
        same, todo, parts = set(copies), [], []
        try:
            for P in dict.fromkeys(c.a for c in copies):  # E-C14
                at = [c for c in copies if c.a == P]
                own = _state(m, at, same, N)
                if own:
                    parts.append(own)
                else:
                    todo.append(at)
        except _Refuse as e:
            plan.refuse(e.code, f"{tag}: {e}")
            continue
        work.append((tag, same, todo, parts))
    for tag, same, todo, parts in work:
        try:
            for at in todo:
                P, d = at[0].a, at[0].out if direction == "auto" else _DIRS[direction]
                E = (P[0] + d[0] * L, P[1] + d[1] * L)  # E-C7
                why = m.blocker((P, E), set(at))
                if why is None:
                    plan.add_wire(P, E)
                    plan.add_label(E, N, _LABEL_ROT[d])
                    parts.append(f"stub {_DIR_NAME[d]} {_mm(L)} mm {_pt(*P)}-{_pt(*E)}, label"
                                 f" '{N}' at its end")
                elif (why2 := m.blocker((P, P), same)) is None:
                    plan.add_label(P, N, _LABEL_ROT[d])
                    parts.append(f"label '{N}' on the pin end {_pt(*P)} (stub {_DIR_NAME[d]}"
                                 f" blocked by {why})")
                else:
                    raise _Refuse("blocked", f"stub {_DIR_NAME[d]} from {_pt(*P)} blocked by"
                                  f" {why}; a label on the pin end blocked by {why2}. Remedy:"
                                  " pass another direction, move the part, or wire it by hand.")
                plan.wired += 1
            plan.ok(f"{tag}: " + "; ".join(parts))
        except _Refuse as e:
            plan.refuse(e.code, f"{tag}: {e}")


# ----------------------------------------------------------------------------------- validation


def _check_net_name(text, what="label_text"):
    if not isinstance(text, str) or text == "":
        raise _err("validation", f"{what} must be a non-empty net name. Pass the net's name.")
    if "${" in text:
        raise _err("validation", f"{what} {text!r} contains a text variable ('${{'): its resolved"
                   " name, and so what it would join, cannot be known here. Use the literal name.")
    if _AUTO_NAME.match(text):
        raise _err("validation", f"{what} {text!r} looks like a KiCad auto-generated net name;"
                   " KiCad renames or splits nets that collide with one. Choose a real net name.")
    if _BUS_RANGE.search(text) or any(
        c == "{" and (i == 0 or text[i - 1] not in "_^~") for i, c in enumerate(text)
    ):
        raise _err("validation", f"{what} {text!r} is bus syntax; a bus label on a wire is a"
                   " bus/net conflict. Choose a plain net name.")


def _check_stub(stub_length) -> int:
    msg = "stub_length must be a finite number of mm greater than 0"
    try:
        v = float(stub_length)
    except (TypeError, ValueError):
        raise _err("validation", f"{msg}.") from None
    if isinstance(stub_length, bool) or not math.isfinite(v) or v <= 0:
        raise _err("validation", f"{msg}; got {stub_length!r}.")
    L = _kiround(v * 10000.0)
    if L < 1:
        raise _err("validation", f"stub_length {v} mm rounds to 0 at KiCad's 0.0001 mm"
                   " resolution.")
    return L


# ---------------------------------------------------------------------------------- the tools


@_coded
def wire_pins_to_net(
    pins: list,
    label_text: str,
    direction: str = "auto",
    stub_length: float = 2.54,
    auto_pwr_flag: bool = True,
    schematic_path: str = SCH_PATH,
) -> str:
    """Put every listed pin on net label_text by writing only into empty space. One write."""
    _check_net_name(label_text)
    if direction not in ("auto", "left", "right", "up", "down"):
        raise _err("validation", "direction must be one of auto, left, right, up, down; got"
                   f" {direction!r}.")
    L = _check_stub(stub_length)
    if not pins:
        return f"Wired 0 pins to '{label_text}'."
    tree, root, *_ = _sch._open_sch_cst(schematic_path)
    m = _Model(root)
    m.check_loadable()
    plan = _Plan(m)
    _wire(plan, schematic_path, pins, label_text, direction, L)
    if plan.codes:
        raise ToolError(plan.head("wire_pins_to_net") + "\n- ".join(plan.lines))
    if not plan.nodes:
        return f"No change: every pin is already on '{label_text}'.\n- " + "\n- ".join(plan.lines)
    plan.write(tree, root, schematic_path)
    return f"Wired {plan.wired} pin end(s) to '{label_text}' in one write.\n- " + \
        "\n- ".join(plan.lines)


@_coded
def connect_pins(ref1: str, pin1: str, ref2: str, pin2: str, schematic_path: str = SCH_PATH) -> str:
    """Connect two free pins with a wire through empty space (E-C16). One write."""
    tree, root, *_ = _sch._open_sch_cst(schematic_path)
    m = _Model(root)
    m.check_loadable()
    ends = []
    for ref, pin in ((ref1, pin1), (ref2, pin2)):
        try:
            num, copies, refs = m.resolve(ref, str(pin))
        except _Refuse as e:
            raise _err(e.code, f"{_CHEAD} {ref}:{pin}: {e}") from None
        ends.append((f"{ref}:{pin}", num, copies, refs))
    (ta, _na, ca, _), (tb, _nb, cb, _) = ends
    if ca == cb:
        return f"No change: {ta} and {tb} are the same pin."
    for tag, num, copies, _r in ends:
        if len(copies) != 1:
            raise _err("copies", f"{_CHEAD} {tag} is pad {num}, which has {len(copies)} drawn"
                       " copies on this sheet. Use wire_pins_to_net, which wires every copy.")
    far = _elsewhere(schematic_path, ends)
    if far:
        raise _err("cross_sheet", f"{_CHEAD} " + "; ".join(
            f"{t} has a copy on another sheet ({', '.join(w)})" for t, w in far.items()) + ".")
    a, b = ca[0], cb[0]
    if _implicit(a) is not None and _implicit(b) is not None:
        raise _err("implicit", f"{_CHEAD} {ta} names its net '{_implicit(a)}' and {tb} names its"
                   f" net '{_implicit(b)}'; a wire would merge two named nets. If intended, do"
                   " it by hand.")
    for tag, c in ((ta, a), (tb, b)):
        try:
            _state(m, [c], {c}, None)
        except _Refuse as e:
            raise _err(e.code, f"{_CHEAD} {tag}: {e}") from None
    Pa, Pb = a.a, b.a
    if Pa[0] == Pb[0] or Pa[1] == Pb[1]:
        cands = [("straight", [(Pa, Pb)])]
    else:
        ch, cv = (Pb[0], Pa[1]), (Pa[0], Pb[1])
        cands = [("L horizontal-first", [(Pa, ch), (ch, Pb)]),
                 ("L vertical-first", [(Pa, cv), (cv, Pb)])]
    tried = []
    for name, segs in cands:
        why = next((w for s in segs if (w := m.blocker(s, {a, b}))), None)
        if why is None:
            plan = _Plan(m)
            for A, B in segs:
                plan.add_wire(A, B)
            plan.write(tree, root, schematic_path)
            pts = " -> ".join(_pt(*p) for p in [Pa] + [B for _, B in segs])
            return f"Connected {ta} -> {tb} via {len(segs)} wire segment(s) ({name}: {pts});" \
                   " no label, no junction."
        tried.append(f"{name} blocked by {why}")
    raise _err("blocked", f"{_CHEAD} Every route comes within 0.05 mm of something else:\n- "
               + "\n- ".join(tried) + "\nUse wire_pins_to_net with one shared net name for both"
               " pins instead.")


def _exact_lib(root, name):
    libs = root.find("lib_symbols")
    for ls in libs.find_all("symbol") if libs is not None else []:
        if len(ls.atoms) > 1 and ls.atoms[1].text == name:
            return ls
    return None


def _compose_place(root, page, lib_id, reference, value, x, y, rotation, symbol_lib_path,
                   schematic_path, project_path):
    """place_component's node construction on an in-memory tree (exact lib entry names; K's)."""
    if not isinstance(reference, str) or not _sch._VALID_REF_RE.match(reference):
        raise ToolError(f"'{reference}' is not a valid KiCad reference designator. Must match"
                        " pattern [A-Z]+[0-9]+[A-Z]* (e.g. 'C5').")
    _shared._check_rotation(rotation)
    if any(_shared._sym_property_cst(s, "Reference") == reference for s in root.find_all("symbol")):
        raise ToolError(f"{reference} is already placed in this schematic.")
    _sch._bounds_check(x, y, *page)
    x, y = _shared._snap_grid(x), _shared._snap_grid(y)
    symbol_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    entry = symbol_name if symbol_lib_path else lib_id
    ls = _exact_lib(root, entry)
    if ls is None:
        if symbol_lib_path:
            _shared._require_kicad_path(symbol_lib_path, "symbol library")
            if not _sch._copy_lib_symbol_from_file_cst(root, symbol_lib_path, symbol_name,
                                                       symbol_name):
                raise ToolError(f"symbol '{symbol_name}' not found in {symbol_lib_path}.")
        elif ":" in lib_id:
            prefix = lib_id.split(":")[0]
            if not _sch._copy_system_lib_symbol_cst(root, prefix, symbol_name):
                raise ToolError(f"symbol '{symbol_name}' not found in the {prefix} library.")
        else:
            raise ToolError(f"'{lib_id}' is not in this schematic's lib_symbols; pass"
                            " Library:Symbol or symbol_lib_path.")
        ls = _exact_lib(root, entry)
        if ls is None:
            raise ToolError(f"could not load '{lib_id}' into lib_symbols.")
    if ls.find("extends") is not None:
        raise _err("derived", f"'{lib_id}' is a derived symbol ((extends ...)); placing it"
                   " unflattened makes a schematic KiCad cannot load. Nothing was written."
                   " Remedy: use its parent symbol.")
    node = _sch._SYMBOL_TPL.copy()
    node.find("lib_name").atoms[1].set_text(ls.atoms[1].text)
    node.find("lib_id").atoms[1].set_text(lib_id)
    _cst._fill_at(node, x, y, rotation)
    sym_uuid = _shared._gen_uuid()
    node.find("uuid").atoms[1].set_text(sym_uuid)
    props = node.find_all("property")
    props[0].atoms[2].set_text(reference)
    _cst._fill_at(props[0], x, round(y - 3.81, 4))
    props[1].atoms[2].set_text(value)
    _cst._fill_at(props[1], x, round(y + 3.81, 4))
    _cst._fill_at(props[2], x, y)
    _cst._fill_at(props[3], x, y)
    instances = node.find("instances")
    pin_nums = {n for u in ls.find_all("symbol") for p in u.find_all("pin")
                if (n := _child_text(p, "number", None)) is not None}
    for pn in sorted(pin_nums):
        pnode = _sch._PIN_REF_TPL.copy()
        pnode.atoms[1].set_text(pn)
        pnode.find("uuid").atoms[1].set_text(_shared._gen_uuid())
        node.insert_before(instances, pnode)
    root_uuid = _shared._node_uuid(root)
    try:
        if project_path:
            project_name, sheet_path = _shared._resolve_hierarchy_path(
                project_path, schematic_path, root_uuid)
        else:
            project_name, sheet_path = Path(schematic_path).stem, f"/{root_uuid}"
    except OSError as e:
        raise ToolError(f"cannot resolve the hierarchy from {project_path}: {e}") from e
    project = instances.find("project")
    project.atoms[1].set_text(project_name)
    ipath = project.find("path")
    ipath.atoms[1].set_text(sheet_path)
    ipath.find("reference").atoms[1].set_text(reference)
    if project_path:  # same parent-instance logic as place_component (read-only on others)
        for pro_file in Path(schematic_path).parent.glob("*.kicad_pro"):
            parent = pro_file.with_suffix(".kicad_sch")
            if not parent.exists() or parent.resolve() == Path(schematic_path).resolve():
                continue
            proot = _cst.parse(parent.read_bytes()).lists[0]
            s = next((s for s in proot.find_all("sheet")
                      if _shared._sheet_file_cst(s) == Path(schematic_path).name), None)
            if s is not None:
                extra = project.copy()
                extra.atoms[1].set_text(pro_file.stem)
                epath = extra.find("path")
                epath.atoms[1].set_text(f"/{_shared._node_uuid(proot)}/{_shared._node_uuid(s)}")
                epath.find("reference").atoms[1].set_text(reference)
                instances.insert_after(project, extra)
                break
    _sch._splice_sch_node(root, "symbol", node)
    return node, sym_uuid, x, y


def _root_index(root, schematic_path, project_path, sym_uuid, reference, value):
    """place_component's symbol_instances upsert without writing (K's): applied to `root` when
    the schematic is its own root, else (other_path, other_bytes) to write after it."""
    root_path = _shared._resolve_root(schematic_path, project_path) or \
        _shared._find_root_schematic(schematic_path)
    if root_path is not None:
        t2 = _cst.parse(_shared._read_kicad_bytes(root_path, "root schematic"))
        r2 = t2.lists[0]
        sheet_uuid = next((_shared._node_uuid(s) for s in r2.find_all("sheet")
                           if _shared._sheet_file_cst(s) == Path(schematic_path).name), None)
        if sheet_uuid is None:
            return None, None
        _shared._upsert_entry(r2, f"/{_shared._node_uuid(r2)}/{sheet_uuid}/{sym_uuid}",
                              reference, 1, value, "")
        return root_path, _cst.serialize(t2)
    if Path(schematic_path).with_suffix(".kicad_pro").exists():
        _shared._upsert_entry(root, f"/{_shared._node_uuid(root)}/{sym_uuid}", reference, 1,
                              value, "")
    return None, None


@_coded
def auto_place_decoupling_cap(
    lib_id: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    power_net: str,
    ground_net: str,
    rotation: int = 0,
    symbol_lib_path: str = "",
    schematic_path: str = SCH_PATH,
    project_path: str = "",
) -> str:
    """Place a capacitor on free ground and wire pin 1 to power_net, pin 2 to ground_net with
    E's rules (E-C17). One write per file, every file intact on refusal."""
    _check_net_name(power_net, "power_net")
    _check_net_name(ground_net, "ground_net")
    tree, root, page_w, page_h, page_name = _sch._open_sch_cst(schematic_path)
    node, sym_uuid, sx, sy = _compose_place(
        root, (page_w, page_h, page_name), lib_id, reference, value, x, y, rotation,
        symbol_lib_path, schematic_path, project_path)
    m = _Model(root)
    m.check_loadable()
    new = [it for it in m.items if it.kind == "pin" and it.sym.node is node]
    for p in new:
        why = m.blocker((p.a, p.a), {q for q in new if q.num == p.num})
        if why:
            raise _err("landing", f"auto_place_decoupling_cap refused; nothing was written."
                       f" {reference} pin {p.num} would land at {_pt(*p.a)}, within 0.05 mm of"
                       f" {why}. Choose another spot.")
    plan = _Plan(m)
    L = _check_stub(2.54)
    _wire(plan, schematic_path, [{"reference": reference, "pin": "1"}], power_net, "auto", L)
    _wire(plan, schematic_path, [{"reference": reference, "pin": "2"}], ground_net, "auto", L)
    if plan.codes:
        raise ToolError(plan.head("auto_place_decoupling_cap") + "\n- ".join(plan.lines))
    other_path, other_bytes = _root_index(root, schematic_path, project_path, sym_uuid,
                                          reference, value)
    plan.write(tree, root, schematic_path)
    if other_path is not None:
        _shared._atomic_write(other_path, other_bytes)
    return f"Placed {reference} ({value}) at ({sx}, {sy}) | pin 1->{power_net} | pin 2->" \
           f"{ground_net}\n- " + "\n- ".join(plan.lines)


def pin_positions(schematic_path: str, reference: str) -> dict:
    """{number: [(x_mm, y_mm, outward)]} for every drawn copy, from KiCad's transform."""
    _, root, *_ = _sch._open_sch_cst(schematic_path)
    out: dict[str, list] = {}
    for it in _Model(root).items:
        if it.kind == "pin" and it.ref == reference:
            out.setdefault(it.num, []).append((it.a[0] / 10000, it.a[1] / 10000,
                                               _DIR_NAME.get(it.out)))
    return out
