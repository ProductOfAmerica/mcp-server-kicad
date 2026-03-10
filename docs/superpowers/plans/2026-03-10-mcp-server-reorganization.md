# MCP Server Reorganization + Tool Consolidation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize from 3 MCP servers to 5 domain-aligned servers AND consolidate 82 tools down to 63 per Anthropic best practices.

**Architecture:** Move tools from grab-bag `export.py` into domain servers. Extract symbol/footprint library tools. Give project.py its own FastMCP. Consolidate related tools using format/item_type parameters and batch merges. Delete export.py.

**Tech Stack:** Python, FastMCP (`mcp.server.fastmcp`), kiutils, kicad-cli (subprocess)

**Spec:** `docs/superpowers/specs/2026-03-10-mcp-server-reorganization-design.md`

**CRITICAL: When the plan says "copy verbatim from <file>:<lines>", copy the EXACT function signature, docstring, and body. Do NOT rename parameters or invent new ones. Read the source file first.**

---

## Chunk 1: Create kicad-symbol server

### Task 1: Create symbol.py with 4 tools

**Files:**
- Create: `mcp_server_kicad/symbol.py`
- Modify: `mcp_server_kicad/schematic.py` (remove 2 tools)
- Reference: `mcp_server_kicad/export.py` (copy 2 tools)

- [ ] **Step 1: Create `mcp_server_kicad/symbol.py`**

Read the source files first, then create `symbol.py` with this structure:

```python
"""KiCad symbol library MCP server."""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    SYM_LIB_PATH,
    SymbolLib,
    _run_cli,
    OUTPUT_DIR,
)

mcp = FastMCP(
    "kicad-symbol",
    instructions=(
        "KiCad symbol library tools for browsing, inspecting, exporting,"
        " and upgrading symbol libraries."
    ),
)


# ── Library browsing ──────────────────────────────────────────────

# Copy list_lib_symbols verbatim from schematic.py:1444-1458
# (include @mcp.tool() decorator and exact function signature + body)

# Copy get_symbol_info verbatim from schematic.py:1460-1480
# (include @mcp.tool() decorator and exact function signature + body)


# ── Export & upgrade (wraps kicad-cli) ────────────────────────────

# Copy export_symbol_svg verbatim from export.py:570-594
# (exact signature: def export_symbol_svg(symbol_lib_path: str = SYM_LIB_PATH, output_dir: str = OUTPUT_DIR) -> str:)

# Copy upgrade_symbol_lib verbatim from export.py:627-638
# (exact signature: def upgrade_symbol_lib(symbol_lib_path: str) -> str:)


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for mcp-server-kicad-symbol console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

**IMPORTANT:** Copy function bodies exactly from their source files — including parameter names, default values, docstrings, and body logic. Do NOT rename parameters.

- [ ] **Step 2: Remove `list_lib_symbols` and `get_symbol_info` from `schematic.py`**

Delete lines ~1444-1480 in `schematic.py` (the two `@mcp.tool()` decorated functions). These are the last two tool definitions before the `_register_project_tools(mcp)` call at line 1483.

Do NOT remove `add_lib_symbol` — it writes into the schematic's internal lib_symbols table and stays on the schematic server.

- [ ] **Step 3: Run existing tests to confirm nothing broke**

Run: `uv run pytest tests/test_read_tools.py tests/test_write_tools.py tests/test_routing_tools.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add mcp_server_kicad/symbol.py mcp_server_kicad/schematic.py
git commit -m "feat: create kicad-symbol server with 4 library tools"
```

### Task 2: Update symbol-related tests

**Files:**
- Create: `tests/test_symbol_access_tools.py`
- Modify: `tests/test_lib_access_tools.py` (remove symbol tests, keep footprint tests)

NOTE: Do NOT rename `tests/test_lib_tools.py`. That file tests `add_lib_symbol` which remains on the schematic server.

- [ ] **Step 1: Create `tests/test_symbol_access_tools.py`**

Copy the `TestListLibSymbols` and `TestGetSymbolInfo` classes from `tests/test_lib_access_tools.py`. Update imports to `from mcp_server_kicad import symbol` and change calls from `schematic.list_lib_symbols(...)` to `symbol.list_lib_symbols(...)` etc.

- [ ] **Step 2: Remove symbol tests from `test_lib_access_tools.py`**

Remove `TestListLibSymbols` and `TestGetSymbolInfo`, leaving only footprint tests.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_symbol_access_tools.py tests/test_lib_access_tools.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: reorganize symbol library tests for new symbol server"
```

---

## Chunk 2: Create kicad-footprint server

### Task 3: Create footprint.py with 4 tools

**Files:**
- Create: `mcp_server_kicad/footprint.py`
- Modify: `mcp_server_kicad/pcb.py` (remove 2 tools)
- Reference: `mcp_server_kicad/export.py` (copy 2 tools)

- [ ] **Step 1: Create `mcp_server_kicad/footprint.py`**

