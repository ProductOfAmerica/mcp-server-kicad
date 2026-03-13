---
name: pcb-layout
description: >
  Use when placing footprints on a PCB, routing traces, adding vias or
  copper zones, deciding trace widths, or planning board layout strategy.
  Also use when the user asks about PCB stackup, ground planes, trace
  spacing, or component placement on a board.
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

# KiCad PCB Layout

Place footprints and route traces to produce a manufacturable board.
This skill assumes you have a completed schematic with a netlist.

## MCP Tools for This Skill

These are the kicad MCP tools you should be using during PCB layout:

**Reading / inspection:**
- `list_pcb_items` — list footprints, traces, vias, zones, etc.
- `get_board_info` — get board outline, layer count, design rules
- `get_footprint_pads` — get pad positions and net assignments

**Placing and moving footprints:**
- `place_footprint` — place a footprint on the board
- `move_footprint` — reposition a placed footprint
- `remove_footprint` — delete a placed footprint

**Routing:**
- `add_trace` — add a trace segment with layer, width, coordinates
- `add_via` — add a via at a coordinate
- `autoroute_pcb` — run Freerouting autorouter (requires Java 17+, auto-downloads JAR)

**Post-routing refinement:**
- `add_copper_zone` — create a copper zone (ground plane, power fill) with polygon outline
- `fill_zones` — compute copper fills for all zones (requires pcbnew)
- `set_trace_width` — change width of existing traces by net, layer, or region
- `add_thermal_vias` — add a via array under a footprint pad (QFN thermal pads)
- `remove_traces` — delete traces by net, layer, or region
- `set_net_class` — create/update net classes with design rules (requires pcbnew)
- `remove_dangling_tracks` — clean up unconnected trace stubs

**Drawing and annotation:**
- `add_pcb_text` — add silkscreen text or other layer text
- `add_pcb_line` — draw lines (board outline on Edge.Cuts, etc.)

**Verification and export:**
- `run_drc` — run design rules check
- `export_pcb` — export PCB as PDF/SVG
- `export_gerbers` — export Gerber manufacturing files
- `export_3d` — export 3D model (STEP/VRML)
- `export_positions` — export pick-and-place file
- `export_ipc2581` — export IPC-2581 data

**Footprint libraries:**
- `list_lib_footprints` — browse .pretty library directories
- `get_footprint_info` — check pad dimensions and pin mapping

## Pre-flight Checks

Before starting PCB layout, verify these prerequisites:

1. **ERC gate cleared:** Run `run_erc` and confirm `violation_count = 0`.
   If ERC has not been run or has violations, STOP and invoke the
   verification skill first.
2. **Footprint verification:** For each schematic component, call
   `list_lib_footprints` to verify the assigned footprint exists.
3. **BOM cross-reference:** If `specs/bom.md` exists, cross-reference
   footprints against the BOM's footprint column.
4. **Board size constraints:** Check board size from BOM artifact (if
   present) and configure board outline accordingly.
5. **IPC design class:** If BOM specifies IPC design class, configure
   design rules (trace width, spacing, annular ring) to match class
   requirements:
   - Class 1 (consumer): relaxed tolerances
   - Class 2 (industrial): standard tolerances (default)
   - Class 3 (high-rel): tightest tolerances

## Layout Process

1. **Board setup** — define board outline, layer count, design rules.
2. **Import netlist** — footprints appear in a cluster; all nets defined.
3. **Place footprints** — group by function, then optimize.
4. **Route critical traces** — power, high-speed, sensitive analog.
5. **Route remaining traces** — signal interconnects (or use `autoroute_pcb`).
6. **Post-route refinement** — review and improve routing quality:
   a. Set net classes for power nets: `set_net_class` (e.g., "Power" class with 0.5mm+ width)
   b. Widen power traces: `set_trace_width` on power nets (VIN, VOUT, SW, GND paths)
   c. Add thermal vias: `add_thermal_vias` under QFN/exposed pads
   d. Clean up: `remove_dangling_tracks` to remove autorouter stubs
   e. Remove and re-route if needed: `remove_traces` + `add_trace` for problem areas
7. **Add copper zones** — `add_copper_zone` for ground planes on both layers, then `fill_zones`.
8. **Run DRC** — fix violations, re-run until clean.
9. **Export** — Gerbers, drill files, pick-and-place, BOM.

## Footprint Placement Strategy

**Group by functional stage** — keep components from each schematic
stage physically together. The schematic's block diagram maps directly
to PCB placement regions.

**Placement priority order:**
1. Connectors — fixed by mechanical constraints (edge of board)
2. ICs / active components — central to their stage
3. Decoupling caps — as close as possible to IC power pins (< 3mm)
4. Input/output passives — near their IC, following signal flow
5. Bulk caps and inductors — near but not blocking routing channels
6. Test points, mounting holes — last, fill remaining space

