# Skill Orchestration Redesign

## Problem

The KiCad MCP plugin's skills lack structured handoffs between phases.
The AI hallucinates symbol names (e.g., `Device:Q_PMOS_GSD` which
doesn't exist) and miscalculates placement coordinates (y=228.6mm on
an A4 page with 210mm height). Five rounds of server-side fixes
(better error messages, bounds checking, fuzzy matching) haven't
solved the problem because the root cause is at the orchestration
layer, not the tool layer.

The tools work correctly — they reject bad input with good error
messages. The issue is that the AI generates bad input because there's
no validation phase between "I have a BOM" and "I'm calling
place_component."

## Approach

Hybrid of three strategies:

- **Full pipeline with written artifacts and reviewer subagents** at
  the two critical handoffs (BOM validation, schematic plan validation)
- **Pre-flight checks** added to existing skills at lighter-weight
  boundaries (pcb-layout)
- **Hard gates** at every phase boundary requiring explicit evidence
  before progression

Validated against IPC-2231 (DFX), ISO 9001 (design controls), NASA
NPR 7123.1 (review gates), and Altium's 4-stage design flow.
Modeled after the superpowers plugin's brainstorm → write-plan →
execute-plan → finish pipeline.

## Pipeline Overview

```
using-kicad (orchestrator — enforces pipeline order)
    |
    v
circuit-design (requirements -> validated BOM artifact)
    |
    |  EXIT GATE: resolve all lib_ids, write specs/bom.md
    |  Reviewer subagent validates BOM
    |  HARD GATE: user approves BOM
    |
    v
schematic-plan (BOM -> placement & wiring plan artifact)
    |
    |  Reads specs/bom.md
    |  Produces specs/schematic-plan.md with exact coordinates
    |  Reviewer subagent validates plan
    |  HARD GATE: user approves plan
    |
    v
schematic-execute (plan -> mechanical execution)
    |
    |  Reads specs/schematic-plan.md
    |  Executes place_component/wire calls per plan
    |  EXIT GATE: run ERC, must be zero violations
    |
    v
verification (ERC gate — blocks PCB until clean)
    |
    |  HARD GATE: ERC = 0 violations
    |
    v
pcb-layout (netlist -> board layout)
    |
    |  PRE-FLIGHT: verify all footprints exist
    |  Placement, routing, zones
    |  EXIT GATE: run DRC, must be zero violations
    |
    v
verification (DRC gate — blocks export until clean)
    |
    |  HARD GATE: DRC = 0 violations
    |
    v
Export (Gerbers, BOM, pick-and-place, 3D)
```

## Standards Alignment

| Phase | Altium Stage | NASA Gate | ISO 9001 |
|-------|-------------|-----------|----------|
| circuit-design | Component Creation + Library | PDR | Design Inputs (8.3.3) |
| schematic-plan | — | Between PDR and CDR | Design Controls (8.3.4) |
| schematic-execute | Schematic Capture | CDR | Design Outputs (8.3.5) |
| verification (ERC) | ERC Gate | CDR exit criteria | Design Verification (8.3.4) |
| pcb-layout | PCB Layout | — | Design Outputs (8.3.5) |
| verification (DRC) | DRC Gate | MRR | Design Verification (8.3.4) |
| export | Output Generation | MRR deliverables | — |

## Skill Details

### 1. `using-kicad` — Pipeline Orchestrator

Upgraded from a routing table to a pipeline enforcer. Injected at
session start for any electronics/KiCad conversation.

**Responsibilities:**
- Track which phase the user is in and what artifacts exist
- Enforce phase ordering — block progression if prior gates haven't
  been passed
- Decide entry point based on context (new design vs. modification
  vs. verification)

**Phase enforcement:**
- schematic-plan requires specs/bom.md to exist
- schematic-execute requires specs/schematic-plan.md to exist
- pcb-layout requires ERC to have passed
- export requires DRC to have passed

**Entry points:**
- New design from scratch: start at circuit-design, full pipeline
- Modifying existing schematic: enter at schematic-execute directly
- Running checks on existing work: enter at verification directly
- User provides complete plan/spec: enter at schematic-plan or
  schematic-execute depending on plan content

**Rationalization prevention table:**

| Thought | Reality |
|---------|---------|
| "I already know the components, skip circuit-design" | You'll hallucinate lib_ids. Do the phase. |
| "The plan is simple enough to do in my head" | That's what caused 9 wasted tool calls last time. Write the plan. |
| "I'll just fix the page size when it fails" | Pre-calculate it. Reactive fixes waste tokens. |
| "ERC will probably pass, start PCB layout" | Run ERC. "Probably" is not evidence. |
| "This is just a small change, no need for the full pipeline" | Small changes to existing schematics can use schematic-execute directly. New designs go through the pipeline. |

### 2. `circuit-design` — Requirements to Validated BOM

Current skill content stays (topology selection, component selection
rules, protection patterns, preferred values, package defaults).

**Changes:**

