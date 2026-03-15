# Release Notes

## v0.7.9
### Fixes
- Add TYPE_CHECKING import for Board to satisfy pyright
- Reset displaced footprint text after Freerouting SES import
- Parse DRC JSON correctly and rewrite set_net_class for KiCad 9

## v0.7.8
### Fixes
- Handle already-corrupted boards with empty `(tstamp )` tokens

## v0.7.7
### Fixes
- Handle KiCad 9 uuid/tstamp and net class API changes

## v0.7.6
### Fixes
- Use newlines instead of semicolons in set_net_class script generation

## v0.7.5
_(No user-facing changes — version sync only)_

## v0.7.4
### Features
- Add set_net_class and remove_dangling_tracks MCP tools
- Add add_thermal_vias MCP tool
- Add set_trace_width and remove_traces MCP tools
- Add add_copper_zone and fill_zones MCP tools
- Add helper utilities: _filter_segments, _find_net, zone/subprocess imports, FillSettings/Hatch/ZonePolygon imports

### Improvements
- Add post-autoroute tools and workflow to pcb-layout skill
- Add post-autoroute checks to verification skill

## v0.7.3
### Fixes
- Try system python for pcbnew when running inside uvx/venv

## v0.7.2
### Fixes
- Resolve pcb_path to absolute for autoroute subprocess calls

## v0.7.1
_(No user-facing changes — version sync only)_

## v0.7.0
### Features
- Add autoroute_pcb MCP tool for automatic PCB routing via Freerouting
- Add _freerouting.py helper module

### Improvements
- Update GitHub Actions to Node.js 24-compatible versions
- Remove redundant publish.yml workflow

### Fixes
- Add pyright type guards in freerouting tests
- Narrow jar_path type for pyright compatibility

## v0.6.1
_(No user-facing changes — version sync only)_

## v0.6.0
### Features
- Add schematic-plan skill for placement and wiring planning
- Add schematic plan reviewer agent prompt
- Add BOM reviewer agent prompt
- Rewrite using-kicad as pipeline enforcer with hard gates
- Add pre-flight checks and expanded export to pcb-layout skill
- Add hard gate enforcement and stuck escalation to verification skill
- Refactor schematic-design as plan executor with two modes
- Add exit gate, BOM artifact, and reviewer dispatch to circuit-design

### Fixes
- Restore MCP Tools section to circuit-design skill

## v0.5.12
### Fixes
- Add SymbolProjectInstance to auto-placed PWR_FLAG in wire_pins_to_net

## v0.5.11
### Features
- Add set_page_size tool
- Sub-sheet ERC auto-redirect
- Fix system library symbol round-trip

## v0.5.10
### Fixes
- Load PWR_FLAG from system library to eliminate ERC mismatch warnings

## v0.5.9
### Features
- Auto wire stubs on hierarchical sheets
- Auto PWR_FLAG on power_in nets

## v0.5.8
### Fixes
- Fix 5 schematic bugs: rotation, label deletion, fuzzy match, ref validation, bounds checking

## v0.5.7
_(No user-facing changes — version sync only)_

## v0.5.6
### Improvements
- Add complete MCP tool references to all 5 skills

## v0.5.5
### Fixes
- Inline guard hook to avoid CLAUDE_PLUGIN_ROOT env var bug

## v0.5.4
### Features
- Add PreToolUse hook to block Read/Write/Edit on KiCad files

## v0.5.3
_(No user-facing changes — version sync only)_

## v0.5.2
### Fixes
- Add CRITICAL-RULE to all skills to enforce MCP-only KiCad file access

## v0.5.1
### Fixes
- Add mcpServers to plugin.json for Claude Code plugin discovery

## v0.5.0
### Features
- Consolidate to unified MCP server, merge 6 tools (65 to 59)
- Add kiutils type stubs and fix Pyright errors
- Thread project_path through power/decoupling tools and add_hierarchical_sheet
- Add _resolve_hierarchy_path and project_path to place_component

### Improvements
- Add autouse kicad-cli validation for all generated schematics

### Fixes
- Add missing angle parameter to hierarchical sheet property positions

