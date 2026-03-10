# Project Scaffolding Tools Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 5 project scaffolding tools to the KiCad schematic MCP server so Claude can create KiCad projects from scratch without writing raw s-expressions.

**Architecture:** New `project.py` module exports a `register_tools(mcp)` function that decorates 5 tools onto the schematic server's FastMCP instance. Uses kiutils for schematic/symbol-lib creation; writes raw text for `.kicad_pro`, `.kicad_prl`, and `sym-lib-table` (kiutils doesn't handle those formats).

**Tech Stack:** Python 3.10+, kiutils >=1.4, mcp (FastMCP), pytest

**Spec:** `docs/superpowers/specs/2026-03-09-project-scaffolding-tools-design.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `mcp_server_kicad/_shared.py` | Move `_snap_grid` and `_GRID_MM` here (currently in schematic.py) |
| `mcp_server_kicad/project.py` | 5 tool implementations + `register_tools(mcp)` entry point |
| `mcp_server_kicad/schematic.py` | Import `_snap_grid` from `_shared` instead of defining locally; import and call `register_tools(mcp)` |
| `tests/test_project_tools.py` | All tests for the 5 tools |

---

## Chunk 1: Scaffolding and Simple File Creation Tools

### Task 0: Move `_snap_grid` to `_shared.py`

`_snap_grid` and `_GRID_MM` are currently defined in `schematic.py` but needed by `project.py`. Move them to `_shared.py` to avoid circular imports.

**Files:**
- Modify: `mcp_server_kicad/_shared.py`
- Modify: `mcp_server_kicad/schematic.py`

- [ ] **Step 1: Add `_snap_grid` and `_GRID_MM` to `_shared.py`**

Add after the `_default_stroke` function in `mcp_server_kicad/_shared.py`:

```python
# Default KiCad grid spacing in mm (50 mils).
_GRID_MM = 1.27


def _snap_grid(val: float, grid: float = _GRID_MM) -> float:
    """Snap *val* to the nearest multiple of *grid*."""
    return round(round(val / grid) * grid, 4)
```

Add `_GRID_MM` and `_snap_grid` to the `__all__` list.

- [ ] **Step 2: Update schematic.py to import from _shared**

In `mcp_server_kicad/schematic.py`, add `_GRID_MM` and `_snap_grid` to the import from `_shared`. Remove the local definitions of `_GRID_MM` and `_snap_grid` (lines 69-75).

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/ -x -v`
Expected: ALL PASSED

- [ ] **Step 4: Commit**

```bash
cd /home/sc17/PycharmProjects/mcp-server-kicad && git add mcp_server_kicad/_shared.py mcp_server_kicad/schematic.py && git commit -m "refactor: move _snap_grid to _shared for cross-module use"
```

---

### Task 1: Create project.py module skeleton and `create_project` tool

**Files:**
- Create: `mcp_server_kicad/project.py`
- Create: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing tests for `create_project`**

```python
"""Tests for project scaffolding tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server_kicad import project


class TestCreateProject:
    def test_creates_pro_and_prl(self, tmp_path: Path):
        result = project.create_project(directory=str(tmp_path / "myproj"), name="myproj")
        assert "myproj.kicad_pro" in result

        pro = tmp_path / "myproj" / "myproj.kicad_pro"
        prl = tmp_path / "myproj" / "myproj.kicad_prl"
        assert pro.exists()
        assert prl.exists()

        pro_data = json.loads(pro.read_text())
        assert pro_data["meta"]["filename"] == "myproj.kicad_pro"
        assert pro_data["meta"]["version"] == 1

        prl_data = json.loads(prl.read_text())
        assert prl_data["meta"]["filename"] == "myproj.kicad_prl"
        assert prl_data["meta"]["version"] == 3

    def test_creates_directory_if_missing(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "proj"
        project.create_project(directory=str(target), name="test")
        assert (target / "test.kicad_pro").exists()

    def test_errors_if_pro_exists(self, tmp_path: Path):
        (tmp_path / "dup.kicad_pro").write_text("{}")
        result = project.create_project(directory=str(tmp_path), name="dup")
        assert "already exists" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/test_project_tools.py -x -v`
Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Implement `create_project` in project.py**

