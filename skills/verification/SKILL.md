---
name: verification
description: >
  Use when running ERC or DRC checks, fixing electrical or design rule
  violations, debugging unconnected nets, resolving "power pin not driven"
  errors, or preparing a design for manufacturing export. Also use when
  the user says "my ERC has errors" or "DRC is failing."
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

<HARD-GATE>
No phase proceeds past verification without zero violations.
"Probably clean" is not evidence. Run the check, show the output,
confirm violation_count = 0.
</HARD-GATE>

# KiCad Design Verification

Systematic workflows for fixing ERC and DRC violations. Run these
checks after completing schematic capture or PCB layout — never skip
them.

## Response Format

When this skill activates, announce which check is being run:

- **ERC gate:** Print exactly: "Running ERC verification on the
  schematic."
- **DRC gate:** Print exactly: "Running DRC verification on the
  PCB."

Then proceed directly to running the check. Do not ask the user what
to do. Report violations found, then fix them systematically per the
workflow below.

## MCP Tools for This Skill

These are the kicad MCP tools you should be using during verification:

**Schematic checks:**
- `run_erc` — run electrical rules check, returns all violations.
  Auto-redirects sub-sheets to root for full hierarchy context.
- `list_unconnected_pins` — find unconnected pins by component.
  Auto-redirects sub-sheets to root to avoid false positives.
- `get_net_connections` — trace a net to debug connectivity issues
- `validate_hierarchy` — check for unannotated refs, duplicate refs,
  orphaned labels/pins, direction mismatches, junctions and bus entry
  ends left inside wires, and no-connect flags and wire ends left off
  the pins of mirrored, rotated parts (run from root schematic)
- `trace_hierarchical_net` — trace a net across sheet boundaries to
  debug cross-sheet connectivity issues

**Schematic fixes:**
- `add_power_symbol` — place PWR_FLAG to fix "power pin not driven"
- `connect_pins` — wire two pins together
- `wire_pins_to_net` — connect pins to a named net
- `no_connect_pin` — mark intentionally unused pins
- `remove_label` — remove misplaced labels
- `set_page_size` — resize sheet when components exceed page boundary
- `annotate_schematic` — auto-assign reference designators to unannotated
  components (pass `project_path` for hierarchical designs)
- `set_component_property` — reset duplicate references to `X?` before
  re-annotation
- `remove_text` — remove leftover text annotations from debug/workaround
  operations

**PCB checks:**
- `run_drc` — run design rules check, returns all violations
- `get_board_info` — verify board setup and design rules
- `list_pcb_footprints` — list footprints on the board
- `list_pcb_traces` — list traces on the board
- `list_pcb_nets` — list nets on the board
- `list_pcb_zones` — list zones on the board
- `list_pcb_layers` — list layers on the board
- `list_pcb_graphic_items` — list graphic items on the board
- `validate_board` — check all footprints against keep-out zones and board edge

**PCB fixes:**
- `add_trace` — route missing connections
- `add_via` — add vias for layer transitions
- `move_footprint` — fix clearance violations by repositioning
- `set_trace_width` — fix trace width violations
- `remove_traces` — remove problematic traces for re-routing
- `add_copper_zone` — add missing ground planes / copper fills
- `fill_zones` — recompute zone fills after changes
- `remove_dangling_tracks` — clean up unconnected trace stubs

**Export (post-verification):**
- `export_gerbers` — Gerber manufacturing files
- `export_positions` — pick-and-place file
- `export_bom` — bill of materials
- `export_3d` — 3D model for mechanical review
- `export_schematic` — schematic PDF/SVG
- `export_pcb` — PCB PDF/SVG
- `export_ipc2581` — IPC-2581 manufacturing data (alternative to Gerbers
  for some fabs)
- `export_hierarchical_netlist` — netlist with hierarchy info for
  hierarchical designs (use instead of `export_netlist` when design has
  sub-sheets)

## ERC Workflow (Schematic)

### Step 0: Pre-ERC Hierarchy Validation

