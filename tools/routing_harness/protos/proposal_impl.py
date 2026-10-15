"""Reference implementation of routing proposal P, for probing with kicad-cli.

P is pr57work/routing-proposal.md (scratch, not durable). This module implements it AS
WRITTEN, including the parts the phase-1 digest says are wrong (repo pin positions, the outward
flip "mirrored or not", automatic junctions, no 1 IU label capture, sheet pins not being names),
so probes can measure P rather than a repaired P. Where P is silent or ambiguous, the reading most
favourable to P that a competent implementer would pick is used; every such reading is listed
below and exposed as the module-level list INTERPRETATIONS.

Exported API (same conventions as the repo tools: a message string on success, mcp ToolError on
refusal with every file byte-identical):

    wire_pins_to_net(pins, label_text, direction="auto", stub_length=2.54, auto_pwr_flag=True,
                     schematic_path=...)
    connect_pins(ref1, pin1, ref2, pin2, schematic_path=...)
    auto_place_decoupling_cap(lib_id, reference, value, x, y, power_net, ground_net, rotation=0,
                              symbol_lib_path="", schematic_path=..., project_path="")

The repo is imported from main (or PT_REPO) through sys.path and never modified; its helpers are
called, not copied, except the PWR_FLAG block of wire_pins_to_net, which is copied verbatim.

INTERPRETATIONS:
  I01 Names join by text. P says labels of every kind (local, global, hierarchical) join by text
      and that a power_in pin on a power symbol, or a hidden power_in pin, "names its net". All of
      these live in one namespace on the sheet: every two items carrying the same name are in one
      component, whatever their kinds (label + power Value, label + hidden pin, power + power).
  I02 Power names follow SCH_PIN::IsGlobalPower: electrical type power_in (the lib pin's type; a
      placed (alternate ...) is ignored) and (hidden, or on a power symbol). A power symbol names
      the net by the placed symbol's Value property, otherwise the pin by its own (name ...).
      "Power symbol" = the resolved lib symbol carries a (power ...) child in any spelling
      ((power), (power global), (power local)).
  I03 Directive labels (netclass_flag, directive_label) are nameless point items: they join by
      point sharing and, being labels, by "a label anywhere along a wire joins that wire".
  I04 Every geometric test is exact integer IU, KiROUND(mm * 1e4) half away from zero: point
      equality, point-on-closed-segment by integer cross product, proper crossing by strict
      orientation signs. No tolerance, no 1 IU label capture.
  I05 Point sharing (rule 1) covers exactly P's list: pin ends, wire ends, label anchors (all
      four kinds), junctions, sheet pins, bus-entry ends. NC flags and bus ends are NOT in it.
  I06 Rule 3 ("a label or a junction anywhere along a wire joins that wire") applies to wires
      only, closed segment (ends included), for labels of all four kinds and junctions.
  I07 A bus is one item joined to nothing (no rule in P joins a bus). A bus entry is one item
      registered at both ends, so it joins whatever shares either end point. An NC flag is one
      item joined to nothing. Each of the three still counts for touch, and a component holding
      any of them is not inert.
  I08 Sheet pins are point items, not names (P names only labels and power pins, and lists sheet
      pins separately from names in its "inert" definition). A net named only through a sheet
      pin is therefore "unnamed" for the decisions.
  I09 Pins: every root-level placed symbol; lib symbol via the repo's _find_lib_symbol_cst
      (lib_id with bare-name fallback, lib_name unused); unit from the top-level (unit N) via
      _sym_unit_cst; body style via _sym_body_style_cst; sub-symbols via _instance_units; hidden
      pins included (bare `hide` atom or (hide yes)); NC-type pins are ordinary pins; positions
      from the repo's unchanged _transform_pin_pos. A symbol whose lib symbol cannot be resolved
      contributes no pins (the repo's get_net_connections does the same).
  I10 Graphic polylines, text, text boxes, rule areas and images are ignored (P lists none).
  I11 Names compare raw text, case-sensitive; text variables (${...}) are not resolved.
  I12 Target pin: exactly the placed symbol and lib pin the repo's _get_pin_pos_cst resolves
      (first placed unit drawing it, name-or-number match in file order). Only that one copy is
      wired; other copies of a unit-0 pin are ordinary pins of the sheet.
  I13 Outward fix: when the target symbol's rotation is 90 or 270, mirrored or not, the repo's
      outward angle is turned by 180 degrees; position math is unchanged. Direction "auto" snaps
      that angle to the nearest multiple of 90 as today.
  I14 Touch, new wire segment S: every existing item with a connection point on closed S (pin
      ends incl. hidden ones and the target's own other pins, wire ends, label and directive
      anchors, junctions, sheet pins, bus-entry ends, NC flags, bus ends), plus every existing
      wire OR BUS that meets S anywhere except a proper crossing (both interiors, one point).
      So T contacts in either direction, L and end contacts, and collinear overlaps all count.
      The "plain crossing" exception is read as covering buses too (BUS-11: inert in KiCad).
  I15 Touch, new label at Q: every existing item with a connection point exactly at Q, plus
      every existing wire or bus whose closed segment contains Q.
  I16 Items created earlier in the same call are part of the rebuilt model (I19), so they are
      touched like any other item; the stub and label of the candidate under test are not.
  I17 Safe: every touched item's component is the target pin's own component (both pins' for
      connect_pins), or carries name N (wire_pins_to_net only), or is inert (no pins, no names,
      no sheet pin, bus, bus entry or NC flag). In a multi-pin call another target pin's
      component counts only once that pin has been wired (it then carries N); before that it is
      foreign, per the required reading "the pin's own component", although P's prose ("one of
      the nets being joined") would also admit it.
  I18 Candidates per pin: stub of 1x, 2x, 3x stub_length in the chosen direction (end point
      P + dir * KiROUND(k * stub_length * 1e4) IU, not grid-snapped, as today), label at the
      stub end with the direction's label rotation; then a label on the pin (same rotation)
      unless a wire or bus runs through the pin's point (the point lies strictly inside it; a
      wire ending at the pin does not block); the label must also pass the touch test. Else
      the pin is refused.
  I19 The model is rebuilt from the in-memory tree before every pin, so it sees the stubs and
      labels written for earlier pins of the call. The automatic junctions and the PWR_FLAG are
      added once after the last pin, as today, so the per-pin models do not see them.
  I20 Decision order per pin: the component's names contain N -> no-op; any other name ->
      refuse; an NC flag exactly at the pin point -> refuse; otherwise proceed and report the
      component's other pins ("brought along").
  I21 Multi-pin calls: every pin is evaluated and its outcome listed; any refusal refuses the
      whole call with nothing written; if every pin is a no-op nothing is written (no PWR_FLAG).
  I22 Automatic junctions: the repo's _auto_junctions_cst (float tolerance 0.01 mm, scans every
      wire including new ones, never splits) runs once on both endpoints of every new stub.
      A label-on-pin outcome adds no endpoints.
  I23 PWR_FLAG: the repo's block kept verbatim, fed only by pins wired in this call; its
      position is the chosen label point (the stub end, or the pin for a label-on-pin).
  I24 Input validation P does not mention, added because a competent implementer would and
      because it only removes noise: direction must be auto/left/right/up/down, stub_length
      finite and at least 1 IU, label_text non-empty. Pin-lookup ValueErrors become ToolError
      (as wire_pins_to_net does today; connect_pins today leaks ValueError).
  I25 connect_pins: an NC flag at either pin refuses (P's general decision list). "Both named
      differently" = both components carry names; since names join (I01), two distinct
      components never share one. No N exists, so the safe set is the two components plus
      inert ones. Aligned pins (equal x or equal y in IU) try only the straight wire; otherwise
      the horizontal-first L (corner (x2, y1)), then the vertical-first L (corner (x1, y2)).
      _auto_junctions_cst runs on both pin points and the chosen corner, as today.
  I26 connect_pins auto label 'Net-(ref1-pin1)' (local, at pin 1, rotation 0, as today) is added
      only if (a) neither component has a name, (b) its touch set (I15) lies within the two
      components or inert ones, and (c) no other component already carries that text: under
      P's own rule I01 a same-text label joins that component, which is touching another net.
      A failed (b) or (c) skips the label with a note; it never refuses the route.
  I27 auto_place_decoupling_cap snapshots the schematic and the file the repo's
      _root_instance_target names (the root schematic for a sub-sheet, or the sheet itself
      when it is a root with a .kicad_pro), then places with the repo's place_component and
      wires pin 1 'up' and pin 2 'down' through this module's wire_pins_to_net (two writes, as
      today). On any exception from placement or either wiring call it writes back, through
      _atomic_write, exactly the snapshot files whose bytes changed, and re-raises.
  I28 Messages: success says how many pins were wired and notes no-ops ("already on N"),
      longer stubs (with what blocked the shorter ones), labels on pins (with what blocked the
      stubs) and pins brought along; a refusal lists every pin's outcome and, for a geometric
      refusal, each candidate's first obstacle with its component.
"""

