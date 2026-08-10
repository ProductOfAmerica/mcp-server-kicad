"""KiCad project scaffolding tools.

Tools for creating KiCad project files, schematics, symbol libraries,
sym-lib-tables, hierarchical sheets, jobset execution, and version info.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp.exceptions import ToolError

import mcp_server_kicad._cst as _cst
from mcp_server_kicad._cst import _fill_at, _node_text, _num, _numish
from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    SCH_PATH,
    _find_root_schematic,
    _gen_uuid,
    _node_uuid,
    _resolve_hierarchy_path,
    _resolve_root,
    _run_cli,
    _sheet_file_cst,
    _sheet_name_cst,
    _snap_grid,
    _sym_property_cst,
    _upsert_root_symbol_instance,
    build_server,
)
from mcp_server_kicad.models import (
    CrossSheetNetsResult,
    HierarchicalNetlistResult,
    HierarchyResult,
    HierarchyValidationResult,
    NetTraceResult,
    RootSchematicResult,
    SheetInfoResult,
    SymbolInstancesResult,
    VersionResult,
)
from mcp_server_kicad.schematic import (
    _HLABEL_TPL,
    _LABEL_TPL,
    _open_sch_cst,
    _splice_sch_node,
    _splice_wire,
)
from mcp_server_kicad.symbol import _SYM_LIB_TPL

mcp = build_server(
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


def _find_sheet_cst(root, sheet_uuid: str):
    """Return the hierarchical sheet with the given UUID, or raise ToolError."""
    for sheet in root.find_all("sheet"):
        if _node_uuid(sheet) == sheet_uuid:
            return sheet
    raise ToolError(f"Sheet with UUID '{sheet_uuid}' not found")


_SHEET_TPL = _cst.parse(
    b"(sheet\n\t(at 0 0)\n\t(size 25.4 10.16)\n\t(fields_autoplaced yes)\n\t(stroke\n\t\t(widt"
    b'h 0.1)\n\t\t(type default)\n\t)\n\t(fill\n\t\t(color 0 0 0 0.0000)\n\t)\n\t(uuid "x")\n'
    b'\t(property "Sheetname" "X"\n\t\t(at 0 0 0)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.'
    b'27 1.27)\n\t\t\t)\n\t\t)\n\t)\n\t(property "Sheetfile" "x.kicad_sch"\n\t\t(at 0 0 0)\n\t'
    b"\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t)\n\t)\n\t(instances\n\t"
    b'\t(project "X"\n\t\t\t(path "/x"\n\t\t\t\t(page "2")\n\t\t\t)\n\t\t)\n\t)\n)'
).lists[0]

_SHEET_PIN_TPL = _cst.parse(
    b'(pin "X" input\n\t(at 0 0 180)\n\t(effects\n\t\t(font\n\t\t\t(size 1.27 1.27)\n\t\t)\n\t'
    b')\n\t(uuid "x")\n)'
).lists[0]

_SYM_INSTANCES_TPL = _cst.parse(
    b'(instances\n\t(project "X"\n\t\t(path "/x"\n\t\t\t(reference "R")\n\t\t\t(unit 1)\n\t\t)'
    b"\n\t)\n)"
).lists[0]

# Native empty KiCad 9 schematic for create_schematic; uuid filled per call.
_EMPTY_SCH_TPL = (
    b"(kicad_sch\n"
    b"\t(version 20250114)\n"
    b'\t(generator "eeschema")\n'
    b'\t(generator_version "9.0")\n'
    b'\t(uuid "x")\n'
    b'\t(paper "A4")\n'
    b"\t(lib_symbols)\n"
    b"\t(sheet_instances\n"
    b'\t\t(path "/"\n'
    b'\t\t\t(page "1")\n'
    b"\t\t)\n"
    b"\t)\n"
    b"\t(embedded_fonts no)\n"
    b")\n"
)


@mcp.tool(annotations=_ADDITIVE)
def create_project(directory: str, name: str) -> str:
    """Create a KiCad 9 project (.kicad_pro + .kicad_prl + .kicad_sch).

    Args:
        directory: Directory to create the project in (created if missing)
        name: Project name (used for filenames)
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)

    pro_path = d / f"{name}.kicad_pro"
    if pro_path.exists():
        raise ToolError(f"{pro_path} already exists.")

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
        create_schematic(str(sch_path))

    return f"Created project at {pro_path} (including root schematic)"


@mcp.tool(annotations=_ADDITIVE)
def create_schematic(schematic_path: str) -> str:
    """Create a valid empty KiCad 9 schematic file.

    Args:
        schematic_path: Path for the new .kicad_sch file
    """
    p = Path(schematic_path)
    if p.exists():
        raise ToolError(f"{p} already exists.")

    p.parent.mkdir(parents=True, exist_ok=True)

    tree = _cst.parse(_EMPTY_SCH_TPL)
    tree.lists[0].find("uuid").atoms[1].set_text(_gen_uuid())
    p.write_bytes(_cst.serialize(tree))
    return f"Created schematic at {p}"


@mcp.tool(annotations=_ADDITIVE)
def create_symbol_library(symbol_lib_path: str) -> str:
    """Create a valid empty KiCad 9 symbol library.

    Args:
        symbol_lib_path: Path for the new .kicad_sym file
    """
    p = Path(symbol_lib_path)
    if p.exists():
        raise ToolError(f"{p} already exists.")

    p.parent.mkdir(parents=True, exist_ok=True)

    p.write_bytes(_SYM_LIB_TPL)
    return f"Created symbol library at {p}"


