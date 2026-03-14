"""KiCad Schematic MCP Server — schematic manipulation, ERC analysis, and schematic export tools."""

import difflib
import json
import math
import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    OUTPUT_DIR,
    SCH_PATH,
    ColorRGBA,
    Connection,
    Effects,
    Font,
    GlobalLabel,
    HierarchicalLabel,
    Junction,
    LocalLabel,
    NoConnect,
    Position,
    Property,
    SchematicSymbol,
    SymbolLib,
    SymbolProjectInstance,
    SymbolProjectPath,
    Text,
    _default_effects,
    _default_stroke,
    _file_meta,
    _gen_uuid,
    _load_sch,
    _load_system_lib_symbol,
    _remove_root_symbol_instance,
    _resolve_hierarchy_path,
    _resolve_root,
    _resolve_system_lib,
    _run_cli,
    _save_sch,
    _snap_grid,
    _sym_ref_val_fp,
    _upsert_root_symbol_instance,
)

mcp = FastMCP(
    "kicad-schematic",
    instructions=(
        "KiCad schematic manipulation, ERC analysis, and schematic export tools.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write .kicad_sch files directly. All schematic"
        " manipulation MUST go through these MCP tools. The S-expression format"
        " is fragile and manual edits will corrupt the file.\n"
        "- NEVER run kicad-cli commands directly. Use the export and ERC tools"
        " provided by this server instead.\n"
        "- NEVER grep/search inside .kicad_sch files for coordinates or data."
        " Use get_pin_positions, list_components, list_labels, get_net_connections.\n"
        "- When a tool returns an error, try different parameters or a different"
        " MCP tool. Do NOT fall back to manual file editing.\n\n"
        "WIRING WORKFLOW:\n"
        "1. Place components with place_component\n"
        "2. Discover pin names with get_pin_positions\n"
        "3. Wire using wire_pin_to_label (pin-to-net) or connect_pins (pin-to-pin)\n"
        "4. For bulk wiring, use wire_pins_to_net (multiple pins to one net)\n"
        "5. Verify with list_labels and get_net_connections\n\n"
        "CLEANUP WORKFLOW:\n"
        "- To find existing wires before removal, use"
        " list_schematic_items(item_type='wires') which returns x1/y1/x2/y2"
        " endpoints for every wire segment. Pass those coordinates to remove_wire.\n\n"
        "ERC WORKFLOW:\n"
        "1. Run run_erc to get violations\n"
        "2. Fix 'power pin not driven' with add_power_symbol (lib_id='power:PWR_FLAG')\n"
        "3. Fix unconnected pins with wire_pin_to_label or no_connect_pin\n"
        "4. Re-run run_erc to verify fixes\n"
        "5. If blocked, report the error — do NOT edit the schematic file manually\n\n"
        "HIERARCHY WORKFLOW:\n"
        "1. Create hierarchy with add_hierarchical_sheet (project server)\n"
        "2. Add hierarchical labels with add_hierarchical_label to connect sub-sheets\n"
        "3. List items with list_schematic_items (hierarchical_labels, sheets)\n"
        "4. Trace nets with get_net_connections (multi-hop BFS)\n"
        "5. Run run_erc from root with project_path for validation"
    ),
)


# Standard KiCad page sizes in mm (width, height) — landscape orientation
_PAGE_SIZES: dict[str, tuple[float, float]] = {
    "A5": (210, 148),
    "A4": (297, 210),
    "A3": (420, 297),
    "A2": (594, 420),
    "A1": (841, 594),
    "A0": (1189, 841),
    "A": (279.4, 215.9),
    "B": (431.8, 279.4),
    "C": (558.8, 431.8),
    "D": (863.6, 558.8),
    "E": (1117.6, 863.6),
}

_VALID_REF_RE = re.compile(r"^#?[A-Z]+[0-9]+[A-Z]*$")


def _get_page_size(sch) -> tuple[float, float]:
    """Return (width, height) in mm for the schematic's page setting."""
    paper = sch.paper
    size_name = paper.paperSize
    if size_name == "User":
        w = paper.width or 297
        h = paper.height or 210
    else:
        w, h = _PAGE_SIZES.get(size_name, (297, 210))
    if getattr(paper, "portrait", False):
        w, h = h, w
    return w, h


def _validate_position(x: float, y: float, sch) -> str | None:
    """Return an error string if (x, y) is outside the sheet boundary, else None."""
    page_w, page_h = _get_page_size(sch)
    if x < 0 or x > page_w or y < 0 or y > page_h:
        page_name = sch.paper.paperSize if sch.paper else "A4"
        sizes = ", ".join(_PAGE_SIZES.keys())
        return (
            f"Error: position ({x}, {y}) is outside the sheet boundary "
            f"({page_w}x{page_h}mm, page '{page_name}'). "
            f"Use set_page_size to resize (available: {sizes}, or 'User')."
        )
    return None


def _find_lib_symbol(sch, lib_id: str):
    """Find a lib_symbol by lib_id, checking both bare and prefixed names.

    KiCad schematics may store lib_symbols with the library prefix
    (e.g. ``"Device:C"``) or without (e.g. ``"C"``).  This helper
    normalises the lookup so callers don't need to worry about which
    convention the file uses.

    Returns the matching Symbol object, or ``None``.
    """
    bare = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    for ls in sch.libSymbols:
        if ls.entryName == bare or ls.entryName == lib_id:
            return ls
        # kiutils exposes libId with the library prefix even when
        # entryName returns the bare name.
        if getattr(ls, "libId", None) == lib_id:
            return ls
    return None


def _lib_symbol_file_name(ls) -> str:
    """Return the name as it appears in the file (may include library prefix).

    kiutils' ``entryName`` always strips the library prefix, but the
    file may store ``"Device:C"`` or just ``"C"``.  ``libId`` preserves
    the original.
    """
    return getattr(ls, "libId", None) or ls.entryName


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


def _get_pin_pos(sch, reference: str, pin_name: str) -> tuple[float, float, float]:
    """Return absolute (x, y, outward_angle_deg) for a placed component's pin.

    Matches pin by name (e.g. "IN", "GND") first, then by number (e.g. "1").
    If multiple pins share a name, returns the first match.
    Raises ValueError if reference or pin not found.
    """
    target = None
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference for p in sym.properties):
            target = sym
            break
    if target is None:
        raise ValueError(f"Component {reference} not found")

    lib_sym = _find_lib_symbol(sch, target.libId)
    if lib_sym is None:
        raise ValueError(f"Lib symbol for {reference} not found")

    cx, cy = target.position.X, target.position.Y
    comp_angle = target.position.angle or 0
    mir = getattr(target, "mirror", None)

    for unit in lib_sym.units:
        for pin in unit.pins:
            if pin.name == pin_name or pin.number == pin_name:
                return _transform_pin_pos(
                    pin.position.X,
                    pin.position.Y,
                    pin.position.angle or 0,
                    cx,
                    cy,
                    comp_angle,
                    mir,
                )

    raise ValueError(f"Pin '{pin_name}' not found on {reference}")


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


def _auto_junctions(sch, new_points: list[tuple[float, float]], tol: float = 0.01):
    """Add junctions where new wire endpoints land on existing wire interiors.

    Checks each point in new_points against all wire segments in
    sch.graphicalItems. If a point is on a wire's interior (not at its
    endpoint), and no junction already exists there, a Junction is added.
    """
    for px, py in new_points:
        # Skip if junction already exists here
        if any(
            abs(j.position.X - px) < tol and abs(j.position.Y - py) < tol for j in sch.junctions
        ):
            continue

        for item in sch.graphicalItems:
            if not (isinstance(item, Connection) and item.type == "wire"):
                continue
            if len(item.points) < 2:
                continue
            ax, ay = item.points[0].X, item.points[0].Y
            bx, by = item.points[1].X, item.points[1].Y
            if _point_on_wire_interior(px, py, ax, ay, bx, by, tol):
                sch.junctions.append(
                    Junction(
                        position=Position(X=px, Y=py),
                        diameter=0,
                        color=ColorRGBA(R=0, G=0, B=0, A=0),
                        uuid=_gen_uuid(),
                    )
                )
                break  # One junction per point is enough


