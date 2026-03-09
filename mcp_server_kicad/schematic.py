"""KiCad Schematic MCP Server — schematic manipulation + symbol library tools."""

import math
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    SCH_PATH,
    SYM_LIB_PATH,
    ColorRGBA,
    Connection,
    Effects,
    Font,
    GlobalLabel,
    Junction,
    LocalLabel,
    NoConnect,
    Position,
    Property,
    SchematicSymbol,
    SymbolLib,
    SymbolProjectInstance,
    SymbolProjectPath,
    Text,
    _default_effects,
    _default_stroke,
    _gen_uuid,
    _load_sch,
)

mcp = FastMCP(
    "kicad-schematic",
    instructions="KiCad schematic manipulation and symbol library tools built on kiutils",
)


def _find_lib_symbol(sch, lib_id: str):
    """Find a lib_symbol by lib_id, checking both bare and prefixed names.

    KiCad schematics may store lib_symbols with the library prefix
    (e.g. ``"Device:C"``) or without (e.g. ``"C"``).  This helper
    normalises the lookup so callers don't need to worry about which
    convention the file uses.

    Returns the matching Symbol object, or ``None``.
    """
    bare = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    for ls in sch.libSymbols:
        if ls.entryName == bare or ls.entryName == lib_id:
            return ls
        # kiutils exposes libId with the library prefix even when
        # entryName returns the bare name.
        if getattr(ls, "libId", None) == lib_id:
            return ls
    return None


def _lib_symbol_file_name(ls) -> str:
    """Return the name as it appears in the file (may include library prefix).

    kiutils' ``entryName`` always strips the library prefix, but the
    file may store ``"Device:C"`` or just ``"C"``.  ``libId`` preserves
    the original.
    """
    return getattr(ls, "libId", None) or ls.entryName


# Default KiCad grid spacing in mm (50 mils).
_GRID_MM = 1.27


def _snap_grid(val: float, grid: float = _GRID_MM) -> float:
    """Snap *val* to the nearest multiple of *grid*."""
    return round(round(val / grid) * grid, 4)


# ---------------------------------------------------------------------------
# Schematic read tools (6)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_components(schematic_path: str = SCH_PATH) -> str:
    """List all placed components in a schematic with their references, values, and positions."""
    sch = _load_sch(schematic_path)
    lines = []
    for sym in sch.schematicSymbols:
        ref = next((p.value for p in sym.properties if p.key == "Reference"), "?")
        val = next((p.value for p in sym.properties if p.key == "Value"), "?")
        pos = sym.position
        lines.append(f"{ref}: {val} (lib={sym.libId}) @ ({pos.X}, {pos.Y}) rot={pos.angle}")
    return "\n".join(lines) if lines else "No components found."


@mcp.tool()
def list_labels(schematic_path: str = SCH_PATH) -> str:
    """List all net labels in a schematic."""
    sch = _load_sch(schematic_path)
    lines = []
    for label in sch.labels:
        lines.append(f"{label.text} @ ({label.position.X}, {label.position.Y})")
    return "\n".join(lines) if lines else "No labels found."


@mcp.tool()
def list_wires(schematic_path: str = SCH_PATH) -> str:
    """List all wires in a schematic with their endpoints."""
    sch = _load_sch(schematic_path)
    lines = []
    for item in sch.graphicalItems:
        if isinstance(item, Connection) and item.type == "wire":
            p = item.points
            if len(p) >= 2:
                lines.append(f"({p[0].X}, {p[0].Y}) -> ({p[1].X}, {p[1].Y})")
    return "\n".join(lines) if lines else "No wires found."


