"""KiCad project scaffolding tools.

Tools for creating KiCad project files, schematics, symbol libraries,
sym-lib-tables, hierarchical sheets, jobset execution, and version info.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

import mcp_server_kicad._connectivity as _connectivity
import mcp_server_kicad._cst as _cst
from mcp_server_kicad._cst import SCH_IU_PER_MM, _fill_at, _node_text, _num, _numish, _snap
from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    SCH_PATH,
    _atomic_write,
    _ensure_dir,
    _gen_uuid,
    _node_uuid,
    _read_kicad_bytes,
    _require_kicad_path,
    _resolve_hierarchy_path,
    _resolve_root,
    _run_cli,
    _sheet_file_cst,
    _sheet_name_cst,
    _snap_grid,
    _sym_property_cst,
    _upsert_root_symbol_instances,
    build_server,
)
from mcp_server_kicad.models import (
    CrossSheetNetsResult,
    HierarchicalNetlistResult,
    HierarchyResult,
    HierarchyValidationResult,
    LibTableEntry,
    NetTraceResult,
    RootSchematicResult,
    SheetInfoResult,
    SheetPinSpec,
    SymbolInstancesResult,
    VersionResult,
)
from mcp_server_kicad.schematic import (
    _HLABEL_TPL,
    _LABEL_TPL,
    _cuts_inside_lines,
    _off_pin_items,
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
    raise ToolError(
        f"Sheet with UUID '{sheet_uuid}' not found."
        " Use list_schematic_sheets to see the sheets and their UUIDs."
    )


def _sheet_file_nonempty(path: Path) -> bool:
    """Whether a file a Sheetfile names is one this module will read.

    A Sheetfile is document content. is_file() alone turns away a directory and a device
    such as /dev/zero, but a procfs pseudo-file like /proc/self/pagemap is a regular file
    of size 0 that streams without end, so the size has to be checked too. An empty file
    is no schematic either, so nothing is lost by treating it as missing.
    """
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


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
    d = _ensure_dir(directory, "directory")

    pro_path = d / f"{name}.kicad_pro"
    if pro_path.exists():
        raise ToolError(f"{pro_path} already exists.")

    pro_data = {"meta": {"filename": f"{name}.kicad_pro", "version": 1}}
    _atomic_write(pro_path, (json.dumps(pro_data, indent=2) + "\n").encode())

    prl_data = {"meta": {"filename": f"{name}.kicad_prl", "version": 3}}
    prl_path = d / f"{name}.kicad_prl"
    _atomic_write(prl_path, (json.dumps(prl_data, indent=2) + "\n").encode())

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
        schematic_path: Path for the new .kicad_sch file.
            Optional; omit to use the configured default.
    """
    p = Path(schematic_path)
    if p.exists():
        raise ToolError(f"{p} already exists.")

    _ensure_dir(p.parent, "parent directory")

    tree = _cst.parse(_EMPTY_SCH_TPL)
    tree.lists[0].find("uuid").atoms[1].set_text(_gen_uuid())
    _atomic_write(p, _cst.serialize(tree))
    return f"Created schematic at {p}"


@mcp.tool(annotations=_ADDITIVE)
def create_symbol_library(symbol_lib_path: str) -> str:
    """Create a valid empty KiCad 9 symbol library.

    Args:
        symbol_lib_path: Path for the new .kicad_sym file.
            Optional; omit to use the configured default.
    """
    p = Path(symbol_lib_path)
    if p.exists():
        raise ToolError(f"{p} already exists.")

    _ensure_dir(p.parent, "parent directory")

    _atomic_write(p, _SYM_LIB_TPL)
    return f"Created symbol library at {p}"


# A sym-lib-table as KiCad 9 writes it, with one row (SYMBOL_LIB_TABLE::Format,
# eeschema/symbol_lib_table.cpp:300-309 at 9.0.8). Names and URIs go in through set_text, which
# quotes as KiCad's Quotew does.
_SYM_LIB_TABLE_TPL = (
    b"(sym_lib_table\n  (version 7)\n"
    b'  (lib (name "x")(type "KiCad")(uri "x")(options "")(descr ""))\n)\n'
)

#: What a table value cannot hold. KiCad 9 decodes escapes in a table value and KiCad 10
#: decodes none (library_table_grammar.h:60 at 10.0.7), so a value that needs an escape reads
#: differently in the two: a double quote, a line end, and a NUL, which KiCad 9 takes as the
#: end of a value.
_TABLE_UNSPELLABLE = '"\n\r\x00'


def _lib_row_problem(name: str, uri: str) -> tuple[str, str] | None:
    """The (field, reason) of the first thing a table row cannot hold, or None. ``uri`` is
    the text as written, its backslashes already turned into '/'. A nickname is not empty
    and holds no character below U+0020, no backslash and no colon (LIB_ID's rule); a URI is
    not empty; neither holds a character of _TABLE_UNSPELLABLE."""
    if not name:
        return "name", "a library nickname cannot be empty"
    illegal = next((c for c in name if ord(c) < 0x20 or c in "\\:"), None)
    if illegal is not None:
        return "name", f"{name!r} holds {illegal!r}, which a library nickname cannot hold"
    if not uri:
        return "uri", "a library path cannot be empty"
    for field, text in (("name", name), ("uri", uri)):
        bad = next((c for c in text if c in _TABLE_UNSPELLABLE), None)
        if bad is not None:
            return field, (
                f"{text!r} holds {bad!r}, which KiCad 9 and KiCad 10 do not read alike in a table"
            )
    return None