@mcp.tool(annotations=_ADDITIVE)
def create_sym_lib_table(directory: str, entries: list[dict]) -> str:
    """Create a sym-lib-table file in the given directory.

    Each entry dict needs 'name' and 'uri' keys.
    Overwrites existing sym-lib-table if present.

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


@mcp.tool(annotations=_ADDITIVE)
def add_hierarchical_sheet(
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
    child_path = Path(sheet_file)
    if not child_path.exists():
        raise ToolError(f"{child_path} does not exist. Create it with create_schematic first.")

    parent_tree, parent_root, *_ = _open_sch_cst(parent_schematic_path)
    x, y = _snap_grid(x), _snap_grid(y)

    # Sheet dimensions: fixed width, height scales with pin count
    sheet_width = 25.4
    pin_spacing = 2.54
    sheet_height = max(10.16, (len(pins) + 1) * pin_spacing)

    # Build sheet block
    sheet = _SHEET_TPL.copy()
    _fill_at(sheet, x, y)
    size = sheet.find("size")
    size.atoms[1].set_text(_num(sheet_width))
    size.atoms[2].set_text(_num(sheet_height))
    sheet_uuid = _gen_uuid()
    sheet.find("uuid").atoms[1].set_text(sheet_uuid)
    sheet_props = {pr.atoms[1].text: pr for pr in sheet.find_all("property")}
    name_prop = sheet_props["Sheetname"]
    name_prop.atoms[2].set_text(sheet_name)
    _fill_at(name_prop, x, round(y - 1.27, 4))
    file_prop = sheet_props["Sheetfile"]
    file_prop.atoms[2].set_text(child_path.name)
    _fill_at(file_prop, x, round(y + sheet_height + 1.27, 4))

    # Pins on the sheet block (left edge) plus stubs and labels in the parent
    inst_anchor = sheet.find("instances")
    for i, pin_def in enumerate(pins):
        pin_y = _snap_grid(y + (i + 1) * pin_spacing)
        pin = _SHEET_PIN_TPL.copy()
        pin.atoms[1].set_text(pin_def["name"])
        pin.atoms[2].set_text(pin_def["direction"])
        _fill_at(pin, x, pin_y, 180)
        pin.find("uuid").atoms[1].set_text(_gen_uuid())
        sheet.insert_before(inst_anchor, pin)

        # Wire stub going LEFT from pin, with a net label at its end
        stub_end_x = _snap_grid(x - 2.54)
        _splice_wire(parent_root, x, pin_y, stub_end_x, pin_y)
        label = _LABEL_TPL.copy()
        label.atoms[1].set_text(pin_def["name"])
        _fill_at(label, stub_end_x, pin_y, 180)
        label.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(parent_root, "label", label)

    # Instances block for the sheet
    project_name = Path(project_path).stem if project_path else Path(parent_schematic_path).stem
    page = str(len(parent_root.find_all("sheet")) + 2)
    inst_project = inst_anchor.find("project")
    inst_project.atoms[1].set_text(project_name)
    ipath = inst_project.find("path")
    ipath.atoms[1].set_text(f"/{_node_uuid(parent_root)}/{sheet_uuid}")
    ipath.find("page").atoms[1].set_text(page)

    _splice_sch_node(parent_root, "sheet", sheet)
    Path(parent_schematic_path).write_bytes(_cst.serialize(parent_tree))

    # Add matching hierarchical labels to child schematic
    child_tree, child_root, *_ = _open_sch_cst(sheet_file)
    label_x = _snap_grid(25.4)
    for i, pin_def in enumerate(pins):
        label_y = _snap_grid(25.4 + i * 5.08)
        hl = _HLABEL_TPL.copy()
        hl.atoms[1].set_text(pin_def["name"])
        hl.find("shape").atoms[1].set_text(pin_def["direction"])
        _fill_at(hl, label_x, label_y, 180)
        hl.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(child_root, "hierarchical_label", hl)

        # Wire stub going RIGHT from label, with a net label at its end
        stub_end_x = _snap_grid(label_x + 2.54)
        _splice_wire(child_root, label_x, label_y, stub_end_x, label_y)
        lab = _LABEL_TPL.copy()
        lab.atoms[1].set_text(pin_def["name"])
        _fill_at(lab, stub_end_x, label_y, 0)
        lab.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(child_root, "label", lab)

    # Add parent project instances to child symbols
    if project_path:
        root_sch_path = Path(project_path).with_suffix(".kicad_sch")
        if root_sch_path.exists():
            hierarchy_root = _cst.parse(root_sch_path.read_bytes()).lists[0]
            parent_project_name = Path(project_path).stem
            parent_sheet_path = f"/{_node_uuid(hierarchy_root)}/{sheet_uuid}"
            for sym in child_root.find_all("symbol"):
                instances = sym.find("instances")
                has_parent = instances is not None and any(
                    pj.atoms[1].text == parent_project_name for pj in instances.find_all("project")
                )
                if not has_parent:
                    ref = _sym_property_cst(sym, "Reference") or "?"
                    entry = _SYM_INSTANCES_TPL.copy()
                    pj = entry.find("project")
                    pj.atoms[1].set_text(parent_project_name)
                    pp = pj.find("path")
                    pp.atoms[1].set_text(parent_sheet_path)
                    pp.find("reference").atoms[1].set_text(ref)
                    if instances is None:
                        sym.append_child(entry, b"\n\t")
                    else:
                        projects = instances.find_all("project")
                        if projects:
                            instances.insert_after(projects[-1], pj)
                        else:
                            instances.append_child(pj, b"\n\t\t")
                ref = _sym_property_cst(sym, "Reference") or "?"
                val = _sym_property_cst(sym, "Value") or ""
                fp = _sym_property_cst(sym, "Footprint") or ""
                _upsert_root_symbol_instance(
                    str(child_path),
                    project_path,
                    _node_uuid(sym),
                    ref,
                    value=val,
                    footprint=fp,
                )

    Path(sheet_file).write_bytes(_cst.serialize(child_tree))

    return f"Added sheet '{sheet_name}' with {len(pins)} pins to {parent_schematic_path}"


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_hierarchical_sheet(
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
    if not name and not uuid:
        raise ToolError("Provide at least one of 'name' or 'uuid'.")

    tree, root, *_ = _open_sch_cst(parent_schematic_path)
    sheets = root.find_all("sheet")

    def _normalize_uuid(u: str) -> str:
        return u.replace("-", "").lower()

    def _sheet_name(s) -> str:
        return _sym_property_cst(s, "Sheetname") or ""

    # Find matching sheets
    matches: list[int] = []
    for i, sheet in enumerate(sheets):
        s_uuid = _node_uuid(sheet)
        if uuid:
            if s_uuid and _normalize_uuid(s_uuid) == _normalize_uuid(uuid):
                if name and _sheet_name(sheet) != name:
                    raise ToolError(
                        f"Sheet with uuid={uuid} found but its name is "
                        f"'{_sheet_name(sheet)}', not '{name}'."
                    )
                matches.append(i)
                break
        else:
            if _sheet_name(sheet) == name:
                matches.append(i)

    if not matches:
        criteria = f"uuid={uuid}" if uuid else f"name='{name}'"
        raise ToolError(f"No hierarchical sheet found matching {criteria}.")

    if len(matches) > 1:

        def _xy(s):
            s_at = s.find("at")
            return float(s_at.atoms[1].text), float(s_at.atoms[2].text)

        info = ", ".join(
            f"uuid={_node_uuid(sheets[i])} at ({_xy(sheets[i])[0]}, {_xy(sheets[i])[1]})"
            for i in matches
        )
        raise ToolError(
            f"Multiple sheets named '{name}' found: [{info}]. Provide uuid to disambiguate."
        )

    target = sheets[matches[0]]
    sheet_name = _sheet_name(target)
    sheet_uuid = _node_uuid(target)
    child_filename = _sheet_file_cst(target) or ""
    msg = f"Removed hierarchical sheet '{sheet_name}' (uuid={sheet_uuid})."

    # Handle child file deletion
    if delete_child_file:
        parent_dir = Path(parent_schematic_path).parent
        child_path = parent_dir / child_filename
        # Check if any OTHER sheet still references this child file
        other_refs = any(
            _sheet_file_cst(s) == child_filename for j, s in enumerate(sheets) if j != matches[0]
        )
        if other_refs:
            msg += f" Kept child file '{child_filename}' — still referenced by another sheet block."
        elif child_path.exists():
            child_path.unlink()
            msg += f" Deleted child file '{child_filename}'."

    root.remove_child(target)
    Path(parent_schematic_path).write_bytes(_cst.serialize(tree))
    return msg


@mcp.tool(annotations=_DESTRUCTIVE)
def modify_hierarchical_sheet(
    sheet_uuid: str,
    schematic_path: str = SCH_PATH,
    sheet_name: str = "",
    file_name: str = "",
    width: float | None = None,
    height: float | None = None,
) -> str:
    """Modify properties of an existing hierarchical sheet block.

    Args:
        sheet_uuid: UUID of the sheet to modify (from list_schematic_sheets)
        schematic_path: Path to parent .kicad_sch
        sheet_name: New display name (empty = keep)
        file_name: New file path (empty = keep)
        width: New width in mm (None = keep)
        height: New height in mm (None = keep)
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sheet_cst(root, sheet_uuid)
    props = {pr.atoms[1].text: pr for pr in target.find_all("property")}
    size = target.find("size")
    changes = []
    if sheet_name:
        props["Sheetname"].atoms[2].set_text(sheet_name)
        changes.append(f"name='{sheet_name}'")
    if file_name:
        props["Sheetfile"].atoms[2].set_text(file_name)
        changes.append(f"file='{file_name}'")
    if width is not None:
        size.atoms[1].set_text(_num(width))
        changes.append(f"width={width}")
    if height is not None:
        size.atoms[2].set_text(_num(height))
        changes.append(f"height={height}")
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Modified sheet: {', '.join(changes)}"


