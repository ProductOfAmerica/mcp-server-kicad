"""Schematics for the routing tests, built with the repo's own tools.

Symbols come from tests/fixtures/routing.kicad_sym, copied verbatim from KiCad 9.0.8's stock
libraries: Device R, D, C and R_Network03_Split; Transistor_BJT Q_NPN_BCE; Connector_Generic
Conn_01x04; power VCC, GND, +5V and PWR_FLAG; 74xx 74LS00 and 74LS04; 4xxx_IEEE 4011. Placing
from that file needs no KiCad install and pins the geometry, so the same tests mean the same
thing on a KiCad 9 and a KiCad 10 runner.

place_component writes the ``(instances ...)`` block, without which kicad-cli 9 leaves unnamed
nets out of the netlist and the oracle cannot see a short between two of them.

Raw constructs that no tool writes, or that the tool under test would otherwise have to write
for its own fixture, are spliced as nodes with exact coordinates, the way the routing pressure
test's harness built them (docs/adr-routing-safety.md).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from mcp_server_kicad import _cst, project, schematic

LIB = str(Path(__file__).parent / "fixtures" / "routing.kicad_sym")

#: The library prefix each fixture symbol is placed under (cosmetic: lib_name decides).
PREFIX = {
    "R": "Device",
    "D": "Device",
    "C": "Device",
    "R_Network03_Split": "Device",
    "Q_NPN_BCE": "Transistor_BJT",
    "Conn_01x04": "Connector_Generic",
    "VCC": "power",
    "GND": "power",
    "+5V": "power",
    "PWR_FLAG": "power",
    "74LS00": "74xx",
    "74LS04": "74xx",
    "4011": "4xxx_IEEE",
}


U, D, L, R = (0, -1), (0, 1), (-1, 0), (1, 0)

#: KiCad-true pin ends relative to the symbol origin (mm) and the outward direction, per
#: (rotation, mirror). Source: the pressure test's sweep (pi_jobs b_o12), where 84 of 84
#: wire_pins_to_net calls through this geometry were delivered in kicad-cli 9.0.8.
ORIENTED = {
    "R": {
        (0, ""): [(0, -3.81, U), (0, 3.81, D)],
        (90, ""): [(-3.81, 0, L), (3.81, 0, R)],
        (180, ""): [(0, 3.81, D), (0, -3.81, U)],
        (270, ""): [(3.81, 0, R), (-3.81, 0, L)],
        (0, "x"): [(0, 3.81, D), (0, -3.81, U)],
        (90, "x"): [(-3.81, 0, L), (3.81, 0, R)],
        (180, "x"): [(0, -3.81, U), (0, 3.81, D)],
        (270, "x"): [(3.81, 0, R), (-3.81, 0, L)],
        (0, "y"): [(0, -3.81, U), (0, 3.81, D)],
        (90, "y"): [(3.81, 0, R), (-3.81, 0, L)],
        (180, "y"): [(0, 3.81, D), (0, -3.81, U)],
        (270, "y"): [(-3.81, 0, L), (3.81, 0, R)],
    },
    "D": {
        (0, ""): [(-3.81, 0, L), (3.81, 0, R)],
        (90, ""): [(0, 3.81, D), (0, -3.81, U)],
        (180, ""): [(3.81, 0, R), (-3.81, 0, L)],
        (270, ""): [(0, -3.81, U), (0, 3.81, D)],
        (0, "x"): [(-3.81, 0, L), (3.81, 0, R)],
        (90, "x"): [(0, -3.81, U), (0, 3.81, D)],
        (180, "x"): [(3.81, 0, R), (-3.81, 0, L)],
        (270, "x"): [(0, 3.81, D), (0, -3.81, U)],
        (0, "y"): [(3.81, 0, R), (-3.81, 0, L)],
        (90, "y"): [(0, 3.81, D), (0, -3.81, U)],
        (180, "y"): [(-3.81, 0, L), (3.81, 0, R)],
        (270, "y"): [(0, -3.81, U), (0, 3.81, D)],
    },
    "Q_NPN_BCE": {
        (0, ""): [(-5.08, 0, L), (2.54, -5.08, U), (2.54, 5.08, D)],
        (90, ""): [(0, 5.08, D), (-5.08, -2.54, L), (5.08, -2.54, R)],
        (180, ""): [(5.08, 0, R), (-2.54, 5.08, D), (-2.54, -5.08, U)],
        (270, ""): [(0, -5.08, U), (5.08, 2.54, R), (-5.08, 2.54, L)],
        (0, "x"): [(-5.08, 0, L), (2.54, 5.08, D), (2.54, -5.08, U)],
        (90, "x"): [(0, -5.08, U), (-5.08, 2.54, L), (5.08, 2.54, R)],
        (180, "x"): [(5.08, 0, R), (-2.54, -5.08, U), (-2.54, 5.08, D)],
        (270, "x"): [(0, 5.08, D), (5.08, -2.54, R), (-5.08, -2.54, L)],
        (0, "y"): [(5.08, 0, R), (-2.54, -5.08, U), (-2.54, 5.08, D)],
        (90, "y"): [(0, 5.08, D), (5.08, -2.54, R), (-5.08, -2.54, L)],
        (180, "y"): [(-5.08, 0, L), (2.54, 5.08, D), (2.54, -5.08, U)],
        (270, "y"): [(0, -5.08, U), (-5.08, 2.54, L), (5.08, 2.54, R)],
    },
}


def fresh(tmp_path: Path, name: str = "r") -> str:
    """A new empty schematic in its own directory, no project file."""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.kicad_sch"
    project.create_schematic(schematic_path=str(path))
    return str(path)


def place(
    path: str, symbol: str, ref: str, x: float, y: float, rot: Any = 0, mirror: Any = "", value=None
):
    """place_component from the fixture library. The origin snaps to 1.27 mm."""
    return schematic.place_component(
        lib_id=f"{PREFIX[symbol]}:{symbol}",
        reference=ref,
        value=value or ref,
        x=x,
        y=y,
        rotation=rot,
        mirror=mirror,
        symbol_lib_path=LIB,
        schematic_path=path,
    )


def power(path: str, name: str, ref: str, x: float, y: float, rot=0):
    """A power symbol, named by its Value as KiCad names the net."""
    return place(path, name, ref, x, y, rot=rot, value=name)


def _n(v: float) -> str:
    s = f"{round(v, 4):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def splice(path: str, sexpr: str, keep_uuid: bool = False) -> None:
    """Insert one node after its last same-kind sibling, with a fresh uuid unless kept."""
    node = _cst.parse(sexpr.encode()).lists[0]
    u = node.find("uuid")
    if u is not None and not keep_uuid:
        u.atoms[1].set_text(str(uuid.uuid4()))
    p = Path(path)
    tree = _cst.parse(p.read_bytes())
    schematic._splice_sch_node(tree.lists[0], node.head, node)
    p.write_bytes(_cst.serialize(tree))


def wire(path: str, x1, y1, x2, y2) -> None:
    """A raw wire, exactly as given: no junction anywhere."""
    splice(
        path,
        f"(wire (pts (xy {_n(x1)} {_n(y1)}) (xy {_n(x2)} {_n(y2)}))"
        ' (stroke (width 0) (type default)) (uuid "x"))',
    )


def bus(path: str, x1, y1, x2, y2) -> None:
    splice(
        path,
        f"(bus (pts (xy {_n(x1)} {_n(y1)}) (xy {_n(x2)} {_n(y2)}))"
        ' (stroke (width 0) (type default)) (uuid "x"))',
    )


def polyline(path: str, x1, y1, x2, y2) -> None:
    """A 2-point graphic line, which KiCad treats as a line a label can attach to."""
    splice(
        path,
        f"(polyline (pts (xy {_n(x1)} {_n(y1)}) (xy {_n(x2)} {_n(y2)}))"
        ' (stroke (width 0) (type default)) (uuid "x"))',
    )


def bus_entry(path: str, x, y, dx, dy) -> None:
    """A bus entry from (x, y) to (x + dx, y + dy)."""
    splice(
        path,
        f"(bus_entry (at {_n(x)} {_n(y)}) (size {_n(dx)} {_n(dy)})"
        ' (stroke (width 0) (type default)) (uuid "x"))',
    )


def rule_area(path: str, pts) -> None:
    """A schematic rule area with this outline (KiCad 9's rule_area node)."""
    xy = " ".join(f"(xy {_n(x)} {_n(y)})" for x, y in pts)
    splice(
        path,
        f"(rule_area (polyline (pts {xy}) (stroke (width 0) (type dash)) (fill (type none))"
        f' (uuid "{uuid.uuid4()}")))',
    )


def netclass_flag(path: str, cls: str, x, y, rot=0) -> None:
    """A directive label assigning net class *cls*, anchored at (x, y)."""
    splice(
        path,
        f'(netclass_flag "" (length 2.54) (shape round) (at {_n(x)} {_n(y)} {rot})'
        ' (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "x")'
        f' (property "Netclass" "{cls}" (at {_n(x)} {_n(y)} 0)'
        " (effects (font (size 1.27 1.27) (italic yes)) (justify left))))",
    )


def junction(path: str, x, y) -> None:
    splice(path, f'(junction (at {_n(x)} {_n(y)}) (diameter 0) (color 0 0 0 0) (uuid "x"))')


def no_connect(path: str, x, y) -> None:
    splice(path, f'(no_connect (at {_n(x)} {_n(y)}) (uuid "x"))')


def label(path: str, text: str, x, y, rot=0, kind="label", shape="input") -> None:
    """kind: label, global_label or hierarchical_label."""
    shape_s = f"(shape {shape}) " if kind != "label" else ""
    splice(
        path,
        f'({kind} "{text}" {shape_s}(at {_n(x)} {_n(y)} {rot})'
        ' (effects (font (size 1.27 1.27))) (uuid "x"))',
    )


def stub(path: str, x, y, dx, dy, text: str) -> None:
    """A raw stub from (x, y) by (dx, dy) with a local label at its end: setup that must not
    depend on the tool under test."""
    wire(path, x, y, x + dx, y + dy)
    rot = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}[
        ((dx > 0) - (dx < 0), (dy > 0) - (dy < 0))
    ]
    label(path, text, x + dx, y + dy, rot)


