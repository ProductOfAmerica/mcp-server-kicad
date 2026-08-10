"""KiCad Schematic MCP Server — schematic manipulation, ERC analysis, and schematic export tools."""

import difflib
import json
import math
import os
import re
from pathlib import Path

from mcp.server.fastmcp.exceptions import ToolError

import mcp_server_kicad._cst as _cst
from mcp_server_kicad._cst import _fill_at, _node_text, _node_xy, _num, _numish
from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    OUTPUT_DIR,
    SCH_PATH,
    _file_meta,
    _gen_uuid,
    _node_uuid,
    _remove_root_symbol_instance,
    _resolve_hierarchy_path,
    _resolve_root,
    _resolve_system_lib,
    _run_cli,
    _sheet_file_cst,
    _sheet_name_cst,
    _snap_grid,
    _sym_property_cst,
    _upsert_root_symbol_instance,
    build_server,
)
from mcp_server_kicad.models import (
    BomExportResult,
    BusEntryItem,
    ComponentItem,
    ErcResult,
    ExportResult,
    GlobalLabelItem,
    HierarchicalLabelItem,
    JunctionItem,
    LabelItem,
    MultiFileExportResult,
    NetConnectionsResult,
    NoConnectItem,
    PinRefSpec,
    PointSpec,
    SchematicSummary,
    SheetItem,
    UnconnectedPinsResult,
    WireItem,
    WireSpec,
)

mcp = build_server(
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
        " Use get_pin_positions, list_schematic_components,"
        " list_schematic_labels, get_net_connections.\n"
        "- When a tool returns an error, try different parameters or a different"
        " MCP tool. Do NOT fall back to manual file editing.\n\n"
        "WIRING WORKFLOW:\n"
        "1. Place components with place_component\n"
        "2. Discover pin names with get_pin_positions\n"
        "3. Wire using wire_pins_to_net (pins-to-net) or connect_pins (pin-to-pin)\n"
        "4. Verify with list_schematic_labels and get_net_connections\n\n"
        "CLEANUP WORKFLOW:\n"
        "- To find existing wires before removal, use"
        " list_schematic_wires which returns x1/y1/x2/y2"
        " endpoints for every wire segment. Pass those coordinates to remove_wire.\n\n"
        "ERC WORKFLOW:\n"
        "1. Run run_erc to get violations\n"
        "2. Fix 'power pin not driven' with add_power_symbol (lib_id='power:PWR_FLAG')\n"
        "3. Fix unconnected pins with wire_pins_to_net or no_connect_pin\n"
        "4. Re-run run_erc to verify fixes\n"
        "5. If blocked, report the error — do NOT edit the schematic file manually\n\n"
        "HIERARCHY WORKFLOW:\n"
        "1. Create hierarchy with add_hierarchical_sheet (project server)\n"
        "2. Add hierarchical labels with add_hierarchical_label to connect sub-sheets\n"
        "3. List items with list_schematic_hierarchical_labels and list_schematic_sheets\n"
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


def _find_sym(sch, reference: str):
    """Return the first placed symbol whose Reference property matches, or None."""
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference for p in sym.properties):
            return sym
    return None


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
    target = _find_sym(sch, reference)
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


# ---------------------------------------------------------------------------
# Schematic read tools (13)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ_ONLY)
def get_schematic_summary(schematic_path: str = SCH_PATH) -> SchematicSummary:
    """Get schematic page info and item counts.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    _, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    return SchematicSummary(
        page_size=page_name,
        page_width_mm=page_w,
        page_height_mm=page_h,
        components=len(root.find_all("symbol")),
        labels=len(root.find_all("label")),
        global_labels=len(root.find_all("global_label")),
        hierarchical_labels=len(root.find_all("hierarchical_label")),
        sheets=len(root.find_all("sheet")),
        wires=len(root.find_all("wire")),
        junctions=len(root.find_all("junction")),
        no_connects=len(root.find_all("no_connect")),
    )


@mcp.tool(annotations=_READ_ONLY)
def list_schematic_components(schematic_path: str = SCH_PATH) -> list[ComponentItem]:
    """List all placed components in the schematic.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    items = []
    for sym in root.find_all("symbol"):
        at = sym.find("at")
        lib_id = sym.find("lib_id")
        items.append(
            ComponentItem(
                reference=_sym_property_cst(sym, "Reference") or "?",
                value=_sym_property_cst(sym, "Value") or "?",
                lib_id=lib_id.atoms[1].text if lib_id is not None else "",
                x=float(at.atoms[1].text),
                y=float(at.atoms[2].text),
                rotation=float(at.atoms[3].text) if len(at.atoms) > 3 else 0,
            )
        )
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_schematic_labels(schematic_path: str = SCH_PATH) -> list[LabelItem]:
    """List all net labels in the schematic.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    items = []
    for label in root.find_all("label"):
        x, y = _node_xy(label)
        items.append(LabelItem(text=_node_text(label), x=x, y=y))
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_schematic_wires(schematic_path: str = SCH_PATH) -> list[WireItem]:
    """List all wire segments in the schematic.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    items = []
    for wire in root.find_all("wire"):
        pts = _wire_xys(wire)
        if len(pts) >= 2:
            items.append(WireItem(x1=pts[0][0], y1=pts[0][1], x2=pts[1][0], y2=pts[1][1]))
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_schematic_global_labels(schematic_path: str = SCH_PATH) -> list[GlobalLabelItem]:
    """List all global labels in the schematic.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    items = []
    for gl in root.find_all("global_label"):
        x, y = _node_xy(gl)
        shape = gl.find("shape")
        items.append(
            GlobalLabelItem(
                text=_node_text(gl),
                shape=shape.atoms[1].text if shape is not None else "input",
                x=x,
                y=y,
            )
        )
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_schematic_hierarchical_labels(
    schematic_path: str = SCH_PATH,
) -> list[HierarchicalLabelItem]:
    """List all hierarchical labels in the schematic.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    items = []
    for hl in root.find_all("hierarchical_label"):
        at = hl.find("at")
        shape = hl.find("shape")
        items.append(
            HierarchicalLabelItem(
                text=_node_text(hl),
                shape=shape.atoms[1].text if shape is not None else "input",
                x=float(at.atoms[1].text),
                y=float(at.atoms[2].text),
                rotation=float(at.atoms[3].text) if len(at.atoms) > 3 else 0,
                uuid=_node_uuid(hl),
            )
        )
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_schematic_sheets(schematic_path: str = SCH_PATH) -> list[SheetItem]:
    """List all hierarchical sheets in the schematic.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    items = []
    for sheet in root.find_all("sheet"):
        at = sheet.find("at")
        size = sheet.find("size")
        items.append(
            SheetItem(
                sheet_name=_sheet_name_cst(sheet) or "",
                file_name=_sheet_file_cst(sheet) or "",
                x=float(at.atoms[1].text),
                y=float(at.atoms[2].text),
                width=float(size.atoms[1].text),
                height=float(size.atoms[2].text),
                pin_count=len(sheet.find_all("pin")),
                uuid=_node_uuid(sheet),
            )
        )
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_schematic_junctions(schematic_path: str = SCH_PATH) -> list[JunctionItem]:
    """List all junctions in the schematic.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    items = []
    for j in root.find_all("junction"):
        x, y = _node_xy(j)
        diameter = j.find("diameter")
        items.append(
            JunctionItem(
                x=x,
                y=y,
                diameter=float(diameter.atoms[1].text) if diameter is not None else 0,
            )
        )
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_schematic_no_connects(schematic_path: str = SCH_PATH) -> list[NoConnectItem]:
    """List all no-connect flags in the schematic.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    items = []
    for nc in root.find_all("no_connect"):
        x, y = _node_xy(nc)
        items.append(NoConnectItem(x=x, y=y))
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_schematic_bus_entries(schematic_path: str = SCH_PATH) -> list[BusEntryItem]:
    """List all bus entries in the schematic.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    items = []
    for be in root.find_all("bus_entry"):
        x, y = _node_xy(be)
        size = be.find("size")
        items.append(
            BusEntryItem(
                x=x,
                y=y,
                size_x=float(size.atoms[1].text),
                size_y=float(size.atoms[2].text),
            )
        )
    return items