from __future__ import annotations

import math
import os
import sys
from collections import defaultdict
from pathlib import Path

import hpaths  # noqa: E402,F401  (repo under test on sys.path)

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

import mcp_server_kicad._cst as _cst  # noqa: E402
from mcp_server_kicad import _shared  # noqa: E402
from mcp_server_kicad import schematic as _sch  # noqa: E402
from mcp_server_kicad._cst import _fill_at  # noqa: E402
from mcp_server_kicad._shared import (  # noqa: E402
    SCH_PATH,
    _atomic_write,
    _gen_uuid,
    _node_uuid,
    _resolve_hierarchy_path,
    _sym_property_cst,
)
# Helpers frozen at 54160c1 (core/compat54.py): six are gone from the package since, and the two
# pin reads changed. This module measures proposal P against the pin reads of that commit.
from compat54 import (  # noqa: E402
    _ANGLE_TO_DIR,
    _DIR_OFFSETS,
    _PWR_FLAG_LIB_TPL,
    _PWR_FLAG_SYM_TPL,
    _auto_junctions_cst,
    _get_pin_pos_cst,
    _pin_electrical_types_cst,
    _pin_matches_cst,
    _transform_pin_pos,
)
from mcp_server_kicad.schematic import (  # noqa: E402
    _LABEL_TPL,
    _copy_system_lib_symbol_cst,
    _find_lib_symbol_cst,
    _find_sym_cst,
    _find_syms_cst,
    _instance_units,
    _node_xy,
    _open_sch_cst,
    _splice_lib_symbol_cst,
    _splice_sch_node,
    _splice_wire,
    _sym_body_style_cst,
    _sym_unit_cst,
)

