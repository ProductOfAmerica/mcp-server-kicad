"""Unified MCP server registering all KiCad tools."""

import shutil

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad import footprint, pcb, project, schematic, symbol

mcp = FastMCP(
    "kicad",
    instructions=(
        "KiCad EDA tools for schematic capture, PCB layout, symbol/footprint"
        " libraries, and project management.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write KiCad files (.kicad_sch, .kicad_pcb,"
        " .kicad_sym, .kicad_mod, .kicad_pro) directly. All manipulation"
        " MUST go through these MCP tools.\n"
        "- NEVER run kicad-cli commands directly. Use the export, ERC, and"
        " DRC tools provided by this server.\n"
        "- When a tool returns an error, try different parameters or a"
        " different tool. Do NOT fall back to manual file editing."
    ),
)

# Tools that require kicad-cli on PATH
_CLI_TOOLS: set[str] = {
    # schematic
    "export_schematic",
    "export_netlist",
    "export_bom",
    "run_erc",
    "list_unconnected_pins",
    # pcb
    "export_pcb",
    "export_gerbers",
    "export_3d",
    "export_positions",
    "export_ipc2581",
    "run_drc",
    # symbol
    "export_symbol_svg",
    "upgrade_symbol_lib",
    # footprint
    "export_footprint_svg",
    "upgrade_footprint_lib",
    # project
    "run_jobset",
    "get_version",
}


def _copy_tools(source_mcp: FastMCP, target_mcp: FastMCP, has_cli: bool) -> None:
    """Copy tools from a source FastMCP instance into the target server.

    Uses _tool_manager._tools (private API) because FastMCP has no public
    tool-copy API.  The project's test suite (test_tool_annotations.py) already
    depends on this internal structure.
    """
    for name, tool in source_mcp._tool_manager._tools.items():
        if not has_cli and name in _CLI_TOOLS:
            continue
        target_mcp._tool_manager._tools[name] = tool


def main() -> None:
    """Entry point for unified mcp-server-kicad console script."""
    has_cli = shutil.which("kicad-cli") is not None
    for mod in [schematic, pcb, symbol, footprint, project]:
        _copy_tools(mod.mcp, mcp, has_cli)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
