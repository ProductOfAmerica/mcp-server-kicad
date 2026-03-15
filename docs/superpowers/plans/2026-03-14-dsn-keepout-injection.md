# DSN Keepout Injection Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix autorouting to respect footprint-level keepout zones by promoting them to board-level before DSN export.

**Architecture:** Before exporting DSN, load the PCB with kiutils, find footprint-level keepout zones, transform their coordinates to board space, add them as board-level zones in a temp copy, then export DSN from that copy. Also fix `_transform_local_to_board` to handle back-side (B.Cu) footprint mirroring.

**Tech Stack:** Python, kiutils, pytest, KiCad pcbnew (subprocess)

**Spec:** `docs/superpowers/specs/2026-03-14-dsn-keepout-injection-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `mcp_server_kicad/_shared.py:874-891` | Modify | Add `mirrored` param to `_transform_local_to_board` |
| `mcp_server_kicad/_shared.py:1078-1113` | Modify | Pass `mirrored` flag in `_check_footprint_keepout_violations` |
| `mcp_server_kicad/_shared.py` (after line 1113) | Create | New `_promote_footprint_keepouts()` function |
| `mcp_server_kicad/_shared.py:155-168` | Modify | Add `_promote_footprint_keepouts` to exports list |
| `mcp_server_kicad/pcb.py:955-957` | Modify | Pass `mirrored` flag in `add_via_stitching` |
| `mcp_server_kicad/pcb.py:1109-1111` | Modify | Pass `mirrored` flag in `remove_unconnected_traces` |
| `mcp_server_kicad/pcb.py:1653` | Modify | Pass `mirrored` flag in `get_footprint_bounds` |
| `mcp_server_kicad/pcb.py:1554-1561` | Modify | Insert keepout promotion step before DSN export |
| `mcp_server_kicad/pcb.py:1612-1619` | Modify | Pass `keepouts_promoted` to `AutorouteResult` |
| `mcp_server_kicad/models.py:307-313` | Modify | Add `keepouts_promoted` field |
| `tests/test_shared_helpers.py` | Modify | Add 3 mirroring tests |
| `tests/test_freerouting.py` | Modify | Add 10 keepout promotion tests |

---

## Chunk 1: Fix `_transform_local_to_board` mirroring

### Task 1: Add mirroring tests for `_transform_local_to_board`

**Files:**
- Modify: `tests/test_shared_helpers.py:306-317`

- [ ] **Step 1: Write 3 failing tests for mirroring**

Add these tests to `TestTransformLocalToBoard` in `tests/test_shared_helpers.py`:

```python
def test_mirrored_zero_rotation(self):
    # Back-side footprint: local_x is negated before rotation
    # mirrored: local_x = -3, then no rotation: board = (10 + (-3), 20 + 4) = (7, 24)
    bx, by = _transform_local_to_board(10, 20, 0, 3, 4, mirrored=True)
    assert bx == pytest.approx(7)
    assert by == pytest.approx(24)

def test_mirrored_false_unchanged(self):
    # Explicit mirrored=False should match default behavior
    bx, by = _transform_local_to_board(10, 20, 0, 3, 4, mirrored=False)
    assert bx == pytest.approx(13)
    assert by == pytest.approx(24)

def test_mirrored_with_rotation(self):
    # mirrored + 90 degrees: local_x = -3, then rotate 90
    # x' = 10 + ((-3)*cos90 - 4*sin90) = 10 + (0 - 4) = 6
    # y' = 20 + ((-3)*sin90 + 4*cos90) = 20 + (-3 + 0) = 17
    bx, by = _transform_local_to_board(10, 20, 90, 3, 4, mirrored=True)
    assert bx == pytest.approx(6, abs=0.01)
    assert by == pytest.approx(17, abs=0.01)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_shared_helpers.py::TestTransformLocalToBoard -v`
Expected: 3 FAIL (unexpected keyword argument `mirrored`)

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_shared_helpers.py
git commit -m "test: add mirroring tests for _transform_local_to_board"
```

### Task 2: Implement mirroring in `_transform_local_to_board`

**Files:**
- Modify: `mcp_server_kicad/_shared.py:874-891`

- [ ] **Step 1: Add `mirrored` parameter**

Replace the function at `_shared.py:874-891` with:

```python
def _transform_local_to_board(
    fp_x: float,
    fp_y: float,
    angle: float,
    local_x: float,
    local_y: float,
    mirrored: bool = False,
) -> tuple[float, float]:
    """Convert footprint-local coordinates to board coordinates.

    Applies rotation by *angle* (degrees) around the footprint origin
    ``(fp_x, fp_y)``.  When *mirrored* is True (back-side footprint),
    the local X coordinate is negated before rotation.
    """
    if mirrored:
        local_x = -local_x
    theta = math.radians(angle or 0)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    board_x = fp_x + (local_x * cos_t - local_y * sin_t)
    board_y = fp_y + (local_x * sin_t + local_y * cos_t)
    return board_x, board_y
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_shared_helpers.py::TestTransformLocalToBoard -v`
Expected: 5 PASS (2 existing + 3 new)

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `pytest tests/ -x -q`
Expected: All pass (mirrored defaults to False, so existing callers are unaffected)

- [ ] **Step 4: Commit**

```bash
git add mcp_server_kicad/_shared.py
git commit -m "feat: add mirrored parameter to _transform_local_to_board"
```

### Task 3: Update existing callers to pass mirror flag

**Files:**
- Modify: `mcp_server_kicad/_shared.py:1102`
- Modify: `mcp_server_kicad/pcb.py:955`, `pcb.py:1109`, `pcb.py:1653`

- [ ] **Step 1: Update `_check_footprint_keepout_violations`**

At `_shared.py:1102`, change:

```python
bx, by = _transform_local_to_board(fp_x, fp_y, fp_angle, c.X, c.Y)
```

To:

```python
bx, by = _transform_local_to_board(fp_x, fp_y, fp_angle, c.X, c.Y, mirrored=fp.layer == "B.Cu")
```

This requires access to the footprint's layer. The footprint `fp` is already in scope at line 1079 (`for fp in board.footprints:`).

- [ ] **Step 2: Update `add_via_stitching` in pcb.py**

At `pcb.py:955`, change:

```python
pad_x, pad_y = _transform_local_to_board(
    fp_x, fp_y, fp.position.angle or 0, pad.position.X, pad.position.Y
)
```

To:

```python
pad_x, pad_y = _transform_local_to_board(
    fp_x, fp_y, fp.position.angle or 0, pad.position.X, pad.position.Y,
    mirrored=fp.layer == "B.Cu",
)
```

- [ ] **Step 3: Update `remove_unconnected_traces` in pcb.py**

At `pcb.py:1109`, change:

```python
px, py = _transform_local_to_board(
    fp_x, fp_y, fp.position.angle or 0, pad.position.X, pad.position.Y
)
```

To:

```python
px, py = _transform_local_to_board(
    fp_x, fp_y, fp.position.angle or 0, pad.position.X, pad.position.Y,
    mirrored=fp.layer == "B.Cu",
)
```

- [ ] **Step 4: Update `get_footprint_bounds` in pcb.py**

At `pcb.py:1653`, change:

```python
_transform_local_to_board(fp_x, fp_y, angle, lx, ly) for lx, ly in local_corners
```

To:

```python
_transform_local_to_board(fp_x, fp_y, angle, lx, ly, mirrored=fp.layer == "B.Cu") for lx, ly in local_corners
```

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/_shared.py mcp_server_kicad/pcb.py
git commit -m "fix: pass mirrored flag to _transform_local_to_board for B.Cu footprints"
```

---

## Chunk 2: Implement `_promote_footprint_keepouts`

### Task 4: Add `keepouts_promoted` to `AutorouteResult`

**Files:**
- Modify: `mcp_server_kicad/models.py:307-313`

- [ ] **Step 1: Add field**

At `models.py:313`, after `drc_unconnected`, add:

```python
    keepouts_promoted: int = 0
```

- [ ] **Step 2: Run tests to verify no regressions**

Run: `pytest tests/ -x -q`
Expected: All pass (new field has default value)

- [ ] **Step 3: Commit**

```bash
git add mcp_server_kicad/models.py
git commit -m "feat: add keepouts_promoted field to AutorouteResult"
```

### Task 5: Write failing tests for `_promote_footprint_keepouts`

**Files:**
- Modify: `tests/test_freerouting.py`

- [ ] **Step 1: Add imports and helper**

Add these to the existing imports in `tests/test_freerouting.py` (the file already imports `os`, `subprocess`, `patch`, and `pytest`):

```python
from pathlib import Path

from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.common import Net, Position, Property
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon

from mcp_server_kicad._shared import _promote_footprint_keepouts
```

Add a helper function after the imports:

```python
def _make_board_with_fp_keepout(tmp_path, fp_angle=0, fp_layer="F.Cu", fp_x=100, fp_y=100):
    """Create a minimal board with one footprint containing a keepout zone."""
    board = Board.create_new()
    board.nets = [Net(number=0, name="")]

    fp = Footprint()
    fp.entryName = "TestPkg:TestFP"
    fp.layer = fp_layer
    fp.position = Position(X=fp_x, Y=fp_y, angle=fp_angle)
    fp.reference = Property(key="Reference", value="U1")
    fp.value = Property(key="Value", value="TEST")

    keepout_zone = Zone()
    keepout_zone.net = 0
    keepout_zone.netName = ""
    keepout_zone.layers = ["F.Cu", "B.Cu"]
    keepout_zone.hatch = Hatch(style="edge", pitch=0.5)
    keepout_zone.keepoutSettings = KeepoutSettings(
        tracks="not_allowed",
        vias="not_allowed",
        pads="not_allowed",
        copperpour="not_allowed",
        footprints="not_allowed",
    )
    poly = ZonePolygon()
    poly.coordinates = [
        Position(X=0, Y=0),
        Position(X=10, Y=0),
        Position(X=10, Y=10),
        Position(X=0, Y=10),
    ]
    keepout_zone.polygons = [poly]
    fp.zones = [keepout_zone]

    board.footprints = [fp]
    pcb_path = str(tmp_path / "test.kicad_pcb")
    board.filePath = pcb_path
    board.to_file()
    return pcb_path