```python
"""KiCad footprint library MCP server."""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    FP_LIB_PATH,
    Footprint,
    _run_cli,
    OUTPUT_DIR,
)

mcp = FastMCP(
    "kicad-footprint",
    instructions=(
        "KiCad footprint library tools for browsing, inspecting, exporting,"
        " and upgrading footprint libraries."
    ),
)


# ── Library browsing ──────────────────────────────────────────────

# Copy list_lib_footprints verbatim from pcb.py:406-421
# (exact signature: def list_lib_footprints(pretty_dir: str = FP_LIB_PATH) -> str:)
# Keep the parameter name "pretty_dir" exactly as-is.

# Copy get_footprint_info verbatim from pcb.py:423-439
# (exact signature: def get_footprint_info(footprint_path: str) -> str:)


# ── Export & upgrade (wraps kicad-cli) ────────────────────────────

# Copy export_footprint_svg verbatim from export.py:596-619
# (exact signature: def export_footprint_svg(footprint_path: str, output_dir: str = OUTPUT_DIR) -> str:)

# Copy upgrade_footprint_lib verbatim from export.py:641-652
# (exact signature: def upgrade_footprint_lib(footprint_path: str) -> str:)


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for mcp-server-kicad-footprint console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

Copy function bodies exactly. Do NOT rename parameters.

- [ ] **Step 2: Remove `list_lib_footprints` and `get_footprint_info` from `pcb.py`**

Delete the two `@mcp.tool()` decorated functions at `pcb.py:406-439`.

- [ ] **Step 3: Run existing PCB tests**

Run: `uv run pytest tests/test_pcb_read_tools.py tests/test_pcb_write_tools.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add mcp_server_kicad/footprint.py mcp_server_kicad/pcb.py
git commit -m "feat: create kicad-footprint server with 4 library tools"
```

### Task 4: Update footprint-related tests

**Files:**
- Rename: `tests/test_lib_access_tools.py` → `tests/test_footprint_access_tools.py`

- [ ] **Step 1: Rename and update imports**

```bash
git mv tests/test_lib_access_tools.py tests/test_footprint_access_tools.py
```

Update imports: `from mcp_server_kicad import pcb` → `from mcp_server_kicad import footprint`, update all function calls.

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_footprint_access_tools.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "test: reorganize footprint library tests for new footprint server"
```

---

## Chunk 3: Rework kicad-project server

### Task 5: Give project.py its own FastMCP instance

**Files:**
- Modify: `mcp_server_kicad/project.py`
- Modify: `mcp_server_kicad/schematic.py` (remove _register_project_tools)
- Reference: `mcp_server_kicad/export.py:655-666` (copy run_jobset)

- [ ] **Step 1: Add FastMCP instance to project.py**

Add imports:
```python
from mcp.server.fastmcp import FastMCP
from mcp_server_kicad._shared import _run_cli
```

Add after imports:
```python
mcp = FastMCP(
    "kicad-project",
    instructions=(
        "KiCad project scaffolding, hierarchical sheet management,"
        " jobset execution, and version info."
    ),
)
```

- [ ] **Step 2: Convert register_tools() to direct @mcp.tool() decorators**

Delete the entire `register_tools(mcp: FastMCP)` function (lines 243-311). Replace with direct `@mcp.tool()` decorated functions that delegate to the internal `_create_*` functions:

```python
@mcp.tool()
def create_project(directory: str, name: str) -> str:
    """Create a KiCad 9 project (.kicad_pro + .kicad_prl + .kicad_sch)."""
    return _create_project(directory, name)
```

Do the same for all 5: `create_project`, `create_schematic`, `create_symbol_library`, `create_sym_lib_table`, `add_hierarchical_sheet`.

Keep the public aliases at lines 235-240 for test imports.

- [ ] **Step 3: Add `run_jobset` tool**

Copy `run_jobset` verbatim from `export.py:655-666`. Returns **plain strings**, not JSON.

- [ ] **Step 4: Add `get_version` tool**

```python
@mcp.tool()
def get_version() -> str:
    """Get KiCad version information including build details and library versions."""
    result = _run_cli(["version", "--format", "about"], check=False)
    if result.returncode != 0:
        return json.dumps({"error": result.stderr.strip()})
    return json.dumps({"version_info": result.stdout.strip()})
```

- [ ] **Step 5: Add `main()` entry point**

```python
def main():
    """Entry point for mcp-server-kicad-project console script."""
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Remove `_register_project_tools` from schematic.py**

Remove: `from mcp_server_kicad.project import register_tools as _register_project_tools` (line ~33)
Remove: `_register_project_tools(mcp)` (line ~1483)

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_project_tools.py tests/test_read_tools.py tests/test_write_tools.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add mcp_server_kicad/project.py mcp_server_kicad/schematic.py
git commit -m "feat: give kicad-project server its own FastMCP instance with jobset and version tools"
```

### Task 6: Add tests for new project tools

**Files:**
- Modify: `tests/test_project_tools.py`

- [ ] **Step 1: Write tests**

```python
import shutil

@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")
class TestGetVersion:
    def test_returns_version_info(self):
        result = json.loads(project.get_version())
        assert "version_info" in result or "error" in result

class TestRunJobset:
    def test_missing_jobset_returns_error(self, tmp_path):
        result = project.run_jobset(str(tmp_path / "nonexistent.kicad_jobset"))
        assert "failed" in result.lower() or "error" in result.lower()
```

