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
    SymbolProjectInstance,
    SymbolProjectPath,
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
        "6. remove_hierarchical_sheet — removes a sheet block from parent\n\n"
        "HIERARCHY WORKFLOW:\n"
        "1. Create hierarchy with add_hierarchical_sheet\n"
        "2. Inspect with list_hierarchy, get_sheet_info\n"
        "3. Validate with validate_hierarchy\n"
        "4. Fix label/pin mismatches with add/remove_hierarchical_label"
        " (schematic server), add/remove_sheet_pin\n"
        "5. Trace nets across sheets with trace_hierarchical_net\n"
        "6. Annotate all sheets with annotate_schematic\n"
        "7. Run run_erc from root for final validation"
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
    # Add parent project instances to child symbols
    if project_path:
        root_sch_path = Path(project_path).with_suffix(".kicad_sch")
        if root_sch_path.exists():
            root_sch = _load_sch(str(root_sch_path))
            parent_project_name = Path(project_path).stem
            parent_sheet_path = f"/{root_sch.uuid}/{sheet.uuid}"
            for sym in child_sch.schematicSymbols:
                instances = getattr(sym, "instances", None) or []
                has_parent = any(inst.name == parent_project_name for inst in instances)
                if not has_parent:
                    ref_prop = next((p for p in sym.properties if p.key == "Reference"), None)
                    if not hasattr(sym, "instances") or sym.instances is None:
                        sym.instances = []
                    sym.instances.append(
                        SymbolProjectInstance(
                            name=parent_project_name,
                            paths=[
                                SymbolProjectPath(
                                    sheetInstancePath=parent_sheet_path,
                                    reference=ref_prop.value if ref_prop else "?",
                                    unit=1,
                                )
                            ],
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


def _validate_hierarchy(schematic_path: str) -> str:
    """Validate hierarchical schematic for common issues.

    Checks for orphaned labels/pins, direction mismatches, duplicate
    reference designators, unannotated components, and missing files.

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    sch_dir = Path(schematic_path).parent
    issues: list[dict] = []
    all_refs: dict[str, list[str]] = {}  # ref -> [sheet_names]

    # Check root schematic refs
    for sym in sch.schematicSymbols:
        ref_prop = next((p for p in sym.properties if p.key == "Reference"), None)
        if ref_prop:
            if "?" in ref_prop.value:
                issues.append(
                    {
                        "type": "unannotated_ref",
                        "sheet": Path(schematic_path).name,
                        "reference": ref_prop.value,
                    }
                )
            else:
                all_refs.setdefault(ref_prop.value, []).append(Path(schematic_path).name)

    for sheet in sch.sheets:
        child_path = sch_dir / sheet.fileName.value
        if not child_path.exists():
            issues.append(
                {
                    "type": "missing_file",
                    "sheet_name": sheet.sheetName.value,
                    "file_name": sheet.fileName.value,
                }
            )
            continue

        child_sch = _load_sch(str(child_path))
        pin_names = {p.name: p.connectionType for p in sheet.pins}
        label_names = {hl.text: hl.shape for hl in child_sch.hierarchicalLabels}

        # Orphaned labels (in child, no matching pin)
        for label_name, label_shape in label_names.items():
            if label_name not in pin_names:
                issues.append(
                    {
                        "type": "orphaned_label",
                        "sheet_name": sheet.sheetName.value,
                        "label": label_name,
                    }
                )
            elif pin_names[label_name] != label_shape:
                issues.append(
                    {
                        "type": "direction_mismatch",
                        "sheet_name": sheet.sheetName.value,
                        "pin": label_name,
                        "pin_direction": pin_names[label_name],
                        "label_direction": label_shape,
                    }
                )

        # Orphaned pins (in parent, no matching label)
        for pin_name in pin_names:
            if pin_name not in label_names:
                issues.append(
                    {
                        "type": "orphaned_pin",
                        "sheet_name": sheet.sheetName.value,
                        "pin": pin_name,
                    }
                )

        # Check child refs
        for sym in child_sch.schematicSymbols:
            ref_prop = next((p for p in sym.properties if p.key == "Reference"), None)
            if ref_prop:
                if "?" in ref_prop.value:
                    issues.append(
                        {
                            "type": "unannotated_ref",
                            "sheet": sheet.fileName.value,
                            "reference": ref_prop.value,
                        }
                    )
                else:
                    all_refs.setdefault(ref_prop.value, []).append(sheet.fileName.value)

    # Check for duplicate refs across sheets
    for ref, sheets_list in all_refs.items():
        if ref.startswith("#"):  # Skip power symbols
            continue
        if len(sheets_list) > 1:
            issues.append(
                {
                    "type": "duplicate_ref",
                    "reference": ref,
                    "sheets": sheets_list,
                }
            )

    status = "ok" if not issues else "issues_found"
    return json.dumps(
        {
            "status": status,
            "issue_count": len(issues),
            "issues": issues,
        }
    )


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


def _trace_hierarchical_net(net_name: str, schematic_path: str) -> str:
    """Trace a net across the hierarchy, following hierarchical pins and labels.

    Args:
        net_name: Net/label name to trace
        schematic_path: Path to root .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    sch_dir = Path(schematic_path).parent
    root_name = Path(schematic_path).name

    sheets_touched: list[str] = []
    connections: list[dict] = []

    # Check root schematic for labels matching net_name
    root_labels = [lbl for lbl in sch.labels if lbl.text == net_name]
    root_glabels = [g for g in sch.globalLabels if g.text == net_name]
    if root_labels or root_glabels:
        sheets_touched.append(root_name)

    # Check sheet pins for matching name
    for sheet in sch.sheets:
        pin_match = any(p.name == net_name for p in sheet.pins)
        if pin_match:
            if root_name not in sheets_touched:
                sheets_touched.append(root_name)
            connections.append(
                {
                    "type": "sheet_pin",
                    "sheet_name": sheet.sheetName.value,
                    "file_name": sheet.fileName.value,
                }
            )
            # Look inside child
            child_path = sch_dir / sheet.fileName.value
            if child_path.exists():
                child_sch = _load_sch(str(child_path))
                child_hlabels = [hl for hl in child_sch.hierarchicalLabels if hl.text == net_name]
                if child_hlabels:
                    sheets_touched.append(sheet.fileName.value)
                    connections.append(
                        {
                            "type": "hierarchical_label",
                            "sheet_name": sheet.sheetName.value,
                            "file_name": sheet.fileName.value,
                            "label_count": len(child_hlabels),
                        }
                    )
                # Check for component connections in child
                child_labels = [lbl for lbl in child_sch.labels if lbl.text == net_name]
                if child_labels:
                    connections.append(
                        {
                            "type": "local_label",
                            "file_name": sheet.fileName.value,
                            "count": len(child_labels),
                        }
                    )

    # Also check global labels in all sheets
    for sheet in sch.sheets:
        child_path = sch_dir / sheet.fileName.value
        if child_path.exists():
            child_sch = _load_sch(str(child_path))
            child_glabels = [g for g in child_sch.globalLabels if g.text == net_name]
            if child_glabels:
                if sheet.fileName.value not in sheets_touched:
                    sheets_touched.append(sheet.fileName.value)
                connections.append(
                    {
                        "type": "global_label",
                        "file_name": sheet.fileName.value,
                        "count": len(child_glabels),
                    }
                )

    return json.dumps(
        {
            "net_name": net_name,
            "sheets_touched": sheets_touched,
            "connection_count": len(connections),
            "connections": connections,
        }
    )


def _list_cross_sheet_nets(schematic_path: str) -> str:
    """List all nets that cross sheet boundaries (hierarchical pins and global labels).

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    sch_dir = Path(schematic_path).parent

    hierarchical_nets: list[dict] = []
    global_nets: dict[str, list[str]] = {}  # name -> [sheet files]

    for sheet in sch.sheets:
        for pin in sheet.pins:
            child_path = sch_dir / sheet.fileName.value
            has_label = False
            if child_path.exists():
                child_sch = _load_sch(str(child_path))
                has_label = any(hl.text == pin.name for hl in child_sch.hierarchicalLabels)
            hierarchical_nets.append(
                {
                    "name": pin.name,
                    "direction": pin.connectionType,
                    "sheet_name": sheet.sheetName.value,
                    "file_name": sheet.fileName.value,
                    "label_matched": has_label,
                }
            )

        # Collect global labels
        child_path = sch_dir / sheet.fileName.value
        if child_path.exists():
            child_sch = _load_sch(str(child_path))
            for gl in child_sch.globalLabels:
                global_nets.setdefault(gl.text, []).append(sheet.fileName.value)

    # Also check root for global labels
    for gl in sch.globalLabels:
        global_nets.setdefault(gl.text, []).append(Path(schematic_path).name)

    global_net_list = [
        {"name": name, "sheets": sheets} for name, sheets in sorted(global_nets.items())
    ]

    return json.dumps(
        {
            "hierarchical_nets": hierarchical_nets,
            "global_nets": global_net_list,
        }
    )


def _get_symbol_instances(schematic_path: str) -> str:
    """List all symbol instances from a root schematic's symbolInstances table.

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    instances = []
    for si in getattr(sch, "symbolInstances", []):
        instances.append(
            {
                "path": si.path if hasattr(si, "path") else "",
                "reference": si.reference if hasattr(si, "reference") else "",
                "unit": si.unit if hasattr(si, "unit") else 1,
                "value": si.value if hasattr(si, "value") else "",
                "footprint": si.footprint if hasattr(si, "footprint") else "",
            }
        )
    return json.dumps({"instances": instances, "count": len(instances)})


def _move_hierarchical_sheet(
    sheet_uuid: str, new_x: float, new_y: float, schematic_path: str
) -> str:
    """Move a hierarchical sheet block to a new position, including all pins.

    Args:
        sheet_uuid: UUID of the sheet to move
        new_x: New X position in mm
        new_y: New Y position in mm
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
    dx = new_x - target.position.X
    dy = new_y - target.position.Y
    target.position.X = new_x
    target.position.Y = new_y
    # Move pins by the same delta
    for pin in target.pins:
        pin.position.X = round(pin.position.X + dx, 4)
        pin.position.Y = round(pin.position.Y + dy, 4)
    # Move property positions
    if hasattr(target.sheetName, "position") and target.sheetName.position:
        target.sheetName.position.X = round(target.sheetName.position.X + dx, 4)
        target.sheetName.position.Y = round(target.sheetName.position.Y + dy, 4)
    if hasattr(target.fileName, "position") and target.fileName.position:
        target.fileName.position.X = round(target.fileName.position.X + dx, 4)
        target.fileName.position.Y = round(target.fileName.position.Y + dy, 4)
    _save_sch(sch)
    return f"Moved sheet to ({new_x}, {new_y})"


def _reorder_sheet_pages(page_order: list[str], schematic_path: str) -> str:
    """Reorder hierarchical sheets by specifying the desired UUID order.

    Args:
        page_order: List of sheet UUIDs in desired order
        schematic_path: Path to root .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    # Build uuid->sheet map
    sheet_map = {s.uuid: s for s in sch.sheets}
    missing = [u for u in page_order if u not in sheet_map]
    if missing:
        return f"Sheet UUIDs not found: {missing}"
    # Reorder
    new_sheets = [sheet_map[u] for u in page_order]
    # Add any sheets not in the order (preserve at end)
    for s in sch.sheets:
        if s.uuid not in page_order:
            new_sheets.append(s)
    sch.sheets = new_sheets
    _save_sch(sch)
    return f"Reordered {len(page_order)} sheets"


def _duplicate_sheet(
    sheet_uuid: str,
    new_sheet_name: str,
    schematic_path: str,
    project_path: str = "",
    new_file_name: str = "",
) -> str:
    """Duplicate a hierarchical sheet, copying the child file with new UUIDs.

    Args:
        sheet_uuid: UUID of the sheet to duplicate
        new_sheet_name: Display name for the new sheet
        schematic_path: Path to parent .kicad_sch
        project_path: Path to .kicad_pro (for hierarchy metadata)
        new_file_name: Name for the copied file (auto-generated if empty)
    """
    import shutil
    import uuid as _uuid_mod

    sch = _load_sch(schematic_path)
    sch_dir = Path(schematic_path).parent

    # Find source sheet
    source = None
    for s in sch.sheets:
        if s.uuid == sheet_uuid:
            source = s
            break
    if source is None:
        return f"Sheet with UUID '{sheet_uuid}' not found"

    # Determine new file name
    if not new_file_name:
        base = Path(source.fileName.value).stem
        new_file_name = f"{base}_{new_sheet_name.replace(' ', '_').lower()}.kicad_sch"

    # Copy the child file
    src_path = sch_dir / source.fileName.value
    dst_path = sch_dir / new_file_name
    if not src_path.exists():
        return f"Source file not found: {src_path}"

    shutil.copy2(str(src_path), str(dst_path))

    # Regenerate UUIDs in the copy
    copy_sch = _load_sch(str(dst_path))
    copy_sch.uuid = str(_uuid_mod.uuid4())
    for sym in copy_sch.schematicSymbols:
        sym.uuid = str(_uuid_mod.uuid4())
    for label in copy_sch.labels:
        label.uuid = str(_uuid_mod.uuid4())
    for gl in copy_sch.globalLabels:
        gl.uuid = str(_uuid_mod.uuid4())
    for hl in copy_sch.hierarchicalLabels:
        hl.uuid = str(_uuid_mod.uuid4())
    for gi in copy_sch.graphicalItems:
        if hasattr(gi, "uuid"):
            gi.uuid = str(_uuid_mod.uuid4())
    for j in copy_sch.junctions:
        j.uuid = str(_uuid_mod.uuid4())
    for nc in copy_sch.noConnects:
        nc.uuid = str(_uuid_mod.uuid4())
    _save_sch(copy_sch)

    # Create new sheet block in parent (copy properties from source)
    dx = source.width + 5
    new_sheet = HierarchicalSheet()
    new_sheet.uuid = _gen_uuid()
    new_sheet.position = Position(
        X=source.position.X + dx,
        Y=source.position.Y,
    )
    new_sheet.width = source.width
    new_sheet.height = source.height
    new_sheet.stroke = Stroke(width=0.1, type="default")
    new_sheet.fill = ColorRGBA()
    new_sheet.fieldsAutoplaced = True
    new_sheet.sheetName = Property(
        key="Sheetname",
        value=new_sheet_name,
        id=0,
        effects=Effects(font=Font(height=1.27, width=1.27)),
        position=Position(
            X=source.position.X + dx,
            Y=source.position.Y - 1.27,
            angle=0,
        ),
    )
    new_sheet.fileName = Property(
        key="Sheetfile",
        value=new_file_name,
        id=1,
        effects=Effects(font=Font(height=1.27, width=1.27)),
        position=Position(
            X=source.position.X + dx,
            Y=round(source.position.Y + source.height + 1.27, 4),
            angle=0,
        ),
    )

    # Copy pins with offset
    for pin in source.pins:
        new_pin = HierarchicalPin(
            name=pin.name,
            connectionType=pin.connectionType,
            position=Position(
                X=round(pin.position.X + dx, 4),
                Y=pin.position.Y,
                angle=pin.position.angle if hasattr(pin.position, "angle") else 0,
            ),
            uuid=_gen_uuid(),
        )
        new_pin.effects = Effects(font=Font(height=1.27, width=1.27))
        new_sheet.pins.append(new_pin)

    # Add instances block
    project_name = (
        Path(project_path).stem
        if project_path
        else (Path(sch.filePath).stem if sch.filePath else "")
    )
    new_sheet.instances = [
        HierarchicalSheetProjectInstance(
            name=project_name,
            paths=[
                HierarchicalSheetProjectPath(
                    sheetInstancePath=f"/{sch.uuid}/{new_sheet.uuid}",
                    page=str(len(sch.sheets) + 2),
                ),
            ],
        ),
    ]

    sch.sheets.append(new_sheet)
    _save_sch(sch)
    return f"Duplicated sheet as '{new_sheet_name}' -> {new_file_name}"


def _flatten_hierarchy(
    schematic_path: str,
    output_path: str = "",
) -> str:
    """Flatten a hierarchical schematic into a single sheet.

    Creates a new file — does NOT modify the original hierarchy.

    Args:
        schematic_path: Path to root .kicad_sch file
        output_path: Path for flattened output (defaults to *_flat.kicad_sch)
    """
    import copy
    import uuid as _uuid_mod

    sch = _load_sch(schematic_path)
    sch_dir = Path(schematic_path).parent

    if not output_path:
        stem = Path(schematic_path).stem
        output_path = str(sch_dir / f"{stem}_flat.kicad_sch")

    # Create output schematic as a copy of root
    flat = copy.deepcopy(sch)
    flat.uuid = str(_uuid_mod.uuid4())
    flat.filePath = output_path

    # Find the max Y extent of root content for offset
    max_y = 0.0
    for sym in flat.schematicSymbols:
        if sym.position and sym.position.Y > max_y:
            max_y = sym.position.Y
    for gi in flat.graphicalItems:
        if hasattr(gi, "points"):
            for pt in gi.points:
                if pt.Y > max_y:
                    max_y = pt.Y

    y_offset = max_y + 50  # Start child content 50mm below root content

    sheet_index = 0
    for sheet in sch.sheets:
        child_path = sch_dir / sheet.fileName.value
        if not child_path.exists():
            continue

        child_sch = _load_sch(str(child_path))
        x_offset = sheet_index * 200  # Space sheets horizontally

        # Merge lib symbols (avoid duplicates)
        existing_lib_names = {s.entryName for s in flat.libSymbols}
        for lib_sym in child_sch.libSymbols:
            if lib_sym.entryName not in existing_lib_names:
                flat.libSymbols.append(lib_sym)
                existing_lib_names.add(lib_sym.entryName)

        # Merge components with offset
        for sym in child_sch.schematicSymbols:
            new_sym = copy.deepcopy(sym)
            new_sym.uuid = str(_uuid_mod.uuid4())
            if new_sym.position:
                new_sym.position.X += x_offset
                new_sym.position.Y += y_offset
            # Offset property positions
            for prop in new_sym.properties:
                if prop.position:
                    prop.position.X += x_offset
                    prop.position.Y += y_offset
            flat.schematicSymbols.append(new_sym)

        # Merge wires/graphical items with offset
        for gi in child_sch.graphicalItems:
            new_gi = copy.deepcopy(gi)
            if hasattr(new_gi, "uuid"):
                new_gi.uuid = str(_uuid_mod.uuid4())
            if hasattr(new_gi, "points"):
                for pt in new_gi.points:
                    pt.X += x_offset
                    pt.Y += y_offset
            flat.graphicalItems.append(new_gi)

        # Merge labels with offset
        for label in child_sch.labels:
            new_label = copy.deepcopy(label)
            new_label.uuid = str(_uuid_mod.uuid4())
            if new_label.position:
                new_label.position.X += x_offset
                new_label.position.Y += y_offset
            flat.labels.append(new_label)

        # Merge global labels with offset
        for gl in child_sch.globalLabels:
            new_gl = copy.deepcopy(gl)
            new_gl.uuid = str(_uuid_mod.uuid4())
            if new_gl.position:
                new_gl.position.X += x_offset
                new_gl.position.Y += y_offset
            flat.globalLabels.append(new_gl)

        # Merge junctions with offset
        for j in child_sch.junctions:
            new_j = copy.deepcopy(j)
            new_j.uuid = str(_uuid_mod.uuid4())
            if new_j.position:
                new_j.position.X += x_offset
                new_j.position.Y += y_offset
            flat.junctions.append(new_j)

        # Merge no-connects with offset
        for nc in child_sch.noConnects:
            new_nc = copy.deepcopy(nc)
            new_nc.uuid = str(_uuid_mod.uuid4())
            if new_nc.position:
                new_nc.position.X += x_offset
                new_nc.position.Y += y_offset
            flat.noConnects.append(new_nc)

        sheet_index += 1

    # Remove sheet blocks from flattened output
    flat.sheets = []
    # Remove hierarchical labels (no longer needed without sheets)
    flat.hierarchicalLabels = []
    # Clear symbol instances and sheet instances (no longer valid)
    if hasattr(flat, "symbolInstances"):
        flat.symbolInstances = []
    if hasattr(flat, "sheetInstances"):
        flat.sheetInstances = []

    _save_sch(flat)

    total_components = len(flat.schematicSymbols)
    return f"Flattened hierarchy to {Path(output_path).name}: {total_components} components"


def _export_hierarchical_netlist(
    schematic_path: str,
    output_dir: str = "",
) -> str:
    import xml.etree.ElementTree as ET

    if not output_dir:
        output_dir = str(Path(schematic_path).parent)

    output_path = str(Path(output_dir) / (Path(schematic_path).stem + ".net"))

    try:
        _run_cli(
            [
                "sch",
                "export",
                "netlist",
                "--output",
                output_path,
                schematic_path,
            ]
        )
    except RuntimeError as e:
        return json.dumps({"error": str(e)})

    # Parse the netlist XML
    if not Path(output_path).exists():
        return json.dumps({"error": "Netlist file not generated"})

    try:
        tree = ET.parse(output_path)
        root = tree.getroot()

        components = []
        comp_section = root.find(".//components")
        if comp_section is not None:
            for comp in comp_section.findall("comp"):
                ref = comp.get("ref", "")
                value_el = comp.find("value")
                fp_el = comp.find("footprint")
                sheetpath_el = comp.find("sheetpath")
                components.append(
                    {
                        "reference": ref,
                        "value": value_el.text if value_el is not None else "",
                        "footprint": fp_el.text if fp_el is not None else "",
                        "sheet_path": sheetpath_el.get("names", "/")
                        if sheetpath_el is not None
                        else "/",
                    }
                )

        nets = []
        net_section = root.find(".//nets")
        if net_section is not None:
            for net in net_section.findall("net"):
                net_name = net.get("name", "")
                net_code = net.get("code", "")
                nodes = []
                for node in net.findall("node"):
                    nodes.append(
                        {
                            "ref": node.get("ref", ""),
                            "pin": node.get("pin", ""),
                            "pinfunction": node.get("pinfunction", ""),
                        }
                    )
                nets.append(
                    {
                        "name": net_name,
                        "code": net_code,
                        "node_count": len(nodes),
                        "nodes": nodes,
                    }
                )

        return json.dumps(
            {
                "output_path": output_path,
                "component_count": len(components),
                "net_count": len(nets),
                "components": components,
                "nets": nets,
            }
        )
    except ET.ParseError as e:
        return json.dumps({"output_path": output_path, "parse_error": str(e)})


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
validate_hierarchy = _validate_hierarchy
is_root_schematic = _is_root_schematic
list_hierarchy = _list_hierarchy
get_sheet_info = _get_sheet_info
trace_hierarchical_net = _trace_hierarchical_net
list_cross_sheet_nets = _list_cross_sheet_nets
get_symbol_instances = _get_symbol_instances
move_hierarchical_sheet = _move_hierarchical_sheet
reorder_sheet_pages = _reorder_sheet_pages
duplicate_sheet = _duplicate_sheet
flatten_hierarchy = _flatten_hierarchy
export_hierarchical_netlist = _export_hierarchical_netlist


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
def validate_hierarchy(schematic_path: str = SCH_PATH) -> str:  # noqa: F811
    """Validate hierarchical schematic for common issues.

    Checks for orphaned labels/pins, direction mismatches, duplicate
    reference designators, unannotated components, and missing files.

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    return _validate_hierarchy(schematic_path)


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


@mcp.tool(annotations=_READ_ONLY)
def trace_hierarchical_net(net_name: str, schematic_path: str = SCH_PATH) -> str:  # noqa: F811
    """Trace a net across the hierarchy, following hierarchical pins and labels.

    Args:
        net_name: Net/label name to trace
        schematic_path: Path to root .kicad_sch file
    """
    return _trace_hierarchical_net(net_name, schematic_path)


@mcp.tool(annotations=_READ_ONLY)
def list_cross_sheet_nets(schematic_path: str = SCH_PATH) -> str:  # noqa: F811
    """List all nets that cross sheet boundaries (hierarchical pins and global labels).

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    return _list_cross_sheet_nets(schematic_path)


@mcp.tool(annotations=_READ_ONLY)
def get_symbol_instances(schematic_path: str = SCH_PATH) -> str:  # noqa: F811
    """List all symbol instances from a root schematic's symbolInstances table.

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    return _get_symbol_instances(schematic_path)


@mcp.tool(annotations=_DESTRUCTIVE)
def move_hierarchical_sheet(  # noqa: F811
    sheet_uuid: str,
    new_x: float,
    new_y: float,
    schematic_path: str = SCH_PATH,
) -> str:
    """Move a hierarchical sheet block to a new position, including all pins.

    Args:
        sheet_uuid: UUID of the sheet to move
        new_x: New X position in mm
        new_y: New Y position in mm
        schematic_path: Path to parent .kicad_sch
    """
    return _move_hierarchical_sheet(sheet_uuid, new_x, new_y, schematic_path)


@mcp.tool(annotations=_DESTRUCTIVE)
def reorder_sheet_pages(  # noqa: F811
    page_order: list[str],
    schematic_path: str = SCH_PATH,
) -> str:
    """Reorder hierarchical sheets by specifying the desired UUID order.

    Args:
        page_order: List of sheet UUIDs in desired order
        schematic_path: Path to root .kicad_sch file
    """
    return _reorder_sheet_pages(page_order, schematic_path)


@mcp.tool(annotations=_ADDITIVE)
def duplicate_sheet(  # noqa: F811
    sheet_uuid: str,
    new_sheet_name: str,
    schematic_path: str = SCH_PATH,
    project_path: str = "",
    new_file_name: str = "",
) -> str:
    """Duplicate a hierarchical sheet, copying the child file with new UUIDs.

    Args:
        sheet_uuid: UUID of the sheet to duplicate
        new_sheet_name: Display name for the new sheet
        schematic_path: Path to parent .kicad_sch
        project_path: Path to .kicad_pro (for hierarchy metadata)
        new_file_name: Name for the copied file (auto-generated if empty)
    """
    return _duplicate_sheet(sheet_uuid, new_sheet_name, schematic_path, project_path, new_file_name)


@mcp.tool(annotations=_ADDITIVE)
def flatten_hierarchy(  # noqa: F811
    schematic_path: str = SCH_PATH,
    output_path: str = "",
) -> str:
    """Flatten a hierarchical schematic into a single sheet.

    Merges all child sheet content into one schematic with offset positions.
    Creates a new file — does NOT modify the original hierarchy.

    Args:
        schematic_path: Path to root .kicad_sch file
        output_path: Path for flattened output (defaults to *_flat.kicad_sch)
    """
    return _flatten_hierarchy(schematic_path, output_path)


@mcp.tool(annotations=_EXPORT)
def export_hierarchical_netlist(  # noqa: F811
    schematic_path: str = SCH_PATH,
    output_dir: str = "",
) -> str:
    """Export a netlist from the root schematic, including hierarchy info.

    Runs kicad-cli to generate a netlist and returns parsed component/net data
    with sheet path information for each component.

    Args:
        schematic_path: Path to root .kicad_sch file
        output_dir: Directory for netlist output (defaults to schematic directory)
    """
    return _export_hierarchical_netlist(schematic_path, output_dir)


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
