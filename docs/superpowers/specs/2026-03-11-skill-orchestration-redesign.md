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

## Scope Boundary

This pipeline covers concept design through manufacturing export
(IPC-2231 phases 1–2 + partial phase 5). Post-fabrication phases
(first build, product validation, field support) are outside the
tool's domain.

## Approach

Hybrid of three strategies:

- **Full pipeline with written artifacts and reviewer subagents** at
  the two critical handoffs (BOM validation, schematic plan validation)
- **Pre-flight checks** added to existing skills at lighter-weight
  boundaries (pcb-layout, modification mode)
- **Hard gates** at every phase boundary requiring explicit evidence
  before progression

Every artifact — whether AI-generated or user-provided — passes
through its reviewer before execution. User-provided plans are
accepted but then verified, audited, and challenged, following the
superpowers pattern of "accept the input, then validate it."

Validated against IPC-2231 (DFX), ISO 9001 (design controls), NASA
NPR 7123.1 (review gates), and Altium's 4-stage design flow.
Modeled after the superpowers plugin's brainstorm → write-plan →
execute-plan → finish pipeline.

## Anti-Patterns for Electronics Design

These rationalizations are the electronics equivalent of superpowers'
"This Is Too Simple To Need A Design." Every one has caused real
failures.

| Anti-Pattern | Why It Fails |
|-------------|-------------|
| "I know this IC's pinout from memory" | You're recalling training data, not the library. Call `get_symbol_info`. Pin 3 might be GND or NC depending on the package variant. |
| "The datasheet app circuit is simple enough to place without planning" | That's exactly what produced 9 wasted tool calls in the RUN.txt failure — coordinates computed on-the-fly exceeded page bounds. |
| "Standard 100nF decoupling is fine" | The datasheet specifies the cap value and ESR. A 100nF on a switching regulator's input might need 22uF. Check the datasheet. |
| "This symbol name is probably right" | `Q_PMOS_GSD` sounded right. It doesn't exist. Call `list_lib_symbols` and verify. |
| "A4 is big enough, I'll check later" | Later = after 4 failed placements. Calculate first. |
| "These nets are obvious, I don't need to plan the wiring" | Obvious to you means hallucinated pin names. Query the library. |
| "I'll just use the same component I used last time" | Training data bias. The library may have been updated. Verify. |

## Pipeline Overview

```
using-kicad (orchestrator — enforces pipeline order)
    |
    v
circuit-design (requirements -> validated BOM artifact)
    |
    |  EXIT GATE: resolve all lib_ids + footprints, write specs/bom.md
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
schematic-design (plan -> mechanical execution)
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
Export (Gerbers, drill, BOM, pick-and-place, 3D,
       fabrication notes, assembly drawing)
```

## Standards Alignment

| Phase | Altium Stage | NASA Gate | ISO 9001 |
|-------|-------------|-----------|----------|
| circuit-design | Component Creation + Library | PDR | Design Inputs (8.3.3) |
| schematic-plan | — | Between PDR and CDR | Design Controls (8.3.4) |
| schematic-design | Schematic Capture | CDR | Design Outputs (8.3.5) |
| verification (ERC) | ERC Gate | CDR exit criteria | Design Verification (8.3.4) |
| pcb-layout | PCB Layout | — | Design Outputs (8.3.5) |
| verification (DRC) | DRC Gate | MRR | Design Verification (8.3.4) |
| export | Output Generation | MRR deliverables | Design Outputs (8.3.5) |

## Artifact Paths

All artifact paths are relative to the KiCad project directory — the
directory containing the `.kicad_pro` file:
- `specs/bom.md` — validated BOM from circuit-design
- `specs/schematic-plan.md` — placement and wiring plan

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

<HARD-GATE>
Do NOT proceed to schematic-plan until specs/bom.md exists AND its
reviewer has returned APPROVED AND the user has confirmed approval.
</HARD-GATE>