## v0.4.2
### Features
- Switch plugin MCP servers to uvx and sync plugin.json version in releases
- Add plugin skills and fix MCP server config
- Add Claude Code plugin with schematic-design skill
- Add ToolAnnotations to all 65 MCP tools
- Add input validation, stub collision avoidance, schematic info tool, and symbol authoring

### Fixes
- Apply ruff formatting and resolve lint errors in tests

## v0.4.1
### Improvements
- Expand MCP server instructions with usage rules and workflows

## v0.4.0
### Features
- Create kicad-symbol server with 4 library tools
- Create kicad-footprint server with 4 library tools
- Give kicad-project server its own FastMCP instance with jobset and version tools
- Consolidate 4 schematic list tools into list_schematic_items (later split into per-type tools: get_schematic_summary, list_schematic_components, list_schematic_labels, list_schematic_wires, list_schematic_global_labels, list_schematic_hierarchical_labels, list_schematic_sheets, list_schematic_junctions, list_schematic_no_connects, list_schematic_bus_entries)
- Consolidate 6 PCB list tools into list_pcb_items (later split into per-type tools: list_pcb_footprints, list_pcb_traces, list_pcb_nets, list_pcb_zones, list_pcb_layers, list_pcb_graphic_items)
- Absorb ERC tools and consolidated schematic exports into kicad-schematic
- Absorb DRC and consolidated PCB export tools into kicad-pcb
- Add export_pcb_dxf and export_ipc2581 tools to PCB server

### Breaking Changes
- Remove edit_component_value and set_component_footprint (use set_component_property)
- Remove singular add_wire, add_junction, wire_pin_to_label (batch variants are the canonical API)
- Reorganize from 3 servers to 5 domain-aligned servers (82 to 63 tools)

### Fixes
- Handle missing kicad-cli in run_jobset and get_version

## v0.3.1
### Improvements
- Register publish workflow with GitHub Actions

## v0.3.0
### Features
- Add auto_place_decoupling_cap composite tool
- Add add_power_rail composite tool
- Add wire_pins_to_net batch wiring tool
- Add list_unconnected_pins tool (ERC-based)
- Add get_net_connections tool
- Add remove_junction tool
- Add set_component_property tool
- Add set_component_footprint tool
- Add remove_label and remove_wire tools
- Annotate ERC sub-sheet hierarchical label errors as expected
- place_component suggests similar symbols when lib_id not found
- wire_pin_to_label warns on conflicting label at endpoint
- add_power_symbol no longer creates duplicate when placing PWR_FLAG
- create_project now creates root .kicad_sch (matches KiCad behavior)
- Auto-embed lib_symbols from system KiCad libraries in place_component
- Add _resolve_system_lib helper for KiCad system library lookup

### Fixes
- Handle None property IDs and skip tests requiring system libs
- CI lint E741 and decoupling cap tests without system libs
- Lint cleanup and use _resolve_system_lib in add_power_symbol

### Improvements
- Add pre-commit hooks for ruff lint + format
- Consolidate workflows into ci.yml + release.yml

## v0.2.0
### Features
- Add create_project tool
- Add create_schematic tool
- Add create_symbol_library tool
- Add create_sym_lib_table tool
- Add add_hierarchical_sheet tool
- Add no_connect_pin tool
- Add connect_pins high-level routing tool
- Add wire_pin_to_label high-level routing tool
- Auto-place PWR_FLAG when adding power symbols for ERC compliance

### Improvements
- Move _snap_grid to _shared for cross-module use
- Extract _transform_pin_pos and _get_pin_pos helpers from get_pin_positions

### Fixes
- Negate Y-axis in get_pin_positions for correct schematic coordinates

## v0.1.1
### Fixes
- Add instances block and grid snapping to place_component
- Add kicad-cli skipif guards and fix pyright config
- Add uv.lock, pyright CI step, and pypi environment
- Fix ruff lint errors and apply formatting

## v0.1.0
### Features
- Initial release
- Port schematic server (22 tools) and tests
- Port PCB server (17 tools) and tests
- Port export server (22 tools) and tests
- Add _shared.py with config resolution and TDD tests
- Scaffold mcp-server-kicad project