# ---------------------------------------------------------------------------
# Schematic read tools (8)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ_ONLY)
def list_schematic_items(item_type: str, schematic_path: str = SCH_PATH) -> str:
    """List schematic items by type.

    Args:
        item_type: One of "summary", "components", "labels", "wires", "global_labels",
            "hierarchical_labels", "sheets", "junctions", "no_connects", "bus_entries"
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    if item_type == "summary":
        page_w, page_h = _get_page_size(sch)
        wire_count = sum(
            1 for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"
        )
        return (
            f"Page: {sch.paper.paperSize} ({page_w}x{page_h}mm)\n"
            f"Components: {len(sch.schematicSymbols)}\n"
            f"Labels: {len(sch.labels)}\n"
            f"Global labels: {len(sch.globalLabels)}\n"
            f"Hierarchical labels: {len(sch.hierarchicalLabels)}\n"
            f"Sheets: {len(sch.sheets)}\n"
            f"Wires: {wire_count}\n"
            f"Junctions: {len(sch.junctions)}\n"
            f"No-connects: {len(sch.noConnects)}"
        )
    elif item_type == "components":
        items = []
        for sym in sch.schematicSymbols:
            ref = next((p.value for p in sym.properties if p.key == "Reference"), "?")
            val = next((p.value for p in sym.properties if p.key == "Value"), "?")
            pos = sym.position
            items.append(
                {
                    "reference": ref,
                    "value": val,
                    "lib_id": sym.libId,
                    "x": pos.X,
                    "y": pos.Y,
                    "rotation": pos.angle,
                }
            )
        return json.dumps(items)
    elif item_type == "labels":
        items = []
        for label in sch.labels:
            items.append({"text": label.text, "x": label.position.X, "y": label.position.Y})
        return json.dumps(items)
    elif item_type == "wires":
        items = []
        for item in sch.graphicalItems:
            if isinstance(item, Connection) and item.type == "wire":
                p = item.points
                if len(p) >= 2:
                    items.append({"x1": p[0].X, "y1": p[0].Y, "x2": p[1].X, "y2": p[1].Y})
        return json.dumps(items)
    elif item_type == "global_labels":
        items = []
        for gl in sch.globalLabels:
            items.append(
                {
                    "text": gl.text,
                    "shape": gl.shape,
                    "x": gl.position.X,
                    "y": gl.position.Y,
                }
            )
        return json.dumps(items)
    elif item_type == "hierarchical_labels":
        items = []
        for hl in sch.hierarchicalLabels:
            items.append(
                {
                    "text": hl.text,
                    "shape": hl.shape,
                    "x": hl.position.X,
                    "y": hl.position.Y,
                    "rotation": hl.position.angle or 0,
                    "uuid": hl.uuid,
                }
            )
        return json.dumps(items)
    elif item_type == "sheets":
        items = []
        for sheet in sch.sheets:
            items.append(
                {
                    "sheet_name": sheet.sheetName.value,
                    "file_name": sheet.fileName.value,
                    "x": sheet.position.X,
                    "y": sheet.position.Y,
                    "width": sheet.width,
                    "height": sheet.height,
                    "pin_count": len(sheet.pins),
                    "uuid": sheet.uuid,
                }
            )
        return json.dumps(items)
    elif item_type == "junctions":
        items = []
        for j in sch.junctions:
            items.append(
                {
                    "x": j.position.X,
                    "y": j.position.Y,
                    "diameter": j.diameter,
                }
            )
        return json.dumps(items)
    elif item_type == "no_connects":
        items = []
        for nc in sch.noConnects:
            items.append(
                {
                    "x": nc.position.X,
                    "y": nc.position.Y,
                }
            )
        return json.dumps(items)
    elif item_type == "bus_entries":
        items = []
        for be in sch.busEntries:
            items.append(
                {
                    "x": be.position.X,
                    "y": be.position.Y,
                    "size_x": be.size.X,
                    "size_y": be.size.Y,
                }
            )
        return json.dumps(items)
    else:
        return json.dumps(
            {
                "error": f"Unknown item_type: {item_type}. "
                "Use: summary, components, labels, wires, global_labels, "
                "hierarchical_labels, sheets, junctions, no_connects, bus_entries"
            }
        )


@mcp.tool(annotations=_READ_ONLY)
def get_symbol_pins(symbol_name: str, schematic_path: str = SCH_PATH) -> str:
    """Get pin info for a symbol in the schematic's lib_symbols.

    Args:
        symbol_name: Symbol name (e.g. "LM7805", "C", "Fuse")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    ls = _find_lib_symbol(sch, symbol_name)
    if ls:
        lines = [f"Symbol: {symbol_name}"]
        for unit in ls.units:
            for pin in unit.pins:
                lines.append(
                    f"  Pin {pin.number}: {pin.name} "
                    f"({pin.electricalType}) "
                    f"@ ({pin.position.X}, {pin.position.Y}) "
                    f"rot={pin.position.angle} len={pin.length}"
                )
        return "\n".join(lines)
    return f"'{symbol_name}' not found."


