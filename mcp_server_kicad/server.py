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
        " different tool. Do NOT fall back to manual file editing.\n\n"
        # Named here because a host that defers tool schemas gives the model
        # tool names first and makes it search for the rest. Measured in Claude
        # Desktop on 2026-08-13: one of the 109 tools was named anywhere in
        # these instructions, and "place a 10k resistor" cost three tool_search
        # calls before place_component surfaced. A name that is already in
        # context needs no search at all, only a direct schema fetch. Every
        # name below is checked against the live registry by
        # test_instructions_name_only_real_tools, so a rename cannot leave a
        # phantom here.
        "START HERE (fetch one of these by name; do not search blind):\n"
        "- See what is in a schematic: list_schematic_components\n"
        "- Add a part (resistor, capacitor, IC, ...): place_component\n"
        "- Connect pins to a net: wire_pins_to_net\n"
        "- Check the schematic: run_erc\n"
        "- See what is on the board: list_pcb_footprints\n"
        "- Place or move a footprint: place_footprint, move_footprint\n"
        "- Route: add_trace, add_via, autoroute_pcb\n"
        "- Check the board: run_drc\n"
        "- Push the schematic to the board: update_pcb_from_schematic\n"
        "- Manufacturing output: export_gerbers\n"
        "- Start a new project: create_project\n"
        "- Anything else: search by what you want to do. There are 109 tools;"
        " these are the entry points, not the whole surface."
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