<HARD-GATE>
Do NOT proceed to schematic-design until specs/schematic-plan.md
exists AND its reviewer has returned APPROVED AND the user has
confirmed approval.
</HARD-GATE>

<HARD-GATE>
Do NOT proceed to pcb-layout until run_erc output shows
violation_count = 0. "Probably clean" is not evidence.
</HARD-GATE>

<HARD-GATE>
Do NOT proceed to export until run_drc output shows
violation_count = 0. "Probably clean" is not evidence.
</HARD-GATE>

**Entry points — every path validates:**

- **New design from scratch:** Start at circuit-design, full pipeline.
- **User provides their own BOM:** Circuit-design runs in
  validation-only mode — resolves every lib_id and footprint, fills
  in missing fields, writes specs/bom.md, runs the BOM reviewer.
  Skips topology selection but does NOT skip validation. The user's
  BOM is accepted, then audited.
- **User provides a placement plan:** The plan is written to
  specs/schematic-plan.md, then the schematic plan reviewer is
  dispatched. The user's plan is accepted, then audited. If the
  reviewer finds issues (wrong lib_ids, coordinates outside page
  bounds, incorrect pin names), they are surfaced and must be
  resolved before execution. The plan does NOT skip the reviewer.
- **Modifying existing schematic:** Enter at schematic-design in
  modification mode. Lightweight pre-flight validates lib_ids for
  any new components via `list_lib_symbols` before placement.
- **Running checks on existing work:** Enter at verification directly.

**What constitutes "user approval":** The user explicitly says
something like "approved," "looks good," "proceed," "yes," or "go
ahead." Asking a question or requesting changes is NOT approval.

**Rationalization prevention:**

| Thought | Reality |
|---------|---------|
| "I already know the components, skip circuit-design" | You'll hallucinate lib_ids. Do the phase. |
| "The plan is simple enough to do in my head" | That's what caused 9 wasted tool calls last time. Write the plan. |
| "I'll just fix the page size when it fails" | Pre-calculate it. Reactive fixes waste tokens. |
| "ERC will probably pass, start PCB layout" | Run ERC. "Probably" is not evidence. |
| "This is just a small change, no need for the full pipeline" | Small changes use modification mode. But modification mode still validates lib_ids. |
| "The user already validated this, skip the reviewer" | The reviewer checks what humans miss — pin names, coordinate math, spacing. Always run it. |

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
- IPC design class (1 = consumer, 2 = industrial, 3 = high-rel).
  Default to Class 2 if unknown. This determines trace width,
  spacing, and annular ring minimums downstream.

Recorded in BOM artifact header. Defaults used for unknowns.

**Symbol AND footprint resolution becomes mandatory and blocking:**
For each component, before the BOM is finalized:
1. Call `list_lib_symbols` on the expected library — confirm the
   exact symbol name exists
2. Call `list_lib_footprints` on the expected footprint library —
   confirm the footprint exists
3. If either is not found, resolve immediately — pick a generic
   symbol/footprint or create a custom one
4. Record the verified `lib_id` and footprint in the BOM

**Written BOM artifact (`specs/bom.md`):**

```markdown
# BOM: [Project Name]

## Constraints
- Input: 12-24V DC
- Board size: 50x40mm (if known)
- Layers: 2 (default)
- IPC class: 2
- Manufacturer min trace: 0.15mm

## Block Diagram
[stage names and interfaces between them]

## Components
| Ref | lib_id (verified) | Value | Rating | Package | Stage | Footprint (verified) | Notes |
|-----|-------------------|-------|--------|---------|-------|---------------------|-------|
| F1  | Device:Fuse       | 2A    | 32V    | 1206    | Input protection | Fuse_1206 | Slow-blow |
| ...
```

The `lib_id` and `Footprint` columns contain identifiers that were
resolved against actual KiCad libraries via MCP tool calls, not
guessed from training data.

**Phase-specific rationalization prevention:**

