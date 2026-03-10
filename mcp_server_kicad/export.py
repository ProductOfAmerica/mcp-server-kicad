"""KiCad Export MCP Server — CLI export, analysis, and utility tools."""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    OUTPUT_DIR,
    SYM_LIB_PATH,
    _run_cli,
)

mcp = FastMCP(
    "kicad-export",
    instructions="KiCad CLI export, analysis, and utility tools wrapping kicad-cli",
)


# ---------------------------------------------------------------------------
# CLI symbol/footprint export tools (2)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# CLI utility tools (3)
# ---------------------------------------------------------------------------


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


@mcp.tool()
def run_jobset(jobset_path: str) -> str:
    """Run a KiCad jobset file.

    Args:
        jobset_path: Path to .kicad_jobset file
    """
    try:
        result = _run_cli(["jobset", "run", jobset_path])
        return f"Jobset completed successfully.\n{result.stdout}"
    except RuntimeError as e:
        return f"Jobset failed: {e}"


def main():
    """Entry point for mcp-server-kicad-export console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