@mcp.tool(annotations=_READ_ONLY)
def get_pin_positions(reference: str, schematic_path: str = SCH_PATH) -> str:
    """Get absolute pin positions for a placed component (accounts for rotation/mirror).

    Args:
        reference: Component reference (e.g. "U1", "R1")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)

    target = None
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference for p in sym.properties):
            target = sym
            break
    if target is None:
        return f"{reference} not found."

    symbol_name = target.libId.split(":")[-1] if ":" in target.libId else target.libId
    lib_sym = _find_lib_symbol(sch, target.libId)
    if lib_sym is None:
        return f"Lib symbol for {reference} not found."

    cx, cy = target.position.X, target.position.Y
    angle_deg = target.position.angle or 0
    mir = getattr(target, "mirror", None)

    lines = [f"{reference} ({symbol_name}) @ ({cx}, {cy}) rot={angle_deg} mirror={mir}"]

    for unit in lib_sym.units:
        for pin in unit.pins:
            final_x, final_y, _ = _transform_pin_pos(
                pin.position.X,
                pin.position.Y,
                pin.position.angle or 0,
                cx,
                cy,
                angle_deg,
                mir,
            )
            lines.append(
                f"  Pin {pin.number} ({pin.name}): ({round(final_x, 2)}, {round(final_y, 2)})"
            )

    return "\n".join(lines)


@mcp.tool(annotations=_READ_ONLY)
def get_net_connections(
    label_text: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Find all component pins connected to a net label.

    Scans labels matching the text, traces wires from label positions,
    and identifies component pins at wire endpoints.

    Args:
        label_text: Net name to search for (e.g. "VCC", "GND")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    tol = 0.1

    # Collect all label positions for this net
    label_positions: set[tuple[float, float]] = set()
    for lbl in sch.labels:
        if lbl.text == label_text:
            label_positions.add((lbl.position.X, lbl.position.Y))
    for glbl in sch.globalLabels:
        if glbl.text == label_text:
            label_positions.add((glbl.position.X, glbl.position.Y))

    # BFS: expand from label positions through connected wire endpoints
    reachable: set[tuple[float, float]] = set(label_positions)
    frontier = set(label_positions)
    while frontier:
        next_frontier: set[tuple[float, float]] = set()
        for fx, fy in frontier:
            for item in sch.graphicalItems:
                if not (isinstance(item, Connection) and item.type == "wire"):
                    continue
                if len(item.points) < 2:
                    continue
                p0, p1 = item.points[0], item.points[1]
                if abs(p0.X - fx) < tol and abs(p0.Y - fy) < tol:
                    pt = (p1.X, p1.Y)
                    if pt not in reachable:
                        reachable.add(pt)
                        next_frontier.add(pt)
                elif abs(p1.X - fx) < tol and abs(p1.Y - fy) < tol:
                    pt = (p0.X, p0.Y)
                    if pt not in reachable:
                        reachable.add(pt)
                        next_frontier.add(pt)
        frontier = next_frontier

    # Find component pins at reachable positions
    connections = []
    for sym in sch.schematicSymbols:
        ref = next(
            (p.value for p in sym.properties if p.key == "Reference"),
            None,
        )
        if ref is None:
            continue
        lib_sym = _find_lib_symbol(sch, sym.libId)
        if lib_sym is None:
            continue
        cx, cy = sym.position.X, sym.position.Y
        comp_angle = sym.position.angle or 0
        mir = getattr(sym, "mirror", None)
        for unit in lib_sym.units:
            for pin in unit.pins:
                px, py, _ = _transform_pin_pos(
                    pin.position.X,
                    pin.position.Y,
                    pin.position.angle or 0,
                    cx,
                    cy,
                    comp_angle,
                    mir,
                )
                for rx, ry in reachable:
                    if abs(px - rx) < tol and abs(py - ry) < tol:
                        connections.append(
                            {
                                "reference": ref,
                                "pin": pin.number,
                                "pin_name": pin.name,
                                "x": px,
                                "y": py,
                            }
                        )
    return json.dumps(
        {
            "net": label_text,
            "label_count": len(label_positions),
            "connections": connections,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Schematic write tools (19)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_ADDITIVE)
def place_component(
    lib_id: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    rotation: float = 0,
    symbol_lib_path: str = "",
    mirror: str = "",
    schematic_path: str = SCH_PATH,
    project_path: str = "",
) -> str:
    """Place a component in the schematic.

    Args:
        lib_id: Library identifier (e.g. "Device:R", "Device:C", "MyLib:MyPart")
        reference: Reference designator (e.g. "R1", "U1")
        value: Component value (e.g. "10K", "100nF")
        x: X position in schematic units (mm)
        y: Y position in schematic units (mm)
        rotation: Rotation angle in degrees (0, 90, 180, 270)
        symbol_lib_path: Path to .kicad_sym file if using custom library
        mirror: Mirror axis ("x", "y", or "" for none)
        schematic_path: Path to .kicad_sch file
        project_path: Path to .kicad_pro file (for correct hierarchy resolution in sub-sheets)
    """
    # Validate reference designator
    if not _VALID_REF_RE.match(reference):
        return (
            f"Error: '{reference}' is not a valid KiCad reference designator. "
            "Must match pattern [A-Z]+[0-9]+[A-Z]* (e.g. 'R1', 'U2', 'C5B')."
        )

    sch = _load_sch(schematic_path)

    # Validate position against page boundaries
    err = _validate_position(x, y, sch)
    if err:
        return err

    # Snap placement to grid
    x = _snap_grid(x)
    y = _snap_grid(y)

    # Load symbol definition from custom lib or system library
    symbol_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    _loaded_sym_lib = None
    if not _find_lib_symbol(sch, lib_id):
        if symbol_lib_path:
            _loaded_sym_lib = SymbolLib.from_file(symbol_lib_path)
            for s in _loaded_sym_lib.symbols:
                if s.entryName == symbol_name:
                    sch.libSymbols.append(s)
                    break
        elif ":" in lib_id:
            lib_prefix = lib_id.split(":")[0]
            if not _load_system_lib_symbol(sch, lib_prefix, symbol_name):
                # Load full lib for error suggestions
                lib_path = _resolve_system_lib(lib_prefix)
                if lib_path:
                    _loaded_sym_lib = SymbolLib.from_file(lib_path)

    # Check if lib_symbol was found; give helpful error if not
    if not _find_lib_symbol(sch, lib_id) and ":" in lib_id:
        if _loaded_sym_lib is not None:
            available = [s.entryName for s in _loaded_sym_lib.symbols]
            similar = difflib.get_close_matches(symbol_name, available, n=5, cutoff=0.4)
            lib_prefix = lib_id.split(":")[0]
            hint = ""
            if similar:
                hint = f" Similar: {', '.join(similar)}"
            else:
                hint = " Try list_lib_symbols to search across all libraries."
            return f"Error: symbol '{symbol_name}' not found in {lib_prefix} library.{hint}"

    # Create instance — set libName to match the lib_symbol's name as stored
    # in the file so KiCad can resolve the lookup without crashing.
    lib_sym = _find_lib_symbol(sch, lib_id)
    sym = SchematicSymbol()
    sym.libId = lib_id
    if lib_sym:
        sym.libName = _lib_symbol_file_name(lib_sym)
    else:
        sym.libName = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    sym.position = Position(X=x, Y=y, angle=rotation)
    sym.uuid = _gen_uuid()
    sym.unit = 1
    sym.inBom = True
    sym.onBoard = True
    if mirror:
        sym.mirror = mirror

    # Properties
    sym.properties = [
        Property(
            key="Reference",
            value=reference,
            id=0,
            effects=_default_effects(),
            position=Position(X=x, Y=y - 3.81, angle=0),
        ),
        Property(
            key="Value",
            value=value,
            id=1,
            effects=_default_effects(),
            position=Position(X=x, Y=y + 3.81, angle=0),
        ),
        Property(
            key="Footprint",
            value="",
            id=2,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=x, Y=y, angle=0),
        ),
        Property(
            key="Datasheet",
            value="~",
            id=3,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=x, Y=y, angle=0),
        ),
    ]

    # Find lib symbol and add pin UUIDs
    if lib_sym:
        pin_nums = set()
        for unit in lib_sym.units:
            for pin in unit.pins:
                pin_nums.add(pin.number)
        sym.pins = {pn: _gen_uuid() for pn in sorted(pin_nums)}

    # Instances block — required by KiCad 9 for proper annotation
    assert sch.uuid is not None, "Schematic must have a UUID before placing components"
    if project_path:
        project_name, sheet_path = _resolve_hierarchy_path(project_path, schematic_path, sch.uuid)
    else:
        project_name = Path(sch.filePath).stem if sch.filePath else ""
        sheet_path = f"/{sch.uuid}"
    sym.instances = [
        SymbolProjectInstance(
            name=project_name,
            paths=[
                SymbolProjectPath(
                    sheetInstancePath=sheet_path,
                    reference=reference,
                    unit=1,
                ),
            ],
        ),
    ]

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
            parent_sch = _load_sch(str(parent_sch_path))
            for s in parent_sch.sheets:
                if s.fileName.value == target_name:
                    parent_path = f"/{parent_sch.uuid}/{s.uuid}"
                    sym.instances.append(
                        SymbolProjectInstance(
                            name=pro_file.stem,
                            paths=[
                                SymbolProjectPath(
                                    sheetInstancePath=parent_path,
                                    reference=reference,
                                    unit=1,
                                )
                            ],
                        )
                    )
                    break
            else:
                continue
            break

    sch.schematicSymbols.append(sym)
    _save_sch(sch)
    _upsert_root_symbol_instance(
        schematic_path,
        project_path,
        sym.uuid,
        reference=reference,
        value=value,
        footprint="",
    )
    return f"Placed {reference} ({value}) at ({x}, {y})"


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_component(reference: str, schematic_path: str = SCH_PATH) -> str:
    """Remove a component by reference designator.

    Args:
        reference: Reference designator to remove (e.g. "U2")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    target = None
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference for p in sym.properties):
            target = sym
            break
    if target is None:
        return f"Component {reference} not found."
    sch.schematicSymbols.remove(target)
    _save_sch(sch)
    _remove_root_symbol_instance(schematic_path, "", target.uuid)
    return f"Removed {reference}"


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_label(
    text: str,
    x: float | None = None,
    y: float | None = None,
    schematic_path: str = SCH_PATH,
) -> str:
    """Remove net label(s) by text, optionally filtered by position.

    If x and y are provided, only removes labels matching both text AND
    position (within 0.1mm tolerance). Otherwise removes ALL labels with
    matching text.

    Args:
        text: Label text to match (e.g. "VCC", "PGND")
        x: Optional X position filter
        y: Optional Y position filter
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    tol = 0.1
    if x is not None and y is not None:
        pass  # Compare directly against stored positions — no grid snapping
    removed = []
    remaining = []
    for lbl in sch.labels:
        if lbl.text == text:
            if x is not None and y is not None:
                if abs(lbl.position.X - x) < tol and abs(lbl.position.Y - y) < tol:
                    removed.append(lbl)
                    continue
            else:
                removed.append(lbl)
                continue
        remaining.append(lbl)
    if not removed:
        return f"Label '{text}' not found."
    sch.labels = remaining
    _save_sch(sch)
    return f"Removed {len(removed)} label(s) '{text}'."


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_wire(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    schematic_path: str = SCH_PATH,
) -> str:
    """Remove a wire segment by its endpoint coordinates.

    Matches wires with endpoints within 0.1mm tolerance (in either order).
    Use list_schematic_items(item_type="wires") to get wire coordinates first.

    Args:
        x1: Start X
        y1: Start Y
        x2: End X
        y2: End Y
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    tol = 0.1
    removed = []
    remaining = []
    for item in sch.graphicalItems:
        if isinstance(item, Connection) and item.type == "wire" and len(item.points) >= 2:
            p0, p1 = item.points[0], item.points[1]
            fwd = (
                abs(p0.X - x1) < tol
                and abs(p0.Y - y1) < tol
                and abs(p1.X - x2) < tol
                and abs(p1.Y - y2) < tol
            )
            rev = (
                abs(p0.X - x2) < tol
                and abs(p0.Y - y2) < tol
                and abs(p1.X - x1) < tol
                and abs(p1.Y - y1) < tol
            )
            if fwd or rev:
                removed.append(item)
                continue
        remaining.append(item)
    if not removed:
        return f"Wire ({x1},{y1})->({x2},{y2}) not found."
    sch.graphicalItems = remaining
    _save_sch(sch)
    return f"Removed {len(removed)} wire(s)."


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_junction(
    x: float,
    y: float,
    schematic_path: str = SCH_PATH,
) -> str:
    """Remove a junction at the given coordinates.

    Args:
        x: X position
        y: Y position
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    tol = 0.1
    for i, junc in enumerate(sch.junctions):
        if abs(junc.position.X - x) < tol and abs(junc.position.Y - y) < tol:
            sch.junctions.pop(i)
            _save_sch(sch)
            return f"Removed junction at ({x}, {y})"
    return f"Junction at ({x}, {y}) not found."


@mcp.tool(annotations=_ADDITIVE)
def add_wires(wires: list[dict], schematic_path: str = SCH_PATH) -> str:
    """Add multiple wires at once. Each wire dict has keys: x1, y1, x2, y2.

    Args:
        wires: List of wire defs [{x1, y1, x2, y2}, ...]
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    for w in wires:
        for xk, yk in [("x1", "y1"), ("x2", "y2")]:
            err = _validate_position(w[xk], w[yk], sch)
            if err:
                return err
        wire = Connection(
            type="wire",
            points=[
                Position(X=round(w["x1"], 4), Y=round(w["y1"], 4)),
                Position(X=round(w["x2"], 4), Y=round(w["y2"], 4)),
            ],
            stroke=_default_stroke(),
            uuid=_gen_uuid(),
        )
        sch.graphicalItems.append(wire)
    # Auto-add junctions where new wire endpoints hit existing wire interiors
    all_points = []
    for w in wires:
        all_points.append((round(w["x1"], 4), round(w["y1"], 4)))
        all_points.append((round(w["x2"], 4), round(w["y2"], 4)))
    _auto_junctions(sch, all_points)
    _save_sch(sch)
    return f"Added {len(wires)} wires"


