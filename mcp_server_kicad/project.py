"""KiCad project scaffolding tools.

Tools for creating KiCad project files, schematics, symbol libraries,
sym-lib-tables, and hierarchical sheets from scratch. Registered on the
schematic server via register_tools().
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import _gen_uuid, _load_sch, _snap_grid


# KiCad 9 file format constants
_KICAD_SCH_VERSION = 20250114
_KICAD_SCH_GENERATOR = "eeschema"
_KICAD_SYM_VERSION = "20231120"


def _create_project(directory: str, name: str) -> str:
    """Create a KiCad 9 project (.kicad_pro + .kicad_prl).

    Args:
        directory: Directory to create the project in (created if missing)
        name: Project name (used for filenames)
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)

    pro_path = d / f"{name}.kicad_pro"
    if pro_path.exists():
        return f"Error: {pro_path} already exists."

    pro_data = {"meta": {"filename": f"{name}.kicad_pro", "version": 1}}
    pro_path.write_text(json.dumps(pro_data, indent=2) + "\n")

    prl_data = {"meta": {"filename": f"{name}.kicad_prl", "version": 3}}
    prl_path = d / f"{name}.kicad_prl"
    prl_path.write_text(json.dumps(prl_data, indent=2) + "\n")

    return f"Created project at {pro_path}"


# Public aliases — tests call these directly without going through MCP
create_project = _create_project


def register_tools(mcp: FastMCP) -> None:
    """Register all project scaffolding tools on the given FastMCP instance."""

    @mcp.tool()
    def create_project(directory: str, name: str) -> str:
        """Create a KiCad 9 project (.kicad_pro + .kicad_prl).

        Args:
            directory: Directory to create the project in (created if missing)
            name: Project name (used for filenames)
        """
        return _create_project(directory, name)
