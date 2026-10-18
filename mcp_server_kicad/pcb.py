"""KiCad PCB MCP Server — PCB manipulation, DRC, and export tools."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, NamedTuple

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

import mcp_server_kicad._cst as _cst
from mcp_server_kicad._cst import (
    PCB_IU_PER_MM,
    _anchor_ws,
    _angle_text,
    _fill_at,
    _num,
    _numish,
    _snap,
)
from mcp_server_kicad._freerouting import (
    check_java as _check_java,
)
from mcp_server_kicad._freerouting import (
    ensure_jar as _ensure_jar,
)
from mcp_server_kicad._freerouting import (
    export_dsn as _export_dsn,
)
from mcp_server_kicad._freerouting import (
    find_pcbnew_python as _find_pcbnew_python,
)
from mcp_server_kicad._freerouting import (
    import_ses as _import_ses,
)
from mcp_server_kicad._freerouting import (
    pcbnew_major as _pcbnew_major,
)
from mcp_server_kicad._freerouting import (
    run_freerouting as _run_freerouting,
)
from mcp_server_kicad._freerouting import (
    wx_app_prelude as _wx_app_prelude,
)
from mcp_server_kicad._netlist_import import (
    grid_slot as _grid_slot,
)
from mcp_server_kicad._netlist_import import (
    new_summary as _new_summary,
)
from mcp_server_kicad._netlist_import import (
    parse_netlist as _parse_netlist,
)
from mcp_server_kicad._netlist_import import (
    resolve_pretty as _resolve_pretty,
)
from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    FP_LIB_PATH,
    OUTPUT_DIR,
    PCB_PATH,
    SCH_PATH,
    _atomic_write,
    _chain_edge_polygon,
    _courtyard_bbox_cst,
    _ensure_dir,
    _file_meta,
    _find_on_path,
    _gen_uuid,
    _keepout_dict,
    _kicad_cli_major,
    _kicad_root,
    _kiround_for,
    _linearize_arc,
    _n180,
    _n360,
    _point_in_polygon,
    _read_kicad_bytes,
    _require_kicad_path,
    _resolve_root,
    _rotate_point_iu,
    _run_cli,
    _run_pcbnew,
    _transform_local_to_board,
    _xy,
    build_server,
    kiround,
)
from mcp_server_kicad.models import (
    AutorouteResult,
    BoardValidationResult,
    DanglingTracksResult,
    DrcResult,
    ExportResult,
    FillZonesResult,
    FootprintBoundsResult,
    GerberExportResult,
    GraphicItem,
    KeepoutZoneResult,
    LayerItem,
    Model3dExportResult,
    NetClassResult,
    NetItem,
    PcbExportResult,
    PcbFootprintItem,
    PlacementCheckResult,
    PointSpec,
    PositionExportResult,
    RemoveTracesResult,
    ThermalViasResult,
    TraceSegmentItem,
    TraceWidthResult,
    UpdatePcbResult,
    ZoneItem,
    ZoneResult,
)

mcp = build_server(
    "kicad-pcb",
    instructions=(
        "KiCad PCB manipulation, DRC analysis, and PCB export tools"
        " including Gerber, drill, 3D models, and pick-and-place.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write .kicad_pcb files directly. All PCB"
        " manipulation MUST go through these MCP tools.\n"
        "- NEVER run kicad-cli commands directly. Use the export and DRC"
        " tools provided by this server.\n"
        "- NEVER grep/search inside .kicad_pcb files. Use list_pcb_footprints,"
        " list_pcb_traces, list_pcb_nets, list_pcb_zones, list_pcb_layers,"
        " and list_pcb_graphic_items to query board contents.\n"
        "- When a tool returns an error, try different parameters or a different"
        " MCP tool. Do NOT fall back to manual file editing.\n\n"
        "QUERY PATTERN: Use per-type list tools (list_pcb_footprints,"
        " list_pcb_traces, list_pcb_nets, list_pcb_zones, list_pcb_layers,"
        " list_pcb_graphic_items).\n\n"
        "EXPORT PATTERN: export_pcb(format, pcb_path) supports formats:"
        " pdf, svg, dxf. Use export_gerbers for manufacturing output."
    ),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CST substrate (every board path; see docs/adr-cst-substrate.md)
# ---------------------------------------------------------------------------


# ponytail: unbounded dict, one parsed Doc per board path (roughly 11x file
# size retained); add eviction if a session ever touches many large boards.
# mtime_ns + size misses a same-size rewrite inside one timestamp tick; our
# writers pop their entry before mutating, and pcbnew rewrites move mtime.
_BOARD_CACHE: dict[str, tuple[int, int, _cst.Doc]] = {}


def _open_pcb_cst(pcb_path: str):
    """Parse a board into a CST for the board tools.

    Works on any format KiCad writes. Parsed trees are cached per resolved
    path while mtime and size hold (board parses are seconds at demo-board
    scale); every writer must pop its entry BEFORE mutating and never
    reinsert, so an exception between mutation and write can never leave a
    poisoned tree cached.
    """
    # Same refusal as every other opener; os.stat below would otherwise raise a
    # bare FileNotFoundError straight through to the client.
    _read_kicad_bytes(pcb_path, "board")
    key = str(Path(pcb_path).resolve())
    st = os.stat(key)
    hit = _BOARD_CACHE.get(key)
    if hit is not None and (hit[0], hit[1]) == (st.st_mtime_ns, st.st_size):
        tree = hit[2]
    else:
        tree = _cst.parse(Path(key).read_bytes())
        _BOARD_CACHE[key] = (st.st_mtime_ns, st.st_size, tree)
    root = tree.lists[0] if tree.lists else None
    if root is None or root.head != "kicad_pcb":
        _BOARD_CACHE.pop(key, None)
        raise ToolError(f"{Path(pcb_path).name} is not a KiCad PCB.")
    return tree, root, key


def _fp_prop_cst(fp, key: str) -> str:
    """ "Reference"/"Value" of a CST footprint node: property first, fp_text fallback."""
    for prop in fp.find_all("property"):
        if prop.atoms[1].text == key:
            return prop.atoms[2].text
    for t in fp.find_all("fp_text"):
        if t.atoms[1].text == key.lower():
            atom = _text_atom(t)
            return atom.text if atom is not None else "?"
    return "?"


def _text_atom(text):
    """The atom holding a footprint text's content, or None: its third, or its fourth after
    the bare ``locked`` KiCad 6 and 7 wrote after an fp_text's type, which the parser
    skips (pcb_io_kicad_sexpr_parser.cpp:3400-3405 at 9.0.8, 3917-3922 at 10.0.7); a
    quoted "locked" is content."""
    atoms = text.atoms
    k = 3 if text.head == "fp_text" and len(atoms) > 3 and atoms[2].raw == b"locked" else 2
    return atoms[k] if len(atoms) > k else None


def _fp_layer(fp) -> str:
    """A footprint node's layer, defaulting to the front copper layer."""
    layer = fp.find("layer")
    return layer.atoms[1].text if layer is not None else "F.Cu"


#: The board-level items that carry a (net ...): tracks, zones and the copper
#: shapes, the set KiCad's AllConnectedItems walks over the board itself
#: (pcbnew/board.cpp:2898-2921 at 9.0.8, 3542-3574 at 10.0.7). A shape's net is
#: written at pcb_io_kicad_sexpr.cpp:1027-1028 at 9.0.8 and 1117-1118 at 10.0.7.
#: Footprint-level zones and graphics are not in it.
_NET_ITEMS = (
    "segment",
    "arc",
    "via",
    "zone",
    "gr_line",
    "gr_rect",
    "gr_circle",
    "gr_arc",
    "gr_poly",
    "gr_curve",
)

#: What KiCad's lexer reads as a NUMBER: a sign, digits with at most one point,
#: and an exponent with digits of its own (isNumber, common/dsnlexer.cpp:495-542
#: at 9.0.8, 491-538 at 10.0.7). A quoted token is a string, digits or not
#: (dsnlexer.cpp:643-725 at 10.0.7).
_NUMBER_TOKEN = re.compile(rb"[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?")
_INTEGER_PREFIX = re.compile(rb"[-+]?[0-9]+")


def _net_code(atom) -> int | None:
    """The net number a (net ...) atom gives, or None when it gives a name.

    Decided on the atom's raw bytes, which keep the quotes: KiCad's lexer reads
    a quoted token as a string and an unquoted one as a number only when all of
    it is one, and a net reference takes the number's integer part, strtol
    (parseInt, pcb_io_kicad_sexpr_parser.h:354-363 at 9.0.8). So "5" is a name
    wherever it is written, 5 is a number, 1.5 is the number 1 and .5 is 0.
    KiCad 10 writes every net name quoted, digits or not (pcb_io_kicad_sexpr.cpp:
    2861 and OUTPUTFORMATTER::Quotes, common/richio.cpp:470-506 at 10.0.7), and
    its parser takes a quoted token for a name (pcb_io_kicad_sexpr_parser.cpp:
    296-342 at 10.0.7).
    """
    raw = atom.raw
    if raw[:1] == b'"' or not _NUMBER_TOKEN.fullmatch(raw):
        return None
    digits = _INTEGER_PREFIX.match(raw)
    return int(digits.group()) if digits else 0


def _net_rows(root) -> list[tuple[int, str]]:
    """(number, name) of each (net N "NAME") row of the board's net table.

    Only a board of KiCad 9's format or of KiCad 10's development formats has
    one. KiCad 9 binds a net number only through a row (getNetCode over the map
    parseNETINFO_ITEM fills, pcb_io_kicad_sexpr_parser.h:156-162 and
    pcb_io_kicad_sexpr_parser.cpp:2693-2721 at 9.0.8), so the rows, never the
    synthesized table of _net_table, say which numbers a KiCad 9 board can refer to.
    """
    rows = []
    for row in root.find_all("net"):
        atoms = row.atoms
        code = _net_code(atoms[1]) if len(atoms) > 1 else None
        if code is not None:
            rows.append((code, atoms[2].text if len(atoms) > 2 else ""))
    return rows


def _by_number(nets: list[tuple[int, str]]) -> dict[int, str]:
    """The name of each number of ``nets``, the first where two rows share one."""
    names: dict[int, str] = {}
    for number, name in nets:
        names.setdefault(number, name)
    return names


def _row_names(root) -> dict[int, str]:
    """The name of each numbered row of the board's table (_net_rows)."""
    return _by_number(_net_rows(root))


def _bound_names(root) -> dict[int, str]:
    """The name of the net each bare number of a (net N) binds to on this board; a
    number that finds no net is absent. A zone's bare number binds only where KiCad 10
    keeps it (_read_net_name).

    The rows of the board's table bind them: KiCad reads a number through the map the
    rows fill and finds no net for one they leave out (getNetCode and SetNetCode,
    pcb_io_kicad_sexpr_parser.h:156-162 and pcbnew/board_connected_item.cpp:87-115 at
    9.0.8).

    A board with no rows is read by the KiCad that loads its format. KiCad 9 (format
    20241229 and below) makes a net only from a row or from a zone's net_name, so no
    number finds one there (pcb_io_kicad_sexpr_parser.cpp:2713-2720 and 7387-7412 at
    9.0.8; a pad's name only checks the net its number finds, 5216-5243). KiCad 10
    makes a net where it first meets a name, numbers the nets 1, 2, ... in the order it
    makes them, and binds a number to the net with that number, the silent rebind of
    ADR-2 guardrail 5 (parseNet and the pad's branch, pcb_io_kicad_sexpr_parser.cpp:
    296-342 and 6185-6244; getNetCode, pcb_io_kicad_sexpr_parser.h:176-184;
    NETINFO_LIST::AppendNet and getFreeNetCode, pcbnew/netinfo_list.cpp:149-165 and
    266-275; all at 10.0.7). That order is the numbering _net_table derives from usage,
    so there a number reads as the net the derived table gives it. Two cases are not
    modelled: KiCad makes the nets as it parses, so a number past the nets made so far
    finds none yet, where this table, built over the whole board, finds one made later
    in the file; and a net a KiCad 9 zone's net_name makes is one a later bare number
    can find, where the rows alone give none.
    """
    rows = _row_names(root)
    if rows or _board_version(root) <= _NUMERIC_NET_VERSION_MAX:
        return rows
    return _by_number(_net_table(root))


def _zone_keeps_number(zone, net) -> bool:
    """Whether KiCad 10 keeps the number of a zone's bare ``net``: the zone is on copper
    when it reads the number and again after the zone's last child, and is no rule area.

    The zone is on copper while the last nonempty (layer ...) or (layers ...) read so far
    names a copper layer (ZONE::IsOnCopperLayer, and SetLayerSet, which ignores an empty
    set: pcbnew/zone.cpp:581-585 and 594-597). SetNetCode puts a number on net 0 for
    a zone not on copper (pcbnew/board_connected_item.cpp:92-93), and after the last child
    parseZONE puts on net 0 a zone not on copper or one a (keepout ...) or (placement ...)
    made a rule area (pcb_io_kicad_sexpr_parser.cpp:8290-8291, 8346-8348 and 8665-8671).
    All at 10.0.7."""
    copper = when_read = False
    for child in zone.lists:
        if child is net:
            when_read = copper
        elif child.head in ("keepout", "placement"):
            return False
        names = [a.text for a in child.atoms[1:]] if child.head in ("layer", "layers") else []
        if names:
            copper = any(name.endswith(".Cu") for name in names)
    return when_read and copper


def _read_net_name(node, version: int, bound: Mapping[int, str]) -> str:
    """The name of the net an item's (net ...) puts it on, "" for no net.

    A quoted or non-numeric atom is a name. A bare number is the net ``bound`` gives
    it (_bound_names), except a zone's (below). A pad's (net N "NAME") is its name. A
    pad that holds a number alone is a board KiCad refuses to load (its parser needs a
    name after the number, pcb_io_kicad_sexpr_parser.cpp:5225 at 9.0.8 and 6210 at
    10.0.7), so it has no KiCad reading, and here reads like any other item holding a
    number.

    A zone is read the way the KiCad that writes its format reads it. KiCad 9
    puts a copper zone on the net its net_name names and, with none or an empty
    one, on no net, whatever its number says (pcb_io_kicad_sexpr_parser.cpp:
    7387-7412 at 9.0.8). KiCad 10 does the same for a non-empty net_name (8037-8041
    and 8673-8697 at 10.0.7), and keeps a bare number only on a zone that is on copper
    when it reads the number, in file order, and after its last child, and is no rule
    area (_zone_keeps_number). So a zone that lists its net first is on no net.

    ``version`` is the board's format, ``bound`` the board's _bound_names; a caller
    reading many items computes both once, since finding either walks the whole board.
    """
    if node.head == "zone":
        legacy = node.find("net_name")
        named = legacy.atoms[1].text if legacy is not None and len(legacy.atoms) > 1 else ""
        if named or version <= _NUMERIC_NET_VERSION_MAX:
            return named
    net = node.find("net")
    if net is None or len(net.atoms) < 2:
        return ""
    code = _net_code(net.atoms[1])
    if code is None:
        return net.atoms[1].text
    if len(net.atoms) > 2:
        return net.atoms[2].text
    if node.head == "zone" and not _zone_keeps_number(node, net):
        return ""
    return bound.get(code, "")


def _net_name_of(root, node) -> str:
    """The name of the net an item's (net ...) puts it on, in either dialect
    (_read_net_name). Callers reading many items use that with a version and the
    board's _bound_names computed once."""
    return _read_net_name(node, _board_version(root), _bound_names(root))


def _net_table(root) -> list[tuple[int, str]]:
    """(number, name) rows for the board's nets, both dialects.

    KiCad 9 format boards declare (net N "NAME") rows at the root. KiCad 10
    dropped the table and the numbers entirely: nets exist only as name
    references on pads, tracks, zones and copper shapes (measured on the K10
    runner, slice 13 probe), so numbers are synthesized from document order
    for the tool surface, every name once, net 0 left out. The same derivation
    feeds reads and writers, so a number handed out by list_pcb_nets resolves
    back to its name.
    """
    rows = _net_rows(root)
    if rows:
        return rows
    version = _board_version(root)
    names: list[str] = []
    seen: set[str] = set()
    for item in root.lists:
        if item.head == "footprint":
            holders = item.find_all("pad")
        elif item.head in _NET_ITEMS:
            holders = [item]
        else:
            continue
        for holder in holders:
            name = _read_net_name(holder, version, {})
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return [(i + 1, n) for i, n in enumerate(names)]


def _item_net_number(net_node, name_to_num: dict[str, int]) -> int:
    """Net number from a segment/via (net ...) child, either dialect."""
    if net_node is None or len(net_node.atoms) < 2:
        return 0
    code = _net_code(net_node.atoms[1])
    if code is not None:
        return code
    return name_to_num.get(net_node.atoms[1].text, 0)


# Token head to kiutils class name, so list_pcb_graphic_items output matches
# the kiutils-era `type(item).__name__` fallback byte for byte.
_GRAPHIC_CLASS = {
    "gr_rect": "GrRect",
    "gr_circle": "GrCircle",
    "gr_arc": "GrArc",
    "gr_poly": "GrPoly",
    "gr_curve": "GrCurve",
    "gr_text_box": "GrTextBox",
    "image": "Image",
}

# Native-shape trace templates for the CST write path; values filled per call
# via set_text. Always (uuid ...): KiCad 9 aliases tstamp/uuid on read, and
# kiutils' next save is healed by _fix_empty_tstamps.
_SEGMENT_TPL = _cst.parse(
    b"(segment\n\t\t(start 0 0)\n\t\t(end 0 0)\n\t\t(width 0.25)"
    b'\n\t\t(layer "F.Cu")\n\t\t(net 0)\n\t\t(uuid "x")\n\t)'
).lists[0]

_VIA_TPL = _cst.parse(
    b"(via\n\t\t(at 0 0)\n\t\t(size 0.6)\n\t\t(drill 0.3)"
    b'\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(net 0)\n\t\t(uuid "x")\n\t)'
).lists[0]

# The three constructs written with a name or path in them, filled with set_text, whose
# quoting of a quoted atom is KiCad's (pcb_io_kicad_sexpr.cpp:678-680, 1613-1614 and 1175 at
# 9.0.8; 1858 and 1278 at 10.0.7): a KiCad 9 net row or pad net, a KiCad 10 net, a
# footprint's path.
_NET_ROW_TPL = _cst.parse(b'(net 0 "x")').lists[0]
_NET_NAMED_TPL = _cst.parse(b'(net "x")').lists[0]
_FP_PATH_TPL = _cst.parse(b'(path "x")').lists[0]

#: Most vias add_thermal_vias places in one call. A heuristic, not a KiCad
#: limit: real exposed-pad grids run about 3x3 to 10x10, and every via is spliced
#: in after the board's last track before the one write, so the cost is grid size
#: times board size. 256 took 0.8 s on a synthetic board of 1000 footprints and
#: 1000 tracks in either format, measured 2026-10-17. Resolving the via's net
#: once per call matters: doing it for every via took 9.7 s on the KiCad 10 board
#: (1.2 s on KiCad 9), where it walks every pad and track.
_MAX_THERMAL_VIAS = 256


# Highest board format that carries a net table, so the highest one whose net
# references may be numeric. Above it KiCad derives nets from usage and rebinds
# a number by load order, silently landing it on the wrong net (ADR-2
# guardrail 5, measured in the slice-13 probe).
_NUMERIC_NET_VERSION_MAX = 20241229


def _board_version(root) -> int:
    v = root.find("version")
    return int(v.atoms[1].text) if v is not None else 0


def _board_major(root) -> int:
    """The KiCad whose format the board carries, whose arithmetic the board's
    positions follow (_shared._transform_local_to_board): 10 above
    _NUMERIC_NET_VERSION_MAX, a KiCad 10 format that KiCad 9 cannot load, and 9
    at or below it, where KiCad 9.0.x writes 20241229. The two round one double
    apart (_shared.kiround)."""
    return 10 if _board_version(root) > _NUMERIC_NET_VERSION_MAX else 9


def _splice_pcb_node(root, node) -> None:
    """Insert *node* after the last trace item, else before the board tail."""
    _splice_after(root, node, ("segment", "arc", "via"), ("zone", "group", "embedded_fonts"))


def _unknown_net(net: int, nets: list[tuple[int, str]]) -> ToolError:
    """The refusal of a net number that is no net of the board. The leading words
    stay "Net N not found", which callers and tests match on."""
    listed = ", ".join(f"{num} ({name})" for num, name in nets if name) or "none"
    return ToolError(
        f"Net {net} not found: no net on this board has that number, and 0 means no net."
        f" The board's nets are {listed}. Nothing was written."
    )


def _new_net(value: str, *, quoted: bool):
    """A (net ...) node holding ``value``, a quoted name or a bare number."""
    node = _NET_NAMED_TPL.copy() if quoted else _cst.parse(b"(net 0)").lists[0]
    node.atoms[1].set_text(value)
    return node


def _put_net(node, old, new) -> None:
    """Make ``new`` the (net ...) child of ``node``: in place of ``old`` with its
    separator, else where KiCad writes one (straight after a zone's head, before
    any other item's uuid). A separator taken from a sibling is cut to its line
    break and indentation (_anchor_ws), as insert_after cuts its anchor's, so a
    comment line is never copied beside a node."""
    if old is not None:
        new.sep = old.sep
        node.children[node.children.index(old)] = new
    elif node.head == "zone":
        first = node.lists[0] if node.lists else None
        sep = _anchor_ws(first.sep) if first is not None else b"\n\t\t"
        node.insert_after(node.atoms[0], new, sep=sep)
    else:
        uuid = node.find("uuid")
        if uuid is not None:
            node.insert_before(uuid, new)
        else:
            node.append_child(new, _anchor_ws(node.lists[-1].sep) if node.lists else b"\n\t\t")


def _set_zone_net_name(zone, name: str) -> None:
    """A KiCad 9 zone's (net_name ...), which KiCad reads the zone's net by, set to
    ``name`` and put straight after its (net ...) when the zone has none."""
    held = zone.find("net_name")
    if held is not None and len(held.atoms) > 1:
        if held.atoms[1].text != name:
            held.atoms[1].set_text(name)
        return
    new = _cst.parse(b'(net_name "")').lists[0]
    new.atoms[1].set_text(name)
    if held is not None:
        new.sep = held.sep
        zone.children[zone.children.index(held)] = new
    else:
        net = zone.find("net")
        zone.insert_after(net, new, sep=_anchor_ws(net.sep))


def _set_item_net(node, root, net: int, version: int | None = None) -> None:
    """Put a segment, arc, via, zone or copper shape on net ``net`` as the board's
    format writes it, or refuse and leave everything as it was.

    ``net`` is 0, which is no net, or the number of one of the board's nets: a row
    of its table on a board of KiCad 9's format, a number list_pcb_nets reports on
    any other. KiCad 9 binds a number only through a row, so a number past the table
    finds no net ("Invalid net ID") and a negative one or one in a gap lands on net
    0 (getNetCode and SetNetCode, pcb_io_kicad_sexpr_parser.h:156-162 and
    pcbnew/board_connected_item.cpp:87-115 at 9.0.8); KiCad 10 rebinds a number by
    load order (ADR-2 guardrail 5). The check comes before the first change.

    A net is written in the board's dialect, never the other (ADR-2 guardrail 5): a
    number up to format 20241229, (net N) and on a zone also (net_name "NAME"), the
    name KiCad 9 reads a copper zone's net by; a quoted name above it. No net is
    spelled by item, as KiCad's writer spells it (pcb_io_kicad_sexpr.cpp, 9.0.8 then
    10.0.7):

    - a segment, arc or via: (net 0), then (net "") (2436, 2860-2861), net 0 being
      the net a board's net list registers under the empty name
      (netinfo_list.cpp:44-45 at 10.0.7)
    - a zone: (net 0) with (net_name ""), then no token at all (2452-2454,
      2872-2876); a legacy (net_name ...) that is not empty would put a KiCad 10
      zone on a net of that name, creating it if need be
      (pcb_io_kicad_sexpr_parser.cpp:8037-8041 and 8673-8697 at 10.0.7), so the zone
      loses it
    - a copper shape: no token on either (1027-1028, 1117-1118)

    ``version`` is the board's format when the caller has it, since finding it walks
    the whole board.
    """
    numeric = (_board_version(root) if version is None else version) <= _NUMERIC_NET_VERSION_MAX
    name = ""
    if net != 0:
        nets = _net_rows(root) if numeric else _net_table(root)
        names = _by_number(nets)
        if net not in names:
            raise _unknown_net(net, nets)
        name = names[net]
    is_zone = node.head == "zone"
    if net == 0 and node.head not in ("segment", "arc", "via") and not (numeric and is_zone):
        for stale in node.find_all("net") + node.find_all("net_name"):
            node.remove_child(stale)
        return
    held = node.find("net")
    if numeric:
        atoms = held.atoms if held is not None else []
        if len(atoms) == 2 and _net_code(atoms[1]) is not None:
            if _net_code(atoms[1]) != net:
                atoms[1].set_text(str(net))
        else:
            _put_net(node, held, _new_net(str(net), quoted=False))
        if is_zone:
            _set_zone_net_name(node, name)
        return
    atoms = held.atoms if held is not None else []
    if not (len(atoms) == 2 and atoms[1].raw[:1] == b'"' and atoms[1].text == name):
        _put_net(node, held, _new_net(name, quoted=True))
    for stale in node.find_all("net_name"):
        node.remove_child(stale)


def _find_fp_cst(root, reference):
    """The footprint node with *reference*, or raise ToolError."""
    for fp in root.find_all("footprint"):
        if _fp_prop_cst(fp, "Reference") == reference:
            return fp
    raise ToolError(
        f"Footprint {reference!r} not found. Use list_pcb_footprints to see what is on the board."
    )


def _edge_polygon_cst(root) -> list[tuple[float, float]] | None:
    """CST twin of _board_edge_polygon: Edge.Cuts lines/arcs chained closed."""
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for item in root.lists:
        layer = item.find("layer")
        if layer is None or layer.atoms[1].text != "Edge.Cuts":
            continue
        if item.head == "gr_line":
            start, end = item.find("start"), item.find("end")
            s = (round(float(start.atoms[1].text), 3), round(float(start.atoms[2].text), 3))
            e = (round(float(end.atoms[1].text), 3), round(float(end.atoms[2].text), 3))
            if s != e:
                segments.append((s, e))
        elif item.head == "gr_arc":
            start, mid, end = item.find("start"), item.find("mid"), item.find("end")
            arc_pts = _linearize_arc(
                float(start.atoms[1].text),
                float(start.atoms[2].text),
                float(mid.atoms[1].text),
                float(mid.atoms[2].text),
                float(end.atoms[1].text),
                float(end.atoms[2].text),
            )
            for k in range(len(arc_pts) - 1):
                s = (round(arc_pts[k][0], 3), round(arc_pts[k][1], 3))
                e = (round(arc_pts[k + 1][0], 3), round(arc_pts[k + 1][1], 3))
                if s != e:
                    segments.append((s, e))
    return _chain_edge_polygon(segments)


#: The parser's layer set names and the layers each reads as
#: (pcb_io_kicad_sexpr_parser.cpp:115-123 at 9.0.8, 120-128 at 10.0.7).
_INNER_CU_NAMES = tuple(f"In{k}.Cu" for k in range(1, 31))
_LAYER_SETS = {
    "*.Cu": frozenset(("F.Cu", "B.Cu", *_INNER_CU_NAMES)),
    "*In.Cu": frozenset(_INNER_CU_NAMES),
    "F&B.Cu": frozenset(("F.Cu", "B.Cu")),
    **{
        f"*.{kind}": frozenset((f"F.{kind}", f"B.{kind}"))
        for kind in ("Adhes", "Paste", "Mask", "SilkS", "Fab", "CrtYd")
    },
}


def _zone_layers(zone) -> frozenset[str]:
    """The layers KiCad reads a zone on: the names its (layer ...) or (layers ...)
    writes, a layer set name read through the parser's table (_LAYER_SETS)."""
    names: set[str] = set()
    for node in zone.find_all("layer") + zone.find_all("layers"):
        for atom in node.atoms[1:]:
            names |= _LAYER_SETS.get(atom.text, {atom.text})
    return frozenset(names)


def _fp_side(fp) -> str:
    """The side KiCad reads a footprint on: B.Cu when its (layer) says so, F.Cu for
    anything else (_fp_is_back)."""
    return "B.Cu" if _fp_is_back(fp) else "F.Cu"


def _zone_forbids_footprints(zone, x: float, y: float, side: str, pts) -> bool:
    """Whether ``zone`` forbids a footprint on ``side`` whose origin is (x, y): a rule
    area forbidding footprints whose layers, as KiCad reads them, hold that side, as
    intersectsArea needs a layer common to the area and the footprint
    (pcbnew/pcbexpr_functions.cpp:712-715 at 9.0.8), and whose outline holds the
    point."""
    ko = zone.find("keepout")
    if ko is None:
        return False
    rule = ko.find("footprints")
    if rule is None or rule.atoms[1].text != "not_allowed":
        return False
    if side not in _zone_layers(zone):
        return False
    return bool(pts) and _point_in_polygon(x, y, pts)


def _zone_pts(zone):
    poly = zone.find("polygon")
    if poly is None:
        return []
    return [(round(x, 3), round(y, 3)) for x, y in map(_xy, poly.find("pts").find_all("xy"))]


