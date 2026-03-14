---
name: circuit-design
description: >
  Use when designing an electronic circuit from requirements, choosing
  topology or regulator type, selecting component values, planning
  protection circuits, or deciding what goes on a board before schematic
  capture begins. Also use when the user asks "what components do I need"
  or "how should I power this."
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

# Circuit Design

Design the circuit completely — every component, every value, every
rating — before opening the schematic editor. This skill produces a
**component list and block diagram** that feeds directly into the
schematic-design skill.

## Response Format

When this skill activates, print exactly:

> Using circuit-design to design the circuit before schematic capture.

Then, if the user has not already described what they want, ask
exactly:

> What circuit are we designing? I need:
> 1. **Function** — what does it do?
> 2. **Input power** — voltage range, AC/DC
> 3. **Output requirements** — voltages, currents, signals
> 4. **Environment** — indoor/outdoor, temperature, enclosure
> 5. **Specific parts** — any components you already want to use?
>
> Or if you have a BOM already, I can run validation-only mode.

Do not list what you found in the project. Do not suggest example
circuits or subsystems. Wait for the user's answer before proceeding
to the Process checklist.

If the user already described the circuit (in the same message that
invoked this skill, or in prior conversation), skip the question and
proceed directly to step 1 of the Process.

## Process

1. **Clarify requirements** — voltage, current, environment, cost,
   form factor. Ask the user if anything is ambiguous.
2. **Gather constraints** — before topology selection:
   - Board size constraints (if known)
   - Mechanical constraints (connector positions, mounting holes)
   - Thermal constraints (ambient temp, enclosure, airflow)
   - EMI/EMC requirements
   - Target manufacturer capabilities (trace width, layer count)
   - IPC design class (1 = consumer, 2 = industrial, 3 = high-rel).
     Default to Class 2 if unknown. This determines trace width,
     spacing, and annular ring minimums downstream.
   Record in BOM artifact header. Use defaults for unknowns.
3. **Block diagram** — decompose into functional stages. Name each
   stage and define the interface between them (voltage, signal type).
4. **Topology selection** — choose a circuit topology for each block.
5. **Component selection** — pick every part with value, rating, and
   package. Verify against KiCad symbol libraries.
6. **Protection and filtering** — add input protection, decoupling,
   and EMI filtering. These are not optional.
7. **Bill of materials** — produce the final BOM table.
8. **Hand off** to schematic-design for placement and wiring.

## Topology Quick Reference

| Need | First choice | When to upgrade |
|------|-------------|-----------------|
| Fixed step-down, <500mA | LDO (e.g., AMS1117, LM1117) | Dropout < 1V, low noise needed |
| Fixed step-down, 0.5–3A | Buck converter (e.g., LM2596, MP1584) | Efficiency matters, dropout > 1V |
| Step-down, >3A | Synchronous buck (e.g., TPS54360) | High current, tight regulation |
| Step-up | Boost converter (e.g., MT3608, TPS61040) | Vout > Vin required |
| Negative voltage | Inverting buck-boost or charge pump | Analog circuits needing -V |
| Isolation | Flyback with optocoupler | Safety, galvanic isolation |
| Relay/solenoid drive | N-ch MOSFET low-side switch | Inductive load, >50mA |
| Motor (brushed DC) | H-bridge driver IC | Bidirectional control needed |
| LED constant current | Linear CC driver or buck CC | String voltage determines choice |
| Signal level shift | BSS138 MOSFET level shifter | Bidirectional, I2C/SPI |
| ADC input | Voltage divider + buffer opamp | High-impedance source |

These are starting points. Always verify against the specific
requirements — an LDO is wrong if the dropout is too high even if
current is <500mA.

## Protection Patterns

Every board needs protection. Select from these standard patterns:

**Input protection (power entry):**
- Fuse → TVS diode → bulk capacitor
- Fuse: rated 1.5–2x normal operating current
- TVS: clamp voltage below max Vin of downstream regulator
- Bulk cap: 10–100uF electrolytic/ceramic at input

**Reverse polarity protection:**
- P-ch MOSFET (preferred, low loss) — gate to ground, source to Vin
- Series Schottky diode (simple, 0.3–0.5V drop)