Run `validate_hierarchy` on the root schematic before ERC. This catches
issues that kicad-cli ERC does not report: unannotated components,
duplicate reference designators across sheets, orphaned hierarchical
labels/pins, and direction mismatches.

**If `unannotated_ref` issues:**
1. Run `annotate_schematic(schematic_path, project_path="path/to/project.kicad_pro")`
   — the `project_path` param scans the hierarchy to avoid cross-sheet conflicts
2. Re-run `validate_hierarchy` to confirm

**If `duplicate_ref` issues:**
`annotate_schematic` only assigns numbers to `?` refs — it does NOT fix
duplicates. To fix:
1. Reset the conflicting ref to `X?` with `set_component_property`:
   `set_component_property(reference="R1", key="Reference", value="R?",
   schematic_path="sheet_with_duplicate.kicad_sch")`
2. Run `annotate_schematic` with `project_path` to assign a unique number
3. Re-run `validate_hierarchy` to confirm

**If `orphaned_label`, `orphaned_pin`, or `direction_mismatch`:**
Fix with `add_sheet_pin`/`remove_sheet_pin` or `modify_hierarchical_label`.

**If `junction_inside_wire` or `bus_entry_inside_wire`:** an earlier
version of this package left a junction or a bus entry end inside a
wire, and kicad-cli 9 (ERC, netlist, PCB update) cuts the wire there.
Make the call each issue names in `repair`, exactly as given, then
re-run `validate_hierarchy` to confirm.

**If `no_connect_off_pin` or `wire_end_off_pin`:** an earlier version
put a no-connect flag or a wire end where it computed a pin of a part
rotated 90 or 270 degrees and mirrored, and KiCad draws that pin at
`pin_x`, `pin_y`, so the flag or the wire misses it. For a wire end
with a `repair`, make that call exactly as given: it joins the pin to
what the wire reaches, and leaves the old wire where it is (if that
wire then dangles, `run_erc` lists it and `remove_wire` removes it).
`validate_hierarchy` checked that call before naming it; `connect_pins`
checks nothing itself, so for it the report asks what `wire_pins_to_net`
asks of a pin. A flag always carries a `note` (below), since no tool
removes a flag by its position.

**If an issue carries a `note` and no `repair`:** no tool call fixes it,
and the note says why. Do not work around it with other tools (removing,
redrawing or moving wires, junctions or bus entries). Report the note to
the user and wait, as in stuck escalation below:
- `unreadable_file`: the named sheet file cannot be parsed, so none of
  its checks ran. The user must restore or repair that file.
- A sheet KiCad cannot load (a symbol or pin at an angle KiCad does not
  store): the user applies the remedy the note names in KiCad.
- A bus entry end where KiCad allows no junction: the user opens the
  schematic in KiCad 9's editor, which splits the line on load, makes
  any edit and undoes it, then saves. Saving with nothing edited writes
  nothing to a schematic already in KiCad 9's format.
- `references_unchecked`: the hierarchy has too many sheet instances
  for the reference check; tell the user it was not completed.
- `no_connect_off_pin`: the user deletes the flag at `x`, `y` in
  KiCad's editor. After that, make the `no_connect_pin` call the note
  names, if it names one.
- `wire_end_off_pin` with a note: the call that would join the pin is
  refused, or what the wire was meant to join is unknown; the note
  says which, and a refusal names its reason (the pin and the wire's
  net carry two names, the pin has a no-connect flag, the route would
  touch another part). Do not make the refused call, or `connect_pins`
  or `add_wires` in its place: they skip the check that refused it, and
  a join they make can short two named nets with `validate_hierarchy`
  then reporting ok. The user decides how the pin is wired.
After the user acts, re-run `validate_hierarchy`.

**Debug cross-sheet nets:** Use `trace_hierarchical_net` to follow a net
across sheet boundaries when investigating connectivity violations that
span multiple sheets.

Proceed to Step 1 only when `validate_hierarchy` returns `status: "ok"`.

### Step 1: Run ERC