def text_of(path: str) -> bytes:
    return Path(path).read_bytes()


def _placed(root, ref: str):
    """The placed symbol nodes whose Reference property is *ref*."""
    return [
        s
        for s in root.find_all("symbol")
        if any(
            q.atoms[1].text == "Reference" and q.atoms[2].text == ref
            for q in s.find_all("property")
            if len(q.atoms) > 2
        )
    ]


def _edit(path: str, fn) -> None:
    p = Path(path)
    tree = _cst.parse(p.read_bytes())
    fn(tree.lists[0])
    p.write_bytes(_cst.serialize(tree))


def _set_unit(node, unit: int) -> None:
    node.find("unit").atoms[1].set_text(str(unit))
    for proj in node.find("instances").find_all("project"):
        for path_node in proj.find_all("path"):
            path_node.find("unit").atoms[1].set_text(str(unit))


def set_unit(path: str, ref: str, unit: int) -> None:
    """Make every placed *ref* on this sheet unit *unit*, top level and every instance entry."""
    _edit(path, lambda root: [_set_unit(s, unit) for s in _placed(root, ref)])


def set_lib_name(path: str, ref: str, name: str) -> None:
    """Point *ref*'s lib_name at *name*, whether or not lib_symbols holds an entry of that name
    (the routing pressure test's set_lib_name)."""
    _edit(path, lambda root: _placed(root, ref)[0].find("lib_name").atoms[1].set_text(name))


