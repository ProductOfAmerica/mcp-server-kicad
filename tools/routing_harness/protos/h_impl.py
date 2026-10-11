"""Design H for the coarse-model experiment: a standalone copy of coarse_impl.py (design K) with the
behaviour chosen after the K/M2 comparison hard-wired, the attribution machinery (switches,
counterfactual rebuilds, "[lifts with: ...]") removed, and two refusals added for the defects
neither K nor M2 covered (duplicate references, netclass carriers).

Scratch prototype for the routing pressure test, not repo code. It imports the repo from main
(or PT_REPO) read-only and copies or wraps the helpers it needs. Every refusal raises mcp
ToolError before any write, so a refused call leaves every file byte-identical; a successful
call writes each file it changes exactly once through the repo's _atomic_write.

THE TWO MODELS
- Narrow no-op model C, exactly K's: only joins every KiCad reader makes; it may under-count, at
  the cost of a redundant label. Exact point coincidence among pin ends (not no_connect type, not
  on a symbol whose instance entries disagree on its unit), ends of reliable wires (no junction or
  bus-entry end on the interior, no collinear overlap), labels of the three named kinds and
  junctions; a wire joins its own two ends; a named label exactly on the interior of exactly one
  reliable wire, with no other item at its anchor, joins that wire.
- Coarse refusal model: over-approximate. Margin r = 500 IU (0.05 mm). Point items: every
  connection point of every item (pin ends of every type, hidden and no_connect type included;
  wire, bus and graphic line ends; label anchors of every kind, directive labels included;
  junctions; NC flags; sheet pins; both ends of every bus entry). Lines: wires, buses, bus entries
  and 2-point graphic polylines. Joins: two points within r; a point within r of a line; a bus
  entry's two ends; every copy of a pad; KiCad 10 jumper groups; same-text names, transitively.
  Lines join only through ends and points within r: a plain crossing does not join.
- Names, both models, KiCad's rule only: texts of the three named label kinds; a power_in pin on a
  power symbol, by the symbol's Value; a hidden power_in pin on any other symbol, by its primary
  name. Directive labels, PWR_FLAG (its pin is power_out) and visible power_in pins on ordinary
  symbols name nothing.
- Unhandled items (refuse when the coarse component reaches one): bus, bus entry, sheet pin, text
  with '${', a symbol with jumper pins, a unit-0 pin of a part whose units are not all placed on
  this sheet, a symbol whose instance entries disagree on its unit. A derived or unresolved library
  symbol anywhere on the sheet refuses the whole call.

DECISIONS
  wire_pins_to_net, per pad: (0) a duplicated reference refuses (H-C1); (1) every copy on N in C:
  no-op; no-connect type or flag: refuse; (2) the coarse component reaches an unhandled item:
  refuse; (3) its coarse names hold anything other than N: refuse; (4) otherwise, for each copy
  not on N in C, the first candidate (outward stub then label at its end; label on the pin) that
  passes the touch rule and the netclass check (H-C5), also when only the coarse model reaches N
  (the outcome line says so).
  connect_pins: (0) as above, both pads; (1) one C component or a shared C name, for every copy
  pair: no-op; copies, no-connect type, no-connect flag: refuse; (2) either coarse component
  reaches an unhandled item: refuse; (3) both sides carry coarse names: refuse; (4) otherwise the
  first route (straight, L horizontal-first, L vertical-first) that passes the touch rule and the
  netclass check.
  auto_place_decoupling_cap: the same decisions, composed.
  When nothing on this sheet carries N the result says "'N' is a new local net on this sheet."

CONSERVATIVE CHOICES THE SPEC LEFT OPEN (H-Cn; K-Cn names the K entry an inherited one came from)
- H-C1 Duplicate references. Scope: every placed symbol on every sheet reachable from the root
  (the .kicad_pro's same-stem schematic beside this sheet, found as the repo's
  _find_root_schematic finds it, else this sheet), this sheet included; a sheet file name is
  resolved against its parent sheet's directory, then the root's. A symbol is known by every
  reference it can carry: its Reference property and every (reference) in its (instances)
  entries, all projects and paths. The target pad (looked up by Reference property, I22) is
  refused when another placed symbol shares any of those references with a symbol the target
  reference names, unless every symbol in that group resolves to a library symbol with the same
  name and identical bytes, none has instance entries that disagree on its unit, and each places
  a distinct unit (each unit once). A sheet that cannot be read or parsed refuses too. Checked
  for every pad the call names, before the no-op test.
- H-C2 Netclass carriers: any label of the four kinds with a non-empty "Netclass" property (KiCad
  reads that field on every subgraph item, 9.0.8 connection_graph.cpp:437-481, not only on
  directive labels); and every rule area (rule_area polyline, closed), whose classes are those of
  the directive labels anchored within r of its outline (KiCad: 5 IU of the edge,
  sch_rule_area.cpp:243; a directive deep inside the area does not attach).
- H-C3 Possible classes. Of a coarse component: its label carriers, and the classes of every rule
  area any of its items touches: a point item inside the polygon or within r of its outline; a
  line (any coarse line kind) inside, crossing, or within r plus half its stroke width of the
  outline (KiCad collides the wire segment with its width, sch_rule_area.cpp:259-262). Of new
  geometry: the same for each new wire and new label anchor, plus label carriers within r.
- H-C4 Certain classes of a narrow component: the Netclass values of its named labels; of
  directive labels it certainly holds (anchor exactly at one of its certain conductors, or alone
  at its anchor on the interior of exactly one of its reliable wires: C's own label rule); and of
  every rule area that certainly holds both a directive (anchor within 4 IU of the outline,
  inside KiCad's 5 IU whichever way it rounds) and one of the component's items (a visible pin
  end, a named label anchor or an end of a reliable wire, inside or exactly on the outline). A
  class text containing '${' is never certain.
- H-C5 The netclass check, every candidate geometry of both tools: it fires when the pin's coarse
  component (connect_pins: either pin's) or the new geometry possibly carries any class. It then
  passes only when every part being joined certainly carries one class set S and possibly carries
  nothing else, and the new geometry possibly carries nothing outside S. The parts: the pin's (or
  both pins') component and, for wire_pins_to_net, N's component on this sheet (every narrow
  component holding an item named N, read together; absent when nothing here is named N). A
  rejected stub falls back to the label on the pin, as for the touch rule; a pad with no
  candidate left is refused [H:netclass]. Classes only N's side carries do not fire it: KiCad
  then gives the pin's net N's classes, which is the requested join.
- H-C6 Pin names are taken verbatim, '' and '~' included (a hidden power_in pin so named on an
  ordinary symbol names its net in KiCad's rule; verbatim text is the wider join key). The '${'
  check for a pin covers its name only when the pin names something.
- H-C7 (K-C5) Bus entries are lines: a point within r of an entry's body joins it. Reaching an
  entry refuses anyway.
- H-C8 (K-C6) "Text containing '${'": the text of every label kind, every label property value
  except Intersheetrefs (Netclass included), and a pin's name when it names its net.
- H-C9 (K-C9) A symbol with jumper pins: its library symbol has a jumper_pin_groups entry holding
  at least one group, or duplicate_pin_numbers_are_jumpers yes. Reaching any of its pins refuses.
- H-C10 (K-C10) A unit-0 pin is drawn from a library sub-symbol whose name encodes unit 0. "All
  units placed": the library's units are all among the units this sheet places under that
  reference.
- H-C11 (K-C11) The no-connect type and flag refusals run after decision 1 and before 2 and 3.
- H-C12 (K-C12) All unhandled reasons on a component are reported, in the fixed order bus,
  bus_entry, sheet_pin, text_var, jumper, unit0_unplaced, units_disagree.
- H-C13 (K-C14) Graphic polylines of 3 or more points stay out of both models (they load as
  graphic shapes); 2-point ones are coarse lines (GLINE kept).
- H-C14 (K-C15) Same-text joins and name matching compare raw text (case-sensitive, no {slash}
  normalisation).
- H-C15 (K-C16) Items the call adds join the models for later wiring in the same call.
- H-C16 (K-C17) Pins of a symbol whose instance entries disagree on its unit enter the coarse
  model for every listed unit and refuse as units_disagree.
- H-C17 (K-C18) "Already connected to a wired pin" mates are read from the coarse component.
- H-C18 (K-C19) Zero-length lines are points only.
- H-C19 Crossings. With CROSS_BAN False (the default) the touch rule lets a new stub or route
  cross an existing line's interior at a clean point; a crossing with any point item within r of
  the new wire is refused by rule 3 already. No reader joins at such a crossing when it is
  written, and no later H call can land on it (rules 1 and 2). A later call that puts a label, a
  junction or a wire end exactly on the crossing joins both lines: the repo's add_label,
  add_global_label, add_junctions and add_wires at 54160c1 all do (h_build/crossban_probe.out).
  CROSS_BAN True adds rule 5: a new wire may not cross any coarse line (wire, bus, bus entry,
  2-point graphic line) at an interior point of both; the stub then falls back to the label on
  the pin. It stops H adding such points; the crossings a sheet already has are untouched.

REASON CODES. A refusal message starts with the bracketed code of every distinct reason in it, in
order of first occurrence; each per-pin line of wire_pins_to_net repeats its own code(s).
  [H:validation]     an argument is invalid, the file cannot be read, or the repo rejects a
                     place_component input (also the default for any uncoded ToolError)
  [H:unloadable]     a placed symbol or a library pin at an angle KiCad cannot load
  [H:derived]        a derived or unresolved library symbol anywhere on the sheet
  [H:resolve]        reference or pin not found, a name matching several pads, or no copy of the
                     pad drawn on this sheet
  [H:dup_ref]        the target reference is shared by symbols that are not distinct units of one
                     part (H-C1), or a sheet of the hierarchy could not be read
  [H:units_disagree] the target symbol's instance entries disagree on its unit, or the coarse
                     component reaches such a symbol
  [H:nc_type]        the pin's electrical type is no_connect
  [H:nc_flag]        a no-connect flag on the pin end
  [H:bus] [H:bus_entry] [H:sheet_pin] [H:text_var] [H:jumper] [H:unit0_unplaced]
                     the coarse component reaches that unhandled item
  [H:names]          coarse names other than N (connect_pins: coarse names on both sides)
  [H:copies]         connect_pins on a pad with several drawn copies
  [H:touch]          every candidate geometry fails the strict touch rule
  [H:netclass]       every candidate geometry fails the netclass check (H-C5)

Exported API (same shapes as the repo tools):
    wire_pins_to_net(pins, label_text, direction="auto", stub_length=2.54, auto_pwr_flag=True,
                     schematic_path=SCH_PATH)
    connect_pins(ref1, pin1, ref2, pin2, schematic_path=SCH_PATH)
    auto_place_decoupling_cap(lib_id, reference, value, x, y, power_net, ground_net, rotation=0,
                              symbol_lib_path="", schematic_path=SCH_PATH, project_path="")
Probe helper: pin_positions(schematic_path, reference) -> {number: [(x_mm, y_mm, outward)]}.

New geometry must pass M's touch rule (M 5.3) with a 0.05 mm tolerance. No junction is ever
written.

INTERPRETATIONS (carried over from K and M; THE TWO MODELS and DECISIONS above replace I05, I08,
I09, I19, I20, I23 and I24 where they differ)
- I01 Lib symbol resolution follows KiCad exactly: lib_name when present and non-empty, else
  lib_id, exact name match in lib_symbols; no fallback from an unresolved lib_name to lib_id.
- I02 Unit: the set of (unit N) values over the symbol's instance entries; if empty, the
  top-level (unit N), else 1. More than one distinct value makes the symbol unit-ambiguous.
- I04 Pin lookup: exact number over every pin of the resolved lib symbol, else exact primary
  name; a name matching several numbers is refused. Copies are every pin drawn on this sheet by
  a placed symbol with that Reference property and number.
- I06 Per-copy decision: each copy already (certainly) on N is left alone, every other copy gets
  geometry. A pad is "already on N" only when all copies are.
- I07 A foreign name that is transitively the same net as N still refuses.
- I10 Tolerance: 0.05 mm = 500 IU, Euclidean point-point and point-segment distance.
- I11-I13 The touch rule (M 5.3) as K has it.
- I14 Label rotation follows the stub direction (right 0, up 90, left 180, down 270).
- I15 auto_pwr_flag is accepted and never places a flag.
- I16 Validation of label_text, direction and stub_length as K.
- I17 An empty pins list validates the arguments and returns without reading or writing.
- I18 Pins are deduplicated by resolved pad (reference, number).
- I21 auto_place_decoupling_cap: the new symbol's pin ends are new endpoints under rule 2.
- I22 Pad identity uses the placed symbol's Reference property.
- I25 A (mirror) token written before (at) is ignored, as KiCad's parser does.
- I26 Coordinates are written from integer IU with exact decimal formatting.
- I28 The CST is walked once per call; new items are appended to the item tables and the models
  are rebuilt from the tables.
- I29 Check order: argument validation, file read, loadability, then every pin is resolved and
  checked, then geometry; all per-pin refusals are reported together.
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

TOL = 500  # 0.05 mm in IU: the touch-rule tolerance (M 5.3) and the coarse margin r
TOL2 = TOL * TOL
CERT2 = 16  # H-C4: a directive within 4 IU of a rule area's outline certainly attaches
CROSS_BAN = False  # H-C19: True refuses new wires that cross an existing line (touch rule 5)
_GRID = 25400  # coarse line index cell, IU (2.54 mm)
_SEGS = ("wire", "bus", "be", "gline")  # coarse-model lines
_BAD = {  # unhandled item code: (what it is, why this tool cannot judge it, remedy)
    "bus": ("a bus", "bus members are not modelled", "keep this net's wiring off the bus"),
    "bus_entry": ("a bus entry", "bus members are not modelled",
                  "keep this net's wiring off the bus entry"),
    "sheet_pin": ("a sheet pin", "its net is named across the hierarchy",
                  "label this net with the name the child sheet gives that port"),
    "text_var": ("text with a text variable", "its value is unknown here",
                 "replace the text variable with the literal text"),
    "jumper": ("a symbol with jumper pins", "KiCad 10 joins pins inside it",
               "keep this net off that symbol"),
    "unit0_unplaced": ("a pin common to all units of a part not fully placed here",
                       "the pad has copies this sheet cannot see",
                       "place every unit of that part on this sheet"),
    "units_disagree": ("a symbol whose instance entries disagree on its unit",
                       "a reused sheet draws different pins per instance",
                       "give every instance of the sheet the same unit for it"),
}

_ROT = {0: (1, 0, 0, 1), 90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}
_MIR = {"x": (1, 0, 0, -1), "y": (-1, 0, 0, 1)}
_TOWARD = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}  # sch_pin.cpp:608-615
_DIRS = {"right": (1, 0), "left": (-1, 0), "up": (0, -1), "down": (0, 1)}
_DIR_NAME = {v: k for k, v in _DIRS.items()}
_LABEL_ROT = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}
_LABEL_KINDS = {
    "label": "label",
    "global_label": "global label",
    "hierarchical_label": "hierarchical label",
    "netclass_flag": "directive label",
    "directive_label": "directive label",
}
_AUTO_NAME = re.compile(r"^(Net|unconnected)-\(")
_BUS_RANGE = re.compile(r"\[[^\]]*\.\.[^\]]*\]")

_TAG = "H"  # reason-code prefix


class _Refuse(Exception):
    """A per-pin refusal with its reason code(s); collected and reported, never escapes."""

    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


def _codes(codes) -> str:
    return " ".join(f"[{_TAG}:{c}]" for c in dict.fromkeys(codes))


def _err(code, text) -> ToolError:
    return ToolError(f"{_codes([code] if isinstance(code, str) else code)} {text}")


def _coded(fn):
    """A ToolError from a repo helper the tool calls gets the [validation] code."""

    @functools.wraps(fn)
    def run(*args, **kw):
        try:
            return fn(*args, **kw)
        except ToolError as e:
            if str(e).startswith(f"[{_TAG}:"):
                raise
            raise _err("validation", str(e)) from None

    return run


# ----------------------------------------------------------------------------------- numbers


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


def _within(px, py, ax, ay, bx, by, r2) -> bool:
    """Squared distance from point to closed segment <= r2, exact in integers."""
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


def _d2(ax, ay, bx, by) -> int:
    return (ax - bx) ** 2 + (ay - by) ** 2


def _side(ax, ay, bx, by, px, py) -> int:
    v = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    return (v > 0) - (v < 0)


def _edges(poly):
    return zip(poly, poly[1:] + poly[:1])


def _in_poly(x, y, poly) -> bool:
    """Even-odd point in polygon, exact in integers (points on the outline are undefined here;
    every caller also tests the outline)."""
    inside = False
    for (ax, ay), (bx, by) in _edges(poly):
        if (ay > y) != (by > y):
            lhs, rhs = (x - ax) * (by - ay), (y - ay) * (bx - ax)
            if (lhs < rhs) if by > ay else (lhs > rhs):
                inside = not inside
    return inside


def _near_poly(x, y, poly, r2) -> bool:
    """Inside the polygon or within sqrt(r2) of its outline."""
    return _in_poly(x, y, poly) or any(_within(x, y, *a, *b, r2) for a, b in _edges(poly))


def _seg_poly(ax, ay, bx, by, poly, r2) -> bool:
    """A segment inside, crossing, or within sqrt(r2) of the polygon's outline."""
    if _near_poly(ax, ay, poly, r2) or _near_poly(bx, by, poly, r2):
        return True
    for (cx, cy), (dx, dy) in _edges(poly):
        if _within(cx, cy, ax, ay, bx, by, r2):
            return True
        if _side(cx, cy, dx, dy, ax, ay) * _side(cx, cy, dx, dy, bx, by) < 0 \
                and _side(ax, ay, bx, by, cx, cy) * _side(ax, ay, bx, by, dx, dy) < 0:
            return True
    return False


