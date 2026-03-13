"""KiCad project scaffolding tools.

Tools for creating KiCad project files, schematics, symbol libraries,
sym-lib-tables, hierarchical sheets, jobset execution, and version info.
"""

from __future__ import annotations

import json
from pathlib import Path

from kiutils.items.common import ColorRGBA, Effects, Font, Position, Property, Stroke
from kiutils.items.schitems import (
    Connection,
    HierarchicalLabel,
    HierarchicalPin,
    HierarchicalSheet,
    HierarchicalSheetProjectInstance,
    HierarchicalSheetProjectPath,
    LocalLabel,
)
from kiutils.schematic import Schematic
from kiutils.symbol import SymbolLib
from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    _default_effects,
    _default_stroke,
    _gen_uuid,
    _load_sch,
    _run_cli,
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
    sch.to_file()
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
    parent_sch.to_file()

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
    child_sch.to_file()

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
    sch.to_file()
    return msg


# Public aliases — tests call these directly without going through MCP
create_project = _create_project
create_schematic = _create_schematic
create_symbol_library = _create_symbol_library
create_sym_lib_table = _create_sym_lib_table
add_hierarchical_sheet = _add_hierarchical_sheet
remove_hierarchical_sheet = _remove_hierarchical_sheet


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