- [ ] **Step 2: Run and commit**

Run: `uv run pytest tests/test_project_tools.py -v`

```bash
git add tests/test_project_tools.py
git commit -m "test: add tests for get_version and run_jobset project tools"
```

---

## Chunk 4: Consolidate existing schematic tools

### Task 7: Consolidate schematic list tools → list_schematic_items

**Files:**
- Modify: `mcp_server_kicad/schematic.py`
- Modify: `tests/test_read_tools.py`

- [ ] **Step 1: Read existing list tool implementations**

Read `schematic.py` and locate these 4 functions to understand their logic:
- `list_components` (~line 162)
- `list_labels` (~line 175)
- `list_wires` (~line 185)
- `list_global_labels` (~line 279)

Each takes `schematic_path: str = SCH_PATH` and returns JSON.

- [ ] **Step 2: Write failing test for `list_schematic_items`**

Add to `tests/test_read_tools.py`:

```python
class TestListSchematicItems:
    def test_list_components(self, scratch_schematic):
        result = json.loads(schematic.list_schematic_items("components", str(scratch_schematic)))
        assert isinstance(result, list)

    def test_list_labels(self, scratch_schematic):
        result = json.loads(schematic.list_schematic_items("labels", str(scratch_schematic)))
        assert isinstance(result, list)

    def test_list_wires(self, scratch_schematic):
        result = json.loads(schematic.list_schematic_items("wires", str(scratch_schematic)))
        assert isinstance(result, list)

    def test_list_global_labels(self, scratch_schematic):
        result = json.loads(schematic.list_schematic_items("global_labels", str(scratch_schematic)))
        assert isinstance(result, list)

    def test_invalid_item_type(self, scratch_schematic):
        result = json.loads(schematic.list_schematic_items("invalid", str(scratch_schematic)))
        assert "error" in result
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_read_tools.py::TestListSchematicItems -v`
Expected: FAIL

- [ ] **Step 4: Implement `list_schematic_items`**

Replace the 4 individual `@mcp.tool()` list functions with one consolidated function. Preserve the existing body logic for each item_type branch:

```python
@mcp.tool()
def list_schematic_items(item_type: str, schematic_path: str = SCH_PATH) -> str:
    """List schematic items by type.

    Args:
        item_type: One of "components", "labels", "wires", "global_labels"
        schematic_path: Path to .kicad_sch file
    """
    if item_type == "components":
        # ... existing list_components body ...
    elif item_type == "labels":
        # ... existing list_labels body ...
    elif item_type == "wires":
        # ... existing list_wires body ...
    elif item_type == "global_labels":
        # ... existing list_global_labels body ...
    else:
        return json.dumps({"error": f"Unknown item_type: {item_type}. Use: components, labels, wires, global_labels"})
```

Delete the 4 old `@mcp.tool()` functions (`list_components`, `list_labels`, `list_wires`, `list_global_labels`).

- [ ] **Step 5: Update existing tests in `test_read_tools.py`**

Find any existing test classes for the old individual list tools (`TestListComponents`, `TestListLabels`, `TestListWires`, `TestListGlobalLabels`) and update them to call `list_schematic_items("components", ...)` etc., or remove them if the new `TestListSchematicItems` covers the same cases.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_read_tools.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_read_tools.py
git commit -m "feat: consolidate 4 schematic list tools into list_schematic_items"
```

### Task 8: Consolidate batch tools (remove singular variants)

**Files:**
- Modify: `mcp_server_kicad/schematic.py`
- Modify: `tests/test_write_tools.py`
- Modify: `tests/test_routing_tools.py`

- [ ] **Step 1: Remove `add_wire` (keep `add_wires`)**

In `schematic.py`, delete the `add_wire` `@mcp.tool()` function. `add_wires` already accepts a list of wire dicts. Callers pass a single-element list: `add_wires([{"x1": 0, "y1": 0, "x2": 1, "y2": 1}])`.

- [ ] **Step 2: Remove `add_junction` (keep `add_junctions`)**

Delete the `add_junction` `@mcp.tool()` function. `add_junctions` already accepts a list of point dicts. Single junction: `add_junctions([{"x": 0, "y": 0}])`.

- [ ] **Step 3: Remove `wire_pin_to_label` (keep `wire_pins_to_net`)**

Delete the `wire_pin_to_label` `@mcp.tool()` function. `wire_pins_to_net` handles single pins: `wire_pins_to_net(pins=[{"reference": "U1", "pin_name": "VCC"}], label_text="+3V3")`.

- [ ] **Step 4: Update tests**

In `tests/test_write_tools.py`, find tests calling `add_wire(x1, y1, x2, y2, ...)` and update them to call `add_wires([{"x1": ..., "y1": ..., "x2": ..., "y2": ...}], ...)`. Same for `add_junction` → `add_junctions`.

In `tests/test_routing_tools.py`, find tests calling `wire_pin_to_label(...)` and update to `wire_pins_to_net(pins=[{...}], ...)`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_write_tools.py tests/test_routing_tools.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_write_tools.py tests/test_routing_tools.py
git commit -m "feat: remove singular add_wire, add_junction, wire_pin_to_label (batch variants are the canonical API)"
```

