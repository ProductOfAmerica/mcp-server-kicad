"""KiCad Schematic MCP Server — schematic manipulation, ERC analysis, and schematic export tools."""

import difflib
import json
import re
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError

import mcp_server_kicad._connectivity as _connectivity
import mcp_server_kicad._cst as _cst
from mcp_server_kicad._connectivity import (
    _instance_units,
    _sym_body_style_cst,
    _sym_unit_cst,
    not_drawn_message,
    pin_matches,
)
from mcp_server_kicad._cst import (
    SCH_IU_PER_MM,
    _fill_at,
    _node_text,
    _node_xy,
    _num,
    _numish,
    _snap,
)
from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    OUTPUT_DIR,
    SCH_PATH,
    _atomic_write,
    _check_rotation,
    _ensure_dir,
    _file_meta,
    _gen_uuid,
    _kicad_root,
    _node_uuid,
    _open_sym_lib,
    _read_kicad_bytes,
    _remove_root_symbol_instance,
    _require_kicad_path,
    _resolve_hierarchy_path,
    _resolve_root,
    _resolve_system_lib,
    _run_cli,
    _sheet_file_cst,
    _sheet_name_cst,
    _snap_grid,
    _sym_property_cst,
    _system_sym_dirs,
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
    NetConnectionsResult,
    NoConnectItem,
    PinRefSpec,
    PointSpec,
    SchematicExportResult,
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

#: Appended to every not-found error whose answer is one list call away.
_SEE_PLACED = " Use list_schematic_components to see what is placed."


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


# ---------------------------------------------------------------------------
# Schematic read tools (13)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ_ONLY)
def get_schematic_summary(schematic_path: str = SCH_PATH) -> SchematicSummary:
    """Get schematic page info and item counts.

    Args:
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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


@mcp.tool(annotations=_READ_ONLY, title="Symbol pins from the schematic's embedded library")
def get_symbol_pins(symbol_name: str, schematic_path: str = SCH_PATH) -> str:
    """Get pin info for a symbol in the schematic's lib_symbols.

    Args:
        symbol_name: Symbol name (e.g. "LM7805", "C", "Fuse")
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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

    A multi-unit part is placed as one symbol per unit and they all share the
    reference, so every placed unit is listed, each with its own pins plus the
    unit-0 pins they all share.

    Args:
        reference: Component reference (e.g. "U1", "R1")
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    _, root, *_ = _open_sch_cst(schematic_path)

    targets = _find_syms_cst(root, reference)
    if not targets:
        raise ToolError(f"{reference} not found." + _SEE_PLACED)

    lines: list[str] = []
    libs = _connectivity.lib_index(root)
    for target in targets:
        lib_id = target.find("lib_id").atoms[1].text
        symbol_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
        lib_sym = libs.get(_connectivity.lib_key(target))
        if lib_sym is None:
            raise ToolError(f"Lib symbol for {reference} not found.")

        at = target.find("at")
        cx = _numish(at.atoms[1].text)
        cy = _numish(at.atoms[2].text)
        angle_deg = _numish(at.atoms[3].text) if len(at.atoms) > 3 else 0
        # The mirror KiCad applies: one written before (at) is ignored on load.
        mir = _connectivity.effective_mirror(target)
        unit = _sym_unit_cst(target)

        head = f"{reference} ({symbol_name}) @ ({cx}, {cy}) rot={angle_deg} mirror={mir}"
        lines.append(head if len(targets) == 1 else f"{head} unit={unit}")

        for unit_node in _instance_units(lib_sym, unit, _sym_body_style_cst(target)):
            for pin in unit_node.find_all("pin"):
                try:
                    final_x, final_y, _ = _connectivity.pin_point_mm(target, pin, reference)
                except _connectivity.Refusal as e:
                    raise ToolError(e.text) from None
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
    and identifies component pins at wire endpoints. Each placed symbol
    contributes only its own unit's pins, so a multi-unit part does not
    report a sibling gate's input as sitting on this net.

    Args:
        label_text: Net name to search for (e.g. "VCC", "GND")
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
    libs = _connectivity.lib_index(root)
    for sym in root.find_all("symbol"):
        ref = _sym_property_cst(sym, "Reference")
        if ref is None:
            continue
        lib_sym = libs.get(_connectivity.lib_key(sym))
        if lib_sym is None:
            continue
        for unit in _instance_units(lib_sym, _sym_unit_cst(sym), _sym_body_style_cst(sym)):
            for pin in unit.find_all("pin"):
                try:
                    px, py, _ = _connectivity.pin_point_mm(sym, pin, ref)
                except _connectivity.Refusal as e:
                    raise ToolError(e.text) from None
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


# The part list in place_component's docstring is not decoration and is not a
# closed set: under a host that defers tool schemas, the description is the
# retrieval key, and a user asking to "place a resistor" matched no tool at all
# until those words were there. Measured in Claude Desktop, 2026-08-13: three
# tool_search calls before this tool surfaced, then one, ranked first, after.
# test_retrieval_vocabulary pins the words; keep them if you rewrite the prose.
@mcp.tool(annotations=_ADDITIVE)
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
        symbol_lib_path: Path to a .kicad_sym file that defines the symbol, for a
            library outside KiCad's stock set. Optional; omit to load the symbol
            from the stock library named by the lib_id prefix.
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
    # package produces one legitimately either: this tool has no unit parameter,
    # so it can never be placing a second unit of a multi-unit part.
    if any(_sym_property_cst(sym, "Reference") == reference for sym in root.find_all("symbol")):
        raise ToolError(
            f"{reference} is already placed in this schematic. Reference designators"
            " must be unique: use a different one, or move_component to reposition"
            " the existing symbol."
        )

    # Snap placement to grid, then check the position that is written.
    x = _snap_grid(x)
    y = _snap_grid(y)
    _bounds_check(x, y, page_w, page_h, page_name)

    # A placed symbol is a part only if its definition sits in lib_symbols
    # beside it: KiCad keeps the body and pins there, and the instance merely
    # points at them. Load it from symbol_lib_path when given, else from the
    # stock library the lib_id prefix names, and refuse if neither has it.
    # Measured 2026-10-02: this used to write the instance regardless, and a
    # symbol with no definition has no pins, so kicad-cli loaded the file and ERC
    # reported nothing at all. Placement time is the only time it can be caught.
    symbol_name = lib_id.split(":")[-1]
    lib_prefix = lib_id.split(":")[0] if ":" in lib_id else ""
    lib_sym = _find_lib_symbol_cst(root, lib_id)
    if lib_sym is None:
        if symbol_lib_path:
            _copy_lib_symbol_from_file_cst(root, symbol_lib_path, symbol_name, symbol_name)
        elif lib_prefix:
            _copy_system_lib_symbol_cst(root, lib_prefix, symbol_name)
        lib_sym = _find_lib_symbol_cst(root, lib_id)
    if lib_sym is None:
        raise ToolError(_no_definition_message(lib_id, symbol_name, lib_prefix, symbol_lib_path))
    # The copy refuses a derived stub; this catches one an older version embedded.
    _refuse_derived(lib_sym, lib_id)

    # Create the instance. lib_name mirrors the lib_symbol's stored name so KiCad
    # can resolve the lookup without crashing (see the slice-8 segfault note).
    node = _SYMBOL_TPL.copy()
    node.find("lib_name").atoms[1].set_text(lib_sym.atoms[1].text)
    node.find("lib_id").atoms[1].set_text(lib_id)
    _check_rotation(rotation)
    _fill_at(node, x, y, rotation, SCH_IU_PER_MM)
    if mirror:
        m = _cst.parse(b"(mirror x)").lists[0]
        m.atoms[1].set_text(mirror)
        node.insert_after(node.find("at"), m)
    sym_uuid = _gen_uuid()
    node.find("uuid").atoms[1].set_text(sym_uuid)

    # Properties
    props = node.find_all("property")
    props[0].atoms[2].set_text(reference)
    _fill_at(props[0], x, y - 3.81, iu=SCH_IU_PER_MM)
    props[1].atoms[2].set_text(value)
    _fill_at(props[1], x, y + 3.81, iu=SCH_IU_PER_MM)
    _fill_at(props[2], x, y, iu=SCH_IU_PER_MM)
    _fill_at(props[3], x, y, iu=SCH_IU_PER_MM)

    # Pin UUIDs from the lib symbol
    instances = node.find("instances")
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


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_component(reference: str, schematic_path: str = SCH_PATH) -> str:
    """Remove a component by reference designator.

    Args:
        reference: Reference designator to remove (e.g. "U2")
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sym_cst(root, reference)
    if target is None:
        raise ToolError(f"Component {reference} not found." + _SEE_PLACED)
    uuid = _node_uuid(target)
    root.remove_child(target)
    _atomic_write(schematic_path, _cst.serialize(tree))
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        raise ToolError(
            f"Label '{text}' not found. Use list_schematic_labels to see the labels present."
        )
    _atomic_write(schematic_path, _cst.serialize(tree))
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        raise ToolError(
            f"Wire ({x1},{y1})->({x2},{y2}) not found. Use list_schematic_wires for endpoints."
        )
    _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Removed {removed} wire(s)."


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_junction(
    x: float,
    y: float,
    schematic_path: str = SCH_PATH,
) -> str:
    """Remove a junction at the given coordinates, as KiCad's delete removes one.

    The junction within 0.1 mm goes, with any other at exactly its point. Then, as KiCad's
    DeleteJunction does (bus-wire-junction.cpp:115-185 at 9.0.8), of the wires and buses
    ending there each parallel pair is made one, or one of two identical lines goes, so
    whatever met only at the junction is disconnected: a T loses its stem, a crossing becomes
    two wires that do not join. Wires that merely meet there at an angle stay joined. Where a
    bus entry ends at the point nothing is merged, since kicad-cli 9 cuts a wire at a bus entry
    end inside it. The result names each merge.

    Args:
        x: X position
        y: Y position
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    tol = 0.1
    found = next(
        (
            n
            for n in root.find_all("junction")
            if abs(_node_xy(n)[0] - x) < tol and abs(_node_xy(n)[1] - y) < tol
        ),
        None,
    )
    if found is None:
        raise ToolError(
            f"Junction at ({x}, {y}) not found. Use list_schematic_junctions to see positions."
        )
    p = _xy_iu(found.find("at"))
    dots = [n for n in root.find_all("junction") if _xy_iu(n.find("at")) == p]
    gone = {_node_uuid(n) for n in dots}
    for n in dots:
        root.remove_child(n)
    lines = [f"Removed junction at ({x}, {y})"]
    if len(dots) > 1:
        lines.append(f"Also removed {len(dots) - 1} more junction(s) at {_connectivity.pt(p)}.")
    if p in _bus_entry_ends(root):
        lines.append(
            f"Merged no wires: a bus entry ends at {_connectivity.pt(p)}, and kicad-cli 9 cuts a"
            " wire at a bus entry end inside it."
        )
    else:
        merges, dropped = _merge_at(root, p)
        gone |= dropped
        lines += merges
    _leave_groups(root, gone)
    _atomic_write(schematic_path, _cst.serialize(tree))
    return "\n".join(lines)


