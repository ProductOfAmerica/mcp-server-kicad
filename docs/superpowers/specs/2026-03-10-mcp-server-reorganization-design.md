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
- **Anthropic tool use best practice** — "Consolidate related operations into fewer tools.
  Fewer, more capable tools reduce selection ambiguity and make your tool surface easier
  for Claude to navigate." (from platform.claude.com tool use docs)

## Tool Consolidation

To reduce tool surface area from 82 → 63, the following consolidations apply:

### Format-parameter merges (7 tools → 3)

| Old tools | New tool | Rationale |
|---|---|---|
| `export_schematic_pdf`, `_svg`, `_dxf` | `export_schematic(format="pdf\|svg\|dxf")` | Identical signatures |
| `export_pcb_pdf`, `_pcb_svg` | `export_pcb(format="pdf\|svg")` | Identical signatures (DXF stays separate — distinct required params) |
| `export_step`, `_stl`, `_glb` | `export_3d(format="step\|stl\|glb")` | Identical signatures |

### Absorption merges (2 tools → 1)

| Old tools | New tool | Rationale |
|---|---|---|
| `export_gerbers` + `export_drill` | `export_gerbers(include_drill=True)` | Always used together in fabrication |

### Single/batch merges (6 tools → 3)

| Old tools | New tool | Rationale |
|---|---|---|
| `add_wire` + `add_wires` | `add_wires` (list, handles single) | Batch superset |
| `add_junction` + `add_junctions` | `add_junctions` (list, handles single) | Batch superset |
| `wire_pin_to_label` + `wire_pins_to_net` | `wire_pins_to_net` (list, handles single) | Batch superset |

### Query consolidation (10 tools → 2)

| Old tools | New tool | Rationale |
|---|---|---|
| `list_components`, `list_labels`, `list_wires`, `list_global_labels` | `list_schematic_items(item_type=...)` | Same signature, same pattern |
| `list_footprints`, `list_traces`, `list_nets`, `list_zones`, `list_layers`, `list_board_graphic_items` | `list_pcb_items(item_type=...)` | Same signature, same pattern |

### Property tool consolidation (3 tools → 1)

| Old tools | New tool | Rationale |
|---|---|---|
| `edit_component_value`, `set_component_footprint`, `set_component_property` | `set_component_property` (already general) | Superset handles all cases via key param |

## Tool Assignment (post-consolidation)

### kicad-schematic (29 tools)

**Read (4):**
- `list_schematic_items` — query by item_type: components, labels, wires, global_labels
- `get_symbol_pins`, `get_pin_positions`, `get_net_connections`

**Write (16):**
- `place_component`, `remove_component`, `move_component`
- `set_component_property` — set any property (Value, Reference, Footprint, custom)
- `add_lib_symbol`
- `add_wires` — add one or more wires (batch; replaces add_wire)
- `remove_wire`
- `add_label`, `remove_label`, `add_global_label`
- `add_junctions` — add one or more junctions (batch; replaces add_junction)
- `remove_junction`
- `add_no_connect`, `add_power_symbol`, `add_power_rail`, `auto_place_decoupling_cap`

**Routing (4):**
- `add_text`
- `wire_pins_to_net` — wire one or more pins to a net label (replaces wire_pin_to_label)
- `connect_pins`, `no_connect_pin`

**Analysis (2):**
- `run_erc`, `list_unconnected_pins`

**Export (3):**
- `export_schematic` — format param: pdf, svg, dxf
- `export_netlist` — netlist formats (kicadxml, kicadnet)
- `export_bom`

**Not included (moved to kicad-symbol):** `list_lib_symbols`, `get_symbol_info`

### kicad-pcb (19 tools)

**Read (3):**
- `list_pcb_items` — query by item_type: footprints, traces, nets, zones, layers,
  graphic_items
- `get_board_info`, `get_footprint_pads`

**Write (7):**
- `place_footprint`, `move_footprint`, `remove_footprint`
- `add_trace`, `add_via`, `add_pcb_text`, `add_pcb_line`

**Analysis (1):**
- `run_drc`

**Export (8):**
- `export_pcb` — 2D export, format param: pdf, svg
- `export_pcb_dxf` — DXF export (separate: required layers, units, contour options)
- `export_gerbers` — all Gerber layers + optional drill (include_drill param)
- `export_gerber` — single-layer Gerber
- `export_positions` — pick-and-place
- `export_3d` — 3D model, format param: step, stl, glb
- `render_3d` — 3D render to image (separate: width, height, side, quality)
- `export_ipc2581` — IPC-2581

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

**Total: 63 tools** (82 pre-consolidation − 19 consolidated away)

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

## New & Consolidated Tool Specifications

### list_schematic_items (consolidated)

Replaces `list_components`, `list_labels`, `list_wires`, `list_global_labels`.

Parameters:
- `item_type: str` — one of: "components", "labels", "wires", "global_labels" (required)
- `schematic_path: str` — path to .kicad_sch (default: `SCH_PATH`)

Each item_type dispatches to the existing query logic (kiutils parsing).

### list_pcb_items (consolidated)

Replaces `list_footprints`, `list_traces`, `list_nets`, `list_zones`, `list_layers`,
`list_board_graphic_items`.

Parameters:
- `item_type: str` — one of: "footprints", "traces", "nets", "zones", "layers",
  "graphic_items" (required)
- `pcb_path: str` — path to .kicad_pcb (default: `PCB_PATH`)

### export_schematic (consolidated)

Replaces `export_schematic_pdf`, `export_schematic_svg`, `export_schematic_dxf`.
Wraps `kicad-cli sch export <format>`.

