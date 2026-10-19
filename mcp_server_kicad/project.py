"""KiCad project scaffolding tools.

Tools for creating KiCad project files, schematics, symbol libraries,
sym-lib-tables, hierarchical sheets, jobset execution, and version info.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Literal, NamedTuple

from mcp.server.mcpserver.exceptions import ToolError

import mcp_server_kicad._connectivity as _connectivity
import mcp_server_kicad._cst as _cst
from mcp_server_kicad._cst import (
    SCH_IU_PER_MM,
    _anchor_ws,
    _fill_at,
    _node_text,
    _num,
    _numish,
    _snap,
)
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
    _VALID_LABEL_SHAPES,
    _cuts_inside_lines,
    _label_spin,
    _line_xys,
    _off_pin_items,
    _open_sch_cst,
    _page_size_cst,
    _splice_sch_node,
    _splice_wire,
    _xy_iu,
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
        "1. create_project: creates .kicad_pro and the root .kicad_sch (no .kicad_prl)\n"
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


# Sheet block and sheet pin templates as KiCad 9.0.8 writes them, whitespace included, with
# placeholder values. Source, in its shipped demos: the vme_buffers_addr sheet of
# vme-wren/vme_interface.kicad_sch, without its pins, and one of its pins.
_SHEET_TPL = _cst.parse(
    b"(sheet\n\t\t(at 0 0)\n\t\t(size 25.4 10.16)\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)"
    b"\n\t\t(on_board yes)\n\t\t(dnp no)\n\t\t(fields_autoplaced yes)\n\t\t(stroke"
    b"\n\t\t\t(width 0.1524)\n\t\t\t(type solid)\n\t\t)\n\t\t(fill\n\t\t\t(color 0 0 0 0.0000)"
    b'\n\t\t)\n\t\t(uuid "x")\n\t\t(property "Sheetname" "X"\n\t\t\t(at 0 0 0)\n\t\t\t(effects'
    b"\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left bottom)"
    b'\n\t\t\t)\n\t\t)\n\t\t(property "Sheetfile" "x.kicad_sch"\n\t\t\t(at 0 0 0)\n\t\t\t(effects'
    b"\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left top)\n\t\t\t)"
    b'\n\t\t)\n\t\t(instances\n\t\t\t(project "X"\n\t\t\t\t(path "/x"\n\t\t\t\t\t(page "2")'
    b"\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)"
).lists[0]

_SHEET_PIN_TPL = _cst.parse(
    b'(pin "X" input\n\t\t\t(at 0 0 180)\n\t\t\t(uuid "x")\n\t\t\t(effects\n\t\t\t\t(font'
    b"\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t\t(justify left)\n\t\t\t)\n\t\t)"
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
    """Create a KiCad 9 project (.kicad_pro + root .kicad_sch).

    No .kicad_prl is written, and an existing .kicad_sch or .kicad_prl of that name is left
    as it is; the result says whether the root schematic was created or kept.

    Args:
        directory: Directory to create the project in (created if missing)
        name: Project name (used for filenames)
    """
    d = _ensure_dir(directory, "directory")

    pro_path = d / f"{name}.kicad_pro"
    if pro_path.exists():
        raise ToolError(f"{pro_path} already exists.")

    # The root schematic first, then the project file that names it: a failure in between
    # leaves a schematic with no project, never a project with no schematic.
    sch_path = d / f"{name}.kicad_sch"
    kept = sch_path.exists()
    if not kept:
        create_schematic(str(sch_path))
    pro_data = {"meta": {"filename": f"{name}.kicad_pro", "version": 1}}
    _atomic_write(pro_path, (json.dumps(pro_data, indent=2) + "\n").encode())

    if kept:
        return f"Created project at {pro_path}; kept the existing root schematic {sch_path}"
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


# ---------------------------------------------------------------------------------------------
# Sheet geometry, in internal units (10000 per mm). Pin positions are read the way KiCad's
# parser leaves them (SCH_SHEET_PIN::ConstrainOnEdge and SetSide), so every edit starts from
# where a pin really is, whatever its written position says.
# ---------------------------------------------------------------------------------------------

#: This tool's layout rule for sheet pins, not KiCad's: one every 2.54 mm down an edge.
_PIN_PITCH = 25400
_MIL = 254
_GRID = 50 * _MIL
_MIN_SHEET_WIDTH = 500 * _MIL
_MIN_SHEET_HEIGHT = 150 * _MIL
_SIDE_OF_ANGLE = {0: "right", 90: "top", 180: "left", 270: "bottom"}
_ANGLE_OF_SIDE = {side: angle for angle, side in _SIDE_OF_ANGLE.items()}
#: The horizontal justify of the text a pin on each side gets from SetSide's spin.
_PIN_JUSTIFY = {"left": "left", "bottom": "left", "right": "right", "top": "right"}
_DEFAULT_PEN = 6 * _MIL
_DEFAULT_TEXT_OFFSET_RATIO = 0.15
_DEFAULT_TEXT_SIZE = 12700


def _iu(value: float) -> int:
    """A millimetre value in internal units, as it would read back from the file."""
    return _connectivity.iu(_num(value, SCH_IU_PER_MM))


class _Box(NamedTuple):
    x: int
    y: int
    w: int
    h: int


class _SheetPin(NamedTuple):
    node: object
    side: str
    x: int
    y: int


def _sheet_box(sheet) -> _Box:
    at, size = sheet.find("at"), sheet.find("size")
    return _Box(
        _connectivity.iu(at.atoms[1].text),
        _connectivity.iu(at.atoms[2].text),
        _connectivity.iu(size.atoms[1].text),
        _connectivity.iu(size.atoms[2].text),
    )


def _nearest_side(box: _Box, px: int, py: int) -> str:
    """The sheet edge nearest a point (top, right, bottom, left; the first wins a tie)."""
    right, bottom = box.x + box.w, box.y + box.h
    edges = (
        ("top", box.x, box.y, right, box.y),
        ("right", right, box.y, right, bottom),
        ("bottom", box.x, bottom, right, bottom),
        ("left", box.x, box.y, box.x, bottom),
    )
    best, best_d = "", -1
    for side, x0, y0, x1, y1 in edges:
        dx = max(x0 - px, 0, px - x1)
        dy = max(y0 - py, 0, py - y1)
        d = dx * dx + dy * dy
        if best_d < 0 or d < best_d:
            best, best_d = side, d
    return best


def _constrain(box: _Box, side: str, px: int, py: int) -> tuple[int, int]:
    """The point on an edge of the sheet: onto the edge across it, clamped along it."""
    if side in ("left", "right"):
        return box.x + (box.w if side == "right" else 0), min(max(py, box.y), box.y + box.h)
    return min(max(px, box.x), box.x + box.w), box.y + (box.h if side == "bottom" else 0)


def _loaded_pins(sheet, box: _Box) -> list[_SheetPin]:
    """The sheet's pins where KiCad's parser puts them: SCH_SHEET_PIN::SetPosition moves the pin
    onto the nearest edge, then the angle in its (at) picks the side (sch_sheet_pin.cpp:114-223
    and sch_sheet_pin.h:194-196 at 9.0.8)."""
    pins = []
    for node in sheet.find_all("pin"):
        at = node.find("at")
        try:
            side = _SIDE_OF_ANGLE[int(float(at.atoms[3].text))]
        except (IndexError, KeyError, ValueError):
            raise ToolError(
                f"Sheet pin '{node.atoms[1].text}' has an angle other than 0, 90, 180 or 270."
            ) from None
        px, py = _connectivity.iu(at.atoms[1].text), _connectivity.iu(at.atoms[2].text)
        x, y = _constrain(box, _nearest_side(box, px, py), px, py)
        if side in ("left", "right"):
            x = box.x + (box.w if side == "right" else 0)
        else:
            y = box.y + (box.h if side == "bottom" else 0)
        pins.append(_SheetPin(node, side, x, y))
    return pins


def _font(node) -> tuple[int, int, bool, int]:
    """(width, height, bold, thickness) of a node's text, from its effects, which print
    (size HEIGHT WIDTH). Bold is `bold`, `(bold)` or `(bold yes)` (not `(bold no)`), the last
    one written winning; thickness is 0 when absent."""
    effects = node.find("effects")
    font = effects.find("font") if effects is not None else None
    bold, thickness = False, 0
    for child in (font.children or ()) if font is not None else ():
        if child.kind == "atom" and child.text == "bold":
            bold = True
        elif child.kind == "list" and child.head == "bold":
            bold = not (len(child.atoms) > 1 and child.atoms[1].text == "no")
        elif child.kind == "list" and child.head == "thickness" and len(child.atoms) > 1:
            thickness = _connectivity.iu(child.atoms[1].text)
    size = font.find("size") if font is not None else None
    if size is None or len(size.atoms) < 3:
        return _DEFAULT_TEXT_SIZE, _DEFAULT_TEXT_SIZE, bold, thickness
    return (
        _connectivity.iu(size.atoms[2].text),
        _connectivity.iu(size.atoms[1].text),
        bold,
        thickness,
    )


def _pin_span(pin: _SheetPin, ratio: float) -> tuple[int, int]:
    """The pin's box along its edge: text height plus pen width plus the text offset, centred
    on the pin (SCH_HIERLABEL::GetBodyBoundingBox, sch_label.cpp:2155-2205 at 9.0.8). The pen
    is the font's thickness above 1 IU, else width/8 (bold width/5), clamped to a quarter of
    the smaller size (EDA_TEXT::GetEffectiveTextPenWidth, eda_text.cpp:458-476)."""
    width, height, bold, thickness = _font(pin.node)
    pen = thickness if thickness > 1 else _cst.kiround(width / (5.0 if bold else 8.0))
    pen = min(pen, _cst.kiround(min(width, height) * 0.25))
    extent = height + pen + _cst.kiround(ratio * height)
    low = (pin.y if pin.side in ("left", "right") else pin.x) - extent // 2
    return low, low + extent


def _bump(value: int, direction: int) -> int:
    """bumpToNextGrid: out to the next 50 mil line (sch_sheet.cpp:525-544 at 9.0.8)."""
    base = int(value / _GRID)  # C integer division truncates toward zero
    if direction > 0:
        return (base + 1) * _GRID
    return base * _GRID if abs(value - base * _GRID) > 0 else (base - 1) * _GRID


def _min_extent(box: _Box, pins: list[_SheetPin], ratio: float, vertical: bool) -> int:
    """GetMinHeight( true ) when vertical, else GetMinWidth( true ): the extent the pins along
    those edges need, at least 150 mils high or 500 mils wide (sch_sheet.cpp:547-612 at 9.0.8)."""
    lo, hi = (box.y + box.h, box.y) if vertical else (box.x + box.w, box.x)
    sides = ("left", "right") if vertical else ("top", "bottom")
    for pin in pins:
        if pin.side in sides:
            span = _pin_span(pin, ratio)
            lo, hi = min(lo, span[0]), max(hi, span[1])
    lo, hi = _bump(lo, -1), _bump(hi, 1)
    origin = box.y if vertical else box.x
    need = 0 if lo >= hi else hi - origin
    return max(need, _MIN_SHEET_HEIGHT if vertical else _MIN_SHEET_WIDTH)


def _drawing_settings(schematic_path: str) -> tuple[int, float]:
    """(default line width in internal units, text offset ratio) of the schematic's project:
    the .kicad_pro named after it, else the only one in its folder, else 6 mils and 0.15."""
    path = Path(schematic_path)
    pro = path.with_suffix(".kicad_pro")
    if not pro.is_file():
        pros = list(path.parent.glob("*.kicad_pro"))
        pro = pros[0] if len(pros) == 1 else pro
    pen, ratio = _DEFAULT_PEN, _DEFAULT_TEXT_OFFSET_RATIO
    try:
        drawing = json.loads(pro.read_bytes())["schematic"]["drawing"]
    except (OSError, ValueError, KeyError, TypeError):
        return pen, ratio
    if not isinstance(drawing, dict):
        return pen, ratio
    width, offset = drawing.get("default_line_thickness"), drawing.get("text_offset_ratio")
    if isinstance(width, (int, float)) and not isinstance(width, bool) and width > 0:
        pen = _cst.kiround(width * _MIL)
    if isinstance(offset, (int, float)) and not isinstance(offset, bool) and 0 <= offset <= 2:
        ratio = float(offset)
    return pen, ratio


def _set_justify(node, *words: str) -> bool:
    """Make the node's (justify ...) say ``words``, adding it when it has none. True if it
    changed anything."""
    effects = node.find("effects")
    if effects is None:
        return False
    current = effects.find("justify")
    if current is not None and [a.text for a in current.atoms[1:]] == list(words):
        return False
    new = _cst.parse(("(justify " + " ".join(words) + ")").encode()).lists[0]
    if current is not None:
        new.sep = current.sep
        effects.children[effects.children.index(current)] = new
    else:
        font = effects.find("font")
        if font is None:
            effects.append_child(new, b" ")
        else:
            effects.insert_after(font, new)
    return True


def _set_xy(node, x: int, y: int) -> bool:
    """Set the x and y of a node's (at) or (xy) list, leaving the text alone when the value
    already reads the same. True if it changed anything."""
    at = node if node.head in ("xy", "at") else node.find("at")
    changed = False
    for atom, value in zip(at.atoms[1:3], (x, y), strict=True):
        if _connectivity.iu(atom.text) != value:
            atom.set_text(_connectivity.mm(value))
            changed = True
    return changed


def _autoplaced(sheet) -> bool:
    """Whether a sheet block carries ``(fields_autoplaced)`` and it is not set to ``no``."""
    flag = sheet.find("fields_autoplaced")
    return flag is not None and not (len(flag.atoms) > 1 and flag.atoms[1].text == "no")


def _autoplace_fields(sheet, default_pen: int) -> None:
    """Put the Sheetname above the sheet and the Sheetfile below it, or beside it when every
    pin is on the top or bottom edge: SCH_SHEET::AutoplaceFields (sch_sheet.cpp:665-706 at
    9.0.8). The pen is the sheet's border width, else the project's default line width."""
    box = _sheet_box(sheet)
    stroke = sheet.find("stroke")
    width = stroke.find("width") if stroke is not None else None
    border = _connectivity.iu(width.atoms[1].text) if width is not None else 0
    pen = border if border > 0 else default_pen
    sides = [pin.side for pin in _loaded_pins(sheet, box)]
    vertical = bool(sides) and all(side in ("top", "bottom") for side in sides)
    margin = _cst.kiround(pen / 2.0) + 4
    props = {p.atoms[1].text.lower(): p for p in sheet.find_all("property")}
    name = props.get("sheetname") or props["sheet name"]
    file = props.get("sheetfile") or props["sheet file"]
    name_margin = margin + _cst.kiround(max(_font(name)[:2]) * 0.5)
    file_margin = margin + _cst.kiround(max(_font(file)[:2]) * 0.4)
    if vertical:
        spots = ((box.x - name_margin, box.y + box.h), (box.x + box.w + file_margin, box.y + box.h))
    else:
        spots = ((box.x, box.y - name_margin), (box.x, box.y + box.h + file_margin))
    for prop, (x, y), words in zip(
        (name, file), spots, (("left", "bottom"), ("left", "top")), strict=True
    ):
        _set_xy(prop, x, y)
        at = prop.find("at")
        angle = "90" if vertical else "0"
        if at.atoms[3].text != angle:
            at.atoms[3].set_text(angle)
        _set_justify(prop, *words)


# ---------------------------------------------------------------------------------------------
# Where a sheet's instances live
# ---------------------------------------------------------------------------------------------


def _file_uuid(path: Path) -> str:
    return _node_uuid(_cst.parse(_read_kicad_bytes(path, "schematic")).lists[0])


def _parent_paths(parent: Path, project_path: str) -> tuple[list[str], list[Path]]:
    """The instance paths of a schematic in its project's hierarchy, and the project's
    top-level sheets. KiCad reads the page of a sheet at its PARENT's path, so these are the
    paths a new sheet's instance entries go under. A schematic no sheet of the project uses is
    its own root: /<its uuid>.
    """
    root = Path(project_path).with_suffix(".kicad_sch") if project_path else None
    placement = _connectivity.place(parent, root if root is not None and root.is_file() else None)
    if placement.kind == "sheet" and placement.project is not None:
        tops = _connectivity._top_level_sheets(placement.project)
        paths = []
        for top in tops:
            chains, _unread = _connectivity._instances(top, parent)
            paths += ["/" + "/".join((_file_uuid(top), *chain)) for chain in chains]
        return paths, tops
    top = placement.root if placement.kind == "root" and placement.root is not None else parent
    return [f"/{_file_uuid(top)}"], [top]


def _used_pages(tops: list[Path], skip: Path | None = None) -> set[str]:
    """The page labels written in the hierarchy under the top-level sheets: each sheet block's
    entry at the path of the sheet that holds it, and each top sheet's own page (1 when it names
    none). The blocks of the file ``skip`` are left out, for a caller that is about to rewrite
    them."""
    pages: set[str] = set()

    def read(path: Path):
        try:
            if not _sheet_file_nonempty(path):
                return None
            return _cst.parse(path.read_bytes()).lists[0]
        except (OSError, SyntaxError, IndexError):
            return None

    stack: list[tuple[Path, str, tuple[Path, ...]]] = []
    for top in tops:
        root = read(top)
        own = None
        if root is not None:
            table = root.find("sheet_instances")
            for entry in table.find_all("path") if table is not None else []:
                page = entry.find("page")
                if entry.atoms[1].text == "/" and page is not None:
                    own = page.atoms[1].text
            stack.append((top, f"/{_node_uuid(root)}", (_connectivity._real(top),)))
        pages.add(own if own is not None else "1")
    entered = 0
    while stack and entered < _connectivity._MAX_INSTANCES:
        path, live, chain = stack.pop()
        entered += 1
        root = read(path)
        skipped = skip is not None and _connectivity._real(path) == _connectivity._real(skip)
        for block in root.find_all("sheet") if root is not None else []:
            for entry in _entries_at(block, live) if not skipped else ():
                pages.add(entry.find("page").atoms[1].text)
            name = _sheet_file_cst(block)
            if not name:
                continue
            child = Path(os.path.normpath(os.path.join(path.parent, name.replace("\\", "/"))))
            if _connectivity._real(child) not in chain:
                stack.append(
                    (child, f"{live}/{_node_uuid(block)}", (*chain, _connectivity._real(child)))
                )
    return pages


def _entries_at(block, parent_path: str) -> list:
    """The ``(path ...)`` entries of a sheet block's instances that sit at ``parent_path`` and
    carry a page, every project: where KiCad reads the sheet's page."""
    instances = block.find("instances")
    return [
        entry
        for pj in (instances.find_all("project") if instances is not None else [])
        for entry in pj.find_all("path")
        if entry.atoms[1].text == parent_path and entry.find("page") is not None
    ]


