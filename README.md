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
[![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue?style=flat-square)](LICENSE)

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
  auto_place_decoupling_cap    place C3 and wire both pins to their nets
  run_erc                      report what the change broke

"Now push the netlist to the board and route it."

  update_pcb_from_schematic    sync footprints and pad nets onto the PCB
  autoroute_pcb                route with Freerouting
  run_drc                      clearance, width, and unconnected checks
```

Every tool works on the real file, and a write changes only the bytes you asked
it to. An edit that cannot be done correctly is refused with the file intact.
KiCad itself writes a design file in just two places: the library upgrades, and
`autoroute_pcb`'s routed copy, a new board that leaves yours untouched.

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
itself; you need KiCad installed, plus Java 25 or newer for `autoroute_pcb`.

Desktop does not run from your project directory, so fill in the schematic,
board, and export paths it asks for. Leave the `kicad-cli` field blank unless
KiCad is installed somewhere unusual. The design skills are Claude Code only.

[latest]: https://github.com/ProductOfAmerica/mcp-server-kicad/releases/latest

### Everything else

```bash
pip install mcp-server-kicad
```

Then point your MCP client at the unified server, and name your schematic,
board, and export folder in `env` by absolute path, so tools find them without
being told on every call:

```json
{
  "mcpServers": {
    "kicad": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad"],
      "env": {
        "KICAD_SCH_PATH": "/path/to/project/project.kicad_sch",
        "KICAD_PCB_PATH": "/path/to/project/project.kicad_pcb",
        "KICAD_OUTPUT_DIR": "/path/to/project"
      }
    }
  }
}
```

That single entry registers all 109 tools. A client that starts the server in
your project's folder can leave `env` out: the server then takes the paths from
the single `.kicad_pro` there (see [How paths resolve](#how-paths-resolve)).
Prefer to split the tools across five smaller servers? See
[Configuration](#configuration).

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
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-schematic"]
    },
    "kicad-pcb": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-pcb"]
    },
    "kicad-symbol": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-symbol"]
    },
    "kicad-footprint": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-footprint"]
    },
    "kicad-project": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-project"]
    }
  }
}
```