| Thought | Reality |
|---------|---------|
| "This symbol name is probably right" | Call `list_lib_symbols`. "Probably" caused the Q_PMOS_GSD failure. |
| "Standard 100nF decoupling is fine for this IC" | Read the datasheet. The IC specifies what it needs. |
| "I know this footprint exists" | Call `list_lib_footprints`. Verify, don't assume. |
| "The rating is close enough" | Show the derating math. 60-80% of rated voltage, 70-80% of rated current. |

**BOM reviewer subagent dispatched after artifact is written.**

<HARD-GATE>
User must explicitly approve the BOM before schematic-plan can
start. "I already know the components" is not approval.
</HARD-GATE>

**User rejection handling:** If the user rejects the BOM or requests
changes, fix the specific issues and re-run the BOM reviewer on the
updated artifact. Do not restart from scratch unless the user asks.

### 3. `schematic-plan` — BOM to Placement & Wiring Plan (NEW)

New skill. Pure planning — no file modifications. Only inspection
MCP tools (`list_lib_symbols`, `get_symbol_info`). Note:
`get_symbol_pins` requires a schematic with placed symbols and cannot
be used during planning. Use `get_symbol_info` instead — it reads
pin definitions directly from library files.

**Inputs:** `specs/bom.md`
**Outputs:** `specs/schematic-plan.md`

**Planning steps:**

**Step 1: Page size calculation.**
Count components per stage. Estimate space using spacing rules:
- Each component: ~12.7mm vertical, ~12.7mm horizontal
- Inter-stage gap: 25.4-50.8mm
- Title block: ~108x32mm at bottom-right
- Margins: 10mm all sides
- Usable area = page dimensions - margins - title block

Calculate total bounding box. Pick smallest standard page that fits.
Record decision and math explicitly (show the arithmetic).

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
For each net, specify tool and connections. Available tools:
- `connect_pins` — direct Manhattan wire between two adjacent pins
- `wire_pins_to_net` — connect one or more pins to a named net label
  (subsumes the old `wire_pin_to_label` for single-pin cases)

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

**Step 5: No-connect and power flags.**
List pins needing no-connect flags and nets needing PWR_FLAG.

**Step 6: Hierarchical sheets (if applicable).**

**Phase-specific rationalization prevention:**

| Thought | Reality |
|---------|---------|
| "A4 is big enough" | Show the math. Components * spacing + margins + title block. |
| "These coordinates look right" | Check every Y < page_height - margin, every X < page_width - margin. |
| "I know this pin name" | Call `get_symbol_info`. Don't guess pin names from memory. |
| "The spacing is fine" | Minimum 10.16mm vertical, 12.7mm horizontal. Measure, don't eyeball. |

**Schematic plan reviewer subagent dispatched after artifact is
written.**

<HARD-GATE>
User must explicitly approve the plan before schematic-design can
start. "The coordinates look reasonable" is not approval — the
reviewer must have returned APPROVED first.
</HARD-GATE>

**User rejection handling:** If the user rejects the plan or requests
changes (e.g., different stage layout, different page size), fix the
specific issues and re-run the schematic plan reviewer.

### 4. `schematic-design` — Mechanical Plan Execution

Refactored from the current `schematic-design` skill. The skill file
stays at `skills/schematic-design/SKILL.md` to preserve the
`/kicad:schematic-design` invocation name (no breaking change). The
content is rewritten to be a plan executor.

**Two modes:**

- **Plan mode (new designs):** Reads `specs/schematic-plan.md` and
  executes mechanically. All intelligence happened in prior phases.
  The plan file is the sole source of truth — ignore prior
  conversation context about how the plan was created. Read the plan,
  execute the plan.
- **Modification mode (existing schematics):** No plan artifact
  required. User instructions serve as the plan. Pre-flight verifies
  the schematic file exists and lists current components. For any new
  component being added, verify its lib_id via `list_lib_symbols`
  before calling `place_component`. The existing spacing/wiring/naming
  conventions guide the work.