def _keepout_violations_cst(root, x: float, y: float, side: str, own=None) -> list[dict]:
    """The rule areas that forbid a footprint on ``side`` at (x, y), as KiCad reads them:
    board-level zones first, then each footprint's zones, whose points are board
    coordinates as stored (format( ZONE ) writes them without the footprint,
    pcb_io_kicad_sexpr.cpp:2628-2637 at 9.0.8, 3061-3074 at 10.0.7), less those of
    ``own``, the footprint being checked: KiCad never tests a footprint against an area
    it owns (intersectsArea skips an area whose parent is the item,
    pcbnew/pcbexpr_functions.cpp:697 at 9.0.8, 825 at 10.0.7). The layers each area
    holds are read through the parser's layer set names (_zone_layers)."""
    candidates = [("board", z) for z in root.find_all("zone")]
    for fp in root.find_all("footprint"):
        if fp is own:
            continue
        source = f"footprint:{_fp_prop_cst(fp, 'Reference')}"
        candidates += [(source, zone) for zone in fp.find_all("zone")]

    violations: list[dict] = []
    for source, zone in candidates:
        if not _zone_forbids_footprints(zone, x, y, side, _zone_pts(zone)):
            continue
        layers_node = zone.find("layers") or zone.find("layer")
        violations.append(
            {
                "source": source,
                "layers": [a.text for a in layers_node.atoms[1:]],
                "restrictions": _keepout_dict(zone.find("keepout")),
            }
        )
    return violations


#: The pad-less marker place_footprint puts down without a library: a footprint in
#: the layout pcbnew writes on a board, layer, uuid, (at), then the fields. Its (at)
#: has no angle, as format( FOOTPRINT ) leaves out an orientation of 0
#: (pcb_io_kicad_sexpr.cpp:1127-1131 at 9.0.8, 1231-1235 at 10.0.7; pcbnew 9.0.8's
#: SaveBoard of a footprint at 0 writes (at 50 40), harvested 2026-10-11), while a
#: field's (at) always carries one (1992-1994 at 9.0.8).
_FOOTPRINT_TPL = _cst.parse(
    b'(footprint ""\n\t\t(layer "F.Cu")\n\t\t(uuid "x")\n\t\t(at 0 0)'
    b'\n\t\t(property "Reference" "R"\n\t\t\t(at 0 -2 0)\n\t\t\t(layer "F.SilkS")\n\t\t\t(uuid "x")'
    b"\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)"
    b'\n\t\t(property "Value" "V"\n\t\t\t(at 0 2 0)\n\t\t\t(layer "F.Fab")\n\t\t\t(uuid "x")'
    b"\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)"
).lists[0]

# Board children that sort after footprints in native files.
_PCB_TAIL_HEADS = (
    "gr_line",
    "gr_text",
    "gr_rect",
    "gr_circle",
    "gr_arc",
    "gr_poly",
    "gr_curve",
    "gr_text_box",
    "image",
    "segment",
    "arc",
    "via",
    "zone",
    "group",
    "embedded_fonts",
)


_GRAPHIC_HEADS = ("gr_line", "gr_text") + tuple(_GRAPHIC_CLASS)
_TRACE_AND_TAIL_HEADS = ("segment", "arc", "via", "zone", "group", "embedded_fonts")


#: An empty board, harvested from pcbnew.NewBoard rather than written by hand,
#: because update_pcb_from_schematic's documented first use creates the board and
#: nothing in this package could make one. Stamped at the KiCad 9 format it was
#: harvested at, the same rule project.py's _EMPTY_SCH_TPL follows; editing an
#: existing board through the CST never touches its stamp, so a KiCad 10 project
#: stays KiCad 10.
_EMPTY_PCB_TPL = (
    b"(kicad_pcb\r\n"
    b"\t(version 20241229)\r\n"
    b'\t(generator "pcbnew")\r\n'
    b'\t(generator_version "9.0")\r\n'
    b"\t(general\r\n"
    b"\t\t(thickness 1.6)\r\n"
    b"\t\t(legacy_teardrops no)\r\n"
    b"\t)\r\n"
    b'\t(paper "A4")\r\n'
    b"\t(layers\r\n"
    b'\t\t(0 "F.Cu" signal)\r\n'
    b'\t\t(2 "B.Cu" signal)\r\n'
    b'\t\t(9 "F.Adhes" user "F.Adhesive")\r\n'
    b'\t\t(11 "B.Adhes" user "B.Adhesive")\r\n'
    b'\t\t(13 "F.Paste" user)\r\n'
    b'\t\t(15 "B.Paste" user)\r\n'
    b'\t\t(5 "F.SilkS" user "F.Silkscreen")\r\n'
    b'\t\t(7 "B.SilkS" user "B.Silkscreen")\r\n'
    b'\t\t(1 "F.Mask" user)\r\n'
    b'\t\t(3 "B.Mask" user)\r\n'
    b'\t\t(17 "Dwgs.User" user "User.Drawings")\r\n'
    b'\t\t(19 "Cmts.User" user "User.Comments")\r\n'
    b'\t\t(21 "Eco1.User" user "User.Eco1")\r\n'
    b'\t\t(23 "Eco2.User" user "User.Eco2")\r\n'
    b'\t\t(25 "Edge.Cuts" user)\r\n'
    b'\t\t(27 "Margin" user)\r\n'
    b'\t\t(31 "F.CrtYd" user "F.Courtyard")\r\n'
    b'\t\t(29 "B.CrtYd" user "B.Courtyard")\r\n'
    b'\t\t(35 "F.Fab" user)\r\n'
    b'\t\t(33 "B.Fab" user)\r\n'
    b'\t\t(39 "User.1" user)\r\n'
    b'\t\t(41 "User.2" user)\r\n'
    b'\t\t(43 "User.3" user)\r\n'
    b'\t\t(45 "User.4" user)\r\n'
    b"\t)\r\n"
    b"\t(setup\r\n"
    b"\t\t(pad_to_mask_clearance 0)\r\n"
    b"\t\t(allow_soldermask_bridges_in_footprints no)\r\n"
    b"\t\t(tenting front back)\r\n"
    b"\t\t(pcbplotparams\r\n"
    b"\t\t\t(layerselection 0x00000000_00000000_55555555_5755f5ff)\r\n"
    b"\t\t\t(plot_on_all_layers_selection 0x00000000_00000000_00000000_00000000)\r\n"
    b"\t\t\t(disableapertmacros no)\r\n"
    b"\t\t\t(usegerberextensions no)\r\n"
    b"\t\t\t(usegerberattributes yes)\r\n"
    b"\t\t\t(usegerberadvancedattributes yes)\r\n"
    b"\t\t\t(creategerberjobfile yes)\r\n"
    b"\t\t\t(dashed_line_dash_ratio 12.000000)\r\n"
    b"\t\t\t(dashed_line_gap_ratio 3.000000)\r\n"
    b"\t\t\t(svgprecision 4)\r\n"
    b"\t\t\t(plotframeref no)\r\n"
    b"\t\t\t(mode 1)\r\n"
    b"\t\t\t(useauxorigin no)\r\n"
    b"\t\t\t(hpglpennumber 1)\r\n"
    b"\t\t\t(hpglpenspeed 20)\r\n"
    b"\t\t\t(hpglpendiameter 15.000000)\r\n"
    b"\t\t\t(pdf_front_fp_property_popups yes)\r\n"
    b"\t\t\t(pdf_back_fp_property_popups yes)\r\n"
    b"\t\t\t(pdf_metadata yes)\r\n"
    b"\t\t\t(pdf_single_document no)\r\n"
    b"\t\t\t(dxfpolygonmode yes)\r\n"
    b"\t\t\t(dxfimperialunits yes)\r\n"
    b"\t\t\t(dxfusepcbnewfont yes)\r\n"
    b"\t\t\t(psnegative no)\r\n"
    b"\t\t\t(psa4output no)\r\n"
    b"\t\t\t(plot_black_and_white yes)\r\n"
    b"\t\t\t(sketchpadsonfab no)\r\n"
    b"\t\t\t(plotpadnumbers no)\r\n"
    b"\t\t\t(hidednponfab no)\r\n"
    b"\t\t\t(sketchdnponfab yes)\r\n"
    b"\t\t\t(crossoutdnponfab yes)\r\n"
    b"\t\t\t(subtractmaskfromsilk no)\r\n"
    b"\t\t\t(outputformat 1)\r\n"
    b"\t\t\t(mirror no)\r\n"
    b"\t\t\t(drillshape 1)\r\n"
    b"\t\t\t(scaleselection 1)\r\n"
    b'\t\t\t(outputdirectory "")\r\n'
    b"\t\t)\r\n"
    b"\t)\r\n"
    b'\t(net 0 "")\r\n'
    b"\t(embedded_fonts no)\r\n"
    b")\r\n"
)


#: Children a library .kicad_mod carries that a placed board footprint never
#: does. KiCad stamps the library file with the format it was written at and the
#: tool that wrote it; a board carries one stamp for the whole document, so
#: copying these in would put a second, stale one inside a footprint.
_LIB_ONLY_FOOTPRINT_HEADS = ("version", "generator", "generator_version")


def _regen_uuids(node) -> None:
    """Give every (uuid ...) in the subtree a fresh value, in place.

    Library footprints already carry uuids, on the root's properties and on
    every pad, so a straight copy is not merely untidy: placing the same
    footprint twice would put the same uuid on two different objects, and a uuid
    is what KiCad matches a board object back to its schematic symbol by.
    """
    for child in node.lists:
        if child.head == "uuid" and len(child.atoms) > 1:
            child.atoms[1].set_text(_gen_uuid())
        else:
            _regen_uuids(child)


#: How deep a (type "Table") row may nest. KiCad 10's global fp-lib-table is a
#: single such row pointing at the stock template table, so one level is what
#: ships; three leaves room for a user who chains their own.
_LIB_TABLE_DEPTH = 3

#: The install subtree each KiCad path variable names, for an install found
#: through the resolved kicad-cli when nothing else defines the variable.
_KICAD_VAR_DIRS = {
    "FOOTPRINT": "footprints",
    "TEMPLATE": "template",
    "SYMBOL": "symbols",
    "3DMODEL": "3dmodels",
}

#: The folder KiCad gives itself inside the user's documents folder, and inside
#: KICAD_DOCUMENTS_HOME when that stands in for it: KICAD_PATH_STR in KiCad's
#: paths.h, capitalised on Windows and macOS and lowercase elsewhere.
_KICAD_PATH_STR = "KiCad" if sys.platform in ("win32", "darwin") else "kicad"


def _kicad_config_dirs() -> list[Path]:
    """KiCad's settings directories for the running kicad-cli, most likely first.

    KICAD_CONFIG_HOME when set, else ~/.config/kicad (XDG_CONFIG_HOME honoured)
    on Linux, ~/Library/Preferences/kicad on macOS and %APPDATA%/kicad on
    Windows, each with a <major>.<minor> subdirectory. The minor is taken as 0
    (a nightly's <major>.99 is also tried) and the unversioned directory last,
    for a KICAD_CONFIG_HOME laid out without one. No kicad-cli means no opinion
    and an empty list.
    """
    major = _kicad_cli_major()
    if major is None:
        return []
    configured = os.environ.get("KICAD_CONFIG_HOME")
    if configured:
        base = Path(configured)
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return []
        base = Path(appdata) / "kicad"
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Preferences/kicad"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kicad"
    return [base / f"{major}.0", *sorted(base.glob(f"{major}.*")), base]


def _kicad_config_file(name: str) -> Path | None:
    """The first of KiCad's settings directories holding *name*, or None."""
    for folder in _kicad_config_dirs():
        path = folder / name
        if path.is_file():
            return path
    return None


def _global_fp_lib_table() -> Path | None:
    """The user's global fp-lib-table for the running KiCad, or None."""
    return _kicad_config_file("fp-lib-table")


def _kicad_settings(name: str) -> dict:
    """One of KiCad's JSON settings files (kicad_common.json, kicad.json) as a
    dict, or {} when it is missing or unreadable: a setting that cannot be read
    means its default, never a failed tool."""
    path = _kicad_config_file(name)
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _configured_vars() -> dict[str, str]:
    """The path variables set in Preferences > Configure Paths.

    KiCad keeps them in kicad_common.json under environment.vars. The OS
    environment wins over them when both define a name, which _kicad_var
    honours by asking the environment first.
    """
    environment = _kicad_settings("kicad_common.json").get("environment")
    variables = environment.get("vars") if isinstance(environment, dict) else None
    if not isinstance(variables, dict):
        return {}
    return {str(key): str(value) for key, value in variables.items() if value}


def _windows_documents_dir() -> Path:
    """The Documents folder the Windows shell names, which is the one KiCad uses.

    KiCad asks wxWidgets, whose GetDocumentsDir on Windows is
    SHGetFolderPath(CSIDL_PERSONAL, SHGFP_TYPE_CURRENT) in wx 3.2.8, the
    version KiCad 9.0.8 ships there. That follows OneDrive folder backup and
    folder redirection, which move Documents away from ~/Documents. Raises
    OSError when the shell has no answer, and anywhere but Windows.
    """
    if sys.platform != "win32":
        raise OSError("the shell's Documents folder is a Windows notion")
    import ctypes

    path = ctypes.create_unicode_buffer(260)  # MAX_PATH
    # CSIDL_PERSONAL (5) at SHGFP_TYPE_CURRENT (0), the question wx asks.
    status = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, path)
    if status != 0 or not path.value:
        raise OSError("the shell named no Documents folder")
    return Path(path.value)


def _kicad_documents_home() -> Path:
    """Where KiCad keeps the user's own data (projects, 3rd-party packages),
    short of the version folder: KiCad's PATHS::getUserDocumentPath.

    The base is KICAD_DOCUMENTS_HOME when set, else the platform's documents
    folder (the shell's on Windows, falling back to ~/Documents when it has no
    answer; ~/Documents on macOS; the XDG data home on Linux), and KiCad
    appends its own folder name (_KICAD_PATH_STR) to either. So
    KICAD_DOCUMENTS_HOME=D puts the packages under D/KiCad/<N>.0/3rdparty on
    Windows, not D/<N>.0/3rdparty.
    """
    base = os.environ.get("KICAD_DOCUMENTS_HOME")
    if not base:
        if sys.platform == "win32":
            try:
                base = _windows_documents_dir()
            except OSError:
                base = Path.home() / "Documents"
        elif sys.platform == "darwin":
            base = Path.home() / "Documents"
        else:
            base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
    return Path(base) / _KICAD_PATH_STR


def _kicad_var(name: str, project_dir: Path | None, configured: dict | None = None) -> str | None:
    """A KiCad path variable's value, or None when nothing defines it.

    KIPRJMOD is the project directory. Anything else is resolved as KiCad
    resolves it: the OS environment first, which is where KiCad itself and a
    Guix or Nix profile set them, then Preferences > Configure Paths
    (*configured*, read from kicad_common.json when not given), then KiCad's
    own defaults for its predefined variables: <N>.0/3rdparty under
    _kicad_documents_home() for KICAD<N>_3RD_PARTY (PATHS::GetDefault3rdPartyPath),
    the root the Plugin and Content Manager installs into, which KiCad
    computes at run time and never writes anywhere, and the install the
    resolved kicad-cli belongs to for the versioned KICAD<N>_FOOTPRINT_DIR
    family and the KiCad 5 spelling KISYSMOD.
    """
    if name == "KIPRJMOD":
        return str(project_dir) if project_dir is not None else None
    env = os.environ.get(name)
    if env:
        return env
    if configured is None:
        configured = _configured_vars()
    if configured.get(name):
        return configured[name]
    third_party = re.fullmatch(r"KICAD(\d+)_3RD_PARTY", name)
    if third_party:
        return str(_kicad_documents_home() / f"{third_party.group(1)}.0" / "3rdparty")
    matched = re.fullmatch(r"KICAD\d*_(FOOTPRINT|TEMPLATE|SYMBOL|3DMODEL)_DIR", name)
    kind = matched.group(1) if matched else ("FOOTPRINT" if name == "KISYSMOD" else None)
    root = _kicad_root()
    if kind is None or root is None:
        return None
    for sub in ("share/kicad", "SharedSupport"):
        cand = root / sub / _KICAD_VAR_DIRS[kind]
        if cand.is_dir():
            return str(cand)
    return None


def _expand_lib_uri(
    uri: str, project_dir: Path | None, configured: dict | None = None
) -> str | None:
    """*uri* with every ${VAR} expanded, or None when one of them is undefined."""

    def expand(match: re.Match) -> str:
        value = _kicad_var(match.group(1), project_dir, configured)
        if value is None:
            raise KeyError(match.group(1))
        return value

    try:
        return re.sub(r"\$\{([^}]+)\}", expand, uri)
    except KeyError:
        return None


def _uri_readings(atom: _cst.Atom) -> list[str]:
    """The ways the two KiCad majors read a table value, KiCad 9's first.

    KiCad 9 decodes the escapes its lexer decodes inside quotes (Atom.text); KiCad 10 keeps
    the bytes between the quotes as they stand (library_table_grammar.h:60 at 10.0.7). A
    hand-written ${KIPRJMOD}\\footprints\\X.pretty therefore names a different path in each.
    One reading when they agree.
    """
    raw = atom.raw
    as_written = (raw[1:-1] if raw[:1] == b'"' else raw).decode("utf-8", "surrogateescape")
    return list(dict.fromkeys((atom.text, as_written)))


def _existing_target(
    atom: _cst.Atom, exists: Callable[[Path], bool], project_dir: Path | None, configured: dict
) -> str | None:
    """The expanded URI of *atom*, under the first of its readings that *exists* says is
    there, or None."""
    for reading in _uri_readings(atom):
        target = _expand_lib_uri(reading, project_dir, configured)
        if target is not None and exists(Path(target)):
            return target
    return None


def _read_fp_lib_table(
    path: Path,
    project_dir: Path | None,
    depth: int = _LIB_TABLE_DEPTH,
    configured: dict | None = None,
    *,
    _memo: dict | None = None,
) -> dict:
    """nickname -> .pretty directory for the rows of one fp-lib-table.

    Only rows this package can serve are returned: type KiCad with a directory
    that exists, after ${VAR} expansion, under the URI as either KiCad major
    reads it (_uri_readings). Disabled rows, rows of the plugin types KiCad
    reads through code this package has no counterpart for (Eagle, Altium,
    GitHub), rows naming an undefined variable and rows whose directory is
    missing are left out, so a nickname absent here falls through to the
    directory search the caller does next and the refusal names what was
    tried. A (type "Table") row is followed into the table it names, which is
    how KiCad 10's global table reaches the stock libraries. An unreadable or
    malformed table answers empty rather than failing the tool, because the
    table is a hint about where libraries are and not the operation itself.
    *configured* is Configure Paths, read once per table and shared with the
    tables it nests. *_memo* holds the nested tables already read in this
    traversal, keyed on the file's identity (device and inode) and depth left;
    it is made per top-level call, never kept, so a live edit of a table stays
    visible.
    """
    if configured is None:
        configured = _configured_vars()
    if _memo is None:
        _memo = {}
    try:
        tree = _cst.parse(path.read_bytes())
    except (OSError, SyntaxError):
        return {}
    if not tree.lists or tree.lists[0].head != "fp_lib_table":
        return {}
    table: dict = {}
    for row in tree.lists[0].find_all("lib"):
        name, kind, uri = row.find("name"), row.find("type"), row.find("uri")
        if name is None or uri is None or len(name.atoms) < 2 or len(uri.atoms) < 2:
            continue
        if row.find("disabled") is not None:
            continue
        plugin = kind.atoms[1].text.lower() if kind is not None and len(kind.atoms) > 1 else "kicad"
        if plugin not in ("kicad", "table"):
            continue
        exists = Path.is_file if plugin == "table" else Path.is_dir
        target = _existing_target(uri.atoms[1], exists, project_dir, configured)
        if target is None:
            continue
        if plugin == "table":
            if depth > 0:
                # A nested table is parsed once per depth in this traversal.
                # Unmemoised, a table naming itself in N rows was parsed
                # 1+N+N^2+N^3 times (400 for seven rows). The key is the file
                # (device and inode; realpath where a volume reports no inode),
                # because a hard link, or a case variant on a case-insensitive
                # volume, is a spelling realpath does not collapse: keyed on the
                # path, 200 hard links still cost 601 parses (7.8 s). The depth
                # is in the key because a table first reached with less depth
                # left must not cut a deeper follow short, and a miss is tested
                # by membership because a table with nothing servable answers {}.
                try:
                    st = os.stat(target)
                except OSError:
                    continue
                ident = (st.st_dev, st.st_ino) if st.st_ino else os.path.realpath(target)
                key = (ident, depth - 1)
                if key not in _memo:
                    _memo[key] = _read_fp_lib_table(
                        Path(target), project_dir, depth - 1, configured, _memo=_memo
                    )
                nested = _memo[key]
                for nick, pretty in nested.items():
                    table.setdefault(nick, pretty)
        else:
            table.setdefault(name.atoms[1].text, str(Path(target)))
    return table


def _lib_table_rows(path: Path) -> list[tuple[str, str]]:
    """(nickname, URI as written) for every row of one fp-lib-table, disabled
    rows and every plugin type included, or [] when the table cannot be read.

    What KiCad checks before it adds a library the Plugin and Content Manager
    installed: whether some row already has that nickname, or that URI before
    any ${VAR} in it is expanded, whatever the row's state.
    """
    try:
        tree = _cst.parse(path.read_bytes())
    except (OSError, SyntaxError):
        return []
    if not tree.lists or tree.lists[0].head != "fp_lib_table":
        return []
    rows = []
    for row in tree.lists[0].find_all("lib"):
        name, uri = row.find("name"), row.find("uri")
        if name is not None and len(name.atoms) > 1:
            written = uri.atoms[1].text if uri is not None and len(uri.atoms) > 1 else ""
            rows.append((name.atoms[1].text, written))
    return rows


def _pcm_footprint_libs(
    configured: dict | None = None, rows: list[tuple[str, str]] | None = None
) -> dict[str, str]:
    """nickname -> .pretty for the footprint libraries the Plugin and Content
    Manager installed, as KiCad adds them to the global table.

    With "auto add" on (pcm.lib_auto_add in kicad.json, the default) KiCad
    walks ${KICAD<N>_3RD_PARTY}/footprints while it loads the global table and
    takes every .pretty directory inside a package, at any depth
    (PCM_FP_LIB_TRAVERSER in KiCad 9's fp_lib_table.cpp, PCM_LIB_TRAVERSER in
    KiCad 10's library_manager.cpp). A library whose
    ${KICAD<N>_3RD_PARTY}/footprints/... URI is already a row of the table is
    left as that row has it, renamed or disabled. Any other is added as
    <prefix><lib> (pcm.lib_prefix, default PCM_), or as <prefix><lib>_1, _2
    and so on while the nickname is taken by a row, disabled or not, or by a
    library added before it; KiCad numbers those in the order its directory
    walk meets them, and the walk here is sorted. KiCad 9 inserts the rows in
    memory at every load, and KiCad 10 also saves the table afterwards, so
    there they come back as rows of the file, which the URI check skips.
    *rows* are the table's own (nickname, URI) pairs from _lib_table_rows; the
    caller merges the result under the file's rows.
    """
    major = _kicad_cli_major()
    if major is None:
        return {}
    pcm = _kicad_settings("kicad.json").get("pcm")
    pcm = pcm if isinstance(pcm, dict) else {}
    if pcm.get("lib_auto_add", True) is False:
        return {}
    prefix = str(pcm.get("lib_prefix", "PCM_"))
    variable = f"KICAD{major}_3RD_PARTY"
    root = _kicad_var(variable, None, configured)
    footprints = Path(root) / "footprints" if root else None
    if footprints is None or not footprints.is_dir():
        return {}
    taken = {name for name, _ in rows or ()}
    listed = {uri for _, uri in rows or ()}
    libs: dict[str, str] = {}
    for pretty in sorted(footprints.rglob("*.pretty")):
        parts = pretty.relative_to(footprints).parts
        # endswith is case-sensitive, as KiCad's EndsWith is; on Windows the glob is not.
        if len(parts) < 2 or not pretty.name.endswith(".pretty") or not pretty.is_dir():
            continue
        if "/".join(["${" + variable + "}", "footprints", *parts]) in listed:
            continue
        base = prefix + pretty.name[: -len(".pretty")]
        nickname, n = base, 1
        while nickname in taken:
            nickname, n = f"{base}_{n}", n + 1
        taken.add(nickname)
        libs[nickname] = str(pretty)
    return libs


def _project_dir(pcb_path: str, schematic_path: str = "", project_path: str = "") -> Path | None:
    """Where ${KIPRJMOD} points: the .kicad_pro's directory when one is named,
    else the first of the board's and the schematic's directories that holds a
    project or an fp-lib-table, else the board's own directory."""
    if project_path:
        return Path(project_path).resolve().parent
    dirs = [Path(p).resolve().parent for p in (pcb_path, schematic_path) if p]
    for d in dirs:
        if (d / "fp-lib-table").is_file() or any(d.glob("*.kicad_pro")):
            return d
    return dirs[0] if dirs else None


def _fp_search_dirs(*paths: str) -> list[str]:
    """The directories searched for <nickname>.pretty between the project's
    table and the global one: the directory of each given file (the board,
    and the schematic for the import), then KICAD_FP_LIB, which may name a
    .pretty itself (searched together with its parent) or a directory holding
    several. They come before the global table, as they always have in this
    package, so a library beside the board shadows a global row of the same
    nickname."""
    dirs = dict.fromkeys(str(Path(p).resolve().parent) for p in paths if p)
    if FP_LIB_PATH:
        fp_lib = Path(FP_LIB_PATH)
        for d in [fp_lib.parent, fp_lib] if fp_lib.suffix == ".pretty" else [fp_lib]:
            dirs.setdefault(str(d))
    return list(dirs)


class _FpLibResolver:
    """Footprint library nickname -> .pretty directory, in KiCad's order.

    The project's own fp-lib-table (${KIPRJMOD} is *project_dir*), then
    <nickname>.pretty under each of *search_dirs* (see _fp_search_dirs), then
    the user's global fp-lib-table together with the libraries the Plugin and
    Content Manager installed, then KiCad's stock footprints under the
    kicad-cli install. update_pcb_from_schematic and place_footprint both
    resolve through one of these, so a hand-placed part and an imported one
    name the same file. The project and global tables are each read at most
    once per resolver (a table they nest, once per level of nesting it is
    reached at), and the global table only once a local lookup has missed: the
    stock table's 155 nested rows cost about 15 ms to read, which a project
    library should not pay for, and reading it per resolver rather than once
    per process keeps a live edit of the table visible.
    """

    def __init__(self, project_dir: Path | None, search_dirs: list[str]) -> None:
        self.project_dir = project_dir
        self.search_dirs = list(search_dirs)
        self._project: dict | None = None
        self._global: dict | None = None

    @property
    def project_table(self) -> dict:
        if self._project is None:
            table = self.project_dir / "fp-lib-table" if self.project_dir is not None else None
            self._project = (
                _read_fp_lib_table(table, self.project_dir)
                if table is not None and table.is_file()
                else {}
            )
        return self._project

    @property
    def global_table(self) -> dict:
        if self._global is None:
            configured = _configured_vars()
            path = _global_fp_lib_table()
            table = (
                _read_fp_lib_table(path, self.project_dir, configured=configured) if path else {}
            )
            rows = _lib_table_rows(path) if path else []
            for nick, pretty in _pcm_footprint_libs(configured, rows).items():
                table.setdefault(nick, pretty)
            self._global = table
        return self._global

    @property
    def stock_dirs(self) -> list[str]:
        root = _kicad_root()
        subs = ("share/kicad/footprints", "SharedSupport/footprints")
        return [str(root / sub) for sub in subs if (root / sub).is_dir()] if root else []

    def resolve(self, nickname: str) -> str | None:
        return (
            self.project_table.get(nickname)
            or _resolve_pretty(nickname, self.search_dirs)
            or self.global_table.get(nickname)
            or _resolve_pretty(nickname, self.stock_dirs)
        )


def _resolve_pretty_dir(library: str, pcb_path: str = "") -> str:
    """A .pretty directory from a nickname or a path.

    A nickname goes through _FpLibResolver, the order update_pcb_from_schematic
    uses, so a footprint placed by hand and the same footprint placed from a
    schematic resolve to the same file.
    """
    direct = Path(library)
    if direct.is_dir():
        return str(direct)
    project_dir = _project_dir(pcb_path) if pcb_path else None
    resolver = _FpLibResolver(project_dir, _fp_search_dirs(pcb_path))
    hit = resolver.resolve(library)
    if hit:
        return hit
    searched = ", ".join([*resolver.search_dirs, *resolver.stock_dirs])
    searched = searched or "nowhere: no board and no libraries configured"
    tables = len(resolver.project_table) + len(resolver.global_table)
    raise ToolError(
        f"Footprint library {library!r} not found. Looked for {library}.pretty in: {searched};"
        f" {tables} fp-lib-table row(s) name other libraries."
        " Pass a path to a .pretty directory instead of a nickname if it lives elsewhere."
    )


#: A footprint field as KiCad's own Update PCB from Schematic adds one, measured
#: on boards it wrote and checked against its source: hidden, on the fabrication
#: layer of the footprint's side, at the footprint's origin and at the
#: footprint's own angle (the writer stores a text's absolute board angle, so
#: the field turns with the footprint; the 180-degree sample this was first
#: measured on is the one angle that cannot tell this from a cancelling angle,
#: and KiCad's code rules the cancelling one out), with (unlocked yes), which
#: is Keep Upright OFF and not a free-to-rotate flag, at a 1 mm/0.15 mm text
#: style, and mirrored when it lands on the back.
_FP_FIELD_TPL = _cst.parse(
    b'(property "N" "V"\n\t\t\t(at 0 0 0)\n\t\t\t(unlocked yes)\n\t\t\t(layer "F.Fab")'
    b'\n\t\t\t(hide yes)\n\t\t\t(uuid "x")\n\t\t\t(effects\n\t\t\t\t(font'
    b"\n\t\t\t\t\t(size 1 1)\n\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)"
).lists[0]

#: KiCad's own order for the (attr ...) tokens, so a footprint flagged here reads
#: exactly as one KiCad flagged (the writer in pcb_io_kicad_sexpr.cpp). A token
#: this list does not know keeps its place after the known ones.
_ATTR_ORDER = (
    "smd",
    "through_hole",
    "board_only",
    "exclude_from_pos_files",
    "exclude_from_bom",
    "allow_missing_courtyard",
    "dnp",
    "allow_soldermask_bridges",
)

