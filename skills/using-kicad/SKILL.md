---
name: using-kicad
description: >
  Use when starting any conversation involving electronics, KiCad, PCB
  design, schematic capture, circuit design, or EDA tasks. Establishes
  the design workflow and ensures the correct skill is invoked before
  any action is taken.
---

<SUBAGENT-STOP>
If you were dispatched as a subagent to execute a specific task, skip this skill.
</SUBAGENT-STOP>

<EXTREMELY-IMPORTANT>
When working on ANY electronics or KiCad task, you MUST invoke the
matching skill before taking action. Do not place components, route
traces, run checks, or select parts without first loading the skill
that covers that stage of the design.

This is not optional. Do not rationalize skipping it.
</EXTREMELY-IMPORTANT>

# Using KiCad Skills

This plugin provides a complete electronics design workflow through
four skills and a library of MCP tools for driving KiCad. **Always
invoke the relevant skill before starting work.**

## Skill Catalog

| Skill | Invoke as | When to use |
|-------|-----------|-------------|
| **circuit-design** | `/kicad:circuit-design` | Choosing topology, selecting components, calculating values, creating a BOM — everything before you open the schematic editor |
| **schematic-design** | `/kicad:schematic-design` | Placing symbols on a schematic sheet, wiring pins, adding power symbols and net labels |
| **pcb-layout** | `/kicad:pcb-layout` | Placing footprints on a PCB, routing traces, adding vias and copper zones, defining board outline |
| **verification** | `/kicad:verification` | Running ERC/DRC, fixing violations, preparing for manufacturing export |

## Design Workflow

Follow this order. Do not skip stages.

```
 Requirements
     |
     v
 circuit-design ---- produce BOM + block diagram
     |
     v
 schematic-design -- place symbols, wire nets, add power
     |
     v
 verification ------ run ERC, fix all violations
     |
     v
 pcb-layout -------- place footprints, route traces, zones
     |
     v
 verification ------ run DRC, fix all violations
     |
     v
 Export ------------- Gerbers, drill, BOM, pick-and-place
```

**circuit-design** must complete before schematic-design begins.
ERC must pass before starting PCB layout. DRC must pass before export.

## The Rule

**Invoke the matching skill BEFORE taking action.** If the user says
"place the regulator on the schematic," invoke `schematic-design`
before placing anything. If they say "route the power traces," invoke
`pcb-layout` first.

If you are unsure which skill applies, ask which stage of the design
the user is working in.

## What This Plugin Provides

The KiCad MCP server gives you tools to drive KiCad programmatically.
You do not need the user to click anything in KiCad — the tools do it
for you. Key tool groups:

- **Schematic:** `place_symbol`, `connect_pins`, `wire_pins_to_net`,
  `add_power_symbol`, `no_connect_pin`, `run_erc`
- **PCB:** `place_footprint`, `add_trace`, `add_via`, `add_zone`,
  `run_drc`
- **Libraries:** `list_lib_symbols`, `get_symbol_info`,
  `list_lib_footprints`, `get_footprint_info`
- **Export:** `export_gerbers`, `export_bom`, `export_positions`,
  `export_3d`

Always read the skill for conventions, spacing, and strategy before
using these tools.