@mcp.tool(annotations=_ADDITIVE)
def add_label(
    text: str, x: float, y: float, rotation: float = 0, schematic_path: str = SCH_PATH
) -> str:
    """Add a net label at a position.

    Args:
        text: Net name (e.g. "VIN_PROT", "5V_REL")
        x: X position
        y: Y position
        rotation: Degrees (0=right, 90=up, 180=left, 270=down)
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    err = _validate_position(x, y, sch)
    if err:
        return err
    x, y = round(x, 4), round(y, 4)
    label = LocalLabel(
        text=text,
        position=Position(X=x, Y=y, angle=rotation),
        effects=_default_effects(),
        uuid=_gen_uuid(),
    )
    sch.labels.append(label)
    _save_sch(sch)
    return f"Label '{text}' at ({x}, {y})"


@mcp.tool(annotations=_ADDITIVE)
def add_junctions(points: list[dict], schematic_path: str = SCH_PATH) -> str:
    """Add multiple junctions. Each point dict has keys: x, y.

    Args:
        points: List of junction positions [{x, y}, ...]
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    for p in points:
        err = _validate_position(p["x"], p["y"], sch)
        if err:
            return err
        junc = Junction(
            position=Position(X=round(p["x"], 4), Y=round(p["y"], 4)),
            diameter=0,
            color=ColorRGBA(R=0, G=0, B=0, A=0),
            uuid=_gen_uuid(),
        )
        sch.junctions.append(junc)
    _save_sch(sch)
    return f"Added {len(points)} junctions"


@mcp.tool(annotations=_ADDITIVE)
def add_lib_symbol(symbol_lib_path: str, symbol_name: str, schematic_path: str = SCH_PATH) -> str:
    """Load a symbol definition from a .kicad_sym library into the schematic.

    Args:
        symbol_lib_path: Path to .kicad_sym file
        symbol_name: Symbol name (e.g. "LM7805")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    sym_lib = SymbolLib.from_file(symbol_lib_path)
    for s in sym_lib.symbols:
        if s.entryName == symbol_name:
            if _find_lib_symbol(sch, symbol_name):
                return f"'{symbol_name}' already in lib_symbols."
            sch.libSymbols.append(s)
            _save_sch(sch)
            return f"Added '{symbol_name}' to lib_symbols."
    return f"'{symbol_name}' not found in {symbol_lib_path}."


@mcp.tool(annotations=_ADDITIVE)
def move_component(
    reference: str,
    x: float,
    y: float,
    rotation: float | None = None,
    schematic_path: str = SCH_PATH,
) -> str:
    """Move a placed component to a new position.

    Args:
        reference: Reference designator (e.g. "R1")
        x: New X position
        y: New Y position
        rotation: New rotation in degrees (None = keep current)
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    err = _validate_position(x, y, sch)
    if err:
        return err
    x, y = _snap_grid(x), _snap_grid(y)
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference for p in sym.properties):
            sym.position.X = x
            sym.position.Y = y
            if rotation is not None:
                sym.position.angle = rotation
            _save_sch(sch)
            return f"Moved {reference} to ({x}, {y})"
    return f"Component {reference} not found."


@mcp.tool(annotations=_ADDITIVE)
def set_component_property(
    reference: str,
    key: str,
    value: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Set any property on a placed component. Creates it if missing.

    Args:
        reference: Component reference (e.g. "R1")
        key: Property name (e.g. "MPN", "Tolerance", "Value")
        value: Property value
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference for p in sym.properties):
            # Update existing property
            for prop in sym.properties:
                if prop.key == key:
                    prop.value = value
                    _save_sch(sch)
                    if key in ("Reference", "Value", "Footprint"):
                        ref, val, fp_val = _sym_ref_val_fp(sym)
                        _upsert_root_symbol_instance(
                            schematic_path,
                            "",
                            sym.uuid,
                            ref,
                            value=val,
                            footprint=fp_val,
                        )
                    return f"Set {reference}.{key} = {value}"
            # Create new property (hidden, at component center)
            new_id = max((p.id for p in sym.properties if p.id is not None), default=-1) + 1
            sym.properties.append(
                Property(
                    key=key,
                    value=value,
                    id=new_id,
                    effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
                    position=Position(X=sym.position.X, Y=sym.position.Y, angle=0),
                )
            )
            _save_sch(sch)
            if key in ("Reference", "Value", "Footprint"):
                ref, val, fp_val = _sym_ref_val_fp(sym)
                _upsert_root_symbol_instance(
                    schematic_path,
                    "",
                    sym.uuid,
                    ref,
                    value=val,
                    footprint=fp_val,
                )
            return f"Set {reference}.{key} = {value} (new property)"
    return f"Component {reference} not found."


