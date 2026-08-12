"""KiCad PCB MCP Server — PCB manipulation, DRC, and export tools."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

import mcp_server_kicad._cst as _cst
from mcp_server_kicad._cst import _fill_at, _num, _numish
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
    _gen_uuid,
    _keepout_dict,
    _kicad_root,
    _linearize_arc,
    _point_in_polygon,
    _read_kicad_bytes,
    _require_kicad_path,
    _resolve_root,
    _run_cli,
    _transform_local_to_board,
    _xy,
    build_server,
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
            return t.atoms[2].text
    return "?"


def _fp_layer(fp) -> str:
    """A footprint node's layer, defaulting to the front copper layer."""
    layer = fp.find("layer")
    return layer.atoms[1].text if layer is not None else "F.Cu"


def _net_table(root) -> list[tuple[int, str]]:
    """(number, name) rows for the board's nets, both dialects.

    KiCad 9 format boards declare (net N "NAME") rows at the root. KiCad 10
    dropped the table and the numbers entirely: nets exist only as name
    references on pads, segments, vias and zones (measured on the K10
    runner, slice 13 probe), so numbers are synthesized from document order
    for the tool surface. The same derivation feeds reads and writers, so
    a number handed out by list_pcb_nets resolves back to its name.
    """
    rows = [c for c in root.find_all("net") if len(c.atoms) > 1]
    if rows:
        return [(int(n.atoms[1].text), n.atoms[2].text if len(n.atoms) > 2 else "") for n in rows]
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if name and not name.lstrip("-").isdigit() and name not in seen:
            seen.add(name)
            names.append(name)

    for item in root.lists:
        if item.head == "footprint":
            for pad in item.find_all("pad"):
                net = pad.find("net")
                if net is not None and len(net.atoms) > 1:
                    _add(net.atoms[1].text)
        elif item.head in ("segment", "arc", "via", "zone"):
            net = item.find("net_name") or item.find("net")
            if net is not None and len(net.atoms) > 1:
                _add(net.atoms[1].text)
    return [(i + 1, n) for i, n in enumerate(names)]


def _item_net_number(net_node, name_to_num: dict[str, int]) -> int:
    """Net number from a segment/via (net ...) child, either dialect."""
    if net_node is None or len(net_node.atoms) < 2:
        return 0
    t = net_node.atoms[1].text
    if t.lstrip("-").isdigit():
        return int(t)
    return name_to_num.get(t, 0)


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


# Highest board format that carries a net table, so the highest one whose net
# references may be numeric. Above it KiCad derives nets from usage and rebinds
# a number by load order, silently landing it on the wrong net (ADR-2
# guardrail 5, measured in the slice-13 probe).
_NUMERIC_NET_VERSION_MAX = 20241229


def _board_version(root) -> int:
    v = root.find("version")
    return int(v.atoms[1].text) if v is not None else 0


def _splice_pcb_node(root, node) -> None:
    """Insert *node* after the last trace item, else before the board tail."""
    _splice_after(root, node, ("segment", "arc", "via"), ("zone", "group", "embedded_fonts"))


def _set_item_net(node, root, net: int) -> None:
    """Fill the (net ...) child per ADR-2 guardrail 5: numeric for KiCad 9
    format boards, name-based (quoted) for newer, never the wrong dialect."""
    net_node = node.find("net")
    if _board_version(root) <= _NUMERIC_NET_VERSION_MAX:
        net_node.atoms[1].set_text(str(net))
        return
    for num, name in _net_table(root):
        if num == net:
            named = _cst.parse(b'(net "x")').lists[0]
            named.atoms[1].set_text(name)
            named.sep = net_node.sep
            node.children[node.children.index(net_node)] = named
            return
    raise ToolError(
        f"Net {net} not found in this KiCad 10 format board. Numeric net "
        "references are silently rebound by load order there, so the tool "
        "refuses rather than emit one (ADR-2 guardrail 5)."
    )


def _find_fp_cst(root, reference):
    """The footprint node with *reference*, or raise ToolError."""
    for fp in root.find_all("footprint"):
        if _fp_prop_cst(fp, "Reference") == reference:
            return fp
    raise ToolError(
        f"Footprint {reference!r} not found. Use list_pcb_footprints to see what is on the board."
    )