@mcp.tool(annotations=_ADDITIVE)
def add_sheet_pin(
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
    _valid_types = {"input", "output", "bidirectional", "tri_state", "passive"}
    if connection_type not in _valid_types:
        raise ToolError(
            f"Invalid connection_type '{connection_type}'. Use: {', '.join(sorted(_valid_types))}"
        )
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sheet_cst(root, sheet_uuid)
    at = target.find("at")
    size = target.find("size")
    sx, sy = float(at.atoms[1].text), float(at.atoms[2].text)
    # Calculate pin position on sheet edge
    existing_pins = target.find_all("pin")
    pin_y = sy + 2.54 * (len(existing_pins) + 1)
    pin_x = sx + float(size.atoms[1].text) if side == "right" else sx
    pin = _SHEET_PIN_TPL.copy()
    pin.atoms[1].set_text(pin_name)
    pin.atoms[2].set_text(connection_type)
    _fill_at(pin, pin_x, pin_y, 180 if side == "left" else 0)
    pin.find("uuid").atoms[1].set_text(_gen_uuid())
    if existing_pins:
        target.insert_after(existing_pins[-1], pin)
    else:
        props = target.find_all("property")
        target.insert_after(props[-1], pin)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Added sheet pin '{pin_name}' ({connection_type}) to sheet"


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_sheet_pin(
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
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sheet_cst(root, sheet_uuid)
    pin = next((p for p in target.find_all("pin") if p.atoms[1].text == pin_name), None)
    if pin is None:
        raise ToolError(f"Pin '{pin_name}' not found on sheet")
    target.remove_child(pin)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Removed pin '{pin_name}' from sheet"


@mcp.tool(annotations=_ADDITIVE)
def annotate_schematic(schematic_path: str = SCH_PATH, project_path: str = "") -> str:
    """Auto-assign reference designators to unannotated components.

    Finds components with '?' in their reference (e.g. R?, U?) and assigns
    sequential numbers, respecting existing references in the schematic
    and across the hierarchy when project_path is provided.

    Args:
        schematic_path: Path to .kicad_sch file
        project_path: Path to .kicad_pro file (scans hierarchy for existing refs)
    """
    import re

    tree, root, *_ = _open_sch_cst(schematic_path)

    def _collect_refs_cst(r) -> set[str]:
        refs: set[str] = set()
        for s in r.find_all("symbol"):
            ref = _sym_property_cst(s, "Reference")
            if ref and "?" not in ref:
                refs.add(ref)
        return refs

    # Collect existing refs across hierarchy
    existing_refs: set[str] = set()

    if project_path:
        root_path = _resolve_root(schematic_path, project_path)
        root_file = root_path or schematic_path
        root_dir = Path(root_file).parent
        hierarchy_root = _cst.parse(Path(root_file).read_bytes()).lists[0]
        existing_refs.update(_collect_refs_cst(hierarchy_root))
        for sheet in hierarchy_root.find_all("sheet"):
            child_path = root_dir / (_sheet_file_cst(sheet) or "")
            if child_path.exists() and str(child_path.resolve()) != str(
                Path(schematic_path).resolve()
            ):
                child_root = _cst.parse(child_path.read_bytes()).lists[0]
                existing_refs.update(_collect_refs_cst(child_root))

    # Also collect refs from target schematic
    existing_refs.update(_collect_refs_cst(root))

    # Find unannotated components and group by prefix
    unannotated = []  # (symbol node, prefix)
    ref_re = re.compile(r"^(#?[A-Z]+)\?$")
    for sym in root.find_all("symbol"):
        ref = _sym_property_cst(sym, "Reference")
        if ref and "?" in ref:
            m = ref_re.match(ref)
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
        ref_prop = next(pr for pr in sym.find_all("property") if pr.atoms[1].text == "Reference")
        ref_prop.atoms[2].set_text(new_ref)
        # Update instance paths if present
        instances = sym.find("instances")
        if instances is not None and instances.find("project") is not None:
            for project in instances.find_all("project"):
                for path_node in project.find_all("path"):
                    ref_node = path_node.find("reference")
                    if ref_node is not None:
                        ref_node.atoms[1].set_text(new_ref)
        else:
            # Create instances block if missing (symbols placed without it)
            if project_path:
                proj_name, sheet_path = _resolve_hierarchy_path(
                    project_path, schematic_path, _node_uuid(root)
                )
            else:
                proj_name = Path(schematic_path).stem if schematic_path else ""
                sheet_path = f"/{_node_uuid(root)}"
            if instances is not None:
                sym.remove_child(instances)
            node = _SYM_INSTANCES_TPL.copy()
            project = node.find("project")
            project.atoms[1].set_text(proj_name)
            ipath = project.find("path")
            ipath.atoms[1].set_text(sheet_path)
            ipath.find("reference").atoms[1].set_text(new_ref)
            sym.append_child(node, b"\n\t")
        assigned.setdefault(prefix, []).append(new_ref)

    Path(schematic_path).write_bytes(_cst.serialize(tree))

    # Sync root symbolInstances for all annotated symbols
    for sym, _prefix in unannotated:
        ref = _sym_property_cst(sym, "Reference") or "?"
        val = _sym_property_cst(sym, "Value") or ""
        fp = _sym_property_cst(sym, "Footprint") or ""
        _upsert_root_symbol_instance(
            schematic_path,
            project_path,
            _node_uuid(sym),
            ref,
            value=val,
            footprint=fp,
        )

    parts = []
    for prefix in sorted(assigned):
        refs = assigned[prefix]
        parts.append(f"{refs[0]}-{refs[-1]}" if len(refs) > 1 else refs[0])
    total = sum(len(v) for v in assigned.values())
    return f"Annotated {total} components: {', '.join(parts)}"


@mcp.tool(annotations=_READ_ONLY)
def validate_hierarchy(schematic_path: str = SCH_PATH) -> HierarchyValidationResult:
    """Validate hierarchical schematic for common issues.

    Checks for orphaned labels/pins, direction mismatches, duplicate
    reference designators, unannotated components, and missing files.

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    sch_dir = Path(schematic_path).parent
    issues: list[dict] = []
    all_refs: dict[str, list[str]] = {}  # ref -> [sheet_names]

    def _scan_refs(node_root, sheet_label: str) -> None:
        for sym in node_root.find_all("symbol"):
            ref = _sym_property_cst(sym, "Reference")
            if ref is None:
                continue
            if "?" in ref:
                issues.append({"type": "unannotated_ref", "sheet": sheet_label, "reference": ref})
            else:
                all_refs.setdefault(ref, []).append(sheet_label)

    # Check root schematic refs
    _scan_refs(root, Path(schematic_path).name)

    for sheet in root.find_all("sheet"):
        sheet_name = _sheet_name_cst(sheet) or ""
        file_name = _sheet_file_cst(sheet) or ""
        child_path = sch_dir / file_name
        if not child_path.exists():
            issues.append(
                {
                    "type": "missing_file",
                    "sheet_name": sheet_name,
                    "file_name": file_name,
                }
            )
            continue

        child_root = _cst.parse(child_path.read_bytes()).lists[0]
        pin_names = {p.atoms[1].text: p.atoms[2].text for p in sheet.find_all("pin")}
        label_names: dict[str, str] = {}
        for hl in child_root.find_all("hierarchical_label"):
            shape = hl.find("shape")
            # kiutils defaulted a shapeless label to "input"; KiCad always writes one.
            label_names[_node_text(hl)] = shape.atoms[1].text if shape is not None else "input"

        # Orphaned labels (in child, no matching pin)
        for label_name, label_shape in label_names.items():
            if label_name not in pin_names:
                issues.append(
                    {
                        "type": "orphaned_label",
                        "sheet_name": sheet_name,
                        "label": label_name,
                    }
                )
            elif pin_names[label_name] != label_shape:
                issues.append(
                    {
                        "type": "direction_mismatch",
                        "sheet_name": sheet_name,
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
                        "sheet_name": sheet_name,
                        "pin": pin_name,
                    }
                )

        # Check child refs
        _scan_refs(child_root, file_name)

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
    return HierarchyValidationResult(
        status=status,
        issue_count=len(issues),
        issues=issues,
    )


@mcp.tool(annotations=_READ_ONLY)
def is_root_schematic(schematic_path: str = SCH_PATH) -> RootSchematicResult:
    """Check if a schematic is the root or a sub-sheet.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    root = _find_root_schematic(schematic_path)
    return RootSchematicResult(
        is_root=root is None,
        root_path=root,
    )


@mcp.tool(annotations=_READ_ONLY)
def list_hierarchy(schematic_path: str = SCH_PATH) -> HierarchyResult:
    """List the full sheet hierarchy starting from a root schematic.

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    sch_dir = Path(schematic_path).parent
    root_name = Path(schematic_path).name

    sheets = []
    for sheet in root.find_all("sheet"):
        file_name = _sheet_file_cst(sheet) or ""
        child_path = sch_dir / file_name
        at = sheet.find("at")
        child_info: dict = {
            "sheet_name": _sheet_name_cst(sheet) or "",
            "file_name": file_name,
            "uuid": _node_uuid(sheet),
            "pin_count": len(sheet.find_all("pin")),
            "x": _numish(at.atoms[1].text),
            "y": _numish(at.atoms[2].text),
        }
        if child_path.exists():
            child_root = _cst.parse(child_path.read_bytes()).lists[0]
            child_info["component_count"] = len(child_root.find_all("symbol"))
            child_info["label_count"] = len(child_root.find_all("label"))
            child_info["hierarchical_label_count"] = len(child_root.find_all("hierarchical_label"))
            child_info["sub_sheets"] = [
                {
                    "sheet_name": _sheet_name_cst(sub) or "",
                    "file_name": _sheet_file_cst(sub) or "",
                    "uuid": _node_uuid(sub),
                }
                for sub in child_root.find_all("sheet")
            ]
        else:
            child_info["error"] = f"File not found: {child_path}"
        sheets.append(child_info)

    return HierarchyResult(
        root=root_name,
        component_count=len(root.find_all("symbol")),
        sheet_count=len(sheets),
        sheets=sheets,
    )


@mcp.tool(annotations=_READ_ONLY)
def get_sheet_info(sheet_uuid: str, schematic_path: str = SCH_PATH) -> SheetInfoResult:
    """Get detailed info about a hierarchical sheet including pin/label matching.

    Args:
        sheet_uuid: UUID of the sheet
        schematic_path: Path to parent .kicad_sch
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sheet_cst(root, sheet_uuid)

    file_name = _sheet_file_cst(target) or ""
    child_path = Path(schematic_path).parent / file_name

    # Load child to check label matching
    child_labels: set[str] = set()
    child_info: dict = {}
    if child_path.exists():
        child_root = _cst.parse(child_path.read_bytes()).lists[0]
        child_labels = {_node_text(hl) for hl in child_root.find_all("hierarchical_label")}
        child_info = {
            "component_count": len(child_root.find_all("symbol")),
            "label_count": len(child_root.find_all("label")),
            "hierarchical_label_count": len(child_root.find_all("hierarchical_label")),
        }

    pins = []
    for pin in target.find_all("pin"):
        pin_at = pin.find("at")
        pins.append(
            {
                "name": pin.atoms[1].text,
                "connection_type": pin.atoms[2].text,
                "x": _numish(pin_at.atoms[1].text),
                "y": _numish(pin_at.atoms[2].text),
                "matched": pin.atoms[1].text in child_labels,
            }
        )

    at = target.find("at")
    size = target.find("size")
    return SheetInfoResult(
        sheet_name=_sheet_name_cst(target) or "",
        file_name=file_name,
        uuid=_node_uuid(target),
        x=float(at.atoms[1].text),
        y=float(at.atoms[2].text),
        width=float(size.atoms[1].text),
        height=float(size.atoms[2].text),
        pins=pins,
        **child_info,
    )


@mcp.tool(annotations=_READ_ONLY)
def trace_hierarchical_net(net_name: str, schematic_path: str = SCH_PATH) -> NetTraceResult:
    """Trace a net across the hierarchy, following hierarchical pins and labels.

    Args:
        net_name: Net/label name to trace
        schematic_path: Path to root .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    sch_dir = Path(schematic_path).parent
    root_name = Path(schematic_path).name

    sheets_touched: list[str] = []
    connections: list[dict] = []

    def _count(node_root, token: str) -> int:
        return sum(1 for n in node_root.find_all(token) if _node_text(n) == net_name)

    # Check root schematic for labels matching net_name
    if _count(root, "label") or _count(root, "global_label"):
        sheets_touched.append(root_name)

    # Check sheet pins for matching name
    for sheet in root.find_all("sheet"):
        pin_match = any(p.atoms[1].text == net_name for p in sheet.find_all("pin"))
        if pin_match:
            sheet_name = _sheet_name_cst(sheet) or ""
            file_name = _sheet_file_cst(sheet) or ""
            if root_name not in sheets_touched:
                sheets_touched.append(root_name)
            connections.append(
                {
                    "type": "sheet_pin",
                    "sheet_name": sheet_name,
                    "file_name": file_name,
                }
            )
            # Look inside child
            child_path = sch_dir / file_name
            if child_path.exists():
                child_root = _cst.parse(child_path.read_bytes()).lists[0]
                hlabel_count = _count(child_root, "hierarchical_label")
                if hlabel_count:
                    sheets_touched.append(file_name)
                    connections.append(
                        {
                            "type": "hierarchical_label",
                            "sheet_name": sheet_name,
                            "file_name": file_name,
                            "label_count": hlabel_count,
                        }
                    )
                # Check for component connections in child
                label_count = _count(child_root, "label")
                if label_count:
                    connections.append(
                        {
                            "type": "local_label",
                            "file_name": file_name,
                            "count": label_count,
                        }
                    )

    # Also check global labels in all sheets
    for sheet in root.find_all("sheet"):
        file_name = _sheet_file_cst(sheet) or ""
        child_path = sch_dir / file_name
        if child_path.exists():
            child_root = _cst.parse(child_path.read_bytes()).lists[0]
            glabel_count = _count(child_root, "global_label")
            if glabel_count:
                if file_name not in sheets_touched:
                    sheets_touched.append(file_name)
                connections.append(
                    {
                        "type": "global_label",
                        "file_name": file_name,
                        "count": glabel_count,
                    }
                )

    return NetTraceResult(
        net_name=net_name,
        sheets_touched=sheets_touched,
        connection_count=len(connections),
        connections=connections,
    )


@mcp.tool(annotations=_READ_ONLY)
def list_cross_sheet_nets(schematic_path: str = SCH_PATH) -> CrossSheetNetsResult:
    """List all nets that cross sheet boundaries (hierarchical pins and global labels).

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    sch_dir = Path(schematic_path).parent

    hierarchical_nets: list[dict] = []
    global_nets: dict[str, list[str]] = {}  # name -> [sheet files]

    for sheet in root.find_all("sheet"):
        sheet_name = _sheet_name_cst(sheet) or ""
        file_name = _sheet_file_cst(sheet) or ""
        child_path = sch_dir / file_name
        child_root = _cst.parse(child_path.read_bytes()).lists[0] if child_path.exists() else None
        hlabels = (
            {_node_text(hl) for hl in child_root.find_all("hierarchical_label")}
            if child_root is not None
            else set()
        )

        for pin in sheet.find_all("pin"):
            hierarchical_nets.append(
                {
                    "name": pin.atoms[1].text,
                    "direction": pin.atoms[2].text,
                    "sheet_name": sheet_name,
                    "file_name": file_name,
                    "label_matched": pin.atoms[1].text in hlabels,
                }
            )

        # Collect global labels
        if child_root is not None:
            for gl in child_root.find_all("global_label"):
                global_nets.setdefault(_node_text(gl), []).append(file_name)

    # Also check root for global labels
    for gl in root.find_all("global_label"):
        global_nets.setdefault(_node_text(gl), []).append(Path(schematic_path).name)

    global_net_list = [
        {"name": name, "sheets": sheets} for name, sheets in sorted(global_nets.items())
    ]

    return CrossSheetNetsResult(
        hierarchical_nets=hierarchical_nets,
        global_nets=global_net_list,
    )


@mcp.tool(annotations=_READ_ONLY)
def get_symbol_instances(schematic_path: str = SCH_PATH) -> SymbolInstancesResult:
    """List all symbol instances from a root schematic's symbolInstances table.

    Args:
        schematic_path: Path to root .kicad_sch file
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    instances = []
    # KiCad 8+ carries instances per placed symbol instead, so the whole
    # section is often absent: that is an empty list, not an error.
    si = root.find("symbol_instances")
    for entry in si.find_all("path") if si is not None else []:
        fields = {c.head: c.atoms[1].text for c in entry.lists if len(c.atoms) > 1}
        instances.append(
            {
                "path": entry.atoms[1].text,
                "reference": fields.get("reference", ""),
                "unit": int(fields.get("unit", 1)),
                "value": fields.get("value", ""),
                "footprint": fields.get("footprint", ""),
            }
        )
    return SymbolInstancesResult(instances=instances, count=len(instances))


@mcp.tool(annotations=_DESTRUCTIVE)
def move_hierarchical_sheet(
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
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sheet_cst(root, sheet_uuid)
    at = target.find("at")
    dx = new_x - float(at.atoms[1].text)
    dy = new_y - float(at.atoms[2].text)
    at.atoms[1].set_text(_num(new_x))
    at.atoms[2].set_text(_num(new_y))

    def _shift(node) -> None:
        n_at = node.find("at")
        if n_at is None:
            return
        n_at.atoms[1].set_text(_num(round(float(n_at.atoms[1].text) + dx, 4)))
        n_at.atoms[2].set_text(_num(round(float(n_at.atoms[2].text) + dy, 4)))

    # Move pins and property positions by the same delta
    for pin in target.find_all("pin"):
        _shift(pin)
    for prop in target.find_all("property"):
        _shift(prop)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Moved sheet to ({new_x}, {new_y})"


@mcp.tool(annotations=_DESTRUCTIVE)
def reorder_sheet_pages(
    page_order: list[str],
    schematic_path: str = SCH_PATH,
) -> str:
    """Reorder hierarchical sheets by specifying the desired UUID order.

    Args:
        page_order: List of sheet UUIDs in desired order
        schematic_path: Path to root .kicad_sch file
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    sheets = root.find_all("sheet")
    sheet_map = {_node_uuid(s): s for s in sheets}
    missing = [u for u in page_order if u not in sheet_map]
    if missing:
        raise ToolError(f"Sheet UUIDs not found: {missing}")
    new_order = [sheet_map[u] for u in page_order]
    new_order += [s for s in sheets if _node_uuid(s) not in page_order]
    # Swap nodes in place, keeping each slot's leading whitespace where it was.
    slots = [i for i, c in enumerate(root.children) if c.kind == "list" and c.head == "sheet"]
    slot_seps = [root.children[i].sep for i in slots]
    for slot, sep, node in zip(slots, slot_seps, new_order):
        node.sep = sep
        root.children[slot] = node
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Reordered {len(page_order)} sheets"


@mcp.tool(annotations=_ADDITIVE)
def duplicate_sheet(
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
    import shutil
    import uuid as _uuid_mod

    tree, root, *_ = _open_sch_cst(schematic_path)
    sch_dir = Path(schematic_path).parent

    source = _find_sheet_cst(root, sheet_uuid)
    src_at = source.find("at")
    src_x, src_y = float(src_at.atoms[1].text), float(src_at.atoms[2].text)
    src_size = source.find("size")
    src_w = float(src_size.atoms[1].text)
    src_h = float(src_size.atoms[2].text)
    source_file = _sheet_file_cst(source) or ""

    # Determine new file name
    if not new_file_name:
        base = Path(source_file).stem
        new_file_name = f"{base}_{new_sheet_name.replace(' ', '_').lower()}.kicad_sch"

    # Copy the child file
    src_path = sch_dir / source_file
    dst_path = sch_dir / new_file_name
    if not src_path.exists():
        raise ToolError(f"Source file not found: {src_path}")

    shutil.copy2(str(src_path), str(dst_path))

    # Regenerate UUIDs in the copy: the root uuid plus each top-level item's
    # own uuid; nested uuids (e.g. symbol pin uuids) keep kiutils parity.
    copy_tree = _cst.parse(dst_path.read_bytes())
    copy_root = copy_tree.lists[0]
    root_uuid_node = copy_root.find("uuid")
    if root_uuid_node is not None:
        root_uuid_node.atoms[1].set_text(str(_uuid_mod.uuid4()))
    uuid_tokens = {
        "symbol",
        "label",
        "global_label",
        "hierarchical_label",
        "wire",
        "bus",
        "bus_entry",
        "polyline",
        "junction",
        "no_connect",
    }
    for item in copy_root.lists:
        if item.head in uuid_tokens:
            iu = item.find("uuid")
            if iu is not None:
                iu.atoms[1].set_text(str(_uuid_mod.uuid4()))
    dst_path.write_bytes(_cst.serialize(copy_tree))

    # Create new sheet block in parent (geometry from source, fresh identity)
    dx = src_w + 5
    new_sheet = _SHEET_TPL.copy()
    new_uuid = _gen_uuid()
    new_sheet.find("uuid").atoms[1].set_text(new_uuid)
    _fill_at(new_sheet, round(src_x + dx, 4), src_y)
    new_size = new_sheet.find("size")
    new_size.atoms[1].set_text(_num(src_w))
    new_size.atoms[2].set_text(_num(src_h))
    new_props = {pr.atoms[1].text: pr for pr in new_sheet.find_all("property")}
    name_prop = new_props["Sheetname"]
    name_prop.atoms[2].set_text(new_sheet_name)
    _fill_at(name_prop, round(src_x + dx, 4), round(src_y - 1.27, 4))
    file_prop = new_props["Sheetfile"]
    file_prop.atoms[2].set_text(new_file_name)
    _fill_at(file_prop, round(src_x + dx, 4), round(src_y + src_h + 1.27, 4))

    # Copy pins with offset and fresh uuids
    inst_anchor = new_sheet.find("instances")
    for pin in source.find_all("pin"):
        new_pin = pin.copy()
        pin_at = new_pin.find("at")
        pin_at.atoms[1].set_text(_num(round(float(pin_at.atoms[1].text) + dx, 4)))
        pu = new_pin.find("uuid")
        if pu is not None:
            pu.atoms[1].set_text(_gen_uuid())
        new_sheet.insert_before(inst_anchor, new_pin)

    # Instances block
    project_name = Path(project_path).stem if project_path else Path(schematic_path).stem
    page = str(len(root.find_all("sheet")) + 2)
    pj = inst_anchor.find("project")
    pj.atoms[1].set_text(project_name)
    pp = pj.find("path")
    pp.atoms[1].set_text(f"/{_node_uuid(root)}/{new_uuid}")
    pp.find("page").atoms[1].set_text(page)

    _splice_sch_node(root, "sheet", new_sheet)
    Path(schematic_path).write_bytes(_cst.serialize(tree))
    return f"Duplicated sheet as '{new_sheet_name}' -> {new_file_name}"


@mcp.tool(annotations=_ADDITIVE)
def flatten_hierarchy(
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
    import uuid as _uuid_mod

    flat_tree, flat_root, *_ = _open_sch_cst(schematic_path)
    sch_dir = Path(schematic_path).parent

    if not output_path:
        stem = Path(schematic_path).stem
        output_path = str(sch_dir / f"{stem}_flat.kicad_sch")

    # The output starts as a byte-copy of the root with a fresh uuid
    root_uuid_node = flat_root.find("uuid")
    if root_uuid_node is not None:
        root_uuid_node.atoms[1].set_text(str(_uuid_mod.uuid4()))

    # Remember the child files, then drop hierarchy constructs from the output
    child_files = [_sheet_file_cst(s) or "" for s in flat_root.find_all("sheet")]
    for token in ("sheet", "hierarchical_label", "symbol_instances", "sheet_instances"):
        for node in flat_root.find_all(token):
            flat_root.remove_child(node)

    def _offset_at(node, ox: float, oy: float) -> None:
        n_at = node.find("at")
        if n_at is None:
            return
        n_at.atoms[1].set_text(_num(float(n_at.atoms[1].text) + ox))
        n_at.atoms[2].set_text(_num(float(n_at.atoms[2].text) + oy))

    # Find the max Y extent of root content for offset
    max_y = 0.0
    for sym in flat_root.find_all("symbol"):
        s_at = sym.find("at")
        if s_at is not None:
            max_y = max(max_y, float(s_at.atoms[2].text))
    for token in ("wire", "bus", "polyline"):
        for gi in flat_root.find_all(token):
            pts = gi.find("pts")
            if pts is not None:
                for xy in pts.find_all("xy"):
                    max_y = max(max_y, float(xy.atoms[2].text))

    y_offset = max_y + 50  # Start child content 50mm below root content

    lib_symbols = flat_root.find("lib_symbols")
    existing_lib_names = set()
    if lib_symbols is not None:
        for ls in lib_symbols.find_all("symbol"):
            raw = ls.atoms[1].text
            existing_lib_names.add(raw.split(":")[-1] if ":" in raw else raw)

    sheet_index = 0
    for child_file in child_files:
        child_path = sch_dir / child_file
        if not child_path.exists():
            continue

        child_root = _cst.parse(child_path.read_bytes()).lists[0]
        x_offset = sheet_index * 200  # Space sheets horizontally

        # Merge lib symbols (avoid duplicates, entryName semantics)
        child_libs = child_root.find("lib_symbols")
        if child_libs is not None and lib_symbols is not None:
            for ls in child_libs.find_all("symbol"):
                raw = ls.atoms[1].text
                bare = raw.split(":")[-1] if ":" in raw else raw
                if bare not in existing_lib_names:
                    entries = lib_symbols.find_all("symbol")
                    node = ls.copy()
                    if entries:
                        lib_symbols.insert_after(entries[-1], node)
                    else:
                        lib_symbols.append_child(node, b"\n\t\t")
                    existing_lib_names.add(bare)

        # Merge components with offset
        for sym in child_root.find_all("symbol"):
            new_sym = sym.copy()
            su = new_sym.find("uuid")
            if su is not None:
                su.atoms[1].set_text(str(_uuid_mod.uuid4()))
            _offset_at(new_sym, x_offset, y_offset)
            for prop in new_sym.find_all("property"):
                _offset_at(prop, x_offset, y_offset)
            _splice_sch_node(flat_root, "symbol", new_sym)

        # Merge wires/graphical items with offset
        for token in ("wire", "bus", "polyline", "bus_entry"):
            for gi in child_root.find_all(token):
                new_gi = gi.copy()
                gu = new_gi.find("uuid")
                if gu is not None:
                    gu.atoms[1].set_text(str(_uuid_mod.uuid4()))
                pts = new_gi.find("pts")
                if pts is not None:
                    for xy in pts.find_all("xy"):
                        xy.atoms[1].set_text(_num(float(xy.atoms[1].text) + x_offset))
                        xy.atoms[2].set_text(_num(float(xy.atoms[2].text) + y_offset))
                _splice_sch_node(flat_root, token, new_gi)

        # Merge labels, junctions, and no-connects with offset
        for token in ("label", "global_label", "junction", "no_connect"):
            for item in child_root.find_all(token):
                new_item = item.copy()
                iu = new_item.find("uuid")
                if iu is not None:
                    iu.atoms[1].set_text(str(_uuid_mod.uuid4()))
                _offset_at(new_item, x_offset, y_offset)
                _splice_sch_node(flat_root, token, new_item)

        sheet_index += 1

    Path(output_path).write_bytes(_cst.serialize(flat_tree))

    total_components = len(flat_root.find_all("symbol"))
    return f"Flattened hierarchy to {Path(output_path).name}: {total_components} components"


@mcp.tool(annotations=_EXPORT)
def export_hierarchical_netlist(
    schematic_path: str = SCH_PATH,
    output_dir: str = "",
) -> HierarchicalNetlistResult:
    """Export a netlist from the root schematic, including hierarchy info.

    Runs kicad-cli to generate a netlist and returns parsed component/net data
    with sheet path information for each component.

    Args:
        schematic_path: Path to root .kicad_sch file
        output_dir: Directory for netlist output (defaults to schematic directory)
    """
    import xml.etree.ElementTree as ET

    if not output_dir:
        output_dir = str(Path(schematic_path).parent)

    output_path = str(Path(output_dir) / (Path(schematic_path).stem + ".net"))

    _run_cli(
        [
            "sch",
            "export",
            "netlist",
            "--output",
            output_path,
            # Without this kicad-cli writes an S-expression netlist, which the
            # ET.parse below can never read.
            "--format",
            "kicadxml",
            schematic_path,
        ]
    )

    # Parse the netlist XML
    if not Path(output_path).exists():
        raise ToolError("Netlist file not generated")

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

        return HierarchicalNetlistResult(
            output_path=output_path,
            component_count=len(components),
            net_count=len(nets),
            components=components,
            nets=nets,
        )
    except ET.ParseError as e:
        raise ToolError(f"Failed to parse netlist at {output_path}: {e}") from e


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
        raise ToolError(f"Jobset failed: {e}") from e


@mcp.tool(annotations=_READ_ONLY)
def get_version() -> VersionResult:
    """Get KiCad version information including build details and library versions."""
    try:
        result = _run_cli(["version", "--format", "about"], check=False)
    except (RuntimeError, FileNotFoundError) as e:
        raise ToolError(str(e)) from e
    if result.returncode != 0:
        raise ToolError(result.stderr.strip() or f"kicad-cli exited {result.returncode}")
    return VersionResult(version_info=result.stdout.strip())


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for mcp-server-kicad-project console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