@mcp.tool(annotations=_READ_ONLY)
def get_symbol_pins(symbol_name: str, schematic_path: str = SCH_PATH) -> str:
    """Get pin info for a symbol in the schematic's lib_symbols.

    Args:
        symbol_name: Symbol name (e.g. "LM7805", "C", "Fuse")
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    ls = _find_lib_symbol_cst(root, symbol_name)
    if ls is not None:
        lines = [f"Symbol: {symbol_name}"]
        for unit in ls.find_all("symbol"):
            for pin in unit.find_all("pin"):
                number = pin.find("number")
                name = pin.find("name")
                at = pin.find("at")
                length = pin.find("length")
                px = _numish(at.atoms[1].text)
                py = _numish(at.atoms[2].text)
                rot = _numish(at.atoms[3].text) if len(at.atoms) > 3 else 0
                plen = _numish(length.atoms[1].text) if length is not None else 0
                lines.append(
                    f"  Pin {number.atoms[1].text if number is not None else ''}: "
                    f"{name.atoms[1].text if name is not None else '~'} "
                    f"({pin.atoms[1].text}) "
                    f"@ ({px}, {py}) "
                    f"rot={rot} len={plen}"
                )
        return "\n".join(lines)
    raise ToolError(f"'{symbol_name}' not found.")


@mcp.tool(annotations=_READ_ONLY)
def get_pin_positions(reference: str, schematic_path: str = SCH_PATH) -> str:
    """Get absolute pin positions for a placed component (accounts for rotation/mirror).

    Args:
        reference: Component reference (e.g. "U1", "R1")
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)

    target = _find_sym_cst(root, reference)
    if target is None:
        raise ToolError(f"{reference} not found.")

    lib_id = target.find("lib_id").atoms[1].text
    symbol_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    lib_sym = _find_lib_symbol_cst(root, lib_id)
    if lib_sym is None:
        raise ToolError(f"Lib symbol for {reference} not found.")

    at = target.find("at")
    cx = _numish(at.atoms[1].text)
    cy = _numish(at.atoms[2].text)
    angle_deg = _numish(at.atoms[3].text) if len(at.atoms) > 3 else 0
    m = target.find("mirror")
    mir = m.atoms[1].text if m is not None else None

    lines = [f"{reference} ({symbol_name}) @ ({cx}, {cy}) rot={angle_deg} mirror={mir}"]

    for unit in lib_sym.find_all("symbol"):
        for pin in unit.find_all("pin"):
            pat = pin.find("at")
            final_x, final_y, _ = _transform_pin_pos(
                float(pat.atoms[1].text),
                float(pat.atoms[2].text),
                float(pat.atoms[3].text) if len(pat.atoms) > 3 else 0,
                cx,
                cy,
                angle_deg,
                mir,
            )
            number = pin.find("number")
            name = pin.find("name")
            lines.append(
                f"  Pin {number.atoms[1].text if number is not None else ''} "
                f"({name.atoms[1].text if name is not None else '~'}): "
                f"({round(final_x, 2)}, {round(final_y, 2)})"
            )

    return "\n".join(lines)


