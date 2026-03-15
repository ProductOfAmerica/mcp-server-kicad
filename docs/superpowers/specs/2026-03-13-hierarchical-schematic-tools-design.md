# Hierarchical Schematic Tools — Design Spec

**Date:** 2026-03-13
**Status:** Draft
**Scope:** Bug fixes, new tools, and enhancements to enable autonomous agent workflows on multi-sheet KiCad schematics

## Problem Statement

Agents working with hierarchical KiCad schematics through the MCP server hit a wall: they can create hierarchical designs via `add_hierarchical_sheet` but cannot inspect, validate, or modify them afterward. Key failures observed:

1. Components with unresolved references (`U?`, `R?`) block `connect_pins` and `get_pin_positions`
2. `add_wires` creates wires that don't form proper T-connections (missing auto-junctions)
3. Hierarchical labels are invisible — no query tool exposes them
4. `get_net_connections` only traces one wire hop, missing multi-segment paths
5. `run_erc` root auto-detection fails silently when project structure is ambiguous
6. No tools exist to inspect sheet hierarchy, validate label↔pin matching, or trace nets across sheets

The result: agents go in circles, eventually giving up and asking the user to open the KiCad GUI.

## Design Overview

8 categories of changes, 26 total items:

| Category | Items | New Tools | Fixes | Target File |
|----------|-------|-----------|-------|-------------|
| 1. Bug fixes | 3 | 0 | 3 | `schematic.py` |
| 2. Per-type schematic list tools | 10 tools (split from list_schematic_items) | 10 | 0 | `schematic.py` |
| 3. Annotation | 1 | 1 | 0 | `project.py` |
| 4. Hierarchical label management | 3 | 3 | 0 | `schematic.py` |
| 5. Hierarchical sheet modification | 4 | 4 | 0 | `project.py` |
| 6. Hierarchy inspection & traversal | 3 | 3 | 0 | `project.py` |
| 7. Cross-sheet connectivity & validation | 3 | 3 | 0 | `project.py` |
| 8. Advanced operations | 5 | 5 | 0 | mixed |

### File Placement Rules

- **`schematic.py`**: Tools that operate on a single `.kicad_sch` file (read items, add/remove items within one sheet, wire tracing within one sheet).
- **`project.py`**: Tools that operate across multiple sheets or need project-level context (hierarchy traversal, cross-sheet validation, annotation across hierarchy, sheet management).
- All tools that modify schematics MUST use `_save_sch()` from `_shared.py` (not `sch.to_file()` directly). The `_save_sch` wrapper post-processes system library symbols that kiutils corrupts. **Note:** Existing `project.py` tools use `sch.to_file()` directly — this should be migrated to `_save_sch()` as a prerequisite.

### Shared Helper

Add `_resolve_root(schematic_path, project_path="")` to `_shared.py`:
- If `project_path` provided, derive root `.kicad_sch` from the `.kicad_pro` stem
- Otherwise, fall back to `_find_root_schematic()` (glob-based detection)
- Used by all tools that need root context (ERC, annotation, hierarchy traversal, validation)

---

## Section 1: Bug Fixes to Existing Tools

### 1a. `add_wires` — add `_auto_junctions()` call

**File:** `schematic.py`, function `add_wires`

**Context:** A prior fix replaced `_snap_grid(val)` with `round(val, 4)` in `add_wires` (and 5 other connectivity tools) because `_snap_grid` rounds to 1.27mm grid, destroying coordinate precision and causing wires to miss pin positions. That fix resolved the primary "dangling wire" issue. However, `add_wires` still does not call `_auto_junctions()`, which is needed for T-connections (new wire endpoint landing on existing wire interior).

After appending all wire segments, collect all wire endpoints and call `_auto_junctions(sch, points)` before saving. Same pattern `connect_pins` already uses.

```python
# After the for loop, before _save_sch:
all_points = []
for w in wires:
    all_points.append((round(w["x1"], 4), round(w["y1"], 4)))
    all_points.append((round(w["x2"], 4), round(w["y2"], 4)))
_auto_junctions(sch, all_points)
_save_sch(sch)
```

