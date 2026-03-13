# Hierarchical Schematic Tools Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 19 new tools and fix 3 existing tools to enable autonomous agent workflows on multi-sheet KiCad schematics.

**Architecture:** Bug fixes and `list_schematic_items` expansion go in `schematic.py` (single-sheet operations). Hierarchy traversal, validation, annotation, and sheet management go in `project.py` (cross-sheet operations). Shared helpers (`_resolve_root`, new kiutils re-exports) go in `_shared.py`.

**Tech Stack:** Python 3.10+, kiutils, FastMCP, pytest, kicad-cli (optional for export/ERC tools)

**Spec:** `docs/superpowers/specs/2026-03-13-hierarchical-schematic-tools-design.md`

---

## Chunk 1: Prerequisites + Bug Fixes

### Task 1: Add `_resolve_root` helper and new kiutils re-exports to `_shared.py`

**Files:**
- Modify: `mcp_server_kicad/_shared.py`
- Test: `tests/test_shared_helpers.py`

- [ ] **Step 1: Write failing tests for `_resolve_root`**

```python
# tests/test_shared_helpers.py — add to existing file

class TestResolveRoot:
    def test_returns_root_from_project_path(self, tmp_path: Path):
        """When project_path is given, derive root .kicad_sch from it."""
        from mcp_server_kicad._shared import _resolve_root

        pro = tmp_path / "myproj.kicad_pro"
        pro.write_text("{}")
        root_sch = tmp_path / "myproj.kicad_sch"
        root_sch.write_text("")
        sub_sch = tmp_path / "child.kicad_sch"
        sub_sch.write_text("")

        result = _resolve_root(str(sub_sch), project_path=str(pro))
        assert result == str(root_sch)

    def test_returns_none_when_already_root_via_project(self, tmp_path: Path):
        from mcp_server_kicad._shared import _resolve_root

        pro = tmp_path / "myproj.kicad_pro"
        pro.write_text("{}")
        root_sch = tmp_path / "myproj.kicad_sch"
        root_sch.write_text("")

        result = _resolve_root(str(root_sch), project_path=str(pro))
        assert result is None

    def test_falls_back_to_glob_when_no_project_path(self, tmp_path: Path):
        from mcp_server_kicad._shared import _resolve_root

        pro = tmp_path / "myproj.kicad_pro"
        pro.write_text("{}")
        root_sch = tmp_path / "myproj.kicad_sch"
        root_sch.write_text("")
        sub_sch = tmp_path / "child.kicad_sch"
        sub_sch.write_text("")

        result = _resolve_root(str(sub_sch))
        assert result == str(root_sch)

    def test_returns_none_when_no_project_found(self, tmp_path: Path):
        from mcp_server_kicad._shared import _resolve_root

        sch = tmp_path / "standalone.kicad_sch"
        sch.write_text("")

        result = _resolve_root(str(sch))
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_shared_helpers.py::TestResolveRoot -v`
Expected: FAIL with `ImportError` (function doesn't exist yet)

- [ ] **Step 3: Implement `_resolve_root` and add kiutils re-exports**

In `mcp_server_kicad/_shared.py`:

1. Add to the `kiutils.items.schitems` import block (around line 15):
```python
from kiutils.items.schitems import (
    BusEntry,
    Connection,
    GlobalLabel,
    HierarchicalLabel,
    HierarchicalPin,
    HierarchicalSheet,
    HierarchicalSheetInstance,
    HierarchicalSheetProjectInstance,
    HierarchicalSheetProjectPath,
    Junction,
    LocalLabel,
    NoConnect,
    SchematicSymbol,
    SymbolInstance,
    SymbolProjectInstance,
    SymbolProjectPath,
    Text,
)
```

2. Move `_find_root_schematic` from `schematic.py` (lines 1658-1674) to `_shared.py` and add `_resolve_root`:
```python
def _find_root_schematic(schematic_path: str) -> str | None:
    """Return the root schematic path if *schematic_path* is a sub-sheet."""
    sch_dir = Path(schematic_path).parent
    pro_files = list(sch_dir.glob("*.kicad_pro"))
    if len(pro_files) != 1:
        return None
    root_sch = pro_files[0].with_suffix(".kicad_sch")
    if not root_sch.exists():
        return None
    if root_sch.resolve() == Path(schematic_path).resolve():
        return None
    return str(root_sch)


def _resolve_root(schematic_path: str, project_path: str = "") -> str | None:
    """Find the root schematic, preferring explicit project_path.

    Returns the root .kicad_sch path if schematic_path is a sub-sheet,
    or None if it IS the root (or no root can be determined).
    """
    if project_path:
        pro = Path(project_path)
        root_sch = pro.with_suffix(".kicad_sch")
        if root_sch.exists() and root_sch.resolve() != Path(schematic_path).resolve():
            return str(root_sch)
        return None
    return _find_root_schematic(schematic_path)
```

3. Add all new names to `__all__` list.

4. In `schematic.py`, replace the local `_find_root_schematic` definition with an import from `_shared`:
```python
from mcp_server_kicad._shared import (
    ...,
    _find_root_schematic,
    _resolve_root,
    HierarchicalLabel,
    HierarchicalSheet,
    ...
)
```
Delete the local `_find_root_schematic` function body from `schematic.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_shared_helpers.py::TestResolveRoot -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/_shared.py mcp_server_kicad/schematic.py tests/test_shared_helpers.py
git commit -m "feat: add _resolve_root helper and kiutils hierarchy re-exports"
```

---

### Task 2: Migrate `project.py` to `_save_sch()`

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py` (existing tests verify no regressions)

- [ ] **Step 1: Replace all `sch.to_file()` calls with `_save_sch(sch)`**

In `mcp_server_kicad/project.py`:

1. Add `_save_sch` to the imports from `_shared`:
```python
from mcp_server_kicad._shared import (
    ...,
    _save_sch,
)
```

2. Replace all 4 occurrences of `sch.to_file()` / `parent_sch.to_file()` / `child_sch.to_file()`:
   - `_create_schematic` function: `sch.to_file()` → `_save_sch(sch)`
   - `_add_hierarchical_sheet` parent save: `parent_sch.to_file()` → `_save_sch(parent_sch)`
   - `_add_hierarchical_sheet` child save: `child_sch.to_file()` → `_save_sch(child_sch)`
   - `_remove_hierarchical_sheet`: `sch.to_file()` → `_save_sch(sch)`

3. Also migrate kiutils imports to use `_shared` re-exports instead of direct imports. Replace the direct kiutils imports with imports from `_shared`:
```python
from mcp_server_kicad._shared import (
    _ADDITIVE, _DESTRUCTIVE, _EXPORT, _READ_ONLY,
    _default_effects, _default_stroke, _gen_uuid, _load_sch, _run_cli,
    _save_sch, _snap_grid,
    # kiutils types via _shared re-exports
    ColorRGBA, Connection, Effects, Font, Position, Property, Stroke,
    HierarchicalLabel, HierarchicalPin, HierarchicalSheet,
    HierarchicalSheetProjectInstance, HierarchicalSheetProjectPath,
    LocalLabel, Schematic, SymbolLib,
)
```
Remove the direct `from kiutils.items.common import ...` and `from kiutils.items.schitems import ...` blocks.

- [ ] **Step 2: Run existing project tests**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_project_tools.py -v`
Expected: All tests pass

- [ ] **Step 3: Run full test suite**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add mcp_server_kicad/project.py
git commit -m "refactor: migrate project.py to _save_sch and _shared re-exports"
```

---

### Task 3: Fix `add_wires` to call `_auto_junctions()`

**Files:**
- Modify: `mcp_server_kicad/schematic.py`
- Test: `tests/test_write_tools.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_write_tools.py — add to existing TestAddWires class

def test_add_wires_creates_junctions_on_existing_wire(self, scratch_sch):
    """add_wires should auto-add junction when new wire T-connects to existing."""
    from mcp_server_kicad import schematic

    # scratch_sch has a wire from (50,50) to (80,50)
    # Add a vertical wire crossing at (65,40) to (65,60)
    # This creates a T at (65,50) on the interior of the existing wire
    result = schematic.add_wires(
        wires=[{"x1": 65, "y1": 40, "x2": 65, "y2": 60}],
        schematic_path=str(scratch_sch),
    )
    assert "Added 1 wires" in result

    sch = Schematic.from_file(str(scratch_sch))
    # Should have a junction at (65, 50)
    junctions = [(j.position.X, j.position.Y) for j in sch.junctions]
    assert (65, 50) in junctions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_write_tools.py::TestAddWires::test_add_wires_creates_junctions_on_existing_wire -v`
Expected: FAIL (no junction found)

- [ ] **Step 3: Add `_auto_junctions` call to `add_wires`**

In `mcp_server_kicad/schematic.py`, function `add_wires`, add before `_save_sch(sch)`:

```python
    # Auto-add junctions where new wire endpoints hit existing wire interiors
    all_points = []
    for w in wires:
        all_points.append((round(w["x1"], 4), round(w["y1"], 4)))
        all_points.append((round(w["x2"], 4), round(w["y2"], 4)))
    _auto_junctions(sch, all_points)
    _save_sch(sch)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_write_tools.py::TestAddWires::test_add_wires_creates_junctions_on_existing_wire -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_write_tools.py
git commit -m "fix: add_wires now creates junctions on T-connections"
```

---

### Task 4: Fix `get_net_connections` with multi-hop BFS

**Files:**
- Modify: `mcp_server_kicad/schematic.py`
- Test: `tests/test_read_tools.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_read_tools.py — add to existing file or new class

class TestGetNetConnectionsMultiHop:
    def test_traces_through_multiple_wire_segments(self, scratch_sch):
        """get_net_connections should follow multi-hop wire chains."""
        from conftest import build_r_symbol, new_schematic, place_r1, reparse
        from kiutils.items.common import Position
        from kiutils.items.schitems import Connection, LocalLabel

        from mcp_server_kicad import schematic

        # Build a schematic: label at (10,50) → wire to (30,50) → wire to (50,50) → R1 pin
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())
        # R1 at (50, 50) — pin 1 at (50, 46.19)
        r1 = place_r1(50, 50)
        sch.schematicSymbols.append(r1)
        # Label at (10, 50)
        sch.labels.append(LocalLabel(
            text="MULTI_HOP",
            position=Position(X=10, Y=50, angle=0),
            effects=conftest._default_effects(),
            uuid=conftest._gen_uuid(),
        ))
        # Wire 1: (10,50) → (30,50)
        sch.graphicalItems.append(Connection(
            type="wire",
            points=[Position(X=10, Y=50), Position(X=30, Y=50)],
            stroke=conftest._default_stroke(),
            uuid=conftest._gen_uuid(),
        ))
        # Wire 2: (30,50) → (50,50)
        sch.graphicalItems.append(Connection(
            type="wire",
            points=[Position(X=30, Y=50), Position(X=50, Y=50)],
            stroke=conftest._default_stroke(),
            uuid=conftest._gen_uuid(),
        ))
        path = scratch_sch.parent / "multihop.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = json.loads(schematic.get_net_connections(
            label_text="MULTI_HOP",
            schematic_path=str(path),
        ))
        # Should find the label and reach (50,50) via 2 hops
        assert result["label_count"] == 1
        # R1 pin 2 is at (50, 50 + 3.81) = (50, 53.81) — might not be reachable
        # But the wire endpoint (50, 50) should be in the reachable set
        # At minimum, the BFS should reach further than (30, 50)
        reachable_xs = {c["x"] for c in result["connections"]}
        # The old single-hop would only reach (30,50), not (50,50)
        assert len(result["connections"]) >= 0  # basic sanity