def _lib_table_rows(entries: list[LibTableEntry]) -> list[tuple[str, str]]:
    """The (nickname, uri) row each entry becomes, or a ToolError naming the first entry the
    table cannot hold, before anything is written. A URI's backslashes become '/', as KiCad's
    own writer stores them. A nickname an earlier entry has is refused too."""
    rows: list[tuple[str, str]] = []
    first_at: dict[str, int] = {}
    for index, entry in enumerate(entries):
        name = entry["name"]
        uri = entry["uri"].replace("\\", "/")
        problem = _lib_row_problem(name, uri)
        if problem is None and name in first_at:
            problem = "name", f"{name!r} is also the nickname of entries[{first_at[name]}]"
        if problem is not None:
            field, reason = problem
            raise ToolError(
                f"entries[{index}] {field}: {reason}. Nothing was written; a sym-lib-table"
                " already in the directory is unchanged."
            )
        first_at[name] = index
        rows.append((name, uri))
    return rows


@mcp.tool(annotations=_DESTRUCTIVE)
def create_sym_lib_table(directory: str, entries: list[LibTableEntry]) -> str:
    """Create a sym-lib-table file in the given directory.

    Each entry dict needs 'name' (the library nickname) and 'uri' (its path, usually
    "${KIPRJMOD}/..."). Overwrites existing sym-lib-table if present. A backslash in a uri
    is written as '/', as KiCad stores it.

    The whole list is checked first. An entry that cannot be written as given refuses the
    call, names the entry, and leaves any existing table as it was: an empty name; a name
    holding a character below U+0020, a backslash or a colon (a library nickname cannot);
    a name an earlier entry has; an empty uri; a double quote in a name or uri, or a line
    end or NUL in a uri (KiCad 9 and KiCad 10 read these differently in a table).

    Args:
        directory: Directory to write sym-lib-table in
        entries: List of dicts with 'name' and 'uri' keys
    """
    rows = _lib_table_rows(entries)
    tree = _cst.parse(_SYM_LIB_TABLE_TPL)
    table = tree.lists[0]
    row_tpl = table.find("lib")
    table.remove_child(row_tpl)
    anchor = table.find("version")
    for name, uri in rows:
        row = row_tpl.copy()
        row.find("name").atoms[1].set_text(name)
        row.find("uri").atoms[1].set_text(uri)
        table.insert_after(anchor, row)
        anchor = row
    data = _cst.serialize(tree)

    d = _ensure_dir(directory, "directory")
    table_path = d / "sym-lib-table"
    _atomic_write(table_path, data)
    return f"Created sym-lib-table with {len(entries)} entries at {table_path}"