@mcp.tool(annotations=_ADDITIVE)
def set_page_size(
    size: str,
    width: float | None = None,
    height: float | None = None,
    portrait: bool = False,
    schematic_path: str = SCH_PATH,
) -> str:
    """Set the schematic page/sheet size.

    Args:
        size: Standard name (A5, A4, A3, A2, A1, A0, A, B, C, D, E) or 'User' for custom
        width: Custom width in mm (required when size='User')
        height: Custom height in mm (required when size='User')
        portrait: If True, swap width/height for portrait orientation
        schematic_path: Path to .kicad_sch file
    """
    size_key = size.strip()
    if size_key == "User":
        if width is None or height is None:
            return "Error: 'User' page size requires both width and height parameters."
        w, h = float(width), float(height)
    elif size_key in _PAGE_SIZES:
        w, h = _PAGE_SIZES[size_key]
    else:
        valid = ", ".join(list(_PAGE_SIZES.keys()) + ["User"])
        return f"Error: unknown page size '{size_key}'. Valid sizes: {valid}."

    sch = _load_sch(schematic_path)
    sch.paper.paperSize = size_key
    if size_key == "User":
        sch.paper.width = w
        sch.paper.height = h
    else:
        sch.paper.width = None
        sch.paper.height = None
    sch.paper.portrait = portrait
    _save_sch(sch)

    if portrait:
        return f"Page size set to {size_key} ({h}x{w}mm, portrait)"
    return f"Page size set to {size_key} ({w}x{h}mm)"