@mcp.tool()
def get_symbol_pins(symbol_name: str, schematic_path: str = SCH_PATH) -> str:
    """Get pin info for a symbol in the schematic's lib_symbols.

    Args:
        symbol_name: Symbol name (e.g. "LM7805", "C", "Fuse")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    ls = _find_lib_symbol(sch, symbol_name)
    if ls:
        lines = [f"Symbol: {symbol_name}"]
        for unit in ls.units:
            for pin in unit.pins:
                lines.append(
                    f"  Pin {pin.number}: {pin.name} "
                    f"({pin.electricalType}) "
                    f"@ ({pin.position.X}, {pin.position.Y}) "
                    f"rot={pin.position.angle} len={pin.length}"
                )
        return "\n".join(lines)
    return f"'{symbol_name}' not found."


@mcp.tool()
def get_pin_positions(reference: str, schematic_path: str = SCH_PATH) -> str:
    """Get absolute pin positions for a placed component (accounts for rotation/mirror).

    Args:
        reference: Component reference (e.g. "U1", "R1")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)

    target = None
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference for p in sym.properties):
            target = sym
            break
    if target is None:
        return f"{reference} not found."

    symbol_name = target.libId.split(":")[-1] if ":" in target.libId else target.libId
    lib_sym = _find_lib_symbol(sch, target.libId)
    if lib_sym is None:
        return f"Lib symbol for {reference} not found."

    cx, cy = target.position.X, target.position.Y
    angle_deg = target.position.angle or 0
    angle_rad = math.radians(angle_deg)
    mir = getattr(target, "mirror", None)

    lines = [f"{reference} ({symbol_name}) @ ({cx}, {cy}) rot={angle_deg} mirror={mir}"]

    for unit in lib_sym.units:
        for pin in unit.pins:
            px, py = pin.position.X, pin.position.Y

            # Negate Y to convert from lib_symbol (Y-up) to schematic (Y-down)
            py = -py

            # Apply mirror in schematic coordinate space
            if mir == "x":
                py = -py
            elif mir == "y":
                px = -px

            # Apply rotation (KiCad rotates CCW)
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)
            final_x = cx + px * cos_a - py * sin_a
            final_y = cy + px * sin_a + py * cos_a

            lines.append(
                f"  Pin {pin.number} ({pin.name}): ({round(final_x, 2)}, {round(final_y, 2)})"
            )

    return "\n".join(lines)


@mcp.tool()
def list_global_labels(schematic_path: str = SCH_PATH) -> str:
    """List all global labels in a schematic."""
    sch = _load_sch(schematic_path)
    lines = []
    for gl in sch.globalLabels:
        lines.append(f"{gl.text} ({gl.shape}) @ ({gl.position.X}, {gl.position.Y})")
    return "\n".join(lines) if lines else "No global labels found."


# ---------------------------------------------------------------------------
# Schematic write tools (14)
# ---------------------------------------------------------------------------


