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


def splice(path: str, sexpr: str) -> None:
    """Insert one node after its last same-kind sibling, with a fresh uuid."""
    node = _cst.parse(sexpr.encode()).lists[0]
    u = node.find("uuid")
    if u is not None:
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


def place_units(path: str, symbol: str, ref: str, placements) -> None:
    """One multi-unit part: placements [(unit, x, y), ...], the first placed by place_component
    and the rest cloned from it with their own unit, position and uuids, the way KiCad stores
    units of one reference."""
    (unit0, x0, y0), *rest = placements
    place(path, symbol, ref, x0, y0)
    p = Path(path)
    tree = _cst.parse(p.read_bytes())
    root = tree.lists[0]
    src = next(
        s
        for s in root.find_all("symbol")
        if any(q.atoms[2].text == ref for q in s.find_all("property") if len(q.atoms) > 2)
    )

    def set_unit(node, unit: int) -> None:
        node.find("unit").atoms[1].set_text(str(unit))
        for proj in node.find("instances").find_all("project"):
            for path_node in proj.find_all("path"):
                path_node.find("unit").atoms[1].set_text(str(unit))

    set_unit(src, unit0)
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
        set_unit(node, unit)
        root.insert_after(anchor, node)
        anchor = node
    p.write_bytes(_cst.serialize(tree))


def custom_lib(directory: Path, name: str, pins) -> str:
    """A one-symbol library: pins [(number, name, type, x, y, angle, hidden)], one unit."""
    eff = "(effects (font (size 1.27 1.27)))"
    body = " ".join(
        f"(pin {typ} line (at {_n(x)} {_n(y)} {ang}) (length 2.54)"
        + (" (hide yes)" if hidden else "")
        + f' (name "{pin_name}" {eff}) (number "{num}" {eff}))'
        for num, pin_name, typ, x, y, ang, hidden in pins
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
        " (stroke (width 0.254) (type default)) (fill (type none))))"
        f' (symbol "{name}_1_1" {body})'
        " (embedded_fonts no))\n)\n"
    )
    path = Path(directory) / f"{name}.kicad_sym"
    path.write_text(text, encoding="utf-8")
    return str(path)


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