Give each entry the `env` block from [Everything else](#everything-else), with
`KICAD_SYM_LIB` or `KICAD_FP_LIB` for the library servers, unless your client
starts them in the project's folder.

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

- **Python 3.11+**
- **KiCad 9.x or 10.x**, only for the tools that call KiCad:
  - `kicad-cli`: ERC, DRC, exports, jobsets, `get_version`, the library
    upgrades and `update_pcb_from_schematic`
  - `pcbnew` Python bindings: `fill_zones` and `autoroute_pcb` (which also
    needs Java 25 or newer; see [The Freerouting download](#the-freerouting-download))
  - KiCad's stock libraries: placing a stock symbol or footprint

  Every other tool parses files directly and needs no KiCad.

CI covers Linux (KiCad 9 and 10), macOS and Windows (KiCad 10), and Python
3.11 to 3.14.

### The Freerouting download

`autoroute_pcb` runs [Freerouting](https://github.com/freerouting/freerouting).
Unless `FREEROUTING_JAR` names a jar, its first call downloads Freerouting 2.5.0,
`freerouting-2.5.0.jar` (65 MB, needs Java 25 or newer), from
<https://github.com/freerouting/freerouting/releases/download/v2.5.0/freerouting-2.5.0.jar>
and caches it as `~/.local/share/mcp-server-kicad/freerouting-2.5.0.jar`
(`%USERPROFILE%\.local\share\mcp-server-kicad\` on Windows). Its SHA-256 is
checked against the value pinned in this package before the download is saved
and every time the cached copy is used. A download that does not match is not
saved; a cached copy that does not match is not run, and is downloaded again.

Freerouting is run with `--gui.enabled=false` and
`--usage_and_diagnostic_data.disable_analytics=true`, the settings its
documentation gives for running headless and for not sending usage data, so it
opens no window and its analytics stay off. When it starts, Freerouting still
asks GitHub for its newest release; it has no setting to turn that off.

To skip the download, set `FREEROUTING_JAR` to the absolute path of a Freerouting
jar you trust. That jar is used as given, with no hash check. A relative path, or
one that names no file, is ignored and the download goes ahead. Earlier releases
cached an unpinned `freerouting.jar` in the same folder; it is no longer read and
can be deleted.

### Finding your KiCad install

A stock install needs no configuration. `kicad-cli` is found on `PATH`, then in
`/Applications/KiCad/KiCad.app` on macOS, then under `Program Files` and
`AppData` on Windows, newest first. The stock libraries and `pcbnew`'s Python
are found relative to it. For an unusual install:

| Variable | Overrides |
|----------|-----------|
| `KICAD_CLI_PATH` | the `kicad-cli` executable, and everything derived from its location |
| `KICAD_SYMBOL_DIR` | the directory holding the stock `.kicad_sym` libraries |
| `KICAD_PYTHON` | the interpreter used for `pcbnew` (`fill_zones`, `autoroute_pcb`), as an absolute path or a name on `PATH` |

Without `kicad-cli`, the tools that need it stay listed and fail with a message
naming `KICAD_CLI_PATH`.

### KiCad 10 files

The server's own reads and writes handle KiCad 9 and 10 schematics, boards,
symbol libraries and footprints alike, and never change bytes you did not ask to
change (see
[docs/adr-cst-substrate.md](docs/adr-cst-substrate.md)). Work handed to KiCad
itself only succeeds if your install can load the file, and KiCad 9 cannot load
a KiCad 10 board:

- **`fill_zones` and `autoroute_pcb`** refuse a KiCad 10 board on a KiCad 9
  `pcbnew` before starting; install KiCad 10 or point `KICAD_PYTHON` at one.
  `autoroute_pcb` on a KiCad 9 board through KiCad 10's `pcbnew` works but
  warns that the routed copy comes back in KiCad 10 format. It never touches
  the original board.
- **`upgrade_symbol_lib` and `upgrade_footprint_lib`** let KiCad rewrite your
  files, by design. Each file is replaced atomically and only if it changed, so
  an interrupted upgrade leaves none damaged. A backup goes to `<name>.bak`,
  overwritten every run.

### Windows: Controlled Folder Access

Defender's Controlled Folder Access blocks unrecognised apps from writing under
Documents, where KiCad keeps its data. `kicad-cli` then crashes at startup
(exit `3221225477`) on every call, `--version` included, and the error looks
like a missing file: `error 2: The system cannot find the file specified`.

The server works around this: it retries once with `KICAD_DOCUMENTS_HOME`
pointed at an unprotected local folder and keeps it for the session. Set
`KICAD_DOCUMENTS_HOME` yourself to pick the folder (this turns the retry off).

**It cannot fix your project files.** If your project lives under Documents,
the write tools fail the same way. Move the project, or allow the tools under
Windows Security > Virus & threat protection > Ransomware protection > Allow an
app through Controlled folder access.

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
| `list_unconnected_pins` | List the pins ERC reports as not connected, positions in millimetres; a sub-sheet gets its own, from its project's root |

**Write**

| Tool | Description |
|------|-------------|
| `place_component` | Place a component in the schematic |
| `remove_component` | Remove a component by reference designator |
| `move_component` | Move or turn a placed component. Its fields travel with it; wires stay where they are, as with KiCad's own Move, and the result names every pin whose connections changed. Called at the symbol's own position it turns the symbol in place, without snapping |
| `set_component_property` | Set any property (Value, Reference, Footprint, ...) on a placed component |
| `add_lib_symbol` | Load a symbol definition from a `.kicad_sym` library into the schematic |
| `add_wires` | Add one or more wires between points as KiCad's wire tool does: a junction where three or more wires or pins meet, the wires split there |
| `remove_wire` | Remove a wire segment |
| `add_junctions` | Add one or more junction dots where three or more wires or pins meet, splitting the wires through them; refused anywhere else |
| `remove_junction` | Remove a junction dot, merging the wires it split as KiCad's delete does |
| `add_label` | Add a net label at a position |
| `remove_label` | Remove a net label or global label |
| `add_global_label` | Add a global net label visible across all sheets |
| `add_hierarchical_label` | Add a hierarchical label for sheet-to-sheet connections |
| `remove_hierarchical_label` | Remove a hierarchical label by name or UUID |
| `modify_hierarchical_label` | Modify text, shape, or position of a hierarchical label |
| `add_power_symbol` | Place a power symbol (VCC, GND, +3V3, PWR_FLAG, ...) |
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
| `run_erc` | Run Electrical Rules Check, positions in millimetres; a sub-sheet is checked from its project's root and gets its own violations, with `project_path` to name the project; a run that makes no report raises and leaves `output_dir` as it was |
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
| `update_pcb_from_schematic` | Import or sync the schematic netlist onto the board: footprints, their fields and footprint filters, DNP and BOM flags (position-file flag too on KiCad 10), sheet linkage and pad nets; libraries through the project's and the user's `fp-lib-table` |

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
| `export_positions` | Export a pick-and-place position file as CSV, in millimetres |
| `export_ipc2581` | Export in IPC-2581 format for manufacturing data exchange |

</details>

<details>
<summary><b>Project</b> (24 tools)</summary>

**Scaffolding**

| Tool | Description |
|------|-------------|
| `create_project` | Create a KiCad 9 project (`.kicad_pro` and the root `.kicad_sch`; no `.kicad_prl`) |
| `create_schematic` | Create a blank schematic file |
| `create_symbol_library` | Create a blank symbol library file |
| `create_sym_lib_table` | Create a `sym-lib-table` file |

**Sheet management**

| Tool | Description |
|------|-------------|
| `add_hierarchical_sheet` | Add a hierarchical sheet with matching labels in the child, all checked before anything is written |
| `remove_hierarchical_sheet` | Remove a hierarchical sheet block from a parent |
| `modify_hierarchical_sheet` | Modify sheet name, file, width, or height; a resize keeps pins on their edges and moves the wires on them |
| `move_hierarchical_sheet` | Move a sheet block to a new position, pins included |
| `add_sheet_pin` | Add a pin to the left or right edge of a sheet block, at the first free slot |
| `remove_sheet_pin` | Remove a pin from a hierarchical sheet block |
| `duplicate_sheet` | Duplicate a sheet: a copy of the block and of the child file, with the lowest free page and parts numbered apart from the originals; a sheet with sub-sheets of its own is refused |
| `reorder_sheet_pages` | Put sheets in the page order given by rewriting their page numbers, the order KiCad lists them in; call it on the root schematic |

**Hierarchy inspection**

| Tool | Description |
|------|-------------|
| `is_root_schematic` | Check whether a schematic is its project's root or one of its sheets, including a sheet in a folder below the project's; a file no sheet uses is a root |
| `list_hierarchy` | List the full sheet hierarchy tree from root |
| `get_sheet_info` | Get sheet details with pin and label matching status |
| `validate_hierarchy` | Check every sheet for orphaned labels and pins, direction mismatches, duplicate refs, junctions and bus entry ends left inside wires, and no-connect flags and wire ends left off the pins of mirrored, rotated parts, with the call that repairs each where a tool can |

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
| `flatten_hierarchy` | Flatten a hierarchical schematic into a new single-sheet file whose netlist is the hierarchy's; refuses, writing nothing, where it could not keep it (bus names across sheets, for one) |
| `run_jobset` | Run a KiCad jobset against the project beside it; an error when nothing ran or a job failed |
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
npx -y @modelcontextprotocol/inspector uvx --from mcp-server-kicad mcp-server-kicad
```

## Contributing

Issues and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for development setup, and
[docs/adr-cst-substrate.md](docs/adr-cst-substrate.md) for the architecture
decisions behind the byte-preserving write path.

- [Report a bug](https://github.com/ProductOfAmerica/mcp-server-kicad/issues/new)
- [Browse open issues](https://github.com/ProductOfAmerica/mcp-server-kicad/issues)
- [Security policy](SECURITY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)

## License

[GPL-3.0-or-later](LICENSE). Releases up to and including 0.20.1 were MIT-licensed; see [NOTICE](NOTICE).
