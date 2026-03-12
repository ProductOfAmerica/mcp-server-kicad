# Post-Autoroute PCB Tools Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add seven MCP tools for post-autoroute PCB refinement: copper zones, zone filling, trace width adjustment, thermal vias, trace removal, net classes, and dangling track cleanup.

**Architecture:** All tools live in `pcb.py`, following existing patterns (kiutils for file I/O, `_load_board` + manipulate + `board.to_file()`). Two tools (`fill_zones`, `set_net_class`) use pcbnew subprocess. Shared helpers (`_filter_segments`, `_find_net`) avoid duplication. TDD throughout.

**Tech Stack:** Python, kiutils, pcbnew (subprocess), pytest

**Spec:** `docs/superpowers/specs/2026-03-12-post-autoroute-tools-design.md`

---

## Chunk 1: Shared Helpers + `_find_net` + `_filter_segments`

### Task 1: Add kiutils zone imports to `_shared.py`

**Files:**
- Modify: `mcp_server_kicad/_shared.py:25` (add imports)
- Modify: `mcp_server_kicad/_shared.py:94` (add to `__all__`)

- [ ] **Step 1: Add zone-related imports**

In `mcp_server_kicad/_shared.py`, update the zone import line:

```python
# Change line 25 from:
from kiutils.items.zones import Zone

# To:
from kiutils.items.zones import FillSettings, Hatch, Zone, ZonePolygon
```

- [ ] **Step 2: Add new types to `__all__`**

In `mcp_server_kicad/_shared.py`, add `FillSettings`, `Hatch`, `ZonePolygon` to the `__all__` list alongside the existing `"Zone"` entry.

- [ ] **Step 3: Verify import works**

Run: `python -c "from mcp_server_kicad._shared import FillSettings, Hatch, ZonePolygon, Zone; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add mcp_server_kicad/_shared.py
git commit -m "feat: add FillSettings, Hatch, ZonePolygon imports to _shared"
```

---

### Task 2: Add new imports to `pcb.py`

**Files:**
- Modify: `mcp_server_kicad/pcb.py:28-46` (import block)

- [ ] **Step 1: Add new imports**

In `mcp_server_kicad/pcb.py`, add to the `_shared` import block:

```python
from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    OUTPUT_DIR,
    PCB_PATH,
    FillSettings,       # NEW
    Footprint,
    FpText,
    GrLine,
    GrText,
    Hatch,              # NEW
    Position,
    Segment,
    Via,
    Zone,               # NEW
    ZonePolygon,        # NEW
    _default_effects,
    _file_meta,
    _fp_ref,
    _fp_val,
    _gen_uuid,
    _load_board,
    _run_cli,
)
```

Also add `find_pcbnew_python` import from `_freerouting` (needed for `fill_zones` and `set_net_class`):

```python
from mcp_server_kicad._freerouting import (
    find_pcbnew_python as _find_pcbnew_python,
)
```

And add `import math` and `import subprocess` to the stdlib imports at the top (needed for thermal via rotation and pcbnew subprocess calls).

- [ ] **Step 2: Verify imports**

Run: `python -c "from mcp_server_kicad import pcb; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add mcp_server_kicad/pcb.py
git commit -m "feat: add zone and subprocess imports to pcb.py"
```

---

### Task 3: Implement `_find_net` helper

**Files:**
- Modify: `mcp_server_kicad/pcb.py` (add helper before tool functions, after line 46)
- Test: `tests/test_pcb_write_tools.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_pcb_write_tools.py`, add:

```python
class TestFindNet:
    def test_finds_existing_net(self, scratch_pcb):
        board = Board.from_file(str(scratch_pcb))
        net_num, net_name = pcb._find_net(board, "Net1")
        assert net_num == 1
        assert net_name == "Net1"

    def test_raises_for_missing_net(self, scratch_pcb):
        board = Board.from_file(str(scratch_pcb))
        with pytest.raises(ValueError, match="not found"):
            pcb._find_net(board, "NonExistent")
```

