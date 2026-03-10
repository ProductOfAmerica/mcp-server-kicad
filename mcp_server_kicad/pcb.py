"""KiCad PCB MCP Server — PCB manipulation, DRC, and export tools."""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
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
        " including Gerber, drill, 3D models, and pick-and-place."
    ),
)


# ---------------------------------------------------------------------------
# PCB read tools (8)
# ---------------------------------------------------------------------------


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
def export_pcb(
    format: str = "pdf",
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    layers: list[str] | None = None,
) -> str:
    """Export PCB to PDF or SVG format.

    Args:
        format: Output format - "pdf" or "svg"
        pcb_path: Path to .kicad_pcb file
        output_dir: Directory for output files
        layers: Optional list of layer names to include
    """
    fmt = format.lower()
    if fmt not in ("pdf", "svg"):
        return json.dumps({"error": f"Unknown format: {format}. Use: pdf, svg"})
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


@mcp.tool()
def export_gerbers(
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    include_drill: bool = True,
) -> str:
    """Export Gerber files for all copper and mask layers, optionally including drill files.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory for gerber files
        include_drill: Also export drill files (default: True)
    """
    try:
        out = output_dir or str(Path(pcb_path).parent / "gerbers")
        os.makedirs(out, exist_ok=True)
        _run_cli(["pcb", "export", "gerbers", "--output", out, pcb_path])
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


@mcp.tool()
def export_gerber(
    pcb_path: str = PCB_PATH,
    layer: str = "F.Cu",
    output_dir: str = OUTPUT_DIR,
) -> str:
    """Export a single Gerber file for one layer.

    Args:
        pcb_path: Path to .kicad_pcb file
        layer: Layer name (e.g. "F.Cu", "B.Cu", "F.SilkS")
        output_dir: Output directory
    """
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / f"{Path(pcb_path).stem}-{layer.replace('.', '_')}.gbr")
        _run_cli(["pcb", "export", "gerber", "--layers", layer, "--output", out_path, pcb_path])
        meta = _file_meta(out_path)
        meta.update({"format": "gerber", "layer": layer})
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": "gerber", "layer": layer}, indent=2)


@mcp.tool()
def export_3d(
    format: str = "step",
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
) -> str:
    """Export PCB 3D model in STEP, STL, or GLB format.

    Args:
        format: Output format - "step", "stl", or "glb"
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
    """
    fmt = format.lower()
    if fmt not in ("step", "stl", "glb"):
        return json.dumps({"error": f"Unknown format: {format}. Use: step, stl, glb"})
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + f".{fmt}"))
        _run_cli(["pcb", "export", fmt, "--output", out_path, pcb_path])
        meta = _file_meta(out_path)
        meta["format"] = fmt
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": fmt}, indent=2)


@mcp.tool()
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


@mcp.tool()
def render_3d(
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    width: int = 1600,
    height: int = 900,
    side: str = "top",
    quality: str = "basic",
) -> str:
    """Render PCB 3D view to image.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
        width: Image width in pixels
        height: Image height in pixels
        side: View side: top, bottom, left, right, front, back
        quality: Render quality: basic, high
    """
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


@mcp.tool()
def export_pcb_dxf(
    pcb_path: str = PCB_PATH,
    output: str = "",
    layers: str = "",
    output_units: str = "in",
    exclude_refdes: bool = False,
    exclude_value: bool = False,
    use_contours: bool = False,
    include_border_title: bool = False,
) -> str:
    """Export PCB layers to DXF format for mechanical CAD exchange.

    Args:
        pcb_path: Path to .kicad_pcb file
        output: Output file path
        layers: Comma-separated layer names (required)
        output_units: Output units - "in" or "mm"
        exclude_refdes: Exclude reference designators
        exclude_value: Exclude component values
        use_contours: Use board outline contours
        include_border_title: Include border and title block
    """
    if not layers:
        return json.dumps({"error": "layers parameter is required"})
    out = output or str(Path(OUTPUT_DIR) / (Path(pcb_path).stem + ".dxf"))
    args = ["pcb", "export", "dxf", pcb_path, "-o", out, "-l", layers]
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
    result = _run_cli(args, check=False)
    if result.returncode != 0:
        return json.dumps({"error": result.stderr.strip()})
    return json.dumps({**_file_meta(out), "layers": layers})


@mcp.tool()
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


def main():
    """Entry point for mcp-server-kicad-pcb console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