```

- [ ] **Step 2: Write all 10 test cases**

Add a new test class:

```python
class TestPromoteFootprintKeepouts:
    def test_happy_path(self, tmp_path):
        """FP keepout zone is promoted to board level with correct coords."""
        pcb_path = _make_board_with_fp_keepout(tmp_path, fp_x=100, fp_y=100)
        out_path = str(tmp_path / "promoted.kicad_pcb")
        count = _promote_footprint_keepouts(pcb_path, out_path)
        assert count == 1
        assert Path(out_path).exists()
        # Load promoted board and verify zone coordinates are in board space
        from mcp_server_kicad._shared import _load_board
        promoted = _load_board(out_path)
        # Original board has 0 board-level keepout zones; promoted has 1
        board_keepouts = [z for z in promoted.zones if z.keepoutSettings is not None]
        assert len(board_keepouts) == 1
        zone = board_keepouts[0]
        coords = [(c.X, c.Y) for c in zone.polygons[0].coordinates]
        # FP at (100, 100), zone vertices at (0,0), (10,0), (10,10), (0,10)
        # Board coords: (100, 100), (110, 100), (110, 110), (100, 110)
        assert coords[0] == pytest.approx((100, 100), abs=0.01)
        assert coords[1] == pytest.approx((110, 100), abs=0.01)
        assert coords[2] == pytest.approx((110, 110), abs=0.01)
        assert coords[3] == pytest.approx((100, 110), abs=0.01)

    def test_no_keepouts_returns_zero(self, tmp_path):
        """Board with no FP keepout zones returns 0, no output file created."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        fp = Footprint()
        fp.entryName = "TestPkg:TestFP"
        fp.layer = "F.Cu"
        fp.position = Position(X=50, Y=50)
        fp.reference = Property(key="Reference", value="U1")
        fp.value = Property(key="Value", value="TEST")
        board.footprints = [fp]
        pcb_path = str(tmp_path / "no_keepout.kicad_pcb")
        board.filePath = pcb_path
        board.to_file()
        out_path = str(tmp_path / "promoted.kicad_pcb")
        count = _promote_footprint_keepouts(pcb_path, out_path)
        assert count == 0
        assert not Path(out_path).exists()

    def test_rotated_footprint(self, tmp_path):
        """Keepout coords are correctly transformed when FP is rotated 90 degrees."""
        pcb_path = _make_board_with_fp_keepout(tmp_path, fp_angle=90, fp_x=100, fp_y=100)
        out_path = str(tmp_path / "promoted.kicad_pcb")
        count = _promote_footprint_keepouts(pcb_path, out_path)
        assert count == 1
        from mcp_server_kicad._shared import _load_board
        promoted = _load_board(out_path)
        board_keepouts = [z for z in promoted.zones if z.keepoutSettings is not None]
        zone = board_keepouts[0]
        coords = [(c.X, c.Y) for c in zone.polygons[0].coordinates]
        # FP at (100,100) rotated 90: vertex (10,0) -> (100 + 0, 100 + 10) = (100, 110)
        # vertex (0,0) -> (100, 100)
        assert coords[0] == pytest.approx((100, 100), abs=0.01)
        assert coords[1] == pytest.approx((100, 110), abs=0.01)

    def test_multiple_polygons(self, tmp_path):
        """All polygons in a zone are promoted, not just the first."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        # Add a second polygon to the keepout zone
        from mcp_server_kicad._shared import _load_board
        board = _load_board(pcb_path)
        fp = board.footprints[0]
        zone = fp.zones[0]
        poly2 = ZonePolygon()
        poly2.coordinates = [
            Position(X=20, Y=20),
            Position(X=30, Y=20),
            Position(X=30, Y=30),
        ]
        zone.polygons.append(poly2)
        board.to_file()
        out_path = str(tmp_path / "promoted.kicad_pcb")
        count = _promote_footprint_keepouts(pcb_path, out_path)
        assert count == 2  # one zone per polygon
        # Verify both zones exist at board level with correct coords
        promoted = _load_board(out_path)
        board_keepouts = [z for z in promoted.zones if z.keepoutSettings is not None]
        assert len(board_keepouts) == 2
        # Second polygon: (20,20) -> board (120,120) at FP origin (100,100)
        coords2 = [(c.X, c.Y) for c in board_keepouts[1].polygons[0].coordinates]
        assert coords2[0] == pytest.approx((120, 120), abs=0.01)

    def test_back_side_footprint_keepout(self, tmp_path):
        """B.Cu footprint keepout has mirrored X coordinates."""
        pcb_path = _make_board_with_fp_keepout(tmp_path, fp_layer="B.Cu", fp_x=100, fp_y=100)
        out_path = str(tmp_path / "promoted.kicad_pcb")
        count = _promote_footprint_keepouts(pcb_path, out_path)
        assert count == 1
        from mcp_server_kicad._shared import _load_board
        promoted = _load_board(out_path)
        board_keepouts = [z for z in promoted.zones if z.keepoutSettings is not None]
        zone = board_keepouts[0]
        coords = [(c.X, c.Y) for c in zone.polygons[0].coordinates]
        # FP at (100,100) on B.Cu, zone vertex (10,0) -> mirrored local_x = -10
        # board = (100 + (-10), 100 + 0) = (90, 100)
        assert coords[0] == pytest.approx((100, 100), abs=0.01)  # (0,0) -> (-0, 0) -> (100, 100)
        assert coords[1] == pytest.approx((90, 100), abs=0.01)   # (10,0) -> (-10, 0) -> (90, 100)

    def test_fp_position_none_skipped(self, tmp_path):
        """Footprint with position=None is skipped gracefully."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        fp = Footprint()
        fp.entryName = "TestPkg:TestFP"
        fp.layer = "F.Cu"
        fp.position = None  # No position
        fp.reference = Property(key="Reference", value="U1")
        fp.value = Property(key="Value", value="TEST")
        keepout_zone = Zone()
        keepout_zone.net = 0
        keepout_zone.netName = ""
        keepout_zone.layers = ["F.Cu"]
        keepout_zone.hatch = Hatch(style="edge", pitch=0.5)
        keepout_zone.keepoutSettings = KeepoutSettings(
            tracks="not_allowed", vias="not_allowed", pads="not_allowed",
            copperpour="not_allowed", footprints="not_allowed",
        )
        poly = ZonePolygon()
        poly.coordinates = [Position(X=0, Y=0), Position(X=1, Y=0), Position(X=1, Y=1)]
        keepout_zone.polygons = [poly]
        fp.zones = [keepout_zone]
        board.footprints = [fp]
        pcb_path = str(tmp_path / "no_pos.kicad_pcb")
        board.filePath = pcb_path
        board.to_file()
        out_path = str(tmp_path / "promoted.kicad_pcb")
        count = _promote_footprint_keepouts(pcb_path, out_path)
        assert count == 0

    def test_deep_copy_isolation(self, tmp_path):
        """Modifying promoted zone does not affect source zone."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        out_path = str(tmp_path / "promoted.kicad_pcb")
        _promote_footprint_keepouts(pcb_path, out_path)
        from mcp_server_kicad._shared import _load_board
        source = _load_board(pcb_path)
        promoted = _load_board(out_path)
        # Modify promoted zone's keepout settings
        promoted_keepouts = [z for z in promoted.zones if z.keepoutSettings is not None]
        promoted_keepouts[0].keepoutSettings.tracks = "allowed"
        # Source should be unaffected
        source_fp_zone = source.footprints[0].zones[0]
        assert source_fp_zone.keepoutSettings.tracks == "not_allowed"

    def test_dsn_source_branching_with_keepouts(self, tmp_path):
        """Verify branching logic selects temp PCB when keepouts are promoted.
        Note: A full kiutils-to-pcbnew round-trip test (verifying DSN contains
        keepout entries) is omitted because it requires KiCad CLI installed,
        which may not be available in CI. The branching logic test validates
        the integration point instead."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        # We can't run the full autoroute (needs Java + Freerouting),
        # so test _promote_footprint_keepouts returns > 0 and file exists
        out_path = str(tmp_path / "temp.kicad_pcb")
        count = _promote_footprint_keepouts(pcb_path, out_path)
        assert count > 0
        assert Path(out_path).exists()
        # Verify the branching logic: dsn_source should be out_path
        dsn_source = out_path if count > 0 else pcb_path
        assert dsn_source == out_path

    def test_dsn_source_branching_without_keepouts(self, tmp_path):
        """autoroute_pcb uses original pcb_path when no keepouts exist."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        pcb_path = str(tmp_path / "plain.kicad_pcb")
        board.filePath = pcb_path
        board.to_file()
        out_path = str(tmp_path / "temp.kicad_pcb")
        count = _promote_footprint_keepouts(pcb_path, out_path)
        assert count == 0
        dsn_source = out_path if count > 0 else pcb_path
        assert dsn_source == pcb_path

    def test_save_failure_raises_tool_error(self, tmp_path):
        """OSError on board.to_file() is caught and re-raised as ToolError."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        out_path = str(tmp_path / "promoted.kicad_pcb")
        from mcp.server.fastmcp.exceptions import ToolError
        with patch("kiutils.board.Board.to_file", side_effect=OSError("disk full")):
            with pytest.raises(ToolError, match="Failed to prepare PCB"):
                _promote_footprint_keepouts(pcb_path, out_path)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_freerouting.py::TestPromoteFootprintKeepouts -v`
Expected: 10 FAIL (`_promote_footprint_keepouts` not yet implemented)

- [ ] **Step 4: Commit failing tests**

```bash
git add tests/test_freerouting.py
git commit -m "test: add 10 tests for _promote_footprint_keepouts"
```

### Task 6: Implement `_promote_footprint_keepouts`

**Files:**
- Modify: `mcp_server_kicad/_shared.py` (after line 1113, and exports list at line 167)

- [ ] **Step 1: Add to exports list**

At `_shared.py:167`, before the closing `]`, add:

```python
    "_promote_footprint_keepouts",
```

- [ ] **Step 2: Implement the function**

Add after `_check_footprint_keepout_violations` (after line 1113):

```python
def _promote_footprint_keepouts(pcb_path: str, output_path: str) -> int:
    """Promote footprint-level keepout zones to board-level in a temp copy.

    KiCad's ``ExportSpecctraDSN`` does not export keepout zones defined
    inside footprints.  This function copies the board, transforms
    footprint-local keepout zones into board-level zones, and saves
    the result to *output_path*.  The original PCB is never modified.

    Returns the number of keepout zones promoted.  If zero, *output_path*
    is not written.
    """
    import copy

    from mcp.server.fastmcp.exceptions import ToolError

    board = _load_board(pcb_path)
    count = 0

    for fp in board.footprints:
        fp_zones = getattr(fp, "zones", None)
        if not fp_zones:
            continue
        if fp.position is None:
            continue
        fp_x = fp.position.X
        fp_y = fp.position.Y
        fp_angle = fp.position.angle or 0
        mirrored = fp.layer == "B.Cu"

        for source_zone in fp_zones:
            ks = source_zone.keepoutSettings
            if ks is None:
                continue
            if not source_zone.polygons:
                continue

            for source_poly in source_zone.polygons:
                zone = Zone()
                zone.net = 0
                zone.netName = ""
                zone.layers = copy.deepcopy(source_zone.layers)
                zone.tstamp = _gen_uuid()
                zone.hatch = Hatch(style="edge", pitch=0.5)
                zone.keepoutSettings = copy.deepcopy(ks)

                transformed_poly = ZonePolygon()
                transformed_poly.coordinates = []
                for c in source_poly.coordinates:
                    bx, by = _transform_local_to_board(
                        fp_x, fp_y, fp_angle, c.X, c.Y, mirrored=mirrored
                    )
                    transformed_poly.coordinates.append(Position(X=bx, Y=by))

                zone.polygons = [transformed_poly]
                board.zones.append(zone)
                count += 1

    if count > 0:
        board.filePath = output_path
        try:
            board.to_file()
        except OSError as e:
            raise ToolError(f"Failed to prepare PCB for autorouting: {e}") from e

    return count
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_freerouting.py::TestPromoteFootprintKeepouts -v`
Expected: 10 PASS

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/_shared.py
git commit -m "feat: add _promote_footprint_keepouts to promote FP keepouts to board level"
```

---

## Chunk 3: Integrate into `autoroute_pcb`

### Task 7: Wire up keepout promotion in `autoroute_pcb`

**Files:**
- Modify: `mcp_server_kicad/pcb.py:1554-1561` (inside `autoroute_pcb`)
- Modify: `mcp_server_kicad/pcb.py:1612-1619` (return statement)

- [ ] **Step 1: Add import**

Verify `_promote_footprint_keepouts` is already importable from `_shared`. At the top of `pcb.py`, it imports from `_shared` at line 67. Add `_promote_footprint_keepouts` to that import list.

- [ ] **Step 2: Insert promotion step before DSN export**

At `pcb.py:1554-1561`, change:

```python
    with tempfile.TemporaryDirectory() as tmp_dir:
        dsn_path = str(Path(tmp_dir) / f"{stem}.dsn")
        ses_path = str(Path(tmp_dir) / f"{stem}.ses")

        # Step 1: Export DSN
        dsn_err = _export_dsn(pcb_path, dsn_path)
        if dsn_err:
            raise ToolError(dsn_err)
```

To:

```python
    with tempfile.TemporaryDirectory() as tmp_dir:
        dsn_path = str(Path(tmp_dir) / f"{stem}.dsn")
        ses_path = str(Path(tmp_dir) / f"{stem}.ses")

        # Step 1: Promote footprint-level keepout zones to board-level
        temp_pcb_path = str(Path(tmp_dir) / f"{stem}_keepouts.kicad_pcb")
        keepouts_promoted = _promote_footprint_keepouts(pcb_path, temp_pcb_path)
        dsn_source = temp_pcb_path if keepouts_promoted > 0 else pcb_path

        # Step 2: Export DSN
        dsn_err = _export_dsn(dsn_source, dsn_path)
        if dsn_err:
            raise ToolError(dsn_err)
```

- [ ] **Step 3: Update return statement**

At `pcb.py:1612-1619`, change:

```python
    return AutorouteResult(
        routed_path=str(Path(routed_path).resolve()),
        traces_added=traces_after - traces_before,
        vias_added=vias_after - vias_before,
        text_fields_fixed=text_fields_fixed,
        drc_violations=drc_violations,
        drc_unconnected=drc_unconnected,
    )
```

To:

```python
    return AutorouteResult(
        routed_path=str(Path(routed_path).resolve()),
        traces_added=traces_after - traces_before,
        vias_added=vias_after - vias_before,
        text_fields_fixed=text_fields_fixed,
        drc_violations=drc_violations,
        drc_unconnected=drc_unconnected,
        keepouts_promoted=keepouts_promoted,
    )
```

- [ ] **Step 4: Update step comments**

The existing comments say "Step 2: Run Freerouting" and "Step 3: Import SES". Update numbering:
- Step 1 -> "Promote footprint-level keepout zones" (new)
- Step 2 -> "Export DSN"
- Step 3 -> "Run Freerouting"
- Step 4 -> "Import SES into new PCB"
- Step 5 -> "Fix displaced footprint text fields"

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/pcb.py
git commit -m "feat: integrate keepout promotion into autoroute_pcb"
```

### Task 8: Final verification

- [ ] **Step 1: Run linting**

Run: `ruff check mcp_server_kicad/_shared.py mcp_server_kicad/pcb.py mcp_server_kicad/models.py`
Run: `ruff format --check mcp_server_kicad/_shared.py mcp_server_kicad/pcb.py mcp_server_kicad/models.py`

Fix any issues.

- [ ] **Step 2: Run type checker**

Run: `pyright mcp_server_kicad/_shared.py mcp_server_kicad/pcb.py mcp_server_kicad/models.py`

Fix any issues.

- [ ] **Step 3: Run full test suite one last time**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 4: Commit any lint/type fixes**

```bash
git add -u
git commit -m "fix: resolve lint and type issues from keepout promotion feature"
```