@mcp.tool(annotations=_READ_ONLY)
def get_net_connections(
    label_text: str,
    schematic_path: str = SCH_PATH,
) -> NetConnectionsResult:
    """Find all component pins connected to a net label.

    Scans labels matching the text, traces wires from label positions,
    and identifies component pins at wire endpoints.

    Args:
        label_text: Net name to search for (e.g. "VCC", "GND")
        schematic_path: Path to .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    tol = 0.1

    # Collect all label positions for this net
    label_positions: set[tuple[float, float]] = set()
    for token in ("label", "global_label"):
        for lbl in root.find_all(token):
            if _node_text(lbl) == label_text:
                label_positions.add(_node_xy(lbl))

    # BFS: expand from label positions through connected wire endpoints
    wire_ends = []
    for wire in root.find_all("wire"):
        pts = _wire_xys(wire)
        if len(pts) >= 2:
            wire_ends.append((pts[0], pts[1]))
    reachable: set[tuple[float, float]] = set(label_positions)
    frontier = set(label_positions)
    while frontier:
        next_frontier: set[tuple[float, float]] = set()
        for fx, fy in frontier:
            for (p0x, p0y), (p1x, p1y) in wire_ends:
                if abs(p0x - fx) < tol and abs(p0y - fy) < tol:
                    pt = (p1x, p1y)
                    if pt not in reachable:
                        reachable.add(pt)
                        next_frontier.add(pt)
                elif abs(p1x - fx) < tol and abs(p1y - fy) < tol:
                    pt = (p0x, p0y)
                    if pt not in reachable:
                        reachable.add(pt)
                        next_frontier.add(pt)
        frontier = next_frontier

    # Find component pins at reachable positions
    connections = []
    for sym in root.find_all("symbol"):
        ref = _sym_property_cst(sym, "Reference")
        if ref is None:
            continue
        lib_id_node = sym.find("lib_id")
        if lib_id_node is None:
            continue
        lib_sym = _find_lib_symbol_cst(root, lib_id_node.atoms[1].text)
        if lib_sym is None:
            continue
        at = sym.find("at")
        cx, cy = float(at.atoms[1].text), float(at.atoms[2].text)
        comp_angle = float(at.atoms[3].text) if len(at.atoms) > 3 else 0
        m = sym.find("mirror")
        mir = m.atoms[1].text if m is not None else None
        for unit in lib_sym.find_all("symbol"):
            for pin in unit.find_all("pin"):
                pat = pin.find("at")
                px, py, _ = _transform_pin_pos(
                    float(pat.atoms[1].text),
                    float(pat.atoms[2].text),
                    float(pat.atoms[3].text) if len(pat.atoms) > 3 else 0,
                    cx,
                    cy,
                    comp_angle,
                    mir,
                )
                number = pin.find("number")
                name = pin.find("name")
                for rx, ry in reachable:
                    if abs(px - rx) < tol and abs(py - ry) < tol:
                        connections.append(
                            {
                                "reference": ref,
                                "pin": number.atoms[1].text if number is not None else "",
                                "pin_name": name.atoms[1].text if name is not None else "~",
                                "x": px,
                                "y": py,
                            }
                        )
    return NetConnectionsResult(
        net=label_text,
        label_count=len(label_positions),
        connections=connections,
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
        raise ToolError(
            f"'{reference}' is not a valid KiCad reference designator. "
            "Must match pattern [A-Z]+[0-9]+[A-Z]* (e.g. 'R1', 'U2', 'C5B')."
        )

    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
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

    # Create instance — lib_name mirrors the lib_symbol's stored name so KiCad
    # can resolve the lookup without crashing (see the slice-8 segfault note).
    lib_sym = _find_lib_symbol_cst(root, lib_id)
    node = _SYMBOL_TPL.copy()
    lib_name = lib_sym.atoms[1].text if lib_sym is not None else symbol_name
    node.find("lib_name").atoms[1].set_text(lib_name)
    node.find("lib_id").atoms[1].set_text(lib_id)
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

    # Instances block — required by KiCad 9 for proper annotation
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
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    _upsert_root_symbol_instance(
        schematic_path,
        project_path,
        sym_uuid,
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
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sym_cst(root, reference)
    if target is None:
        raise ToolError(f"Component {reference} not found.")
    uuid = _node_uuid(target)
    root.remove_child(target)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    _remove_root_symbol_instance(schematic_path, "", uuid)
    return f"Removed {reference}"


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_label(
    text: str,
    x: float | None = None,
    y: float | None = None,
    schematic_path: str = SCH_PATH,
) -> str:
    """Remove net label(s) or global label(s) by text, optionally filtered by position.

    If x and y are provided, only removes labels matching both text AND
    position (within 0.1mm tolerance). Otherwise removes ALL labels with
    matching text. To move a global label, remove it and re-add with
    add_global_label (which takes shape and rotation).

    Args:
        text: Label text to match (e.g. "VCC", "PGND")
        x: Optional X position filter
        y: Optional Y position filter
        schematic_path: Path to .kicad_sch file
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    tol = 0.1

    def _matches(node) -> bool:
        if _node_text(node) != text:
            return False
        if x is None or y is None:
            return True
        nx, ny = _node_xy(node)
        return abs(nx - x) < tol and abs(ny - y) < tol

    counts = {}
    for token in ("label", "global_label"):
        matched = [n for n in root.find_all(token) if _matches(n)]
        for n in matched:
            root.remove_child(n)
        counts[token] = len(matched)
    if not counts["label"] and not counts["global_label"]:
        raise ToolError(f"Label '{text}' not found.")
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    parts = []
    if counts["label"]:
        parts.append(f"{counts['label']} label(s)")
    if counts["global_label"]:
        parts.append(f"{counts['global_label']} global label(s)")
    return f"Removed {' and '.join(parts)} '{text}'."


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
    Use list_schematic_wires to get wire coordinates first.

    Args:
        x1: Start X
        y1: Start Y
        x2: End X
        y2: End Y
        schematic_path: Path to .kicad_sch file
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    tol = 0.1
    removed = 0
    for node in root.find_all("wire"):
        pts = _wire_xys(node)
        if len(pts) < 2:
            continue
        (p0x, p0y), (p1x, p1y) = pts[0], pts[1]
        fwd = (
            abs(p0x - x1) < tol
            and abs(p0y - y1) < tol
            and abs(p1x - x2) < tol
            and abs(p1y - y2) < tol
        )
        rev = (
            abs(p0x - x2) < tol
            and abs(p0y - y2) < tol
            and abs(p1x - x1) < tol
            and abs(p1y - y1) < tol
        )
        if fwd or rev:
            root.remove_child(node)
            removed += 1
    if not removed:
        raise ToolError(f"Wire ({x1},{y1})->({x2},{y2}) not found.")
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Removed {removed} wire(s)."


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
    tree, root, *_ = _open_sch_cst(schematic_path)
    tol = 0.1
    for node in root.find_all("junction"):
        nx, ny = _node_xy(node)
        if abs(nx - x) < tol and abs(ny - y) < tol:
            root.remove_child(node)
            Path(schematic_path).write_bytes(_cst.serialize(tree))
            return f"Removed junction at ({x}, {y})"
    raise ToolError(f"Junction at ({x}, {y}) not found.")


