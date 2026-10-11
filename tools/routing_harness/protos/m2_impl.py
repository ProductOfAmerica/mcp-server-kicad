"""Design M2 for the coarse-model experiment: proto/mine_impl.py (design M) plus the two decision
changes that make it follow pressure-test-report.md section 5. The models (P0, P, C), the touch
rule, pin geometry and resolution, validation and the writes are mine_impl's, unchanged.

Scratch prototype for the routing pressure test, not repo code. It imports the repo from main
(or PT_REPO) read-only and copies or wraps the helpers it needs. Every refusal raises mcp
ToolError before any write, so a refused call leaves every file byte-identical; a successful
call writes each file it changes exactly once through the repo's _atomic_write.

M2 CHANGES (the only decision changes against mine_impl.py)
- M2-1 No-op before refusal. wire_pins_to_net: a pad whose every copy is on N in the certain
  model C is reported as already on N before _pad_conflict runs, so a no-connect flag, a port,
  an unknown name or a foreign name on its P component cannot refuse it. connect_pins: once both
  pins resolve, the call is a no-op when every copy of one pad shares a C component, or a C name,
  with every copy of the other. That test runs before the copies, no-connect and name refusals.
- M2-2 Proceed where only P0 puts the pin on N. wire_pins_to_net: a copy on N in P0 but not in C
  (mine_impl refused it as ambiguous) gets geometry like a free copy (outward stub, then label on
  the pin), provided _pad_conflict passed for its pad; its outcome line says so. connect_pins, the
  same change (report 5.5 step 4): pins joined only in P0 are routed unless both P sides carry
  names. Both named and sharing a name only in P is refused as [M:ambiguous]; other named pairs
  as [M:port], [M:unknown] or [M:names].
_Model.suspects is deleted with the ambiguous refusals that were its only users.

REASON CODES. A refusal message starts with the bracketed code of every distinct reason in it, in
order of first occurrence; each per-pin line of wire_pins_to_net repeats its own code. The human
text and a remedy follow.
  [M:validation] an argument is invalid, the file cannot be read, or the repo rejects a
                 place_component input (also the default for any uncoded ToolError)
  [M:unloadable] a placed symbol or a library pin at an angle KiCad cannot load
  [M:derived]    a derived library symbol on the sheet, or a target on an unresolved one
  [M:resolve]    reference or pin not found, a name matching several pads, or no copy of the pad
                 drawn on this sheet
  [M:units]      the target symbol's instance entries disagree on its unit
  [M:nc_type]    the pin's electrical type is no_connect
  [M:nc_flag]    a no-connect flag on the pin end
  [M:port]       a sheet pin on the pin's P component
  [M:unknown]    a text-variable name on the pin's P component
  [M:names]      a different name on the pin's P component (connect_pins: names on both sides)
  [M:ambiguous]  connect_pins: both sides named, and the name they share is only in P
  [M:copies]     connect_pins on a pad with several drawn copies
  [M:touch]      every candidate geometry fails the strict touch rule

Exported API (same shapes as the repo tools):
    wire_pins_to_net(pins, label_text, direction="auto", stub_length=2.54, auto_pwr_flag=True,
                     schematic_path=SCH_PATH)
    connect_pins(ref1, pin1, ref2, pin2, schematic_path=SCH_PATH)
    auto_place_decoupling_cap(lib_id, reference, value, x, y, power_net, ground_net, rotation=0,
                              symbol_lib_path="", schematic_path=SCH_PATH, project_path="")
Probe helper: pin_positions(schematic_path, reference) -> {number: [(x_mm, y_mm, outward)]}.

Design summary. Pins use KiCad's own transform (rotate, then mirror, integer IU). Each call
builds one per-sheet index from the CST and keeps three union-finds over it:
  P0  over-approximate component model (M 5.2): every exact point coincidence, a label joins
      every wire or bus within 2 IU, a junction or bus-entry end joins a line through it,
      collinear overlapping wires join;
  P   = P0 plus every copy of one pad and KiCad 10 jumper groups (used for refusals);
  C   under-approximate "certain" model: joins that hold in kicad-cli 9, the 9 GUI and 10
      alike (used only to decide no-ops; see I05).
New geometry must pass the touch rule (M 5.3) with a 0.05 mm tolerance. No junction is ever
written.

INTERPRETATIONS
- I01 Lib symbol resolution follows KiCad exactly: lib_name when present and non-empty, else
  lib_id, exact name match in lib_symbols (sourced: 9.0 sch_symbol.cpp:242-248
  GetSchSymbolLibraryName, 9.0 sch_screen.cpp:816-839). M's "lib_name first, then exact
  lib_id" is read as "lib_id when there is no lib_name": an unresolved lib_name does NOT fall
  back to lib_id, because KiCad does not, and such a symbol has no pins in KiCad. A target on
  one is refused; as an obstacle it contributes nothing (KiCad draws no pin ends for it).
- I02 Unit: the set of (unit N) values over the symbol's (instances (project (path ...)))
  entries; if that set is empty, the top-level (unit N), else 1. More than one distinct value
  makes the symbol unit-ambiguous: as a target it is refused; as an obstacle and name source it
  contributes the pins of every listed unit (over-approximation), and it is left out of C.
- I03 Derived lib symbols ((extends ...)) refuse the WHOLE call when any placed symbol on the
  sheet resolves to one, not only the target: such a file does not load in KiCad (digest S15,
  RX-18) and the symbol's pins cannot be enumerated for the touch rule.
- I04 Pin lookup: exact number over every pin of the resolved lib symbol (all units and body
  styles), else exact primary name (alternate names are not matched). A name matching pins
  with more than one distinct number is refused with the list. Copies are every pin drawn on
  this sheet by a placed symbol with that Reference property and that number (unit-0 pins of
  several placed units, duplicate numbers). A pad with no drawn copy here is refused with the
  unit/body-style hint.
- I05 No-op soundness (gap in M): M decides "already on N" on the over-approximate model, so
  the claim can be false in some reading (N reached only through a junction or bus-entry end
  on an unsplit wire, a collinear overlap, a bus, a bus entry, or a label stacked on another
  label). A copy counts as already on N only when N is on its C component. C joins: exact
  point coincidence among wire ends, non-NC pin ends of unit-unambiguous symbols, labels of
  all four kinds, junctions and sheet pins, on wires with no junction or bus-entry end on
  their interior and no collinear overlap; a label on a wire's interior only when exactly one
  line passes and no pin end, other label, sheet pin or NC flag shares the anchor (K9 rule,
  which implies K10), or when two or more such reliable wires pass. No buses, bus entries,
  pad-copy or jumper joins. Where N is on the P0 component but not the C one, the pin is
  REFUSED as ambiguous rather than reported as a no-op. (M2-2: it now gets geometry instead.)
- I06 Per-copy decision (gap in M): invariant clause 1 needs every placed copy on N, so M 5.4
  step 2's per-pad union is used for refusals only; each copy already (certainly) on N is left
  alone and every other copy gets geometry. A pad is "already on N" only when all copies are.
- I07 A foreign name that is transitively the same net as N (another component carries both)
  still refuses: M 5.4 says "names contain anything else: refuse"; no name closure is computed.
- I08 Name sources: label, global_label and hierarchical_label texts; power_in pins on a power
  symbol ((power), (power global), (power local)) named by the placed symbol's Value property;
  hidden power_in pins on other symbols named by the lib pin's primary name (sourced: 9.0.8
  sch_pin.cpp:1621-1637), with the type taken from a selected alternate (sch_pin.cpp:279-293).
  Directive labels (netclass_flag, directive_label) name nothing. A bus-syntax label text is a
  foreign name like any other. Any name containing '${' is unknown.
- I09 Ports: a sheet pin on the P component refuses wire_pins_to_net whatever label_text is
  (the correction supplied with the task). In connect_pins a sheet pin or a '${' name is an
  unknown name: it refuses when the other side carries any name or unknown, and is allowed
  when the other side is unnamed.
- I10 Tolerance: 0.05 mm = 500 IU, Euclidean point-point and point-segment distance, in every
  clause of 5.3. An item within tolerance of P but not exactly at P is refused as a near miss
  (rule 1's allowance covers exact coincidence only).
- I11 "Line" in rules 1, 2 and 4 means wire, bus or 2-point graphic polyline (polylines count
  for KiCad's two-or-more-lines label rule, sourced 9.0.8 connection_graph.cpp:1241-1262).
  Rule 3's collinear overlap covers wires and buses, as written: parallel within tolerance
  with a projected overlap of positive length. End-to-end touching is a point contact.
- I12 Allowed exactly at P (rule 1): other non-NC-type pin ends, wire ends, junctions, labels,
  sheet pins (graphic lines ending exactly at P are tolerated too). Refused at or near P: NC
  flags, NC-type pin ends, bus ends, bus-entry ends, any line passing through or within
  tolerance of P without ending exactly there. The label-on-pin candidate also refuses a
  junction, label or sheet pin at P and any line within tolerance except wires ending exactly
  at P (rule 4).
- I13 Point items for rules 2 and 3: pin ends of every type and part (hidden, NC-type, the
  target's own other pins and other copies), wire and bus ends, junctions, NC flags, both
  bus-entry ends, labels of all four kinds, sheet pins. Items this call already added are in
  the index (5.4 step 4), so two pins in one call cannot share a stub end, and a later copy
  that reaches N through an earlier pin's new geometry becomes a no-op.
- I14 Label rotation follows the stub direction (repo convention: right 0, up 90, left 180,
  down 270); the label-on-pin fallback uses the rotation of the stub it replaces.
- I15 auto_pwr_flag: M adds no PWR_FLAG in 5.4 and leaves the question to the user (section 9).
  The prototype accepts the parameter and never places a flag (the "delete it" option); the
  message says so when a wired pin is power_in.
- I16 Validation: label_text a non-empty str; '${' anywhere refused; auto names
  ^(Net|unconnected)-\\( refused; bus syntax = a '[..]' range or a '{' not preceded by '_', '^'
  or '~' (a superset of NET_SETTINGS::ParseBusVector/ParseBusGroup, sourced 9.0.8
  net_settings.cpp:1099-1420). direction in {auto, left, right, up, down}, exact. stub_length
  a finite number > 0 that is at least 1 IU after KiROUND. Whitespace is neither trimmed nor
  refused.
- I17 An empty pins list validates the arguments and returns without reading or writing
  (repo behaviour).
- I18 Pins are deduplicated by resolved pad (reference, number), so "1" and a name resolving
  to pin 1 count once.
- I19 Net-mates ("came along") are reported from the P component, excluding '#' references.
- I20 connect_pins: refused when either pad has zero or several placed copies here, when either
  pin is NC-type or has an NC flag at its end (M 5.5 is silent; an NC-type pin can never
  satisfy the request and rule 1 forbids the flag). Same pad, or one C component, or a name
  shared by both C components: no-op. One P0 component but not one C component, or a common
  name only on the P components: refused as ambiguous (I05). Both sides named (or unknown)
  with no common name: refused naming both. Otherwise straight if aligned, else L
  horizontal-first, then L vertical-first; first candidate passing 5.3 wins. (M2-1 moves the
  no-op ahead of the copies and no-connect refusals; M2-2 routes a pair joined only in P0 unless
  both sides are named.)
- I21 auto_place_decoupling_cap: the placement is part of the composition, so the new symbol's
  pin ends are new endpoints under rule 2 (nothing within tolerance, no line within tolerance);
  otherwise a cap pin landing on a foreign unnamed net would bring it into power_net. Lib
  entry: exact name lib_id (or the bare symbol name when symbol_lib_path is given, as the repo
  stores it), else copied in from the library; no bare-name cross-library match; derived
  entries refused. power_net == ground_net is not refused (the request implies that join).
  When the schematic is its own root the symbol_instances entry goes into the same in-memory
  tree, so every file is written once; a separate root file is written after the schematic.
- I22 Pad identity uses the placed symbol's Reference property, as the repo does, not the
  per-instance reference.
- I23 Possible-model capture is squared distance <= 4 IU^2 (a superset of KiCad's
  TestSegmentHit(accuracy 1)); the certain model uses exact containment only.
- I24 KiCad 10 extras in P: jumper_pin_groups and duplicate_pin_numbers_are_jumpers join pins;
  stacked pin numbers such as "[1-4]" are matched only verbatim.
- I25 A (mirror) token written before (at) is ignored, as KiCad's parser does ((at) replaces the
  transform, (mirror) composes onto it; sourced 9.0 parser.cpp:3071-3098).
- I26 Coordinates are written from integer IU with exact decimal formatting; a stub wire runs
  from the pin end to the stub end.
- I27 New stubs and labels are not checked against the page bounds (M is silent; the repo's
  wire_pins_to_net does not check them either).
- I28 "Build the index once per call and add new items per pin": the CST is walked once; after
  each accepted pin the new wire and label are appended to the item tables and the three
  union-finds are rebuilt from those tables (perf-14's rebuild-from-tables), not re-walked.
- I29 Check order: argument validation (reported alone), file read, loadability (I03), then
  every pin is resolved and name-checked, then geometry runs for the pins that passed; all
  per-pin refusals are collected and reported together with the other pins' would-be outcomes.
- I30 Name matching is exact and case-sensitive on the raw label or Value text (KiCad joins
  case variants never, digest N01); no escaping such as '{slash}' is normalised.
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


def _parse_interpretations(doc: str) -> list[str]:
    out: list[str] = []
    body = doc.split("\nINTERPRETATIONS\n", 1)[1]
    for line in body.splitlines():
        if line.startswith("- "):
            out.append(line[2:].strip())
        elif line.startswith("  ") and out:
            out[-1] += " " + line.strip()
    return out


INTERPRETATIONS = _parse_interpretations(__doc__)

SCH_PATH = _sch.SCH_PATH

TOL = 500  # 0.05 mm in IU: the touch-rule refusal tolerance (M 5.3)
TOL2 = TOL * TOL
CAP2 = 4  # possible-model capture for labels / junctions / bus-entry ends, IU^2

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


_TAG = "M"  # reason-code prefix


class _Refuse(Exception):
    """A per-pin refusal with its reason code; collected and reported, never escapes."""

    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


def _codes(codes) -> str:
    return " ".join(f"[{_TAG}:{c}]" for c in dict.fromkeys(codes))


def _err(code, text) -> ToolError:
    return ToolError(f"{_codes([code])} {text}")


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


def _units(sym) -> list[int]:
    units: set[int] = set()
    inst = sym.find("instances")
    if inst is not None:
        for proj in inst.find_all("project"):
            for path in proj.find_all("path"):
                u = path.find("unit")
                if u is not None and len(u.atoms) > 1:
                    try:
                        units.add(int(u.atoms[1].text))
                    except ValueError:
                        pass
    if not units:
        units.add(_sch._sym_unit_cst(sym))
    return sorted(units)


def _pin_hidden(p) -> bool:
    if any(a.text == "hide" for a in p.atoms[3:]):
        return True
    h = p.find("hide")
    return h is not None and (len(h.atoms) < 2 or h.atoms[1].text == "yes")


# ------------------------------------------------------------------------------------- items


class _Sym:
    __slots__ = ("node", "ref", "value", "key", "lib", "derived", "units", "amb", "style",
                 "is_power", "pins")

    def __init__(self, node, ref, value, key, lib):
        self.node, self.ref, self.value, self.key, self.lib = node, ref, value, key, lib
        self.derived = False
        self.units: list[int] = []
        self.amb = False
        self.style = 1
        self.is_power = False
        self.pins: list = []


class _It:
    __slots__ = ("id", "kind", "x", "y", "x2", "y2", "sub", "text", "name", "port", "ref",
                 "num", "pname", "etype", "hidden", "nc", "amb", "out", "sym", "new")

    def __init__(self, kind, x, y, x2=None, y2=None, **kw):
        self.id = -1
        self.kind, self.x, self.y = kind, x, y
        self.x2 = x if x2 is None else x2
        self.y2 = y if y2 is None else y2
        for k in ("sub", "text", "name", "ref", "num", "pname", "etype", "out", "sym"):
            setattr(self, k, kw.get(k))
        self.port = kw.get("port", False)
        self.hidden = kw.get("hidden", False)
        self.nc = kw.get("nc", False)
        self.amb = kw.get("amb", False)
        self.new = kw.get("new", False)


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

    def copy(self):
        u = _UF(0)
        u.p = list(self.p)
        return u


class _Names:
    __slots__ = ("known", "unknown", "ports")

    def __init__(self):
        self.known: dict[str, list] = {}
        self.unknown: list = []
        self.ports: list = []


_NO_NAMES = _Names()


# ------------------------------------------------------------------------------------- model


class _Model:
    """Per-sheet index plus the P0 / P / C component models (M 5.2, I05)."""

    def __init__(self, root):
        self.items: list[_It] = []
        self.libs: dict[str, object] = {}
        self.syms: list[_Sym] = []
        self.pads: dict[tuple[str, str], list[_It]] = {}
        self.jumpers: list[list[_It]] = []
        self.derived: list[_Sym] = []
        libs = root.find("lib_symbols")
        if libs is not None:
            for ls in libs.find_all("symbol"):
                if len(ls.atoms) > 1:
                    self.libs.setdefault(ls.atoms[1].text, ls)
        for ch in root.lists:
            self._take(ch)
        self.dirty = True

    # -- enumeration ----------------------------------------------------------------------
    def add(self, it: _It) -> _It:
        it.id = len(self.items)
        self.items.append(it)
        self.dirty = True
        return it

    def _take(self, ch):
        h = ch.head
        if h in ("wire", "bus", "polyline"):
            pts = ch.find("pts")
            xys = [(_iu(p.atoms[1].text), _iu(p.atoms[2].text)) for p in pts.find_all("xy")] \
                if pts is not None else []
            if h == "polyline":
                if len(xys) == 2:  # a 2-point graphic polyline is an SCH_LINE on LAYER_NOTES
                    self.add(_It("gline", *xys[0], *xys[1]))
                return
            for a, b in zip(xys, xys[1:]):
                self.add(_It(h, *a, *b))
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
            self.add(_It("label", _iu(at.atoms[1].text), _iu(at.atoms[2].text),
                         sub=_LABEL_KINDS[h], text=text, name=None if directive else text))
        elif h == "sheet":
            sname = _shared._sheet_name_cst(ch) or "?"
            for p in ch.find_all("pin"):
                at = p.find("at")
                if at is None:
                    continue
                self.add(_It("sheetpin", _iu(at.atoms[1].text), _iu(at.atoms[2].text),
                             sub=sname, text=p.atoms[1].text if len(p.atoms) > 1 else "",
                             port=True))
        elif h == "symbol":
            self._take_symbol(ch)

    def _take_symbol(self, node):
        ref = _shared._sym_property_cst(node, "Reference") or "?"
        value = _shared._sym_property_cst(node, "Value") or ""
        lib_id = _child_text(node, "lib_id")
        lib_name = _child_text(node, "lib_name")
        key = lib_name or lib_id  # I01: KiCad's GetSchSymbolLibraryName, no fallback
        ls = self.libs.get(key)
        s = _Sym(node, ref, value, key, ls)
        self.syms.append(s)
        if ls is None:
            return
        if ls.find("extends") is not None:
            s.derived = True
            self.derived.append(s)
            return
        pos, t = _sym_transform(node, ref)
        s.units = _units(node)
        s.amb = len(s.units) > 1
        s.style = _sch._sym_body_style_cst(node)
        s.is_power = ls.find("power") is not None
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
                for p in sub.find_all("pin"):
                    self._take_pin(s, p, pos, t, alts)
        jumpers = ls.find("duplicate_pin_numbers_are_jumpers")
        if jumpers is not None and _child_text(ls, "duplicate_pin_numbers_are_jumpers") == "yes":
            by_num: dict[str, list] = {}
            for it in s.pins:
                by_num.setdefault(it.num, []).append(it)
            self.jumpers.extend(g for g in by_num.values() if len(g) > 1)
        groups = ls.find("jumper_pin_groups")
        if groups is not None:
            for g in groups.lists:
                nums = {a.text for a in g.atoms}
                members = [it for it in s.pins if it.num in nums]
                if len(members) > 1:
                    self.jumpers.append(members)

    def _take_pin(self, s, p, pos, t, alts):
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
        name = None
        if etype == "power_in" and (hidden or s.is_power):
            name = s.value if s.is_power else pname
        it = self.add(_It("pin", pos[0] + dx, pos[1] + dy, ref=s.ref, num=num, pname=pname,
                          etype=etype, hidden=hidden, nc=etype == "no_connect", amb=s.amb,
                          out=out, sym=s, name=name))
        s.pins.append(it)
        self.pads.setdefault((s.ref, num), []).append(it)

    def check_loadable(self):
        if self.derived:
            s = self.derived[0]
            raise _err(
                "derived",
                f"{s.ref} uses library symbol '{s.key}', which is derived ((extends ...)) and"
                " was never flattened into this file. KiCad cannot load such a schematic and"
                " that symbol's pins are unknown here, so nothing was written. Re-place"
                f" {s.ref} from its parent symbol."
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
        return k in ("label", "junction", "sheetpin")

    def _blocked(self, lab) -> bool:
        """K9 UpdateDanglingState early exit: pin end, other label, sheet pin or NC flag at the
        anchor (sourced 9.0.8 sch_label.cpp:1089-1117)."""
        for it in self._pts.get((lab.x, lab.y), ()):
            if it is not lab and it.kind in ("pin", "label", "sheetpin", "nc"):
                return True
        return False

    def _build(self):
        items = self.items
        n = len(items)
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
        overlaps = self._overlaps(lines)
        for a, b in overlaps:
            unreliable.add(a.id)
            unreliable.add(b.id)
        self.unreliable = unreliable

        P0, C = _UF(n), _UF(n)
        for a, b in overlaps:
            P0.union(a.id, b.id)
        for its in pts.values():
            first = its[0].id
            for it in its[1:]:
                P0.union(first, it.id)
            cond = [it for it in its if self._certain_conductor(it, unreliable)]
            for it in cond[1:]:
                C.union(cond[0].id, it.id)
        for it in items:
            if it.kind == "label":
                for ln in self._near(it.x, it.y, CAP2, ("wire", "bus")):
                    P0.union(it.id, ln.id)
                exact = list(self._near(it.x, it.y, 0, _LINES))
                if exact and all(ln.kind == "wire" and ln.id not in unreliable for ln in exact):
                    if len(exact) >= 2:
                        for ln in exact:
                            C.union(it.id, ln.id)
                    elif not self._blocked(it):
                        C.union(it.id, exact[0].id)
            elif it.kind == "junction":
                for ln in self._near(it.x, it.y, CAP2, ("wire", "bus")):
                    P0.union(it.id, ln.id)
            elif it.kind == "be":
                for x, y in ((it.x, it.y), (it.x2, it.y2)):
                    for ln in self._near(x, y, CAP2, ("wire", "bus")):
                        P0.union(it.id, ln.id)
        P = P0.copy()
        for copies in self.pads.values():
            for it in copies[1:]:
                P.union(copies[0].id, it.id)
        for grp in self.jumpers:
            for it in grp[1:]:
                P.union(grp[0].id, it.id)
        self.P0, self.P, self.C = P0, P, C
        self.names = {"P0": self._names(P0), "P": self._names(P), "C": self._names(C)}
        self.dirty = False

    def _names(self, uf):
        out: dict[int, _Names] = {}
        for it in self.items:
            if it.name is None and not it.port:
                continue
            r = uf.find(it.id)
            d = out.get(r)
            if d is None:
                d = out[r] = _Names()
            if it.port:
                d.ports.append(it)
            elif "${" in it.name:
                d.unknown.append(it)
            else:
                d.known.setdefault(it.name, []).append(it)
        return out

    def names_of(self, model: str, it) -> _Names:
        uf = getattr(self, model)
        return self.names[model].get(uf.find(it.id), _NO_NAMES)

    def members(self, model: str, it) -> list:
        uf = getattr(self, model)
        r = uf.find(it.id)
        return [p for p in self.items if p.kind == "pin" and uf.find(p.id) == r]

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
            if s.lib is None:
                raise _Refuse(
                    "derived",
                    f"{ref} uses library symbol '{s.key}', which this file's lib_symbols does"
                    " not hold, so KiCad draws it without pins. Remedy: re-place"
                    f" {ref} with place_component so its library symbol is embedded.",
                )
            if s.amb:
                raise _Refuse(
                    "units",
                    f"{ref}'s instance entries disagree on its unit"
                    f" ({', '.join(map(str, s.units))}): a reused sheet draws different units"
                    " per instance, so this sheet has no single pin geometry. Remedy: give"
                    f" every instance the same unit for {ref}, or wire it on a sheet used once.",
                )
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
    @staticmethod
    def _joins_at_p(it) -> bool:
        if it.kind == "pin":
            return not it.nc
        return it.kind in ("wire", "junction", "label", "sheetpin")

    def _rule1(self, P, tgt):
        for x, y, it in self._point_entries():
            dd = _d2(x, y, *P)
            if dd > TOL2 or it is tgt:
                continue
            if dd == 0 and self._joins_at_p(it):
                continue
            where = "at" if dd == 0 else "within 0.05 mm of"
            return f"{_pdesc(x, y, it)} {where} the pin end {_pt(*P)}"
        for ln in self._lines():
            if not _within(P[0], P[1], ln.x, ln.y, ln.x2, ln.y2, TOL2):
                continue
            if ln.kind in ("wire", "gline") and P in ((ln.x, ln.y), (ln.x2, ln.y2)):
                continue
            return f"{_desc(ln)} passes through or within 0.05 mm of the pin end {_pt(*P)}"
        return None

    def _rule2(self, E):
        for x, y, it in self._point_entries():
            if _d2(x, y, *E) <= TOL2:
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

    def touch_label_on_pin(self, P, tgt):
        """Rules 1 and 4 for a label placed on the pin end itself."""
        for x, y, it in self._point_entries():
            dd = _d2(x, y, *P)
            if dd > TOL2 or it is tgt:
                continue
            if dd == 0 and ((it.kind == "pin" and not it.nc) or it.kind == "wire"):
                continue
            where = "at" if dd == 0 else "within 0.05 mm of"
            return f"{_pdesc(x, y, it)} {where} the pin end {_pt(*P)}"
        for ln in self._lines():
            if not _within(P[0], P[1], ln.x, ln.y, ln.x2, ln.y2, TOL2):
                continue
            if ln.kind == "wire" and P in ((ln.x, ln.y), (ln.x2, ln.y2)):
                continue
            return f"{_desc(ln)} passes through or within 0.05 mm of the pin end {_pt(*P)}"
        return None

    def touch_new_point(self, P, exclude):
        """Rule 2 for a new pin end (auto_place_decoupling_cap's placement, I21)."""
        for x, y, it in self._point_entries():
            if it in exclude:
                continue
            if _d2(x, y, *P) <= TOL2:
                return f"{_pdesc(x, y, it)} at or within 0.05 mm of {_pt(*P)}"
        for ln in self._lines():
            if _within(P[0], P[1], ln.x, ln.y, ln.x2, ln.y2, TOL2):
                return f"{_desc(ln)} passes at or within 0.05 mm of {_pt(*P)}"
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
        self.notes: list[str] = []
        self.wired = 0

    def ok(self, text):
        self.lines.append(text)

    def refuse(self, code, text):
        self.refused = True
        self.codes.append(code)
        self.lines.append(f"REFUSED {_codes([code])} {text}")

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


def _pad_conflict(m, copies, N):
    """M 5.4 step 2 refusals on the P component (pad copies joined): (code, text) or None."""
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
    names = m.names_of("P", copies[0])
    if names.ports:
        return "port", (
            f"its net reaches {_desc(names.ports[0])}, a hierarchy port: that net's name is"
            " decided across the hierarchy, so this sheet cannot tell whether a new name"
            " would merge two named nets. Remedy: find the name the child sheet gives that"
            f" port; if it is '{N}', label this net '{N}' yourself with add_label."
        )
    if names.unknown:
        it = names.unknown[0]
        return "unknown", (
            f"its net carries {_desc(it)}, a name with a text variable this tool cannot"
            " resolve, so it cannot tell whether the new name would merge two nets. Remedy:"
            " replace the text variable with the literal name."
        )
    foreign = sorted(k for k in names.known if k != N)
    if foreign:
        via = names.known[foreign[0]][0]
        shown = ", ".join(repr(f) for f in foreign)
        return "names", (
            f"it is already on {shown} (via {_desc(via)}); wiring it to '{N}' would merge"
            f" that net with '{N}'. Use the existing name, or remove it first."
        )
    return None


def _copy_state(m, c, N):
    nC = m.names_of("C", c)
    if N in nC.known:
        return "on", nC.known[N][0]
    n0 = m.names_of("P0", c)
    if N in n0.known:
        return "ambiguous", n0.known[N][0]
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
    if why_stub is None:
        plan.add_wire(P, E)
        plan.add_label(E, N, _LABEL_ROT[d])
        return f"stub {dname} {_mm(L)} mm from {_pt(*P)} to {_pt(*E)}, label '{N}' at its end"
    why_label = m.touch_label_on_pin(P, c)
    if why_label is None:
        plan.add_label(P, N, _LABEL_ROT[d])
        return f"label '{N}' on the pin end {_pt(*P)} (stub {dname} blocked: {why_stub})"
    raise _Refuse("touch", f"stub {dname} blocked: {why_stub}; label on the pin blocked:"
                  f" {why_label}. Remedy: move the obstacle or the part, pass another"
                  " direction, or draw this connection by hand.")


def _wire_request(plan, pins, N, direction, L, came_along, power_in):
    """M 5.4 for one net on plan's model, with M2-1 and M2-2. Geometry goes into plan."""
    m = plan.m
    m.ensure()
    pads = []
    seen: dict[tuple[str, str], str] = {}
    for pd in pins:
        if not isinstance(pd, dict) or not isinstance(pd.get("reference"), str) \
                or not isinstance(pd.get("pin"), (str, int)) or isinstance(pd.get("pin"), bool):
            plan.refuse("validation", f"{pd!r}: each pin must be {{'reference': str, 'pin': str}}.")
            continue
        ref, ptxt = pd["reference"], str(pd["pin"])
        tag = f"{ref}:{ptxt}"
        try:
            num, copies = m.resolve(ref, ptxt)
        except _Refuse as e:
            plan.refuse(e.code, f"{tag}: {e}")
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
            for p in m.members("P", copies[0])
            if not p.ref.startswith("#") and (p.ref, p.num) not in requested
        }
        states = [_copy_state(m, c, N) for c in copies]
        if all(state == "on" for state, _ in states):  # M2-1: no-op before any refusal
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
            ready.append((tag, num, copies))
            came_along.setdefault(tag, mates)

    for tag, num, copies in ready:
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
                if state == "ambiguous":  # M2-2: geometry instead of the ambiguous refusal
                    done += (f" (P0 already reached '{N}' via {_desc(via)}, C did not; wired"
                             " explicitly)")
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
    out = []
    for it in m.items:
        if it.name == N and not it.new:
            out.append(_desc(it))
    return out


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
    """Put every listed pin on net label_text (design M 5.4). All-or-nothing, one write."""
    _check_net_name(label_text)
    _check_direction(direction)
    L = _check_stub(stub_length)
    if not pins:
        return f"Wired 0 pins to '{label_text}'."
    tree, root, *_ = _sch._open_sch_cst(schematic_path)
    m = _Model(root)
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
            f" '{label_text}' as a secondary name (digest H1), so do not rely on the name to"
            " reach it; wire to it explicitly if that was the intent."
        )
    mates = sorted(set().union(*came_along.values())) if came_along else []
    if mates:
        msg.append("Already connected to a wired pin, so now also on it: " + ", ".join(mates))
    if auto_pwr_flag and power_in:
        msg.append(
            "auto_pwr_flag: not implemented in this prototype (design M leaves PWR_FLAG to the"
            " user); no PWR_FLAG was placed."
        )
    return "\n".join(msg)


