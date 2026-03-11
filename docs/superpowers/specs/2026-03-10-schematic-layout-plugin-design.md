# Schematic Layout Plugin — Design Spec

**Date:** 2026-03-10
**Status:** Draft

---

## Problem

The MCP server tools work correctly, but the LLM makes poor layout decisions
when designing schematics. It picks arbitrary coordinates, defaults to
`wire_pins_to_net` everywhere (stamping a net label on every pin), and
produces schematics with overlapping labels, no signal-flow structure, and
visual spaghetti.

This is a knowledge problem, not a tooling problem. The LLM needs layout
conventions in context before it starts placing components.

## Decision: Plugin Architecture

### Why not expand `instructions`?

The schematic server's `instructions` field (in the `FastMCP` constructor) is
a single always-on string delivered via the MCP protocol. It works for
critical tool-usage rules ("never edit .kicad_sch directly") but is wrong
for layout knowledge because:

- It's monolithic — can't conditionally load different knowledge for different
  tasks (design vs. review vs. modification).
- It can't be iterated independently of a PyPI release.
- Layout conventions may grow into multiple skills over time (power supply
  layout, digital design, mixed-signal, etc.).

### Why not a standalone Claude Code skill?

The MCP server is distributed via PyPI. Users `pip install mcp-server-kicad`
and configure it in their MCP client config. A standalone skill in
`.claude/skills/` would require separate manual setup — users would get the
tools but not the knowledge unless they know to also install the skill.

### Why a plugin?

A Claude Code plugin bundles MCP server configuration + skills in one
installable unit. Users install the plugin and get everything: the tools,
the layout knowledge, and any future skills (ERC workflow, PCB layout, etc.).

Trade-off: this is Claude Code-specific. Other MCP clients won't get the
skills. This is acceptable — Claude Code is the primary client, and the
`instructions` field already covers the critical tool-usage rules for all
clients.

## Distribution

The MCP server and the plugin have **independent distribution channels**:

- **MCP server** (Python package): distributed via PyPI. Users who only want
  the tools (on any MCP client) run `uvx --from mcp-server-kicad ...` or
  `pip install mcp-server-kicad`. The plugin files (`.claude-plugin/`,
  `skills/`, `.mcp.json`) are not included in the PyPI wheel — they are not
  part of the `mcp_server_kicad` Python package.

- **Plugin** (Claude Code): distributed via git. Users install the plugin
  from the GitHub repository, which gives them the MCP server configuration
  + skills. The plugin's `.mcp.json` uses `uvx` to fetch the server from
  PyPI at runtime.

### Installation

Users install the plugin by adding the repository to a Claude Code
marketplace, then installing from it:

```bash
claude plugin marketplace add ProductOfAmerica/mcp-server-kicad
claude plugin install kicad
```

Or for project-scoped installation, the repository URL can be referenced
directly. The README should be updated with plugin installation instructions
alongside the existing MCP server configuration instructions.

## Architecture

### Repository Structure

The plugin files live in the same repo as the MCP server source. One repo,
one version, tools and skills stay in sync.

```
mcp-server-kicad/
├── mcp_server_kicad/              # Python package (unchanged)
│   ├── schematic.py               # 30 schematic tools
│   ├── pcb.py                     # 19 PCB tools
│   ├── symbol.py                  # 5 symbol tools
│   ├── footprint.py               # 4 footprint tools
│   ├── project.py                 # 7 project tools
│   └── _shared.py                 # Shared utilities
├── .claude-plugin/
│   └── plugin.json                # Plugin manifest
├── .mcp.json                      # MCP server configs (uses uvx)
├── skills/
│   └── schematic-design/
│       └── SKILL.md               # Layout conventions skill
├── pyproject.toml
├── tests/
└── ...
```

### Plugin Manifest (`.claude-plugin/plugin.json`)