__all__ = [
    "INTERPRETATIONS",
    "ToolError",
    "auto_place_decoupling_cap",
    "connect_pins",
    "wire_pins_to_net",
]


def _parse_interpretations(doc: str) -> list[str]:
    out: list[str] = []
    started = False
    for line in doc.splitlines():
        if line.strip() == "INTERPRETATIONS:":
            started = True
            continue
        if not started:
            continue
        s = line.strip()
        if not s:
            continue
        if len(s) > 3 and s[0] == "I" and s[1:3].isdigit() and s[3] == " ":
            out.append(s)
        elif out:
            out[-1] += " " + s
    return out


INTERPRETATIONS: list[str] = _parse_interpretations(__doc__ or "")

_LABEL_KINDS = ("label", "global_label", "hierarchical_label")
_DIRECTIVE_KINDS = ("netclass_flag", "directive_label")


# ---------------------------------------------------------------------------
# Integer geometry (I04)
# ---------------------------------------------------------------------------


def _iu(v) -> int:
    """KiROUND(mm * 1e4): KiCad's parser rounds half away from zero."""
    s = float(v) * 10000.0
    return int(s + 0.5) if s >= 0 else -int(-s + 0.5)


def _mm(iu: int) -> float:
    return iu / 10000.0


def _fmt(pt) -> str:
    return f"({_cst._num(_mm(pt[0]))}, {_cst._num(_mm(pt[1]))})"


def _orient(o, a, b) -> int:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _on_segment(p, a, b) -> bool:
    """p lies on the closed segment a-b (exact)."""
    if not (
        min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
    ):
        return False
    return _orient(a, b, p) == 0


def _proper_cross(a, b, c, d) -> bool:
    """Segments a-b and c-d cross at one point strictly inside both (a plain crossing)."""
    d1, d2 = _orient(a, b, c), _orient(a, b, d)
    d3, d4 = _orient(c, d, a), _orient(c, d, b)
    return ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4))


def _segments_meet(a, b, c, d) -> bool:
    if _proper_cross(a, b, c, d):
        return True
    return _on_segment(c, a, b) or _on_segment(d, a, b) or _on_segment(a, c, d) or _on_segment(
        b, c, d
    )


def _xy(node) -> tuple[int, int]:
    at = node.find("at")
    return _iu(at.atoms[1].text), _iu(at.atoms[2].text)


def _line_pts(node):
    pts = node.find("pts")
    xys = pts.find_all("xy") if pts is not None else []
    return [(_iu(p.atoms[1].text), _iu(p.atoms[2].text)) for p in xys]


def _sym_angle(sym) -> float:
    at = sym.find("at")
    return float(at.atoms[3].text) if len(at.atoms) > 3 else 0.0


def _pin_hidden(pin) -> bool:
    if any(a.text == "hide" for a in pin.atoms[1:]):
        return True
    return any(len(h.atoms) > 1 and h.atoms[1].text == "yes" for h in pin.find_all("hide"))


# ---------------------------------------------------------------------------
# P's connectivity model
# ---------------------------------------------------------------------------


