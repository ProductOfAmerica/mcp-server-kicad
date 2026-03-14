"""Tests for the unified MCP server."""

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad import footprint, pcb, project, schematic, server, symbol

_MODULES = [schematic, pcb, symbol, footprint, project]


def _build_unified(has_cli: bool) -> FastMCP:
    """Build a fresh unified FastMCP instance for testing (avoids mutating module state)."""
    target = FastMCP("kicad-test")
    for mod in _MODULES:
        server._copy_tools(mod.mcp, target, has_cli)
    return target


class TestUnifiedServer:
    def test_server_module_has_mcp(self):
        assert hasattr(server, "mcp")
        assert hasattr(server, "main")

    def test_copy_tools_with_cli(self):
        """All tools from sub-modules are registered when has_cli=True."""
        target = _build_unified(has_cli=True)
        registered = set(target._tool_manager._tools.keys())
        # Spot-check a few tools from each module
        assert "place_component" in registered  # schematic
        assert "add_trace" in registered  # pcb
        assert "list_lib_symbols" in registered  # symbol
        assert "list_lib_footprints" in registered  # footprint
        assert "create_project" in registered  # project
        # CLI tools should be present
        assert "run_erc" in registered
        assert "export_gerbers" in registered
        # Total tool count
        assert len(registered) == 80, f"Expected 80 tools, got {len(registered)}: {registered}"

    def test_copy_tools_without_cli(self):
        """CLI-dependent tools are excluded when has_cli=False."""
        target = _build_unified(has_cli=False)
        registered = set(target._tool_manager._tools.keys())
        # Non-CLI tools should be present
        assert "place_component" in registered
        assert "add_trace" in registered
        # CLI tools should NOT be present
        for cli_tool in server._CLI_TOOLS:
            assert cli_tool not in registered, f"{cli_tool} should be excluded"
        # Tool count: 73 total - 17 CLI = 56
        assert len(registered) == 63, f"Expected 63 non-CLI tools, got {len(registered)}"

    def test_no_tool_name_collisions(self):
        """All tool names across modules are unique."""
        all_names: list[str] = []
        for mod in _MODULES:
            all_names.extend(mod.mcp._tool_manager._tools.keys())
        assert len(all_names) == len(set(all_names)), "Duplicate tool names found"