### 1b. `get_net_connections` — multi-hop BFS wire tracing

**File:** `schematic.py`, function `get_net_connections`

Replace the single-hop label→wire→pin lookup with BFS flood-fill:

1. Start with label positions as seed set
2. For each point in the frontier, find all wire endpoints that share that point (within tolerance)
3. Add the opposite endpoint of each matching wire to the frontier
4. Repeat until no new points are discovered
5. Match the full reachable set against all component pin positions

This catches label → wire → wire → wire → pin chains of arbitrary length.

### 1c. `run_erc` / `list_unconnected_pins` — add `project_path` parameter

**File:** `schematic.py`, functions `run_erc` and `list_unconnected_pins`

Add optional `project_path: str = ""` parameter to both tools. Use the new `_resolve_root()` helper: when `project_path` is provided, derive the root schematic from the `.kicad_pro` file directly instead of relying on `_find_root_schematic`'s glob-based detection (which fails with 0 or 2+ `.kicad_pro` files in the directory). Falls back to current auto-detection when not provided.

---

## Section 2: Per-Type Schematic List Tools

**File:** `schematic.py`

The former `list_schematic_items` tool has been split into per-type tools: `get_schematic_summary`, `list_schematic_components`, `list_schematic_labels`, `list_schematic_wires`, `list_schematic_global_labels`, `list_schematic_hierarchical_labels`, `list_schematic_sheets`, `list_schematic_junctions`, `list_schematic_no_connects`, `list_schematic_bus_entries`.

### 2a. `hierarchical_labels`

Returns: `[{text, shape, x, y, rotation, uuid}]`

Source: `sch.hierarchicalLabels` (List[HierarchicalLabel]). The `shape` field is the direction type (input/output/bidirectional/tri_state/passive).

### 2b. `sheets`

Returns: `[{sheet_name, file_name, x, y, width, height, pin_count, uuid}]`

Source: `sch.sheets` (List[HierarchicalSheet]). Access name via `sheet.sheetName.value` and filename via `sheet.fileName.value` — these are `Property` objects, not plain strings.

### 2c. `junctions`

Returns: `[{x, y, diameter}]`

Source: `sch.junctions`.

### 2d. `no_connects`

Returns: `[{x, y}]`

Source: `sch.noConnects` (List[NoConnect]). kiutils always populates this list from the `no_connect` S-expression tokens.

### 2e. `bus_entries`

Returns: `[{x, y, size_x, size_y}]`

Source: `sch.busEntries`. Access coordinates via `entry.position.X/Y` and size via `entry.size.X` → `size_x`, `entry.size.Y` → `size_y` (the `size` field reuses the `Position` type; ignore its `.angle` attribute).

### Summary update

Update the `summary` item type to include counts for all new types: hierarchical labels, sheets, junctions, no-connects, bus entries.

---

## Section 3: `annotate_schematic` Tool

**File:** `project.py` (cross-sheet operation requiring hierarchy traversal)

**New tool:** `annotate_schematic(schematic_path: str = SCH_PATH, project_path: str = "")`

**Purpose:** Auto-assign reference designators to unannotated components (those with `?` in their reference).

**Algorithm:**