```python
"""KiCad project scaffolding tools.

Tools for creating KiCad project files, schematics, symbol libraries,
sym-lib-tables, and hierarchical sheets from scratch. Registered on the
schematic server via register_tools().
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import _gen_uuid, _load_sch, _snap_grid


# KiCad 9 file format constants
_KICAD_SCH_VERSION = 20250114
_KICAD_SCH_GENERATOR = "eeschema"
_KICAD_SYM_VERSION = "20231120"


def _create_project(directory: str, name: str) -> str:
    """Create a KiCad 9 project (.kicad_pro + .kicad_prl).

    Args:
        directory: Directory to create the project in (created if missing)
        name: Project name (used for filenames)
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)

    pro_path = d / f"{name}.kicad_pro"
    if pro_path.exists():
        return f"Error: {pro_path} already exists."

    pro_data = {"meta": {"filename": f"{name}.kicad_pro", "version": 1}}
    pro_path.write_text(json.dumps(pro_data, indent=2) + "\n")

    prl_data = {"meta": {"filename": f"{name}.kicad_prl", "version": 3}}
    prl_path = d / f"{name}.kicad_prl"
    prl_path.write_text(json.dumps(prl_data, indent=2) + "\n")

    return f"Created project at {pro_path}"


def register_tools(mcp: FastMCP) -> None:
    """Register all project scaffolding tools on the given FastMCP instance."""

    @mcp.tool()
    def create_project(directory: str, name: str) -> str:
        """Create a KiCad 9 project (.kicad_pro + .kicad_prl).

        Args:
            directory: Directory to create the project in (created if missing)
            name: Project name (used for filenames)
        """
        return _create_project(directory, name)
```

Note: We expose each function both as `_create_project` (internal) and `create_project` (public alias for tests). Add this after the function definition:

```python
# Public aliases — tests call these directly without going through MCP
create_project = _create_project
```

Do the same for all 5 tools as they are added: `create_schematic = _create_schematic`, `create_symbol_library = _create_symbol_library`, `create_sym_lib_table = _create_sym_lib_table`, `add_hierarchical_sheet = _add_hierarchical_sheet`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/test_project_tools.py::TestCreateProject -x -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
cd /home/sc17/PycharmProjects/mcp-server-kicad && git add mcp_server_kicad/project.py tests/test_project_tools.py && git commit -m "feat: add create_project tool"
```

---

### Task 2: Add `create_schematic` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Modify: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_project_tools.py`:

```python
from kiutils.schematic import Schematic


class TestCreateSchematic:
    def test_creates_valid_schematic(self, tmp_path: Path):
        sch_path = str(tmp_path / "test.kicad_sch")
        result = project.create_schematic(schematic_path=sch_path)
        assert "test.kicad_sch" in result

        sch = Schematic.from_file(sch_path)
        assert sch.version == 20250114
        assert sch.generator == "eeschema"
        assert sch.uuid is not None
        assert sch.schematicSymbols == []

    def test_errors_if_exists(self, tmp_path: Path):
        sch_path = tmp_path / "dup.kicad_sch"
        sch_path.write_text("")
        result = project.create_schematic(schematic_path=str(sch_path))
        assert "already exists" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/test_project_tools.py::TestCreateSchematic -x -v`
Expected: FAIL

- [ ] **Step 3: Implement `create_schematic`**

Add to `project.py` (module-level function + register in `register_tools`):

```python
from kiutils.schematic import Schematic


def _create_schematic(schematic_path: str) -> str:
    """Create a valid empty KiCad 9 schematic file.

    Args:
        schematic_path: Path for the new .kicad_sch file
    """
    p = Path(schematic_path)
    if p.exists():
        return f"Error: {p} already exists."

    p.parent.mkdir(parents=True, exist_ok=True)

    sch = Schematic.create_new()
    sch.version = _KICAD_SCH_VERSION
    sch.generator = _KICAD_SCH_GENERATOR
    sch.uuid = _gen_uuid()
    sch.filePath = str(p)
    sch.to_file()
    return f"Created schematic at {p}"
```

