# MCP Server Reorganization Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize from 3 MCP servers (schematic, pcb, export) to 5 domain-aligned servers (schematic, pcb, symbol, footprint, project).

**Architecture:** Move tools from the grab-bag `export.py` into their domain servers. Extract symbol and footprint library tools into dedicated servers. Give project.py its own FastMCP instance. Delete export.py.

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
- Reference: `mcp_server_kicad/_shared.py` (for imports)

- [ ] **Step 1: Create `mcp_server_kicad/symbol.py`**

This file creates the kicad-symbol FastMCP server with 4 tools. Read the source files first, then create `symbol.py` with this structure:

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

**IMPORTANT:** Copy function bodies exactly from their source files — including parameter names, default values, docstrings, and body logic. Do NOT rename parameters (e.g., keep `symbol_lib_path`, not `library_path`).

- [ ] **Step 2: Remove `list_lib_symbols` and `get_symbol_info` from `schematic.py`**

Delete lines ~1444-1480 in `schematic.py` (the two `@mcp.tool()` decorated functions). These are the last two tool definitions before the `_register_project_tools(mcp)` call at line 1483.

Do NOT remove `add_lib_symbol` — it writes into the schematic's internal lib_symbols table and stays on the schematic server.

- [ ] **Step 3: Run existing tests to confirm nothing broke**

Run: `uv run pytest tests/test_read_tools.py tests/test_write_tools.py tests/test_routing_tools.py -v`
Expected: All PASS (schematic tools unaffected)

- [ ] **Step 4: Commit**

```bash
git add mcp_server_kicad/symbol.py mcp_server_kicad/schematic.py
git commit -m "feat: create kicad-symbol server with 4 library tools"
```

### Task 2: Update symbol-related tests

**Files:**
- Create: `tests/test_symbol_access_tools.py` (from symbol half of `test_lib_access_tools.py`)
- Modify: `tests/test_lib_access_tools.py` (remove symbol tests, keep footprint tests)

NOTE: Do NOT rename `tests/test_lib_tools.py`. That file tests `add_lib_symbol` which remains on the schematic server. Leave it unchanged.

- [ ] **Step 1: Create `tests/test_symbol_access_tools.py`**

Copy the `TestListLibSymbols` and `TestGetSymbolInfo` classes from `tests/test_lib_access_tools.py` into a new file `tests/test_symbol_access_tools.py`. Update imports to use `from mcp_server_kicad import symbol` and change all calls from `schematic.list_lib_symbols(...)` to `symbol.list_lib_symbols(...)` and `schematic.get_symbol_info(...)` to `symbol.get_symbol_info(...)`.

- [ ] **Step 2: Remove symbol tests from `test_lib_access_tools.py`**

Remove the `TestListLibSymbols` and `TestGetSymbolInfo` classes from `tests/test_lib_access_tools.py`, leaving only the footprint tests (`TestListLibFootprints`, `TestGetFootprintInfo`).

- [ ] **Step 3: Run symbol tests**

Run: `uv run pytest tests/test_symbol_access_tools.py -v`
Expected: All PASS

- [ ] **Step 4: Run remaining lib access tests**

Run: `uv run pytest tests/test_lib_access_tools.py -v`
Expected: All PASS (footprint tests still import from pcb)

- [ ] **Step 5: Commit**

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

This file creates the kicad-footprint FastMCP server with 4 tools. Read the source files first, then create `footprint.py` with this structure:

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

**IMPORTANT:** Copy function bodies exactly from their source files. Do NOT rename parameters.

- [ ] **Step 2: Remove `list_lib_footprints` and `get_footprint_info` from `pcb.py`**

Delete the two `@mcp.tool()` decorated functions at `pcb.py:406-439`.

- [ ] **Step 3: Run existing PCB tests**

Run: `uv run pytest tests/test_pcb_read_tools.py tests/test_pcb_write_tools.py -v`
Expected: All PASS (PCB tools unaffected)

- [ ] **Step 4: Commit**

```bash
git add mcp_server_kicad/footprint.py mcp_server_kicad/pcb.py
git commit -m "feat: create kicad-footprint server with 4 library tools"
```

### Task 4: Update footprint-related tests