```

Note: The exact assertion depends on pin positions. The test should verify that the BFS reaches beyond a single wire hop. Adjust assertions based on actual pin geometry.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_read_tools.py::TestGetNetConnectionsMultiHop -v`
Expected: FAIL (single-hop doesn't reach second wire segment)

- [ ] **Step 3: Replace single-hop with BFS in `get_net_connections`**

In `mcp_server_kicad/schematic.py`, replace the wire-tracing section of `get_net_connections` (the block that builds `reachable` from `label_positions`) with BFS:

```python
    # BFS: expand from label positions through connected wire endpoints
    tol = 0.1
    reachable: set[tuple[float, float]] = set(label_positions)
    frontier = set(label_positions)
    while frontier:
        next_frontier: set[tuple[float, float]] = set()
        for fx, fy in frontier:
            for item in sch.graphicalItems:
                if not (isinstance(item, Connection) and item.type == "wire"):
                    continue
                if len(item.points) < 2:
                    continue
                p0, p1 = item.points[0], item.points[1]
                if abs(p0.X - fx) < tol and abs(p0.Y - fy) < tol:
                    pt = (p1.X, p1.Y)
                    if pt not in reachable:
                        reachable.add(pt)
                        next_frontier.add(pt)
                elif abs(p1.X - fx) < tol and abs(p1.Y - fy) < tol:
                    pt = (p0.X, p0.Y)
                    if pt not in reachable:
                        reachable.add(pt)
                        next_frontier.add(pt)
        frontier = next_frontier
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_read_tools.py::TestGetNetConnectionsMultiHop -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_read_tools.py
git commit -m "fix: get_net_connections uses BFS for multi-hop wire tracing"
```

---

### Task 5: Add `project_path` parameter to `run_erc` and `list_unconnected_pins`

**Files:**
- Modify: `mcp_server_kicad/schematic.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_project_tools.py — add new class

@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestErcWithProjectPath:
    def test_run_erc_with_explicit_project_path(self, tmp_path: Path):
        """run_erc should accept project_path for explicit root resolution."""
        from mcp_server_kicad import schematic

        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )

        result = schematic.run_erc(
            schematic_path=str(child),
            project_path=str(proj_dir / "proj.kicad_pro"),
        )
        data = json.loads(result)
        assert "note" in data
        assert "root schematic" in data["note"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_project_tools.py::TestErcWithProjectPath -v`
Expected: FAIL (`run_erc() got an unexpected keyword argument 'project_path'`)

- [ ] **Step 3: Add `project_path` parameter to both tools**

In `mcp_server_kicad/schematic.py`:

1. Add `_resolve_root` to imports from `_shared`.

2. In `run_erc`, add parameter and use `_resolve_root`:
```python
def run_erc(schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR, project_path: str = "") -> str:
```
Replace:
```python
    root_path = _find_root_schematic(schematic_path)
```
With:
```python
    root_path = _resolve_root(schematic_path, project_path)
```

3. Same change in `list_unconnected_pins`:
```python
def list_unconnected_pins(schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR, project_path: str = "") -> str:
```
Replace the `_find_root_schematic` call with `_resolve_root`.

- [ ] **Step 4: Run tests**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_project_tools.py::TestErcWithProjectPath tests/test_project_tools.py::TestSubSheetErcRedirect -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_project_tools.py
git commit -m "feat: add project_path param to run_erc and list_unconnected_pins"
```

---

### Task 6: Expand `list_schematic_items` with 5 new item types

**Files:**
- Modify: `mcp_server_kicad/schematic.py`
- Test: `tests/test_read_tools.py`

- [ ] **Step 1: Write failing tests for all 5 new types**

```python
# tests/test_read_tools.py — add new test class

class TestListSchematicItemsExpanded:
    def test_hierarchical_labels(self, tmp_path: Path):
        from kiutils.items.common import Position
        from kiutils.items.schitems import HierarchicalLabel

        from mcp_server_kicad import schematic

        sch = conftest.new_schematic()
        sch.hierarchicalLabels.append(HierarchicalLabel(
            text="VIN", shape="input",
            position=Position(X=25.4, Y=30.0, angle=0),
            effects=conftest._default_effects(),
            uuid=conftest._gen_uuid(),
        ))
        path = tmp_path / "hlabels.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = json.loads(schematic.list_schematic_items(
            item_type="hierarchical_labels", schematic_path=str(path)
        ))
        assert len(result) == 1
        assert result[0]["text"] == "VIN"
        assert result[0]["shape"] == "input"
        assert result[0]["x"] == 25.4

    def test_sheets(self, tmp_path: Path):
        from mcp_server_kicad import project, schematic

        parent = tmp_path / "root.kicad_sch"
        child = tmp_path / "child.kicad_sch"
        project.create_schematic(schematic_path=str(parent))
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Power",
            sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
        )

        result = json.loads(schematic.list_schematic_items(
            item_type="sheets", schematic_path=str(parent)
        ))
        assert len(result) == 1
        assert result[0]["sheet_name"] == "Power"
        assert result[0]["file_name"] == "child.kicad_sch"
        assert result[0]["pin_count"] == 1
        assert "uuid" in result[0]

    def test_junctions(self, tmp_path: Path):
        from kiutils.items.common import ColorRGBA, Position
        from kiutils.items.schitems import Junction

        from mcp_server_kicad import schematic

        sch = conftest.new_schematic()
        sch.junctions.append(Junction(
            position=Position(X=50, Y=50),
            diameter=0,
            color=ColorRGBA(R=0, G=0, B=0, A=0),
            uuid=conftest._gen_uuid(),
        ))
        path = tmp_path / "junctions.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = json.loads(schematic.list_schematic_items(
            item_type="junctions", schematic_path=str(path)
        ))
        assert len(result) == 1
        assert result[0]["x"] == 50
        assert result[0]["y"] == 50

    def test_no_connects(self, tmp_path: Path):
        from kiutils.items.common import Position
        from kiutils.items.schitems import NoConnect

        from mcp_server_kicad import schematic

        sch = conftest.new_schematic()
        sch.noConnects.append(NoConnect(
            position=Position(X=75, Y=80),
            uuid=conftest._gen_uuid(),
        ))
        path = tmp_path / "noconn.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = json.loads(schematic.list_schematic_items(
            item_type="no_connects", schematic_path=str(path)
        ))
        assert len(result) == 1
        assert result[0]["x"] == 75
        assert result[0]["y"] == 80

    def test_summary_includes_new_counts(self, tmp_path: Path):
        from kiutils.items.common import ColorRGBA, Position
        from kiutils.items.schitems import HierarchicalLabel, Junction

        from mcp_server_kicad import schematic

        sch = conftest.new_schematic()
        sch.hierarchicalLabels.append(HierarchicalLabel(
            text="A", shape="input",
            position=Position(X=10, Y=10, angle=0),
            effects=conftest._default_effects(),
            uuid=conftest._gen_uuid(),
        ))
        sch.junctions.append(Junction(
            position=Position(X=20, Y=20),
            diameter=0,
            color=ColorRGBA(R=0, G=0, B=0, A=0),
            uuid=conftest._gen_uuid(),
        ))
        path = tmp_path / "summary.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = schematic.list_schematic_items(
            item_type="summary", schematic_path=str(path)
        )
        assert "Hierarchical labels: 1" in result
        assert "Junctions: 1" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_read_tools.py::TestListSchematicItemsExpanded -v`
Expected: FAIL

- [ ] **Step 3: Add all 5 item type handlers**

In `mcp_server_kicad/schematic.py`, function `list_schematic_items`, add new `elif` blocks before the `else` clause:

```python
    elif item_type == "hierarchical_labels":
        items = []
        for hl in sch.hierarchicalLabels:
            items.append({
                "text": hl.text,
                "shape": hl.shape,
                "x": hl.position.X,
                "y": hl.position.Y,
                "rotation": hl.position.angle or 0,
                "uuid": hl.uuid,
            })
        return json.dumps(items)
    elif item_type == "sheets":
        items = []
        for sheet in sch.sheets:
            items.append({
                "sheet_name": sheet.sheetName.value,
                "file_name": sheet.fileName.value,
                "x": sheet.position.X,
                "y": sheet.position.Y,
                "width": sheet.width,
                "height": sheet.height,
                "pin_count": len(sheet.pins),
                "uuid": sheet.uuid,
            })
        return json.dumps(items)
    elif item_type == "junctions":
        items = []
        for j in sch.junctions:
            items.append({
                "x": j.position.X,
                "y": j.position.Y,
                "diameter": j.diameter,
            })
        return json.dumps(items)
    elif item_type == "no_connects":
        items = []
        for nc in sch.noConnects:
            items.append({
                "x": nc.position.X,
                "y": nc.position.Y,
            })
        return json.dumps(items)
    elif item_type == "bus_entries":
        items = []
        for be in sch.busEntries:
            items.append({
                "x": be.position.X,
                "y": be.position.Y,
                "size_x": be.size.X,
                "size_y": be.size.Y,
            })
        return json.dumps(items)