Add `import pytest` to the test file imports if not already there.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pcb_write_tools.py::TestFindNet -v`
Expected: FAIL with `AttributeError: module 'mcp_server_kicad.pcb' has no attribute '_find_net'`

- [ ] **Step 3: Write minimal implementation**

In `mcp_server_kicad/pcb.py`, add after the import block (before the first `@mcp.tool`):

```python
def _find_net(board, net_name: str) -> tuple[int, str]:
    """Return (net_number, net_name) for a named net, or raise ValueError."""
    for n in board.nets:
        if n.name == net_name:
            return n.number, n.name
    available = [n.name for n in board.nets if n.name]
    raise ValueError(
        f"Net {net_name!r} not found. Available nets: {available}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pcb_write_tools.py::TestFindNet -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_write_tools.py
git commit -m "feat: add _find_net helper for net lookup by name"
```

---

### Task 4: Implement `_filter_segments` helper

**Files:**
- Modify: `mcp_server_kicad/pcb.py` (add helper after `_find_net`)
- Test: `tests/test_pcb_write_tools.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_pcb_write_tools.py`, add a helper to build a board with multiple traces, then test the filter:

```python
def _board_with_traces(scratch_pcb):
    """Add several traces on different nets/layers for filter testing."""
    board = Board.from_file(str(scratch_pcb))
    for i, (net, layer, x) in enumerate([
        (1, "F.Cu", 10),
        (1, "B.Cu", 20),
        (2, "F.Cu", 30),
        (2, "B.Cu", 40),
    ]):
        seg = Segment()
        seg.start = Position(X=x, Y=50)
        seg.end = Position(X=x + 5, Y=50)
        seg.width = 0.25
        seg.layer = layer
        seg.net = net
        seg.tstamp = str(uuid.uuid4())
        board.traceItems.append(seg)
    board.to_file()
    return board


class TestFilterSegments:
    def test_filter_by_net(self, scratch_pcb):
        board = _board_with_traces(scratch_pcb)
        # Net 1 has the original scratch trace + 2 new ones = 3
        result = pcb._filter_segments(board, net_name="Net1", layer=None,
                                       x_min=None, y_min=None, x_max=None, y_max=None)
        assert len(result) == 3

    def test_filter_by_layer(self, scratch_pcb):
        board = _board_with_traces(scratch_pcb)
        result = pcb._filter_segments(board, net_name=None, layer="B.Cu",
                                       x_min=None, y_min=None, x_max=None, y_max=None)
        assert len(result) == 2

    def test_filter_by_net_and_layer(self, scratch_pcb):
        board = _board_with_traces(scratch_pcb)
        result = pcb._filter_segments(board, net_name="Net1", layer="F.Cu",
                                       x_min=None, y_min=None, x_max=None, y_max=None)
        # Original scratch trace (net 1, F.Cu) + 1 new one = 2
        assert len(result) == 2

    def test_filter_by_bbox(self, scratch_pcb):
        board = _board_with_traces(scratch_pcb)
        result = pcb._filter_segments(board, net_name=None, layer=None,
                                       x_min=25, y_min=45, x_max=45, y_max=55)
        # Only traces at x=30 and x=40 fall in this box
        assert len(result) == 2

    def test_no_filters_raises(self, scratch_pcb):
        board = _board_with_traces(scratch_pcb)
        with pytest.raises(ValueError, match="at least one filter"):
            pcb._filter_segments(board, net_name=None, layer=None,
                                  x_min=None, y_min=None, x_max=None, y_max=None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pcb_write_tools.py::TestFilterSegments -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write minimal implementation**

In `mcp_server_kicad/pcb.py`, add after `_find_net`:

```python
def _filter_segments(
    board,
    net_name: str | None,
    layer: str | None,
    x_min: float | None,
    y_min: float | None,
    x_max: float | None,
    y_max: float | None,
) -> list:
    """Filter board trace segments by net name, layer, and/or bounding box.

    At least one filter must be provided.
    Returns matching Segment objects.
    """
    if all(v is None for v in (net_name, layer, x_min, y_min, x_max, y_max)):
        raise ValueError("at least one filter is required")

    # Build net number lookup if filtering by name
    net_num = None
    if net_name is not None:
        net_num, _ = _find_net(board, net_name)

    result = []
    for item in board.traceItems:
        if not isinstance(item, Segment):
            continue
        if net_num is not None and item.net != net_num:
            continue
        if layer is not None and item.layer != layer:
            continue
        if x_min is not None or y_min is not None or x_max is not None or y_max is not None:
            sx, sy = item.start.X, item.start.Y
            ex, ey = item.end.X, item.end.Y
            if x_min is not None and (sx < x_min or ex < x_min):
                continue
            if y_min is not None and (sy < y_min or ey < y_min):
                continue
            if x_max is not None and (sx > x_max or ex > x_max):
                continue
            if y_max is not None and (sy > y_max or ey > y_max):
                continue
        result.append(item)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pcb_write_tools.py::TestFilterSegments -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_write_tools.py
git commit -m "feat: add _filter_segments helper for trace filtering"
```

---

## Chunk 2: `add_copper_zone` + `fill_zones`

### Task 5: Implement `add_copper_zone`

**Files:**
- Modify: `mcp_server_kicad/pcb.py` (add tool after `add_pcb_line`, before CLI analysis tools section ~line 469)
- Test: `tests/test_pcb_write_tools.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_pcb_write_tools.py`, add:

```python
from kiutils.items.zones import Zone


class TestAddCopperZone:
    def test_basic_zone(self, scratch_pcb):
        result = pcb.add_copper_zone(
            net_name="Net1",
            layer="F.Cu",
            corners=[{"x": 0, "y": 0}, {"x": 50, "y": 0}, {"x": 50, "y": 50}, {"x": 0, "y": 50}],
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert data["net"] == "Net1"
        assert data["layer"] == "F.Cu"
        assert data["corners"] == 4

        board = Board.from_file(str(scratch_pcb))
        assert len(board.zones) == 1
        zone = board.zones[0]
        assert zone.netName == "Net1"
        assert zone.layers == ["F.Cu"]
        assert zone.clearance == 0.5
        assert len(zone.polygons) == 1
        assert len(zone.polygons[0].coordinates) == 4

    def test_no_thermal_relief(self, scratch_pcb):
        pcb.add_copper_zone(
            net_name="Net1",
            layer="B.Cu",
            corners=[{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
            thermal_relief=False,
            pcb_path=str(scratch_pcb),
        )
        board = Board.from_file(str(scratch_pcb))
        zone = board.zones[0]
        assert zone.connectPads == "full"

    def test_fewer_than_3_corners(self, scratch_pcb):
        result = pcb.add_copper_zone(
            net_name="Net1",
            layer="F.Cu",
            corners=[{"x": 0, "y": 0}, {"x": 10, "y": 0}],
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert "error" in data

    def test_invalid_net(self, scratch_pcb):
        result = pcb.add_copper_zone(
            net_name="NonExistent",
            layer="F.Cu",
            corners=[{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert "error" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pcb_write_tools.py::TestAddCopperZone -v`
Expected: FAIL with `AttributeError: module 'mcp_server_kicad.pcb' has no attribute 'add_copper_zone'`

- [ ] **Step 3: Write minimal implementation**

In `mcp_server_kicad/pcb.py`, add after `add_pcb_line` (before the CLI analysis tools comment):

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
    """Create an unfilled copper zone. Call fill_zones afterward to compute fills.

    Args:
        net_name: Net name (e.g. "GND")
        layer: Copper layer (e.g. "F.Cu", "B.Cu")
        corners: Polygon vertices [{"x": float, "y": float}, ...], min 3
        clearance: Zone clearance in mm
        min_thickness: Minimum copper thickness in mm
        thermal_relief: Use thermal relief on pad connections
        thermal_gap: Thermal relief gap in mm
        thermal_bridge_width: Thermal relief bridge width in mm
        priority: Zone priority (higher fills first)
        pcb_path: Path to .kicad_pcb file
    """
    if len(corners) < 3:
        return json.dumps({"error": "At least 3 corners required for a zone polygon."})

    board = _load_board(pcb_path)
    try:
        net_num, _ = _find_net(board, net_name)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    zone = Zone()
    zone.net = net_num
    zone.netName = net_name
    zone.layers = [layer]
    zone.priority = priority
    zone.clearance = clearance
    zone.minThickness = min_thickness
    zone.tstamp = _gen_uuid()
    zone.hatch = Hatch(style="edge", pitch=0.5)

    if not thermal_relief:
        zone.connectPads = "full"
    # else: connectPads=None means thermal relief (KiCad default)

    zone.fillSettings = FillSettings(
        thermalGap=thermal_gap,
        thermalBridgeWidth=thermal_bridge_width,
    )

    poly = ZonePolygon()
    poly.coordinates = [Position(X=c["x"], Y=c["y"]) for c in corners]
    zone.polygons = [poly]

    board.zones.append(zone)
    board.to_file()
    return json.dumps({
        "net": net_name,
        "layer": layer,
        "corners": len(corners),
        "clearance_mm": clearance,
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pcb_write_tools.py::TestAddCopperZone -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_write_tools.py
git commit -m "feat: add add_copper_zone MCP tool"
```

---

### Task 6: Implement `fill_zones`

**Files:**
- Modify: `mcp_server_kicad/pcb.py` (add tool after `add_copper_zone`)
- Test: `tests/test_pcb_write_tools.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_pcb_write_tools.py`, add:

```python
class TestFillZones:
    def test_no_pcbnew_returns_error(self, scratch_pcb):
        """When pcbnew is not available, return a clear error."""
        with patch("mcp_server_kicad.pcb._find_pcbnew_python", return_value=(None, None)):
            result = pcb.fill_zones(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert "error" in data

    def test_success_with_mocked_subprocess(self, scratch_pcb):
        """Mock the pcbnew subprocess to test the happy path."""
        # First add a zone so there's something to fill
        pcb.add_copper_zone(
            net_name="Net1", layer="F.Cu",
            corners=[{"x": 0, "y": 0}, {"x": 50, "y": 0}, {"x": 50, "y": 50}, {"x": 0, "y": 50}],
            pcb_path=str(scratch_pcb),
        )

        mock_result = type("Result", (), {"returncode": 0, "stdout": "1\n", "stderr": ""})()
        with patch("mcp_server_kicad.pcb._find_pcbnew_python", return_value=("/usr/bin/python3", None)), \
             patch("subprocess.run", return_value=mock_result):
            result = pcb.fill_zones(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["zones_filled"] == 1
        assert data["status"] == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pcb_write_tools.py::TestFillZones -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write minimal implementation**

In `mcp_server_kicad/pcb.py`, add after `add_copper_zone`:

```python
@mcp.tool(annotations=_ADDITIVE)
def fill_zones(
    pcb_path: str = PCB_PATH,
) -> str:
    """Fill all copper zones on the board using pcbnew's zone filler.

    Requires KiCad's pcbnew Python bindings to be installed.

    Args:
        pcb_path: Path to .kicad_pcb file
    """
    pcb_path = str(Path(pcb_path).resolve())
    python, env = _find_pcbnew_python()
    if not python:
        return json.dumps({
            "error": "pcbnew Python bindings not found. Ensure KiCad is installed."
        })

    script = (
        "import pcbnew; "
        f"b = pcbnew.LoadBoard({pcb_path!r}); "
        "filler = pcbnew.ZONE_FILLER(b); "
        "zones = b.Zones(); "
        "filler.Fill(zones); "
        f"pcbnew.SaveBoard({pcb_path!r}, b); "
        "print(len(zones))"
    )
    result = subprocess.run(
        [python, "-c", script],
        capture_output=True, text=True, timeout=120,
        env=env,
    )
    if result.returncode != 0:
        return json.dumps({"error": f"Zone fill failed: {result.stderr.strip()}"})

    try:
        zone_count = int(result.stdout.strip())
    except ValueError:
        zone_count = 0

    return json.dumps({"zones_filled": zone_count, "status": "ok"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pcb_write_tools.py::TestFillZones -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_write_tools.py
git commit -m "feat: add fill_zones MCP tool"
```

---

## Chunk 3: `set_trace_width` + `remove_traces`

### Task 7: Implement `set_trace_width`

**Files:**
- Modify: `mcp_server_kicad/pcb.py` (add tool after `fill_zones`)
- Test: `tests/test_pcb_write_tools.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_pcb_write_tools.py`, add:

```python
class TestSetTraceWidth:
    def test_widen_by_net(self, scratch_pcb):
        _board_with_traces(scratch_pcb)
        result = pcb.set_trace_width(width=0.5, net_name="Net1", pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["traces_modified"] == 3  # original + 2 added
        assert data["new_width_mm"] == 0.5

        board = Board.from_file(str(scratch_pcb))
        for seg in board.traceItems:
            if isinstance(seg, Segment) and seg.net == 1:
                assert seg.width == 0.5

    def test_no_filters_returns_error(self, scratch_pcb):
        result = pcb.set_trace_width(width=0.5, pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert "error" in data

    def test_no_matches_returns_zero(self, scratch_pcb):
        result = pcb.set_trace_width(width=0.5, net_name="Net2", pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["traces_modified"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pcb_write_tools.py::TestSetTraceWidth -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write minimal implementation**

In `mcp_server_kicad/pcb.py`, add after `fill_zones`:

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
    """Change the width of existing traces matching the given filters.

    At least one filter (net_name, layer, or bounding box) is required.

    Args:
        width: New trace width in mm
        net_name: Filter by net name
        layer: Filter by layer
        x_min: Bounding box minimum X in mm
        y_min: Bounding box minimum Y in mm
        x_max: Bounding box maximum X in mm
        y_max: Bounding box maximum Y in mm
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    try:
        segments = _filter_segments(board, net_name, layer, x_min, y_min, x_max, y_max)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    for seg in segments:
        seg.width = width

    board.to_file()
    return json.dumps({
        "traces_modified": len(segments),
        "net": net_name,
        "new_width_mm": width,
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pcb_write_tools.py::TestSetTraceWidth -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_write_tools.py
git commit -m "feat: add set_trace_width MCP tool"
```

---

### Task 8: Implement `remove_traces`

**Files:**
- Modify: `mcp_server_kicad/pcb.py` (add tool after `set_trace_width`)
- Test: `tests/test_pcb_write_tools.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_pcb_write_tools.py`, add:

```python
class TestRemoveTraces:
    def test_remove_by_net(self, scratch_pcb):
        _board_with_traces(scratch_pcb)
        result = pcb.remove_traces(net_name="Net2", pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["traces_removed"] == 2

        board = Board.from_file(str(scratch_pcb))
        net2_segs = [t for t in board.traceItems if isinstance(t, Segment) and t.net == 2]
        assert len(net2_segs) == 0

    def test_does_not_remove_vias(self, scratch_pcb):
        # Add a via on Net1
        pcb.add_via(100, 100, net=1, pcb_path=str(scratch_pcb))
        result = pcb.remove_traces(net_name="Net1", pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["traces_removed"] == 1  # only the scratch trace segment

        board = Board.from_file(str(scratch_pcb))
        vias = [t for t in board.traceItems if isinstance(t, Via)]
        assert len(vias) == 1  # via preserved

    def test_no_filters_returns_error(self, scratch_pcb):
        result = pcb.remove_traces(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert "error" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pcb_write_tools.py::TestRemoveTraces -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write minimal implementation**

In `mcp_server_kicad/pcb.py`, add after `set_trace_width`:

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
    """Remove trace segments matching the given filters. Does not remove vias.

    At least one filter (net_name, layer, or bounding box) is required.

    Args:
        net_name: Filter by net name
        layer: Filter by layer
        x_min: Bounding box minimum X in mm
        y_min: Bounding box minimum Y in mm
        x_max: Bounding box maximum X in mm
        y_max: Bounding box maximum Y in mm
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    try:
        segments = _filter_segments(board, net_name, layer, x_min, y_min, x_max, y_max)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    for seg in segments:
        board.traceItems.remove(seg)

    board.to_file()
    return json.dumps({
        "traces_removed": len(segments),
        "net": net_name,
        "layer": layer,
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pcb_write_tools.py::TestRemoveTraces -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_write_tools.py
git commit -m "feat: add remove_traces MCP tool"
```

---

## Chunk 4: `add_thermal_vias`

### Task 9: Implement `add_thermal_vias`

**Files:**
- Modify: `mcp_server_kicad/pcb.py` (add tool after `remove_traces`)
- Test: `tests/test_pcb_write_tools.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_pcb_write_tools.py`, add:

```python
class TestAddThermalVias:
    def test_basic_grid(self, scratch_pcb):
        """R1 is at (100, 100) in the scratch board. It has pads."""
        result = pcb.add_thermal_vias(
            reference="R1",
            pad_number="1",
            rows=2,
            cols=2,
            spacing=1.0,
            via_size=0.6,
            via_drill=0.3,
            net_name="Net1",
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert data["vias_added"] == 4
        assert data["reference"] == "R1"

        board = Board.from_file(str(scratch_pcb))
        vias = [t for t in board.traceItems if isinstance(t, Via)]
        assert len(vias) == 4

    def test_footprint_not_found(self, scratch_pcb):
        result = pcb.add_thermal_vias(
            reference="U99",
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert "error" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pcb_write_tools.py::TestAddThermalVias -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write minimal implementation**

In `mcp_server_kicad/pcb.py`, add after `remove_traces`:

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
    """Add a grid of thermal vias under a footprint pad.

    Args:
        reference: Footprint reference (e.g. "U1")
        pad_number: Pad number (empty = auto-detect largest SMD pad)
        rows: Number of via rows
        cols: Number of via columns
        spacing: Center-to-center via spacing in mm
        via_size: Via annular ring diameter in mm
        via_drill: Via drill diameter in mm
        net_name: Net for vias (auto-detected from pad if None)
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)

    # Find footprint
    fp = None
    for f in board.footprints:
        if _fp_ref(f) == reference:
            fp = f
            break
    if fp is None:
        return json.dumps({"error": f"Footprint {reference!r} not found."})

    # Find pad
    pad = None
    if pad_number:
        for p in fp.pads:
            if p.number == pad_number:
                pad = p
                break
        if pad is None:
            return json.dumps({"error": f"Pad {pad_number!r} not found on {reference}."})
    else:
        # Auto-detect: largest SMD pad by area
        best_area = 0
        for p in fp.pads:
            if p.type == "smd":
                area = (p.size.X or 0) * (p.size.Y or 0)
                if area > best_area:
                    best_area = area
                    pad = p
        if pad is None:
            return json.dumps({
                "error": f"No SMD pad found on {reference}. Specify pad_number explicitly."
            })

    # Compute pad center in board coordinates
    fp_x = fp.position.X
    fp_y = fp.position.Y
    theta = math.radians(fp.position.angle or 0)
    offset_x = pad.position.X
    offset_y = pad.position.Y
    pad_x = fp_x + (offset_x * math.cos(theta) - offset_y * math.sin(theta))
    pad_y = fp_y + (offset_x * math.sin(theta) + offset_y * math.cos(theta))

    # Determine net
    via_net = 0
    if net_name:
        try:
            via_net, _ = _find_net(board, net_name)
        except ValueError as e:
            return json.dumps({"error": str(e)})
    elif pad.net is not None:
        via_net = pad.net.number

    # Generate grid centered on pad
    vias_added = 0
    for r in range(rows):
        for c in range(cols):
            vx = pad_x + (c - (cols - 1) / 2) * spacing
            vy = pad_y + (r - (rows - 1) / 2) * spacing
            via = Via()
            via.position = Position(X=round(vx, 4), Y=round(vy, 4))
            via.size = via_size
            via.drill = via_drill
            via.net = via_net
            via.layers = ["F.Cu", "B.Cu"]
            via.tstamp = _gen_uuid()
            board.traceItems.append(via)
            vias_added += 1

    board.to_file()
    return json.dumps({
        "vias_added": vias_added,
        "reference": reference,
        "pad": pad.number,
        "net": net_name or (pad.net.name if pad.net else ""),
        "center": {"x": round(pad_x, 4), "y": round(pad_y, 4)},
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pcb_write_tools.py::TestAddThermalVias -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_write_tools.py
git commit -m "feat: add add_thermal_vias MCP tool"
```

---

## Chunk 5: `set_net_class` + `remove_dangling_tracks`

### Task 10: Implement `set_net_class`

**Files:**
- Modify: `mcp_server_kicad/pcb.py` (add tool after `add_thermal_vias`)
- Test: `tests/test_pcb_write_tools.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_pcb_write_tools.py`, add:

```python
class TestSetNetClass:
    def test_no_pcbnew_returns_error(self, scratch_pcb):
        with patch("mcp_server_kicad.pcb._find_pcbnew_python", return_value=(None, None)):
            result = pcb.set_net_class(
                name="Power", nets=["Net1"],
                track_width=0.5, pcb_path=str(scratch_pcb),
            )
        data = json.loads(result)
        assert "error" in data

    def test_success_with_mocked_subprocess(self, scratch_pcb):
        mock_result = type("Result", (), {"returncode": 0, "stdout": "3\n", "stderr": ""})()
        with patch("mcp_server_kicad.pcb._find_pcbnew_python", return_value=("/usr/bin/python3", None)), \
             patch("subprocess.run", return_value=mock_result):
            result = pcb.set_net_class(
                name="Power", nets=["Net1", "Net2"],
                track_width=0.5, clearance=0.3,
                pcb_path=str(scratch_pcb),
            )
        data = json.loads(result)
        assert data["net_class"] == "Power"
        assert data["nets_assigned"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pcb_write_tools.py::TestSetNetClass -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write minimal implementation**

In `mcp_server_kicad/pcb.py`, add after `add_thermal_vias`:

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
    """Create or update a net class with design rules and assign nets.

    Requires KiCad's pcbnew Python bindings.

    Args:
        name: Net class name (e.g. "Power")
        nets: Net names to assign to this class
        track_width: Default trace width in mm
        clearance: Clearance in mm
        via_size: Via size in mm
        via_drill: Via drill in mm
        pcb_path: Path to .kicad_pcb file
    """
    pcb_path = str(Path(pcb_path).resolve())
    python, env = _find_pcbnew_python()
    if not python:
        return json.dumps({
            "error": "pcbnew Python bindings not found. Ensure KiCad is installed."
        })

    # Build pcbnew script
    lines = [
        "import pcbnew",
        f"b = pcbnew.LoadBoard({pcb_path!r})",
        "ds = b.GetDesignSettings()",
        "ncs = ds.GetNetClasses()",
    ]
    # Create or get net class
    lines.append(f"nc = pcbnew.NETCLASS({name!r})")
    if track_width is not None:
        lines.append(f"nc.SetTrackWidth(pcbnew.FromMM({track_width}))")
    if clearance is not None:
        lines.append(f"nc.SetClearance(pcbnew.FromMM({clearance}))")
    if via_size is not None:
        lines.append(f"nc.SetViaDiameter(pcbnew.FromMM({via_size}))")
    if via_drill is not None:
        lines.append(f"nc.SetViaDrill(pcbnew.FromMM({via_drill}))")
    lines.append(f"ncs[{name!r}] = nc")

    # Assign nets
    for net in nets:
        lines.append(f"ni = b.FindNet({net!r})")
        lines.append(f"if ni: ni.SetNetClassName({name!r})")

    lines.append(f"pcbnew.SaveBoard({pcb_path!r}, b)")
    lines.append(f"print(len({nets!r}))")

    script = "; ".join(lines)
    result = subprocess.run(
        [python, "-c", script],
        capture_output=True, text=True, timeout=60,
        env=env,
    )
    if result.returncode != 0:
        return json.dumps({"error": f"set_net_class failed: {result.stderr.strip()}"})

    return json.dumps({
        "net_class": name,
        "nets_assigned": len(nets),
        "track_width_mm": track_width,
        "clearance_mm": clearance,
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pcb_write_tools.py::TestSetNetClass -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_write_tools.py
git commit -m "feat: add set_net_class MCP tool"
```

---

### Task 11: Implement `remove_dangling_tracks`

**Files:**
- Modify: `mcp_server_kicad/pcb.py` (add tool after `set_net_class`)
- Test: `tests/test_pcb_write_tools.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_pcb_write_tools.py`, add:

```python
class TestRemoveDanglingTracks:
    def test_removes_dangling_segment(self, scratch_pcb):
        """Add a trace that connects to nothing on one end."""
        board = Board.from_file(str(scratch_pcb))
        seg = Segment()
        seg.start = Position(X=200, Y=200)  # connects to nothing
        seg.end = Position(X=210, Y=200)    # connects to nothing
        seg.width = 0.25
        seg.layer = "F.Cu"
        seg.net = 1
        seg.tstamp = str(uuid.uuid4())
        board.traceItems.append(seg)
        board.to_file()

        result = pcb.remove_dangling_tracks(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["tracks_removed"] >= 1

    def test_preserves_connected_traces(self, scratch_pcb):
        """The scratch board trace connects to R1 pads — should not be removed."""
        result = pcb.remove_dangling_tracks(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["tracks_removed"] == 0

        board = Board.from_file(str(scratch_pcb))
        segs = [t for t in board.traceItems if isinstance(t, Segment)]
        assert len(segs) == 1  # original trace preserved

    def test_empty_board(self, scratch_pcb):
        """Board with no traces at all."""
        board = Board.from_file(str(scratch_pcb))
        board.traceItems = []
        board.to_file()

        result = pcb.remove_dangling_tracks(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["tracks_removed"] == 0
        assert data["iterations"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pcb_write_tools.py::TestRemoveDanglingTracks -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write minimal implementation**

In `mcp_server_kicad/pcb.py`, add after `set_net_class`:

```python
@mcp.tool(annotations=_DESTRUCTIVE)
def remove_dangling_tracks(
    pcb_path: str = PCB_PATH,
) -> str:
    """Detect and remove trace segments with unconnected endpoints.

    Iteratively removes dangling segments until no more are found.

    Args:
        pcb_path: Path to .kicad_pcb file
    """
    board = _load_board(pcb_path)
    tolerance = 0.001  # mm

    total_removed = 0
    iterations = 0

    while True:
        # Build connection point set: pads + via centers + trace endpoints
        connection_points = []

        # Pad positions (in board coordinates)
        for fp in board.footprints:
            fp_x = fp.position.X
            fp_y = fp.position.Y
            theta = math.radians(fp.position.angle or 0)
            for pad in fp.pads:
                ox, oy = pad.position.X, pad.position.Y
                px = fp_x + (ox * math.cos(theta) - oy * math.sin(theta))
                py = fp_y + (ox * math.sin(theta) + oy * math.cos(theta))
                connection_points.append((round(px, 3), round(py, 3)))

        # Via positions
        for item in board.traceItems:
            if isinstance(item, Via):
                connection_points.append((round(item.position.X, 3), round(item.position.Y, 3)))

        # Trace endpoints (each endpoint of each segment)
        segments = [t for t in board.traceItems if isinstance(t, Segment)]
        for seg in segments:
            connection_points.append((round(seg.start.X, 3), round(seg.start.Y, 3)))
            connection_points.append((round(seg.end.X, 3), round(seg.end.Y, 3)))

        # For each segment, check if both endpoints have at least one OTHER connection
        dangling = []
        for seg in segments:
            start = (round(seg.start.X, 3), round(seg.start.Y, 3))
            end = (round(seg.end.X, 3), round(seg.end.Y, 3))

            # Count connections at start (excluding this segment's own endpoints)
            start_connections = sum(
                1 for pt in connection_points
                if abs(pt[0] - start[0]) < tolerance and abs(pt[1] - start[1]) < tolerance
            ) - 1  # subtract this segment's own start point

            end_connections = sum(
                1 for pt in connection_points
                if abs(pt[0] - end[0]) < tolerance and abs(pt[1] - end[1]) < tolerance
            ) - 1  # subtract this segment's own end point

            if start_connections < 1 or end_connections < 1:
                dangling.append(seg)

        if not dangling:
            break

        for seg in dangling:
            board.traceItems.remove(seg)
        total_removed += len(dangling)
        iterations += 1

    if total_removed > 0:
        board.to_file()

    return json.dumps({"tracks_removed": total_removed, "iterations": iterations})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pcb_write_tools.py::TestRemoveDanglingTracks -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_write_tools.py
git commit -m "feat: add remove_dangling_tracks MCP tool"
```

---

## Chunk 6: Test Infrastructure Updates

### Task 12: Update tool count assertions

**Files:**
- Modify: `tests/test_unified_server.py:37,50`

- [ ] **Step 1: Update tool counts**

In `tests/test_unified_server.py`:

Line 37: Change `61` to `68` (61 + 7 new tools). All 7 new tools are available without CLI:
- `add_copper_zone` — kiutils
- `fill_zones` — pcbnew subprocess (not kicad-cli)
- `set_trace_width` — kiutils
- `remove_traces` — kiutils
- `add_thermal_vias` — kiutils
- `set_net_class` — pcbnew subprocess (not kicad-cli)
- `remove_dangling_tracks` — kiutils

Line 50: Change `44` to `51` (44 + 7).

- [ ] **Step 2: Run test to verify**

Run: `pytest tests/test_unified_server.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_unified_server.py
git commit -m "test: update tool count assertions for 7 new post-autoroute tools"
```

---

### Task 13: Update annotation tests

**Files:**
- Modify: `tests/test_tool_annotations.py:119-136`

- [ ] **Step 1: Update `_ADDITIVE` parametrize list**

In `tests/test_tool_annotations.py`, update the PCB additive list (around line 119) to add:

```python
@pytest.mark.parametrize(
    "tool_name",
    [
        "place_footprint",
        "move_footprint",
        "add_trace",
        "add_via",
        "add_pcb_text",
        "add_pcb_line",
        "add_copper_zone",    # NEW
        "fill_zones",         # NEW
        "set_trace_width",    # NEW
        "add_thermal_vias",   # NEW
        "set_net_class",      # NEW
    ],
)
def test_pcb_additive(tool_name):
    assert _get_annotations(pcb, tool_name) == _ADDITIVE
```

- [ ] **Step 2: Update `_DESTRUCTIVE` parametrize list**

Update the PCB destructive list (around line 134) to add:

```python
@pytest.mark.parametrize("tool_name", [
    "remove_footprint",
    "remove_traces",            # NEW
    "remove_dangling_tracks",   # NEW
])
def test_pcb_destructive(tool_name):
    assert _get_annotations(pcb, tool_name) == _DESTRUCTIVE
```

- [ ] **Step 3: Run tests to verify**

Run: `pytest tests/test_tool_annotations.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_tool_annotations.py
git commit -m "test: add annotation tests for 7 new post-autoroute tools"
```

---

### Task 14: Run full test suite

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Run linting**

Run: `ruff check mcp_server_kicad/ tests/ && ruff format --check mcp_server_kicad/ tests/ && pyright mcp_server_kicad/`
Expected: No errors

- [ ] **Step 3: Fix any issues, commit**

If any tests fail or lint errors exist, fix them and commit.

---

## Chunk 7: Skill Updates

### Task 15: Update `pcb-layout` skill

**Files:**
- Modify: `skills/pcb-layout/SKILL.md`

- [ ] **Step 1: Add post-routing refinement tool group**

After the "Routing" group (after line 51 with `autoroute_pcb`), add:

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

- [ ] **Step 2: Update Layout Process**

Replace lines 88-97 with the updated 9-step process from the spec (see spec lines 369-386).

- [ ] **Step 3: Add Post-Autoroute Refinement section**

Insert the full "Post-Autoroute Refinement" section from the spec (see spec lines 390-439) after "Via Usage" (after line 182) and before "Layer Stack" (line 183).

- [ ] **Step 4: Commit**

```bash
git add skills/pcb-layout/SKILL.md
git commit -m "docs: add post-autoroute tools and workflow to pcb-layout skill"
```

---

### Task 16: Update `verification` skill

**Files:**
- Modify: `skills/verification/SKILL.md`

- [ ] **Step 1: Update PCB fixes tool list**

Replace lines 64-68 (current PCB fixes) with the expanded list from the spec (see spec lines 448-457).

- [ ] **Step 2: Add Category 5 post-autoroute quality checks**

After Category 4 (copper zone issues, around line 210), add the Category 5 section from the spec (see spec lines 463-475).

- [ ] **Step 3: Add pre-manufacturing checklist items**

After line 248 (`Design rules match manufacturer capabilities`), add the 4 new checklist items from the spec (see spec lines 482-486).

- [ ] **Step 4: Commit**

```bash
git add skills/verification/SKILL.md
git commit -m "docs: add post-autoroute checks to verification skill"
```

---

### Task 17: Final verification

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Run full lint**

Run: `ruff check mcp_server_kicad/ tests/ && ruff format --check mcp_server_kicad/ tests/ && pyright mcp_server_kicad/`
Expected: No errors

- [ ] **Step 3: Review diff**

Run: `git diff main --stat`
Verify: Only expected files modified, no stray changes.