@mcp.tool()
def place_component(
    lib_id: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    rotation: float = 0,
    symbol_lib_path: str = "",
    mirror: str = "",
    schematic_path: str = SCH_PATH,
) -> str:
    """Place a component in the schematic.

    Args:
        lib_id: Library identifier (e.g. "Device:R", "Device:C", "MyLib:MyPart")
        reference: Reference designator (e.g. "R1", "U1")
        value: Component value (e.g. "10K", "100nF")
        x: X position in schematic units (mm)
        y: Y position in schematic units (mm)
        rotation: Rotation angle in degrees (0, 90, 180, 270)
        symbol_lib_path: Path to .kicad_sym file if using custom library
        mirror: Mirror axis ("x", "y", or "" for none)
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)

    # Snap placement to grid
    x = _snap_grid(x)
    y = _snap_grid(y)

    # Load symbol definition from custom lib if needed
    if symbol_lib_path:
        sym_lib = SymbolLib.from_file(symbol_lib_path)
        symbol_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
        for s in sym_lib.symbols:
            if s.entryName == symbol_name:
                if not _find_lib_symbol(sch, lib_id):
                    sch.libSymbols.append(s)
                break

    # Create instance — set libName to match the lib_symbol's name as stored
    # in the file so KiCad can resolve the lookup without crashing.
    lib_sym = _find_lib_symbol(sch, lib_id)
    sym = SchematicSymbol()
    sym.libId = lib_id
    if lib_sym:
        sym.libName = _lib_symbol_file_name(lib_sym)
    else:
        sym.libName = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    sym.position = Position(X=x, Y=y, angle=rotation)
    sym.uuid = _gen_uuid()
    sym.unit = 1
    sym.inBom = True
    sym.onBoard = True
    if mirror:
        sym.mirror = mirror

    # Properties
    sym.properties = [
        Property(
            key="Reference",
            value=reference,
            id=0,
            effects=_default_effects(),
            position=Position(X=x, Y=y - 3.81, angle=0),
        ),
        Property(
            key="Value",
            value=value,
            id=1,
            effects=_default_effects(),
            position=Position(X=x, Y=y + 3.81, angle=0),
        ),
        Property(
            key="Footprint",
            value="",
            id=2,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=x, Y=y, angle=0),
        ),
        Property(
            key="Datasheet",
            value="~",
            id=3,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=x, Y=y, angle=0),
        ),
    ]

    # Find lib symbol and add pin UUIDs
    if lib_sym:
        pin_nums = set()
        for unit in lib_sym.units:
            for pin in unit.pins:
                pin_nums.add(pin.number)
        sym.pins = {pn: _gen_uuid() for pn in sorted(pin_nums)}

    # Instances block — required by KiCad 9 for proper annotation
    project_name = Path(sch.filePath).stem if sch.filePath else ""
    sym.instances = [
        SymbolProjectInstance(
            name=project_name,
            paths=[
                SymbolProjectPath(
                    sheetInstancePath=f"/{sch.uuid}",
                    reference=reference,
                    unit=1,
                ),
            ],
        ),
    ]

    sch.schematicSymbols.append(sym)
    sch.to_file()
    return f"Placed {reference} ({value}) at ({x}, {y})"


@mcp.tool()
def remove_component(reference: str, schematic_path: str = SCH_PATH) -> str:
    """Remove a component by reference designator.

    Args:
        reference: Reference designator to remove (e.g. "U2")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    target = None
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference for p in sym.properties):
            target = sym
            break
    if target is None:
        return f"Component {reference} not found."
    sch.schematicSymbols.remove(target)
    sch.to_file()
    return f"Removed {reference}"


@mcp.tool()
def add_wire(x1: float, y1: float, x2: float, y2: float, schematic_path: str = SCH_PATH) -> str:
    """Add a wire between two points.

    Args:
        x1: Start X
        y1: Start Y
        x2: End X
        y2: End Y
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    x1, y1, x2, y2 = _snap_grid(x1), _snap_grid(y1), _snap_grid(x2), _snap_grid(y2)
    wire = Connection(
        type="wire",
        points=[Position(X=x1, Y=y1), Position(X=x2, Y=y2)],
        stroke=_default_stroke(),
        uuid=_gen_uuid(),
    )
    sch.graphicalItems.append(wire)
    sch.to_file()
    return f"Wire: ({x1}, {y1}) -> ({x2}, {y2})"


@mcp.tool()
def add_wires(wires: list[dict], schematic_path: str = SCH_PATH) -> str:
    """Add multiple wires at once. Each wire dict has keys: x1, y1, x2, y2.

    Args:
        wires: List of wire defs [{x1, y1, x2, y2}, ...]
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    for w in wires:
        wire = Connection(
            type="wire",
            points=[
                Position(X=_snap_grid(w["x1"]), Y=_snap_grid(w["y1"])),
                Position(X=_snap_grid(w["x2"]), Y=_snap_grid(w["y2"])),
            ],
            stroke=_default_stroke(),
            uuid=_gen_uuid(),
        )
        sch.graphicalItems.append(wire)
    sch.to_file()
    return f"Added {len(wires)} wires"


