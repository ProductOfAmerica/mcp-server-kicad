"""KiCad symbol library MCP server."""

import json
import math
import os
from pathlib import Path

from kiutils.items.common import Effects, Fill, Font, Position, Property, Stroke
from kiutils.items.syitems import SyRect
from kiutils.symbol import Symbol, SymbolPin
from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    OUTPUT_DIR,
    SYM_LIB_PATH,
    SymbolLib,
    _run_cli,
)

mcp = FastMCP(
    "kicad-symbol",
    instructions=(
        "KiCad symbol library tools for browsing, inspecting, exporting,"
        " upgrading, and authoring symbol libraries.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write .kicad_sym files directly. Use these"
        " MCP tools for all symbol library operations.\n"
        "- NEVER run kicad-cli commands directly. Use export_symbol_svg and"
        " upgrade_sym_lib instead.\n"
        "- Use list_lib_symbols to browse, get_symbol_info to inspect pin"
        " details, add_symbol to create new symbols."
        " Do NOT grep inside .kicad_sym files."
    ),
)

_KICAD_SYM_VERSION = "20231120"

_VALID_PIN_TYPES = {
    "input",
    "output",
    "bidirectional",
    "passive",
    "power_in",
    "power_out",
    "tri_state",
    "open_collector",
    "open_emitter",
    "unconnected",
    "free",
}


# ── Library browsing ──────────────────────────────────────────────


@mcp.tool(annotations=_READ_ONLY)
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


@mcp.tool(annotations=_READ_ONLY)
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


# ── Symbol authoring ─────────────────────────────────────────────


def _auto_body_rect(pins_data: list[dict]) -> tuple[float, float, float, float]:
    """Compute a body rectangle from pin body-attachment points.

    Each pin extends from its position toward the body.  The body-end
    coordinate is ``position + length`` in the direction of the pin angle.
    The rectangle encloses all body-end points with a minimum size guarantee.
    """
    body_xs: list[float] = []
    body_ys: list[float] = []
    for p in pins_data:
        x, y = float(p.get("x", 0)), float(p.get("y", 0))
        length = float(p.get("length", 2.54))
        angle_rad = math.radians(float(p.get("rotation", 0)))
        body_xs.append(x + length * math.cos(angle_rad))
        body_ys.append(y + length * math.sin(angle_rad))

    if not body_xs:
        return (-2.54, -2.54, 2.54, 2.54)

    min_x, max_x = min(body_xs), max(body_xs)
    min_y, max_y = min(body_ys), max(body_ys)

    # Ensure minimum 2.54 mm in each dimension
    if max_x - min_x < 2.54:
        cx = (min_x + max_x) / 2
        min_x, max_x = cx - 1.27, cx + 1.27
    if max_y - min_y < 2.54:
        cy = (min_y + max_y) / 2
        min_y, max_y = cy - 1.27, cy + 1.27

    return (round(min_x, 4), round(min_y, 4), round(max_x, 4), round(max_y, 4))


