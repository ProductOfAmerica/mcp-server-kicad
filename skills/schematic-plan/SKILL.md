---
name: schematic-plan
description: >
  Use after circuit-design produces a validated BOM (specs/bom.md) to
  plan exact component placement coordinates and wiring before
  schematic capture begins. Pure planning — no file modifications.
  Produces specs/schematic-plan.md artifact.
---

<CRITICAL-RULE>
NEVER use the Read, Write, or Edit tools on KiCad files (.kicad_sch,
.kicad_pcb, .kicad_sym, .kicad_mod, .kicad_pro, .kicad_prl). ALL
KiCad file manipulation MUST go through the kicad MCP tools. NEVER
run kicad-cli commands via Bash. If an MCP tool returns an error, try
different parameters — do NOT fall back to manual file editing.

EVERY KiCad operation has a corresponding MCP tool. Do NOT claim a
tool does not exist without first listing all available tools. Key
tools that MUST be used instead of file writes:
- `add_symbol` — create custom symbol definitions in .kicad_sym files
- `create_symbol_library` — create new .kicad_sym library files
- `create_schematic` — create new .kicad_sch files
- `create_project` — create new .kicad_pro project files
If you find yourself thinking "there's no MCP tool for this," you are
wrong. Check the tool list again.
</CRITICAL-RULE>

# Schematic Plan

Pure planning phase — no file modifications. Only inspection MCP
tools (`list_lib_symbols`, `get_symbol_info`). Note:
`get_symbol_pins` requires a schematic with placed symbols and cannot
be used during planning. Use `get_symbol_info` instead — it reads
pin definitions directly from library files.

**Inputs:** `specs/bom.md` (validated BOM from circuit-design)
**Outputs:** `specs/schematic-plan.md` (placement and wiring plan)

## Pre-flight

1. Verify `specs/bom.md` exists
2. Verify BOM reviewer returned APPROVED
3. Read the BOM and count components per stage

## Planning Steps

### Step 1: Page Size Calculation

Count components per stage. Estimate space using spacing rules:
- Each component: ~12.7mm vertical, ~12.7mm horizontal
- Inter-stage gap: 25.4–50.8mm
- Title block: ~108x32mm at bottom-right
- Margins: 10mm all sides
- Usable area = page dimensions - margins - title block

Calculate total bounding box. Pick smallest standard page that fits.
Record decision and math explicitly (show the arithmetic).

Standard page sizes (landscape):
| Size | Width (mm) | Height (mm) | Usable W | Usable H |
|------|-----------|-------------|----------|----------|
| A4   | 297       | 210         | 179      | 168      |
| A3   | 420       | 297         | 302      | 255      |
| A2   | 594       | 420         | 476      | 378      |

Usable W = width - 20 (margins) - 108 (title block width near bottom-right).
Usable H = height - 20 (margins) - 32 (title block height).

### Step 2: Stage Layout

Assign each functional stage a bounding box on the sheet:

```markdown
## Stage Layout
| Stage | Bounding Box | Components |
|-------|-------------|------------|
| Input protection | (25, 50) -> (140, 90) | J1, F1, D1, C1, C2, Q1, D3, R5 |
| Buck converter | (25, 115) -> (240, 170) | U1, C3, C4, ... |
```

### Step 3: Component Coordinates

Assign exact (x, y) per component within its stage bounding box:
- Active component centered in stage
- Input passives to the left, output to the right
- Decoupling caps 25.4mm from their IC
- Signal flow left-to-right, voltage top-to-bottom

```markdown
## Placement
| Ref | lib_id | x | y | rotation | Stage |
|-----|--------|---|---|----------|-------|
| J1 | Connector_Generic:Conn_01x02 | 25.4 | 50.8 | 0 | Input protection |
```

### Step 4: Wiring Plan

For each net, specify tool and connections. Available tools:
- `connect_pins` — direct Manhattan wire between two adjacent pins
- `wire_pins_to_net` — connect one or more pins to a named net label

