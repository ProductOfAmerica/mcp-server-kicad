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
10. Annotate — run `annotate_schematic` to assign reference designators.
    For hierarchical designs, pass `project_path` to avoid cross-sheet
    ref conflicts.
11. Validate hierarchy — run `validate_hierarchy` from root to check
    for orphaned labels, direction mismatches, duplicate refs.
12. Run ERC

### Modification Mode (existing schematics)

No plan artifact required. User instructions serve as the plan.

**Pre-flight checks:**
1. Verify the schematic file exists
2. List current components via `list_schematic_components`
3. For each new component to be added, call `list_lib_symbols` to
   verify its lib_id exists before placement

Follow existing spacing/wiring/naming conventions in the schematic.

## Error Handling — Escalate, Don't Improvise

| Situation | Response |
|-----------|----------|
| Symbol not found | STOP. Do not fuzzy-match or substitute. Report the error. If a previously-verified symbol is missing, instruct the user to re-run from circuit-design to re-validate the BOM. |
| Position outside page bounds | STOP. Report error. The plan's page calculation should have prevented this. Instruct the user to re-run schematic-plan. |
| connect_pins fails | Try wire_pins_to_net for that connection. If that fails, report and continue with remaining wiring. |
| `wire_pins_to_net` refuses | Act on the bracketed code, per the table below. |
| ERC violations | Report violations. Invoke verification skill. |

### `wire_pins_to_net` refusals

A refused call writes nothing and lists every refused pin with a
bracketed code, the obstacle and a remedy. Fix the cause and call
again with the same pins. Never work around a refusal with `add_label`
or `add_wires`: they check none of this, so they can merge or split
nets silently.

| Code | Meaning | Do this |
|------|---------|---------|
| `validation` | An argument is malformed: an empty or padded name, a leading `/`, `${`, a KiCad auto-name such as `Net-(...)`, bus syntax, an unknown direction, a stub not a multiple of 1.27 mm or over 1000 mm | Fix the argument |
| `resolve` | The reference is not on this sheet, the pin does not exist, or a pin name matches pads at different points | Pass the pad numbers the message lists, or wire the pin on the sheet that holds its unit |
| `names` | The pin's net already carries another name | If that name is a `Net-(...)` label `connect_pins` wrote, remove it with `remove_label` as the message says, then call again. If the pin belongs on the net, call again with its name as the message shows it; otherwise stop and report |
| `touch` | Every stub and label position would touch something | Pass another `direction`, or move the part; otherwise stop and report |
| `netclass` | Wiring would move a net into another net class | Stop and report |
| `nc_type` | The pin is a no-connect type pin | Pick another pin |
| `nc_flag` | The pin has a no-connect flag | If it should be connected, `remove_no_connect`, then call again |
| `dup_ref` | Another part shares the reference somewhere in the hierarchy, or a sheet of the hierarchy cannot be read, so sharing cannot be ruled out | Give each part its own reference (`annotate_schematic` numbers `?` references), then call again. For an unreadable sheet, stop and report which one the message names |
| `bus`, `bus_entry`, `sheet_pin`, `text_var`, `jumper`, `unit0_unplaced`, `units_disagree` | The pin's net may reach something this tool cannot judge | Stop and report |
| `derived`, `unloadable` | A symbol on the sheet has pins KiCad's file does not define, or defines twice, or an angle KiCad cannot load | Stop and report |

When `auto_place_decoupling_cap` meets one of these after placing the
cap, its error says what it left on disk and the calls that remove it;
any library symbol it copied into the file stays, and no net depends on
it.

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
- [ ] Annotate schematic — no `?` refs remaining
- [ ] Validate hierarchy — no orphaned labels or duplicate refs
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

What `wire_pins_to_net` writes: a short stub away from each pin with
the label at its end, or the label on the pin end when the stub would
touch something. It never writes a junction, and a pin already on the
net is left alone. Read the result's notes: "new local net" means
nothing on this sheet carried that name, so a power symbol or global
label of that name elsewhere does not join it (use `add_power_symbol`
or `add_global_label` to reach those); on a sheet used more than once,
the wiring lands in every instance, and the note names each one.

## Power and Ground

- Use `add_power_symbol` for VCC, GND, and named power rails
- Use `wire_pins_to_net` for custom power nets (VIN_PROT, 5V_REL, etc.)
- Place decoupling capacitors visually adjacent to the IC they serve
- Add `PWR_FLAG` on every power net that would otherwise trigger
  "power pin not driven" ERC errors
- No tool places a PWR_FLAG for you, because only you know which nets have a
  source ERC cannot see. Place one with `add_power_symbol`
  (lib_id `power:PWR_FLAG`), once per net that needs it
- PWR_FLAG is only needed on nets with power_in pins and NO power_out/output pins.
  Do not add one to a net that already has a driver or a flag: ERC then reports
  two power outputs connected

## Naming

- Use **uppercase descriptive names** for nets: VIN_PROT, 5V_REL,
  SPI_MOSI