_CHEAD = "connect_pins refused; nothing was written."


def _resolve_pad(m, ref, ptxt):
    try:
        return m.resolve(ref, ptxt)
    except _Refuse as e:
        raise _err(e.code, f"{_CHEAD} {ref}:{ptxt}: {e}") from None


def _joined(m, copies_a, copies_b):
    """M2-1: (True, a shared C name or None) when every copy pair shares a C component or name."""
    name = None
    for x in copies_a:
        for y in copies_b:
            if m.C.find(x.id) == m.C.find(y.id):
                continue
            common = sorted(set(m.names_of("C", x).known) & set(m.names_of("C", y).known))
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


def _names_text(n: _Names) -> str:
    parts = [repr(k) for k in sorted(n.known)]
    parts += [f"unknown name via {_desc(it)}" for it in n.unknown]
    parts += [f"port {_desc(it)}" for it in n.ports]
    return ", ".join(parts)


@_coded
def connect_pins(ref1: str, pin1: str, ref2: str, pin2: str, schematic_path: str = SCH_PATH) -> str:
    """Connect two pins with a wire that touches nothing else (design M 5.5). One write."""
    tree, root, *_ = _sch._open_sch_cst(schematic_path)
    m = _Model(root)
    m.check_loadable()
    m.ensure()
    ta, tb = f"{ref1}:{pin1}", f"{ref2}:{pin2}"
    num_a, copies_a = _resolve_pad(m, ref1, str(pin1))
    num_b, copies_b = _resolve_pad(m, ref2, str(pin2))
    same, common = _joined(m, copies_a, copies_b)  # M2-1: no-op before any refusal
    if same:
        if (ref1, num_a) == (ref2, num_b):
            return f"No change: {ta} and {tb} are the same pin."
        if common is None:
            return f"No change: {ta} and {tb} are already connected."
        return f"No change: {ta} and {tb} are already on the same net '{common}'."
    a = _one_copy(m, ta, num_a, copies_a)
    b = _one_copy(m, tb, num_b, copies_b)
    head = _CHEAD
    na, nb = m.names_of("P", a), m.names_of("P", b)  # M2-2: no refusal for P0-only joins
    named_a = bool(na.known or na.unknown or na.ports)
    named_b = bool(nb.known or nb.unknown or nb.ports)
    if named_a and named_b:
        if set(na.known) & set(nb.known):
            raise _err(
                "ambiguous",
                f"{head} {ta} and {tb} share a name only through connections KiCad versions"
                " read differently. Fix that geometry first, or use wire_pins_to_net with the"
                " shared name.",
            )
        code = "port" if na.ports or nb.ports else "unknown" if na.unknown or nb.unknown \
            else "names"
        raise _err(
            code,
            f"{head} {ta} is on {_names_text(na)} and {tb} is on {_names_text(nb)};"
            " connecting them would merge those nets. If that merge is intended, do it"
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
    tried = []
    for name, corners, segs in cands:
        why = m.touch([(Pa, a), (Pb, b)], corners, segs)
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
        "touch",
        f"{head} Every route touches something else:\n- " + "\n- ".join(tried)
        + "\nUse wire_pins_to_net with one shared net name for both pins instead.",
    )