@mcp.tool(annotations=_ADDITIVE)
def add_symbol(
    name: str,
    pins: list[dict],
    reference_prefix: str = "U",
    is_power: bool = False,
    pin_names_offset: float = 0.508,
    in_bom: bool = True,
    on_board: bool = True,
    footprint: str = "",
    datasheet: str = "~",
    rectangles: list[dict] | None = None,
    symbol_lib_path: str = SYM_LIB_PATH,
) -> str:
    """Add a new symbol definition to a .kicad_sym library.

    Creates a complete symbol with pins and body graphics.  If the library
    file does not exist it will be created.

    Args:
        name: Symbol name (e.g. "MP4572GQB-P", "TLV75733PDBVR")
        pins: Pin definitions — list of dicts, each with keys:
            number (str): pin number, e.g. "1"
            name (str): pin name, e.g. "VIN" ("~" for unnamed)
            type (str): electrical type — "input", "output", "bidirectional",
              "passive", "power_in", "power_out", "tri_state",
              "open_collector", "open_emitter", "unconnected", "free"
            x (float): X position in mm (default 0)
            y (float): Y position in mm (default 0)
            rotation (float): angle 0/90/180/270 (default 0)
            length (float): pin length in mm (default 2.54)
        reference_prefix: Reference prefix e.g. "U", "R", "C" (default "U")
        is_power: True for power symbols (default False)
        pin_names_offset: Pin name label offset in mm (default 0.508)
        in_bom: Include in BOM (default True)
        on_board: Place on board (default True)
        footprint: Default footprint e.g. "Package_SO:SOIC-8" (default "")
        datasheet: Datasheet URL (default "~")
        rectangles: Optional body rectangle(s) — list of dicts with keys:
            x1, y1, x2, y2 (float): corner coordinates in mm
            fill (str): "none", "background", or "outline" (default "background")
            If omitted, a rectangle is auto-computed from pin positions.
        symbol_lib_path: Path to .kicad_sym file
    """
    if not name:
        return "Error: symbol name is required."
    if not pins:
        return "Error: at least one pin is required."
    if not symbol_lib_path:
        return "Error: symbol_lib_path is required."

    # Validate pins
    for i, p in enumerate(pins):
        for key in ("number", "name", "type"):
            if key not in p:
                return f"Error: pin {i} missing required key '{key}'."
        if p["type"] not in _VALID_PIN_TYPES:
            return (
                f"Error: pin {i} has invalid type '{p['type']}'. "
                f"Valid: {sorted(_VALID_PIN_TYPES)}"
            )

    # Load or create library
    lib_path = Path(symbol_lib_path)
    if lib_path.exists():
        lib = SymbolLib.from_file(str(lib_path))
        for existing in lib.symbols:
            if existing.entryName == name:
                return f"Error: symbol '{name}' already exists in {symbol_lib_path}."
    else:
        lib_path.parent.mkdir(parents=True, exist_ok=True)
        lib = SymbolLib(version=_KICAD_SYM_VERSION, generator="kicad_symbol_editor")
        lib.filePath = str(lib_path)

    # Build the symbol
    sym = Symbol()
    sym.entryName = name
    sym.isPower = is_power
    sym.pinNamesOffset = pin_names_offset
    sym.inBom = in_bom
    sym.onBoard = on_board

    # Standard properties
    sym.properties = [
        Property(
            key="Reference",
            value=reference_prefix,
            id=0,
            effects=Effects(font=Font(height=1.27, width=1.27)),
            position=Position(X=0, Y=-1.27, angle=0),
        ),
        Property(
            key="Value",
            value=name,
            id=1,
            effects=Effects(font=Font(height=1.27, width=1.27)),
            position=Position(X=0, Y=1.27, angle=0),
        ),
        Property(
            key="Footprint",
            value=footprint,
            id=2,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=0, Y=0, angle=0),
        ),
        Property(
            key="Datasheet",
            value=datasheet,
            id=3,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=0, Y=0, angle=0),
        ),
    ]

    # Unit 0 — graphics (no pins)
    unit0 = Symbol()
    unit0.entryName = name
    unit0.unitId = 0
    unit0.styleId = 1

    if rectangles:
        unit0.graphicItems = [
            SyRect(
                start=Position(X=r["x1"], Y=r["y1"]),
                end=Position(X=r["x2"], Y=r["y2"]),
                stroke=Stroke(width=0.254, type="default"),
                fill=Fill(type=r.get("fill", "background")),
            )
            for r in rectangles
        ]
    else:
        x1, y1, x2, y2 = _auto_body_rect(pins)
        unit0.graphicItems = [
            SyRect(
                start=Position(X=x1, Y=y1),
                end=Position(X=x2, Y=y2),
                stroke=Stroke(width=0.254, type="default"),
                fill=Fill(type="background"),
            )
        ]

    # Unit 1 — pins
    unit1 = Symbol()
    unit1.entryName = name
    unit1.unitId = 1
    unit1.styleId = 1
    unit1.pins = [
        SymbolPin(
            electricalType=p["type"],
            position=Position(
                X=float(p.get("x", 0)),
                Y=float(p.get("y", 0)),
                angle=float(p.get("rotation", 0)),
            ),
            length=float(p.get("length", 2.54)),
            name=p["name"],
            number=p["number"],
        )
        for p in pins
    ]

    sym.units = [unit0, unit1]

    lib.symbols.append(sym)
    lib.to_file()

    return f"Added symbol '{name}' ({len(pins)} pins) to {symbol_lib_path}"


# ── Export & upgrade (wraps kicad-cli) ────────────────────────────


@mcp.tool(annotations=_EXPORT)
def export_symbol_svg(symbol_lib_path: str = SYM_LIB_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export symbol library to SVG images.

    Args:
        symbol_lib_path: Path to .kicad_sym file
        output_dir: Output directory
    """
    try:
        out = output_dir or str(Path(symbol_lib_path).parent)
        os.makedirs(out, exist_ok=True)
        _run_cli(["sym", "export", "svg", "--output", out, symbol_lib_path])
        svgs = sorted(Path(out).glob("*.svg"))
        return json.dumps(
            {
                "path": out,
                "format": "svg",
                "files": [f.name for f in svgs],
                "count": len(svgs),
            },
            indent=2,
        )
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": "svg"}, indent=2)


@mcp.tool(annotations=_DESTRUCTIVE)
def upgrade_symbol_lib(symbol_lib_path: str) -> str:
    """Upgrade a symbol library to current KiCad format.

    Args:
        symbol_lib_path: Path to .kicad_sym file
    """
    try:
        _run_cli(["sym", "upgrade", symbol_lib_path])
        return f"Successfully upgraded {symbol_lib_path}"
    except RuntimeError as e:
        return f"Error: {e}"


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for mcp-server-kicad-symbol console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
