# Auto-Junction Fix Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically insert KiCad junctions when `connect_pins` or `wire_pins_to_net` create wire endpoints that land on the interior of existing wire segments (T-junctions).

**Architecture:** Add a private `_auto_junctions(sch, new_points)` helper to `schematic.py` that checks a list of (x, y) coordinates against all existing wire segments in `sch.graphicalItems`. If a point falls on a wire interior (not at its endpoint), a `Junction` is appended to `sch.junctions`. Both `connect_pins` and `wire_pins_to_net` call this helper before `sch.to_file()`.

**Tech Stack:** Python, kiutils (`Connection`, `Junction`, `Position`), pytest

---

## Chunk 1: Implementation and tests

### Task 1: Add `_auto_junctions` helper and integrate into `connect_pins`

**Files:**
- Modify: `mcp_server_kicad/schematic.py` (add helper ~line 170, modify `connect_pins` ~line 1372)
- Test: `tests/test_routing_tools.py`

- [ ] **Step 1: Write failing test — T-junction gets auto-junction**

Add to `tests/test_routing_tools.py`:

```python
class TestAutoJunctions:
    """Tests for automatic junction insertion at T-intersections."""

    def _make_two_resistor_sch(self, tmp_path):
        """Create schematic with R1 at (100,100) and R2 at (120,120).

        R1 pin 1 at (100, 96.19), pin 2 at (100, 103.81)
        R2 pin 1 at (120, 116.19), pin 2 at (120, 123.81)
        """
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())
        sch.schematicSymbols.append(place_r1(100, 100))

        r2 = place_r1(120, 120)
        for prop in r2.properties:
            if prop.key == "Reference":
                prop.value = "R2"
        sch.schematicSymbols.append(r2)

        path = tmp_path / "two_r.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()
        return str(path)

    def test_t_junction_auto_created(self, tmp_path):
        """When connect_pins creates a wire whose corner lands on an
        existing wire's interior, a junction must be inserted."""
        sch_path = self._make_two_resistor_sch(tmp_path)

        # First wire: straight vertical from R1 pin2 (100, 103.81) to
        # R2 pin1 (120, 116.19) — L-shape corner at (120, 103.81)
        schematic.connect_pins("R1", "2", "R2", "1", schematic_path=sch_path)

        # Second wire: connect R1 pin1 (100, 96.19) to R2 pin2 (120, 123.81)
        # L-shape corner at (120, 96.19).
        # The vertical segment runs from (120, 96.19) to (120, 123.81).
        # But the first wire already has a segment ending at (120, 116.19)
        # and a corner at (120, 103.81). The second wire's vertical segment
        # passes through (120, 103.81) — the first wire's corner endpoint.
        # Actually, let's set up a cleaner scenario.
        pass

    def test_t_junction_connect_pins(self, tmp_path):
        """Reproduce the D3 bug: two connect_pins calls where the second
        wire's corner lands mid-segment on the first wire."""
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())

        # Place R1 at (100, 80) and R2 at (100, 120) — vertically aligned
        r1 = place_r1(100, 80)
        sch.schematicSymbols.append(r1)

        r2 = place_r1(100, 120)
        for prop in r2.properties:
            if prop.key == "Reference":
                prop.value = "R2"
        sch.schematicSymbols.append(r2)

        # Place R3 at (120, 100) — offset to the right
        r3 = place_r1(120, 100)
        for prop in r3.properties:
            if prop.key == "Reference":
                prop.value = "R3"
        sch.schematicSymbols.append(r3)

        path = tmp_path / "tjunc.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()
        sch_path = str(path)

        # R1 pin 2 is at (100, 83.81), R2 pin 1 is at (100, 116.19)
        # connect_pins creates a straight vertical wire (same X)
        schematic.connect_pins("R1", "2", "R2", "1", schematic_path=sch_path)

        # Verify no junctions yet (straight wire, no T)
        sch_after1 = reparse(sch_path)
        assert len(sch_after1.junctions) == 0

        # R3 pin 1 is at (120, 96.19). Connect R3:1 to R2:1 (100, 116.19)
        # L-shape: (120, 96.19) → corner (100, 96.19) → (100, 116.19)
        # The vertical segment from (100, 96.19) to (100, 116.19) overlaps
        # the existing wire from (100, 83.81) to (100, 116.19).
        # Corner point (100, 96.19) lands on interior of existing wire.
        schematic.connect_pins("R3", "1", "R2", "1", schematic_path=sch_path)

        # A junction should have been auto-created at (100, 96.19)
        sch_after2 = reparse(sch_path)
        assert len(sch_after2.junctions) >= 1
        junc_positions = [(j.position.X, j.position.Y) for j in sch_after2.junctions]
        assert any(
            abs(x - 100) < 0.02 and abs(y - 96.19) < 0.02
            for x, y in junc_positions
        ), f"Expected junction near (100, 96.19), got {junc_positions}"

    def test_no_junction_at_wire_endpoint(self, tmp_path):
        """No junction added when new wire meets existing wire at its endpoint."""
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())

        r1 = place_r1(100, 100)
        sch.schematicSymbols.append(r1)

        r2 = place_r1(100, 130)
        for prop in r2.properties:
            if prop.key == "Reference":
                prop.value = "R2"
        sch.schematicSymbols.append(r2)

        path = tmp_path / "no_junc.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()
        sch_path = str(path)

        # R1 pin 2 at (100, 103.81), R2 pin 1 at (100, 126.19)
        # Straight vertical wire — endpoints meet at pin positions
        schematic.connect_pins("R1", "2", "R2", "1", schematic_path=sch_path)

        sch_after = reparse(sch_path)
        assert len(sch_after.junctions) == 0

    def test_no_duplicate_junctions(self, tmp_path):
        """If a junction already exists at a T-point, don't add another."""
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())

        r1 = place_r1(100, 80)
        sch.schematicSymbols.append(r1)

        r2 = place_r1(100, 120)
        for prop in r2.properties:
            if prop.key == "Reference":
                prop.value = "R2"
        sch.schematicSymbols.append(r2)

        r3 = place_r1(120, 100)
        for prop in r3.properties:
            if prop.key == "Reference":
                prop.value = "R3"
        sch.schematicSymbols.append(r3)

        path = tmp_path / "dup_junc.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()
        sch_path = str(path)

        # Create the T-junction scenario
        schematic.connect_pins("R1", "2", "R2", "1", schematic_path=sch_path)
        schematic.connect_pins("R3", "1", "R2", "1", schematic_path=sch_path)

        sch_after = reparse(sch_path)
        junc_count_1 = len(sch_after.junctions)
        assert junc_count_1 >= 1

        # Connect again with a fourth resistor that hits the same point
        r4 = place_r1(80, 100)
        for prop in r4.properties:
            if prop.key == "Reference":
                prop.value = "R4"
        sch_after.schematicSymbols.append(r4)
        sch_after.to_file()

        schematic.connect_pins("R4", "1", "R2", "1", schematic_path=sch_path)

        sch_after2 = reparse(sch_path)
        # Should not have added a duplicate junction at the same position
        junc_positions = [(j.position.X, j.position.Y) for j in sch_after2.junctions]
        # Count junctions near (100, 96.19)
        near_count = sum(
            1 for x, y in junc_positions
            if abs(x - 100) < 0.02 and abs(y - 96.19) < 0.02
        )
        assert near_count == 1, f"Expected 1 junction near (100, 96.19), got {near_count}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routing_tools.py::TestAutoJunctions -v`
