# Post-Autoroute PCB Tools Design Spec

**Date:** 2026-03-12
**Status:** Draft

## Summary

Add seven new MCP tools to support post-autoroute PCB refinement: copper zone creation, zone filling, trace width adjustment, thermal via arrays, trace removal, net class management, and dangling track cleanup. These tools close the gap between the autorouter (which connects nets with minimum-width traces) and a manufacturing-ready board (which needs ground planes, proper power trace widths, thermal management, and cleanup).

## Decisions

- **Implementation pattern:** kiutils for all tools except `fill_zones` (which requires pcbnew's geometry engine via subprocess). Consistent with existing tools (`add_trace`, `add_via`, `place_footprint`).
- **Fallback:** If `remove_dangling_tracks` proves unreliable with kiutils endpoint matching, fall back to pcbnew subprocess with `board.GetConnectivity()`.
- **Individual tools, not a pipeline:** Each tool is independently callable. The LLM orchestrates the post-autoroute workflow via the pcb-layout skill, not a monolithic tool.
- **File mutation:** All tools modify the PCB file in-place (same pattern as existing write tools). The autorouter already writes to a `_routed.kicad_pcb` copy, so the original is safe.

## Tool APIs

### 1. `add_copper_zone`

Creates an unfilled copper zone definition. Caller must invoke `fill_zones` afterward to compute copper polygons.

```python
@mcp.tool(annotations=_ADDITIVE)
def add_copper_zone(
    net_name: str,
    layer: str,
    corners: list[dict],
    clearance: float = 0.5,
    min_thickness: float = 0.25,
    thermal_relief: bool = True,
    thermal_gap: float = 0.5,
    thermal_bridge_width: float = 0.5,
    priority: int = 0,
    pcb_path: str = PCB_PATH,
) -> str:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `net_name` | `str` | required | Net to fill (e.g. "GND", "PGND") |
| `layer` | `str` | required | Copper layer (e.g. "F.Cu", "B.Cu") |
| `corners` | `list[dict]` | required | Polygon vertices `[{"x": float, "y": float}, ...]` in mm, minimum 3 |
| `clearance` | `float` | `0.5` | Zone clearance in mm |
| `min_thickness` | `float` | `0.25` | Minimum copper thickness in mm |
| `thermal_relief` | `bool` | `True` | Use thermal relief on pad connections |
| `thermal_gap` | `float` | `0.5` | Thermal relief gap in mm |
| `thermal_bridge_width` | `float` | `0.5` | Thermal relief bridge width in mm |
| `priority` | `int` | `0` | Zone priority (higher fills first) |
| `pcb_path` | `str` | `PCB_PATH` | Path to `.kicad_pcb` file |

**Return:** JSON `{"net": "GND", "layer": "F.Cu", "corners": 4, "clearance_mm": 0.5}`

**Implementation:**
- Create a kiutils `Zone` object with `net`, `netName`, `layers`, `priority`, `clearance`, `minThickness`
- For thermal relief: leave `connectPads` as `None` (default = thermal relief). For direct connect: set `connectPads = "full"`. For no connect: set `connectPads = "no"`
- Set `fillSettings = FillSettings(thermalGap=thermal_gap, thermalBridgeWidth=thermal_bridge_width)` — thermal gap/bridge width live on `FillSettings`, not on `connectPads`
- If `thermal_relief` is `False`, set `connectPads = "full"` (solid copper connection, no relief)
- Create a `ZonePolygon` from the corners list and assign to `zone.polygons`
- Set `hatch` to edge hatch (default KiCad style)
- Look up the net number from `board.nets` by matching `net_name`
- Append to `board.zones` and save

### 2. `fill_zones`

Invokes pcbnew's zone filler engine to compute copper fill polygons for all zones on the board.

```python
@mcp.tool(annotations=_ADDITIVE)
def fill_zones(
    pcb_path: str = PCB_PATH,
) -> str:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pcb_path` | `str` | `PCB_PATH` | Path to `.kicad_pcb` file |

**Return:** JSON `{"zones_filled": 2, "status": "ok"}`

**Implementation:**
- Uses pcbnew subprocess (same `find_pcbnew_python()` from `_freerouting.py`):
  ```python
  script = """
  import pcbnew
  b = pcbnew.LoadBoard({path!r})
  filler = pcbnew.ZONE_FILLER(b)
  zones = b.Zones()
  filler.Fill(zones)
  pcbnew.SaveBoard({path!r}, b)
  print(len(zones))
  """
  ```
- Always fills all zones (partial fills cause inconsistencies)
- Returns zone count from subprocess stdout

### 3. `set_trace_width`

Changes the width of existing traces matching the given filters.

```python
@mcp.tool(annotations=_ADDITIVE)
def set_trace_width(
    width: float,
    net_name: str | None = None,
    layer: str | None = None,
    x_min: float | None = None,
    y_min: float | None = None,
    x_max: float | None = None,
    y_max: float | None = None,
    pcb_path: str = PCB_PATH,
) -> str:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `width` | `float` | required | New trace width in mm |
| `net_name` | `str \| None` | `None` | Filter by net name |
| `layer` | `str \| None` | `None` | Filter by layer |
| `x_min, y_min, x_max, y_max` | `float \| None` | `None` | Bounding box filter in mm |
| `pcb_path` | `str` | `PCB_PATH` | Path to `.kicad_pcb` file |

**Return:** JSON `{"traces_modified": 14, "net": "VIN", "new_width_mm": 1.0}`

**Implementation:**
- Iterates `board.traceItems`, filtering `Segment` objects
- At least one filter is required (error if all filters are None)
- Filters are AND'd: net AND layer AND bounding box
- Bounding box check: both endpoints of the segment must fall within the box
- Updates `segment.width` on each match
- Saves board via kiutils

### 4. `add_thermal_vias`

Adds a grid of vias under a footprint pad for thermal dissipation (typically QFN exposed pads).

```python
@mcp.tool(annotations=_ADDITIVE)
def add_thermal_vias(
    reference: str,
    pad_number: str = "",
    rows: int = 3,
    cols: int = 3,
    spacing: float = 1.0,
    via_size: float = 0.8,
    via_drill: float = 0.3,
    net_name: str | None = None,
    pcb_path: str = PCB_PATH,
) -> str:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `reference` | `str` | required | Footprint reference (e.g. "U1") |
| `pad_number` | `str` | `""` | Specific pad number (empty = auto-detect thermal pad) |
| `rows` | `int` | `3` | Number of via rows |
| `cols` | `int` | `3` | Number of via columns |
| `spacing` | `float` | `1.0` | Center-to-center via spacing in mm |
| `via_size` | `float` | `0.8` | Via annular ring diameter in mm |
| `via_drill` | `float` | `0.3` | Via drill diameter in mm |
| `net_name` | `str \| None` | `None` | Net for vias (auto-detected from pad if None) |
| `pcb_path` | `str` | `PCB_PATH` | Path to `.kicad_pcb` file |

**Return:** JSON `{"vias_added": 9, "reference": "U1", "pad": "5", "net": "GND", "center": {"x": 25.0, "y": 30.0}}`

**Implementation:**
- Find footprint by `reference` in `board.footprints`
- If `pad_number` is empty, find the thermal/exposed pad: the SMD pad with the largest area (width × height) that is not a standard pin
- Compute pad center in board coordinates: apply 2D rotation of pad offset around footprint origin using footprint rotation angle `θ`:
  ```
  pad_x = fp_x + (offset_x * cos(θ) - offset_y * sin(θ))
  pad_y = fp_y + (offset_x * sin(θ) + offset_y * cos(θ))
  ```
  where `fp_x, fp_y` is the footprint position, `offset_x, offset_y` is the pad's local position, and `θ` is the footprint rotation in radians
- Get net from pad (or use `net_name` override)
- Compute grid positions centered on pad center
- Create kiutils `Via` objects (same approach as existing `add_via` tool) with `position`, `size`, `drill`, `net`, `layers` (F.Cu + B.Cu)
- Append to `board.traceItems` and save

### 5. `remove_traces`

Removes trace segments matching the given filters.

```python
@mcp.tool(annotations=_DESTRUCTIVE)
def remove_traces(
    net_name: str | None = None,
    layer: str | None = None,
    x_min: float | None = None,
    y_min: float | None = None,
    x_max: float | None = None,
    y_max: float | None = None,
    pcb_path: str = PCB_PATH,
) -> str:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `net_name` | `str \| None` | `None` | Filter by net name |
| `layer` | `str \| None` | `None` | Filter by layer |
| `x_min, y_min, x_max, y_max` | `float \| None` | `None` | Bounding box filter in mm |
| `pcb_path` | `str` | `PCB_PATH` | Path to `.kicad_pcb` file |

**Return:** JSON `{"traces_removed": 8, "net": "VIN", "layer": "F.Cu"}`

**Implementation:**
- Same filtering logic as `set_trace_width` (AND'd, at least one required)
- Removes matching `Segment` objects from `board.traceItems`
- Does not remove vias (traces only)
- Saves board via kiutils

### 6. `set_net_class`

Creates or updates a net class with design rules, and assigns nets to it.

```python
@mcp.tool(annotations=_ADDITIVE)
def set_net_class(
    name: str,
    nets: list[str],
    track_width: float | None = None,
    clearance: float | None = None,
    via_size: float | None = None,
    via_drill: float | None = None,
    pcb_path: str = PCB_PATH,
) -> str:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Net class name (e.g. "Power") |
| `nets` | `list[str]` | required | Net names to assign |
| `track_width` | `float \| None` | `None` | Default trace width in mm (None = inherit board default) |
| `clearance` | `float \| None` | `None` | Clearance in mm |
| `via_size` | `float \| None` | `None` | Via size in mm |
| `via_drill` | `float \| None` | `None` | Via drill in mm |
| `pcb_path` | `str` | `PCB_PATH` | Path to `.kicad_pcb` file |

**Return:** JSON `{"net_class": "Power", "nets_assigned": 3, "track_width_mm": 0.5, "clearance_mm": 0.3}`

**Implementation:**
- **Note:** kiutils `Board` has no `designSettings` or `netClasses` attribute. Net classes in KiCad 8 `.kicad_pcb` files are stored as S-expressions within the `(setup ...)` block (e.g., `(net_class "Power" (clearance 0.3) (track_width 0.5) ...)`) and net-to-class assignments are in `(net_class_assignments ...)` or within each net definition.
- **Approach:** Use raw S-expression manipulation on the `.kicad_pcb` file:
  1. Read the file as text
  2. Parse or locate the `(setup ...)` block
  3. Insert/update the net class definition with the provided parameters
  4. Insert/update net-to-class assignments for each net in `nets`
  5. Write the file back
- Alternatively, use pcbnew subprocess (consistent with `fill_zones`):
  ```python
  script = """
  import pcbnew
  b = pcbnew.LoadBoard({path!r})
  ds = b.GetDesignSettings()
  nc = pcbnew.NETCLASS({name!r})
  nc.SetTrackWidth(pcbnew.FromMM({track_width}))
  nc.SetClearance(pcbnew.FromMM({clearance}))
  nc.SetViaDiameter(pcbnew.FromMM({via_size}))
  nc.SetViaDrill(pcbnew.FromMM({via_drill}))
  ds.SetNetClass({name!r}, nc)
  for net_name in {nets!r}:
      b.FindNet(net_name).SetNetClass(nc)
  pcbnew.SaveBoard({path!r}, b)
  """
  ```
- **Recommended:** Use pcbnew subprocess. Net class management involves design settings that kiutils doesn't model, and pcbnew has native API support. This is the same pattern as `fill_zones`.

### 7. `remove_dangling_tracks`

Detects and removes trace segments that have an unconnected endpoint.

```python
@mcp.tool(annotations=_DESTRUCTIVE)
def remove_dangling_tracks(
    pcb_path: str = PCB_PATH,
) -> str:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pcb_path` | `str` | `PCB_PATH` | Path to `.kicad_pcb` file |

**Return:** JSON `{"tracks_removed": 2, "iterations": 1}`

**Implementation (kiutils, primary):**
1. Collect all connection points: pad positions (from footprints), via positions, trace endpoints
2. For each trace segment, check both endpoints against the connection point set (within 0.001mm tolerance)
3. A segment is "dangling" if either endpoint has no other connection
4. Remove dangling segments iteratively (removing one may expose new dangling ends)
5. Repeat until stable (no more removals)

**Implementation (pcbnew fallback):**
If kiutils endpoint matching proves unreliable with edge cases (arcs, angled stubs):
```python
script = """
import pcbnew
b = pcbnew.LoadBoard({path!r})
conn = b.GetConnectivity()
removed = 0
changed = True
while changed:
    changed = False
    for t in list(b.GetTracks()):
        if isinstance(t, pcbnew.PCB_TRACK):
            s = conn.GetConnectedItems(t, [pcbnew.PCB_PAD_T, pcbnew.PCB_VIA_T, pcbnew.PCB_TRACE_T])
            # check endpoint connectivity
            ...
"""
```

## Shared Implementation Details

### Filtering Pattern

`set_trace_width`, `remove_traces` share the same filtering logic. Extract a shared helper:

```python
def _filter_segments(
    board: Board,
    net_name: str | None,
    layer: str | None,
    x_min: float | None,
    y_min: float | None,
    x_max: float | None,
    y_max: float | None,
) -> list[Segment]:
```

This avoids duplicating the filter/bounding-box logic.

### Net Lookup

Multiple tools need to look up net numbers by name. Extract a helper:

```python
def _find_net(board: Board, net_name: str) -> tuple[int, str]:
    """Returns (net_number, net_name) or raises ValueError."""
```

### Tool Annotations

Tools use the existing annotation presets from `pcb.py`:
- `_ADDITIVE` (creates/modifies): `add_copper_zone`, `fill_zones`, `set_trace_width`, `add_thermal_vias`, `set_net_class` — consistent with `add_trace`, `add_via`, `place_footprint`
- `_DESTRUCTIVE` (removes): `remove_traces`, `remove_dangling_tracks` — consistent with `remove_footprint`

## Skill Updates

### `skills/pcb-layout/SKILL.md`

**1. Add new tools to MCP Tools section:**

Add a new tool group after "Routing":

```markdown
**Post-routing refinement:**
- `add_copper_zone` — create a copper zone (ground plane, power fill) with polygon outline
- `fill_zones` — compute copper fills for all zones (requires pcbnew)
- `set_trace_width` — change width of existing traces by net, layer, or region
- `add_thermal_vias` — add a via array under a footprint pad (QFN thermal pads)
- `remove_traces` — delete traces by net, layer, or region
- `set_net_class` — create/update net classes with design rules (requires pcbnew)
- `remove_dangling_tracks` — clean up unconnected trace stubs
```

**2. Add post-autoroute phase to Layout Process:**

Replace the current steps 5-6 transition with an explicit post-autoroute phase. The updated Layout Process becomes:

```markdown
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
```

**3. Add a new "Post-Autoroute Refinement" section** after "Via Usage" (before "Layer Stack"):

```markdown
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
```

### `skills/verification/SKILL.md`

**1. Add new tools to PCB fixes section:**

Update the "PCB fixes" tool list (after line 68) to include:

```markdown
**PCB fixes:**
- `add_trace` — route missing connections
- `add_via` — add vias for layer transitions
- `move_footprint` — fix clearance violations by repositioning
- `set_trace_width` — fix trace width violations
- `remove_traces` — remove problematic traces for re-routing
- `add_copper_zone` — add missing ground planes / copper fills
- `fill_zones` — recompute zone fills after changes
- `remove_dangling_tracks` — clean up unconnected trace stubs
```

**2. Add post-autoroute quality checks to DRC workflow:**

Add a new "Category 5" after "Category 4: Copper zone issues" in the DRC workflow:

```markdown
**Category 5: Post-autoroute quality issues**

These are not DRC errors but quality problems that the autorouter creates:

| Check | What to look for | Fix |
|-------|-----------------|-----|
| Power trace width | Power nets (VIN, VOUT, SW, GND) at minimum width (0.25mm) | `set_trace_width` to widen per current table |
| Missing copper zones | Board has 0 zones (no ground plane) | `add_copper_zone` on both layers, then `fill_zones` |
| Missing thermal vias | QFN/exposed-pad ICs with no vias under thermal pad | `add_thermal_vias` for each exposed-pad IC |
| Dangling tracks | Trace stubs from autorouter that connect to nothing | `remove_dangling_tracks` |
| Excessive vias | Autorouter used unnecessary layer transitions | Manual review — remove and re-route if egregious |
```

**3. Add to pre-manufacturing checklist:**

Add these items to the checklist (after line 248):

```markdown
- [ ] Power traces sized for expected current (not autorouter minimums)
- [ ] Ground planes present on appropriate layers
- [ ] Thermal vias under all QFN/exposed-pad ICs
- [ ] No dangling track stubs
```

## Error Handling

| Condition | Behavior |
|-----------|----------|
| `add_copper_zone`: fewer than 3 corners | Return JSON error |
| `add_copper_zone`: net not found in board | Return JSON error listing available nets |
| `fill_zones`: pcbnew not available | Return JSON error with install guidance |
| `set_trace_width` / `remove_traces`: no filters provided | Return JSON error requiring at least one filter |
| `set_trace_width` / `remove_traces`: no traces match | Return JSON with `traces_modified: 0` (not an error) |
| `add_thermal_vias`: footprint not found | Return JSON error |
| `add_thermal_vias`: no suitable thermal pad found | Return JSON error suggesting explicit `pad_number` |
| `set_net_class`: net not found in board | Return JSON error listing available nets |
| `set_net_class`: pcbnew not available | Return JSON error with install guidance |
| `remove_dangling_tracks`: no tracks on board | Return JSON `{"tracks_removed": 0, "iterations": 0}` (not an error) |

## Testing

### Unit Tests (kiutils, no subprocess)

- `add_copper_zone`: verify zone added to `board.zones` with correct net, layer, polygon, settings
- `set_trace_width`: create board with traces on different nets, verify selective width changes
- `add_thermal_vias`: place a footprint, add thermal vias, verify grid positions and net assignment
- `remove_traces`: create board with traces, verify selective removal
- `set_net_class`: verify net class creation and net assignment
- `remove_dangling_tracks`: create board with known dangling segments, verify removal
- Shared filter helper: test net, layer, bounding box, and combined filters

### Integration Tests (requires pcbnew)

- `fill_zones`: verify zones get filled (check `filledPolygons` is populated)
- End-to-end: `add_copper_zone` → `fill_zones` → verify board has filled zones

### Test Infrastructure

- Use existing `scratch_pcb` pytest fixture from `tests/conftest.py`
- Add helper to create boards with pre-placed traces for filter tests
- Update `test_unified_server.py` tool count assertions (+7 tools)
- Update `test_tool_annotations.py`: add additive tools to `_ADDITIVE` list, destructive tools to `_DESTRUCTIVE` list

## New Files

None — all tools go in existing `mcp_server_kicad/pcb.py`.

## Modified Files

- `mcp_server_kicad/pcb.py` — Add seven tools + shared helpers
- `skills/pcb-layout/SKILL.md` — Add tools, post-autoroute phase, post-autoroute refinement section
- `skills/verification/SKILL.md` — Add tools to PCB fixes, post-autoroute quality checks, pre-manufacturing checklist items
- `tests/test_pcb_write_tools.py` — Add tests for all seven tools
- `tests/test_unified_server.py` — Update tool count assertions
- `tests/test_tool_annotations.py` — Add to `_ADDITIVE` and `_DESTRUCTIVE` annotation lists