def _pad_net_name(net_node, default: str) -> str:
    """Display name from a pad's (net ...) child, either dialect."""
    if net_node is None or len(net_node.atoms) < 2:
        return default
    if len(net_node.atoms) > 2:
        return net_node.atoms[2].text
    t = net_node.atoms[1].text
    return t if not t.lstrip("-").isdigit() else default


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


def _zone_forbids_footprints(zone, x: float, y: float, layer: str, pts) -> bool:
    ko = zone.find("keepout")
    if ko is None:
        return False
    rule = ko.find("footprints")
    if rule is None or rule.atoms[1].text != "not_allowed":
        return False
    layers_node = zone.find("layers") or zone.find("layer")
    if layers_node is None or layer not in [a.text for a in layers_node.atoms[1:]]:
        return False
    return bool(pts) and _point_in_polygon(x, y, pts)


def _zone_pts(zone):
    poly = zone.find("polygon")
    if poly is None:
        return []
    return [(round(x, 3), round(y, 3)) for x, y in map(_xy, poly.find("pts").find_all("xy"))]


def _keepout_violations_cst(root, x: float, y: float, layer: str) -> list[dict]:
    """CST twin of _check_footprint_keepout_violations.

    Board-level zones first, then footprint-embedded ones with their
    polygons transformed into board coordinates.
    """
    candidates = [("board", z, _zone_pts(z)) for z in root.find_all("zone")]
    for fp in root.find_all("footprint"):
        at = fp.find("at")
        if at is None:
            continue
        fx, fy = _xy(at)
        angle = float(at.atoms[3].text) if len(at.atoms) > 3 else 0
        mirrored = _fp_layer(fp) == "B.Cu"
        source = f"footprint:{_fp_prop_cst(fp, 'Reference')}"
        for zone in fp.find_all("zone"):
            pts = []
            for px, py in _zone_pts(zone):
                bx, by = _transform_local_to_board(fx, fy, angle, px, py, mirrored=mirrored)
                pts.append((round(bx, 3), round(by, 3)))
            candidates.append((source, zone, pts))

    violations: list[dict] = []
    for source, zone, pts in candidates:
        if not _zone_forbids_footprints(zone, x, y, layer, pts):
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