@mcp.tool(annotations=_ADDITIVE)
def add_wires(wires: list[WireSpec], schematic_path: str = SCH_PATH) -> str:
    """Add multiple wires at once, as KiCad's wire tool adds them. Each wire dict has keys:
    x1, y1, x2, y2.

    Following SCH_LINE_WIRE_BUS_TOOL::finishSegments (sch_line_wire_bus_tool.cpp:1168-1276 at
    9.0.8): a junction goes wherever three or more wire directions, pins or sheet pins meet,
    at a new wire's end or at a point already on the sheet that a new wire runs through (a
    pin, a wire end), and every wire through a junction is split there, so a T, or a wire
    drawn through a pin, joins in every reader. As KiCad merges the new wire with the
    collinear wires of its stroke that it overlaps or meets end to end (where no junction
    is), a stretch such a wire already covers is left out, and a merged run holding both
    pins of a two-pin part loses the stretch between them, so drawing across a part never
    shorts it. A wire of no length is left out, as KiCad's tool drops one. Existing wires
    are only ever split: where KiCad's trim would cut a wire already on the sheet, the call
    is refused and nothing is written. The result counts the wire nodes written and names
    each junction, split, left-out and trimmed stretch; when nothing changes it says so and
    writes nothing.

    Args:
        wires: List of wire defs [{x1, y1, x2, y2}, ...]
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    for w in wires:
        for xk, yk in [("x1", "y1"), ("x2", "y2")]:
            _bounds_check(
                _snap(w[xk], SCH_IU_PER_MM), _snap(w[yk], SCH_IU_PER_MM), page_w, page_h, page_name
            )
    before = _cst.serialize(tree)
    m = _model(root)
    m.ensure()
    requested = [(_req_point(w["x1"], w["y1"]), _req_point(w["x2"], w["y2"])) for w in wires]
    # KiCad's tool drops a wire of no length before anything else (simplifyWireList,
    # sch_line_wire_bus_tool.cpp:1134-1165 at 9.0.8).
    dropped = [a for a, b in requested if a == b]
    requested = [(a, b) for a, b in requested if a != b]
    # finishSegments collects each new wire's ends and every connection point already on the
    # sheet that lies on it, before CleanUp and TrimWire change anything (1182-1202 at 9.0.8).
    candidates = []
    for a, b in requested:
        candidates += [a, b]
        candidates += [
            q for q in m._pts if _connectivity._within(q[0], q[1], a[0], a[1], b[0], b[1], 0)
        ]
    plain = _plain_wires(root)
    raw = [_connectivity.Item("wire", *a, *b) for a, b in requested]
    parts = _two_pin_parts(m)
    pieces: list[tuple[_connectivity.Point, _connectivity.Point]] = []
    covered, trimmed = [], []
    for a, b in requested:
        kept, pieces, cut = _merge_and_trim(m, a, b, plain, pieces, parts, raw)
        trimmed += cut
        for s, e in kept:
            new, cov = _new_pieces(s, e, plain + pieces)
            pieces += new
            covered += cov
    for a, b in pieces:
        _splice_line(root, a, b)
        m.add(_connectivity.Item("wire", *a, *b))
    joined = _join_points(root, m, candidates)
    pt = _connectivity.pt
    lines = [f"Added {len(pieces)} wires"]
    lines += [f"Left out the wire of no length at {pt(a)}." for a in dropped]
    lines += [f"Left out {pt(a)}-{pt(b)}: a wire there already covers it." for a, b in covered]
    lines += [f"Trimmed {pt(a)}-{pt(b)} between the pins of {ref}." for ref, a, b in trimmed]
    lines += _join_report(joined)
    after = _cst.serialize(tree)
    if after == before:
        lines.append("No change was made to the file.")
    else:
        _atomic_write(schematic_path, after)
    return "\n".join(lines)


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
    tree = _cst.parse(_read_kicad_bytes(schematic_path, "schematic"))
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
    """Splice one wire node; returns its endpoints as the file holds them, for auto-junction
    scans."""
    node = _WIRE_TPL.copy()
    xys = node.find("pts").find_all("xy")
    points = []
    for xy, vx, vy in [(xys[0], x1, y1), (xys[1], x2, y2)]:
        px, py = _snap(vx, SCH_IU_PER_MM), _snap(vy, SCH_IU_PER_MM)
        xy.atoms[1].set_text(_num(px, SCH_IU_PER_MM))
        xy.atoms[2].set_text(_num(py, SCH_IU_PER_MM))
        points.append((px, py))
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "wire", node)
    return points


# ---------------------------------------------------------------------------
# Junctions as KiCad's editor writes them: the junction rule is
# _connectivity.junction_info, the split is SCH_LINE::BreakAt
# ---------------------------------------------------------------------------

Point = _connectivity.Point


def _req_point(x: float, y: float) -> Point:
    """A requested point in internal units, as KiCad reads the text _splice_wire writes."""
    return (
        _connectivity.iu(_num(x, SCH_IU_PER_MM)),
        _connectivity.iu(_num(y, SCH_IU_PER_MM)),
    )


def _xy_iu(node) -> Point:
    """An (xy x y) or (at x y ...) list's position in internal units, as KiCad parses it."""
    return _connectivity.iu(node.atoms[1].text), _connectivity.iu(node.atoms[2].text)


def _line_xys(node):
    """A wire or bus node's two (xy ...) lists, or None for any other count, which KiCad's
    parser does not load (parseLine reads exactly two)."""
    pts = node.find("pts")
    xys = pts.find_all("xy") if pts is not None else []
    return xys if len(xys) == 2 else None


def _set_xy(xy, p: Point, texts: tuple[str, str] | None = None) -> None:
    """Point an (xy ...) list at p, rewriting only the atoms whose value changes, with
    ``texts`` when given (a point's own spelling, carried from the line that had it)."""
    for k, (atom, v) in enumerate(zip(xy.atoms[1:3], p)):
        if _connectivity.iu(atom.text) != v:
            atom.set_text(texts[k] if texts else _connectivity.mm(v))


def _splice_line(root, a: Point, b: Point) -> None:
    """Splice one wire node from a to b, from KiCad's own wire template."""
    node = _WIRE_TPL.copy()
    for xy, p in zip(node.find("pts").find_all("xy"), (a, b)):
        xy.atoms[1].set_text(_connectivity.mm(p[0]))
        xy.atoms[2].set_text(_connectivity.mm(p[1]))
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "wire", node)


def _model(root) -> _connectivity.Model:
    """The sheet's connectivity model; a sheet KiCad cannot load (a symbol at an angle it does
    not store) is refused before anything is written."""
    try:
        return _connectivity.Model(root)
    except _connectivity.Refusal as e:
        raise ToolError(f"{_connectivity.tags(e.codes)} {e.text} Nothing was written.") from None


def _split_lines_at(root, p: Point) -> list[tuple[str, Point, Point]]:
    """SCH_LINE::BreakAt (sch_line.cpp:574-583 at 9.0.8) on every wire and bus with p strictly
    inside: a copy of the node, the user's own bytes and stroke, with a fresh uuid, runs from p
    to the old end, spliced right after it; the node itself ends at p and keeps its start, uuid
    and every other byte. The copy joins no group, as in KiCad 10.0.7, whose SCH_LINE copy
    constructor leaves the group unset (sch_line.cpp:83-103), so Duplicate finds none to add it
    to (sch_item.cpp:149-177). Returns each split line's kind and old ends."""
    done = []
    for node in [n for n in root.lists if n.head in ("wire", "bus")]:
        xys = _line_xys(node)
        if xys is None:
            continue
        a, b = _xy_iu(xys[0]), _xy_iu(xys[1])
        if not _connectivity.inside(p[0], p[1], a[0], a[1], b[0], b[1]):
            continue
        copy = node.copy()
        _set_xy(copy.find("pts").find_all("xy")[0], p)
        uuid = copy.find("uuid")
        if uuid is not None and len(uuid.atoms) > 1:
            uuid.atoms[1].set_text(_gen_uuid())
        _set_xy(xys[1], p)
        root.insert_after(node, copy)
        done.append((node.head, a, b))
    return done


def _join_points(root, m, points, *, allow: bool = False):
    """KiCad's junction step at the points a call touches, the only writer of junctions.

    Every point is judged on the call's finished geometry before anything is split, which is
    KiCad's sequential result, since a split at one point changes no other point's verdict. A
    junction is written where the rule wants one and none is there: IsExplicitJunctionNeeded,
    or with ``allow`` (add_junctions) IsExplicitJunctionAllowed (sch_screen.cpp:479-504 at
    9.0.8). Every wire and bus through the point is then split wherever this call wrote a
    junction, one already sits, or a bus entry ends: the points kicad-cli 9 would cut a wire
    at (SCHEMATIC::BreakSegmentsOnJunctions, schematic.cpp:1090-1113 at 9.0.8). Returns
    (point, junction written, lines split) per distinct point."""
    plan = []
    for p in dict.fromkeys(points):
        info = _connectivity.junction_info(m, p, break_crossings=allow)
        plan.append((p, info.explicit and not info.dot, info))
    done = []
    for p, write, info in plan:
        if write:
            node = _JUNCTION_TPL.copy()
            at = node.find("at")
            at.atoms[1].set_text(_connectivity.mm(p[0]))
            at.atoms[2].set_text(_connectivity.mm(p[1]))
            node.find("uuid").atoms[1].set_text(_gen_uuid())
            _splice_sch_node(root, "junction", node)
        split = _split_lines_at(root, p) if write or info.dot or info.bus_entry else []
        done.append((p, write, split))
    return done


def _join_report(joined) -> list[str]:
    pt = _connectivity.pt
    out = []
    for p, wrote, split in joined:
        if wrote:
            out.append(f"Junction at {pt(p)}.")
        out += [f"Split the {kind} {pt(a)}-{pt(b)} at {pt(p)}." for kind, a, b in split]
    return out


#: The newest schematic format KiCad 9 reads (SEXPR_SCHEMATIC_FILE_VERSION,
#: sch_file_versions.h:117 at 9.0.8); kicad-cli 9 loads no file stamped later.
_K9_SCH_VERSION_MAX = 20250114