def _page_key(label: str):
    """SCH_SHEET::ComparePageNum as a sort key (sch_sheet.cpp:1609-1642 at 9.0.8): whole numbers
    first and numerically, then the other labels in natural string order."""
    if label.isascii() and label.isdigit():
        return (0, int(label), ())
    return (1, 0, tuple(int(t) if i % 2 else t for i, t in enumerate(re.split(r"(\d+)", label))))


def _set_sheet_instances(sheet, project_name: str, live_paths: list[str], taken: set[int]) -> None:
    """Give a sheet block one instance entry for each place its parent is used, at the parent's
    path (where KiCad reads a sheet's page), each with the lowest page number not in ``taken``,
    which grows by the numbers handed out."""
    node = _SHEET_TPL.find("instances").copy()
    project_node = node.find("project")
    project_node.atoms[1].set_text(project_name)
    first = project_node.find("path")
    entries = [first] + [first.copy() for _ in live_paths[1:]]
    for k, (entry, live) in enumerate(zip(entries, live_paths, strict=True)):
        page = min(set(range(1, len(taken) + 2)) - taken)
        taken.add(page)
        entry.atoms[1].set_text(live)
        entry.find("page").atoms[1].set_text(str(page))
        if k:
            project_node.insert_after(entries[k - 1], entry)
    old = sheet.find("instances")
    if old is None:
        sheet.append_child(node, b"\n\t\t")
    else:
        node.sep = old.sep
        sheet.children[sheet.children.index(old)] = node