@mcp.tool(annotations=_ADDITIVE)
def add_wires(wires: list[WireSpec], schematic_path: str = SCH_PATH) -> str:
    """Add multiple wires at once. Each wire dict has keys: x1, y1, x2, y2.

    Args:
        wires: List of wire defs [{x1, y1, x2, y2}, ...]
        schematic_path: Path to .kicad_sch file
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
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Added {len(wires)} wires"


# Native-shape label template (tabs, quoted uuid, no justify) for the CST write
# path. Values are filled per call via set_text, which handles KiCad escaping.
_LABEL_TPL = _cst.parse(
    b'(label "X"\n\t(at 0 0 0)\n\t(effects\n\t\t(font\n\t\t\t(size 1.27 1.27)\n\t\t)\n\t)'
    b'\n\t(uuid "x")\n)'
).lists[0]

_GLABEL_TPL = _cst.parse(
    b'(global_label "X"\n\t(shape input)\n\t(at 0 0 0)\n\t(effects\n\t\t(font'
    b'\n\t\t\t(size 1.27 1.27)\n\t\t)\n\t)\n\t(uuid "x")\n)'
).lists[0]

_HLABEL_TPL = _cst.parse(
    b'(hierarchical_label "X"\n\t(shape input)\n\t(at 0 0 0)\n\t(effects\n\t\t(font'
    b'\n\t\t\t(size 1.27 1.27)\n\t\t)\n\t)\n\t(uuid "x")\n)'
).lists[0]

_TEXT_TPL = _cst.parse(
    b'(text "X"\n\t(at 0 0 0)\n\t(effects\n\t\t(font\n\t\t\t(size 1.27 1.27)\n\t\t)\n\t)'
    b'\n\t(uuid "x")\n)'
).lists[0]

_JUNCTION_TPL = _cst.parse(
    b'(junction\n\t(at 0 0)\n\t(diameter 0)\n\t(color 0 0 0 0)\n\t(uuid "x")\n)'
).lists[0]

_WIRE_TPL = _cst.parse(
    b"(wire\n\t(pts\n\t\t(xy 0 0) (xy 0 0)\n\t)\n\t(stroke\n\t\t(width 0)\n\t\t(type default)\n\t)"
    b'\n\t(uuid "x")\n)'
).lists[0]

_NC_TPL = _cst.parse(b'(no_connect\n\t(at 0 0)\n\t(uuid "x")\n)').lists[0]

# Placed-symbol template for place_component: native shape, Reference/Value
# visible, Footprint/Datasheet hidden, one instances entry. Pins are spliced
# per placement from _PIN_REF_TPL; (mirror ...) is inserted after (at) on demand.
_SYMBOL_TPL = _cst.parse(
    b'(symbol\n\t(lib_name "X")\n\t(lib_id "X")\n\t(at 0 0 0)\n\t(unit 1)\n\t(exclude_from_sim no)'
    b'\n\t(in_bom yes)\n\t(on_board yes)\n\t(dnp no)\n\t(uuid "x")'
    b'\n\t(property "Reference" "R"\n\t\t(at 0 0 0)\n\t\t(effects\n\t\t\t(font'
    b"\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t)\n\t)"
    b'\n\t(property "Value" "V"\n\t\t(at 0 0 0)\n\t\t(effects\n\t\t\t(font'
    b"\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t)\n\t)"
    b'\n\t(property "Footprint" ""\n\t\t(at 0 0 0)\n\t\t(effects\n\t\t\t(font'
    b"\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(hide yes)\n\t\t)\n\t)"
    b'\n\t(property "Datasheet" "~"\n\t\t(at 0 0 0)\n\t\t(effects\n\t\t\t(font'
    b"\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(hide yes)\n\t\t)\n\t)"
    b'\n\t(instances\n\t\t(project "X"\n\t\t\t(path "/x"\n\t\t\t\t(reference "R")'
    b"\n\t\t\t\t(unit 1)\n\t\t\t)\n\t\t)\n\t)\n)"
).lists[0]

_PIN_REF_TPL = _cst.parse(b'(pin "1"\n\t(uuid "x")\n)').lists[0]

# New component property (hidden, at component center). Carries (id N) like
# the kiutils writer did; KiCad 9 accepts and ignores the legacy id.
_PROP_TPL = _cst.parse(
    b'(property "K" "V"\n\t(id 0)\n\t(at 0 0 0)\n\t(effects\n\t\t(font'
    b"\n\t\t\t(size 1.27 1.27)\n\t\t)\n\t\t(hide yes)\n\t)\n)"
).lists[0]

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


def _page_size_cst(root) -> tuple[float, float, str]:
    """(width, height, page name) from a CST schematic root; mirrors _get_page_size."""
    paper = root.find("paper")
    if paper is None:
        return 297, 210, "A4"
    atoms = paper.atoms
    name = atoms[1].text if len(atoms) > 1 else "A4"
    if name == "User" and len(atoms) > 3:
        w, h = float(atoms[2].text), float(atoms[3].text)
    else:
        w, h = _PAGE_SIZES.get(name, (297, 210))
    if any(a.text == "portrait" for a in atoms[1:]):
        w, h = h, w
    return w, h, name


def _open_sch_cst(schematic_path: str):
    """Parse a schematic into a CST for the splice tools.

    Works on any format KiCad writes (portability measured per token by the
    KiCad 10 e2e tests; see docs/adr-cst-substrate.md).
    """
    tree = _cst.parse(Path(schematic_path).read_bytes())
    root = tree.lists[0] if tree.lists else None
    if root is None or root.head != "kicad_sch":
        raise ToolError(f"{Path(schematic_path).name} is not a KiCad schematic.")
    page_w, page_h, page_name = _page_size_cst(root)
    return tree, root, page_w, page_h, page_name


def _bounds_check(x: float, y: float, page_w: float, page_h: float, page_name: str) -> None:
    if x < 0 or x > page_w or y < 0 or y > page_h:
        sizes = ", ".join(_PAGE_SIZES.keys())
        raise ToolError(
            f"Position ({x}, {y}) is outside the sheet boundary "
            f"({page_w}x{page_h}mm, page '{page_name}'). "
            f"Use set_page_size to resize (available: {sizes}, or 'User')."
        )


def _splice_sch_node(root, token: str, node) -> None:
    """Insert *node* after the last same-token sibling, else before the file tail."""
    siblings = root.find_all(token)
    tail = root.find("sheet_instances") or root.find("embedded_fonts")
    if siblings:
        root.insert_after(siblings[-1], node)
    elif tail is not None:
        root.insert_before(tail, node)
    else:
        root.append_child(node, b"\n")


def _wire_xys(node) -> list[tuple[float, float]]:
    return [
        (float(p.atoms[1].text), float(p.atoms[2].text)) for p in node.find("pts").find_all("xy")
    ]


def _splice_wire(root, x1: float, y1: float, x2: float, y2: float) -> list[tuple[float, float]]:
    """Splice one wire node; returns its rounded endpoints for auto-junction scans."""
    node = _WIRE_TPL.copy()
    xys = node.find("pts").find_all("xy")
    points = []
    for xy, vx, vy in [(xys[0], x1, y1), (xys[1], x2, y2)]:
        px, py = round(vx, 4), round(vy, 4)
        xy.atoms[1].set_text(_num(px))
        xy.atoms[2].set_text(_num(py))
        points.append((px, py))
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "wire", node)
    return points


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


def _find_sym_cst(root, reference: str):
    """First placed symbol whose Reference property matches, or None."""
    return next(
        (s for s in root.find_all("symbol") if _sym_property_cst(s, "Reference") == reference),
        None,
    )


def _find_lib_symbol_cst(root, lib_id: str):
    """CST twin of _find_lib_symbol: bare and prefixed names both match."""
    bare = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    libs = root.find("lib_symbols")
    for ls in libs.find_all("symbol") if libs is not None else []:
        raw = ls.atoms[1].text
        entry = raw.split(":")[-1] if ":" in raw else raw
        if entry == bare or entry == lib_id or raw == lib_id:
            return ls
    return None


def _get_pin_pos_cst(root, reference: str, pin_name: str) -> tuple[float, float, float]:
    """CST twin of _get_pin_pos; same match rules and ValueError strings.

    Placed symbols are the root-level symbol nodes (find_all never descends
    into lib_symbols). Pin match is name-OR-number per pin in file order, and
    every unit is scanned regardless of the placed symbol's (unit N).
    """
    target = _find_sym_cst(root, reference)
    if target is None:
        raise ValueError(f"Component {reference} not found")
    lib_sym = _find_lib_symbol_cst(root, target.find("lib_id").atoms[1].text)
    if lib_sym is None:
        raise ValueError(f"Lib symbol for {reference} not found")
    at = target.find("at")
    cx, cy = float(at.atoms[1].text), float(at.atoms[2].text)
    comp_angle = float(at.atoms[3].text) if len(at.atoms) > 3 else 0
    m = target.find("mirror")
    mir = m.atoms[1].text if m is not None else None
    for unit in lib_sym.find_all("symbol"):
        for pin in unit.find_all("pin"):
            name = pin.find("name")
            number = pin.find("number")
            if (name is not None and name.atoms[1].text == pin_name) or (
                number is not None and number.atoms[1].text == pin_name
            ):
                pat = pin.find("at")
                px, py = float(pat.atoms[1].text), float(pat.atoms[2].text)
                pangle = float(pat.atoms[3].text) if len(pat.atoms) > 3 else 0
                return _transform_pin_pos(px, py, pangle, cx, cy, comp_angle, mir)
    raise ValueError(f"Pin '{pin_name}' not found on {reference}")


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


def _splice_lib_symbol_cst(root, node) -> None:
    libs = root.find("lib_symbols")
    if libs is None:
        libs = _cst.parse(b"(lib_symbols\n)").lists[0]
        _splice_sch_node(root, "lib_symbols", libs)
    syms = libs.find_all("symbol")
    if syms:
        libs.insert_after(syms[-1], node)
    else:
        libs.append_child(node, b"\n\t\t")


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
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    _bounds_check(x, y, page_w, page_h, page_name)
    x, y = round(x, 4), round(y, 4)
    node = _LABEL_TPL.copy()
    node.atoms[1].set_text(text)
    _fill_at(node, x, y, rotation)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "label", node)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Label '{text}' at ({x}, {y})"


@mcp.tool(annotations=_ADDITIVE)
def add_junctions(points: list[PointSpec], schematic_path: str = SCH_PATH) -> str:
    """Add multiple junctions. Each point dict has keys: x, y.

    Args:
        points: List of junction positions [{x, y}, ...]
        schematic_path: Path to .kicad_sch file
    """
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    for p in points:
        _bounds_check(p["x"], p["y"], page_w, page_h, page_name)
    for p in points:
        node = _JUNCTION_TPL.copy()
        _fill_at(node, round(p["x"], 4), round(p["y"], 4))
        node.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(root, "junction", node)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Added {len(points)} junctions"


@mcp.tool(annotations=_ADDITIVE)
def add_lib_symbol(symbol_lib_path: str, symbol_name: str, schematic_path: str = SCH_PATH) -> str:
    """Load a symbol definition from a .kicad_sym library into the schematic.

    Args:
        symbol_lib_path: Path to .kicad_sym file
        symbol_name: Symbol name (e.g. "LM7805")
        schematic_path: Path to .kicad_sch file
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    if _find_lib_symbol_cst(root, symbol_name) is not None:
        raise ToolError(f"'{symbol_name}' already in lib_symbols.")
    if not _copy_lib_symbol_from_file_cst(root, symbol_lib_path, symbol_name, symbol_name):
        raise ToolError(f"'{symbol_name}' not found in {symbol_lib_path}.")
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Added '{symbol_name}' to lib_symbols."


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
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    _bounds_check(x, y, page_w, page_h, page_name)
    x, y = _snap_grid(x), _snap_grid(y)
    sym = _find_sym_cst(root, reference)
    if sym is None:
        raise ToolError(f"Component {reference} not found.")
    _fill_at(sym, x, y, rotation)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Moved {reference} to ({x}, {y})"


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
    tree, root, *_ = _open_sch_cst(schematic_path)
    sym = _find_sym_cst(root, reference)
    if sym is None:
        raise ToolError(f"Component {reference} not found.")
    props = sym.find_all("property")
    prop = next((p for p in props if p.atoms[1].text == key), None)
    if prop is not None:
        prop.atoms[2].set_text(value)
        created = ""
    else:
        # Create new property (hidden, at component center)
        ids = []
        for p in props:
            id_node = p.find("id")
            if id_node is not None:
                ids.append(int(id_node.atoms[1].text))
        node = _PROP_TPL.copy()
        node.atoms[1].set_text(key)
        node.atoms[2].set_text(value)
        node.find("id").atoms[1].set_text(str(max(ids, default=-1) + 1))
        cx, cy = _node_xy(sym)
        _fill_at(node, cx, cy)
        if props:
            sym.insert_after(props[-1], node)
        else:
            sym.append_child(node, b"\n\t")
        created = " (new property)"
    if key == "Reference":
        instances = sym.find("instances")
        if instances is not None:
            for project in instances.find_all("project"):
                for path_node in project.find_all("path"):
                    ref_node = path_node.find("reference")
                    if ref_node is not None:
                        ref_node.atoms[1].set_text(value)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    if key in ("Reference", "Value", "Footprint"):
        ref = _sym_property_cst(sym, "Reference") or "?"
        val = _sym_property_cst(sym, "Value") or ""
        fp_val = _sym_property_cst(sym, "Footprint") or ""
        _upsert_root_symbol_instance(
            schematic_path,
            "",
            _node_uuid(sym),
            ref,
            value=val,
            footprint=fp_val,
        )
    return f"Set {reference}.{key} = {value}{created}"


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
            raise ToolError("'User' page size requires both width and height parameters.")
        w, h = float(width), float(height)
    elif size_key in _PAGE_SIZES:
        w, h = _PAGE_SIZES[size_key]
    else:
        valid = ", ".join(list(_PAGE_SIZES.keys()) + ["User"])
        raise ToolError(f"unknown page size '{size_key}'. Valid sizes: {valid}.")

    tree, root, *_ = _open_sch_cst(schematic_path)
    parts = f'(paper "{size_key}"'
    if size_key == "User":
        parts += f" {_num(w)} {_num(h)}"
    if portrait:
        parts += " portrait"
    new_paper = _cst.parse((parts + ")").encode()).lists[0]
    paper = root.find("paper")
    if paper is not None:
        new_paper.sep = paper.sep
        root.children[root.children.index(paper)] = new_paper
    else:
        _splice_sch_node(root, "paper", new_paper)
    Path(schematic_path).write_bytes(_cst.serialize(tree))

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
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    _bounds_check(x, y, page_w, page_h, page_name)
    x, y = round(x, 4), round(y, 4)
    node = _GLABEL_TPL.copy()
    node.atoms[1].set_text(text)
    node.find("shape").atoms[1].set_text(shape)
    _fill_at(node, x, y, rotation)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "global_label", node)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
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
        raise ToolError(f"invalid shape '{shape}'. Use: {', '.join(sorted(_VALID_HLABEL_SHAPES))}")
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    _bounds_check(x, y, page_w, page_h, page_name)
    x, y = round(x, 4), round(y, 4)
    node = _HLABEL_TPL.copy()
    node.atoms[1].set_text(text)
    node.find("shape").atoms[1].set_text(shape)
    _fill_at(node, x, y, rotation)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "hierarchical_label", node)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
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
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = None
    for node in root.find_all("hierarchical_label"):
        if uuid and _node_uuid(node) == uuid:
            target = node
            break
        if _node_text(node) == text and not uuid:
            target = node
            break
    if target is None:
        raise ToolError(f"Hierarchical label '{text}' not found")
    target_text = _node_text(target)
    root.remove_child(target)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Removed hierarchical label '{target_text}'"


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
        raise ToolError(
            f"invalid shape '{new_shape}'. Use: {', '.join(sorted(_VALID_HLABEL_SHAPES))}"
        )
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = None
    for node in root.find_all("hierarchical_label"):
        if uuid and _node_uuid(node) == uuid:
            target = node
            break
        if _node_text(node) == text and not uuid:
            target = node
            break
    if target is None:
        raise ToolError(f"Hierarchical label '{text}' not found")
    changes = []
    if new_text:
        target.atoms[1].set_text(new_text)
        changes.append(f"text='{new_text}'")
    if new_shape:
        target.find("shape").atoms[1].set_text(new_shape)
        changes.append(f"shape={new_shape}")
    at = target.find("at")
    if new_x is not None:
        at.atoms[1].set_text(_num(round(new_x, 4)))
        changes.append(f"x={new_x}")
    if new_y is not None:
        at.atoms[2].set_text(_num(round(new_y, 4)))
        changes.append(f"y={new_y}")
    Path(schematic_path).write_bytes(_cst.serialize(tree))
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
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    _bounds_check(x, y, page_w, page_h, page_name)
    node = _TEXT_TPL.copy()
    node.atoms[1].set_text(text)
    _fill_at(node, x, y, rotation)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "text", node)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
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
    tree, root, *_ = _open_sch_cst(schematic_path)
    tol = 0.1
    matched = []
    for node in root.find_all("text"):
        if _node_text(node) != text:
            continue
        if x is not None and y is not None:
            nx, ny = _node_xy(node)
            if not (abs(nx - x) < tol and abs(ny - y) < tol):
                continue
        matched.append(node)
    if not matched:
        raise ToolError(f"Text '{text}' not found.")
    for node in matched:
        root.remove_child(node)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Removed {len(matched)} text(s) '{text}'."


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
    pins: list[PinRefSpec],
    label_text: str,
    direction: str = "auto",
    stub_length: float = 2.54,
    auto_pwr_flag: bool = True,
    schematic_path: str = SCH_PATH,
) -> str:
    """Wire multiple component pins to the same net label.

    Wires each pin with a short stub and a shared net label, one file write.

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
    tree, root, *_ = _open_sch_cst(schematic_path)
    tol = 0.1
    warnings = []
    stub_endpoints = []
    first_power_in_pos = None  # (x, y) of first power_in stub endpoint
    has_power_out = False  # True if any wired pin is power_out
    for pin_def in pins:
        ref = pin_def["reference"]
        pin_name = pin_def["pin"]
        try:
            px, py, outward = _get_pin_pos_cst(root, ref, pin_name)
        except ValueError as e:
            raise ToolError(f"Error wiring {ref}:{pin_name}: {e}") from e

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
            for existing in root.find_all("label"):
                if _node_text(existing) == label_text:
                    continue
                lx, ly = _node_xy(existing)
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
        _splice_wire(root, px, py, end_x, end_y)
        stub_endpoints.append((px, py))
        stub_endpoints.append((end_x, end_y))
        # Net label
        label_node = _LABEL_TPL.copy()
        label_node.atoms[1].set_text(label_text)
        _fill_at(label_node, end_x, end_y, label_rot)
        label_node.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(root, "label", label_node)

        # Track pin electrical types for auto PWR_FLAG logic
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

    _auto_junctions_cst(root, stub_endpoints)

    # Auto-add PWR_FLAG if net has power_in but no power_out
    if auto_pwr_flag and first_power_in_pos is not None and not has_power_out:
        # Check if PWR_FLAG already exists on this net
        labels_xy = [
            _node_xy(lbl) for lbl in root.find_all("label") if _node_text(lbl) == label_text
        ]
        has_existing_flag = any(
            abs(lx - sx) < tol and abs(ly - sy) < tol
            for sym in root.find_all("symbol")
            if _sym_property_cst(sym, "Value") == "PWR_FLAG"
            for sx, sy in [_node_xy(sym)]
            for lx, ly in labels_xy
        )

        if not has_existing_flag:
            # Ensure PWR_FLAG lib symbol exists: verbatim copy from the system
            # library, falling back to the synthetic template on bare CI hosts.
            if _find_lib_symbol_cst(root, "power:PWR_FLAG") is None:
                if not _copy_system_lib_symbol_cst(root, "power", "PWR_FLAG"):
                    _splice_lib_symbol_cst(root, _PWR_FLAG_LIB_TPL.copy())

            # Generate unique #FLG reference
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

            # Instances block — required by KiCad 9 for proper annotation
            root_uuid = _node_uuid(root)
            project_name = Path(schematic_path).stem
            sheet_path = f"/{root_uuid}"
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
                            str(pro), schematic_path, root_uuid
                        )
                    except Exception:
                        pass  # Fall back to simple path
            project = node.find("instances").find("project")
            project.atoms[1].set_text(project_name)
            inst_path = project.find("path")
            inst_path.atoms[1].set_text(sheet_path)
            inst_path.find("reference").atoms[1].set_text(flg_ref)

            _splice_sch_node(root, "symbol", node)

    Path(schematic_path).write_bytes(_cst.serialize(tree))
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
    tree, root, *_ = _open_sch_cst(schematic_path)
    x1, y1, _ = _get_pin_pos_cst(root, ref1, pin1)
    x2, y2, _ = _get_pin_pos_cst(root, ref2, pin2)
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

    Path(schematic_path).write_bytes(_cst.serialize(tree))

    n = len(segments)
    return f"Connected {ref1}:{pin1} -> {ref2}:{pin2} via {n} wire segment{'s' if n > 1 else ''}"


@mcp.tool(annotations=_ADDITIVE)
def no_connect_pin(
    reference: str,
    pin_name: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Place a no-connect flag on a component pin.

    Resolves pin position and places a no-connect flag. Idempotent:
    calling again for a pin that already has one is a no-op.

    Args:
        reference: Component reference (e.g. "U2")
        pin_name: Pin name (e.g. "NC") or number (e.g. "3")
        schematic_path: Path to .kicad_sch file
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    px, py, _ = _get_pin_pos_cst(root, reference, pin_name)
    px, py = round(px, 4), round(py, 4)

    tol = 0.1
    for nc in root.find_all("no_connect"):
        nx, ny = _node_xy(nc)
        if abs(nx - px) < tol and abs(ny - py) < tol:
            return f"No-connect already present on {reference}:{pin_name} at ({px}, {py})"

    node = _NC_TPL.copy()
    _fill_at(node, px, py)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "no_connect", node)
    Path(schematic_path).write_bytes(_cst.serialize(tree))

    return f"No-connect on {reference}:{pin_name} at ({px}, {py})"


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_no_connect(
    reference: str,
    pin_name: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Remove no-connect flag(s) from a component pin.

    Removes every no-connect at the pin's position, so stacked
    duplicates from repeated no_connect_pin calls clear in one go.

    Args:
        reference: Component reference (e.g. "U2")
        pin_name: Pin name (e.g. "NC") or number (e.g. "3")
        schematic_path: Path to .kicad_sch file
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    px, py, _ = _get_pin_pos_cst(root, reference, pin_name)

    tol = 0.1
    matched = [
        nc
        for nc in root.find_all("no_connect")
        if abs(_node_xy(nc)[0] - px) < tol and abs(_node_xy(nc)[1] - py) < tol
    ]
    if not matched:
        raise ToolError(f"No no-connect flag on {reference}:{pin_name}.")
    for nc in matched:
        root.remove_child(nc)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Removed {len(matched)} no-connect flag(s) from {reference}:{pin_name}"


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


@mcp.tool(annotations=_EXPORT)
def list_unconnected_pins(
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
    project_path: str = "",
) -> UnconnectedPinsResult:
    """List unconnected pins by running ERC and filtering results.

    Requires kicad-cli. Auto-redirects to root schematic for sub-sheets
    to avoid false positives from hierarchical label context.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Directory for ERC report file
        project_path: Path to .kicad_pro file for explicit root resolution
    """
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
        raise ToolError("ERC failed to produce output")

    pins = _parse_unconnected_pins(report, sheet_filter=sheet_filter)
    note = "ERC ran from root schematic to include full hierarchy context" if root_path else None
    return UnconnectedPinsResult(unconnected_count=len(pins), pins=pins, note=note)


@mcp.tool(annotations=_EXPORT)
def run_erc(
    schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR, project_path: str = ""
) -> ErcResult:
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
        raise ToolError("ERC failed to produce output file")

    all_violations = []
    for sheet in report.get("sheets", []):
        if sheet_filter:
            sheet_path = sheet.get("path", "")
            if sheet_filter not in sheet_path:
                continue
        all_violations.extend(sheet.get("violations", []))

    note = "ERC ran from root schematic to include full hierarchy context" if root_path else None
    return ErcResult(
        source=report.get("source", ""),
        kicad_version=report.get("kicad_version", ""),
        violation_count=len(all_violations),
        violations=all_violations,
        note=note,
    )


# ---------------------------------------------------------------------------
# Schematic export tools (3)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_EXPORT)
def export_schematic(
    format: str = "pdf",
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
) -> ExportResult | MultiFileExportResult:
    """Export schematic to PDF, SVG, or DXF format.

    Args:
        format: Output format - "pdf", "svg", or "dxf"
        schematic_path: Path to .kicad_sch file
        output_dir: Directory for output files
    """
    fmt = format.lower()
    if fmt not in ("pdf", "svg", "dxf"):
        raise ToolError(f"Unknown format: {format}. Use: pdf, svg, dxf")

    out_dir = output_dir or str(Path(schematic_path).parent)
    stem = Path(schematic_path).stem

    if fmt == "pdf":
        out_path = str(Path(out_dir) / f"{stem}.pdf")
        _run_cli(["sch", "export", "pdf", "--output", out_path, schematic_path])
        meta = _file_meta(out_path)
        return ExportResult(path=meta["path"], size_bytes=meta["size_bytes"], format="pdf")
    elif fmt == "svg":
        os.makedirs(out_dir, exist_ok=True)
        _run_cli(["sch", "export", "svg", "--output", out_dir, schematic_path])
        svgs = sorted(Path(out_dir).glob("*.svg"))
        return MultiFileExportResult(
            path=out_dir,
            format="svg",
            files=[f.name for f in svgs],
            count=len(svgs),
        )
    else:  # dxf
        out_path = str(Path(out_dir) / f"{stem}.dxf")
        _run_cli(["sch", "export", "dxf", "--output", out_path, schematic_path])
        meta = _file_meta(out_path)
        return ExportResult(path=meta["path"], size_bytes=meta["size_bytes"], format="dxf")


@mcp.tool(annotations=_EXPORT)
def export_netlist(
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
    format: str = "kicadxml",
) -> ExportResult:
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
    return ExportResult(path=meta["path"], size_bytes=meta["size_bytes"], format=format)


@mcp.tool(annotations=_EXPORT)
def export_bom(schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR) -> BomExportResult:
    """Export Bill of Materials (BOM) as CSV.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Output directory
    """
    out_dir = output_dir or str(Path(schematic_path).parent)
    out_path = str(Path(out_dir) / (Path(schematic_path).stem + "-bom.csv"))
    _run_cli(["sch", "export", "bom", "--output", out_path, schematic_path])
    meta = _file_meta(out_path)
    with open(out_path) as f:
        lines = f.readlines()
    component_count = max(0, len(lines) - 1)  # minus header
    return BomExportResult(
        path=meta["path"],
        size_bytes=meta["size_bytes"],
        format="csv",
        component_count=component_count,
    )


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for mcp-server-kicad-schematic console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