@mcp.tool()
def add_label(
    text: str, x: float, y: float, rotation: float = 0, schematic_path: str = SCH_PATH
) -> str:
    """Add a net label at a position.

    Args:
        text: Net name (e.g. "VIN_PROT", "5V_REL")
        x: X position
        y: Y position
        rotation: Degrees (0=right, 90=up, 180=left, 270=down)
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    x, y = _snap_grid(x), _snap_grid(y)
    label = LocalLabel(
        text=text,
        position=Position(X=x, Y=y, angle=rotation),
        effects=_default_effects(),
        uuid=_gen_uuid(),
    )
    sch.labels.append(label)
    sch.to_file()
    return f"Label '{text}' at ({x}, {y})"


@mcp.tool()
def add_junction(x: float, y: float, schematic_path: str = SCH_PATH) -> str:
    """Add a junction dot where wires cross and should connect.

    Args:
        x: X position
        y: Y position
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    x, y = _snap_grid(x), _snap_grid(y)
    junc = Junction(
        position=Position(X=x, Y=y),
        diameter=0,
        color=ColorRGBA(R=0, G=0, B=0, A=0),
        uuid=_gen_uuid(),
    )
    sch.junctions.append(junc)
    sch.to_file()
    return f"Junction at ({x}, {y})"


@mcp.tool()
def add_junctions(points: list[dict], schematic_path: str = SCH_PATH) -> str:
    """Add multiple junctions. Each point dict has keys: x, y.

    Args:
        points: List of junction positions [{x, y}, ...]
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    for p in points:
        junc = Junction(
            position=Position(X=_snap_grid(p["x"]), Y=_snap_grid(p["y"])),
            diameter=0,
            color=ColorRGBA(R=0, G=0, B=0, A=0),
            uuid=_gen_uuid(),
        )
        sch.junctions.append(junc)
    sch.to_file()
    return f"Added {len(points)} junctions"


@mcp.tool()
def add_lib_symbol(symbol_lib_path: str, symbol_name: str, schematic_path: str = SCH_PATH) -> str:
    """Load a symbol definition from a .kicad_sym library into the schematic.

    Args:
        symbol_lib_path: Path to .kicad_sym file
        symbol_name: Symbol name (e.g. "LM7805")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    sym_lib = SymbolLib.from_file(symbol_lib_path)
    for s in sym_lib.symbols:
        if s.entryName == symbol_name:
            if _find_lib_symbol(sch, symbol_name):
                return f"'{symbol_name}' already in lib_symbols."
            sch.libSymbols.append(s)
            sch.to_file()
            return f"Added '{symbol_name}' to lib_symbols."
    return f"'{symbol_name}' not found in {symbol_lib_path}."


@mcp.tool()
def move_component(
    reference: str,
    x: float,
    y: float,
    rotation: float | None = None,
    schematic_path: str = SCH_PATH,
) -> str:
    """Move a placed component to a new position.

    Args:
        reference: Reference designator (e.g. "R1")
        x: New X position
        y: New Y position
        rotation: New rotation in degrees (None = keep current)
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    x, y = _snap_grid(x), _snap_grid(y)
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference for p in sym.properties):
            sym.position.X = x
            sym.position.Y = y
            if rotation is not None:
                sym.position.angle = rotation
            sch.to_file()
            return f"Moved {reference} to ({x}, {y})"
    return f"Component {reference} not found."


@mcp.tool()
def edit_component_value(
    reference: str,
    value: str = "",
    new_reference: str = "",
    footprint: str = "",
    schematic_path: str = SCH_PATH,
) -> str:
    """Edit properties of a placed component.

    Args:
        reference: Current reference designator (e.g. "R1")
        value: New value (e.g. "4.7K"). Empty = no change.
        new_reference: New reference designator. Empty = no change.
        footprint: New footprint. Empty = no change.
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference for p in sym.properties):
            for prop in sym.properties:
                if prop.key == "Value" and value:
                    prop.value = value
                elif prop.key == "Reference" and new_reference:
                    prop.value = new_reference
                elif prop.key == "Footprint" and footprint:
                    prop.value = footprint
            sch.to_file()
            return f"Updated {reference}"
    return f"Component {reference} not found."