def _stretch(a: Point, b: Point) -> dict:
    mm = _connectivity.IU_PER_MM
    return {"x1": a[0] / mm, "y1": a[1] / mm, "x2": b[0] / mm, "y2": b[1] / mm}


def _cuts_inside_lines(root, path: str) -> list[dict]:
    """validate_hierarchy's issues for the points on one parsed sheet where a line is cut in
    kicad-cli 9 though KiCad's editor holds it whole or split: a junction strictly inside a
    wire or bus (junction_inside_wire), and on a file kicad-cli 9 loads, a bus-entry end
    strictly inside one with no junction there (bus_entry_inside_wire). KiCad's editor leaves
    neither: it splits every line through a junction it places (AddJunction,
    bus-wire-junction.cpp:186-197 at 9.0.8) and every line at every junction and bus-entry end
    when it loads a file, so an earlier version of this package wrote them. A sheet holding none
    costs this scan alone; the model is built only for one that holds some.

    Each line's kept and dropped stretches, on a file kicad-cli 9 loads: its load-time CleanUp
    cuts every line at every junction and bus-entry end inside it, and BreakSegment shortens the
    line to end there and, with no schematic holder, never adds the other half
    (schematic.cpp:1055-1113 and 1147 at 9.0.8), so the line keeps its first point to the cut
    nearest it. Then the editor's reading: it splits the same way, keeping both halves, then
    deletes a junction unless IsExplicitJunction holds there (1149-1158), which once split is
    what IsExplicitJunctionAllowed says before (junction_info with break_crossings). At a
    bus-entry end two line ends and the entry meet, so CleanUp's merge leaves the halves apart
    (MergeOverlap refuses at IsJunction, sch_line.cpp:556-557).

    The repair makes every reader read the line as the editor holds it: where KiCad allows a
    junction, add_junctions, which splits the line there; else, for a junction,
    remove_junction, which leaves the line whole (where a bus entry also ends there, the re-run
    reports that end). A bus-entry end where KiCad allows no junction gets a note instead. A
    KiCad 10 file holds no bus-entry cut: KiCad 10 breaks no line on load (CleanUp,
    schematic.cpp:1537-1728 at 10.0.7), and its graph reads a line through a bus-entry end
    whole, joining a wire there to nothing and a bus to the entry, since GetBus finds a bus
    anywhere along it (connection_graph.cpp:1438-1460 and 1523-1536, sch_screen.h:451-455). A
    sheet the model refuses, a symbol or pin at an angle KiCad cannot load, gets the reason in
    place of the readings and the repair."""
    lines = []
    for node in root.lists:
        xys = _line_xys(node) if node.head in ("wire", "bus") else None
        if xys is not None:
            lines.append((node.head, _xy_iu(xys[0]), _xy_iu(xys[1])))
    dots = [_xy_iu(at) for node in root.find_all("junction") if (at := node.find("at")) is not None]
    try:
        version = int(_connectivity._child_text(root, "version", "0"))
    except ValueError:
        version = 0
    k9 = version <= _K9_SCH_VERSION_MAX
    entries = _bus_entry_ends(root)
    rows: dict[int, list] = {}
    columns: dict[int, list] = {}
    slants = []
    for line in lines:
        _kind, a, b = line
        if a[1] == b[1]:
            rows.setdefault(a[1], []).append(line)
        elif a[0] == b[0]:
            columns.setdefault(a[0], []).append(line)
        else:
            slants.append(line)
    hits = {}
    points = [(p, "junction_inside_wire") for p in dict.fromkeys(dots)]
    if k9:
        points += [(p, "bus_entry_inside_wire") for p in sorted(entries - set(dots))]
    for p, kind in points:
        near = rows.get(p[1], []) + columns.get(p[0], []) + slants
        through = [ln for ln in near if _connectivity.inside(p[0], p[1], *ln[1], *ln[2])]
        if through:
            hits[p] = (kind, through)
    if not hits:
        return []
    cuts = set(dots) | entries
    try:
        model, why = _connectivity.Model(root), ""
    except _connectivity.Refusal as e:
        model, why = None, e.text
    readable = model is not None and k9
    issues = []
    for p, (kind, through) in hits.items():
        spot = {"x": p[0] / _connectivity.IU_PER_MM, "y": p[1] / _connectivity.IU_PER_MM}
        issue = {"type": kind, "file": path, **spot, "lines": []}
        for line_kind, a, b in through:
            line = {"kind": line_kind, **_stretch(a, b)}
            if readable:
                inner = (q for q in cuts if _connectivity.inside(q[0], q[1], *a, *b))
                cut = min(inner, key=lambda q: _along(a, b, q))
                line["kept"], line["dropped"] = _stretch(a, cut), _stretch(cut, b)
            issue["lines"].append(line)
        if model is None:
            issue["note"] = f"This sheet cannot be judged: {why}"
            issues.append(issue)
            continue
        allowed = _connectivity.junction_info(model, p, break_crossings=True).explicit
        if readable:
            if kind == "bus_entry_inside_wire":
                issue["editor"] = "splits_wire"
            else:
                issue["editor"] = "keeps_junction" if allowed else "deletes_junction"
        if allowed:
            issue["repair"] = {
                "tool": "add_junctions",
                "arguments": {"points": [dict(spot)], "schematic_path": path},
            }
        elif kind == "junction_inside_wire":
            issue["repair"] = {
                "tool": "remove_junction",
                "arguments": {**spot, "schematic_path": path},
            }
        else:
            # KiCad 9's editor splits the line as it loads the schematic, through a commit it
            # never pushes, and adding the second half marks nothing modified (files-io.cpp:
            # 537-539, schematic.cpp:1054-1072, sch_base_frame.cpp:459-479 at 9.0.8). Its save
            # then writes nothing to a root already in its own format unless something was
            # edited (files-io.cpp:999-1006); an undo counts as an edit (OnModify,
            # tools/sch_editor_control.cpp:1289-1316).
            issue["note"] = (
                "KiCad allows no junction at this bus-entry end, so no call here splits the"
                " line. KiCad 9's editor splits it when it opens the schematic, but saves a"
                " schematic already in its own format only once something was edited: there,"
                " make any edit and undo it, then save."
            )
        issues.append(issue)
    return issues


def _off_pin_items(root, path: str) -> list[dict]:
    """validate_hierarchy's issues for the no-connect flags (no_connect_off_pin) and wire ends
    (wire_end_off_pin) that releases before 0.21.0 left on one parsed sheet where they put a
    pin. They mirrored a pin before rotating it and KiCad rotates first, so at rotation 90 or
    270 with a mirror their point is the pin's end reflected through the symbol's origin
    (_connectivity._old_order_transform); 0.20.1's no_connect_pin, wire_pins_to_net and
    connect_pins all wrote there (measured: tests/test_ladder_r3_mirrored_pins.py).

    Only a point where KiCad draws no pin is checked. On another pin's end, which is where
    the other pin of a two-pin part lies, those releases wrote the bytes an edit of that pin
    writes, so nothing tells the two apart. A flag at the point is reported unless a sheet pin
    or a hierarchical label is there too, which KiCad accepts a flag on. A wire end is left
    alone when anything else ends at the point but such a flag and the label 0.20.1's
    connect_pins put there, Net-(<ref>-<pin>) with the pin as the call named it: KiCad then
    reads the end as connected (SCH_LINE::UpdateDanglingState, sch_line.cpp:596-640 at
    9.0.8), and the wire ends KiCad's own demos hold at such points all meet something. Either
    is left alone where any reader may join it to the pin already (the model's possible view):
    the flag then marks the pin, and the wire gives it what it was meant to, as once its repair
    is made.

    A flag carries a note, since no tool removes a flag by its position. A wire end's repair
    joins the pin to what the wire reaches: wire_pins_to_net with the one local label name on
    the wire's net, if planning that call refuses nothing; else connect_pins to the one pin a
    run of wires from it ends at, if connect_pins finds both pins where KiCad draws them;
    else a note. A sheet the model refuses gets nothing: KiCad cannot load it either."""
    if not any(_connectivity.effective_mirror(s) for s in root.find_all("symbol")):
        return []
    try:
        model = _connectivity.Model(root)
    except _connectivity.Refusal:
        return []
    model.ensure()
    drawn = {(it.x, it.y) for it in model.items if it.kind == "pin"}
    issues = []
    for s in model.syms:
        if s.lib is None or s.derived or _connectivity.effective_mirror(s.node) is None:
            continue
        pos, t = _connectivity.symbol_transform(s.node, s.ref)
        old_pos, old_t = _connectivity._old_order_transform(s.node, s.ref)
        subs = {id(sub): sub for u in s.units for sub in _instance_units(s.lib, u, s.style)}
        for sub in subs.values():
            for pin in sub.find_all("pin"):
                if pin.find("at") is None:
                    continue
                end = _connectivity.pin_end(pos, t, pin, s.ref)[:2]
                at = _connectivity.pin_end(old_pos, old_t, pin, s.ref)[:2]
                if at != end and at not in drawn:
                    issues += _left_at(model, root, s, pin, at, end, path)
    return issues


def _left_at(model, root, s, pin, at: Point, end: Point, path: str) -> list[dict]:
    """_off_pin_items' issues for one pin of placed symbol s: what lies at the point at, where
    KiCad draws no pin, for the pin KiCad draws at end."""
    num = _connectivity._child_text(pin, "number")
    name = _connectivity._child_text(pin, "name")
    mm = _connectivity.IU_PER_MM
    base = {
        "file": path,
        "reference": s.ref,
        "pin": num,
        "x": at[0] / mm,
        "y": at[1] / mm,
        "pin_x": end[0] / mm,
        "pin_y": end[1] / mm,
    }
    pt = _connectivity.pt
    here = model._pts.get(at, [])
    pins = [it for it in model._pts.get(end, ()) if it.kind == "pin" and it.sym is s]

    def joined(it) -> bool:
        """Whether any reader may join it to the pin already: the model's possible view, which
        the model builds once."""
        possible = model.coarse().uf
        return any(possible.find(p.id) == possible.find(it.id) for p in pins)

    out = []
    # KiCad accepts a no-connect at a sheet pin or a hierarchical label as marking that port
    # (ercCheckNoConnects, connection_graph.cpp:3735-3750 at 9.0.8).
    port = any(
        it.kind == "sheetpin" or (it.kind == "label" and it.sub == "hierarchical label")
        for it in here
    )
    flags = [] if port else [it for it in here if it.kind == "nc" and not joined(it)]
    if flags:
        if any(it.kind == "nc" for it in model._pts.get(end, ())):
            then = "; the pin is flagged already."
        elif _get_pin_or_none(root, s.ref, num) == end:
            then = f", then no_connect_pin({s.ref!r}, {num!r}) flags the pin."
        else:
            then = ", and flag the pin there too."
        note = (
            f"Releases before 0.21.0 put this flag here for {s.ref} pin {num}, at the point"
            f" they gave the pin, mirrored before rotated; KiCad draws the pin at {pt(end)},"
            " so the flag misses it. No tool removes a no-connect flag by its position"
            " (remove_no_connect looks at the pin KiCad draws): delete it in KiCad's editor"
            f"{then}"
        )
        out += [{"type": "no_connect_off_pin", **base, "note": note} for _flag in flags]
    own = {f"Net-({s.ref}-{num})", f"Net-({s.ref}-{name})"}
    # dict.fromkeys: a wire of no length is listed at its point twice.
    for w in dict.fromkeys(it for it in here if it.kind == "wire"):
        others = [
            it
            for it in here
            if it is not w
            and it.kind != "nc"
            and not (
                it.kind == "label"
                and it.sub == "label"
                and _connectivity.unescape(it.text or "") in own
            )
        ]
        if others or joined(w):
            continue
        issue = {"type": "wire_end_off_pin", **base, "wire": _stretch((w.x, w.y), (w.x2, w.y2))}
        repair, why = _join_repair(model, root, s.ref, num, w, at, end, path)
        if repair is not None:
            issue["repair"] = repair
        else:
            issue["note"] = (
                f"Releases before 0.21.0 ended this wire at the point they gave {s.ref} pin"
                f" {num}, mirrored before rotated; KiCad draws the pin at {pt(end)}, so the"
                f" wire misses it. {why}"
            )
        out.append(issue)
    return out