Use `run_erc` to get the full violation list. Read every violation
before fixing anything — some errors share a root cause.

**Sub-sheet note:** `run_erc` and `list_unconnected_pins` automatically
redirect sub-sheets to the root schematic for full hierarchy context.
This eliminates false positives from missing parent connections
(hierarchical label errors, dangling wire errors). The result is
filtered to only include violations from the target sub-sheet, and a
`"note"` field indicates when redirection occurred.

### Step 2: Fix by Category

Work through violations in this order. Fixing earlier categories
often eliminates later ones.

**Category 1: Power pin not driven**

The most common ERC error. A power input pin has no driving source.

| Cause | Fix |
|-------|-----|
| Regulator output has no PWR_FLAG | Add `PWR_FLAG` on the output net |
| Battery/connector supplies power but pin type is passive | Add `PWR_FLAG` on the supply net |
| Power symbol not connected to anything | Wire the power symbol to the net |
| Custom symbol pin type is wrong | Fix the symbol: set pin type to "Power output" |

Use `add_power_symbol` to place PWR_FLAG. Connect it to the net with
`wire_pins_to_net` or `connect_pins`.

**No automatic PWR_FLAG:** neither `wire_pins_to_net` nor
`add_power_symbol` inserts one, because whether a net needs a flag
depends on its real source, which only the design knows. Place each
flag yourself, one per net that needs it.

**Where to place PWR_FLAG manually:** On every net that is driven by
something KiCad does not recognize as a power source — regulator
outputs, battery terminals, connector pins providing external power.

**Category 2: Unconnected pins**