@mcp.tool()
def add_global_label(
    text: str,
    x: float,
    y: float,
    rotation: float = 0,
    shape: str = "input",
    schematic_path: str = SCH_PATH,
) -> str:
    """Add a global net label (visible across all sheets).

    Args:
        text: Net name (e.g. "VCC", "SDA")
        x: X position
        y: Y position
        rotation: Degrees (0=right, 90=up, 180=left, 270=down)
        shape: Label shape: input, output, bidirectional, tri_state, passive
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    x, y = _snap_grid(x), _snap_grid(y)
    gl = GlobalLabel(
        text=text,
        position=Position(X=x, Y=y, angle=rotation),
        shape=shape,
        effects=_default_effects(),
        uuid=_gen_uuid(),
    )
    sch.globalLabels.append(gl)
    sch.to_file()
    return f"Global label '{text}' ({shape}) at ({x}, {y})"


@mcp.tool()
def add_no_connect(x: float, y: float, schematic_path: str = SCH_PATH) -> str:
    """Add a no-connect flag on an unused pin.

    Args:
        x: X position
        y: Y position
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    x, y = _snap_grid(x), _snap_grid(y)
    nc = NoConnect(position=Position(X=x, Y=y), uuid=_gen_uuid())
    sch.noConnects.append(nc)
    sch.to_file()
    return f"No-connect at ({x}, {y})"


@mcp.tool()
def add_power_symbol(
    lib_id: str,
    reference: str,
    x: float,
    y: float,
    rotation: float = 0,
    symbol_lib_path: str = "",
    schematic_path: str = SCH_PATH,
) -> str:
    """Place a power symbol (VCC, GND, +3V3, etc.).

    Uses place_component internally. Power symbols are regular symbols
    from the 'power' library with isPower=True.

    Args:
        lib_id: Library ID (e.g. "power:VCC", "power:GND")
        reference: Reference (e.g. "#PWR01")
        x: X position
        y: Y position
        rotation: Rotation in degrees
        symbol_lib_path: Path to power symbol .kicad_sym if not in schematic
        schematic_path: Path to .kicad_sch file
    """
    return place_component(
        lib_id=lib_id,
        reference=reference,
        value=lib_id.split(":")[-1],
        x=x,
        y=y,
        rotation=rotation,
        symbol_lib_path=symbol_lib_path,
        schematic_path=schematic_path,
    )


@mcp.tool()
def add_text(
    text: str,
    x: float,
    y: float,
    rotation: float = 0,
    schematic_path: str = SCH_PATH,
) -> str:
    """Add a text annotation to the schematic.

    Args:
        text: Text content
        x: X position
        y: Y position
        rotation: Rotation in degrees
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    t = Text(
        text=text,
        position=Position(X=x, Y=y, angle=rotation),
        effects=_default_effects(),
        uuid=_gen_uuid(),
    )
    sch.texts.append(t)
    sch.to_file()
    return f"Text '{text}' at ({x}, {y})"


# ---------------------------------------------------------------------------
# Symbol library access tools (2)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_lib_symbols(symbol_lib_path: str = SYM_LIB_PATH) -> str:
    """List all symbols in a .kicad_sym library file.

    Args:
        symbol_lib_path: Path to .kicad_sym file
    """
    lib = SymbolLib.from_file(symbol_lib_path)
    lines = []
    for sym in lib.symbols:
        pin_count = sum(len(u.pins) for u in sym.units)
        lines.append(f"{sym.entryName} ({pin_count} pins)")
    return "\n".join(lines) if lines else "No symbols found."


@mcp.tool()
def get_symbol_info(symbol_name: str, symbol_lib_path: str = SYM_LIB_PATH) -> str:
    """Get detailed pin and property info for a symbol in a library.

    Args:
        symbol_name: Symbol name (e.g. "LM7805")
        symbol_lib_path: Path to .kicad_sym file
    """
    lib = SymbolLib.from_file(symbol_lib_path)
    for sym in lib.symbols:
        if sym.entryName == symbol_name:
            lines = [f"Symbol: {symbol_name}"]
            for prop in sym.properties or []:
                lines.append(f"  {prop.key}: {prop.value}")
            for unit in sym.units:
                for pin in unit.pins:
                    lines.append(
                        f"  Pin {pin.number}: {pin.name} ({pin.electricalType}) "
                        f"@ ({pin.position.X}, {pin.position.Y}) rot={pin.position.angle}"
                    )
            return "\n".join(lines)
    return f"'{symbol_name}' not found in {symbol_lib_path}."


def main():
    """Entry point for mcp-server-kicad-schematic console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