def _get_pin_or_none(root, reference: str, pin_name: str) -> Point | None:
    """Where connect_pins and no_connect_pin find a pin, in internal units, or None."""
    try:
        x, y, _ = _get_pin_pos_cst(root, reference, pin_name)
    except ValueError:
        return None
    return _req_point(x, y)


def _join_repair(model, root, ref: str, num: str, w, at: Point, end: Point, path: str):
    """(repair, None) joining pin num of ref to the net of wire w, whose end at misses it; or
    (None, why not). See _off_pin_items."""
    labels = (
        it
        for its in model.narrow_names(w).values()
        for it in its
        if it.kind == "label" and it.sub == "label"
    )
    usable: dict[str, str] = {}
    for text in sorted({it.text for it in labels if it.text}):
        try:
            _connectivity.check_args(text, "auto", 2.54)
        except _connectivity.Refusal:
            continue
        usable.setdefault(_connectivity.netname(text), text)
    mm = _connectivity.mm
    remove = f"remove_wire({mm(w.x)}, {mm(w.y)}, {mm(w.x2)}, {mm(w.y2)}) removes this wire."
    refused = ""
    if len(usable) == 1:
        (text,) = usable.values()
        pins = [{"reference": ref, "pin": num}]
        plan = _connectivity.plan_wire_pins(root, pins, text, "auto", 2.54, path)
        if not plan.refused:
            arguments = {"pins": pins, "label_text": text, "schematic_path": path}
            return {"tool": "wire_pins_to_net", "arguments": arguments}, None
        refused = (
            f"wire_pins_to_net would put the pin on {text!r}, the wire's net, but refuses: "
            + "; ".join(plan.lines)
        )
    far = _run_end(model, w, at)
    if far is not None and far.ref and far.num:
        here = _get_pin_or_none(root, ref, num) == end
        there = _get_pin_or_none(root, far.ref, far.num) == (far.x, far.y)
        if here and there:
            arguments = {"ref1": ref, "pin1": num, "ref2": far.ref, "pin2": far.num}
            return {
                "tool": "connect_pins",
                "arguments": {**arguments, "schematic_path": path},
            }, None
    if refused:
        return None, f"{refused} Act on that, or wire the pin as meant; {remove}"
    return None, (
        "Its net carries no local label to put the pin on, and its wires run to no single"
        f" pin, so what it was meant to join is not known here: wire the pin as meant; {remove}"
    )


def _run_end(model, w, at: Point):
    """The one pin a run of wires from wire w ends at: from w's end that is not at, along the
    one other wire ending at each point where nothing else does, to a point holding pins.
    None where the run branches, meets anything else, or ends at no pin or at two."""
    seen = {w.id}
    q = (w.x2, w.y2) if (w.x, w.y) == at else (w.x, w.y)
    while True:
        here = [it for it in model._pts.get(q, ()) if it.id not in seen]
        pins = [it for it in here if it.kind == "pin"]
        if pins:
            return pins[0] if len(pins) == 1 else None
        if len(here) != 1 or here[0].kind != "wire":
            return None
        w = here[0]
        seen.add(w.id)
        q = (w.x2, w.y2) if (w.x, w.y) == q else (w.x, w.y)


def _plain_stroke(node) -> bool:
    """Whether a line is drawn as the call's own wires are, (width 0) (type default): equal
    width and colour, and a default or solid style (SCH_LINE::IsStrokeEquivalent,
    sch_line.h:195-209 at 9.0.8). An absent colour is COLOR4D::UNSPECIFIED, 0 0 0 0."""
    stroke = node.find("stroke")
    if stroke is None:
        return True
    try:
        width = stroke.find("width")
        if width is not None and _connectivity.iu(width.atoms[1].text) != 0:
            return False
        color = stroke.find("color")
        if color is not None and any(float(a.text) != 0 for a in color.atoms[1:]):
            return False
    except (IndexError, ValueError):
        return False
    style = stroke.find("type")
    return style is None or len(style.atoms) < 2 or style.atoms[1].text in ("default", "solid")


def _plain_wires(root) -> list[tuple[Point, Point]]:
    out = []
    for node in root.find_all("wire"):
        xys = _line_xys(node)
        if xys is not None and _plain_stroke(node):
            out.append((_xy_iu(xys[0]), _xy_iu(xys[1])))
    return out


def _along(a: Point, b: Point, q: Point) -> int:
    return (q[0] - a[0]) * (b[0] - a[0]) + (q[1] - a[1]) * (b[1] - a[1])