```json
{
  "name": "kicad",
  "description": "KiCad EDA tools and design skills for Claude Code",
  "author": {
    "name": "ProductOfAmerica"
  },
  "repository": "https://github.com/ProductOfAmerica/mcp-server-kicad",
  "homepage": "https://github.com/ProductOfAmerica/mcp-server-kicad",
  "license": "MIT",
  "keywords": ["kicad", "eda", "schematic", "pcb", "electronics"]
}
```

Note: `version` is intentionally omitted. The plugin is distributed via git
(not PyPI), so versioning follows git tags/commits. The MCP server has its
own version in `pyproject.toml` managed by the release workflow — keeping a
separate version in `plugin.json` would require synchronization logic for no
benefit.

### MCP Server Config (`.mcp.json`)

All five servers, using `uvx` to auto-download from PyPI. No `cwd` — the
server inherits Claude Code's working directory and auto-detects `.kicad_pro`
files via its existing 3-tier path resolution (auto-detect → env vars →
tool parameters).

```json
{
  "kicad-schematic": {
    "type": "stdio",
    "command": "uvx",
    "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-schematic"]
  },
  "kicad-pcb": {
    "type": "stdio",
    "command": "uvx",
    "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-pcb"]
  },
  "kicad-symbol": {
    "type": "stdio",
    "command": "uvx",
    "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-symbol"]
  },
  "kicad-footprint": {
    "type": "stdio",
    "command": "uvx",
    "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-footprint"]
  },
  "kicad-project": {
    "type": "stdio",
    "command": "uvx",
    "args": ["--from", "mcp-server-kicad", "mcp-server-kicad-project"]
  }
}
```

### Schematic Design Skill (`skills/schematic-design/SKILL.md`)

Invoked as `/kicad:schematic-design` by the user, or automatically by
Claude Code when it detects schematic design intent.

```markdown
---
name: schematic-design
description: >
  Use when designing a KiCad schematic from scratch, laying out components
  on a schematic sheet, placing symbols and wiring them together, or when
  the user asks about schematic layout, component placement, or wiring
  strategy. Provides conventions for placement spacing, signal flow
  direction, wiring tool selection, and functional stage organization.
---

# KiCad Schematic Design Conventions

Follow these conventions when placing components and wiring a new schematic.

## Signal Flow and Stage Layout

- **Left-to-right** signal flow: inputs on the left, outputs on the right.
- **Top-to-bottom** voltage flow: higher voltages near the top of the sheet,
  lower voltages and ground toward the bottom.
- Group components into **functional stages** along the signal path.
  Example: input protection → buck regulation → LDO → output.
- Arrange stages as **vertical bands** (left-to-right) or **horizontal rows**
  (top-to-bottom by voltage), depending on the design. A power supply with
  cascading voltage levels flows naturally top-to-bottom; a signal chain
  flows left-to-right.
- Within each stage, place the main active component (IC, MOSFET) on the
  signal path. Place supporting passives (bypass caps, resistors) directly
  adjacent — input-side passives to the left, output-side to the right.

## Placement Spacing

All coordinates are on the 1.27mm grid (auto-snapped by the tools).
Reference designators and values are placed 3.81mm above/below the
component center, so each component's text zone spans ~7.62mm vertically.

Minimum spacing:
- **Vertical between components**: 10.16mm (8 grid units). Prevents
  reference/value text overlap. Use 12.7mm (10 grid units) for comfort.
- **Horizontal between passives in a row**: 12.7mm (10 grid units).
- **IC to its supporting passives**: 25.4mm (20 grid units) horizontal.
  Input caps 25.4mm to the left, output caps 25.4mm to the right.
- **Between functional stages**: 25.4–50.8mm gap (vertical or horizontal,
  depending on layout direction).

Starting position: on the default A4 landscape sheet (297x210mm), place the
first component (input connector or leftmost element) at approximately
**(25, 50)** to clear the page margin (10mm) and leave room for sheet
labels. Scale proportionally for other page sizes.

Title block clearance: the title block occupies ~108x32mm at the
bottom-right corner. On A4 landscape (297x210mm), keep components
within X < 180mm and Y < 175mm to stay clear.

## Wiring Strategy

**Use `connect_pins`** (direct Manhattan wire) when:
- Two pins are within ~25mm of each other.
- The wire path is visually obvious (one L-shaped segment).
- Example: fuse output to adjacent TVS cathode, decoupling cap to IC
  power pin.

**Use `wire_pin_to_label`** (single pin to net label) when:
- Connecting one pin to a named net that other components also reference.
- Example: connecting an IC's VCC pin to the 5V_REL rail.

**Use `wire_pins_to_net`** (batch: multiple pins to one net label) when:
- A net connects 3 or more components that are not all adjacent.
- The net spans across functional stages (e.g., a 5V rail feeding
  multiple ICs).
- Power and ground rails — always use net labels, never daisy-chain.

**Do NOT default to `wire_pins_to_net` for everything.** Direct wires
between adjacent components produce cleaner, more readable schematics.
Net labels everywhere turns the schematic into a disconnected parts list.

## Power and Ground

- Use `add_power_symbol` for VCC, GND, and named power rails.
  Power symbols pointing **up** for positive rails, **down** for ground.
- Use `wire_pins_to_net` for custom power nets (VIN_PROT, 5V_REL, etc.)
  that don't have standard power symbols.
- Place **decoupling capacitors visually adjacent** to the IC they serve,
  connected via the same net label — not with long wires across the sheet.
- Add `PWR_FLAG` on every power net that would otherwise trigger
  "power pin not driven" ERC errors (regulator outputs, battery terminals).

## Naming

- Use **uppercase descriptive names** for nets: VIN_PROT, 5V_REL, SPI_MOSI.
- Active-low signals: suffix with `_N` (e.g., RESET_N, CS_N).
- Do not use generic names like NET1 or WIRE3.

## Verification

After completing placement and wiring:
1. Run `run_erc` to check for violations.
2. Fix "power pin not driven" with PWR_FLAG symbols.
3. Fix unconnected pins with `wire_pin_to_label` or `no_connect_pin`.
4. Re-run ERC until clean.
```