**Pre-flight checks (plan mode):**
1. Verify `specs/schematic-plan.md` exists and its reviewer returned
   APPROVED
2. Read page size from plan, call `set_page_size` immediately if
   not A4
3. Verify project/schematic files exist (create if needed)
4. If plan references custom symbol library, verify it exists via
   `list_lib_symbols`

**Pre-flight checks (modification mode):**
1. Verify the schematic file exists
2. List current components via `list_schematic_items`
3. For each new component to be added, call `list_lib_symbols` to
   verify its lib_id exists before placement

**Execution order (plan mode):**
1. Project setup (create_project, create_schematic, etc.)
2. Set page size (if plan specifies non-A4)
3. Register symbol libraries (create_sym_lib_table)
4. Create custom symbols (add_symbol)
5. Place all components (place_component per coordinate table)
6. Wire all connections per wiring table — group all pins for the
   same net into a single `wire_pins_to_net` call
7. Add no-connect flags
8. Add hierarchical sheets (if applicable)
9. Run ERC

**Error handling — escalate, don't improvise:**

| Situation | Response |
|-----------|----------|
| Symbol not found | STOP. Do not fuzzy-match or substitute. Report the error. If a previously-verified symbol is missing, instruct the user to re-run from circuit-design to re-validate the BOM. |
| Position outside page bounds | STOP. Report error. The plan's page calculation should have prevented this. Instruct the user to re-run schematic-plan. |
| connect_pins fails | Try wire_pins_to_net for that connection. If that fails, report and continue with remaining wiring. |
| ERC violations | Report violations. Invoke verification skill. |

**Phase-specific rationalization prevention:**

| Thought | Reality |
|---------|---------|
| "This pin name is close enough" | Use the exact pin name from the plan. The plan was verified against `get_symbol_info`. |
| "I can adjust the coordinates slightly" | Use the exact coordinates from the plan. The plan was verified against page bounds. |
| "I'll add an extra component not in the plan" | Do not improvise. If the design needs changes, go back to schematic-plan. |
| "The plan is probably outdated, I'll adapt" | The plan is the source of truth. If it's wrong, re-plan. Don't patch on the fly. |

**Exit gate:** Run ERC. Zero violations → proceed. Violations →
invoke verification.

**Commit strategy:** Commit after schematic-design completes and
ERC passes (not after each chunk). If ERC fails, fix violations via
verification skill, then commit the clean state. Do not commit
intermediate broken states.

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

<HARD-GATE>
No phase proceeds past verification without zero violations.
"Probably clean" is not evidence. Run the check, show the output,
confirm violation_count = 0.
</HARD-GATE>

- **Verification-before-completion pattern:** Before claiming
  ERC/DRC is clean, the skill must show actual tool output with
  `violation_count: 0`. No claims without fresh evidence.
- **Re-run after fixes:** Previous results are stale after fixes.
  Loop until zero.
- **Two gate points:** Invoked after schematic-design (ERC) and
  after pcb-layout (DRC). Same skill, different context.
- **Stuck escalation:** If ERC/DRC violations cannot be resolved
  (e.g., requires a design change that invalidates the schematic
  plan), report the situation to the user. Options: re-run from
  schematic-plan with updated constraints, accept known violations
  with user approval, or abort.

### 6. `pcb-layout` — Pre-flight Checks Added

Current content stays (placement strategy, trace width tables,
routing rules, layer stack, copper zones, DRC workflow).

**Pre-flight checks added:**
1. Verify ERC gate was cleared (run_erc output with violation_count=0)
2. For each schematic component, call `list_lib_footprints` to
   verify assigned footprint exists
3. If `specs/bom.md` exists, cross-reference footprints against
   BOM's footprint column
4. Check board size constraints from BOM artifact (if present)
5. If BOM specifies IPC design class, configure design rules
   (trace width, spacing, annular ring) to match class requirements