def _new_pieces(a: Point, b: Point, others):
    """CleanUp's merge for one requested wire a-b (schematic.cpp:1212-1303 at 9.0.8), touching
    no wire already there: a-b is cut at every end of a collinear wire of the same stroke lying
    strictly inside it, and each piece such a wire covers is left out (coverage is constant
    between two cuts, so a piece's midpoint decides, in doubled coordinates). Returns (pieces
    to write, pieces left out)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    same = [
        (c, d)
        for c, d in others
        if c != d and all(dx * (q[1] - a[1]) == dy * (q[0] - a[0]) for q in (c, d))
    ]
    cuts = {
        q
        for c, d in same
        for q in (c, d)
        if _connectivity.inside(q[0], q[1], a[0], a[1], b[0], b[1])
    }
    stops = [a, *sorted(cuts, key=lambda q: _along(a, b, q)), b]
    new, covered = [], []
    for p, q in zip(stops, stops[1:]):
        mx, my = p[0] + q[0], p[1] + q[1]
        within = _connectivity._within
        hit = any(within(mx, my, 2 * c[0], 2 * c[1], 2 * d[0], 2 * d[1], 0) for c, d in same)
        (covered if hit else new).append((p, q))
    return new, covered


def _two_pin_parts(m) -> list[tuple[str, Point, Point]]:
    """(reference, pin, pin) for each placed part drawing exactly two pins at two points: the
    parts finishSegments runs TrimWire for (sch_line_wire_bus_tool.cpp:1244-1261 at 9.0.8)."""
    pins: dict[int, list] = {}
    for it in m.items:
        if it.kind == "pin" and it.sym is not None:
            pins.setdefault(id(it.sym), []).append(it)
    out = []
    for its in pins.values():
        if len(its) == 2 and (its[0].x, its[0].y) != (its[1].x, its[1].y):
            out.append((its[0].ref or "?", (its[0].x, its[0].y), (its[1].x, its[1].y)))
    return out


def _merge_and_trim(m, a: Point, b: Point, plain, pieces, parts, raw):
    """CleanUp's merge, then TrimWire, for one requested wire a-b, in finishSegments' order
    (sch_line_wire_bus_tool.cpp:1242-1261 at 9.0.8). The merged run is a-b with every
    collinear wire of its stroke, already on the sheet or one of the call's earlier pieces,
    that it overlaps, or meets end to end where no junction is, transitively (CleanUp's
    MergeOverlap, schematic.cpp:1212-1303 and sch_line.cpp:453-571 at 9.0.8). For each part
    of two pins both on the run, unless the run is exactly the wire from one pin to the other
    (bus-wire-junction.cpp:83-88), the stretch between the pins goes from a-b and from the
    call's earlier pieces (TrimWire, 50-112). KiCad cuts it out of a wire already on the
    sheet too; such a wire is left as drawn, which changes no join unless it runs through a
    pin, and then the call is refused. Returns (a-b's stretches left, the call's pieces, the
    stretches trimmed as (reference, pin, pin))."""
    dx, dy = b[0] - a[0], b[1] - a[1]

    def on(q: Point) -> bool:
        return dx * (q[1] - a[1]) == dy * (q[0] - a[0])

    def at(q: Point) -> int:
        return _along(a, b, q)

    lines = []  # (already on the sheet, (start, end), low, high) along a-b's line
    for old, (c, d) in [(True, s) for s in plain] + [(False, s) for s in pieces]:
        if c != d and on(c) and on(d):
            lines.append((old, (c, d), *sorted((at(c), at(d)))))
    lo, hi = 0, at(b)
    chain = []
    grown = True
    while grown:
        grown = False
        for ln in lines:
            if ln in chain:
                continue
            old, (c, d), s, e = ln
            if not max(lo, s) < min(hi, e):  # no overlap: merged only end to end
                if e != lo and s != hi:
                    continue
                touch = c if at(c) == (e if e == lo else s) else d
                if _connectivity.junction_info(m, touch, break_crossings=False, extra=raw).junction:
                    continue
            chain.append(ln)
            lo, hi = min(lo, s), max(hi, e)
            grown = True
    runs = [(lo, hi)]
    cuts = []
    for ref, p1, p2 in parts:
        if not (on(p1) and on(p2)):
            continue
        u, v = sorted((at(p1), at(p2)))
        for k, (s, e) in enumerate(runs):
            if s <= u and v <= e and (s, e) != (u, v):
                runs[k : k + 1] = [r for r in ((s, u), (v, e)) if r[0] < r[1]]
                cuts.append((u, v, ref, *sorted((p1, p2), key=at)))
                break
    if not cuts:
        return [(a, b)], pieces, []
    pt = _connectivity.pt
    for old, (c, d), s, e in chain:
        for u, v, ref, p1, p2 in cuts:
            if old and (s < u < e or s < v < e):
                raise ToolError(
                    f"add_wires refused; nothing was written. KiCad's wire tool would merge the"
                    f" wire {pt(a)}-{pt(b)} with the wire {pt(c)}-{pt(d)} already on the sheet"
                    f" and cut that one between the pins of {ref} at {pt(p1)} and {pt(p2)};"
                    " add_wires edits a wire already on the sheet only by splitting it. Draw"
                    f" the new wire to stop at a pin of {ref}, or remove the old wire first."
                )
    pos = {at(q): q for q in (a, b)}
    for u, v, ref, p1, p2 in cuts:
        pos[u], pos[v] = p1, p2

    def left(s: int, e: int) -> list[tuple[int, int]]:
        spans = [(s, e)]
        for u, v, *_ in cuts:
            spans = [
                r
                for x, y in spans
                for r in (((x, y),) if y <= u or x >= v else ((x, u), (v, y)))
                if r[0] < r[1]
            ]
        return spans

    kept = [(pos[x], pos[y]) for x, y in left(0, at(b))]
    merged = {ln[1] for ln in chain if not ln[0]}
    out = []
    for c, d in pieces:
        if (c, d) not in merged:
            out.append((c, d))
            continue
        pos[at(c)], pos[at(d)] = c, d
        spans = left(*sorted((at(c), at(d))))
        out += [(pos[x], pos[y]) if at(c) < at(d) else (pos[y], pos[x]) for x, y in spans]
    return kept, out, [(ref, p1, p2) for _, _, ref, p1, p2 in cuts]


def _bus_entry_ends(root) -> set[Point]:
    """Both ends of every bus entry: its position, and that plus its size
    (SCH_BUS_ENTRY_BASE::GetEnd, sch_bus_entry.cpp:131-134 at 9.0.8)."""
    ends = set()
    for node in root.find_all("bus_entry"):
        at, size = node.find("at"), node.find("size")
        if at is None:
            continue
        x, y = _xy_iu(at)
        dx, dy = _xy_iu(size) if size is not None else (0, 0)
        ends |= {(x, y), (x + dx, y + dy)}
    return ends


class _End:
    """One line of DeleteJunction's list: its node, layer, ends, the spelling of each end, and
    whether it is still alive."""

    __slots__ = ("node", "kind", "a", "b", "texts", "alive")

    def __init__(self, node, kind, a, b, texts):
        self.node, self.kind, self.a, self.b, self.texts = node, kind, a, b, texts
        self.alive = True


def _merge_overlap(first: _End, second: _End):
    """second.MergeOverlap( first ) with aCheckJunctions false (sch_line.cpp:453-571 at 9.0.8):
    the union of two collinear lines that touch or overlap, ends ordered x first, or None."""
    left = sorted((first.a, first.b))
    right = sorted((second.a, second.b))
    if right[0] < left[0]:
        left, right = right, left
    other = list(right)
    if right[1] < left[1]:
        right = list(left)
    if left[1] < other[0]:
        return None
    if left == other:
        return tuple(left)
    (lx, ly), (ex, ey) = left
    (ox, oy), (fx, fy) = other
    if ly == ey and oy == fy:
        collinear = ly == oy
    elif lx == ex and ox == fx:
        collinear = lx == ox
    else:
        dx, dy = ex - lx, ey - ly
        collinear = (oy - ly) * dx == (ox - lx) * dy and (fy - ly) * dx == (fx - lx) * dy
    return (left[0], right[1]) if collinear else None


def _merge_at(root, p: Point) -> tuple[list[str], set]:
    """DeleteJunction's pair loop (bus-wire-junction.cpp:115-185 at 9.0.8, 117-187 at 10.0.7)
    over the wires and buses ending at p, in file order, each merge appended to the list as
    KiCad appends it: a pair that is not parallel is skipped; of two identical lines one goes
    (KiCad's pick follows its spatial index; the later in the list goes here); two collinear
    lines on one layer that touch or overlap become one, carried by the first's node, whose
    moved end takes the absorbed line's own spelling. Returns the report lines and the uuids
    of the lines KiCad removes, a merged pair's both: its merged line is a new item in no
    group (sch_commit.cpp:265-268 and 323-324 at 10.0.7)."""
    ends = []
    for node in root.lists:
        if node.head not in ("wire", "bus"):
            continue
        xys = _line_xys(node)
        if xys is None:
            continue
        a, b = _xy_iu(xys[0]), _xy_iu(xys[1])
        if p in (a, b):
            texts = {
                q: (xy.atoms[1].text, xy.atoms[2].text) for q, xy in ((a, xys[0]), (b, xys[1]))
            }
            ends.append(_End(node, node.head, a, b, texts))
    pt = _connectivity.pt
    report = []
    i = 0
    while i < len(ends):
        first = ends[i]
        j = i + 1
        while j < len(ends):
            second = ends[j]
            j += 1
            if not (first.alive and second.alive):
                continue
            u = (first.b[0] - first.a[0], first.b[1] - first.a[1])
            v = (second.b[0] - second.a[0], second.b[1] - second.a[1])
            if u[0] * v[1] - u[1] * v[0]:
                continue
            if {first.a, first.b} == {second.a, second.b}:
                second.alive = False
                report.append(
                    f"Removed a {second.kind} identical to another, {pt(second.a)}-{pt(second.b)}."
                )
                continue
            merged = _merge_overlap(first, second) if first.kind == second.kind else None
            if merged is None:
                continue
            first.alive = second.alive = False
            texts = {**second.texts, **first.texts}
            ends.append(_End(first.node, first.kind, merged[0], merged[1], texts))
            report.append(
                f"Merged the {first.kind}s {pt(first.a)}-{pt(first.b)} and {pt(second.a)}-"
                f"{pt(second.b)} into {pt(merged[0])}-{pt(merged[1])}."
            )
        i += 1
    live = {id(e.node) for e in ends if e.alive}
    removed = set()
    gone = set()
    for e in ends:
        if e.alive:
            # The node takes the merged ends: points it already holds stay, counted as a
            # multiset, so a line of no length that carries a merge gets its far end
            # (MergeOverlap returns the first line with the merged ends, sch_line.cpp:559-570).
            xys = e.node.find("pts").find_all("xy")
            want = [e.a, e.b]
            moved = []
            for k in (0, 1):
                q = _xy_iu(xys[k])
                if q in want:
                    want.remove(q)
                else:
                    moved.append(k)
            for k, q in zip(moved, want):
                _set_xy(xys[k], q, e.texts[q])
        else:
            gone.add(_node_uuid(e.node))
            if id(e.node) not in live and id(e.node) not in removed:
                removed.add(id(e.node))
                root.remove_child(e.node)
    return report, gone


def _leave_groups(root, uuids) -> None:
    """Take removed items out of KiCad 10 groups' members, and drop a group left with none:
    KiCad's commit takes a removed item out of its group (sch_commit.cpp:323-324 at 10.0.7)
    and saveGroup writes no empty group (sch_io_kicad_sexpr.cpp:1635-1637 at 10.0.7)."""
    uuids = {u for u in uuids if u}
    if not uuids:
        return
    for group in root.find_all("group"):
        members = group.find("members")
        if members is None:
            continue
        for atom in [a for a in members.atoms[1:] if a.text in uuids]:
            members.remove_child(atom)
        if len(members.atoms) == 1:
            root.remove_child(group)


def _find_syms_cst(root, reference: str):
    """Every placed symbol whose Reference property matches, in file order.

    A multi-unit part is placed as one symbol node per unit and they all carry
    the same reference, so this is a list wherever units can be split.
    """
    return [s for s in root.find_all("symbol") if _sym_property_cst(s, "Reference") == reference]


def _find_sym_cst(root, reference: str):
    """First placed symbol whose Reference property matches, or None."""
    found = _find_syms_cst(root, reference)
    return found[0] if found else None


def _find_lib_symbol_cst(root, lib_id: str):
    """The lib_symbols entry for *lib_id*, or None: bare and prefixed names both match."""
    bare = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    libs = root.find("lib_symbols")
    for ls in libs.find_all("symbol") if libs is not None else []:
        raw = ls.atoms[1].text
        entry = raw.split(":")[-1] if ":" in raw else raw
        if entry == bare or entry == lib_id or raw == lib_id:
            return ls
    return None


def _drawn_pin_pos_cst(root, target, pin_name: str, reference: str):
    """Absolute (x, y, outward) of *pin_name* if the placed node *target* draws it.

    None when it does not: the pin is on another unit, or on this unit's
    other body style. Pin match is name-OR-number per pin in file order.
    """
    lib_sym = _connectivity.lib_index(root).get(_connectivity.lib_key(target))
    if lib_sym is None:
        raise ValueError(f"Lib symbol for {reference} not found")
    for unit in _instance_units(lib_sym, _sym_unit_cst(target), _sym_body_style_cst(target)):
        for pin in unit.find_all("pin"):
            if pin_matches(pin, pin_name):
                try:
                    return _connectivity.pin_point_mm(target, pin, reference)
                except _connectivity.Refusal as e:
                    raise ValueError(e.text) from None
    return None


def _get_pin_pos_cst(root, reference: str, pin_name: str) -> tuple[float, float, float]:
    """Sheet position (mm) and outward angle of *reference*'s pin *pin_name*.

    The pin matches by name or number, per pin in file order; ValueError when
    the part or the pin is missing. The part is found by its Reference property
    and drawn with its top-level unit, which can differ from the reference and
    unit KiCad reads at the sheet's live instance path (a stale property, a
    reused sheet's other instances).

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
    lib_sym = _connectivity.lib_index(root).get(_connectivity.lib_key(targets[0]))
    placed = [(_sym_unit_cst(t), _sym_body_style_cst(t)) for t in targets]
    raise ValueError(not_drawn_message(reference, pin_name, lib_sym, placed))


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
    knowledge); only the name atom is rewritten to *new_name*. A derived symbol
    is refused rather than copied; see _refuse_derived.
    """
    _, lib_root = _open_sym_lib(lib_path)
    for s in lib_root.find_all("symbol"):
        if s.atoms[1].text == symbol_name:
            _refuse_derived(s, new_name)
            node = s.copy()
            node.atoms[1].set_text(new_name)
            _splice_lib_symbol_cst(root, node)
            return True
    return False


def _refuse_derived(definition, name: str) -> None:
    """Refuse a derived symbol definition, which holds no pins of its own.

    A derived symbol is an (extends "Parent") plus properties, with the body and
    pins left in the parent; 12,127 of the stock symbols in a KiCad 9 install
    are derived (counted 2026-10-02). KiCad embeds one flattened: the three
    KiCad 9 demo schematics that use a derived stock symbol all carry the
    parent's pins and no (extends ...). Copied verbatim, the stub names a parent
    the schematic does not have, and kicad-cli 9 then refuses to load the whole
    file: "Failed to load schematic" for Regulator_Linear:AMS1117-3.3, measured
    2026-10-02, and the same file loaded once the (extends ...) node was removed.

    Flattening is not implemented yet, so the honest outcome is a refusal that
    names the parent: a derived symbol shares its parent's pins, so placing the
    parent and setting its fields gets the same part.
    """
    extends = definition.find("extends")
    if extends is None:
        return
    parent = extends.atoms[1].text
    prefix = name.split(":")[0] + ":" if ":" in name else ""
    raise ToolError(
        f"'{name}' is a derived symbol: it extends '{parent}' and has no pins of its"
        " own, and this server cannot flatten derived symbols yet. Embedded as it"
        " stands, it makes KiCad refuse to load the schematic. Use"
        f" '{prefix}{parent}' instead, which has the same pins, and set its Value,"
        " Footprint and Datasheet with set_component_property. Nothing was written."
    )


def _no_definition_message(
    lib_id: str, symbol_name: str, lib_prefix: str, symbol_lib_path: str
) -> str:
    """Why place_component has no definition for *lib_id*, and the ways out."""
    lib_path = symbol_lib_path or (_resolve_system_lib(lib_prefix) if lib_prefix else None)
    if lib_path:
        # A library was read and lacks the symbol: name it, and what is close.
        _, lib_root = _open_sym_lib(lib_path)
        names = [s.atoms[1].text for s in lib_root.find_all("symbol")]
        similar = difflib.get_close_matches(symbol_name, names, n=5, cutoff=0.4)
        where = f"{lib_prefix} library ({lib_path})" if lib_prefix else lib_path
        hint = (
            f"Similar: {', '.join(similar)}."
            if similar
            else f"list_lib_symbols on {lib_path} shows what it holds."
        )
        return (
            f"'{lib_id}': symbol '{symbol_name}' not found in {where}. {hint} Nothing was written."
        )
    if lib_prefix:
        # No library file by that name in any folder the search covers.
        searched = ", ".join(str(d) for d in _system_sym_dirs())
        unknown = (
            ""
            if _kicad_root()
            else " kicad-cli was not found, so KiCad's own library folder is unknown."
        )
        return (
            f"'{lib_id}' not found: no {lib_prefix}.kicad_sym in {searched}.{unknown}"
            " A lib_id prefix only resolves against KiCad's stock symbol libraries; a"
            " project sym-lib-table is not read. Pass symbol_lib_path to the .kicad_sym"
            f" that defines {symbol_name}, set KICAD_SYMBOL_DIR to the folder holding"
            f" {lib_prefix}.kicad_sym, or load the definition first with add_lib_symbol."
            " Nothing was written."
        )
    return (
        f"'{lib_id}' not found: no symbol of that name is defined in this schematic,"
        " and a lib_id without a library prefix has nowhere to load one from. Use the"
        " Library:Symbol form (e.g. 'Device:R'), pass symbol_lib_path to a .kicad_sym"
        " that defines it, or load it first with add_lib_symbol. Nothing was written."
    )


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
    text: str,
    x: float,
    y: float,
    rotation: Literal[0, 90, 180, 270] = 0,
    schematic_path: str = SCH_PATH,
) -> str:
    """Add a net label at a position.

    Args:
        text: Net name (e.g. "VIN_PROT", "5V_REL")
        x: X position
        y: Y position
        rotation: Degrees (0=right, 90=up, 180=left, 270=down)
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    x, y = _snap(x, SCH_IU_PER_MM), _snap(y, SCH_IU_PER_MM)
    _bounds_check(x, y, page_w, page_h, page_name)
    node = _LABEL_TPL.copy()
    node.atoms[1].set_text(text)
    _check_rotation(rotation)
    _fill_at(node, x, y, rotation, SCH_IU_PER_MM)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "label", node)
    _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Label '{text}' at ({x}, {y})"


