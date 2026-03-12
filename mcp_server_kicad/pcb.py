"""KiCad PCB MCP Server — PCB manipulation, DRC, and export tools."""

import json
import os
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
    Footprint,
    FpText,
    GrLine,
    GrText,
    Position,
    Segment,
    Via,
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
# PCB write tools (7)
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
    all_violations = []
    for sheet in report.get("sheets", []):
        all_violations.extend(sheet.get("violations", []))
    return json.dumps(
        {
            "source": report.get("source", ""),
            "kicad_version": report.get("kicad_version", ""),
            "violation_count": len(all_violations),
            "violations": all_violations,
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

    # Count traces/vias in routed board
    routed_board = _load_board(routed_path)
    traces_after = sum(1 for t in routed_board.traceItems if isinstance(t, Segment))
    vias_after = sum(1 for t in routed_board.traceItems if isinstance(t, Via))

    result = {
        "routed_path": str(Path(routed_path).resolve()),
        "traces_added": traces_after - traces_before,
        "vias_added": vias_after - vias_before,
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
        violations = []
        for sheet in drc.get("sheets", []):
            violations.extend(sheet.get("violations", []))
        result["drc_violations"] = len(violations)
    except Exception:
        pass  # DRC is optional — kicad-cli may not be available

    return json.dumps(result, indent=2)


def main():
    """Entry point for mcp-server-kicad-pcb console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
