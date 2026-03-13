"""KiCad project scaffolding tools.

Tools for creating KiCad project files, schematics, symbol libraries,
sym-lib-tables, hierarchical sheets, jobset execution, and version info.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    SCH_PATH,
    # kiutils types via _shared re-exports
    ColorRGBA,
    Connection,
    Effects,
    Font,
    HierarchicalLabel,
    HierarchicalPin,
    HierarchicalSheet,
    HierarchicalSheetProjectInstance,
    HierarchicalSheetProjectPath,
    LocalLabel,
    Position,
    Property,
    Schematic,
    SchematicSymbol,
    Stroke,
    SymbolLib,
    _default_effects,
    _default_stroke,
    _find_root_schematic,
    _gen_uuid,
    _load_sch,
    _resolve_root,
    _run_cli,
    _save_sch,
    _snap_grid,
)

# KiCad 9 file format constants
_KICAD_SCH_VERSION = 20250114
_KICAD_SCH_GENERATOR = "eeschema"
_KICAD_SYM_VERSION = "20231120"


mcp = FastMCP(
    "kicad-project",
    instructions=(
        "KiCad project scaffolding, hierarchical sheet management,"
        " jobset execution, and version info.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write KiCad files (.kicad_pro, .kicad_prl,"
        " .kicad_sch, .kicad_sym, sym-lib-table) directly. All file creation"
        " and manipulation MUST go through these MCP tools.\n"
        "- NEVER run kicad-cli commands directly. Use run_jobset and"
        " get_version instead.\n"
        "- When a tool returns an error, try different parameters. Do NOT"
        " fall back to manual file editing.\n\n"
        "PROJECT SETUP WORKFLOW:\n"
        "1. create_project — creates .kicad_pro, .kicad_prl, root .kicad_sch\n"
        "2. create_schematic — creates sub-sheet .kicad_sch files\n"
        "3. create_symbol_library + write symbols for custom parts\n"
        "4. create_sym_lib_table — registers libraries with the project\n"
        "5. add_hierarchical_sheet — links sub-sheets to root with pins\n"
        "6. remove_hierarchical_sheet — removes a sheet block from parent"
    ),
)


def _create_project(directory: str, name: str) -> str:
    """Create a KiCad 9 project (.kicad_pro + .kicad_prl + .kicad_sch).

    Args:
        directory: Directory to create the project in (created if missing)
        name: Project name (used for filenames)
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)

    pro_path = d / f"{name}.kicad_pro"
    if pro_path.exists():
        return f"Error: {pro_path} already exists."

    pro_data = {"meta": {"filename": f"{name}.kicad_pro", "version": 1}}
    pro_path.write_text(json.dumps(pro_data, indent=2) + "\n")

    prl_data = {"meta": {"filename": f"{name}.kicad_prl", "version": 3}}
    prl_path = d / f"{name}.kicad_prl"
    prl_path.write_text(json.dumps(prl_data, indent=2) + "\n")

    # Also create the root schematic (matching real KiCad behavior).
    # Guard is defensive — the .kicad_pro check above ensures this is
    # only reached on a fresh project, but a stray .kicad_sch could exist.
    sch_path = d / f"{name}.kicad_sch"
    if not sch_path.exists():
        _create_schematic(str(sch_path))

    return f"Created project at {pro_path} (including root schematic)"


def _create_schematic(schematic_path: str) -> str:
    """Create a valid empty KiCad 9 schematic file.

    Args:
        schematic_path: Path for the new .kicad_sch file
    """
    p = Path(schematic_path)
    if p.exists():
        return f"Error: {p} already exists."

    p.parent.mkdir(parents=True, exist_ok=True)

    sch = Schematic.create_new()
    sch.version = _KICAD_SCH_VERSION
    sch.generator = _KICAD_SCH_GENERATOR
    sch.uuid = _gen_uuid()
    sch.filePath = str(p)
    _save_sch(sch)
    return f"Created schematic at {p}"