### Task 9: Consolidate property tools

**Files:**
- Modify: `mcp_server_kicad/schematic.py`
- Modify: `tests/test_write_tools.py`

- [ ] **Step 1: Remove `edit_component_value` and `set_component_footprint`**

Delete both `@mcp.tool()` functions from `schematic.py`. The existing `set_component_property(reference, key, value, schematic_path)` already handles all cases:
- `set_component_property(ref, "Value", "10k")` — replaces `edit_component_value(ref, value="10k")`
- `set_component_property(ref, "Reference", "R2")` — replaces `edit_component_value(ref, new_reference="R2")`
- `set_component_property(ref, "Footprint", "R_0402")` — replaces `set_component_footprint(ref, "R_0402")`

- [ ] **Step 2: Verify `set_component_property` handles all property keys**

Read the body of `set_component_property` in `schematic.py` to confirm it can set Value, Reference, and Footprint properties. If the implementation uses `component.properties` dict, these standard keys should work. If it needs special handling for Reference renaming (e.g., updating the `reference` field on the component object), add that logic.

- [ ] **Step 3: Update tests**

In `tests/test_write_tools.py`, find tests for `edit_component_value` and `set_component_footprint` and rewrite them to use `set_component_property`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_write_tools.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_write_tools.py
git commit -m "feat: remove edit_component_value and set_component_footprint (use set_component_property)"
```

---

## Chunk 5: Absorb + consolidate schematic exports

### Task 10: Move ERC tools and consolidate schematic exports into schematic.py

**Files:**
- Modify: `mcp_server_kicad/schematic.py`
- Modify: `mcp_server_kicad/export.py`

- [ ] **Step 1: Add new imports to schematic.py**

```python
import json
import os
from mcp_server_kicad._shared import _run_cli, _file_meta, OUTPUT_DIR
```

Note: `schematic.py` has a local `import json as _json` inside `get_net_connections()`. After adding top-level `import json`, clean up that local import to use `json` directly.

- [ ] **Step 2: Copy ERC helpers and tools**

Copy verbatim from `export.py`:
- `_annotate_erc_violations()` (lines 29-43)
- `_parse_unconnected_pins()` (lines 46-69)
- `list_unconnected_pins()` (line 72) — preserves inline `import shutil`
- `run_erc()` (line 116)

Place after existing private helpers, before the read tools.

- [ ] **Step 3: Create consolidated `export_schematic` tool**

Instead of copying 3 individual export tools, create one consolidated tool:

```python
@mcp.tool()
def export_schematic(
    format: str = "pdf",
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
) -> str:
    """Export schematic to PDF, SVG, or DXF format.

    Args:
        format: Output format - "pdf", "svg", or "dxf"
        schematic_path: Path to .kicad_sch file
        output_dir: Directory for output files
    """
    fmt = format.lower()
    if fmt not in ("pdf", "svg", "dxf"):
        return json.dumps({"error": f"Unknown format: {format}. Use: pdf, svg, dxf"})

    stem = Path(schematic_path).stem
    if fmt == "pdf":
        out = str(Path(output_dir) / f"{stem}.pdf")
        args = ["sch", "export", "pdf", schematic_path, "-o", out]
    elif fmt == "svg":
        out_dir = str(Path(output_dir) / f"{stem}_svg")
        os.makedirs(out_dir, exist_ok=True)
        args = ["sch", "export", "svg", schematic_path, "-o", out_dir]
    else:  # dxf
        out = str(Path(output_dir) / f"{stem}.dxf")
        args = ["sch", "export", "dxf", schematic_path, "-o", out]

    result = _run_cli(args, check=False)
    if result.returncode != 0:
        return json.dumps({"error": result.stderr.strip()})

    if fmt == "svg":
        # SVG exports a directory of files
        return json.dumps({"output_dir": out_dir, "format": fmt})
    return json.dumps({**_file_meta(out), "format": fmt})
```

**IMPORTANT:** Read the actual bodies of `export_schematic_pdf`, `export_schematic_svg`, `export_schematic_dxf` in `export.py` FIRST. The implementation above is a template — match the actual CLI args and output handling from each original function.

- [ ] **Step 4: Create `export_netlist` tool (renamed from `export_schematic_netlist`)**

Copy the body of `export_schematic_netlist` from `export.py:231` but rename the function to `export_netlist`:

```python
@mcp.tool()
def export_netlist(
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
    format: str = "kicadxml",
) -> str:
    """Export schematic netlist in KiCad XML or KiCad net format."""
    # ... copy exact body from export_schematic_netlist ...