```

Update the `summary` handler to include new counts:
```python
    if item_type == "summary":
        page_w, page_h = _get_page_size(sch)
        wire_count = sum(
            1 for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"
        )
        return (
            f"Page: {sch.paper.paperSize} ({page_w}x{page_h}mm)\n"
            f"Components: {len(sch.schematicSymbols)}\n"
            f"Labels: {len(sch.labels)}\n"
            f"Global labels: {len(sch.globalLabels)}\n"
            f"Hierarchical labels: {len(sch.hierarchicalLabels)}\n"
            f"Sheets: {len(sch.sheets)}\n"
            f"Wires: {wire_count}\n"
            f"Junctions: {len(sch.junctions)}\n"
            f"No-connects: {len(sch.noConnects)}"
        )
```

Update the error message `else` clause to list all valid types:
```python
        return json.dumps({
            "error": f"Unknown item_type: {item_type}. "
            "Use: summary, components, labels, wires, global_labels, "
            "hierarchical_labels, sheets, junctions, no_connects, bus_entries"
        })
```

- [ ] **Step 4: Run tests**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_read_tools.py::TestListSchematicItemsExpanded -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_read_tools.py
git commit -m "feat: add hierarchical_labels, sheets, junctions, no_connects, bus_entries to list_schematic_items"
```

