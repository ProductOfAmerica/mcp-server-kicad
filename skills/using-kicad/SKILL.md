---
name: using-kicad
description: >
  Use when starting any conversation involving electronics, KiCad, PCB
  design, schematic capture, circuit design, or EDA tasks. Establishes
  the design workflow and ensures the correct skill is invoked before
  any action is taken.
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
for you. Tool groups (59 tools total):

- **Project:** `create_project`, `create_schematic`,
  `create_symbol_library`, `create_sym_lib_table`,
  `add_hierarchical_sheet`, `run_jobset`, `get_version`
- **Symbol Authoring:** `add_symbol` (create custom symbol
  definitions), `list_lib_symbols`, `get_symbol_info`,
  `export_symbol_svg`, `upgrade_symbol_lib`
- **Schematic — Place & Edit:** `place_component`, `move_component`,
  `remove_component`, `set_component_property`, `add_lib_symbol`
- **Schematic — Wiring:** `connect_pins`, `wire_pins_to_net`,
  `add_wires`, `add_label`, `add_global_label`, `add_junctions`,
  `no_connect_pin`, `remove_label`, `remove_wire`, `remove_junction`
- **Schematic — Power:** `add_power_symbol`,
  `auto_place_decoupling_cap`
- **Schematic — Inspect:** `list_schematic_items`, `get_symbol_pins`,
  `get_pin_positions`, `get_net_connections`,
  `list_unconnected_pins`, `add_text`
- **Schematic — Export:** `run_erc`, `export_schematic`,
  `export_netlist`, `export_bom`
- **PCB — Place & Edit:** `place_footprint`, `move_footprint`,
  `remove_footprint`
- **PCB — Route:** `add_trace`, `add_via`
- **PCB — Draw:** `add_pcb_text`, `add_pcb_line`
- **PCB — Inspect:** `list_pcb_items`, `get_board_info`,
  `get_footprint_pads`
- **PCB — Export:** `run_drc`, `export_pcb`, `export_gerbers`,
  `export_3d`, `export_positions`, `export_ipc2581`
- **Footprint Libraries:** `list_lib_footprints`,
  `get_footprint_info`, `export_footprint_svg`,
  `upgrade_footprint_lib`

Always read the skill for conventions, spacing, and strategy before
using these tools.