class _Model:
    """P's per-sheet union-find model (I01-I11), rebuilt from the CST on demand."""

    def __init__(self, root):
        self.kind: list[str] = []
        self.data: list[dict] = []
        self.parent: list[int] = []
        self.share: dict[tuple[int, int], list[int]] = defaultdict(list)  # rule 1 (I05)
        self.at: dict[tuple[int, int], list[int]] = defaultdict(list)  # every point item (touch)
        self.lines: list[tuple[int, str, tuple, tuple]] = []  # wires and buses
        self.pin_key: dict[tuple[int, int], int] = {}
        self._comps: dict[int, dict] | None = None
        self._build(root)

    # -- union-find --------------------------------------------------------
    def _add(self, kind: str, **data) -> int:
        idx = len(self.kind)
        self.kind.append(kind)
        self.data.append(data)
        self.parent.append(idx)
        return idx

    def find(self, i: int) -> int:
        root = i
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[i] != root:
            self.parent[i], i = root, self.parent[i]
        return root

    def _union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def _point(self, idx: int, pt, share: bool) -> None:
        self.at[pt].append(idx)
        if share:
            self.share[pt].append(idx)

    # -- build -------------------------------------------------------------
    def _build(self, root) -> None:
        lib_cache: dict[str, object] = {}  # same nodes the repo lookup returns, looked up once
        for sym in root.find_all("symbol"):
            lib_id = sym.find("lib_id")
            if lib_id is None:
                continue
            lid = lib_id.atoms[1].text
            if lid not in lib_cache:
                lib_cache[lid] = _find_lib_symbol_cst(root, lid)
            lib_sym = lib_cache[lid]
            if lib_sym is None:
                continue  # I09
            is_power = lib_sym.find("power") is not None  # I02
            ref = _sym_property_cst(sym, "Reference") or "?"
            value = _sym_property_cst(sym, "Value") or ""
            at = sym.find("at")
            cx, cy = float(at.atoms[1].text), float(at.atoms[2].text)
            ang = _sym_angle(sym)
            m = sym.find("mirror")
            mir = m.atoms[1].text if m is not None else None
            for sub in _instance_units(lib_sym, _sym_unit_cst(sym), _sym_body_style_cst(sym)):
                for pin in sub.find_all("pin"):
                    pat = pin.find("at")
                    if pat is None:
                        continue
                    x, y, _ = _transform_pin_pos(
                        float(pat.atoms[1].text),
                        float(pat.atoms[2].text),
                        float(pat.atoms[3].text) if len(pat.atoms) > 3 else 0,
                        cx,
                        cy,
                        ang,
                        mir,
                    )
                    pt = (_iu(x), _iu(y))
                    etype = pin.atoms[1].text if len(pin.atoms) > 1 else ""
                    hidden = _pin_hidden(pin)
                    num = pin.find("number")
                    nam = pin.find("name")
                    number = num.atoms[1].text if num is not None and len(num.atoms) > 1 else ""
                    pname = nam.atoms[1].text if nam is not None and len(nam.atoms) > 1 else ""
                    power_name = None
                    if etype == "power_in" and (hidden or is_power):
                        power_name = value if is_power else pname
                    idx = self._add(
                        "pin",
                        pt=pt,
                        ref=ref,
                        number=number,
                        name=pname,
                        etype=etype,
                        hidden=hidden,
                        power_name=power_name,
                    )
                    self.pin_key[(id(sym), id(pin))] = idx
                    self._point(idx, pt, share=True)

        for token in ("wire", "bus"):
            for node in root.find_all(token):
                pts = _line_pts(node)
                if len(pts) < 2:
                    continue
                a, b = pts[0], pts[1]
                idx = self._add(token, a=a, b=b)
                self.lines.append((idx, token, a, b))
                for p in (a, b) if a != b else (a,):
                    self._point(idx, p, share=(token == "wire"))  # bus ends: I05

        for node in root.find_all("bus_entry"):
            at = node.find("at")
            size = node.find("size")
            a = (_iu(at.atoms[1].text), _iu(at.atoms[2].text))
            b = (a[0] + _iu(size.atoms[1].text), a[1] + _iu(size.atoms[2].text))
            idx = self._add("bus_entry", a=a, b=b)
            for p in (a, b) if a != b else (a,):
                self._point(idx, p, share=True)

        along: list[int] = []  # items that join any wire they lie on (rule 3)
        for node in root.find_all("junction"):
            pt = _xy(node)
            idx = self._add("junction", pt=pt)
            self._point(idx, pt, share=True)
            along.append(idx)
        for node in root.find_all("no_connect"):
            pt = _xy(node)
            idx = self._add("no_connect", pt=pt)
            self._point(idx, pt, share=False)  # I05/I07
        for token in _LABEL_KINDS:
            for node in root.find_all(token):
                pt = _xy(node)
                idx = self._add(token, pt=pt, text=node.atoms[1].text)
                self._point(idx, pt, share=True)
                along.append(idx)
        for token in _DIRECTIVE_KINDS:
            for node in root.find_all(token):
                pt = _xy(node)
                idx = self._add("directive", pt=pt)
                self._point(idx, pt, share=True)
                along.append(idx)
        for sheet in root.find_all("sheet"):
            for sp in sheet.find_all("pin"):
                if sp.find("at") is None:
                    continue
                pt = _xy(sp)
                idx = self._add("sheet_pin", pt=pt, text=sp.atoms[1].text)
                self._point(idx, pt, share=True)

        # Rule 1: items sharing a point connect.
        for idxs in self.share.values():
            for j in idxs[1:]:
                self._union(idxs[0], j)
        # Rule 3: a label (any kind) or a junction anywhere along a wire joins that wire.
        for idx in along:
            p = self.data[idx]["pt"]
            for lidx, token, a, b in self.lines:
                if token == "wire" and _on_segment(p, a, b):
                    self._union(idx, lidx)
        # Rules 5 and 6: names join by text (I01).
        by_name: dict[str, list[int]] = defaultdict(list)
        for idx, k in enumerate(self.kind):
            if k in _LABEL_KINDS:
                by_name[self.data[idx]["text"]].append(idx)
            elif k == "pin" and self.data[idx]["power_name"] is not None:
                by_name[self.data[idx]["power_name"]].append(idx)
        for idxs in by_name.values():
            for j in idxs[1:]:
                self._union(idxs[0], j)

    # -- components --------------------------------------------------------
    def comps(self) -> dict[int, dict]:
        if self._comps is None:
            comps: dict[int, dict] = {}
            for idx, k in enumerate(self.kind):
                info = comps.setdefault(
                    self.find(idx), {"pins": [], "names": set(), "flags": set()}
                )
                d = self.data[idx]
                if k == "pin":
                    info["pins"].append(idx)
                    if d["power_name"] is not None:
                        info["names"].add(d["power_name"])
                elif k in _LABEL_KINDS:
                    info["names"].add(d["text"])
                elif k == "sheet_pin":
                    info["flags"].add(f"sheet pin '{d['text']}'")
                elif k == "bus":
                    info["flags"].add("bus")
                elif k == "bus_entry":
                    info["flags"].add("bus entry")
                elif k == "no_connect":
                    info["flags"].add("no-connect flag")
            self._comps = comps
        return self._comps

    def comp(self, idx: int) -> dict:
        return self.comps()[self.find(idx)]

    def inert(self, root_idx: int) -> bool:
        info = self.comps()[root_idx]
        return not info["pins"] and not info["names"] and not info["flags"]

    # -- touch (I14, I15) --------------------------------------------------
    def touch_segment(self, a, b) -> list[int]:
        hits: list[int] = []
        for p, idxs in self.at.items():
            if _on_segment(p, a, b):
                hits.extend(idxs)
        for idx, _token, c, d in self.lines:
            if not _proper_cross(a, b, c, d) and _segments_meet(a, b, c, d):
                hits.append(idx)
        return hits

    def touch_label(self, q) -> list[int]:
        hits = list(self.at.get(q, ()))
        for idx, _token, c, d in self.lines:
            if _on_segment(q, c, d):
                hits.append(idx)
        return hits

    def runs_through(self, q) -> list[int]:
        """Wires or buses whose segment contains q strictly inside (not at an end)."""
        return [idx for idx, _t, c, d in self.lines if q not in (c, d) and _on_segment(q, c, d)]

    def first_block(self, hits, own_roots, name):
        """First touched item whose component is not own, not carrying *name*, not inert."""
        for idx in hits:
            r = self.find(idx)
            if r in own_roots:
                continue
            if name is not None and name in self.comps()[r]["names"]:
                continue
            if self.inert(r):
                continue
            return idx
        return None

    def no_connect_at(self, pt) -> bool:
        return any(self.kind[i] == "no_connect" for i in self.at.get(pt, ()))

    # -- descriptions --------------------------------------------------------
    def pin_label(self, idx: int) -> str:
        d = self.data[idx]
        return f"{d['ref']}:{d['number'] or d['name']}"

    def describe(self, idx: int) -> str:
        k, d = self.kind[idx], self.data[idx]
        if k == "pin":
            return f"pin {self.pin_label(idx)}{' (hidden)' if d['hidden'] else ''} at {_fmt(d['pt'])}"
        if k in ("wire", "bus"):
            return f"{k} {_fmt(d['a'])}-{_fmt(d['b'])}"
        if k == "bus_entry":
            return f"bus entry {_fmt(d['a'])}-{_fmt(d['b'])}"
        if k in _LABEL_KINDS:
            return f"{k.replace('_', ' ')} '{d['text']}' at {_fmt(d['pt'])}"
        if k == "sheet_pin":
            return f"sheet pin '{d['text']}' at {_fmt(d['pt'])}"
        if k == "directive":
            return f"directive label at {_fmt(d['pt'])}"
        if k == "no_connect":
            return f"no-connect flag at {_fmt(d['pt'])}"
        return f"{k} at {_fmt(d['pt'])}"

    def describe_comp(self, idx: int) -> str:
        info = self.comp(idx)
        parts = []
        if info["names"]:
            parts.append("named " + "/".join(f"'{n}'" for n in sorted(info["names"])))
        if info["pins"]:
            parts.append("pins " + ", ".join(sorted(self.pin_label(i) for i in info["pins"])))
        parts.extend(sorted(info["flags"]))
        return "; ".join(parts) or "inert"

    def obstacle(self, idx: int) -> str:
        return f"{self.describe(idx)} (on a net with {self.describe_comp(idx)})"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _target(root, ref: str, pin_name: str):
    """The (placed symbol, lib pin) _get_pin_pos_cst resolves (I12)."""
    for sym in _find_syms_cst(root, ref):
        lib_sym = _find_lib_symbol_cst(root, sym.find("lib_id").atoms[1].text)
        if lib_sym is None:
            return None
        for sub in _instance_units(lib_sym, _sym_unit_cst(sym), _sym_body_style_cst(sym)):
            for pin in sub.find_all("pin"):
                if _pin_matches_cst(pin, pin_name):
                    return sym, pin
    return None