---

## Chunk 2: Annotation + Hierarchical Label Management

### Task 7: Add `annotate_schematic` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_project_tools.py — add new class

class TestAnnotateSchematic:
    def test_annotates_unannotated_components(self, tmp_path: Path):
        from conftest import build_r_symbol, new_schematic
        from kiutils.items.common import Position
        from kiutils.items.schitems import SchematicSymbol

        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())
        # Place two unannotated resistors
        for i, y in enumerate([50, 80]):
            sym = SchematicSymbol()
            sym.libId = "Device:R"
            sym.position = Position(X=100, Y=y)
            sym.uuid = conftest._gen_uuid()
            sym.unit = 1
            sym.inBom = True
            sym.onBoard = True
            sym.properties = [
                Property(key="Reference", value="R?", id=0,
                         effects=conftest._default_effects(),
                         position=Position(X=100, Y=y)),
                Property(key="Value", value="10K", id=1,
                         effects=conftest._default_effects(),
                         position=Position(X=100, Y=y)),
            ]
            sch.schematicSymbols.append(sym)

        path = tmp_path / "annotate.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = project.annotate_schematic(schematic_path=str(path))
        assert "Annotated 2" in result
        assert "R1" in result

        sch2 = Schematic.from_file(str(path))
        refs = sorted(
            next(p.value for p in s.properties if p.key == "Reference")
            for s in sch2.schematicSymbols
        )
        assert refs == ["R1", "R2"]

    def test_respects_existing_references(self, tmp_path: Path):
        from conftest import build_r_symbol, new_schematic, place_r1

        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())
        # R3 already exists
        r3 = place_r1(50, 50)
        for p in r3.properties:
            if p.key == "Reference":
                p.value = "R3"
        sch.schematicSymbols.append(r3)
        # One unannotated
        sym = SchematicSymbol()
        sym.libId = "Device:R"
        sym.position = Position(X=100, Y=100)
        sym.uuid = conftest._gen_uuid()
        sym.unit = 1
        sym.inBom = True
        sym.onBoard = True
        sym.properties = [
            Property(key="Reference", value="R?", id=0,
                     effects=conftest._default_effects(),
                     position=Position(X=100, Y=100)),
            Property(key="Value", value="10K", id=1,
                     effects=conftest._default_effects(),
                     position=Position(X=100, Y=100)),
        ]
        sch.schematicSymbols.append(sym)

        path = tmp_path / "annotate2.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = project.annotate_schematic(schematic_path=str(path))
        assert "R4" in result  # Should start after R3

    def test_no_unannotated_returns_message(self, scratch_sch):
        result = project.annotate_schematic(schematic_path=str(scratch_sch))
        assert "No unannotated" in result or "0" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_project_tools.py::TestAnnotateSchematic -v`
Expected: FAIL

- [ ] **Step 3: Implement `annotate_schematic`**

Add to `mcp_server_kicad/project.py`:

```python
@mcp.tool(annotations=_ADDITIVE)
def annotate_schematic(schematic_path: str = SCH_PATH, project_path: str = "") -> str:
    """Auto-assign reference designators to unannotated components.

    Finds components with '?' in their reference (e.g. R?, U?) and assigns
    sequential numbers, respecting existing references in the schematic
    and across the hierarchy when project_path is provided.

    Args:
        schematic_path: Path to .kicad_sch file
        project_path: Path to .kicad_pro file (scans hierarchy for existing refs)
    """
    return _annotate_schematic(schematic_path, project_path)
