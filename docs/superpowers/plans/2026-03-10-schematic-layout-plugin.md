# Schematic Layout Plugin — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Claude Code plugin infrastructure to mcp-server-kicad — plugin manifest, MCP server config, and a schematic-design skill that teaches layout conventions.

**Architecture:** Three new files (`.claude-plugin/plugin.json`, `.mcp.json`, `skills/schematic-design/SKILL.md`) plus updates to `TODO.md` and `README.md`. No Python code changes. The plugin bundles MCP server configuration (auto-downloads from PyPI via `uvx`) with skills that teach the LLM how to make good layout decisions.

**Spec:** `docs/superpowers/specs/2026-03-10-schematic-layout-plugin-design.md`

---

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Create | `.claude-plugin/plugin.json` | Plugin manifest |
| Create | `.mcp.json` | MCP server configuration for all 5 servers |
| Create | `skills/schematic-design/SKILL.md` | Schematic layout conventions skill |
| Modify | `TODO.md` | Replace outdated "Claude Skill" section |
| Modify | `README.md` | Add plugin installation instructions |

---

## Chunk 1: Plugin Infrastructure

### Task 1: Create plugin manifest

**Files:**
- Create: `.claude-plugin/plugin.json`

- [ ] **Step 1: Create the `.claude-plugin` directory**

```bash
mkdir -p .claude-plugin
```

- [ ] **Step 2: Write `plugin.json`**

Create `.claude-plugin/plugin.json` with this exact content:

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

- [ ] **Step 3: Verify JSON is valid**

```bash
python3 -c "import json; json.load(open('.claude-plugin/plugin.json')); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add .claude-plugin/plugin.json
git commit -m "feat: add Claude Code plugin manifest"
```

---

### Task 2: Create MCP server configuration

**Files:**
- Create: `.mcp.json`

- [ ] **Step 1: Write `.mcp.json`**

Create `.mcp.json` at the repo root with this exact content:

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

No `cwd` field — the servers inherit Claude Code's working directory and
auto-detect `.kicad_pro` files via the existing 3-tier path resolution.

- [ ] **Step 2: Verify JSON is valid**

```bash
python3 -c "import json; json.load(open('.mcp.json')); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add .mcp.json
git commit -m "feat: add MCP server configuration for plugin"
```

---

## Chunk 2: Schematic Design Skill

### Task 3: Create the schematic-design skill

**Files:**
- Create: `skills/schematic-design/SKILL.md`

- [ ] **Step 1: Create the skill directory**

```bash
mkdir -p skills/schematic-design
```

- [ ] **Step 2: Write `SKILL.md`**

Create `skills/schematic-design/SKILL.md` with this exact content:

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

- [ ] **Step 3: Verify the skill file exists and has frontmatter**

```bash
head -10 skills/schematic-design/SKILL.md
```

Expected: should show the YAML frontmatter block with `name:` and `description:`.

- [ ] **Step 4: Commit**

```bash
git add skills/schematic-design/SKILL.md
git commit -m "feat: add schematic-design skill with layout conventions"
```

---

## Chunk 3: Update Existing Files

### Task 4: Update TODO.md

**Files:**
- Modify: `TODO.md`

- [ ] **Step 1: Replace the "Schematic Layout Quality" section**

Replace lines 1–26 of `TODO.md` (everything from `# TODO` through the skill
bullet list) with:

```markdown
# TODO

## ~~Schematic Layout Quality~~ (DONE — Plugin Skill)

Layout conventions are now delivered as the `schematic-design` skill inside
the `kicad` Claude Code plugin (`skills/schematic-design/SKILL.md`). The
skill is auto-invoked when Claude detects schematic design intent, teaching
placement spacing, signal flow direction, and wiring tool selection.

See `docs/superpowers/specs/2026-03-10-schematic-layout-plugin-design.md`
for the full design rationale.
```

Keep the existing "Auto-Junction Bug" section (lines 28–33) unchanged.

- [ ] **Step 2: Verify the file looks correct**

```bash
cat TODO.md
```

Expected: updated header section followed by the existing auto-junction note.

- [ ] **Step 3: Commit**

```bash
git add TODO.md
git commit -m "docs: update TODO — schematic layout addressed by plugin skill"
```

---

### Task 5: Update README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add plugin installation section**

After the existing `## Installation` section (which ends at line 33 with the
`uvx` commands), insert this new section:

```markdown

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
```

This goes **before** the existing `## Configuration` section so the order is:
Installation → Claude Code Plugin → Configuration (manual setup for other
clients).

- [ ] **Step 2: Verify the README renders correctly**

```bash
head -50 README.md
```

Expected: Installation section, then Claude Code Plugin section, then
Configuration section.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add Claude Code plugin installation to README"
```