def _add_symbol_instances(sym, project_name: str, paths: list[str]) -> None:
    """Give a placed symbol an instance entry at each of ``paths`` it lacks. The entry is a
    copy of the first one it has, so its reference and unit carry over; a symbol
    with none gets one made from its Reference property and its unit."""
    instances = sym.find("instances")
    projects = instances.find_all("project") if instances is not None else []
    entries = [entry for pj in projects for entry in pj.find_all("path")]
    held = {entry.atoms[1].text for entry in entries}
    for path in paths:
        if path in held:
            continue
        held.add(path)
        if entries:
            entry = entries[0].copy()
        else:
            entry = _SYM_INSTANCES_TPL.find("project").find("path").copy()
            entry.find("reference").atoms[1].set_text(_sym_property_cst(sym, "Reference") or "?")
            entry.find("unit").atoms[1].set_text(str(_connectivity._sym_unit_cst(sym)))
        entry.atoms[1].set_text(path)
        block = next((pj for pj in projects if pj.atoms[1].text == project_name), None)
        if block is not None:
            block.insert_after(block.find_all("path")[-1], entry)
            continue
        block = _SYM_INSTANCES_TPL.find("project").copy()
        block.atoms[1].set_text(project_name)
        block.children[block.children.index(block.find("path"))] = entry
        if instances is None:
            instances = _SYM_INSTANCES_TPL.copy()
            instances.children[instances.children.index(instances.find("project"))] = block
            sym.insert_after(sym.lists[-1], instances)
        elif projects:
            instances.insert_after(projects[-1], block)
        else:
            instances.append_child(block, b"\n\t\t")
        projects.append(block)


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

    Creates the sheet block in the parent, with its pins on the left edge, and the
    corresponding hierarchical labels in the child schematic. The child schematic must
    already exist (create it with create_schematic first).

    Everything is checked before the first write, so a refusal changes neither file: a pin
    direction that is not one of the five below, an empty pin name, a child that is no
    schematic, and a child that is the parent or has the parent somewhere below it.

    The sheet's page is the lowest number no sheet of the hierarchy uses. It is written at
    the instance path of the parent (the sheet that holds the block), which is where KiCad
    reads a sheet's page; give project_path when the parent is itself a sub-sheet, so that
    path can be found. Symbols already in the child get an instance entry at the new
    sheet's path.

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
    for i, pin_def in enumerate(pins):
        if pin_def["direction"] not in _VALID_LABEL_SHAPES:
            raise ToolError(
                f"Sheet pin {i} ('{pin_def['name']}') has direction '{pin_def['direction']}'."
                f" Use: {', '.join(sorted(_VALID_LABEL_SHAPES))}. Nothing was changed."
            )
        if not pin_def["name"]:
            raise ToolError(f"Sheet pin {i} has an empty name. Nothing was changed.")
    child_path = Path(sheet_file)
    if not child_path.exists():
        raise ToolError(f"{child_path} does not exist. Create it with create_schematic first.")

    parent_path = Path(parent_schematic_path)
    parent_tree, parent_root, *_ = _open_sch_cst(parent_schematic_path)
    child_tree, child_root, *_ = _open_sch_cst(sheet_file)
    inside, _unread = _connectivity._instances(child_path, parent_path)
    if inside:
        raise ToolError(
            f"{child_path.name} is {parent_path.name} or has it somewhere below, so a sheet of"
            f" it in {parent_path.name} would contain itself. Nothing was changed."
        )
    root_sch = Path(project_path).with_suffix(".kicad_sch") if project_path else None
    if root_sch is not None and root_sch.exists():
        _open_sch_cst(str(root_sch))  # a root that is no schematic is refused before any write
    parent_live, tops = _parent_paths(parent_path, project_path)
    pages_used = {int(page) for page in _used_pages(tops) if page.isascii() and page.isdigit()}

    x, y = _snap_grid(x), _snap_grid(y)
    x_iu, y_iu = _iu(x), _iu(y)
    sheet_height = max(101600, (len(pins) + 1) * _PIN_PITCH)

    # Build sheet block
    sheet = _SHEET_TPL.copy()
    _fill_at(sheet, x, y, iu=SCH_IU_PER_MM)
    size = sheet.find("size")
    size.atoms[1].set_text(_connectivity.mm(254000))
    size.atoms[2].set_text(_connectivity.mm(sheet_height))
    sheet_uuid = _gen_uuid()
    sheet.find("uuid").atoms[1].set_text(sheet_uuid)
    sheet_props = {pr.atoms[1].text: pr for pr in sheet.find_all("property")}
    sheet_props["Sheetname"].atoms[2].set_text(sheet_name)
    sheet_props["Sheetfile"].atoms[2].set_text(child_path.name)

    # Pins on the sheet block (left edge) plus stubs and labels in the parent
    inst_anchor = sheet.find("instances")
    stub_end = x_iu - _PIN_PITCH
    for i, pin_def in enumerate(pins):
        pin_y = y_iu + (i + 1) * _PIN_PITCH
        pin = _SHEET_PIN_TPL.copy()
        pin.atoms[1].set_text(pin_def["name"])
        pin.atoms[2].set_text(pin_def["direction"])
        _set_xy(pin, x_iu, pin_y)
        pin.find("uuid").atoms[1].set_text(_gen_uuid())
        sheet.insert_before(inst_anchor, pin)

        # Wire stub going LEFT from pin, with a net label at its end
        _splice_wire(
            parent_root, x, pin_y / SCH_IU_PER_MM, stub_end / SCH_IU_PER_MM, pin_y / SCH_IU_PER_MM
        )
        label = _LABEL_TPL.copy()
        label.atoms[1].set_text(pin_def["name"])
        _set_xy(label, stub_end, pin_y)
        _label_spin(label, 180)
        label.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(parent_root, "label", label)
    _autoplace_fields(sheet, _DEFAULT_PEN)

    # One instance entry per place the parent is used, each with a page of its own
    project_name = Path(project_path).stem if project_path else parent_path.stem
    _set_sheet_instances(sheet, project_name, parent_live, pages_used)
    _splice_sch_node(parent_root, "sheet", sheet)

    # Add matching hierarchical labels to child schematic
    label_x = _snap_grid(25.4)
    for i, pin_def in enumerate(pins):
        label_y = _snap_grid(25.4 + i * 5.08)
        hl = _HLABEL_TPL.copy()
        hl.atoms[1].set_text(pin_def["name"])
        hl.find("shape").atoms[1].set_text(pin_def["direction"])
        _fill_at(hl, label_x, label_y, iu=SCH_IU_PER_MM)
        _label_spin(hl, 180)
        hl.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(child_root, "hierarchical_label", hl)

        # Wire stub going RIGHT from label, with a net label at its end
        stub_end_x = _snap_grid(label_x + 2.54)
        _splice_wire(child_root, label_x, label_y, stub_end_x, label_y)
        lab = _LABEL_TPL.copy()
        lab.atoms[1].set_text(pin_def["name"])
        _fill_at(lab, stub_end_x, label_y, iu=SCH_IU_PER_MM)
        _label_spin(lab, 0)
        lab.find("uuid").atoms[1].set_text(_gen_uuid())
        _splice_sch_node(child_root, "label", lab)

    # The child's symbols are read at the new sheet's path, so each gets an entry there
    new_paths = [f"{live}/{sheet_uuid}" for live in parent_live]
    root_entries: list[tuple[str, str, int, str, str]] = []
    has_root = bool(project_path) and Path(project_path).with_suffix(".kicad_sch").exists()
    for sym in child_root.find_all("symbol"):
        _add_symbol_instances(sym, project_name, new_paths)
        if has_root:
            root_entries.append(
                (
                    _node_uuid(sym),
                    _sym_property_cst(sym, "Reference") or "?",
                    _connectivity._sym_unit_cst(sym),
                    _sym_property_cst(sym, "Value") or "",
                    _sym_property_cst(sym, "Footprint") or "",
                )
            )

    # Child, then parent, then the root's table. The root's symbol_instances is an index into
    # the child, so it goes last: a write that fails leaves it pointing at nothing new.
    _atomic_write(sheet_file, _cst.serialize(child_tree))
    _atomic_write(parent_schematic_path, _cst.serialize(parent_tree))
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


