# MCP Server Reorganization: 3 Servers → 5 Domain-Aligned Servers

**Date:** 2026-03-10
**Status:** Approved

## Problem

The current 3-server architecture (`kicad-schematic`, `kicad-pcb`, `kicad-export`) groups
tools by implementation detail (kiutils manipulation vs kicad-cli wrapping) rather than by
domain. The `kicad-export` server is a grab-bag mixing schematic exports, PCB exports,
symbol exports, footprint exports, and analysis tools. This doesn't match how KiCad users
think or how the KiCad CLI itself is organized (5 subcommands: `fp`, `pcb`, `sch`, `sym`,
`version`).

## Decision

Reorganize into 5 domain-aligned servers, mirroring KiCad's own structure:

1. **kicad-schematic** — schematic manipulation + ERC + schematic exports
2. **kicad-pcb** — PCB manipulation + DRC + PCB exports
3. **kicad-symbol** — symbol library browsing, export, upgrade
4. **kicad-footprint** — footprint library browsing, export, upgrade
5. **kicad-project** — project scaffolding, hierarchical sheets, jobsets, version

This is a **clean break** (no backward-compatible aliases). Acceptable at v0.3.x with a
small user base.

## Design Principles Applied (from modelcontextprotocol.io)

- **"Composability over specificity"** — each server is a coherent domain primitive
- **"Convergence over choice"** — one server per domain, not split by implementation
- **"Pragmatism over purity"** — shared config stays in `_shared.py` even though each
  server only uses a subset
- **Domain-scoped servers** — matches the pattern of MCP reference servers (Filesystem,
  Git, Memory, Time) where each server covers one coherent domain

## Tool Assignment

### kicad-schematic (39 tools)

**Existing from schematic.py (32):**
- Read: `list_components`, `list_labels`, `list_wires`, `get_symbol_pins`,
  `get_pin_positions`, `list_global_labels`, `get_net_connections`
- Write: `place_component`, `remove_component`, `remove_label`, `remove_wire`,
  `remove_junction`, `add_wire`, `add_wires`, `add_label`, `add_junction`,
  `add_junctions`, `add_lib_symbol`, `move_component`, `edit_component_value`,
  `set_component_footprint`, `set_component_property`, `add_global_label`,
  `add_no_connect`, `add_power_symbol`, `add_power_rail`, `auto_place_decoupling_cap`
- Routing: `add_text`, `wire_pin_to_label`, `wire_pins_to_net`, `connect_pins`,
  `no_connect_pin`

**From export.py (7):**
- Analysis: `run_erc`, `list_unconnected_pins`
- Export: `export_schematic_pdf`, `export_schematic_svg`, `export_schematic_dxf`,
  `export_schematic_netlist`, `export_bom`

**Not included (moved to kicad-symbol):** `list_lib_symbols`, `get_symbol_info`

### kicad-pcb (28 tools)

**Existing from pcb.py (15):**
- Read: `list_footprints`, `list_traces`, `list_nets`, `list_zones`, `list_layers`,
  `get_board_info`, `get_footprint_pads`, `list_board_graphic_items`
- Write: `place_footprint`, `move_footprint`, `remove_footprint`, `add_trace`, `add_via`,
  `add_pcb_text`, `add_pcb_line`

**From export.py (11):**
- Analysis: `run_drc`
- Export: `export_gerbers`, `export_gerber`, `export_drill`, `export_pcb_pdf`,
  `export_pcb_svg`, `export_positions`, `export_step`, `export_stl`, `export_glb`,
  `render_3d`

**New (2):**
- `export_pcb_dxf` — wraps `kicad-cli pcb export dxf`
- `export_ipc2581` — wraps `kicad-cli pcb export ipc2581`

**Not included (moved to kicad-footprint):** `list_lib_footprints`, `get_footprint_info`

### kicad-symbol (4 tools)

- From schematic.py: `list_lib_symbols`, `get_symbol_info`
- From export.py: `export_symbol_svg`, `upgrade_symbol_lib`

### kicad-footprint (4 tools)

- From pcb.py: `list_lib_footprints`, `get_footprint_info`
- From export.py: `export_footprint_svg`, `upgrade_footprint_lib`

### kicad-project (7 tools)

- Existing: `create_project`, `create_schematic`, `create_symbol_library`,
  `create_sym_lib_table`, `add_hierarchical_sheet`
- From export.py: `run_jobset`
- New: `get_version` — wraps `kicad-cli version --format about`

**Total: 82 unique tools** (79 existing + 3 new: `export_pcb_dxf`, `export_ipc2581`, `get_version`)

## File Structure

```
mcp_server_kicad/
├── __init__.py
├── __main__.py          # unchanged (error message)
├── _shared.py           # unchanged (shared config, helpers)
├── schematic.py         # existing tools + absorbed schematic exports/ERC
├── pcb.py               # existing tools + absorbed PCB exports/DRC
├── symbol.py            # NEW — symbol lib tools + symbol exports
├── footprint.py         # NEW — footprint lib tools + footprint exports
├── project.py           # reworked — own FastMCP instance + jobset + version
```

## Entry Points (pyproject.toml)

```toml
[project.scripts]
mcp-server-kicad-schematic = "mcp_server_kicad.schematic:main"
mcp-server-kicad-pcb = "mcp_server_kicad.pcb:main"
mcp-server-kicad-symbol = "mcp_server_kicad.symbol:main"
mcp-server-kicad-footprint = "mcp_server_kicad.footprint:main"
mcp-server-kicad-project = "mcp_server_kicad.project:main"
```