@mcp.tool(annotations=_ADDITIVE)
def add_junctions(points: list[PointSpec], schematic_path: str = SCH_PATH) -> str:
    """Add multiple junctions, as KiCad's junction tool places them. Each point dict has
    keys: x, y.

    A junction joins only where three or more wire directions, pins or sheet pins meet: a
    crossing, a T, a wire end at a pin with another wire. Anywhere else (empty space, a
    straight wire's interior, an L corner, a wire ending at a pin) KiCad's editor refuses it,
    "Junction location contains no joinable wires and/or pins." (sch_drawing_tools.cpp:
    1465-1497 at 9.0.8), and its cleanup deletes one placed there, so the whole call is
    refused, naming each such point, and nothing is written. Every wire through a junction
    is split there, which kicad-cli 9 needs to read the join; where a junction is already
    exactly at a point no second one is added, but a wire still running through it is split.
    A point where KiCad allows a junction, or where one already is, is taken even off the
    page: it lies on wires or pins already drawn, which KiCad reads there as anywhere else.
    Any other point off the page is refused as off the page. The result names each junction
    written, each one already there and each split.

    Args:
        points: List of junction positions [{x, y}, ...]
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    before = _cst.serialize(tree)
    m = _model(root)
    wanted = {}
    for spec in points:
        wanted.setdefault(_req_point(spec["x"], spec["y"]), spec)
    refused = []
    for q, spec in wanted.items():
        info = _connectivity.junction_info(m, q, break_crossings=True)
        if not info.dot and not info.explicit:
            refused.append(spec)
    # The page check is for a point the rule refuses, where it names the likelier mistake. A
    # point KiCad takes a junction at, or that holds one, lies on wires or pins already drawn,
    # which KiCad reads off the page as anywhere else, so the repairs validate_hierarchy names
    # hold there as well.
    for spec in refused:
        _bounds_check(
            _snap(spec["x"], SCH_IU_PER_MM),
            _snap(spec["y"], SCH_IU_PER_MM),
            page_w,
            page_h,
            page_name,
        )
    if refused:
        named = ", ".join(f"({spec['x']}, {spec['y']})" for spec in refused)
        raise ToolError(
            "add_junctions refused; nothing was written. KiCad's editor places a junction only"
            " where three or more wires, pins or sheet pins meet, and removes one placed"
            " anywhere else (empty space, a straight wire's interior, an L corner, a wire"
            f" ending at a pin): {named}."
        )
    joined = _join_points(root, m, list(wanted), allow=True)
    pt = _connectivity.pt
    lines = [f"Added {sum(wrote for _, wrote, _ in joined)} junctions"]
    for p, wrote, split in joined:
        if not wrote:
            lines.append(f"A junction is already at {pt(p)}.")
        lines += [f"Split the {kind} {pt(a)}-{pt(b)} at {pt(p)}." for kind, a, b in split]
    after = _cst.serialize(tree)
    if after == before:
        lines.append("No change was made to the file.")
    else:
        _atomic_write(schematic_path, after)
    return "\n".join(lines)


@mcp.tool(annotations=_ADDITIVE)
def add_lib_symbol(symbol_lib_path: str, symbol_name: str, schematic_path: str = SCH_PATH) -> str:
    """Load a symbol definition from a .kicad_sym library into the schematic.

    Args:
        symbol_lib_path: Path to .kicad_sym file. Optional; omit to use the configured default.
        symbol_name: Symbol name (e.g. "LM7805")
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    if _find_lib_symbol_cst(root, symbol_name) is not None:
        raise ToolError(f"'{symbol_name}' already in lib_symbols.")
    if not _copy_lib_symbol_from_file_cst(root, symbol_lib_path, symbol_name, symbol_name):
        raise ToolError(
            f"'{symbol_name}' not found in {symbol_lib_path}."
            " Use list_lib_symbols to see what the library contains."
        )
    _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Added '{symbol_name}' to lib_symbols."