# --------------------------------------------------------------------------------- transform


def _compose(old, temp):
    """new = old * temp, exactly as SCH_SYMBOL::SetOrientation (9.0 sch_symbol.cpp:1906-1913)."""
    ox1, oy1, ox2, oy2 = old
    tx1, ty1, tx2, ty2 = temp
    return (
        ox1 * tx1 + ox2 * ty1,
        oy1 * tx1 + oy2 * ty1,
        ox1 * tx2 + ox2 * ty2,
        oy1 * tx2 + oy2 * ty2,
    )


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
                raise _err(
                    "unloadable",
                    f"{ref} is placed at angle {a[3].text}, which KiCad cannot load (0, 90, 180"
                    " or 270 only). Nothing was written. Remedy: rotate it to one of those.",
                )
            t = _ROT[ang]
        elif ch.head == "mirror" and len(ch.atoms) > 1 and ch.atoms[1].text in _MIR:
            t = _compose(t, _MIR[ch.atoms[1].text])
    return pos, t


def _child_text(node, key, default=""):
    c = node.find(key)
    return c.atoms[1].text if c is not None and len(c.atoms) > 1 else default


def _inst_paths(sym):
    inst = sym.find("instances")
    for proj in inst.find_all("project") if inst is not None else ():
        yield from proj.find_all("path")


def _units(sym) -> list[int]:
    units: set[int] = set()
    for path in _inst_paths(sym):
        u = path.find("unit")
        if u is not None and len(u.atoms) > 1:
            try:
                units.add(int(u.atoms[1].text))
            except ValueError:
                pass
    if not units:
        units.add(_sch._sym_unit_cst(sym))
    return sorted(units)