def rename_ref(path: str, old: str, new: str) -> None:
    """Give the placed *old* the reference *new*: its Reference property and every instance
    entry, the way two parts end up sharing one."""

    def fn(root) -> None:
        for s in _placed(root, old):
            for q in s.find_all("property"):
                if len(q.atoms) > 2 and q.atoms[1].text == "Reference":
                    q.atoms[2].set_text(new)
            for proj in s.find("instances").find_all("project"):
                for path_node in proj.find_all("path"):
                    path_node.find("reference").atoms[1].set_text(new)

    _edit(path, fn)


def add_instance(path: str, ref: str, inst_path: str, unit: int) -> None:
    """Give *ref* one more instance entry, at *inst_path* with *unit*: the shape a sheet used
    twice gives its symbols."""

    def fn(root) -> None:
        proj = _placed(root, ref)[0].find("instances").find("project")
        old = proj.find_all("path")[-1]
        new = old.copy()
        new.atoms[1].set_text(inst_path)
        new.find("unit").atoms[1].set_text(str(unit))
        proj.insert_after(old, new)

    _edit(path, fn)


def stale_reference(path: str, ref: str, stale: str) -> None:
    """Give the placed *ref* a first instance entry from another project, on a path this one
    does not have, naming it *stale*, and *stale* as its Reference property. KiCad's parser makes
    the first entry a symbol's own reference, so that is what KiCad saves after a sheet copied
    from another project is re-annotated (smps-com in KiCad's demos carries R2 on eight
    resistors). KiCad still reads *ref* at the live path."""

    def fn(root) -> None:
        node = _placed(root, ref)[0]
        inst = node.find("instances")
        proj = inst.find("project")
        dead = proj.copy()
        dead.atoms[1].set_text("elsewhere")
        for extra in dead.find_all("path")[1:]:
            dead.remove_child(extra)
        entry = dead.find("path")
        entry.atoms[1].set_text("/00000000-0000-0000-0000-0000000000dd")
        entry.find("reference").atoms[1].set_text(stale)
        inst.insert_before(proj, dead)
        for q in node.find_all("property"):
            if len(q.atoms) > 2 and q.atoms[1].text == "Reference":
                q.atoms[2].set_text(stale)

    _edit(path, fn)


