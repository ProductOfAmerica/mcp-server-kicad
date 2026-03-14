---
name: schematic-design
description: >
  Use when executing a schematic placement plan (plan mode) or
  modifying an existing schematic (modification mode).
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

# KiCad Schematic Design — Plan Executor

This skill executes schematic plans mechanically. All intelligence
(component selection, coordinate calculation, wiring decisions)
happened in prior phases. This skill reads a plan and executes it.

## Response Format

When this skill activates, announce the mode:

- **Plan mode:** Print exactly: "Using schematic-design to execute
  the placement plan."
- **Modification mode:** Print exactly: "Using schematic-design to
  modify the existing schematic."

Then proceed directly to the pre-flight checks for the appropriate
mode. Do not ask the user what to do — the plan or user instructions
are the input. Report progress as each placement/wiring step
completes, but keep updates brief (one line per step).

## Two Modes

### Plan Mode (new designs)

Reads `specs/schematic-plan.md` and executes mechanically. The plan
file is the sole source of truth — ignore prior conversation context
about how the plan was created. Read the plan, execute the plan.

**Pre-flight checks:**
1. Verify `specs/schematic-plan.md` exists and its reviewer returned
   APPROVED
2. Read page size from plan, call `set_page_size` immediately if
   not A4
3. Verify project/schematic files exist (create if needed)
4. If plan references custom symbol library, verify it exists via
   `list_lib_symbols`

**Execution order:**
1. Project setup (`create_project`, `create_schematic`, etc.)
2. Set page size (if plan specifies non-A4)
3. Register symbol libraries (`create_sym_lib_table`)
4. Create custom symbols (`add_symbol`)
5. Place all components (`place_component` per coordinate table)
6. Wire all connections per wiring table — group all pins for the
   same net into a single `wire_pins_to_net` call
7. Add no-connect flags
8. Add power flags
9. Add hierarchical sheets (if applicable)
10. Run ERC

### Modification Mode (existing schematics)

No plan artifact required. User instructions serve as the plan.

**Pre-flight checks:**
1. Verify the schematic file exists
2. List current components via `list_schematic_items`
3. For each new component to be added, call `list_lib_symbols` to
   verify its lib_id exists before placement

Follow existing spacing/wiring/naming conventions in the schematic.

## Error Handling — Escalate, Don't Improvise

| Situation | Response |
|-----------|----------|
| Symbol not found | STOP. Do not fuzzy-match or substitute. Report the error. If a previously-verified symbol is missing, instruct the user to re-run from circuit-design to re-validate the BOM. |
| Position outside page bounds | STOP. Report error. The plan's page calculation should have prevented this. Instruct the user to re-run schematic-plan. |
| connect_pins fails | Try wire_pins_to_net for that connection. If that fails, report and continue with remaining wiring. |
| ERC violations | Report violations. Invoke verification skill. |

## Checklist (Plan Mode)

**IMPORTANT: Use TodoWrite to create todos for EACH checklist item below.**

- [ ] Verify `specs/schematic-plan.md` exists and is APPROVED
- [ ] Pre-flight: verify project/schematic files exist
- [ ] Set page size (if non-A4)
- [ ] Register symbol libraries
- [ ] Create custom symbols (if needed)
- [ ] Place all components per coordinate table
- [ ] Wire all connections per wiring table
- [ ] Add no-connect flags
- [ ] Add power flags and PWR_FLAG symbols
- [ ] Run ERC — must show zero violations

## Rationalization Prevention

| Thought | Reality |
|---------|---------|
| "This pin name is close enough" | Use the exact pin name from the plan. The plan was verified against `get_symbol_info`. |
| "I can adjust the coordinates slightly" | Use the exact coordinates from the plan. The plan was verified against page bounds. |
| "I'll add an extra component not in the plan" | Do not improvise. If the design needs changes, go back to schematic-plan. |
| "The plan is probably outdated, I'll adapt" | The plan is the source of truth. If it's wrong, re-plan. Don't patch on the fly. |

## Exit Gate

Run `run_erc`. Zero violations → proceed to pcb-layout.
Violations → invoke verification skill.

**Commit strategy:** Commit after schematic-design completes and
ERC passes (not after each chunk). If ERC fails, fix violations via
verification skill, then commit the clean state. Do not commit
intermediate broken states.

## Placement Spacing Reference

All coordinates are on the 1.27mm grid (auto-snapped by the tools).

Minimum spacing (for modification mode and plan verification):
- **Vertical between components**: 10.16mm (8 grid units)
- **Horizontal between passives in a row**: 12.7mm (10 grid units)
- **IC to its supporting passives**: 25.4mm (20 grid units) horizontal
- **Between functional stages**: 25.4–50.8mm gap