Parameters:
- `format: str` — "pdf", "svg", or "dxf" (default: "pdf")
- `schematic_path: str` — path to .kicad_sch (default: `SCH_PATH`)
- `output_dir: str` — output directory (default: `OUTPUT_DIR`)

### export_netlist (renamed)

Was `export_schematic_netlist`. Wraps `kicad-cli sch export netlist`.

Parameters:
- `schematic_path: str` — path to .kicad_sch (default: `SCH_PATH`)
- `output_dir: str` — output directory (default: `OUTPUT_DIR`)
- `format: str` — "kicadxml" or "kicadnet" (default: "kicadxml")

### export_pcb (consolidated)

Replaces `export_pcb_pdf`, `export_pcb_svg`.
Wraps `kicad-cli pcb export <format>`.

Parameters:
- `format: str` — "pdf" or "svg" (default: "pdf")
- `pcb_path: str` — path to .kicad_pcb (default: `PCB_PATH`)
- `output_dir: str` — output directory (default: `OUTPUT_DIR`)
- `layers: list[str] | None` — layer list (default: None = all layers)

### export_pcb_dxf (new)

Wraps `kicad-cli pcb export dxf`. Kept separate from `export_pcb` because DXF has
distinct required parameters.

Parameters:
- `pcb_path: str` — path to .kicad_pcb (default: `PCB_PATH`)
- `output_dir: str` — output directory (default: `OUTPUT_DIR`)
- `layers: str` — comma-separated layer list (required)
- `output_units: str` — "mm" or "in" (default: "in")
- `exclude_refdes: bool` — exclude reference designators (default: False)
- `exclude_value: bool` — exclude footprint values (default: False)
- `use_contours: bool` — plot using contours (default: False)
- `include_border_title: bool` — include border and title block (default: False)

### export_gerbers (consolidated)

Absorbs `export_drill`. Wraps `kicad-cli pcb export gerbers` + `kicad-cli pcb export
drill`.

Parameters:
- `pcb_path: str` — path to .kicad_pcb (default: `PCB_PATH`)
- `output_dir: str` — output directory (default: `OUTPUT_DIR`)
- `include_drill: bool` — also export drill files (default: True)

### export_3d (consolidated)

Replaces `export_step`, `export_stl`, `export_glb`.
Wraps `kicad-cli pcb export <format>`.

Parameters:
- `format: str` — "step", "stl", or "glb" (default: "step")
- `pcb_path: str` — path to .kicad_pcb (default: `PCB_PATH`)
- `output_dir: str` — output directory (default: `OUTPUT_DIR`)

### export_ipc2581 (new)

Wraps `kicad-cli pcb export ipc2581`.

Parameters:
- `pcb_path: str` — path to .kicad_pcb (default: `PCB_PATH`)
- `output_dir: str` — output directory (default: `OUTPUT_DIR`)
- `precision: int` — decimal digits (default: 3)
- `compress: bool` — ZIP compress output (default: False)
- `version: str` — IPC-2581 version "B" or "C" (default: "C")
- `units: str` — "mm" or "in" (default: "mm")

### get_version (new)

Wraps `kicad-cli version --format about`.

Parameters: none
Returns: KiCad version string with build and library info.

## Test Reorganization

| Current test file | Action |
|---|---|
| `test_cli_sch_export.py` | Update: imports from schematic, test consolidated `export_schematic(format=...)` |
| `test_cli_pcb_export.py` | Update: imports from pcb, test consolidated `export_pcb(format=...)` etc. |
| `test_cli_analysis.py` | Split: ERC tests import from schematic, DRC from pcb |
| `test_read_tools.py` | Update: test `list_schematic_items(item_type=...)` instead of individual list tools |
| `test_write_tools.py` | Update: remove tests for `add_wire`/`add_junction`/`edit_component_value`/`set_component_footprint` (absorbed into batch/general tools) |
| `test_routing_tools.py` | Update: test `wire_pins_to_net` for single-pin case (replaces `wire_pin_to_label`) |
| `test_lib_tools.py` | No change (tests `add_lib_symbol` which stays on schematic server) |
| `test_lib_access_tools.py` | Split: symbol tests → `test_symbol_access_tools.py`, footprint tests → `test_footprint_access_tools.py` |
| `test_pcb_read_tools.py` | Update: test `list_pcb_items(item_type=...)` instead of individual list tools |
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
| New: `test_consolidation.py` | Tests for consolidated tools (`export_3d`, `export_gerbers` w/ drill, etc.) |

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
- **Consolidation approach:** Consolidated tools (e.g., `export_schematic`) should use a
  `format` or `item_type` parameter that dispatches to the appropriate CLI command or
  kiutils logic internally. The function body contains a match/if-elif on the parameter.
- **Batch tools:** `add_wires`, `add_junctions`, `wire_pins_to_net` already accept lists.
  Remove the singular variants (`add_wire`, `add_junction`, `wire_pin_to_label`) and
  update callers/tests. Single-item calls pass a one-element list.
- **Property consolidation:** Remove `edit_component_value` and `set_component_footprint`.
  Users call `set_component_property(ref, key="Value", value="10k")` etc. The existing
  `set_component_property` implementation already handles arbitrary keys.

## Scope Exclusions

The following KiCad CLI commands are intentionally **not** implemented (legacy/niche):
- `pcb export vrml` — GLB/STEP cover 3D needs
- `sch export hpgl` — pen plotter format, niche
- `sch export ps` — PostScript, PDF covers this
- `sch export python-bom` — legacy XML BOM, modern BOM export exists
