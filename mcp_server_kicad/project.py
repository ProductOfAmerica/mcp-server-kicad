"""KiCad project scaffolding tools.

Tools for creating KiCad project files, schematics, symbol libraries,
sym-lib-tables, and hierarchical sheets from scratch. Registered on the
schematic server via register_tools().
"""

from __future__ import annotations

import json
from pathlib import Path

from kiutils.items.common import ColorRGBA, Effects, Font, Position, Property, Stroke
from kiutils.items.schitems import (
    HierarchicalLabel,
    HierarchicalPin,
    HierarchicalSheet,
    HierarchicalSheetProjectInstance,
    HierarchicalSheetProjectPath,
)
from kiutils.schematic import Schematic
from kiutils.symbol import SymbolLib
from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import _gen_uuid, _load_sch, _snap_grid

# KiCad 9 file format constants
_KICAD_SCH_VERSION = 20250114
_KICAD_SCH_GENERATOR = "eeschema"
_KICAD_SYM_VERSION = "20231120"


def _create_project(directory: str, name: str) -> str:
    """Create a KiCad 9 project (.kicad_pro + .kicad_prl).

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

    return f"Created project at {pro_path}"


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
        position=Position(X=x, Y=y - 1.27),
    )
    sheet.fileName = Property(
        key="Sheetfile",
        value=child_path.name,
        id=1,
        effects=Effects(font=Font(height=1.27, width=1.27)),
        position=Position(X=x, Y=y + sheet_height + 1.27),
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
    sheet.pins = sheet_pins

    # Add instances block for the sheet
    project_name = Path(parent_sch.filePath).stem if parent_sch.filePath else ""
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
    child_sch.to_file()

    return f"Added sheet '{sheet_name}' with {len(pins)} pins to {parent_schematic_path}"


# Public aliases — tests call these directly without going through MCP
create_project = _create_project
create_schematic = _create_schematic
create_symbol_library = _create_symbol_library
create_sym_lib_table = _create_sym_lib_table
add_hierarchical_sheet = _add_hierarchical_sheet


def register_tools(mcp: FastMCP) -> None:
    """Register all project scaffolding tools on the given FastMCP instance."""

    @mcp.tool()
    def create_project(directory: str, name: str) -> str:
        """Create a KiCad 9 project (.kicad_pro + .kicad_prl).

        Args:
            directory: Directory to create the project in (created if missing)
            name: Project name (used for filenames)
        """
        return _create_project(directory, name)

    @mcp.tool()
    def create_schematic(schematic_path: str) -> str:
        """Create a valid empty KiCad 9 schematic file.

        Args:
            schematic_path: Path for the new .kicad_sch file
        """
        return _create_schematic(schematic_path)

    @mcp.tool()
    def create_symbol_library(symbol_lib_path: str) -> str:
        """Create a valid empty KiCad 9 symbol library.

        Args:
            symbol_lib_path: Path for the new .kicad_sym file
        """
        return _create_symbol_library(symbol_lib_path)

    @mcp.tool()
    def create_sym_lib_table(directory: str, entries: list[dict]) -> str:
        """Create a sym-lib-table file in the given directory.

        Each entry dict needs 'name' and 'uri' keys.
        Overwrites existing sym-lib-table if present.

        Args:
            directory: Directory to write sym-lib-table in
            entries: List of dicts with 'name' and 'uri' keys
        """
        return _create_sym_lib_table(directory, entries)

    @mcp.tool()
    def add_hierarchical_sheet(
        parent_schematic_path: str,
        sheet_name: str,
        sheet_file: str,
        pins: list[dict],
        x: float = 25.4,
        y: float = 25.4,
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
        """
        return _add_hierarchical_sheet(parent_schematic_path, sheet_name, sheet_file, pins, x, y)
