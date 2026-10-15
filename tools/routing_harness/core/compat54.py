"""Frozen copies of mcp_server_kicad.schematic helpers as they were at commit 54160c1.

The code below is the package's own (MIT), copied from `git show 54160c1:mcp_server_kicad/schematic.py`
with the bodies untouched; only the @mcp.tool decorators are gone, two comments that carried a
dash were reworded and connect_pins reads pins through the package (below). It is frozen so the prototype designs and the fixture builders behave as they did
when the measurement baselines were taken, whatever the package has changed since:

  _lib_unit_style           moved to _connectivity and not re-exported (install() restores it)
  _ANGLE_TO_DIR, _DIR_OFFSETS, _PWR_FLAG_LIB_TPL, _PWR_FLAG_SYM_TPL, _pin_matches_cst,
  _pin_electrical_types_cst gone
  _get_pin_pos_cst, _transform_pin_pos, _drawn_pin_pos_cst
                            the old mirror-first pin transform; the package's is slice 1's
                            rotate-then-mirror fix
  place_component           the package now refuses a derived symbol and a missing definition; this
                            copy, with its own _copy_lib_symbol_from_file_cst and
                            _copy_system_lib_symbol_cst, does not
  add_power_symbol          the package no longer places a PWR_FLAG beside the symbol; this copy does
  _point_on_wire_interior, _auto_junctions_cst
                            the junction scan that never splits a wire (design P's I22), which
                            the package replaced with KiCad's split
  add_wires, connect_pins   the wiring the fixture builders run, which writes those junctions;
                            this connect_pins reads pins with the package's own _get_pin_pos_cst
                            (imported as _package_get_pin_pos_cst, the one change to its body), so
                            it reads each target's pins as that target does

Only names that exist in the package both at 54160c1 and now are imported from it (never
_connectivity), so this module also loads under PT_REPO=<an export of 54160c1>. install() adds
exactly one attribute to the package, schematic._lib_unit_style, and only when it is absent. Every
other name is imported from here, by name, at the call sites that need it. The package source is
never edited.
"""

import difflib
import math
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError

import mcp_server_kicad._cst as _cst
from mcp_server_kicad._cst import _fill_at, _node_xy
from mcp_server_kicad._shared import (
    SCH_PATH,
    _atomic_write,
    _check_rotation,
    _gen_uuid,
    _node_uuid,
    _resolve_hierarchy_path,
    _resolve_system_lib,
    _sheet_file_cst,
    _snap_grid,
    _sym_property_cst,
    _upsert_root_symbol_instance,
)
from mcp_server_kicad.models import WireSpec
from mcp_server_kicad.schematic import (
    _JUNCTION_TPL,
    _LABEL_TPL,
    _PIN_REF_TPL,
    _SYMBOL_TPL,
    _VALID_REF_RE,
    _bounds_check,
    _find_lib_symbol_cst,
    _find_syms_cst,
    _instance_units,
    _open_sch_cst,
    _splice_lib_symbol_cst,
    _splice_sch_node,
    _splice_wire,
    _sym_body_style_cst,
    _sym_unit_cst,
    _wire_xys,
)
from mcp_server_kicad.schematic import _get_pin_pos_cst as _package_get_pin_pos_cst


def install() -> None:
    """Add schematic._lib_unit_style when the package has none (it moved to _connectivity)."""
    from mcp_server_kicad import schematic

    if not hasattr(schematic, "_lib_unit_style"):
        schematic._lib_unit_style = _lib_unit_style


# ---- templates and tables (54160c1 schematic.py lines 1112 to 1138 and 2093 to 2102)

# Synthetic PWR_FLAG lib symbol for hosts without a KiCad install (CI): same
# semantics as the system one (power flag, one power_out pin). When a system
# library exists, the real node is copied verbatim instead.
_PWR_FLAG_LIB_TPL = _cst.parse(
    b'(symbol "power:PWR_FLAG"\n\t(power)\n\t(exclude_from_sim no)\n\t(in_bom no)\n\t(on_board yes)'
    b'\n\t(symbol "PWR_FLAG_0_1")\n\t(symbol "PWR_FLAG_1_1"\n\t\t(pin power_out line'
    b'\n\t\t\t(at 0 0 90)\n\t\t\t(length 0)\n\t\t\t(name "~"\n\t\t\t\t(effects\n\t\t\t\t\t(font'
    b"\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t)"
    b'\n\t\t\t(number "1"\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)'
    b"\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n)"
).lists[0]

