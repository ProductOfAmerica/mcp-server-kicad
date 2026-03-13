# Remove Hierarchical Sheet Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `remove_hierarchical_sheet` MCP tool to `project.py` that removes a hierarchical sheet block from a parent schematic, with optional child file deletion.

**Architecture:** New function in `project.py` following the existing three-layer pattern (private function, public alias, MCP wrapper). Identifies sheets by UUID or name with disambiguation. Optional child file deletion with reference-count safety check.

**Tech Stack:** Python, kiutils, FastMCP, pytest

**Spec:** `docs/superpowers/specs/2026-03-13-remove-hierarchical-sheet-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `mcp_server_kicad/project.py` | Modify | Add `_remove_hierarchical_sheet`, public alias, MCP wrapper; update imports and FastMCP instructions |
| `tests/test_project_tools.py` | Modify | Add `TestRemoveHierarchicalSheet` class with 9 test cases |
| `tests/test_tool_annotations.py` | Modify | Add `test_project_destructive` for the new destructive tool |

---

## Chunk 1: Core Implementation

### Task 1: Write failing tests for remove-by-UUID and remove-by-name

**Files:**
- Modify: `tests/test_project_tools.py` (after `TestAddHierarchicalSheet` class, before `HAS_KICAD_CLI` line ~330)

- [ ] **Step 1: Write the failing tests**

Add a new test class after `TestAddHierarchicalSheet`. Reuse the `_make_parent_and_child` pattern.

```python
class TestRemoveHierarchicalSheet:
    def _make_parent_and_child(self, tmp_path: Path) -> tuple[Path, Path]:
        """Helper: create empty parent + child schematics."""
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        return Path(parent), Path(child)

    def _add_sheet(self, parent: Path, child: Path, name: str = "Power") -> str:
        """Helper: add a hierarchical sheet and return its UUID."""
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name=name,
            sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
        )
        sch = Schematic.from_file(str(parent))
        uuid = sch.sheets[-1].uuid
        assert uuid is not None
        return uuid

    def test_remove_by_uuid(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        sheet_uuid = self._add_sheet(parent, child)

        result = project.remove_hierarchical_sheet(
            uuid=sheet_uuid,
            parent_schematic_path=str(parent),
        )
        assert "Removed" in result

        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 0

    def test_remove_by_name_unique(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        self._add_sheet(parent, child, name="Power")

        result = project.remove_hierarchical_sheet(
            name="Power",
            parent_schematic_path=str(parent),
        )
        assert "Removed" in result

        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_project_tools.py::TestRemoveHierarchicalSheet -v`
Expected: FAIL with `AttributeError: module 'mcp_server_kicad.project' has no attribute 'remove_hierarchical_sheet'`

- [ ] **Step 3: Implement minimal `_remove_hierarchical_sheet` + alias + MCP wrapper**

In `mcp_server_kicad/project.py`:

**First**, add `_DESTRUCTIVE` to the imports (line 26-36):

```python
from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    _default_effects,
    _default_stroke,
    _gen_uuid,
    _load_sch,
    _run_cli,
    _snap_grid,
)
```

**Second**, add the private function after `_add_hierarchical_sheet` (after line 322):

```python
def _remove_hierarchical_sheet(
    parent_schematic_path: str,
    name: str | None = None,
    uuid: str | None = None,
    delete_child_file: bool = False,
) -> str:
    """Remove a hierarchical sheet block from a parent schematic.

    Args:
        parent_schematic_path: Path to parent .kicad_sch
        name: Sheet name to match (via sheet.sheetName.value)
        uuid: Sheet UUID for unambiguous identification
        delete_child_file: If True, delete the child .kicad_sch file (unless still referenced)
    """
    if not name and not uuid:
        return "Provide at least one of 'name' or 'uuid'."

    sch = _load_sch(parent_schematic_path)

    def _normalize_uuid(u: str) -> str:
        return u.replace("-", "").lower()

    # Find matching sheets
    matches: list[int] = []
    for i, sheet in enumerate(sch.sheets):
        if uuid:
            if _normalize_uuid(sheet.uuid) == _normalize_uuid(uuid):
                if name and sheet.sheetName.value != name:
                    return (
                        f"Sheet with uuid={uuid} found but its name is "
                        f"'{sheet.sheetName.value}', not '{name}'."
                    )
                matches.append(i)
                break
        else:
            if sheet.sheetName.value == name:
                matches.append(i)

    if not matches:
        criteria = f"uuid={uuid}" if uuid else f"name='{name}'"
        return f"No hierarchical sheet found matching {criteria}."

    if len(matches) > 1:
        info = ", ".join(
            f"uuid={sch.sheets[i].uuid} at "
            f"({sch.sheets[i].position.X}, {sch.sheets[i].position.Y})"
            for i in matches
        )
        return f"Multiple sheets named '{name}' found: [{info}]. Provide uuid to disambiguate."

    target = sch.sheets[matches[0]]
    sheet_name = target.sheetName.value
    sheet_uuid = target.uuid
    child_filename = target.fileName.value
    msg = f"Removed hierarchical sheet '{sheet_name}' (uuid={sheet_uuid})."

    # Handle child file deletion
    if delete_child_file:
        parent_dir = Path(parent_schematic_path).parent
        child_path = parent_dir / child_filename
        # Check if any OTHER sheet still references this child file
        other_refs = any(
            s.fileName.value == child_filename
            for j, s in enumerate(sch.sheets)
            if j != matches[0]
        )
        if other_refs:
            msg += (
                f" Kept child file '{child_filename}' — "
                f"still referenced by another sheet block."
            )
        elif child_path.exists():
            child_path.unlink()
            msg += f" Deleted child file '{child_filename}'."

    sch.sheets.pop(matches[0])
    sch.to_file()
    return msg
```

**Third**, add the public alias (after the existing aliases, line ~330):

```python
remove_hierarchical_sheet = _remove_hierarchical_sheet
```

**Fourth**, add the MCP wrapper (after the `add_hierarchical_sheet` wrapper):

```python
@mcp.tool(annotations=_DESTRUCTIVE)
def remove_hierarchical_sheet(  # noqa: F811
    parent_schematic_path: str,
    name: str | None = None,
    uuid: str | None = None,
    delete_child_file: bool = False,
) -> str:
    """Remove a hierarchical sheet block from a parent schematic.

    Identify the sheet by name, uuid, or both. If name matches multiple sheets,
    returns an error with UUIDs for disambiguation.

    Args:
        parent_schematic_path: Path to parent .kicad_sch
        name: Sheet name to match
        uuid: Sheet UUID for unambiguous identification
        delete_child_file: If True, delete the child .kicad_sch file (unless still referenced by another sheet)
    """
    return _remove_hierarchical_sheet(parent_schematic_path, name, uuid, delete_child_file)
```

**Fifth**, update the FastMCP instructions string (line 62) to mention the new tool:

Change:
```python
        "5. add_hierarchical_sheet — links sub-sheets to root with pins"
```
To:
```python
        "5. add_hierarchical_sheet — links sub-sheets to root with pins\n"
        "6. remove_hierarchical_sheet — removes a sheet block from parent"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_project_tools.py::TestRemoveHierarchicalSheet::test_remove_by_uuid tests/test_project_tools.py::TestRemoveHierarchicalSheet::test_remove_by_name_unique -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/project.py tests/test_project_tools.py
git commit -m "feat: add remove_hierarchical_sheet tool with basic tests"
```

---

### Task 2: Write and pass disambiguation and error tests

**Files:**
- Modify: `tests/test_project_tools.py` (add more tests to `TestRemoveHierarchicalSheet`)

- [ ] **Step 1: Write the failing tests**

Add these tests to the `TestRemoveHierarchicalSheet` class:

```python
    def test_ambiguous_name_error(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        # Add two sheets with the same name
        self._add_sheet(parent, child, name="Power")
        self._add_sheet(parent, child, name="Power")

        result = project.remove_hierarchical_sheet(
            name="Power",
            parent_schematic_path=str(parent),
        )
        assert "Multiple sheets" in result
        assert "uuid=" in result
        assert "disambiguate" in result

        # Verify neither was removed
        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 2

    def test_remove_by_name_and_uuid(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        uuid1 = self._add_sheet(parent, child, name="Power")
        self._add_sheet(parent, child, name="Power")

        result = project.remove_hierarchical_sheet(
            name="Power",
            uuid=uuid1,
            parent_schematic_path=str(parent),
        )
        assert "Removed" in result

        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 1
        assert sch.sheets[0].uuid != uuid1

    def test_name_uuid_mismatch_error(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        sheet_uuid = self._add_sheet(parent, child, name="Power")

        result = project.remove_hierarchical_sheet(
            name="WrongName",
            uuid=sheet_uuid,
            parent_schematic_path=str(parent),
        )
        assert "found but its name is" in result
        assert "'Power'" in result
        assert "'WrongName'" in result

        # Verify sheet was NOT removed
        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 1

    def test_no_match_error(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)

        result = project.remove_hierarchical_sheet(
            name="NonExistent",
            parent_schematic_path=str(parent),
        )
        assert "No hierarchical sheet found" in result

    def test_no_parameters_error(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)

        result = project.remove_hierarchical_sheet(
            parent_schematic_path=str(parent),
        )
        assert "Provide at least one of" in result
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_project_tools.py::TestRemoveHierarchicalSheet -v`
Expected: All 7 tests PASS (the implementation from Task 1 should already handle these cases)

- [ ] **Step 3: Commit**

```bash
git add tests/test_project_tools.py
git commit -m "test: add disambiguation and error tests for remove_hierarchical_sheet"
```

---

## Chunk 2: Child File Deletion Tests

### Task 3: Write and pass child file deletion tests

**Files:**
- Modify: `tests/test_project_tools.py` (add more tests to `TestRemoveHierarchicalSheet`)

- [ ] **Step 1: Write the tests**

Add these tests to the `TestRemoveHierarchicalSheet` class:

```python
    def test_delete_child_file_no_other_refs(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        self._add_sheet(parent, child, name="Power")

        assert child.exists()
        result = project.remove_hierarchical_sheet(
            name="Power",
            delete_child_file=True,
            parent_schematic_path=str(parent),
        )
        assert "Removed" in result
        assert "Deleted child file" in result
        assert not child.exists()

    def test_delete_child_file_still_referenced(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        # Two sheet blocks pointing to the same child file
        uuid1 = self._add_sheet(parent, child, name="Power1")
        self._add_sheet(parent, child, name="Power2")

        result = project.remove_hierarchical_sheet(
            uuid=uuid1,
            delete_child_file=True,
            parent_schematic_path=str(parent),
        )
        assert "Removed" in result
        assert "Kept child file" in result
        assert "still referenced" in result
        assert child.exists()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_project_tools.py::TestRemoveHierarchicalSheet -v`
Expected: All 9 tests PASS

- [ ] **Step 3: Run full test suite to check for regressions**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_project_tools.py -v`
Expected: All existing tests PASS

- [ ] **Step 4: Run lint**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && ruff check mcp_server_kicad/project.py tests/test_project_tools.py && ruff format --check mcp_server_kicad/project.py tests/test_project_tools.py && pyright mcp_server_kicad/project.py`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add tests/test_project_tools.py
git commit -m "test: add child file deletion tests for remove_hierarchical_sheet"
```

---

### Task 4: Add tool annotation test and verify registration

**Files:**
- Modify: `tests/test_tool_annotations.py` (add `test_project_destructive` — the project module has never had a destructive tool before)

- [ ] **Step 1: Add `test_project_destructive` to `tests/test_tool_annotations.py`**

After the `test_project_export` function (line ~204), add:

```python
@pytest.mark.parametrize("tool_name", ["remove_hierarchical_sheet"])
def test_project_destructive(tool_name):
    assert _get_annotations(project, tool_name) == _DESTRUCTIVE
```

- [ ] **Step 2: Run tool annotation tests**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest tests/test_tool_annotations.py -v`
Expected: All PASS including the new `test_project_destructive`

- [ ] **Step 3: Run full test suite**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && python -m pytest -x -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_tool_annotations.py
git commit -m "test: add project destructive annotation test for remove_hierarchical_sheet"
```