#: The attributes every netlist lets the schematic own. KiCad's update sets both
#: from the symbol every time, so clearing a box in the schematic clears the flag
#: on the board. exclude_from_pos_files joins them only when the netlist carries
#: KiCad 10's marker for it (parse_netlist answers a bool then, None on a KiCad 9
#: netlist, which has no such marker); every other token (the mounting type,
#: board_only, the courtyard and solder-mask choices) is the board's own and is
#: left exactly as found.
_SYMBOL_OWNED_ATTRS = ("exclude_from_bom", "dnp")


def _fp_rotation(fp) -> float:
    at = fp.find("at")
    return float(at.atoms[3].text) if at is not None and len(at.atoms) > 3 else 0.0


def _fp_property_node(fp, key: str):
    for prop in fp.find_all("property"):
        if len(prop.atoms) > 2 and prop.atoms[1].text == key:
            return prop
    return None


def _insert_fp_field(fp, node) -> None:
    """Splice a new field in where KiCad's writer puts user fields: after the
    last (property "..." ...) and before (property ki_fp_filters ...), which the
    writer emits last of the properties, just ahead of (path ...)."""
    props = [p for p in fp.find_all("property") if p.atoms[1].text != "ki_fp_filters"]
    fp.insert_after(props[-1] if props else fp.find("at"), node)


def _sync_fp_fields(fp, fields: dict) -> bool:
    """Every symbol field onto the footprint; True when any text moved.

    A field the footprint already carries (Datasheet and Description come with
    the library footprint, and a user field from an earlier import) is updated
    in place, keeping its position, layer and style. A new one is added in the
    template KiCad uses, where its writer puts user fields. Nothing is removed:
    a field that left the symbol stays on the footprint, which is also what a
    footprint keeps of its own library fields (measured: KiLib_Generator
    survives KiCad's own update, whose "Remove footprint fields not found in
    symbols" box is off by default), and removing text the user may have placed
    is not something an import should do unasked.
    """
    changed = False
    for name, value in fields.items():
        prop = _fp_property_node(fp, name)
        if prop is not None:
            if prop.atoms[2].text != value:
                prop.atoms[2].set_text(value)
                changed = True
            continue
        node = _FP_FIELD_TPL.copy()
        node.atoms[1].set_text(name)
        node.atoms[2].set_text(value)
        node.find("uuid").atoms[1].set_text(_gen_uuid())
        angle = _fp_rotation(fp) % 360
        if angle:
            node.find("at").atoms[3].set_text(_angle_text(angle))
        if _fp_layer(fp).startswith("B."):
            # KiCad's StyleFromSettings mirrors a text on a back layer, so the
            # field reads from the back like the library's own texts do after a
            # flip. Laid out as the writer lays it out, under the font.
            node.find("layer").atoms[1].set_text("B.Fab")
            mirror = _cst.parse(b"(justify mirror)").lists[0]
            node.find("effects").append_child(mirror, b"\n\t\t\t\t")
        _insert_fp_field(fp, node)
        changed = True
    return changed


def _set_fp_sheet(fp, head: str, value: str) -> bool:
    """(sheetname ...) or (sheetfile ...) set to what the netlist says.

    Set in place, inserted after (path ...) when new, and removed when the
    netlist's value is empty: KiCad's update assigns the value as it is and its
    writer leaves an empty one out.
    """
    existing = fp.find(head)
    if not value:
        if existing is None:
            return False
        fp.remove_child(existing)
        return True
    if existing is not None:
        if existing.atoms[1].text == value:
            return False
        existing.atoms[1].set_text(value)
        return True
    node = _cst.parse(f'({head} "x")'.encode()).lists[0]
    node.atoms[1].set_text(value)
    anchor = (fp.find("sheetname") if head == "sheetfile" else None) or fp.find("path")
    fp.insert_after(anchor or fp.find("at"), node)
    return True


def _set_fp_filters(fp, value: str) -> bool:
    """(property ki_fp_filters "...") set to the symbol's footprint filters.

    KiCad's update copies them onto the footprint (SetFilters) and its writer
    emits them as the last property, which in a footprint it wrote puts them
    just before (path ...); they are left out when empty. The same here, so a
    symbol without filters clears them.
    """
    node = _fp_property_node(fp, "ki_fp_filters")
    if not value:
        if node is None:
            return False
        fp.remove_child(node)
        return True
    if node is not None:
        if node.atoms[2].text == value:
            return False
        node.atoms[2].set_text(value)
        return True
    node = _cst.parse(b'(property ki_fp_filters "x")').lists[0]
    node.atoms[2].set_text(value)
    _insert_fp_field(fp, node)
    return True


def _sync_fp_attributes(
    fp, dnp: bool, exclude_from_bom: bool, exclude_from_pos_files: bool | None = None
) -> bool:
    """The symbol-owned (attr ...) tokens follow the symbol; True when they moved.

    exclude_from_pos_files is None when the netlist does not say (KiCad 9
    writes no marker for it), and the token is then the board's own, kept as
    found. The tokens are compared as a set, so a footprint whose (attr ...) a
    hand or another tool ordered differently is left alone until a flag
    actually changes, and only then rewritten in KiCad's order.
    """
    attr = fp.find("attr")
    tokens = [a.text for a in attr.atoms[1:]] if attr is not None else []
    owned: dict[str, bool] = dict(zip(_SYMBOL_OWNED_ATTRS, (exclude_from_bom, dnp)))
    if exclude_from_pos_files is not None:
        owned["exclude_from_pos_files"] = exclude_from_pos_files
    wanted = [tok for tok in tokens if tok not in owned]
    wanted += [tok for tok, on in owned.items() if on]
    if set(wanted) == set(tokens):
        return False
    ordered = [tok for tok in _ATTR_ORDER if tok in wanted]
    ordered += [tok for tok in wanted if tok not in _ATTR_ORDER]
    if attr is not None and not ordered:
        fp.remove_child(attr)
        return True
    node = _cst.parse(("(attr " + " ".join(ordered) + ")").encode()).lists[0]
    if attr is not None:
        node.sep = attr.sep
        fp.children[fp.children.index(attr)] = node
        return True
    anchor = fp.find("sheetfile") or fp.find("sheetname") or fp.find("path") or fp.find("at")
    fp.insert_after(anchor, node)
    return True


def _sync_fp_from_component(fp, comp: dict) -> bool:
    """Fields, footprint filters, sheet linkage and the symbol-owned attributes
    of one netlist component onto its footprint. Reference, Value, the library
    id and the path are the caller's; this is the rest of what KiCad's own
    update copies, short of the component class (dropped with the names KiCad
    handles apart, see _netlist_import) and KiCad 10's variant overrides,
    which are not applied. True when anything changed."""
    changed = _sync_fp_fields(fp, comp.get("fields", {}))
    changed = _set_fp_filters(fp, comp.get("fp_filters", "")) or changed
    changed = _set_fp_sheet(fp, "sheetname", comp.get("sheetname", "")) or changed
    changed = _set_fp_sheet(fp, "sheetfile", comp.get("sheetfile", "")) or changed
    flags = (
        comp.get("dnp", False),
        comp.get("exclude_from_bom", False),
        comp.get("exclude_from_pos_files"),
    )
    return _sync_fp_attributes(fp, *flags) or changed


def _copy_lib_footprint_cst(lib_path: str, name: str, fpid: str):
    """A board-ready footprint node copied out of a .kicad_mod, or None.

    The footprint twin of schematic._copy_lib_symbol_from_file_cst, and the
    reason it did not exist before is that a board footprint is not the library
    node with a different name on it. Both sides were measured rather than
    reasoned about, against KiCad's own multichannel_mixer-unrouted, whose
    Potentiometer_Alps_RK09K_Single_Vertical is directly comparable with the
    stock library file (17 fp_line, 5 pad and 1 fp_circle on both sides, so the
    geometry is identical and every remaining difference is the transform):

    - the name atom goes from "NAME" to "LIB:NAME"
    - version, generator and generator_version are dropped
    - (uuid ...) and (at ...) are added after (layer ...), in that order
    - every uuid already in the file is regenerated
    - every pad and text angle is spelled as KiCad writes it once it has loaded
      the library file (_respell_library_angles)

    Two further differences are the caller's, not this function's: a placed
    footprint gains path/sheetname/sheetfile and the Footprint/ki_fp_filters
    properties when it came from a schematic, and its pads gain
    (net ...)/(pinfunction ...)/(pintype ...) when they are bound to a net.
    A footprint placed directly has none of those, which is exactly what KiCad
    writes for one placed by hand.
    """
    path = Path(lib_path) / f"{name}.kicad_mod"
    if not path.is_file():
        return None
    node = _cst.parse(_read_kicad_bytes(str(path), "footprint")).lists[0]
    if node.head != "footprint":
        raise ToolError(f"{path.name} is not a KiCad footprint.")
    node = node.copy()
    node.atoms[1].set_text(fpid)
    for head in _LIB_ONLY_FOOTPRINT_HEADS:
        for stale in node.find_all(head):
            node.remove_child(stale)
    _regen_uuids(node)
    layer = node.find("layer")
    if layer is None:
        raise ToolError(f"{path.name} declares no layer.")
    # uuid then at, matching the order KiCad writes. The uuid is filled here
    # rather than by the caller: the template ships the literal "x", every
    # footprint placed would carry the same one, and nothing complains. kicad-cli
    # loaded a board with two identical placeholder uuids at rc 0.
    node.insert_after(layer, _cst.parse(b'(uuid "x")').lists[0])
    root_uuid = node.find("uuid")
    root_uuid.atoms[1].set_text(_gen_uuid())
    node.insert_after(root_uuid, _cst.parse(b"(at 0 0)").lists[0])
    _respell_library_angles(node)
    return node


# ---------------------------------------------------------------------------
# A footprint's orientation, as KiCad turns it
# ---------------------------------------------------------------------------
#
# What a board file stores (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp, 9.0.8
# lines; the rules are the same at 10.0.7): the footprint's (at x y A) holds the
# orientation Normalize180'd, left out at 0 (1127-1131); a pad's (at x y P) holds
# its ABSOLUTE angle in [0, 360), left out at 0 (1492-1496); a field's or an
# fp_text's (at x y T) holds its absolute angle, always written (1992-1994). Every
# position in them is footprint-relative, so turning a footprint rewrites angle
# atoms and nothing else of its pads, texts and graphics.


#: A token KiCad's lexer reads as a NUMBER (DSNLEXER's isNumber,
#: common/dsnlexer.cpp:495-542 at 9.0.8), which is what the parser takes an
#: angle atom from.
_NUMBER_TEXT = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _at_angle_atom(at):
    """The angle atom of an (at x y [angle] ...) list, or None when it has none."""
    atoms = at.atoms
    if len(atoms) > 3 and _NUMBER_TEXT.fullmatch(atoms[3].text):
        return atoms[3]
    return None


def _set_at_angle(at, text: str | None) -> None:
    """Write the angle atom of an (at x y ...) list: rewritten in place, inserted
    after y with the single space KiCad's writer puts there (``(at %s %s)``),
    before a legacy bare ``unlocked``, or removed with the space before it when
    ``text`` is None. An atom that already reads ``text`` is not touched."""
    atom = _at_angle_atom(at)
    if text is None:
        if atom is not None:
            at.remove_child(atom)
        return
    if atom is not None:
        if atom.text != text:
            atom.set_text(text)
        return
    y = at.atoms[2]
    atom = y.copy()
    atom.sep = b" "
    atom.set_text(text)
    at.children.insert(at.children.index(y) + 1, atom)


#: Footprint properties KiCad does not hold as texts: the footprint's filters, and
#: the Footprint field it drops since version 9 (pcb_io_kicad_sexpr_parser.cpp:
#: 4611-4636 at 9.0.8, 5306-5321 at 10.0.7).
_NOT_FP_TEXTS = ("ki_fp_filters", "Footprint")


def _fp_texts(fp) -> list:
    """The texts KiCad turns with a footprint that carry an (at): its fields,
    written as (property ...), and its fp_text items. A text without (at), such as
    the bare (property "Reference" "R1") kiutils writes, has no angle written to
    turn and is left as it is; so is an fp_text_box, whose corners are local and
    whose angle KiCad stores relative to the footprint."""
    texts = []
    for child in fp.lists:
        if child.head == "property":
            if len(child.atoms) < 2 or child.atoms[1].text in _NOT_FP_TEXTS:
                continue
        elif child.head != "fp_text":
            continue
        if child.find("at") is not None:
            texts.append(child)
    return texts


def _text_keeps_upright(text) -> bool:
    """Whether KiCad keeps a footprint text upright: it does unless (unlocked yes),
    or a bare ``unlocked`` after the angle in a legacy (at ...), turns that off,
    the last one written winning (parsePCB_TEXT_effects,
    pcb_io_kicad_sexpr_parser.cpp:3438-3576 at 9.0.8, 3955-4093 at 10.0.7)."""
    upright = True
    for child in text.lists:
        if child.head == "at":
            if any(atom.text == "unlocked" for atom in child.atoms[3:]):
                upright = False
        elif child.head == "unlocked" and len(child.atoms) > 1:
            upright = child.atoms[1].text != "yes"
    return upright


#: KeepUpright negates both justifications: left and right swap, top and bottom
#: swap, centre (no word) stays (GR_TEXT_H_ALIGN_T and GR_TEXT_V_ALIGN_T hold
#: them as -1, 0 and 1).
_JUSTIFY_FLIP = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}


def _justify_words(effects) -> list[str]:
    """The justification KiCad reads from a text's (effects ...), as the words
    EDA_TEXT::Format writes for it, in its order: horizontal, vertical, mirror
    (common/eda_text.cpp:1096-1110 at 9.0.8, 1078-1092 at 10.0.7)."""
    h = v = None
    mirror = False
    for node in effects.find_all("justify"):
        for atom in node.atoms[1:]:
            if atom.text in ("left", "right"):
                h = atom.text
            elif atom.text in ("top", "bottom"):
                v = atom.text
            elif atom.text == "mirror":
                mirror = True
    return [word for word in (h, v, "mirror" if mirror else None) if word]


def _set_justify(effects, words: list[str]) -> None:
    """Write a text's (justify ...) words. An existing node keeps its place, its
    leading whitespace and its close: only its words change, one space apart.
    With no node, one goes in after (font ...) with the font node's leading
    whitespace, which is where and how pcbnew writes it. With no words, the node
    goes, its whitespace with it. A second (justify ...), which KiCad never
    writes, is folded into the first."""
    nodes = effects.find_all("justify")
    if not nodes:
        if not words:
            return
        node = _cst.parse(("(justify " + " ".join(words) + ")").encode()).lists[0]
        font = effects.find("font")
        if font is not None:
            effects.insert_after(font, node)
        else:
            effects.append_child(node, b" ")
        return
    first, *extra = nodes
    for node in extra:
        effects.remove_child(node)
    if not words:
        effects.remove_child(first)
        return
    old = first.atoms[1:]
    if len(old) == len(words):
        for atom, word in zip(old, words):
            if atom.text != word:
                atom.set_text(word)
        return
    first.children[1:] = [_cst.Atom(word.encode(), b" ") for word in words]


def _respell_library_angles(node) -> None:
    """Spell each pad and text angle of a library footprint just copied as KiCad
    writes it after loading the library file: a pad's Normalize( angle ), left
    out at 0, and a text's Normalize( angle ), always written (the parser's
    PADSTACK::SetOrientation, pcbnew/padstack.h:294-298 at 9.0.8, 348-352 at
    10.0.7, and its text arithmetic at orientation 0). KiCad 9.0.8's stock
    libraries write some text angles as -90, which KiCad writes back as 270. The
    node is a new copy, so this changes no byte of the user's board."""
    for pad in node.find_all("pad"):
        at = pad.find("at")
        atom = _at_angle_atom(at) if at is not None else None
        if atom is not None:
            angle = _n360(float(atom.text))
            _set_at_angle(at, None if angle == 0 else _angle_text(angle))
    for text in _fp_texts(node):
        at = text.find("at")
        atom = _at_angle_atom(at)
        _set_at_angle(at, _angle_text(_n360(float(atom.text) if atom is not None else 0.0)))


def _board_point_nodes(fp):
    """The lists of a footprint that hold a point in BOARD coordinates, as (x y ...):
    every (xy) of its zones' outlines and fills and the start, mid and end of an (arc)
    among them, and the (at) of each KiCad 10 (point). KiCad writes a footprint's zones
    and points without the footprint (format( ZONE ), pcb_io_kicad_sexpr.cpp:2628-2637
    at 9.0.8, 3061-3074 at 10.0.7; format( PCB_POINT ), 1157-1168 at 10.0.7), where its
    pads, texts and graphics are footprint-relative. A KiCad 10 zone's (property (layer)
    (hatch_position (xy))) is an offset, which ZONE::Move, Rotate and Flip leave alone,
    and is not among them."""
    for zone in fp.find_all("zone"):
        for polygon in zone.find_all("polygon") + zone.find_all("filled_polygon"):
            pts = polygon.find("pts")
            for entry in pts.lists if pts is not None else ():
                if entry.head == "xy":
                    yield entry
                elif entry.head == "arc":
                    yield from (p for p in entry.lists if p.head in ("start", "mid", "end"))
    for point in fp.find_all("point"):
        at = point.find("at")
        if at is not None:
            yield at


def _board_nm(node, major: int) -> tuple[int, int]:
    """The board point an (x y ...) list holds, read as KiCad ``major``'s parser reads
    it: KiROUND( mm * 1e6 ) (parseBoardUnits; its clamp is not modelled)."""
    rnd = _kiround_for(major)
    return rnd(float(node.atoms[1].text) * 1e6), rnd(float(node.atoms[2].text) * 1e6)


def _move_board_points(fp, move, major: int) -> None:
    """Rewrite every board-coordinate point of ``fp`` (_board_point_nodes) to
    ``move( x, y )``, in integer nanometres: a coordinate is written with _mm, KiCad's
    text for it, only where its value changes, so a point that stays keeps its bytes."""
    for node in _board_point_nodes(fp):
        old = _board_nm(node, major)
        new = move(*old)
        for k in (0, 1):
            if new[k] != old[k]:
                node.atoms[k + 1].set_text(_mm(new[k]))


def _turn_about(centre: tuple[int, int], degrees: float, major: int):
    """KiCad ``major``'s RotatePoint about ``centre`` (trigo.cpp:267-278 at 9.0.8): the
    offset from the centre turned by the integer RotatePoint, the centre added back."""

    def turn(x: int, y: int) -> tuple[int, int]:
        rx, ry = _rotate_point_iu(x - centre[0], y - centre[1], degrees, major=major)
        return rx + centre[0], ry + centre[1]

    return turn


def _fp_translate(fp, x_text: str, y_text: str, major: int) -> None:
    """FOOTPRINT::SetPosition in file terms: the footprint's own (at) x and y, and every
    zone and KiCad 10 point it owns moved by the same delta, since those are board
    coordinates (pcbnew/footprint.cpp:2553-2584 at 9.0.8, 3002-3039 at 10.0.7;
    ZONE::Move, pcbnew/zone.cpp:904-929 at 9.0.8). The delta is between KiCad
    ``major``'s readings of the old and new atoms, in integer nanometres. Pads, texts
    and graphics are footprint-relative, so they move with it and nothing of theirs is
    rewritten."""
    at = fp.find("at")
    old = _board_nm(at, major)
    at.atoms[1].set_text(x_text)
    at.atoms[2].set_text(y_text)
    new = _board_nm(at, major)
    dx, dy = new[0] - old[0], new[1] - old[1]
    if dx or dy:
        _move_board_points(fp, lambda x, y: (x + dx, y + dy), major)


def _fp_turn(fp, target: float | None, major: int, upright=None) -> None:
    """FOOTPRINT::SetOrientation( target ) in file terms (pcbnew/footprint.cpp:2641-2669
    at 9.0.8, 3099-3137 at 10.0.7), then KeepUpright on each text ``upright( text )``
    names, on every text when ``upright`` is None; with ``target`` None, KeepUpright
    alone. One pass, so every angle is written once, from the double it ends at.

    The change is ``target`` minus the orientation the footprint holds, A, the
    Normalize180 of its angle atom (0 without one). The footprint's angle becomes
    Normalize180( target ), written by FormatAngle and left out at 0. Every pad's
    absolute angle P becomes Normalize( Normalize( P ) + change ) (PAD::Rotate,
    pcbnew/pad.cpp:1614-1620 at 9.0.8, 2183-2189 at 10.0.7), left out at 0. Every field
    and fp_text with an (at) takes Normalize( T + change ), T being what the parser holds
    for its written angle, Normalize( ( T - A ) + A ) (PCB_TEXT::Rotate,
    pcbnew/pcb_text.cpp:381-390 at 9.0.8, 460-469 at 10.0.7), and its angle is written.
    KeepUpright turns a keep-upright text at 180 or more half a turn and negates its
    justification (321-339 at 9.0.8, 389-407 at 10.0.7). Positions, graphics, pad shapes
    and text boxes are footprint-relative and stay as they are. The zones and KiCad 10
    points the footprint owns are board coordinates, which SetOrientation turns about the
    footprint's position by the change with the integer RotatePoint (ZONE::Rotate,
    pcbnew/zone.cpp:959-967 at 9.0.8; PCB_POINT::Rotate, pcbnew/pcb_point.cpp:138-141 at
    10.0.7), rounded as KiCad ``major`` rounds.
    """
    at = fp.find("at")
    atom = _at_angle_atom(at)
    held = _n180(float(atom.text)) if atom is not None else 0.0
    change = 0.0
    if target is not None:
        stored = _n180(target)
        change = target - held
        _set_at_angle(at, None if stored == 0 else _angle_text(stored))
        _move_board_points(fp, _turn_about(_board_nm(at, major), change, major), major)
        for pad in fp.find_all("pad"):
            pad_at = pad.find("at")
            if pad_at is None:
                continue
            pad_atom = _at_angle_atom(pad_at)
            angle = _n360((_n360(float(pad_atom.text)) if pad_atom is not None else 0.0) + change)
            _set_at_angle(pad_at, None if angle == 0 else _angle_text(angle))
    for text in _fp_texts(fp):
        text_at = text.find("at")
        text_atom = _at_angle_atom(text_at)
        written = float(text_atom.text) if text_atom is not None else 0.0
        angle = _n360(_n360((written - held) + held) + change)
        turned = angle >= 180.0 and _text_keeps_upright(text) and (upright is None or upright(text))
        if turned:
            angle = _n360(angle + 180.0)
            effects = text.find("effects")
            if effects is not None:
                words = _justify_words(effects)
                negated = [_JUSTIFY_FLIP.get(word, word) for word in words]
                if negated != words:
                    _set_justify(effects, negated)
        if target is not None or turned:
            _set_at_angle(text_at, _angle_text(angle))


def _fp_rotate_to(fp, target: float, major: int) -> None:
    """Turn a footprint to the angle ``target`` as KiCad's FOOTPRINT::Rotate turns
    it about its own position: what the editor's rotate command and its footprint
    properties dialog run (pcbnew/footprint.cpp:2456-2479 at 9.0.8, 2898-2918 at
    10.0.7): SetOrientation to ``target``, then KeepUpright on every field and fp_text
    (_fp_turn).

    Rotate, with KeepUpright, rather than SetOrientation, without it: Rotate is
    what KiCad writes when a user turns a part, and only its result keeps a left-
    or right-justified text on the same side of its anchor after a half turn.

    A target the footprint already holds (Normalize180 gives the same angle, so
    270 for a footprint at -90, or 360 for one at 0) changes nothing at all.
    KiCad's own Rotate returns at once only for a change of exactly 0, and its
    properties dialog compares the typed angle, normalized into [0, 360), with
    the stored one in (-180, 180], so for a footprint at -90 and a typed 270 it
    runs Rotate( 360 ), whose KeepUpright can turn texts nobody asked to turn
    (dialogs/dialog_footprint_properties.cpp:636-639 at 9.0.8). A rotation names
    the footprint's final angle here, and a request that leaves it where it is
    leaves the file byte-identical.
    """
    atom = _at_angle_atom(fp.find("at"))
    held = _n180(float(atom.text)) if atom is not None else 0.0
    if _n180(target) == held:
        return
    _fp_turn(fp, target, major)


def _require_finite_rotation(rotation: float | None) -> None:
    """Refuse a rotation of inf, -inf or nan before anything is touched. KiCad's
    parser takes a footprint's angle only as a NUMBER token, so a board holding
    one at such an angle does not load at all (kicad-cli 9.0.8 writes no report
    for it, and pcbnew's LoadBoard returns None; measured 2026-10-11)."""
    if rotation is not None and not math.isfinite(rotation):
        raise ToolError(
            f"rotation must be a finite angle in degrees, not {rotation}. KiCad cannot load"
            " a footprint at that angle, so nothing was written."
        )


def _splice_after(root, node, heads, tail_heads) -> None:
    """Insert after the last *heads* child, else before the first *tail_heads*."""
    anchors = [c for c in root.lists if c.head in heads]
    if anchors:
        root.insert_after(anchors[-1], node)
        return
    tail = next((c for c in root.lists if c.head in tail_heads), None)
    if tail is not None:
        root.insert_before(tail, node)
    else:
        root.append_child(node, b"\n\t")


def _resolve_net_cst(root, net_name: str) -> int:
    """Net number for *net_name*, or ToolError listing the available names."""
    for num, name in _net_table(root):
        if name == net_name:
            return num
    available = [name for _, name in _net_table(root) if name]
    raise ToolError(f"Net {net_name!r} not found. Available nets: {available}")


def _board_layers(root) -> list[str]:
    """Canonical layer names from the board's own stackup table.

    A row is ``(31 "B.Cu" signal)``, optionally with a fourth atom holding a
    user-facing alias: ``(36 "B.SilkS" user "B.Silkscreen")``. Only ``atoms[1]``
    is the name items reference in their own ``(layer ...)``, so the alias is
    deliberately not returned.
    """
    layers = root.find("layers")
    return [layer.atoms[1].text for layer in (layers.lists if layers is not None else ())]


def _resolve_layer_cst(root, layer: str, *, copper_only: bool = False) -> str:
    """*layer* as-is, or ToolError listing what the board actually defines.

    An enum cannot express this: the legal set is per-board and users rename
    layers, so the truth is the table inside the file being edited. Same shape
    as _resolve_net_cst, and for the same reason.

    Unvalidated, a typo reached the disk verbatim. Measured 2026-08-12:
    add_trace(layer="banana") wrote (layer banana) and kicad-cli then refused
    the board entirely with "Failed to load board".

    copper_only tests the ``.Cu`` suffix rather than the row's type atom,
    because KiCad writes ``power``, ``mixed`` and ``jumper`` for inner copper
    and a type test would reject a legal power plane.
    """
    available = _board_layers(root)
    if layer not in available:
        raise ToolError(
            f"Layer {layer!r} is not defined on this board. Available layers: {available}"
        )
    if copper_only and not layer.endswith(".Cu"):
        copper = [name for name in available if name.endswith(".Cu")]
        raise ToolError(f"Layer {layer!r} is not a copper layer. Copper layers: {copper}")
    return layer


def _filter_segments_cst(root, net_name, layer, x_min, y_min, x_max, y_max) -> list:
    """CST twin of the retired _filter_segments: segment nodes matching filters."""
    if all(v is None for v in (net_name, layer, x_min, y_min, x_max, y_max)):
        raise ToolError("at least one filter is required")
    net_num = None
    name_to_num = {name: num for num, name in _net_table(root)}
    if net_name is not None:
        net_num = _resolve_net_cst(root, net_name)
    # Symmetric with the net check above. Unvalidated, a typo'd layer matched
    # nothing and came back as "removed 0", which reads as "there was nothing
    # there" rather than "you misspelled it".
    if layer is not None:
        _resolve_layer_cst(root, layer)
    result = []
    for item in root.lists:
        if item.head != "segment":
            continue
        if net_num is not None and _item_net_number(item.find("net"), name_to_num) != net_num:
            continue
        if layer is not None and item.find("layer").atoms[1].text != layer:
            continue
        if x_min is not None or y_min is not None or x_max is not None or y_max is not None:
            start, end = item.find("start"), item.find("end")
            sx, sy = float(start.atoms[1].text), float(start.atoms[2].text)
            ex, ey = float(end.atoms[1].text), float(end.atoms[2].text)
            if x_min is not None and (sx < x_min or ex < x_min):
                continue
            if y_min is not None and (sy < y_min or ey < y_min):
                continue
            if x_max is not None and (sx > x_max or ex > x_max):
                continue
            if y_max is not None and (sy > y_max or ey > y_max):
                continue
        result.append(item)
    return result


# Zone templates follow the measured native shapes: KiCad 9 solid connect is
# "(connect_pads yes ...)" (measured locally via pcbnew 9, kiutils' "full" is
# wrong); the KiCad 10 dialect (slice-14 probe) drops net_name and
# filled_areas_thickness and uses name-only (net "NAME").
#
# The bare "yes" after (fill is the pour-enabled flag, and this template omitted
# it until 2026-08-14. All 20 boards KiCad ships write "(fill yes ...)"; ours
# wrote "(fill (thermal_gap ...) ...)", which is the same shape KiCad writes for
# a zone whose pour is switched off. What that costs is NOT established: taking
# KiCad's own ecc83-pp and stripping the yes changed the exported F.Cu gerber by
# 2 bytes, because the plot path reads stored filled_polygon data and never
# consults the flag. Whether ZONE_FILLER and the GUI honour it is unmeasured;
# the direct probe crashed pcbnew on the Windows box. Fixed regardless, because
# this repo's rule is that emitted constructs match KiCad's own output.
_COPPER_ZONE_TPL = _cst.parse(
    b'(zone\n\t\t(net 0)\n\t\t(net_name "x")\n\t\t(layer "F.Cu")\n\t\t(uuid "x")'
    b"\n\t\t(hatch edge 0.5)\n\t\t(priority 0)"
    b"\n\t\t(connect_pads\n\t\t\t(clearance 0.5)\n\t\t)"
    b"\n\t\t(min_thickness 0.25)\n\t\t(filled_areas_thickness no)"
    b"\n\t\t(fill yes\n\t\t\t(thermal_gap 0.5)\n\t\t\t(thermal_bridge_width 0.5)\n\t\t)"
    b"\n\t\t(polygon\n\t\t\t(pts\n\t\t\t\t(xy 0 0)\n\t\t\t)\n\t\t)\n\t)"
).lists[0]

