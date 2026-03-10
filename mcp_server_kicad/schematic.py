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
    _resolve_system_lib,
    _snap_grid,
)
from mcp_server_kicad.project import register_tools as _register_project_tools

mcp = FastMCP(
    "kicad-schematic",
    instructions=(
        "KiCad schematic manipulation and symbol library tools built on kiutils."
        " Use wire_pin_to_label and connect_pins for efficient wiring instead of"
        " manually computing coordinates with get_pin_positions + add_wire + add_label."
    ),
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


def _transform_pin_pos(
    px: float,
    py: float,
    pin_angle: float,
    cx: float,
    cy: float,
    comp_angle_deg: float,
    mirror: str | None,
) -> tuple[float, float, float]:
    """Transform a pin from lib coords to absolute schematic coords.

    Returns (final_x, final_y, outward_angle_deg).

    The outward angle is the direction away from the component body in
    schematic coordinates (0=right, 90=down/+Y, 180=left, 270=up/-Y).
    """
    angle_rad = math.radians(comp_angle_deg)

    # Negate Y to convert from lib_symbol (Y-up) to schematic (Y-down)
    py = -py

    # Apply mirror and compute absolute pin angle (toward-body direction)
    if mirror == "x":
        py = -py
        abs_pin_angle = pin_angle + comp_angle_deg
    elif mirror == "y":
        px = -px
        abs_pin_angle = pin_angle + 180 + comp_angle_deg
    else:
        abs_pin_angle = -pin_angle + comp_angle_deg

    # Apply rotation (KiCad rotates CCW)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    final_x = cx + px * cos_a - py * sin_a
    final_y = cy + px * sin_a + py * cos_a

    # Outward direction (away from body)
    outward = (abs_pin_angle + 180) % 360
    return final_x, final_y, outward


def _get_pin_pos(sch, reference: str, pin_name: str) -> tuple[float, float, float]:
    """Return absolute (x, y, outward_angle_deg) for a placed component's pin.

    Matches pin by name (e.g. "IN", "GND") first, then by number (e.g. "1").
    If multiple pins share a name, returns the first match.
    Raises ValueError if reference or pin not found.
    """
    target = None
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference for p in sym.properties):
            target = sym
            break
    if target is None:
        raise ValueError(f"Component {reference} not found")

    lib_sym = _find_lib_symbol(sch, target.libId)
    if lib_sym is None:
        raise ValueError(f"Lib symbol for {reference} not found")

    cx, cy = target.position.X, target.position.Y
    comp_angle = target.position.angle or 0
    mir = getattr(target, "mirror", None)

    for unit in lib_sym.units:
        for pin in unit.pins:
            if pin.name == pin_name or pin.number == pin_name:
                return _transform_pin_pos(
                    pin.position.X,
                    pin.position.Y,
                    pin.position.angle or 0,
                    cx,
                    cy,
                    comp_angle,
                    mir,
                )

    raise ValueError(f"Pin '{pin_name}' not found on {reference}")


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
    mir = getattr(target, "mirror", None)

    lines = [f"{reference} ({symbol_name}) @ ({cx}, {cy}) rot={angle_deg} mirror={mir}"]

    for unit in lib_sym.units:
        for pin in unit.pins:
            final_x, final_y, _ = _transform_pin_pos(
                pin.position.X,
                pin.position.Y,
                pin.position.angle or 0,
                cx,
                cy,
                angle_deg,
                mir,
            )
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