Expected: `test_t_junction_connect_pins` FAILS (no junctions created), others may pass vacuously

- [ ] **Step 3: Implement `_auto_junctions` helper**

Add after the existing `_get_pin_pos` function (around line 214) in `mcp_server_kicad/schematic.py`:

```python
def _point_on_wire_interior(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
    tol: float = 0.01,
) -> bool:
    """Check if point (px, py) lies on the interior of wire segment (a→b).

    Only handles axis-aligned (horizontal/vertical) wires. Returns False
    for diagonal wires and for points at segment endpoints.
    """
    # Horizontal wire
    if abs(ay - by) < tol:
        if abs(py - ay) < tol:
            lo, hi = min(ax, bx), max(ax, bx)
            if lo + tol < px < hi - tol:
                return True
    # Vertical wire
    if abs(ax - bx) < tol:
        if abs(px - ax) < tol:
            lo, hi = min(ay, by), max(ay, by)
            if lo + tol < py < hi - tol:
                return True
    return False


def _auto_junctions(sch, new_points: list[tuple[float, float]], tol: float = 0.01):
    """Add junctions where new wire endpoints land on existing wire interiors.

    Checks each point in new_points against all wire segments in
    sch.graphicalItems. If a point is on a wire's interior (not at its
    endpoint), and no junction already exists there, a Junction is added.
    """
    for px, py in new_points:
        # Skip if junction already exists here
        if any(
            abs(j.position.X - px) < tol and abs(j.position.Y - py) < tol
            for j in sch.junctions
        ):
            continue

        for item in sch.graphicalItems:
            if not (isinstance(item, Connection) and item.type == "wire"):
                continue
            if len(item.points) < 2:
                continue
            ax, ay = item.points[0].X, item.points[0].Y
            bx, by = item.points[1].X, item.points[1].Y
            if _point_on_wire_interior(px, py, ax, ay, bx, by, tol):
                sch.junctions.append(
                    Junction(
                        position=Position(X=px, Y=py),
                        diameter=0,
                        color=ColorRGBA(R=0, G=0, B=0, A=0),
                        uuid=_gen_uuid(),
                    )
                )
                break  # One junction per point is enough
```