In `register_tools`, add:

```python
    @mcp.tool()
    def create_schematic(schematic_path: str) -> str:
        """Create a valid empty KiCad 9 schematic file.

        Args:
            schematic_path: Path for the new .kicad_sch file
        """
        return _create_schematic(schematic_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/test_project_tools.py::TestCreateSchematic -x -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
cd /home/sc17/PycharmProjects/mcp-server-kicad && git add mcp_server_kicad/project.py tests/test_project_tools.py && git commit -m "feat: add create_schematic tool"
```

---

### Task 3: Add `create_symbol_library` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Modify: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_project_tools.py`:

```python
from kiutils.symbol import SymbolLib


class TestCreateSymbolLibrary:
    def test_creates_valid_sym_lib(self, tmp_path: Path):
        lib_path = str(tmp_path / "custom.kicad_sym")
        result = project.create_symbol_library(symbol_lib_path=lib_path)
        assert "custom.kicad_sym" in result

        lib = SymbolLib.from_file(lib_path)
        assert lib.version == "20231120"
        assert lib.generator == "kicad_symbol_editor"
        assert lib.symbols == []

    def test_errors_if_exists(self, tmp_path: Path):
        lib_path = tmp_path / "dup.kicad_sym"
        lib_path.write_text("")
        result = project.create_symbol_library(symbol_lib_path=str(lib_path))
        assert "already exists" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/test_project_tools.py::TestCreateSymbolLibrary -x -v`
Expected: FAIL

- [ ] **Step 3: Implement `create_symbol_library`**

Add to `project.py`:

```python
from kiutils.symbol import SymbolLib


def _create_symbol_library(symbol_lib_path: str) -> str:
    """Create a valid empty KiCad 9 symbol library.

    Args:
        symbol_lib_path: Path for the new .kicad_sym file
    """
    p = Path(symbol_lib_path)
    if p.exists():
        return f"Error: {p} already exists."

    p.parent.mkdir(parents=True, exist_ok=True)

    lib = SymbolLib(version=_KICAD_SYM_VERSION, generator="kicad_symbol_editor")
    lib.filePath = str(p)
    lib.to_file()
    return f"Created symbol library at {p}"
```

Register in `register_tools`:

```python
    @mcp.tool()
    def create_symbol_library(symbol_lib_path: str) -> str:
        """Create a valid empty KiCad 9 symbol library.

        Args:
            symbol_lib_path: Path for the new .kicad_sym file
        """
        return _create_symbol_library(symbol_lib_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/test_project_tools.py::TestCreateSymbolLibrary -x -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
cd /home/sc17/PycharmProjects/mcp-server-kicad && git add mcp_server_kicad/project.py tests/test_project_tools.py && git commit -m "feat: add create_symbol_library tool"
```

---

