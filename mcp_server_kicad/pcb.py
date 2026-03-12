"""KiCad PCB MCP Server — PCB manipulation, DRC, and export tools."""

import json
import math  # noqa: F401 – used by upcoming post-autoroute tools
import os
import subprocess  # noqa: F401 – used by upcoming post-autoroute tools
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

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
    find_pcbnew_python as _find_pcbnew_python,  # noqa: F401
)
from mcp_server_kicad._freerouting import (
    import_ses as _import_ses,
)
from mcp_server_kicad._freerouting import (
    run_freerouting as _run_freerouting,
)
from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    OUTPUT_DIR,
    PCB_PATH,
    FillSettings,  # noqa: F401
    Footprint,
    FpText,
    GrLine,
    GrText,
    Hatch,  # noqa: F401
    Position,
    Segment,
    Via,
    Zone,  # noqa: F401
    ZonePolygon,  # noqa: F401
    _default_effects,
    _file_meta,
    _fp_ref,
    _fp_val,
    _gen_uuid,
    _load_board,
    _run_cli,
)

mcp = FastMCP(
    "kicad-pcb",
    instructions=(
        "KiCad PCB manipulation, DRC analysis, and PCB export tools"
        " including Gerber, drill, 3D models, and pick-and-place.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write .kicad_pcb files directly. All PCB"
        " manipulation MUST go through these MCP tools.\n"
        "- NEVER run kicad-cli commands directly. Use the export and DRC"
        " tools provided by this server.\n"
        "- NEVER grep/search inside .kicad_pcb files. Use list_pcb_items"
        " to query board contents (footprints, traces, vias, zones, etc.).\n"
        "- When a tool returns an error, try different parameters or a different"
        " MCP tool. Do NOT fall back to manual file editing.\n\n"
        "QUERY PATTERN: list_pcb_items(item_type, pcb_path) supports types:"
        " footprints, traces, vias, zones, drawings, text.\n\n"
        "EXPORT PATTERN: export_pcb(format, pcb_path) supports formats:"
        " pdf, svg, dxf. Use export_gerbers for manufacturing output."
    ),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_net(board, net_name: str) -> tuple[int, str]:
    """Return (net_number, net_name) for a named net, or raise ValueError."""
    for n in board.nets:
        if n.name == net_name:
            return n.number, n.name
    available = [n.name for n in board.nets if n.name]
    raise ValueError(f"Net {net_name!r} not found. Available nets: {available}")


def _filter_segments(board, net_name, layer, x_min, y_min, x_max, y_max):
    """Filter board trace segments by net name, layer, and/or bounding box."""
    if all(v is None for v in (net_name, layer, x_min, y_min, x_max, y_max)):
        raise ValueError("at least one filter is required")
    net_num = None
    if net_name is not None:
        net_num, _ = _find_net(board, net_name)
    result = []
    for item in board.traceItems:
        if not isinstance(item, Segment):
            continue
        if net_num is not None and item.net != net_num:
            continue
        if layer is not None and item.layer != layer:
            continue
        if x_min is not None or y_min is not None or x_max is not None or y_max is not None:
            sx, sy = item.start.X, item.start.Y
            ex, ey = item.end.X, item.end.Y
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


# ---------------------------------------------------------------------------
# PCB read tools (8)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ_ONLY)
def list_pcb_items(item_type: str, pcb_path: str = PCB_PATH) -> str:
    """List PCB items by type.

    Args:
        item_type: One of "footprints", "traces", "nets", "zones", "layers", "graphic_items"
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    if item_type == "footprints":
        items = []
        for fp in board.footprints:
            ref = _fp_ref(fp)
            val = _fp_val(fp)
            pos = fp.position
            items.append(
                {
                    "reference": ref,
                    "value": val,
                    "lib_id": fp.libId,
                    "x": pos.X,
                    "y": pos.Y,
                    "rotation": pos.angle,
                    "layer": fp.layer,
                }
            )
        return json.dumps(items)
    elif item_type == "traces":
        items = []
        for item in board.traceItems:
            if isinstance(item, Segment):
                items.append(
                    {
                        "type": "segment",
                        "start_x": item.start.X,
                        "start_y": item.start.Y,
                        "end_x": item.end.X,
                        "end_y": item.end.Y,
                        "width": item.width,
                        "layer": item.layer,
                        "net": item.net,
                    }
                )
            elif isinstance(item, Via):
                items.append(
                    {
                        "type": "via",
                        "x": item.position.X,
                        "y": item.position.Y,
                        "size": item.size,
                        "drill": item.drill,
                        "layers": item.layers,
                        "net": item.net,
                    }
                )
        return json.dumps(items)
    elif item_type == "nets":
        items = []
        for net in board.nets:
            if net.name:  # skip unnamed net 0
                items.append({"number": net.number, "name": net.name})
        return json.dumps(items)
    elif item_type == "zones":
        items = []
        for z in board.zones:
            items.append({"net_name": z.netName, "layers": z.layers, "priority": z.priority})
        return json.dumps(items)
    elif item_type == "layers":
        items = []
        for layer in board.layers:
            items.append({"ordinal": layer.ordinal, "name": layer.name, "type": layer.type})
        return json.dumps(items)
    elif item_type == "graphic_items":
        items = []
        for item in board.graphicItems:
            if isinstance(item, GrLine):
                items.append(
                    {
                        "type": "line",
                        "start_x": item.start.X,
                        "start_y": item.start.Y,
                        "end_x": item.end.X,
                        "end_y": item.end.Y,
                        "layer": item.layer,
                    }
                )
            elif isinstance(item, GrText):
                items.append(
                    {
                        "type": "text",
                        "text": item.text,
                        "x": item.position.X,
                        "y": item.position.Y,
                        "layer": item.layer,
                    }
                )
            else:
                items.append(
                    {
                        "type": type(item).__name__,
                        "layer": getattr(item, "layer", "unknown"),
                    }
                )
        return json.dumps(items)
    else:
        return json.dumps(
            {
                "error": f"Unknown item_type: {item_type}."
                " Use: footprints, traces, nets, zones, layers, graphic_items"
            }
        )


@mcp.tool(annotations=_READ_ONLY)
def get_board_info(pcb_path: str = PCB_PATH) -> str:
    """Get board summary: footprint count, trace count, net count, thickness."""
    board = _load_board(pcb_path)
    seg_count = sum(1 for t in board.traceItems if isinstance(t, Segment))
    via_count = sum(1 for t in board.traceItems if isinstance(t, Via))
    return (
        f"Footprints: {len(board.footprints)}\n"
        f"Traces: {seg_count}\n"
        f"Vias: {via_count}\n"
        f"Nets: {len(board.nets)}\n"
        f"Zones: {len(board.zones)}\n"
        f"Thickness: {board.general.thickness}mm"
    )


@mcp.tool(annotations=_READ_ONLY)
def get_footprint_pads(reference: str, pcb_path: str = PCB_PATH) -> str:
    """Get pad info for a placed footprint on the PCB.

    Args:
        reference: Footprint reference (e.g. "R1", "U1")
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    for fp in board.footprints:
        ref = _fp_ref(fp)
        if ref == reference:
            lines = [f"{reference} pads:"]
            for pad in fp.pads:
                net_name = pad.net.name if pad.net else "none"
                lines.append(
                    f"  Pad {pad.number}: {pad.type} {pad.shape} "
                    f"@ ({pad.position.X}, {pad.position.Y}) "
                    f"size=({pad.size.X}, {pad.size.Y}) "
                    f"layers={pad.layers} net={net_name}"
                )
            return "\n".join(lines)
    return f"Footprint {reference} not found."


# ---------------------------------------------------------------------------
# PCB write tools (14)
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
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    fp = Footprint()
    fp.layer = layer
    fp.position = Position(X=x, Y=y, angle=rotation)
    fp.properties = {"Reference": reference, "Value": value}
    fp.graphicItems = [
        FpText(
            type="reference",
            text=reference,
            layer="F.SilkS",
            effects=_default_effects(),
            position=Position(X=0, Y=-2),
        ),
        FpText(
            type="value",
            text=value,
            layer="F.Fab",
            effects=_default_effects(),
            position=Position(X=0, Y=2),
        ),
    ]
    board.footprints.append(fp)
    board.to_file()
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
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    for fp in board.footprints:
        if _fp_ref(fp) == reference:
            fp.position.X = x
            fp.position.Y = y
            if rotation is not None:
                fp.position.angle = rotation
            if layer:
                fp.layer = layer
            board.to_file()
            return f"Moved {reference} to ({x}, {y})"
    return f"Footprint {reference} not found."


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_footprint(reference: str, pcb_path: str = PCB_PATH) -> str:
    """Remove a footprint by reference designator.

    Args:
        reference: Reference designator (e.g. "R1")
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    target = None
    for fp in board.footprints:
        if _fp_ref(fp) == reference:
            target = fp
            break
    if target is None:
        return f"Footprint {reference} not found."
    board.footprints.remove(target)
    board.to_file()
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
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    seg = Segment()
    seg.start = Position(X=x1, Y=y1)
    seg.end = Position(X=x2, Y=y2)
    seg.width = width
    seg.layer = layer
    seg.net = net
    seg.tstamp = _gen_uuid()
    board.traceItems.append(seg)
    board.to_file()
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
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    via = Via()
    via.position = Position(X=x, Y=y)
    via.size = size
    via.drill = drill
    via.net = net
    via.layers = layers or ["F.Cu", "B.Cu"]
    via.tstamp = _gen_uuid()
    board.traceItems.append(via)
    board.to_file()
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
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    gt = GrText()
    gt.text = text
    gt.position = Position(X=x, Y=y, angle=rotation)
    gt.layer = layer
    gt.effects = _default_effects()
    gt.tstamp = _gen_uuid()
    board.graphicItems.append(gt)
    board.to_file()
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
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    line = GrLine()
    line.start = Position(X=x1, Y=y1)
    line.end = Position(X=x2, Y=y2)
    line.layer = layer
    line.width = width
    line.tstamp = _gen_uuid()
    board.graphicItems.append(line)
    board.to_file()
    return f"Line: ({x1}, {y1}) -> ({x2}, {y2}) on {layer}"


@mcp.tool(annotations=_ADDITIVE)
def add_copper_zone(
    net_name: str,
    layer: str,
    corners: list[dict],
    clearance: float = 0.5,
    min_thickness: float = 0.25,
    thermal_relief: bool = True,
    thermal_gap: float = 0.5,
    thermal_bridge_width: float = 0.5,
    priority: int = 0,
    pcb_path: str = PCB_PATH,
) -> str:
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
        pcb_path: Path to .kicad_pcb file
    """
    if len(corners) < 3:
        return json.dumps({"error": "At least 3 corners required for a zone polygon."})
    board = _load_board(pcb_path)
    try:
        net_num, _ = _find_net(board, net_name)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    zone = Zone()
    zone.net = net_num
    zone.netName = net_name
    zone.layers = [layer]
    zone.priority = priority
    zone.clearance = clearance
    zone.minThickness = min_thickness
    zone.tstamp = _gen_uuid()
    zone.hatch = Hatch(style="edge", pitch=0.5)
    if not thermal_relief:
        zone.connectPads = "full"
    zone.fillSettings = FillSettings(
        thermalGap=thermal_gap, thermalBridgeWidth=thermal_bridge_width
    )
    poly = ZonePolygon()
    poly.coordinates = [Position(X=c["x"], Y=c["y"]) for c in corners]
    zone.polygons = [poly]
    board.zones.append(zone)
    board.to_file()
    return json.dumps(
        {"net": net_name, "layer": layer, "corners": len(corners), "clearance_mm": clearance}
    )


@mcp.tool(annotations=_ADDITIVE)
def fill_zones(pcb_path: str = PCB_PATH) -> str:
    """Fill all copper zones on the board using pcbnew's zone filler.

    Requires KiCad's pcbnew Python bindings to be installed.

    Args:
        pcb_path: Path to .kicad_pcb file
    """
    pcb_path = str(Path(pcb_path).resolve())
    python, env = _find_pcbnew_python()
    if not python:
        return json.dumps({"error": "pcbnew Python bindings not found. Ensure KiCad is installed."})
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
        return json.dumps({"error": f"Zone fill failed: {result.stderr.strip()}"})
    try:
        zone_count = int(result.stdout.strip())
    except ValueError:
        zone_count = 0
    return json.dumps({"zones_filled": zone_count, "status": "ok"})


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
) -> str:
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
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    try:
        segments = _filter_segments(board, net_name, layer, x_min, y_min, x_max, y_max)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    for seg in segments:
        seg.width = width
    board.to_file()
    return json.dumps({"traces_modified": len(segments), "net": net_name, "new_width_mm": width})


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_traces(
    net_name: str | None = None,
    layer: str | None = None,
    x_min: float | None = None,
    y_min: float | None = None,
    x_max: float | None = None,
    y_max: float | None = None,
    pcb_path: str = PCB_PATH,
) -> str:
    """Remove trace segments matching the given filters. Does not remove vias.
    At least one filter (net_name, layer, or bounding box) is required.

    Args:
        net_name: Filter by net name
        layer: Filter by layer name (e.g. "F.Cu", "B.Cu")
        x_min: Left edge of bounding box filter (mm)
        y_min: Top edge of bounding box filter (mm)
        x_max: Right edge of bounding box filter (mm)
        y_max: Bottom edge of bounding box filter (mm)
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    try:
        segments = _filter_segments(board, net_name, layer, x_min, y_min, x_max, y_max)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    for seg in segments:
        board.traceItems.remove(seg)
    board.to_file()
    return json.dumps({"traces_removed": len(segments), "net": net_name, "layer": layer})


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
) -> str:
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
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)

    # Find footprint
    fp = None
    for f in board.footprints:
        if _fp_ref(f) == reference:
            fp = f
            break
    if fp is None:
        return json.dumps({"error": f"Footprint {reference!r} not found."})

    # Find pad
    pad = None
    if pad_number:
        for p in fp.pads:
            if p.number == pad_number:
                pad = p
                break
        if pad is None:
            return json.dumps({"error": f"Pad {pad_number!r} not found on {reference}."})
    else:
        # Auto-detect: largest SMD pad by area
        best_area = 0
        for p in fp.pads:
            if p.type == "smd":
                area = (p.size.X or 0) * (p.size.Y or 0)
                if area > best_area:
                    best_area = area
                    pad = p
        if pad is None:
            return json.dumps(
                {"error": f"No SMD pad found on {reference}. Specify pad_number explicitly."}
            )

    # Compute pad center in board coordinates with rotation
    fp_x = fp.position.X
    fp_y = fp.position.Y
    theta = math.radians(fp.position.angle or 0)
    offset_x = pad.position.X
    offset_y = pad.position.Y
    pad_x = fp_x + (offset_x * math.cos(theta) - offset_y * math.sin(theta))
    pad_y = fp_y + (offset_x * math.sin(theta) + offset_y * math.cos(theta))

    # Determine net
    via_net = 0
    if net_name:
        try:
            via_net, _ = _find_net(board, net_name)
        except ValueError as e:
            return json.dumps({"error": str(e)})
    elif pad.net is not None:
        via_net = pad.net.number

    # Generate grid centered on pad
    vias_added = 0
    for r in range(rows):
        for c in range(cols):
            vx = pad_x + (c - (cols - 1) / 2) * spacing
            vy = pad_y + (r - (rows - 1) / 2) * spacing
            via = Via()
            via.position = Position(X=round(vx, 4), Y=round(vy, 4))
            via.size = via_size
            via.drill = via_drill
            via.net = via_net
            via.layers = ["F.Cu", "B.Cu"]
            via.tstamp = _gen_uuid()
            board.traceItems.append(via)
            vias_added += 1

    board.to_file()
    return json.dumps(
        {
            "vias_added": vias_added,
            "reference": reference,
            "pad": pad.number,
            "net": net_name or (pad.net.name if pad.net else ""),
            "center": {"x": round(pad_x, 4), "y": round(pad_y, 4)},
        }
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
) -> str:
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
        pcb_path: Path to .kicad_pcb file
    """
    pcb_file = Path(pcb_path).resolve()
    pro_file = pcb_file.with_suffix(".kicad_pro")

    if not pro_file.exists():
        return json.dumps(
            {
                "error": f"Project file not found: {pro_file}. "
                "A .kicad_pro file must exist alongside the .kicad_pcb file."
            }
        )

    # Read existing project JSON
    try:
        pro_data = json.loads(pro_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return json.dumps({"error": f"Failed to read project file: {exc}"})

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
        pro_file.write_text(json.dumps(pro_data, indent=2) + "\n")
    except OSError as exc:
        return json.dumps({"error": f"Failed to write project file: {exc}"})

    return json.dumps(
        {
            "net_class": name,
            "nets_assigned": len(nets),
            "track_width_mm": track_width,
            "clearance_mm": clearance,
        }
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def remove_dangling_tracks(pcb_path: str = PCB_PATH) -> str:
    """Detect and remove trace segments with unconnected endpoints.

    Iteratively removes dangling segments until no more are found.
    A segment is considered dangling if either endpoint does not connect
    to a pad, via, or another trace endpoint.

    Args:
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    tolerance = 0.001  # mm
    total_removed = 0
    iterations = 0

    while True:
        # Build connection points: pad positions + via centers + trace endpoints
        connection_points: list[tuple[float, float]] = []

        # Pad positions in board coordinates
        for fp in board.footprints:
            fp_x = fp.position.X
            fp_y = fp.position.Y
            theta = math.radians(fp.position.angle or 0)
            for pad in fp.pads:
                ox, oy = pad.position.X, pad.position.Y
                px = fp_x + (ox * math.cos(theta) - oy * math.sin(theta))
                py = fp_y + (ox * math.sin(theta) + oy * math.cos(theta))
                connection_points.append((round(px, 3), round(py, 3)))

        # Via positions
        for item in board.traceItems:
            if isinstance(item, Via):
                connection_points.append((round(item.position.X, 3), round(item.position.Y, 3)))

        # Trace endpoints (each segment contributes both start and end)
        segments = [t for t in board.traceItems if isinstance(t, Segment)]
        for seg in segments:
            connection_points.append((round(seg.start.X, 3), round(seg.start.Y, 3)))
            connection_points.append((round(seg.end.X, 3), round(seg.end.Y, 3)))

        # Check each segment for dangling endpoints
        dangling = []
        for seg in segments:
            start = (round(seg.start.X, 3), round(seg.start.Y, 3))
            end = (round(seg.end.X, 3), round(seg.end.Y, 3))

            # Count connections at start point (subtract this segment's own contribution)
            start_connections = (
                sum(
                    1
                    for pt in connection_points
                    if abs(pt[0] - start[0]) < tolerance and abs(pt[1] - start[1]) < tolerance
                )
                - 1
            )

            # Count connections at end point (subtract this segment's own contribution)
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
            board.traceItems.remove(seg)
        total_removed += len(dangling)
        iterations += 1

    if total_removed > 0:
        board.to_file()

    return json.dumps({"tracks_removed": total_removed, "iterations": iterations})


# ---------------------------------------------------------------------------
# CLI analysis tools (1)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_EXPORT)
def run_drc(pcb_path: str = PCB_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Run Design Rules Check (DRC) on a PCB.

    Returns JSON report with violations.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Directory for report file (default: same as PCB)
    """
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
        return json.dumps({"error": "DRC failed to produce output file"}, indent=2)
    violations = report.get("violations", [])
    unconnected = report.get("unconnected_items", [])
    return json.dumps(
        {
            "source": report.get("source", ""),
            "kicad_version": report.get("kicad_version", ""),
            "violation_count": len(violations),
            "violations": violations,
            "unconnected_count": len(unconnected),
            "unconnected_items": unconnected,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# CLI PCB export tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_EXPORT)
def export_pcb(
    format: str = "pdf",
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    layers: list[str] | None = None,
    output_units: str = "in",
    exclude_refdes: bool = False,
    exclude_value: bool = False,
    use_contours: bool = False,
    include_border_title: bool = False,
) -> str:
    """Export PCB to PDF, SVG, or DXF format.

    Args:
        format: Output format - "pdf", "svg", or "dxf"
        pcb_path: Path to .kicad_pcb file
        output_dir: Directory for output files
        layers: Optional list of layer names to include (required for DXF)
        output_units: DXF output units - "in" or "mm" (DXF only)
        exclude_refdes: Exclude reference designators (DXF only)
        exclude_value: Exclude component values (DXF only)
        use_contours: Use board outline contours (DXF only)
        include_border_title: Include border and title block (DXF only)
    """
    fmt = format.lower()
    if fmt not in ("pdf", "svg", "dxf"):
        return json.dumps({"error": f"Unknown format: {format}. Use: pdf, svg, dxf"})

    if fmt == "dxf":
        if not layers:
            return json.dumps({"error": "layers parameter is required for DXF export"})
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + ".dxf"))
        args = ["pcb", "export", "dxf", pcb_path, "-o", out_path, "-l", ",".join(layers)]
        if output_units != "in":
            args += ["--output-units", output_units]
        if exclude_refdes:
            args.append("--exclude-refdes")
        if exclude_value:
            args.append("--exclude-value")
        if use_contours:
            args.append("--use-contours")
        if include_border_title:
            args.append("--include-border-title")
        try:
            result = _run_cli(args, check=False)
            if result.returncode != 0:
                return json.dumps({"error": result.stderr.strip()})
            return json.dumps({**_file_meta(out_path), "format": "dxf", "layers": layers})
        except (RuntimeError, FileNotFoundError) as e:
            return json.dumps({"error": str(e), "format": "dxf"}, indent=2)

    # PDF / SVG path
    try:
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
        meta.update({"format": fmt, "layers": layer_list})
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": fmt}, indent=2)


@mcp.tool(annotations=_EXPORT)
def export_gerbers(
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    include_drill: bool = True,
    layers: list[str] | None = None,
) -> str:
    """Export Gerber files for manufacturing.

    When layers contains exactly one layer, exports a single Gerber file.
    Otherwise exports all layers (or the specified subset) plus optional drill files.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory for gerber files
        include_drill: Also export drill files (default: True, ignored in single-layer mode)
        layers: Optional list of layer names. Single layer = single file output.
    """
    # Single-layer mode: one file, like the old export_gerber
    if layers and len(layers) == 1:
        try:
            layer = layers[0]
            out_dir = output_dir or str(Path(pcb_path).parent)
            out_path = str(Path(out_dir) / f"{Path(pcb_path).stem}-{layer.replace('.', '_')}.gbr")
            _run_cli(["pcb", "export", "gerber", "--layers", layer, "--output", out_path, pcb_path])
            meta = _file_meta(out_path)
            meta.update({"format": "gerber", "layer": layer})
            return json.dumps(meta, indent=2)
        except (RuntimeError, FileNotFoundError) as e:
            return json.dumps({"error": str(e), "format": "gerber", "layer": layer}, indent=2)

    try:
        # Multi-layer mode: directory of files
        out = output_dir or str(Path(pcb_path).parent / "gerbers")
        os.makedirs(out, exist_ok=True)
        cmd = ["pcb", "export", "gerbers"]
        if layers:
            cmd += ["--layers", ",".join(layers)]
        cmd += ["--output", out, pcb_path]
        _run_cli(cmd)
        files = sorted(Path(out).glob("*"))
        result = {
            "path": out,
            "format": "gerber",
            "files": [f.name for f in files],
            "count": len(files),
        }
        if include_drill:
            _run_cli(["pcb", "export", "drill", "--output", out, pcb_path])
            drill_files = sorted(Path(out).glob("*.drl")) + sorted(Path(out).glob("*.DRL"))
            result["drill_files"] = [f.name for f in drill_files]
            result["drill_count"] = len(drill_files)
        return json.dumps(result, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool(annotations=_EXPORT)
def export_3d(
    format: str = "step",
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    width: int = 1600,
    height: int = 900,
    side: str = "top",
    quality: str = "basic",
) -> str:
    """Export PCB 3D model or render 3D view to image.

    Args:
        format: Output format - "step", "stl", "glb", or "render" (PNG image)
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
        width: Image width in pixels (render only)
        height: Image height in pixels (render only)
        side: View side: top, bottom, left, right, front, back (render only)
        quality: Render quality: basic, high (render only)
    """
    fmt = format.lower()
    if fmt not in ("step", "stl", "glb", "render"):
        return json.dumps({"error": f"Unknown format: {format}. Use: step, stl, glb, render"})

    if fmt == "render":
        try:
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
            meta.update({"format": "png", "width": width, "height": height, "side": side})
            return json.dumps(meta, indent=2)
        except (RuntimeError, FileNotFoundError) as e:
            return json.dumps({"error": str(e), "format": "png"}, indent=2)

    # STEP / STL / GLB path
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + f".{fmt}"))
        _run_cli(["pcb", "export", fmt, "--output", out_path, pcb_path])
        meta = _file_meta(out_path)
        meta["format"] = fmt
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": fmt}, indent=2)


@mcp.tool(annotations=_EXPORT)
def export_positions(pcb_path: str = PCB_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export component position file (pick and place).

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
    """
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + "-pos.csv"))
        _run_cli(["pcb", "export", "pos", "--format", "csv", "--output", out_path, pcb_path])
        meta = _file_meta(out_path)
        meta["format"] = "csv"
        with open(out_path) as f:
            meta["component_count"] = max(0, len(f.readlines()) - 1)
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool(annotations=_EXPORT)
def export_ipc2581(
    pcb_path: str = PCB_PATH,
    output: str = "",
    precision: int = 3,
    compress: bool = False,
    version: str = "C",
    units: str = "mm",
) -> str:
    """Export PCB in IPC-2581 format for manufacturing data exchange.

    Args:
        pcb_path: Path to .kicad_pcb file
        output: Output file path
        precision: Numeric precision (default: 3)
        compress: Compress output file
        version: IPC-2581 version (default: "C")
        units: Output units - "mm" or "in"
    """
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
        return json.dumps({"error": result.stderr.strip()})
    return json.dumps(_file_meta(out))


_FP_TEXT_DISPLACEMENT_THRESHOLD_MM = 5.0
"""Maximum distance (mm) an FpText position may be from the footprint center
before it is considered displaced and reset.  FpText positions are stored
relative to their parent footprint, so (0, 0) means centered on the footprint."""

_FP_TEXT_DEFAULT_OFFSETS: dict[str, tuple[float, float]] = {
    "reference": (0, -1.5),
    "value": (0, 1.5),
}
"""Default (X, Y) offsets for well-known FpText types, relative to footprint
center.  Any displaced text type not listed here is reset to (0, 0)."""


def _fix_displaced_fp_text(board: "Board", routed_path: str) -> int:  # noqa: F821
    """Reset footprint text fields displaced by Freerouting round-trip.

    After the DSN->SES round-trip, FpText items (Reference, Value, etc.)
    may have their positions scrambled.  This function checks every FpText
    on every footprint and, if the text's relative position exceeds
    ``_FP_TEXT_DISPLACEMENT_THRESHOLD_MM`` from the footprint center,
    resets it to a sensible default offset.

    Returns the number of text fields that were fixed.
    """
    fixed = 0
    for fp in board.footprints:
        for item in fp.graphicItems:
            if not isinstance(item, FpText):
                continue
            if item.position is None:
                continue
            dist = (item.position.X**2 + item.position.Y**2) ** 0.5
            if dist > _FP_TEXT_DISPLACEMENT_THRESHOLD_MM:
                default_x, default_y = _FP_TEXT_DEFAULT_OFFSETS.get(item.type, (0, 0))
                item.position.X = default_x
                item.position.Y = default_y
                fixed += 1
    if fixed > 0:
        board.to_file()
    return fixed


@mcp.tool(annotations=_EXPORT)
def autoroute_pcb(
    pcb_path: str = PCB_PATH,
    max_passes: int = 20,
    num_threads: int = 4,
    timeout: int = 600,
    output_dir: str = OUTPUT_DIR,
) -> str:
    """Autoroute PCB traces using the Freerouting autorouter.

    Exports the board to Specctra DSN format, runs Freerouting for automated
    trace routing, and imports the results into a new PCB file. The original
    board is never modified.

    Requires Java 17+ and KiCad's pcbnew Python bindings. On first run,
    the Freerouting JAR is auto-downloaded (~20MB).

    Args:
        pcb_path: Path to .kicad_pcb file
        max_passes: Maximum autorouter optimization passes
        num_threads: Thread count for routing
        timeout: Max seconds to wait for routing (default: 600)
        output_dir: Directory for output files (default: same as PCB)
    """
    # Resolve to absolute path for subprocess calls
    pcb_path = str(Path(pcb_path).resolve())

    # Pre-flight: check Java
    java_err = _check_java()
    if java_err:
        return json.dumps({"error": java_err})

    # Pre-flight: ensure Freerouting JAR
    jar_path, jar_err = _ensure_jar()
    if jar_err or not jar_path:
        return json.dumps({"error": jar_err or "Freerouting JAR not found."})

    # Count existing traces/vias for before/after comparison
    board = _load_board(pcb_path)
    traces_before = sum(1 for t in board.traceItems if isinstance(t, Segment))
    vias_before = sum(1 for t in board.traceItems if isinstance(t, Via))

    out_dir = output_dir or str(Path(pcb_path).parent)
    stem = Path(pcb_path).stem
    routed_path = str(Path(out_dir) / f"{stem}_routed.kicad_pcb")

    with tempfile.TemporaryDirectory() as tmp_dir:
        dsn_path = str(Path(tmp_dir) / f"{stem}.dsn")
        ses_path = str(Path(tmp_dir) / f"{stem}.ses")

        # Step 1: Export DSN
        dsn_err = _export_dsn(pcb_path, dsn_path)
        if dsn_err:
            return json.dumps({"error": dsn_err})

        # Step 2: Run Freerouting
        route_err = _run_freerouting(
            jar_path=jar_path,
            dsn_path=dsn_path,
            ses_path=ses_path,
            max_passes=max_passes,
            num_threads=num_threads,
            timeout=timeout,
        )
        if route_err:
            return json.dumps({"error": route_err})

        if not Path(ses_path).exists():
            return json.dumps({"error": "Freerouting did not produce a session file."})

        # Step 3: Import SES into new PCB
        ses_err = _import_ses(pcb_path, ses_path, routed_path)
        if ses_err:
            return json.dumps({"error": ses_err})

    # Step 4: Fix displaced footprint text fields
    # The Freerouting DSN->SES round-trip often scrambles FpText positions
    # (Reference, Value, etc.), displacing them far from their parent footprint.
    # Reset any text field whose position is more than 5mm from the footprint
    # center back to a sensible default offset.
    text_fields_fixed = _fix_displaced_fp_text(_load_board(routed_path), routed_path)

    # Count traces/vias in routed board
    routed_board = _load_board(routed_path)
    traces_after = sum(1 for t in routed_board.traceItems if isinstance(t, Segment))
    vias_after = sum(1 for t in routed_board.traceItems if isinstance(t, Via))

    result = {
        "routed_path": str(Path(routed_path).resolve()),
        "traces_added": traces_after - traces_before,
        "vias_added": vias_after - vias_before,
        "text_fields_fixed": text_fields_fixed,
    }

    # Optional DRC
    try:
        drc_out = str(Path(out_dir) / f"{stem}_routed-drc.json")
        _run_cli(
            ["pcb", "drc", "--format", "json", "--severity-all", "--output", drc_out, routed_path],
            check=False,
        )
        with open(drc_out) as f:
            drc = json.load(f)
        result["drc_violations"] = len(drc.get("violations", []))
        result["drc_unconnected"] = len(drc.get("unconnected_items", []))
    except Exception:
        pass  # DRC is optional — kicad-cli may not be available

    return json.dumps(result, indent=2)


def main():
    """Entry point for mcp-server-kicad-pcb console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