```

- [ ] **Step 5: Copy `export_bom` verbatim**

Copy `export_bom` verbatim from `export.py:253`. No consolidation needed.

- [ ] **Step 6: Remove moved tools from export.py**

Remove from `export.py`:
- `_annotate_erc_violations`, `_parse_unconnected_pins`
- `list_unconnected_pins`, `run_erc`
- `export_schematic_pdf`, `export_schematic_svg`, `export_schematic_dxf`
- `export_schematic_netlist`, `export_bom`

Also remove unused imports (`SCH_PATH`, `SYM_LIB_PATH` if no remaining tools use them).

- [ ] **Step 7: Update schematic.py FastMCP instructions**

```python
mcp = FastMCP(
    "kicad-schematic",
    instructions=(
        "KiCad schematic manipulation, ERC analysis, and schematic export tools."
        " Use wire_pins_to_net and connect_pins for efficient wiring instead of"
        " manually computing coordinates with get_pin_positions + add_wires."
    ),
)
```

- [ ] **Step 8: Run tests and commit**

Run: `uv run pytest tests/test_read_tools.py tests/test_write_tools.py tests/test_routing_tools.py -v`

```bash
git add mcp_server_kicad/schematic.py mcp_server_kicad/export.py
git commit -m "feat: absorb ERC tools and consolidated schematic exports into kicad-schematic"
```

### Task 11: Update schematic export and ERC tests

**Files:**
- Modify: `tests/test_cli_sch_export.py`
- Modify: `tests/test_cli_analysis.py`

- [ ] **Step 1: Update `test_cli_sch_export.py`**

Change `from mcp_server_kicad import export` to `from mcp_server_kicad import schematic`.

Update ALL function calls — both public tools and private helpers:
- `export.export_schematic_pdf(...)` → `schematic.export_schematic(format="pdf", ...)`
- `export.export_schematic_svg(...)` → `schematic.export_schematic(format="svg", ...)`
- `export.export_schematic_dxf(...)` → `schematic.export_schematic(format="dxf", ...)`
- `export.export_schematic_netlist(...)` → `schematic.export_netlist(...)`
- `export.export_bom(...)` → `schematic.export_bom(...)`
- `export._annotate_erc_violations(...)` → `schematic._annotate_erc_violations(...)`
- `export._parse_unconnected_pins(...)` → `schematic._parse_unconnected_pins(...)`

- [ ] **Step 2: Update ERC tests in `test_cli_analysis.py`**

Change `TestRunErc` to import from `schematic`. Add import for `pcb` (DRC stays on `export` until chunk 6):

```python
from mcp_server_kicad import schematic
from mcp_server_kicad import export  # still needed for DRC until chunk 6
```

Update `TestRunErc` calls: `export.run_erc(...)` → `schematic.run_erc(...)`.

- [ ] **Step 3: Run tests and commit**

Run: `uv run pytest tests/test_cli_sch_export.py tests/test_cli_analysis.py -v`

```bash
git add tests/test_cli_sch_export.py tests/test_cli_analysis.py
git commit -m "test: update schematic export and ERC test imports for consolidated tools"
```

---

## Chunk 6: Consolidate PCB tools + absorb exports + new tools

### Task 12: Consolidate PCB list tools → list_pcb_items

**Files:**
- Modify: `mcp_server_kicad/pcb.py`
- Modify: `tests/test_pcb_read_tools.py`

- [ ] **Step 1: Read existing PCB list tool implementations**

Read `pcb.py` and locate these 6 functions:
- `list_footprints`, `list_traces`, `list_nets`, `list_zones`, `list_layers`, `list_board_graphic_items`

All take `pcb_path: str = PCB_PATH` and return JSON. (`get_board_info` and `get_footprint_pads` stay separate — different semantics/params.)

- [ ] **Step 2: Write failing test**

Add to `tests/test_pcb_read_tools.py`:

```python
class TestListPcbItems:
    @pytest.mark.parametrize("item_type", ["footprints", "traces", "nets", "zones", "layers", "graphic_items"])
    def test_list_items(self, scratch_pcb, item_type):
        result = json.loads(pcb.list_pcb_items(item_type, str(scratch_pcb)))
        assert isinstance(result, (list, dict))

    def test_invalid_item_type(self, scratch_pcb):
        result = json.loads(pcb.list_pcb_items("invalid", str(scratch_pcb)))
        assert "error" in result
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_pcb_read_tools.py::TestListPcbItems -v`
Expected: FAIL

- [ ] **Step 4: Implement `list_pcb_items`**

Replace the 6 individual `@mcp.tool()` list functions with one consolidated function:

```python
@mcp.tool()
def list_pcb_items(item_type: str, pcb_path: str = PCB_PATH) -> str:
    """List PCB items by type.

    Args:
        item_type: One of "footprints", "traces", "nets", "zones", "layers", "graphic_items"
        pcb_path: Path to .kicad_pcb file
    """
    if item_type == "footprints":
        # ... existing list_footprints body ...
    elif item_type == "traces":
        # ... existing list_traces body ...
    elif item_type == "nets":
        # ... existing list_nets body ...
    elif item_type == "zones":
        # ... existing list_zones body ...
    elif item_type == "layers":
        # ... existing list_layers body ...
    elif item_type == "graphic_items":
        # ... existing list_board_graphic_items body ...
    else:
        return json.dumps({"error": f"Unknown item_type: {item_type}. Use: footprints, traces, nets, zones, layers, graphic_items"})
