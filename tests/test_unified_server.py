"""Tests for the unified MCP server."""

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad import footprint, pcb, project, schematic, server, symbol

_MODULES = [schematic, pcb, symbol, footprint, project]


def _build_unified() -> FastMCP:
    """Build a fresh unified FastMCP instance for testing (avoids mutating module state)."""
    target = FastMCP("kicad-test")
    for mod in _MODULES:
        server._copy_tools(mod.mcp, target)
    return target


class TestUnifiedServer:
    def test_server_module_has_mcp(self):
        assert hasattr(server, "mcp")
        assert hasattr(server, "main")

    def test_all_tools_registered(self):
        """Every tool is registered, including the kicad-cli ones.

        Registration is unconditional: a CLI tool that fails with an
        actionable error is more useful to a client than a tool that is
        silently absent, and any kicad-cli probe at startup is a guess.
        """
        target = _build_unified()
        registered = set(target._tool_manager._tools.keys())
        # Spot-check a few tools from each module
        assert "place_component" in registered  # schematic
        assert "add_trace" in registered  # pcb
        assert "list_lib_symbols" in registered  # symbol
        assert "list_lib_footprints" in registered  # footprint
        assert "create_project" in registered  # project
        # CLI tools
        assert "run_erc" in registered
        assert "run_drc" in registered
        assert "export_gerbers" in registered
        # Total tool count
        assert len(registered) == 107, f"Expected 107 tools, got {len(registered)}: {registered}"

    def test_no_tool_name_collisions(self):
        """All tool names across modules are unique."""
        all_names: list[str] = []
        for mod in _MODULES:
            all_names.extend(mod.mcp._tool_manager._tools.keys())
        assert len(all_names) == len(set(all_names)), "Duplicate tool names found"