def _sym_refs(sym) -> set:
    """H-C1: every reference a placed symbol can carry."""
    refs = {_shared._sym_property_cst(sym, "Reference") or "?"}
    refs.update(_child_text(p, "reference") for p in _inst_paths(sym)
                if p.find("reference") is not None)
    return refs


def _lib_index(root) -> dict:
    libs = root.find("lib_symbols")
    out: dict = {}
    for ls in libs.find_all("symbol") if libs is not None else ():
        if len(ls.atoms) > 1:
            out.setdefault(ls.atoms[1].text, ls)
    return out


def _sym_record(sym, libs):
    """H-C1: (references, library name, library node or None, units) of one placed symbol."""
    key = _child_text(sym, "lib_name") or _child_text(sym, "lib_id")
    return _sym_refs(sym), key, libs.get(key), _units(sym)


def _pin_hidden(p) -> bool:
    if any(a.text == "hide" for a in p.atoms[3:]):
        return True
    h = p.find("hide")
    return h is not None and (len(h.atoms) < 2 or h.atoms[1].text == "yes")


# ------------------------------------------------------------------------------------- items


class _Sym:
    __slots__ = ("node", "ref", "value", "key", "lib", "derived", "units", "amb", "style",
                 "is_power", "pins", "jumper", "lunits")

    def __init__(self, node, ref, value, key, lib):
        self.node, self.ref, self.value, self.key, self.lib = node, ref, value, key, lib
        self.derived = False
        self.units: list[int] = []
        self.amb = False
        self.style = 1
        self.is_power = False
        self.pins: list = []
        self.jumper = False
        self.lunits: set[int] = {1}


class _It:
    __slots__ = ("id", "kind", "x", "y", "x2", "y2", "sub", "text", "name", "tv", "ref", "num",
                 "pname", "etype", "hidden", "nc", "amb", "out", "sym", "new", "u0", "w", "cls")

    def __init__(self, kind, x, y, x2=None, y2=None, **kw):
        self.id = -1
        self.kind, self.x, self.y = kind, x, y
        self.x2 = x if x2 is None else x2
        self.y2 = y if y2 is None else y2
        for k in ("sub", "text", "name", "ref", "num", "pname", "etype", "out", "sym"):
            setattr(self, k, kw.get(k))
        self.u0 = kw.get("u0", False)  # a unit-0 pin (H-C10)
        self.tv = kw.get("tv", False)  # text with '${' (H-C8)
        self.hidden = kw.get("hidden", False)
        self.nc = kw.get("nc", False)
        self.amb = kw.get("amb", False)
        self.new = kw.get("new", False)
        self.w = kw.get("w", 0)  # stroke width, IU (H-C3)
        self.cls = kw.get("cls", ())  # Netclass values (H-C2)


_LINES = ("wire", "bus", "gline")


def _conn_points(it):
    if it.kind in ("wire", "bus", "be"):
        return ((it.x, it.y), (it.x2, it.y2))
    if it.kind == "gline":
        return ()
    return ((it.x, it.y),)


def _desc(it) -> str:
    k = it.kind
    if k == "pin":
        if it.sym is not None and it.sym.is_power:
            return f"power symbol {it.ref} (Value {it.sym.value!r}) pin at {_pt(it.x, it.y)}"
        extra = [w for w, f in (("hidden", it.hidden), ("no-connect type", it.nc)) if f]
        tail = f" ({', '.join(extra)})" if extra else ""
        return f"{it.ref}:{it.num} pin end at {_pt(it.x, it.y)}{tail}"
    if k == "label":
        return f"{it.sub} '{it.text}' at {_pt(it.x, it.y)}"
    if k == "junction":
        return f"junction at {_pt(it.x, it.y)}"
    if k == "nc":
        return f"no-connect flag at {_pt(it.x, it.y)}"
    if k == "sheetpin":
        return f"sheet pin '{it.text}' of sheet '{it.sub}' at {_pt(it.x, it.y)}"
    if k == "be":
        return f"bus entry {_pt(it.x, it.y)}-{_pt(it.x2, it.y2)}"
    name = {"wire": "wire", "bus": "bus", "gline": "graphic line"}[k]
    return f"{'new ' if it.new else ''}{name} {_pt(it.x, it.y)}-{_pt(it.x2, it.y2)}"


def _pdesc(x, y, it) -> str:
    if it.kind in ("wire", "bus", "be"):
        return f"end {_pt(x, y)} of {_desc(it)}"
    return _desc(it)


class _UF:
    __slots__ = ("p",)

    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        p = self.p
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


class _Coarse:
    """The coarse model: union-find, and per root its names [(item, text)] and unhandled items
    [(code, item)]."""

    __slots__ = ("uf", "names", "bad")

    def __init__(self, uf, names, bad):
        self.uf, self.names, self.bad = uf, names, bad

    def names_of(self, it) -> list:
        return self.names.get(self.uf.find(it.id), [])

    def bad_of(self, it) -> list:
        return self.bad.get(self.uf.find(it.id), [])


# ------------------------------------------------------------------------------------- model


