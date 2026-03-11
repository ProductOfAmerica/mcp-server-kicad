---
name: schematic-design
description: >
  Use when designing a KiCad schematic from scratch, laying out components
  on a schematic sheet, placing symbols and wiring them together, or when
  the user asks about schematic layout, component placement, or wiring
  strategy. Provides conventions for placement spacing, signal flow
  direction, wiring tool selection, and functional stage organization.
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

## MCP Tools for This Skill

These are the kicad MCP tools you should be using during schematic design:

**Reading / inspection:**
- `list_schematic_items` — list symbols, wires, labels, etc. on a sheet
- `get_symbol_pins` — get pin names and types for a symbol
- `get_pin_positions` — get placed pin coordinates (for wiring)
- `get_net_connections` — trace a net to see what's connected
- `list_unconnected_pins` — find pins that need wiring or no-connect

**Placing components:**
- `place_component` — place a symbol instance on the schematic
- `move_component` — reposition a placed component
- `remove_component` — delete a placed component
- `set_component_property` — change reference, value, footprint, etc.
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
  - To find wire coordinates for `remove_wire`, first call
    `list_schematic_items(item_type="wires")` which returns x1/y1/x2/y2
    for every wire segment.

**Power and decoupling:**
- `add_power_symbol` — place VCC, GND, +3V3, PWR_FLAG, etc.
- `auto_place_decoupling_cap` — auto-place a decoupling cap near an IC

**Annotations and hierarchy:**
- `add_text` — add text annotations to the sheet
- `add_hierarchical_sheet` — add a sub-sheet reference

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
   numbers, footprint, and datasheet. The tool handles body
   rectangles, property placement, and s-expression formatting.
3. `add_lib_symbol` — load the custom symbol into the schematic so
   it can be placed with `place_symbol`

The `add_symbol` tool accepts full pin definitions including
electrical type (power_in, power_out, input, output, passive,
bidirectional, open_collector, etc.), position, rotation, and length.

## Verification

After completing placement and wiring:
1. Run `run_erc` to check for violations.
2. Fix "power pin not driven" with PWR_FLAG symbols.
3. Fix unconnected pins with `wire_pin_to_label` or `no_connect_pin`.
4. Re-run ERC until clean.