_PWR_FLAG_SYM_TPL = _cst.parse(
    b'(symbol\n\t(lib_id "power:PWR_FLAG")\n\t(at 0 0 0)\n\t(unit 1)\n\t(exclude_from_sim no)'
    b'\n\t(in_bom no)\n\t(on_board yes)\n\t(dnp no)\n\t(uuid "x")'
    b'\n\t(property "Reference" "#FLG01"\n\t\t(at 0 0 0)\n\t\t(effects\n\t\t\t(font'
    b"\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(hide yes)\n\t\t)\n\t)"
    b'\n\t(property "Value" "PWR_FLAG"\n\t\t(at 0 0 0)\n\t\t(effects\n\t\t\t(font'
    b"\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t)\n\t)"
    b'\n\t(property "Footprint" ""\n\t\t(at 0 0 0)\n\t\t(effects\n\t\t\t(font'
    b"\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(hide yes)\n\t\t)\n\t)"
    b'\n\t(property "Datasheet" "~"\n\t\t(at 0 0 0)\n\t\t(effects\n\t\t\t(font'
    b"\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(hide yes)\n\t\t)\n\t)"
    b'\n\t(pin "1"\n\t\t(uuid "x")\n\t)'
    b'\n\t(instances\n\t\t(project "X"\n\t\t\t(path "/x"\n\t\t\t\t(reference "#FLG01")'
    b"\n\t\t\t\t(unit 1)\n\t\t\t)\n\t\t)\n\t)\n)"
).lists[0]

# Direction -> (dx_sign, dy_sign, label_rotation)
_DIR_OFFSETS = {
    "right": (1, 0, 0),
    "left": (-1, 0, 180),
    "up": (0, -1, 90),
    "down": (0, 1, 270),
}

# Outward angle (math Y-down) -> cardinal direction name
_ANGLE_TO_DIR = {0: "right", 90: "down", 180: "left", 270: "up"}


# ---- pin position helpers (54160c1 schematic.py lines 160 to 199, 1281 to 1296, 1333 to 1441)


def _transform_pin_pos(
    px: float,
    py: float,
    pin_angle: float,
    cx: float,
    cy: float,
    comp_angle_deg: float,
    mirror: str | None,
) -> tuple[float, float, float]:
    """Transform a pin from lib coords to absolute schematic coords.

    Returns (final_x, final_y, outward_angle_deg).

    The outward angle is the direction away from the component body in
    schematic coordinates (0=right, 90=down/+Y, 180=left, 270=up/-Y).
    """
    angle_rad = math.radians(comp_angle_deg)

    # Negate Y to convert from lib_symbol (Y-up) to schematic (Y-down)
    py = -py

    # Apply mirror and compute absolute pin angle (toward-body direction)
    if mirror == "x":
        py = -py
        abs_pin_angle = pin_angle + comp_angle_deg
    elif mirror == "y":
        px = -px
        abs_pin_angle = pin_angle + 180 + comp_angle_deg
    else:
        abs_pin_angle = -pin_angle + comp_angle_deg

    # Apply rotation (KiCad rotates CW in the Y-down coordinate system)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    final_x = cx + px * cos_a + py * sin_a
    final_y = cy - px * sin_a + py * cos_a

    # Outward direction (away from body)
    outward = (abs_pin_angle + 180) % 360
    return round(final_x, 4), round(final_y, 4), outward


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


def _pin_matches_cst(pin, pin_name: str) -> bool:
    """True when a lib pin node's name or number is *pin_name*."""
    name = pin.find("name")
    number = pin.find("number")
    return (name is not None and name.atoms[1].text == pin_name) or (
        number is not None and number.atoms[1].text == pin_name
    )