def _exact_lib(root, name):
    libs = root.find("lib_symbols")
    for ls in libs.find_all("symbol") if libs is not None else []:
        if len(ls.atoms) > 1 and ls.atoms[1].text == name:
            return ls
    return None


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
            raise ToolError(
                f"'{lib_id}' is not in this schematic's lib_symbols; pass Library:Symbol or"
                " symbol_lib_path."
            )
        ls = _exact_lib(root, entry)
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
    """Place a capacitor and wire pin 1 to power_net, pin 2 to ground_net (M slice 4).

    One in-memory composition, one write per file, every file intact on refusal.
    """
    _check_net_name(power_net, "power_net")
    _check_net_name(ground_net, "ground_net")
    tree, root, page_w, page_h, page_name = _sch._open_sch_cst(schematic_path)
    node, sym_uuid, sx, sy = _compose_place(
        root, (page_w, page_h, page_name), lib_id, reference, value, x, y, rotation,
        symbol_lib_path, schematic_path, project_path,
    )
    m = _Model(root)
    m.check_loadable()
    m.ensure()
    new_pins = [it for it in m.items if it.kind == "pin" and it.sym is not None
                and it.sym.node is node]
    own = set(new_pins)
    for p in new_pins:
        why = m.touch_new_point((p.x, p.y), own)
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
    m = _Model(root)
    out: dict[str, list] = {}
    for it in m.items:
        if it.kind == "pin" and it.ref == reference:
            out.setdefault(it.num, []).append((it.x / 10000, it.y / 10000, _DIR_NAME.get(it.out)))
    return out