def _create_symbol_library(symbol_lib_path: str) -> str:
    """Create a valid empty KiCad 9 symbol library.

    Args:
        symbol_lib_path: Path for the new .kicad_sym file
    """
    p = Path(symbol_lib_path)
    if p.exists():
        return f"Error: {p} already exists."

    p.parent.mkdir(parents=True, exist_ok=True)

    lib = SymbolLib(version=_KICAD_SYM_VERSION, generator="kicad_symbol_editor")
    lib.filePath = str(p)
    lib.to_file()
    return f"Created symbol library at {p}"


def _create_sym_lib_table(directory: str, entries: list[dict]) -> str:
    """Create a sym-lib-table file.

    Args:
        directory: Directory to write sym-lib-table in
        entries: List of dicts with 'name' and 'uri' keys
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)

    lines = ["(sym_lib_table", "  (version 7)"]
    for entry in entries:
        name = entry["name"]
        uri = entry["uri"]
        lines.append(f'  (lib (name "{name}")(type "KiCad")(uri "{uri}")(options "")(descr ""))')
    lines.append(")")

    table_path = d / "sym-lib-table"
    table_path.write_text("\n".join(lines) + "\n")
    return f"Created sym-lib-table with {len(entries)} entries at {table_path}"


def _add_hierarchical_sheet(
    parent_schematic_path: str,
    sheet_name: str,
    sheet_file: str,
    pins: list[dict],
    x: float = 25.4,
    y: float = 25.4,
    project_path: str = "",
) -> str:
    """Add a hierarchical sheet to a parent schematic with matching labels in the child.

    Args:
        parent_schematic_path: Path to parent .kicad_sch
        sheet_name: Display name for the sheet block
        sheet_file: Path to the child .kicad_sch (must exist)
        pins: List of dicts with 'name' and 'direction' keys
              (direction: input, output, bidirectional, tri_state, passive)
        x: X position of sheet block in parent
        y: Y position of sheet block in parent
        project_path: Path to .kicad_pro file (for sub-sheet instance tracking)
    """
    child_path = Path(sheet_file)
    if not child_path.exists():
        return f"Error: {child_path} does not exist. Create it with create_schematic first."

    parent_sch = _load_sch(parent_schematic_path)
    x, y = _snap_grid(x), _snap_grid(y)

    # Sheet dimensions: fixed width, height scales with pin count
    sheet_width = 25.4
    pin_spacing = 2.54
    sheet_height = max(10.16, (len(pins) + 1) * pin_spacing)

    # Build sheet block
    sheet = HierarchicalSheet()
    sheet.position = Position(X=x, Y=y)
    sheet.width = sheet_width
    sheet.height = sheet_height
    sheet.stroke = Stroke(width=0.1, type="default")
    sheet.fill = ColorRGBA()  # default transparent
    sheet.uuid = _gen_uuid()
    sheet.fieldsAutoplaced = True

    # Sheet name and filename — use dedicated fields, not properties list
    sheet.sheetName = Property(
        key="Sheetname",
        value=sheet_name,
        id=0,
        effects=Effects(font=Font(height=1.27, width=1.27)),
        position=Position(X=x, Y=y - 1.27, angle=0),
    )
    sheet.fileName = Property(
        key="Sheetfile",
        value=child_path.name,
        id=1,
        effects=Effects(font=Font(height=1.27, width=1.27)),
        position=Position(X=x, Y=round(y + sheet_height + 1.27, 4), angle=0),
    )

    # Build pins on the sheet block (positioned along left edge)
    sheet_pins = []
    for i, pin_def in enumerate(pins):
        pin = HierarchicalPin()
        pin.name = pin_def["name"]
        pin.connectionType = pin_def["direction"]
        pin.position = Position(
            X=x,
            Y=_snap_grid(y + (i + 1) * pin_spacing),
            angle=180,
        )
        pin.effects = Effects(font=Font(height=1.27, width=1.27))
        pin.uuid = _gen_uuid()
        sheet_pins.append(pin)

        # Wire stub going LEFT from pin
        pin_y = _snap_grid(y + (i + 1) * pin_spacing)
        stub_end_x = _snap_grid(x - 2.54)
        parent_sch.graphicalItems.append(
            Connection(
                type="wire",
                points=[
                    Position(X=x, Y=pin_y),
                    Position(X=stub_end_x, Y=pin_y),
                ],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )

        # Net label at stub endpoint
        parent_sch.labels.append(
            LocalLabel(
                text=pin_def["name"],
                position=Position(X=stub_end_x, Y=pin_y, angle=180),
                effects=_default_effects(),
                uuid=_gen_uuid(),
            )
        )
    sheet.pins = sheet_pins

    # Add instances block for the sheet
    project_name = (
        Path(project_path).stem
        if project_path
        else (Path(parent_sch.filePath).stem if parent_sch.filePath else "")
    )
    sheet.instances = [
        HierarchicalSheetProjectInstance(
            name=project_name,
            paths=[
                HierarchicalSheetProjectPath(
                    sheetInstancePath=f"/{parent_sch.uuid}/{sheet.uuid}",
                    page=str(len(parent_sch.sheets) + 2),
                ),
            ],
        ),
    ]

    parent_sch.sheets.append(sheet)
    _save_sch(parent_sch)

    # Add matching hierarchical labels to child schematic
    child_sch = _load_sch(sheet_file)
    label_x = _snap_grid(25.4)
    for i, pin_def in enumerate(pins):
        label = HierarchicalLabel()
        label.text = pin_def["name"]
        label.shape = pin_def["direction"]
        label.position = Position(
            X=label_x,
            Y=_snap_grid(25.4 + i * 5.08),
            angle=180,
        )
        label.effects = Effects(font=Font(height=1.27, width=1.27))
        label.uuid = _gen_uuid()
        child_sch.hierarchicalLabels.append(label)

        # Wire stub going RIGHT from label
        label_y = _snap_grid(25.4 + i * 5.08)
        stub_end_x = _snap_grid(label_x + 2.54)
        child_sch.graphicalItems.append(
            Connection(
                type="wire",
                points=[
                    Position(X=label_x, Y=label_y),
                    Position(X=stub_end_x, Y=label_y),
                ],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )

        # Net label at stub endpoint
        child_sch.labels.append(
            LocalLabel(
                text=pin_def["name"],
                position=Position(X=stub_end_x, Y=label_y, angle=0),
                effects=_default_effects(),
                uuid=_gen_uuid(),
            )
        )
    _save_sch(child_sch)

    return f"Added sheet '{sheet_name}' with {len(pins)} pins to {parent_schematic_path}"


def _remove_hierarchical_sheet(
    parent_schematic_path: str,
    name: str | None = None,
    uuid: str | None = None,
    delete_child_file: bool = False,
) -> str:
    """Remove a hierarchical sheet block from a parent schematic.

    Args:
        parent_schematic_path: Path to parent .kicad_sch
        name: Sheet name to match (via sheet.sheetName.value)
        uuid: Sheet UUID for unambiguous identification
        delete_child_file: If True, delete the child .kicad_sch file (unless still referenced)
    """
    if not name and not uuid:
        return "Provide at least one of 'name' or 'uuid'."

    sch = _load_sch(parent_schematic_path)

    def _normalize_uuid(u: str) -> str:
        return u.replace("-", "").lower()

    # Find matching sheets
    matches: list[int] = []
    for i, sheet in enumerate(sch.sheets):
        if uuid:
            if sheet.uuid and _normalize_uuid(sheet.uuid) == _normalize_uuid(uuid):
                if name and sheet.sheetName.value != name:
                    return (
                        f"Sheet with uuid={uuid} found but its name is "
                        f"'{sheet.sheetName.value}', not '{name}'."
                    )
                matches.append(i)
                break
        else:
            if sheet.sheetName.value == name:
                matches.append(i)

    if not matches:
        criteria = f"uuid={uuid}" if uuid else f"name='{name}'"
        return f"No hierarchical sheet found matching {criteria}."

    if len(matches) > 1:
        info = ", ".join(
            f"uuid={sch.sheets[i].uuid} at ({sch.sheets[i].position.X}, {sch.sheets[i].position.Y})"
            for i in matches
        )
        return f"Multiple sheets named '{name}' found: [{info}]. Provide uuid to disambiguate."

    target = sch.sheets[matches[0]]
    sheet_name = target.sheetName.value
    sheet_uuid = target.uuid
    child_filename = target.fileName.value
    msg = f"Removed hierarchical sheet '{sheet_name}' (uuid={sheet_uuid})."

    # Handle child file deletion
    if delete_child_file:
        parent_dir = Path(parent_schematic_path).parent
        child_path = parent_dir / child_filename
        # Check if any OTHER sheet still references this child file
        other_refs = any(
            s.fileName.value == child_filename for j, s in enumerate(sch.sheets) if j != matches[0]
        )
        if other_refs:
            msg += f" Kept child file '{child_filename}' — still referenced by another sheet block."
        elif child_path.exists():
            child_path.unlink()
            msg += f" Deleted child file '{child_filename}'."

    sch.sheets.pop(matches[0])
    _save_sch(sch)
    return msg


def _modify_hierarchical_sheet(
    sheet_uuid: str,
    schematic_path: str,
    sheet_name: str = "",
    file_name: str = "",
    width: float | None = None,
    height: float | None = None,
) -> str:
    """Modify properties of an existing hierarchical sheet block.

    Args:
        sheet_uuid: UUID of the sheet to modify (from list_schematic_items sheets)
        schematic_path: Path to parent .kicad_sch
        sheet_name: New display name (empty = keep)
        file_name: New file path (empty = keep)
        width: New width in mm (None = keep)
        height: New height in mm (None = keep)
    """
    sch = _load_sch(schematic_path)
    target = None
    for s in sch.sheets:
        if s.uuid == sheet_uuid:
            target = s
            break
    if target is None:
        return f"Sheet with UUID '{sheet_uuid}' not found"
    changes = []
    if sheet_name:
        target.sheetName.value = sheet_name
        changes.append(f"name='{sheet_name}'")
    if file_name:
        target.fileName.value = file_name
        changes.append(f"file='{file_name}'")
    if width is not None:
        target.width = width
        changes.append(f"width={width}")
    if height is not None:
        target.height = height
        changes.append(f"height={height}")
    _save_sch(sch)
    return f"Modified sheet: {', '.join(changes)}"


def _add_sheet_pin(
    sheet_uuid: str,
    pin_name: str,
    connection_type: str,
    schematic_path: str,
    side: str = "left",
) -> str:
    """Add a pin to an existing hierarchical sheet block.

    Args:
        sheet_uuid: UUID of the sheet
        pin_name: Pin name (should match a hierarchical label in the child schematic)
        connection_type: input, output, bidirectional, tri_state, passive
        schematic_path: Path to parent .kicad_sch
        side: Which sheet edge to place pin on (left or right)
    """
    _valid_types = {"input", "output", "bidirectional", "tri_state", "passive"}
    if connection_type not in _valid_types:
        return (
            f"Error: invalid connection_type '{connection_type}'. "
            f"Use: {', '.join(sorted(_valid_types))}"
        )
    sch = _load_sch(schematic_path)
    target = None
    for s in sch.sheets:
        if s.uuid == sheet_uuid:
            target = s
            break
    if target is None:
        return f"Sheet with UUID '{sheet_uuid}' not found"
    # Calculate pin position on sheet edge
    existing_pins_on_side = len(target.pins)
    pin_y = target.position.Y + 2.54 * (existing_pins_on_side + 1)
    if side == "right":
        pin_x = target.position.X + target.width
    else:
        pin_x = target.position.X
    pin = HierarchicalPin(
        name=pin_name,
        connectionType=connection_type,
        position=Position(X=pin_x, Y=pin_y, angle=180 if side == "left" else 0),
        uuid=_gen_uuid(),
    )
    target.pins.append(pin)
    _save_sch(sch)
    return f"Added sheet pin '{pin_name}' ({connection_type}) to sheet"


def _remove_sheet_pin(sheet_uuid: str, pin_name: str, schematic_path: str) -> str:
    """Remove a pin from a hierarchical sheet block.

    Args:
        sheet_uuid: UUID of the sheet
        pin_name: Name of the pin to remove
        schematic_path: Path to parent .kicad_sch
    """
    sch = _load_sch(schematic_path)
    target = None
    for s in sch.sheets:
        if s.uuid == sheet_uuid:
            target = s
            break
    if target is None:
        return f"Sheet with UUID '{sheet_uuid}' not found"
    pin = None
    for p in target.pins:
        if p.name == pin_name:
            pin = p
            break
    if pin is None:
        return f"Pin '{pin_name}' not found on sheet"
    target.pins.remove(pin)
    _save_sch(sch)
    return f"Removed pin '{pin_name}' from sheet"


def _collect_refs(sch) -> set[str]:
    """Collect all non-'?' reference designators from a schematic."""
    refs: set[str] = set()
    for sym in sch.schematicSymbols:
        ref_prop = next((p for p in sym.properties if p.key == "Reference"), None)
        if ref_prop and "?" not in ref_prop.value:
            refs.add(ref_prop.value)
    return refs


def _annotate_schematic(schematic_path: str, project_path: str = "") -> str:
    """Auto-assign reference designators to unannotated components.

    Finds components with '?' in their reference (e.g. R?, U?) and assigns
    sequential numbers, respecting existing references in the schematic
    and across the hierarchy when project_path is provided.

    Args:
        schematic_path: Path to .kicad_sch file
        project_path: Path to .kicad_pro file (scans hierarchy for existing refs)
    """
    import re

    sch = _load_sch(schematic_path)

    # Collect existing refs across hierarchy
    existing_refs: set[str] = set()

    if project_path:
        root_path = _resolve_root(schematic_path, project_path)
        root = root_path or schematic_path
        root_dir = Path(root).parent
        root_sch = _load_sch(root)
        existing_refs.update(_collect_refs(root_sch))
        for sheet in root_sch.sheets:
            child_path = root_dir / sheet.fileName.value
            if child_path.exists() and str(child_path.resolve()) != str(
                Path(schematic_path).resolve()
            ):
                child_sch = _load_sch(str(child_path))
                existing_refs.update(_collect_refs(child_sch))

    # Also collect refs from target schematic
    existing_refs.update(_collect_refs(sch))

    # Find unannotated components and group by prefix
    unannotated: list[tuple[SchematicSymbol, str]] = []  # (symbol, prefix)
    ref_re = re.compile(r"^(#?[A-Z]+)\?$")
    for sym in sch.schematicSymbols:
        ref_prop = next((p for p in sym.properties if p.key == "Reference"), None)
        if ref_prop and "?" in ref_prop.value:
            m = ref_re.match(ref_prop.value)
            if m:
                unannotated.append((sym, m.group(1)))

    if not unannotated:
        return "No unannotated components found"

    # For each prefix, find max existing number
    num_re = re.compile(r"^(#?[A-Z]+)(\d+)")
    max_nums: dict[str, int] = {}
    for ref in existing_refs:
        m = num_re.match(ref)
        if m:
            prefix, num = m.group(1), int(m.group(2))
            max_nums[prefix] = max(max_nums.get(prefix, 0), num)

    # Assign sequential numbers
    assigned: dict[str, list[str]] = {}
    for sym, prefix in unannotated:
        next_num = max_nums.get(prefix, 0) + 1
        max_nums[prefix] = next_num
        new_ref = f"{prefix}{next_num}"
        ref_prop = next(p for p in sym.properties if p.key == "Reference")
        ref_prop.value = new_ref
        # Update SymbolProjectInstance if present
        for inst in getattr(sym, "instances", []):
            for path_entry in getattr(inst, "paths", []):
                path_entry.reference = new_ref
        assigned.setdefault(prefix, []).append(new_ref)

    _save_sch(sch)

    parts = []
    for prefix in sorted(assigned):
        refs = assigned[prefix]
        parts.append(f"{refs[0]}-{refs[-1]}" if len(refs) > 1 else refs[0])
    total = sum(len(v) for v in assigned.values())
    return f"Annotated {total} components: {', '.join(parts)}"


def _is_root_schematic(schematic_path: str) -> str:
    """Check if a schematic is the root or a sub-sheet.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    root = _find_root_schematic(schematic_path)
    return json.dumps(
        {
            "is_root": root is None,
            "root_path": root,
        }
    )