def _drawn_pin_pos_cst(root, target, pin_name: str, reference: str):
    """Absolute (x, y, outward) of *pin_name* if the placed node *target* draws it.

    None when it does not: the pin is on another unit, or on this unit's
    other body style. Pin match is name-OR-number per pin in file order.
    """
    lib_sym = _find_lib_symbol_cst(root, target.find("lib_id").atoms[1].text)
    if lib_sym is None:
        raise ValueError(f"Lib symbol for {reference} not found")
    at = target.find("at")
    cx, cy = float(at.atoms[1].text), float(at.atoms[2].text)
    comp_angle = float(at.atoms[3].text) if len(at.atoms) > 3 else 0
    m = target.find("mirror")
    mir = m.atoms[1].text if m is not None else None
    for unit in _instance_units(lib_sym, _sym_unit_cst(target), _sym_body_style_cst(target)):
        for pin in unit.find_all("pin"):
            if _pin_matches_cst(pin, pin_name):
                pat = pin.find("at")
                px, py = float(pat.atoms[1].text), float(pat.atoms[2].text)
                pangle = float(pat.atoms[3].text) if len(pat.atoms) > 3 else 0
                return _transform_pin_pos(px, py, pangle, cx, cy, comp_angle, mir)
    return None


def _get_pin_pos_cst(root, reference: str, pin_name: str) -> tuple[float, float, float]:
    """CST twin of _get_pin_pos; same match rules and ValueError strings.

    Placed symbols are the root-level symbol nodes (find_all never descends
    into lib_symbols). A multi-unit part is several of them sharing one
    reference, so this walks them in file order and takes the first whose
    unit and body style draw the pin, which is KiCad's own rule. A unit-0 pin
    is the same pad whichever unit draws it, so the first placed unit is right
    for it. When no placed unit on this sheet draws the pin (its unit is on
    another sheet, or not placed), the error names the unit that does, or the
    body style when that is what differs; nothing has been written at that
    point, so the file is untouched.
    """
    targets = _find_syms_cst(root, reference)
    if not targets:
        raise ValueError(f"Component {reference} not found")
    for target in targets:
        pos = _drawn_pin_pos_cst(root, target, pin_name, reference)
        if pos is not None:
            return pos
    lib_sym = _find_lib_symbol_cst(root, targets[0].find("lib_id").atoms[1].text)
    subs = lib_sym.find_all("symbol") if lib_sym is not None else []
    carriers = sorted(
        {
            ids
            for sub in subs
            if (ids := _lib_unit_style(sub)) is not None
            and any(_pin_matches_cst(pin, pin_name) for pin in sub.find_all("pin"))
        }
    )
    if not carriers:
        raise ValueError(f"Pin '{pin_name}' not found on {reference}")
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
        raise ValueError(
            f"Pin '{pin_name}' of {reference} is on {where}, which is not placed on this sheet "
            f"({reference} here: {placed}). Place that unit, or wire the pin on the sheet "
            "that holds it."
        )
    raise ValueError(
        f"Pin '{pin_name}' of {reference} is on {where}, which this sheet does not draw "
        f"({reference} here: {placed}). Switch the placed symbol to that body style in KiCad."
    )


def _pin_electrical_types_cst(lib_sym, pin_name: str) -> list[str]:
    """Electrical types of every lib pin matching *pin_name* by name or number."""
    out = []
    for unit in lib_sym.find_all("symbol"):
        for pin in unit.find_all("pin"):
            name = pin.find("name")
            number = pin.find("number")
            if (name is not None and name.atoms[1].text == pin_name) or (
                number is not None and number.atoms[1].text == pin_name
            ):
                out.append(pin.atoms[1].text)
    return out


# ---- library symbol copies (54160c1 schematic.py lines 1456 to 1484)


def _copy_lib_symbol_from_file_cst(root, lib_path: str, symbol_name: str, new_name: str) -> bool:
    """Splice a copy of a .kicad_sym symbol node into lib_symbols.

    The node's bytes come straight from the library file (no emission
    knowledge); only the name atom is rewritten to *new_name*.
    """
    lib_root = _cst.parse(Path(lib_path).read_bytes()).lists[0]
    for s in lib_root.find_all("symbol"):
        if s.atoms[1].text == symbol_name:
            node = s.copy()
            node.atoms[1].set_text(new_name)
            _splice_lib_symbol_cst(root, node)
            return True
    return False