@mcp.tool(annotations=_ADDITIVE)
def add_global_label(
    text: str,
    x: float,
    y: float,
    rotation: float = 0,
    shape: str = "input",
    schematic_path: str = SCH_PATH,
) -> str:
    """Add a global net label (visible across all sheets).

    Args:
        text: Net name (e.g. "VCC", "SDA")
        x: X position
        y: Y position
        rotation: Degrees (0=right, 90=up, 180=left, 270=down)
        shape: Label shape: input, output, bidirectional, tri_state, passive
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    err = _validate_position(x, y, sch)
    if err:
        return err
    x, y = round(x, 4), round(y, 4)
    gl = GlobalLabel(
        text=text,
        position=Position(X=x, Y=y, angle=rotation),
        shape=shape,
        effects=_default_effects(),
        uuid=_gen_uuid(),
    )
    sch.globalLabels.append(gl)
    _save_sch(sch)
    return f"Global label '{text}' ({shape}) at ({x}, {y})"


_VALID_HLABEL_SHAPES = {"input", "output", "bidirectional", "tri_state", "passive"}


@mcp.tool(annotations=_ADDITIVE)
def add_hierarchical_label(
    text: str,
    shape: str,
    x: float,
    y: float,
    rotation: float = 0,
    schematic_path: str = SCH_PATH,
) -> str:
    """Add a hierarchical label to a sub-sheet schematic.

    Args:
        text: Label name (must match parent sheet pin name)
        shape: Direction — input, output, bidirectional, tri_state, passive
        x: X position in mm
        y: Y position in mm
        rotation: Degrees (0, 90, 180, 270)
        schematic_path: Path to .kicad_sch file
    """
    if shape not in _VALID_HLABEL_SHAPES:
        return f"Error: invalid shape '{shape}'. Use: {', '.join(sorted(_VALID_HLABEL_SHAPES))}"
    sch = _load_sch(schematic_path)
    err = _validate_position(x, y, sch)
    if err:
        return err
    x, y = round(x, 4), round(y, 4)
    sch.hierarchicalLabels.append(
        HierarchicalLabel(
            text=text,
            shape=shape,
            position=Position(X=x, Y=y, angle=rotation),
            effects=_default_effects(),
            uuid=_gen_uuid(),
        )
    )
    _save_sch(sch)
    return f"Added hierarchical label '{text}' ({shape}) at ({x}, {y})"


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_hierarchical_label(
    text: str,
    schematic_path: str = SCH_PATH,
    uuid: str = "",
) -> str:
    """Remove a hierarchical label by name or UUID.

    Args:
        text: Label text to match
        schematic_path: Path to .kicad_sch file
        uuid: Optional UUID for disambiguation when multiple labels share a name
    """
    sch = _load_sch(schematic_path)
    target = None
    for hl in sch.hierarchicalLabels:
        if uuid and hl.uuid == uuid:
            target = hl
            break
        if hl.text == text and not uuid:
            target = hl
            break
    if target is None:
        return f"Hierarchical label '{text}' not found"
    sch.hierarchicalLabels.remove(target)
    _save_sch(sch)
    return f"Removed hierarchical label '{target.text}'"


@mcp.tool(annotations=_DESTRUCTIVE)
def modify_hierarchical_label(
    text: str,
    schematic_path: str = SCH_PATH,
    new_text: str = "",
    new_shape: str = "",
    new_x: float | None = None,
    new_y: float | None = None,
    uuid: str = "",
) -> str:
    """Modify an existing hierarchical label.

    Args:
        text: Current label text to find
        schematic_path: Path to .kicad_sch file
        new_text: New label text (empty = keep current)
        new_shape: New shape/direction (empty = keep current)
        new_x: New X position (None = keep current)
        new_y: New Y position (None = keep current)
        uuid: UUID for disambiguation
    """
    if new_shape and new_shape not in _VALID_HLABEL_SHAPES:
        return f"Error: invalid shape '{new_shape}'. Use: {', '.join(sorted(_VALID_HLABEL_SHAPES))}"
    sch = _load_sch(schematic_path)
    target = None
    for hl in sch.hierarchicalLabels:
        if uuid and hl.uuid == uuid:
            target = hl
            break
        if hl.text == text and not uuid:
            target = hl
            break
    if target is None:
        return f"Hierarchical label '{text}' not found"
    changes = []
    if new_text:
        target.text = new_text
        changes.append(f"text='{new_text}'")
    if new_shape:
        target.shape = new_shape
        changes.append(f"shape={new_shape}")
    if new_x is not None:
        target.position.X = round(new_x, 4)
        changes.append(f"x={new_x}")
    if new_y is not None:
        target.position.Y = round(new_y, 4)
        changes.append(f"y={new_y}")
    _save_sch(sch)
    warning = ""
    if new_text:
        warning = " Warning: update the matching sheet pin in the parent schematic."
    return f"Modified hierarchical label: {', '.join(changes)}.{warning}"


@mcp.tool(annotations=_ADDITIVE)
def add_power_symbol(
    lib_id: str,
    reference: str,
    x: float,
    y: float,
    rotation: float = 0,
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
        symbol_lib_path: Path to power symbol .kicad_sym if not in schematic
        schematic_path: Path to .kicad_sch file
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
        sch = _load_sch(schematic_path)
        existing = {
            p.value
            for sym in sch.schematicSymbols
            for p in sym.properties
            if p.key == "Reference" and p.value.startswith("#FLG")
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


@mcp.tool(annotations=_ADDITIVE)
def auto_place_decoupling_cap(
    lib_id: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    power_net: str,
    ground_net: str,
    rotation: float = 0,
    symbol_lib_path: str = "",
    schematic_path: str = SCH_PATH,
    project_path: str = "",
) -> str:
    """Place a decoupling capacitor and wire it to power/ground nets.

    Places the cap, wires pin 1 (top) to power_net and pin 2 (bottom)
    to ground_net via stub wires + labels.

    Args:
        lib_id: Cap symbol (e.g. "Device:C")
        reference: Reference (e.g. "C5")
        value: Cap value (e.g. "100nF")
        x: X position
        y: Y position
        power_net: Label for pin 1 (e.g. "VCC", "+3V3")
        ground_net: Label for pin 2 (e.g. "GND", "PGND")
        rotation: Rotation in degrees (default 0)
        symbol_lib_path: Path to .kicad_sym if using custom lib
        schematic_path: Path to .kicad_sch file
        project_path: Path to .kicad_pro file (for sub-sheet instance tracking)
    """
    result = place_component(
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
    if result.startswith("Error"):
        return result

    # Wire pin 1 (top) to power net
    wire_pins_to_net(
        pins=[{"reference": reference, "pin": "1"}],
        label_text=power_net,
        direction="up",
        schematic_path=schematic_path,
    )

    # Wire pin 2 (bottom) to ground net
    wire_pins_to_net(
        pins=[{"reference": reference, "pin": "2"}],
        label_text=ground_net,
        direction="down",
        schematic_path=schematic_path,
    )

    return f"{result} | pin 1->{power_net} | pin 2->{ground_net}"


@mcp.tool(annotations=_ADDITIVE)
def add_text(
    text: str,
    x: float,
    y: float,
    rotation: float = 0,
    schematic_path: str = SCH_PATH,
) -> str:
    """Add a text annotation to the schematic.

    Args:
        text: Text content
        x: X position
        y: Y position
        rotation: Rotation in degrees
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    err = _validate_position(x, y, sch)
    if err:
        return err
    t = Text(
        text=text,
        position=Position(X=x, Y=y, angle=rotation),
        effects=_default_effects(),
        uuid=_gen_uuid(),
    )
    sch.texts.append(t)
    _save_sch(sch)
    return f"Text '{text}' at ({x}, {y})"


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_text(
    text: str,
    x: float | None = None,
    y: float | None = None,
    schematic_path: str = SCH_PATH,
) -> str:
    """Remove text annotation(s) by content, optionally filtered by position.

    If x and y are provided, only removes texts matching both content AND
    position (within 0.1mm tolerance). Otherwise removes ALL texts with
    matching content.

    Args:
        text: Text content to match
        x: Optional X position filter
        y: Optional Y position filter
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    tol = 0.1
    removed = []
    remaining = []
    for t in sch.texts:
        if t.text == text:
            if x is not None and y is not None:
                if abs(t.position.X - x) < tol and abs(t.position.Y - y) < tol:
                    removed.append(t)
                    continue
            else:
                removed.append(t)
                continue
        remaining.append(t)
    if not removed:
        return f"Text '{text}' not found."
    sch.texts = remaining
    _save_sch(sch)
    return f"Removed {len(removed)} text(s) '{text}'."


# ---------------------------------------------------------------------------
# High-level routing tools (4)
# ---------------------------------------------------------------------------

# Direction -> (dx_sign, dy_sign, label_rotation)
_DIR_OFFSETS = {
    "right": (1, 0, 0),
    "left": (-1, 0, 180),
    "up": (0, -1, 90),
    "down": (0, 1, 270),
}

# Outward angle (math Y-down) -> cardinal direction name
_ANGLE_TO_DIR = {0: "right", 90: "down", 180: "left", 270: "up"}


@mcp.tool(annotations=_ADDITIVE)
def wire_pins_to_net(
    pins: list[dict],
    label_text: str,
    direction: str = "auto",
    stub_length: float = 2.54,
    auto_pwr_flag: bool = True,
    schematic_path: str = SCH_PATH,
) -> str:
    """Wire multiple component pins to the same net label.

    Batch version of wire_pin_to_label. Single file load/save cycle.

    Args:
        pins: List of {"reference": "R1", "pin": "1"} dicts
        label_text: Net label text (e.g. "GND", "VCC")
        direction: Wire direction: "auto", "left", "right", "up", "down"
        stub_length: Wire stub length in mm (default 2.54)
        auto_pwr_flag: Auto-place PWR_FLAG when net has power_in but no power_out (default True)
        schematic_path: Path to .kicad_sch file
    """
    if not pins:
        return f"Wired 0 pins to '{label_text}'."
    sch = _load_sch(schematic_path)
    tol = 0.1
    warnings = []
    stub_endpoints = []
    first_power_in_pos = None  # (x, y) of first power_in stub endpoint
    has_power_out = False  # True if any wired pin is power_out
    for pin_def in pins:
        ref = pin_def["reference"]
        pin_name = pin_def["pin"]
        try:
            px, py, outward = _get_pin_pos(sch, ref, pin_name)
        except ValueError as e:
            return f"Error wiring {ref}:{pin_name}: {e}"

        if direction == "auto":
            snapped = round(outward / 90) * 90 % 360
            d = _ANGLE_TO_DIR[snapped]
        else:
            d = direction

        dx_sign, dy_sign, label_rot = _DIR_OFFSETS[d]
        end_x = round(px + dx_sign * stub_length, 4)
        end_y = round(py + dy_sign * stub_length, 4)

        # Check for stub collision with existing labels from different nets.
        # If the chosen direction produces a stub that overlaps an existing
        # label of a different net within stub_length along the same axis,
        # try alternate directions to avoid a short circuit.
        def _stub_collides(ex: float, ey: float) -> bool:
            """True if endpoint (ex, ey) collides with a different-net label."""
            for existing in sch.labels:
                if existing.text == label_text:
                    continue
                lx, ly = existing.position.X, existing.position.Y
                # Check if label is on the stub path (between pin and end)
                if dx_sign != 0 and abs(ly - py) < tol:
                    lo = min(px, ex)
                    hi = max(px, ex)
                    if lo - tol <= lx <= hi + tol:
                        return True
                if dy_sign != 0 and abs(lx - px) < tol:
                    lo = min(py, ey)
                    hi = max(py, ey)
                    if lo - tol <= ly <= hi + tol:
                        return True
                # Check endpoint overlap
                if abs(lx - ex) < tol and abs(ly - ey) < tol:
                    return True
            return False

        if _stub_collides(end_x, end_y):
            # Try alternate directions
            resolved = False
            for alt_d in _DIR_OFFSETS:
                if alt_d == d:
                    continue
                adx, ady, alt_rot = _DIR_OFFSETS[alt_d]
                alt_ex = round(px + adx * stub_length, 4)
                alt_ey = round(py + ady * stub_length, 4)
                if not _stub_collides(alt_ex, alt_ey):
                    d = alt_d
                    dx_sign, dy_sign, label_rot = adx, ady, alt_rot
                    end_x, end_y = alt_ex, alt_ey
                    resolved = True
                    break
            if not resolved:
                warnings.append(
                    f"{ref}:{pin_name} stub collides with existing net; no safe direction found"
                )

        # Wire stub
        sch.graphicalItems.append(
            Connection(
                type="wire",
                points=[
                    Position(X=px, Y=py),
                    Position(X=end_x, Y=end_y),
                ],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )
        stub_endpoints.append((px, py))
        stub_endpoints.append((end_x, end_y))
        # Net label
        sch.labels.append(
            LocalLabel(
                text=label_text,
                position=Position(X=end_x, Y=end_y, angle=label_rot),
                effects=_default_effects(),
                uuid=_gen_uuid(),
            )
        )

        # Track pin electrical types for auto PWR_FLAG logic
        if first_power_in_pos is None or not has_power_out:
            target = None
            for sym in sch.schematicSymbols:
                if any(p.key == "Reference" and p.value == ref for p in sym.properties):
                    target = sym
                    break
            if target:
                lib_sym = _find_lib_symbol(sch, target.libId)
                if lib_sym:
                    for unit in lib_sym.units:
                        for lpin in unit.pins:
                            if lpin.name == pin_name or lpin.number == pin_name:
                                if lpin.electricalType == "power_in" and first_power_in_pos is None:
                                    first_power_in_pos = (end_x, end_y)
                                if lpin.electricalType == "power_out":
                                    has_power_out = True

    _auto_junctions(sch, stub_endpoints)

    # Auto-add PWR_FLAG if net has power_in but no power_out
    if auto_pwr_flag and first_power_in_pos is not None and not has_power_out:
        # Check if PWR_FLAG already exists on this net
        has_existing_flag = False
        for sym in sch.schematicSymbols:
            if any(p.key == "Value" and p.value == "PWR_FLAG" for p in sym.properties):
                sx, sy = sym.position.X, sym.position.Y
                for lbl in sch.labels:
                    if (
                        lbl.text == label_text
                        and abs(lbl.position.X - sx) < tol
                        and abs(lbl.position.Y - sy) < tol
                    ):
                        has_existing_flag = True
                        break
            if has_existing_flag:
                break

        if not has_existing_flag:
            from kiutils.symbol import Symbol as LibSymbol
            from kiutils.symbol import SymbolPin

            # Ensure PWR_FLAG lib symbol exists
            if not _find_lib_symbol(sch, "power:PWR_FLAG"):
                # Try loading from KiCad system library first
                _loaded = _load_system_lib_symbol(sch, "power", "PWR_FLAG")
                # Fallback: synthetic symbol for CI without KiCad
                if not _loaded:
                    pwr_flag = LibSymbol()
                    pwr_flag.entryName = "PWR_FLAG"
                    pwr_flag.isPower = True
                    pwr_flag.inBom = False
                    pwr_flag.onBoard = True
                    unit0 = LibSymbol()
                    unit0.entryName = "PWR_FLAG"
                    unit0.unitId = 0
                    unit0.styleId = 1
                    unit1 = LibSymbol()
                    unit1.entryName = "PWR_FLAG"
                    unit1.unitId = 1
                    unit1.styleId = 1
                    unit1.pins = [
                        SymbolPin(
                            electricalType="power_out",
                            position=Position(X=0, Y=0, angle=90),
                            length=0,
                            name="~",
                            number="1",
                        )
                    ]
                    pwr_flag.units = [unit0, unit1]
                    sch.libSymbols.append(pwr_flag)

            # Generate unique #FLG reference
            existing_flg = {
                p.value
                for sym in sch.schematicSymbols
                for p in sym.properties
                if p.key == "Reference" and p.value.startswith("#FLG")
            }
            n = 1
            while f"#FLG{n:02d}" in existing_flg:
                n += 1
            flg_ref = f"#FLG{n:02d}"

            fx, fy = first_power_in_pos
            flg_sym = SchematicSymbol()
            flg_sym.libId = "power:PWR_FLAG"
            flg_sym.libName = "PWR_FLAG"
            flg_sym.position = Position(X=fx, Y=fy, angle=0)
            flg_sym.uuid = _gen_uuid()
            flg_sym.unit = 1
            flg_sym.inBom = False
            flg_sym.onBoard = True
            flg_sym.properties = [
                Property(
                    key="Reference",
                    value=flg_ref,
                    id=0,
                    effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
                    position=Position(X=fx, Y=fy - 3.81, angle=0),
                ),
                Property(
                    key="Value",
                    value="PWR_FLAG",
                    id=1,
                    effects=_default_effects(),
                    position=Position(X=fx, Y=fy + 3.81, angle=0),
                ),
                Property(
                    key="Footprint",
                    value="",
                    id=2,
                    effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
                    position=Position(X=fx, Y=fy, angle=0),
                ),
                Property(
                    key="Datasheet",
                    value="~",
                    id=3,
                    effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
                    position=Position(X=fx, Y=fy, angle=0),
                ),
            ]
            flg_sym.pins = {"1": _gen_uuid()}

            # Instances block — required by KiCad 9 for proper annotation
            project_name = Path(sch.filePath).stem if sch.filePath else ""
            sheet_path = f"/{sch.uuid}"
            # Check if this is a sub-sheet by looking for a .kicad_pro
            sch_dir = Path(schematic_path).parent
            pro_files = list(sch_dir.glob("*.kicad_pro"))
            if len(pro_files) == 1:
                pro = pro_files[0]
                project_name = pro.stem
                root_sch_path = pro.with_suffix(".kicad_sch")
                if root_sch_path.resolve() != Path(schematic_path).resolve():
                    try:
                        project_name, sheet_path = _resolve_hierarchy_path(
                            str(pro), schematic_path, str(sch.uuid)
                        )
                    except Exception:
                        pass  # Fall back to simple path
            flg_sym.instances = [
                SymbolProjectInstance(
                    name=project_name,
                    paths=[
                        SymbolProjectPath(
                            sheetInstancePath=sheet_path,
                            reference=flg_ref,
                            unit=1,
                        ),
                    ],
                ),
            ]

            sch.schematicSymbols.append(flg_sym)

    _save_sch(sch)
    msg = f"Wired {len(pins)} pins to '{label_text}'."
    if warnings:
        msg += " WARNINGS: " + "; ".join(warnings)
    return msg


@mcp.tool(annotations=_ADDITIVE)
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
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    x1, y1, _ = _get_pin_pos(sch, ref1, pin1)
    x2, y2, _ = _get_pin_pos(sch, ref2, pin2)

    segments = []
    if x1 == x2 or y1 == y2:
        # Axis-aligned: single straight wire
        segments.append(
            Connection(
                type="wire",
                points=[Position(X=x1, Y=y1), Position(X=x2, Y=y2)],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )
    else:
        # L-shaped: horizontal from pin1 to x2, then vertical to pin2
        mid_x, mid_y = x2, y1
        segments.append(
            Connection(
                type="wire",
                points=[Position(X=x1, Y=y1), Position(X=mid_x, Y=mid_y)],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )
        segments.append(
            Connection(
                type="wire",
                points=[Position(X=mid_x, Y=mid_y), Position(X=x2, Y=y2)],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )

    for seg in segments:
        sch.graphicalItems.append(seg)

    # Collect all new wire endpoints (pin positions + L-shape corner)
    new_points = [(x1, y1), (x2, y2)]
    if x1 != x2 and y1 != y2:
        new_points.append((x2, y1))  # L-shape corner
    _auto_junctions(sch, new_points)

    # Auto-add net label for hierarchical ERC compatibility
    # Walk wires from both pin endpoints to find all connected points,
    # then skip if any connected point already has a label.
    tol = 0.01

    def _connected_points(seed_x: float, seed_y: float) -> set[tuple[float, float]]:
        """BFS over wires to collect all points electrically connected to seed."""
        visited: set[tuple[float, float]] = set()
        queue = [(seed_x, seed_y)]
        while queue:
            cx, cy = queue.pop()
            if (cx, cy) in visited:
                continue
            visited.add((cx, cy))
            for item in sch.graphicalItems:
                if getattr(item, "type", None) != "wire" or len(item.points) < 2:
                    continue
                p0 = item.points[0]
                p1 = item.points[-1]
                if abs(p0.X - cx) < tol and abs(p0.Y - cy) < tol:
                    nxt = (p1.X, p1.Y)
                    if nxt not in visited:
                        queue.append(nxt)
                elif abs(p1.X - cx) < tol and abs(p1.Y - cy) < tol:
                    nxt = (p0.X, p0.Y)
                    if nxt not in visited:
                        queue.append(nxt)
        return visited

    net_points = _connected_points(x1, y1) | _connected_points(x2, y2)

    has_label = False
    for lbl in sch.labels:
        lx, ly = lbl.position.X, lbl.position.Y
        if any(abs(lx - px) < tol and abs(ly - py) < tol for px, py in net_points):
            has_label = True
            break
    if not has_label:
        for gl in sch.globalLabels:
            lx, ly = gl.position.X, gl.position.Y
            if any(abs(lx - px) < tol and abs(ly - py) < tol for px, py in net_points):
                has_label = True
                break
    if not has_label:
        net_name = f"Net-({ref1}-{pin1})"
        sch.labels.append(
            LocalLabel(
                text=net_name,
                position=Position(X=x1, Y=y1, angle=0),
                effects=_default_effects(),
                uuid=_gen_uuid(),
            )
        )

    _save_sch(sch)

    n = len(segments)
    return f"Connected {ref1}:{pin1} -> {ref2}:{pin2} via {n} wire segment{'s' if n > 1 else ''}"


@mcp.tool(annotations=_ADDITIVE)
def no_connect_pin(
    reference: str,
    pin_name: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Place a no-connect flag on a component pin.

    Resolves pin position and places a no-connect flag.

    Args:
        reference: Component reference (e.g. "U2")
        pin_name: Pin name (e.g. "NC") or number (e.g. "3")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    px, py, _ = _get_pin_pos(sch, reference, pin_name)
    px, py = round(px, 4), round(py, 4)

    nc = NoConnect(position=Position(X=px, Y=py), uuid=_gen_uuid())
    sch.noConnects.append(nc)
    _save_sch(sch)

    return f"No-connect on {reference}:{pin_name} at ({px}, {py})"


# ---------------------------------------------------------------------------
# ERC analysis tools (2)
# ---------------------------------------------------------------------------


def _parse_unconnected_pins(erc_report: dict, sheet_filter: str | None = None) -> list[dict]:
    """Extract unconnected pin violations from an ERC report.

    When *sheet_filter* is set, only violations from matching sheet paths
    are included.
    """
    results = []
    for sheet in erc_report.get("sheets", []):
        if sheet_filter:
            sheet_path = sheet.get("path", "")
            if sheet_filter not in sheet_path:
                continue
        for v in sheet.get("violations", []):
            desc = v.get("description", "")
            if "not connected" not in desc.lower():
                continue
            entry: dict = {"description": desc, "severity": v.get("severity", "")}
            items = v.get("items", [])
            if items:
                item_desc = items[0].get("description", "")
                entry["detail"] = item_desc
                pos = items[0].get("pos", {})
                if pos:
                    entry["x"] = pos.get("x")
                    entry["y"] = pos.get("y")
            results.append(entry)
    return results


@mcp.tool(annotations=_READ_ONLY)
def list_unconnected_pins(
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
    project_path: str = "",
) -> str:
    """List unconnected pins by running ERC and filtering results.

    Requires kicad-cli. Auto-redirects to root schematic for sub-sheets
    to avoid false positives from hierarchical label context.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Directory for ERC report file
        project_path: Path to .kicad_pro file for explicit root resolution
    """
    import shutil

    if not shutil.which("kicad-cli"):
        return json.dumps({"error": "kicad-cli not found"}, indent=2)

    # Auto-redirect sub-sheets to root for full hierarchy context
    root_path = _resolve_root(schematic_path, project_path)
    erc_target = root_path or schematic_path
    sheet_filter = Path(schematic_path).name if root_path else None

    out_dir = output_dir or str(Path(erc_target).parent)
    out_path = str(Path(out_dir) / (Path(erc_target).stem + "-erc.json"))
    _run_cli(
        [
            "sch",
            "erc",
            "--format",
            "json",
            "--severity-all",
            "--output",
            out_path,
            erc_target,
        ],
        check=False,
    )
    try:
        with open(out_path) as f:
            report = json.load(f)
    except FileNotFoundError:
        return json.dumps({"error": "ERC failed to produce output"}, indent=2)

    pins = _parse_unconnected_pins(report, sheet_filter=sheet_filter)
    result: dict = {"unconnected_count": len(pins), "pins": pins}
    if root_path:
        result["note"] = "ERC ran from root schematic to include full hierarchy context"
    return json.dumps(result, indent=2)


@mcp.tool(annotations=_EXPORT)
def run_erc(
    schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR, project_path: str = ""
) -> str:
    """Run Electrical Rules Check (ERC) on a schematic.

    Auto-redirects to root schematic for sub-sheets to avoid false
    positives from missing hierarchical context.

    Returns JSON report with violations.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Directory for report file (default: same as schematic)
        project_path: Path to .kicad_pro file for explicit root resolution
    """
    # Auto-redirect sub-sheets to root for full hierarchy context
    root_path = _resolve_root(schematic_path, project_path)
    erc_target = root_path or schematic_path
    sheet_filter = Path(schematic_path).name if root_path else None

    out_dir = output_dir or str(Path(erc_target).parent)
    out_path = str(Path(out_dir) / (Path(erc_target).stem + "-erc.json"))
    _run_cli(
        ["sch", "erc", "--format", "json", "--severity-all", "--output", out_path, erc_target],
        check=False,
    )
    try:
        with open(out_path) as f:
            report = json.load(f)
    except FileNotFoundError:
        return json.dumps({"error": "ERC failed to produce output file"}, indent=2)

    all_violations = []
    for sheet in report.get("sheets", []):
        if sheet_filter:
            sheet_path = sheet.get("path", "")
            if sheet_filter not in sheet_path:
                continue
        all_violations.extend(sheet.get("violations", []))

    result = {
        "source": report.get("source", ""),
        "kicad_version": report.get("kicad_version", ""),
        "violation_count": len(all_violations),
        "violations": all_violations,
    }
    if root_path:
        result["note"] = "ERC ran from root schematic to include full hierarchy context"
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Schematic export tools (3)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_EXPORT)
def export_schematic(
    format: str = "pdf",
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
) -> str:
    """Export schematic to PDF, SVG, or DXF format.

    Args:
        format: Output format - "pdf", "svg", or "dxf"
        schematic_path: Path to .kicad_sch file
        output_dir: Directory for output files
    """
    fmt = format.lower()
    if fmt not in ("pdf", "svg", "dxf"):
        return json.dumps({"error": f"Unknown format: {format}. Use: pdf, svg, dxf"})

    out_dir = output_dir or str(Path(schematic_path).parent)
    stem = Path(schematic_path).stem

    if fmt == "pdf":
        out_path = str(Path(out_dir) / f"{stem}.pdf")
        _run_cli(["sch", "export", "pdf", "--output", out_path, schematic_path])
        meta = _file_meta(out_path)
        meta["format"] = "pdf"
        return json.dumps(meta, indent=2)
    elif fmt == "svg":
        os.makedirs(out_dir, exist_ok=True)
        _run_cli(["sch", "export", "svg", "--output", out_dir, schematic_path])
        svgs = sorted(Path(out_dir).glob("*.svg"))
        return json.dumps(
            {
                "path": out_dir,
                "format": "svg",
                "files": [f.name for f in svgs],
                "count": len(svgs),
            },
            indent=2,
        )
    else:  # dxf
        out_path = str(Path(out_dir) / f"{stem}.dxf")
        _run_cli(["sch", "export", "dxf", "--output", out_path, schematic_path])
        meta = _file_meta(out_path)
        meta["format"] = "dxf"
        return json.dumps(meta, indent=2)


@mcp.tool(annotations=_EXPORT)
def export_netlist(
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
    format: str = "kicadxml",
) -> str:
    """Export schematic netlist in KiCad XML or KiCad net format.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Output directory
        format: Netlist format: kicadxml, cadstar, orcadpcb2
    """
    out_dir = output_dir or str(Path(schematic_path).parent)
    ext = ".xml" if format == "kicadxml" else ".net"
    out_path = str(Path(out_dir) / (Path(schematic_path).stem + ext))
    _run_cli(["sch", "export", "netlist", "--format", format, "--output", out_path, schematic_path])
    meta = _file_meta(out_path)
    meta["format"] = format
    return json.dumps(meta, indent=2)


@mcp.tool(annotations=_EXPORT)
def export_bom(schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export Bill of Materials (BOM) as CSV.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Output directory
    """
    out_dir = output_dir or str(Path(schematic_path).parent)
    out_path = str(Path(out_dir) / (Path(schematic_path).stem + "-bom.csv"))
    _run_cli(["sch", "export", "bom", "--output", out_path, schematic_path])
    meta = _file_meta(out_path)
    meta["format"] = "csv"
    with open(out_path) as f:
        lines = f.readlines()
    meta["component_count"] = max(0, len(lines) - 1)  # minus header
    return json.dumps(meta, indent=2)


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for mcp-server-kicad-schematic console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
