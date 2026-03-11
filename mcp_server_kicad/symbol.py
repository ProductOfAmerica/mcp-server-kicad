"""KiCad symbol library MCP server."""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    OUTPUT_DIR,
    SYM_LIB_PATH,
    SymbolLib,
    _run_cli,
)

mcp = FastMCP(
    "kicad-symbol",
    instructions=(
        "KiCad symbol library tools for browsing, inspecting, exporting,"
        " and upgrading symbol libraries.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write .kicad_sym files directly. Use these"
        " MCP tools for all symbol library operations.\n"
        "- NEVER run kicad-cli commands directly. Use export_symbol_svg and"
        " upgrade_sym_lib instead.\n"
        "- Use list_lib_symbols to browse, get_symbol_pins to inspect pin"
        " details. Do NOT grep inside .kicad_sym files."
    ),
)


# ── Library browsing ──────────────────────────────────────────────


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


# ── Export & upgrade (wraps kicad-cli) ────────────────────────────


@mcp.tool()
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


@mcp.tool()
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