**ESD protection (connectors):**
- TVS array on data lines (USB, UART, SPI exposed externally)
- Place physically close to the connector

**Inductive load protection:**
- Flyback diode (1N4148 for small relays, 1N4007 for larger)
- Diode cathode to supply, anode to drain

## Component Selection Rules

**Voltage rating:** Derate to 60–80% of rated voltage. A 16V cap on
a 12V rail, not a 16V cap on a 15V rail.

**Current rating:** Derate to 70–80%. A 2A fuse for 1.2A continuous.

**Capacitor values from datasheets:** For regulators, the datasheet
specifies input and output capacitor values and ESR requirements.
Use those values, not arbitrary ones. If the datasheet says 22uF
low-ESR on the output, use 22uF low-ESR.

**Resistor values from datasheets:** Feedback dividers, current-sense
resistors, and timing resistors are calculated from datasheet
formulas. Show the formula and calculation for each.

**Preferred values:** Use E12 or E24 series values (1.0, 1.2, 1.5,
1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2). Do not specify
values like 2.35k — round to nearest preferred value (2.2k or 2.4k)
and recalculate to verify the result is within tolerance.

**Packages:** Default to 0805 for resistors and capacitors unless
space-constrained (then 0603). Use through-hole for prototyping if
the user requests it.

## Verifying Symbols AND Footprints (Mandatory)

Before finalizing the BOM, you MUST confirm each component has BOTH a
symbol and a footprint in KiCad's libraries. This is blocking — do not
proceed until every row is verified.

For each component:
1. Call `list_lib_symbols` on the expected library — confirm the
   exact symbol name exists
2. Call `list_lib_footprints` on the expected footprint library —
   confirm the footprint exists
3. If either is not found, resolve immediately — pick a generic
   symbol/footprint or create a custom one via `add_symbol`
4. Record the verified `lib_id` and footprint in the BOM

Common symbol libraries:
- `Regulator_Linear.kicad_sym` — LDOs, linear regulators
- `Regulator_Switching.kicad_sym` — buck, boost, flyback controllers
- `Device.kicad_sym` — R, C, L, D, LED, fuse, ferrite bead
- `Transistor_FET.kicad_sym` — MOSFETs
- `Transistor_BJT.kicad_sym` — BJTs
- `Connector_Generic.kicad_sym` — pin headers, screw terminals
- `Relay.kicad_sym` — relays
- `Diode.kicad_sym` — diodes, TVS, Zener, Schottky
- `power.kicad_sym` — VCC, GND, +3V3, +5V, etc.

If an exact part is not in the library:
- **Generic symbol**: Use a generic symbol with the correct pin
  count and set the value property to the actual part number.
- **Custom symbol via `add_symbol`**: For ICs and complex parts,
  use the `add_symbol` MCP tool to create a custom symbol in a
  project-local .kicad_sym library. NEVER write .kicad_sym files
  with the Write or Edit tools — always use `add_symbol`.

## MCP Tools for This Skill

These are the kicad MCP tools you should be using during circuit design:

**Library browsing (verify parts exist):**
- `list_lib_symbols` — list symbols in a .kicad_sym library
- `get_symbol_info` — get pin names, types, and properties for a symbol