def project_file(directory, name: str, classes=()) -> str:
    """A minimal .kicad_pro, which makes <name>.kicad_sch beside it the root of its hierarchy,
    defining net classes Default and *classes* (the routing pressure test's v_write_pro)."""

    def cls(cname: str, prio: int) -> dict:
        return {
            "name": cname,
            "priority": prio,
            "clearance": 0.2,
            "track_width": 0.25,
            "via_diameter": 0.8,
            "via_drill": 0.4,
            "microvia_diameter": 0.3,
            "microvia_drill": 0.1,
            "diff_pair_width": 0.2,
            "diff_pair_gap": 0.25,
            "diff_pair_via_gap": 0.25,
            "wire_width": 6,
            "bus_width": 12,
            "line_style": 0,
            "pcb_color": "rgba(0, 0, 0, 0.000)",
            "schematic_color": "rgba(0, 0, 0, 0.000)",
        }

    p = Path(directory) / f"{name}.kicad_pro"
    data: dict = {"meta": {"filename": p.name, "version": 1}}
    if classes:
        data["net_settings"] = {
            "classes": [cls("Default", 2147483647)] + [cls(c, i) for i, c in enumerate(classes)],
            "meta": {"version": 4},
            "net_colors": None,
            "netclass_assignments": None,
            "netclass_patterns": [],
        }
    p.write_text(json.dumps(data, indent=2) + "\n")
    return str(p)


