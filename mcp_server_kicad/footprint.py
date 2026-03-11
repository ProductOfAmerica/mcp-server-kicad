"""KiCad footprint library MCP server."""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    FP_LIB_PATH,
    OUTPUT_DIR,
    Footprint,
    _run_cli,
)

mcp = FastMCP(
    "kicad-footprint",
    instructions=(
        "KiCad footprint library tools for browsing, inspecting, exporting,"
        " and upgrading footprint libraries.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write .kicad_mod files directly. Use these"
        " MCP tools for all footprint library operations.\n"
        "- NEVER run kicad-cli commands directly. Use export_footprint_svg"
        " and upgrade_fp_lib instead.\n"
        "- Use list_lib_footprints to browse, get_footprint_details to"
        " inspect. Do NOT grep inside .pretty directories."
    ),
)


# ── Library browsing ──────────────────────────────────────────────


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


# ── Export & upgrade (wraps kicad-cli) ────────────────────────────


@mcp.tool()
def export_footprint_svg(footprint_path: str, output_dir: str = OUTPUT_DIR) -> str:
    """Export footprint to SVG.

    Args:
        footprint_path: Path to .kicad_mod file
        output_dir: Output directory
    """
    try:
        out = output_dir or str(Path(footprint_path).parent)
        os.makedirs(out, exist_ok=True)
        _run_cli(["fp", "export", "svg", "--output", out, footprint_path])
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
def upgrade_footprint_lib(footprint_path: str) -> str:
    """Upgrade a footprint library to current KiCad format.

    Args:
        footprint_path: Path to .kicad_mod file or .pretty directory
    """
    try:
        _run_cli(["fp", "upgrade", footprint_path])
        return f"Successfully upgraded {footprint_path}"
    except RuntimeError as e:
        return f"Error: {e}"


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for mcp-server-kicad-footprint console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
