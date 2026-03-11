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

## Verification

After completing placement and wiring:
1. Run `run_erc` to check for violations.
2. Fix "power pin not driven" with PWR_FLAG symbols.
3. Fix unconnected pins with `wire_pin_to_label` or `no_connect_pin`.
4. Re-run ERC until clean.