def _list_hierarchy(schematic_path: str) -> str:
    """List the full sheet hierarchy starting from a root schematic.

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    sch_dir = Path(schematic_path).parent
    root_name = Path(schematic_path).name

    sheets = []
    for sheet in sch.sheets:
        child_path = sch_dir / sheet.fileName.value
        child_info: dict = {
            "sheet_name": sheet.sheetName.value,
            "file_name": sheet.fileName.value,
            "uuid": sheet.uuid,
            "pin_count": len(sheet.pins),
            "x": sheet.position.X,
            "y": sheet.position.Y,
        }
        if child_path.exists():
            child_sch = _load_sch(str(child_path))
            child_info["component_count"] = len(child_sch.schematicSymbols)
            child_info["label_count"] = len(child_sch.labels)
            child_info["hierarchical_label_count"] = len(child_sch.hierarchicalLabels)
            # Recurse for nested sheets
            child_info["sub_sheets"] = []
            for sub_sheet in child_sch.sheets:
                child_info["sub_sheets"].append(
                    {
                        "sheet_name": sub_sheet.sheetName.value,
                        "file_name": sub_sheet.fileName.value,
                        "uuid": sub_sheet.uuid,
                    }
                )
        else:
            child_info["error"] = f"File not found: {child_path}"
        sheets.append(child_info)

    return json.dumps(
        {
            "root": root_name,
            "component_count": len(sch.schematicSymbols),
            "sheet_count": len(sch.sheets),
            "sheets": sheets,
        }
    )


def _get_sheet_info(sheet_uuid: str, schematic_path: str) -> str:
    """Get detailed info about a hierarchical sheet including pin/label matching.

    Args:
        sheet_uuid: UUID of the sheet
        schematic_path: Path to parent .kicad_sch
    """
    sch = _load_sch(schematic_path)
    target = None
    for s in sch.sheets:
        if s.uuid == sheet_uuid:
            target = s
            break
    if target is None:
        return json.dumps({"error": f"Sheet with UUID '{sheet_uuid}' not found"})

    sch_dir = Path(schematic_path).parent
    child_path = sch_dir / target.fileName.value

    # Load child to check label matching
    child_labels: set[str] = set()
    child_info: dict = {}
    if child_path.exists():
        child_sch = _load_sch(str(child_path))
        child_labels = {hl.text for hl in child_sch.hierarchicalLabels}
        child_info = {
            "component_count": len(child_sch.schematicSymbols),
            "label_count": len(child_sch.labels),
            "hierarchical_label_count": len(child_sch.hierarchicalLabels),
        }

    pins = []
    for pin in target.pins:
        pins.append(
            {
                "name": pin.name,
                "connection_type": pin.connectionType,
                "x": pin.position.X,
                "y": pin.position.Y,
                "matched": pin.name in child_labels,
            }
        )

    result = {
        "sheet_name": target.sheetName.value,
        "file_name": target.fileName.value,
        "uuid": target.uuid,
        "x": target.position.X,
        "y": target.position.Y,
        "width": target.width,
        "height": target.height,
        "pins": pins,
        **child_info,
    }
    return json.dumps(result)


# Public aliases — tests call these directly without going through MCP
create_project = _create_project
create_schematic = _create_schematic
create_symbol_library = _create_symbol_library
create_sym_lib_table = _create_sym_lib_table
add_hierarchical_sheet = _add_hierarchical_sheet
remove_hierarchical_sheet = _remove_hierarchical_sheet
modify_hierarchical_sheet = _modify_hierarchical_sheet
add_sheet_pin = _add_sheet_pin
remove_sheet_pin = _remove_sheet_pin
annotate_schematic = _annotate_schematic
is_root_schematic = _is_root_schematic
list_hierarchy = _list_hierarchy
get_sheet_info = _get_sheet_info


# ── MCP tool wrappers ─────────────────────────────────────────────


@mcp.tool(annotations=_ADDITIVE)
def create_project(directory: str, name: str) -> str:  # noqa: F811
    """Create a KiCad 9 project (.kicad_pro + .kicad_prl + .kicad_sch).

    Args:
        directory: Directory to create the project in (created if missing)
        name: Project name (used for filenames)
    """
    return _create_project(directory, name)


@mcp.tool(annotations=_ADDITIVE)
def create_schematic(schematic_path: str) -> str:  # noqa: F811
    """Create a valid empty KiCad 9 schematic file.

    Args:
        schematic_path: Path for the new .kicad_sch file
    """
    return _create_schematic(schematic_path)


@mcp.tool(annotations=_ADDITIVE)
def create_symbol_library(symbol_lib_path: str) -> str:  # noqa: F811
    """Create a valid empty KiCad 9 symbol library.

    Args:
        symbol_lib_path: Path for the new .kicad_sym file
    """
    return _create_symbol_library(symbol_lib_path)


@mcp.tool(annotations=_ADDITIVE)
def create_sym_lib_table(directory: str, entries: list[dict]) -> str:  # noqa: F811
    """Create a sym-lib-table file in the given directory.

    Each entry dict needs 'name' and 'uri' keys.
    Overwrites existing sym-lib-table if present.

    Args:
        directory: Directory to write sym-lib-table in
        entries: List of dicts with 'name' and 'uri' keys
    """
    return _create_sym_lib_table(directory, entries)


@mcp.tool(annotations=_ADDITIVE)
def add_hierarchical_sheet(  # noqa: F811
    parent_schematic_path: str,
    sheet_name: str,
    sheet_file: str,
    pins: list[dict],
    x: float = 25.4,
    y: float = 25.4,
    project_path: str = "",
) -> str:
    """Add a hierarchical sheet to a parent schematic with matching labels in the child.

    Creates the sheet block in the parent and corresponding hierarchical
    labels in the child schematic. The child schematic must already exist
    (create it with create_schematic first).

    Args:
        parent_schematic_path: Path to parent .kicad_sch
        sheet_name: Display name for the sheet
        sheet_file: Path to child .kicad_sch (must exist)
        pins: List of dicts with 'name' (str) and 'direction' (str) keys.
              Direction: input, output, bidirectional, tri_state, passive.
        x: X position of sheet block (default 25.4)
        y: Y position of sheet block (default 25.4)
        project_path: Path to .kicad_pro file (for sub-sheet instance tracking)
    """
    return _add_hierarchical_sheet(
        parent_schematic_path, sheet_name, sheet_file, pins, x, y, project_path
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_hierarchical_sheet(  # noqa: F811
    parent_schematic_path: str,
    name: str | None = None,
    uuid: str | None = None,
    delete_child_file: bool = False,
) -> str:
    """Remove a hierarchical sheet block from a parent schematic.

    Identify the sheet by name, uuid, or both. If name matches multiple sheets,
    returns an error with UUIDs for disambiguation.

    Args:
        parent_schematic_path: Path to parent .kicad_sch
        name: Sheet name to match
        uuid: Sheet UUID for unambiguous identification
        delete_child_file: If True, delete the child .kicad_sch file
              (unless still referenced by another sheet)
    """
    return _remove_hierarchical_sheet(parent_schematic_path, name, uuid, delete_child_file)


@mcp.tool(annotations=_DESTRUCTIVE)
def modify_hierarchical_sheet(  # noqa: F811
    sheet_uuid: str,
    schematic_path: str = SCH_PATH,
    sheet_name: str = "",
    file_name: str = "",
    width: float | None = None,
    height: float | None = None,
) -> str:
    """Modify properties of an existing hierarchical sheet block.

    Args:
        sheet_uuid: UUID of the sheet to modify (from list_schematic_items sheets)
        schematic_path: Path to parent .kicad_sch
        sheet_name: New display name (empty = keep)
        file_name: New file path (empty = keep)
        width: New width in mm (None = keep)
        height: New height in mm (None = keep)
    """
    return _modify_hierarchical_sheet(
        sheet_uuid, schematic_path, sheet_name, file_name, width, height
    )


@mcp.tool(annotations=_ADDITIVE)
def add_sheet_pin(  # noqa: F811
    sheet_uuid: str,
    pin_name: str,
    connection_type: str,
    schematic_path: str = SCH_PATH,
    side: str = "left",
) -> str:
    """Add a pin to an existing hierarchical sheet block.

    Args:
        sheet_uuid: UUID of the sheet
        pin_name: Pin name (should match a hierarchical label in the child schematic)
        connection_type: input, output, bidirectional, tri_state, passive
        schematic_path: Path to parent .kicad_sch
        side: Which sheet edge to place pin on (left or right)
    """
    return _add_sheet_pin(sheet_uuid, pin_name, connection_type, schematic_path, side)


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_sheet_pin(  # noqa: F811
    sheet_uuid: str,
    pin_name: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Remove a pin from a hierarchical sheet block.

    Args:
        sheet_uuid: UUID of the sheet
        pin_name: Name of the pin to remove
        schematic_path: Path to parent .kicad_sch
    """
    return _remove_sheet_pin(sheet_uuid, pin_name, schematic_path)