@mcp.tool(annotations=_ADDITIVE)
def move_component(
    reference: str,
    x: float,
    y: float,
    rotation: Literal[0, 90, 180, 270] | None = None,
    schematic_path: str = SCH_PATH,
) -> str:
    """Move a placed component to a new position.

    Args:
        reference: Reference designator (e.g. "R1")
        x: New X position
        y: New Y position
        rotation: New rotation in degrees (None = keep current)
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    x, y = _snap_grid(x), _snap_grid(y)
    _bounds_check(x, y, page_w, page_h, page_name)
    sym = _find_sym_cst(root, reference)
    if sym is None:
        raise ToolError(f"Component {reference} not found." + _SEE_PLACED)
    _check_rotation(rotation)
    _fill_at(sym, x, y, rotation, SCH_IU_PER_MM)
    _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Moved {reference} to ({x}, {y})"


@mcp.tool(annotations=_ADDITIVE)
def set_component_property(
    reference: str,
    key: str,
    value: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Set any property on a placed component, or rename it. Creates the
    property if it is missing.

    Reference, Value and Footprint are mirrored into the symbol's own
    instances and into the root symbol_instances table, so setting
    key="Reference" is how a component is renamed and both views stay in
    step. The root table needs the .kicad_pro to resolve the hierarchy
    path; a loose .kicad_sch with no project beside it keeps the symbol
    correct and leaves that table alone.

    Args:
        reference: Component reference (e.g. "R1")
        key: Property name (e.g. "MPN", "Tolerance", "Value")
        value: Property value
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    sym = _find_sym_cst(root, reference)
    if sym is None:
        raise ToolError(f"Component {reference} not found." + _SEE_PLACED)
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
        _fill_at(node, cx, cy, iu=SCH_IU_PER_MM)
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
    _atomic_write(schematic_path, _cst.serialize(tree))
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
    _atomic_write(schematic_path, _cst.serialize(tree))

    if portrait:
        return f"Page size set to {size_key} ({h}x{w}mm, portrait)"
    return f"Page size set to {size_key} ({w}x{h}mm)"


#: KiCad uses one shape vocabulary for global and hierarchical labels.
_VALID_LABEL_SHAPES = {"input", "output", "bidirectional", "tri_state", "passive"}


@mcp.tool(annotations=_ADDITIVE)
def add_global_label(
    text: str,
    x: float,
    y: float,
    rotation: Literal[0, 90, 180, 270] = 0,
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    # add_hierarchical_label has always checked this; add_global_label never
    # did, so shape="banana" reached the file and kicad-cli then refused to
    # load the schematic. Found by the hostile-value sweep, 2026-08-12.
    if shape not in _VALID_LABEL_SHAPES:
        raise ToolError(f"invalid shape '{shape}'. Use: {', '.join(sorted(_VALID_LABEL_SHAPES))}")
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    x, y = _snap(x, SCH_IU_PER_MM), _snap(y, SCH_IU_PER_MM)
    _bounds_check(x, y, page_w, page_h, page_name)
    node = _GLABEL_TPL.copy()
    node.atoms[1].set_text(text)
    node.find("shape").atoms[1].set_text(shape)
    _check_rotation(rotation)
    _fill_at(node, x, y, rotation, SCH_IU_PER_MM)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "global_label", node)
    _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Global label '{text}' ({shape}) at ({x}, {y})"


@mcp.tool(annotations=_ADDITIVE)
def add_hierarchical_label(
    text: str,
    shape: str,
    x: float,
    y: float,
    rotation: Literal[0, 90, 180, 270] = 0,
    schematic_path: str = SCH_PATH,
) -> str:
    """Add a hierarchical label to a sub-sheet schematic.

    Args:
        text: Label name (must match parent sheet pin name)
        shape: Direction — input, output, bidirectional, tri_state, passive
        x: X position in mm
        y: Y position in mm
        rotation: Degrees (0, 90, 180, 270)
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    if shape not in _VALID_LABEL_SHAPES:
        raise ToolError(f"invalid shape '{shape}'. Use: {', '.join(sorted(_VALID_LABEL_SHAPES))}")
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    x, y = _snap(x, SCH_IU_PER_MM), _snap(y, SCH_IU_PER_MM)
    _bounds_check(x, y, page_w, page_h, page_name)
    node = _HLABEL_TPL.copy()
    node.atoms[1].set_text(text)
    node.find("shape").atoms[1].set_text(shape)
    _check_rotation(rotation)
    _fill_at(node, x, y, rotation, SCH_IU_PER_MM)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "hierarchical_label", node)
    _atomic_write(schematic_path, _cst.serialize(tree))
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
        raise ToolError(
            f"Hierarchical label '{text}' not found. Use"
            " list_schematic_hierarchical_labels to see what this sheet has."
        )
    target_text = _node_text(target)
    root.remove_child(target)
    _atomic_write(schematic_path, _cst.serialize(tree))
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        new_text: New label text (empty = keep current)
        new_shape: New shape/direction (empty = keep current)
        new_x: New X position (None = keep current)
        new_y: New Y position (None = keep current)
        uuid: UUID for disambiguation
    """
    if new_shape and new_shape not in _VALID_LABEL_SHAPES:
        raise ToolError(
            f"invalid shape '{new_shape}'. Use: {', '.join(sorted(_VALID_LABEL_SHAPES))}"
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
        raise ToolError(
            f"Hierarchical label '{text}' not found. Use"
            " list_schematic_hierarchical_labels to see what this sheet has."
        )
    changes = []
    if new_text:
        target.atoms[1].set_text(new_text)
        changes.append(f"text='{new_text}'")
    if new_shape:
        target.find("shape").atoms[1].set_text(new_shape)
        changes.append(f"shape={new_shape}")
    at = target.find("at")
    if new_x is not None:
        new_x = _snap(new_x, SCH_IU_PER_MM)
        at.atoms[1].set_text(_num(new_x, SCH_IU_PER_MM))
        changes.append(f"x={new_x}")
    if new_y is not None:
        new_y = _snap(new_y, SCH_IU_PER_MM)
        at.atoms[2].set_text(_num(new_y, SCH_IU_PER_MM))
        changes.append(f"y={new_y}")
    _atomic_write(schematic_path, _cst.serialize(tree))
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
    rotation: Literal[0, 90, 180, 270] = 0,
    symbol_lib_path: str = "",
    schematic_path: str = SCH_PATH,
    project_path: str = "",
) -> str:
    """Place a power symbol (VCC, GND, +3V3, etc.).

    Uses place_component internally. Power symbols are regular symbols
    from the 'power' library with isPower=True.

    Places only the symbol asked for. A PWR_FLAG tells ERC the net has a
    source it cannot see, which only the designer knows, so add one with
    lib_id="power:PWR_FLAG" on each net that needs it.

    Args:
        lib_id: Library ID (e.g. "power:VCC", "power:GND")
        reference: Reference (e.g. "#PWR01")
        x: X position
        y: Y position
        rotation: Rotation in degrees
        symbol_lib_path: Path to a .kicad_sym file that defines the power symbol,
            for a library outside KiCad's stock set. Optional; omit to load it
            from the stock library named by the lib_id prefix.
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        project_path: Path to .kicad_pro file (for sub-sheet instance tracking)
    """
    return place_component(
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


@mcp.tool(annotations=_ADDITIVE)
def auto_place_decoupling_cap(
    lib_id: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    power_net: str,
    ground_net: str,
    rotation: Literal[0, 90, 180, 270] = 0,
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
        symbol_lib_path: Path to a .kicad_sym file that defines the capacitor,
            for a library outside KiCad's stock set. Optional; omit to load it
            from the stock library named by the lib_id prefix.
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        project_path: Path to .kicad_pro file (for sub-sheet instance tracking)
    """
    # The net names need nothing from the sheet, so they are checked before the first write.
    # What the stubs will meet is known only once the cap is placed, and the cap and each pin
    # are written separately, so a later refusal leaves the earlier writes on disk; it then says
    # what they were and how to undo them.
    for param, net in (("power_net", power_net), ("ground_net", ground_net)):
        try:
            _connectivity.check_args(net, "auto", 2.54, param=param)
        except _connectivity.Refusal as e:
            raise ToolError(
                f"{_connectivity.tags(e.codes)} {e.text} Nothing was written."
            ) from None
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
    done = f"placed {reference}"
    undo = [f"remove_component({reference!r})"]
    notes: list[str] = []
    pins = (("1", power_net, "up", "power_net"), ("2", ground_net, "down", "ground_net"))
    for pin, net, direction, param in pins:
        try:
            plan = _wire_pins(
                [{"reference": reference, "pin": pin}],
                net,
                direction,
                2.54,
                schematic_path,
                param=param,
                turns=False,  # each pin's direction is fixed here
            )
        except _WireRefused as e:
            raise ToolError(
                f"{_connectivity.tags(e.plan.codes)} auto_place_decoupling_cap {done}, but wiring"
                f" pin {pin} to {net!r} was refused, and what it wrote stays on disk. To undo it:"
                f" {', '.join(undo)}. Those remove what it placed and wired; any library symbol"
                " it copied into lib_symbols stays, and no net depends on it.\n- "
                + "\n- ".join(e.plan.lines)
            ) from None
        assert plan is not None  # one pin, so the file was read
        notes += [n for n in plan.notes if n not in notes]
        done += f" and wired pin {pin} to {net!r}"
        mm = _connectivity.mm
        undo += [
            f"remove_wire({mm(a[0])}, {mm(a[1])}, {mm(b[0])}, {mm(b[1])})" for a, b in plan.wires
        ]
        undo += [f"remove_label({net!r}, {mm(lx)}, {mm(ly)})" for (lx, ly), _r in plan.labels]

    # The wiring's notes say, among other things, when a net name is new on this sheet: a
    # power symbol or global label of that name elsewhere does not reach the cap.
    return "\n".join([f"{result} | pin 1->{power_net} | pin 2->{ground_net}", *notes])


@mcp.tool(annotations=_ADDITIVE)
def add_text(
    text: str,
    x: float,
    y: float,
    rotation: Literal[0, 90, 180, 270] = 0,
    schematic_path: str = SCH_PATH,
) -> str:
    """Add a text annotation to the schematic.

    Args:
        text: Text content
        x: X position
        y: Y position
        rotation: Rotation in degrees
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, page_w, page_h, page_name = _open_sch_cst(schematic_path)
    x, y = _snap(x, SCH_IU_PER_MM), _snap(y, SCH_IU_PER_MM)
    _bounds_check(x, y, page_w, page_h, page_name)
    node = _TEXT_TPL.copy()
    node.atoms[1].set_text(text)
    _check_rotation(rotation)
    _fill_at(node, x, y, rotation, SCH_IU_PER_MM)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "text", node)
    _atomic_write(schematic_path, _cst.serialize(tree))
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
    _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Removed {len(matched)} text(s) '{text}'."


# ---------------------------------------------------------------------------
# High-level routing tools (4)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_ADDITIVE)
def wire_pins_to_net(
    pins: list[PinRefSpec],
    label_text: str,
    direction: Literal["auto", "left", "right", "up", "down"] = "auto",
    stub_length: float = 2.54,
    schematic_path: str = SCH_PATH,
) -> str:
    """Put each listed pin on the net named label_text, or refuse and write nothing.

    Each pin gets a short wire stub pointing away from its symbol, with a net
    label at the stub's end. When the stub would touch, overlap or cross
    anything else on the sheet, or move a net into another net class, the
    label goes on the pin end itself instead. No junction is ever written, and
    a pin already on the net is left alone. The file is written once or not
    at all.

    The whole call is refused when any pin cannot be wired safely: its net may
    already carry another name, it may reach something this tool cannot judge
    (a bus, a sheet pin, a text variable, a copy of the pad on another sheet),
    another part shares its reference, or every position is blocked. The
    refusal lists every refused pin with a bracketed reason code such as
    [names] or [touch], the obstacle, and a remedy. add_label and add_wires
    check none of this, so they are no way around a refusal.

    It places no PWR_FLAG: whether a net needs one depends on every driver on
    the net, not on the pins in one call. Use add_power_symbol for that.

    Args:
        pins: List of {"reference": "R1", "pin": "1"} dicts
        label_text: Net name (e.g. "GND", "VCC"), exactly as it should read: no surrounding
            spaces, no leading "/", no text variable ("${...}"), no bus syntax, not a KiCad
            auto name such as "Net-(R1-1)"
        direction: Stub direction: "auto" (away from the symbol), "left", "right", "up", "down"
        stub_length: Stub length in mm, a multiple of 1.27 up to 1000 (default 2.54)
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    plan = _wire_pins(pins, label_text, direction, stub_length, schematic_path)
    if plan is None:
        return f"Wired 0 pins to '{label_text}'."
    if not plan.wires and not plan.labels:
        return plan.no_change()
    return plan.success()


class _WireRefused(ToolError):
    """wire_pins_to_net's refusal, with the plan kept for a caller that composes it."""

    def __init__(self, plan: _connectivity.WirePlan):
        super().__init__(plan.refusal())
        self.plan = plan