_KEEPOUT_ZONE_TPL = _cst.parse(
    b'(zone\n\t\t(net 0)\n\t\t(net_name "")\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(uuid "x")'
    b"\n\t\t(hatch edge 0.5)"
    b"\n\t\t(connect_pads\n\t\t\t(clearance 0)\n\t\t)"
    b"\n\t\t(min_thickness 0.25)"
    b"\n\t\t(keepout\n\t\t\t(tracks not_allowed)\n\t\t\t(vias not_allowed)"
    b"\n\t\t\t(pads not_allowed)"
    b"\n\t\t\t(copperpour not_allowed)\n\t\t\t(footprints not_allowed)\n\t\t)"
    b"\n\t\t(polygon\n\t\t\t(pts\n\t\t\t\t(xy 0 0)\n\t\t\t)\n\t\t)\n\t)"
).lists[0]


def _fill_zone_polygon(node, corners: list[PointSpec]) -> None:
    """Fill the template's single (xy) with corner 0 and clone the rest inline."""
    pts = node.find("polygon").find("pts")
    first = pts.find("xy")
    first.atoms[1].set_text(_num(corners[0]["x"], PCB_IU_PER_MM))
    first.atoms[2].set_text(_num(corners[0]["y"], PCB_IU_PER_MM))
    anchor = first
    for c in corners[1:]:
        xy = first.copy()
        xy.atoms[1].set_text(_num(c["x"], PCB_IU_PER_MM))
        xy.atoms[2].set_text(_num(c["y"], PCB_IU_PER_MM))
        pts.insert_after(anchor, xy, sep=b" ")
        anchor = xy


def _splice_pcb_zone(root, node) -> None:
    anchors = [c for c in root.lists if c.head == "zone"]
    if anchors:
        root.insert_after(anchors[-1], node)
    else:
        _splice_pcb_node(root, node)


_GR_TEXT_TPL = _cst.parse(
    b'(gr_text "x"\n\t\t(at 0 0 0)\n\t\t(layer "F.SilkS")\n\t\t(uuid "x")'
    b"\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t)\n\t)"
).lists[0]

_GR_LINE_TPL = _cst.parse(
    b"(gr_line\n\t\t(start 0 0)\n\t\t(end 0 0)"
    b"\n\t\t(stroke\n\t\t\t(width 0.05)\n\t\t\t(type default)\n\t\t)"
    b'\n\t\t(layer "Edge.Cuts")\n\t\t(uuid "x")\n\t)'
).lists[0]


# ---------------------------------------------------------------------------
# PCB read tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ_ONLY)
def list_pcb_footprints(pcb_path: str = PCB_PATH) -> list[PcbFootprintItem]:
    """List all footprints on the PCB.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    items: list[PcbFootprintItem] = []
    for fp in root.find_all("footprint"):
        at = fp.find("at")
        items.append(
            PcbFootprintItem(
                reference=_fp_prop_cst(fp, "Reference"),
                value=_fp_prop_cst(fp, "Value"),
                lib_id=fp.atoms[1].text,
                x=float(at.atoms[1].text),
                y=float(at.atoms[2].text),
                rotation=float(at.atoms[3].text) if len(at.atoms) > 3 else 0,
                layer=_fp_layer(fp),
            )
        )
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_pcb_traces(pcb_path: str = PCB_PATH) -> list[TraceSegmentItem]:
    """List all trace segments and vias on the PCB.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    name_to_num = {name: num for num, name in _net_table(root)}
    items: list[TraceSegmentItem] = []
    for item in root.lists:
        if item.head == "segment":
            start, end = item.find("start"), item.find("end")
            items.append(
                TraceSegmentItem(
                    type="segment",
                    start_x=float(start.atoms[1].text),
                    start_y=float(start.atoms[2].text),
                    end_x=float(end.atoms[1].text),
                    end_y=float(end.atoms[2].text),
                    width=float(item.find("width").atoms[1].text),
                    layer=item.find("layer").atoms[1].text,
                    net=_item_net_number(item.find("net"), name_to_num),
                )
            )
        elif item.head == "via":
            at = item.find("at")
            items.append(
                TraceSegmentItem(
                    type="via",
                    x=float(at.atoms[1].text),
                    y=float(at.atoms[2].text),
                    size=float(item.find("size").atoms[1].text),
                    drill=float(item.find("drill").atoms[1].text),
                    layers=[a.text for a in item.find("layers").atoms[1:]],
                    net=_item_net_number(item.find("net"), name_to_num),
                )
            )
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_pcb_nets(pcb_path: str = PCB_PATH) -> list[NetItem]:
    """List all named nets on the PCB.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    return [NetItem(number=num, name=name) for num, name in _net_table(root) if name]


@mcp.tool(annotations=_READ_ONLY)
def list_pcb_zones(pcb_path: str = PCB_PATH) -> list[ZoneItem]:
    """List all zones (copper and keepout) on the PCB.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    version, bound = _board_version(root), _bound_names(root)
    items: list[ZoneItem] = []
    for z in root.find_all("zone"):
        ko = z.find("keepout")
        keepout = _keepout_dict(ko) if ko is not None else None
        polygon = None
        poly = z.find("polygon")
        if poly is not None:
            polygon = [
                {"x": _numish(p.atoms[1].text), "y": _numish(p.atoms[2].text)}
                for p in poly.find("pts").find_all("xy")
            ]
        # The net as the KiCad that wrote the zone's format reads it: KiCad 9 by the
        # net_name, KiCad 10 by the (net "NAME") a zone on a net carries.
        zone_net = _read_net_name(z, version, bound)
        layers_node = z.find("layers") or z.find("layer")
        priority = z.find("priority")
        items.append(
            ZoneItem(
                net_name=zone_net,
                layers=[a.text for a in layers_node.atoms[1:]] if layers_node is not None else [],
                priority=int(priority.atoms[1].text) if priority is not None else 0,
                is_keepout=ko is not None,
                keepout=keepout,
                polygon=polygon,
            )
        )
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_pcb_layers(pcb_path: str = PCB_PATH) -> list[LayerItem]:
    """List all layers defined in the PCB stackup: copper, silkscreen,
    soldermask, solder paste, courtyard, fabrication, and the board outline.

    Which of those a given board actually defines is up to the board. Call
    this before any tool that takes a layer name, since the valid set is
    per-board and renamed layers are common.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    layers = root.find("layers")
    items: list[LayerItem] = []
    for layer in layers.lists if layers is not None else ():
        a = layer.atoms
        items.append(LayerItem(ordinal=int(a[0].text), name=a[1].text, type=a[2].text))
    return items


@mcp.tool(annotations=_READ_ONLY)
def list_pcb_graphic_items(pcb_path: str = PCB_PATH) -> list[GraphicItem]:
    """List all graphic items (lines, text, etc.) on the PCB.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    items: list[GraphicItem] = []
    for item in root.lists:
        head = item.head
        layer_node = item.find("layer")
        layer = layer_node.atoms[1].text if layer_node is not None else "unknown"
        if head == "gr_line":
            start, end = item.find("start"), item.find("end")
            items.append(
                GraphicItem(
                    type="line",
                    start_x=float(start.atoms[1].text),
                    start_y=float(start.atoms[2].text),
                    end_x=float(end.atoms[1].text),
                    end_y=float(end.atoms[2].text),
                    layer=layer,
                )
            )
        elif head == "gr_text":
            at = item.find("at")
            items.append(
                GraphicItem(
                    type="text",
                    text=item.atoms[1].text,
                    x=float(at.atoms[1].text),
                    y=float(at.atoms[2].text),
                    layer=layer,
                )
            )
        elif head in _GRAPHIC_CLASS:
            items.append(GraphicItem(type=_GRAPHIC_CLASS[head], layer=layer))
    return items