| Cause | Fix |
|-------|-----|
| Pin should be connected | Wire it: `connect_pins` or `wire_pins_to_net`. If `wire_pins_to_net` refuses, act on its bracketed code (schematic-design's refusal table), never by hand with `add_label` |
| Pin is intentionally unused | Mark it: `no_connect_pin` |
| Wire just misses the pin | Check pin position with `get_pin_positions` (it reports where KiCad draws the pin, rotation and mirror included), rewire |

Use `list_unconnected_pins` to get a precise list of which pins are
unconnected and on which components.

**Category 3: Different net names on same wire**

A wire connects two differently-named nets, creating a conflict.

| Cause | Fix |
|-------|-----|
| Two labels on the same wire segment | Remove one label with `remove_label` |
| Intended connection between named nets | Use one consistent name, rename the other |

**Category 4: Pin type conflicts**

KiCad warns when incompatible pin types connect (e.g., two outputs
driving the same net).

| Cause | Fix |
|-------|-----|
| Two power outputs on same net | Correct if intentional (e.g., parallel regulators); add PWR_FLAG |
| Output driving another output | Check the circuit — usually a design error |
| Bidirectional pin warnings | Usually safe to ignore if the circuit is correct |

### Step 3: Re-run ERC

After fixing all violations, run `run_erc` again. Repeat until the
violation count is zero. Do not leave warnings unaddressed — each
one is either a real problem or needs an explicit no-connect.

**Verification-before-completion:** Before claiming ERC is clean, you
must show actual tool output with `violation_count: 0`. No claims
without fresh evidence. Previous results are stale after fixes.

**Stuck escalation:** If ERC violations cannot be resolved (e.g.,
requires a design change that invalidates the schematic plan), report
the situation to the user. Options:
- Re-run from schematic-plan with updated constraints
- Accept known violations with explicit user approval
- Abort

## DRC Workflow (PCB)

### Step 1: Run DRC

Use `run_drc` to get the full violation list.

### Step 2: Fix by Category

**Category 1: Clearance violations**

Trace, pad, or via too close to another copper object.

| Cause | Fix |
|-------|-----|
| Trace squeezed between pads | Re-route with more space or narrower trace |
| Via too close to pad | Move the via |
| Copper zone too close to trace | Increase zone clearance or re-route |

**Category 2: Unconnected nets**

A net from the schematic has no physical connection on the PCB.

| Cause | Fix |
|-------|-----|
| Missing trace | Route the connection with `add_trace` |
| Footprint pad not connected | Add trace or via to reach the net |
| Wrong footprint assigned | Fix in schematic, re-run `update_pcb_from_schematic` |

**Category 3: Track width violations**

A trace is narrower than the design rule minimum.

| Cause | Fix |
|-------|-----|
| Trace routed too narrow | Delete and re-route with correct width |
| Net class minimum not met | Check net class settings, increase width |

**Category 4: Copper zone issues**

| Cause | Fix |
|-------|-----|
| Isolated copper island | Delete the island or connect it to a net |
| Zone not filled | Refill zones after moving components |
| Thermal relief too thin | Increase spoke width in zone properties |

**Category 5: Post-autoroute quality issues**

These are not DRC errors but quality problems that the autorouter creates:

| Check | What to look for | Fix |
|-------|-----------------|-----|
| Power trace width | Power nets (VIN, VOUT, SW, GND) at minimum width (0.25mm) | `set_trace_width` to widen per current table |
| Missing copper zones | Board has 0 zones (no ground plane) | `add_copper_zone` on both layers, then `fill_zones` |
| Missing thermal vias | QFN/exposed-pad ICs with no vias under thermal pad | `add_thermal_vias` for each exposed-pad IC |
| Dangling tracks | Trace stubs from autorouter that connect to nothing | `remove_dangling_tracks` |
| Excessive vias | Autorouter used unnecessary layer transitions | Manual review — remove and re-route if egregious |

### Step 3: Re-run DRC

Repeat until zero violations. Manufacturing houses will reject boards
with DRC errors.

**Verification-before-completion:** Before claiming DRC is clean, you
must show actual tool output with `violation_count: 0`. No claims
without fresh evidence. Previous results are stale after fixes.

**Stuck escalation:** If DRC violations cannot be resolved (e.g.,
requires a design change), report the situation to the user. Options:
- Re-run from pcb-layout with adjusted placement
- Accept known violations with explicit user approval
- Abort

## Checklist

**IMPORTANT: Use TodoWrite to create todos for EACH checklist item below.**

- [ ] Run `validate_hierarchy` from root (for ERC gate)
- [ ] Fix unannotated/duplicate refs with `annotate_schematic`
- [ ] Run ERC or DRC (as appropriate for current gate)
- [ ] Read ALL violations before fixing
- [ ] Fix Category 1 violations
- [ ] Fix Category 2 violations
- [ ] Fix Category 3 violations
- [ ] Fix remaining violations
- [ ] Re-run check — must show violation_count = 0
- [ ] Show fresh tool output as evidence

## Two Gate Points

This skill is invoked at two points in the pipeline:
- **After schematic-design:** ERC gate — blocks PCB layout until clean
- **After pcb-layout:** DRC gate — blocks export until clean

Same skill, different context. The pipeline orchestrator (`using-kicad`)
determines which gate you're at.

## Pre-Manufacturing Checklist

Run this before exporting Gerbers:

- [ ] ERC: zero violations
- [ ] DRC: zero violations
- [ ] All nets connected (no ratsnest lines remaining)
- [ ] Board outline closed on Edge.Cuts layer
- [ ] Mounting holes placed and sized correctly
- [ ] Silkscreen readable — no text over pads or vias
- [ ] Fiducials placed (if required for SMD assembly)
- [ ] All component values match the BOM
- [ ] Design rules match manufacturer capabilities
- [ ] Power traces sized for expected current (not autorouter minimums)
- [ ] Ground planes present on appropriate layers
- [ ] Thermal vias under all QFN/exposed-pad ICs
- [ ] No dangling track stubs

## Export for Manufacturing

When verification is complete:

1. `export_gerbers` — produces the full Gerber set (copper layers,
   mask, silkscreen, drill, board outline)
2. `export_positions` — pick-and-place file for automated assembly
3. `export_bom` — bill of materials from the schematic
4. `export_3d` — 3D model for mechanical review

Verify the Gerber output by reviewing layer count and checking that
the board outline, drill hits, and copper match expectations.
