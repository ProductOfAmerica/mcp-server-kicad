# mcp-server-kicad

[![PyPI version](https://img.shields.io/pypi/v/mcp-server-kicad)](https://pypi.org/project/mcp-server-kicad/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://github.com/ProductOfAmerica/mcp-server-kicad/actions/workflows/test.yml/badge.svg)](https://github.com/ProductOfAmerica/mcp-server-kicad/actions/workflows/test.yml)

MCP servers for KiCad schematic, PCB, and export automation.

## Servers

| Server | Tools | Description |
|--------|-------|-------------|
| `mcp-server-kicad-schematic` | 22 | Schematic read/write and symbol library tools built on kiutils |
| `mcp-server-kicad-pcb` | 17 | PCB read/write and footprint library tools built on kiutils |
| `mcp-server-kicad-export` | 22 | ERC/DRC analysis, exports (Gerber, PDF, SVG, STEP, STL, GLB, etc.), and utilities via kicad-cli |

## Installation

```bash
pip install mcp-server-kicad
```

Or run directly with `uvx`:

```bash
uvx --from mcp-server-kicad mcp-server-kicad-schematic
uvx --from mcp-server-kicad mcp-server-kicad-pcb
uvx --from mcp-server-kicad mcp-server-kicad-export
```

## Configuration

Add the servers to your Claude Desktop or Claude Code MCP config. Set `cwd` to your KiCad project directory so the servers can auto-detect project files.

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
    "kicad-export": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-export"],
      "cwd": "/path/to/your/kicad/project"
    }
  }
}
```

## Project Path Resolution

The servers resolve file paths in this order:

1. **Auto-detect**: Scans the current working directory for a `.kicad_pro` file and derives schematic, PCB, symbol library, and footprint library paths from it.
2. **Environment variables**: `KICAD_SCH_PATH`, `KICAD_PCB_PATH`, `KICAD_SYM_LIB`, `KICAD_FP_LIB`, and `KICAD_OUTPUT_DIR` override auto-detected values.
3. **Tool parameters**: Every tool accepts an explicit path parameter that takes highest priority.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `KICAD_SCH_PATH` | Path to `.kicad_sch` schematic file |
| `KICAD_PCB_PATH` | Path to `.kicad_pcb` PCB file |
| `KICAD_SYM_LIB` | Path to `.kicad_sym` symbol library file |
| `KICAD_FP_LIB` | Path to `.pretty` footprint library directory |
| `KICAD_OUTPUT_DIR` | Output directory for exports and reports |

## Available Tools

### Schematic Server (22 tools)

#### Read Tools

| Tool | Description |
|------|-------------|
| `list_components` | List all placed components with references, values, and positions |
| `list_labels` | List all net labels in a schematic |
| `list_wires` | List all wires with their endpoints |
| `get_symbol_pins` | Get pin info for a symbol in the schematic's lib_symbols |
| `get_pin_positions` | Get absolute pin positions for a placed component (accounts for rotation/mirror) |
| `list_global_labels` | List all global labels in a schematic |

#### Write Tools

| Tool | Description |
|------|-------------|
| `place_component` | Place a component in the schematic |
| `remove_component` | Remove a component by reference designator |
| `add_wire` | Add a wire between two points |
| `add_wires` | Add multiple wires at once |
| `add_label` | Add a net label at a position |
| `add_junction` | Add a junction dot where wires cross and should connect |
| `add_junctions` | Add multiple junctions at once |
| `add_lib_symbol` | Load a symbol definition from a .kicad_sym library into the schematic |
| `move_component` | Move a placed component to a new position |
| `edit_component_value` | Edit properties of a placed component (value, reference, footprint) |
| `add_global_label` | Add a global net label visible across all sheets |
| `add_no_connect` | Add a no-connect flag on an unused pin |
| `add_power_symbol` | Place a power symbol (VCC, GND, +3V3, etc.) |
| `add_text` | Add a text annotation to the schematic |

#### Symbol Library Tools

| Tool | Description |
|------|-------------|
| `list_lib_symbols` | List all symbols in a .kicad_sym library file |
| `get_symbol_info` | Get detailed pin and property info for a symbol in a library |

### PCB Server (17 tools)

#### Read Tools

| Tool | Description |
|------|-------------|
| `list_footprints` | List all footprints with references, values, positions, and layers |
| `list_traces` | List all traces with start/end, width, layer, and net |
| `list_nets` | List all nets on a PCB |
| `list_zones` | List all copper zones on a PCB |
| `list_layers` | List all enabled layers on a PCB |
| `get_board_info` | Get board summary: footprint count, trace count, net count, thickness |
| `get_footprint_pads` | Get pad info for a placed footprint on the PCB |
| `list_board_graphic_items` | List graphic items on the PCB (lines, text, dimensions) |

#### Write Tools

| Tool | Description |
|------|-------------|
| `place_footprint` | Place a footprint on the PCB |
| `move_footprint` | Move a footprint to a new position |
| `remove_footprint` | Remove a footprint by reference designator |
| `add_trace` | Add a trace segment between two points |
| `add_via` | Add a via at a position |
| `add_pcb_text` | Add text to the PCB (silkscreen, fab layer, etc.) |
| `add_pcb_line` | Add a graphic line to the PCB (edge cuts, silkscreen, etc.) |

#### Footprint Library Tools

| Tool | Description |
|------|-------------|
| `list_lib_footprints` | List all footprints in a .pretty library directory |
| `get_footprint_info` | Get pad and outline details for a footprint .kicad_mod file |

### Export Server (22 tools)

#### Analysis Tools

| Tool | Description |
|------|-------------|
| `run_erc` | Run Electrical Rules Check (ERC) on a schematic |
| `run_drc` | Run Design Rules Check (DRC) on a PCB |

#### Schematic Export Tools

| Tool | Description |
|------|-------------|
| `export_schematic_pdf` | Export schematic to PDF |
| `export_schematic_svg` | Export schematic to SVG |
| `export_schematic_netlist` | Export schematic netlist (KiCad XML, CadStar, OrcadPCB2) |
| `export_bom` | Export Bill of Materials (BOM) as CSV |
| `export_schematic_dxf` | Export schematic to DXF |

#### PCB Export Tools

| Tool | Description |
|------|-------------|
| `export_gerbers` | Export Gerber files for all layers |
| `export_gerber` | Export a single Gerber file for one layer |
| `export_drill` | Export drill files |
| `export_pcb_pdf` | Export PCB layers to PDF |
| `export_pcb_svg` | Export PCB layers to SVG |
| `export_positions` | Export component position file (pick and place) |
| `export_step` | Export PCB as STEP 3D model |
| `export_stl` | Export PCB as STL 3D model |
| `export_glb` | Export PCB as GLB (binary glTF) 3D model |
| `render_3d` | Render PCB 3D view to image |

#### Symbol/Footprint Export Tools

| Tool | Description |
|------|-------------|
| `export_symbol_svg` | Export symbol library to SVG images |
| `export_footprint_svg` | Export footprint to SVG |

#### Utility Tools

| Tool | Description |
|------|-------------|
| `upgrade_symbol_lib` | Upgrade a symbol library to current KiCad format |
| `upgrade_footprint_lib` | Upgrade a footprint library to current KiCad format |
| `run_jobset` | Run a KiCad jobset file |

## System Requirements

- **Python 3.10+**
- **KiCad 9.x** -- required only for the export server (`kicad-cli` must be on `PATH`)
- The schematic and PCB servers use [kiutils](https://github.com/mvnmgrx/kiutils) for file parsing and do not require a KiCad installation

## Debugging

Use the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) to test and debug the servers interactively:

```bash
npx @modelcontextprotocol/inspector mcp-server-kicad-schematic
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

[MIT](LICENSE)