def _resize_sheet(root, sheet, old: _Box, new: _Box, pins: list[_SheetPin], pen: int):
    """Resize a sheet as SCH_SHEET::Resize does (sch_sheet.cpp:1023-1037 at 9.0.8): the size,
    the autoplaced fields, every pin kept on its own edge with the justify for it. Then each
    wire or bus end and each no-connect that sat on a pin that moved goes to its new place.
    Returns (pins moved, wire and bus ends moved, no-connects moved)."""
    size = sheet.find("size")
    if new.w != old.w:
        size.atoms[1].set_text(_connectivity.mm(new.w))
    if new.h != old.h:
        size.atoms[2].set_text(_connectivity.mm(new.h))
    if _autoplaced(sheet):
        _autoplace_fields(sheet, pen)
    moved: dict[tuple[int, int], tuple[int, int]] = {}
    for pin in pins:
        x, y = _constrain(new, pin.side, pin.x, pin.y)
        if (x, y) != (pin.x, pin.y):
            moved[(pin.x, pin.y)] = (x, y)
        _set_xy(pin.node, x, y)
        _set_justify(pin.node, _PIN_JUSTIFY[pin.side])
    ends = 0
    for wire in [*root.find_all("wire"), *root.find_all("bus")]:
        xys = _line_xys(wire)
        if xys is None:
            continue
        for xy in xys:
            if _xy_iu(xy) in moved:
                _set_xy(xy, *moved[_xy_iu(xy)])
                ends += 1
                break
    flags = 0
    for flag in root.find_all("no_connect"):
        if _xy_iu(flag.find("at")) in moved:
            _set_xy(flag, *moved[_xy_iu(flag.find("at"))])
            flags += 1
    return len(moved), ends, flags


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

    A new width or height keeps each pin on its edge and moves it with the edge, places the
    name and file fields of an autoplaced sheet again, and moves a wire, bus end or
    no-connect that sat on a moved pin with it, so the sheet stays connected. A size smaller
    than the sheet's pins need is refused, naming the minimum, and a size equal to the
    current one changes nothing.

    Args:
        sheet_uuid: UUID of the sheet to modify (from list_schematic_sheets)
        schematic_path: Path to parent .kicad_sch. Optional; omit to use the configured default.
        sheet_name: New display name (empty = keep)
        file_name: New file path (empty = keep)
        width: New width in mm (None = keep)
        height: New height in mm (None = keep)
    """
    for label, value in (("width", width), ("height", height)):
        if value is not None and not value > 0:
            raise ToolError(f"The sheet {label} must be a positive number of mm, not {value}.")
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sheet_cst(root, sheet_uuid)
    props = {pr.atoms[1].text: pr for pr in target.find_all("property")}
    changes = []
    changed = False
    if sheet_name:
        changed |= props["Sheetname"].atoms[2].text != sheet_name
        props["Sheetname"].atoms[2].set_text(sheet_name)
        changes.append(f"name='{sheet_name}'")
    if file_name:
        changed |= props["Sheetfile"].atoms[2].text != file_name
        props["Sheetfile"].atoms[2].set_text(file_name)
        changes.append(f"file='{file_name}'")
    moved = ""
    if width is not None or height is not None:
        old = _sheet_box(target)
        new = _Box(
            old.x,
            old.y,
            _iu(_snap(width, SCH_IU_PER_MM)) if width is not None else old.w,
            _iu(_snap(height, SCH_IU_PER_MM)) if height is not None else old.h,
        )
        if width is not None:
            changes.append(f"width={_snap(width, SCH_IU_PER_MM)}")
        if height is not None:
            changes.append(f"height={_snap(height, SCH_IU_PER_MM)}")
        if new != old:
            pen, ratio = _drawing_settings(schematic_path)
            pins = _loaded_pins(target, old)
            for label, size, was, vertical in (
                ("width", new.w, old.w, False),
                ("height", new.h, old.h, True),
            ):
                need = _min_extent(old, pins, ratio, vertical)
                if size != was and size < need:
                    raise ToolError(
                        f"A {label} of {_connectivity.mm(size)} mm is less than the"
                        f" {_connectivity.mm(need)} mm this sheet's pins need. Nothing was"
                        " changed."
                    )
            n_pins, n_ends, n_flags = _resize_sheet(root, target, old, new, pins, pen)
            changed = True
            moved = (
                f" (moved {n_pins} pin(s), {n_ends} wire or bus end(s) and {n_flags} no-connect(s))"
            )
    if changed:
        _atomic_write(schematic_path, _cst.serialize(tree))
    return f"Modified sheet: {', '.join(changes)}{moved}"


@mcp.tool(annotations=_ADDITIVE)
def add_sheet_pin(
    sheet_uuid: str,
    pin_name: str,
    connection_type: str,
    schematic_path: str = SCH_PATH,
    side: Literal["left", "right"] = "left",
) -> str:
    """Add a pin to an existing hierarchical sheet block.

    The pin goes on the chosen edge at the first slot, counting down from the sheet's top
    edge in steps of 2.54 mm, that has no pin within 2.54 mm and lies inside the edge (this
    layout is the tool's own, not KiCad's). When the edge has no such slot the call is
    refused, writing nothing, and the message gives the height that takes one more pin
    (set it with modify_hierarchical_sheet). A direction that is not one of the five below,
    an empty name and a side other than left or right are refused too.

    Args:
        sheet_uuid: UUID of the sheet
        pin_name: Pin name (should match a hierarchical label in the child schematic)
        connection_type: input, output, bidirectional, tri_state, passive
        schematic_path: Path to parent .kicad_sch. Optional; omit to use the configured default.
        side: Which sheet edge to place pin on (left or right)
    """
    if connection_type not in _VALID_LABEL_SHAPES:
        raise ToolError(
            f"Invalid connection_type '{connection_type}'. Use: "
            f"{', '.join(sorted(_VALID_LABEL_SHAPES))}"
        )
    if side not in ("left", "right"):
        raise ToolError(f"Invalid side '{side}'. Use: left, right")
    if not pin_name:
        raise ToolError("A sheet pin needs a name.")
    tree, root, *_ = _open_sch_cst(schematic_path)
    target = _find_sheet_cst(root, sheet_uuid)
    box = _sheet_box(target)
    pins = _loaded_pins(target, box)
    taken = [pin.y for pin in pins if pin.side == side]
    k = 1
    while any(abs(box.y + k * _PIN_PITCH - y) < _PIN_PITCH for y in taken):
        k += 1
    pin_y = box.y + k * _PIN_PITCH
    if pin_y >= box.y + box.h:
        raise ToolError(
            f"The {side} edge of this sheet has no free slot for another pin: it holds"
            f" {len(taken)} in its {_connectivity.mm(box.h)} mm height. Give the sheet a"
            f" height of {_connectivity.mm((k + 1) * _PIN_PITCH)} mm or more with"
            " modify_hierarchical_sheet, then add the pin."
        )
    pin_x = box.x + (box.w if side == "right" else 0)
    pin = _SHEET_PIN_TPL.copy()
    pin.atoms[1].set_text(pin_name)
    pin.atoms[2].set_text(connection_type)
    _set_xy(pin, pin_x, pin_y)
    pin.find("at").atoms[3].set_text(str(_ANGLE_OF_SIDE[side]))
    pin.find("uuid").atoms[1].set_text(_gen_uuid())
    _set_justify(pin, _PIN_JUSTIFY[side])
    existing = target.find_all("pin")
    if existing:
        target.insert_after(existing[-1], pin)
    else:
        target.insert_after(target.find_all("property")[-1], pin)
    _atomic_write(schematic_path, _cst.serialize(tree))
    return (
        f"Added sheet pin '{pin_name}' ({connection_type}) on the {side} edge at "
        f"({_connectivity.mm(pin_x)}, {_connectivity.mm(pin_y)})"
    )


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
    folder. A file that no sheet of its project uses is a root. For a sub-sheet,
    root_path is the top-level sheet of its project that holds it.

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
    """Put hierarchical sheets in the page order given: sheets listed come first, in that order,
    then the others in the order they have now.

    KiCad orders sheets by their page numbers, not by where their blocks sit in the file, so
    this rewrites the page numbers and leaves the blocks where they are. Call it on the root
    schematic: a sheet's page is stored at its parent's path, and a sub-sheet's blocks are
    refused. The numbers already in use are given back in the new order, so custom labels
    such as "Intro" stay; when they repeat, or a deeper sheet uses one, the sheets are numbered
    from the lowest positive whole number no other sheet holds. A sheet whose instance entry
    was written at its own path, as earlier versions of the tools did, has it moved to the path
    KiCad reads. A root older than format 20221110, which keeps every page in its own
    sheet_instances table, is refused.

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
    # sheet_map keeps only the last block per UUID, so two blocks sharing a listed UUID would
    # lose one of them even when page_order itself has no repeats.
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
    # Where each block's page is read: the entry at this file's path, or failing that one an
    # earlier version wrote at the block's own path (moved to this file's path below).
    here = f"/{_node_uuid(root)}"
    if int(_connectivity._child_text(root, "version", "0")) < 20221110:
        raise ToolError(
            f"{name} is in a format older than 20221110, which keeps its sheet pages in a"
            " table this tool does not rewrite. Open and save the project in KiCad first."
            f" {name} was not changed."
        )
    pages: dict[int, list] = {}
    moved: list = []
    for s in sheets:
        entries = _entries_at(s, here)
        if not entries:
            entries = _entries_at(s, f"{here}/{_node_uuid(s)}")
            moved += entries
        if not entries:
            raise ToolError(
                f"Sheet '{_sheet_name_cst(s)}' has no page at the path of {name}, so this is not"
                " the root schematic of its hierarchy (a sheet's page is stored at its parent's"
                f" path). Call reorder_sheet_pages on the root schematic. {name} was not"
                " changed."
            )
        pages[id(s)] = entries

    def label(s) -> str:
        return pages[id(s)][0].find("page").atoms[1].text

    def position(s):
        box = _sheet_box(s)
        return (box.x, box.y, _node_uuid(s))

    current = sorted(sheets, key=lambda s: (_page_key(label(s)), position(s)))
    listed = [sheet_map[u] for u in page_order]
    new_order = listed + [s for s in current if s not in listed]
    held = [label(s) for s in current]
    deeper = _used_pages([Path(schematic_path)], skip=Path(schematic_path))
    # Compared by key, not by text: KiCad reads "01" and "1" as one page, which it cannot order.
    held_keys = {_page_key(page) for page in held}
    if len(held_keys) == len(held) and not held_keys & {_page_key(page) for page in deeper}:
        new_labels = sorted(held, key=_page_key)
    else:
        taken, new_labels = {int(p) for p in deeper if p.isascii() and p.isdigit()}, []
        for _ in new_order:
            page = next(n for n in range(1, len(taken) + len(new_order) + 2) if n not in taken)
            taken.add(page)
            new_labels.append(str(page))
    changed = False
    for s, new in zip(new_order, new_labels, strict=True):
        for entry in pages[id(s)]:
            if entry in moved:
                entry.atoms[1].set_text(here)
                changed = True
            page = entry.find("page").atoms[1]
            if page.text != new:
                page.set_text(new)
                changed = True
    if changed:
        _atomic_write(schematic_path, _cst.serialize(tree))
    numbering = ", ".join(f"{_sheet_name_cst(s)} = {n}" for s, n in zip(new_order, new_labels))
    return f"Reordered {len(page_order)} sheets. Pages now: {numbering}"