**Export deliverables (post-DRC):**
- Gerber set (copper layers, mask, silkscreen, drill, board outline)
- Drill files
- BOM export
- Pick-and-place file
- 3D model (STEP) for mechanical review
- Fabrication notes: layer stack, copper weight, finish, IPC class,
  solder mask color
- Post-export verification: confirm Gerber layer count matches
  design, BOM export matches schematic BOM

**Extensibility note:** For complex boards (>30 components, 4+
layers, high-speed signals), consider creating a `pcb-plan` artifact
following the same pattern as `schematic-plan`. For simpler boards,
pre-flight checks and existing placement strategy are sufficient.

## Reviewer Subagent Prompts

Two reviewer agent prompts, dispatched via the Agent tool at gate
checkpoints. Reviewers are dispatched for ALL artifacts — whether
AI-generated or user-provided. A user-provided BOM still gets the
BOM reviewer. A user-provided placement plan still gets the schematic
plan reviewer. The input is accepted, then audited.

**Invocation pattern:** The orchestrating skill reads the reviewer
prompt file and passes its content as the Agent tool's `prompt`
parameter, along with the artifact path and project path. The
reviewer subagent has access to all KiCad MCP tools. Example:

```
Agent(
  prompt="<contents of agents/bom-reviewer.md>\n\nBOM path: /path/to/specs/bom.md",
  subagent_type="general-purpose"
)
```

### BOM Reviewer (`agents/bom-reviewer.md`)

Dispatched after circuit-design writes `specs/bom.md`, OR when a
user-provided BOM is written to `specs/bom.md`.

<CRITICAL-DO-NOT-TRUST>
The circuit-design phase claims it verified every lib_id and
footprint. You MUST verify independently. The "(verified)" label
in the BOM columns is the author's claim — not evidence.

DO:
- Call `list_lib_symbols` yourself for EVERY row
- Call `list_lib_footprints` yourself for EVERY footprint
- Calculate derating margins yourself from the Constraints section
- Count decoupling caps yourself per IC

DO NOT:
- Trust the "(verified)" label in column headers
- Trust that a value "looks like" an E-series value without checking
- Skip rows because "Device:R is obviously valid"
- Accept the BOM because it "looks complete"
</CRITICAL-DO-NOT-TRUST>

**Checklist:**
1. For every row, call `list_lib_symbols` on the specified library.
   Confirm the symbol name exists. If not, report FAIL with what's
   available.
2. For every row, call `list_lib_footprints` on the footprint
   library. Confirm the footprint exists.
3. Every IC has at least one decoupling capacitor in the BOM.
4. Every voltage/current rating has derating margin (component
   rating > operating value from the Constraints section).
5. Input protection exists for every external interface (connectors).
6. Resistor/capacitor values are E-series preferred (E12 or E24).
7. No duplicate reference designators.
8. Reference designators follow standard format (letter prefix +
   integer, e.g., R1, C3, U2 — not C5B).

**Output:**
```
STATUS: APPROVED | ISSUES_FOUND

Issues (if any):
- [Row F1] lib_id "Device:Fuse_PTC" not found. Available: Fuse, Fuse_Small.
- [Row C5B] Reference "C5B" is non-standard. Use C11 or next available integer.
- [Row U1] No decoupling capacitor found for U1.
- [Row R3] Value 2.35K is not E-series. Nearest: 2.2K or 2.4K.
```

### Schematic Plan Reviewer (`agents/schematic-plan-reviewer.md`)

Dispatched after schematic-plan writes `specs/schematic-plan.md`, OR
when a user-provided plan is written to `specs/schematic-plan.md`.

<CRITICAL-DO-NOT-TRUST>
The schematic-plan phase claims it calculated page size correctly
and verified all pin names. You MUST verify independently.

DO:
- Calculate page bounds yourself: every Y < (page_height - margin),
  every X < (page_width - margin - title_block_width if near right edge)
- Call `get_symbol_info` yourself for every unique symbol to verify
  pin names
