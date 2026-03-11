# mcp-server-kicad

[![PyPI version](https://img.shields.io/pypi/v/mcp-server-kicad)](https://pypi.org/project/mcp-server-kicad/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://github.com/ProductOfAmerica/mcp-server-kicad/actions/workflows/test.yml/badge.svg)](https://github.com/ProductOfAmerica/mcp-server-kicad/actions/workflows/test.yml)

MCP servers for KiCad schematic, PCB, symbol, footprint, and project automation.

## Servers

| Server | Tools | Description |
|--------|-------|-------------|
| `mcp-server-kicad-schematic` | 29 | Schematic read/write, ERC analysis, and schematic exports (PDF, SVG, DXF, netlist, BOM) |
| `mcp-server-kicad-pcb` | 19 | PCB read/write, DRC analysis, and PCB exports (Gerber, drill, 3D models, pick-and-place) |
| `mcp-server-kicad-symbol` | 4 | Symbol library browsing, SVG export, and library upgrade |
| `mcp-server-kicad-footprint` | 4 | Footprint library browsing, SVG export, and library upgrade |
| `mcp-server-kicad-project` | 7 | Project scaffolding, hierarchical sheets, jobset execution, and version info |

## Installation

```bash
pip install mcp-server-kicad
```

Or run directly with `uvx`:

```bash
uvx --from mcp-server-kicad mcp-server-kicad-schematic
uvx --from mcp-server-kicad mcp-server-kicad-pcb
uvx --from mcp-server-kicad mcp-server-kicad-symbol
uvx --from mcp-server-kicad mcp-server-kicad-footprint
uvx --from mcp-server-kicad mcp-server-kicad-project
```

## Claude Code Plugin

For Claude Code users, install the plugin to get MCP server configuration
and schematic design skills bundled together:

```bash
claude plugin marketplace add ProductOfAmerica/mcp-server-kicad
claude plugin install kicad
```

The plugin automatically configures all five MCP servers and includes skills
that teach layout conventions for schematic design. See
[skills/schematic-design/SKILL.md](skills/schematic-design/SKILL.md) for
details.

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

### Schematic Tools (27 tools)

#### Read Tools

| Tool | Description |
|------|-------------|
| `list_schematic_items` | List schematic items by type (components, labels, wires, global_labels, summary) |
| `get_symbol_pins` | Get pin info for a symbol in the schematic's lib_symbols |
| `get_pin_positions` | Get absolute pin positions for a placed component (accounts for rotation/mirror) |
| `get_net_connections` | Get all connections for a named net |
| `list_unconnected_pins` | List unconnected pins from ERC data |

#### Write Tools

| Tool | Description |
|------|-------------|
| `place_component` | Place a component in the schematic |
| `remove_component` | Remove a component by reference designator |
| `add_wires` | Add one or more wires between points |
| `add_label` | Add a net label at a position |
| `add_junctions` | Add one or more junction dots |
| `add_lib_symbol` | Load a symbol definition from a .kicad_sym library into the schematic |
| `move_component` | Move a placed component to a new position |
| `set_component_property` | Set any property (Value, Reference, Footprint, etc.) on a placed component |
| `add_global_label` | Add a global net label visible across all sheets |
| `add_power_symbol` | Place a power symbol (VCC, GND, +3V3, etc.) with auto PWR_FLAG |
| `add_text` | Add a text annotation to the schematic |
| `wire_pins_to_net` | Wire one or more pins to a named net |
| `auto_place_decoupling_cap` | Automatically place a decoupling capacitor near an IC |
| `connect_pins` | Wire two component pins together |
| `no_connect_pin` | Place a no-connect flag on an unused pin |
| `remove_label` | Remove a net label |
| `remove_wire` | Remove a wire segment |
| `remove_junction` | Remove a junction dot |

#### ERC Analysis

| Tool | Description |
|------|-------------|
| `run_erc` | Run Electrical Rules Check (ERC) on a schematic |

#### Schematic Export Tools

| Tool | Description |
|------|-------------|
| `export_schematic` | Export schematic to PDF, SVG, or DXF format |
| `export_netlist` | Export schematic netlist |
| `export_bom` | Export Bill of Materials (BOM) as CSV |

### PCB Tools (16 tools)

#### Read Tools

| Tool | Description |
|------|-------------|
| `list_pcb_items` | List PCB items by type (footprints, traces, nets, zones, layers, graphic_items) |
| `get_board_info` | Get board summary: footprint count, trace count, net count, thickness |
| `get_footprint_pads` | Get pad info for a placed footprint on the PCB |

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

#### DRC Analysis

| Tool | Description |
|------|-------------|
| `run_drc` | Run Design Rules Check (DRC) on a PCB |

#### PCB Export Tools

| Tool | Description |
|------|-------------|
| `export_pcb` | Export PCB layers to PDF, SVG, or DXF |
| `export_gerbers` | Export Gerber files (all layers or specific layer list) |
| `export_3d` | Export PCB 3D model (STEP/STL/GLB) or render 3D view to PNG |
| `export_positions` | Export component position file (pick and place) |
| `export_ipc2581` | Export PCB in IPC-2581 format for manufacturing data exchange |

### Symbol Tools (5 tools)

| Tool | Description |
|------|-------------|
| `list_lib_symbols` | List all symbols in a .kicad_sym library file |
| `get_symbol_info` | Get detailed pin and property info for a symbol in a library |
| `add_symbol` | Add a new symbol to a .kicad_sym library |
| `export_symbol_svg` | Export symbol library to SVG images |
| `upgrade_symbol_lib` | Upgrade a symbol library to current KiCad format |

### Footprint Tools (4 tools)

| Tool | Description |
|------|-------------|
| `list_lib_footprints` | List all footprints in a .pretty library directory |
| `get_footprint_info` | Get pad and outline details for a footprint .kicad_mod file |
| `export_footprint_svg` | Export footprint to SVG |
| `upgrade_footprint_lib` | Upgrade a footprint library to current KiCad format |

### Project Tools (7 tools)

| Tool | Description |
|------|-------------|
| `create_project` | Create a KiCad 9 project (.kicad_pro + .kicad_prl + .kicad_sch) |
| `create_schematic` | Create a blank schematic file |
| `create_symbol_library` | Create a blank symbol library file |
| `create_sym_lib_table` | Create a sym-lib-table file |
| `add_hierarchical_sheet` | Add a hierarchical sheet to a parent schematic |
| `run_jobset` | Run a KiCad jobset file |
| `get_version` | Get KiCad version information |

## System Requirements

- **Python 3.10+**
- **KiCad 9.x** -- required for CLI-based tools (ERC, DRC, exports). The `kicad-cli` binary must be on `PATH`.
- The schematic and PCB read/write tools use [kiutils](https://github.com/mvnmgrx/kiutils) for file parsing and do not require a KiCad installation.

## Debugging

Use the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) to test and debug the servers interactively:

```bash
npx @modelcontextprotocol/inspector mcp-server-kicad
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

[MIT](LICENSE)