@mcp.tool()
def get_net_connections(
    label_text: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Find all component pins connected to a net label.

    Scans labels matching the text, traces wires from label positions,
    and identifies component pins at wire endpoints.

    Args:
        label_text: Net name to search for (e.g. "VCC", "GND")
        schematic_path: Path to .kicad_sch file
    """
    import json as _json

    sch = _load_sch(schematic_path)
    tol = 0.1

    # Collect all label positions for this net
    label_positions: set[tuple[float, float]] = set()
    for lbl in sch.labels:
        if lbl.text == label_text:
            label_positions.add((lbl.position.X, lbl.position.Y))
    for glbl in sch.globalLabels:
        if glbl.text == label_text:
            label_positions.add((glbl.position.X, glbl.position.Y))

    # Collect all wire endpoints reachable from label positions
    reachable: set[tuple[float, float]] = set(label_positions)
    for lx, ly in label_positions:
        for item in sch.graphicalItems:
            if (isinstance(item, Connection) and item.type == "wire"
                    and len(item.points) >= 2):
                p0, p1 = item.points[0], item.points[1]
                if abs(p0.X - lx) < tol and abs(p0.Y - ly) < tol:
                    reachable.add((p1.X, p1.Y))
                elif abs(p1.X - lx) < tol and abs(p1.Y - ly) < tol:
                    reachable.add((p0.X, p0.Y))

    # Find component pins at reachable positions
    connections = []
    for sym in sch.schematicSymbols:
        ref = next(
            (p.value for p in sym.properties if p.key == "Reference"),
            None,
        )
        if ref is None:
            continue
        lib_sym = _find_lib_symbol(sch, sym.libId)
        if lib_sym is None:
            continue
        cx, cy = sym.position.X, sym.position.Y
        comp_angle = sym.position.angle or 0
        mir = getattr(sym, "mirror", None)
        for unit in lib_sym.units:
            for pin in unit.pins:
                px, py, _ = _transform_pin_pos(
                    pin.position.X, pin.position.Y,
                    pin.position.angle or 0,
                    cx, cy, comp_angle, mir,
                )
                px, py = _snap_grid(px), _snap_grid(py)
                for rx, ry in reachable:
                    if abs(px - rx) < tol and abs(py - ry) < tol:
                        connections.append({
                            "reference": ref,
                            "pin": pin.number,
                            "pin_name": pin.name,
                            "x": px,
                            "y": py,
                        })
    return _json.dumps({
        "net": label_text,
        "label_count": len(label_positions),
        "connections": connections,
    }, indent=2)


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

    # Load symbol definition from custom lib or system library
    symbol_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    _loaded_sym_lib = None
    if not _find_lib_symbol(sch, lib_id):
        lib_path = symbol_lib_path
        if not lib_path and ":" in lib_id:
            lib_prefix = lib_id.split(":")[0]
            lib_path = _resolve_system_lib(lib_prefix)
        if lib_path:
            _loaded_sym_lib = SymbolLib.from_file(lib_path)
            for s in _loaded_sym_lib.symbols:
                if s.entryName == symbol_name:
                    sch.libSymbols.append(s)
                    break

    # Check if lib_symbol was found; give helpful error if not
    if not _find_lib_symbol(sch, lib_id) and ":" in lib_id:
        if _loaded_sym_lib is not None:
            available = [s.entryName for s in _loaded_sym_lib.symbols]
            similar = [
                n
                for n in available
                if symbol_name.lower() in n.lower()
                or n.lower() in symbol_name.lower()
            ]
            hint = ""
            if similar:
                hint = f" Similar: {', '.join(similar[:5])}"
            lib_prefix = lib_id.split(":")[0]
            return (
                f"Error: symbol '{symbol_name}' not found in"
                f" {lib_prefix} library.{hint}"
            )

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
def remove_label(
    text: str,
    x: float | None = None,
    y: float | None = None,
    schematic_path: str = SCH_PATH,
) -> str:
    """Remove net label(s) by text, optionally filtered by position.

    If x and y are provided, only removes labels matching both text AND
    position (within 0.1mm tolerance). Otherwise removes ALL labels with
    matching text.

    Args:
        text: Label text to match (e.g. "VCC", "PGND")
        x: Optional X position filter
        y: Optional Y position filter
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    tol = 0.1
    # Snap filter coordinates the same way add_label snaps placement coords
    if x is not None and y is not None:
        x, y = _snap_grid(x), _snap_grid(y)
    removed = []
    remaining = []
    for lbl in sch.labels:
        if lbl.text == text:
            if x is not None and y is not None:
                if abs(lbl.position.X - x) < tol and abs(lbl.position.Y - y) < tol:
                    removed.append(lbl)
                    continue
            else:
                removed.append(lbl)
                continue
        remaining.append(lbl)
    if not removed:
        return f"Label '{text}' not found."
    sch.labels = remaining
    sch.to_file()
    return f"Removed {len(removed)} label(s) '{text}'."


@mcp.tool()
def remove_wire(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    schematic_path: str = SCH_PATH,
) -> str:
    """Remove a wire segment by its endpoint coordinates.

    Matches wires with endpoints within 0.1mm tolerance (in either order).

    Args:
        x1: Start X
        y1: Start Y
        x2: End X
        y2: End Y
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    tol = 0.1
    removed = []
    remaining = []
    for item in sch.graphicalItems:
        if isinstance(item, Connection) and item.type == "wire" and len(item.points) >= 2:
            p0, p1 = item.points[0], item.points[1]
            fwd = (abs(p0.X - x1) < tol and abs(p0.Y - y1) < tol
                   and abs(p1.X - x2) < tol and abs(p1.Y - y2) < tol)
            rev = (abs(p0.X - x2) < tol and abs(p0.Y - y2) < tol
                   and abs(p1.X - x1) < tol and abs(p1.Y - y1) < tol)
            if fwd or rev:
                removed.append(item)
                continue
        remaining.append(item)
    if not removed:
        return f"Wire ({x1},{y1})->({x2},{y2}) not found."
    sch.graphicalItems = remaining
    sch.to_file()
    return f"Removed {len(removed)} wire(s)."


@mcp.tool()
def remove_junction(
    x: float,
    y: float,
    schematic_path: str = SCH_PATH,
) -> str:
    """Remove a junction at the given coordinates.

    Args:
        x: X position
        y: Y position
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    tol = 0.1
    for i, junc in enumerate(sch.junctions):
        if (abs(junc.position.X - x) < tol
                and abs(junc.position.Y - y) < tol):
            sch.junctions.pop(i)
            sch.to_file()
            return f"Removed junction at ({x}, {y})"
    return f"Junction at ({x}, {y}) not found."


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
def set_component_footprint(
    reference: str,
    footprint: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Set the footprint on a placed component.

    Args:
        reference: Component reference (e.g. "R1", "U1")
        footprint: Footprint string (e.g. "Resistor_SMD:R_0402_1005Metric")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference
               for p in sym.properties):
            for prop in sym.properties:
                if prop.key == "Footprint":
                    prop.value = footprint
                    sch.to_file()
                    return f"Set {reference} footprint to {footprint}"
    return f"Component {reference} not found."


@mcp.tool()
def set_component_property(
    reference: str,
    key: str,
    value: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Set any property on a placed component. Creates it if missing.

    Args:
        reference: Component reference (e.g. "R1")
        key: Property name (e.g. "MPN", "Tolerance", "Value")
        value: Property value
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    for sym in sch.schematicSymbols:
        if any(p.key == "Reference" and p.value == reference
               for p in sym.properties):
            # Update existing property
            for prop in sym.properties:
                if prop.key == key:
                    prop.value = value
                    sch.to_file()
                    return f"Set {reference}.{key} = {value}"
            # Create new property (hidden, at component center)
            new_id = max((p.id for p in sym.properties), default=-1) + 1
            sym.properties.append(
                Property(
                    key=key,
                    value=value,
                    id=new_id,
                    effects=Effects(
                        font=Font(height=1.27, width=1.27), hide=True
                    ),
                    position=Position(
                        X=sym.position.X, Y=sym.position.Y, angle=0
                    ),
                )
            )
            sch.to_file()
            return f"Set {reference}.{key} = {value} (new property)"
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

    Automatically places a PWR_FLAG at the same position so the net
    satisfies ERC (power pin driven).

    Args:
        lib_id: Library ID (e.g. "power:VCC", "power:GND")
        reference: Reference (e.g. "#PWR01")
        x: X position
        y: Y position
        rotation: Rotation in degrees
        symbol_lib_path: Path to power symbol .kicad_sym if not in schematic
        schematic_path: Path to .kicad_sch file
    """
    result = place_component(
        lib_id=lib_id,
        reference=reference,
        value=lib_id.split(":")[-1],
        x=x,
        y=y,
        rotation=rotation,
        symbol_lib_path=symbol_lib_path,
        schematic_path=schematic_path,
    )

    # Don't auto-add PWR_FLAG if we just placed one
    symbol_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    if symbol_name == "PWR_FLAG":
        return result

    # Auto-place PWR_FLAG at the same position for ERC compliance
    pwr_lib = symbol_lib_path or _resolve_system_lib("power")

    if pwr_lib:
        sch = _load_sch(schematic_path)
        existing = {
            p.value
            for sym in sch.schematicSymbols
            for p in sym.properties
            if p.key == "Reference" and p.value.startswith("#FLG")
        }
        n = 1
        while f"#FLG{n:02d}" in existing:
            n += 1
        flg_ref = f"#FLG{n:02d}"

        place_component(
            lib_id="power:PWR_FLAG",
            reference=flg_ref,
            value="PWR_FLAG",
            x=x,
            y=y,
            rotation=0,
            symbol_lib_path=pwr_lib,
            schematic_path=schematic_path,
        )
        result += f" + {flg_ref}"

    return result


@mcp.tool()
def add_power_rail(
    lib_id: str,
    reference: str,
    pins: list[dict],
    x: float,
    y: float,
    rotation: float = 0,
    symbol_lib_path: str = "",
    schematic_path: str = SCH_PATH,
) -> str:
    """Place a power symbol and wire listed pins to that net.

    Combines add_power_symbol + batch wire_pin_to_label. Each pin
    gets a stub wire + label with the power net name.

    Args:
        lib_id: Power symbol (e.g. "power:VCC", "power:GND")
        reference: Power reference (e.g. "#PWR01")
        pins: List of {"reference": "C1", "pin": "2"} to connect
        x: Power symbol X position
        y: Power symbol Y position
        rotation: Power symbol rotation (default 0)
        symbol_lib_path: Path to power .kicad_sym if needed
        schematic_path: Path to .kicad_sch file
    """
    result = add_power_symbol(
        lib_id=lib_id,
        reference=reference,
        x=x,
        y=y,
        rotation=rotation,
        symbol_lib_path=symbol_lib_path,
        schematic_path=schematic_path,
    )

    net_name = lib_id.split(":")[-1] if ":" in lib_id else lib_id

    if pins:
        wire_result = wire_pins_to_net(
            pins=pins,
            label_text=net_name,
            schematic_path=schematic_path,
        )
        result += f" | {wire_result}"
    else:
        result += f" | Wired 0 pins to '{net_name}'."

    return result


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
# High-level routing tools (3)
# ---------------------------------------------------------------------------

# Direction -> (dx_sign, dy_sign, label_rotation)
_DIR_OFFSETS = {
    "right": (1, 0, 0),
    "left": (-1, 0, 180),
    "up": (0, -1, 90),
    "down": (0, 1, 270),
}

# Outward angle (math Y-down) -> cardinal direction name
_ANGLE_TO_DIR = {0: "right", 90: "down", 180: "left", 270: "up"}


@mcp.tool()
def wire_pin_to_label(
    reference: str,
    pin_name: str,
    label_text: str,
    stub_length: float = 2.54,
    direction: str = "auto",
    schematic_path: str = SCH_PATH,
) -> str:
    """Wire a component pin to a net label with a short stub.

    Combines get_pin_positions + add_wire + add_label into one call.

    Args:
        reference: Component reference (e.g. "U1", "R1")
        pin_name: Pin name (e.g. "IN", "GND") or number (e.g. "1")
        label_text: Net label text (e.g. "VIN_PROT")
        stub_length: Wire stub length in mm (default 2.54)
        direction: Wire direction: "left", "right", "up", "down", or "auto"
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    px, py, outward = _get_pin_pos(sch, reference, pin_name)
    px, py = _snap_grid(px), _snap_grid(py)

    if direction == "auto":
        snapped = round(outward / 90) * 90 % 360
        direction = _ANGLE_TO_DIR[snapped]

    dx_sign, dy_sign, label_rot = _DIR_OFFSETS[direction]
    end_x = _snap_grid(px + dx_sign * stub_length)
    end_y = _snap_grid(py + dy_sign * stub_length)

    # Check for conflicting labels at the endpoint
    conflict_warning = ""
    tol = 0.1
    for existing in sch.labels:
        if (abs(existing.position.X - end_x) < tol
                and abs(existing.position.Y - end_y) < tol
                and existing.text != label_text):
            conflict_warning = (
                f" WARNING: conflicting label '{existing.text}' already at "
                f"({end_x}, {end_y}) — may create a short with '{label_text}'"
            )
            break

    # Wire stub
    wire = Connection(
        type="wire",
        points=[Position(X=px, Y=py), Position(X=end_x, Y=end_y)],
        stroke=_default_stroke(),
        uuid=_gen_uuid(),
    )
    sch.graphicalItems.append(wire)

    # Net label at endpoint
    label = LocalLabel(
        text=label_text,
        position=Position(X=end_x, Y=end_y, angle=label_rot),
        effects=_default_effects(),
        uuid=_gen_uuid(),
    )
    sch.labels.append(label)

    sch.to_file()
    msg = f"Wired {reference}:{pin_name} -> label '{label_text}' at ({end_x}, {end_y})"
    return msg + conflict_warning


@mcp.tool()
def wire_pins_to_net(
    pins: list[dict],
    label_text: str,
    direction: str = "auto",
    stub_length: float = 2.54,
    schematic_path: str = SCH_PATH,
) -> str:
    """Wire multiple component pins to the same net label.

    Batch version of wire_pin_to_label. Single file load/save cycle.

    Args:
        pins: List of {"reference": "R1", "pin": "1"} dicts
        label_text: Net label text (e.g. "GND", "VCC")
        direction: Wire direction: "auto", "left", "right", "up", "down"
        stub_length: Wire stub length in mm (default 2.54)
        schematic_path: Path to .kicad_sch file
    """
    if not pins:
        return f"Wired 0 pins to '{label_text}'."
    sch = _load_sch(schematic_path)
    tol = 0.1
    warnings = []
    for pin_def in pins:
        ref = pin_def["reference"]
        pin_name = pin_def["pin"]
        try:
            px, py, outward = _get_pin_pos(sch, ref, pin_name)
        except ValueError as e:
            return f"Error wiring {ref}:{pin_name}: {e}"
        px, py = _snap_grid(px), _snap_grid(py)

        if direction == "auto":
            snapped = round(outward / 90) * 90 % 360
            d = _ANGLE_TO_DIR[snapped]
        else:
            d = direction

        dx_sign, dy_sign, label_rot = _DIR_OFFSETS[d]
        end_x = _snap_grid(px + dx_sign * stub_length)
        end_y = _snap_grid(py + dy_sign * stub_length)

        # Check for conflicting labels
        for existing in sch.labels:
            if (abs(existing.position.X - end_x) < tol
                    and abs(existing.position.Y - end_y) < tol
                    and existing.text != label_text):
                warnings.append(
                    f"{ref}:{pin_name} conflicts with"
                    f" '{existing.text}'"
                )
                break

        # Wire stub
        sch.graphicalItems.append(
            Connection(
                type="wire",
                points=[
                    Position(X=px, Y=py),
                    Position(X=end_x, Y=end_y),
                ],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )
        # Net label
        sch.labels.append(
            LocalLabel(
                text=label_text,
                position=Position(
                    X=end_x, Y=end_y, angle=label_rot
                ),
                effects=_default_effects(),
                uuid=_gen_uuid(),
            )
        )

    sch.to_file()
    msg = f"Wired {len(pins)} pins to '{label_text}'."
    if warnings:
        msg += " WARNINGS: " + "; ".join(warnings)
    return msg


@mcp.tool()
def connect_pins(
    ref1: str,
    pin1: str,
    ref2: str,
    pin2: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Connect two component pins with Manhattan (L-shaped) wire routing.

    Combines get_pin_positions + coordinate math + add_wires into one call.

    Args:
        ref1: First component reference (e.g. "U1")
        pin1: First pin name or number
        ref2: Second component reference (e.g. "C3")
        pin2: Second pin name or number
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    x1, y1, _ = _get_pin_pos(sch, ref1, pin1)
    x2, y2, _ = _get_pin_pos(sch, ref2, pin2)

    x1, y1 = _snap_grid(x1), _snap_grid(y1)
    x2, y2 = _snap_grid(x2), _snap_grid(y2)

    segments = []
    if x1 == x2 or y1 == y2:
        # Axis-aligned: single straight wire
        segments.append(
            Connection(
                type="wire",
                points=[Position(X=x1, Y=y1), Position(X=x2, Y=y2)],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )
    else:
        # L-shaped: horizontal from pin1 to x2, then vertical to pin2
        mid_x, mid_y = x2, y1
        segments.append(
            Connection(
                type="wire",
                points=[Position(X=x1, Y=y1), Position(X=mid_x, Y=mid_y)],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )
        segments.append(
            Connection(
                type="wire",
                points=[Position(X=mid_x, Y=mid_y), Position(X=x2, Y=y2)],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )

    for seg in segments:
        sch.graphicalItems.append(seg)
    sch.to_file()

    n = len(segments)
    return f"Connected {ref1}:{pin1} -> {ref2}:{pin2} via {n} wire segment{'s' if n > 1 else ''}"


@mcp.tool()
def no_connect_pin(
    reference: str,
    pin_name: str,
    schematic_path: str = SCH_PATH,
) -> str:
    """Place a no-connect flag on a component pin.

    Combines get_pin_positions + add_no_connect into one call.

    Args:
        reference: Component reference (e.g. "U2")
        pin_name: Pin name (e.g. "NC") or number (e.g. "3")
        schematic_path: Path to .kicad_sch file
    """
    sch = _load_sch(schematic_path)
    px, py, _ = _get_pin_pos(sch, reference, pin_name)
    px, py = _snap_grid(px), _snap_grid(py)

    nc = NoConnect(position=Position(X=px, Y=py), uuid=_gen_uuid())
    sch.noConnects.append(nc)
    sch.to_file()

    return f"No-connect on {reference}:{pin_name} at ({px}, {py})"


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


_register_project_tools(mcp)


def main():
    """Entry point for mcp-server-kicad-schematic console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