def sheet(
    parent: str,
    child: str,
    name: str,
    x,
    y,
    pins=(),
    w=30.48,
    h=20.32,
    file: str | None = None,
    again: dict | None = None,
) -> dict:
    """A sheet block on the root sheet *parent* for *child*, in the node shape the routing
    pressure test's hierarchy probes wrote. pins: [(name, shape, side, k)], pin k at
    y + k * 2.54 on the left or right edge, with no wire or label added on either side. *file*
    is the Sheetfile text, the child's file name by default.

    Every symbol already on *child* is given the instance path KiCad gives it under this sheet,
    so place the child's symbols first. With *again*, the sheet is a further instance of a
    child already placed: each symbol keeps its entries and gains one for this path, with the
    reference *again* maps its current one to (unchanged when absent). Returns {pin name: (x,
    y)}.
    """
    root = _cst.parse(Path(parent).read_bytes()).lists[0]
    root_uuid = root.find("uuid").atoms[1].text
    page = len(root.find_all("sheet")) + 2
    su = str(uuid.uuid4())
    eff = "(effects (font (size 1.27 1.27))"
    where, pin_nodes = {}, []
    for pin_name, shape, side, k in pins:
        px, py = (x if side == "left" else x + w), y + k * 2.54
        ang, just = (180, "left") if side == "left" else (0, "right")
        where[pin_name] = (round(px, 4), round(py, 4))
        pin_nodes.append(
            f'(pin "{pin_name}" {shape} (at {_n(px)} {_n(py)} {ang}) (uuid "{uuid.uuid4()}")'
            f" {eff} (justify {just})))"
        )
    stem = Path(parent).stem
    splice(
        parent,
        f"(sheet (at {_n(x)} {_n(y)}) (size {_n(w)} {_n(h)}) (exclude_from_sim no)"
        " (in_bom yes) (on_board yes) (dnp no) (fields_autoplaced yes)"
        f' (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0000)) (uuid "{su}")'
        f' (property "Sheetname" "{name}" (at {_n(x)} {_n(y - 0.7116)} 0)'
        f" {eff} (justify left bottom)))"
        f' (property "Sheetfile" "{file or Path(child).name}" (at {_n(x)} {_n(y + h + 0.5846)} 0)'
        f" {eff} (justify left top))) "
        + " ".join(pin_nodes)
        + f' (instances (project "{stem}" (path "/{root_uuid}" (page "{page}")))))',
        keep_uuid=True,
    )

    def repath(child_root) -> None:
        for s in child_root.find_all("symbol"):
            inst = s.find("instances")
            for proj in inst.find_all("project") if inst is not None else ():
                proj.atoms[1].set_text(stem)
                paths = proj.find_all("path")
                if again is None:
                    for path_node in paths:
                        path_node.atoms[1].set_text(f"/{root_uuid}/{su}")
                    continue
                new = paths[-1].copy()
                new.atoms[1].set_text(f"/{root_uuid}/{su}")
                ref = new.find("reference").atoms[1]
                ref.set_text(again.get(ref.text, ref.text))
                proj.insert_after(paths[-1], new)

    _edit(child, repath)
    return where


def duplicate_r_swapped(path: str) -> None:
    """A second lib_symbols entry named "R" after the first, pins 1 and 2 swapped: what a hand
    edit or a git merge can leave. KiCad 9.0.8 and 10.0.6 draw the last one
    (SCH_SCREEN::AddLibSymbol replaces an earlier entry of the same name)."""

    def fn(root) -> None:
        libs = root.find("lib_symbols")
        first = next(s for s in libs.find_all("symbol") if s.atoms[1].text == "R")
        dup = first.copy()
        for sub in dup.find_all("symbol"):
            for pin in sub.find_all("pin"):
                num = pin.find("number").atoms[1]
                num.set_text({"1": "2", "2": "1"}[num.text])
        libs.insert_after(first, dup)

    _edit(path, fn)


def place_units(path: str, symbol: str, ref: str, placements) -> None:
    """One multi-unit part: placements [(unit, x, y), ...], the first placed by place_component
    and the rest cloned from it with their own unit, position and uuids, the way KiCad stores
    units of one reference."""
    (unit0, x0, y0), *rest = placements
    place(path, symbol, ref, x0, y0)
    p = Path(path)
    tree = _cst.parse(p.read_bytes())
    root = tree.lists[0]
    (src,) = _placed(root, ref)
    _set_unit(src, unit0)
    anchor = src
    for unit, x, y in rest:
        node = src.copy()
        at = node.find("at")
        at.atoms[1].set_text(_n(x))
        at.atoms[2].set_text(_n(y))
        for prop in node.find_all("property"):
            pat = prop.find("at")
            pat.atoms[1].set_text(_n(float(pat.atoms[1].text) - x0 + x))
            pat.atoms[2].set_text(_n(float(pat.atoms[2].text) - y0 + y))
        node.find("uuid").atoms[1].set_text(str(uuid.uuid4()))
        for pin in node.find_all("pin"):
            pin.find("uuid").atoms[1].set_text(str(uuid.uuid4()))
        _set_unit(node, unit)
        root.insert_after(anchor, node)
        anchor = node
    p.write_bytes(_cst.serialize(tree))