def _resolve_pin(root, ref: str, pin_name: str, verb: str):
    try:
        x, y, outward = _get_pin_pos_cst(root, ref, pin_name)
    except ValueError as e:
        raise ToolError(f"Error {verb} {ref}:{pin_name}: {e}") from e
    tgt = _target(root, ref, pin_name)
    if tgt is None:  # unreachable when _get_pin_pos_cst succeeded
        raise ToolError(f"Error {verb} {ref}:{pin_name}: pin not resolved")
    return x, y, outward, tgt


def _model_pin(model: _Model, tgt, ref: str, pin_name: str) -> int:
    idx = model.pin_key.get((id(tgt[0]), id(tgt[1])))
    if idx is None:
        raise ToolError(f"{ref}:{pin_name} is not in the connectivity model")
    return idx


def _p_outward(outward: float, comp_angle: float) -> float:
    """P's outward fix (I13): flip for rotation 90/270, mirrored or not."""
    if round(comp_angle) % 360 in (90, 270):
        return (outward + 180) % 360
    return outward


def _add_label(root, text: str, pt, rot: float) -> None:
    node = _LABEL_TPL.copy()
    node.atoms[1].set_text(text)
    _fill_at(node, _mm(pt[0]), _mm(pt[1]), rot)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "label", node)