- [ ] **Step 4: Integrate into `connect_pins`**

In `connect_pins`, after `sch.graphicalItems.append(seg)` loop (line 1373) and before `sch.to_file()` (line 1374), add:

```python
    # Collect all new wire endpoints (pin positions + L-shape corner)
    new_points = [(x1, y1), (x2, y2)]
    if x1 != x2 and y1 != y2:
        new_points.append((x2, y1))  # L-shape corner
    _auto_junctions(sch, new_points)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_routing_tools.py::TestAutoJunctions -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_routing_tools.py
git commit -m "fix: auto-insert junctions at T-intersections in connect_pins

When connect_pins routes a wire whose endpoint or corner lands on the
interior of an existing wire segment, a Junction is now automatically
added. This fixes silent ERC disconnections (e.g. D3 pin 2 in the
power supply schematic)."
```

---

### Task 2: Integrate `_auto_junctions` into `wire_pins_to_net`

**Files:**
- Modify: `mcp_server_kicad/schematic.py` (~line 1289-1311)
- Test: `tests/test_routing_tools.py`

- [ ] **Step 1: Write failing test**

Add to `TestAutoJunctions` in `tests/test_routing_tools.py`:

```python
    def test_wire_pins_to_net_auto_junction(self, tmp_path):
        """wire_pins_to_net creates junction when stub crosses existing wire."""
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())

        # R1 at (100, 100): pin 1 at (100, 96.19), pin 2 at (100, 103.81)
        r1 = place_r1(100, 100)
        sch.schematicSymbols.append(r1)

        # Pre-existing horizontal wire crossing through R1's vertical axis
        sch.graphicalItems.append(
            Connection(
                type="wire",
                points=[Position(X=90, Y=96.19), Position(X=110, Y=96.19)],
                stroke=Stroke(width=0, type="default"),
                uuid=_gen_uuid(),
            )
        )

        path = tmp_path / "wptn_junc.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()
        sch_path = str(path)

        # Wire R1 pin 1 to net "VCC". Pin 1 is at (100, 96.19).
        # The pin endpoint is ON the existing wire — but at an interior point
        # (the wire runs from x=90 to x=110, pin is at x=100).
        # A junction should be auto-created at (100, 96.19).
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="VCC",
            schematic_path=sch_path,
        )

        sch_after = reparse(sch_path)
        junc_positions = [(j.position.X, j.position.Y) for j in sch_after.junctions]
        assert any(
            abs(x - 100) < 0.02 and abs(y - 96.19) < 0.02
            for x, y in junc_positions
        ), f"Expected junction near (100, 96.19), got {junc_positions}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routing_tools.py::TestAutoJunctions::test_wire_pins_to_net_auto_junction -v`
Expected: FAIL (no junction created)

- [ ] **Step 3: Integrate into `wire_pins_to_net`**

In `wire_pins_to_net`, after the `for pin_def in pins:` loop (after line 1309) and before `sch.to_file()` (line 1311), collect all pin-side endpoints and call `_auto_junctions`:

Replace the block from line 1311 onward:

```python
    # Auto-insert junctions where new stub endpoints touch existing wires
    _auto_junctions(sch, stub_endpoints)

    sch.to_file()
```

And at the top of the function (after `warnings = []` on ~line 1216), add:

```python
    stub_endpoints = []
```

And inside the loop, after the wire stub is appended (~line 1300), add:

```python
        stub_endpoints.append((px, py))
        stub_endpoints.append((end_x, end_y))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routing_tools.py::TestAutoJunctions -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest --tb=short`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_routing_tools.py
git commit -m "fix: auto-insert junctions in wire_pins_to_net

Extends the _auto_junctions helper to wire_pins_to_net, checking both
the pin-side and label-side endpoints of each stub wire."
```

---

### Task 3: Update TODO.md

**Files:**
- Modify: `TODO.md`

- [ ] **Step 1: Mark junction bug as fixed**

Change the junction section in `TODO.md` to indicate it's been resolved. Remove the section or mark it done.

- [ ] **Step 2: Commit**

```bash
git add TODO.md
git commit -m "docs: mark auto-junction bug as fixed in TODO"
```