def _wire_pins(
    pins: list[PinRefSpec],
    label_text: str,
    direction: str,
    stub_length: float,
    schematic_path: str,
    param: str = "label_text",
    turns: bool = True,
) -> _connectivity.WirePlan | None:
    """wire_pins_to_net's work: plan the edit, and write it when it adds anything.

    Raises _WireRefused with the file untouched. None for an empty pin list, which reads no
    file. *param* and *turns* fit the remedies to a caller with other parameters
    (_connectivity.plan_wire_pins).
    """
    # Literal publishes the choices; this check is for direct Python callers, which pydantic
    # never sees. It runs before the empty-list return so a bad call fails the same either way.
    try:
        _connectivity.check_args(label_text, direction, stub_length, param=param)
    except _connectivity.Refusal as e:
        plan = _connectivity.WirePlan(str(label_text))
        plan.refuse(e.codes, e.text)
        raise _WireRefused(plan) from None
    if not pins:
        return None
    tree, root, *_ = _open_sch_cst(schematic_path)
    plan = _connectivity.plan_wire_pins(
        root, pins, label_text, direction, stub_length, schematic_path, param=param, turns=turns
    )
    if plan.refused:
        raise _WireRefused(plan)
    if not plan.wires and not plan.labels:
        return plan
    for a, b in plan.wires:
        node = _WIRE_TPL.copy()
        for xy, (x, y) in zip(node.find("pts").find_all("xy"), (a, b)):
            xy.atoms[1].set_text(_connectivity.mm(x))
            xy.atoms[2].set_text(_connectivity.mm(y))
        node.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(root, "wire", node)
    for (x, y), rot in plan.labels:
        node = _LABEL_TPL.copy()
        node.atoms[1].set_text(label_text)
        at = node.find("at")
        at.atoms[1].set_text(_connectivity.mm(x))
        at.atoms[2].set_text(_connectivity.mm(y))
        at.atoms[3].set_text(str(rot))
        node.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(root, "label", node)
    _atomic_write(schematic_path, _cst.serialize(tree))
    return plan


@mcp.tool(annotations=_ADDITIVE)
def connect_pins(
    ref1: str,
    pin1: str,
    ref2: str,
    pin2: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Connect two component pins with Manhattan (L-shaped) wire routing.

    Combines get_pin_positions + coordinate math + add_wires into one call. At its own
    points (both pins and the L's corner) a junction goes where three or more directions
    meet, and any wire through it is split there, as KiCad's wire tool does
    (sch_line_wire_bus_tool.cpp:1263-1267 at 9.0.8); a leg running through a junction or a
    bus entry end already on the sheet is split there too, as KiCad's cleanup splits it. The
    result names each junction and split.

    Args:
        ref1: First component reference (e.g. "U1")
        pin1: First pin name or number
        ref2: Second component reference (e.g. "C3")
        pin2: Second pin name or number
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
    m = _model(root)
    legs = [(_req_point(s[0], s[1]), _req_point(s[2], s[3])) for s in segments]
    # A junction or a bus entry end already on a leg is a point the leg is split at too: KiCad
    # 9's cleanup splits every wire through a junction and through both ends of every bus entry
    # (SCHEMATIC::BreakSegmentsOnJunctions, schematic.cpp:1090-1113 at 9.0.8), which kicad-cli
    # 9 turns into a cut when the wire is left whole.
    cuts = [
        q
        for it in m.items
        if it.kind in ("junction", "be")
        for q in _connectivity._conn_points(it)
        if any(_connectivity._within(q[0], q[1], a[0], a[1], b[0], b[1], 0) for a, b in legs)
    ]
    joined = _join_points(root, m, [_req_point(px, py) for px, py in new_points] + cuts)

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
        _fill_at(node, x1, y1, 0, SCH_IU_PER_MM)
        node.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(root, "label", node)

    _atomic_write(schematic_path, _cst.serialize(tree))

    n = len(segments)
    lines = [f"Connected {ref1}:{pin1} -> {ref2}:{pin2} via {n} wire segment{'s' if n > 1 else ''}"]
    return "\n".join(lines + _join_report(joined))


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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    px, py, _ = _get_pin_pos_cst(root, reference, pin_name)
    px, py = _snap(px, SCH_IU_PER_MM), _snap(py, SCH_IU_PER_MM)

    tol = 0.1
    for nc in root.find_all("no_connect"):
        nx, ny = _node_xy(nc)
        if abs(nx - px) < tol and abs(ny - py) < tol:
            return f"No-connect already present on {reference}:{pin_name} at ({px}, {py})"

    node = _NC_TPL.copy()
    _fill_at(node, px, py, iu=SCH_IU_PER_MM)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_sch_node(root, "no_connect", node)
    _atomic_write(schematic_path, _cst.serialize(tree))

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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
    _atomic_write(schematic_path, _cst.serialize(tree))
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
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        output_dir: Directory for ERC report file. Optional; omit to use the configured default.
        project_path: Path to .kicad_pro file for explicit root resolution
    """
    _require_kicad_path(schematic_path, "schematic")
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
        with open(out_path, encoding="utf-8") as f:
            report = json.load(f)
    except FileNotFoundError:
        raise ToolError("ERC failed to produce output")

    pins = _parse_unconnected_pins(report, sheet_filter=sheet_filter)
    note = "ERC ran from root schematic to include full hierarchy context" if root_path else None
    return UnconnectedPinsResult(unconnected_count=len(pins), pins=pins, note=note)


def _sym_lib_table_note(violations: list) -> str | None:
    """Turn KiCad's opaque library warning into something actionable, or None.

    "The current configuration does not include the symbol library 'Device'"
    names neither the file that is missing nor where it belongs, so a model
    reading it can only tell the user to "update your sym-lib-table". KiCad
    copies a stock table into its user config on first run; when that copy did
    not happen, the warning fires on every ERC of every project forever.

    Measured 2026-08-12 on a Windows install: fp-lib-table had been copied and
    sym-lib-table had not, so symbols were unresolvable while footprints were
    fine. Every symbol this server places is embedded in the schematic, which
    is why placement still works and only ERC complains.
    """
    if not any(v.get("type") == "lib_symbol_issues" for v in violations):
        return None
    # Both layouts, the same pair _resolve_system_lib uses: a Unix prefix and a
    # Windows install put data under share/kicad, the macOS .app bundle under
    # SharedSupport. Named only when it is really there, because pointing a user
    # at a path that does not exist is worse than saying nothing about it, and
    # some packaging layouts match neither.
    root = _kicad_root()
    template = next(
        (
            candidate
            for sub in ("share/kicad/template", "SharedSupport/template")
            if root and (candidate := root / sub / "sym-lib-table").is_file()
        ),
        None,
    )
    where = f" KiCad ships one at {template}, to be copied there." if template else ""
    return (
        "The library warnings mean KiCad's user configuration has no"
        f" sym-lib-table; opening KiCad once normally creates it.{where}"
        " Placement is unaffected: symbols are embedded in the schematic."
    )


@mcp.tool(annotations=_EXPORT)
def run_erc(
    schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR, project_path: str = ""
) -> ErcResult:
    """Run Electrical Rules Check (ERC) on a schematic.

    Auto-redirects to root schematic for sub-sheets to avoid false
    positives from missing hierarchical context.

    Returns JSON report with violations.

    Args:
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        output_dir: Directory for report file (default: same as schematic).
            Optional; omit to use the configured default.
        project_path: Path to .kicad_pro file for explicit root resolution
    """
    _require_kicad_path(schematic_path, "schematic")
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
        with open(out_path, encoding="utf-8") as f:
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

    root_note = "ERC ran from root schematic to include full hierarchy context" if root_path else ""
    note = " ".join(filter(None, [root_note, _sym_lib_table_note(all_violations)])) or None
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
    format: Literal["pdf", "svg", "dxf"] = "pdf",
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
) -> SchematicExportResult:
    """Export schematic to PDF, SVG, or DXF format.

    pdf and dxf produce one file, so path names it and size_bytes is filled.
    svg produces one file per sheet, so path names the directory. files and
    count are filled either way.

    Args:
        format: Output format - "pdf", "svg", or "dxf"
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        output_dir: Directory for output files. Optional; omit to use the configured default.
    """
    _require_kicad_path(schematic_path, "schematic")
    # See export_pcb: the enum is for the model, this is for direct callers.
    fmt = format.lower()
    if fmt not in ("pdf", "svg", "dxf"):
        raise ToolError(f"Unknown format: {format}. Use: pdf, svg, dxf")

    out_dir = output_dir or str(Path(schematic_path).parent)
    stem = Path(schematic_path).stem

    if fmt == "svg":
        _ensure_dir(out_dir)
        _run_cli(["sch", "export", "svg", "--output", out_dir, schematic_path])
        svgs = sorted(Path(out_dir).glob("*.svg"))
        return SchematicExportResult(
            path=out_dir,
            format="svg",
            files=[f.name for f in svgs],
            count=len(svgs),
        )

    out_path = str(Path(out_dir) / f"{stem}.{fmt}")
    _run_cli(["sch", "export", fmt, "--output", out_path, schematic_path])
    meta = _file_meta(out_path)
    return SchematicExportResult(
        path=meta["path"],
        format=fmt,
        files=[Path(meta["path"]).name],
        count=1,
        size_bytes=meta["size_bytes"],
    )


@mcp.tool(annotations=_EXPORT, title="Export a netlist for one schematic")
def export_netlist(
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
    format: Literal[
        "kicadxml", "kicadsexpr", "cadstar", "orcadpcb2", "spice", "spicemodel", "pads", "allegro"
    ] = "kicadxml",
) -> ExportResult:
    """Export a schematic netlist.

    Args:
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        output_dir: Output directory. Optional; omit to use the configured default.
        format: Netlist format. spice and spicemodel produce simulator input.
    """
    _require_kicad_path(schematic_path, "schematic")
    out_dir = output_dir or str(Path(schematic_path).parent)
    # kicad-cli writes whatever --output names, so the extension is ours to
    # pick. Measured against kicad-cli 9.0.8, which accepts all eight.
    ext = {"kicadxml": ".xml", "spice": ".cir", "spicemodel": ".lib"}.get(format, ".net")
    out_path = str(Path(out_dir) / (Path(schematic_path).stem + ext))
    _run_cli(["sch", "export", "netlist", "--format", format, "--output", out_path, schematic_path])
    meta = _file_meta(out_path)
    return ExportResult(path=meta["path"], size_bytes=meta["size_bytes"], format=format)


@mcp.tool(annotations=_EXPORT)
def export_bom(schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR) -> BomExportResult:
    """Export Bill of Materials (BOM) as CSV.

    Args:
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        output_dir: Output directory. Optional; omit to use the configured default.
    """
    _require_kicad_path(schematic_path, "schematic")
    out_dir = output_dir or str(Path(schematic_path).parent)
    out_path = str(Path(out_dir) / (Path(schematic_path).stem + "-bom.csv"))
    _run_cli(["sch", "export", "bom", "--output", out_path, schematic_path])
    meta = _file_meta(out_path)
    with open(out_path, encoding="utf-8") as f:
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