class _Model:
    """Per-sheet index plus the narrow model C and the coarse model (THE TWO MODELS)."""

    def __init__(self, root, path=None):
        self.root, self.path = root, path
        self.items: list[_It] = []
        self.syms: list[_Sym] = []
        self.areas: list[list] = []  # rule area outlines (H-C2)
        self.pads: dict[tuple[str, str], list[_It]] = {}
        self.jumpers: list[list[_It]] = []
        self.placed: dict[str, set[int]] = {}  # units this sheet places per reference (H-C10)
        self.libs = _lib_index(root)
        self._hier = self._mine = None
        for ch in root.lists:
            self._take(ch)
        self.dirty = True

    # -- enumeration ----------------------------------------------------------------------
    def add(self, it: _It) -> _It:
        it.id = len(self.items)
        self.items.append(it)
        self.dirty = True
        return it

    @staticmethod
    def _xys(node):
        pts = node.find("pts")
        return [(_iu(p.atoms[1].text), _iu(p.atoms[2].text)) for p in pts.find_all("xy")] \
            if pts is not None else []

    def _take(self, ch):
        h = ch.head
        if h in ("wire", "bus", "polyline"):
            xys = self._xys(ch)
            if h == "polyline":
                if len(xys) == 2:  # a 2-point graphic polyline is an SCH_LINE on LAYER_NOTES
                    self.add(_It("gline", *xys[0], *xys[1]))
                return
            stroke = ch.find("stroke")
            w = _iu(_child_text(stroke, "width", "0")) if stroke is not None else 0
            for a, b in zip(xys, xys[1:]):
                self.add(_It(h, *a, *b, w=max(w, 0)))
        elif h == "rule_area":
            poly = ch.find("polyline")
            xys = self._xys(poly) if poly is not None else []
            if xys:
                self.areas.append(xys)
        elif h == "bus_entry":
            at, size = ch.find("at"), ch.find("size")
            x, y = _iu(at.atoms[1].text), _iu(at.atoms[2].text)
            dx, dy = (_iu(size.atoms[1].text), _iu(size.atoms[2].text)) if size is not None \
                else (0, 0)
            self.add(_It("be", x, y, x + dx, y + dy))
        elif h in ("junction", "no_connect"):
            at = ch.find("at")
            self.add(_It("junction" if h == "junction" else "nc",
                         _iu(at.atoms[1].text), _iu(at.atoms[2].text)))
        elif h in _LABEL_KINDS:
            at = ch.find("at")
            text = ch.atoms[1].text if len(ch.atoms) > 1 else ""
            directive = h in ("netclass_flag", "directive_label")
            props = [(q.atoms[1].text, q.atoms[2].text) for q in ch.find_all("property")
                     if len(q.atoms) > 2 and q.atoms[1].text != "Intersheetrefs"]  # H-C8
            self.add(_It("label", _iu(at.atoms[1].text), _iu(at.atoms[2].text),
                         sub=_LABEL_KINDS[h], text=text, name=None if directive else text,
                         tv=any("${" in v for v in [text, *(v for _, v in props)]),
                         cls=tuple(v for k, v in props if k == "Netclass" and v)))
        elif h == "sheet":
            sname = _shared._sheet_name_cst(ch) or "?"
            for p in ch.find_all("pin"):
                at = p.find("at")
                if at is None:
                    continue
                self.add(_It("sheetpin", _iu(at.atoms[1].text), _iu(at.atoms[2].text),
                             sub=sname, text=p.atoms[1].text if len(p.atoms) > 1 else ""))
        elif h == "symbol":
            self._take_symbol(ch)

    def _take_symbol(self, node):
        ref = _shared._sym_property_cst(node, "Reference") or "?"
        value = _shared._sym_property_cst(node, "Value") or ""
        key = _child_text(node, "lib_name") or _child_text(node, "lib_id")  # I01
        ls = self.libs.get(key)
        s = _Sym(node, ref, value, key, ls)
        self.syms.append(s)
        if ls is None:
            return
        if ls.find("extends") is not None:
            s.derived = True
            return
        pos, t = _sym_transform(node, ref)
        s.units = _units(node)
        s.amb = len(s.units) > 1
        s.style = _sch._sym_body_style_cst(node)
        s.is_power = ls.find("power") is not None
        s.lunits = {ids[0] for sub in ls.find_all("symbol")
                    if (ids := _sch._lib_unit_style(sub)) and ids[0] > 0} or {1}
        self.placed.setdefault(ref, set()).update(s.units)
        alts = {}
        for p in node.find_all("pin"):
            a = p.find("alternate")
            if a is not None and len(a.atoms) > 1 and len(p.atoms) > 1:
                alts[p.atoms[1].text] = a.atoms[1].text
        seen = set()
        for u in s.units:
            for sub in _sch._instance_units(ls, u, s.style):
                if id(sub) in seen:
                    continue
                seen.add(id(sub))
                u0 = (_sch._lib_unit_style(sub) or (1, 1))[0] == 0
                for p in sub.find_all("pin"):
                    self._take_pin(s, p, pos, t, alts, u0)
        if _child_text(ls, "duplicate_pin_numbers_are_jumpers") == "yes":
            s.jumper = True
            by_num: dict[str, list] = {}
            for it in s.pins:
                by_num.setdefault(it.num, []).append(it)
            self.jumpers.extend(g for g in by_num.values() if len(g) > 1)
        groups = ls.find("jumper_pin_groups")
        if groups is not None:
            s.jumper = s.jumper or bool(groups.lists)
            for g in groups.lists:
                nums = {a.text for a in g.atoms}
                members = [it for it in s.pins if it.num in nums]
                if len(members) > 1:
                    self.jumpers.append(members)

    def _take_pin(self, s, p, pos, t, alts, u0):
        at = p.find("at")
        if at is None:
            return
        lx, ly = _iu(at.atoms[1].text), _iu(at.atoms[2].text)
        ang = int(float(at.atoms[3].text)) if len(at.atoms) > 3 else 0
        if ang not in _TOWARD:
            raise _err("unloadable", f"{s.ref} has a pin at angle {ang}, which KiCad cannot load."
                       " Nothing was written. Remedy: fix that library pin's angle.")
        dx, dy = _apply(t, lx, -ly)  # lib Y is inverted on parse (parseXY(true))
        tx, ty = _TOWARD[ang]
        out = _apply(t, -tx, -ty)
        etype = p.atoms[1].text if len(p.atoms) > 1 else "unspecified"
        hidden = _pin_hidden(p)
        pname = _child_text(p, "name")
        num = _child_text(p, "number")
        alt = alts.get(num)
        if alt:
            for a in p.find_all("alternate"):
                if len(a.atoms) > 2 and a.atoms[1].text == alt:
                    etype = a.atoms[2].text
                    break
        name = None  # KiCad's rule (sch_pin.cpp:1621-1637), H-C6
        if etype == "power_in" and (hidden or s.is_power):
            name = s.value if s.is_power else pname
        it = self.add(_It("pin", pos[0] + dx, pos[1] + dy, ref=s.ref, num=num, pname=pname,
                          etype=etype, hidden=hidden, nc=etype == "no_connect", amb=s.amb,
                          out=out, sym=s, name=name, u0=u0, tv=name is not None and "${" in name))
        s.pins.append(it)
        self.pads.setdefault((s.ref, num), []).append(it)

    def check_loadable(self):
        """A derived or unresolved library symbol anywhere refuses the whole call."""
        for s in self.syms:
            if s.derived or s.lib is None:
                what = ("is derived ((extends ...)) and was never flattened into this file"
                        if s.derived else "this file's lib_symbols does not hold")
                raise _err(
                    "derived",
                    f"{s.ref} uses library symbol '{s.key}', which {what}, so its pins are"
                    " unknown here and nothing was written. Remedy: re-place"
                    f" {s.ref} with place_component from a non-derived library symbol.",
                )

    # -- geometry helpers -----------------------------------------------------------------
    def _point_entries(self):
        for it in self.items:
            for x, y in _conn_points(it):
                yield x, y, it

    def _lines(self, kinds=_LINES):
        for it in self.items:
            if it.kind in kinds:
                yield it

    def _near(self, x, y, r2, kinds):
        """Lines within sqrt(r2) IU of (x, y), via axis buckets (small radii only)."""
        r = math.isqrt(r2) + 1
        seen = set()
        for yy in range(y - r, y + r + 1):
            for lo, hi, ln in self._H.get(yy, ()):
                if ln.kind in kinds and lo - r <= x <= hi + r and ln.id not in seen \
                        and _within(x, y, ln.x, ln.y, ln.x2, ln.y2, r2):
                    seen.add(ln.id)
                    yield ln
        for xx in range(x - r, x + r + 1):
            for lo, hi, ln in self._V.get(xx, ()):
                if ln.kind in kinds and lo - r <= y <= hi + r and ln.id not in seen \
                        and _within(x, y, ln.x, ln.y, ln.x2, ln.y2, r2):
                    seen.add(ln.id)
                    yield ln
        for ln in self._D:
            if ln.kind in kinds and ln.id not in seen \
                    and min(ln.x, ln.x2) - r <= x <= max(ln.x, ln.x2) + r \
                    and min(ln.y, ln.y2) - r <= y <= max(ln.y, ln.y2) + r \
                    and _within(x, y, ln.x, ln.y, ln.x2, ln.y2, r2):
                seen.add(ln.id)
                yield ln

    @staticmethod
    def _overlaps(lines):
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
            for i in range(len(dg)):
                a = dg[i]
                ax, ay = a.x2 - a.x, a.y2 - a.y
                for b in dg[i + 1:]:
                    bx, by = b.x2 - b.x, b.y2 - b.y
                    if ax * by - ay * bx != 0:
                        continue
                    if (b.x - a.x) * ay - (b.y - a.y) * ax != 0:
                        continue
                    l2 = ax * ax + ay * ay
                    t1 = (b.x - a.x) * ax + (b.y - a.y) * ay
                    t2 = (b.x2 - a.x) * ax + (b.y2 - a.y) * ay
                    if min(l2, max(t1, t2)) - max(0, min(t1, t2)) > 0:
                        out.append((a, b))
        return out

    # -- component models -----------------------------------------------------------------
    def ensure(self):
        if self.dirty:
            self._build()

    def _certain_conductor(self, it, unreliable) -> bool:
        k = it.kind
        if k == "wire":
            return it.id not in unreliable
        if k == "pin":
            return not it.nc and not it.amb
        if k == "label":
            return it.name is not None  # the three named kinds
        return k == "junction"

    def _lone_wire(self, it):
        """C's label rule: the one reliable wire whose interior holds it alone, else None."""
        if len(self._pts[(it.x, it.y)]) == 1:
            exact = list(self._near(it.x, it.y, 0, _LINES))
            if len(exact) == 1 and exact[0].kind == "wire" and exact[0].id not in self.unreliable:
                return exact[0]
        return None

    def _build(self):
        items = self.items
        lines = [it for it in items if it.kind in _LINES]
        H: dict[int, list] = {}
        V: dict[int, list] = {}
        D: list = []
        for ln in lines:
            if ln.y == ln.y2:
                H.setdefault(ln.y, []).append((min(ln.x, ln.x2), max(ln.x, ln.x2), ln))
            elif ln.x == ln.x2:
                V.setdefault(ln.x, []).append((min(ln.y, ln.y2), max(ln.y, ln.y2), ln))
            else:
                D.append(ln)
        self._H, self._V, self._D = H, V, D

        pts: dict[tuple[int, int], list] = {}
        for it in items:
            for xy in _conn_points(it):
                pts.setdefault(xy, []).append(it)
        self._pts = pts

        # Lines kicad-cli 9 truncates (junction or bus-entry end on the interior, G04/G16) or
        # the GUI merges (collinear overlap, G06): never part of the certain model.
        unreliable: set[int] = set()
        for it in items:
            if it.kind == "junction":
                cps = ((it.x, it.y),)
            elif it.kind == "be":
                cps = ((it.x, it.y), (it.x2, it.y2))
            else:
                continue
            for x, y in cps:
                for ln in self._near(x, y, 0, ("wire", "bus")):
                    if (x, y) not in ((ln.x, ln.y), (ln.x2, ln.y2)):
                        unreliable.add(ln.id)
        for a, b in self._overlaps(lines):
            unreliable.add(a.id)
            unreliable.add(b.id)
        self.unreliable = unreliable

        C = _UF(len(items))
        for its in pts.values():
            cond = [it for it in its if self._certain_conductor(it, unreliable)]
            for it in cond[1:]:
                C.union(cond[0].id, it.id)
        for it in items:  # a named label alone at its anchor on one reliable wire's interior
            if it.kind == "label" and it.name is not None and (w := self._lone_wire(it)):
                C.union(it.id, w.id)
        self.C = C
        self.names = {"C": self._names(C)}
        self._coarse = None
        self._poss: dict = {}
        self._cert = None
        self.dirty = False

    def _names(self, uf):
        out: dict[int, dict] = {}
        for it in self.items:
            if it.name is not None and "${" not in it.name:
                out.setdefault(uf.find(it.id), {}).setdefault(it.name, []).append(it)
        return out

    def names_of(self, model: str, it) -> dict:
        """{name: [items]} of it's component in the narrow model (model is "C")."""
        return self.names[model].get(getattr(self, model).find(it.id), {})

    def members(self, it) -> list:
        cm = self.coarse()
        r = cm.uf.find(it.id)
        return [p for p in self.items if p.kind == "pin" and cm.uf.find(p.id) == r]

    # -- the coarse model -----------------------------------------------------------------
    def coarse(self) -> _Coarse:
        self.ensure()
        if self._coarse is None:
            self._coarse = self._build_coarse()
        return self._coarse

    def _build_coarse(self) -> _Coarse:
        uf = _UF(len(self.items))
        pts = []
        for it in self.items:
            if it.kind in _SEGS:
                pts += [(it.x, it.y, it), (it.x2, it.y2, it)]
            else:
                pts.append((it.x, it.y, it))
        grid: dict[tuple[int, int], list] = {}
        for p in pts:
            grid.setdefault((p[0] // TOL, p[1] // TOL), []).append(p)
        for (cx, cy), cell in grid.items():  # two points within r
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for u, v, b in grid.get((cx + dx, cy + dy), ()):
                        for x, y, a in cell:
                            if a.id < b.id and _d2(x, y, u, v) <= TOL2:
                                uf.union(a.id, b.id)
        cells: dict[tuple[int, int], list] = {}
        for ln in self.items:
            if ln.kind in _SEGS and (ln.x, ln.y) != (ln.x2, ln.y2):  # H-C18
                for gx in range((min(ln.x, ln.x2) - TOL) // _GRID,
                                (max(ln.x, ln.x2) + TOL) // _GRID + 1):
                    for gy in range((min(ln.y, ln.y2) - TOL) // _GRID,
                                    (max(ln.y, ln.y2) + TOL) // _GRID + 1):
                        cells.setdefault((gx, gy), []).append(ln)
        for x, y, a in pts:  # a point within r of a line
            for ln in cells.get((x // _GRID, y // _GRID), ()):
                if ln is not a and _within(x, y, ln.x, ln.y, ln.x2, ln.y2, TOL2):
                    uf.union(a.id, ln.id)
        for group in [*self.pads.values(), *self.jumpers]:
            for it in group[1:]:
                uf.union(group[0].id, it.id)
        first: dict[str, int] = {}
        for it in self.items:  # same-text names join, transitively
            if it.name is not None:
                uf.union(first.setdefault(it.name, it.id), it.id)
        names: dict[int, list] = {}
        bad: dict[int, list] = {}
        for it in self.items:
            r = uf.find(it.id)
            if it.name is not None:
                names.setdefault(r, []).append((it, it.name))
            for code in self._unhandled(it):
                bad.setdefault(r, []).append((code, it))
        return _Coarse(uf, names, bad)

    def _unhandled(self, it) -> list:
        k = it.kind
        if k in ("bus", "be", "sheetpin"):
            return [{"bus": "bus", "be": "bus_entry", "sheetpin": "sheet_pin"}[k]]
        out = ["text_var"] if it.tv else []
        if k == "pin":
            s = it.sym
            if s.jumper:
                out.append("jumper")
            if it.u0 and not s.lunits <= self.placed.get(s.ref, set()):
                out.append("unit0_unplaced")
            if s.amb:
                out.append("units_disagree")
        return out

    # -- netclass carriers (H-C2 to H-C5) -------------------------------------------------
    def _area_classes(self, r2):
        """[(outline, classes of the directives within sqrt(r2) of it)]."""
        dls = [it for it in self.items if it.kind == "label" and it.name is None and it.cls]
        return [(poly, {c for d in dls for c in d.cls
                        if any(_within(d.x, d.y, *a, *b, r2) for a, b in _edges(poly))})
                for poly in self.areas]

    def _touches(self, it, poly) -> bool:
        if it.kind in _SEGS:
            return _seg_poly(it.x, it.y, it.x2, it.y2, poly, (TOL + it.w // 2) ** 2)
        return _near_poly(it.x, it.y, poly, TOL2)

    def poss_classes(self, its) -> set:
        """H-C3: classes the items possibly carry (labels and touched rule areas)."""
        out = {c for it in its for c in it.cls}
        for poly, cls in self._area_classes(TOL2):
            if cls and any(self._touches(it, poly) for it in its):
                out |= cls
        return out

    def _comp_poss(self, it) -> set:
        cm = self.coarse()
        r = cm.uf.find(it.id)
        if r not in self._poss:
            self._poss[r] = self.poss_classes([x for x in self.items if cm.uf.find(x.id) == r])
        return self._poss[r]

    def cert_classes(self, roots) -> set:
        """H-C4: classes the narrow components with these C roots certainly carry."""
        if self._cert is None:
            cert: dict[int, set] = {}
            unrel = self.unreliable
            for it in self.items:
                if it.kind != "label" or not it.cls:
                    continue
                if it.name is not None:
                    tgt = it
                else:
                    at = [x for x in self._pts[(it.x, it.y)] if self._certain_conductor(x, unrel)]
                    tgt = at[0] if at else self._lone_wire(it)
                if tgt is not None:
                    cert.setdefault(self.C.find(tgt.id), set()).update(it.cls)
            for poly, cls in self._area_classes(CERT2):
                for it in self.items if cls else ():
                    if (it.kind == "pin" and it.hidden) or not self._certain_conductor(it, unrel) \
                            or it.kind == "junction":
                        continue
                    if any(_near_poly(x, y, poly, 0) for x, y in _conn_points(it)):
                        cert.setdefault(self.C.find(it.id), set()).update(cls)
            self._cert = cert
        out: set = set()
        for r in roots:
            out |= self._cert.get(r, set())
        return {c for c in out if "${" not in c}

    def class_block(self, pins, pts, segs, N=None):
        """H-C5 for a candidate: None if it passes, else the reason (str)."""
        if not self.areas and not any(it.cls for it in self.items):
            return None
        self.ensure()
        parts = [(self._comp_poss(p), self.cert_classes({self.C.find(p.id)}), _desc(p))
                 for p in pins]
        new = [_It("wire", *a, *b) for a, b in segs] + [_It("label", *p) for p in pts]
        g = self.poss_classes(new)
        g |= {c for it in self.items if it.cls for c in it.cls
              if any(_within(it.x, it.y, n.x, n.y, n.x2, n.y2, TOL2) for n in new)}
        touched = set().union(g, *(ps for ps, _, _ in parts))
        if not touched:
            return None
        named = [it for it in self.items if it.name == N] if N is not None else []
        if named:
            parts.append((self._comp_poss(named[0]),
                          self.cert_classes({self.C.find(it.id) for it in named}), f"net '{N}'"))
        S = parts[0][1]
        if all(ps == S and cs == S for ps, cs, _ in parts) and g <= S:
            return None
        shown = "; ".join(f"{d} possibly {sorted(ps)}, certainly {sorted(cs)}"
                          for ps, cs, d in parts)
        return (f"net classes {sorted(touched)} are touched and not certainly the same on every"
                f" part being joined ({shown}; the new geometry possibly {sorted(g)})")

    # -- duplicate references (H-C1) ------------------------------------------------------
    def _hier_records(self):
        """H-C1: records of every placed symbol on the other sheets of the hierarchy."""
        if self._hier is not None:
            return self._hier
        here = Path(self.path).resolve() if self.path else None
        start = Path(_shared._find_root_schematic(self.path) or self.path) if self.path else None
        out, seen, todo = [], set(), [start] if start else []
        while todo:
            f = todo.pop()
            rf = f.resolve()
            if rf in seen or not f.exists():  # KiCad loads a missing sheet file empty
                continue
            seen.add(rf)
            try:
                root = self.root if rf == here else _cst.parse(f.read_bytes()).lists[0]
            except Exception as e:  # noqa: BLE001
                raise _Refuse("dup_ref", f"sheet {f} could not be read ({type(e).__name__}), so"
                              " the reference cannot be checked for uniqueness. Remedy: fix or"
                              " remove that sheet.") from None
            if rf != here:
                libs = _lib_index(root)
                out += [_sym_record(s, libs) for s in root.find_all("symbol")]
            for sh in root.find_all("sheet"):
                name = _shared._sheet_file_cst(sh)
                if name:
                    cands = [f.parent / name, start.parent / name]
                    todo.append(next((c for c in cands if c.exists()), cands[0]))
        self._hier = out
        return out

    def check_unique(self, ref):
        """H-C1: raise _Refuse unless ref names one part (distinct units of one symbol)."""
        if self._mine is None:
            self._mine = [_sym_record(s.node, self.libs) for s in self.syms]
        mine = self._mine
        refs = set().union(*(r for (r, *_), s in zip(mine, self.syms) if s.ref == ref))
        group = [rec for rec in mine + self._hier_records() if rec[0] & refs]
        units = [rec[3] for rec in group]
        libs = {(k, _cst.serialize(ls) if ls is not None else None) for _, k, ls, _ in group}
        if len(libs) == 1 and group[0][2] is not None \
                and all(len(u) == 1 for u in units) and len({u[0] for u in units}) == len(units):
            return
        shown = "; ".join(f"{'/'.join(sorted(r))} ({k}, unit {','.join(map(str, u))})"
                          for r, k, _, u in group[:6])
        raise _Refuse(
            "dup_ref",
            f"reference {ref} is carried by {len(group)} placed symbols in this hierarchy that are"
            f" not distinct units of one part ({shown}); KiCad joins their pads by reference."
            " Remedy: annotate so every part has its own reference.",
        )

    # -- pins -----------------------------------------------------------------------------
    def resolve(self, ref: str, ptxt: str):
        """(number, copies) for ref:ptxt (M 5.1, I04), or raise _Refuse."""
        syms = [s for s in self.syms if s.ref == ref]
        if not syms:
            raise _Refuse(
                "resolve",
                f"{ref} is not placed on this sheet. Use list_schematic_components to see what"
                " is placed.",
            )
        for s in syms:
            if s.amb:
                raise _Refuse(
                    "units_disagree",
                    f"{ref}'s instance entries disagree on its unit"
                    f" ({', '.join(map(str, s.units))}): a reused sheet draws different units"
                    " per instance, so this sheet has no single pin geometry. Remedy: give"
                    f" every instance the same unit for {ref}, or wire it on a sheet used once.",
                )
        self.check_unique(ref)
        libs: list = []
        for s in syms:
            if all(s.lib is not lib for lib in libs):
                libs.append(s.lib)
        allpins = []
        for ls in libs:
            for sub in ls.find_all("symbol"):
                ids = _sch._lib_unit_style(sub)
                for p in sub.find_all("pin"):
                    allpins.append((_child_text(p, "number"), _child_text(p, "name"), ids))
        if ptxt in {n for n, _, _ in allpins}:
            num = ptxt
        else:
            cands = sorted({n for n, nm, _ in allpins if nm == ptxt})
            if not cands:
                raise _Refuse(
                    "resolve",
                    f"{ref} has no pin numbered or named '{ptxt}'. Use get_pin_positions to"
                    " list its pins.",
                )
            if len(cands) > 1:
                raise _Refuse(
                    "resolve",
                    f"pin name '{ptxt}' on {ref} matches several pads ({', '.join(cands)});"
                    " pass the pin number.",
                )
            num = cands[0]
        copies = self.pads.get((ref, num), [])
        if not copies:
            carriers = sorted({ids for n, _, ids in allpins if n == num and ids is not None})
            where = ", ".join(f"unit {u} body style {b}" for u, b in carriers) or "no unit"
            placed = ", ".join(f"unit {s.units[0]} body style {s.style}" for s in syms)
            raise _Refuse(
                "resolve",
                f"pin {num} of {ref} is drawn by {where}, but this sheet places {ref} as"
                f" {placed}. Place that unit, or wire the pin on the sheet that holds it.",
            )
        return num, copies

    # -- the touch rule (M 5.3) -----------------------------------------------------------
    def _rule1(self, P, tgt, at_p=("wire", "junction", "label", "sheetpin"),
               ends=("wire", "gline")):
        """Rule 1 (the label-on-pin candidate passes rule 4's narrower allowances): items allowed
        exactly at P are non-NC pins and the kinds in at_p; lines may end exactly at P only if
        their kind is in ends."""
        for x, y, it in self._point_entries():
            dd = _d2(x, y, *P)
            if dd > TOL2 or it is tgt:
                continue
            if dd == 0 and ((it.kind == "pin" and not it.nc) or it.kind in at_p):
                continue
            where = "at" if dd == 0 else "within 0.05 mm of"
            return f"{_pdesc(x, y, it)} {where} the pin end {_pt(*P)}"
        for ln in self._lines():
            if not _within(P[0], P[1], ln.x, ln.y, ln.x2, ln.y2, TOL2):
                continue
            if ln.kind in ends and P in ((ln.x, ln.y), (ln.x2, ln.y2)):
                continue
            return f"{_desc(ln)} passes through or within 0.05 mm of the pin end {_pt(*P)}"
        return None

    def _rule2(self, E, exclude=()):
        """Rule 2 for a new endpoint (also a new pin end of auto_place_decoupling_cap, I21)."""
        for x, y, it in self._point_entries():
            if it not in exclude and _d2(x, y, *E) <= TOL2:
                return f"{_pdesc(x, y, it)} at or within 0.05 mm of {_pt(*E)}"
        for ln in self._lines():
            if _within(E[0], E[1], ln.x, ln.y, ln.x2, ln.y2, TOL2):
                return f"{_desc(ln)} passes at or within 0.05 mm of {_pt(*E)}"
        return None

    def _rule3(self, A, B, pset):
        ends = {A, B} & pset
        for x, y, it in self._point_entries():
            if (x, y) in ends:
                continue  # exact coincidence at P was judged by rule 1
            if _within(x, y, A[0], A[1], B[0], B[1], TOL2):
                return f"{_pdesc(x, y, it)} lies on or within 0.05 mm of the new wire" \
                       f" {_pt(*A)}-{_pt(*B)}"
        for ln in self._lines(("wire", "bus")):
            if _overlap_axis(A, B, ln):
                return f"{_desc(ln)} overlaps the new wire {_pt(*A)}-{_pt(*B)} collinearly"
        if CROSS_BAN:
            for ln in self._lines(_SEGS):
                if _side(ln.x, ln.y, ln.x2, ln.y2, *A) * _side(ln.x, ln.y, ln.x2, ln.y2, *B) < 0 \
                        and _side(*A, *B, ln.x, ln.y) * _side(*A, *B, ln.x2, ln.y2) < 0:
                    return f"{_desc(ln)} crosses the new wire {_pt(*A)}-{_pt(*B)}"
        return None

    def touch(self, pins, free, segments):
        """None if new geometry passes rules 1-3, else the first obstacle (str).

        pins: [(P, target_pin_item)]; free: other new endpoints; segments: [(A, B)].
        """
        for P, tgt in pins:
            why = self._rule1(P, tgt)
            if why:
                return why
        for E in free:
            why = self._rule2(E)
            if why:
                return why
        pset = {P for P, _ in pins}
        for A, B in segments:
            why = self._rule3(A, B, pset)
            if why:
                return why
        return None


def _overlap_axis(A, B, ln) -> bool:
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


# ----------------------------------------------------------------------------- write planning


class _Plan:
    def __init__(self, model):
        self.m = model
        self.nodes: list[tuple[str, object]] = []
        self.lines: list[str] = []
        self.refused = False
        self.codes: list[str] = []
        self.wired = 0

    def ok(self, text):
        self.lines.append(text)

    def refuse(self, code, text):
        codes = [code] if isinstance(code, str) else list(code)
        self.refused = True
        self.codes += codes
        self.lines.append(f"REFUSED {_codes(codes)} {text}")

    def head(self, tool):
        return f"{_codes(self.codes)} {tool} refused the whole call; nothing was written.\n- "

    def add_wire(self, A, B):
        self.m.add(_It("wire", A[0], A[1], B[0], B[1], new=True))
        node = _sch._WIRE_TPL.copy()
        xys = node.find("pts").find_all("xy")
        for xy, (x, y) in zip(xys, (A, B)):
            xy.atoms[1].set_text(_mm(x))
            xy.atoms[2].set_text(_mm(y))
        node.find("uuid").atoms[1].set_text(_shared._gen_uuid())
        self.nodes.append(("wire", node))

    def add_label(self, P, text, rot):
        self.m.add(_It("label", P[0], P[1], sub="label", text=text, name=text, new=True))
        node = _sch._LABEL_TPL.copy()
        node.atoms[1].set_text(text)
        at = node.find("at")
        at.atoms[1].set_text(_mm(P[0]))
        at.atoms[2].set_text(_mm(P[1]))
        at.atoms[3].set_text(str(rot))
        node.find("uuid").atoms[1].set_text(_shared._gen_uuid())
        self.nodes.append(("label", node))

    def splice_into(self, root):
        for token, node in self.nodes:
            _sch._splice_sch_node(root, token, node)


def _check_net_name(text, what="label_text"):
    if not isinstance(text, str) or text == "":
        raise _err("validation", f"{what} must be a non-empty net name. Pass the net's name.")
    if "${" in text:
        raise _err(
            "validation",
            f"{what} {text!r} contains a text variable ('${{'): its resolved name, and so what"
            " it would join, cannot be known here. Use the literal net name.",
        )
    if _AUTO_NAME.match(text):
        raise _err(
            "validation",
            f"{what} {text!r} looks like a KiCad auto-generated net name; KiCad renames or"
            " splits nets that collide with one. Choose a real net name.",
        )
    if _BUS_RANGE.search(text) or any(
        c == "{" and (i == 0 or text[i - 1] not in "_^~") for i, c in enumerate(text)
    ):
        raise _err(
            "validation",
            f"{what} {text!r} is bus syntax; a bus label on a wire is a bus/net conflict."
            " Choose a plain net name.",
        )


def _check_direction(direction):
    if direction not in ("auto", "left", "right", "up", "down"):
        raise _err(
            "validation",
            f"direction must be one of auto, left, right, up, down; got {direction!r}.",
        )


def _check_stub(stub_length) -> int:
    if isinstance(stub_length, bool):
        raise _err("validation", "stub_length must be a number of mm greater than 0.")
    try:
        v = float(stub_length)
    except (TypeError, ValueError):
        raise _err("validation", "stub_length must be a number of mm greater than 0.") from None
    if not math.isfinite(v) or v <= 0:
        raise _err("validation",
                   f"stub_length must be a finite number of mm greater than 0; got {v}.")
    L = _kiround(v * 10000.0)
    if L < 1:
        raise _err("validation",
                   f"stub_length {v} mm rounds to 0 at KiCad's 0.0001 mm resolution.")
    return L


def _bad_text(bad) -> tuple:
    """Decision 2: (codes, text) for the unhandled items [(code, item)] reached."""
    codes = [c for c in _BAD if any(code == c for code, _ in bad)]
    shown = "; ".join(f"{_BAD[c][0]} ({_desc(next(it for k, it in bad if k == c))}): "
                      f"{_BAD[c][1]}" for c in codes)
    fix = "; ".join(_BAD[c][2] for c in codes)
    return codes, (f"its possible net reaches {shown}. Remedy: {fix}, or connect it by hand"
                   " with add_wires/add_label.")


def _names_text(entries) -> str:
    """Offending coarse name entries [(item, text)], grouped by text."""
    by_text: dict = {}
    for it, text in entries:
        by_text.setdefault(text, []).append(it)
    parts = []
    for text, its in list(by_text.items())[:4]:
        more = f" and {len(its) - 2} more" if len(its) > 2 else ""
        parts.append(f"{text!r} via {'; '.join(_desc(it) for it in its[:2])}{more}")
    if len(by_text) > 4:
        parts.append(f"{len(by_text) - 4} more names")
    return ", ".join(parts)


def _pad_conflict(m, copies, N):
    """Decisions 2 and 3 on the pad's coarse component (copies joined): (code(s), text) or
    None, after the no-connect refusals (H-C11)."""
    for c in copies:
        if c.nc:
            return "nc_type", ("it is a no-connect type pin; KiCad never connects it. Remedy:"
                               " pick another pin; this one cannot join any net.")
        for x, y, it in m._point_entries():
            if it.kind == "nc" and _d2(x, y, c.x, c.y) <= TOL2:
                return "nc_flag", (
                    f"it has a no-connect flag at {_pt(x, y)}. Remove the flag with"
                    " remove_no_connect first if the pin should be connected."
                )
    cm, c0 = m.coarse(), copies[0]
    bad = cm.bad_of(c0)
    if bad:
        return _bad_text(bad)
    other = [e for e in cm.names_of(c0) if e[1] != N]
    if other:
        return "names", (
            f"its possible net carries {_names_text(other)}; wiring it to '{N}' could merge"
            f" that net with '{N}'. Use the existing name, remove it first, or move apart the"
            " geometry that joins it."
        )
    return None


def _copy_state(m, c, N):
    nC = m.names_of("C", c)
    if N in nC:
        return "on", nC[N][0]
    return "free", None


def _wire_copy(plan, c, N, direction, L):
    m = plan.m
    d = c.out if direction == "auto" else _DIRS[direction]
    if d not in _LABEL_ROT:
        raise _Refuse("touch", f"pin end {_pt(c.x, c.y)} has no axis-aligned outward direction."
                      " Remedy: pass direction explicitly.")
    P = (c.x, c.y)
    E = (c.x + d[0] * L, c.y + d[1] * L)
    dname = _DIR_NAME[d]
    why_stub = m.touch([(P, c)], [E], [(P, E)])
    cls_stub = None if why_stub else m.class_block([c], [E], [(P, E)], N)
    if not why_stub and not cls_stub:
        plan.add_wire(P, E)
        plan.add_label(E, N, _LABEL_ROT[d])
        return f"stub {dname} {_mm(L)} mm from {_pt(*P)} to {_pt(*E)}, label '{N}' at its end"
    why_label = m._rule1(P, c, ("wire",), ("wire",))
    cls_label = None if why_label else m.class_block([c], [P], [], N)
    if not why_label and not cls_label:
        plan.add_label(P, N, _LABEL_ROT[d])
        return f"label '{N}' on the pin end {_pt(*P)} (stub {dname} blocked: {why_stub or cls_stub})"
    codes = ["touch" if w else "netclass" for w in (why_stub, why_label)]
    raise _Refuse(codes, f"stub {dname} blocked: {why_stub or cls_stub}; label on the pin"
                  f" blocked: {why_label or cls_label}. Remedy: move the obstacle or the part,"
                  " pass another direction, or draw this connection by hand.")


def _resolve_pin(m, pd):
    """(tag, ref, num, copies) for one pins entry, or raise _Refuse."""
    if not isinstance(pd, dict) or not isinstance(pd.get("reference"), str) \
            or not isinstance(pd.get("pin"), (str, int)) or isinstance(pd.get("pin"), bool):
        raise _Refuse("validation", f"{pd!r}: each pin must be {{'reference': str, 'pin': str}}.")
    ref, ptxt = pd["reference"], str(pd["pin"])
    tag = f"{ref}:{ptxt}"
    try:
        return (tag, ref, *m.resolve(ref, ptxt))
    except _Refuse as e:
        raise _Refuse(e.code, f"{tag}: {e}") from None


def _wire_request(plan, pins, N, direction, L, came_along, power_in):
    """The H decisions for one net on plan's model. Geometry goes into plan."""
    m = plan.m
    m.ensure()
    pads = []
    seen: dict[tuple[str, str], str] = {}
    for pd in pins:
        try:
            tag, ref, num, copies = _resolve_pin(m, pd)
        except _Refuse as e:
            plan.refuse(e.code, str(e))
            continue
        key = (ref, num)
        if key in seen:
            plan.ok(f"{tag}: same pad as {seen[key]}; counted once.")
            continue
        seen[key] = tag
        pads.append((tag, num, copies))

    ready = []
    requested = {(c.ref, c.num) for _, _, copies in pads for c in copies}
    for tag, num, copies in pads:
        mates = {
            f"{p.ref}:{p.num}"
            for p in m.members(copies[0])
            if not p.ref.startswith("#") and (p.ref, p.num) not in requested
        }
        states = [_copy_state(m, c, N) for c in copies]
        if all(state == "on" for state, _ in states):  # decision 1: no-op before any refusal
            came_along.setdefault(tag, mates)
            plan.ok(f"{tag}: " + "; ".join(
                (f"copy at {_pt(c.x, c.y)}: " if len(copies) > 1 else "")
                + f"already on '{N}' via {_desc(via)}; unchanged"
                for c, (_, via) in zip(copies, states)))
            continue
        why = _pad_conflict(m, copies, N)
        if why:
            plan.refuse(why[0], f"{tag}: {why[1]}")
        else:
            hint = next((it for it, t in m.coarse().names_of(copies[0]) if t == N), None)
            ready.append((tag, num, copies, hint))
            came_along.setdefault(tag, mates)

    for tag, num, copies, hint in ready:
        parts = []
        failed = None
        for c in copies:
            m.ensure()
            where = f"copy at {_pt(c.x, c.y)}: " if len(copies) > 1 else ""
            state, via = _copy_state(m, c, N)
            if state == "on":
                parts.append(f"{where}already on '{N}' via {_desc(via)}; unchanged")
                continue
            try:
                done = _wire_copy(plan, c, N, direction, L)
                if hint is not None:  # decision 4: the narrow model missed what coarse sees
                    done += (f" (the coarse model already reached '{N}' via {_desc(hint)}, the"
                             " narrow model did not; wired explicitly)")
                parts.append(where + done)
                plan.wired += 1
                if c.etype == "power_in":
                    power_in.append(tag)
            except _Refuse as e:
                failed = (e.code, f"{where}{e}")
                break
        if failed:
            plan.refuse(failed[0], f"{tag}: {failed[1]}")
        else:
            plan.ok(f"{tag}: " + "; ".join(parts))


def _same_name_sources(m, N) -> list[str]:
    return [_desc(it) for it in m.items if it.name == N and not it.new]


# --------------------------------------------------------------------------------- the tools


@_coded
def wire_pins_to_net(
    pins: list,
    label_text: str,
    direction: str = "auto",
    stub_length: float = 2.54,
    auto_pwr_flag: bool = True,
    schematic_path: str = SCH_PATH,
) -> str:
    """Put every listed pin on net label_text. All-or-nothing, one write."""
    _check_net_name(label_text)
    _check_direction(direction)
    L = _check_stub(stub_length)
    if not pins:
        return f"Wired 0 pins to '{label_text}'."
    tree, root, *_ = _sch._open_sch_cst(schematic_path)
    m = _Model(root, schematic_path)
    m.check_loadable()
    m.ensure()
    joins = _same_name_sources(m, label_text)
    plan = _Plan(m)
    came_along: dict[str, set] = {}
    power_in: list[str] = []
    _wire_request(plan, pins, label_text, direction, L, came_along, power_in)
    if plan.refused:
        raise ToolError(plan.head("wire_pins_to_net") + "\n- ".join(plan.lines))
    if not plan.nodes:
        return f"No change: every pin is already on '{label_text}'.\n- " + "\n- ".join(plan.lines)
    plan.splice_into(root)
    _shared._atomic_write(schematic_path, _cst.serialize(tree))
    msg = [f"Wired {plan.wired} pin end(s) to '{label_text}' in one write."]
    msg += [f"- {line}" for line in plan.lines]
    if joins:
        msg.append(f"'{label_text}' joins on this sheet: " + "; ".join(joins) + ".")
    else:
        msg.append(f"'{label_text}' is a new local net on this sheet.")
    ports = [_desc(it) for it in m.items if it.kind == "sheetpin" and it.text == label_text]
    if ports:
        msg.append(
            "Note: " + "; ".join(ports) + " is a hierarchy port, not a name: KiCad joins it to"
            f" a label '{label_text}' only when another subgraph on this sheet carries"
            f" '{label_text}' as a secondary name, so do not rely on the name to reach it; wire"
            " to it explicitly if that was the intent."
        )
    mates = sorted(set().union(*came_along.values())) if came_along else []
    if mates:
        msg.append("Already connected to a wired pin, so now also on it: " + ", ".join(mates))
    if auto_pwr_flag and power_in:
        msg.append("auto_pwr_flag: not implemented in this prototype; no PWR_FLAG was placed.")
    return "\n".join(msg)


_CHEAD = "connect_pins refused; nothing was written."


def _resolve_pad(m, ref, ptxt):
    try:
        return m.resolve(ref, ptxt)
    except _Refuse as e:
        raise _err(e.code, f"{_CHEAD} {ref}:{ptxt}: {e}") from None


def _joined(m, copies_a, copies_b):
    """Decision 1: (True, a shared C name or None) when every copy pair shares a C component or
    a C name."""
    name = None
    for x in copies_a:
        for y in copies_b:
            if m.C.find(x.id) == m.C.find(y.id):
                continue
            common = sorted(set(m.names_of("C", x)) & set(m.names_of("C", y)))
            if not common:
                return False, None
            name = name or common[0]
    return True, name


def _one_copy(m, tag, num, copies):
    if len(copies) != 1:
        at = ", ".join(_pt(c.x, c.y) for c in copies)
        raise _err(
            "copies",
            f"{_CHEAD} {tag} is pad {num}, which has {len(copies)} placed copies on this sheet"
            f" ({at}). Use wire_pins_to_net, which wires every copy.",
        )
    c = copies[0]
    if c.nc:
        raise _err("nc_type", f"{_CHEAD} {tag} is a no-connect type pin; KiCad never connects"
                   " it. Remedy: pick another pin.")
    for x, y, it in m._point_entries():
        if it.kind == "nc" and _d2(x, y, c.x, c.y) <= TOL2:
            raise _err("nc_flag", f"{_CHEAD} {tag} has a no-connect flag at {_pt(x, y)}. Remove"
                       " it with remove_no_connect first.")
    return c


@_coded
def connect_pins(ref1: str, pin1: str, ref2: str, pin2: str, schematic_path: str = SCH_PATH) -> str:
    """Connect two pins with a wire that touches nothing else. One write."""
    tree, root, *_ = _sch._open_sch_cst(schematic_path)
    m = _Model(root, schematic_path)
    m.check_loadable()
    m.ensure()
    ta, tb = f"{ref1}:{pin1}", f"{ref2}:{pin2}"
    num_a, copies_a = _resolve_pad(m, ref1, str(pin1))
    num_b, copies_b = _resolve_pad(m, ref2, str(pin2))
    same, common = _joined(m, copies_a, copies_b)  # decision 1: no-op before any refusal
    if same:
        if (ref1, num_a) == (ref2, num_b):
            return f"No change: {ta} and {tb} are the same pin."
        if common is None:
            return f"No change: {ta} and {tb} are already connected."
        return f"No change: {ta} and {tb} are already on the same net '{common}'."
    a = _one_copy(m, ta, num_a, copies_a)
    b = _one_copy(m, tb, num_b, copies_b)
    cm = m.coarse()
    bad = cm.bad_of(a) + (cm.bad_of(b) if cm.uf.find(a.id) != cm.uf.find(b.id) else [])
    if bad:  # decision 2
        codes, text = _bad_text(bad)
        raise ToolError(f"{_codes(codes)} {_CHEAD} {ta} or {tb}: {text}")
    na, nb = cm.names_of(a), cm.names_of(b)
    if na and nb:  # decision 3 (C names do not intersect, or decision 1 had returned)
        raise _err(
            "names",
            f"{_CHEAD} {ta} is possibly on {_names_text(na)} and {tb} on {_names_text(nb)};"
            " connecting them could merge those nets. If that merge is intended, do it"
            " explicitly with add_wires/add_label.",
        )
    Pa, Pb = (a.x, a.y), (b.x, b.y)
    if Pa[0] == Pb[0] or Pa[1] == Pb[1]:
        cands = [("straight", [], [(Pa, Pb)])]
    else:
        ch = (Pb[0], Pa[1])
        cv = (Pa[0], Pb[1])
        cands = [
            ("L horizontal-first", [ch], [(Pa, ch), (ch, Pb)]),
            ("L vertical-first", [cv], [(Pa, cv), (cv, Pb)]),
        ]
    tried, codes = [], []
    for name, corners, segs in cands:
        why = m.touch([(Pa, a), (Pb, b)], corners, segs)
        codes.append("touch" if why else "netclass")
        why = why or m.class_block([a, b], corners, segs)
        if why is None:
            plan = _Plan(m)
            for A, B in segs:
                plan.add_wire(A, B)
            plan.splice_into(root)
            _shared._atomic_write(schematic_path, _cst.serialize(tree))
            pts = " -> ".join(_pt(*p) for p in [Pa, *corners, Pb])
            extra = f" ({'; '.join(tried)})" if tried else ""
            return (
                f"Connected {ta} -> {tb} via {len(segs)} wire segment"
                f"{'s' if len(segs) > 1 else ''} ({name}: {pts}); no label, no junction.{extra}"
            )
        tried.append(f"{name} blocked: {why}")
    raise _err(
        codes,
        f"{_CHEAD} Every route touches something else:\n- " + "\n- ".join(tried)
        + "\nUse wire_pins_to_net with one shared net name for both pins instead.",
    )


def _compose_place(root, page, lib_id, reference, value, x, y, rotation, symbol_lib_path,
                   schematic_path, project_path):
    """place_component's node construction on an in-memory tree (exact lib entry names)."""
    if not isinstance(reference, str) or not _sch._VALID_REF_RE.match(reference):
        raise ToolError(
            f"'{reference}' is not a valid KiCad reference designator. Must match pattern"
            " [A-Z]+[0-9]+[A-Z]* (e.g. 'C5')."
        )
    _shared._check_rotation(rotation)
    if any(_shared._sym_property_cst(s, "Reference") == reference for s in root.find_all("symbol")):
        raise ToolError(f"{reference} is already placed in this schematic.")
    _sch._bounds_check(x, y, *page)
    x, y = _shared._snap_grid(x), _shared._snap_grid(y)
    symbol_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    entry = symbol_name if symbol_lib_path else lib_id
    ls = _lib_index(root).get(entry)
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
            raise ToolError(
                f"'{lib_id}' is not in this schematic's lib_symbols; pass Library:Symbol or"
                " symbol_lib_path."
            )
        ls = _lib_index(root).get(entry)
        if ls is None:
            raise ToolError(f"could not load '{lib_id}' into lib_symbols.")
    if ls.find("extends") is not None:
        raise _err(
            "derived",
            f"'{lib_id}' is a derived symbol ((extends ...)); placing it unflattened makes a"
            " schematic KiCad cannot load. Nothing was written. Remedy: use its parent symbol.",
        )
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
    pin_nums = set()
    for unit_node in ls.find_all("symbol"):
        for pin in unit_node.find_all("pin"):
            num = _child_text(pin, "number", None)
            if num is not None:
                pin_nums.add(num)
    for pn in sorted(pin_nums):
        pnode = _sch._PIN_REF_TPL.copy()
        pnode.atoms[1].set_text(pn)
        pnode.find("uuid").atoms[1].set_text(_shared._gen_uuid())
        node.insert_before(instances, pnode)
    root_uuid = _shared._node_uuid(root)
    try:
        if project_path:
            project_name, sheet_path = _shared._resolve_hierarchy_path(
                project_path, schematic_path, root_uuid
            )
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
        sch_dir = Path(schematic_path).parent
        target_name = Path(schematic_path).name
        for pro_file in sch_dir.glob("*.kicad_pro"):
            parent = pro_file.with_suffix(".kicad_sch")
            if not parent.exists() or parent.resolve() == Path(schematic_path).resolve():
                continue
            proot = _cst.parse(parent.read_bytes()).lists[0]
            for s in proot.find_all("sheet"):
                if _shared._sheet_file_cst(s) == target_name:
                    extra = project.copy()
                    extra.atoms[1].set_text(pro_file.stem)
                    epath = extra.find("path")
                    epath.atoms[1].set_text(f"/{_shared._node_uuid(proot)}/{_shared._node_uuid(s)}")
                    epath.find("reference").atoms[1].set_text(reference)
                    instances.insert_after(project, extra)
                    break
            else:
                continue
            break
    _sch._splice_sch_node(root, "symbol", node)
    return node, sym_uuid, x, y


def _root_index(root, schematic_path, project_path, sym_uuid, reference, value):
    """The symbol_instances upsert place_component does, without writing.

    Applied to `root` when the schematic is its own root; otherwise returns
    (other_path, other_bytes) for the caller to write after the schematic.
    """
    root_path = _shared._resolve_root(schematic_path, project_path)
    if root_path is None:
        root_path = _shared._find_root_schematic(schematic_path)
    if root_path is not None:
        t2 = _cst.parse(_shared._read_kicad_bytes(root_path, "root schematic"))
        r2 = t2.lists[0]
        target = Path(schematic_path).name
        sheet_uuid = next(
            (_shared._node_uuid(s) for s in r2.find_all("sheet")
             if _shared._sheet_file_cst(s) == target),
            None,
        )
        if sheet_uuid is None:
            return None, None
        _shared._upsert_entry(r2, f"/{_shared._node_uuid(r2)}/{sheet_uuid}/{sym_uuid}",
                              reference, 1, value, "")
        return root_path, _cst.serialize(t2)
    if not Path(schematic_path).with_suffix(".kicad_pro").exists():
        return None, None
    _shared._upsert_entry(root, f"/{_shared._node_uuid(root)}/{sym_uuid}", reference, 1, value, "")
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
    """Place a capacitor and wire pin 1 to power_net, pin 2 to ground_net.

    One in-memory composition, one write per file, every file intact on refusal.
    """
    _check_net_name(power_net, "power_net")
    _check_net_name(ground_net, "ground_net")
    tree, root, page_w, page_h, page_name = _sch._open_sch_cst(schematic_path)
    node, sym_uuid, sx, sy = _compose_place(
        root, (page_w, page_h, page_name), lib_id, reference, value, x, y, rotation,
        symbol_lib_path, schematic_path, project_path,
    )
    m = _Model(root, schematic_path)
    m.check_loadable()
    m.ensure()
    new_pins = [it for it in m.items if it.kind == "pin" and it.sym is not None
                and it.sym.node is node]
    own = set(new_pins)
    for p in new_pins:
        why = m._rule2((p.x, p.y), own)
        if why:
            raise _err(
                "touch",
                f"auto_place_decoupling_cap refused; nothing was written. {reference} pin"
                f" {p.num} would land at {_pt(p.x, p.y)}, touching {why}. Choose another spot.",
            )
    plan = _Plan(m)
    came_along: dict[str, set] = {}
    power_in: list[str] = []
    L = _check_stub(2.54)
    _wire_request(plan, [{"reference": reference, "pin": "1"}], power_net, "auto", L,
                  came_along, power_in)
    _wire_request(plan, [{"reference": reference, "pin": "2"}], ground_net, "auto", L,
                  came_along, power_in)
    if plan.refused:
        raise ToolError(plan.head("auto_place_decoupling_cap") + "\n- ".join(plan.lines))
    plan.splice_into(root)
    other_path, other_bytes = _root_index(root, schematic_path, project_path, sym_uuid,
                                          reference, value)
    _shared._atomic_write(schematic_path, _cst.serialize(tree))
    if other_path is not None:
        _shared._atomic_write(other_path, other_bytes)
    head = f"Placed {reference} ({value}) at ({sx}, {sy}) | pin 1->{power_net} | pin 2->{ground_net}"
    return head + "\n- " + "\n- ".join(plan.lines)


def pin_positions(schematic_path: str, reference: str) -> dict:
    """{number: [(x_mm, y_mm, outward)]} for every drawn copy, from KiCad's transform."""
    _, root, *_ = _sch._open_sch_cst(schematic_path)
    m = _Model(root, schematic_path)
    out: dict[str, list] = {}
    for it in m.items:
        if it.kind == "pin" and it.ref == reference:
            out.setdefault(it.num, []).append((it.x / 10000, it.y / 10000, _DIR_NAME.get(it.out)))
    return out