def _copy_system_lib_symbol_cst(root, lib_prefix: str, symbol_name: str) -> bool:
    """Copy a system-library symbol into lib_symbols under its prefixed lib_id.

    The prefix is not cosmetic: a placed symbol whose lib_id has no matching
    lib_symbols entry and no lib_name fallback segfaults kicad-cli 9.0 on
    load (measured, rc=0xC0000005). KiCad itself imports symbols prefixed.
    """
    lib_path = _resolve_system_lib(lib_prefix)
    if not lib_path:
        return False
    return _copy_lib_symbol_from_file_cst(
        root, lib_path, symbol_name, f"{lib_prefix}:{symbol_name}"
    )


# ---- placement (54160c1 schematic.py lines 702 to 881 and 1880 to 1955, decorators dropped)


def place_component(
    lib_id: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    rotation: Literal[0, 90, 180, 270] = 0,
    symbol_lib_path: str = "",
    mirror: Literal["", "x", "y"] = "",
    schematic_path: str = SCH_PATH,
    project_path: str = "",
) -> str:
    """Place a schematic symbol: resistor, capacitor, inductor, diode, LED,
    transistor, crystal, switch, connector, or an IC such as a microcontroller
    or regulator chip. Any lib_id in a library works.

    Args:
        lib_id: Library identifier (e.g. "Device:R", "Device:C", "MyLib:MyPart")
        reference: Reference designator (e.g. "R1", "U1")
        value: Component value (e.g. "10K", "100nF")
        x: X position in schematic units (mm)
        y: Y position in schematic units (mm)
        rotation: Rotation angle in degrees (0, 90, 180, 270)
        symbol_lib_path: Path to .kicad_sym file if using custom library.
            Optional; omit to use the configured default.
        mirror: Mirror axis ("x", "y", or "" for none)
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        project_path: Path to .kicad_pro file (for correct hierarchy resolution in sub-sheets)
    """
    # Validate reference designator
    if not _VALID_REF_RE.match(reference):
        raise ToolError(
            f"'{reference}' is not a valid KiCad reference designator. "
            "Must match pattern [A-Z]+[0-9]+[A-Z]* (e.g. 'R1', 'U2', 'C5B')."
        )

    # Literal publishes the enum so a model picks a valid value; this check
    # still matters because Literal is only enforced by pydantic at the MCP
    # boundary, and a direct Python call sails straight past it. The value used
    # to be written into the file verbatim, so "Y" produced (mirror Y).
    mirror = mirror.lower()  # type: ignore[assignment]
    if mirror not in ("", "x", "y"):
        raise ToolError(f'Unknown mirror axis: {mirror!r}. Use "x", "y", or "" for none.')

    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)

    # A reference designator identifies one component, so a second symbol
    # carrying it corrupts the netlist, the BOM and any PCB update. Nothing
    # downstream catches it: measured 2026-08-12, `kicad-cli sch erc` reports no
    # duplicate-reference violation even for a resistor and a capacitor both
    # called R1, so this tool is the only place it can be caught. Nothing in the
    # package produces one legitimately either, since add_power_symbol already
    # scans for a free #FLG number and this tool has no unit parameter, so it
    # can never be placing a second unit of a multi-unit part.
    if any(_sym_property_cst(sym, "Reference") == reference for sym in root.find_all("symbol")):
        raise ToolError(
            f"{reference} is already placed in this schematic. Reference designators"
            " must be unique: use a different one, or move_component to reposition"
            " the existing symbol."
        )

    _bounds_check(x, y, page_w, page_h, page_name)

    # Snap placement to grid
    x = _snap_grid(x)
    y = _snap_grid(y)

    # Load symbol definition from custom lib or system library
    symbol_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    suggestions_lib = None
    if _find_lib_symbol_cst(root, lib_id) is None:
        if symbol_lib_path:
            _copy_lib_symbol_from_file_cst(root, symbol_lib_path, symbol_name, symbol_name)
            suggestions_lib = symbol_lib_path
        elif ":" in lib_id:
            lib_prefix = lib_id.split(":")[0]
            if not _copy_system_lib_symbol_cst(root, lib_prefix, symbol_name):
                suggestions_lib = _resolve_system_lib(lib_prefix)

    # Check if lib_symbol was found; give helpful error if not
    if _find_lib_symbol_cst(root, lib_id) is None and ":" in lib_id:
        if suggestions_lib is not None:
            lib_root = _cst.parse(Path(suggestions_lib).read_bytes()).lists[0]
            available = [s.atoms[1].text for s in lib_root.find_all("symbol")]
            similar = difflib.get_close_matches(symbol_name, available, n=5, cutoff=0.4)
            lib_prefix = lib_id.split(":")[0]
            if similar:
                hint = f" Similar: {', '.join(similar)}"
            else:
                hint = " Try list_lib_symbols to search across all libraries."
            raise ToolError(f"symbol '{symbol_name}' not found in {lib_prefix} library.{hint}")

    # Create instance: lib_name mirrors the lib_symbol's stored name so KiCad
    # can resolve the lookup without crashing (see the slice-8 segfault note).
    lib_sym = _find_lib_symbol_cst(root, lib_id)
    node = _SYMBOL_TPL.copy()
    lib_name = lib_sym.atoms[1].text if lib_sym is not None else symbol_name
    node.find("lib_name").atoms[1].set_text(lib_name)
    node.find("lib_id").atoms[1].set_text(lib_id)
    _check_rotation(rotation)
    _fill_at(node, x, y, rotation)
    if mirror:
        m = _cst.parse(b"(mirror x)").lists[0]
        m.atoms[1].set_text(mirror)
        node.insert_after(node.find("at"), m)
    sym_uuid = _gen_uuid()
    node.find("uuid").atoms[1].set_text(sym_uuid)

    # Properties
    props = node.find_all("property")
    props[0].atoms[2].set_text(reference)
    _fill_at(props[0], x, round(y - 3.81, 4))
    props[1].atoms[2].set_text(value)
    _fill_at(props[1], x, round(y + 3.81, 4))
    _fill_at(props[2], x, y)
    _fill_at(props[3], x, y)

    # Pin UUIDs from the lib symbol
    instances = node.find("instances")
    if lib_sym is not None:
        pin_nums = set()
        for unit_node in lib_sym.find_all("symbol"):
            for pin in unit_node.find_all("pin"):
                number = pin.find("number")
                if number is not None:
                    pin_nums.add(number.atoms[1].text)
        for pn in sorted(pin_nums):
            pnode = _PIN_REF_TPL.copy()
            pnode.atoms[1].set_text(pn)
            pnode.find("uuid").atoms[1].set_text(_gen_uuid())
            node.insert_before(instances, pnode)

    # Instances block: required by KiCad 9 for proper annotation
    root_uuid = _node_uuid(root)
    if project_path:
        project_name, sheet_path = _resolve_hierarchy_path(project_path, schematic_path, root_uuid)
    else:
        project_name = Path(schematic_path).stem
        sheet_path = f"/{root_uuid}"
    project = instances.find("project")
    project.atoms[1].set_text(project_name)
    ipath = project.find("path")
    ipath.atoms[1].set_text(sheet_path)
    ipath.find("reference").atoms[1].set_text(reference)

    # If this is a sub-sheet in a parent project, also add parent instance
    if project_path:
        sch_dir = Path(schematic_path).parent
        target_name = Path(schematic_path).name
        for pro_file in sch_dir.glob("*.kicad_pro"):
            parent_sch_path = pro_file.with_suffix(".kicad_sch")
            if not parent_sch_path.exists():
                continue
            if str(parent_sch_path.resolve()) == str(Path(schematic_path).resolve()):
                continue
            parent_root = _cst.parse(parent_sch_path.read_bytes()).lists[0]
            for s in parent_root.find_all("sheet"):
                if _sheet_file_cst(s) == target_name:
                    extra = project.copy()
                    extra.atoms[1].set_text(pro_file.stem)
                    epath = extra.find("path")
                    epath.atoms[1].set_text(f"/{_node_uuid(parent_root)}/{_node_uuid(s)}")
                    epath.find("reference").atoms[1].set_text(reference)
                    instances.insert_after(project, extra)
                    break
            else:
                continue
            break

    _splice_sch_node(root, "symbol", node)
    _atomic_write(schematic_path, _cst.serialize(tree))
    _upsert_root_symbol_instance(
        schematic_path,
        project_path,
        sym_uuid,
        reference=reference,
        value=value,
        footprint="",
    )
    return f"Placed {reference} ({value}) at ({x}, {y})"