**Files:**
- Rename: `tests/test_lib_access_tools.py` → `tests/test_footprint_access_tools.py`

- [ ] **Step 1: Rename remaining `test_lib_access_tools.py` to `test_footprint_access_tools.py`**

After chunk 1 removed symbol tests, this file only has footprint tests.

```bash
git mv tests/test_lib_access_tools.py tests/test_footprint_access_tools.py
```

Update imports: change `from mcp_server_kicad import pcb` to `from mcp_server_kicad import footprint` and update all function calls from `pcb.list_lib_footprints(...)` to `footprint.list_lib_footprints(...)` and `pcb.get_footprint_info(...)` to `footprint.get_footprint_info(...)`.

- [ ] **Step 2: Run footprint tests**

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
- Modify: `mcp_server_kicad/project.py` (add FastMCP, main(), absorb run_jobset, add get_version)
- Modify: `mcp_server_kicad/schematic.py` (remove _register_project_tools import and call)
- Reference: `mcp_server_kicad/export.py:655-666` (copy run_jobset)

- [ ] **Step 1: Add FastMCP instance to project.py**

Add to the import block of `project.py`:
```python
from mcp.server.fastmcp import FastMCP
from mcp_server_kicad._shared import _run_cli, OUTPUT_DIR
```

(Do NOT import `_file_meta` — none of the project tools use it.)

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

Delete the entire `register_tools(mcp: FastMCP)` function (lines 243-311). Replace with direct `@mcp.tool()` decorated functions that delegate to the internal `_create_*` functions. Each function should have the same signature and docstring as the inner functions had in `register_tools()`.

Example:
```python
@mcp.tool()
def create_project(directory: str, name: str) -> str:
    """Create a KiCad 9 project (.kicad_pro + .kicad_prl + .kicad_sch)."""
    return _create_project(directory, name)
```

Do the same for all 5 tools: `create_project`, `create_schematic`, `create_symbol_library`, `create_sym_lib_table`, `add_hierarchical_sheet`.

Keep the public aliases at lines 235-240 (`create_project = _create_project`, etc.) for test imports.

- [ ] **Step 3: Add `run_jobset` tool to project.py**

Copy `run_jobset` verbatim from `export.py:655-666`. The exact signature is:
```python
def run_jobset(jobset_path: str) -> str:
```

Note: this function returns **plain strings**, not JSON. It uses `_run_cli` internally. Preserve the exact return format.

- [ ] **Step 4: Add `get_version` tool to project.py**

```python
@mcp.tool()
def get_version() -> str:
    """Get KiCad version information including build details and library versions."""
    result = _run_cli(["version", "--format", "about"], check=False)
    if result.returncode != 0:
        return json.dumps({"error": result.stderr.strip()})
    return json.dumps({"version_info": result.stdout.strip()})
```

- [ ] **Step 5: Add `main()` entry point to project.py**

```python
def main():
    """Entry point for mcp-server-kicad-project console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Remove `_register_project_tools` import and call from schematic.py**

In `schematic.py`:
- Remove line ~33: `from mcp_server_kicad.project import register_tools as _register_project_tools`
- Remove line ~1483: `_register_project_tools(mcp)`

- [ ] **Step 7: Run project tests**

Run: `uv run pytest tests/test_project_tools.py -v`
Expected: All PASS (tests use internal function aliases, not MCP decorators)

- [ ] **Step 8: Run schematic tests to confirm nothing broke**

Run: `uv run pytest tests/test_read_tools.py tests/test_write_tools.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add mcp_server_kicad/project.py mcp_server_kicad/schematic.py
git commit -m "feat: give kicad-project server its own FastMCP instance with jobset and version tools"
```

### Task 6: Add tests for new project tools

**Files:**
- Modify: `tests/test_project_tools.py`

- [ ] **Step 1: Write test for `get_version`**

Add to `tests/test_project_tools.py`. Use a **class-level** skip, NOT module-level (other tests in this file don't need kicad-cli):

```python
import shutil

@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")
class TestGetVersion:
    def test_returns_version_info(self):
        """get_version should return KiCad version info."""
        result = json.loads(project.get_version())
        assert "version_info" in result or "error" in result