1. Load the target schematic. If `project_path` provided, use `_resolve_root()` and scan all sheets in the hierarchy to collect existing references.
2. Group unannotated components by prefix (`U`, `R`, `C`, etc.)
3. For each prefix, find the max existing number across the hierarchy (e.g., if `U5` exists, start at `U6`)
4. Assign sequential numbers to unannotated components
5. Update annotation in **two places**:
   - The `Reference` property on each `SchematicSymbol` (in the component's own `.kicad_sch` file)
   - The `SymbolProjectInstance` → `SymbolProjectPath.reference` inside each symbol's instance data (per-symbol, KiCad v7+ format)
   - The root schematic's `SymbolInstance` list entries (root-level `sch.symbolInstances`, which holds `path`, `reference`, `value`, `footprint` for every component across all sheets)
6. Save using `_save_sch()`

**Returns:** Summary like `"Annotated 12 components: U5-U7, R1-R6, C1-C3"`

**Key detail:** KiCad tracks annotation in both per-symbol instance data and root-level `symbolInstances`. Both must be updated or KiCad will show stale/conflicting references.

**Annotations:** `_ADDITIVE`

---

## Section 4: Hierarchical Label Management

**File:** `schematic.py` (single-sheet operations)

### 4a. `add_hierarchical_label`

```
add_hierarchical_label(text, shape, x, y, rotation=0, schematic_path: str = SCH_PATH)
```

Creates a `HierarchicalLabel` at the given position. The `shape` parameter is one of: `input`, `output`, `bidirectional`, `tri_state`, `passive`. Snaps to grid, validates position against page boundaries.

**Annotations:** `_ADDITIVE`

### 4b. `remove_hierarchical_label`

```
remove_hierarchical_label(text, schematic_path: str = SCH_PATH, uuid="")
```

Removes a hierarchical label by name, or by UUID for disambiguation when multiple labels share the same name. Returns error if label not found.

**Annotations:** `_DESTRUCTIVE`

### 4c. `modify_hierarchical_label`

```
modify_hierarchical_label(text, schematic_path: str = SCH_PATH, new_text="", new_shape="", new_x=None, new_y=None, uuid="")
```

Modify properties of an existing hierarchical label — rename, change direction/shape, or reposition. Only provided fields are changed; others are left as-is. When renaming, warns that the corresponding sheet pin in the parent also needs renaming to maintain connectivity.

**Annotations:** `_DESTRUCTIVE` (renaming changes the label's logical identity and can break parent↔child connectivity)

---

## Section 5: Hierarchical Sheet Modification

**File:** `project.py` (extends existing sheet management tools)

### 5a. `modify_hierarchical_sheet`

```
modify_hierarchical_sheet(sheet_uuid, schematic_path: str = SCH_PATH, sheet_name="", file_name="", width=None, height=None)
```

Edit properties of an existing sheet block. Only provided fields are changed. Updates are applied to the `.value` attribute of the existing `Property` objects (preserving position and formatting). Renaming `file_name` updates the property but does NOT rename the actual file on disk — returns a warning. The `sheet_uuid` is required for disambiguation (available from `list_schematic_sheets()`).

**Annotations:** `_ADDITIVE`

### 5b. `add_sheet_pin`

```
add_sheet_pin(sheet_uuid, pin_name, connection_type, side="right", position_offset=None, schematic_path: str = SCH_PATH)
```

Add a new pin to an existing sheet block. The `side` parameter (`left`, `right`, `top`, `bottom`) determines which edge the pin is placed on. `position_offset` is the distance along that edge from the top/left corner — if omitted, auto-spaces below existing pins on that side. The `connection_type` is one of `input`, `output`, `bidirectional`, `tri_state`, `passive`. Creates a matching wire stub AND net label at the stub endpoint on the parent side (following the same pattern as `add_hierarchical_sheet`).

**Annotations:** `_ADDITIVE`

### 5c. `remove_sheet_pin`

```
remove_sheet_pin(sheet_uuid, pin_name, schematic_path: str = SCH_PATH)
```

Remove a pin from a sheet block. Returns warning if a corresponding hierarchical label exists in the child sheet that will become orphaned. Does NOT auto-delete the child label.

**Annotations:** `_DESTRUCTIVE`

### 5d. `duplicate_sheet`

```
duplicate_sheet(sheet_uuid, new_sheet_name, new_file_name, x, y, schematic_path: str = SCH_PATH, project_path: str = "")
```

Duplicate a hierarchical sheet block and its child schematic file. Copies the child `.kicad_sch` to `new_file_name`, places a new sheet block at `(x, y)` with matching pins, generates new UUIDs throughout, and re-annotates the copy's components with fresh references to avoid conflicts. Handles sheet instance paths for the new copy.

**Annotations:** `_ADDITIVE`

---

## Section 6: Hierarchy Inspection & Traversal

**File:** `project.py` (cross-sheet operations)

### 6a. `list_hierarchy`

```
list_hierarchy(schematic_path: str = SCH_PATH, project_path: str = "")
```

Returns the complete hierarchy tree starting from the root schematic. Recursively loads each sheet's child file and builds a tree.

**Output format:**

```json
{
  "root": "l1-io-node.kicad_sch",
  "sheets": [
    {
      "sheet_name": "Relay Drivers",
      "file_name": "relay-drivers.kicad_sch",
      "uuid": "...",
      "instance_path": "/root_uuid/sheet_uuid",
      "page": "2",
      "component_count": 24,
      "label_count": 15,
      "hierarchical_label_count": 12,
      "sub_sheets": []
    }
  ]
}
```

Auto-detects root via `_resolve_root()`. If called on a sub-sheet without project context, returns just that sheet's children (partial tree).

**Annotations:** `_READ_ONLY`

### 6b. `get_sheet_info`

```
get_sheet_info(sheet_uuid="", sheet_name="", schematic_path: str = SCH_PATH)
```

Detailed info for a single sheet block. Look up by UUID or name. Returns: sheet properties, all pins (with positions and connection types), the corresponding hierarchical labels found in the child file, and match status (whether each pin has a matching label and vice versa).

**Annotations:** `_READ_ONLY`

### 6c. `is_root_schematic`

```
is_root_schematic(schematic_path: str = SCH_PATH)
```

Returns whether the given schematic is the root (has a matching `.kicad_pro`) or a sub-sheet. Also returns the root path if it's a sub-sheet.

**Annotations:** `_READ_ONLY`

---

## Section 7: Cross-Sheet Connectivity & Validation

**File:** `project.py` (cross-sheet operations)

### 7a. `validate_hierarchy`

```
validate_hierarchy(schematic_path: str = SCH_PATH, project_path: str = "")
```

Scans the entire hierarchy and reports all mismatches:

- **Pin↔Label matching:** Every sheet pin must have a matching hierarchical label in the child, and vice versa.
- **Direction consistency:** Pin `connection_type` should match label `shape`.
- **Dangling hierarchical labels:** Labels in sub-sheets with no parent sheet pin.
- **Unannotated components:** Components with `?` references anywhere in the hierarchy.
- **Duplicate references:** Same reference designator used in multiple sheets.

**Output format:**

```json
{
  "status": "ok | issues_found",
  "issue_count": 4,
  "issues": [
    {"sheet": "relay-drivers.kicad_sch", "type": "orphaned_label", "label": "RELAY_10", "detail": "No matching pin on parent sheet block"},
    {"sheet": "l1-io-node.kicad_sch", "type": "orphaned_pin", "sheet_block": "Relay Drivers", "pin": "BUZZER_2", "detail": "No matching hierarchical label in child"},
    {"sheet": "relay-drivers.kicad_sch", "type": "direction_mismatch", "name": "5V_REL", "pin_type": "input", "label_type": "output"},
    {"sheet": "relay-drivers.kicad_sch", "type": "unannotated", "references": ["U?", "R?", "R?"]}
  ]
}
```

When no issues are found: `{"status": "ok", "issue_count": 0, "issues": []}`.

**Annotations:** `_READ_ONLY`

### 7b. `trace_hierarchical_net`

```
trace_hierarchical_net(net_name, schematic_path: str = SCH_PATH, project_path: str = "")
```

Follow a net across the entire hierarchy. Starting from a net name, finds all labels (local, global, hierarchical), traces wires using BFS (reusing the improved multi-hop algorithm from 1b), crosses sheet boundaries via pin↔label matching, and returns every component pin touched by that net across all sheets.

**Output format:**

```json
{
  "net": "5V_REL",
  "sheets_touched": ["l1-io-node.kicad_sch", "relay-drivers.kicad_sch"],
  "connections": [
    {"sheet": "l1-io-node.kicad_sch", "type": "hierarchical_pin", "sheet_block": "Relay Drivers", "pin": "5V_REL"},
    {"sheet": "relay-drivers.kicad_sch", "type": "hierarchical_label", "text": "5V_REL"},
    {"sheet": "relay-drivers.kicad_sch", "type": "component_pin", "reference": "U5", "pin": "10", "pin_name": "COM"}
  ]
}
```

**Annotations:** `_READ_ONLY`

### 7c. `list_cross_sheet_nets`

```
list_cross_sheet_nets(schematic_path: str = SCH_PATH, project_path: str = "")
```

Returns all nets that cross sheet boundaries — every hierarchical label/pin pair and every global label — with a flag indicating whether connectivity is complete or broken. Quick overview for agents to identify which nets need attention.

**Annotations:** `_READ_ONLY`

---

## Section 8: Advanced Operations

### 8a. `get_symbol_instances`

**File:** `project.py`

```
get_symbol_instances(schematic_path: str = SCH_PATH, project_path: str = "")
```

Exposes the `symbolInstances` data from the root schematic. Returns all component references across the entire hierarchy with their instance paths, values, and footprints.

```json
[
  {"path": "/root/sheet1/sym_uuid", "reference": "U5", "unit": 1, "value": "ULN2803A", "footprint": "Package_DIP:DIP-18_W7.62mm"},
  {"path": "/root/sheet2/sym_uuid", "reference": "R3", "unit": 1, "value": "10K", "footprint": "Resistor_SMD:R_0402"}
]
```

**Annotations:** `_READ_ONLY`

### 8b. `move_hierarchical_sheet`

**File:** `project.py`

```
move_hierarchical_sheet(sheet_uuid, x, y, schematic_path: str = SCH_PATH)
```

Reposition a sheet block in the parent schematic. Moves the sheet and all its pins to the new position, maintaining relative pin offsets. Also repositions associated wire stubs connected to pins.

**Annotations:** `_ADDITIVE`

### 8c. `reorder_sheet_pages`

**File:** `project.py`

```
reorder_sheet_pages(page_order: list[dict], schematic_path: str = SCH_PATH, project_path: str = "")
```

Update the page numbering of sheets in the hierarchy. `page_order` is a list of `{"sheet_uuid": str, "page": str}` mappings. Updates `sheetInstances` in the root schematic. KiCad uses these for PDF export page ordering.

**Annotations:** `_ADDITIVE`

### 8d. `export_hierarchical_netlist`

**File:** `project.py`

```
export_hierarchical_netlist(schematic_path: str = SCH_PATH, project_path: str = "", output_dir: str = "")
```

Export a netlist that includes full hierarchy path information. Wraps `kicad-cli sch export netlist` then post-processes the XML output:

1. Parse the generated KiCad XML netlist
2. For each net, collect the sheet paths of all connected components (from `SymbolInstance` path data)
3. Add a `sheets_touched` annotation to each net entry
4. Write the enriched netlist to the output directory

Must be registered in `_CLI_TOOLS` in `server.py` since it requires `kicad-cli`.

**Annotations:** `_EXPORT`

### 8e. `flatten_hierarchy`

**File:** `project.py`

```
flatten_hierarchy(schematic_path: str = SCH_PATH, output_path: str = "", project_path: str = "")
```

Create a single flat schematic from a hierarchical design. The original hierarchy is untouched — this creates a new file.

**Algorithm:**

1. Load root schematic, recursively load all child sheets
2. Create new empty schematic with page size large enough to contain all content
3. For each child sheet, offset all positions by the sheet's placement coordinates to avoid overlap
4. Copy all components, wires, labels, junctions, no-connects from each sheet into the flat schematic
5. Replace hierarchical label↔pin connections with direct wire segments connecting the formerly-separated nets
6. Regenerate all UUIDs to avoid conflicts
7. Run `annotate_schematic` logic on the result to ensure unique references
8. Merge `libSymbols` from all sheets (deduplicate by name)
9. Build fresh `symbolInstances` and `sheetInstances` for the flat output
10. Save via `_save_sch()`

**Complexity note:** This is the most complex tool in the spec. Consider implementing it last and potentially as a dedicated sub-spec if implementation reveals additional edge cases (e.g., nested hierarchies 3+ levels deep, global labels that span multiple sheets, bus connections).

**Annotations:** `_ADDITIVE`

---

## Implementation Notes

### kiutils Types Already Available

All hierarchical types needed are already in kiutils:

| Type | kiutils Class | Schematic Attribute |
|------|--------------|-------------------|
| Hierarchical Label | `HierarchicalLabel` | `sch.hierarchicalLabels` |
| Hierarchical Sheet | `HierarchicalSheet` | `sch.sheets` |
| Hierarchical Pin | `HierarchicalPin` | `sheet.pins` |
| Sheet Instance | `HierarchicalSheetInstance` | `sch.sheetInstances` |
| Sheet Project Instance | `HierarchicalSheetProjectInstance` | `sheet.instances` |
| Sheet Project Path | `HierarchicalSheetProjectPath` | `instance.paths` |
| Symbol Instance | `SymbolInstance` | `sch.symbolInstances` |
| No Connect | `NoConnect` | `sch.noConnects` |
| Bus Entry | `BusEntry` | `sch.busEntries` |

### Imports Unification

Currently `project.py` imports kiutils types directly while `schematic.py` imports via `_shared.py` re-exports. **All new tools should import via `_shared.py` re-exports** (the `schematic.py` pattern). Add to `_shared.py`'s `kiutils.items.schitems` import block:

- `HierarchicalLabel`
- `HierarchicalSheet`
- `HierarchicalPin`
- `HierarchicalSheetInstance`
- `HierarchicalSheetProjectInstance`
- `HierarchicalSheetProjectPath`
- `SymbolInstance`
- `BusEntry`

Existing `project.py` direct imports should be migrated to use the `_shared.py` re-exports in a prerequisite cleanup step.

### Prerequisite: Migrate `project.py` to `_save_sch()`

Before implementing new tools, migrate all `sch.to_file()` calls in `project.py` to use `_save_sch()` from `_shared.py`. This prevents system library symbol corruption when editing schematics that include components from KiCad's built-in libraries.

### Tool Count Impact

Current tools: 28 in `schematic.py` + 8 in `project.py` = 36 total.
After this spec: 36 + 19 new = 55 total tools across both servers.

### Server Instructions Update

Update the `mcp` server instructions string in both `schematic.py` and `project.py` to include:

```
HIERARCHY WORKFLOW:
1. Create hierarchy with add_hierarchical_sheet
2. Inspect with list_hierarchy, get_sheet_info
3. Validate with validate_hierarchy
4. Fix label/pin mismatches with add/remove_hierarchical_label, add/remove_sheet_pin
5. Trace nets across sheets with trace_hierarchical_net
6. Annotate all sheets with annotate_schematic
7. Run run_erc from root for final validation
```

---

## Implementation Priority

**Phase 1 — Unblock agents (critical):**
- Prerequisite: migrate `project.py` to `_save_sch()`, add `_resolve_root()` helper
- 1a: add_wires auto-junctions fix
- 1b: get_net_connections BFS
- 1c: run_erc project_path param
- 2a-2b: list_schematic_hierarchical_labels + list_schematic_sheets
- 3: annotate_schematic
- 7a: validate_hierarchy

**Phase 2 — Full hierarchy management:**
- 2c-2e: list_schematic_junctions, list_schematic_no_connects, list_schematic_bus_entries
- 4a-4c: hierarchical label CRUD
- 5a-5c: sheet modification tools
- 6a-6c: hierarchy traversal tools
- 7b-7c: trace_hierarchical_net, list_cross_sheet_nets

**Phase 3 — Advanced:**
- 5d: duplicate_sheet
- 8a-8e: symbol instances, move sheet, reorder pages, hierarchical netlist, flatten