**Constraint gathering (new section at the start):**
Before topology selection, gather constraints:
- Board size constraints (if known)
- Mechanical constraints (connector positions, mounting holes)
- Thermal constraints (ambient temp, enclosure, airflow)
- EMI/EMC requirements
- Target manufacturer capabilities (trace width, layer count)

Recorded in BOM artifact header. Defaults used for unknowns.

**Symbol resolution becomes mandatory and blocking:**
For each component, before the BOM is finalized:
1. Call `list_lib_symbols` on the expected library
2. Confirm the exact symbol name exists
3. If not found, resolve immediately — pick a generic symbol or
   create a custom one via `add_symbol`
4. Record the verified `lib_id` in the BOM

**Written BOM artifact (`specs/bom.md`):**

```markdown
# BOM: [Project Name]

## Constraints
- Input: 12-24V DC
- Board size: 50x40mm (if known)
- Layers: 2 (default)
- Manufacturer min trace: 0.15mm

## Block Diagram
[stage names and interfaces between them]

## Components
| Ref | lib_id (verified) | Value | Rating | Package | Stage | Footprint | Notes |
|-----|-------------------|-------|--------|---------|-------|-----------|-------|
| F1  | Device:Fuse       | 2A    | 32V    | 1206    | Input protection | Fuse_1206 | Slow-blow |
| ...
```

The `lib_id` column contains verified identifiers resolved against
actual KiCad libraries, not guessed from training data.

**BOM reviewer subagent dispatched after artifact is written.**

**Hard gate: user approves BOM before schematic-plan can start.**

### 3. `schematic-plan` — BOM to Placement & Wiring Plan (NEW)

New skill. Pure planning — no file modifications. Only inspection
MCP tools (`list_lib_symbols`, `get_symbol_pins`, `get_symbol_info`).

**Inputs:** `specs/bom.md`
**Outputs:** `specs/schematic-plan.md`

**Planning steps:**

**Step 1: Page size calculation.**
Count components per stage. Estimate space using spacing rules:
- Each component: ~12.7mm vertical, ~12.7mm horizontal
- Inter-stage gap: 25.4-50.8mm
- Title block: ~108x32mm at bottom-right
- Margins: 10mm all sides

Calculate total bounding box. Pick smallest standard page that fits.
Record decision and math.

**Step 2: Stage layout.**
Assign each functional stage a bounding box on the sheet:

```markdown
## Stage Layout
| Stage | Bounding Box | Components |
|-------|-------------|------------|
| Input protection | (25, 50) -> (140, 90) | J1, F1, D1, C1, C2, Q1, D3, R5 |
| Buck converter | (25, 115) -> (240, 170) | U1, C3, C4, ... |
```

**Step 3: Component coordinates.**
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

**Step 4: Wiring plan.**
For each net, specify tool and connections:

```markdown
## Wiring
| Net | Tool | Pins |
|-----|------|------|
| J1:1 -> F1:1 | connect_pins | Direct, adjacent |
| VIN | wire_pins_to_net | F1:2, D1:K, C1:1, C2:1, Q1:S, D3:K |
```

**Step 5: No-connect and power flags.**
List pins needing no-connect flags and nets needing PWR_FLAG.

**Step 6: Hierarchical sheets (if applicable).**

**Schematic plan reviewer subagent dispatched after artifact is
written.**

**Hard gate: user approves plan before schematic-execute can start.**

### 4. `schematic-execute` — Mechanical Plan Execution

Replaces current `schematic-design`. Reads the plan and executes
mechanically. All intelligence happened in prior phases.

**Pre-flight checks:**
1. Verify `specs/schematic-plan.md` exists and has been approved
2. Read page size from plan, call `set_page_size` immediately if
   not A4
3. Verify project/schematic files exist (create if needed)
4. If plan references custom symbol library, verify it exists via
   `list_lib_symbols`

**Execution order:**
1. Project setup (create_project, create_schematic, etc.)
2. Set page size (if plan specifies non-A4)
3. Register symbol libraries (create_sym_lib_table)
4. Create custom symbols (add_symbol)
5. Place all components (place_component per coordinate table)
6. Wire all connections (connect_pins / wire_pins_to_net per wiring table)
7. Add no-connect flags
8. Add hierarchical sheets (if applicable)
9. Run ERC

**Error handling — escalate, don't improvise:**

| Situation | Response |
|-----------|----------|
| Symbol not found | STOP. Report error. The plan's BOM validation should have caught this. |
| Position outside page bounds | STOP. Report error. The plan's page calculation should have prevented this. |
| connect_pins fails | Try wire_pins_to_net. If that fails, report and continue. |
| ERC violations | Report violations. Invoke verification skill. |

**Exit gate:** Run ERC. Zero violations → proceed. Violations →
invoke verification.

**Retained from current schematic-design:** Spacing conventions,
wiring strategy reference, naming conventions, power symbol guidance.
Available as reference if the plan doesn't specify something
explicitly.

**Removed:** All decision-making about what to place and where.
That's schematic-plan's job.

### 5. `verification` — Hard Gate Enforcement

Current content stays (ERC/DRC fix-by-category workflows,
pre-manufacturing checklist).