# ---------------------------------------------------------------------------
# wire_pins_to_net
# ---------------------------------------------------------------------------


def wire_pins_to_net(
    pins,
    label_text: str,
    direction: str = "auto",
    stub_length: float = 2.54,
    auto_pwr_flag: bool = True,
    schematic_path: str = SCH_PATH,
) -> str:
    """P's wire_pins_to_net: join each pin's net with net *label_text*, or refuse untouched."""
    if not pins:
        return f"Wired 0 pins to '{label_text}'."
    # I24: validation P does not mention.
    if direction != "auto" and direction not in _DIR_OFFSETS:
        raise ToolError(
            f"Unknown direction {direction!r}. Use auto, {', '.join(_DIR_OFFSETS)}. Nothing written."
        )
    try:
        stub_ok = math.isfinite(float(stub_length)) and _iu(stub_length) >= 1
    except (TypeError, ValueError):
        stub_ok = False
    if not stub_ok:
        raise ToolError(f"stub_length must be a positive length in mm, got {stub_length!r}.")
    if not label_text:
        raise ToolError("label_text must not be empty. Nothing written.")

    tree, root, *_ = _open_sch_cst(schematic_path)

    outcomes: list[tuple[str, str, str]] = []  # (pin, status, detail)
    stub_endpoints: list[tuple[float, float]] = []
    wired = 0
    first_power_in_pos = None
    has_power_out = False

    for pin_def in pins:
        ref = pin_def["reference"]
        pin_name = pin_def["pin"]
        tag = f"{ref}:{pin_name}"
        px, py, outward, tgt = _resolve_pin(root, ref, pin_name, "wiring")
        model = _Model(root)  # rebuilt for every pin (I19)
        tidx = _model_pin(model, tgt, ref, pin_name)
        own = model.find(tidx)
        info = model.comps()[own]
        P = (_iu(px), _iu(py))

        if label_text in info["names"]:  # I20
            outcomes.append((tag, "noop", f"already on '{label_text}'"))
            continue
        if info["names"]:
            names = "/".join(f"'{n}'" for n in sorted(info["names"]))
            outcomes.append(
                (
                    tag,
                    "refused",
                    f"already on net {names}; wiring it to '{label_text}' would merge two named nets",
                )
            )
            continue
        if model.no_connect_at(P):
            outcomes.append(
                (tag, "refused", "has a no-connect flag; remove it first (remove_no_connect)")
            )
            continue
        others = sorted(model.pin_label(i) for i in info["pins"] if i != tidx)

        if direction == "auto":
            out = _p_outward(outward, _sym_angle(tgt[0]))
            d = _ANGLE_TO_DIR[round(out / 90) * 90 % 360]
        else:
            d = direction
        dx, dy, rot = _DIR_OFFSETS[d]

        chosen = None
        blocked: list[str] = []
        for k in (1, 2, 3):
            length = _iu(k * float(stub_length))
            E = (P[0] + dx * length, P[1] + dy * length)
            hit = model.first_block(
                model.touch_segment(P, E) + model.touch_label(E), {own}, label_text
            )
            if hit is None:
                chosen = ("stub", k, E)
                break
            blocked.append(f"{_cst._num(_mm(length))} mm {d} stub: {model.obstacle(hit)}")
        if chosen is None:
            through = model.runs_through(P)
            if through:
                blocked.append(f"label on the pin: {model.describe(through[0])} runs through it")
            else:
                hit = model.first_block(model.touch_label(P), {own}, label_text)
                if hit is None:
                    chosen = ("label", 0, P)
                else:
                    blocked.append(f"label on the pin: {model.obstacle(hit)}")
        if chosen is None:
            outcomes.append((tag, "refused", "no safe geometry; " + "; ".join(blocked)))
            continue

        kind, k, Q = chosen
        if kind == "stub":
            _splice_wire(root, _mm(P[0]), _mm(P[1]), _mm(Q[0]), _mm(Q[1]))
            stub_endpoints.append((_mm(P[0]), _mm(P[1])))
            stub_endpoints.append((_mm(Q[0]), _mm(Q[1])))
        _add_label(root, label_text, Q, rot)
        wired += 1

        notes = []
        if kind == "stub" and k > 1:
            notes.append(f"used a {k}x stub ({_cst._num(_mm(_iu(k * float(stub_length))))} mm {d})")
            notes.append("shorter blocked by " + "; ".join(blocked))
        elif kind == "label":
            notes.append("labelled directly on the pin")
            notes.append("stubs blocked by " + "; ".join(blocked))
        if others:
            notes.append("brought along " + ", ".join(others) + " (already wired to it)")
        outcomes.append((tag, "wired", "; ".join(notes)))

        # Track pin electrical types for the auto PWR_FLAG (I23, repo logic).
        end_x, end_y = _mm(Q[0]), _mm(Q[1])
        if first_power_in_pos is None or not has_power_out:
            target = _find_sym_cst(root, ref)
            if target is not None:
                lib_sym = _find_lib_symbol_cst(root, target.find("lib_id").atoms[1].text)
                if lib_sym is not None:
                    for etype in _pin_electrical_types_cst(lib_sym, pin_name):
                        if etype == "power_in" and first_power_in_pos is None:
                            first_power_in_pos = (end_x, end_y)
                        if etype == "power_out":
                            has_power_out = True

    if any(status == "refused" for _, status, _ in outcomes):
        raise ToolError(
            f"Refused to wire to '{label_text}'; nothing was written. "
            + " | ".join(f"{t}: {s}{(' (' + d + ')') if d else ''}" for t, s, d in outcomes)
        )
    if wired == 0:
        return "No change: " + "; ".join(f"{t} {d}" for t, _, d in outcomes) + "."

    _auto_junctions_cst(root, stub_endpoints)  # I22
    _pwr_flag(root, schematic_path, label_text, auto_pwr_flag, first_power_in_pos, has_power_out)
    _atomic_write(schematic_path, _cst.serialize(tree))

    msg = f"Wired {wired} pin{'s' if wired != 1 else ''} to '{label_text}'."
    notes = [f"{t} {d}" if s == "noop" else f"{t}: {d}" for t, s, d in outcomes if d]
    if notes:
        msg += " Notes: " + " | ".join(notes)
    return msg