def custom_lib(directory: Path, name: str, pins, alternates=None) -> str:
    """A one-symbol library: pins [(number, name, type, x, y, angle, hidden[, unit])]. The unit
    defaults to 1; unit 0 puts the pin in the common sub-symbol, which every unit draws.
    *alternates* maps a pin number to its alternates [(name, type)]."""
    eff = "(effects (font (size 1.27 1.27)))"
    by_unit: dict[int, list[str]] = {}
    for num, pin_name, typ, x, y, ang, hidden, *unit in pins:
        alts = "".join(f' (alternate "{a}" {t} line)' for a, t in (alternates or {}).get(num, ()))
        by_unit.setdefault(unit[0] if unit else 1, []).append(
            f"(pin {typ} line (at {_n(x)} {_n(y)} {ang}) (length 2.54)"
            + (" (hide yes)" if hidden else "")
            + f' (name "{pin_name}" {eff}) (number "{num}" {eff}){alts})'
        )
    common = " ".join(by_unit.pop(0, []))
    units = " ".join(
        f'(symbol "{name}_{u}_1" {" ".join(body)})' for u, body in sorted(by_unit.items())
    )
    text = (
        '(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor")'
        ' (generator_version "9.0")\n'
        f'  (symbol "{name}" (exclude_from_sim no) (in_bom yes) (on_board yes)'
        f' (property "Reference" "U" (at 0 7.62 0) {eff})'
        f' (property "Value" "{name}" (at 0 -7.62 0) {eff})'
        ' (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))'
        ' (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))'
        f' (symbol "{name}_0_1" (rectangle (start -2.54 2.54) (end 2.54 -2.54)'
        f" (stroke (width 0.254) (type default)) (fill (type none))) {common})"
        f" {units}"
        " (embedded_fonts no))\n)\n"
    )
    path = Path(directory) / f"{name}.kicad_sym"
    path.write_text(text, encoding="utf-8")
    return str(path)


def select_alternate(path: str, ref: str, num: str, alt: str) -> None:
    """Select alternate *alt* on pin *num* of every placed *ref*, in the shape KiCad saves it:
    (pin "N" (uuid ...) (alternate "ALT"))."""

    def fn(root) -> None:
        for s in _placed(root, ref):
            (pin,) = [q for q in s.find_all("pin") if q.atoms[1].text == num]
            pin.append_child(_cst.parse(f'(alternate "{alt}")'.encode()).lists[0], b" ")

    _edit(path, fn)


def place_custom(path: str, lib: str, name: str, ref: str, x: float, y: float) -> None:
    schematic.place_component(
        lib_id=f"Test:{name}",
        reference=ref,
        value=name,
        x=x,
        y=y,
        symbol_lib_path=lib,
        schematic_path=path,
    )


def demo_dir() -> Path | None:
    """KiCad's installed demos, found from the resolved kicad-cli, or None."""
    from mcp_server_kicad._shared import _kicad_root

    root = _kicad_root()
    for sub in ("share/kicad/demos", "SharedSupport/demos"):
        if root is not None and (root / sub).is_dir():
            return root / sub
    return None


def standalone_copy(src, dst) -> str:
    """A copy of one sheet of a KiCad project that kicad-cli exports on its own.

    Each symbol's first instance path is pointed at this file's root and every other one at a
    path that exists nowhere, so kicad-cli lists the sheet's unnamed nets (the routing pressure
    test's standalone_copy). The Reference property is set to that first entry's reference,
    which is the one kicad-cli then prints, so the model and the netlist name each pin alike.
    """
    tree = _cst.parse(Path(src).read_bytes())
    root = tree.lists[0]
    own = f"/{root.find('uuid').atoms[1].text}"
    for s in root.find_all("symbol"):
        inst = s.find("instances")
        entries = (
            [p for proj in inst.find_all("project") for p in proj.find_all("path")]
            if inst is not None
            else []
        )
        for i, path_node in enumerate(entries):
            path_node.atoms[1].set_text(own if i == 0 else "/ffffffff-0000-0000-0000-000000000000")
        ref = entries[0].find("reference") if entries else None
        if ref is not None:
            for q in s.find_all("property"):
                if len(q.atoms) > 2 and q.atoms[1].text == "Reference":
                    q.atoms[2].set_text(ref.atoms[1].text)
    Path(dst).write_bytes(_cst.serialize(tree))
    return str(dst)