## What Changes in Existing Code

### `instructions` field — no change

The existing `instructions` string in `schematic.py` stays as-is. It covers
tool-usage rules and workflows that apply to ALL MCP clients. The layout
knowledge is additive (in the skill), not a replacement.

### `TODO.md` — update

Replace the "Schematic Layout Quality — Claude Skill (not MCP)" section with
a reference to the plugin approach. The current text says "(not a plugin —
skills are lighter weight)" which contradicts this design. Update to note
that the layout conventions are delivered as a skill bundled inside the
`kicad` Claude Code plugin.

### `README.md` — update

Add plugin installation instructions alongside the existing MCP server
configuration section. Document both distribution paths: PyPI for standalone
MCP server usage, plugin for Claude Code users who want tools + skills.

## Future Skills

The plugin architecture supports adding more skills without any server
changes:

- `skills/erc-workflow/SKILL.md` — ERC diagnosis and fix patterns
- `skills/pcb-layout/SKILL.md` — PCB placement and routing conventions
- `skills/symbol-authoring/SKILL.md` — custom symbol creation guidelines
- `skills/mixed-signal/SKILL.md` — analog/digital separation conventions

Each gets its own directory under `skills/` and is namespaced as
`/kicad:<skill-name>`.

## Sources

Layout convention numbers derived from:

- **KiCad Library Convention (KLC)**: S3.2 (text sizing), S4.1 (pin grid)
- **KiCad defaults**: 1.27mm grid, 1.27mm text, 2.54mm pin length, 3.81mm
  property offset (from MCP server `place_component`)
- **IPC-2612**: signal flow direction (left-to-right, top-to-bottom voltage)
- **KiCad source** (`default_values.h`): junction diameter, wire width,
  no-connect size
- **Empirical**: spacing values validated against the L1 I/O Node power
  supply schematic plan (27 components, 4 stages)