def _pwr_flag(root, schematic_path, label_text, auto_pwr_flag, first_power_in_pos, has_power_out):
    """The repo's auto PWR_FLAG block, verbatim apart from being a function (I23)."""
    tol = 0.1
    if auto_pwr_flag and first_power_in_pos is not None and not has_power_out:
        labels_xy = [
            _node_xy(lbl) for lbl in root.find_all("label") if lbl.atoms[1].text == label_text
        ]
        has_existing_flag = any(
            abs(lx - sx) < tol and abs(ly - sy) < tol
            for sym in root.find_all("symbol")
            if _sym_property_cst(sym, "Value") == "PWR_FLAG"
            for sx, sy in [_node_xy(sym)]
            for lx, ly in labels_xy
        )
        if not has_existing_flag:
            if _find_lib_symbol_cst(root, "power:PWR_FLAG") is None:
                if not _copy_system_lib_symbol_cst(root, "power", "PWR_FLAG"):
                    _splice_lib_symbol_cst(root, _PWR_FLAG_LIB_TPL.copy())
            existing_flg = {
                r
                for sym in root.find_all("symbol")
                for r in [_sym_property_cst(sym, "Reference")]
                if r is not None and r.startswith("#FLG")
            }
            n = 1
            while f"#FLG{n:02d}" in existing_flg:
                n += 1
            flg_ref = f"#FLG{n:02d}"
            fx, fy = first_power_in_pos
            node = _PWR_FLAG_SYM_TPL.copy()
            _fill_at(node, fx, fy)
            node.find("uuid").atoms[1].set_text(_gen_uuid())
            props = node.find_all("property")
            offsets = [round(fy - 3.81, 4), round(fy + 3.81, 4), fy, fy]
            for prop, py_off in zip(props, offsets):
                _fill_at(prop, fx, py_off)
            props[0].atoms[2].set_text(flg_ref)
            node.find("pin").find("uuid").atoms[1].set_text(_gen_uuid())
            root_uuid = _node_uuid(root)
            project_name = Path(schematic_path).stem
            sheet_path = f"/{root_uuid}"
            sch_dir = Path(schematic_path).parent
            pro_files = list(sch_dir.glob("*.kicad_pro"))
            if len(pro_files) == 1:
                pro = pro_files[0]
                project_name = pro.stem
                root_sch_path = pro.with_suffix(".kicad_sch")
                if root_sch_path.resolve() != Path(schematic_path).resolve():
                    try:
                        project_name, sheet_path = _resolve_hierarchy_path(
                            str(pro), schematic_path, root_uuid
                        )
                    except Exception:
                        pass
            project = node.find("instances").find("project")
            project.atoms[1].set_text(project_name)
            inst_path = project.find("path")
            inst_path.atoms[1].set_text(sheet_path)
            inst_path.find("reference").atoms[1].set_text(flg_ref)
            _splice_sch_node(root, "symbol", node)


# ---------------------------------------------------------------------------
# connect_pins
# ---------------------------------------------------------------------------