@mcp.tool(annotations=_ADDITIVE)
def add_hierarchical_sheet(
    parent_schematic_path: str,
    sheet_name: str,
    sheet_file: str,
    pins: list[SheetPinSpec],
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
    _fill_at(sheet, x, y, iu=SCH_IU_PER_MM)
    size = sheet.find("size")
    size.atoms[1].set_text(_num(sheet_width, SCH_IU_PER_MM))
    size.atoms[2].set_text(_num(sheet_height, SCH_IU_PER_MM))
    sheet_uuid = _gen_uuid()
    sheet.find("uuid").atoms[1].set_text(sheet_uuid)
    sheet_props = {pr.atoms[1].text: pr for pr in sheet.find_all("property")}
    name_prop = sheet_props["Sheetname"]
    name_prop.atoms[2].set_text(sheet_name)
    _fill_at(name_prop, x, y - 1.27, iu=SCH_IU_PER_MM)
    file_prop = sheet_props["Sheetfile"]
    file_prop.atoms[2].set_text(child_path.name)
    _fill_at(file_prop, x, y + sheet_height + 1.27, iu=SCH_IU_PER_MM)

    # Pins on the sheet block (left edge) plus stubs and labels in the parent
    inst_anchor = sheet.find("instances")
    for i, pin_def in enumerate(pins):
        pin_y = _snap_grid(y + (i + 1) * pin_spacing)
        pin = _SHEET_PIN_TPL.copy()
        pin.atoms[1].set_text(pin_def["name"])
        pin.atoms[2].set_text(pin_def["direction"])
        _fill_at(pin, x, pin_y, 180, SCH_IU_PER_MM)
        pin.find("uuid").atoms[1].set_text(_gen_uuid())
        sheet.insert_before(inst_anchor, pin)

        # Wire stub going LEFT from pin, with a net label at its end
        stub_end_x = _snap_grid(x - 2.54)
        _splice_wire(parent_root, x, pin_y, stub_end_x, pin_y)
        label = _LABEL_TPL.copy()
        label.atoms[1].set_text(pin_def["name"])
        _fill_at(label, stub_end_x, pin_y, 180, SCH_IU_PER_MM)
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
    _atomic_write(parent_schematic_path, _cst.serialize(parent_tree))

    # Add matching hierarchical labels to child schematic
    child_tree, child_root, *_ = _open_sch_cst(sheet_file)
    label_x = _snap_grid(25.4)
    for i, pin_def in enumerate(pins):
        label_y = _snap_grid(25.4 + i * 5.08)
        hl = _HLABEL_TPL.copy()
        hl.atoms[1].set_text(pin_def["name"])
        hl.find("shape").atoms[1].set_text(pin_def["direction"])
        _fill_at(hl, label_x, label_y, 180, SCH_IU_PER_MM)
        hl.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(child_root, "hierarchical_label", hl)

        # Wire stub going RIGHT from label, with a net label at its end
        stub_end_x = _snap_grid(label_x + 2.54)
        _splice_wire(child_root, label_x, label_y, stub_end_x, label_y)
        lab = _LABEL_TPL.copy()
        lab.atoms[1].set_text(pin_def["name"])
        _fill_at(lab, stub_end_x, label_y, 0, SCH_IU_PER_MM)
        lab.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(child_root, "label", lab)

    # Add parent project instances to child symbols
    root_entries: list[tuple[str, str, int, str, str]] = []
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
                root_entries.append(
                    (
                        _node_uuid(sym),
                        _sym_property_cst(sym, "Reference") or "?",
                        1,
                        _sym_property_cst(sym, "Value") or "",
                        _sym_property_cst(sym, "Footprint") or "",
                    )
                )

    # Child before root, and the root once rather than once per symbol. The
    # root's symbol_instances is an index into the child, so writing it first
    # meant a failed child write left the root pointing at symbols that were
    # never written. Neither file can tear now, but they could still disagree,
    # and this is the ordering where the surviving disagreement is harmless.
    _atomic_write(sheet_file, _cst.serialize(child_tree))
    _upsert_root_symbol_instances(str(child_path), project_path, root_entries)

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
        parent_dir = Path(parent_schematic_path).parent.resolve()
        child_path = parent_dir / child_filename
        # child_filename is the Sheetfile property, so it is file content and can
        # name ../../../anything. This is the only delete in the package, and it
        # runs before the parent is written, so refusing leaves both files intact.
        if not child_path.resolve().is_relative_to(parent_dir):
            raise ToolError(
                f"Sheet file '{child_filename}' resolves outside {parent_dir}, so it was "
                "not deleted and the sheet block was not removed. Re-run with "
                "delete_child_file=False to remove the block on its own."
            )
        # Inside the directory it can still name something that is no sheet of this parent:
        # the board or the project file, the project's root schematic, or the parent itself,
        # which was unlinked and then rewritten at the umask's mode on a fresh inode.
        not_a_child = ""
        if child_path.suffix.lower() != ".kicad_sch":
            not_a_child = "is not a .kicad_sch file"
        elif child_path.exists() and child_path.samefile(parent_schematic_path):
            not_a_child = "is the parent schematic itself"
        elif child_path.with_suffix(".kicad_pro").exists():
            not_a_child = "is the root schematic of a project"
        if not_a_child:
            raise ToolError(
                f"Sheet file '{child_filename}' {not_a_child}, so it was not deleted and the "
                "sheet block was not removed. Re-run with delete_child_file=False to remove "
                "the block on its own."
            )

        # Check if any OTHER sheet still references this child file. A different spelling
        # of the same file ('./child.kicad_sch', an absolute path, a symlink) counts too.
        def _names_child(s) -> bool:
            sf = _sheet_file_cst(s) or ""
            if not sf:
                return False
            if sf == child_filename:
                return True
            # KiCad squeezes '.' and '..' out of a sheet path lexically before opening it
            # (wxFileName::MakeAbsolute normalizes with wxPATH_NORM_DOTS), while the kernel
            # walks each component, so 'zz/../child.kicad_sch' with no zz, or
            # 'child.kicad_sch/../child.kicad_sch', is the child to KiCad and missing to
            # samefile. Compare the lexical path too, both as text and as a file.
            lexical = Path(os.path.normpath(parent_dir / sf))
            if lexical == Path(os.path.normpath(child_path)):
                return True
            if not child_path.exists():
                return False
            for cand in (parent_dir / sf, lexical):
                try:
                    if cand.samefile(child_path):
                        return True
                except (FileNotFoundError, NotADirectoryError):
                    pass
                except (OSError, ValueError):
                    # This guards the package's only delete, so a block whose file cannot
                    # be compared for any reason but its absence keeps the child.
                    return True
            return False

        other_refs = any(_names_child(s) for j, s in enumerate(sheets) if j != matches[0])
        if other_refs:
            msg += f" Kept child file '{child_filename}' — still referenced by another sheet block."
        elif child_path.is_file():
            child_path.unlink()
            msg += f" Deleted child file '{child_filename}'."

    root.remove_child(target)
    _atomic_write(parent_schematic_path, _cst.serialize(tree))
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
        schematic_path: Path to parent .kicad_sch. Optional; omit to use the configured default.
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
        width = _snap(width, SCH_IU_PER_MM)
        size.atoms[1].set_text(_num(width, SCH_IU_PER_MM))
        changes.append(f"width={width}")
    if height is not None:
        height = _snap(height, SCH_IU_PER_MM)
        size.atoms[2].set_text(_num(height, SCH_IU_PER_MM))
        changes.append(f"height={height}")
    _atomic_write(schematic_path, _cst.serialize(tree))
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
        schematic_path: Path to parent .kicad_sch. Optional; omit to use the configured default.
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
    _fill_at(pin, pin_x, pin_y, 180 if side == "left" else 0, SCH_IU_PER_MM)
    pin.find("uuid").atoms[1].set_text(_gen_uuid())
    if existing_pins:
        target.insert_after(existing_pins[-1], pin)
    else:
        props = target.find_all("property")
        target.insert_after(props[-1], pin)
    _atomic_write(schematic_path, _cst.serialize(tree))
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
        schematic_path: Path to parent .kicad_sch. Optional; omit to use the configured default.
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sheet_cst(root, sheet_uuid)
    pin = next((p for p in target.find_all("pin") if p.atoms[1].text == pin_name), None)
    if pin is None:
        raise ToolError(f"Pin '{pin_name}' not found on sheet")
    target.remove_child(pin)
    _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Removed pin '{pin_name}' from sheet"


@mcp.tool(annotations=_ADDITIVE)
def annotate_schematic(schematic_path: str = SCH_PATH, project_path: str = "") -> str:
    """Auto-assign reference designators to unannotated components.

    Finds components with '?' in their reference (e.g. R?, U?) and assigns
    sequential numbers, respecting existing references in the schematic
    and across the hierarchy when project_path is provided.

    Args:
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
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
            if _sheet_file_nonempty(child_path) and str(child_path.resolve()) != str(
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

    _atomic_write(schematic_path, _cst.serialize(tree))

    # Sync root symbolInstances for all annotated symbols. One parse and one
    # write for the batch: this was a per-symbol call, so a fifty-symbol sheet
    # re-parsed and rewrote the whole root schematic fifty times.
    _upsert_root_symbol_instances(
        schematic_path,
        project_path,
        [
            (
                _node_uuid(sym),
                _sym_property_cst(sym, "Reference") or "?",
                1,
                _sym_property_cst(sym, "Value") or "",
                _sym_property_cst(sym, "Footprint") or "",
            )
            for sym, _prefix in unannotated
        ],
    )

    parts = []
    for prefix in sorted(assigned):
        refs = assigned[prefix]
        parts.append(f"{refs[0]}-{refs[-1]}" if len(refs) > 1 else refs[0])
    total = sum(len(v) for v in assigned.values())
    return f"Annotated {total} components: {', '.join(parts)}"


def _read_sheet(path: Path):
    """(parsed root, "") for a sheet file, or (None, why) when it cannot be read or parsed."""
    try:
        doc = _cst.parse(path.read_bytes())
    except (OSError, SyntaxError) as e:
        return None, str(e)
    node = doc.lists[0] if doc.lists else None
    if node is None or node.head != "kicad_sch":
        return None, "it is not a KiCad schematic"
    return node, ""


@mcp.tool(annotations=_READ_ONLY)
def validate_hierarchy(schematic_path: str = SCH_PATH) -> HierarchyValidationResult:
    """Validate a hierarchical schematic for common issues.

    Checks every sheet below the root, however deep, each file once. A relative sheet file
    name is found beside the file holding the sheet, as KiCad finds it; a sheet that contains
    itself, which KiCad refuses, is skipped. Per sheet block: orphaned labels and pins,
    direction mismatches, a missing file, a file that cannot be parsed ("unreadable_file",
    whose "note" names the file; its checks are skipped, the rest still run). Per sheet
    instance, so a reused sheet counts once per use: duplicate and unannotated reference
    designators, each read from the entry KiCad reads at that instance, the root's
    symbol_instances table on a root in KiCad 6's form, else the symbol's own (the units of
    one multi-unit part share theirs); an instance with no entry reads the symbol's first
    entry, else its Reference property. Past 4096 instances a "references_unchecked" note says
    the rest went unchecked. Per file: the points where kicad-cli 9 drops part of a line that
    KiCad's editor keeps.

    Those are a junction strictly inside a wire or bus ("junction_inside_wire") and, in a file
    kicad-cli 9 loads, a bus entry end strictly inside one ("bus_entry_inside_wire"); KiCad's
    editor never saves either, and earlier versions of this package wrote both. kicad-cli 9
    (ERC, netlist export, PCB update) cuts the line there: per line, "kept" is the stretch it
    keeps and "dropped" the stretch it loses, cutting off whatever joins the line only along
    that. "editor" says what KiCad 9's editor makes of it: it splits the line, keeping a
    junction where three or more directions meet ("keeps_junction"), deleting it elsewhere
    ("deletes_junction"), and at a bus entry end placing none ("splits_wire"). A file stamped
    past KiCad 9's format gets neither reading. The readings follow KiCad's source; whether
    kicad-cli 9 loads the sheet at all is not checked. To fix one, make the call in its
    "repair" as given: add_junctions at the point, which splits the line, or remove_junction.
    Such an issue carries a "note" in place of a repair in two cases only: its sheet is one the
    connectivity model refuses (a symbol or pin at an angle KiCad cannot load; no reading
    either), or it is a bus entry end where KiCad allows no junction.

    Per file too: the no-connect flags ("no_connect_off_pin") and wire ends
    ("wire_end_off_pin") at the point releases 0.5.8 to 0.20.1 gave a pin of a symbol at
    rotation 90 or 270 with a mirror. They mirrored before rotating, so that point is the
    pin's end reflected through the symbol's origin, and 0.20.1's no_connect_pin,
    wire_pins_to_net and connect_pins wrote there (measured). Only a point where KiCad draws
    no pin is checked. Each issue names the symbol ("reference"), the "pin", the point ("x",
    "y") and where KiCad draws the pin ("pin_x", "pin_y"), and a wire end its "wire". Where
    the old transform put several pins at the point, the issue lists them under "pins" in
    place of those four, with a note and no repair, since which pin the flag or wire was
    written for is not known. A flag is not reported where a sheet pin or a hierarchical label
    is too; a wire end is not where an end of another item but a bus's lies on it, a flag and
    the Net-(<ref>-<pin>) label connect_pins wrote there apart; neither is where any reader
    may join it to a pin KiCad draws for the point already. A flag carries a note: no tool
    removes one by its position, so delete it in KiCad's editor, then no_connect_pin flags the
    pin. A wire end's "repair" is wire_pins_to_net with the one local label name on its net
    that the tool accepts, planned first; else a note, with the plan's refusal or, where the
    net has no single such name, the pin the wire reaches. Nothing is written.

    Args:
        schematic_path: Path to root .kicad_sch file. Optional; omit to use the configured default.
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    top = Path(schematic_path)
    issues: list[dict] = []

    top_real = _connectivity._real(top)
    trees = {top_real: root}  # each file parsed once, by its resolved path
    unread: dict[Path, str] = {}  # each file that could not be parsed, with why
    # Per parsed file, its sheets whose files parsed: (sheet uuid, resolved file, label).
    kids: dict[Path, list[tuple[str, Path, str]]] = {}
    issues.extend(_cuts_inside_lines(root, str(top)))
    issues.extend(_off_pin_items(root, str(top)))
    # Depth first, in file order: (file holding the sheet, the sheet, resolved files from the
    # root down to the one holding it).
    todo = [(top, sheet, (top_real,)) for sheet in reversed(root.find_all("sheet"))]
    while todo:
        parent, sheet, chain = todo.pop()
        sheet_name = _sheet_name_cst(sheet) or ""
        file_name = _sheet_file_cst(sheet) or ""
        # KiCad's loader makes a relative name absolute against the directory of the file
        # being loaded (loadHierarchy, sch_io_kicad_sexpr.cpp:189-198 at 9.0.8).
        child_path = parent.parent / file_name
        # Not exists(), here and at every other read of a Sheetfile: it is document
        # content, and a directory, a device such as /dev/zero or a procfs pseudo-file,
        # which read_bytes() never finishes, counts as a missing sheet.
        if not _sheet_file_nonempty(child_path):
            issues.append(
                {
                    "type": "missing_file",
                    "sheet_name": sheet_name,
                    "file_name": file_name,
                }
            )
            continue
        real = _connectivity._real(child_path)
        new = real not in trees and real not in unread
        if new:
            child_root, why = _read_sheet(child_path)
            if child_root is None:
                unread[real] = why
            else:
                trees[real] = child_root
        if real in unread:
            issues.append(
                {
                    "type": "unreadable_file",
                    "sheet_name": sheet_name,
                    "file_name": file_name,
                    "note": f"{child_path} cannot be parsed, so it was not checked: {unread[real]}",
                }
            )
            continue
        kids.setdefault(chain[-1], []).append((_node_uuid(sheet), real, file_name))
        if real in chain:  # KiCad refuses a sheet that contains itself (loadHierarchy, 202-222)
            continue
        child_root = trees[real]
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

        if new:
            issues.extend(_cuts_inside_lines(child_root, str(child_path)))
            issues.extend(_off_pin_items(child_root, str(child_path)))
            below = (*chain, real)
            todo += [(child_path, s, below) for s in reversed(child_root.find_all("sheet"))]

    # References, per sheet instance: a reused sheet's symbol carries one per instance path,
    # "/<root uuid>/<sheet uuid>/..." (SymRecord.at, after sch_symbol.cpp GetRef at 9.0.8). On
    # a root older than 20221002, KiCad 6's form, a symbol takes at each instance the first
    # entry of the root's (symbol_instances) table for that instance, whose path is written
    # below the root, as the parser puts the root's uuid in front (UpdateSymbolInstanceData,
    # sch_sheet_path.cpp:1277-1318, called from eeschema_helpers.cpp:180-181, and
    # sch_io_kicad_sexpr_parser.cpp:2543-2552, at 9.0.8).
    top_facts = _connectivity.sheet_facts(root)
    table: dict[str, tuple[str, int]] = {}
    if top_facts.version < _connectivity._ON_SYMBOL_VERSION:
        for where, ref, unit in top_facts.legacy:
            table.setdefault(where, (ref, unit))
    all_refs: dict[str, list] = {}  # ref -> [(record, (unit,), sheet label)], one per instance
    records: dict[Path, tuple] = {top_real: top_facts.records}
    # (file, its instance path, that path below the root, sheet label, files root to it)
    walk: list[tuple[Path, str, str, str, tuple[Path, ...]]] = [
        (top_real, f"/{_node_uuid(root)}", "", top.name, (top_real,))
    ]
    seen = 0
    while walk:
        real, kpath, under, label, chain = walk.pop()
        seen += 1
        if seen > _connectivity._MAX_INSTANCES:
            issues.append(
                {
                    "type": "references_unchecked",
                    "note": f"The hierarchy has more than {_connectivity._MAX_INSTANCES} sheet"
                    " instances; references were checked on the first"
                    f" {_connectivity._MAX_INSTANCES} only.",
                }
            )
            break
        if real not in records:
            records[real] = _connectivity.sheet_facts(trees[real]).records
        for rec in records[real]:
            hit = table.get(f"{under}/{rec.uuid}")
            if hit is not None:
                ref, unit = hit
            elif not rec.entries and rec.prop_ref == "?":
                continue  # no Reference at all
            else:
                # Not modelled: at an instance with no entry, KiCad reads the reference and unit
                # its load last left in the symbol, the entry of the last instance in its page
                # order that has one (UpdateAllScreenReferences, sch_sheet_path.cpp:376-396 at
                # 9.0.8). SymRecord.at reads the first entry, else the Reference property, which
                # KiCad reads too unless an entry at another instance names something else.
                ref, unit = rec.at(kpath)
            if "?" in ref:
                issues.append({"type": "unannotated_ref", "sheet": label, "reference": ref})
            else:
                all_refs.setdefault(ref, []).append((rec, (unit,), label))
        for su, child, name in reversed(kids.get(real, ())):
            if child not in chain:
                walk.append((child, f"{kpath}/{su}", f"{under}/{su}", name, (*chain, child)))

    for ref, placed in all_refs.items():
        if ref.startswith("#"):  # Skip power symbols
            continue
        # The units of one multi-unit part share a reference; anything else sharing one is a
        # duplicate.
        if len(placed) > 1 and _connectivity._one_part([(r, u) for r, u, _ in placed]):
            issues.append(
                {
                    "type": "duplicate_ref",
                    "reference": ref,
                    "sheets": [label for *_, label in placed],
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

    A sheet is found in its project wherever its file sits below the project's
    folder. A file that no sheet of its project uses is a root, as KiCad opens
    it. For a sub-sheet, root_path is the project's root schematic.

    Args:
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
    """
    # is_root=True meant "no parent sheet references it", which a file that
    # does not exist also satisfies, so a bogus path used to report itself the
    # root of a hierarchy. Read it first and let the shared opener refuse.
    _read_kicad_bytes(schematic_path, "schematic")
    placement = _connectivity.place(schematic_path)
    if placement.kind != "sheet":
        return RootSchematicResult(is_root=True, root_path=None)
    return RootSchematicResult(is_root=False, root_path=str(placement.root))


@mcp.tool(annotations=_READ_ONLY)
def list_hierarchy(schematic_path: str = SCH_PATH) -> HierarchyResult:
    """List the full sheet hierarchy starting from a root schematic.

    Args:
        schematic_path: Path to root .kicad_sch file. Optional; omit to use the configured default.
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
        if _sheet_file_nonempty(child_path):
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
        schematic_path: Path to parent .kicad_sch. Optional; omit to use the configured default.
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sheet_cst(root, sheet_uuid)

    file_name = _sheet_file_cst(target) or ""
    child_path = Path(schematic_path).parent / file_name

    # Load child to check label matching
    child_labels: set[str] = set()
    child_info: dict = {}
    if _sheet_file_nonempty(child_path):
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
        schematic_path: Path to root .kicad_sch file. Optional; omit to use the configured default.
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
            if _sheet_file_nonempty(child_path):
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
        if _sheet_file_nonempty(child_path):
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
        schematic_path: Path to root .kicad_sch file. Optional; omit to use the configured default.
    """
    _, root, *_ = _open_sch_cst(schematic_path)
    sch_dir = Path(schematic_path).parent

    hierarchical_nets: list[dict] = []
    global_nets: dict[str, list[str]] = {}  # name -> [sheet files]

    for sheet in root.find_all("sheet"):
        sheet_name = _sheet_name_cst(sheet) or ""
        file_name = _sheet_file_cst(sheet) or ""
        child_path = sch_dir / file_name
        child_root = (
            _cst.parse(child_path.read_bytes()).lists[0]
            if _sheet_file_nonempty(child_path)
            else None
        )
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
        schematic_path: Path to root .kicad_sch file. Optional; omit to use the configured default.
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
        schematic_path: Path to parent .kicad_sch. Optional; omit to use the configured default.
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sheet_cst(root, sheet_uuid)
    at = target.find("at")
    new_x, new_y = _snap(new_x, SCH_IU_PER_MM), _snap(new_y, SCH_IU_PER_MM)
    dx = new_x - float(at.atoms[1].text)
    dy = new_y - float(at.atoms[2].text)
    at.atoms[1].set_text(_num(new_x, SCH_IU_PER_MM))
    at.atoms[2].set_text(_num(new_y, SCH_IU_PER_MM))

    def _shift(node) -> None:
        n_at = node.find("at")
        if n_at is None:
            return
        n_at.atoms[1].set_text(_num(float(n_at.atoms[1].text) + dx, SCH_IU_PER_MM))
        n_at.atoms[2].set_text(_num(float(n_at.atoms[2].text) + dy, SCH_IU_PER_MM))

    # Move pins and property positions by the same delta
    for pin in target.find_all("pin"):
        _shift(pin)
    for prop in target.find_all("property"):
        _shift(prop)
    _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Moved sheet to ({new_x}, {new_y})"


@mcp.tool(annotations=_DESTRUCTIVE)
def reorder_sheet_pages(
    page_order: list[str],
    schematic_path: str = SCH_PATH,
) -> str:
    """Reorder hierarchical sheets by specifying the desired UUID order.

    Args:
        page_order: List of sheet UUIDs in desired order
        schematic_path: Path to root .kicad_sch file. Optional; omit to use the configured default.
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    sheets = root.find_all("sheet")
    sheet_map = {_node_uuid(s): s for s in sheets}
    missing = [u for u in page_order if u not in sheet_map]
    if missing:
        raise ToolError(
            f"Sheet UUIDs not found: {missing}."
            " Use list_schematic_sheets to see the sheets and their UUIDs."
        )
    # The slots are filled from new_order, so a block listed twice took two slots and the
    # last sheet fell off the end with its pins and instances: [A, A] over A, B, C wrote
    # A, A, B. sheet_map also keeps only the last block per UUID, so two blocks sharing a
    # listed UUID lost one of them even when page_order itself had no repeats.
    name = Path(schematic_path).name
    repeated = sorted(u for u, n in Counter(page_order).items() if n > 1)
    if repeated:
        raise ToolError(
            f"Sheet UUIDs listed more than once in page_order: {repeated}. List each sheet"
            f" once; {name} was not changed."
        )
    uuid_count = Counter(_node_uuid(s) for s in sheets)
    shared = [u for u in page_order if uuid_count[u] > 1]
    if shared:
        raise ToolError(
            f"Sheet UUIDs {shared} each belong to more than one sheet block in {name}, so"
            " their order is ambiguous and the file was not changed. Leave them out of"
            " page_order to keep those blocks where they are."
        )
    new_order = [sheet_map[u] for u in page_order]
    new_order += [s for s in sheets if _node_uuid(s) not in page_order]
    if len(new_order) != len(sheets) or {id(n) for n in new_order} != {id(s) for s in sheets}:
        raise ToolError(
            f"page_order would not keep every sheet block of {name} exactly once, so the"
            " file was not changed."
        )
    # Swap nodes in place, keeping each slot's leading whitespace where it was.
    slots = [i for i, c in enumerate(root.children) if c.kind == "list" and c.head == "sheet"]
    slot_seps = [root.children[i].sep for i in slots]
    for slot, sep, node in zip(slots, slot_seps, new_order):
        node.sep = sep
        root.children[slot] = node
    _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Reordered {len(page_order)} sheets"


@mcp.tool(annotations=_DESTRUCTIVE)
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
        schematic_path: Path to parent .kicad_sch. Optional; omit to use the configured default.
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
    # source_file is the document's Sheetfile. copy2 streams a device such as /dev/zero
    # until the disk is full and leaves the partial copy behind, and a procfs pseudo-file
    # like /proc/self/pagemap is a regular file of size 0 that streams the same way. An
    # empty source is no schematic either, so all of them are refused before any write.
    if not _sheet_file_nonempty(src_path):
        raise ToolError(
            f"Source file not found, empty, or not a regular file: {src_path}. Nothing was"
            f" copied and {Path(schematic_path).name} was not changed."
        )
    if dst_path.exists():
        # Called twice with the same name, this silently replaced the first
        # copy with the source sheet. Every other create in this module guards
        # (create_project, create_schematic, create_symbol_library); this one
        # did not.
        raise ToolError(f"{dst_path} already exists. Pass a different new_file_name.")

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
    _atomic_write(dst_path, _cst.serialize(copy_tree))

    # Create new sheet block in parent (geometry from source, fresh identity)
    dx = src_w + 5
    new_sheet = _SHEET_TPL.copy()
    new_uuid = _gen_uuid()
    new_sheet.find("uuid").atoms[1].set_text(new_uuid)
    _fill_at(new_sheet, src_x + dx, src_y, iu=SCH_IU_PER_MM)
    new_size = new_sheet.find("size")
    new_size.atoms[1].set_text(_num(src_w, SCH_IU_PER_MM))
    new_size.atoms[2].set_text(_num(src_h, SCH_IU_PER_MM))
    new_props = {pr.atoms[1].text: pr for pr in new_sheet.find_all("property")}
    name_prop = new_props["Sheetname"]
    name_prop.atoms[2].set_text(new_sheet_name)
    _fill_at(name_prop, src_x + dx, src_y - 1.27, iu=SCH_IU_PER_MM)
    file_prop = new_props["Sheetfile"]
    file_prop.atoms[2].set_text(new_file_name)
    _fill_at(file_prop, src_x + dx, src_y + src_h + 1.27, iu=SCH_IU_PER_MM)

    # Copy pins with offset and fresh uuids
    inst_anchor = new_sheet.find("instances")
    for pin in source.find_all("pin"):
        new_pin = pin.copy()
        pin_at = new_pin.find("at")
        pin_at.atoms[1].set_text(_num(float(pin_at.atoms[1].text) + dx, SCH_IU_PER_MM))
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
    _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Duplicated sheet as '{new_sheet_name}' -> {new_file_name}"


# Distinct sheet files flatten_hierarchy walks when checking an existing output_path. Past
# it the check cannot vouch for the output, so the tool refuses rather than write.
_MAX_HIERARCHY_FILES = 4096


@mcp.tool(annotations=_DESTRUCTIVE)
def flatten_hierarchy(
    schematic_path: str = SCH_PATH,
    output_path: str = "",
) -> str:
    """Flatten a hierarchical schematic into a single sheet.

    Merges all child sheet content into one schematic with offset positions.
    Creates a new file — does NOT modify the original hierarchy.

    Args:
        schematic_path: Path to root .kicad_sch file. Optional; omit to use the configured default.
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

    # The docstring above promises the original hierarchy is not modified, and
    # output_path was unchecked, so pointing it at an input silently consumed
    # the file being flattened. Narrow on purpose: a bare exists() check would
    # break re-flattening to the same output, which is the normal loop, and the
    # ADR records that refuse-to-clobber in general needs an overwrite
    # parameter first.
    out = Path(output_path).resolve()
    if not out.exists():
        inputs = {Path(schematic_path).resolve()} | {
            (sch_dir / f).resolve() for f in child_files if f
        }
        clash = out in inputs
    else:
        # Only an existing file can be lost, and the root and its own children are not the
        # whole hierarchy: output_path naming a grandchild overwrote it. So walk every
        # sheet. A sheet file resolves against its parent's directory, then the root's, and
        # both are protected. One that cannot be read or parsed stays protected, though
        # what lies below it is unknown.
        def _real(p: Path) -> Path:
            # Before Python 3.13 resolving a symlink loop raises RuntimeError; later
            # versions return the path unresolved.
            try:
                return p.resolve()
            except (OSError, RuntimeError):
                return p.absolute()

        inputs: set[Path] = set()
        todo = [Path(schematic_path)]
        while todo:
            f = todo.pop()
            rf = _real(f)
            if rf in inputs:
                continue
            if len(inputs) >= _MAX_HIERARCHY_FILES:
                raise ToolError(
                    f"The hierarchy under {Path(schematic_path).name} names more than"
                    f" {_MAX_HIERARCHY_FILES} sheet files, so output_path {out.name} could"
                    " not be checked against them and nothing was written."
                )
            inputs.add(rf)
            if not _sheet_file_nonempty(f):
                continue
            try:
                sheet_root = _cst.parse(f.read_bytes()).lists[0]
            except Exception:
                continue
            for s in sheet_root.find_all("sheet"):
                name = _sheet_file_cst(s) or ""
                if name:
                    # KiCad squeezes '..' lexically, so 'zz/../grand.kicad_sch' with no zz
                    # is a sheet to KiCad and missing to the kernel; protect both readings.
                    cands = [f.parent / name, sch_dir / name]
                    cands += [Path(os.path.normpath(c)) for c in cands]
                    todo += [c for c in cands if c.exists()]
        # samefile also catches a case-only alias on a case-insensitive filesystem.
        clash = out in inputs or any(p.exists() and out.samefile(p) for p in inputs)
    if clash:
        raise ToolError(
            f"output_path {out.name} is part of the hierarchy being flattened."
            " Choose a different output file."
        )
    for token in ("sheet", "hierarchical_label", "symbol_instances", "sheet_instances"):
        for node in flat_root.find_all(token):
            flat_root.remove_child(node)

    def _offset_at(node, ox: float, oy: float) -> None:
        n_at = node.find("at")
        if n_at is None:
            return
        n_at.atoms[1].set_text(_num(float(n_at.atoms[1].text) + ox, SCH_IU_PER_MM))
        n_at.atoms[2].set_text(_num(float(n_at.atoms[2].text) + oy, SCH_IU_PER_MM))

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
        if not _sheet_file_nonempty(child_path):
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
                        x_text = _num(float(xy.atoms[1].text) + x_offset, SCH_IU_PER_MM)
                        y_text = _num(float(xy.atoms[2].text) + y_offset, SCH_IU_PER_MM)
                        xy.atoms[1].set_text(x_text)
                        xy.atoms[2].set_text(y_text)
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

    _atomic_write(output_path, _cst.serialize(flat_tree))

    total_components = len(flat_root.find_all("symbol"))
    return f"Flattened hierarchy to {Path(output_path).name}: {total_components} components"


@mcp.tool(annotations=_EXPORT, title="Export a netlist with sheet paths for the whole hierarchy")
def export_hierarchical_netlist(
    schematic_path: str = SCH_PATH,
    output_dir: str = "",
) -> HierarchicalNetlistResult:
    """Export a netlist from the root schematic, including hierarchy info.

    Runs kicad-cli to generate a netlist and returns parsed component/net data
    with sheet path information for each component.

    Args:
        schematic_path: Path to root .kicad_sch file. Optional; omit to use the configured default.
        output_dir: Directory for netlist output (defaults to schematic directory).
            Optional; omit to use the configured default.
    """
    _require_kicad_path(schematic_path, "schematic")
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


# kicad-cli prints this line once for each output it runs, as literals that no locale
# changes (kicad/jobs_runner.cpp:324-328 at 9.0.8), and colours it.
_JOBSET_COUNTS = re.compile(r"(\d+) jobs succeeded, (\d+) job failed")
_ANSI_COLOUR = re.compile(r"\x1b\[[0-9;]*m")


def _project_beside(jobset: Path) -> Path:
    """The one .kicad_pro in the jobset's own folder, else a ToolError saying why not."""
    found = sorted(jobset.parent.glob("*.kicad_pro"))
    if len(found) == 1:
        return found[0]
    if not found:
        raise ToolError(
            f"No .kicad_pro beside {jobset.name} in {jobset.parent}. run_jobset runs a jobset"
            " against the one project file in the jobset's own folder."
        )
    names = ", ".join(p.name for p in found)
    raise ToolError(
        f"{len(found)} .kicad_pro files beside {jobset.name} in {jobset.parent} ({names}),"
        " and run_jobset needs exactly one to know which project the jobset runs against."
    )


@mcp.tool(annotations=_EXPORT)
def run_jobset(jobset_path: str) -> str:
    """Run a KiCad jobset file: every output it defines.

    The jobset runs against the one .kicad_pro in its own folder, and a relative
    output folder in it is relative to that folder. It is an error when nothing
    ran or a job failed, and the error carries the counts kicad-cli reported.

    Args:
        jobset_path: Path to .kicad_jobset file
    """
    jobset = Path(os.path.abspath(_require_kicad_path(jobset_path, "jobset")))
    pro = _project_beside(jobset)
    result = _run_cli(
        ["jobset", "run", "--file", str(jobset), str(pro)], check=False, cwd=str(pro.parent)
    )
    stdout = _ANSI_COLOUR.sub("", result.stdout)
    counts = _JOBSET_COUNTS.findall(stdout)
    reported = "; ".join(f"{done} jobs succeeded, {failed} job failed" for done, failed in counts)
    if result.returncode != 0 or any(int(failed) for _, failed in counts):
        detail = f" {result.stderr.strip()}" if result.stderr.strip() else ""
        raise ToolError(
            f"Jobset {jobset.name} failed: kicad-cli exit code {result.returncode},"
            f" {reported or 'no job counts'}.{detail}"
        )
    if not any(int(done) for done, _ in counts):
        raise ToolError(
            f"Jobset {jobset.name} ran nothing: kicad-cli reported {reported or 'no job counts'}."
            " A jobset with no outputs, or a file that is not a jobset, runs nothing."
        )
    return f"Jobset completed successfully.\n{stdout}"


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