#: The items of a schematic file whose own uuid a copy of the file regenerates.
_COPY_UUID_ITEMS = frozenset(
    {
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
)


def _references_in_use(parent_path: Path, parent_root) -> set[tuple[str, int]]:
    """(prefix, number) of every reference the hierarchy of ``parent_path`` uses, each read at
    the live paths of the sheet that holds the symbol. When the project is not found, every
    reference a symbol could carry counts."""
    here = _connectivity.sheet_facts(parent_root)
    hier = _connectivity.hierarchy(parent_path, here)
    if hier.error is not None:
        raise ToolError(f"{hier.error.text} Nothing was changed.")
    found: set[str] = set()
    files = [(here.records, hier.live)]
    for other in hier.others:
        try:
            facts = other.facts or _connectivity._file_facts(other.data or b"")
        except Exception:  # noqa: BLE001 - whatever stops the read leaves the sheet unknown
            raise ToolError(f"{other.name} could not be read. Nothing was changed.") from None
        files.append((facts.records, other.paths))
    for records, paths in files:
        for rec in records:
            found |= {rec.at(p)[0] for p in paths} if paths else set(rec.every_ref())
    numbered = (re.fullmatch(r"(.*?)(\d+)", ref) for ref in found)
    return {(m.group(1), int(m.group(2))) for m in numbered if m}


def _renumber_copy(copy_root, src_live: str, new_paths: list[str], project_name: str, used):
    """Give each symbol of a copied sheet file one instance entry per new path, with the first
    free number of its prefix for a new reference (units of one part sharing one) and the unit
    it has at ``src_live``; its Reference property says the first. A reference without a number
    stays as it is. Returns the new references in order."""
    symbols = copy_root.find_all("symbol")
    records = _connectivity.sheet_facts(copy_root).records

    def spot(sym):
        at = sym.find("at")
        return (_connectivity.iu(at.atoms[1].text), _connectivity.iu(at.atoms[2].text))

    order = sorted(range(len(symbols)), key=lambda i: spot(symbols[i]))
    new_refs: dict[tuple[str, str], str] = {}  # (path, old reference) -> new reference
    for i in order:
        old = records[i].at(src_live)[0]
        for path in new_paths:
            if (path, old) in new_refs:
                continue
            m = re.fullmatch(r"(.*?)(\d+)", old)
            if m is None:
                new_refs[(path, old)] = old
                continue
            prefix = m.group(1)
            number = next(n for n in range(1, len(used) + 2) if (prefix, n) not in used)
            used.add((prefix, number))
            new_refs[(path, old)] = f"{prefix}{number}"
    for sym, rec in zip(symbols, records, strict=True):
        old, unit = rec.at(src_live)
        node = _SYM_INSTANCES_TPL.copy()
        project_node = node.find("project")
        project_node.atoms[1].set_text(project_name)
        first = project_node.find("path")
        entries = [first] + [first.copy() for _ in new_paths[1:]]
        for k, (entry, path) in enumerate(zip(entries, new_paths, strict=True)):
            entry.atoms[1].set_text(path)
            entry.find("reference").atoms[1].set_text(new_refs[(path, old)])
            entry.find("unit").atoms[1].set_text(str(unit))
            if k:
                project_node.insert_after(entries[k - 1], entry)
        previous = sym.find("instances")
        if previous is None:
            sym.insert_after(sym.lists[-1], node)
        else:
            node.sep = previous.sep
            sym.children[sym.children.index(previous)] = node
        for prop in sym.find_all("property"):
            if prop.atoms[1].text == "Reference":
                prop.atoms[2].set_text(new_refs[(new_paths[0], old)])
    return list(dict.fromkeys(new_refs[(new_paths[0], records[i].at(src_live)[0])] for i in order))


@mcp.tool(annotations=_DESTRUCTIVE)
def duplicate_sheet(
    sheet_uuid: str,
    new_sheet_name: str,
    schematic_path: str = SCH_PATH,
    project_path: str = "",
    new_file_name: str = "",
) -> str:
    """Duplicate a hierarchical sheet, copying the child file with new UUIDs.

    The new block is a copy of the source block, moved to its right, with its fields placed
    again when the source's were autoplaced, and its page the lowest number no sheet uses,
    stored at the parent's path where KiCad reads it. The copied file's symbols get references
    of their own, so the two sheets are two sets of parts: each prefix takes its first free
    number across the hierarchy, in the order of the symbols' x then y position, and the
    units of one part keep one reference. A reference with no number stays as it is.

    A source file that holds sheets of its own is refused, with nothing written, because its
    sub-sheets would need new instance paths in other files. Give project_path when the
    parent is itself a sub-sheet, so its place in the hierarchy can be found.

    Args:
        sheet_uuid: UUID of the sheet to duplicate
        new_sheet_name: Display name for the new sheet
        schematic_path: Path to parent .kicad_sch. Optional; omit to use the configured default.
        project_path: Path to .kicad_pro (for hierarchy metadata)
        new_file_name: Name for the copied file (auto-generated if empty)
    """
    tree, root, *_ = _open_sch_cst(schematic_path)
    parent = Path(schematic_path)
    sch_dir = parent.parent

    source = _find_sheet_cst(root, sheet_uuid)
    source_file = _sheet_file_cst(source) or ""

    # Determine new file name
    if not new_file_name:
        base = Path(source_file).stem
        new_file_name = f"{base}_{new_sheet_name.replace(' ', '_').lower()}.kicad_sch"

    src_path = sch_dir / source_file
    dst_path = sch_dir / new_file_name
    # source_file is the document's Sheetfile. A device such as /dev/zero would stream until
    # the disk is full, and a procfs pseudo-file like /proc/self/pagemap is a regular file of
    # size 0 that streams the same way. An empty source is no schematic either, so all of them
    # are refused before any write.
    if not _sheet_file_nonempty(src_path):
        raise ToolError(
            f"Source file not found, empty, or not a regular file: {src_path}. Nothing was"
            f" copied and {parent.name} was not changed."
        )
    if dst_path.exists():
        # Called twice with the same name, this silently replaced the first
        # copy with the source sheet. Every other create in this module guards
        # (create_project, create_schematic, create_symbol_library); this one
        # did not.
        raise ToolError(f"{dst_path} already exists. Pass a different new_file_name.")
    copy_tree, copy_root, *_ = _open_sch_cst(str(src_path))
    if copy_root.find("sheet") is not None:
        raise ToolError(
            f"{src_path.name} holds sheets of its own. Duplicating it would need new instance"
            " paths for its sub-sheets in other files, which this tool does not write, so"
            f" nothing was copied and {parent.name} was not changed."
        )
    root_sch = Path(project_path).with_suffix(".kicad_sch") if project_path else None
    if root_sch is not None and root_sch.exists():
        _open_sch_cst(str(root_sch))  # a root that is no schematic is refused before any write
    parent_live, tops = _parent_paths(parent, project_path)
    project_name = Path(project_path).stem if project_path else parent.stem
    new_uuid = _gen_uuid()
    new_paths = [f"{live}/{new_uuid}" for live in parent_live]

    # The copy: fresh uuids, and its symbols numbered apart from every part already placed.
    # Pin uuids inside symbols stay as they are.
    file_uuid = copy_root.find("uuid")
    if file_uuid is not None:
        file_uuid.atoms[1].set_text(_gen_uuid())
    for item in copy_root.lists:
        if item.head in _COPY_UUID_ITEMS:
            uuid_node = item.find("uuid")
            if uuid_node is not None:
                uuid_node.atoms[1].set_text(_gen_uuid())
    new_refs = _renumber_copy(
        copy_root,
        f"{parent_live[0]}/{sheet_uuid}",
        new_paths,
        project_name,
        _references_in_use(parent, root),
    )

    # The new block: a copy of the source, to its right, with its own uuid, name and file
    box = _sheet_box(source)
    dx = box.w + 50800
    new_sheet = source.copy()
    new_sheet.find("uuid").atoms[1].set_text(new_uuid)
    for node in (new_sheet.find("at"), *(p.find("at") for p in new_sheet.find_all("property"))):
        x, y = _connectivity.iu(node.atoms[1].text), _connectivity.iu(node.atoms[2].text)
        _set_xy(node, x + dx, y)
    for pin in new_sheet.find_all("pin"):
        x, y = (
            _connectivity.iu(pin.find("at").atoms[1].text),
            _connectivity.iu(pin.find("at").atoms[2].text),
        )
        _set_xy(pin, x + dx, y)
        pin.find("uuid").atoms[1].set_text(_gen_uuid())
    for prop in new_sheet.find_all("property"):
        key = prop.atoms[1].text.lower()
        if key in ("sheetname", "sheet name"):
            prop.atoms[2].set_text(new_sheet_name)
        elif key in ("sheetfile", "sheet file"):
            prop.atoms[2].set_text(new_file_name)
    if _autoplaced(new_sheet):
        _autoplace_fields(new_sheet, _drawing_settings(schematic_path)[0])
    pages_used = {int(page) for page in _used_pages(tops) if page.isascii() and page.isdigit()}
    _set_sheet_instances(new_sheet, project_name, parent_live, pages_used)
    _splice_sch_node(root, "sheet", new_sheet)

    # The copy, then the parent. The name is claimed with an exclusive create first, because
    # the check above is long past: a second call that took the name meanwhile keeps its file,
    # and the claim makes dst_path this call's own, so a copy the parent never names can be
    # removed and the call repeated.
    try:
        dst_path.open("xb").close()
    except FileExistsError:
        raise ToolError(
            f"{dst_path} already exists. Pass a different new_file_name."
            f" {parent.name} was not changed."
        ) from None
    try:
        _atomic_write(dst_path, _cst.serialize(copy_tree))
        _atomic_write(schematic_path, _cst.serialize(tree))
    except BaseException:
        with contextlib.suppress(OSError):
            dst_path.unlink()
        raise
    return (
        f"Duplicated sheet as '{new_sheet_name}' -> {new_file_name}."
        f" New references: {', '.join(new_refs) or 'none'}"
    )


# Distinct sheet files flatten_hierarchy walks when checking an existing output_path. Past
# it the check cannot vouch for the output, so the tool refuses rather than write.
_MAX_HIERARCHY_FILES = 4096


_FLAT_COORDINATE_ITEMS = frozenset({"at", "xy", "start", "mid", "end", "center"})
#: What a sheet file holds once for the file, not as content a flat file takes from a sub-sheet.
_FLAT_FILE_LEVEL = frozenset(
    {
        "version",
        "generator",
        "generator_version",
        "uuid",
        "paper",
        "title_block",
        "lib_symbols",
        "sheet_instances",
        "symbol_instances",
        "embedded_fonts",
        "embedded_files",
        "bus_alias",
    }
)
#: The gap between two sheets laid out in the flat file, and the grid their places keep to.
_FLAT_GAP = 254000
_FLAT_GRID = 25400
_NET_ESCAPES = re.compile(
    r"\{(?:dblquote|quote|lt|gt|backslash|slash|bar|comma|colon|space|dollar|tab|return|brace)\}"
)
#: Sheet block attributes that, set against the default, apply to everything the sheet holds.
_SHEET_ATTRIBUTES = (
    ("dnp", "yes"),
    ("in_bom", "no"),
    ("on_board", "no"),
    ("exclude_from_sim", "yes"),
)


class _Instance(NamedTuple):
    """One use of a sheet file in the hierarchy."""

    file: str
    node: Any
    page: tuple[int, int]  # the page's width and height in internal units
    live: str  # its KIID path
    prefix: str  # its human path, each name net-escaped, each followed by {slash}


def _net_escape(name: str) -> str:
    """A sheet name as it reads in a net name: '/' as {slash}, CR and LF dropped
    (EscapeString CTX_NETNAME, string_utils.cpp:188-196 at 9.0.8)."""
    return name.replace("/", "{slash}").replace("\n", "").replace("\r", "")


def _bus_shaped(text: str) -> bool:
    """Whether a label or pin name might be a bus: it still holds a bracket or a brace once the
    escapes and the overbar, subscript and superscript braces are taken out. Conservative on
    purpose: an 'A[1]' that is no bus is refused too."""
    plain = _NET_ESCAPES.sub("", re.sub(r"[~^_$]\{", "", text))
    return "[" in plain or "{" in plain


def _coordinate_lists(node):
    """Every (at), (xy), (start), (mid), (end) and (center) list below ``node``."""
    stack = [node]
    while stack:
        for child in stack.pop().children or ():
            if child.kind == "list":
                if child.head in _FLAT_COORDINATE_ITEMS and len(child.atoms) >= 3:
                    yield child
                stack.append(child)


def _shift(node, dx: int, dy: int) -> None:
    for item in _coordinate_lists(node):
        _set_xy(
            item,
            _connectivity.iu(item.atoms[1].text) + dx,
            _connectivity.iu(item.atoms[2].text) + dy,
        )


def _fresh_uuids(node) -> None:
    stack = [node]
    while stack:
        for child in stack.pop().children or ():
            if child.kind == "list":
                if child.head == "uuid" and len(child.atoms) >= 2:
                    child.atoms[1].set_text(_gen_uuid())
                stack.append(child)


def _pin_signature(lib) -> tuple:
    """The part of a library symbol flatten_hierarchy compares: each pin's number, type, place
    and flags in its unit, a power_in pin's name and visibility, and the symbol's power and
    extends. Other pin names, graphics and fields are left out."""
    pins = []
    for sub in lib.find_all("symbol"):
        for pin in sub.find_all("pin"):
            parts = [sub.atoms[1].text, *(a.text for a in pin.atoms[1:])]
            for key in ("at", "length", "number"):
                part = pin.find(key)
                parts.append(tuple(a.text for a in part.atoms[1:]) if part is not None else ())
            if pin.atoms[1].text == "power_in":
                name = pin.find("name")
                parts += [_connectivity._pin_hidden(pin), name.atoms[1].text if name else ""]
            pins.append(tuple(map(str, parts)))
    flags = tuple(
        tuple(a.text for a in node.atoms) for key in ("power", "extends") if (node := lib.find(key))
    )
    return (tuple(sorted(pins)), flags)


def _flat_label(text: str, source_at):
    """A local label with ``text`` at the position of ``source_at`` (an (at) list), turned as
    that list says."""
    label = _LABEL_TPL.copy()
    label.atoms[1].set_text(text)
    _set_xy(
        label, _connectivity.iu(source_at.atoms[1].text), _connectivity.iu(source_at.atoms[2].text)
    )
    try:
        rotation = int(float(source_at.atoms[3].text)) % 360
    except (IndexError, ValueError):
        rotation = 0
    _label_spin(label, rotation if rotation in (0, 90, 180, 270) else 0)
    label.find("uuid").atoms[1].set_text(_gen_uuid())
    return label


def _keep_one_instance(sym, record, live: str, flat_path: str, project_name: str) -> None:
    """Leave a placed symbol with one instance entry, at ``flat_path``: the entry it has at
    ``live`` (with whatever it carries), else one made from the reference and unit
    ``record.at(live)`` gives."""
    previous = sym.find("instances")
    found = None
    for pj in previous.find_all("project") if previous is not None else ():
        for entry in pj.find_all("path"):
            if len(entry.atoms) > 1 and entry.atoms[1].text == live:
                found = (pj, entry)
    node = _SYM_INSTANCES_TPL.copy()
    project_node = node.find("project")
    template = project_node.find("path")
    if found is not None:
        project_node.atoms[1].set_text(found[0].atoms[1].text)
        entry = found[1].copy()
    else:
        first = previous.find("project") if previous is not None else None
        project_node.atoms[1].set_text(first.atoms[1].text if first is not None else project_name)
        entry = template.copy()
        reference, unit = record.at(live)
        entry.find("reference").atoms[1].set_text(reference)
        entry.find("unit").atoms[1].set_text(str(unit))
    entry.atoms[1].set_text(flat_path)
    project_node.children[project_node.children.index(template)] = entry
    if previous is None:
        sym.insert_after(sym.lists[-1], node)
    else:
        node.sep = previous.sep
        sym.children[sym.children.index(previous)] = node


def _splice_many(root, token: str, nodes: list) -> None:
    """_splice_sch_node for many nodes at once: after the last sibling of the token, else
    before the file tail, else at the end."""
    if not nodes:
        return
    children = root.children
    lists = [(i, c) for i, c in enumerate(children) if c.kind == "list"]
    same = [i for i, c in lists if c.head == token]
    if same:
        at, anchor = same[-1] + 1, children[same[-1]]
    else:
        tail = next((i for i, c in lists if c.head == "sheet_instances"), None)
        if tail is None:
            tail = next((i for i, c in lists if c.head == "embedded_fonts"), None)
        at = len(children) if tail is None else tail
        anchor = children[tail] if tail is not None else children[-1]
    sep = _anchor_ws(anchor.sep) or b"\n"
    for node in nodes:
        node.sep = sep
    children[at:at] = nodes


def _definitions(node) -> list:
    """The library symbols a sheet file defines."""
    libs = node.find("lib_symbols")
    return libs.find_all("symbol") if libs is not None else []


@mcp.tool(annotations=_DESTRUCTIVE)
def flatten_hierarchy(
    schematic_path: str = SCH_PATH,
    output_path: str = "",
) -> str:
    """Flatten a hierarchical schematic into a single sheet whose netlist is the hierarchy's.

    The flat file is the root schematic with each sub-sheet's content copied below it, one
    cell per use of a sheet file (a file used twice is copied twice). Each part keeps the
    reference and unit KiCad reads for it at its sheet's path. A sheet pin becomes a
    local label at its place when its sheet has a hierarchical label of its name, and nothing
    otherwise. A local or hierarchical label of a sub-sheet becomes a local label named after
    the sheet's path, so nets keep their joins and no others; net names change to those
    labels. A label whose own sheet has a global label, a power symbol or a hidden power input
    pin of its text keeps that text, so it keeps joining it. Text variables that name the
    sheet are not rewritten.

    The tool refuses, writing nothing, where the flat file could not keep the netlist: a
    sheet pin or sub-sheet label that may be a bus, a file older than format 20221002, a
    local power symbol, a bus_alias in a sub-sheet, a root label named like a global net
    that only another sheet has, a library symbol whose pins differ between two files, a
    sheet marked DNP, out of the BOM, off the board or out of simulation,
    two sheets of one name, more than 4096 sheet instances, or a sheet file that cannot be
    read. A sheet file that is missing is flattened as an empty sheet, as KiCad loads it.

    Creates a new file and does not modify the hierarchy.

    Args:
        schematic_path: Path to root .kicad_sch file. Optional; omit to use the configured default.
        output_path: Path for flattened output (defaults to *_flat.kicad_sch)
    """
    flat_tree, flat_root, *_ = _open_sch_cst(schematic_path)
    sch_dir = Path(schematic_path).parent

    if not output_path:
        stem = Path(schematic_path).stem
        output_path = str(sch_dir / f"{stem}_flat.kicad_sch")

    # The files the root's sheet blocks name, for the clash check below
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
    # Read the hierarchy: a sheet's file against its parent's folder, every use of a file as an
    # instance of its own, a file that is its own ancestor not entered (as _instances does).
    # Everything that would stop the flat file keeping the hierarchy's netlist is refused here,
    # before anything is written.
    root_path = Path(schematic_path)
    root_key = _connectivity._real(root_path)
    loaded: dict[Path, tuple | None] = {}
    missing: list[str] = []

    def load(path: Path):
        key = _connectivity._real(path)
        if key not in loaded:
            if not _sheet_file_nonempty(path):
                loaded[key] = None
            else:
                try:
                    _tree, node, page_w, page_h, _name = _open_sch_cst(str(path))
                except SyntaxError as exc:
                    raise ToolError(
                        f"{path.name} cannot be parsed ({exc}), so nothing was written."
                    ) from None
                check_version(path.name, node)
                loaded[key] = (
                    node,
                    (
                        _connectivity.iu(_num(page_w, SCH_IU_PER_MM)),
                        _connectivity.iu(_num(page_h, SCH_IU_PER_MM)),
                    ),
                )
        return loaded[key]

    def check_version(file: str, node) -> None:
        if int(_connectivity._child_text(node, "version", "0")) < _connectivity._ON_SYMBOL_VERSION:
            raise ToolError(
                f"{file} is in a format older than 20221002, which this tool does not flatten."
                " Nothing was written. Open and save the project in KiCad first."
            )

    def check_text(file: str, kind: str, text: str) -> None:
        if _bus_shaped(text):
            raise ToolError(
                f"The {kind} '{text}' in {file} may be a bus, and this tool does not rename bus"
                " names for the flat file, so nothing was written."
            )

    def check_sheet(file: str, block) -> None:
        for attribute, bad in _SHEET_ATTRIBUTES:
            node = block.find(attribute)
            if node is not None and len(node.atoms) > 1 and node.atoms[1].text == bad:
                raise ToolError(
                    f"The sheet '{_sheet_name_cst(block)}' in {file} has {attribute} {bad}, which"
                    " applies to every part on it; the flat file has no sheet to carry it, so"
                    " nothing was written."
                )

    flat_page = _page_size_cst(flat_root)
    check_version(root_path.name, flat_root)
    instances: list[_Instance] = []
    prefixes: set[str] = set()
    root_live = f"/{_node_uuid(flat_root)}"

    def walk(path: Path, node, live: str, prefix: str, chain: tuple[Path, ...]) -> None:
        for block in node.find_all("sheet"):
            check_sheet(path.name, block)
            name = _sheet_name_cst(block) or ""
            child_prefix = prefix + _net_escape(name) + "{slash}"
            if child_prefix in prefixes:
                raise ToolError(
                    f"Two sheets of {path.name} or its sub-sheets are named '{name}' at the same"
                    " place, so their nets could not be told apart in the flat file. Nothing"
                    " was written."
                )
            prefixes.add(child_prefix)
            file = _sheet_file_cst(block) or ""
            if not file:
                continue
            child = Path(os.path.normpath(os.path.join(path.parent, file.replace("\\", "/"))))
            if _connectivity._real(child) in chain:
                continue  # a sheet inside itself is not entered, as _connectivity._instances
            content = load(child)
            if content is None:
                missing.append(child.name)
                continue
            if len(instances) >= _connectivity._MAX_INSTANCES:
                raise ToolError(
                    f"The hierarchy under {root_path.name} has more than"
                    f" {_connectivity._MAX_INSTANCES} sheet instances, so nothing was written."
                )
            here = f"{live}/{_node_uuid(block)}"
            instances.append(_Instance(child.name, content[0], content[1], here, child_prefix))
            walk(child, content[0], here, child_prefix, (*chain, _connectivity._real(child)))

    walk(root_path, flat_root, root_live, "", (root_key,))

    # Library symbols: a flat file holds one definition of a name. The first is kept, unless
    # another file's differs in what the netlist reads from it.
    libs = flat_root.find("lib_symbols")
    if libs is None:
        libs = _cst.parse(b"(lib_symbols)").lists[0]
        flat_root.insert_after(flat_root.find("paper") or flat_root.find("uuid"), libs)
    defined = {
        _connectivity._lib_name(ls.atoms[1].text): _pin_signature(ls)
        for ls in libs.find_all("symbol")
    }
    shapes: dict[int, tuple] = {}  # a file used twice is compared once

    # Where each sheet goes: the root stays, the others sit in cells of one size on a grid
    # below it, so no two overlap and every offset keeps the 2.54 mm grid.
    def extent(node, page: tuple[int, int]) -> tuple[int, int, int, int]:
        xs, ys = [0, page[0]], [0, page[1]]
        for item in node.lists:
            if item.head in _FLAT_FILE_LEVEL:
                continue
            for at in _coordinate_lists(item):
                xs.append(_connectivity.iu(at.atoms[1].text))
                ys.append(_connectivity.iu(at.atoms[2].text))
        return (
            min(xs) // _FLAT_GRID * _FLAT_GRID,
            min(ys) // _FLAT_GRID * _FLAT_GRID,
            max(xs),
            max(ys),
        )

    root_box = extent(
        flat_root,
        (
            _connectivity.iu(_num(flat_page[0], SCH_IU_PER_MM)),
            _connectivity.iu(_num(flat_page[1], SCH_IU_PER_MM)),
        ),
    )
    boxes = [extent(i.node, i.page) for i in instances]
    cell_w = max((b[2] - b[0] for b in boxes), default=0) + _FLAT_GAP
    cell_h = max((b[3] - b[1] for b in boxes), default=0) + _FLAT_GAP
    cell_w, cell_h = (-(-v // _FLAT_GRID) * _FLAT_GRID for v in (cell_w, cell_h))
    columns = math.isqrt(max(len(instances) - 1, 0)) + 1
    top_y = -(-(root_box[3] + _FLAT_GAP) // _FLAT_GRID) * _FLAT_GRID

    # Build the flat file: the root's bytes with a new uuid, its parts keeping the entry they
    # have at the root's path, and every other sheet's content copied into its cell
    flat_uuid = _gen_uuid()
    uuid_node = flat_root.find("uuid")
    if uuid_node is None:
        raise ToolError(f"{root_path.name} has no uuid, so nothing was written.")
    uuid_node.atoms[1].set_text(flat_uuid)
    flat_path = f"/{flat_uuid}"
    project_name = root_path.stem
    added: dict[str, list] = {}

    # A global label or a power symbol names a net on every sheet, and a local or hierarchical
    # label of the same text on the same sheet joins it. So a label keeps its text where its own
    # sheet has such a net, and is renamed where it has none.
    def global_names(node, live: str) -> set[str]:
        names = {g.atoms[1].text for g in node.find_all("global_label") if len(g.atoms) > 1}
        libs = _connectivity.lib_index(node)
        records = _connectivity.sheet_facts(node).records
        for sym, rec in zip(node.find_all("symbol"), records, strict=True):
            lib = libs.get(_connectivity.lib_key(sym))
            if lib is None:
                continue
            value = _sym_property_cst(sym, "Value")
            if lib.find("power") is not None:
                if value:
                    names.add(value)
            elif lib.find("extends") is None:
                # a hidden power_in pin names a global net by its own name (sch_pin.cpp:323-327
                # at 9.0.8)
                style = _connectivity._sym_body_style_cst(sym)
                for sub in _connectivity._instance_units(lib, rec.at(live)[1], style):
                    for pin in sub.find_all("pin"):
                        name = pin.find("name")
                        if (
                            pin.atoms[1].text == "power_in"
                            and _connectivity._pin_hidden(pin)
                            and name is not None
                            and len(name.atoms) > 1
                        ):
                            names.add(name.atoms[1].text)
        return names

    own_globals = {(id(flat_root), root_live): global_names(flat_root, root_live)}
    for inst in instances:
        own_globals[id(inst.node), inst.live] = global_names(inst.node, inst.live)
    all_globals = set().union(*own_globals.values())

    hier_labels = {
        inst.live: {
            h.atoms[1].text for h in inst.node.find_all("hierarchical_label") if len(h.atoms) > 1
        }
        for inst in instances
    }

    def global_clash(file: str, text: str) -> ToolError:
        return ToolError(
            f"The label '{text}' in {file} would join a global net of that name on the flat"
            " file's one sheet, which the hierarchy keeps apart. Nothing was written."
        )

    def convert(file: str, node, live: str, prefix: str, dx: int, dy: int, is_root: bool) -> None:
        records = dict(
            zip(
                map(id, node.find_all("symbol")),
                _connectivity.sheet_facts(node).records,
                strict=True,
            )
        )
        lib_defs = _connectivity.lib_index(node)
        here = own_globals[id(node), live]
        for item in list(node.lists):
            head = item.head
            if head in _FLAT_FILE_LEVEL:
                continue
            if head == "sheet":
                child_prefix = prefix + _net_escape(_sheet_name_cst(item) or "") + "{slash}"
                joined = hier_labels.get(f"{live}/{_node_uuid(item)}", set())
                for pin in item.find_all("pin"):
                    check_text(file, "sheet pin", pin.atoms[1].text)
                    if pin.atoms[1].text not in joined:
                        continue  # a pin joins hierarchical labels only (connection_graph.cpp:2644)
                    if child_prefix + pin.atoms[1].text in all_globals:
                        raise global_clash(file, child_prefix + pin.atoms[1].text)
                    label = _flat_label(child_prefix + pin.atoms[1].text, pin.find("at"))
                    _shift(label, dx, dy)
                    added.setdefault("label", []).append(label)
                if is_root:
                    node.remove_child(item)
                continue
            if head in ("label", "hierarchical_label"):
                text = item.atoms[1].text
                if is_root:
                    if text in all_globals and text not in here:
                        raise global_clash(file, text)
                    if head == "label":
                        continue
                else:
                    check_text(file, "label", text)
                keeps = is_root or text in here  # joins a global net of its own sheet
                renamed = prefix + text
                if renamed in all_globals and not is_root:
                    raise global_clash(file, renamed)
                if head == "label":
                    label = item.copy()
                    label.atoms[1].set_text(text if keeps else renamed)
                    _shift(label, dx, dy)
                    _fresh_uuids(label)
                    made = [label]
                else:
                    # a hierarchical label also carries the sheet pin's net, so it keeps its
                    # own text and gets the renamed one besides
                    made = [_flat_label(text if keeps else renamed, item.find("at"))]
                    if keeps and not is_root:
                        made.append(_flat_label(renamed, item.find("at")))
                    for label in made:
                        _shift(label, dx, dy)
                added.setdefault("label", []).extend(made)
                if is_root:
                    node.remove_child(item)
                continue
            if head == "group":
                continue  # a group lists its members by uuid (sch_io_kicad_sexpr.cpp:1656 at
                # 10.0.7) and a copy gets new ones
            if head == "symbol":
                lib = lib_defs.get(_connectivity.lib_key(item))
                power = lib.find("power") if lib is not None else None
                if power is not None and any(a.text == "local" for a in power.atoms[1:]):
                    raise ToolError(
                        f"{file} places a local power symbol, whose reach the flat file's one"
                        " sheet would widen. Nothing was written."
                    )
            if is_root:
                if head == "symbol":
                    _keep_one_instance(item, records[id(item)], live, flat_path, project_name)
                continue
            copy = item.copy()
            _shift(copy, dx, dy)
            _fresh_uuids(copy)
            if head == "symbol":
                _keep_one_instance(copy, records[id(item)], live, flat_path, project_name)
            added.setdefault(head, []).append(copy)

    convert(root_path.name, flat_root, root_live, "", 0, 0, True)
    for n in flat_root.find_all("symbol_instances"):
        flat_root.remove_child(n)
    for k, (inst, box) in enumerate(zip(instances, boxes, strict=True)):
        row, column = divmod(k, columns)
        convert(
            inst.file,
            inst.node,
            inst.live,
            inst.prefix,
            column * cell_w - box[0],
            top_y + row * cell_h - box[1],
            False,
        )
        if inst.node.find("bus_alias") is not None:
            raise ToolError(
                f"{inst.file} defines a bus_alias, which names a bus the flat file would not"
                " keep apart. Nothing was written."
            )
        for ls in _definitions(inst.node):
            name = _connectivity._lib_name(ls.atoms[1].text)
            if name not in defined:
                defined[name] = shapes[id(ls)] = _pin_signature(ls)
                libs.append_child(ls.copy(), b"\n\t\t")
            elif defined[name] != shapes.setdefault(id(ls), _pin_signature(ls)):
                raise ToolError(
                    f"{inst.file} defines the library symbol '{name}' with pins that differ from"
                    " another file's, and a flat file holds one definition of a name."
                    " Nothing was written."
                )
    for head, nodes in added.items():
        _splice_many(flat_root, head, nodes)

    _atomic_write(output_path, _cst.serialize(flat_tree))

    total_components = len(flat_root.find_all("symbol"))
    note = (
        f" Sheet files not found, flattened as empty: {', '.join(dict.fromkeys(missing))}."
        if missing
        else ""
    )
    return (
        f"Flattened hierarchy to {Path(output_path).name}: {total_components} components"
        f" from {len(instances) + 1} sheets.{note}"
    )


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


# kicad-cli prints this line, coloured, once for each output it runs; its words are wxT
# literals, not translated (kicad/jobs_runner.cpp:324-328 at 9.0.8).
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
    output folder in it is relative to that folder. kicad-cli is given a copy of
    the jobset, never the file, because it rewrites or deletes a jobset it cannot
    parse. It is an error when nothing ran or a job failed, and the error carries
    the counts kicad-cli reported.

    Args:
        jobset_path: Path to .kicad_jobset file
    """
    data = _read_kicad_bytes(jobset_path, "jobset")
    jobset = Path(os.path.abspath(jobset_path))
    pro = _project_beside(jobset)
    # kicad-cli answers a jobset its own parser refuses by writing an empty jobset over the
    # file or deleting it (JSON_SETTINGS::LoadFromFile, json_settings.cpp:339-346 at 9.0.8).
    # That parser takes comments and refuses lone surrogate escapes, which Python's json does
    # the other way round, so no check of ours says what it will refuse: it gets a copy.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        copy = Path(scratch) / jobset.name
        _atomic_write(copy, data)
        result = _run_cli(
            ["jobset", "run", "--file", str(copy), str(pro)], check=False, cwd=str(pro.parent)
        )
        rewritten = not copy.is_file() or copy.read_bytes() != data
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
        why = (
            "kicad-cli replaced or deleted its copy of the jobset, which it does to one it"
            f" cannot parse. {jobset.name} itself is unchanged."
            if rewritten
            else "A jobset with no outputs, or a file that is not a jobset, runs nothing."
        )
        raise ToolError(
            f"Jobset {jobset.name} ran nothing: kicad-cli reported {reported or 'no job counts'}."
            f" {why}"
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