Group all pins for the same net into a single `wire_pins_to_net`
call. Do not make separate calls for individual pins on the same net.
Wiring order follows table order top-to-bottom; list power nets
before signal nets.

```markdown
## Wiring
| Order | Net | Tool | Pins |
|-------|-----|------|------|
| 1 | J1:1 -> F1:1 | connect_pins | Direct, adjacent |
| 2 | VIN | wire_pins_to_net | F1:2, D1:K, C1:1, C2:1, Q1:S, D3:K |
| 3 | PGND | wire_pins_to_net | J1:2, D1:A, C1:2, C2:2, R5:2 |
```

### Step 5: No-Connect and Power Flags

List pins needing no-connect flags and nets needing PWR_FLAG.

```markdown
## No-Connect Pins
| Ref | Pin | Reason |
|-----|-----|--------|
| U1  | NC  | Unused pin per datasheet |

## Power Flags
| Net | Reason |
|-----|--------|
| VOUT | Regulator output, not recognized as power source by KiCad |
```

### Step 6: Hierarchical Sheets (if applicable)

If the design requires multiple sheets, plan sheet boundaries and
hierarchical labels here.

## MCP Tools for This Skill

These are the ONLY kicad MCP tools you should use during planning
(inspection only — no modifications):

- `list_lib_symbols` — verify symbol exists in library
- `get_symbol_info` — get pin names, types, and properties
- `list_lib_footprints` — verify footprint exists
- `get_footprint_info` — check pad dimensions

Do NOT use `get_symbol_pins` — it requires a placed schematic.
Use `get_symbol_info` instead.

## Schematic Plan Reviewer

After writing `specs/schematic-plan.md`, dispatch the schematic plan
reviewer subagent:

```
Agent(
  prompt="<contents of agents/schematic-plan-reviewer.md>\n\nPlan path: <project_dir>/specs/schematic-plan.md\nBOM path: <project_dir>/specs/bom.md",
  subagent_type="general-purpose"
)
```

Fix any issues reported by the reviewer, update
`specs/schematic-plan.md`, and re-dispatch until the reviewer returns
APPROVED.

<HARD-GATE>
User must explicitly approve the plan before schematic-design can
start. "The coordinates look reasonable" is not approval — the
reviewer must have returned APPROVED first.
</HARD-GATE>

**User rejection handling:** If the user rejects the plan or requests
changes (e.g., different stage layout, different page size), fix the
specific issues and re-run the schematic plan reviewer.

## Checklist

**IMPORTANT: Use TodoWrite to create todos for EACH checklist item below.**

- [ ] Verify `specs/bom.md` exists and is APPROVED
- [ ] Count components per stage
- [ ] Calculate page size (show arithmetic)
- [ ] Assign stage bounding boxes
- [ ] Plan exact (x, y) coordinates per component
- [ ] Plan wiring with tool selection per net
- [ ] Plan no-connect pins and power flags
- [ ] Write `specs/schematic-plan.md` artifact
- [ ] Dispatch schematic plan reviewer subagent
- [ ] Get user approval on plan

## Rationalization Prevention

| Thought | Reality |
|---------|---------|
| "A4 is big enough" | Show the math. Components * spacing + margins + title block. |
| "These coordinates look right" | Check every Y < page_height - margin, every X < page_width - margin. |
| "I know this pin name" | Call `get_symbol_info`. Don't guess pin names from memory. |
| "The spacing is fine" | Minimum 10.16mm vertical, 12.7mm horizontal. Measure, don't eyeball. |

## Your Job

1. Create directory: `mkdir -p skills/schematic-plan`
2. Write the file with the EXACT content above
3. Verify: frontmatter, CRITICAL-RULE, 6 planning steps, page size table, wiring batching, inspection-only tools, reviewer dispatch, HARD-GATE, rationalization table
4. Commit: `git commit -m "feat: add schematic-plan skill for placement and wiring planning"` (NO Co-Authored-By)