Old entry points (`mcp-server-kicad-export`) are removed entirely.

## Server Instructions

- **kicad-schematic**: "KiCad schematic manipulation, ERC analysis, and schematic export
  tools. Use wire_pin_to_label and connect_pins for efficient wiring instead of manually
  computing coordinates."
- **kicad-pcb**: "KiCad PCB manipulation, DRC analysis, and PCB export tools including
  Gerber, drill, 3D models, and pick-and-place."
- **kicad-symbol**: "KiCad symbol library tools for browsing, inspecting, exporting, and
  upgrading symbol libraries."
- **kicad-footprint**: "KiCad footprint library tools for browsing, inspecting, exporting,
  and upgrading footprint libraries."
- **kicad-project**: "KiCad project scaffolding, hierarchical sheet management, jobset
  execution, and version info."

## Shared Infrastructure

`_shared.py` remains unchanged. All servers import from it. Each server uses only the
subset of config it needs (e.g., symbol server uses `SYM_LIB_PATH`, PCB server uses
`PCB_PATH` and `FP_LIB_PATH`). The config resolution cost is negligible (one directory
scan at import).

## New Tool Specifications

### export_pcb_dxf

Wraps `kicad-cli pcb export dxf`.

Parameters:
- `pcb_path: str` — path to .kicad_pcb (default: `PCB_PATH`)
- `output: str` — output file path (default: auto from `OUTPUT_DIR`)
- `layers: str` — comma-separated layer list (required)
- `output_units: str` — "mm" or "in" (default: "in")
- `exclude_refdes: bool` — exclude reference designators (default: False)
- `exclude_value: bool` — exclude footprint values (default: False)
- `use_contours: bool` — plot using contours (default: False)
- `include_border_title: bool` — include border and title block (default: False)

### export_ipc2581

Wraps `kicad-cli pcb export ipc2581`.

Parameters:
- `pcb_path: str` — path to .kicad_pcb (default: `PCB_PATH`)
- `output: str` — output file path (default: auto from `OUTPUT_DIR`)
- `precision: int` — decimal digits (default: 3)
- `compress: bool` — ZIP compress output (default: False)
- `version: str` — IPC-2581 version "B" or "C" (default: "C")
- `units: str` — "mm" or "in" (default: "mm")

### get_version

Wraps `kicad-cli version --format about`.

Parameters: none
Returns: KiCad version string with build and library info.

## Test Reorganization

| Current test file | Action |
|---|---|
| `test_cli_sch_export.py` | Update imports to schematic module |
| `test_cli_pcb_export.py` | Update imports to pcb module |
| `test_cli_analysis.py` | Split: ERC tests import from schematic, DRC from pcb |
| `test_read_tools.py` | No change |
| `test_write_tools.py` | No change |
| `test_routing_tools.py` | No change |
| `test_lib_tools.py` | Rename → `test_symbol_tools.py`, import from symbol module |
| `test_lib_access_tools.py` | Split: symbol tests → `test_symbol_access_tools.py`, footprint tests → `test_footprint_access_tools.py` |
| `test_pcb_read_tools.py` | No change |
| `test_pcb_write_tools.py` | No change |
| `test_new_sch_tools.py` | No change |
| `test_edge_cases.py` | Update imports if any reference export module |
| `test_kicad_native.py` | No change |
| `test_config.py` | No change |
| `test_conftest_smoke.py` | No change |
| `test_project_tools.py` | Add tests for `get_version`, `run_jobset` |
| New: `test_footprint_tools.py` | Tests for footprint lib tools |
| New: `test_pcb_dxf_export.py` | Tests for `export_pcb_dxf` |
| New: `test_ipc2581_export.py` | Tests for `export_ipc2581` |

## README Update

Update the MCP client config example from 3 servers to 5:

```json
{
  "mcpServers": {
    "kicad-schematic": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-schematic"],
      "cwd": "/path/to/your/kicad/project"
    },
    "kicad-pcb": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-pcb"],
      "cwd": "/path/to/your/kicad/project"
    },
    "kicad-symbol": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-symbol"],
      "cwd": "/path/to/your/kicad/project"
    },
    "kicad-footprint": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-footprint"],
      "cwd": "/path/to/your/kicad/project"
    },
    "kicad-project": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-project"],
      "cwd": "/path/to/your/kicad/project"
    }
  }
}
```

## Implementation Notes

- **Helper function migration:** Private helpers in export.py (`_annotate_erc_violations`,
  `_parse_unconnected_pins`, etc.) must move alongside their public tools. ERC helpers go
  to schematic.py.
- **New imports needed:** schematic.py and pcb.py do not currently import `_run_cli`,
  `_file_meta`, or `OUTPUT_DIR` from `_shared.py`. These must be added when absorbing
  CLI-based export tools.
- **project.py rework:** project.py currently uses a `register_tools(mcp)` pattern to
  register on the schematic server. It must be reworked to create its own
  `FastMCP("kicad-project")` instance with a `main()` entry point. The internal
  `_create_project()` etc. functions and their public aliases stay for test imports.

## Scope Exclusions

The following KiCad CLI commands are intentionally **not** implemented (legacy/niche):
- `pcb export vrml` — GLB/STEP cover 3D needs
- `sch export hpgl` — pen plotter format, niche
- `sch export ps` — PostScript, PDF covers this
- `sch export python-bom` — legacy XML BOM, modern BOM export exists