def add_power_symbol(
    lib_id: str,
    reference: str,
    x: float,
    y: float,
    rotation: Literal[0, 90, 180, 270] = 0,
    symbol_lib_path: str = "",
    schematic_path: str = SCH_PATH,
    project_path: str = "",
) -> str:
    """Place a power symbol (VCC, GND, +3V3, etc.).

    Uses place_component internally. Power symbols are regular symbols
    from the 'power' library with isPower=True.

    Automatically places a PWR_FLAG at the same position so the net
    satisfies ERC (power pin driven).

    Args:
        lib_id: Library ID (e.g. "power:VCC", "power:GND")
        reference: Reference (e.g. "#PWR01")
        x: X position
        y: Y position
        rotation: Rotation in degrees
        symbol_lib_path: Path to power symbol .kicad_sym if not in schematic.
            Optional; omit to use the configured default.
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        project_path: Path to .kicad_pro file (for sub-sheet instance tracking)
    """
    result = place_component(
        lib_id=lib_id,
        reference=reference,
        value=lib_id.split(":")[-1],
        x=x,
        y=y,
        rotation=rotation,
        symbol_lib_path=symbol_lib_path,
        schematic_path=schematic_path,
        project_path=project_path,
    )

    # Don't auto-add PWR_FLAG if we just placed one
    symbol_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    if symbol_name == "PWR_FLAG":
        return result

    # Auto-place PWR_FLAG at the same position for ERC compliance
    pwr_lib = symbol_lib_path or _resolve_system_lib("power")

    if pwr_lib:
        _, root, *_ = _open_sch_cst(schematic_path)
        existing = {
            r
            for sym in root.find_all("symbol")
            for r in [_sym_property_cst(sym, "Reference")]
            if r is not None and r.startswith("#FLG")
        }
        n = 1
        while f"#FLG{n:02d}" in existing:
            n += 1
        flg_ref = f"#FLG{n:02d}"

        place_component(
            lib_id="power:PWR_FLAG",
            reference=flg_ref,
            value="PWR_FLAG",
            x=x,
            y=y,
            rotation=0,
            symbol_lib_path=pwr_lib,
            schematic_path=schematic_path,
            project_path=project_path,
        )
        result += f" + {flg_ref}"

    return result