@mcp.tool(annotations=_ADDITIVE)
def annotate_schematic(schematic_path: str = SCH_PATH, project_path: str = "") -> str:  # noqa: F811
    """Auto-assign reference designators to unannotated components.

    Finds components with '?' in their reference (e.g. R?, U?) and assigns
    sequential numbers, respecting existing references in the schematic
    and across the hierarchy when project_path is provided.

    Args:
        schematic_path: Path to .kicad_sch file
        project_path: Path to .kicad_pro file (scans hierarchy for existing refs)
    """
    return _annotate_schematic(schematic_path, project_path)


@mcp.tool(annotations=_READ_ONLY)
def is_root_schematic(schematic_path: str = SCH_PATH) -> str:  # noqa: F811
    """Check if a schematic is the root or a sub-sheet.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    return _is_root_schematic(schematic_path)


@mcp.tool(annotations=_READ_ONLY)
def list_hierarchy(schematic_path: str = SCH_PATH) -> str:  # noqa: F811
    """List the full sheet hierarchy starting from a root schematic.

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    return _list_hierarchy(schematic_path)


@mcp.tool(annotations=_READ_ONLY)
def get_sheet_info(sheet_uuid: str, schematic_path: str = SCH_PATH) -> str:  # noqa: F811
    """Get detailed info about a hierarchical sheet including pin/label matching.

    Args:
        sheet_uuid: UUID of the sheet
        schematic_path: Path to parent .kicad_sch
    """
    return _get_sheet_info(sheet_uuid, schematic_path)


@mcp.tool(annotations=_EXPORT)
def run_jobset(jobset_path: str) -> str:
    """Run a KiCad jobset file.

    Args:
        jobset_path: Path to .kicad_jobset file
    """
    try:
        result = _run_cli(["jobset", "run", jobset_path])
        return f"Jobset completed successfully.\n{result.stdout}"
    except (RuntimeError, FileNotFoundError) as e:
        return f"Jobset failed: {e}"


@mcp.tool(annotations=_READ_ONLY)
def get_version() -> str:
    """Get KiCad version information including build details and library versions."""
    try:
        result = _run_cli(["version", "--format", "about"], check=False)
    except FileNotFoundError:
        return json.dumps({"error": "kicad-cli not found on PATH"})
    if result.returncode != 0:
        return json.dumps({"error": result.stderr.strip()})
    return json.dumps({"version_info": result.stdout.strip()})


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for mcp-server-kicad-project console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
