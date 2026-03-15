# DSN Keepout Injection Design Spec

## Problem

KiCad's `ExportSpecctraDSN()` does not export keepout zones that are defined inside footprints (known KiCad bug #7684). This means Freerouting's autorouter has no awareness of these keepout areas and routes traces/vias through them, causing DRC violations. A common example is the ESP32-S3-WROOM-1 antenna keepout zone.

Board-level keepout zones are exported correctly and are not affected.

## Solution

Before exporting the DSN file, promote footprint-level keepout zones to board-level zones in a temporary copy of the PCB. KiCad's `ExportSpecctraDSN` then includes them in the DSN output, and Freerouting respects them as physical obstacles during routing.

## Architecture

### New Function

**`_promote_footprint_keepouts(pcb_path: str, output_path: str) -> int`** in `_freerouting.py`

1. Load the PCB via kiutils (`_load_board`)
2. Iterate all footprints; for each footprint, iterate its `zones` list
3. For zones with non-None `keepoutSettings`, transform polygon coordinates from footprint-local space to board space using `_transform_local_to_board(fp_x, fp_y, fp_angle, local_x, local_y)`
4. Create equivalent board-level `Zone` objects with matching `keepoutSettings`, layers, and transformed polygon coordinates
5. Append promoted zones to `board.zones`
6. Save the modified board to `output_path` (a temp file)
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
2. _promote_footprint_keepouts(pcb_path, temp_pcb_path)   # NEW
3. export_dsn(temp_pcb_path, dsn_path)                     # from temp PCB
4. run_freerouting(...)
5. import_ses(pcb_path, ...)                               # still targets original PCB
```

If no footprint-level keepout zones exist, skip the promotion step and export DSN from the original PCB (zero overhead for boards without footprint keepouts).

The SES import always targets the original PCB, so promoted keepout zones never persist beyond the temp file.

### Coordinate Transformation

Footprint zones store polygon coordinates in local space relative to the footprint origin and rotation. Each vertex is transformed using the existing `_transform_local_to_board()` helper:

```python
board_x, board_y = _transform_local_to_board(
    fp.position.X, fp.position.Y, fp.position.angle or 0,
    local_x, local_y
)
```

### Keepout Type Handling

No DSN-level type mapping is needed. We create standard kiutils `Zone` objects with the same `keepoutSettings` as the source footprint zone. KiCad's `ExportSpecctraDSN` handles serialization to the correct DSN keepout types:

- `tracks: "not_allowed"` results in DSN `(keepout ...)` entries (blocks trace routing)
- `vias: "not_allowed"` results in DSN `(via_keepout ...)` entries (blocks via placement)
- `pads`, `copperpour`, `footprints` settings are preserved but have no effect on Freerouting

### Zone Construction

Each promoted zone is constructed as:

```python
zone = Zone()
zone.net = 0
zone.netName = ""
zone.layers = source_zone.layers  # preserve original layer list
zone.tstamp = _gen_uuid()
zone.hatch = Hatch(style="edge", pitch=0.5)
zone.keepoutSettings = source_zone.keepoutSettings  # copy restrictions
zone.polygons = [transformed_polygon]
```

### Error Handling

- If no footprint-level keepout zones exist, return 0 and skip temp PCB creation
- The promoted zone count is logged for debugging but does not change the `AutorouteResult` model
- If the temp PCB save fails, propagate the error (autoroute cannot proceed safely without keepout data)

## Testing

- Unit test: verify `_promote_footprint_keepouts` correctly transforms and promotes a footprint keepout zone
- Unit test: verify that when no footprint keepouts exist, the function returns 0
- Integration: verify that a board with a footprint-level keepout zone produces a DSN file containing keepout entries after promotion

## Files Modified

- `mcp_server_kicad/_freerouting.py` - add `_promote_footprint_keepouts()` function
- `mcp_server_kicad/pcb.py` - modify `autoroute_pcb()` flow to call promotion before DSN export
- `tests/test_freerouting.py` - add tests for the new function
