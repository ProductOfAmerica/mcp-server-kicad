"""Tests for the unified MCP server."""

import re
from pathlib import Path

from mcp.server import MCPServer

import mcp_server_kicad
from mcp_server_kicad import footprint, pcb, project, schematic, server, symbol

_MODULES = [schematic, pcb, symbol, footprint, project]


def _build_unified() -> MCPServer:
    """Build a fresh unified MCPServer instance for testing (avoids mutating module state)."""
    target = MCPServer("kicad-test")
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
        assert len(registered) == 109, f"Expected 109 tools, got {len(registered)}: {registered}"

    def test_runtime_imports_no_kiutils(self):
        """kiutils is a test-only dependency since slice 18, so an import of
        it in the package would break a runtime install of the wheel. The
        suite cannot notice that by running, because the dev extra installs
        kiutils, so scan the source instead."""
        src = Path(mcp_server_kicad.__file__).parent
        offenders = [
            p.name
            for p in sorted(src.glob("*.py"))
            if re.search(r"^\s*(from|import) kiutils", p.read_text(), re.M)
        ]
        assert offenders == [], f"kiutils imported by {offenders}; it is in the dev extra only"

    def test_no_tool_name_collisions(self):
        """All tool names across modules are unique."""
        all_names: list[str] = []
        for mod in _MODULES:
            all_names.extend(mod.mcp._tool_manager._tools.keys())
        assert len(all_names) == len(set(all_names)), "Duplicate tool names found"

    def test_server_reports_package_version(self):
        """Every entry point reports our version, not the mcp SDK's.

        An MCPServer left without an explicit version reports an empty string
        and clients fall back to showing something else, so this pins that
        build_server passes ours through to every server instance.
        """
        from importlib.metadata import version as dist_version

        from mcp_server_kicad._shared import SERVER_VERSION

        assert SERVER_VERSION != "0.0.0+unknown", "package metadata not found"
        assert SERVER_VERSION != dist_version("mcp"), "fixture cannot detect a regression"

        for mod in [*_MODULES, server]:
            assert mod.mcp.version == SERVER_VERSION, (
                f"{mod.__name__} reports {mod.mcp.version!r}, expected {SERVER_VERSION!r}"
            )