```

Delete the 6 old functions.

- [ ] **Step 5: Update existing tests**

Update any existing tests for the old functions to call `list_pcb_items("footprints", ...)` etc.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_pcb_read_tools.py -v`

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_read_tools.py
git commit -m "feat: consolidate 6 PCB list tools into list_pcb_items"
```

### Task 13: Absorb + consolidate PCB exports from export.py

**Files:**
- Modify: `mcp_server_kicad/pcb.py`
- Modify: `mcp_server_kicad/export.py`

- [ ] **Step 1: Add new imports to pcb.py**

```python
import json
import os
from mcp_server_kicad._shared import _run_cli, _file_meta, OUTPUT_DIR
```

(`pcb.py` currently has NO `import json` or `import os`.)

- [ ] **Step 2: Copy DRC tool verbatim**

Copy `run_drc()` from `export.py:152-184` into `pcb.py`.

- [ ] **Step 3: Create consolidated `export_pcb` tool**

Consolidates `export_pcb_pdf` + `export_pcb_svg`:

```python
@mcp.tool()
def export_pcb(
    format: str = "pdf",
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    layers: list[str] | None = None,
) -> str:
    """Export PCB to PDF or SVG format.

    Args:
        format: Output format - "pdf" or "svg"
        pcb_path: Path to .kicad_pcb file
        output_dir: Directory for output files
        layers: Optional list of layer names to include
    """
    fmt = format.lower()
    if fmt not in ("pdf", "svg"):
        return json.dumps({"error": f"Unknown format: {format}. Use: pdf, svg"})
    # ... dispatch to appropriate CLI command, matching original export_pcb_pdf/svg logic ...
```

**Read the actual bodies of `export_pcb_pdf` and `export_pcb_svg`** in `export.py` and replicate their CLI arg construction and output handling per format.

- [ ] **Step 4: Create consolidated `export_gerbers` tool (absorbs drill)**

The existing `export_gerbers` function becomes the base. Add `include_drill: bool = True` param:

```python
@mcp.tool()
def export_gerbers(
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    include_drill: bool = True,
) -> str:
    """Export Gerber files for all copper and mask layers, optionally including drill files."""
    # ... existing export_gerbers body ...
    # Then, if include_drill:
    #   ... run the drill export CLI command (from existing export_drill body) ...
```

Copy the existing `export_gerbers` and `export_drill` bodies from `export.py` and combine them.

- [ ] **Step 5: Copy `export_gerber` (single-layer) verbatim**

Copy from `export.py:319`. This stays as a separate tool — different use case (single layer export).

- [ ] **Step 6: Create consolidated `export_3d` tool**

Consolidates `export_step`, `export_stl`, `export_glb`:

```python
@mcp.tool()
def export_3d(
    format: str = "step",
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
) -> str:
    """Export PCB 3D model in STEP, STL, or GLB format.

    Args:
        format: Output format - "step", "stl", or "glb"
    """
    fmt = format.lower()
    if fmt not in ("step", "stl", "glb"):
        return json.dumps({"error": f"Unknown format: {format}. Use: step, stl, glb"})
    # ... dispatch to appropriate CLI command ...
```

Read the original `export_step`, `export_stl`, `export_glb` bodies and replicate.

- [ ] **Step 7: Copy remaining export tools verbatim**

Copy these individually from `export.py` — no consolidation:
- `export_positions()` (line 441)
- `render_3d()` (line 519)

- [ ] **Step 8: Remove moved tools from export.py**

Remove all DRC and PCB export tools. After this, `export.py` should only contain tools already copied to symbol.py, footprint.py, and project.py (dead code, to be deleted in chunk 7).

- [ ] **Step 9: Update pcb.py FastMCP instructions**

```python
mcp = FastMCP(
    "kicad-pcb",
    instructions=(
        "KiCad PCB manipulation, DRC analysis, and PCB export tools"
        " including Gerber, drill, 3D models, and pick-and-place."
    ),
)
```

- [ ] **Step 10: Run tests and commit**

Run: `uv run pytest tests/test_pcb_read_tools.py tests/test_pcb_write_tools.py -v`

```bash
git add mcp_server_kicad/pcb.py mcp_server_kicad/export.py
git commit -m "feat: absorb DRC and consolidated PCB export tools into kicad-pcb"
```

### Task 14: Add new PCB export tools

**Files:**
- Modify: `mcp_server_kicad/pcb.py`
- Create: `tests/test_pcb_dxf_export.py`
- Create: `tests/test_ipc2581_export.py`

- [ ] **Step 1: Write `export_pcb_dxf` test**

```python
"""Tests for PCB DXF export tool."""
import json
import shutil
import pytest
from mcp_server_kicad import pcb

pytestmark = pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")

class TestExportPcbDxf:
    def test_export_runs(self, scratch_pcb, tmp_path):
        result = json.loads(pcb.export_pcb_dxf(pcb_path=str(scratch_pcb), output=str(tmp_path / "board.dxf"), layers="F.Cu"))
        assert "path" in result or "error" in result

    def test_missing_layers_returns_error(self, scratch_pcb, tmp_path):
        result = json.loads(pcb.export_pcb_dxf(pcb_path=str(scratch_pcb), output=str(tmp_path / "board.dxf"), layers=""))
        assert "error" in result