def connect_pins(
    ref1: str,
    pin1: str,
    ref2: str,
    pin2: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """P's connect_pins: join the two pins' nets with a straight or L route, or refuse."""
    tree, root, *_ = _open_sch_cst(schematic_path)
    x1, y1, _o1, t1 = _resolve_pin(root, ref1, pin1, "connecting")
    x2, y2, _o2, t2 = _resolve_pin(root, ref2, pin2, "connecting")
    model = _Model(root)
    ia, ib = _model_pin(model, t1, ref1, pin1), _model_pin(model, t2, ref2, pin2)
    ra, rb = model.find(ia), model.find(ib)
    A, B = (_iu(x1), _iu(y1)), (_iu(x2), _iu(y2))
    tag = f"{ref1}:{pin1} -> {ref2}:{pin2}"

    if ra == rb:
        return f"No change: {ref1}:{pin1} and {ref2}:{pin2} are already connected."
    for pt, label in ((A, f"{ref1}:{pin1}"), (B, f"{ref2}:{pin2}")):
        if model.no_connect_at(pt):  # I25
            raise ToolError(
                f"Refused {tag}: {label} has a no-connect flag; remove it first"
                " (remove_no_connect). Nothing was written."
            )
    na, nb = model.comps()[ra]["names"], model.comps()[rb]["names"]
    if na and nb:
        raise ToolError(
            f"Refused {tag}: {ref1}:{pin1} is on net {'/'.join(sorted(na))} and {ref2}:{pin2} is on"
            f" net {'/'.join(sorted(nb))}; connecting them would merge two named nets."
            " Nothing was written."
        )

    if A[0] == B[0] or A[1] == B[1]:
        candidates = [("straight", [A, B])]
    else:
        candidates = [
            ("horizontal-first L", [A, (B[0], A[1]), B]),
            ("vertical-first L", [A, (A[0], B[1]), B]),
        ]
    chosen = None
    blocked: list[str] = []
    for name, pts in candidates:
        hits: list[int] = []
        for a, b in zip(pts, pts[1:]):
            hits.extend(model.touch_segment(a, b))
        hit = model.first_block(hits, {ra, rb}, None)
        if hit is None:
            chosen = (name, pts)
            break
        blocked.append(f"{name}: {model.obstacle(hit)}")
    if chosen is None:
        raise ToolError(
            f"Refused {tag}: no safe route; " + "; ".join(blocked) + ". Nothing was written."
        )

    name, pts = chosen
    for a, b in zip(pts, pts[1:]):
        _splice_wire(root, _mm(a[0]), _mm(a[1]), _mm(b[0]), _mm(b[1]))
    new_points = [(_mm(A[0]), _mm(A[1])), (_mm(B[0]), _mm(B[1]))]
    if len(pts) == 3:
        new_points.append((_mm(pts[1][0]), _mm(pts[1][1])))
    _auto_junctions_cst(root, new_points)

    notes = []
    if blocked:
        notes.append(f"used the {name}; " + "; ".join(blocked))
    # I26: automatic Net-(ref1-pin1) label.
    text = f"Net-({ref1}-{pin1})"
    if not (na or nb):
        hit = model.first_block(model.touch_label(A), {ra, rb}, None)
        clash = [
            r for r, info in model.comps().items() if r not in (ra, rb) and text in info["names"]
        ]
        if hit is not None:
            notes.append(f"label '{text}' skipped: it would touch {model.obstacle(hit)}")
        elif clash:
            notes.append(f"label '{text}' skipped: another net already carries that name")
        else:
            _add_label(root, text, A, 0)

    _atomic_write(schematic_path, _cst.serialize(tree))
    n = len(pts) - 1
    msg = f"Connected {ref1}:{pin1} -> {ref2}:{pin2} via {n} wire segment{'s' if n > 1 else ''}"
    if notes:
        msg += ". Notes: " + " | ".join(notes)
    return msg


# ---------------------------------------------------------------------------
# auto_place_decoupling_cap
# ---------------------------------------------------------------------------


def _snapshot(schematic_path: str, project_path: str) -> dict[str, tuple[Path, bytes]]:
    paths = [Path(schematic_path)]
    try:
        target = _shared._root_instance_target(schematic_path, project_path)
    except Exception:  # placement will raise the same problem itself
        target = None
    if target is not None:
        paths.append(Path(target[2]))
    snaps: dict[str, tuple[Path, bytes]] = {}
    for p in paths:
        if p.is_file():
            snaps.setdefault(os.path.normcase(str(p.resolve())), (p, p.read_bytes()))
    return snaps


def _restore(snaps: dict[str, tuple[Path, bytes]]) -> list[str]:
    restored = []
    for p, data in snaps.values():
        if not p.is_file() or p.read_bytes() != data:
            _atomic_write(p, data)
            restored.append(p.name)
    return restored


def auto_place_decoupling_cap(
    lib_id: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    power_net: str,
    ground_net: str,
    rotation=0,
    symbol_lib_path: str = "",
    schematic_path: str = SCH_PATH,
    project_path: str = "",
) -> str:
    """P's auto_place_decoupling_cap: place, wire pin 1 up and pin 2 down, roll back on refusal."""
    snaps = _snapshot(schematic_path, project_path)
    try:
        result = _sch.place_component(
            lib_id=lib_id,
            reference=reference,
            value=value,
            x=x,
            y=y,
            rotation=rotation,
            symbol_lib_path=symbol_lib_path,
            schematic_path=schematic_path,
            project_path=project_path,
        )
        m1 = wire_pins_to_net(
            pins=[{"reference": reference, "pin": "1"}],
            label_text=power_net,
            direction="up",
            schematic_path=schematic_path,
        )
        m2 = wire_pins_to_net(
            pins=[{"reference": reference, "pin": "2"}],
            label_text=ground_net,
            direction="down",
            schematic_path=schematic_path,
        )
    except Exception as e:
        restored = _restore(snaps)
        if isinstance(e, ToolError):
            raise ToolError(
                f"{e} "
                + (
                    f"The placement was rolled back ({', '.join(restored)} restored);"
                    if restored
                    else "Nothing had been written;"
                )
                + " every file is as it was."
            ) from e
        raise
    return f"{result} | pin 1->{power_net} | pin 2->{ground_net} [{m1} / {m2}]"