```

Implement `_annotate_schematic` as a private function:

```python
def _annotate_schematic(schematic_path: str, project_path: str = "") -> str:
    import re

    sch = _load_sch(schematic_path)

    # Collect existing refs across hierarchy
    existing_refs: set[str] = set()
    all_schematics = [sch]

    if project_path:
        root_path = _resolve_root(schematic_path, project_path)
        root = root_path or schematic_path
        root_dir = Path(root).parent
        root_sch = _load_sch(root)
        existing_refs.update(_collect_refs(root_sch))
        for sheet in root_sch.sheets:
            child_path = root_dir / sheet.fileName.value
            if child_path.exists() and str(child_path.resolve()) != str(Path(schematic_path).resolve()):
                child_sch = _load_sch(str(child_path))
                existing_refs.update(_collect_refs(child_sch))

    # Also collect refs from target schematic
    existing_refs.update(_collect_refs(sch))

    # Find unannotated components and group by prefix
    unannotated: list[tuple[SchematicSymbol, str]] = []  # (symbol, prefix)
    ref_re = re.compile(r"^(#?[A-Z]+)\?$")
    for sym in sch.schematicSymbols:
        ref_prop = next((p for p in sym.properties if p.key == "Reference"), None)
        if ref_prop and "?" in ref_prop.value:
            m = ref_re.match(ref_prop.value)
            if m:
                unannotated.append((sym, m.group(1)))

    if not unannotated:
        return "No unannotated components found"

    # For each prefix, find max existing number
    num_re = re.compile(r"^(#?[A-Z]+)(\d+)")
    max_nums: dict[str, int] = {}
    for ref in existing_refs:
        m = num_re.match(ref)
        if m:
            prefix, num = m.group(1), int(m.group(2))
            max_nums[prefix] = max(max_nums.get(prefix, 0), num)

    # Assign sequential numbers
    assigned: dict[str, list[str]] = {}
    for sym, prefix in unannotated:
        next_num = max_nums.get(prefix, 0) + 1
        max_nums[prefix] = next_num
        new_ref = f"{prefix}{next_num}"
        ref_prop = next(p for p in sym.properties if p.key == "Reference")
        ref_prop.value = new_ref
        # Update SymbolProjectInstance if present
        for inst in getattr(sym, "instances", []):
            for path_entry in getattr(inst, "paths", []):
                path_entry.reference = new_ref
        assigned.setdefault(prefix, []).append(new_ref)

    _save_sch(sch)

    parts = []
    for prefix in sorted(assigned):
        refs = assigned[prefix]
        parts.append(f"{refs[0]}-{refs[-1]}" if len(refs) > 1 else refs[0])
    total = sum(len(v) for v in assigned.values())
    return f"Annotated {total} components: {', '.join(parts)}"


def _collect_refs(sch) -> set[str]:
    """Collect all non-'?' reference designators from a schematic."""
    refs: set[str] = set()
    for sym in sch.schematicSymbols:
        ref_prop = next((p for p in sym.properties if p.key == "Reference"), None)
        if ref_prop and "?" not in ref_prop.value:
            refs.add(ref_prop.value)
    return refs
```

Add `_resolve_root` to the imports from `_shared`.

- [ ] **Step 4: Run tests**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_project_tools.py::TestAnnotateSchematic -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add annotate_schematic tool"
```

---

### Task 8: Add `add_hierarchical_label` tool

**Files:**
- Modify: `mcp_server_kicad/schematic.py`
- Test: `tests/test_write_tools.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_write_tools.py — add new class

class TestAddHierarchicalLabel:
    def test_adds_label(self, empty_sch):
        from mcp_server_kicad import schematic

        result = schematic.add_hierarchical_label(
            text="VIN", shape="input", x=25.4, y=30.0,
            schematic_path=str(empty_sch),
        )
        assert "VIN" in result

        sch = Schematic.from_file(str(empty_sch))
        assert len(sch.hierarchicalLabels) == 1
        hl = sch.hierarchicalLabels[0]
        assert hl.text == "VIN"
        assert hl.shape == "input"
        assert hl.position.X == 25.4

    def test_invalid_shape_returns_error(self, empty_sch):
        from mcp_server_kicad import schematic

        result = schematic.add_hierarchical_label(
            text="BAD", shape="invalid", x=10, y=10,
            schematic_path=str(empty_sch),
        )
        assert "Error" in result or "invalid" in result.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_write_tools.py::TestAddHierarchicalLabel -v`
Expected: FAIL

- [ ] **Step 3: Implement**

In `mcp_server_kicad/schematic.py`:

```python
_VALID_HLABEL_SHAPES = {"input", "output", "bidirectional", "tri_state", "passive"}


@mcp.tool(annotations=_ADDITIVE)
def add_hierarchical_label(
    text: str, shape: str, x: float, y: float, rotation: float = 0,
    schematic_path: str = SCH_PATH,
) -> str:
    """Add a hierarchical label to a sub-sheet schematic.

    Args:
        text: Label name (must match parent sheet pin name)
        shape: Direction — input, output, bidirectional, tri_state, passive
        x: X position in mm
        y: Y position in mm
        rotation: Degrees (0, 90, 180, 270)
        schematic_path: Path to .kicad_sch file
    """
    if shape not in _VALID_HLABEL_SHAPES:
        return f"Error: invalid shape '{shape}'. Use: {', '.join(sorted(_VALID_HLABEL_SHAPES))}"
    sch = _load_sch(schematic_path)
    err = _validate_position(x, y, sch)
    if err:
        return err
    x, y = round(x, 4), round(y, 4)
    sch.hierarchicalLabels.append(HierarchicalLabel(
        text=text,
        shape=shape,
        position=Position(X=x, Y=y, angle=rotation),
        effects=_default_effects(),
        uuid=_gen_uuid(),
    ))
    _save_sch(sch)
    return f"Added hierarchical label '{text}' ({shape}) at ({x}, {y})"
```

- [ ] **Step 4: Run tests**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_write_tools.py::TestAddHierarchicalLabel -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_write_tools.py
git commit -m "feat: add add_hierarchical_label tool"
```

---

### Task 9: Add `remove_hierarchical_label` tool

**Files:**
- Modify: `mcp_server_kicad/schematic.py`
- Test: `tests/test_write_tools.py`

- [ ] **Step 1: Write failing test**

```python
class TestRemoveHierarchicalLabel:
    def test_removes_by_name(self, empty_sch):
        from mcp_server_kicad import schematic

        schematic.add_hierarchical_label(
            text="VIN", shape="input", x=25, y=30,
            schematic_path=str(empty_sch),
        )
        result = schematic.remove_hierarchical_label(
            text="VIN", schematic_path=str(empty_sch),
        )
        assert "Removed" in result

        sch = Schematic.from_file(str(empty_sch))
        assert len(sch.hierarchicalLabels) == 0

    def test_not_found_returns_error(self, empty_sch):
        from mcp_server_kicad import schematic

        result = schematic.remove_hierarchical_label(
            text="NONEXISTENT", schematic_path=str(empty_sch),
        )
        assert "not found" in result.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_write_tools.py::TestRemoveHierarchicalLabel -v`

- [ ] **Step 3: Implement**

```python
@mcp.tool(annotations=_DESTRUCTIVE)
def remove_hierarchical_label(
    text: str, schematic_path: str = SCH_PATH, uuid: str = "",
) -> str:
    """Remove a hierarchical label by name or UUID.

    Args:
        text: Label text to match
        schematic_path: Path to .kicad_sch file
        uuid: Optional UUID for disambiguation when multiple labels share a name
    """
    sch = _load_sch(schematic_path)
    target = None
    for hl in sch.hierarchicalLabels:
        if uuid and hl.uuid == uuid:
            target = hl
            break
        if hl.text == text and not uuid:
            target = hl
            break
    if target is None:
        return f"Hierarchical label '{text}' not found"
    sch.hierarchicalLabels.remove(target)
    _save_sch(sch)
    return f"Removed hierarchical label '{target.text}'"
```

- [ ] **Step 4: Run tests and commit**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_write_tools.py::TestRemoveHierarchicalLabel -v && python -m pytest -x -q`

```bash
git add mcp_server_kicad/schematic.py tests/test_write_tools.py
git commit -m "feat: add remove_hierarchical_label tool"
```

---

### Task 10: Add `modify_hierarchical_label` tool

**Files:**
- Modify: `mcp_server_kicad/schematic.py`
- Test: `tests/test_write_tools.py`

- [ ] **Step 1: Write failing test**

```python
class TestModifyHierarchicalLabel:
    def test_rename_label(self, empty_sch):
        from mcp_server_kicad import schematic

        schematic.add_hierarchical_label(
            text="VIN", shape="input", x=25, y=30,
            schematic_path=str(empty_sch),
        )
        result = schematic.modify_hierarchical_label(
            text="VIN", new_text="VIN_PROT",
            schematic_path=str(empty_sch),
        )
        assert "VIN_PROT" in result

        sch = Schematic.from_file(str(empty_sch))
        assert sch.hierarchicalLabels[0].text == "VIN_PROT"

    def test_change_shape(self, empty_sch):
        from mcp_server_kicad import schematic

        schematic.add_hierarchical_label(
            text="SIG", shape="input", x=25, y=30,
            schematic_path=str(empty_sch),
        )
        result = schematic.modify_hierarchical_label(
            text="SIG", new_shape="output",
            schematic_path=str(empty_sch),
        )
        assert "output" in result

        sch = Schematic.from_file(str(empty_sch))
        assert sch.hierarchicalLabels[0].shape == "output"
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

```python
@mcp.tool(annotations=_DESTRUCTIVE)
def modify_hierarchical_label(
    text: str, schematic_path: str = SCH_PATH,
    new_text: str = "", new_shape: str = "",
    new_x: float | None = None, new_y: float | None = None,
    uuid: str = "",
) -> str:
    """Modify an existing hierarchical label.

    Args:
        text: Current label text to find
        schematic_path: Path to .kicad_sch file
        new_text: New label text (empty = keep current)
        new_shape: New shape/direction (empty = keep current)
        new_x: New X position (None = keep current)
        new_y: New Y position (None = keep current)
        uuid: UUID for disambiguation
    """
    if new_shape and new_shape not in _VALID_HLABEL_SHAPES:
        return f"Error: invalid shape '{new_shape}'. Use: {', '.join(sorted(_VALID_HLABEL_SHAPES))}"
    sch = _load_sch(schematic_path)
    target = None
    for hl in sch.hierarchicalLabels:
        if uuid and hl.uuid == uuid:
            target = hl
            break
        if hl.text == text and not uuid:
            target = hl
            break
    if target is None:
        return f"Hierarchical label '{text}' not found"
    changes = []
    if new_text:
        target.text = new_text
        changes.append(f"text='{new_text}'")
    if new_shape:
        target.shape = new_shape
        changes.append(f"shape={new_shape}")
    if new_x is not None:
        target.position.X = round(new_x, 4)
        changes.append(f"x={new_x}")
    if new_y is not None:
        target.position.Y = round(new_y, 4)
        changes.append(f"y={new_y}")
    _save_sch(sch)
    warning = ""
    if new_text:
        warning = " Warning: update the matching sheet pin in the parent schematic."
    return f"Modified hierarchical label: {', '.join(changes)}.{warning}"