**Orientation conventions:**
- Align ICs with pin 1 in a consistent corner (top-left preferred)
- Polarized caps: positive toward the supply side
- Diodes: cathode band facing consistent direction per stage
- Connectors: mating face toward board edge

**Spacing minimums:**
- IC to decoupling cap: < 3mm center-to-center
- Between 0805 passives: 1.0mm edge-to-edge minimum (1.5mm comfort)
- Between ICs: 3–5mm edge-to-edge for routing channels
- Components to board edge: 1.0mm minimum (2.0mm for wave solder)

Use `place_footprint` with coordinates in mm. Use `move_footprint`
to adjust after initial placement. Use `get_footprint_pads` to check
pad locations before routing.

## Trace Routing

### Trace Width by Current

| Current (A) | Min width (mm) | Recommended (mm) | Notes |
|-------------|---------------|-------------------|-------|
| < 0.3 | 0.15 | 0.25 | Signal traces |
| 0.3–1.0 | 0.25 | 0.5 | Low power |
| 1.0–2.0 | 0.5 | 0.75 | Moderate power |
| 2.0–3.0 | 0.75 | 1.0 | Power traces |
| 3.0–5.0 | 1.0 | 1.5 | High current |
| > 5.0 | Calculate | Calculate | Use IPC-2152 |

These assume 1oz (35um) copper, 10C rise, outer layer. Inner layers
need ~2x width for the same current.

### Routing Rules

**Power traces first:**
- Route VIN, VOUT, and GND connections before signal traces.
- Use the widest trace the space allows, not the minimum.
- Keep power loops tight — short, wide traces from regulator input
  cap through the IC to the output cap.

**Signal traces:**
- Default 0.25mm width for logic signals.
- Keep parallel runs short to minimize crosstalk.
- Route differential pairs together with matched length.
- Avoid 90-degree corners — use 45-degree bends or arcs.

**Return paths:**
- Every signal needs a return path. On a 2-layer board, route GND
  traces alongside signal traces.
- On 4+ layer boards, use a continuous ground plane — do not split it
  under signal traces.

**Sensitive traces:**
- Analog inputs: guard ring or ground trace on both sides.
- High-impedance nodes: minimize trace length, keep away from
  switching signals.
- Clock/PWM lines: route away from analog sections.

Use `add_trace` with layer, width, and coordinate list. Use `add_via`
to transition between layers.

### Via Usage

- **Signal vias:** 0.3mm drill / 0.6mm annular ring (default)
- **Power vias:** 0.4mm drill / 0.8mm annular ring, use multiple
  vias in parallel for high current (one via ≈ 1A for 1oz copper)
- **Thermal vias:** under thermal pads, 0.3mm drill, array of 4–9
  vias connecting to ground plane
- Place vias close to pads, not in the middle of trace runs

## Post-Autoroute Refinement

After using `autoroute_pcb`, the board has connectivity but needs refinement
before it's manufacturing-ready. The autorouter uses minimum-width traces for
everything and has no concept of power integrity or thermal design.

**Step 1: Define net classes**

Use `set_net_class` to establish design rules for power nets:
- Create a "Power" class with `track_width=0.5` (or wider per current table)
  for VIN, VOUT, and other power rails.
- Create a "HighCurrent" class with `track_width=1.0` for switch nodes (SW)
  and high-current ground paths.

**Step 2: Widen power traces**

Use `set_trace_width` to bring power traces up to their net class width:
- `set_trace_width(width=0.5, net_name="VIN")`
- `set_trace_width(width=1.0, net_name="SW")`
- For ground nets routed as traces (before zone fill): widen to 0.5mm+

**Step 3: Add thermal vias**

Use `add_thermal_vias` for any QFN or exposed-pad IC:
- `add_thermal_vias(reference="U1", rows=3, cols=3, spacing=1.0, via_drill=0.3)`
- Thermal vias connect the exposed pad to the ground plane on the opposite
  layer for heat dissipation.

**Step 4: Clean up**

Use `remove_dangling_tracks` to remove autorouter stubs — trace ends that
connect to nothing. These are cosmetic but will show as DRC warnings.

If any trace routing is unsatisfactory, use `remove_traces` to delete traces
on a specific net, then re-route manually with `add_trace`.

**Step 5: Add copper zones**

Use `add_copper_zone` on both layers:
- Ground fill on F.Cu and B.Cu (2-layer) or dedicated ground plane on In1.Cu (4-layer)
- Set clearance to match design rules (typically 0.3–0.5mm)
- Then call `fill_zones` to compute the actual copper fill

**Step 6: Final DRC**