**Custom symbol creation (when parts aren't in built-in libs):**
- `create_symbol_library` — create a new .kicad_sym library file
- `add_symbol` — define a custom symbol with pins, footprint, datasheet

**Project setup:**
- `create_project` — create a new KiCad project (.kicad_pro)
- `create_schematic` — create a new schematic sheet
- `create_sym_lib_table` — register symbol libraries with the project

**Footprint verification:**
- `list_lib_footprints` — list footprints in a .pretty directory
- `get_footprint_info` — check pad dimensions and pin mapping

**Visual verification (optional):**
- `export_symbol_svg` — export symbol library to SVG for visual review
- `export_footprint_svg` — export footprint library to SVG for visual review

**Library maintenance:**
- `upgrade_symbol_lib` — upgrade symbol library to current KiCad format
- `upgrade_footprint_lib` — upgrade footprint library to current format

## Output: BOM Artifact (`specs/bom.md`)

Write the validated BOM to `specs/bom.md` in the KiCad project
directory (the directory containing the `.kicad_pro` file).

```markdown
# BOM: [Project Name]

## Constraints
- Input: [voltage range]
- Board size: [dimensions] (if known, else "TBD")
- Layers: [count] (default: 2)
- IPC class: [1/2/3] (default: 2)
- Manufacturer min trace: [width] (default: 0.15mm)
- Thermal: [ambient temp, enclosure, airflow] (if known)
- EMI/EMC: [requirements] (if any)

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

**Every component must appear in this table before the next phase
begins.** This includes:
- Decoupling caps for every IC
- Feedback resistors with calculated values
- Bootstrap caps if the regulator requires them
- Pull-up/pull-down resistors
- Test points (if needed)
- Mounting holes (if needed)

## BOM Reviewer

After writing `specs/bom.md`, dispatch the BOM reviewer subagent:

```
Agent(
  prompt="<contents of agents/bom-reviewer.md>\n\nBOM path: <project_dir>/specs/bom.md",
  subagent_type="general-purpose"
)
```

Fix any issues reported by the reviewer, update `specs/bom.md`, and
re-dispatch until the reviewer returns APPROVED.

<HARD-GATE>
User must explicitly approve the BOM before schematic-plan can
start. "I already know the components" is not approval.
</HARD-GATE>

**User rejection handling:** If the user rejects the BOM or requests
changes, fix the specific issues and re-run the BOM reviewer on the
updated artifact. Do not restart from scratch unless the user asks.

## Validation-Only Mode (User-Provided BOM)

When the user provides their own component list or BOM, skip topology
and component selection but still validate everything:

1. **Skip:** Steps 3–6 (block diagram, topology selection, component
   selection, protection patterns) — the user has already made these
   decisions.
2. **Do:** Gather constraints (Step 2) — fill missing fields with
   defaults (IPC Class 2, 2-layer, 0.15mm min trace, etc.).
3. **Do:** For every component in the user's list, call
   `list_lib_symbols` to resolve the exact lib_id and
   `list_lib_footprints` to resolve the footprint.
4. **Do:** Fill in missing BOM columns (Rating, Package, Stage) with
   reasonable defaults or ask the user.
5. **Do:** Write `specs/bom.md` in the standard artifact format.
6. **Do:** Dispatch the BOM reviewer subagent.

The user's BOM is accepted, then audited. Validation-only mode skips
*decisions* but does NOT skip *verification*.

## Rationalization Prevention

| Thought | Reality |
|---------|---------|
| "This symbol name is probably right" | Call `list_lib_symbols`. "Probably" caused the Q_PMOS_GSD failure. |
| "Standard 100nF decoupling is fine for this IC" | Read the datasheet. The IC specifies what it needs. |
| "I know this footprint exists" | Call `list_lib_footprints`. Verify, don't assume. |
| "The rating is close enough" | Show the derating math. 60-80% of rated voltage, 70-80% of rated current. |

## Checklist

**IMPORTANT: Use TodoWrite to create todos for EACH checklist item below.**

- [ ] Clarify requirements and gather constraints
- [ ] Create block diagram with named stages
- [ ] Select topology for each stage
- [ ] Select components with values and ratings
- [ ] Add protection and filtering circuits
- [ ] Verify ALL lib_ids via `list_lib_symbols`
- [ ] Verify ALL footprints via `list_lib_footprints`
- [ ] Write `specs/bom.md` artifact
- [ ] Dispatch BOM reviewer subagent
- [ ] Get user approval on BOM

## Handoff to Schematic Plan

The BOM artifact is the input to the schematic-plan skill. Before
handing off, verify:

- [ ] `specs/bom.md` exists and BOM reviewer returned APPROVED
- [ ] User has explicitly approved the BOM
- [ ] Every IC has decoupling capacitors listed
- [ ] Every calculated value shows its formula
- [ ] Every voltage/current rating has derating margin
- [ ] Protection components are included for every external interface
- [ ] All lib_ids verified via `list_lib_symbols`
- [ ] All footprints verified via `list_lib_footprints`
- [ ] Block diagram shows signal flow and named nets between stages