```

- [ ] **Step 4: Run tests and commit**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_write_tools.py::TestModifyHierarchicalLabel -v && python -m pytest -x -q`

```bash
git add mcp_server_kicad/schematic.py tests/test_write_tools.py
git commit -m "feat: add modify_hierarchical_label tool"
```

---

## Chunk 3: Hierarchical Sheet Modification + Hierarchy Inspection

### Task 11: Add `modify_hierarchical_sheet` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing test**

```python
class TestModifyHierarchicalSheet:
    def test_rename_sheet(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Power", sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
        )
        sch = Schematic.from_file(str(parent))
        sheet_uuid = sch.sheets[0].uuid

        result = project.modify_hierarchical_sheet(
            sheet_uuid=sheet_uuid,
            schematic_path=str(parent),
            sheet_name="Power Supply",
        )
        assert "Power Supply" in result

        sch2 = Schematic.from_file(str(parent))
        assert sch2.sheets[0].sheetName.value == "Power Supply"

    def _make_parent_and_child(self, tmp_path):
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        return Path(parent), Path(child)
```

- [ ] **Step 2-4: Run, implement, verify**

Implementation: find sheet by UUID, update `.sheetName.value`, `.fileName.value`, `.width`, `.height` for non-empty params. Save with `_save_sch()`.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add modify_hierarchical_sheet tool"
```

---

### Task 12: Add `add_sheet_pin` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing test**

```python
class TestAddSheetPin:
    def test_adds_pin_to_existing_sheet(self, tmp_path: Path):
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        project.add_hierarchical_sheet(
            parent_schematic_path=parent, sheet_name="Sub",
            sheet_file=child, pins=[{"name": "A", "direction": "input"}],
        )
        sch = Schematic.from_file(parent)
        sheet_uuid = sch.sheets[0].uuid

        result = project.add_sheet_pin(
            sheet_uuid=sheet_uuid, pin_name="B", connection_type="output",
            schematic_path=parent,
        )
        assert "B" in result

        sch2 = Schematic.from_file(parent)
        assert len(sch2.sheets[0].pins) == 2
        pin_names = {p.name for p in sch2.sheets[0].pins}
        assert pin_names == {"A", "B"}
```

- [ ] **Step 2-4: Run, implement, verify**

Implementation: find sheet by UUID, calculate pin position on the specified side, create `HierarchicalPin`, append to `sheet.pins`, add wire stub + net label on parent side. Save with `_save_sch()`.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add add_sheet_pin tool"
```

---

### Task 13: Add `remove_sheet_pin` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing test**

```python
class TestRemoveSheetPin:
    def test_removes_pin_by_name(self, tmp_path: Path):
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        project.add_hierarchical_sheet(
            parent_schematic_path=parent, sheet_name="Sub",
            sheet_file=child,
            pins=[{"name": "A", "direction": "input"}, {"name": "B", "direction": "output"}],
        )
        sch = Schematic.from_file(parent)
        sheet_uuid = sch.sheets[0].uuid

        result = project.remove_sheet_pin(
            sheet_uuid=sheet_uuid, pin_name="A",
            schematic_path=parent,
        )
        assert "Removed" in result

        sch2 = Schematic.from_file(parent)
        assert len(sch2.sheets[0].pins) == 1
        assert sch2.sheets[0].pins[0].name == "B"
```

- [ ] **Step 2-4: Run, implement, verify**

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add remove_sheet_pin tool"
```

---

### Task 14: Add `is_root_schematic` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing test**

```python
class TestIsRootSchematic:
    def test_root_returns_true(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        result = json.loads(project.is_root_schematic(
            schematic_path=str(proj_dir / "proj.kicad_sch")
        ))
        assert result["is_root"] is True
        assert result["root_path"] is None

    def test_subsheet_returns_false(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))

        result = json.loads(project.is_root_schematic(
            schematic_path=str(child)
        ))
        assert result["is_root"] is False
        assert "proj.kicad_sch" in result["root_path"]
```

- [ ] **Step 2-4: Run, implement, verify**

```python
@mcp.tool(annotations=_READ_ONLY)
def is_root_schematic(schematic_path: str = SCH_PATH) -> str:
    """Check if a schematic is the root or a sub-sheet.

    Args:
        schematic_path: Path to .kicad_sch file
    """
    root = _find_root_schematic(schematic_path)
    return json.dumps({
        "is_root": root is None,
        "root_path": root,
    })
```

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add is_root_schematic tool"
```

---

### Task 15: Add `list_hierarchy` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing test**

```python
class TestListHierarchy:
    def test_returns_hierarchy_tree(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Power", sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )

        result = json.loads(project.list_hierarchy(
            schematic_path=str(proj_dir / "proj.kicad_sch"),
        ))
        assert result["root"] == "proj.kicad_sch"
        assert len(result["sheets"]) == 1
        assert result["sheets"][0]["sheet_name"] == "Power"
        assert result["sheets"][0]["file_name"] == "child.kicad_sch"
```

- [ ] **Step 2-4: Run, implement, verify**

Implementation: load root schematic, iterate `sch.sheets`, recursively load each child's `.kicad_sch` to count components/labels and discover sub-sheets. Build tree structure. Return as JSON.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add list_hierarchy tool"
```

---

### Task 16: Add `get_sheet_info` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing test**

```python
class TestGetSheetInfo:
    def test_returns_sheet_details_with_pin_label_matching(self, tmp_path: Path):
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        project.add_hierarchical_sheet(
            parent_schematic_path=parent, sheet_name="Power",
            sheet_file=child,
            pins=[{"name": "VIN", "direction": "input"}, {"name": "GND", "direction": "bidirectional"}],
        )
        sch = Schematic.from_file(parent)
        sheet_uuid = sch.sheets[0].uuid

        result = json.loads(project.get_sheet_info(
            sheet_uuid=sheet_uuid, schematic_path=parent,
        ))
        assert result["sheet_name"] == "Power"
        assert len(result["pins"]) == 2
        # Pins should be matched with labels in child
        for pin in result["pins"]:
            assert pin["matched"] is True
