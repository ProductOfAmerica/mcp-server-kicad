# DSN Keepout Injection Design Spec

## Problem

KiCad's `ExportSpecctraDSN()` does not export keepout zones that are defined inside footprints (known KiCad bug #7684). This means Freerouting's autorouter has no awareness of these keepout areas and routes traces/vias through them, causing DRC violations. A common example is the ESP32-S3-WROOM-1 antenna keepout zone.

Board-level keepout zones are exported correctly and are not affected.

## Solution

Before exporting the DSN file, promote footprint-level keepout zones to board-level zones in a temporary copy of the PCB. KiCad's `ExportSpecctraDSN` then includes them in the DSN output, and Freerouting respects them as physical obstacles during routing.

Additionally, fix `_transform_local_to_board` to handle back-side (B.Cu) footprints where the local X axis is mirrored.

## Architecture

### New Function

**`_promote_footprint_keepouts(pcb_path: str, output_path: str) -> int`** in `_shared.py`

This function belongs in `_shared.py` because it depends on helpers already there (`_load_board`, `_transform_local_to_board`, `_gen_uuid`) and kiutils types. `_freerouting.py` remains a clean subprocess orchestration module.

1. Load the PCB via kiutils (`_load_board`)
2. Iterate all footprints; for each footprint, iterate its `zones` list (via `getattr(fp, "zones", None)` for safety)
3. Skip footprints with `fp.position is None`
4. For zones with non-None `keepoutSettings`, transform all polygon coordinates from footprint-local space to board space using `_transform_local_to_board(fp_x, fp_y, fp_angle, local_x, local_y, mirrored)` where `mirrored = fp.layer == "B.Cu"`
5. Create equivalent board-level `Zone` objects with deep-copied `keepoutSettings` and `layers` (to avoid shared references)
6. Iterate all polygons per zone (not just `polygons[0]`), creating one promoted zone per polygon
7. Append promoted zones to `board.zones`
8. If any zones were promoted: set `board.filePath = output_path`, then call `board.to_file()`. If none were promoted, do not write the file.
9. Wrap `board.to_file()` in a try/except for `OSError`, re-raising as `ToolError("Failed to prepare PCB for autorouting: {e}")`
10. Return the count of promoted keepout zones

### Integration Point

In `autoroute_pcb()` (`pcb.py`), the flow changes from:

```
1. Create temp dir
2. export_dsn(pcb_path, dsn_path)
3. run_freerouting(...)
4. import_ses(pcb_path, ...)
```

To:

```
1. Create temp dir
2. count = _promote_footprint_keepouts(pcb_path, temp_pcb_path)
3. dsn_source = temp_pcb_path if count > 0 else pcb_path
4. export_dsn(dsn_source, dsn_path)
5. run_freerouting(...)
6. import_ses(pcb_path, ...)    # still targets original PCB
```

If no footprint-level keepout zones exist, `count` is 0, `dsn_source` falls back to the original `pcb_path`, and no temp PCB file is created (zero overhead).

The SES import always targets the original PCB, so promoted keepout zones never persist beyond the temp file.

The promoted zone count is stored in the `AutorouteResult` as `keepouts_promoted` for immediate user visibility.

### Back-Side Footprint Mirroring Fix

**`_transform_local_to_board`** (`_shared.py:874`) gains a new parameter:

```python
def _transform_local_to_board(
    fp_x: float,
    fp_y: float,
    angle: float,
    local_x: float,
    local_y: float,
    mirrored: bool = False,
) -> tuple[float, float]:
```

When `mirrored=True` (footprint on B.Cu), the local X coordinate is negated before applying rotation:

```python
if mirrored:
    local_x = -local_x
theta = math.radians(angle or 0)
cos_t = math.cos(theta)
sin_t = math.sin(theta)
board_x = fp_x + (local_x * cos_t - local_y * sin_t)
board_y = fp_y + (local_x * sin_t + local_y * cos_t)
```

The `mirrored` parameter defaults to `False` so all existing callers continue to work without changes. Callers should be updated to pass `mirrored=fp.layer == "B.Cu"` where applicable:

- `_check_footprint_keepout_violations` (_shared.py:1102) - update
- `_promote_footprint_keepouts` (new) - pass mirrored flag
- `pcb.py:955` (pad position in `add_via_stitching`) - update
- `pcb.py:1109` (pad positions in `remove_unconnected_traces`) - update
- `pcb.py:1653` (courtyard bounds in `get_footprint_bounds`) - update

### Coordinate Transformation

Footprint zones store polygon coordinates in local space relative to the footprint origin and rotation. Each vertex is transformed using the updated `_transform_local_to_board()` helper:

```python
mirrored = fp.layer == "B.Cu"
board_x, board_y = _transform_local_to_board(
    fp.position.X, fp.position.Y, fp.position.angle or 0,
    local_x, local_y, mirrored=mirrored
)
```