# ---- wires and junctions (54160c1 schematic.py lines 248 to 274, 1030 to 1049, 1214 to 1231
# and 2300 to 2387, decorators dropped)


def _point_on_wire_interior(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    tol: float = 0.01,
) -> bool:
    """Check if point (px, py) lies on the interior of wire segment (a->b).

    Only handles axis-aligned (horizontal/vertical) wires. Returns False
    for diagonal wires and for points at segment endpoints.
    """
    # Horizontal wire
    if abs(ay - by) < tol:
        if abs(py - ay) < tol:
            lo, hi = min(ax, bx), max(ax, bx)
            if lo + tol < px < hi - tol:
                return True
    # Vertical wire
    if abs(ax - bx) < tol:
        if abs(px - ax) < tol:
            lo, hi = min(ay, by), max(ay, by)
            if lo + tol < py < hi - tol:
                return True
    return False


def add_wires(wires: list[WireSpec], schematic_path: str = SCH_PATH) -> str:
    """Add multiple wires at once. Each wire dict has keys: x1, y1, x2, y2.

    Args:
        wires: List of wire defs [{x1, y1, x2, y2}, ...]
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    for w in wires:
        for xk, yk in [("x1", "y1"), ("x2", "y2")]:
            _bounds_check(w[xk], w[yk], page_w, page_h, page_name)
    all_points = []
    for w in wires:
        all_points += _splice_wire(root, w["x1"], w["y1"], w["x2"], w["y2"])
    # Auto-add junctions where new wire endpoints hit wire interiors (the scan
    # includes the wires just spliced, matching the kiutils-era behavior)
    _auto_junctions_cst(root, all_points)
    _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Added {len(wires)} wires"


def _auto_junctions_cst(root, new_points: list[tuple[float, float]], tol: float = 0.01) -> None:
    """CST twin of _auto_junctions for the guard-free wire path."""
    junctions = [_node_xy(j) for j in root.find_all("junction")]
    for px, py in new_points:
        if any(abs(jx - px) < tol and abs(jy - py) < tol for jx, jy in junctions):
            continue
        for wire in root.find_all("wire"):
            pts = _wire_xys(wire)
            if len(pts) < 2:
                continue
            (ax, ay), (bx, by) = pts[0], pts[1]
            if _point_on_wire_interior(px, py, ax, ay, bx, by, tol):
                node = _JUNCTION_TPL.copy()
                _fill_at(node, px, py)
                node.find("uuid").atoms[1].set_text(_gen_uuid())
                _splice_sch_node(root, "junction", node)
                junctions.append((px, py))
                break


def connect_pins(
    ref1: str,
    pin1: str,
    ref2: str,
    pin2: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Connect two component pins with Manhattan (L-shaped) wire routing.

    Combines get_pin_positions + coordinate math + add_wires into one call.

    Args:
        ref1: First component reference (e.g. "U1")
        pin1: First pin name or number
        ref2: Second component reference (e.g. "C3")
        pin2: Second pin name or number
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    x1, y1, _ = _package_get_pin_pos_cst(root, ref1, pin1)
    x2, y2, _ = _package_get_pin_pos_cst(root, ref2, pin2)
    if x1 == x2 or y1 == y2:
        # Axis-aligned: single straight wire
        segments = [(x1, y1, x2, y2)]
    else:
        # L-shaped: horizontal from pin1 to x2, then vertical to pin2
        segments = [(x1, y1, x2, y1), (x2, y1, x2, y2)]
    for seg in segments:
        _splice_wire(root, *seg)

    # Collect all new wire endpoints (pin positions + L-shape corner)
    new_points = [(x1, y1), (x2, y2)]
    if x1 != x2 and y1 != y2:
        new_points.append((x2, y1))  # L-shape corner
    _auto_junctions_cst(root, new_points)

    # Auto-add net label for hierarchical ERC compatibility
    # Walk wires from both pin endpoints to find all connected points,
    # then skip if any connected point already has a label.
    tol = 0.01
    wire_ends = []
    for wire in root.find_all("wire"):
        pts = _wire_xys(wire)
        if len(pts) >= 2:
            wire_ends.append((pts[0], pts[-1]))

    def _connected_points(seed_x: float, seed_y: float) -> set[tuple[float, float]]:
        """BFS over wires to collect all points electrically connected to seed."""
        visited: set[tuple[float, float]] = set()
        queue = [(seed_x, seed_y)]
        while queue:
            cx, cy = queue.pop()
            if (cx, cy) in visited:
                continue
            visited.add((cx, cy))
            for (p0x, p0y), (p1x, p1y) in wire_ends:
                if abs(p0x - cx) < tol and abs(p0y - cy) < tol:
                    nxt = (p1x, p1y)
                    if nxt not in visited:
                        queue.append(nxt)
                elif abs(p1x - cx) < tol and abs(p1y - cy) < tol:
                    nxt = (p0x, p0y)
                    if nxt not in visited:
                        queue.append(nxt)
        return visited

    net_points = _connected_points(x1, y1) | _connected_points(x2, y2)

    label_positions = [
        _node_xy(n) for token in ("label", "global_label") for n in root.find_all(token)
    ]
    has_label = any(
        abs(lx - px) < tol and abs(ly - py) < tol
        for lx, ly in label_positions
        for px, py in net_points
    )
    if not has_label:
        node = _LABEL_TPL.copy()
        node.atoms[1].set_text(f"Net-({ref1}-{pin1})")
        _fill_at(node, round(x1, 4), round(y1, 4), 0)
        node.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(root, "label", node)

    _atomic_write(schematic_path, _cst.serialize(tree))

    n = len(segments)
    return f"Connected {ref1}:{pin1} -> {ref2}:{pin2} via {n} wire segment{'s' if n > 1 else ''}"
