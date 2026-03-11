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
</CRITICAL-RULE>

# Circuit Design

Design the circuit completely — every component, every value, every
rating — before opening the schematic editor. This skill produces a
**component list and block diagram** that feeds directly into the
schematic-design skill.

## Process

1. **Clarify requirements** — voltage, current, environment, cost,
   form factor. Ask the user if anything is ambiguous.
2. **Block diagram** — decompose into functional stages. Name each
   stage and define the interface between them (voltage, signal type).
3. **Topology selection** — choose a circuit topology for each block.
4. **Component selection** — pick every part with value, rating, and
   package. Verify against KiCad symbol libraries.
5. **Protection and filtering** — add input protection, decoupling,
   and EMI filtering. These are not optional.
6. **Bill of materials** — produce the final BOM table.
7. **Hand off** to schematic-design for placement and wiring.

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

## Verifying Symbols in KiCad Libraries

Before finalizing the BOM, confirm each component has a symbol in
KiCad's libraries:

1. Use `list_lib_symbols` on the relevant library to browse available
   symbols. Common libraries:
   - `Regulator_Linear.kicad_sym` — LDOs, linear regulators
   - `Regulator_Switching.kicad_sym` — buck, boost, flyback controllers
   - `Device.kicad_sym` — R, C, L, D, LED, fuse, ferrite bead
   - `Transistor_FET.kicad_sym` — MOSFETs
   - `Transistor_BJT.kicad_sym` — BJTs
   - `Connector_Generic.kicad_sym` — pin headers, screw terminals
   - `Relay.kicad_sym` — relays
   - `Diode.kicad_sym` — diodes, TVS, Zener, Schottky
   - `power.kicad_sym` — VCC, GND, +3V3, +5V, etc.
2. Use `get_symbol_info` to check pin names and pin count before
   committing to a specific part.
3. If an exact part is not in the library, use a **generic symbol**
   with the correct pin count and set the value property to the
   actual part number. For example, use the generic `R` symbol from
   Device.kicad_sym for any resistor.

## Output: BOM Table

Present the final design as a table. Every row is one component that
will be placed on the schematic.

| Ref | Symbol | Library | Value | Rating | Package | Stage | Notes |
|-----|--------|---------|-------|--------|---------|-------|-------|
| F1 | Fuse | Device | 2A | 32V | 1206 | Input protection | Slow-blow |
| D1 | D_TVS | Diode | SMBJ24A | 24V standoff | SMB | Input protection | |
| C1 | C_Polarized | Device | 100uF | 35V | 8x10mm | Input protection | Bulk |
| U1 | LM2596 | Regulator_Switching | LM2596-5.0 | 40V/3A | TO-263 | Buck stage | |
| ... | | | | | | | |

**Every component must appear in this table before schematic capture
begins.** This includes:
- Decoupling caps for every IC
- Feedback resistors with calculated values
- Bootstrap caps if the regulator requires them
- Pull-up/pull-down resistors
- Test points (if needed)
- Mounting holes (if needed)

## Handoff to Schematic Design

The BOM table and block diagram are the input to the schematic-design
skill. Before handing off, verify:

- [ ] Every IC has decoupling capacitors listed
- [ ] Every calculated value shows its formula
- [ ] Every voltage/current rating has derating margin
- [ ] Protection components are included for every external interface
- [ ] Symbol names match what is in KiCad's libraries
- [ ] Block diagram shows signal flow and named nets between stages