Title block clearance: ~108x32mm at bottom-right corner. On A4
landscape (297x210mm), keep components within X < 180mm and
Y < 175mm.

## Wiring Strategy Reference

**Use `connect_pins`** (direct Manhattan wire) when:
- Two pins are within ~25mm of each other
- The wire path is visually obvious (one L-shaped segment)

**Use `wire_pins_to_net`** (batch: multiple pins to one net label) when:
- A net connects 3 or more components that are not all adjacent
- The net spans across functional stages
- Power and ground rails — always use net labels, never daisy-chain

## Power and Ground

- Use `add_power_symbol` for VCC, GND, and named power rails
- Use `wire_pins_to_net` for custom power nets (VIN_PROT, 5V_REL, etc.)
- Place decoupling capacitors visually adjacent to the IC they serve
- Add `PWR_FLAG` on every power net that would otherwise trigger
  "power pin not driven" ERC errors

## Naming

- Use **uppercase descriptive names** for nets: VIN_PROT, 5V_REL,
  SPI_MOSI
- Active-low signals: suffix with `_N` (e.g., RESET_N, CS_N)
- Do not use generic names like NET1 or WIRE3

## MCP Tools for This Skill

**Reading / inspection:**
- `list_schematic_items` — list symbols, wires, labels, etc. on a sheet
- `get_symbol_pins` — get pin names and types for a placed symbol
- `get_pin_positions` — get placed pin coordinates (for wiring)
- `get_net_connections` — trace a net to see what's connected
- `list_unconnected_pins` — find pins that need wiring or no-connect

**Placing components:**
- `place_component` — place a symbol instance on the schematic
- `move_component` — reposition a placed component
- `remove_component` — delete a placed component
- `set_component_property` — change reference, value, footprint, etc.
- `set_page_size` — resize the schematic sheet
- `add_lib_symbol` — load a symbol from a library into the schematic

**Wiring and connectivity:**
- `connect_pins` — direct Manhattan wire between two pins
- `wire_pins_to_net` — connect multiple pins to a named net label
- `add_wires` — add raw wire segments by coordinate
- `add_label` — place a net label
- `add_global_label` — place a global (cross-sheet) label
- `add_junctions` — add junction dots at wire intersections
- `no_connect_pin` — mark a pin as intentionally unconnected
- `remove_label` / `remove_wire` / `remove_junction` — cleanup

**Power and decoupling:**
- `add_power_symbol` — place VCC, GND, +3V3, PWR_FLAG, etc.
- `auto_place_decoupling_cap` — auto-place a decoupling cap near an IC

**Annotations and hierarchy:**
- `add_text` — add text annotations to the sheet
- `add_hierarchical_label` — add a hierarchical label for sheet-to-sheet connections
- `remove_hierarchical_label` — remove a hierarchical label by name or UUID
- `modify_hierarchical_label` — modify text, shape, or position of a hierarchical label
- `annotate_schematic` — auto-assign reference designators (project server)

**Hierarchy management (project server):**
- `add_hierarchical_sheet` / `remove_hierarchical_sheet` — create/remove sub-sheet blocks
- `modify_hierarchical_sheet` — change sheet name, file, dimensions
- `add_sheet_pin` / `remove_sheet_pin` — manage pins on sheet blocks
- `validate_hierarchy` — check for orphaned labels/pins, direction mismatches
- `list_hierarchy` / `get_sheet_info` — inspect hierarchy structure
- `is_root_schematic` — check if a schematic is root or sub-sheet

**Verification and export:**
- `run_erc` — run electrical rules check
- `export_schematic` — export schematic as PDF/SVG
- `export_netlist` — generate netlist for PCB import
- `export_bom` — export bill of materials

**Symbol authoring (when a part isn't in KiCad's libraries):**
- `create_symbol_library` — create a project-local .kicad_sym file
- `add_symbol` — define a custom symbol with pins, footprint, datasheet
- `list_lib_symbols` / `get_symbol_info` — browse available symbols

## Custom Symbols

When a part is not in KiCad's built-in libraries, create a custom
symbol using MCP tools — NEVER by writing .kicad_sym files directly:

1. `create_symbol_library` — create a project-local .kicad_sym file
   (if it doesn't exist yet)
2. `add_symbol` — define the symbol with pin names, pin types, pin
   numbers, footprint, and datasheet
3. `add_lib_symbol` — load the custom symbol into the schematic

## Verification

After completing placement and wiring:
1. Run `run_erc` to check for violations
2. Fix "power pin not driven" with PWR_FLAG symbols
3. Fix unconnected pins with `wire_pins_to_net` or `no_connect_pin`
4. Re-run ERC until clean