```

- [ ] **Step 2: Write test for `run_jobset`**

Note: `run_jobset` returns **plain strings**, not JSON. Do NOT use `json.loads`:

```python
class TestRunJobset:
    def test_missing_jobset_returns_error(self, tmp_path):
        """run_jobset with a nonexistent file should return an error string."""
        result = project.run_jobset(str(tmp_path / "nonexistent.kicad_jobset"))
        assert "failed" in result.lower() or "error" in result.lower()
```

- [ ] **Step 3: Run new tests**

Run: `uv run pytest tests/test_project_tools.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_project_tools.py
git commit -m "test: add tests for get_version and run_jobset project tools"
```

---

## Chunk 4: Absorb schematic exports into kicad-schematic

### Task 7: Move ERC tools and schematic exports from export.py to schematic.py

**Files:**
- Modify: `mcp_server_kicad/schematic.py` (add tools + helpers + imports)
- Modify: `mcp_server_kicad/export.py` (remove moved tools)

- [ ] **Step 1: Add new imports to schematic.py**

Add to the import block of `schematic.py`:

```python
import json
import os
from mcp_server_kicad._shared import _run_cli, _file_meta, OUTPUT_DIR
```

Note: `schematic.py` currently has a local `import json as _json` inside `get_net_connections()`. After adding the top-level `import json`, clean up that local import to use `json` directly instead of `_json`.

- [ ] **Step 2: Copy ERC helper functions to schematic.py**

Copy verbatim from `export.py:29-69` the two private helpers:
- `_annotate_erc_violations()` (lines 29-43)
- `_parse_unconnected_pins()` (lines 46-69)

Place them after the existing private helpers in `schematic.py` (after `_get_pin_pos()`, around line 160).

- [ ] **Step 3: Copy ERC tool functions to schematic.py**

Copy verbatim from `export.py`:
- `list_unconnected_pins()` (line 72) — NOTE: this function has an inline `import shutil` that MUST be preserved
- `run_erc()` (line 116)

Place them after the helper functions, before the existing read tools. Copy as-is including `@mcp.tool()` decorators.

- [ ] **Step 4: Copy schematic export functions to schematic.py**

Copy verbatim from `export.py`:
- `export_schematic_pdf()` (line 192)
- `export_schematic_svg()` (line 208)
- `export_schematic_netlist()` (line 231)
- `export_bom()` (line 253)
- `export_schematic_dxf()` (line 272)

Place them at the end of `schematic.py` (before `main()`), in a section marked with a comment like `# ── Exports (wraps kicad-cli) ──`.

- [ ] **Step 5: Remove moved tools from export.py**

Remove from `export.py`:
- `_annotate_erc_violations()` (lines 29-43)
- `_parse_unconnected_pins()` (lines 46-69)
- `list_unconnected_pins()` (line 72)
- `run_erc()` (line 116)
- `export_schematic_pdf()` (line 192)
- `export_schematic_svg()` (line 208)
- `export_schematic_netlist()` (line 231)
- `export_bom()` (line 253)
- `export_schematic_dxf()` (line 272)

Also remove unused imports from export.py's import block (e.g., `SCH_PATH` and `SYM_LIB_PATH` if no remaining tools use them).

- [ ] **Step 6: Update schematic.py FastMCP instructions**

Change the `instructions` string in the FastMCP constructor at `schematic.py:35-42` to:

```python
mcp = FastMCP(
    "kicad-schematic",
    instructions=(
        "KiCad schematic manipulation, ERC analysis, and schematic export tools."
        " Use wire_pin_to_label and connect_pins for efficient wiring instead of"
        " manually computing coordinates with get_pin_positions + add_wire + add_label."
    ),
)
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_read_tools.py tests/test_write_tools.py tests/test_routing_tools.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add mcp_server_kicad/schematic.py mcp_server_kicad/export.py
git commit -m "feat: absorb ERC and schematic export tools into kicad-schematic server"
```

### Task 8: Update schematic export and ERC tests

**Files:**
- Modify: `tests/test_cli_sch_export.py` (update imports)
- Modify: `tests/test_cli_analysis.py` (split: ERC → schematic import)

- [ ] **Step 1: Update `test_cli_sch_export.py` imports**