_FOOTPRINT_TPL = _cst.parse(
    b'(footprint ""\n\t\t(layer "F.Cu")\n\t\t(uuid "x")\n\t\t(at 0 0 0)'
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
_COPPER_ZONE_TPL = _cst.parse(
    b'(zone\n\t\t(net 0)\n\t\t(net_name "x")\n\t\t(layer "F.Cu")\n\t\t(uuid "x")'
    b"\n\t\t(hatch edge 0.5)\n\t\t(priority 0)"
    b"\n\t\t(connect_pads\n\t\t\t(clearance 0.5)\n\t\t)"
    b"\n\t\t(min_thickness 0.25)\n\t\t(filled_areas_thickness no)"
    b"\n\t\t(fill\n\t\t\t(thermal_gap 0.5)\n\t\t\t(thermal_bridge_width 0.5)\n\t\t)"
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
    first.atoms[1].set_text(_num(corners[0]["x"]))
    first.atoms[2].set_text(_num(corners[0]["y"]))
    anchor = first
    for c in corners[1:]:
        xy = first.copy()
        xy.atoms[1].set_text(_num(c["x"]))
        xy.atoms[2].set_text(_num(c["y"]))
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
        net_name = z.find("net_name")
        if net_name is not None:
            zone_net = net_name.atoms[1].text
        else:
            # K10 zones carry name-only (net "NAME") and no net_name child.
            znet = z.find("net")
            t = znet.atoms[1].text if znet is not None and len(znet.atoms) > 1 else ""
            zone_net = t if t and not t.lstrip("-").isdigit() else ""
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
    """List all layers defined in the PCB stackup.

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
        f"Nets: {len(_net_table(root))}\n"
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
    lines = [f"{reference} pads:"]
    for pad in fp.find_all("pad"):
        net_name = _pad_net_name(pad.find("net"), "none")
        at, size = pad.find("at"), pad.find("size")
        layers = pad.find("layers")
        lines.append(
            f"  Pad {pad.atoms[1].text}: {pad.atoms[2].text} {pad.atoms[3].text} "
            f"@ ({_numish(at.atoms[1].text)}, {_numish(at.atoms[2].text)}) "
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
    pcb_path: str = PCB_PATH,
) -> str:
    """Place a footprint on the PCB.

    Args:
        reference: Reference designator (e.g. "R2")
        value: Component value (e.g. "4.7K")
        x: X position in mm
        y: Y position in mm
        rotation: Rotation in degrees
        layer: Layer (F.Cu or B.Cu)
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    tree, root, key = _open_pcb_cst(pcb_path)
    # Validate before the cache pop, so a refusal leaves the cached tree valid.
    _resolve_layer_cst(root, layer, copper_only=True)
    _BOARD_CACHE.pop(key, None)
    node = _FOOTPRINT_TPL.copy()
    node.find("layer").atoms[1].set_text(layer)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    _fill_at(node, x, y, rotation)
    for prop in node.find_all("property"):
        prop.atoms[2].set_text(reference if prop.atoms[1].text == "Reference" else value)
        prop.find("uuid").atoms[1].set_text(_gen_uuid())
    _splice_after(root, node, ("footprint",), _PCB_TAIL_HEADS)
    _atomic_write(key, _cst.serialize(tree))
    return f"Placed {reference} ({value}) at ({x}, {y}) on {layer}"


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

    Args:
        reference: Reference designator (e.g. "R1")
        x: New X position
        y: New Y position
        rotation: New rotation (None = keep current)
        layer: New layer (empty = keep current)
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    tree, root, key = _open_pcb_cst(pcb_path)
    if layer:
        _resolve_layer_cst(root, layer, copper_only=True)
    _BOARD_CACHE.pop(key, None)
    fp = _find_fp_cst(root, reference)
    _fill_at(fp, x, y, rotation)
    if layer:
        fp.find("layer").atoms[1].set_text(layer)
    # Advisory only: moving into a keep-out or off the board edge is legal
    # KiCad, just usually a mistake, so this warns and never refuses. It runs
    # before the write purely so a fault in the checks cannot leave a moved
    # footprint on disk with nothing said about it; the checks read the already
    # mutated tree, so the answers are the same either side of the write.
    warnings: list[str] = []
    try:
        if _keepout_violations_cst(root, x, y, _fp_layer(fp)):
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
    answer. One was accepted and silently ignored until 2026-08-12.

    Args:
        reference: Footprint reference designator
        x: Proposed X position
        y: Proposed Y position
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _, root, _ = _open_pcb_cst(pcb_path)
    fp = _find_fp_cst(root, reference)

    keepout_violations = _keepout_violations_cst(root, x, y, _fp_layer(fp))
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
        net: Net number
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    tree, root, key = _open_pcb_cst(pcb_path)
    _resolve_layer_cst(root, layer, copper_only=True)
    _BOARD_CACHE.pop(key, None)
    node = _SEGMENT_TPL.copy()
    start, end = node.find("start"), node.find("end")
    start.atoms[1].set_text(_num(x1))
    start.atoms[2].set_text(_num(y1))
    end.atoms[1].set_text(_num(x2))
    end.atoms[2].set_text(_num(y2))
    node.find("width").atoms[1].set_text(_num(width))
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
        net: Net number
        layers: Via layers (default: ["F.Cu", "B.Cu"])
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    tree, root, key = _open_pcb_cst(pcb_path)
    via_layers = layers or ["F.Cu", "B.Cu"]
    for name in via_layers:
        _resolve_layer_cst(root, name, copper_only=True)
    _BOARD_CACHE.pop(key, None)
    node = _VIA_TPL.copy()
    at = node.find("at")
    at.atoms[1].set_text(_num(x))
    at.atoms[2].set_text(_num(y))
    node.find("size").atoms[1].set_text(_num(size))
    node.find("drill").atoms[1].set_text(_num(drill))
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
    tree, root, key = _open_pcb_cst(pcb_path)
    _resolve_layer_cst(root, layer)
    _BOARD_CACHE.pop(key, None)
    node = _GR_TEXT_TPL.copy()
    node.atoms[1].set_text(text)
    _fill_at(node, x, y, rotation)
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
    tree, root, key = _open_pcb_cst(pcb_path)
    _resolve_layer_cst(root, layer)
    _BOARD_CACHE.pop(key, None)
    node = _GR_LINE_TPL.copy()
    start, end = node.find("start"), node.find("end")
    start.atoms[1].set_text(_num(x1))
    start.atoms[2].set_text(_num(y1))
    end.atoms[1].set_text(_num(x2))
    end.atoms[2].set_text(_num(y2))
    node.find("stroke").find("width").atoms[1].set_text(_num(width))
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
    """Create an unfilled copper zone. Call fill_zones afterward to compute fills.

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
    tree, root, key = _open_pcb_cst(pcb_path)
    _resolve_layer_cst(root, layer, copper_only=True)
    _BOARD_CACHE.pop(key, None)
    net_num = _resolve_net_cst(root, net_name)
    node = _COPPER_ZONE_TPL.copy()
    node.find("layer").atoms[1].set_text(layer)
    node.find("uuid").atoms[1].set_text(_gen_uuid())
    node.find("priority").atoms[1].set_text(str(priority))
    cp = node.find("connect_pads")
    cp.find("clearance").atoms[1].set_text(_num(clearance))
    if not thermal_relief:
        solid = cp.children[0].copy()
        solid.sep = b" "
        solid.set_text("yes")
        cp.children.insert(1, solid)
    node.find("min_thickness").atoms[1].set_text(_num(min_thickness))
    fill = node.find("fill")
    fill.find("thermal_gap").atoms[1].set_text(_num(thermal_gap))
    fill.find("thermal_bridge_width").atoms[1].set_text(_num(thermal_bridge_width))
    _fill_zone_polygon(node, corners)
    if _board_version(root) <= _NUMERIC_NET_VERSION_MAX:
        node.find("net").atoms[1].set_text(str(net_num))
        node.find("net_name").atoms[1].set_text(net_name)
    else:
        _set_item_net(node, root, net_num)
        node.remove_child(node.find("net_name"))
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
    if _board_version(root) > _NUMERIC_NET_VERSION_MAX:
        # Measured K10 rule areas carry no net tokens at all.
        node.remove_child(node.find("net"))
        node.remove_child(node.find("net_name"))
    _splice_pcb_zone(root, node)
    _atomic_write(key, _cst.serialize(tree))
    return KeepoutZoneResult(
        corners=len(corners),
        layers=zone_layers,
        restrictions=restrictions,
    )


@mcp.tool(annotations=_ADDITIVE)
def fill_zones(pcb_path: str = PCB_PATH) -> FillZonesResult:
    """Fill all copper zones on the board using pcbnew's zone filler.

    Requires KiCad's pcbnew Python bindings to be installed.

    Args:
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
    _require_kicad_path(pcb_path, "board")
    pcb_path = str(Path(pcb_path).resolve())
    python, env = _find_pcbnew_python()
    if not python:
        raise ToolError("pcbnew Python bindings not found. Ensure KiCad is installed.")
    script = (
        "import pcbnew; "
        f"b = pcbnew.LoadBoard({pcb_path!r}); "
        "filler = pcbnew.ZONE_FILLER(b); "
        "zones = b.Zones(); "
        "filler.Fill(zones); "
        f"pcbnew.SaveBoard({pcb_path!r}, b); "
        "print(len(zones))"
    )
    result = subprocess.run(
        [python, "-c", script], capture_output=True, text=True, timeout=120, env=env
    )
    if result.returncode != 0:
        raise ToolError(f"Zone fill failed: {result.stderr.strip()}")
    try:
        zone_count = int(result.stdout.strip())
    except ValueError:
        zone_count = 0
    return FillZonesResult(zones_filled=zone_count, status="ok")


def _netlist_lib_dirs(schematic_path: str, pcb_path: str) -> list[str]:
    """Footprint library search dirs: project .pretty dirs, KICAD_FP_LIB, stock libs."""
    dirs: list[str] = []
    parents = dict.fromkeys(str(Path(p).resolve().parent) for p in (schematic_path, pcb_path))
    for parent in parents:
        for pretty in sorted(Path(parent).glob("*.pretty")):
            if pretty.is_dir():
                dirs.append(str(pretty))
    if FP_LIB_PATH and Path(FP_LIB_PATH).is_dir():
        dirs.append(FP_LIB_PATH)
    root = _kicad_root()
    if root:
        for sub in ("share/kicad/footprints", "SharedSupport/footprints"):
            cand = root / sub
            if cand.is_dir():
                dirs.append(str(cand))
    return dirs


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
    it does not exist; new footprints land in a grid cluster. Existing
    footprints are matched by reference and keep their position; a
    changed footprint assignment swaps the footprint in place. Stale
    board footprints are reported, and removed only with delete_stale
    (locked ones are never removed). Zones are NOT refilled — run
    fill_zones afterward. Net names arrive exactly as KiCad's F8
    produces them (local labels sheet-prefixed, e.g. "/SIG"); read them
    with list_pcb_nets.

    Requires kicad-cli and KiCad's pcbnew Python bindings.

    Args:
        schematic_path: Path to .kicad_sch file. Optional; omit to use the configured default.
        pcb_path: Path to .kicad_pcb file (created if missing).
            Optional; omit to use the configured default.
        delete_stale: Remove unlocked board footprints absent from the schematic
        project_path: Path to .kicad_pro for explicit root resolution (sub-sheets)
    """
    # Before the pcbnew probe below, which spawns a process, so a typo'd path
    # is answered by us rather than by whatever that subprocess says about it.
    #
    # The schematic only. pcb_path is deliberately not required to exist: this
    # tool creates the board when it is missing (_netlist_import calls NewBoard,
    # and the whole E2E suite starts from a schematic and no board at all), so
    # requiring it would refuse the tool's documented first use.
    _require_kicad_path(schematic_path, "schematic")
    if not pcb_path:
        raise ToolError("No PCB path provided. Pass pcb_path parameter.")

    python, env = _find_pcbnew_python()
    if not python:
        raise ToolError("pcbnew Python bindings not found. Ensure KiCad is installed.")

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

        script = str(Path(__file__).with_name("_netlist_import.py"))
        cmd = [python, script, netlist_path, pcb_file]
        for lib_dir in _netlist_lib_dirs(schematic_path, pcb_file):
            cmd += ["--lib-dir", lib_dir]
        if delete_stale:
            cmd.append("--delete-stale")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise ToolError(f"Netlist import failed: {detail}")
    # The summary is the last stdout line; earlier lines may be pcbnew chatter.
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    try:
        summary = json.loads(lines[-1])
    except (IndexError, ValueError) as exc:
        raise ToolError(f"Netlist import produced no summary: {proc.stdout!r}") from exc
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
    tree, root, key = _open_pcb_cst(pcb_path)
    _BOARD_CACHE.pop(key, None)
    segments = _filter_segments_cst(root, net_name, layer, x_min, y_min, x_max, y_max)
    for seg in segments:
        seg.find("width").atoms[1].set_text(_num(width))
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
        rows: Number of rows in the via grid
        cols: Number of columns in the via grid
        spacing: Spacing between vias in mm
        via_size: Via annular ring diameter in mm
        via_drill: Via drill diameter in mm
        net_name: Net to assign to vias. If None, auto-detect from pad.
        pcb_path: Path to .kicad_pcb file. Optional; omit to use the configured default.
    """
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
        mirrored=_fp_layer(fp) == "B.Cu",
    )

    # Determine net. A netless pad on a KiCad 10 format board resolves to
    # net 0, which _set_item_net refuses (guardrail 5, same as add_via).
    name_to_num = {name: num for num, name in _net_table(root)}
    if net_name:
        via_net = _resolve_net_cst(root, net_name)
    else:
        via_net = _item_net_number(pad.find("net"), name_to_num)

    # Generate grid centered on pad
    vias_added = 0
    for r in range(rows):
        for c in range(cols):
            vx = pad_x + (c - (cols - 1) / 2) * spacing
            vy = pad_y + (r - (rows - 1) / 2) * spacing
            node = _VIA_TPL.copy()
            via_at = node.find("at")
            via_at.atoms[1].set_text(_num(round(vx, 4)))
            via_at.atoms[2].set_text(_num(round(vy, 4)))
            node.find("size").atoms[1].set_text(_num(via_size))
            node.find("drill").atoms[1].set_text(_num(via_drill))
            _set_item_net(node, root, via_net)
            node.find("uuid").atoms[1].set_text(_gen_uuid())
            _splice_pcb_node(root, node)
            vias_added += 1

    _atomic_write(key, _cst.serialize(tree))
    return ThermalViasResult(
        vias_added=vias_added,
        reference=reference,
        pad=pad.atoms[1].text,
        net=net_name or _pad_net_name(pad.find("net"), ""),
        center={"x": round(pad_x, 4), "y": round(pad_y, 4)},
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

    # Read existing project JSON
    try:
        pro_data = json.loads(pro_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
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

    # Write back
    try:
        _atomic_write(pro_file, (json.dumps(pro_data, indent=2) + "\n").encode())
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
            mirrored = _fp_layer(fp) == "B.Cu"
            for pad in fp.find_all("pad"):
                pad_at = pad.find("at")
                px, py = _transform_local_to_board(
                    fp_x,
                    fp_y,
                    fp_angle,
                    float(pad_at.atoms[1].text),
                    float(pad_at.atoms[2].text),
                    mirrored=mirrored,
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
# CLI analysis tools (1)
# ---------------------------------------------------------------------------


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
    _run_cli(
        ["pcb", "drc", "--format", "json", "--severity-all", "--output", out_path, pcb_path],
        check=False,
    )
    try:
        with open(out_path) as f:
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
        args = ["pcb", "export", "dxf", pcb_path, "-o", out_path, "-l", ",".join(layers)]
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
            path=meta["path"], size_bytes=meta["size_bytes"], format="dxf", layers=layers
        )

    # PDF / SVG path
    out_dir = output_dir or str(Path(pcb_path).parent)
    ext = ".pdf" if fmt == "pdf" else ".svg"
    out_path = str(Path(out_dir) / (Path(pcb_path).stem + ext))
    if fmt == "pdf":
        layer_list = layers or ["F.Cu", "B.Cu"]
    else:
        layer_list = layers or ["F.Cu"]
    _run_cli(
        [
            "pcb",
            "export",
            fmt,
            "--layers",
            ",".join(layer_list),
            "--output",
            out_path,
            pcb_path,
        ]
    )
    meta = _file_meta(out_path)
    return PcbExportResult(
        path=meta["path"], size_bytes=meta["size_bytes"], format=fmt, layers=layer_list
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
        with tempfile.TemporaryDirectory(dir=out_dir) as tmp_dir:
            result = _run_cli(
                [
                    "pcb",
                    "export",
                    "gerbers",
                    "--layers",
                    layer,
                    "--no-protel-ext",
                    "--output",
                    tmp_dir,
                    pcb_path,
                ]
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
        )

    # Multi-layer mode: directory of files
    out = output_dir or str(Path(pcb_path).parent / "gerbers")
    _ensure_dir(out)
    cmd = ["pcb", "export", "gerbers"]
    if layers:
        cmd += ["--layers", ",".join(layers)]
    cmd += ["--output", out, pcb_path]
    _run_cli(cmd)
    files = sorted(Path(out).glob("*"))
    drill_file_names: list[str] = []
    if include_drill:
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
    with open(out_path) as f:
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
    return ExportResult(path=meta["path"], size_bytes=meta["size_bytes"], format="ipc2581")


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
_ZONE_NET_TPLS = _cst.parse(b'(net 0)\n(net_name "")').lists
_UUID_TPL = _cst.parse(b'(uuid "x")').lists[0]


def _replace_child(node, new) -> None:
    """Swap *node*'s child named like *new* for *new*, splicing it in if absent."""
    old = node.find(new.head)
    if old is None:
        node.insert_after(node.atoms[0], new, sep=b" ")
        return
    new.sep = old.sep
    node.children[node.children.index(old)] = new


def _set_promoted_zone_net(zone, root) -> None:
    """Net tokens on a promoted keepout, per the measured board dialects.

    Numeric (net 0) with an empty net_name at or below the KiCad 9 format;
    no net tokens at all above it, which is the KiCad 10 rule-area shape
    add_keepout_zone emits (ADR-2 guardrail 5).
    """
    for head in ("net", "net_name"):
        stale = zone.find(head)
        if stale is not None:
            zone.remove_child(stale)
    if _board_version(root) > _NUMERIC_NET_VERSION_MAX:
        return
    anchor = zone.atoms[0]
    for tpl in _ZONE_NET_TPLS:
        node = tpl.copy()
        zone.insert_after(anchor, node, sep=b" ")
        anchor = node


def _promote_footprint_keepouts(pcb_path: str, output_path: str) -> int:
    """Promote footprint-level keepout zones to board level in a copy.

    pcbnew's ExportSpecctraDSN does not export keepout zones defined inside
    a footprint, so the autorouter would never see them. This parses
    *pcb_path*, appends one board-level zone per footprint keepout polygon
    with its points transformed into board coordinates, and writes the
    result to *output_path*. The source board is never modified.

    Returns the number of polygons promoted. At zero, *output_path* is not
    written and the caller feeds the original board to the DSN export.
    """
    tree = _cst.parse(_read_kicad_bytes(pcb_path, "board"))
    root = tree.lists[0]
    count = 0

    for fp in root.find_all("footprint"):
        at = fp.find("at")
        if at is None:
            continue
        fp_x, fp_y = _xy(at)
        fp_angle = float(at.atoms[3].text) if len(at.atoms) > 3 else 0
        mirrored = _fp_layer(fp) == "B.Cu"

        for source_zone in fp.find_all("zone"):
            if source_zone.find("keepout") is None:
                continue
            for index in range(len(source_zone.find_all("polygon"))):
                # One board zone per polygon, as the kiutils twin produced.
                zone = source_zone.copy()
                polygons = zone.find_all("polygon")
                for other in polygons[:index] + polygons[index + 1 :]:
                    zone.remove_child(other)
                for xy in polygons[index].find("pts").find_all("xy"):
                    bx, by = _transform_local_to_board(
                        fp_x, fp_y, fp_angle, *_xy(xy), mirrored=mirrored
                    )
                    xy.atoms[1].set_text(_num(round(bx, 6)))
                    xy.atoms[2].set_text(_num(round(by, 6)))
                _replace_child(zone, _HATCH_TPL.copy())
                fresh_uuid = _UUID_TPL.copy()
                fresh_uuid.atoms[1].set_text(_gen_uuid())
                _replace_child(zone, fresh_uuid)
                _set_promoted_zone_net(zone, root)
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
            _fill_at(text, *_FP_TEXT_DEFAULT_OFFSETS.get(kind, (0, 0)))
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
    num_threads: int = 4,
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
        num_threads: Thread count for routing
        timeout: Max seconds to wait for routing (default: 600)
        output_dir: Directory for output files (default: same as PCB).
            Optional; omit to use the configured default.
    """
    _require_kicad_path(pcb_path, "board")
    # Resolve to absolute path for subprocess calls
    pcb_path = str(Path(pcb_path).resolve())

    # Pre-flight: check Java
    java_err = _check_java()
    if java_err:
        raise ToolError(java_err)

    # Pre-flight: ensure Freerouting JAR
    jar_path, jar_err = _ensure_jar()
    if jar_err or not jar_path:
        raise ToolError(
            jar_err
            or "Freerouting JAR not found. Set FREEROUTING_JAR to a local"
            " freerouting.jar, or allow the automatic download."
        )

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
        with open(drc_out) as f:
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
            _transform_local_to_board(fp_x, fp_y, angle, lx, ly, mirrored=layer == "B.Cu")
            for lx, ly in local_corners
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
        if _keepout_violations_cst(root, fp_x, fp_y, fp_layer):
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