**Changes:**
- **HARD GATE declaration:** No phase proceeds past verification
  without zero violations.
- **Verification-before-completion pattern:** Before claiming
  ERC/DRC is clean, the skill must show actual tool output with
  `violation_count: 0`. No claims without fresh evidence.
- **Re-run after fixes:** Previous results are stale after fixes.
  Loop until zero.
- **Two gate points:** Invoked after schematic-execute (ERC) and
  after pcb-layout (DRC). Same skill, different context.

### 6. `pcb-layout` — Pre-flight Checks Added

Current content stays (placement strategy, trace width tables,
routing rules, layer stack, copper zones, DRC workflow).

**Pre-flight checks added:**
1. Verify ERC gate was cleared
2. For each schematic component, call `list_lib_footprints` to
   verify assigned footprint exists
3. If `specs/bom.md` exists, cross-reference footprints against
   BOM's package column
4. Check board size constraints from BOM artifact (if present)

**Extensibility note:** For complex boards (>30 components, 4+
layers, high-speed signals), consider creating a `pcb-plan` artifact
following the same pattern as `schematic-plan`. For simpler boards,
pre-flight checks and existing placement strategy are sufficient.

## Reviewer Subagent Prompts

Two reviewer agent prompts, dispatched via the Agent tool at gate
checkpoints. Both follow superpowers' distrust pattern — they
independently verify claims using MCP tools rather than trusting the
author.

### BOM Reviewer (`agents/bom-reviewer.md`)

Dispatched after circuit-design writes `specs/bom.md`.

**Checklist:**
1. For every row, call `list_lib_symbols` on the specified library.
   Confirm the symbol name exists. If not, report FAIL with what's
   available.
2. Every IC has at least one decoupling capacitor in the BOM.
3. Every voltage/current rating has derating margin (component
   rating > operating value).
4. Input protection exists for every external interface (connectors).
5. Resistor/capacitor values are E-series preferred (E12 or E24).
6. Every component has a footprint column entry.
7. No duplicate reference designators.

**Output:**
```
STATUS: APPROVED | ISSUES_FOUND

Issues (if any):
- [Row F1] lib_id "Device:Fuse_PTC" not found. Available: Fuse, Fuse_Small.
- [Row U1] No decoupling capacitor found for U1.
- [Row R3] Value 2.35K is not E-series. Nearest: 2.2K or 2.4K.
```

### Schematic Plan Reviewer (`agents/schematic-plan-reviewer.md`)

Dispatched after schematic-plan writes `specs/schematic-plan.md`.

**Checklist:**
1. Every BOM component appears in the placement table (no missing
   parts).
2. Every coordinate fits within declared page size (with margins).
3. Vertical spacing between adjacent components >= 10.16mm.
4. Horizontal spacing between adjacent passives >= 12.7mm.
5. IC-to-decoupling-cap distance <= 25.4mm.
6. Inter-stage gaps >= 25.4mm.
7. No component overlaps (bounding box collision check).
8. Pin names in wiring table match actual pins (via
   `get_symbol_pins`).
9. Every net accounts for all pins that should be on it.
10. Page size is smallest standard size that fits (not over-sized).

**Output:**
```
STATUS: APPROVED | ISSUES_FOUND

Issues (if any):
- [U2] Position (152.4, 228.6) exceeds A4 height (210mm). Needs A3.
- [C3, C4] Vertical spacing 7.62mm, minimum 10.16mm.
- [Wiring] Pin "GSD" on Q1 does not exist. Actual pins: G, D, S.
- [Missing] R9 in BOM but not in placement table.
```

### Reviewer behavior rules (both):
- Max 5 iterations. After 5 fix-and-resubmit cycles, surface to
  the user.
- Do not fix, only report. The planning skill fixes issues.
- Distrust the author. Independently verify by calling MCP tools.
- Binary outcome: APPROVED or ISSUES_FOUND. No warnings.

## Skill Count

7 skills total (up from 5):
1. `using-kicad` (modified — pipeline enforcer)
2. `circuit-design` (modified — exit gate + BOM artifact)
3. `schematic-plan` (NEW)
4. `schematic-execute` (refactored from schematic-design)
5. `pcb-layout` (modified — pre-flight checks)
6. `verification` (modified — hard gate enforcement)
7. (pcb-plan — future, when needed)

Plus 2 agent prompts (not skills):
- `agents/bom-reviewer.md`
- `agents/schematic-plan-reviewer.md`

## What This Fixes

The two RUN.txt failures would both be caught before any tool calls:

1. **`Device:Q_PMOS_GSD` not found** — caught by the BOM reviewer
   at the circuit-design exit gate. The reviewer calls
   `list_lib_symbols` on the Device library and reports that
   `Q_PMOS_GSD` doesn't exist. Fixed before schematic-plan starts.

2. **Components at y=228.6 on A4 page** — caught by the schematic
   plan reviewer. The reviewer checks every coordinate against the
   declared page size and reports the overflow. Page size is corrected
   in the plan before schematic-execute starts.

Both failures become plan-time errors (cheap to fix) instead of
execution-time errors (9 wasted tool calls).