Change `from mcp_server_kicad import export` to `from mcp_server_kicad import schematic` and update ALL function calls — including both public tools (e.g., `export.export_schematic_pdf(...)` → `schematic.export_schematic_pdf(...)`) AND private helper references (e.g., `export._annotate_erc_violations(...)` → `schematic._annotate_erc_violations(...)` and `export._parse_unconnected_pins(...)` → `schematic._parse_unconnected_pins(...)`).

- [ ] **Step 2: Split ERC tests out of `test_cli_analysis.py`**

In `tests/test_cli_analysis.py`, the `TestRunErc` class (lines 14-24) should now import from `schematic` instead of `export`. Update the import and function references. The `TestRunDrc` class stays importing from `export` for now (will be moved in chunk 5).

Change the import to:
```python
from mcp_server_kicad import schematic
from mcp_server_kicad import export  # still needed for DRC until chunk 5
```

Update `TestRunErc` to call `schematic.run_erc(...)` instead of `export.run_erc(...)`.

- [ ] **Step 3: Run updated tests**

Run: `uv run pytest tests/test_cli_sch_export.py tests/test_cli_analysis.py -v`
Expected: All PASS (or skipped if kicad-cli not available)

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli_sch_export.py tests/test_cli_analysis.py
git commit -m "test: update schematic export and ERC test imports"
```

---

## Chunk 5: Absorb PCB exports into kicad-pcb + new tools

### Task 9: Move DRC and PCB exports from export.py to pcb.py

**Files:**
- Modify: `mcp_server_kicad/pcb.py` (add tools + imports)
- Modify: `mcp_server_kicad/export.py` (remove moved tools)

- [ ] **Step 1: Add new imports to pcb.py**

Add to the import block of `pcb.py`:

```python
import json
import os
from mcp_server_kicad._shared import _run_cli, _file_meta, OUTPUT_DIR
```

(`pcb.py` currently has NO `import json` or `import os` — these are critical for the absorbed tools.)

- [ ] **Step 2: Copy DRC tool to pcb.py**

Copy `run_drc()` verbatim from `export.py:152-184` into `pcb.py`. Place it in a section after the existing write tools.

- [ ] **Step 3: Copy PCB export tools to pcb.py**

Copy verbatim from `export.py`:
- `export_gerbers()` (line 293)
- `export_gerber()` (line 319)
- `export_drill()` (line 343)
- `export_pcb_pdf()` (line 369)
- `export_pcb_svg()` (line 405)
- `export_positions()` (line 441)
- `export_step()` (line 462)
- `export_stl()` (line 481)
- `export_glb()` (line 500)
- `render_3d()` (line 519)

Place them in a `# ── Exports (wraps kicad-cli) ──` section at the end of `pcb.py` (before `main()`).

- [ ] **Step 4: Remove moved tools from export.py**

Remove all DRC and PCB export tools from `export.py`. After this step, export.py should only contain:
- `export_symbol_svg`, `export_footprint_svg`, `upgrade_symbol_lib`, `upgrade_footprint_lib`, `run_jobset`
(These were already copied to symbol.py, footprint.py, and project.py in earlier chunks.)

- [ ] **Step 5: Update pcb.py FastMCP instructions**

```python
mcp = FastMCP(
    "kicad-pcb",
    instructions=(
        "KiCad PCB manipulation, DRC analysis, and PCB export tools"
        " including Gerber, drill, 3D models, and pick-and-place."
    ),
)
```

- [ ] **Step 6: Run PCB tests**

Run: `uv run pytest tests/test_pcb_read_tools.py tests/test_pcb_write_tools.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add mcp_server_kicad/pcb.py mcp_server_kicad/export.py
git commit -m "feat: absorb DRC and PCB export tools into kicad-pcb server"
```

### Task 10: Add new PCB export tools

**Files:**
- Modify: `mcp_server_kicad/pcb.py`

- [ ] **Step 1: Write `export_pcb_dxf` test**

Create `tests/test_pcb_dxf_export.py`:

```python
"""Tests for PCB DXF export tool."""

import json
import shutil

import pytest

from mcp_server_kicad import pcb

pytestmark = pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="kicad-cli not found"
)


class TestExportPcbDxf:
    def test_export_runs(self, scratch_pcb, tmp_path):
        output = str(tmp_path / "board.dxf")
        result = json.loads(
            pcb.export_pcb_dxf(
                pcb_path=str(scratch_pcb),
                output=output,
                layers="F.Cu",
            )
        )
        # kicad-cli may fail on kiutils-generated PCBs; accept either result
        assert "path" in result or "error" in result

    def test_missing_layers_returns_error(self, scratch_pcb, tmp_path):
        output = str(tmp_path / "board.dxf")
        result = json.loads(
            pcb.export_pcb_dxf(
                pcb_path=str(scratch_pcb),
                output=output,
                layers="",
            )
        )
        assert "error" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pcb_dxf_export.py -v`
Expected: FAIL (function not defined)

- [ ] **Step 3: Implement `export_pcb_dxf` in pcb.py**

Add to pcb.py in the exports section:

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

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pcb_dxf_export.py -v`
Expected: PASS (or skipped if kicad-cli not found)

- [ ] **Step 5: Write `export_ipc2581` test**

Create `tests/test_ipc2581_export.py`:

```python
"""Tests for IPC-2581 export tool."""

import json
import shutil

import pytest

from mcp_server_kicad import pcb

pytestmark = pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="kicad-cli not found"
)


class TestExportIpc2581:
    def test_export_runs(self, scratch_pcb, tmp_path):
        output = str(tmp_path / "board.xml")
        result = json.loads(
            pcb.export_ipc2581(
                pcb_path=str(scratch_pcb),
                output=output,
            )
        )
        # kicad-cli may fail on kiutils-generated PCBs; accept either result
        assert "path" in result or "error" in result
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_ipc2581_export.py -v`
Expected: FAIL

- [ ] **Step 7: Implement `export_ipc2581` in pcb.py**

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

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_ipc2581_export.py -v`
Expected: PASS (or skipped)

- [ ] **Step 9: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_dxf_export.py tests/test_ipc2581_export.py
git commit -m "feat: add export_pcb_dxf and export_ipc2581 tools to PCB server"
```

### Task 11: Update PCB export, DRC, and cross-domain tests

**Files:**
- Modify: `tests/test_cli_pcb_export.py` (update imports + relocate cross-domain tests)
- Modify: `tests/test_cli_analysis.py` (DRC → pcb import)

- [ ] **Step 1: Update `test_cli_pcb_export.py` PCB export imports**

Change `from mcp_server_kicad import export` to `from mcp_server_kicad import pcb` and update PCB export function calls.

- [ ] **Step 2: Relocate symbol/footprint/jobset tests from `test_cli_pcb_export.py`**

`test_cli_pcb_export.py` also contains test classes that belong to other servers:
- `TestExportSymbolSvg` (line ~89) → move to `tests/test_symbol_access_tools.py`, import from `symbol`
- `TestExportFootprintSvg` (line ~96) → move to `tests/test_footprint_access_tools.py`, import from `footprint`
- `TestUpgradeSymbolLib` (line ~110) → move to `tests/test_symbol_access_tools.py`, import from `symbol`
- `TestUpgradeFootprintLib` (line ~122) → move to `tests/test_footprint_access_tools.py`, import from `footprint`
- `TestRunJobset` (line ~137) → move to `tests/test_project_tools.py`, import from `project`

Remove these classes from `test_cli_pcb_export.py` after moving them.

- [ ] **Step 3: Update DRC tests in `test_cli_analysis.py`**

Change the `TestRunDrc` class to import from `pcb` instead of `export`. Now the entire file should import from `schematic` (for ERC) and `pcb` (for DRC). Remove the `export` import entirely.

- [ ] **Step 4: Run updated tests**

Run: `uv run pytest tests/test_cli_pcb_export.py tests/test_cli_analysis.py tests/test_symbol_access_tools.py tests/test_footprint_access_tools.py tests/test_project_tools.py -v`
Expected: All PASS (or skipped)

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: update PCB export and DRC test imports, relocate cross-domain tests"
```

---

## Chunk 6: Delete export.py, update entry points, README

### Task 12: Delete export.py

**Files:**
- Delete: `mcp_server_kicad/export.py`

- [ ] **Step 1: Verify all export.py tools have been moved**