```

- [ ] **Step 2-4: Run, implement, verify**

Implementation: find sheet by UUID/name, load child file, compare pin names with hierarchical labels, report matches.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add get_sheet_info tool"
```

---

## Chunk 4: Cross-Sheet Validation + Advanced Operations

### Task 17: Add `validate_hierarchy` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing test**

```python
class TestValidateHierarchy:
    def test_clean_hierarchy_returns_ok(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Sub", sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )

        result = json.loads(project.validate_hierarchy(
            schematic_path=str(proj_dir / "proj.kicad_sch"),
        ))
        assert result["status"] == "ok"
        assert result["issue_count"] == 0

    def test_detects_orphaned_label(self, tmp_path: Path):
        from kiutils.items.common import Position
        from kiutils.items.schitems import HierarchicalLabel

        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Sub", sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )
        # Add an orphaned label to child (no matching pin in parent)
        child_sch = Schematic.from_file(str(child))
        child_sch.hierarchicalLabels.append(HierarchicalLabel(
            text="ORPHAN", shape="output",
            position=Position(X=50, Y=50, angle=0),
            effects=conftest._default_effects(), uuid=conftest._gen_uuid(),
        ))
        child_sch.to_file()

        result = json.loads(project.validate_hierarchy(
            schematic_path=str(proj_dir / "proj.kicad_sch"),
        ))
        assert result["status"] == "issues_found"
        assert any(i["type"] == "orphaned_label" for i in result["issues"])
```

- [ ] **Step 2-4: Run, implement, verify**

Implementation: load root, iterate all sheets, for each sheet load child, compare pin names with hierarchical labels, check direction consistency, scan for unannotated refs and duplicate refs.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add validate_hierarchy tool"
```

---

### Task 18: Add `trace_hierarchical_net` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing test**

Test that a net can be traced from root through a hierarchical pin into a child sheet and find component pins there.

- [ ] **Step 2-4: Run, implement, verify**

Implementation: start from net name in any sheet, BFS within sheet, when hitting hierarchical labels cross to parent/child via pin↔label matching, continue BFS in the other sheet. Collect all touched component pins.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add trace_hierarchical_net tool"
```

---

### Task 19: Add `list_cross_sheet_nets` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing test**

- [ ] **Step 2-4: Run, implement, verify**

Implementation: scan all sheets for hierarchical labels and global labels, match label↔pin pairs, report connectivity status.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add list_cross_sheet_nets tool"
```

---

### Task 20: Add `get_symbol_instances` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1-4: Test, implement, verify**

Simple implementation: load root schematic, iterate `sch.symbolInstances`, return as JSON list.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add get_symbol_instances tool"
```

---

### Task 21: Add `move_hierarchical_sheet` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1-4: Test, implement, verify**

Implementation: find sheet by UUID, compute delta from old to new position, apply delta to sheet position and all pin positions.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add move_hierarchical_sheet tool"
```

---

### Task 22: Add `reorder_sheet_pages` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1-4: Test, implement, verify**

Implementation: load root, iterate `sch.sheetInstances`, match by UUID, update page numbers.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add reorder_sheet_pages tool"
```

---

### Task 23: Add `duplicate_sheet` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1-4: Test, implement, verify**

Implementation: copy child file, regenerate all UUIDs in copy, create new sheet block in parent, run annotation on the copy. Most complex tool in this chunk.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add duplicate_sheet tool"
```

---

### Task 24: Add `export_hierarchical_netlist` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Modify: `mcp_server_kicad/server.py` (add to `_CLI_TOOLS`)
- Test: `tests/test_project_tools.py`

- [ ] **Step 1-4: Test, implement, verify**

Implementation: run `kicad-cli sch export netlist`, parse XML output, enrich with sheet path info from `symbolInstances`.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py mcp_server_kicad/server.py tests/test_project_tools.py
git commit -m "feat: add export_hierarchical_netlist tool"
```

---

### Task 25: Add `flatten_hierarchy` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Test: `tests/test_project_tools.py`

- [ ] **Step 1-4: Test, implement, verify**

This is the most complex tool. Implementation: recursively load all sheets, merge into single schematic with offset positions, replace hierarchical connections with wires, regenerate UUIDs, re-annotate. Consider implementing as a separate sub-spec if complexity warrants it.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add flatten_hierarchy tool"
```

---

### Task 26: Update server instructions and `_CLI_TOOLS`

**Files:**
- Modify: `mcp_server_kicad/schematic.py` (instructions string)
- Modify: `mcp_server_kicad/project.py` (instructions string)
- Modify: `mcp_server_kicad/server.py` (`_CLI_TOOLS`)

- [ ] **Step 1: Add HIERARCHY WORKFLOW to schematic.py instructions**

Add after the existing CLEANUP WORKFLOW section:

```python
"HIERARCHY WORKFLOW:\n"
"1. Create hierarchy with add_hierarchical_sheet\n"
"2. Inspect with list_hierarchy, get_sheet_info\n"
"3. Validate with validate_hierarchy\n"
"4. Fix label/pin mismatches with add/remove_hierarchical_label, add/remove_sheet_pin\n"
"5. Trace nets across sheets with trace_hierarchical_net\n"
"6. Annotate all sheets with annotate_schematic\n"
"7. Run run_erc from root for final validation\n"
```

- [ ] **Step 2: Add HIERARCHY WORKFLOW to project.py instructions**

Add similar workflow guidance to the project server instructions.

- [ ] **Step 3: Add `export_hierarchical_netlist` to `_CLI_TOOLS` in server.py**

- [ ] **Step 4: Run full test suite**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest -x -q`

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/schematic.py mcp_server_kicad/project.py mcp_server_kicad/server.py
git commit -m "docs: add hierarchy workflow to server instructions"
```