- Active-low signals: suffix with `_N` (e.g., RESET_N, CS_N)
- Do not use generic names like NET1 or WIRE3

## MCP Tools for This Skill

**Reading / inspection:**
- `get_schematic_summary` — get item counts for a sheet
- `list_schematic_components` — list symbols on a sheet
- `list_schematic_labels` — list net labels on a sheet
- `list_schematic_wires` — list wires on a sheet
- `list_schematic_global_labels` — list global labels on a sheet
- `list_schematic_hierarchical_labels` — list hierarchical labels on a sheet
- `list_schematic_sheets` — list hierarchical sheet blocks on a sheet
- `list_schematic_junctions` — list junctions on a sheet
- `list_schematic_no_connects` — list no-connect flags on a sheet
- `list_schematic_bus_entries` — list bus entries on a sheet
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
- `no_connect_pin` — mark a pin as intentionally unconnected (idempotent)
- `remove_no_connect` — clear no-connect flag(s) from a pin
- `remove_label` / `remove_wire` / `remove_junction` — cleanup (labels and global labels)

**Power and decoupling:**
- `add_power_symbol` — place VCC, GND, +3V3, PWR_FLAG, etc.
- `auto_place_decoupling_cap` — auto-place a decoupling cap near an IC

**Annotations and hierarchy:**
- `add_text` — add text annotations to the sheet
- `add_hierarchical_label` — add a hierarchical label for sheet-to-sheet connections
- `remove_hierarchical_label` — remove a hierarchical label by name or UUID
- `modify_hierarchical_label` — modify text, shape, or position of a hierarchical label
- `annotate_schematic` — auto-assign reference designators (project server)
- `remove_text` — remove text annotations by position or content

**Hierarchy management (project server):**
- `add_hierarchical_sheet` / `remove_hierarchical_sheet` — create/remove sub-sheet blocks
- `modify_hierarchical_sheet` — change sheet name, file, dimensions
- `add_sheet_pin` / `remove_sheet_pin` — manage pins on sheet blocks
- `validate_hierarchy` — check for orphaned labels/pins, direction mismatches
- `list_hierarchy` / `get_sheet_info` — inspect hierarchy structure
- `is_root_schematic` — check if a schematic is root or sub-sheet
- `move_hierarchical_sheet` — reposition a sheet block on the canvas
- `reorder_sheet_pages` — change the page order of hierarchical sheets
- `duplicate_sheet` — create a copy of a sheet with new UUIDs
- `flatten_hierarchy` — merge all sub-sheets into a single flat schematic
- `trace_hierarchical_net` — trace a net across the hierarchy for debugging
- `export_hierarchical_netlist` — export netlist with hierarchy info
  (use instead of `export_netlist` for hierarchical designs)

## Hierarchy Workflow Sequences

### Sequence: Setting up a hierarchical design
1. `add_hierarchical_sheet` — create the sub-sheet block on the parent
2. `add_sheet_pin` — add pins to the sheet block for each signal crossing the boundary
3. In the child schematic: `add_hierarchical_label` for each pin
4. `validate_hierarchy` — verify pins match labels, no orphans

### Sequence: Duplicating a channel (e.g., 4 identical sensor channels)
1. Design the first channel as a sub-sheet
2. `duplicate_sheet` — creates a copy with new UUIDs and a new file
3. Repeat for each additional channel
4. `annotate_schematic` with `project_path` — assigns unique refs across all copies

### Sequence: Pre-ERC annotation and validation
1. `annotate_schematic` with `project_path` — assign refs to all `?` components
2. `validate_hierarchy` from root — check for duplicates, orphans, mismatches
3. If duplicates: `set_component_property` to reset to `X?`, then re-annotate
4. `run_erc` — only after validate_hierarchy is clean

### Sequence: Debugging cross-sheet connectivity
1. `trace_hierarchical_net` — follow the net across sheet boundaries
2. `get_net_connections` — check connections on each sheet
3. `list_cross_sheet_nets` — see all nets crossing boundaries
4. Fix with `add_sheet_pin`, `add_hierarchical_label`, or `connect_pins`

### Sequence: Restructuring hierarchy
1. `flatten_hierarchy` — merge all sheets into one (creates new file, non-destructive)
2. OR `move_hierarchical_sheet` — reposition sheet blocks on the parent
3. OR `reorder_sheet_pages` — change page numbering order
4. `validate_hierarchy` — verify structure is still valid

### Sequence: Cleaning up after modifications
1. `remove_text` — delete temporary text annotations
2. `validate_hierarchy` — check for new orphaned labels or direction mismatches
3. `annotate_schematic` — reassign any `?` refs introduced during modifications

**Verification and export:**
- `run_erc` — run electrical rules check
- `export_schematic` — export schematic as PDF/SVG
- `export_netlist` — generate a netlist file (PCB import itself is `update_pcb_from_schematic` on the PCB server)
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
3.5. If components have `?` references, run `annotate_schematic` with
     `project_path` before re-running ERC.
4. Re-run ERC until clean