### Keepout Type Handling

No DSN-level type mapping is needed. We create standard kiutils `Zone` objects with the same `keepoutSettings` as the source footprint zone. KiCad's `ExportSpecctraDSN` handles serialization to the correct DSN keepout types:

- `tracks: "not_allowed"` results in DSN `(keepout ...)` entries (blocks trace routing)
- `vias: "not_allowed"` results in DSN `(via_keepout ...)` entries (blocks via placement)
- `pads`, `copperpour`, `footprints` settings are preserved but have no effect on Freerouting

### Zone Construction

For each polygon in a source keepout zone, a promoted zone is constructed:

```python
import copy

for source_poly in source_zone.polygons:
    zone = Zone()
    zone.net = 0
    zone.netName = ""
    zone.layers = copy.deepcopy(source_zone.layers)
    zone.tstamp = _gen_uuid()
    zone.hatch = Hatch(style="edge", pitch=0.5)
    zone.keepoutSettings = copy.deepcopy(source_zone.keepoutSettings)
    # Transform all vertices from footprint-local to board coordinates
    transformed_poly = ZonePolygon()
    transformed_poly.coordinates = [
        Position(X=bx, Y=by)
        for c in source_poly.coordinates
        for bx, by in [_transform_local_to_board(
            fp_x, fp_y, fp_angle, c.X, c.Y, mirrored=mirrored
        )]
    ]
    zone.polygons = [transformed_poly]
    board.zones.append(zone)
    count += 1
```

`fillSettings` is intentionally omitted. Keepout zones do not need fill, consistent with `add_keepout_zone` in `pcb.py`.

`keepoutSettings` and `layers` are deep-copied to avoid shared references between the footprint zone and the promoted board zone.

### Result Model Update

Add `keepouts_promoted: int = 0` to `AutorouteResult` in `models.py`:

```python
class AutorouteResult(BaseModel):
    routed_path: str
    traces_added: int
    vias_added: int
    text_fields_fixed: int
    drc_violations: int | None = None
    drc_unconnected: int | None = None
    keepouts_promoted: int = 0  # NEW
```

### Error Handling

- If no footprint-level keepout zones exist, return 0. The caller checks the return value and skips the temp PCB path. No file is written.
- The promoted zone count is returned in `AutorouteResult.keepouts_promoted` for user visibility.
- If `board.to_file()` fails with `OSError`, catch and re-raise as `ToolError` with a clear message.
- If `fp.position is None`, skip the footprint (matches `_check_footprint_keepout_violations` pattern).

## Testing

Tests go in `tests/test_freerouting.py` (existing file).

### `_promote_footprint_keepouts` tests

1. **Happy path:** Board with a footprint-level keepout zone. Verify the function returns the correct count, the output file is created, and the promoted zone has correct board-space coordinates.
2. **No keepouts:** Board with no footprint keepout zones. Verify returns 0 and output file is NOT created.
3. **Coordinate transformation:** Verify a footprint at a non-zero angle produces correctly rotated keepout polygon vertices.
4. **Multiple polygons per zone:** Verify all polygons are promoted (one Zone per polygon), not just the first.
5. **`fp.position is None`:** Verify the footprint is skipped gracefully.
6. **Deep copy isolation:** Modify the promoted zone's `keepoutSettings` and verify the source zone is unaffected.
7. **`dsn_source` branching:** Verify `autoroute_pcb` uses `temp_pcb_path` when count > 0 and `pcb_path` when count == 0.
8. **kiutils-to-pcbnew round-trip:** Verify a kiutils-modified PCB (with promoted keepout) successfully exports to DSN via pcbnew and the DSN contains keepout entries.
9. **Save failure:** Mock `board.to_file()` to raise `OSError`, verify it is caught and re-raised as `ToolError`.

### `_transform_local_to_board` mirroring tests

10. **Back-side footprint (mirrored=True):** Verify X coordinate is negated before rotation.
11. **Front-side footprint (mirrored=False):** Verify existing behavior is unchanged (backward compatible).
12. **Mirrored + rotated:** Verify correct result when both mirroring and rotation are applied.

## Files Modified

- `mcp_server_kicad/_shared.py` - add `_promote_footprint_keepouts()`, update `_transform_local_to_board()` with `mirrored` param, update `_check_footprint_keepout_violations` to pass mirror flag
- `mcp_server_kicad/pcb.py` - modify `autoroute_pcb()` flow, update 3 callers of `_transform_local_to_board` to pass mirror flag
- `mcp_server_kicad/models.py` - add `keepouts_promoted` field to `AutorouteResult`
- `tests/test_freerouting.py` - add 9 tests for keepout promotion
- `tests/test_shared_helpers.py` - add 3 tests for mirroring in `_transform_local_to_board`