### Task 4: Add `create_sym_lib_table` tool

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Modify: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_project_tools.py`:

```python
class TestCreateSymLibTable:
    def test_creates_table_with_entries(self, tmp_path: Path):
        entries = [
            {"name": "skrimp", "uri": "${KIPRJMOD}/skrimp.kicad_sym"},
            {"name": "power", "uri": "${KICAD8_SYMBOL_DIR}/power.kicad_sym"},
        ]
        result = project.create_sym_lib_table(directory=str(tmp_path), entries=entries)
        assert "2 entries" in result

        content = (tmp_path / "sym-lib-table").read_text()
        assert "(sym_lib_table" in content
        assert '(name "skrimp")' in content
        assert '(uri "${KIPRJMOD}/skrimp.kicad_sym")' in content
        assert '(name "power")' in content

    def test_creates_empty_table(self, tmp_path: Path):
        result = project.create_sym_lib_table(directory=str(tmp_path), entries=[])
        assert "0 entries" in result
        content = (tmp_path / "sym-lib-table").read_text()
        assert "(sym_lib_table" in content

    def test_overwrites_existing(self, tmp_path: Path):
        (tmp_path / "sym-lib-table").write_text("old content")
        entries = [{"name": "new", "uri": "new.kicad_sym"}]
        result = project.create_sym_lib_table(directory=str(tmp_path), entries=entries)
        assert "1 entries" in result
        content = (tmp_path / "sym-lib-table").read_text()
        assert '(name "new")' in content
        assert "old content" not in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/test_project_tools.py::TestCreateSymLibTable -x -v`
Expected: FAIL

- [ ] **Step 3: Implement `create_sym_lib_table`**

Add to `project.py`:

```python
def _create_sym_lib_table(directory: str, entries: list[dict]) -> str:
    """Create a sym-lib-table file.

    Args:
        directory: Directory to write sym-lib-table in
        entries: List of dicts with 'name' and 'uri' keys
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)

    lines = ["(sym_lib_table", "  (version 7)"]
    for entry in entries:
        name = entry["name"]
        uri = entry["uri"]
        lines.append(
            f'  (lib (name "{name}")(type "KiCad")(uri "{uri}")(options "")(descr ""))'
        )
    lines.append(")")

    table_path = d / "sym-lib-table"
    table_path.write_text("\n".join(lines) + "\n")
    return f"Created sym-lib-table with {len(entries)} entries at {table_path}"
```

Register in `register_tools`:

```python
    @mcp.tool()
    def create_sym_lib_table(directory: str, entries: list[dict]) -> str:
        """Create a sym-lib-table file in the given directory.

        Each entry dict needs 'name' and 'uri' keys.
        Overwrites existing sym-lib-table if present.

        Args:
            directory: Directory to write sym-lib-table in
            entries: List of dicts with 'name' and 'uri' keys
        """
        return _create_sym_lib_table(directory, entries)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/test_project_tools.py::TestCreateSymLibTable -x -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
cd /home/sc17/PycharmProjects/mcp-server-kicad && git add mcp_server_kicad/project.py tests/test_project_tools.py && git commit -m "feat: add create_sym_lib_table tool"
```

---

## Chunk 2: Hierarchical Sheet Tool and Integration

### Task 5: Add `add_hierarchical_sheet` tool

This is the most complex tool. It modifies the parent schematic (adds a `(sheet ...)` block with `(pin ...)` entries) AND modifies the sub-schematic (adds matching `(hierarchical_label ...)` entries).

**kiutils types needed:**
- `HierarchicalSheet` — the sheet block in the parent
- `HierarchicalPin` — each pin on the sheet block
- `HierarchicalLabel` — matching label in the sub-schematic
- `HierarchicalSheetProjectInstance` / `HierarchicalSheetProjectPath` — instances block

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Modify: `tests/test_project_tools.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_project_tools.py`:

```python
from kiutils.schematic import Schematic


class TestAddHierarchicalSheet:
    def _make_parent_and_child(self, tmp_path: Path) -> tuple[Path, Path]:
        """Helper: create empty parent + child schematics."""
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        return Path(parent), Path(child)

    def test_adds_sheet_to_parent(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        pins = [
            {"name": "VIN", "direction": "input"},
            {"name": "VOUT", "direction": "output"},
            {"name": "GND", "direction": "bidirectional"},
        ]
        result = project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Power",
            sheet_file=str(child),
            pins=pins,
        )
        assert "Power" in result
        assert "3 pins" in result

        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 1
        sheet = sch.sheets[0]
        assert sheet.sheetName.value == "Power"
        assert sheet.fileName.value == "child.kicad_sch"
        assert len(sheet.pins) == 3

    def test_adds_labels_to_child(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        pins = [
            {"name": "VIN", "direction": "input"},
            {"name": "VOUT", "direction": "output"},
        ]
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Power",
            sheet_file=str(child),
            pins=pins,
        )
        child_sch = Schematic.from_file(str(child))
        assert len(child_sch.hierarchicalLabels) == 2
        label_names = {hl.text for hl in child_sch.hierarchicalLabels}
        assert label_names == {"VIN", "VOUT"}

    def test_pin_directions_match(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        pins = [{"name": "SIG", "direction": "bidirectional"}]
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=pins,
        )
        sch = Schematic.from_file(str(parent))
        assert sch.sheets[0].pins[0].connectionType == "bidirectional"

        child_sch = Schematic.from_file(str(child))
        assert child_sch.hierarchicalLabels[0].shape == "bidirectional"

    def test_errors_if_child_missing(self, tmp_path: Path):
        parent_path = str(tmp_path / "root.kicad_sch")
        project.create_schematic(schematic_path=parent_path)
        result = project.add_hierarchical_sheet(
            parent_schematic_path=parent_path,
            sheet_name="Missing",
            sheet_file=str(tmp_path / "nonexistent.kicad_sch"),
            pins=[],
        )
        assert "not found" in result or "does not exist" in result

    def test_custom_position(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[{"name": "A", "direction": "input"}],
            x=50.8,
            y=76.2,
        )
        sch = Schematic.from_file(str(parent))
        sheet = sch.sheets[0]
        assert sheet.position.X == 50.8
        assert sheet.position.Y == 76.2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/test_project_tools.py::TestAddHierarchicalSheet -x -v`
Expected: FAIL

- [ ] **Step 3: Implement `add_hierarchical_sheet`**

Add imports to top of `project.py`:

```python
from kiutils.items.schitems import (
    HierarchicalLabel,
    HierarchicalPin,
    HierarchicalSheet,
    HierarchicalSheetProjectInstance,
    HierarchicalSheetProjectPath,
)
from kiutils.items.common import ColorRGBA, Effects, Font, Position, Property, Stroke
```

Add module-level function:

```python
def _add_hierarchical_sheet(
    parent_schematic_path: str,
    sheet_name: str,
    sheet_file: str,
    pins: list[dict],
    x: float = 25.4,
    y: float = 25.4,
) -> str:
    """Add a hierarchical sheet to a parent schematic with matching labels in the child.

    Args:
        parent_schematic_path: Path to parent .kicad_sch
        sheet_name: Display name for the sheet block
        sheet_file: Path to the child .kicad_sch (must exist)
        pins: List of dicts with 'name' and 'direction' keys
              (direction: input, output, bidirectional, tri_state, passive)
        x: X position of sheet block in parent
        y: Y position of sheet block in parent
    """
    child_path = Path(sheet_file)
    if not child_path.exists():
        return f"Error: {child_path} does not exist. Create it with create_schematic first."

    parent_sch = _load_sch(parent_schematic_path)
    x, y = _snap_grid(x), _snap_grid(y)

    # Sheet dimensions: fixed width, height scales with pin count
    sheet_width = 25.4
    pin_spacing = 2.54
    sheet_height = max(10.16, (len(pins) + 1) * pin_spacing)

    # Build sheet block
    sheet = HierarchicalSheet()
    sheet.position = Position(X=x, Y=y)
    sheet.width = sheet_width
    sheet.height = sheet_height
    sheet.stroke = Stroke(width=0.1, type="default")
    sheet.fill = ColorRGBA()  # default transparent
    sheet.uuid = _gen_uuid()
    sheet.fieldsAutoplaced = True

    # Sheet name and filename — use dedicated fields, not properties list
    sheet.sheetName = Property(
        key="Sheetname",
        value=sheet_name,
        id=0,
        effects=Effects(font=Font(height=1.27, width=1.27)),
        position=Position(X=x, Y=y - 1.27),
    )
    sheet.fileName = Property(
        key="Sheetfile",
        value=child_path.name,
        id=1,
        effects=Effects(font=Font(height=1.27, width=1.27)),
        position=Position(X=x, Y=y + sheet_height + 1.27),
    )

    # Build pins on the sheet block (positioned along left edge)
    sheet_pins = []
    for i, pin_def in enumerate(pins):
        pin = HierarchicalPin()
        pin.name = pin_def["name"]
        pin.connectionType = pin_def["direction"]
        pin.position = Position(
            X=x,
            Y=_snap_grid(y + (i + 1) * pin_spacing),
            angle=180,
        )
        pin.effects = Effects(font=Font(height=1.27, width=1.27))
        pin.uuid = _gen_uuid()
        sheet_pins.append(pin)
    sheet.pins = sheet_pins

    # Add instances block for the sheet
    project_name = Path(parent_sch.filePath).stem if parent_sch.filePath else ""
    sheet.instances = [
        HierarchicalSheetProjectInstance(
            name=project_name,
            paths=[
                HierarchicalSheetProjectPath(
                    sheetInstancePath=f"/{parent_sch.uuid}/{sheet.uuid}",
                    page=str(len(parent_sch.sheets) + 2),
                ),
            ],
        ),
    ]

    parent_sch.sheets.append(sheet)
    parent_sch.to_file()

    # Add matching hierarchical labels to child schematic
    child_sch = _load_sch(sheet_file)
    label_x = _snap_grid(25.4)
    for i, pin_def in enumerate(pins):
        label = HierarchicalLabel()
        label.text = pin_def["name"]
        label.shape = pin_def["direction"]
        label.position = Position(
            X=label_x,
            Y=_snap_grid(25.4 + i * 5.08),
            angle=180,
        )
        label.effects = Effects(font=Font(height=1.27, width=1.27))
        label.uuid = _gen_uuid()
        child_sch.hierarchicalLabels.append(label)
    child_sch.to_file()

    return f"Added sheet '{sheet_name}' with {len(pins)} pins to {parent_schematic_path}"
```

Register in `register_tools`:

```python
    @mcp.tool()
    def add_hierarchical_sheet(
        parent_schematic_path: str,
        sheet_name: str,
        sheet_file: str,
        pins: list[dict],
        x: float = 25.4,
        y: float = 25.4,
    ) -> str:
        """Add a hierarchical sheet to a parent schematic with matching labels in the child.

        Creates the sheet block in the parent and corresponding hierarchical
        labels in the child schematic. The child schematic must already exist
        (create it with create_schematic first).

        Args:
            parent_schematic_path: Path to parent .kicad_sch
            sheet_name: Display name for the sheet
            sheet_file: Path to child .kicad_sch (must exist)
            pins: List of dicts with 'name' (str) and 'direction' (str) keys.
                  Direction: input, output, bidirectional, tri_state, passive.
            x: X position of sheet block (default 25.4)
            y: Y position of sheet block (default 25.4)
        """
        return _add_hierarchical_sheet(
            parent_schematic_path, sheet_name, sheet_file, pins, x, y
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/test_project_tools.py::TestAddHierarchicalSheet -x -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
cd /home/sc17/PycharmProjects/mcp-server-kicad && git add mcp_server_kicad/project.py tests/test_project_tools.py && git commit -m "feat: add add_hierarchical_sheet tool"
```

---

### Task 6: Register tools on schematic server and run full test suite

**Files:**
- Modify: `mcp_server_kicad/schematic.py` (add 2 lines)

- [ ] **Step 1: Add registration import to schematic.py**

Add these 2 lines to `mcp_server_kicad/schematic.py` just before the `def main():` line (after the symbol library tools section, before the entry point):

```python
from mcp_server_kicad.project import register_tools as _register_project_tools
_register_project_tools(mcp)
```

- [ ] **Step 2: Run full test suite to ensure nothing breaks**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/ -x -v`
Expected: ALL PASSED (existing tests + new project tool tests)

- [ ] **Step 3: Run ruff linter**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run ruff check mcp_server_kicad/project.py tests/test_project_tools.py`
Expected: no errors (fix any that appear)

- [ ] **Step 4: Run ruff formatter**

Run: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run ruff format mcp_server_kicad/project.py tests/test_project_tools.py`

- [ ] **Step 5: Commit**

```bash
cd /home/sc17/PycharmProjects/mcp-server-kicad && git add mcp_server_kicad/schematic.py mcp_server_kicad/project.py tests/test_project_tools.py && git commit -m "feat: register project scaffolding tools on schematic server"
```

---

## Post-Implementation Notes

After all tasks are complete:

1. The schematic MCP server now exposes 5 additional tools: `create_project`, `create_schematic`, `create_symbol_library`, `create_sym_lib_table`, `add_hierarchical_sheet`
2. No configuration changes needed — tools are auto-registered on the existing `kicad-schematic` server
3. Test with: `cd /home/sc17/PycharmProjects/mcp-server-kicad && uv run pytest tests/ -x -v`