- Measure spacing between adjacent components yourself
- Cross-reference the placement table against the BOM — every BOM
  component must appear

DO NOT:
- Trust the declared page size without checking coordinates against it
- Trust that pin names "look right" without calling `get_symbol_info`
- Trust spacing is adequate because the coordinates "seem spread out"
- Skip the BOM cross-reference because the plan "looks complete"
</CRITICAL-DO-NOT-TRUST>

**Checklist:**
1. Every BOM component appears in the placement table (no missing
   parts).
2. Every coordinate fits within declared page size (with margins:
   10mm all sides, title block 108x32mm at bottom-right).
3. Vertical spacing between adjacent components >= 10.16mm.
4. Horizontal spacing between adjacent passives >= 12.7mm.
5. IC-to-decoupling-cap distance <= 25.4mm.
6. Inter-stage gaps >= 25.4mm.
7. No component overlaps (bounding box collision check).
8. Pin names in wiring table match actual pins (call
   `get_symbol_info` on the library file for each unique symbol —
   not `get_symbol_pins`, which requires a placed schematic).
9. Every net accounts for all pins that should be on it.
10. Page size is smallest standard size that fits (not over-sized).
11. Wiring table groups all pins per net into single calls (no
    duplicate net entries that should be batched).

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
- Max 5 iterations per reviewer invocation (BOM reviewer and
  schematic-plan reviewer each get 5 independently). After 5
  fix-and-resubmit cycles, present the user with the current
  artifact and remaining issues. Ask whether to continue fixing,
  proceed with known issues, or abort.
- Do not fix, only report. The planning skill fixes issues.
- Independently verify every claim by calling MCP tools. The author's
  assertions are hypotheses to be tested, not facts to be trusted.
- Binary outcome: APPROVED or ISSUES_FOUND. No warnings.

## Context Management

**Plan mode execution:** The schematic-design skill in plan mode
should treat `specs/schematic-plan.md` as its sole source of truth.
Do NOT reference prior conversation context about how the plan was
created, what alternatives were considered, or what the reviewer
said. Read the plan, execute the plan.

**Reviewer subagents:** Each reviewer is dispatched as a fresh
subagent with no prior context. It receives only the artifact path
and its review checklist. This prevents context pollution from the
planning phase biasing the review.

## Change Control (ISO 9001 8.3.6)

**Modification mode changes:** When modifying an existing schematic
via modification mode:
1. Before any placement: verify lib_ids via `list_lib_symbols`
2. After all modifications: re-run ERC
3. If `specs/bom.md` exists, check whether the modification makes
   the BOM stale (added/removed components). If so, warn the user
   that the BOM artifact should be updated.

This ensures modification mode — while lighter than the full
pipeline — still validates at system boundaries and maintains
artifact consistency.

## Skill Count

6 skills total (up from 5):
1. `using-kicad` (modified — pipeline enforcer)
2. `circuit-design` (modified — exit gate + BOM artifact)
3. `schematic-plan` (NEW)
4. `schematic-design` (refactored — plan executor + modification mode)
5. `pcb-layout` (modified — pre-flight checks)
6. `verification` (modified — hard gate enforcement)

Future consideration: `pcb-plan` using the same artifact/reviewer
pattern when PCB layout hits the same class of failures.

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
   in the plan before schematic-design starts.

Both failures become plan-time errors (cheap to fix) instead of
execution-time errors (9 wasted tool calls).

**Additionally addressed by this revision:**
- User-provided plans are now audited (would have caught the
  RUN.txt failures even when executing an external plan)
- Modification mode validates lib_ids (prevents hallucination in
  quick-edit scenarios)
- Wire batching is mandated (eliminates the 13 single-pin calls
  seen in RUN.txt)
- Commit timing prevents broken intermediate states
- Footprint verification happens at BOM time (IPC-7351 compliance),
  not after schematic capture
- IPC design class flows from constraints to design rules
- Export deliverables are explicit (fabrication notes, assembly
  drawing, post-export verification)
