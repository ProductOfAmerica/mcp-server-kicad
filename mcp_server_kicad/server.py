"""Unified MCP server registering all KiCad tools."""

from mcp.server import MCPServer

from mcp_server_kicad import footprint, pcb, project, schematic, symbol
from mcp_server_kicad._shared import build_server

mcp = build_server(
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


def _copy_tools(source_mcp: MCPServer, target_mcp: MCPServer) -> None:
    """Copy tools from a source MCPServer instance into the target server.

    Uses _tool_manager._tools (private API) because MCPServer has no public
    tool-copy API.  The project's test suite (test_tool_annotations.py) already
    depends on this internal structure.
    """
    target_mcp._tool_manager._tools.update(source_mcp._tool_manager._tools)


def main() -> None:
    """Entry point for unified mcp-server-kicad console script."""
    for mod in [schematic, pcb, symbol, footprint, project]:
        _copy_tools(mod.mcp, mcp)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
