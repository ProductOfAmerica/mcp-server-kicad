# v0.5.0 Refactoring Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate 5 MCP servers into 1, merge 6 redundant tools (65→59), and conditionally hide CLI-dependent tools when kicad-cli is not on PATH.

**Architecture:** Each module keeps its own `FastMCP` instance for backwards-compatible standalone use. A new `server.py` creates a unified `FastMCP("kicad")` and copies tools from all 5 modules via their internal `_tool_manager._tools` dict (private API — used because the project's test suite already depends on this API, and a per-module `register()` function would require restructuring all 5 modules). CLI-dependent tools are excluded when `shutil.which("kicad-cli")` returns None.

**Note on line numbers:** Tasks modify shared files sequentially. Line numbers reference the original file state. After each task executes, subsequent line numbers shift. Agents should locate code by function name/content, not line numbers, for Tasks 5+ in pcb.py and Tasks 2+ in test_tool_annotations.py.

**Tech Stack:** Python 3.10+, FastMCP, kiutils, kicad-cli

**Spec:** `docs/superpowers/specs/2026-03-11-v050-refactoring-design.md`

---

## Chunk 1: Schematic Tool Removals + Merge

### Task 1: Remove `add_no_connect`

**Files:**
- Modify: `mcp_server_kicad/schematic.py:1019-1033` (delete function)
- Modify: `mcp_server_kicad/schematic.py:1478` (update `no_connect_pin` docstring)
- Modify: `tests/test_new_sch_tools.py:100-106` (delete `TestAddNoConnect`)
- Modify: `tests/test_tool_annotations.py:67` (remove from parametrize list)

- [ ] **Step 1: Delete `add_no_connect` from schematic.py**

Delete lines 1019-1033 (the `@mcp.tool` decorator through `return f"No-connect at ({x}, {y})"`).

- [ ] **Step 2: Update `no_connect_pin` docstring**

In `no_connect_pin`, change the docstring line:
```
Combines get_pin_positions + add_no_connect into one call.
```
to:
```
Resolves pin position and places a no-connect flag.
```

- [ ] **Step 3: Delete `TestAddNoConnect` from test_new_sch_tools.py**

Delete lines 100-106 (`class TestAddNoConnect` and its `test_basic` method).

- [ ] **Step 4: Remove `"add_no_connect"` from test_tool_annotations.py**

In the `test_schematic_additive` parametrize list (line 67), remove `"add_no_connect"`.

- [ ] **Step 5: Run tests to verify**

Run: `pytest tests/test_new_sch_tools.py tests/test_tool_annotations.py tests/test_write_tools.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_new_sch_tools.py tests/test_tool_annotations.py
git commit -m "refactor: remove add_no_connect tool (absorbed by no_connect_pin)"
```

---

### Task 2: Remove `add_power_rail`

**Files:**
- Modify: `mcp_server_kicad/schematic.py:1114-1167` (delete function)
- Modify: `tests/test_routing_tools.py:632-678` (delete `TestAddPowerRail`)
- Modify: `tests/test_tool_annotations.py:69` (remove from parametrize list)

- [ ] **Step 1: Delete `add_power_rail` from schematic.py**

Delete from `@mcp.tool(annotations=_ADDITIVE)` at line 1114 through the `return result` at line 1167, including the blank line after.

- [ ] **Step 2: Delete `TestAddPowerRail` from test_routing_tools.py**

Delete lines 632-678 (the comment block, class, and both test methods).

- [ ] **Step 3: Remove `"add_power_rail"` from test_tool_annotations.py**

In the `test_schematic_additive` parametrize list (line 69), remove `"add_power_rail"`.

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_routing_tools.py tests/test_tool_annotations.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_routing_tools.py tests/test_tool_annotations.py
git commit -m "refactor: remove add_power_rail tool (use add_power_symbol + wire_pins_to_net)"
```

---

### Task 3: Merge `get_schematic_info` into `list_schematic_items`

**Files:**
- Modify: `mcp_server_kicad/schematic.py:285-303` (delete function)
- Modify: `mcp_server_kicad/schematic.py:307-363` (add "summary" branch + update docstring)
- Modify: `tests/test_read_tools.py:229-244` (rewrite tests to use `list_schematic_items`)
- Modify: `tests/test_tool_annotations.py:44` (remove from parametrize list)

- [ ] **Step 1: Add "summary" branch to `list_schematic_items`**

In `list_schematic_items`, update the docstring `item_type` line to:
```
        item_type: One of "summary", "components", "labels", "wires", "global_labels"
```

Add a new branch at the top of the if/elif chain (before `if item_type == "components"`):
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
            f"Wires: {wire_count}"
        )
    elif item_type == "components":
```

Update the error message to include "summary":
```
"Use: summary, components, labels, wires, global_labels"
```

- [ ] **Step 2: Delete `get_schematic_info` function**

Delete lines 285-303 (decorator through return statement).

- [ ] **Step 3: Update tests in test_read_tools.py**

Update the comment at line 225 from `# Tests: get_schematic_info (Bug 5)` to `# Tests: list_schematic_items(item_type="summary")`.

Replace `TestGetSchematicInfo` class (lines 229-244) with:
```python
class TestListSchematicItemsSummary:
    def test_returns_page_and_counts(self, scratch_sch: Path) -> None:
        result = schematic.list_schematic_items("summary", str(scratch_sch))
        assert "A4" in result
        assert "297" in result
        assert "210" in result
        assert "Components:" in result
        assert "Labels:" in result
        assert "Wires:" in result

    def test_empty_schematic(self, empty_sch: Path) -> None:
        result = schematic.list_schematic_items("summary", str(empty_sch))
        assert "A4" in result
        assert "Components: 0" in result
        assert "Labels: 0" in result
        assert "Wires: 0" in result
```

- [ ] **Step 4: Remove `"get_schematic_info"` from test_tool_annotations.py**

In the `test_schematic_read_only` parametrize list (line 44), remove `"get_schematic_info"`.

- [ ] **Step 5: Run tests to verify**

Run: `pytest tests/test_read_tools.py tests/test_tool_annotations.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/schematic.py tests/test_read_tools.py tests/test_tool_annotations.py
git commit -m "refactor: merge get_schematic_info into list_schematic_items(item_type='summary')"
```

---

## Chunk 2: PCB Tool Merges

### Task 4: Merge `export_gerber` into `export_gerbers`

**Files:**
- Modify: `mcp_server_kicad/pcb.py:543-598` (add `layers` param to `export_gerbers`, delete `export_gerber`)
- Modify: `tests/test_cli_pcb_export.py:33-37` (rewrite test to use `export_gerbers`)
- Modify: `tests/test_tool_annotations.py:148` (remove from parametrize list)

- [ ] **Step 1: Add `layers` parameter to `export_gerbers` and implement dispatch**

Replace the `export_gerbers` function (lines 543-574) with:
```python
@mcp.tool(annotations=_EXPORT)
def export_gerbers(
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    include_drill: bool = True,
    layers: list[str] | None = None,
) -> str:
    """Export Gerber files for manufacturing.

    When layers contains exactly one layer, exports a single Gerber file.
    Otherwise exports all layers (or the specified subset) plus optional drill files.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory for gerber files
        include_drill: Also export drill files (default: True, ignored in single-layer mode)
        layers: Optional list of layer names. Single layer = single file output.
    """
    # Single-layer mode: one file, like the old export_gerber
    if layers and len(layers) == 1:
        try:
            layer = layers[0]
            out_dir = output_dir or str(Path(pcb_path).parent)
            out_path = str(
                Path(out_dir) / f"{Path(pcb_path).stem}-{layer.replace('.', '_')}.gbr"
            )
            _run_cli(
                ["pcb", "export", "gerber", "--layers", layer, "--output", out_path, pcb_path]
            )
            meta = _file_meta(out_path)
            meta.update({"format": "gerber", "layer": layer})
            return json.dumps(meta, indent=2)
        except (RuntimeError, FileNotFoundError) as e:
            return json.dumps(
                {"error": str(e), "format": "gerber", "layer": layer}, indent=2
            )

    try:
        # Multi-layer mode: directory of files
        out = output_dir or str(Path(pcb_path).parent / "gerbers")
        os.makedirs(out, exist_ok=True)
        cmd = ["pcb", "export", "gerbers"]
        if layers:
            cmd += ["--layers", ",".join(layers)]
        cmd += ["--output", out, pcb_path]
        _run_cli(cmd)
        files = sorted(Path(out).glob("*"))
        result = {
            "path": out,
            "format": "gerber",
            "files": [f.name for f in files],
            "count": len(files),
        }
        if include_drill:
            _run_cli(["pcb", "export", "drill", "--output", out, pcb_path])
            drill_files = sorted(Path(out).glob("*.drl")) + sorted(Path(out).glob("*.DRL"))
            result["drill_files"] = [f.name for f in drill_files]
            result["drill_count"] = len(drill_files)
        return json.dumps(result, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e)}, indent=2)
```

- [ ] **Step 2: Delete `export_gerber` function**

Delete lines 577-598 (the old single-layer `export_gerber` function).

- [ ] **Step 3: Update test in test_cli_pcb_export.py**

Replace `TestExportGerber` class (lines 33-37) with:
```python
class TestExportGerberSingleLayer:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = pcb.export_gerbers(str(scratch_pcb), str(tmp_path), layers=["F.Cu"])
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportGerbersLayerFilter:
    def test_multi_layer_filter(self, scratch_pcb, tmp_path):
        result = pcb.export_gerbers(
            str(scratch_pcb), str(tmp_path / "gerbers"), layers=["F.Cu", "B.Cu"]
        )
        data = _parse_result(result)
        assert "format" in data or "error" in data
```

- [ ] **Step 4: Remove `"export_gerber"` from test_tool_annotations.py**

In the `test_pcb_export` parametrize list (line 148), remove `"export_gerber"`.

- [ ] **Step 5: Run tests to verify**

Run: `pytest tests/test_cli_pcb_export.py tests/test_tool_annotations.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_cli_pcb_export.py tests/test_tool_annotations.py
git commit -m "refactor: merge export_gerber into export_gerbers(layers=[...])"
```

---

### Task 5: Merge `export_pcb_dxf` into `export_pcb`

**Files:**
- Modify: `mcp_server_kicad/pcb.py:498-540` (add DXF format + params to `export_pcb`)
- Modify: `mcp_server_kicad/pcb.py:695-735` (delete `export_pcb_dxf`)
- Modify: `tests/test_pcb_dxf_export.py` (rewrite to use `export_pcb`)
- Modify: `tests/test_tool_annotations.py:152` (remove from parametrize list)

- [ ] **Step 1: Update `export_pcb` to support DXF format**

Replace the `export_pcb` function (lines 498-540) with:
```python
@mcp.tool(annotations=_EXPORT)
def export_pcb(
    format: str = "pdf",
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    layers: list[str] | None = None,
    output_units: str = "in",
    exclude_refdes: bool = False,
    exclude_value: bool = False,
    use_contours: bool = False,
    include_border_title: bool = False,
) -> str:
    """Export PCB to PDF, SVG, or DXF format.

    Args:
        format: Output format - "pdf", "svg", or "dxf"
        pcb_path: Path to .kicad_pcb file
        output_dir: Directory for output files
        layers: Optional list of layer names to include (required for DXF)
        output_units: DXF output units - "in" or "mm" (DXF only)
        exclude_refdes: Exclude reference designators (DXF only)
        exclude_value: Exclude component values (DXF only)
        use_contours: Use board outline contours (DXF only)
        include_border_title: Include border and title block (DXF only)
    """
    fmt = format.lower()
    if fmt not in ("pdf", "svg", "dxf"):
        return json.dumps({"error": f"Unknown format: {format}. Use: pdf, svg, dxf"})

    if fmt == "dxf":
        if not layers:
            return json.dumps({"error": "layers parameter is required for DXF export"})
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + ".dxf"))
        args = ["pcb", "export", "dxf", pcb_path, "-o", out_path, "-l", ",".join(layers)]
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
        try:
            result = _run_cli(args, check=False)
            if result.returncode != 0:
                return json.dumps({"error": result.stderr.strip()})
            return json.dumps({**_file_meta(out_path), "format": "dxf", "layers": layers})
        except (RuntimeError, FileNotFoundError) as e:
            return json.dumps({"error": str(e), "format": "dxf"}, indent=2)

    # PDF / SVG path
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        ext = ".pdf" if fmt == "pdf" else ".svg"
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + ext))
        if fmt == "pdf":
            layer_list = layers or ["F.Cu", "B.Cu"]
        else:
            layer_list = layers or ["F.Cu"]
        _run_cli(
            [
                "pcb",
                "export",
                fmt,
                "--layers",
                ",".join(layer_list),
                "--output",
                out_path,
                pcb_path,
            ]
        )
        meta = _file_meta(out_path)
        meta.update({"format": fmt, "layers": layer_list})
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": fmt}, indent=2)
```

- [ ] **Step 2: Delete `export_pcb_dxf` function**

Delete the `export_pcb_dxf` function (lines 695-735).

- [ ] **Step 3: Rewrite tests in test_pcb_dxf_export.py**

Replace the entire file content with:
```python
"""Tests for PCB DXF export via export_pcb(format='dxf')."""

import json
import shutil

import pytest

from mcp_server_kicad import pcb

pytestmark = pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")


class TestExportPcbDxf:
    def test_export_runs(self, scratch_pcb, tmp_path):
        result = json.loads(
            pcb.export_pcb(
                format="dxf",
                pcb_path=str(scratch_pcb),
                output_dir=str(tmp_path),
                layers=["F.Cu"],
            )
        )
        assert "path" in result or "error" in result

    def test_missing_layers_returns_error(self):
        result = json.loads(pcb.export_pcb(format="dxf"))
        assert "error" in result

    def test_with_mm_units(self, scratch_pcb, tmp_path):
        result = json.loads(
            pcb.export_pcb(
                format="dxf",
                pcb_path=str(scratch_pcb),
                output_dir=str(tmp_path),
                layers=["F.Cu"],
                output_units="mm",
            )
        )
        assert "path" in result or "error" in result

    def test_with_options(self, scratch_pcb, tmp_path):
        result = json.loads(
            pcb.export_pcb(
                format="dxf",
                pcb_path=str(scratch_pcb),
                output_dir=str(tmp_path),
                layers=["F.Cu"],
                exclude_refdes=True,
                exclude_value=True,
            )
        )
        assert "path" in result or "error" in result
```

- [ ] **Step 4: Remove `"export_pcb_dxf"` from test_tool_annotations.py**

In the `test_pcb_export` parametrize list (line 152), remove `"export_pcb_dxf"`.

- [ ] **Step 5: Update the invalid format test in test_cli_pcb_export.py**

In `TestExportPcbInvalidFormat.test_export_pcb_invalid_format` (line 103), the existing test passes `format="xyz"` which should still fail. No change needed — but verify the error message now says `"Use: pdf, svg, dxf"`.

- [ ] **Step 6: Run tests to verify**

Run: `pytest tests/test_pcb_dxf_export.py tests/test_cli_pcb_export.py tests/test_tool_annotations.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_pcb_dxf_export.py tests/test_cli_pcb_export.py tests/test_tool_annotations.py
git commit -m "refactor: merge export_pcb_dxf into export_pcb(format='dxf')"
```

---

### Task 6: Merge `render_3d` into `export_3d`

**Files:**
- Modify: `mcp_server_kicad/pcb.py:601-625` (add "render" format + params to `export_3d`)
- Modify: `mcp_server_kicad/pcb.py:649-692` (delete `render_3d`)
- Modify: `tests/test_cli_pcb_export.py:92-96` (rewrite test to use `export_3d`)
- Modify: `tests/test_cli_pcb_export.py:106-108` (update invalid format test)
- Modify: `tests/test_tool_annotations.py:151` (remove from parametrize list)

- [ ] **Step 1: Update `export_3d` to support "render" format**

Replace the `export_3d` function (lines 601-625) with:
```python
@mcp.tool(annotations=_EXPORT)
def export_3d(
    format: str = "step",
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    width: int = 1600,
    height: int = 900,
    side: str = "top",
    quality: str = "basic",
) -> str:
    """Export PCB 3D model or render 3D view to image.

    Args:
        format: Output format - "step", "stl", "glb", or "render" (PNG image)
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
        width: Image width in pixels (render only)
        height: Image height in pixels (render only)
        side: View side: top, bottom, left, right, front, back (render only)
        quality: Render quality: basic, high (render only)
    """
    fmt = format.lower()
    if fmt not in ("step", "stl", "glb", "render"):
        return json.dumps({"error": f"Unknown format: {format}. Use: step, stl, glb, render"})

    if fmt == "render":
        try:
            out_dir = output_dir or str(Path(pcb_path).parent)
            out_path = str(Path(out_dir) / (Path(pcb_path).stem + f"-3d-{side}.png"))
            _run_cli(
                [
                    "pcb",
                    "render",
                    "--width",
                    str(width),
                    "--height",
                    str(height),
                    "--side",
                    side,
                    "--quality",
                    quality,
                    "--output",
                    out_path,
                    pcb_path,
                ]
            )
            meta = _file_meta(out_path)
            meta.update({"format": "png", "width": width, "height": height, "side": side})
            return json.dumps(meta, indent=2)
        except (RuntimeError, FileNotFoundError) as e:
            return json.dumps({"error": str(e), "format": "png"}, indent=2)

    # STEP / STL / GLB path
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + f".{fmt}"))
        _run_cli(["pcb", "export", fmt, "--output", out_path, pcb_path])
        meta = _file_meta(out_path)
        meta["format"] = fmt
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": fmt}, indent=2)
```

- [ ] **Step 2: Delete `render_3d` function**

Delete the `render_3d` function (lines 649-692).

- [ ] **Step 3: Update test in test_cli_pcb_export.py**

Replace `TestRender3d` class (lines 92-96) with:
```python
class TestExport3dRender:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = pcb.export_3d(format="render", pcb_path=str(scratch_pcb), output_dir=str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data
```

The invalid format test (`test_export_3d_invalid_format`, line 106-108) uses `format="obj"` which is not in the expanded set (step/stl/glb/render). No change needed — it passes as-is.

- [ ] **Step 4: Remove `"render_3d"` from test_tool_annotations.py**

In the `test_pcb_export` parametrize list (line 151), remove `"render_3d"`.

- [ ] **Step 5: Run tests to verify**

Run: `pytest tests/test_cli_pcb_export.py tests/test_tool_annotations.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add mcp_server_kicad/pcb.py tests/test_cli_pcb_export.py tests/test_tool_annotations.py
git commit -m "refactor: merge render_3d into export_3d(format='render')"
```

---

## Chunk 3: Unified Server + Conditional CLI Registration

### Task 7: Create unified server.py

**Files:**
- Create: `mcp_server_kicad/server.py`
- Modify: `mcp_server_kicad/__main__.py` (update usage message)
- Modify: `mcp_server_kicad/__init__.py` (remove stale `__version__`)
- Modify: `pyproject.toml` (add unified entry point)

- [ ] **Step 1: Create `mcp_server_kicad/server.py`**

```python
"""Unified MCP server registering all KiCad tools."""

import shutil

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad import footprint, pcb, project, schematic, symbol

mcp = FastMCP(
    "kicad",
    instructions=(
        "KiCad EDA tools for schematic capture, PCB layout, symbol/footprint"
        " libraries, and project management.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write KiCad files (.kicad_sch, .kicad_pcb,"
        " .kicad_sym, .kicad_mod, .kicad_pro) directly. All manipulation"
        " MUST go through these MCP tools.\n"
        "- NEVER run kicad-cli commands directly. Use the export, ERC, and"
        " DRC tools provided by this server.\n"
        "- When a tool returns an error, try different parameters or a"
        " different tool. Do NOT fall back to manual file editing."
    ),
)

# Tools that require kicad-cli on PATH
_CLI_TOOLS: set[str] = {
    # schematic
    "export_schematic",
    "export_netlist",
    "export_bom",
    "run_erc",
    "list_unconnected_pins",
    # pcb
    "export_pcb",
    "export_gerbers",
    "export_3d",
    "export_positions",
    "export_ipc2581",
    "run_drc",
    # symbol
    "export_symbol_svg",
    "upgrade_symbol_lib",
    # footprint
    "export_footprint_svg",
    "upgrade_footprint_lib",
    # project
    "run_jobset",
    "get_version",
}


def _copy_tools(source_mcp: FastMCP, target_mcp: FastMCP, has_cli: bool) -> None:
    """Copy tools from a source FastMCP instance into the target server.

    Uses _tool_manager._tools (private API) because FastMCP has no public
    tool-copy API.  The project's test suite (test_tool_annotations.py) already
    depends on this internal structure.
    """
    for name, tool in source_mcp._tool_manager._tools.items():
        if not has_cli and name in _CLI_TOOLS:
            continue
        target_mcp._tool_manager._tools[name] = tool


def main() -> None:
    """Entry point for unified mcp-server-kicad console script."""
    has_cli = shutil.which("kicad-cli") is not None
    for mod in [schematic, pcb, symbol, footprint, project]:
        _copy_tools(mod.mcp, mcp, has_cli)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Update `__main__.py`**

Replace content with:
```python
"""Allow running as `python -m mcp_server_kicad`."""

from mcp_server_kicad.server import main

main()
```

- [ ] **Step 3: Add unified entry point to pyproject.toml**

In `[project.scripts]`, add before the existing entries:
```
mcp-server-kicad = "mcp_server_kicad.server:main"
```

- [ ] **Step 4: Remove stale `__version__` from `__init__.py`**

The `__init__.py` has `__version__ = "0.1.0"` which has been wrong since v0.2.0. The release workflow only bumps `pyproject.toml` and `plugin.json`. Rather than adding a third place to maintain, remove `__version__` entirely. Replace the file content with:
```python
"""MCP servers for KiCad schematic, PCB, and export automation."""
```

Note: Do NOT manually bump versions in `pyproject.toml` or `plugin.json`. The release workflow (`release.yml --field bump=minor`) handles version bumps automatically (0.4.2 → 0.5.0) and commits the change.

- [ ] **Step 5: Write test for unified server**

Create `tests/test_unified_server.py`:
```python
"""Tests for the unified MCP server."""

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad import footprint, pcb, project, schematic, server, symbol

_MODULES = [schematic, pcb, symbol, footprint, project]


def _build_unified(has_cli: bool) -> FastMCP:
    """Build a fresh unified FastMCP instance for testing (avoids mutating module state)."""
    target = FastMCP("kicad-test")
    for mod in _MODULES:
        server._copy_tools(mod.mcp, target, has_cli)
    return target


class TestUnifiedServer:
    def test_server_module_has_mcp(self):
        assert hasattr(server, "mcp")
        assert hasattr(server, "main")

    def test_copy_tools_with_cli(self):
        """All tools from sub-modules are registered when has_cli=True."""
        target = _build_unified(has_cli=True)
        registered = set(target._tool_manager._tools.keys())
        # Spot-check a few tools from each module
        assert "place_component" in registered  # schematic
        assert "add_trace" in registered  # pcb
        assert "list_lib_symbols" in registered  # symbol
        assert "list_lib_footprints" in registered  # footprint
        assert "create_project" in registered  # project
        # CLI tools should be present
        assert "run_erc" in registered
        assert "export_gerbers" in registered
        # Total tool count
        assert len(registered) == 59, f"Expected 59 tools, got {len(registered)}: {registered}"

    def test_copy_tools_without_cli(self):
        """CLI-dependent tools are excluded when has_cli=False."""
        target = _build_unified(has_cli=False)
        registered = set(target._tool_manager._tools.keys())
        # Non-CLI tools should be present
        assert "place_component" in registered
        assert "add_trace" in registered
        # CLI tools should NOT be present
        for cli_tool in server._CLI_TOOLS:
            assert cli_tool not in registered, f"{cli_tool} should be excluded"
        # Tool count: 59 total - 17 CLI = 42
        assert len(registered) == 42, f"Expected 42 non-CLI tools, got {len(registered)}"

    def test_no_tool_name_collisions(self):
        """All tool names across modules are unique."""
        all_names: list[str] = []
        for mod in _MODULES:
            all_names.extend(mod.mcp._tool_manager._tools.keys())
        assert len(all_names) == len(set(all_names)), "Duplicate tool names found"
```

- [ ] **Step 7: Run tests to verify**

Run: `pytest tests/test_unified_server.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add mcp_server_kicad/server.py mcp_server_kicad/__main__.py mcp_server_kicad/__init__.py pyproject.toml tests/test_unified_server.py
git commit -m "feat: add unified MCP server entry point with conditional CLI tool registration"
```

---

## Chunk 4: Config + Docs + Final Validation

### Task 8: Update .mcp.json

**Files:**
- Modify: `.mcp.json`

Note: Version bumps in `pyproject.toml` and `.claude-plugin/plugin.json` are handled by the release workflow (Task 12).

- [ ] **Step 1: Replace `.mcp.json` content**

```json
{
  "kicad": {
    "command": "uvx",
    "args": ["--from", "mcp-server-kicad", "mcp-server-kicad"]
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add .mcp.json
git commit -m "chore: update .mcp.json for unified server"
```

---

### Task 9: Update README.md

**Files:**
- Modify: `README.md:104-221`

- [ ] **Step 1: Update the Available Tools section**

Replace lines 104-221 with the updated tool tables reflecting:
- Remove "Schematic Server", "PCB Server", etc. headers — use a single "KiCad Server (59 tools)" header
- Remove `add_no_connect` from Write Tools table
- Remove `add_power_rail` (was missing from table anyway)
- Remove `get_schematic_info` (was missing from table anyway)
- Update `export_pcb` description: `"Export PCB layers to PDF, SVG, or DXF"`
- Remove `export_gerber` row, update `export_gerbers` description: `"Export Gerber files (all layers or specific layer list)"`
- Remove `render_3d` row, update `export_3d` description: `"Export PCB 3D model (STEP/STL/GLB) or render 3D view to PNG"`
- Remove `export_pcb_dxf` row
- Add missing tools that were absent from the table (`connect_pins`, `no_connect_pin`, `remove_label`, `remove_wire`, `remove_junction`, `add_symbol`)
- Update server count header to reflect single server with 59 tools

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README tool tables for v0.5.0 (59 tools, single server)"
```

---

### Task 10: Verify instructions strings (no-op)

The schematic.py instructions reference `add_power_symbol` and `no_connect_pin` (both still valid). The pcb.py instructions already say `export_pcb` supports `pdf, svg, dxf` (line 48-49). No changes needed to instructions strings. Skip this task.

---

### Task 11: Full test suite + lint validation

- [ ] **Step 1: Run full test suite**

Run: `pytest -x -v`
Expected: All PASS

- [ ] **Step 2: Run ruff check**

Run: `ruff check mcp_server_kicad/ tests/`
Expected: No errors

- [ ] **Step 3: Run ruff format check**

Run: `ruff format --check mcp_server_kicad/ tests/`
Expected: No reformatting needed (run `ruff format` to fix if needed)

- [ ] **Step 4: Run pyright**

Run: `pyright mcp_server_kicad/`
Expected: No errors

- [ ] **Step 5: Final commit if any lint fixes were needed**

```bash
git add -u
git commit -m "fix: resolve lint/format issues for v0.5.0"
```

---

### Task 12: Release

- [ ] **Step 1: Trigger the release workflow**

Run: `gh workflow run release.yml --field bump=minor`

- [ ] **Step 2: Verify the workflow started**

Run: `gh run list --workflow=release.yml --limit=1`