Run `run_drc` and fix any remaining violations. Common post-refinement issues:
- Clearance violations from widened traces — may need to re-route nearby signals
- Zone fill islands — delete or connect them

## Checklist

**IMPORTANT: Use TodoWrite to create todos for EACH checklist item below.**

- [ ] Pre-flight: verify ERC = 0 violations
- [ ] Pre-flight: verify all footprints exist
- [ ] Define board outline and design rules
- [ ] Place footprints by functional group
- [ ] Route critical traces (power, high-speed)
- [ ] Route remaining traces (or autoroute)
- [ ] Post-route: define net classes for power nets
- [ ] Post-route: widen power traces
- [ ] Post-route: add thermal vias for QFN/exposed pads
- [ ] Post-route: clean up dangling tracks
- [ ] Add copper zones (ground planes)
- [ ] Run DRC — must show zero violations

## Layer Stack

**2-layer board (default for simple designs):**
- F.Cu: signal and power traces
- B.Cu: ground fill + remaining traces
- Route most signals on top, use bottom for crossings

**4-layer board (for complex or noise-sensitive designs):**
- F.Cu: signal + power
- In1.Cu: ground plane (continuous, no splits)
- In2.Cu: power plane
- B.Cu: signal + power

Rule of thumb: if the design has > 1 switching regulator, > 20 ICs,
or any signal > 10MHz, use 4 layers.

## Copper Zones (Ground Planes)

- Add a ground zone on B.Cu for 2-layer boards, on In1.Cu for 4-layer.
- Zone clearance: 0.3mm (match design rules).
- Thermal relief on pads: 0.5mm spoke width, 4 spokes.
- Remove isolated copper islands (DRC will flag these).
- On 2-layer boards, add ground zone on F.Cu as well in unused areas
  to improve shielding and reduce etching.

## Silkscreen and Fabrication

- Reference designators: 1.0mm height, 0.15mm line width minimum.
- Move refdes out of pad areas — readability over density.
- Add board name, version, date on silkscreen.
- Add polarity marks near polarized components if not in footprint.
- Add pin-1 indicators near IC footprints if ambiguous.

Use `add_pcb_text` for silkscreen annotations. Use `add_pcb_line`
for board outline on Edge.Cuts layer.

## Design Rule Defaults

Standard 2-layer PCB fab capabilities (most manufacturers):

| Parameter | Minimum | Recommended |
|-----------|---------|-------------|
| Trace width | 0.15mm | 0.25mm |
| Trace spacing | 0.15mm | 0.2mm |
| Via drill | 0.3mm | 0.3mm |
| Via annular ring | 0.15mm | 0.2mm |
| Pad to pad | 0.2mm | 0.25mm |
| Copper to edge | 0.3mm | 0.5mm |

## DRC and Export

After routing is complete:

1. Run `run_drc` — fix all violations before exporting.
2. Common DRC errors:
   - **Clearance violation** — move trace or via, increase spacing.
   - **Unconnected net** — route the missing connection.
   - **Copper zone island** — delete the isolated island or connect it.
3. When DRC is clean, export:
   - `export_gerbers` — full Gerber set for manufacturing
   - `export_positions` — pick-and-place file for assembly
   - `export_3d` — 3D model for mechanical fit check

**Full export deliverables (post-DRC):**
- Gerber set (copper layers, mask, silkscreen, drill, board outline)
- Drill files
- BOM export
- Pick-and-place file
- 3D model (STEP) for mechanical review
- Fabrication notes: layer stack, copper weight, finish, IPC class,
  solder mask color
- Assembly drawing

**Post-export verification:** Confirm Gerber layer count matches
design, BOM export matches schematic BOM.

## Verifying Footprints in KiCad Libraries

Use `list_lib_footprints` on .pretty directories to browse available
footprints. Common libraries:

- `Resistor_SMD.pretty` — 0402, 0603, 0805, 1206, etc.
- `Capacitor_SMD.pretty` — ceramic SMD caps
- `Capacitor_THT.pretty` — electrolytic, film through-hole
- `Package_TO_SOT_SMD.pretty` — SOT-23, SOT-223, TO-263, etc.
- `Package_SO.pretty` — SOIC, SSOP, TSSOP
- `Package_QFP.pretty` — QFP, LQFP, TQFP
- `Connector_PinHeader_2.54mm.pretty` — standard pin headers
- `Diode_SMD.pretty` — SMD diodes
- `Inductor_SMD.pretty` — SMD inductors

Use `get_footprint_info` to check pad dimensions and spacing before
committing to a footprint.

## Extensibility

For complex boards (>30 components, 4+ layers, high-speed signals),
consider creating a `pcb-plan` artifact following the same pattern as
`schematic-plan`. For simpler boards, pre-flight checks and existing
placement strategy are sufficient.
