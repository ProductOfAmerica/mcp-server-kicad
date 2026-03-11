# mcp-server-kicad v0.5.0 Refactoring Spec

## Goal

Prepare the KiCad MCP plugin for Anthropic's official Claude Code plugin marketplace by consolidating servers, removing redundant tools, and conditionally hiding CLI-dependent tools.

## Change 1: Unified Server

### Problem

Official plugins ship 1 MCP server. We ship 5 (`kicad-schematic`, `kicad-pcb`, `kicad-symbol`, `kicad-footprint`, `kicad-project`), each with its own `FastMCP` instance.

### Solution

Create `mcp_server_kicad/server.py` containing a single `FastMCP("kicad")` instance that registers all tools from all 5 modules.

**Registration pattern:** Each module exposes a `register(mcp, has_cli)` function that accepts an external `FastMCP` instance and registers its tools on it. The existing module-level `mcp` and `main()` remain for backwards compatibility.

**Tool name uniqueness:** All 59 tool names are already globally unique across the 5 modules. This invariant must be maintained; if a conflict arises, use the `name` parameter of `mcp.tool()` to disambiguate.

**Instructions string:** The unified `FastMCP("kicad")` instance gets a new combined instructions string summarizing all 5 modules' capabilities. Each standalone module's instructions remain unchanged for backwards compat.

**Entry points:**
- New: `mcp-server-kicad = "mcp_server_kicad.server:main"` (unified)
- Kept: all 5 existing entry points unchanged

**Config updates:**
- `.mcp.json`: single `kicad` server:
  ```json
  {
    "kicad": {
      "command": "uvx",
      "args": ["--from", "mcp-server-kicad", "mcp-server-kicad"]
    }
  }
  ```
- `.claude-plugin/plugin.json`: version bump to 0.5.0
- `pyproject.toml`: add unified entry point, bump version

## Change 2: Tool Merges (65 -> 59)

Six tools are removed, with their functionality absorbed into existing tools.

Actual tool counts by module: schematic (30), pcb (19), symbol (5), footprint (4), project (7) = 65 total. After removing 6: 59.

### 2a. Remove `add_no_connect(x, y)`

**Rationale:** `no_connect_pin(reference, pin_name)` is a strict superset — it resolves pin position automatically. The raw coordinate version adds no value since users always know the component reference, not raw coordinates.

**Action:** Delete `add_no_connect` function and its tests. No changes to `no_connect_pin`.

### 2b. Remove `add_power_rail(...)`

**Rationale:** `add_power_rail` is just `add_power_symbol()` + `wire_pins_to_net()` combined. The LLM can call those two tools sequentially.

**Action:** Delete `add_power_rail` function and its tests. No changes to `add_power_symbol` or `wire_pins_to_net`.

**Note:** `add_power_rail` auto-derives the net name from `lib_id` (e.g. `"power:VCC"` -> `"VCC"`) and forwards `project_path` to `add_power_symbol`. Skills/instructions should guide the LLM to: (1) extract the net name from `lib_id` when calling `wire_pins_to_net` separately, and (2) pass `project_path` to `add_power_symbol` when working in sub-sheets.

### 2c. Merge `get_schematic_info()` into `list_schematic_items(item_type="summary")`

**Rationale:** Two read tools for schematic metadata is redundant. `list_schematic_items` already accepts an `item_type` enum; adding `"summary"` as a new value absorbs `get_schematic_info`.

**Action:**
- Add `"summary"` branch to `list_schematic_items` that returns the same text output as `get_schematic_info`
- Delete `get_schematic_info` function
- Update tests to use `list_schematic_items(item_type="summary")`

### 2d. Merge `export_gerber(layer)` into `export_gerbers(layers=[...])`

**Rationale:** Single-layer and all-layer gerber export are the same operation with different scope.

**Action:**
- Add optional `layers: list[str] | None = None` parameter to `export_gerbers`
- When `layers` has exactly 1 entry: use `kicad-cli pcb export gerber --layers <layer>` (singular subcommand), return single-file metadata. `include_drill` is ignored in single-layer mode.
- When `layers` has multiple entries or is None: use `kicad-cli pcb export gerbers` (plural subcommand) with `--layers` filter if provided, return directory metadata
- Delete `export_gerber` function
- Update tests

### 2e. Merge `export_pcb_dxf(...)` into `export_pcb(format="dxf", ...)`

**Rationale:** DXF is another PCB export format alongside PDF and SVG.

**Action:**
- Add `"dxf"` to the format enum in `export_pcb`
- Add DXF-specific optional parameters: `output_units`, `exclude_refdes`, `exclude_value`, `use_contours`, `include_border_title` (all with defaults matching current behavior)
- These params are ignored for non-DXF formats
- Delete `export_pcb_dxf` function
- Update tests

### 2f. Merge `render_3d(...)` into `export_3d(format="render", ...)`

**Rationale:** 3D rendering is conceptually a 3D export to image format.

**Action:**
- Add `"render"` to the format enum in `export_3d` (alongside step/stl/glb)
- Add render-specific optional parameters: `width`, `height`, `side`, `quality` (with defaults matching current `render_3d`)
- These params are ignored for non-render formats
- When `format="render"`: use `kicad-cli pcb render` subcommand, output PNG
- Delete `render_3d` function
- Update tests

## Change 3: Conditional kicad-cli Tool Registration

### Problem

~20 tools shell out to `kicad-cli`. If KiCad isn't installed, these tools fail at runtime. Better to not register them at all.

### Solution

At startup, check `shutil.which("kicad-cli")`. If not found, skip registering CLI-dependent tools.

**Implementation:** In each module's `register(mcp, has_cli)` function, accept a `has_cli: bool` parameter. All tools that call `_run_cli()` are only registered when `has_cli=True`. This includes tools with various annotations (`_EXPORT`, `_READ_ONLY`, `_DESTRUCTIVE`) — the criterion is `_run_cli()` usage, not annotation type.

**Affected tools by module (all tools that call `_run_cli()`):**

Schematic (5): `export_schematic`, `export_netlist`, `export_bom`, `run_erc`, `list_unconnected_pins`
PCB (6 after merges): `export_pcb`, `export_gerbers`, `export_3d`, `export_positions`, `export_ipc2581`, `run_drc`
Symbol (2): `export_symbol_svg`, `upgrade_symbol_lib`
Footprint (2): `export_footprint_svg`, `upgrade_footprint_lib`
Project (2): `run_jobset`, `get_version`
Total: 17 CLI-dependent tools

**Detection:** One-time `shutil.which("kicad-cli")` check in `server.py` `main()` and in each module's `main()`.

## Version & Documentation Updates

- Bump version to 0.5.0 in `pyproject.toml` and `.claude-plugin/plugin.json`
- Update README.md tool table to reflect 59 tools and merged names
- Update skills/ files that reference removed tool names (`add_no_connect`, `add_power_rail`, `get_schematic_info`, `export_gerber`, `export_pcb_dxf`, `render_3d`)

## Constraints

- All existing tests must pass (with updates for removed/merged tools)
- `ruff check`, `ruff format`, and `pyright` must pass
- The 5 individual server entry points must still work
- Tool behavior unchanged — only consolidation and merge
- No new dependencies
