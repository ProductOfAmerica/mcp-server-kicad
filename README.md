<!-- mcp-name: io.github.ProductOfAmerica/mcp-server-kicad -->

<div align="center">

<img src="https://raw.githubusercontent.com/ProductOfAmerica/mcp-server-kicad/main/.github/assets/logo.png" alt="mcp-server-kicad" width="128">

# mcp-server-kicad

**Let an AI assistant edit real KiCad projects without corrupting them.**

109 MCP tools covering schematic capture, PCB layout, symbol and footprint
libraries, ERC and DRC, and manufacturing exports. No screenshots, no
copy-paste, no hand-edited s-expressions: the model calls tools, KiCad files
change on disk.

[![PyPI](https://img.shields.io/pypi/v/mcp-server-kicad?style=flat-square&logo=pypi&logoColor=white&label=pypi)](https://pypi.org/project/mcp-server-kicad/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-server-kicad?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/mcp-server-kicad/)
[![KiCad](https://img.shields.io/badge/KiCad-9%20%7C%2010-314CB0?style=flat-square&logo=kicad&logoColor=white)](https://www.kicad.org/)
[![Tests](https://img.shields.io/github/actions/workflow/status/ProductOfAmerica/mcp-server-kicad/ci.yml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=tests)](https://github.com/ProductOfAmerica/mcp-server-kicad/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)

[Quick start](#quick-start) •
[What you get](#what-you-get) •
[Configuration](#configuration) •
[Tool reference](#tool-reference) •
[Requirements](#requirements) •
[Contributing](#contributing)

</div>

---

## What it looks like

```text
"Add a 100 nF decoupling cap on U1's VCC pin, then check ERC."

  get_pin_positions            locate U1's VCC and GND pins
  auto_place_decoupling_cap    place C3, wire it, drop the junctions
  run_erc                      report what the change broke

"Now push the netlist to the board and route it."

  update_pcb_from_schematic    sync footprints and pad nets onto the PCB
  autoroute_pcb                route with Freerouting
  run_drc                      clearance, width, and unconnected checks
```

Every tool works on the real file. Writes go through a byte-preserving
substrate, so bytes you did not ask to change reach the disk unchanged, and an
edit that cannot be done correctly is refused with the file intact.

## Quick start

### Claude Code

Install the plugin. It wires up the server and ships the design skills with it:

```bash
claude plugin marketplace add ProductOfAmerica/mcp-server-kicad
claude plugin install kicad
```

### Claude Desktop

Download `kicad-<version>.mcpb` from the [latest release][latest] and install it
from Settings, then Extensions. Claude Desktop fetches Python and the package
itself, so KiCad is the only thing you need installed first.

Fill in the paths it asks for. Unlike the Claude Code plugin, Desktop does not
run from your project directory, so nothing is auto-detected. On Windows, point
it at `kicad-cli.exe` as well; the KiCad installer leaves it off PATH, and the
ERC, DRC, and export tools need it. The design skills are Claude Code only.

[latest]: https://github.com/ProductOfAmerica/mcp-server-kicad/releases/latest

### Everything else

```bash
pip install mcp-server-kicad
```

Then point your MCP client at the unified server and set `cwd` to a KiCad
project directory so paths resolve themselves:

```json
{
  "mcpServers": {
    "kicad": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad"],
      "cwd": "/path/to/your/kicad/project"
    }
  }
}
```

That single entry registers all 109 tools. Prefer to split them across five
smaller servers? See [Configuration](#configuration).

## What you get

### Five tool servers

Run them as one unified server, or separately when you want a smaller tool
surface in context.

| Server | Tools | What it does |
|--------|:-----:|--------------|
| `mcp-server-kicad-schematic` | 42 | Schematic read/write, net tracing, ERC, exports (PDF, SVG, DXF, netlist, BOM) |
| `mcp-server-kicad-pcb` | 34 | Board read/write, netlist import, zones, DRC, autorouting, exports (Gerber, drill, 3D, pick-and-place) |
| `mcp-server-kicad-project` | 24 | Project scaffolding, hierarchical sheets, hierarchy validation, annotation, cross-sheet net tracing |
| `mcp-server-kicad-symbol` | 5 | Symbol library browsing, authoring, SVG export, format upgrade |
| `mcp-server-kicad-footprint` | 4 | Footprint library browsing, SVG export, format upgrade |

### Six design skills (Claude Code plugin)

Tools alone let a model click every button. The bundled skills teach it which
buttons, in what order, and what "done" looks like.

| Skill | Kicks in when |
|-------|---------------|
| [`using-kicad`](skills/using-kicad/SKILL.md) | Any electronics or EDA conversation starts. Routes to the right skill below |
| [`circuit-design`](skills/circuit-design/SKILL.md) | Choosing topology, regulators, and component values, before any file exists |
| [`schematic-plan`](skills/schematic-plan/SKILL.md) | Planning exact placement coordinates and wiring. Pure planning, no writes |
| [`schematic-design`](skills/schematic-design/SKILL.md) | Executing that plan, or modifying an existing schematic |
| [`pcb-layout`](skills/pcb-layout/SKILL.md) | Placing footprints, routing, zones, stackup, trace widths |
| [`verification`](skills/verification/SKILL.md) | ERC and DRC failures, unconnected nets, export prep |

The design skills hand their artifacts to independent reviewer subagents
([`agents/`](agents/)) before moving on, so a bad BOM or a bad placement plan
gets caught before it becomes a schematic.

## Configuration

### Split servers

Five entries instead of one, when you want to load only part of the tool
surface:

<details>
<summary><b>Five-server MCP config</b></summary>

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

Each is also runnable directly:

```bash
uvx --from mcp-server-kicad mcp-server-kicad-schematic
```

</details>

### How paths resolve

Highest priority wins:

1. **Tool parameters.** Every tool accepts an explicit path.
2. **Environment variables.** They override anything auto-detected.
3. **Auto-detect.** The working directory is scanned for a `.kicad_pro`, and
   the schematic, board, and library paths are derived from it.

| Variable | Points at |
|----------|-----------|
| `KICAD_SCH_PATH` | a `.kicad_sch` schematic file |
| `KICAD_PCB_PATH` | a `.kicad_pcb` board file |
| `KICAD_SYM_LIB` | a `.kicad_sym` symbol library file |
| `KICAD_FP_LIB` | a `.pretty` footprint library directory |
| `KICAD_OUTPUT_DIR` | where exports and reports are written |

## Requirements

- **Python 3.10+**
- **KiCad 9.x or 10.x**, for the tools that shell out to `kicad-cli`: ERC, DRC,
  and every export. The read and write tools parse files directly and need no
  KiCad install at all.

CI runs the full suite on Linux against KiCad 9 and on macOS against KiCad 10,
plus a KiCad-free matrix across Python 3.10 through 3.13.

### Finding your KiCad install

`kicad-cli` is looked up on `PATH`, then inside `/Applications/KiCad/KiCad.app`
on macOS, then in the standard Windows install folders, machine-wide under
`Program Files` and per-user under `AppData`, newest version first. Neither
installer adds it to `PATH`. Everything else in the KiCad tree is located
relative to it, the same way KiCad itself does it, so a stock install on any
platform needs no configuration: the stock symbol libraries and the bundled
Python that provides `pcbnew` are both found automatically.

Override any of it if your install is unusual:

| Variable | Overrides |
|----------|-----------|
| `KICAD_CLI_PATH` | the `kicad-cli` executable, and everything derived from its location |
| `KICAD_SYMBOL_DIR` | the directory holding the stock `.kicad_sym` libraries |
| `KICAD_PYTHON` | the interpreter used for `pcbnew` (`fill_zones`, `autoroute_pcb`) |

CLI-backed tools are always registered. Without `kicad-cli` they fail with a
message naming `KICAD_CLI_PATH`, rather than vanishing from the tool list.

### KiCad 10 files

| File | What works today |
|------|------------------|
| `.kicad_sch` | Everything, read and write: the whole schematic server and the whole project server |
| `.kicad_pcb` | Everything, read and write |
| `.kicad_sym`, `.kicad_mod` | Everything: your own libraries as well as the stock ones |

Every tool parses and edits through the byte-preserving substrate, so none of
them refuses a file on its format version and none of them rewrites bytes you
did not ask to change. One caveat, and it is not about the file format:
`autoroute_pcb` hands the board to `pcbnew` for the DSN export and the SES
import, so it additionally needs a `pcbnew` whose era matches the board. It
checks that before it starts. A KiCad 10 board on a KiCad 9 `pcbnew` is refused
with a message naming both versions, rather than failing somewhere inside the
export; install KiCad 10 or point `KICAD_PYTHON` at one. The other direction
runs, and the result carries a warning: a KiCad 9 board routed through a KiCad
10 `pcbnew` comes back as a routed copy in the KiCad 10 format, because that is
what `pcbnew` saves. Your original board is untouched either way. The design
behind the substrate is written up in
[docs/adr-cst-substrate.md](docs/adr-cst-substrate.md).

### Windows and OneDrive

If your Documents folder is redirected to OneDrive, `kicad-cli` can die at
startup with an access violation (exit `3221225477`) because it cannot create
its `KiCad` subfolder there. Every invocation fails, including
`kicad-cli --version`. Point KiCad's own `KICAD_DOCUMENTS_HOME` at a directory
that is not synced.

## Tool reference

<details>
<summary><b>Schematic</b> (42 tools)</summary>

**Read**

| Tool | Description |
|------|-------------|
| `get_schematic_summary` | Get item counts for a schematic sheet |
| `list_schematic_components` | List all components (symbols) on a sheet |
| `list_schematic_labels` | List all net labels on a sheet |
| `list_schematic_wires` | List all wires on a sheet |
| `list_schematic_global_labels` | List all global labels on a sheet |
| `list_schematic_hierarchical_labels` | List all hierarchical labels on a sheet |
| `list_schematic_sheets` | List all hierarchical sheet blocks on a sheet |
| `list_schematic_junctions` | List all junctions on a sheet |
| `list_schematic_no_connects` | List all no-connect flags on a sheet |
| `list_schematic_bus_entries` | List all bus entries on a sheet |
| `get_symbol_pins` | Get pin info for a symbol in the schematic's `lib_symbols` |
| `get_pin_positions` | Get absolute pin positions for a placed component, accounting for rotation and mirror |
| `get_net_connections` | Get all connections for a named net (multi-hop BFS wire tracing) |
| `list_unconnected_pins` | List unconnected pins from ERC data |

**Write**

| Tool | Description |
|------|-------------|
| `place_component` | Place a component in the schematic |
| `remove_component` | Remove a component by reference designator |
| `move_component` | Move a placed component to a new position |
| `set_component_property` | Set any property (Value, Reference, Footprint, ...) on a placed component |
| `add_lib_symbol` | Load a symbol definition from a `.kicad_sym` library into the schematic |
| `add_wires` | Add one or more wires between points, auto-creating junctions on T-connections |
| `remove_wire` | Remove a wire segment |
| `add_junctions` | Add one or more junction dots |
| `remove_junction` | Remove a junction dot |
| `add_label` | Add a net label at a position |
| `remove_label` | Remove a net label or global label |
| `add_global_label` | Add a global net label visible across all sheets |
| `add_hierarchical_label` | Add a hierarchical label for sheet-to-sheet connections |
| `remove_hierarchical_label` | Remove a hierarchical label by name or UUID |
| `modify_hierarchical_label` | Modify text, shape, or position of a hierarchical label |
| `add_power_symbol` | Place a power symbol (VCC, GND, +3V3, ...) with auto PWR_FLAG |
| `add_text` | Add a text annotation to the schematic |
| `remove_text` | Remove text annotation(s) by content, optionally filtered by position |
| `wire_pins_to_net` | Wire one or more pins to a named net |
| `connect_pins` | Wire two component pins together |
| `auto_place_decoupling_cap` | Automatically place a decoupling capacitor near an IC |
| `no_connect_pin` | Place a no-connect flag on an unused pin (idempotent) |
| `remove_no_connect` | Remove no-connect flag(s) from a pin |
| `set_page_size` | Set the schematic page size |

**Analysis and export**

| Tool | Description |
|------|-------------|
| `run_erc` | Run Electrical Rules Check, with `project_path` support for hierarchies |
| `export_schematic` | Export schematic to PDF, SVG, or DXF |
| `export_netlist` | Export the schematic netlist |
| `export_bom` | Export a Bill of Materials as CSV |

</details>

<details>
<summary><b>PCB</b> (34 tools)</summary>

**Read**

| Tool | Description |
|------|-------------|
| `list_pcb_footprints` | List all footprints on the board |
| `list_pcb_traces` | List all traces on the board |
| `list_pcb_nets` | List all nets on the board |
| `list_pcb_zones` | List all zones on the board |
| `list_pcb_layers` | List all layers on the board |
| `list_pcb_graphic_items` | List all graphic items on the board |
| `get_board_info` | Board summary: footprint count, trace count, net count, thickness |
| `get_footprint_pads` | Get pad info for a placed footprint |
| `get_footprint_bounds` | Get the board-coordinate bounding box of a placed footprint |

**Write**

| Tool | Description |
|------|-------------|
| `place_footprint` | Place a footprint on the board |
| `move_footprint` | Move a footprint to a new position |
| `remove_footprint` | Remove a footprint by reference designator |
| `add_trace` | Add a trace segment between two points |
| `remove_traces` | Remove trace segments matching filters |
| `set_trace_width` | Change the width of existing traces |
| `remove_dangling_tracks` | Detect and remove trace segments with unconnected endpoints |
| `add_via` | Add a via at a position |
| `add_thermal_vias` | Add a grid of thermal vias under a footprint pad |
| `add_pcb_text` | Add text to the board (silkscreen, fab layer, ...) |
| `add_pcb_line` | Add a graphic line (edge cuts, silkscreen, ...) |
| `add_copper_zone` | Create an unfilled copper zone |
| `add_keepout_zone` | Create a keep-out zone restricting tracks, vias, pads, pours, or footprints |
| `fill_zones` | Fill all copper zones on the board |
| `set_net_class` | Create or update a net class with design rules |
| `update_pcb_from_schematic` | Import or sync the schematic netlist onto the board (footprints and pad nets) |

**Analysis and export**

| Tool | Description |
|------|-------------|
| `run_drc` | Run Design Rules Check on the board |
| `check_placement` | Check whether a proposed footprint position hits a keep-out zone or the board edge |
| `validate_board` | Check every placed footprint against keep-out zones and the board edge |
| `autoroute_pcb` | Autoroute traces using the Freerouting autorouter |
| `export_pcb` | Export board layers to PDF, SVG, or DXF |
| `export_gerbers` | Export Gerber files, all layers or a specific list |
| `export_3d` | Export a 3D model (STEP/STL/GLB) or render the 3D view to PNG |
| `export_positions` | Export a component position file for pick and place |
| `export_ipc2581` | Export in IPC-2581 format for manufacturing data exchange |

</details>

<details>
<summary><b>Project</b> (24 tools)</summary>

**Scaffolding**

| Tool | Description |
|------|-------------|
| `create_project` | Create a KiCad 9 project (`.kicad_pro`, `.kicad_prl`, `.kicad_sch`) |
| `create_schematic` | Create a blank schematic file |
| `create_symbol_library` | Create a blank symbol library file |
| `create_sym_lib_table` | Create a `sym-lib-table` file |

**Sheet management**

| Tool | Description |
|------|-------------|
| `add_hierarchical_sheet` | Add a hierarchical sheet with matching labels in the child |
| `remove_hierarchical_sheet` | Remove a hierarchical sheet block from a parent |
| `modify_hierarchical_sheet` | Modify sheet name, file, width, or height |
| `move_hierarchical_sheet` | Move a sheet block to a new position, pins included |
| `add_sheet_pin` | Add a pin to an existing hierarchical sheet block |
| `remove_sheet_pin` | Remove a pin from a hierarchical sheet block |
| `duplicate_sheet` | Duplicate a sheet, copying the child file with new UUIDs |
| `reorder_sheet_pages` | Reorder sheets by specifying the desired UUID order |

**Hierarchy inspection**

| Tool | Description |
|------|-------------|
| `is_root_schematic` | Check whether a schematic is the root or a sub-sheet |
| `list_hierarchy` | List the full sheet hierarchy tree from root |
| `get_sheet_info` | Get sheet details with pin and label matching status |
| `validate_hierarchy` | Check for orphaned labels and pins, direction mismatches, duplicate refs |

**Cross-sheet analysis**

| Tool | Description |
|------|-------------|
| `trace_hierarchical_net` | Trace a net across the hierarchy through pins and labels |
| `list_cross_sheet_nets` | List all nets crossing sheet boundaries |
| `get_symbol_instances` | List symbol instances from the root schematic |

**Annotation, export, utilities**

| Tool | Description |
|------|-------------|
| `annotate_schematic` | Auto-assign reference designators, respecting hierarchy |
| `export_hierarchical_netlist` | Export a netlist with hierarchy info (requires `kicad-cli`) |
| `flatten_hierarchy` | Flatten a hierarchical schematic into a single sheet |
| `run_jobset` | Run a KiCad jobset file |
| `get_version` | Get KiCad version information |

</details>

<details>
<summary><b>Symbol and footprint libraries</b> (9 tools)</summary>

**Symbol**

| Tool | Description |
|------|-------------|
| `list_lib_symbols` | List all symbols in a `.kicad_sym` library |
| `get_symbol_info` | Get detailed pin and property info for a library symbol |
| `add_symbol` | Add a new symbol to a `.kicad_sym` library |
| `export_symbol_svg` | Export a symbol library to SVG images |
| `upgrade_symbol_lib` | Upgrade a symbol library to the current KiCad format |

**Footprint**

| Tool | Description |
|------|-------------|
| `list_lib_footprints` | List all footprints in a `.pretty` library directory |
| `get_footprint_info` | Get pad and outline details for a `.kicad_mod` file |
| `export_footprint_svg` | Export a footprint to SVG |
| `upgrade_footprint_lib` | Upgrade a footprint library to the current KiCad format |

</details>

## Debugging

Drive the servers by hand with the
[MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector uvx --from mcp-server-kicad mcp-server-kicad
```

## Contributing

Issues and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for development setup, and
[docs/adr-cst-substrate.md](docs/adr-cst-substrate.md) for the architecture
decisions behind the byte-preserving write path.

- [Report a bug](https://github.com/ProductOfAmerica/mcp-server-kicad/issues/new)
- [Browse open issues](https://github.com/ProductOfAmerica/mcp-server-kicad/issues)
- [Security policy](SECURITY.md)

## License

[MIT](LICENSE)