```

- [ ] **Step 2: Implement `export_pcb_dxf`**

```python
@mcp.tool()
def export_pcb_dxf(
    pcb_path: str = PCB_PATH,
    output: str = "",
    layers: str = "",
    output_units: str = "in",
    exclude_refdes: bool = False,
    exclude_value: bool = False,
    use_contours: bool = False,
    include_border_title: bool = False,
) -> str:
    """Export PCB layers to DXF format for mechanical CAD exchange."""
    if not layers:
        return json.dumps({"error": "layers parameter is required"})
    out = output or str(Path(OUTPUT_DIR) / (Path(pcb_path).stem + ".dxf"))
    args = ["pcb", "export", "dxf", pcb_path, "-o", out, "-l", layers]
    if output_units != "in":
        args += ["--output-units", output_units]
    if exclude_refdes:
        args.append("--exclude-refdes")
    if exclude_value:
        args.append("--exclude-value")
    if use_contours:
        args.append("--use-contours")
    if include_border_title:
        args.append("--include-border-title")
    result = _run_cli(args, check=False)
    if result.returncode != 0:
        return json.dumps({"error": result.stderr.strip()})
    return json.dumps({**_file_meta(out), "layers": layers})
```

- [ ] **Step 3: Write `export_ipc2581` test**

```python
"""Tests for IPC-2581 export tool."""
import json
import shutil
import pytest
from mcp_server_kicad import pcb

pytestmark = pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")

class TestExportIpc2581:
    def test_export_runs(self, scratch_pcb, tmp_path):
        result = json.loads(pcb.export_ipc2581(pcb_path=str(scratch_pcb), output=str(tmp_path / "board.xml")))
        assert "path" in result or "error" in result
```

- [ ] **Step 4: Implement `export_ipc2581`**

```python
@mcp.tool()
def export_ipc2581(
    pcb_path: str = PCB_PATH,
    output: str = "",
    precision: int = 3,
    compress: bool = False,
    version: str = "C",
    units: str = "mm",
) -> str:
    """Export PCB in IPC-2581 format for manufacturing data exchange."""
    out = output or str(Path(OUTPUT_DIR) / (Path(pcb_path).stem + ".xml"))
    args = ["pcb", "export", "ipc2581", pcb_path, "-o", out]
    if precision != 3:
        args += ["--precision", str(precision)]
    if compress:
        args.append("--compress")
    if version != "C":
        args += ["--version", version]
    if units != "mm":
        args += ["--units", units]
    result = _run_cli(args, check=False)
    if result.returncode != 0:
        return json.dumps({"error": result.stderr.strip()})
    return json.dumps(_file_meta(out))
```

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/test_pcb_dxf_export.py tests/test_ipc2581_export.py -v`

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_dxf_export.py tests/test_ipc2581_export.py
git commit -m "feat: add export_pcb_dxf and export_ipc2581 tools to PCB server"
```

### Task 15: Update PCB export, DRC, and cross-domain tests

**Files:**
- Modify: `tests/test_cli_pcb_export.py`
- Modify: `tests/test_cli_analysis.py`
- Modify: `tests/test_symbol_access_tools.py` (add relocated tests)
- Modify: `tests/test_footprint_access_tools.py` (add relocated tests)
- Modify: `tests/test_project_tools.py` (add relocated tests)

- [ ] **Step 1: Update `test_cli_pcb_export.py` PCB export imports**

Change `from mcp_server_kicad import export` to `from mcp_server_kicad import pcb`.

Update function calls to use consolidated tool names:
- `export.export_pcb_pdf(...)` → `pcb.export_pcb(format="pdf", ...)`
- `export.export_pcb_svg(...)` → `pcb.export_pcb(format="svg", ...)`
- `export.export_gerbers(...)` → `pcb.export_gerbers(...)`  (now includes drill by default)
- `export.export_drill(...)` → remove (absorbed into export_gerbers)
- `export.export_step(...)` → `pcb.export_3d(format="step", ...)`
- `export.export_stl(...)` → `pcb.export_3d(format="stl", ...)`
- `export.export_glb(...)` → `pcb.export_3d(format="glb", ...)`
- Other tools: `export.<name>(...)` → `pcb.<name>(...)`

- [ ] **Step 2: Relocate cross-domain tests from `test_cli_pcb_export.py`**

Move these test classes to their new homes:
- `TestExportSymbolSvg` (line ~89) → `tests/test_symbol_access_tools.py`, import from `symbol`
- `TestExportFootprintSvg` (line ~96) → `tests/test_footprint_access_tools.py`, import from `footprint`
- `TestUpgradeSymbolLib` (line ~110) → `tests/test_symbol_access_tools.py`, import from `symbol`
- `TestUpgradeFootprintLib` (line ~122) → `tests/test_footprint_access_tools.py`, import from `footprint`
- `TestRunJobset` (line ~137) → `tests/test_project_tools.py`, import from `project`

Remove these classes from `test_cli_pcb_export.py`.

- [ ] **Step 3: Update DRC tests in `test_cli_analysis.py`**

Change `TestRunDrc` to import from `pcb` instead of `export`. Remove the `export` import entirely.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest tests/test_cli_pcb_export.py tests/test_cli_analysis.py tests/test_symbol_access_tools.py tests/test_footprint_access_tools.py tests/test_project_tools.py -v`