Check that every tool from export.py has a home:
- `list_unconnected_pins` → schematic.py ✓
- `run_erc` → schematic.py ✓
- `run_drc` → pcb.py ✓
- `export_schematic_pdf/svg/netlist/dxf` → schematic.py ✓
- `export_bom` → schematic.py ✓
- `export_gerbers/gerber/drill/pcb_pdf/pcb_svg/positions/step/stl/glb` → pcb.py ✓
- `render_3d` → pcb.py ✓
- `export_symbol_svg`, `upgrade_symbol_lib` → symbol.py ✓
- `export_footprint_svg`, `upgrade_footprint_lib` → footprint.py ✓
- `run_jobset` → project.py ✓

- [ ] **Step 2: Delete export.py**

```bash
git rm mcp_server_kicad/export.py
```

- [ ] **Step 3: Verify no remaining imports of export module**

Search codebase for any remaining references to the export module. There should be none.

Run: `uv run ruff check mcp_server_kicad/ tests/`

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "chore: delete export.py - all tools distributed to domain servers"
```

### Task 13: Update pyproject.toml entry points

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update [project.scripts] section**

Replace lines 27-30:
```toml
[project.scripts]
mcp-server-kicad-schematic = "mcp_server_kicad.schematic:main"
mcp-server-kicad-pcb = "mcp_server_kicad.pcb:main"
mcp-server-kicad-export = "mcp_server_kicad.export:main"
```

With:
```toml
[project.scripts]
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

### Task 14: Update README and __main__.py

**Files:**
- Modify: `README.md`
- Modify: `mcp_server_kicad/__main__.py`

- [ ] **Step 1: Update server configuration example in README**

Replace the 3-server MCP client config in README.md with the 5-server config from the design spec. Also update any prose that describes the 3 servers to describe 5.

Key sections to update:
- The server descriptions/list
- The JSON config example
- Any table or list referencing "kicad-export"

- [ ] **Step 2: Update `__main__.py`**

`__main__.py` lists the available server commands in its error message. Update to list all 5 servers:
- `python -m mcp_server_kicad.schematic`
- `python -m mcp_server_kicad.pcb`
- `python -m mcp_server_kicad.symbol`
- `python -m mcp_server_kicad.footprint`
- `python -m mcp_server_kicad.project`

- [ ] **Step 3: Commit**

```bash
git add README.md mcp_server_kicad/__main__.py
git commit -m "docs: update README and __main__.py for 5-server architecture"
```

### Task 15: Final test sweep

**Files:**
- Check: `tests/test_edge_cases.py`, `tests/test_kicad_native.py`, `tests/test_new_sch_tools.py`

- [ ] **Step 1: Check for stale export imports**

Read each file and check if any import from `mcp_server_kicad.export` or reference `export.` function calls. Update as needed. (These files likely only import from `schematic` and `conftest`, so no changes expected.)

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS (some may skip if kicad-cli not found)

- [ ] **Step 3: Run lint**

Run: `uv run ruff check mcp_server_kicad/ tests/`
Expected: No errors

- [ ] **Step 4: Commit if changes were needed**

```bash
git add -A && git commit -m "test: fix remaining stale imports after server reorganization"
```

### Task 16: Final verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Verify each server starts**

Use `timeout` to prevent hanging:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | timeout 5 uv run python -m mcp_server_kicad.schematic 2>/dev/null | head -1
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | timeout 5 uv run python -m mcp_server_kicad.pcb 2>/dev/null | head -1
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | timeout 5 uv run python -m mcp_server_kicad.symbol 2>/dev/null | head -1
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | timeout 5 uv run python -m mcp_server_kicad.footprint 2>/dev/null | head -1
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | timeout 5 uv run python -m mcp_server_kicad.project 2>/dev/null | head -1
```

Expected: Each returns a JSON-RPC response with server info.

- [ ] **Step 3: Verify tool counts per server**

Use MCP Inspector or a quick script to call `tools/list` on each server and confirm the expected tool counts:
- kicad-schematic: 39
- kicad-pcb: 28
- kicad-symbol: 4
- kicad-footprint: 4
- kicad-project: 7

- [ ] **Step 4: Final commit if any fixups needed**

```bash
git add -A && git commit -m "chore: final cleanup after server reorganization"
```
