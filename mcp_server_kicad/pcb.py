"""KiCad PCB MCP Server — PCB manipulation + footprint library tools."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    FP_LIB_PATH,
    PCB_PATH,
    Footprint,
    FpText,
    GrLine,
    GrText,
    Position,
    Segment,
    Via,
    _default_effects,
    _fp_ref,
    _fp_val,
    _gen_uuid,
    _load_board,
)

mcp = FastMCP(
    "kicad-pcb",
    instructions="KiCad PCB manipulation and footprint library tools built on kiutils",
)


# ---------------------------------------------------------------------------
# PCB read tools (8)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_footprints(pcb_path: str = PCB_PATH) -> str:
    """List all footprints on a PCB with references, values, positions, and layers."""
    board = _load_board(pcb_path)
    lines = []
    for fp in board.footprints:
        ref = _fp_ref(fp)
        val = _fp_val(fp)
        pos = fp.position
        lines.append(
            f"{ref}: {val} (lib={fp.libId}) @ ({pos.X}, {pos.Y}) rot={pos.angle} layer={fp.layer}"
        )
    return "\n".join(lines) if lines else "No footprints found."


@mcp.tool()
def list_traces(pcb_path: str = PCB_PATH) -> str:
    """List all traces on a PCB with start/end, width, layer, and net."""
    board = _load_board(pcb_path)
    lines = []
    for item in board.traceItems:
        if isinstance(item, Segment):
            lines.append(
                f"({item.start.X}, {item.start.Y}) -> ({item.end.X}, {item.end.Y}) "
                f"w={item.width} layer={item.layer} net={item.net}"
            )
        elif isinstance(item, Via):
            lines.append(
                f"Via @ ({item.position.X}, {item.position.Y}) "
                f"size={item.size} drill={item.drill} layers={item.layers} net={item.net}"
            )
    return "\n".join(lines) if lines else "No traces found."


@mcp.tool()
def list_nets(pcb_path: str = PCB_PATH) -> str:
    """List all nets on a PCB."""
    board = _load_board(pcb_path)
    lines = []
    for net in board.nets:
        if net.name:  # skip unnamed net 0
            lines.append(f"Net {net.number}: {net.name}")
    return "\n".join(lines) if lines else "No nets found."


@mcp.tool()
def list_zones(pcb_path: str = PCB_PATH) -> str:
    """List all copper zones on a PCB."""
    board = _load_board(pcb_path)
    lines = []
    for z in board.zones:
        lines.append(f"Zone: net={z.netName} layers={z.layers} priority={z.priority}")
    return "\n".join(lines) if lines else "No zones found."


@mcp.tool()
def list_layers(pcb_path: str = PCB_PATH) -> str:
    """List all enabled layers on a PCB."""
    board = _load_board(pcb_path)
    lines = []
    for layer in board.layers:
        lines.append(f"{layer.ordinal}: {layer.name} ({layer.type})")
    return "\n".join(lines) if lines else "No layers defined."


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


@mcp.tool()
def list_board_graphic_items(pcb_path: str = PCB_PATH) -> str:
    """List graphic items on the PCB (lines, text, dimensions)."""
    board = _load_board(pcb_path)
    lines = []
    for item in board.graphicItems:
        if isinstance(item, GrLine):
            lines.append(
                f"Line: ({item.start.X}, {item.start.Y}) -> "
                f"({item.end.X}, {item.end.Y}) layer={item.layer}"
            )
        elif isinstance(item, GrText):
            lines.append(
                f"Text: '{item.text}' @ ({item.position.X}, {item.position.Y}) layer={item.layer}"
            )
        else:
            lines.append(f"{type(item).__name__} on {getattr(item, 'layer', '?')}")
    return "\n".join(lines) if lines else "No graphic items found."


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
# Footprint library access tools (2)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_lib_footprints(pretty_dir: str = FP_LIB_PATH) -> str:
    """List all footprints in a .pretty library directory.

    Args:
        pretty_dir: Path to .pretty directory containing .kicad_mod files
    """
    p = Path(pretty_dir)
    if not p.is_dir():
        return f"'{pretty_dir}' is not a directory."
    mods = sorted(p.glob("*.kicad_mod"))
    if not mods:
        return "No footprints found."
    lines = [f.stem for f in mods]
    return "\n".join(lines)


@mcp.tool()
def get_footprint_info(footprint_path: str) -> str:
    """Get pad and outline details for a footprint .kicad_mod file.

    Args:
        footprint_path: Path to .kicad_mod file
    """
    fp = Footprint.from_file(footprint_path)
    lines = [f"Footprint: {fp.entryName}"]
    lines.append(f"  Layer: {fp.layer}")
    for pad in fp.pads:
        lines.append(
            f"  Pad {pad.number}: {pad.type} {pad.shape} "
            f"@ ({pad.position.X}, {pad.position.Y}) "
            f"size=({pad.size.X}, {pad.size.Y}) layers={pad.layers}"
        )
    return "\n".join(lines)


def main():
    """Entry point for mcp-server-kicad-pcb console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
