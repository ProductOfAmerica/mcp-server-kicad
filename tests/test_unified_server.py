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

    def test_every_write_goes_through_atomic_write(self):
        """The only write_bytes/write_text in the package is the temp file
        inside _atomic_write.

        A plain write opens with O_TRUNC, so a failure part way through leaves
        the user's file truncated, which the invariant at the top of
        docs/adr-cst-substrate.md forbids. Measured on NTFS with a concurrent
        reader before this was fixed: 345 torn reads out of 800, including reads
        of zero bytes.

        Scanned rather than exercised, for the same reason as the kiutils check
        above: a new direct write would pass every functional test in the suite.
        Ruff cannot express this, because banned-api matches qualified names and
        Path(x).write_bytes(...) is a method on an instance.
        """
        src = Path(mcp_server_kicad.__file__).parent
        offenders = []
        for p in sorted(src.glob("*.py")):
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\.write_(bytes|text)\(", line) and "not a user file" not in line:
                    offenders.append(f"{p.name}:{n}: {line.strip()}")
        assert offenders == [], (
            "write directly to disk; use _shared._atomic_write so a failed write"
            " cannot truncate the file:\n  " + "\n  ".join(offenders)
        )

    def test_every_text_io_names_an_encoding(self):
        """Text mode without an encoding decodes with the platform's code page.

        KiCad writes UTF-8. Python's text mode does not read it: with no
        ``encoding=`` it uses ``locale.getpreferredencoding(False)``, which on
        Windows is the ANSI code page. A board whose designer used a degree sign,
        an ohm, or an accented name then comes back either as mojibake or as a
        UnicodeDecodeError, and the tools this reached were not obscure ones:
        run_erc, run_drc, list_unconnected_pins, export_bom, export_positions,
        set_net_class and the symbol-library reader.

        Measured before the fix: ``grep -n "encoding=" mcp_server_kicad/`` matched
        nothing at all, across the whole package.

        Scanned rather than exercised, for the same reason as the atomic-write
        check above: reproducing it needs a non-UTF-8 locale, so every functional
        test on a UTF-8 machine passes with the bug present.
        """
        src = Path(mcp_server_kicad.__file__).parent
        pattern = re.compile(r"\.(read_text|write_text)\(|(?<![\w.])open\(")
        offenders = []
        for p in sorted(src.glob("*.py")):
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if not pattern.search(line) or "encoding=" in line:
                    continue
                # Bytes mode has no encoding to name, which is the CST's whole path.
                if ".read_bytes(" in line or ".write_bytes(" in line or '"rb"' in line:
                    continue
                offenders.append(f"{p.name}:{n}: {line.strip()}")
        assert offenders == [], (
            "text I/O with no encoding decodes with the platform code page, not"
            ' UTF-8; pass encoding="utf-8", or read bytes:\n  ' + "\n  ".join(offenders)
        )

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