@mcp.tool(annotations=_READ_ONLY)
def get_board_info(pcb_path: str = PCB_PATH) -> str:
    """Get board summary: footprint count, trace count, net count, thickness.
    The net count includes the unconnected net, as KiCad's own count does.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    counts = {"segment": 0, "via": 0, "footprint": 0, "zone": 0}
    for item in root.lists:
        if item.head in counts:
            counts[item.head] += 1
    general = root.find("general")
    thickness = general.find("thickness") if general is not None else None
    tval = _numish(thickness.atoms[1].text) if thickness is not None else 1.6
    return (
        f"Footprints: {counts['footprint']}\n"
        f"Traces: {counts['segment']}\n"
        f"Vias: {counts['via']}\n"
        f"Nets: {len({name for _, name in _net_table(root) if name}) + 1}\n"
        f"Zones: {counts['zone']}\n"
        f"Thickness: {tval}mm"
    )


@mcp.tool(annotations=_READ_ONLY, title="Pads of a footprint placed on the board")
def get_footprint_pads(reference: str, pcb_path: str = PCB_PATH) -> str:
    """Get pad info for a placed footprint on the PCB.

    Args:
        reference: Footprint reference (e.g. "R1", "U1")
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    fp = _find_fp_cst(root, reference)
    fp_at = fp.find("at")
    fp_x, fp_y = _xy(fp_at)
    fp_angle = float(fp_at.atoms[3].text) if len(fp_at.atoms) > 3 else 0
    major = _board_major(root)
    version, bound = _board_version(root), _bound_names(root)
    lines = [
        f"{reference} pads "
        f"(footprint origin ({_numish(fp_x)}, {_numish(fp_y)}), "
        f"rotation {_numish(fp_angle)}, layer {_fp_layer(fp)}):"
    ]
    for pad in fp.find_all("pad"):
        net_name = _read_net_name(pad, version, bound) or "none"
        at, size = pad.find("at"), pad.find("size")
        layers = pad.find("layers")
        local_x, local_y = _xy(at)
        # Board coordinates first: a caller routing to this pad needs those, and
        # reporting only the footprint-local pair invites placing a trace at the
        # wrong end of the board on any rotated part.
        board_x, board_y = _transform_local_to_board(
            fp_x, fp_y, fp_angle, local_x, local_y, major=major
        )
        lines.append(
            f"  Pad {pad.atoms[1].text}: {pad.atoms[2].text} {pad.atoms[3].text} "
            f"@ board ({_numish(round(board_x, 6))}, {_numish(round(board_y, 6))}) "
            f"local ({_numish(local_x)}, {_numish(local_y)}) "
            f"size=({_numish(size.atoms[1].text)}, {_numish(size.atoms[2].text)}) "
            f"layers={[a.text for a in layers.atoms[1:]] if layers is not None else []} "
            f"net={net_name}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PCB write tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_ADDITIVE)
def place_footprint(
    reference: str,
    value: str,
    x: float,
    y: float,
    rotation: float = 0,
    layer: str = "F.Cu",
    library: str = "",
    footprint: str = "",
    pcb_path: str = PCB_PATH,
) -> str:
    """Place a footprint on the PCB.

    Name a library and a footprint to place the real thing: its pads, silkscreen,
    courtyard and 3D model are copied from the library file exactly as KiCad
    would place them, a footprint in KiCad 7's form taking the reference and value
    in its own (fp_text reference|value ...), where one holding %V or %R, which KiCad
    reads there as a variable, is refused. Without them you get a marker carrying
    only the reference and value, which has NO PADS and so cannot be routed or
    checked; that is the older behaviour and it is kept for callers that only want a
    placeholder.

    Use list_lib_footprints to see what a library holds.

    The footprint is turned to its rotation as KiCad's own rotate turns it: every
    pad and text turns with it (a pad's angle is its library angle plus the
    footprint's), and a keep-upright text that would read upside down is turned
    half a turn with its justification mirrored, so it stays readable on the same
    side of its anchor. Angles are written as KiCad writes them: the footprint's
    between -180 and 180 and left out at 0, each pad's and text's absolute. Off the
    quarter turns the result reads as KiCad's placement without matching pcbnew's
    save byte for byte: footprint-relative coordinates stay as the library writes
    them where pcbnew re-derives them through two RotatePoint roundings, an fp_rect
    stays where pcbnew writes an fp_poly, and arc mid points stay as written. A
    library footprint without a Datasheet or Description field is placed without
    it, so KiCad reads that hidden field at its default on F.Fab, whereas pcbnew
    writes it, on B.Fab for a placement on the back.

    Zones the footprint owns (keepouts, say) and a KiCad 10 footprint's points are
    stored in board coordinates (format( ZONE ), pcb_io_kicad_sexpr.cpp:2628-2637 at
    9.0.8), so they land where KiCad's placement puts them: moved with the footprint,
    turned about it and, on the back, mirrored with it.

    A footprint carrying what this tool does not move with it (a dimension, image,
    table or barcode, or a child no KiCad it knows reads), or on the back what it does
    not flip (see move_footprint), is refused and nothing is written.

    Args:
        reference: Reference designator (e.g. "R2")
        value: Component value (e.g. "4.7K")
        x: X position in mm
        y: Y position in mm
        rotation: The footprint's angle in degrees, counter-clockwise as seen on
            screen; any finite value (270 is stored as -90, 360 as 0). inf and nan
            are refused and nothing is written.
        layer: F.Cu or B.Cu; any other layer is refused. B.Cu places the footprint
            flipped as KiCad's own flip does, then turns it to rotation
            (FOOTPRINT::Flip, pcbnew/footprint.cpp:2491-2550 at 9.0.8).
        library: Footprint library nickname (e.g. "Resistor_SMD"), or a path to a
            .pretty directory. A nickname is resolved as update_pcb_from_schematic
            resolves it: the project's fp-lib-table beside the board, .pretty
            directories beside the board, KICAD_FP_LIB, the user's global
            fp-lib-table (with the libraries the Plugin and Content Manager
            installed), then KiCad's stock footprints. Optional; omit for a
            pad-less marker.
        footprint: Footprint name within that library (e.g. "R_0805_2012Metric").
            Required when library is given.
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    if bool(library) != bool(footprint):
        raise ToolError(
            "library and footprint go together: name both to place a real footprint,"
            " or neither for a pad-less marker."
        )
    _require_finite_rotation(rotation)
    x, y = _snap(x, PCB_IU_PER_MM), _snap(y, PCB_IU_PER_MM)
    tree, root, key = _open_pcb_cst(pcb_path)
    # Every check before the cache pop, so a refusal leaves the cached tree valid.
    _resolve_layer_cst(root, layer, copper_only=True)
    _require_side(layer)
    if library:
        pretty = _resolve_pretty_dir(library, pcb_path)
        node = _copy_lib_footprint_cst(pretty, footprint, f"{Path(pretty).stem}:{footprint}")
        if node is None:
            raise ToolError(
                f"Footprint {footprint!r} not found in {pretty}. Use list_lib_footprints"
                " to see what the library holds."
            )
    else:
        node = _FOOTPRINT_TPL.copy()
        node.find("uuid").atoms[1].set_text(_gen_uuid())
        for prop in node.find_all("property"):
            prop.find("uuid").atoms[1].set_text(_gen_uuid())
    layers = _board_flip_layers(root)
    flipping = (layer == "B.Cu") != _fp_is_back(node)
    reason = _fp_unsupported(node, layers, flipping)
    if reason:
        raise ToolError(
            f"{reference} would carry {reason}. Nothing was written; place it in KiCad instead."
        )
    for field, text in (("Reference", reference), ("Value", value)):
        refusal = _legacy_field_refusal(node, field, text)
        if refusal:
            raise ToolError(refusal)
    _BOARD_CACHE.pop(key, None)
    _set_fp_property(node, "Reference", reference)
    _set_fp_property(node, "Value", value)
    _fp_translate(node, _num(x, PCB_IU_PER_MM), _num(y, PCB_IU_PER_MM), layers.major)
    if flipping:
        _fp_flip(node, layers)
    _fp_rotate_to(node, rotation, layers.major)
    _splice_after(root, node, ("footprint",), _PCB_TAIL_HEADS)
    _atomic_write(key, _cst.serialize(tree))
    what = f"{Path(pretty).stem}:{footprint}" if library else "a pad-less marker"
    return f"Placed {reference} ({value}) at ({x}, {y}) on {layer} as {what}"


# ---------------------------------------------------------------------------
# A footprint's side, as KiCad flips it
# ---------------------------------------------------------------------------

#: The layers ::FlipLayer swaps on every board: the F and B copper, silk, adhesive, mask,
#: paste, courtyard and fab layers (common/layer_id.cpp:169-212 at 9.0.8, 173-218 at
#: 10.0.7). With every copper layer they are LSET::SideSpecificMask (common/lset.cpp:
#: 719-723 at 9.0.8, 736-740 at 10.0.7).
_FLIP_PAIRS = {
    f"{a}.{kind}": f"{b}.{kind}"
    for kind in ("Cu", "SilkS", "Adhes", "Mask", "Paste", "CrtYd", "Fab")
    for a, b in (("F", "B"), ("B", "F"))
}
_INNER_CU = re.compile(r"In[0-9]+\.Cu")
_USER_LAYER = re.compile(r"User\.([0-9]+)")


class _FlipLayers(NamedTuple):
    """What a flip needs of a board's layers (_board_flip_layers)."""

    opposite: dict[str, str]  # each layer whose opposite is another layer
    sided: frozenset[str]  # the layers the board's table types front or back
    inner: frozenset[str]  # the board's inner copper layers
    major: int  # the KiCad whose format the board carries (_board_major)

    def flip(self, name: str) -> str:
        return self.opposite.get(name, name)

    def side_specific(self, name: str) -> bool:
        return name in _FLIP_PAIRS or name in self.sided or bool(_INNER_CU.fullmatch(name))


def _board_flip_layers(root) -> _FlipLayers:
    """A board's layer pairs and side-specific layers as KiCad holds them once it has
    read the board's (layers ...) table, whose rows are (ordinal "name" type ["user"]).

    BOARD::recalcOpposites (pcbnew/board.cpp:795-853 at 9.0.8, 913-970 at 10.0.7) adds
    to _FLIP_PAIRS the user layers typed front or back that it pairs: a layer with the
    first later one of the other type whose user name, after its first '.', is the same
    text and not empty; then User.N typed front with User.N+1 typed back when neither is
    paired and neither user name is its canonical name. A row written without a user
    name reads as an empty one, so two such layers pair (measured with pcbnew 9.0.8 in
    test_ladder_r3_judges.py). Inner copper is its own opposite here, which is where
    KiCad 9 leaves it on a board it read; KiCad 10 mirrors it through the stack, and
    _fp_unsupported refuses a flip whose file the two would read apart. An item is side
    specific on a layer of _FLIP_PAIRS, on any copper layer and on a layer the table
    types front or back (BOARD_ITEM::IsSideSpecific, pcbnew/board_item.cpp:156-170 at
    9.0.8, 224-238 at 10.0.7)."""
    rows = []
    table = root.find("layers")
    for row in table.lists if table is not None else ():
        atoms = row.atoms
        if len(atoms) >= 3:
            rows.append((atoms[1].text, atoms[2].text, atoms[3].text if len(atoms) > 3 else ""))
    users = {}
    for name, kind, user in rows:
        match = _USER_LAYER.fullmatch(name)
        if match:
            users[int(match.group(1))] = (kind, user)
    opposite = dict(_FLIP_PAIRS)

    def free(n: int) -> bool:
        return f"User.{n}" not in opposite and users[n][0] in ("front", "back")

    def principal(n: int) -> str:
        user = users[n][1]
        return user.split(".", 1)[1] if "." in user else ""

    def pair(n: int, m: int) -> None:
        opposite[f"User.{n}"], opposite[f"User.{m}"] = f"User.{m}", f"User.{n}"

    order = sorted(users)
    for i, n in enumerate(order):
        for m in order[i + 1 :] if free(n) else ():
            if free(m) and users[m][0] != users[n][0] and principal(m) == principal(n) != "":
                pair(n, m)
                break
    for n in order:
        m = n + 1
        if (
            m in users
            and free(n)
            and free(m)
            and (users[n][0], users[m][0]) == ("front", "back")
            and users[n][1] != f"User.{n}"
            and users[m][1] != f"User.{m}"
        ):
            pair(n, m)
    return _FlipLayers(
        opposite,
        frozenset(name for name, kind, _ in rows if kind in ("front", "back")),
        frozenset(name for name, _, _ in rows if _INNER_CU.fullmatch(name)),
        _board_major(root),
    )


def _fp_is_back(fp) -> bool:
    """Whether KiCad reads the footprint on the back: its (layer) is B.Cu, and any other
    layer reads as F.Cu (pcb_io_kicad_sexpr_parser.cpp:4510-4519 at 9.0.8, 5198-5207 at
    10.0.7)."""
    layer = fp.find("layer")
    return layer is not None and len(layer.atoms) > 1 and layer.atoms[1].text == "B.Cu"


def _require_side(layer: str) -> None:
    """Refuse a side other than F.Cu or B.Cu: FOOTPRINT::SetLayerAndFlip asserts one of
    the two (pcbnew/footprint.cpp:2482-2488 at 9.0.8, 2921-2927 at 10.0.7), and KiCad
    reads any other footprint layer as F.Cu."""
    if layer not in ("F.Cu", "B.Cu"):
        raise ToolError(
            f"layer must be F.Cu or B.Cu, not {layer!r}: a footprint sits on the front or the"
            " back, and KiCad reads any other footprint layer as F.Cu. Nothing was written."
        )


#: Footprint children KiCad moves with a footprint and this package does not transform:
#: dimensions and images, which KiCad writes in board coordinates (pcb_io_kicad_sexpr.cpp:
#: 820-870 and 1035-1066 at 9.0.8), tables and barcodes.
_FP_UNPORTED = ("dimension", "image", "table", "barcode")
#: The other footprint children KiCad's parser reads (parseFOOTPRINT_unchecked,
#: pcb_io_kicad_sexpr_parser.cpp:4417-5015 at 9.0.8, 5104-5830 at 10.0.7). Any child
#: outside them is refused as well: a later KiCad's item may hold board coordinates.
_FP_KNOWN = frozenset(
    """
    version generator generator_version locked placed layer tedit tstamp uuid at descr
    tags path sheetname sheetfile units autoplace_cost90 autoplace_cost180 private_layers
    net_tie_pad_groups duplicate_pad_numbers_are_jumpers jumper_pad_groups
    solder_mask_margin solder_paste_margin solder_paste_ratio solder_paste_margin_ratio
    clearance zone_connect thermal_width thermal_gap attr model group embedded_fonts
    embedded_files component_classes variant stackup property fp_text fp_text_box fp_line
    fp_rect fp_circle fp_arc fp_poly fp_curve pad zone point
    """.split()
)
#: Children whose flip is not ported, refused on a flip only: a text box, which
#: PCB_TEXTBOX::Flip turns its own way (pcbnew/pcb_textbox.cpp:547 at 9.0.8, 573 at
#: 10.0.7), and a KiCad 10 footprint stackup.
_FP_UNFLIPPED = ("fp_text_box", "stackup")
#: Pad tokens whose flip is not ported: a padstack of its own and KiCad 10's back drills
#: and post machining, which PADSTACK::FlipLayers swaps layer by layer (pcbnew/
#: padstack.cpp:722-759 at 9.0.8, 1490-1522 at 10.0.7).
_PAD_UNFLIPPED = (
    "padstack",
    "backdrill",
    "tertiary_drill",
    "front_post_machining",
    "back_post_machining",
)
#: A custom pad's primitives (pcb_io_kicad_sexpr.cpp:1702-1795 at 9.0.8).
_PRIMITIVES = frozenset(
    "gr_line gr_vector gr_rect gr_bbox gr_arc gr_circle gr_curve gr_poly".split()
)
_FP_GRAPHICS = ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_curve")


def _fp_unsupported(fp, layers: _FlipLayers, flipping: bool) -> str | None:
    """What makes an edit of ``fp`` impossible to do as KiCad does it, named for the
    refusal, or None. Every edit refuses a child in _FP_UNPORTED or outside _FP_KNOWN. A
    flip also refuses _FP_UNFLIPPED, a pad token in _PAD_UNFLIPPED, a primitive outside
    _PRIMITIVES, an arc written by its centre and angle (KiCad 5's form), a text with no
    (effects ...) to write its mirror in, and, on a board of four or more copper layers,
    an item on inner copper that does not name every inner layer (a zone's own layers,
    each of its fills and each of its KiCad 10 layer properties counting as an item):
    KiCad 9 leaves it where it is and KiCad 10 mirrors it through the stack, so no file
    reads the same to both. A plated through hole is exempt, since KiCad gives it every
    copper layer whatever it names (FOOTPRINT::FixUpPadsForBoard,
    pcbnew/footprint.cpp:4167-4185 at 9.0.8, 4834-4853 at 10.0.7)."""
    for child in fp.lists:
        if child.head in _FP_UNPORTED or child.head not in _FP_KNOWN:
            return f"a ({child.head} ...), which this tool does not move with the footprint"
    if not flipping:
        return None
    for child in fp.lists:
        if child.head in _FP_UNFLIPPED:
            return f"a ({child.head} ...), whose flip this tool does not port"
        if child.head == "fp_arc" and child.find("mid") is None:
            return "an (fp_arc ...) written by its centre and angle, KiCad 5's form"
    for pad in fp.find_all("pad"):
        for head in _PAD_UNFLIPPED:
            if pad.find(head) is not None:
                return f"a pad with ({head} ...), whose flip this tool does not port"
        primitives = pad.find("primitives")
        for shape in primitives.lists if primitives is not None else ():
            legacy_arc = shape.head == "gr_arc" and shape.find("mid") is None
            if shape.head not in _PRIMITIVES or legacy_arc:
                return f"a pad primitive ({shape.head} ...) whose flip this tool does not port"
    for text in _fp_texts(fp):
        if text.find("effects") is None:
            return f"a ({text.head} ...) with no (effects ...) to write its mirror in"
    if len(layers.inner) < 2:
        return None
    for item in fp.lists:
        if item.head == "pad" and len(item.atoms) > 2 and item.atoms[2].text == "thru_hole":
            continue
        for names in _named_layer_sets(item):
            named = {name for name in names if _INNER_CU.fullmatch(name)}
            if named and not layers.inner <= named:
                return (
                    f"a ({item.head} ...) on {min(named)}, an inner copper layer, which KiCad"
                    " 9 keeps there when it flips the footprint and KiCad 10 mirrors through"
                    " the board's stack, so no file reads the same to both"
                )
    return None


def _layer_words(node) -> list[str]:
    """The layer names a (layer ...) or (layers ...) list writes; [] for None."""
    return [atom.text for atom in node.atoms[1:]] if node is not None else []


def _named_layer_sets(item):
    """The layer names each thing in a footprint's child ``item`` sits on, one list per
    thing: a pad's (layers ...); a zone's (layer ...) or (layers ...), then each of its
    (filled_polygon ...) and KiCad 10 (property ...) layers; any other child's
    (layer ...)."""
    if item.head == "pad":
        yield _layer_words(item.find("layers"))
    elif item.head == "zone":
        yield _layer_words(item.find("layer")) + _layer_words(item.find("layers"))
        for part in item.find_all("filled_polygon") + item.find_all("property"):
            yield _layer_words(part.find("layer"))
    else:
        yield _layer_words(item.find("layer"))


def _negate_y(node) -> None:
    """Negate the y atom of an (at|start|mid|end|center|xy|offset|rect_delta x y ...)."""
    if len(node.atoms) > 2:
        node.atoms[2].set_text(_numish_text(-float(node.atoms[2].text)))


def _flip_shape(node) -> None:
    """EDA_SHAPE::flip top to bottom about its frame's origin (common/eda_shape.cpp:
    697-737 at 9.0.8, 991-1033 at 10.0.7): every y negated, the points of an (arc ...)
    in a polygon's outline included and in their order (measured with pcbnew 9.0.8 in
    test_ladder_r3_judges.py), and an arc's start and end swapped, which is the same
    arc."""
    for key in ("start", "mid", "end", "center"):
        child = node.find(key)
        if child is not None:
            _negate_y(child)
    pts = node.find("pts")
    for entry in pts.lists if pts is not None else ():
        if entry.head == "xy":
            _negate_y(entry)
        elif entry.head == "arc":
            for part in entry.lists:
                if part.head in ("start", "mid", "end"):
                    _negate_y(part)
    start, end = node.find("start"), node.find("end")
    if node.head in ("fp_arc", "gr_arc") and start is not None and end is not None:
        texts = [atom.text for atom in start.atoms[1:3]], [atom.text for atom in end.atoms[1:3]]
        for atoms, words in ((start.atoms[1:3], texts[1]), (end.atoms[1:3], texts[0])):
            for atom, word in zip(atoms, words):
                atom.set_text(word)


#: PAD::Flip's top to bottom swap of the chamfered corners, and the order KiCad writes
#: them in (pcb_io_kicad_sexpr.cpp:1587-1601 at 9.0.8, 1833-1847 at 10.0.7).
_CHAMFER_FLIP = {
    "top_left": "bottom_left",
    "bottom_left": "top_left",
    "top_right": "bottom_right",
    "bottom_right": "top_right",
}
_CHAMFER_ORDER = ("top_left", "top_right", "bottom_left", "bottom_right")
_SIDE_SWAP = {"front": "back", "back": "front"}


def _flip_pad(pad, layers: _FlipLayers, turn: float) -> None:
    """PAD::Flip as FOOTPRINT::Flip runs it, then the turn by 180 (pcbnew/pad.cpp:
    1029-1093 at 9.0.8, 1504-1565 at 10.0.7): local y negated; the absolute angle P
    becomes Normalize( Normalize( -P ) + turn ); the drill offset and the trapezoid delta
    mirror in y; the top and bottom chamfered corners swap; the primitives flip in the
    pad's own frame; the layers go to their opposites. On a KiCad 10 board the tenting
    swaps front and back, as 10.0.7's PADSTACK::FlipLayers swaps the mask properties of
    every pad (padstack.cpp:1490-1522); 9.0.8's leaves a pad without a padstack of its
    own as it is (722-759), so on a KiCad 9 board it stays."""
    at = pad.find("at")
    if at is not None:
        _negate_y(at)
        atom = _at_angle_atom(at)
        held = _n360(float(atom.text)) if atom is not None else 0.0
        angle = _n360(_n360(-(held - 0.0) + 0.0) + turn)
        _set_at_angle(at, None if angle == 0 else _angle_text(angle))
    names = pad.find("layers")
    for atom in names.atoms[1:] if names is not None else ():
        if layers.flip(atom.text) != atom.text:
            atom.set_text(layers.flip(atom.text))
    drill = pad.find("drill")
    for node in (drill.find("offset") if drill is not None else None, pad.find("rect_delta")):
        if node is not None:
            _negate_y(node)
    chamfer = pad.find("chamfer")
    if chamfer is not None:
        words = [str(atom.text) for atom in chamfer.atoms[1:]]
        rank = {corner: k for k, corner in enumerate(_CHAMFER_ORDER)}
        corners = sorted(
            (_CHAMFER_FLIP.get(word, word) for word in words),
            key=lambda corner: rank.get(corner, len(rank)),
        )
        for atom, corner in zip(chamfer.atoms[1:], corners):
            if atom.text != corner:
                atom.set_text(corner)
    primitives = pad.find("primitives")
    for shape in primitives.lists if primitives is not None else ():
        _flip_shape(shape)
    tenting = pad.find("tenting")
    if tenting is not None and layers.major >= 10:
        sides = [node for node in tenting.lists if node.head in _SIDE_SWAP]
        if len(sides) == 2 and all(len(node.atoms) == 2 for node in sides):
            first, second = sides[0].atoms[1].text, sides[1].atoms[1].text
            sides[0].atoms[1].set_text(second)
            sides[1].atoms[1].set_text(first)
        else:
            for node in sides:
                node.atoms[0].set_text(_SIDE_SWAP[node.head])
        for atom in tenting.atoms[1:]:
            if atom.text in _SIDE_SWAP:
                atom.set_text(_SIDE_SWAP[atom.text])


#: The layers FOOTPRINT::addMandatoryFields gives the four fields every footprint holds,
#: which a field written without (layer) keeps whatever the footprint's side
#: (pcbnew/footprint.cpp:94-111 at 9.0.8).
_FIELD_LAYERS = {
    "Reference": "F.SilkS",
    "Value": "F.Fab",
    "Datasheet": "F.Fab",
    "Description": "F.Fab",
}


def _text_default_layer(text, back: bool) -> str:
    """The layer KiCad holds for a text written without (layer): a mandatory field's
    _FIELD_LAYERS; another field F.Fab or B.Fab by the footprint's side (the parser,
    pcb_io_kicad_sexpr_parser.cpp:4644-4651 at 9.0.8, 5330-5338 at 10.0.7); an fp_text
    F.SilkS or B.SilkS by the side (PCB_TEXT's constructor, pcbnew/pcb_text.cpp:56-76 at
    9.0.8, 61-79 at 10.0.7). Measured with pcbnew 9.0.8 in test_ladder_r3_judges.py."""
    if text.head == "property":
        return _FIELD_LAYERS.get(text.atoms[1].text, "B.Fab" if back else "F.Fab")
    return "B.SilkS" if back else "F.SilkS"


def _flip_text(text, layers: _FlipLayers, held: float, turn: float, back: bool) -> None:
    """PCB_TEXT::Flip as FOOTPRINT::Flip runs it, then the turn by 180 and KeepUpright
    (pcbnew/pcb_text.cpp:414-431 and 321-339 at 9.0.8, 493-510 and 389-407 at 10.0.7):
    local y negated; the angle T KiCad holds, Normalize( ( T_written - A ) + A ) on a
    footprint holding A, becomes Normalize( ( 180 - T ) + turn ); the layer goes to its
    opposite, a text written without one getting its default's opposite written after
    (at) and (unlocked), where KiCad's writer puts it; the mirror toggles only when the
    new layer is side specific; then a keep-upright text at 180 or more turns half a
    turn with its justification negated."""
    at = text.find("at")
    _negate_y(at)
    atom = _at_angle_atom(at)
    written = float(atom.text) if atom is not None else 0.0
    angle = _n360((180.0 - _n360((written - held) + held)) + turn)
    node = text.find("layer")
    old = node.atoms[1].text if node is not None else _text_default_layer(text, back)
    new = layers.flip(old)
    if node is None:
        node = _cst.parse(b'(layer "x")').lists[0]
        node.atoms[1].set_text(new)
        unlocked = text.find("unlocked")
        text.insert_after(unlocked if unlocked is not None else at, node)
    elif new != old:
        node.atoms[1].set_text(new)
    effects = text.find("effects")
    words = _justify_words(effects) if effects is not None else []
    turned = [word for word in words if word != "mirror"]
    mirror = ("mirror" in words) != layers.side_specific(new)
    if angle >= 180.0 and _text_keeps_upright(text):
        angle = _n360(angle + 180.0)
        turned = [_JUSTIFY_FLIP.get(word, word) for word in turned]
    final = turned + (["mirror"] if mirror else [])
    if effects is not None and final != words:
        _set_justify(effects, final)
    _set_at_angle(at, _angle_text(angle))


def _fp_flip(fp, layers: _FlipLayers) -> None:
    """Flip a footprint to the other side as KiCad's FOOTPRINT::Flip about its own
    position, LEFT_RIGHT, does (pcbnew/footprint.cpp:2491-2550 at 9.0.8, 2930-2999 at
    10.0.7), in file terms: B.Cu becomes F.Cu and any other side B.Cu; the angle A
    becomes Normalize180( Normalize180( -A ) + 180 ), and every pad and text turns by
    turn = ( new + 180 ) - new, new being Normalize180( -A ), which the doubles do not
    always make 180; pads, texts and graphics flip in the footprint's own frame
    (_flip_pad, _flip_text, _flip_shape) and their layers go to their opposites on this
    board (_board_flip_layers). The zones and KiCad 10 points it owns are board
    coordinates: each point is mirrored top to bottom about the footprint's position, y
    to 2 fy - y, then turned about it by turn with the integer RotatePoint, which in the
    file takes x to 2 fx - x and keeps y (ZONE::Flip and Rotate, pcbnew/zone.cpp:959-993
    at 9.0.8; PCB_POINT::Flip, pcbnew/pcb_point.cpp:144-149 at 10.0.7); a zone's layers,
    its fills' and its KiCad 10 layer properties' go to their opposites, and so does a
    point's. Models and everything else stay as they are: the flip has no code for them.
    The caller has run _fp_unsupported."""
    side = fp.find("layer")
    back = _fp_is_back(fp)
    side.atoms[1].set_text("F.Cu" if back else "B.Cu")
    at = fp.find("at")
    atom = _at_angle_atom(at)
    held = _n180(float(atom.text)) if atom is not None else 0.0
    new = _n180(-held)
    turn = (new + 180.0) - new
    angle = _n180(new + 180.0)
    _set_at_angle(at, None if angle == 0 else _angle_text(angle))
    for pad in fp.find_all("pad"):
        _flip_pad(pad, layers, turn)
    for text in _fp_texts(fp):
        _flip_text(text, layers, held, turn, back)
    flipped_layers = []
    for child in fp.lists:
        if child.head in _FP_GRAPHICS or child.head == "point":
            if child.head != "point":
                _flip_shape(child)
            flipped_layers.append(child.find("layer"))
        elif child.head == "zone":
            flipped_layers += [child.find("layer"), child.find("layers")]
            for part in child.find_all("filled_polygon") + child.find_all("property"):
                flipped_layers.append(part.find("layer"))
    for node in flipped_layers:
        for word in node.atoms[1:] if node is not None else ():
            if layers.flip(word.text) != word.text:
                word.set_text(layers.flip(word.text))
    centre = _board_nm(at, layers.major)
    turned = _turn_about(centre, turn, layers.major)
    _move_board_points(fp, lambda x, y: turned(x, 2 * centre[1] - y), layers.major)


def _numish_text(value: float) -> str:
    """The text KiCad writes for a board coordinate of *value* millimetres: the
    nanometres it reads, KiROUND( value * 1e6 ), through FormatInternalUnits
    (_mm). An int has no sign of zero, so nothing comes out as "-0", and for the
    negation of a coordinate KiCad wrote, -float(t), it is exactly KiCad's text
    for the negated value, which is what the flip writes. It is given no board
    version, so it rounds as KiCad 9 does: on whole nanometres, which is what
    KiCad writes, KiCad 10 rounds the same (_shared.kiround)."""
    return _mm(kiround(value * 1e6))


@mcp.tool(annotations=_ADDITIVE)
def move_footprint(
    reference: str,
    x: float,
    y: float,
    rotation: float | None = None,
    layer: str = "",
    pcb_path: str = PCB_PATH,
) -> str:
    """Move a footprint to a new position.

    A move rewrites the footprint's own position and nothing of its pads, texts
    or graphics, which KiCad stores relative to the footprint. The zones it owns
    and a KiCad 10 footprint's points are stored in board coordinates, so they
    move by the same delta, as KiCad's FOOTPRINT::SetPosition moves them
    (pcbnew/footprint.cpp:2553-2584 at 9.0.8). A rotation turns the footprint to
    that angle as KiCad's own rotate turns it: every pad and text turns by the
    change from the angle it had, its zones and points turn about it, and a
    keep-upright text that would read upside down is turned half a turn with its
    justification mirrored, so it stays readable on the same side of its anchor.
    Angles are written as KiCad writes them: the footprint's between -180 and 180
    and left out at 0. Off the quarter turns the result reads as KiCad's turn
    without matching pcbnew's save byte for byte: footprint-relative coordinates
    stay as written where pcbnew re-derives them through two RotatePoint roundings,
    an fp_rect stays where pcbnew writes an fp_poly, and arc mid points stay as
    written. A footprint carrying what this tool does not move with it
    (a dimension, image, table or barcode, or a child no KiCad it knows reads) is
    refused and nothing is written. The keep-out warning reads keepouts as
    check_placement does.

    Args:
        reference: Reference designator (e.g. "R1")
        x: New X position
        y: New Y position
        rotation: The footprint's angle after the move, in degrees (None keeps the
            current one). An angle the footprint already has (270 for one at -90,
            360 for one at 0) changes nothing and leaves the file byte-identical,
            where KiCad's footprint properties dialog would still re-run its
            rotate by a full turn. inf and nan are refused and nothing is written.
        layer: F.Cu or B.Cu (empty keeps the side; anything else is refused). The
            other side flips the footprint as KiCad's own flip does (FOOTPRINT::Flip,
            pcbnew/footprint.cpp:2491-2550 at 9.0.8): pads, texts and graphics
            mirror, their layers go to their opposites on this board (user layers it
            pairs included), a text's mirror toggles on side-specific layers only,
            zones and points mirror about the footprint with their layers, and 3D
            models stay as they are. With a rotation the flip comes first, so
            rotation names the angle the footprint ends at. Refused, with nothing
            written: a text box, a pad's own padstack, KiCad 10's back drills, and on
            a board of four or more copper layers an item (a zone included) on inner
            copper that does not name every inner layer, which KiCad 9 and 10 flip
            apart.
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _require_finite_rotation(rotation)
    x, y = _snap(x, PCB_IU_PER_MM), _snap(y, PCB_IU_PER_MM)
    tree, root, key = _open_pcb_cst(pcb_path)
    # Every check before the cache pop, so a refusal leaves the cached tree valid.
    if layer:
        _resolve_layer_cst(root, layer, copper_only=True)
        _require_side(layer)
    fp = _find_fp_cst(root, reference)
    layers = _board_flip_layers(root)
    flipping = bool(layer) and (layer == "B.Cu") != _fp_is_back(fp)
    reason = _fp_unsupported(fp, layers, flipping)
    if reason:
        raise ToolError(
            f"{reference} carries {reason}. Nothing was changed; move it in KiCad instead."
        )
    _BOARD_CACHE.pop(key, None)
    _fp_translate(fp, _num(x, PCB_IU_PER_MM), _num(y, PCB_IU_PER_MM), layers.major)
    if flipping:
        # The flip before the angle: the flip rewrites the footprint's own angle
        # from the one it had, and the rotation names the angle it ends at.
        _fp_flip(fp, layers)
    if rotation is not None:
        _fp_rotate_to(fp, rotation, layers.major)
    # Advisory only: moving into a keep-out or off the board edge is legal
    # KiCad, just usually a mistake, so this warns and never refuses. It runs
    # before the write purely so a fault in the checks cannot leave a moved
    # footprint on disk with nothing said about it; the checks read the already
    # mutated tree, so the answers are the same either side of the write.
    warnings: list[str] = []
    try:
        if _keepout_violations_cst(root, x, y, _fp_side(fp), own=fp):
            warnings.append("WARNING: position is inside a keep-out zone (footprints not allowed)")
        edge_poly = _edge_polygon_cst(root)
        if edge_poly is not None and not _point_in_polygon(x, y, edge_poly):
            warnings.append("WARNING: position is outside the board edge")
    except Exception as exc:  # noqa: BLE001 - the move must still happen
        # Was a bare `pass`, which hid any defect in the geometry helpers for
        # as long as nobody went looking. Still does not block the move.
        warnings.append(f"WARNING: placement checks could not run ({type(exc).__name__}: {exc})")

    _atomic_write(key, _cst.serialize(tree))
    msg = f"Moved {reference} to ({x}, {y})"
    if warnings:
        msg += " " + " ".join(warnings)
    return msg


# check_placement took a rotation parameter that was accepted and silently
# ignored until 2026-08-12. It is gone; the docstring explains why an angle
# could not have changed the answer, which is what a caller needs. The date
# is for whoever reads the history, not for every user loading the schema.
@mcp.tool(annotations=_READ_ONLY, title="Check one proposed footprint position")
def check_placement(
    reference: str,
    x: float,
    y: float,
    pcb_path: str = PCB_PATH,
) -> PlacementCheckResult:
    """Check if placing/moving a footprint to (x, y) would violate constraints.

    Both checks are on the footprint's origin point, not its courtyard, so a
    footprint whose body overlaps a keep-out while its origin does not still
    reports ok. That is also why there is no rotation parameter: rotating about
    the origin cannot move the origin, so an angle could not change either
    answer.

    Keep-outs are read as KiCad reads them: a footprint's own keep-outs in board
    coordinates, as the file stores them; a layer set such as *.Cu or F&B.Cu as
    the layers it names; the footprint on its side, B.Cu or else F.Cu, as KiCad
    reads any other footprint layer; and the footprint's own keep-outs never
    count against it (intersectsArea skips an area whose parent is the item,
    pcbnew/pcbexpr_functions.cpp:697 at 9.0.8).

    Args:
        reference: Footprint reference designator
        x: Proposed X position
        y: Proposed Y position
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    fp = _find_fp_cst(root, reference)

    keepout_violations = _keepout_violations_cst(root, x, y, _fp_side(fp), own=fp)
    edge_poly = _edge_polygon_cst(root)
    outside_board_edge = edge_poly is not None and not _point_in_polygon(x, y, edge_poly)

    has_violations = bool(keepout_violations) or outside_board_edge
    return PlacementCheckResult(
        status="violations_found" if has_violations else "ok",
        board_edge_checked=edge_poly is not None,
        keepout_violations=keepout_violations,
        outside_board_edge=outside_board_edge,
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_footprint(reference: str, pcb_path: str = PCB_PATH) -> str:
    """Remove a footprint by reference designator.

    Args:
        reference: Reference designator (e.g. "R1")
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    tree, root, key = _open_pcb_cst(pcb_path)
    _BOARD_CACHE.pop(key, None)
    root.remove_child(_find_fp_cst(root, reference))
    _atomic_write(key, _cst.serialize(tree))
    return f"Removed {reference}"


@mcp.tool(annotations=_ADDITIVE)
def add_trace(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    width: float = 0.25,
    layer: str = "F.Cu",
    net: int = 0,
    pcb_path: str = PCB_PATH,
) -> str:
    """Add a trace segment between two points.

    Args:
        x1: Start X
        y1: Start Y
        x2: End X
        y2: End Y
        width: Trace width in mm
        layer: Copper layer (e.g. "F.Cu", "B.Cu")
        net: A net number from list_pcb_nets, or 0 for no net. Any other number is refused
            and nothing is written. A KiCad 10 board holds nets by name and its numbers are
            derived from where each net first appears, so read list_pcb_nets again after
            an edit.
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    x1, y1, x2, y2, width = (_snap(v, PCB_IU_PER_MM) for v in (x1, y1, x2, y2, width))
    tree, root, key = _open_pcb_cst(pcb_path)
    _resolve_layer_cst(root, layer, copper_only=True)
    _BOARD_CACHE.pop(key, None)
    node = _SEGMENT_TPL.copy()
    start, end = node.find("start"), node.find("end")
    start.atoms[1].set_text(_num(x1, PCB_IU_PER_MM))
    start.atoms[2].set_text(_num(y1, PCB_IU_PER_MM))
    end.atoms[1].set_text(_num(x2, PCB_IU_PER_MM))
    end.atoms[2].set_text(_num(y2, PCB_IU_PER_MM))
    node.find("width").atoms[1].set_text(_num(width, PCB_IU_PER_MM))
    node.find("layer").atoms[1].set_text(layer)
    _set_item_net(node, root, net)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_pcb_node(root, node)
    _atomic_write(key, _cst.serialize(tree))
    return f"Trace: ({x1}, {y1}) -> ({x2}, {y2}) w={width} {layer}"


@mcp.tool(annotations=_ADDITIVE)
def add_via(
    x: float,
    y: float,
    size: float = 0.6,
    drill: float = 0.3,
    net: int = 0,
    layers: list[str] | None = None,
    pcb_path: str = PCB_PATH,
) -> str:
    """Add a via at a position.

    Args:
        x: X position
        y: Y position
        size: Via pad size in mm
        drill: Drill diameter in mm
        net: A net number from list_pcb_nets, or 0 for no net. Any other number is refused
            and nothing is written. A KiCad 10 board holds nets by name and its numbers are
            derived from where each net first appears, so read list_pcb_nets again after
            an edit.
        layers: Via layers (default: ["F.Cu", "B.Cu"])
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    x, y, size, drill = (_snap(v, PCB_IU_PER_MM) for v in (x, y, size, drill))
    tree, root, key = _open_pcb_cst(pcb_path)
    via_layers = layers or ["F.Cu", "B.Cu"]
    for name in via_layers:
        _resolve_layer_cst(root, name, copper_only=True)
    _BOARD_CACHE.pop(key, None)
    node = _VIA_TPL.copy()
    at = node.find("at")
    at.atoms[1].set_text(_num(x, PCB_IU_PER_MM))
    at.atoms[2].set_text(_num(y, PCB_IU_PER_MM))
    node.find("size").atoms[1].set_text(_num(size, PCB_IU_PER_MM))
    node.find("drill").atoms[1].set_text(_num(drill, PCB_IU_PER_MM))
    layers_node = node.find("layers")
    tpl_atom = layers_node.atoms[1]
    del layers_node.children[1:]
    for name in via_layers:
        a = tpl_atom.copy()
        a.sep = b" "
        a.set_text(name)
        layers_node.children.append(a)
    _set_item_net(node, root, net)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_pcb_node(root, node)
    _atomic_write(key, _cst.serialize(tree))
    return f"Via at ({x}, {y}) size={size} drill={drill}"


@mcp.tool(annotations=_ADDITIVE)
def add_pcb_text(
    text: str,
    x: float,
    y: float,
    layer: str = "F.SilkS",
    rotation: float = 0,
    pcb_path: str = PCB_PATH,
) -> str:
    """Add text to the PCB (silkscreen, fab layer, etc.).

    Args:
        text: Text content
        x: X position
        y: Y position
        layer: Layer (e.g. "F.SilkS", "B.SilkS", "F.Fab")
        rotation: Rotation in degrees
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    x, y = _snap(x, PCB_IU_PER_MM), _snap(y, PCB_IU_PER_MM)
    tree, root, key = _open_pcb_cst(pcb_path)
    _resolve_layer_cst(root, layer)
    _BOARD_CACHE.pop(key, None)
    node = _GR_TEXT_TPL.copy()
    node.atoms[1].set_text(text)
    _fill_at(node, x, y, rotation, PCB_IU_PER_MM)
    node.find("layer").atoms[1].set_text(layer)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_after(root, node, _GRAPHIC_HEADS, _TRACE_AND_TAIL_HEADS)
    _atomic_write(key, _cst.serialize(tree))
    return f"Text '{text}' at ({x}, {y}) on {layer}"


@mcp.tool(annotations=_ADDITIVE)
def add_pcb_line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    layer: str = "Edge.Cuts",
    width: float = 0.05,
    pcb_path: str = PCB_PATH,
) -> str:
    """Add a graphic line to the PCB (edge cuts, silkscreen, etc.).

    Args:
        x1: Start X
        y1: Start Y
        x2: End X
        y2: End Y
        layer: Layer (e.g. "Edge.Cuts", "F.SilkS")
        width: Line width in mm
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    x1, y1, x2, y2 = (_snap(v, PCB_IU_PER_MM) for v in (x1, y1, x2, y2))
    tree, root, key = _open_pcb_cst(pcb_path)
    _resolve_layer_cst(root, layer)
    _BOARD_CACHE.pop(key, None)
    node = _GR_LINE_TPL.copy()
    start, end = node.find("start"), node.find("end")
    start.atoms[1].set_text(_num(x1, PCB_IU_PER_MM))
    start.atoms[2].set_text(_num(y1, PCB_IU_PER_MM))
    end.atoms[1].set_text(_num(x2, PCB_IU_PER_MM))
    end.atoms[2].set_text(_num(y2, PCB_IU_PER_MM))
    node.find("stroke").find("width").atoms[1].set_text(_num(width, PCB_IU_PER_MM))
    node.find("layer").atoms[1].set_text(layer)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_after(root, node, _GRAPHIC_HEADS, _TRACE_AND_TAIL_HEADS)
    _atomic_write(key, _cst.serialize(tree))
    return f"Line: ({x1}, {y1}) -> ({x2}, {y2}) on {layer}"


@mcp.tool(annotations=_ADDITIVE)
def add_copper_zone(
    net_name: str,
    layer: str,
    corners: list[PointSpec],
    clearance: float = 0.5,
    min_thickness: float = 0.25,
    thermal_relief: bool = True,
    thermal_gap: float = 0.5,
    thermal_bridge_width: float = 0.5,
    priority: int = 0,
    pcb_path: str = PCB_PATH,
) -> ZoneResult:
    """Create an unfilled copper zone: a ground plane, power plane, or any
    filled copper pour. Call fill_zones afterward to compute the fills.

    Args:
        net_name: Name of the net to assign to this zone (e.g. "GND")
        layer: Copper layer (e.g. "F.Cu", "B.Cu")
        corners: List of {x, y} dicts defining the zone polygon (min 3)
        clearance: Zone clearance in mm
        min_thickness: Minimum copper thickness in mm
        thermal_relief: Use thermal relief pads (True) or solid connection (False)
        thermal_gap: Thermal relief gap in mm
        thermal_bridge_width: Thermal relief bridge width in mm
        priority: Zone fill priority (higher fills first)
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    if len(corners) < 3:
        raise ToolError("At least 3 corners required for a zone polygon.")
    clearance, min_thickness, thermal_gap, thermal_bridge_width = (
        _snap(v, PCB_IU_PER_MM)
        for v in (clearance, min_thickness, thermal_gap, thermal_bridge_width)
    )
    tree, root, key = _open_pcb_cst(pcb_path)
    _resolve_layer_cst(root, layer, copper_only=True)
    _BOARD_CACHE.pop(key, None)
    net_num = _resolve_net_cst(root, net_name)
    node = _COPPER_ZONE_TPL.copy()
    node.find("layer").atoms[1].set_text(layer)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    node.find("priority").atoms[1].set_text(str(priority))
    cp = node.find("connect_pads")
    cp.find("clearance").atoms[1].set_text(_num(clearance, PCB_IU_PER_MM))
    if not thermal_relief:
        solid = cp.children[0].copy()
        solid.sep = b" "
        solid.set_text("yes")
        cp.children.insert(1, solid)
    node.find("min_thickness").atoms[1].set_text(_num(min_thickness, PCB_IU_PER_MM))
    fill = node.find("fill")
    fill.find("thermal_gap").atoms[1].set_text(_num(thermal_gap, PCB_IU_PER_MM))
    fill.find("thermal_bridge_width").atoms[1].set_text(_num(thermal_bridge_width, PCB_IU_PER_MM))
    _fill_zone_polygon(node, corners)
    _set_item_net(node, root, net_num)
    if _board_version(root) > _NUMERIC_NET_VERSION_MAX:
        node.remove_child(node.find("filled_areas_thickness"))
    _splice_pcb_zone(root, node)
    _atomic_write(key, _cst.serialize(tree))
    return ZoneResult(net=net_name, layer=layer, corners=len(corners), clearance_mm=clearance)


@mcp.tool(annotations=_ADDITIVE)
def add_keepout_zone(
    corners: list[PointSpec],
    layers: list[str] | None = None,
    no_tracks: bool = True,
    no_vias: bool = True,
    no_pads: bool = True,
    no_copper_pour: bool = True,
    no_footprints: bool = True,
    pcb_path: str = PCB_PATH,
) -> KeepoutZoneResult:
    """Create a keep-out zone that restricts placement of specified items.

    Args:
        corners: List of {x, y} dicts defining the zone polygon (min 3)
        layers: Layers to apply keep-out to (default: ["F.Cu", "B.Cu"])
        no_tracks: Restrict tracks in this zone
        no_vias: Restrict vias in this zone
        no_pads: Restrict pads in this zone
        no_copper_pour: Restrict copper pour in this zone
        no_footprints: Restrict footprints in this zone
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    if len(corners) < 3:
        raise ToolError("At least 3 corners required for a zone polygon.")
    tree, root, key = _open_pcb_cst(pcb_path)
    zone_layers = layers or ["F.Cu", "B.Cu"]
    for name in zone_layers:
        _resolve_layer_cst(root, name, copper_only=True)
    _BOARD_CACHE.pop(key, None)
    node = _KEEPOUT_ZONE_TPL.copy()
    layers_node = node.find("layers")
    tpl_atom = layers_node.atoms[1]
    del layers_node.children[1:]
    for name in zone_layers:
        a = tpl_atom.copy()
        a.sep = b" "
        a.set_text(name)
        layers_node.children.append(a)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    restrictions = {
        "tracks": "not_allowed" if no_tracks else "allowed",
        "vias": "not_allowed" if no_vias else "allowed",
        "pads": "not_allowed" if no_pads else "allowed",
        "copperpour": "not_allowed" if no_copper_pour else "allowed",
        "footprints": "not_allowed" if no_footprints else "allowed",
    }
    ko = node.find("keepout")
    for k, v in restrictions.items():
        ko.find(k).atoms[1].set_text(v)
    _fill_zone_polygon(node, corners)
    _set_item_net(node, root, 0)
    _splice_pcb_zone(root, node)
    _atomic_write(key, _cst.serialize(tree))
    return KeepoutZoneResult(
        corners=len(corners),
        layers=zone_layers,
        restrictions=restrictions,
    )


def _require_pcbnew_era(pcb_path: str) -> None:
    """Refuse a board the running pcbnew cannot load.

    The same check autoroute_pcb makes, for the same reason and in the same
    words. pcbnew 9's LoadBoard returns None on a KiCad 10 board, so the script
    dies at the next attribute access and the caller gets roughly 700 characters
    of wx image-handler chatter ending in "NoneType object has no attribute
    Zones", naming no version anywhere. Measured: the file is byte-identical
    afterward, so this is a diagnostics fix, not a safety one.
    """
    board_version = _board_version(_cst.parse(_read_kicad_bytes(pcb_path, "board")).lists[0])
    major = _pcbnew_major()
    if major is not None and board_version > _NUMERIC_NET_VERSION_MAX and major < 10:
        raise ToolError(
            f"This board is in the KiCad 10 format (version {board_version}), which "
            f"pcbnew {major} cannot load. Install KiCad 10, or point KICAD_PYTHON at "
            "the Python of a KiCad 10 install."
        )


@mcp.tool(annotations=_ADDITIVE)
def fill_zones(pcb_path: str = PCB_PATH) -> FillZonesResult:
    """Fill all copper zones on the board using pcbnew's zone filler.

    pcbnew computes the fill; this server writes it. The board is handed to
    pcbnew read-only and the computed polygons come back as data, so the file
    that reaches the disk is the original with its zones' fills replaced and
    every other byte untouched. Nothing else about it changes: not its format
    version, not its layer names, not the constructs pcbnew does not model.

    Requires KiCad's pcbnew Python bindings to be installed.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _require_kicad_path(pcb_path, "board")
    pcb_path = str(Path(pcb_path).resolve())
    python, env = _find_pcbnew_python()
    if not python:
        raise ToolError("pcbnew Python bindings not found. Ensure KiCad is installed.")
    # After the availability check, matching autoroute_pcb's ordering: whether
    # pcbnew exists at all beats which era it is.
    _require_pcbnew_era(pcb_path)

    digest_before = hashlib.sha256(_read_kicad_bytes(pcb_path, "board")).hexdigest()
    result = _run_pcbnew(
        [python, "-c", _wx_app_prelude() + _FILL_DUMP.format(path=pcb_path)],
        what="filling zones",
        timeout=300,
        env=env,
    )
    if result.returncode != 0:
        raise ToolError(f"Zone fill failed: {result.stderr.strip()[:400]}")
    line = next((ln for ln in result.stdout.splitlines() if ln.startswith("FILLDUMP")), None)
    if line is None:
        raise ToolError(f"Zone fill produced no polygons: {result.stdout[:300]!r}")
    dumps = json.loads(line[len("FILLDUMP") :])

    # The subprocess only read, but it took time, and a board that changed under
    # us would get another run's copper. Refuse rather than write it.
    tree, root, key = _open_pcb_cst(pcb_path)
    if hashlib.sha256(_cst.serialize(tree)).hexdigest() != digest_before:
        raise ToolError(
            "The board changed while its zones were being filled. Nothing was"
            " written; run fill_zones again."
        )
    _BOARD_CACHE.pop(key, None)
    filled = _splice_fills(root, dumps)
    _atomic_write(pcb_path, _cst.serialize(tree))
    return FillZonesResult(zones_filled=filled, status="ok")


@mcp.tool(annotations=_ADDITIVE)
def update_pcb_from_schematic(
    schematic_path: str = SCH_PATH,
    pcb_path: str = PCB_PATH,
    delete_stale: bool = False,
    project_path: str = "",
) -> UpdatePcbResult:
    """Update the PCB from the schematic (headless Tools -> Update PCB from Schematic).

    Exports the schematic's netlist, loads the assigned footprints from
    libraries, and binds every pad to its net. Creates the .kicad_pcb if
    it does not exist; new footprints land in a grid cluster at orientation
    0, written as KiCad writes a footprint it has just loaded (no angle on the
    footprint, library pad and text angles normalized as KiCad holds them). Existing
    footprints are matched by reference and keep their position; a footprint in KiCad
    7's form, on the board or from its library, takes the symbol's reference and value
    in its (fp_text reference|value ...), and the update is refused when one holds %V or
    %R, which KiCad reads there as a variable. A changed
    footprint assignment swaps the footprint as KiCad's exchange does
    (pcbnew/pcb_edit_frame.cpp:2346-2587 at 9.0.8): it keeps its position,
    angle and side, its texts turned with it as the exchange turns them
    (SetOrientation, then on a KiCad 10 board KeepUpright on those it pairs
    with old ones), and its uuid, its pads' (by number; on a KiCad 10 board
    only a number each footprint holds once) and its fields' (by name), so a
    group still finds it. Off the quarter turns a swapped footprint reads as
    KiCad's exchange leaves it without matching pcbnew's save byte for byte:
    footprint-relative coordinates stay as the library writes them where
    pcbnew re-derives them through two RotatePoint roundings, an fp_rect stays
    where pcbnew writes an fp_poly, and arc mid points stay as written. A
    locked footprint is not swapped
    (skipped: footprint_locked), nor is one whose new footprint carries what
    this tool cannot move or flip (skipped: footprint_unsupported); either
    still takes the symbol's value, fields and nets. A new footprint carrying
    what this tool cannot move is not added (skipped: footprint_unsupported).
    Stale board footprints are reported, and removed only with delete_stale
    (a locked one, in any spelling KiCad reads, never is, and neither is one
    marked board only: KiCad's update keeps both). Zones are NOT refilled: run
    fill_zones afterward. Net names arrive exactly as KiCad's F8 produces them
    (local labels sheet-prefixed, e.g. "/SIG"); read them with list_pcb_nets.
    A pad whose net did not change is left as it is, and one that gains a net
    gets it where KiCad's writer puts it. A net that left the schematic goes to
    no net with everything on it, written as the board's format writes no net,
    unless a pad of a footprint that stays on the board is still on it (a stale
    footprint that is kept: delete_stale off, or locked, or board only); such a
    net keeps its row and everything on it. Segments, arcs and vias sent to no
    net are counted in orphaned_tracks, zones in orphaned_zones, and copper
    shapes are named in warnings. Nothing on a renamed net is moved to its new
    name: it goes to no net with the rest, or stays on the old net while a kept
    pad holds it.

    What KiCad's own update copies from the symbol follows it here, with
    two exceptions named below. The Value. Every other symbol field, as hidden
    text on the fabrication layer of the footprint's side: Datasheet and
    Description are updated where the library footprint put them, a new field
    is added at the footprint's origin at the footprint's own angle (mirrored
    on the back), and a field that left the symbol stays on the footprint
    (KiCad keeps a footprint's library fields the same way, and text the
    user may have placed is not an import's to remove). The symbol's
    footprint filters, as KiCad's (property ki_fp_filters ...). The DNP and
    "In BOM" boxes, set and cleared on the footprint's attributes; on a
    KiCad 10 netlist "Exclude from position files" too, while on KiCad 9,
    whose netlist has no such marker, that flag stays the board's own, as
    the mounting type, board_only and the courtyard choice always do. The
    sheet name and file, and the path that links the footprint to its
    symbol. A symbol with "On board" unticked is never placed and is listed
    in excluded_from_board; a footprint it left behind is reported stale like
    any other. Not copied: the Component Class field, which KiCad assigns as
    a component class rather than a field (dropped here), and KiCad 10's
    variant overrides, which are not applied. Existing footprints whose
    fields, filters, flags or sheet linkage moved are listed in
    fields_updated; a second run that changes nothing writes nothing.

    A footprint's library nickname is resolved the way KiCad resolves it:
    the project's own fp-lib-table first (beside the .kicad_pro, or beside
    the board or the schematic when none is named), then .pretty directories
    beside the board and the schematic, then KICAD_FP_LIB, then the user's
    global fp-lib-table together with the libraries the Plugin and Content
    Manager installed, then KiCad's stock footprints. ${KIPRJMOD}, the
    variables of Preferences > Configure Paths (kicad_common.json), the
    ${KICAD*_3RD_PARTY} and ${KICAD*_FOOTPRINT_DIR} families and the other
    KiCad path variables expand as KiCad expands them: the environment
    first, then the configured value, then KiCad's own default. A nickname
    none of those know is reported in skipped as footprint_lib_not_found.
    place_footprint resolves through the same code, so a hand-placed part
    and an imported one resolve to the same file.

    Requires kicad-cli. It no longer needs KiCad's pcbnew Python bindings.

    Every byte this writes goes through the server's byte-preserving write, so
    a board's format stamp does not move and the parts of it this tool did not
    touch arrive unchanged.

    Args:
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        pcb_path: Path to .kicad_pcb file (created if missing).
            Optional; omit to use the configured default.
        delete_stale: Remove unlocked board footprints absent from the schematic, except
            those marked board only
        project_path: Path to .kicad_pro for explicit root resolution (sub-sheets)
    """
    # The schematic only. pcb_path is deliberately not required to exist: this
    # tool creates the board when it is missing, and the whole E2E suite starts
    # from a schematic and no board at all, so requiring it would refuse the
    # tool's documented first use.
    _require_kicad_path(schematic_path, "schematic")
    if not pcb_path:
        raise ToolError("No PCB path provided. Pass pcb_path parameter.")

    # Netlist must come from the root schematic so the full hierarchy's
    # connectivity is included (same redirect run_erc does).
    sch_target = _resolve_root(schematic_path, project_path) or schematic_path
    pcb_file = str(Path(pcb_path).resolve())

    with tempfile.TemporaryDirectory() as tmp_dir:
        netlist_path = str(Path(tmp_dir) / "netlist.xml")
        result = _run_cli(
            [
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadxml",
                "--output",
                netlist_path,
                sch_target,
            ],
            check=False,
        )
        if result.returncode != 0 or not Path(netlist_path).exists():
            detail = result.stderr.strip() or f"exit code {result.returncode}"
            raise ToolError(f"Netlist export failed: {detail}")
        summary = _apply_netlist_cst(
            netlist_path,
            pcb_file,
            _fp_search_dirs(schematic_path, pcb_file),
            delete_stale,
            _project_dir(pcb_file, sch_target, project_path),
        )
    return UpdatePcbResult(**summary)


@mcp.tool(annotations=_ADDITIVE)
def set_trace_width(
    width: float,
    net_name: str | None = None,
    layer: str | None = None,
    x_min: float | None = None,
    y_min: float | None = None,
    x_max: float | None = None,
    y_max: float | None = None,
    pcb_path: str = PCB_PATH,
) -> TraceWidthResult:
    """Change the width of existing traces matching the given filters.
    At least one filter (net_name, layer, or bounding box) is required.

    Args:
        width: New trace width in mm
        net_name: Filter by net name
        layer: Filter by layer name (e.g. "F.Cu", "B.Cu")
        x_min: Left edge of bounding box filter (mm)
        y_min: Top edge of bounding box filter (mm)
        x_max: Right edge of bounding box filter (mm)
        y_max: Bottom edge of bounding box filter (mm)
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    width = _snap(width, PCB_IU_PER_MM)
    tree, root, key = _open_pcb_cst(pcb_path)
    _BOARD_CACHE.pop(key, None)
    segments = _filter_segments_cst(root, net_name, layer, x_min, y_min, x_max, y_max)
    for seg in segments:
        seg.find("width").atoms[1].set_text(_num(width, PCB_IU_PER_MM))
    _atomic_write(key, _cst.serialize(tree))
    return TraceWidthResult(traces_modified=len(segments), net=net_name, new_width_mm=width)


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_traces(
    net_name: str | None = None,
    layer: str | None = None,
    x_min: float | None = None,
    y_min: float | None = None,
    x_max: float | None = None,
    y_max: float | None = None,
    pcb_path: str = PCB_PATH,
) -> RemoveTracesResult:
    """Remove trace segments matching the given filters. Does not remove vias.
    At least one filter (net_name, layer, or bounding box) is required.

    Args:
        net_name: Filter by net name
        layer: Filter by layer name (e.g. "F.Cu", "B.Cu")
        x_min: Left edge of bounding box filter (mm)
        y_min: Top edge of bounding box filter (mm)
        x_max: Right edge of bounding box filter (mm)
        y_max: Bottom edge of bounding box filter (mm)
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    tree, root, key = _open_pcb_cst(pcb_path)
    _BOARD_CACHE.pop(key, None)
    segments = _filter_segments_cst(root, net_name, layer, x_min, y_min, x_max, y_max)
    for seg in segments:
        root.remove_child(seg)
    _atomic_write(key, _cst.serialize(tree))
    return RemoveTracesResult(traces_removed=len(segments), net=net_name, layer=layer)


@mcp.tool(annotations=_ADDITIVE)
def add_thermal_vias(
    reference: str,
    pad_number: str = "",
    rows: int = 3,
    cols: int = 3,
    spacing: float = 1.0,
    via_size: float = 0.8,
    via_drill: float = 0.3,
    net_name: str | None = None,
    pcb_path: str = PCB_PATH,
) -> ThermalViasResult:
    """Add a grid of thermal vias under a footprint pad.

    Args:
        reference: Footprint reference (e.g. "U1", "R1")
        pad_number: Pad number to center vias on. If empty, auto-selects largest SMD pad.
        rows: Number of rows in the via grid, at least 1. rows * cols is at most 256.
        cols: Number of columns in the via grid, at least 1.
        spacing: Spacing between vias in mm
        via_size: Via annular ring diameter in mm
        via_drill: Via drill diameter in mm
        net_name: Net to assign to vias. If None, auto-detect from pad.
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    # Checked before the board is opened. Every via is built in memory before
    # the one write, so an unbounded grid exhausted memory, and rows=10**18 with
    # cols=0 passes any cap on the product yet spins in the row loop forever.
    board = Path(pcb_path).name or "the board"
    if rows < 1 or cols < 1:
        raise ToolError(
            f"A thermal via grid needs at least one row and one column, not rows={rows},"
            f" cols={cols}. Nothing was written to {board}."
        )
    if rows * cols > _MAX_THERMAL_VIAS:
        raise ToolError(
            f"A {rows}x{cols} grid is {rows * cols} vias; add_thermal_vias places at most"
            f" {_MAX_THERMAL_VIAS} in one call, and exposed-pad grids run about 3x3 to"
            f" 10x10. Nothing was written to {board}."
        )
    tree, root, key = _open_pcb_cst(pcb_path)
    _BOARD_CACHE.pop(key, None)
    fp = _find_fp_cst(root, reference)
    pads = fp.find_all("pad")

    # Find pad
    pad = None
    if pad_number:
        pad = next((p for p in pads if p.atoms[1].text == pad_number), None)
        if pad is None:
            raise ToolError(
                f"Pad {pad_number!r} not found on {reference}."
                " Use get_footprint_pads to list its pads."
            )
    else:
        # Auto-detect: largest SMD pad by area
        best_area = 0
        for p in pads:
            size = p.find("size")
            if p.atoms[2].text == "smd" and size is not None:
                area = float(size.atoms[1].text) * float(size.atoms[2].text)
                if area > best_area:
                    best_area = area
                    pad = p
        if pad is None:
            raise ToolError(f"No SMD pad found on {reference}. Specify pad_number explicitly.")

    # Compute pad center in board coordinates with rotation
    at = fp.find("at")
    fp_x, fp_y = float(at.atoms[1].text), float(at.atoms[2].text)
    fp_angle = float(at.atoms[3].text) if len(at.atoms) > 3 else 0
    pad_at = pad.find("at")
    pad_x, pad_y = _transform_local_to_board(
        fp_x,
        fp_y,
        fp_angle,
        float(pad_at.atoms[1].text),
        float(pad_at.atoms[2].text),
        major=_board_major(root),
    )

    # Determine net. A netless pad, or one on a net the board does not hold, is on
    # no net, net 0, which _set_item_net writes in the board's own spelling:
    # (net 0) on KiCad 9, (net "") on KiCad 10.
    pad_net = _net_name_of(root, pad)
    if net_name:
        via_net = _resolve_net_cst(root, net_name)
    else:
        via_net = next((num for num, name in _net_table(root) if name and name == pad_net), 0)

    # One via with its net, copied for the grid: resolving the net walks the board.
    via = _VIA_TPL.copy()
    _set_item_net(via, root, via_net)
    via.find("size").atoms[1].set_text(_num(via_size, PCB_IU_PER_MM))
    via.find("drill").atoms[1].set_text(_num(via_drill, PCB_IU_PER_MM))

    # Generate grid centered on pad
    vias_added = 0
    for r in range(rows):
        for c in range(cols):
            vx = pad_x + (c - (cols - 1) / 2) * spacing
            vy = pad_y + (r - (rows - 1) / 2) * spacing
            node = via.copy()
            via_at = node.find("at")
            via_at.atoms[1].set_text(_num(vx, PCB_IU_PER_MM))
            via_at.atoms[2].set_text(_num(vy, PCB_IU_PER_MM))
            node.find("uuid").atoms[1].set_text(_gen_uuid())
            _splice_pcb_node(root, node)
            vias_added += 1

    _atomic_write(key, _cst.serialize(tree))
    return ThermalViasResult(
        vias_added=vias_added,
        reference=reference,
        pad=pad.atoms[1].text,
        net=net_name or pad_net,
        center={"x": _snap(pad_x, PCB_IU_PER_MM), "y": _snap(pad_y, PCB_IU_PER_MM)},
    )


@mcp.tool(annotations=_ADDITIVE)
def set_net_class(
    name: str,
    nets: list[str],
    track_width: float | None = None,
    clearance: float | None = None,
    via_size: float | None = None,
    via_drill: float | None = None,
    pcb_path: str = PCB_PATH,
) -> NetClassResult:
    """Create or update a net class with design rules and assign nets.

    Edits the KiCad project file (.kicad_pro) alongside the board to
    define the net class and assign nets.  Does NOT require pcbnew.

    Args:
        name: Net class name (e.g. "Power", "HighSpeed")
        nets: List of net names to assign to this class
        track_width: Track width in mm (None = use default)
        clearance: Clearance in mm (None = use default)
        via_size: Via diameter in mm (None = use default)
        via_drill: Via drill in mm (None = use default)
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    pcb_file = Path(pcb_path).resolve()
    pro_file = pcb_file.with_suffix(".kicad_pro")

    if not pro_file.exists():
        raise ToolError(
            f"Project file not found: {pro_file}. "
            "A .kicad_pro file must exist alongside the .kicad_pcb file."
        )

    # Read existing project JSON. UnicodeDecodeError is listed because it is a
    # ValueError, not an OSError, so it used to escape as a raw traceback; it
    # can still be raised by a file that is genuinely not UTF-8.
    try:
        pro_data = json.loads(pro_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise ToolError(f"Failed to read project file: {exc}") from exc

    # Ensure net_settings structure exists
    if "net_settings" not in pro_data:
        pro_data["net_settings"] = {}
    ns = pro_data["net_settings"]
    if "classes" not in ns:
        ns["classes"] = []
    if "meta" not in ns:
        ns["meta"] = {"version": 4}
    if "netclass_assignments" not in ns or ns["netclass_assignments"] is None:
        ns["netclass_assignments"] = {}

    # Build the net class entry
    nc_entry: dict[str, object] = {"name": name}
    if track_width is not None:
        nc_entry["track_width"] = track_width
    if clearance is not None:
        nc_entry["clearance"] = clearance
    if via_size is not None:
        nc_entry["via_diameter"] = via_size
    if via_drill is not None:
        nc_entry["via_drill"] = via_drill

    # Update or add the net class in the classes list
    found = False
    for i, cls in enumerate(ns["classes"]):
        if cls.get("name") == name:
            ns["classes"][i].update(nc_entry)
            found = True
            break
    if not found:
        ns["classes"].append(nc_entry)

    # Assign nets to this class
    for net_name in nets:
        ns["netclass_assignments"][net_name] = name

    # Write back. ensure_ascii=False keeps a net class or path the user wrote in
    # their own alphabet readable in their own file; the default escapes every
    # non-ASCII character to \uXXXX, which is valid JSON KiCad reads back fine
    # but is not what they had. UTF-8 to match the read above and KiCad's own
    # encoding.
    try:
        _atomic_write(
            pro_file,
            (json.dumps(pro_data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
    except OSError as exc:
        raise ToolError(f"Failed to write project file: {exc}") from exc

    return NetClassResult(
        net_class=name,
        nets_assigned=len(nets),
        track_width_mm=track_width,
        clearance_mm=clearance,
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_dangling_tracks(pcb_path: str = PCB_PATH) -> DanglingTracksResult:
    """Detect and remove trace segments with unconnected endpoints.

    Iteratively removes dangling segments until no more are found.
    A segment is considered dangling if either endpoint does not connect
    to a pad, via, or another trace endpoint.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    tree, root, key = _open_pcb_cst(pcb_path)
    _BOARD_CACHE.pop(key, None)
    tolerance = 0.001  # mm
    total_removed = 0
    iterations = 0
    major = _board_major(root)

    def _seg_ends(seg) -> tuple[tuple[float, float], tuple[float, float]]:
        start, end = seg.find("start"), seg.find("end")
        return (
            (round(float(start.atoms[1].text), 3), round(float(start.atoms[2].text), 3)),
            (round(float(end.atoms[1].text), 3), round(float(end.atoms[2].text), 3)),
        )

    while True:
        # Build connection points: pad positions + via centers + trace endpoints
        connection_points: list[tuple[float, float]] = []

        # Pad positions in board coordinates
        for fp in root.find_all("footprint"):
            at = fp.find("at")
            if at is None:
                continue
            fp_x, fp_y = float(at.atoms[1].text), float(at.atoms[2].text)
            fp_angle = float(at.atoms[3].text) if len(at.atoms) > 3 else 0
            for pad in fp.find_all("pad"):
                pad_at = pad.find("at")
                px, py = _transform_local_to_board(
                    fp_x,
                    fp_y,
                    fp_angle,
                    float(pad_at.atoms[1].text),
                    float(pad_at.atoms[2].text),
                    major=major,
                )
                connection_points.append((round(px, 3), round(py, 3)))

        # Via positions
        for item in root.lists:
            if item.head == "via":
                at = item.find("at")
                connection_points.append(
                    (round(float(at.atoms[1].text), 3), round(float(at.atoms[2].text), 3))
                )

        # Trace endpoints (each segment contributes both start and end)
        segments = [c for c in root.lists if c.head == "segment"]
        for seg in segments:
            s, e = _seg_ends(seg)
            connection_points.append(s)
            connection_points.append(e)

        # ponytail: O(segments x points) scan per iteration, no spatial index;
        # fine at hand-routed scale, index it if boards with thousands of
        # segments ever route through here.
        dangling = []
        for seg in segments:
            start, end = _seg_ends(seg)

            # Count connections at each endpoint (minus the segment's own).
            start_connections = (
                sum(
                    1
                    for pt in connection_points
                    if abs(pt[0] - start[0]) < tolerance and abs(pt[1] - start[1]) < tolerance
                )
                - 1
            )
            end_connections = (
                sum(
                    1
                    for pt in connection_points
                    if abs(pt[0] - end[0]) < tolerance and abs(pt[1] - end[1]) < tolerance
                )
                - 1
            )

            if start_connections < 1 or end_connections < 1:
                dangling.append(seg)

        if not dangling:
            break

        for seg in dangling:
            root.remove_child(seg)
        total_removed += len(dangling)
        iterations += 1

    if total_removed > 0:
        _atomic_write(key, _cst.serialize(tree))

    return DanglingTracksResult(tracks_removed=total_removed, iterations=iterations)


# ---------------------------------------------------------------------------
# Zone fill: pcbnew computes, the CST writes
# ---------------------------------------------------------------------------

#: KiCad packs (xy ...) runs up to this column before wrapping. Matches
#: io_utils.cpp's xySpecialCaseColumnLimit and its own shipped boards.
_XY_COLUMN_LIMIT = 99


def _mm(nm: int) -> str:
    """Nanometres to the millimetre string KiCad writes.

    Trailing zeros are stripped. Measured across 4000 coordinates of KiCad's own
    ecc83-pp: it emits 1 to 6 decimal places and never a padded six.
    """
    text = ("%.6f" % (nm / 1e6)).rstrip("0").rstrip(".")
    return text or "0"


def _filled_polygon_bytes(layer: str, outline: list) -> bytes:
    """One (filled_polygon ...) node in KiCad's own shape.

    Harvested rather than invented, per the rule in CLAUDE.md. Measured on the
    shipped demos: one node per outline per layer (a zone on interf_u carries
    23, all on B.Cu), a single (pts ...) child, and (xy ...) tokens packed to a
    column limit rather than one per line.
    """
    indent = b"\t\t\t\t"
    head = b'\n\t\t(filled_polygon\n\t\t\t(layer "' + layer.encode() + b'")\n\t\t\t(pts'
    lines: list[bytes] = []
    cur = indent
    for x, y in outline:
        tok = ("(xy %s %s)" % (_mm(x), _mm(y))).encode()
        if cur != indent and len(cur) + 1 + len(tok) > _XY_COLUMN_LIMIT:
            lines.append(cur)
            cur = indent + tok
        else:
            cur = cur + (b" " if cur != indent else b"") + tok
    lines.append(cur)
    return head + b"\n" + b"\n".join(lines) + b"\n\t\t\t)\n\t\t)"


def _splice_fills(root, dumps: list[dict]) -> int:
    """Replace each zone's computed fill with *dumps*. Returns zones touched.

    Replacement, not insertion: a zone filled before carries filled_polygon
    children from last time, and appending would double its copper. Keyed on the
    zone uuid, which survives the round trip (measured on both CI majors: 1 of 1
    on every board carrying a zone).
    """
    by_uuid: dict[str, list[dict]] = {}
    for d in dumps:
        by_uuid.setdefault(d["uuid"], []).append(d)
    touched = 0
    for zone in root.find_all("zone"):
        uuid_node = zone.find("uuid")
        if uuid_node is None:
            continue
        entries = by_uuid.get(uuid_node.atoms[1].text)
        if entries is None:
            continue
        for stale in zone.find_all("filled_polygon"):
            zone.remove_child(stale)
        for entry in entries:
            for outline in entry["outlines"]:
                if len(outline) < 3:
                    continue
                node = _cst.parse(_filled_polygon_bytes(entry["layer"], outline)).lists[0]
                zone.append_child(node, b"\n\t\t")
        touched += 1
    return touched


#: Runs inside KiCad's own interpreter. Fills in memory and prints the result;
#: it never calls SaveBoard, which is the entire point. pcbnew computes, and the
#: byte-preserving substrate writes.
_FILL_DUMP = (
    "import json, pcbnew\n"
    "b = pcbnew.LoadBoard({path!r})\n"
    "zones = b.Zones()\n"
    "pcbnew.ZONE_FILLER(b).Fill(zones)\n"
    "out = []\n"
    "for z in zones:\n"
    "    if z.GetIsRuleArea():\n"
    "        continue\n"
    "    for lay in z.GetLayerSet().Seq():\n"
    # HasFilledPolysForLayer first: GetFilledPolysList asserts and kills the
    # process on a layer it holds no fill for. Measured on the KiCad 10 runner.
    "        if not z.HasFilledPolysForLayer(lay):\n"
    "            continue\n"
    "        ps = z.GetFilledPolysList(lay)\n"
    "        outlines = []\n"
    "        for i in range(ps.OutlineCount()):\n"
    "            o = ps.Outline(i)\n"
    "            outlines.append([[o.CPoint(k).x, o.CPoint(k).y] for k in range(o.PointCount())])\n"
    "        out.append({{'uuid': z.m_Uuid.AsString(), 'layer': b.GetLayerName(lay),\n"
    "                    'outlines': outlines}})\n"
    "print('FILLDUMP' + json.dumps(out))\n"
)


# ---------------------------------------------------------------------------
# Netlist import, CST-native (docs/adr-cst-substrate.md)
# ---------------------------------------------------------------------------


def _fp_by_reference(root) -> dict:
    """Reference -> footprint node, for every footprint on the board."""
    return {_fp_prop_cst(fp, "Reference"): fp for fp in root.find_all("footprint")}


def _legacy_field_refusal(fp, key: str, value: str) -> str | None:
    """Why ``value`` cannot be written into the field ``key`` of ``fp``, or None: a legacy
    (fp_text reference|value ...) cannot hold a %V or a %R, which KiCad's parser reads
    there as ${VALUE} and ${REFERENCE} (pcb_io_kicad_sexpr_parser.cpp:3411-3412 at 9.0.8,
    3928-3929 at 10.0.7) where it takes a (property ...) as written."""
    if "%V" not in value and "%R" not in value:
        return None
    if not any(text.head == "fp_text" and _field_name(text) == key for text in fp.lists):
        return None
    return (
        f"the {key} {value!r} holds %V or %R, which KiCad reads as ${{VALUE}} or"
        f" ${{REFERENCE}} in {fp.atoms[1].text}'s legacy (fp_text {key.lower()} ...)."
        " Nothing was written."
    )


def _set_fp_property(fp, key: str, value: str) -> bool:
    """Set a footprint field, reporting whether it actually changed: the text atom
    (_text_atom) of every list KiCad reads as the field (_field_name), since it reads the
    last of them, a legacy (fp_text reference|value ...) included and kept in that form,
    which KiCad reads as the Reference or the Value at every format version
    (pcb_io_kicad_sexpr_parser.cpp:4821-4850 at 9.0.8, 5607-5640 at 10.0.7). A value such
    a text cannot hold is refused (ToolError, _legacy_field_refusal) before any write."""
    refusal = _legacy_field_refusal(fp, key, value)
    if refusal:
        raise ToolError(refusal)
    changed = False
    for text in fp.lists:
        atom = _text_atom(text) if _field_name(text) == key else None
        if atom is not None and atom.text != value:
            atom.set_text(value)
            changed = True
    return changed


def _set_fp_path(fp, path: str) -> None:
    """The KIID path that links a board footprint to its schematic symbol.

    Replaced rather than appended, because a swapped footprint carries the old
    one. GUI linkage only, so a board without it is still correct. The path is
    written by set_text, so it is quoted as KiCad quotes it and a NUL is refused.
    """
    if not path:
        return
    node = _FP_PATH_TPL.copy()
    node.atoms[1].set_text(path)
    existing = fp.find("path")
    if existing is not None:
        node.sep = existing.sep
        fp.children[fp.children.index(existing)] = node
        return
    at = fp.find("at")
    fp.insert_after(at, node)


def _pad_net_bytes(root, number: int, name: str, version: int | None = None) -> bytes:
    """A (net ...) child in this board's own dialect.

    ADR-2 guardrail 5, and the one place a mistake is silent rather than loud:
    a numeric reference in a KiCad 10 board is accepted and rebound by load
    order, measured landing (net 2) on GND with no error at all. The name is
    written by set_text, so it is quoted as KiCad quotes it and a NUL is refused.

    ``version`` is the board's format when the caller has it, since finding it
    walks the whole board.
    """
    if (_board_version(root) if version is None else version) <= _NUMERIC_NET_VERSION_MAX:
        node = _NET_ROW_TPL.copy()
        node.atoms[1].set_text(str(number))
        node.atoms[2].set_text(name)
    else:
        node = _NET_NAMED_TPL.copy()
        node.atoms[1].set_text(name)
    return _cst.serialize(node)


#: The children format( PAD ) writes before a pad's (net ...), in its order
#: (pcb_io_kicad_sexpr.cpp:1432-1613 at 9.0.8, 1635-1858 at 10.0.7; the four before
#: property are 10.0.7's only). KiCad writes the net straight after the last of
#: them a pad holds, then pinfunction, pintype, the margins and the rest. Counted
#: 2026-10-17 over the demos and templates KiCad 9.0.8 ships: 13,497 pads carry a
#: net, every one right after the last of these; 6,215 right after (layers ...),
#: 4,141 after remove_unused_layers and 3,141 after roundrect_rratio.
_PAD_HEADS_BEFORE_NET = (
    "at",
    "size",
    "rect_delta",
    "drill",
    "backdrill",
    "tertiary_drill",
    "front_post_machining",
    "back_post_machining",
    "property",
    "layers",
    "remove_unused_layers",
    "keep_end_layers",
    "zone_layer_connections",
    "roundrect_rratio",
    "chamfer_ratio",
    "chamfer",
)

#: The first board format whose writer leaves net numbers out (pcb_io_kicad_sexpr.h:
#: 201 at 10.0.7). The formats between KiCad 9's last, 20241229, and it are KiCad
#: 10's development: they declare the table and write a pad's net as the pair
#: (net N "NAME"), as KiCad 9 does, while the tools write the name alone for every
#: format above 20241229.
_NET_NAMES_ONLY_VERSION = 20251028


def _pad_holds_net(net, number: int, name: str, version: int) -> bool:
    """Whether a pad's (net ...) child already puts it on net ``number``, named
    ``name``, in a shape its board's format writes: the pair up to format 20241229,
    the name alone from 20251028, either between (_NET_NAMES_ONLY_VERSION)."""
    atoms = net.atoms[1:]
    if len(atoms) == 1:
        return (
            version > _NUMERIC_NET_VERSION_MAX
            and _net_code(atoms[0]) is None
            and atoms[0].text == name
        )
    return (
        len(atoms) == 2
        and version < _NET_NAMES_ONLY_VERSION
        and _net_code(atoms[0]) == number
        and atoms[1].text == name
    )


def _bind_pad_net(root, pad, number: int, name: str, version: int | None = None) -> None:
    """Put *pad* on net ``number``, ``name``, changing it only where it is not
    already on it.

    A pad whose one (net ...) child already puts it there is left alone, whatever
    its bytes (_pad_holds_net). Otherwise the child is replaced where it stands, and
    a pad with none (one copied out of a .kicad_mod, or one that just gained a net)
    gets one straight after the last child KiCad writes before it
    (_PAD_HEADS_BEFORE_NET). That is after (layers ...) on a plain pad and after
    remove_unused_layers or roundrect_rratio on others, which is where KiCad's own
    writer puts it, not after (layers ...) always.

    ``version`` is the board's format when the caller has it, since finding it walks
    the whole board.
    """
    if version is None:
        version = _board_version(root)
    held = pad.find_all("net")
    if len(held) == 1 and _pad_holds_net(held[0], number, name, version):
        return
    node = _cst.parse(_pad_net_bytes(root, number, name, version)).lists[0]
    if held:
        node.sep = held[0].sep
        pad.children[pad.children.index(held[0])] = node
        for extra in held[1:]:
            pad.remove_child(extra)
        return
    anchor = next((c for c in reversed(pad.lists) if c.head in _PAD_HEADS_BEFORE_NET), None)
    if anchor is not None:
        pad.insert_after(anchor, node)
    else:
        pad.append_child(node, b"\n\t\t\t")


def _clear_pad_net(pad) -> None:
    for net in pad.find_all("net"):
        pad.remove_child(net)


def _referenced_net_numbers(root) -> list[int]:
    """Every net number a pad or a net-carrying board item refers to, a name or no
    net leaving none: the set KiCad 9's writer numbers the nets in use from
    (NETINFO_MAPPING::Update, pcbnew/netinfo_list.cpp:286-330 at 9.0.8)."""
    numbers = []
    for item in root.lists:
        if item.head == "footprint":
            holders = item.find_all("pad")
        elif item.head in _NET_ITEMS:
            holders = [item]
        else:
            continue
        for holder in holders:
            net = holder.find("net")
            code = _net_code(net.atoms[1]) if net is not None and len(net.atoms) > 1 else None
            if code is not None:
                numbers.append(code)
    return numbers


def _ensure_net_rows(root, names: list[str]) -> int:
    """Declare any missing nets, returning how many were added.

    KiCad 9 format boards carry a (net N "NAME") table at the root and numbers
    are assigned from it. KiCad 10 dropped the table entirely: nets exist only
    as name references on the items that use them, so there is nothing to
    declare and this reports zero rather than inventing a number. A name is
    written by set_text, so it is quoted as KiCad quotes it and a NUL is refused.

    A name is declared when no row has it, whoever else spells it: a board with
    no table at all (KiCad always writes one) gets a row for every name, a zone's
    net_name included, since KiCad 9 binds a number only through a row. A new row
    is numbered one above the largest number the board uses, a row's or the number
    of a pad, track, via, zone or copper shape: on a board KiCad wrote every one of
    those is a row, so that is the number one above the last row, but a number an
    earlier version wrote that no row has must not become the new row's, or its
    item would silently land on the new net.
    """
    if _board_version(root) > _NUMERIC_NET_VERSION_MAX:
        return 0
    table = _net_rows(root)
    have = {name for _, name in table}
    nxt = max([*(num for num, _ in table), *_referenced_net_numbers(root)], default=0) + 1
    added = 0
    rows = [c for c in root.find_all("net") if len(c.atoms) > 1]
    anchor = rows[-1] if rows else None
    for name in names:
        if name in have:
            continue
        node = _NET_ROW_TPL.copy()
        node.atoms[1].set_text(str(nxt))
        node.atoms[2].set_text(name)
        if anchor is not None:
            root.insert_after(anchor, node)
        else:
            _splice_after(root, node, ("general", "paper", "layers", "setup"), _PCB_TAIL_HEADS)
        anchor = node
        have.add(name)
        nxt += 1
        added += 1
    return added


def _prune_net_rows(root, used: set) -> int:
    """Drop declared nets nothing references. KiCad 9 dialect only."""
    if _board_version(root) > _NUMERIC_NET_VERSION_MAX:
        return 0
    removed = 0
    for row in [c for c in root.find_all("net") if len(c.atoms) > 2]:
        number, name = _net_code(row.atoms[1]), row.atoms[2].text
        if not number or name in used:
            continue
        root.remove_child(row)
        removed += 1
    return removed


def _fp_anchor(root) -> tuple[float, float]:
    """Where the cluster of newly added footprints starts.

    Below everything already placed, +Y being down. pcbnew computed this from a
    bounding box that includes graphics and courtyards; a CST scan sees
    footprint origins only, so new parts land in a slightly different spot than
    the subprocess put them. The tool promises a grid cluster and not a
    coordinate, and nothing downstream reads it.
    """
    ys = []
    xs = []
    for fp in root.find_all("footprint"):
        at = fp.find("at")
        if at is not None and len(at.atoms) > 2:
            xs.append(float(at.atoms[1].text))
            ys.append(float(at.atoms[2].text))
    if not xs:
        return 25.4, 25.4
    return min(xs), max(ys) + 10.0


def _fp_board_only(fp) -> bool:
    """Whether KiCad reads the footprint as board only (a mounting hole, a
    fiducial: a part that exists on the board alone). Its parser ORs the tokens of
    every (attr ...) list into the footprint's attributes, so board_only counts from
    any of them (pcb_io_kicad_sexpr_parser.cpp:4772-4819 and 5007 at 9.0.8,
    5558-5605 and 5822 at 10.0.7), and its update never deletes such a footprint
    (board_netlist_updater.cpp:1427-1428 at 9.0.8, 2334-2335 at 10.0.7)."""
    return any(a.raw == b"board_only" for attr in fp.find_all("attr") for a in attr.atoms[1:])


def _fp_locked(fp) -> bool:
    """Whether KiCad reads the footprint as locked: its parser runs parseMaybeAbsentBool(
    true ) on each locked token in turn (pcb_io_kicad_sexpr_parser.cpp:239-266 and
    4502-4504 at 9.0.8, 265-293 and 5190-5192 at 10.0.7), so a bare locked after the
    library id, (locked), (locked yes) and (locked true) lock it, (locked no) and (locked
    false) do not, and the last one written wins. A word KiCad refuses reads as locked
    here, so the update neither replaces nor removes a footprint it cannot read."""
    locked = False
    for child in fp.children[2:]:
        if child.kind == "atom":
            locked = locked or child.text == "locked"
        elif child.head == "locked":
            words = child.atoms[1:]
            locked = not words or words[0].text not in ("no", "false")
    return locked


def _load_netlist_footprint(fpid: str, resolver: _FpLibResolver):
    """(the board-ready copy of the library footprint ``fpid`` names, None), or (None,
    the reason the update reports in skipped)."""
    if ":" not in fpid:
        return None, "no_footprint_assigned"
    lib, name = fpid.split(":", 1)
    pretty = resolver.resolve(lib)
    if pretty is None:
        return None, f"footprint_lib_not_found:{lib}"
    node = _copy_lib_footprint_cst(pretty, name, fpid)
    if node is None:
        return None, f"footprint_not_found:{fpid}"
    return node, None


def _uuid_text(node) -> str | None:
    """The uuid KiCad reads for a list: its (uuid ...) or a legacy (tstamp ...)."""
    for child in node.lists:
        if child.head in ("uuid", "tstamp") and len(child.atoms) > 1:
            return child.atoms[1].text
    return None


def _set_uuid(node, value: str, before: str) -> None:
    """Give a pad or a text the uuid ``value``: its (uuid ...) or (tstamp ...) rewritten,
    or a (uuid ...) inserted where KiCad's writer puts one, before the first child headed
    ``before`` (a text's effects, a pad's own padstack) or else last
    (pcb_io_kicad_sexpr.cpp:1819 and 1999-2004 at 9.0.8)."""
    found = node.find("uuid") or node.find("tstamp")
    if found is not None:
        found.atoms[1].set_text(value)
        return
    uuid = _UUID_TPL.copy()
    uuid.atoms[1].set_text(value)
    anchor = node.find(before)
    if anchor is not None:
        node.insert_before(anchor, uuid)
    else:
        node.append_child(uuid, node.lists[-1].sep if node.lists else b" ")


def _kicad_pad_number(pad) -> str:
    """A pad's number as KiCad reads it: none for a non-plated hole or for a pad whose
    (layers ...) names no copper layer, which PAD::CanHaveNumber denies one and the parser
    clears (pcb_io_kicad_sexpr_parser.cpp:5558-5563 at 9.0.8, 6584-6589 at 10.0.7)."""
    names = pad.find("layers")
    no_copper = names is not None and not any(w.endswith(".Cu") for w in _layer_words(names))
    if (len(pad.atoms) > 2 and pad.atoms[2].text == "np_thru_hole") or no_copper:
        return ""
    return pad.atoms[1].text if len(pad.atoms) > 1 else ""


def _field_name(text) -> str | None:
    """The field a footprint text is: a (property ...)'s name, Reference or Value for a
    legacy (fp_text reference|value ...), None for any other text."""
    if text.head == "property" and len(text.atoms) > 1:
        name = text.atoms[1].text
        return None if name in _NOT_FP_TEXTS else name
    if text.head == "fp_text" and len(text.atoms) > 1:
        return {"reference": "Reference", "value": "Value"}.get(text.atoms[1].text)
    return None


def _swap_texts(fp, major: int) -> list:
    """(text, content, layer, footprint-relative nm or None) of each plain fp_text, in
    order, its layer KiCad's default when it writes none (_text_default_layer)."""
    out = []
    back = _fp_is_back(fp)
    for text in fp.find_all("fp_text"):
        if _field_name(text) is not None or len(text.atoms) < 3:
            continue
        layer = text.find("layer")
        at = text.find("at")
        out.append(
            (
                text,
                text.atoms[2].text,
                layer.atoms[1].text if layer is not None else _text_default_layer(text, back),
                _board_nm(at, major) if at is not None else None,
            )
        )
    return out


def _matching_text(old: tuple, candidates: list):
    """getMatchingTextItem (pcb_edit_frame.cpp:2298-2343 at 9.0.8): of the new fp_text
    items, the one an old one's uuid goes to: the one with its text; else of those, the
    one on its layer; else the first at its footprint-relative position, on its layer
    when any is; else the first on its layer; else the first with its text."""
    same = [c for c in candidates if c[1] == old[1]]
    if len(same) < 2:
        return same[0] if same else None
    on_layer = [c for c in same if c[2] == old[2]]
    if len(on_layer) == 1:
        return on_layer[0]
    at_place = [c for c in (on_layer or same) if c[3] == old[3]]
    return (at_place or on_layer or same)[0]


def _carry_swap_uuids(old, new, major: int) -> None:
    """The uuids KiCad 9.0.8's ExchangeFootprint carries from the old footprint to the
    new one (pcb_edit_frame.cpp:2346-2587): the footprint's own, so a group listing it
    still does; the Reference's, the Value's and every other field's by name; each plain
    fp_text's to the text getMatchingTextItem picks on the flipped and turned footprint,
    a later old text overwriting an earlier one's; and each pad's, by the number KiCad
    reads (_kicad_pad_number), to the first old pad of that number. KiCad also requires
    the two pads' IsOnCopperLayer to agree, which holds for every pad with a number,
    since a pad on no copper has none, so that test is not repeated here. An old item
    without a uuid in the file has nothing to carry.

    KiCad 10.0.7 pairs pads, fields and texts one to one by similarity instead
    (matchItemsBySimilarity, pcb_edit_frame.cpp:2527-2594 at 10.0.7), which is not
    ported. On a KiCad 10 board (``major``) a pad carries the old pad's uuid only when
    its number appears once in each footprint, the one pairing that similarity makes
    certain (a pad pair of one number scores 2 more than any other), and otherwise keeps
    the fresh one its library copy has: 9.0.8's first match would give the pads of a
    repeated number one uuid, which KiCad 10 never writes."""
    footprint = _uuid_text(old)
    if footprint is not None:
        new.find("uuid").atoms[1].set_text(footprint)
    old_pads = [(_kicad_pad_number(pad), _uuid_text(pad)) for pad in old.find_all("pad")]
    new_pads = [(_kicad_pad_number(pad), pad) for pad in new.find_all("pad")]
    old_numbers = [n for n, _ in old_pads]
    new_numbers = [n for n, _ in new_pads]
    for number, pad in new_pads:
        if not number:
            continue
        if major >= 10 and not old_numbers.count(number) == new_numbers.count(number) == 1:
            continue
        match = next((uuid for n, uuid in old_pads if n == number), None)
        if match is not None:
            _set_uuid(pad, match, "padstack")
    fields = {}
    for text in old.lists:
        if _field_name(text) is not None and _uuid_text(text) is not None:
            fields[_field_name(text)] = _uuid_text(text)
    named = {_field_name(text): text for text in new.lists if _field_name(text) is not None}
    for name, text in named.items():
        if name in fields:
            _set_uuid(text, fields[name], "effects")
    candidates = _swap_texts(new, major)
    for entry in _swap_texts(old, major):
        uuid = _uuid_text(entry[0])
        target = _matching_text(entry, candidates)
        if uuid is not None and target is not None:
            _set_uuid(target[0], uuid, "effects")


#: The fields every footprint holds from its constructor, whatever its file writes
#: (FOOTPRINT::addMandatoryFields, pcbnew/footprint.cpp:94-111 at 9.0.8; the constructor,
#: 114-117 at 10.0.7).
_MANDATORY_FIELDS = ("Reference", "Value", "Datasheet", "Description")


def _swap_paired_texts(old, new, major: int) -> list:
    """The texts of the swapped footprint ``new`` that KiCad 10.0.7's exchange hands an old
    text, whose processTextItem then runs KeepUpright on them (pcb_edit_frame.cpp:2504 at
    10.0.7): every field whose name the old footprint holds, its file's fields and the
    four every footprint holds; every plain fp_text getMatchingTextItem picks for an old
    one. KiCad 10.0.7 pairs by similarity (_carry_swap_uuids), which is not ported: this
    is KiCad 9.0.8's pairing, which 10.0.7 shares where each footprint holds a field name
    or an fp_text text once and those texts lie far apart, but not where an fp_text whose
    text the other footprint lacks meets an unpaired one of another text there, which
    10.0.7 may pair with it. Call it after the flip, as KiCad pairs the flipped
    footprint's texts."""
    names = ({_field_name(text) for text in old.lists} - {None}) | set(_MANDATORY_FIELDS)
    paired = [text for text in _fp_texts(new) if _field_name(text) in names]
    candidates = _swap_texts(new, major)
    for entry in _swap_texts(old, major):
        target = _matching_text(entry, candidates)
        if target is not None:
            paired.append(target[0])
    return paired


def _swap_footprint(old, comp: dict, resolver: _FpLibResolver, layers: _FlipLayers, summary):
    """The footprint the update puts in place of ``old`` when its symbol names another,
    as KiCad's updater swaps it (BOARD_NETLIST_UPDATER::replaceFootprint,
    board_netlist_updater.cpp:300-380 at 9.0.8, 313-393 at 10.0.7), or None when it does
    not, the reason added to skipped: a footprint whose library or name does not resolve;
    a locked one (_fp_locked), which KiCad never replaces while locks are not overridden;
    and one this tool refuses, the new footprint carrying a construct it does not move or
    flip (_fp_unsupported).

    The swap follows ExchangeFootprint (pcb_edit_frame.cpp:2346-2587 at 9.0.8,
    2597-3068 at 10.0.7): the library copy is moved to the old position, flipped when
    its side differs, and set to the old orientation by SetOrientation when its own
    differs, so it keeps the old footprint's position, side and angle with its pads,
    texts and zones turned and flipped with it (_fp_translate, _fp_flip, _fp_turn). No
    KeepUpright follows on a KiCad 9 board; on a KiCad 10 board processTextItem runs
    KeepUpright on each text the exchange pairs with an old one (_swap_paired_texts),
    whether or not the footprint turned. Then it takes the old uuids (_carry_swap_uuids),
    the symbol's Reference, Value, path and fields."""
    node, reason = _load_netlist_footprint(comp["footprint"], resolver)
    if node is None:
        summary["skipped"].append({"ref": comp["ref"], "reason": reason})
        return None
    if _fp_locked(old):
        summary["skipped"].append({"ref": comp["ref"], "reason": "footprint_locked"})
        return None
    flipping = _fp_is_back(node) != _fp_is_back(old)
    unsupported = _fp_unsupported(node, layers, flipping)
    if unsupported:
        reason = f"footprint_unsupported:{comp['footprint']} carries {unsupported}"
        summary["skipped"].append({"ref": comp["ref"], "reason": reason})
        return None
    at = old.find("at")
    x, y = (at.atoms[1].text, at.atoms[2].text) if at is not None else ("0", "0")
    _fp_translate(node, x, y, layers.major)
    if flipping:
        _fp_flip(node, layers)
    atom = _at_angle_atom(at) if at is not None else None
    target = _n180(float(atom.text)) if atom is not None else 0.0
    new_atom = _at_angle_atom(node.find("at"))
    held = _n180(float(new_atom.text)) if new_atom is not None else 0.0
    turn = None if target == held else target
    if layers.major >= 10:
        paired = {id(text) for text in _swap_paired_texts(old, node, layers.major)}
        _fp_turn(node, turn, layers.major, lambda text: id(text) in paired)
    elif turn is not None:
        _fp_turn(node, turn, layers.major, lambda text: False)
    _carry_swap_uuids(old, node, layers.major)
    _set_fp_property(node, "Reference", comp["ref"])
    _set_fp_property(node, "Value", comp["value"])
    _set_fp_path(node, comp["path"])
    # After the turn and the flip, so a new field takes the final side and angle.
    _sync_fp_from_component(node, comp)
    return node


def _apply_netlist_cst(
    netlist_path: str,
    pcb_path: str,
    lib_dirs: list[str],
    delete_stale: bool = False,
    project_dir: Path | None = None,
) -> dict:
    """Apply a parsed netlist to a board through the substrate. Returns a summary.

    A footprint's library nickname goes through _FpLibResolver: the project's
    fp-lib-table under project_dir, then lib_dirs, then the global table, then
    the stock footprints, the order place_footprint uses as well.

    Replaces _netlist_import.apply(), which was the last pcbnew.SaveBoard
    writing a user's own board. What that cost is measured in
    docs/adr-cst-substrate.md: coincident PCB_SHAPEs de-duplicated at save on
    both majors, every fp_text carrying (hide yes) destroyed at load on KiCad 9,
    the format stamp raised in place, and on Windows every line ending rewritten
    even on a no-op round trip.

    The passes, in order: match by reference, add or swap footprints, declare the
    schematic's nets (a KiCad 9 format board's table, _ensure_net_rows), bind pads,
    report or remove stale footprints, send what is on a net that left the schematic
    to no net, prune the rows nothing uses.

    A pad is bound only where its net changes (_bind_pad_net), so one the update has
    nothing to change is not touched, and a pad of a matched footprint that the
    netlist leaves without a net loses its (net ...). Every segment, arc, via, zone
    and copper shape on a net that is neither in the netlist nor under a pad of a
    footprint that stays (a stale one kept: not removed, locked or board only) is put
    on no net, spelled as the board's format spells it (_set_item_net); segments, arcs
    and vias are counted in orphaned_tracks, zones in orphaned_zones, and a shape,
    which has no field, is named in warnings. A net a pad that stays is on keeps its
    row and everything on it. KiCad's update keeps such a net too, since it marks the
    net of every pad of every footprint it keeps current and removes the rest
    (pcbnew/netlist_reader/board_netlist_updater.cpp:1475-1485 and 1506 at 9.0.8).
    Nothing on a renamed net is moved to its new name: it goes to no net with the rest,
    or stays on the old net while a pad that stays holds it.
    """
    components, nets = _parse_netlist(netlist_path)
    summary = _new_summary()
    resolver = _FpLibResolver(project_dir, lib_dirs)

    # A symbol with "On board" unticked is not on this board, which is what
    # KiCad's own update does with it: never placed, and a footprint it left
    # behind is as stale as one whose symbol was deleted. Its pins still appear
    # in the netlist's nets, so the pad pass skips them without a warning.
    excluded = {c["ref"] for c in components if c.get("exclude_from_board")}
    summary["excluded_from_board"] = sorted(excluded)
    components = [c for c in components if c["ref"] not in excluded]

    if not Path(pcb_path).is_file():
        _atomic_write(pcb_path, _EMPTY_PCB_TPL)
    tree, root, key = _open_pcb_cst(pcb_path)
    _BOARD_CACHE.pop(key, None)

    ax, ay = _fp_anchor(root)
    by_ref = _fp_by_reference(root)
    netlist_refs = {c["ref"] for c in components}

    # What the bare numbers on the board's items bind to, read before anything changes: on
    # a board with no table KiCad 10 numbers the nets in the order its pads and items make
    # them (_bound_names), and both the swap below, which puts a library copy with no net
    # on its pads in place of a footprint, and the pad pass renumber them.
    version = _board_version(root)
    bound = _bound_names(root)

    # --- Components, matched by reference (sorted: deterministic layout) ---
    layers = _board_flip_layers(root)
    k = 0
    for comp in sorted(components, key=lambda c: c["ref"]):
        ref, value, fpid = comp["ref"], comp["value"], comp["footprint"]
        existing = by_ref.get(ref)

        if existing is not None and fpid and existing.atoms[1].text != fpid:
            node = _swap_footprint(existing, comp, resolver, layers, summary)
            if node is not None:
                root.children[root.children.index(existing)] = node
                node.sep = existing.sep
                by_ref[ref] = node
                summary["fpid_changed"].append(ref)
                continue
            # A swap that did not happen (skipped says why) leaves the footprint as
            # it is, and the rest of the symbol's update still reaches it, as
            # KiCad's updater updates the old footprint whenever replaceFootprint
            # returns none (board_netlist_updater.cpp:1366-1383 at 9.0.8).

        if existing is not None:
            if _set_fp_property(existing, "Value", value):
                summary["value_updated"].append(ref)
            _set_fp_path(existing, comp["path"])
            if _sync_fp_from_component(existing, comp):
                summary["fields_updated"].append(ref)
            continue

        node, reason = _load_netlist_footprint(fpid, resolver)
        if node is None:
            summary["skipped"].append({"ref": ref, "reason": reason})
            continue
        # The move below leaves behind what the helper does not carry, so a footprint
        # holding it is not added. It stays on its library's side: nothing flips it.
        unsupported = _fp_unsupported(node, layers, False)
        if unsupported:
            reason = f"footprint_unsupported:{fpid} carries {unsupported}"
            summary["skipped"].append({"ref": ref, "reason": reason})
            continue
        _set_fp_property(node, "Reference", ref)
        _set_fp_property(node, "Value", value)
        _set_fp_path(node, comp["path"])
        # A new footprint stands at orientation 0, so its (at) carries no
        # angle, as KiCad's writer leaves one of 0 out.
        x, y = _grid_slot(k, ax, ay, 10.0)
        k += 1
        _fp_translate(node, _num(x, PCB_IU_PER_MM), _num(y, PCB_IU_PER_MM), layers.major)
        _sync_fp_from_component(node, comp)
        _splice_after(root, node, ("footprint",), _PCB_TAIL_HEADS)
        by_ref[ref] = node
        summary["added"].append(ref)

    # --- Nets: declare-only, so existing numbers never move under live tracks ---
    summary["nets_added"] = _ensure_net_rows(root, [n["name"] for n in nets])
    numbers = {name: num for num, name in _net_table(root)}

    # --- Pads, scoped to netlist-matched footprints, so board-only footprints
    # (mounting holes and the like) are never touched. A pad the netlist puts on a
    # net is bound to it, which changes nothing when it is on it already; a pad it
    # leaves without a net loses its net, as KiCad's update sets it unconnected ---
    wanted: dict[tuple[str, str], tuple[int, str]] = {}
    for net in nets:
        name = net["name"]
        number = numbers.get(name, 0)
        for ref, pin in net["nodes"]:
            if ref in excluded:
                continue
            fp = by_ref.get(ref)
            if fp is None:
                summary["warnings"].append(f"net {name}: {ref}.{pin} not on board")
            elif any(pad.atoms[1].text == pin for pad in fp.find_all("pad")):
                wanted[ref, pin] = (number, name)
                summary["pads_bound"] += 1
            else:
                summary["warnings"].append(f"{ref}: pad {pin} not on footprint (net {name})")
    for ref in netlist_refs:
        fp = by_ref.get(ref)
        if fp is None:
            continue
        for pad in fp.find_all("pad"):
            target = wanted.get((ref, pad.atoms[1].text))
            if target is None:
                _clear_pad_net(pad)
            else:
                _bind_pad_net(root, pad, target[0], target[1], version)

    # --- Stale footprints: always reported, removed only on request. KiCad's
    # update never deletes a footprint marked board only, whatever the option, nor
    # does this one; a locked one is kept too ---
    for fp in list(root.find_all("footprint")):
        ref = _fp_prop_cst(fp, "Reference")
        if ref in netlist_refs:
            continue
        summary["stale_footprints"].append(ref)
        if delete_stale and not _fp_locked(fp) and not _fp_board_only(fp):
            root.remove_child(fp)
            by_ref.pop(ref, None)
            summary["stale_removed"].append(ref)

    # --- Items on nets that left the schematic go to no net. A net is kept while a pad
    # of a footprint that stays is on it: KiCad's update marks the net of every pad of
    # every footprint it keeps current, and only a net no pad is on is removed, which
    # sends everything on it to net 0 (board_netlist_updater.cpp:1475-1485 and 1506 at
    # 9.0.8, 2406-2416 and 2438 at 10.0.7; board_commit.cpp:399-400 and board.cpp:
    # 1328-1341 at 9.0.8). So this runs after the stale footprints are gone ---
    live = {n["name"] for n in nets}
    kept = live | {
        _read_net_name(pad, version, bound)
        for fp in root.find_all("footprint")
        for pad in fp.find_all("pad")
    }
    for item in root.lists:
        if item.head not in _NET_ITEMS:
            continue
        name = _read_net_name(item, version, bound)
        if not name or name in kept:
            continue
        _set_item_net(item, root, 0, version)
        if item.head == "zone":
            summary["orphaned_zones"] += 1
        elif item.head in ("segment", "arc", "via"):
            summary["orphaned_tracks"] += 1
        else:
            layer = item.find("layer")
            where = f" on {layer.atoms[1].text}" if layer is not None else ""
            summary["warnings"].append(
                f"{item.head}{where} was on net {name}, which left the schematic;"
                " it is on no net now"
            )

    # --- Prune declared nets nothing references any more ---
    used = {name for name in kept if name}
    for item in root.lists:
        if item.head in _NET_ITEMS:
            used.add(_read_net_name(item, version, bound))
    summary["nets_removed"] = _prune_net_rows(root, used)

    if summary["skipped"]:
        summary["status"] = "incomplete"

    _atomic_write(key, _cst.serialize(tree))
    return summary


# ---------------------------------------------------------------------------
# CLI analysis tools (1)
# ---------------------------------------------------------------------------

_NO_COPPER = "They contribute no copper to this output."


def _unfilled_zone_note(pcb_path: str, consequence: str = _NO_COPPER) -> str | None:
    """Advisory when copper zones carry no computed fill, else None.

    A zone stores its outline and its copper separately: the (polygon ...) child
    is the boundary someone drew, the (filled_polygon ...) children are what
    pcbnew's filler produced, and only the second plots. kicad-cli plots what is
    stored and never refills, so an unfilled zone ships nothing at all, quietly.
    Measured on KiCad's own ecc83-pp: the F.Cu gerber is 54,448 bytes with the
    fills and 6,378 without, at exit 0 with empty stderr. KiCad's GUI warns about
    stale fills; the CLI does not, and neither did we.

    Keyed on filled_polygon presence and nothing else. The (fill yes ...) flag
    is deliberately not consulted: it means "pour enabled", not "poured", and the
    question here is only whether copper will appear in the output.

    Advisory, so it never becomes a failure mode of its own: an unparseable board
    answers None and the export's own error stays the authority.
    """
    try:
        _, root, _ = _open_pcb_cst(pcb_path)
    except ToolError:
        return None
    # find_all is direct children, so footprint-level keepouts are already out of
    # scope; board-level rule areas are skipped explicitly.
    unfilled = sum(
        1
        for z in root.find_all("zone")
        if z.find("keepout") is None and not z.find_all("filled_polygon")
    )
    if not unfilled:
        return None
    plural = "s have" if unfilled > 1 else " has"
    return f"{unfilled} copper zone{plural} no computed fill. {consequence} Run fill_zones first."


#: KiCad 10 can refill zones in memory before it plots, and documents that it
#: does not save the board afterwards. Measured on both KiCad 10.0.5 runners
#: 2026-08-15 rather than taken from the docs: `pcb drc --refill-zones` without
#: --save-board leaves the board byte-identical, and so does
#: `pcb export gerbers --check-zones`. The control in that probe confirmed the
#: instrument could see a save at all, by running --save-board on the same board
#: and watching it gain filled_polygon. KiCad 9 has neither flag and rejects
#: both as unrecognized arguments.
_ZONE_REFILL_MIN_MAJOR = 10


def _refill_or_note(
    flag: str, pcb_path: str, consequence: str = _NO_COPPER
) -> tuple[list[str], str | None]:
    """Either the flag that fixes the output, or the note that warns about it.

    Returned together because they are mutually exclusive and quietly getting
    both wrong is easy: pass --check-zones and the zones are filled, so the note
    saying "they contribute no copper to this output" is not merely redundant,
    it is false. Emitting the argv fragment and the note from one place makes
    that unrepresentable rather than a rule to remember at six call sites.

    An unreadable CLI version means no opinion, so the note is kept and the
    behaviour is exactly what it was before the flag existed.
    """
    major = _kicad_cli_major()
    if major is not None and major >= _ZONE_REFILL_MIN_MAJOR:
        return [flag], None
    return [], _unfilled_zone_note(pcb_path, consequence)


@mcp.tool(annotations=_EXPORT)
def run_drc(pcb_path: str = PCB_PATH, output_dir: str = OUTPUT_DIR) -> DrcResult:
    """Run Design Rules Check (DRC) on a PCB.

    Returns structured report with violations.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
        output_dir: Directory for report file (default: same as PCB).
            Optional; omit to use the configured default.
    """
    _require_kicad_path(pcb_path, "board")
    out_dir = output_dir or str(Path(pcb_path).parent)
    out_path = str(Path(out_dir) / (Path(pcb_path).stem + "-drc.json"))
    # --refill-zones rather than --check-zones: pcb drc does not take the latter,
    # and the two are not spelled the same for the same job. DRC's failure mode
    # is also different from an export's, hence its own consequence sentence.
    refill, note = _refill_or_note(
        "--refill-zones",
        pcb_path,
        "DRC reads the stored fill, so it reports connectivity errors the design does not have.",
    )
    _run_cli(
        ["pcb", "drc", "--format", "json", "--severity-all", "--output", out_path]
        + refill
        + [pcb_path],
        check=False,
    )
    try:
        with open(out_path, encoding="utf-8") as f:
            report = json.load(f)
    except FileNotFoundError:
        raise ToolError("DRC failed to produce output file")
    violations = report.get("violations", [])
    unconnected = report.get("unconnected_items", [])
    return DrcResult(
        source=report.get("source", ""),
        kicad_version=report.get("kicad_version", ""),
        violation_count=len(violations),
        violations=violations,
        unconnected_count=len(unconnected),
        unconnected_items=unconnected,
        note=note,
    )


# ---------------------------------------------------------------------------
# CLI PCB export tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_EXPORT)
def export_pcb(
    format: Literal["pdf", "svg", "dxf"] = "pdf",
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    layers: list[str] | None = None,
    output_units: Literal["in", "mm"] = "in",
    exclude_refdes: bool = False,
    exclude_value: bool = False,
    use_contours: bool = False,
    include_border_title: bool = False,
) -> PcbExportResult:
    """Export PCB to PDF, SVG, or DXF format.

    Args:
        format: Output format - "pdf", "svg", or "dxf"
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
        output_dir: Directory for output files. Optional; omit to use the configured default.
        layers: Optional list of layer names to include (required for DXF)
        output_units: DXF output units - "in" or "mm" (DXF only)
        exclude_refdes: Exclude reference designators (DXF only)
        exclude_value: Exclude component values (DXF only)
        use_contours: Use board outline contours (DXF only)
        include_border_title: Include border and title block (DXF only)
    """
    _require_kicad_path(pcb_path, "board")
    # Literal publishes the enum so a model picks a valid value; this check
    # still matters because Literal is only enforced by pydantic at the MCP
    # boundary, and a direct Python call sails straight past it.
    fmt = format.lower()
    if fmt not in ("pdf", "svg", "dxf"):
        raise ToolError(f"Unknown format: {format}. Use: pdf, svg, dxf")

    units = output_units.lower()
    if units not in ("in", "mm"):
        raise ToolError(f"Unknown output_units: {output_units}. Use: in, mm")

    if fmt == "dxf":
        if not layers:
            raise ToolError("layers parameter is required for DXF export")
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + ".dxf"))
        refill, note = _refill_or_note("--check-zones", pcb_path)
        args = ["pcb", "export", "dxf", pcb_path, "-o", out_path, "-l", ",".join(layers)] + refill
        if units != "in":
            args += ["--output-units", units]
        if exclude_refdes:
            args.append("--exclude-refdes")
        if exclude_value:
            args.append("--exclude-value")
        if use_contours:
            args.append("--use-contours")
        if include_border_title:
            args.append("--include-border-title")
        result = _run_cli(args, check=False)
        if result.returncode != 0:
            raise ToolError(result.stderr.strip())
        meta = _file_meta(out_path)
        return PcbExportResult(
            path=meta["path"],
            size_bytes=meta["size_bytes"],
            format="dxf",
            layers=layers,
            note=note,
        )

    # PDF / SVG path
    out_dir = output_dir or str(Path(pcb_path).parent)
    ext = ".pdf" if fmt == "pdf" else ".svg"
    out_path = str(Path(out_dir) / (Path(pcb_path).stem + ext))
    if fmt == "pdf":
        layer_list = layers or ["F.Cu", "B.Cu"]
    else:
        layer_list = layers or ["F.Cu"]
    refill, note = _refill_or_note("--check-zones", pcb_path)
    _run_cli(
        ["pcb", "export", fmt, "--layers", ",".join(layer_list), "--output", out_path]
        + refill
        + [pcb_path]
    )
    meta = _file_meta(out_path)
    return PcbExportResult(
        path=meta["path"],
        size_bytes=meta["size_bytes"],
        format=fmt,
        layers=layer_list,
        note=note,
    )


@mcp.tool(annotations=_EXPORT)
def export_gerbers(
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    include_drill: bool = True,
    layers: list[str] | None = None,
) -> GerberExportResult:
    """Export Gerber files for manufacturing.

    When layers contains exactly one layer, exports a single Gerber file: path
    names it, and size_bytes and layer are filled. Otherwise exports all layers
    (or the specified subset) plus optional drill files, and path names the
    output directory. files and count are filled either way.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
        output_dir: Output directory for gerber files. Optional; omit to use the configured default.
        include_drill: Also export drill files (default: True, ignored in single-layer mode)
        layers: Optional list of layer names. Single layer = single file output.
    """
    _require_kicad_path(pcb_path, "board")
    # Single-layer mode: one file, like the old export_gerber
    if layers and len(layers) == 1:
        layer = layers[0].strip()
        if not layer:
            raise ToolError("At least one layer must be specified")
        out_dir = output_dir or str(Path(pcb_path).parent)
        _ensure_dir(out_dir)
        out_path = str(Path(out_dir) / f"{Path(pcb_path).stem}-{layer.replace('.', '_')}.gbr")
        # KiCad 10 removed `pcb export gerber` (#8), so plot with the plural
        # into a scratch dir. The plural exits 0 on a bad layer name and always
        # writes a .gbrjob sidecar, so success is "exactly one .gbr", not
        # "exit 0"; globbing rather than predicting the name also survives
        # user-renamed layers. The scratch dir sits inside out_dir so
        # os.replace never crosses filesystems.
        refill, note = _refill_or_note("--check-zones", pcb_path)
        with tempfile.TemporaryDirectory(dir=out_dir) as tmp_dir:
            result = _run_cli(
                ["pcb", "export", "gerbers", "--layers", layer, "--no-protel-ext"]
                + refill
                + ["--output", tmp_dir, pcb_path]
            )
            produced = list(Path(tmp_dir).glob("*.gbr"))
            if len(produced) != 1:
                detail = (result.stdout + result.stderr).strip() or "no output"
                raise ToolError(
                    f"Expected one gerber for layer {layer!r}, got {len(produced)}: {detail}"
                )
            os.replace(produced[0], out_path)
        meta = _file_meta(out_path)
        return GerberExportResult(
            path=meta["path"],
            format="gerber",
            files=[Path(meta["path"]).name],
            count=1,
            size_bytes=meta["size_bytes"],
            layer=layer,
            note=note,
        )

    # Multi-layer mode: directory of files
    out = output_dir or str(Path(pcb_path).parent / "gerbers")
    _ensure_dir(out)
    refill, note = _refill_or_note("--check-zones", pcb_path)
    cmd = ["pcb", "export", "gerbers"]
    if layers:
        cmd += ["--layers", ",".join(layers)]
    cmd += refill + ["--output", out, pcb_path]
    _run_cli(cmd)
    files = sorted(Path(out).glob("*"))
    drill_file_names: list[str] = []
    if include_drill:
        # No refill flag here: pcb export drill does not take one, and drilling
        # does not read zone copper anyway.
        _run_cli(["pcb", "export", "drill", "--output", out, pcb_path])
        drill_files = sorted(Path(out).glob("*.drl")) + sorted(Path(out).glob("*.DRL"))
        drill_file_names = [f.name for f in drill_files]
    return GerberExportResult(
        path=out,
        format="gerber",
        files=[f.name for f in files],
        count=len(files),
        drill_files=drill_file_names,
        drill_count=len(drill_file_names),
        note=note,
    )


@mcp.tool(annotations=_EXPORT)
def export_3d(
    format: Literal["step", "stl", "glb", "render"] = "step",
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    width: int = 1600,
    height: int = 900,
    side: str = "top",
    quality: str = "basic",
) -> Model3dExportResult:
    """Export PCB 3D model or render 3D view to image.

    `render` fills width, height and side; the mesh formats leave them unset.

    Args:
        format: Output format - "step", "stl", "glb", or "render" (PNG image)
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
        output_dir: Output directory. Optional; omit to use the configured default.
        width: Image width in pixels (render only)
        height: Image height in pixels (render only)
        side: View side: top, bottom, left, right, front, back (render only)
        quality: Render quality: basic, high (render only)
    """
    _require_kicad_path(pcb_path, "board")
    # See export_pcb: the enum is for the model, this is for direct callers.
    fmt = format.lower()
    if fmt not in ("step", "stl", "glb", "render"):
        raise ToolError(f"Unknown format: {format}. Use: step, stl, glb, render")

    if fmt == "render":
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + f"-3d-{side}.png"))
        _run_cli(
            [
                "pcb",
                "render",
                "--width",
                str(width),
                "--height",
                str(height),
                "--side",
                side,
                "--quality",
                quality,
                "--output",
                out_path,
                pcb_path,
            ]
        )
        meta = _file_meta(out_path)
        return Model3dExportResult(
            path=meta["path"],
            size_bytes=meta["size_bytes"],
            format="png",
            width=width,
            height=height,
            side=side,
        )

    # STEP / STL / GLB path
    out_dir = output_dir or str(Path(pcb_path).parent)
    out_path = str(Path(out_dir) / (Path(pcb_path).stem + f".{fmt}"))
    _run_cli(["pcb", "export", fmt, "--output", out_path, pcb_path])
    meta = _file_meta(out_path)
    return Model3dExportResult(path=meta["path"], size_bytes=meta["size_bytes"], format=fmt)


@mcp.tool(annotations=_EXPORT)
def export_positions(
    pcb_path: str = PCB_PATH, output_dir: str = OUTPUT_DIR
) -> PositionExportResult:
    """Export component position file (pick and place).

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
        output_dir: Output directory. Optional; omit to use the configured default.
    """
    _require_kicad_path(pcb_path, "board")
    out_dir = output_dir or str(Path(pcb_path).parent)
    out_path = str(Path(out_dir) / (Path(pcb_path).stem + "-pos.csv"))
    _run_cli(["pcb", "export", "pos", "--format", "csv", "--output", out_path, pcb_path])
    meta = _file_meta(out_path)
    with open(out_path, encoding="utf-8") as f:
        component_count = max(0, len(f.readlines()) - 1)
    return PositionExportResult(
        path=meta["path"],
        size_bytes=meta["size_bytes"],
        format="csv",
        component_count=component_count,
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def export_ipc2581(
    pcb_path: str = PCB_PATH,
    output: str = "",
    precision: int = 3,
    compress: bool = False,
    version: str = "C",
    units: str = "mm",
) -> ExportResult:
    """Export PCB in IPC-2581 format for manufacturing data exchange.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
        output: Output file path
        precision: Numeric precision (default: 3)
        compress: Compress output file
        version: IPC-2581 version (default: "C")
        units: Output units - "mm" or "in"
    """
    _require_kicad_path(pcb_path, "board")
    out = output or str(Path(OUTPUT_DIR) / (Path(pcb_path).stem + ".xml"))
    args = ["pcb", "export", "ipc2581", pcb_path, "-o", out]
    if precision != 3:
        args += ["--precision", str(precision)]
    if compress:
        args.append("--compress")
    if version != "C":
        args += ["--version", version]
    if units != "mm":
        args += ["--units", units]
    result = _run_cli(args, check=False)
    if result.returncode != 0:
        raise ToolError(result.stderr.strip())
    meta = _file_meta(out)
    return ExportResult(
        path=meta["path"],
        size_bytes=meta["size_bytes"],
        format="ipc2581",
        # The one export that keeps the note on every KiCad: --check-zones is
        # not offered on ipc2581, only on dxf, gerbers, pdf, ps and svg.
        note=_unfilled_zone_note(pcb_path),
    )


# ---------------------------------------------------------------------------
# Autoroute internals (CST; the DSN/SES round trip itself lives in
# _freerouting.py and rides pcbnew and Java)
# ---------------------------------------------------------------------------


def _trace_counts(pcb_path: str) -> tuple[int, int, int]:
    """(segments, vias, format version) for the board at *pcb_path*.

    Segments and vias are counted as top-level heads. Arcs stay out of both
    counts, which is what the retired kiutils count did: its Arc is a class
    of its own, not a Segment subclass. Parsed fresh rather than through
    _open_pcb_cst, because the routed file this runs on is written between
    the two calls.
    """
    root = _cst.parse(_read_kicad_bytes(pcb_path, "board")).lists[0]
    heads = [c.head for c in root.lists]
    return heads.count("segment"), heads.count("via"), _board_version(root)


_HATCH_TPL = _cst.parse(b"(hatch edge 0.5)").lists[0]
_UUID_TPL = _cst.parse(b'(uuid "x")').lists[0]


def _replace_child(node, new) -> None:
    """Swap *node*'s child named like *new* for *new*, splicing it in if absent."""
    old = node.find(new.head)
    if old is None:
        node.insert_after(node.atoms[0], new, sep=b" ")
        return
    new.sep = old.sep
    node.children[node.children.index(old)] = new


def _promote_footprint_keepouts(pcb_path: str, output_path: str) -> int:
    """Promote footprint-level keepout zones to board level in a copy.

    pcbnew's ExportSpecctraDSN does not export keepout zones defined inside
    a footprint, so the autorouter would never see them. This parses
    *pcb_path*, appends one board-level zone per footprint keepout zone with
    its points copied as stored, and writes the result to *output_path*. The
    source board is never modified.

    A footprint's zone is already in board coordinates: format( ZONE ) writes
    it without the footprint (pcb_io_kicad_sexpr.cpp:2628-2637 at 9.0.8,
    3061-3074 at 10.0.7), so its points mean the same at board level, and a
    footprint with no (at) is promoted like any other.

    A zone's polygons stay together. KiCad's zone parser makes the first one
    the outline and every later one a hole in it, so promoting each polygon as
    a zone of its own turned a cutout into a keepout over exactly the area it
    was cut out to free. A zone with no polygon at all is skipped.

    Returns the number of zones promoted. At zero, *output_path* is not
    written and the caller feeds the original board to the DSN export.
    """
    tree = _cst.parse(_read_kicad_bytes(pcb_path, "board"))
    root = tree.lists[0]
    count = 0

    for fp in root.find_all("footprint"):
        for source_zone in fp.find_all("zone"):
            if source_zone.find("keepout") is None or source_zone.find("polygon") is None:
                continue
            zone = source_zone.copy()
            _replace_child(zone, _HATCH_TPL.copy())
            fresh_uuid = _UUID_TPL.copy()
            fresh_uuid.atoms[1].set_text(_gen_uuid())
            _replace_child(zone, fresh_uuid)
            _set_item_net(zone, root, 0)
            _splice_pcb_zone(root, zone)
            count += 1

    if count > 0:
        try:
            _atomic_write(output_path, _cst.serialize(tree))
        except OSError as e:
            raise ToolError(f"Failed to prepare PCB for autorouting: {e}") from e
    return count


_FP_TEXT_DISPLACEMENT_THRESHOLD_MM = 5.0
"""Maximum distance (mm) a footprint text may sit from the footprint origin
before it counts as displaced and is reset. Footprint text positions are
stored relative to their parent footprint, so (0, 0) means centered on it."""

_FP_TEXT_DEFAULT_OFFSETS: dict[str, tuple[float, float]] = {
    "reference": (0, -1.5),
    "value": (0, 1.5),
}
"""Default (X, Y) offsets for well-known text types, relative to the footprint
origin.  Any displaced text type not listed here is reset to (0, 0)."""


def _fix_displaced_fp_text(pcb_path: str) -> int:
    """Reset footprint text fields displaced by the Freerouting round trip.

    After the DSN->SES round trip, footprint texts (Reference, Value, etc.)
    can come back scrambled far from their footprint. Every text further
    from the origin than ``_FP_TEXT_DISPLACEMENT_THRESHOLD_MM`` is reset to
    its type's default offset, keeping any rotation atom it carries.

    Both node shapes are covered: ``(fp_text <type> ...)`` and the
    ``(property "Reference"/"Value" ...)`` fields pcbnew 7 and newer write
    for those two. The retired kiutils twin saw only FpText graphic items,
    so on a board pcbnew itself had written it reached user texts and
    nothing else.

    Returns the number of texts reset; the file is rewritten only when that
    count is non-zero.
    """
    tree = _cst.parse(_read_kicad_bytes(pcb_path, "board"))
    fixed = 0
    for fp in tree.lists[0].find_all("footprint"):
        for text in fp.find_all("fp_text") + fp.find_all("property"):
            at = text.find("at")
            if at is None:
                continue  # e.g. the bare (property "Reference" "R1") kiutils writes
            if math.hypot(*_xy(at)) <= _FP_TEXT_DISPLACEMENT_THRESHOLD_MM:
                continue
            kind = text.atoms[1].text.lower()
            offset_x, offset_y = _FP_TEXT_DEFAULT_OFFSETS.get(kind, (0, 0))
            _fill_at(text, offset_x, offset_y, iu=PCB_IU_PER_MM)
            fixed += 1
    if fixed > 0:
        _atomic_write(pcb_path, _cst.serialize(tree))
    return fixed


# autoroute_pcb fits no preset, so it carries its own hints.
#
# openWorldHint is true because _ensure_jar reaches api.github.com for the
# latest Freerouting release, downloads that JAR, and then runs it. A client
# using the hint to decide whether a tool may run offline or without egress
# approval has to be told the truth about that.
#
# destructiveHint and idempotentHint are deliberately left unset rather than
# asserted. The spec defaults are true and false respectively, which are both
# the accurate readings here: the tool rewrites <stem>_routed.kicad_pcb and a
# _routed-drc.json beside it, and Freerouting is a heuristic router steered by
# max_passes and num_threads, so two runs with the same arguments need not
# agree. An unset hint beats a wrongly asserted one.
_AUTOROUTE = ToolAnnotations(read_only_hint=False, open_world_hint=True)


@mcp.tool(annotations=_AUTOROUTE)
def autoroute_pcb(
    pcb_path: str = PCB_PATH,
    max_passes: int = 20,
    num_threads: int = 1,
    timeout: int = 600,
    output_dir: str = OUTPUT_DIR,
) -> AutorouteResult:
    """Autoroute PCB traces using the Freerouting autorouter.

    Exports the board to Specctra DSN format, runs Freerouting for automated
    trace routing, and imports the results into a new PCB file. The original
    board is never modified.

    Requires Java 17+ and KiCad's pcbnew Python bindings, whose major version
    has to match the board's format era. On first run, the Freerouting JAR is
    auto-downloaded (~20MB).

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
        max_passes: Maximum autorouter optimization passes
        num_threads: Thread count for routing. Defaults to 1 because
            freerouting prints "Multi-threaded route optimization is broken
            and it is known to generate clearance violations" on every run.
            Raise it only if you are willing to DRC the result carefully.
        timeout: Max seconds to wait for routing (default: 600)
        output_dir: Directory for output files (default: same as PCB).
            Optional; omit to use the configured default.
    """
    _require_kicad_path(pcb_path, "board")
    # Resolve to absolute path for subprocess calls
    pcb_path = str(Path(pcb_path).resolve())

    # Pre-flight: the JAR first, because it is what says which Java it needs.
    # The reverse order asserted a constant 17 while _download_jar fetches
    # whatever release is current, and the current one needs 25.
    jar_path, jar_err = _ensure_jar()
    if jar_err or not jar_path:
        raise ToolError(
            jar_err
            or "Freerouting JAR not found. Set FREEROUTING_JAR to a local"
            " freerouting.jar, or allow the automatic download."
        )

    # Pre-flight: a Java new enough for that JAR specifically. Looked up once,
    # here, and the same path goes to the router, so the java that was checked
    # is the java that runs.
    java = _find_on_path("java")
    java_err = _check_java(jar_path, java=java)
    if java_err or not java:
        raise ToolError(java_err or "Java runtime not found.")

    # Count existing traces/vias for before/after comparison
    traces_before, vias_before, board_version = _trace_counts(pcb_path)

    # Pre-flight: the pcbnew era has to match the board's. The DSN export and
    # the SES import both ride pcbnew, and _NUMERIC_NET_VERSION_MAX is the
    # highest KiCad 9 board format, so anything above it needs a pcbnew 10.
    warnings: list[str] = []
    needs10 = board_version > _NUMERIC_NET_VERSION_MAX
    major = _pcbnew_major()
    if major is not None and needs10 and major < 10:
        raise ToolError(
            f"This board is in the KiCad 10 format (version {board_version}), which "
            f"pcbnew {major} cannot load. Install KiCad 10, or point KICAD_PYTHON at "
            "the Python of a KiCad 10 install."
        )
    if major is not None and not needs10 and major >= 10:
        # pcbnew writes the routed copy through SaveBoard, which always saves
        # in the running pcbnew's format.
        warnings.append(
            f"This board is in a KiCad 9 era format (version {board_version}) but "
            f"pcbnew {major} is doing the routing, so the routed copy is written in "
            "the KiCad 10 format and may not open in KiCad 9. The original board is "
            "not touched."
        )

    out_dir = output_dir or str(Path(pcb_path).parent)
    stem = Path(pcb_path).stem
    routed_path = str(Path(out_dir) / f"{stem}_routed.kicad_pcb")

    with tempfile.TemporaryDirectory() as tmp_dir:
        dsn_path = str(Path(tmp_dir) / f"{stem}.dsn")
        ses_path = str(Path(tmp_dir) / f"{stem}.ses")

        # Step 1: Promote footprint-level keepout zones to board-level
        temp_pcb_path = str(Path(tmp_dir) / f"{stem}_keepouts.kicad_pcb")
        keepouts_promoted = _promote_footprint_keepouts(pcb_path, temp_pcb_path)
        dsn_source = temp_pcb_path if keepouts_promoted > 0 else pcb_path

        # Step 2: Export DSN
        dsn_err = _export_dsn(dsn_source, dsn_path)
        if dsn_err:
            raise ToolError(dsn_err)

        # Step 3: Run Freerouting
        route_err = _run_freerouting(
            jar_path=jar_path,
            dsn_path=dsn_path,
            ses_path=ses_path,
            max_passes=max_passes,
            num_threads=num_threads,
            timeout=timeout,
            java=java,
        )
        if route_err:
            raise ToolError(route_err)

        if not Path(ses_path).exists():
            raise ToolError("Freerouting did not produce a session file.")

        # Step 4: Import SES into new PCB
        ses_err = _import_ses(pcb_path, ses_path, routed_path)
        if ses_err:
            raise ToolError(ses_err)

    # Step 5: Fix displaced footprint text fields
    text_fields_fixed = _fix_displaced_fp_text(routed_path)

    # Count traces/vias in routed board
    traces_after, vias_after, _ = _trace_counts(routed_path)

    drc_violations: int | None = None
    drc_unconnected: int | None = None

    # Optional DRC
    try:
        drc_out = str(Path(out_dir) / f"{stem}_routed-drc.json")
        _run_cli(
            ["pcb", "drc", "--format", "json", "--severity-all", "--output", drc_out, routed_path],
            check=False,
        )
        with open(drc_out, encoding="utf-8") as f:
            drc = json.load(f)
        drc_violations = len(drc.get("violations", []))
        drc_unconnected = len(drc.get("unconnected_items", []))
    except Exception:
        pass  # DRC is optional — kicad-cli may not be available

    return AutorouteResult(
        routed_path=str(Path(routed_path).resolve()),
        traces_added=traces_after - traces_before,
        vias_added=vias_after - vias_before,
        text_fields_fixed=text_fields_fixed,
        drc_violations=drc_violations,
        drc_unconnected=drc_unconnected,
        keepouts_promoted=keepouts_promoted,
        warnings=warnings,
    )


def _courtyard_corner_on_board(
    fp_x: float, fp_y: float, angle: float, local_x: float, local_y: float
) -> tuple[float, float]:
    """A corner of a courtyard's footprint-local box, turned onto the board in
    float millimetres with RotatePoint's signs.

    Not _transform_local_to_board, which reads every coordinate as KiCad's
    integer nanometres: these corners are not points KiCad holds (a circle's
    extremes, the chords of an arc are off its grid), and the box is reported to
    1e-4 mm, so rounding them to the nanometre first could only push a value
    across that rounding.
    """
    theta = math.radians(angle or 0)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return (
        fp_x + (local_x * cos_t + local_y * sin_t),
        fp_y + (-local_x * sin_t + local_y * cos_t),
    )


@mcp.tool(annotations=_READ_ONLY, title="Outline bounds of a footprint placed on the board")
def get_footprint_bounds(reference: str, pcb_path: str = PCB_PATH) -> FootprintBoundsResult:
    """Get the board-coordinate bounding box of a placed footprint.

    Args:
        reference: Footprint reference designator
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    fp = _find_fp_cst(root, reference)
    at = fp.find("at")
    fp_x, fp_y = _xy(at)
    # A K10 footprint writes (at x y) with no angle atom.
    angle = float(at.atoms[3].text) if len(at.atoms) > 3 else 0
    layer = _fp_layer(fp)

    bbox = _courtyard_bbox_cst(fp)
    courtyard = None
    if bbox is not None:
        # Transform all 4 local corners to board coordinates
        local_corners = [
            (bbox["min_x"], bbox["min_y"]),
            (bbox["max_x"], bbox["min_y"]),
            (bbox["max_x"], bbox["max_y"]),
            (bbox["min_x"], bbox["max_y"]),
        ]
        board_corners = [
            _courtyard_corner_on_board(fp_x, fp_y, angle, lx, ly) for lx, ly in local_corners
        ]
        # Recompute axis-aligned bounding box from transformed corners
        xs = [c[0] for c in board_corners]
        ys = [c[1] for c in board_corners]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        courtyard = {
            "min_x": round(min_x, 4),
            "min_y": round(min_y, 4),
            "max_x": round(max_x, 4),
            "max_y": round(max_y, 4),
            "width": round(max_x - min_x, 4),
            "height": round(max_y - min_y, 4),
        }

    return FootprintBoundsResult(
        reference=reference,
        position={"x": fp_x, "y": fp_y},
        rotation=angle,
        courtyard=courtyard,
        layer=layer,
    )


@mcp.tool(annotations=_READ_ONLY, title="Check every footprint already on the board")
def validate_board(pcb_path: str = PCB_PATH) -> BoardValidationResult:
    """Validate all footprint placements against keep-out zones and board edge.

    Each footprint's origin is checked as check_placement checks it: keep-outs read
    as KiCad reads them, footprint keep-outs in board coordinates and layer sets
    such as *.Cu included, and a footprint never flagged by its own keep-outs, which
    KiCad's DRC never tests it against (pcbnew/pcbexpr_functions.cpp:697 at 9.0.8).

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    edge_poly = _edge_polygon_cst(root)
    violations: list[dict] = []
    footprints = root.find_all("footprint")

    # ponytail: the keepout scan is rebuilt per footprint (O(footprints x zones),
    # as the kiutils version was); hoist the candidate list if a board with
    # hundreds of embedded keepouts ever makes this the slow part.
    for fp in footprints:
        fp_x, fp_y = _xy(fp.find("at"))
        fp_layer = _fp_layer(fp)

        fp_violations: list[str] = []
        if _keepout_violations_cst(root, fp_x, fp_y, _fp_side(fp), own=fp):
            fp_violations.append("keepout_zone")
        if edge_poly is not None and not _point_in_polygon(fp_x, fp_y, edge_poly):
            fp_violations.append("outside_board_edge")

        if fp_violations:
            violations.append(
                {
                    "reference": _fp_prop_cst(fp, "Reference"),
                    "position": {"x": fp_x, "y": fp_y},
                    "layer": fp_layer,
                    "issues": fp_violations,
                }
            )

    return BoardValidationResult(
        total_footprints=len(footprints),
        violations=violations,
        board_edge_checked=edge_poly is not None,
        status=f"{len(violations)} violations found" if violations else "ok",
    )


def main():
    """Entry point for mcp-server-kicad-pcb console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