```bash
git add tests/
git commit -m "test: update PCB export and DRC tests for consolidated tools, relocate cross-domain tests"
```

---

## Chunk 7: Delete export.py, update entry points, README, verify

### Task 16: Delete export.py

**Files:**
- Delete: `mcp_server_kicad/export.py`

- [ ] **Step 1: Verify all export.py tools have been moved**

Quick check:
- `list_unconnected_pins`, `run_erc` → schematic.py ✓
- `run_drc` → pcb.py ✓
- `export_schematic_pdf/svg/dxf` → schematic.py (as `export_schematic`) ✓
- `export_schematic_netlist` → schematic.py (as `export_netlist`) ✓
- `export_bom` → schematic.py ✓
- `export_gerbers/drill` → pcb.py (as consolidated `export_gerbers`) ✓
- `export_gerber/pcb_pdf/pcb_svg` → pcb.py (pcb_pdf/svg as `export_pcb`) ✓
- `export_positions/step/stl/glb` → pcb.py (step/stl/glb as `export_3d`) ✓
- `render_3d` → pcb.py ✓
- `export_symbol_svg`, `upgrade_symbol_lib` → symbol.py ✓
- `export_footprint_svg`, `upgrade_footprint_lib` → footprint.py ✓
- `run_jobset` → project.py ✓

- [ ] **Step 2: Delete and verify**

```bash
git rm mcp_server_kicad/export.py
uv run ruff check mcp_server_kicad/ tests/
```

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "chore: delete export.py - all tools distributed to domain servers"
```

### Task 17: Update pyproject.toml entry points

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Replace [project.scripts]**

Replace:
```toml
mcp-server-kicad-schematic = "mcp_server_kicad.schematic:main"
mcp-server-kicad-pcb = "mcp_server_kicad.pcb:main"
mcp-server-kicad-export = "mcp_server_kicad.export:main"
```

With:
```toml
mcp-server-kicad-schematic = "mcp_server_kicad.schematic:main"
mcp-server-kicad-pcb = "mcp_server_kicad.pcb:main"
mcp-server-kicad-symbol = "mcp_server_kicad.symbol:main"
mcp-server-kicad-footprint = "mcp_server_kicad.footprint:main"
mcp-server-kicad-project = "mcp_server_kicad.project:main"
```

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "chore: update entry points from 3 servers to 5 domain-aligned servers"
```

### Task 18: Update README and __main__.py

**Files:**
- Modify: `README.md`
- Modify: `mcp_server_kicad/__main__.py`

- [ ] **Step 1: Update README**

Replace 3-server MCP config with 5-server config from the design spec. Update prose describing servers. Update tool count references.

- [ ] **Step 2: Update `__main__.py`**

List all 5 servers in the error message:
- `python -m mcp_server_kicad.schematic`
- `python -m mcp_server_kicad.pcb`
- `python -m mcp_server_kicad.symbol`
- `python -m mcp_server_kicad.footprint`
- `python -m mcp_server_kicad.project`

- [ ] **Step 3: Commit**

```bash
git add README.md mcp_server_kicad/__main__.py
git commit -m "docs: update README and __main__.py for 5-server architecture with 63 tools"
```

### Task 19: Final test sweep and verification

- [ ] **Step 1: Check for stale imports**

Read `tests/test_edge_cases.py`, `tests/test_kicad_native.py`, `tests/test_new_sch_tools.py` for any stale `export` imports.

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS (some skip if kicad-cli not found)

- [ ] **Step 3: Run lint**

Run: `uv run ruff check mcp_server_kicad/ tests/`
Expected: No errors

- [ ] **Step 4: Verify each server starts**

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | timeout 5 uv run python -m mcp_server_kicad.schematic 2>/dev/null | head -1
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | timeout 5 uv run python -m mcp_server_kicad.pcb 2>/dev/null | head -1
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | timeout 5 uv run python -m mcp_server_kicad.symbol 2>/dev/null | head -1
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | timeout 5 uv run python -m mcp_server_kicad.footprint 2>/dev/null | head -1
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | timeout 5 uv run python -m mcp_server_kicad.project 2>/dev/null | head -1
```

Expected: Each returns a JSON-RPC response.

- [ ] **Step 5: Verify tool counts per server**

Use `tools/list` on each server and confirm:
- kicad-schematic: 29
- kicad-pcb: 19
- kicad-symbol: 4
- kicad-footprint: 4
- kicad-project: 7
- **Total: 63**

- [ ] **Step 6: Commit if any fixups needed**

```bash
git add -A && git commit -m "chore: final cleanup after server reorganization and tool consolidation"
```
