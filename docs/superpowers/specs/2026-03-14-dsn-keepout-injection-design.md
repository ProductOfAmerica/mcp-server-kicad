# DSN Keepout Injection Design Spec

## Problem

KiCad's `ExportSpecctraDSN()` does not export keepout zones that are defined inside footprints (known KiCad bug #7684). This means Freerouting's autorouter has no awareness of these keepout areas and routes traces/vias through them, causing DRC violations. A common example is the ESP32-S3-WROOM-1 antenna keepout zone.

Board-level keepout zones are exported correctly and are not affected.

## Solution

Before exporting the DSN file, promote footprint-level keepout zones to board-level zones in a temporary copy of the PCB. KiCad's `ExportSpecctraDSN` then includes them in the DSN output, and Freerouting respects them as physical obstacles during routing.

## Architecture

### New Function

**`_promote_footprint_keepouts(pcb_path: str, output_path: str) -> int`** in `_shared.py`

This function belongs in `_shared.py` because it depends on helpers already there (`_load_board`, `_transform_local_to_board`, `_gen_uuid`) and kiutils types. `_freerouting.py` remains a clean subprocess orchestration module.

1. Load the PCB via kiutils (`_load_board`)
2. Iterate all footprints; for each footprint, iterate its `zones` list
3. For zones with non-None `keepoutSettings`, transform polygon coordinates from footprint-local space to board space using `_transform_local_to_board(fp_x, fp_y, fp_angle, local_x, local_y)`
4. Create equivalent board-level `Zone` objects with deep-copied `keepoutSettings` and `layers` (to avoid shared references)
5. Append promoted zones to `board.zones`
6. Save the modified board: set `board.filePath = output_path`, then call `board.to_file()` (this ensures the original PCB is never overwritten)
7. Return the count of promoted keepout zones

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

### Coordinate Transformation

Footprint zones store polygon coordinates in local space relative to the footprint origin and rotation. Each vertex is transformed using the existing `_transform_local_to_board()` helper:

```python
board_x, board_y = _transform_local_to_board(
    fp.position.X, fp.position.Y, fp.position.angle or 0,
    local_x, local_y
)
```

**Back-side footprints:** kiutils stores footprint-local coordinates in their canonical form. The `_transform_local_to_board` helper handles rotation but not mirroring. For footprints on `B.Cu`, the X axis is mirrored. This is a pre-existing gap in `_transform_local_to_board` that also affects `_check_footprint_keepout_violations`. If back-side keepout violations are observed during testing, the helper should be updated to handle mirroring, but this is out of scope for this spec.

### Keepout Type Handling

No DSN-level type mapping is needed. We create standard kiutils `Zone` objects with the same `keepoutSettings` as the source footprint zone. KiCad's `ExportSpecctraDSN` handles serialization to the correct DSN keepout types:

- `tracks: "not_allowed"` results in DSN `(keepout ...)` entries (blocks trace routing)
- `vias: "not_allowed"` results in DSN `(via_keepout ...)` entries (blocks via placement)
- `pads`, `copperpour`, `footprints` settings are preserved but have no effect on Freerouting

### Zone Construction

Each promoted zone is constructed as:

```python
import copy

zone = Zone()
zone.net = 0
zone.netName = ""
zone.layers = copy.deepcopy(source_zone.layers)
zone.tstamp = _gen_uuid()
zone.hatch = Hatch(style="edge", pitch=0.5)
zone.keepoutSettings = copy.deepcopy(source_zone.keepoutSettings)
zone.polygons = [transformed_polygon]
```

`fillSettings` is intentionally omitted. Keepout zones do not need fill, consistent with `add_keepout_zone` in `pcb.py`.

`keepoutSettings` and `layers` are deep-copied to avoid shared references between the footprint zone and the promoted board zone.

### Error Handling

- If no footprint-level keepout zones exist, return 0. The caller checks the return value and skips the temp PCB path.
- The promoted zone count is logged for debugging but does not change the `AutorouteResult` model.
- If the temp PCB save fails, propagate the error (autoroute cannot proceed safely without keepout data).

## Testing

Tests go in `tests/test_freerouting.py` (existing file).

- Unit test: verify `_promote_footprint_keepouts` correctly transforms and promotes a footprint keepout zone with proper coordinate transformation
- Unit test: verify deep copies are used (modifying the promoted zone does not affect the source)
- Unit test: verify that when no footprint keepouts exist, the function returns 0 and `output_path` is not created
- Integration: verify that a board with a footprint-level keepout zone produces a DSN file containing keepout entries after promotion

## Files Modified

- `mcp_server_kicad/_shared.py` - add `_promote_footprint_keepouts()` function
- `mcp_server_kicad/pcb.py` - modify `autoroute_pcb()` flow to call promotion before DSN export
- `tests/test_freerouting.py` - add tests for the new function
