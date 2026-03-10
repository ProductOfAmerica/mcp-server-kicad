"""KiCad Export MCP Server — CLI export, analysis, and utility tools."""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    OUTPUT_DIR,
    PCB_PATH,
    SCH_PATH,
    SYM_LIB_PATH,
    _file_meta,
    _run_cli,
)

mcp = FastMCP(
    "kicad-export",
    instructions="KiCad CLI export, analysis, and utility tools wrapping kicad-cli",
)


# ---------------------------------------------------------------------------
# CLI analysis tools (3)
# ---------------------------------------------------------------------------


def _annotate_erc_violations(violations: list[dict]) -> list[dict]:
    """Annotate ERC violations with contextual hints.

    Marks "hierarchical label cannot connect to non-existent parent sheet"
    violations as expected sub-sheet behavior.
    """
    for v in violations:
        desc = v.get("description", "")
        if "cannot be connected to non-existent parent sheet" in desc:
            v["expected_subsheet_issue"] = True
            v["hint"] = (
                "Expected when running ERC on a sub-sheet standalone. "
                "This resolves when opened from the parent schematic."
            )
    return violations


def _parse_unconnected_pins(erc_report: dict) -> list[dict]:
    """Extract unconnected pin violations from an ERC report.

    Filters out sub-sheet hierarchical label noise.
    """
    results = []
    for sheet in erc_report.get("sheets", []):
        for v in sheet.get("violations", []):
            desc = v.get("description", "")
            if "not connected" not in desc.lower():
                continue
            if "non-existent parent sheet" in desc:
                continue
            entry: dict = {"description": desc, "severity": v.get("severity", "")}
            items = v.get("items", [])
            if items:
                item_desc = items[0].get("description", "")
                entry["detail"] = item_desc
                pos = items[0].get("pos", {})
                if pos:
                    entry["x"] = pos.get("x")
                    entry["y"] = pos.get("y")
            results.append(entry)
    return results


@mcp.tool()
def list_unconnected_pins(
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
) -> str:
    """List unconnected pins by running ERC and filtering results.

    Requires kicad-cli. Filters out expected sub-sheet hierarchical
    label noise.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Directory for ERC report file
    """
    import shutil

    if not shutil.which("kicad-cli"):
        return json.dumps({"error": "kicad-cli not found"}, indent=2)

    out_dir = output_dir or str(Path(schematic_path).parent)
    out_path = str(
        Path(out_dir) / (Path(schematic_path).stem + "-erc.json")
    )
    _run_cli(
        [
            "sch", "erc", "--format", "json", "--severity-all",
            "--output", out_path, schematic_path,
        ],
        check=False,
    )
    try:
        with open(out_path) as f:
            report = json.load(f)
    except FileNotFoundError:
        return json.dumps(
            {"error": "ERC failed to produce output"}, indent=2
        )

    pins = _parse_unconnected_pins(report)
    return json.dumps(
        {"unconnected_count": len(pins), "pins": pins}, indent=2
    )


@mcp.tool()
def run_erc(schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Run Electrical Rules Check (ERC) on a schematic.

    Returns JSON report with violations.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Directory for report file (default: same as schematic)
    """
    out_dir = output_dir or str(Path(schematic_path).parent)
    out_path = str(Path(out_dir) / (Path(schematic_path).stem + "-erc.json"))
    _run_cli(
        ["sch", "erc", "--format", "json", "--severity-all", "--output", out_path, schematic_path],
        check=False,
    )
    try:
        with open(out_path) as f:
            report = json.load(f)
    except FileNotFoundError:
        return json.dumps({"error": "ERC failed to produce output file"}, indent=2)
    all_violations = []
    for sheet in report.get("sheets", []):
        all_violations.extend(sheet.get("violations", []))
    all_violations = _annotate_erc_violations(all_violations)
    return json.dumps(
        {
            "source": report.get("source", ""),
            "kicad_version": report.get("kicad_version", ""),
            "violation_count": len(all_violations),
            "violations": all_violations,
        },
        indent=2,
    )


@mcp.tool()
def run_drc(pcb_path: str = PCB_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Run Design Rules Check (DRC) on a PCB.

    Returns JSON report with violations.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Directory for report file (default: same as PCB)
    """
    out_dir = output_dir or str(Path(pcb_path).parent)
    out_path = str(Path(out_dir) / (Path(pcb_path).stem + "-drc.json"))
    _run_cli(
        ["pcb", "drc", "--format", "json", "--severity-all", "--output", out_path, pcb_path],
        check=False,
    )
    try:
        with open(out_path) as f:
            report = json.load(f)
    except FileNotFoundError:
        return json.dumps({"error": "DRC failed to produce output file"}, indent=2)
    all_violations = []
    for sheet in report.get("sheets", []):
        all_violations.extend(sheet.get("violations", []))
    return json.dumps(
        {
            "source": report.get("source", ""),
            "kicad_version": report.get("kicad_version", ""),
            "violation_count": len(all_violations),
            "violations": all_violations,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# CLI schematic export tools (5)
# ---------------------------------------------------------------------------


@mcp.tool()
def export_schematic_pdf(schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export schematic to PDF.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Output directory (default: same as schematic)
    """
    out_dir = output_dir or str(Path(schematic_path).parent)
    out_path = str(Path(out_dir) / (Path(schematic_path).stem + ".pdf"))
    _run_cli(["sch", "export", "pdf", "--output", out_path, schematic_path])
    meta = _file_meta(out_path)
    meta["format"] = "pdf"
    return json.dumps(meta, indent=2)


@mcp.tool()
def export_schematic_svg(schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export schematic to SVG.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Output directory (default: same as schematic)
    """
    out_dir = output_dir or str(Path(schematic_path).parent)
    os.makedirs(out_dir, exist_ok=True)
    _run_cli(["sch", "export", "svg", "--output", out_dir, schematic_path])
    svgs = sorted(Path(out_dir).glob("*.svg"))
    return json.dumps(
        {
            "path": out_dir,
            "format": "svg",
            "files": [f.name for f in svgs],
            "count": len(svgs),
        },
        indent=2,
    )


@mcp.tool()
def export_schematic_netlist(
    schematic_path: str = SCH_PATH,
    output_dir: str = OUTPUT_DIR,
    format: str = "kicadxml",
) -> str:
    """Export schematic netlist.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Output directory
        format: Netlist format: kicadxml, cadstar, orcadpcb2
    """
    out_dir = output_dir or str(Path(schematic_path).parent)
    ext = ".xml" if format == "kicadxml" else ".net"
    out_path = str(Path(out_dir) / (Path(schematic_path).stem + ext))
    _run_cli(["sch", "export", "netlist", "--format", format, "--output", out_path, schematic_path])
    meta = _file_meta(out_path)
    meta["format"] = format
    return json.dumps(meta, indent=2)


@mcp.tool()
def export_bom(schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export Bill of Materials (BOM) as CSV.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Output directory
    """
    out_dir = output_dir or str(Path(schematic_path).parent)
    out_path = str(Path(out_dir) / (Path(schematic_path).stem + "-bom.csv"))
    _run_cli(["sch", "export", "bom", "--output", out_path, schematic_path])
    meta = _file_meta(out_path)
    meta["format"] = "csv"
    with open(out_path) as f:
        lines = f.readlines()
    meta["component_count"] = max(0, len(lines) - 1)  # minus header
    return json.dumps(meta, indent=2)


@mcp.tool()
def export_schematic_dxf(schematic_path: str = SCH_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export schematic to DXF.

    Args:
        schematic_path: Path to .kicad_sch file
        output_dir: Output directory
    """
    out_dir = output_dir or str(Path(schematic_path).parent)
    out_path = str(Path(out_dir) / (Path(schematic_path).stem + ".dxf"))
    _run_cli(["sch", "export", "dxf", "--output", out_path, schematic_path])
    meta = _file_meta(out_path)
    meta["format"] = "dxf"
    return json.dumps(meta, indent=2)


# ---------------------------------------------------------------------------
# CLI PCB export tools (10)
# ---------------------------------------------------------------------------


@mcp.tool()
def export_gerbers(pcb_path: str = PCB_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export Gerber files for all layers.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory for gerber files
    """
    try:
        out = output_dir or str(Path(pcb_path).parent / "gerbers")
        os.makedirs(out, exist_ok=True)
        _run_cli(["pcb", "export", "gerbers", "--output", out, pcb_path])
        files = sorted(Path(out).glob("*"))
        return json.dumps(
            {
                "path": out,
                "format": "gerber",
                "files": [f.name for f in files],
                "count": len(files),
            },
            indent=2,
        )
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
def export_gerber(
    pcb_path: str = PCB_PATH,
    layer: str = "F.Cu",
    output_dir: str = OUTPUT_DIR,
) -> str:
    """Export a single Gerber file for one layer.

    Args:
        pcb_path: Path to .kicad_pcb file
        layer: Layer name (e.g. "F.Cu", "B.Cu", "F.SilkS")
        output_dir: Output directory
    """
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / f"{Path(pcb_path).stem}-{layer.replace('.', '_')}.gbr")
        _run_cli(["pcb", "export", "gerber", "--layers", layer, "--output", out_path, pcb_path])
        meta = _file_meta(out_path)
        meta.update({"format": "gerber", "layer": layer})
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": "gerber", "layer": layer}, indent=2)


@mcp.tool()
def export_drill(pcb_path: str = PCB_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export drill files.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
    """
    try:
        out = output_dir or str(Path(pcb_path).parent)
        os.makedirs(out, exist_ok=True)
        _run_cli(["pcb", "export", "drill", "--output", out, pcb_path])
        files = sorted(Path(out).glob("*.drl")) + sorted(Path(out).glob("*.DRL"))
        return json.dumps(
            {
                "path": out,
                "format": "drill",
                "files": [f.name for f in files],
                "count": len(files),
            },
            indent=2,
        )
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
def export_pcb_pdf(
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    layers: list[str] | None = None,
) -> str:
    """Export PCB layers to PDF.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
        layers: List of layers to include (default: ["F.Cu", "B.Cu"])
    """
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + ".pdf"))
        layer_list = layers or ["F.Cu", "B.Cu"]
        _run_cli(
            [
                "pcb",
                "export",
                "pdf",
                "--layers",
                ",".join(layer_list),
                "--output",
                out_path,
                pcb_path,
            ]
        )
        meta = _file_meta(out_path)
        meta.update({"format": "pdf", "layers": layer_list})
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": "pdf"}, indent=2)


@mcp.tool()
def export_pcb_svg(
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    layers: list[str] | None = None,
) -> str:
    """Export PCB layers to SVG.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
        layers: List of layers (default: ["F.Cu"])
    """
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + ".svg"))
        layer_list = layers or ["F.Cu"]
        _run_cli(
            [
                "pcb",
                "export",
                "svg",
                "--layers",
                ",".join(layer_list),
                "--output",
                out_path,
                pcb_path,
            ]
        )
        meta = _file_meta(out_path)
        meta.update({"format": "svg", "layers": layer_list})
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": "svg"}, indent=2)


@mcp.tool()
def export_positions(pcb_path: str = PCB_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export component position file (pick and place).

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
    """
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + "-pos.csv"))
        _run_cli(["pcb", "export", "pos", "--format", "csv", "--output", out_path, pcb_path])
        meta = _file_meta(out_path)
        meta["format"] = "csv"
        with open(out_path) as f:
            meta["component_count"] = max(0, len(f.readlines()) - 1)
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
def export_step(pcb_path: str = PCB_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export PCB as STEP 3D model.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
    """
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + ".step"))
        _run_cli(["pcb", "export", "step", "--output", out_path, pcb_path])
        meta = _file_meta(out_path)
        meta["format"] = "step"
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": "step"}, indent=2)


@mcp.tool()
def export_stl(pcb_path: str = PCB_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export PCB as STL 3D model.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
    """
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + ".stl"))
        _run_cli(["pcb", "export", "stl", "--output", out_path, pcb_path])
        meta = _file_meta(out_path)
        meta["format"] = "stl"
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": "stl"}, indent=2)


@mcp.tool()
def export_glb(pcb_path: str = PCB_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export PCB as GLB (binary GLTF) 3D model.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
    """
    try:
        out_dir = output_dir or str(Path(pcb_path).parent)
        out_path = str(Path(out_dir) / (Path(pcb_path).stem + ".glb"))
        _run_cli(["pcb", "export", "glb", "--output", out_path, pcb_path])
        meta = _file_meta(out_path)
        meta["format"] = "glb"
        return json.dumps(meta, indent=2)
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": "glb"}, indent=2)


@mcp.tool()
def render_3d(
    pcb_path: str = PCB_PATH,
    output_dir: str = OUTPUT_DIR,
    width: int = 1600,
    height: int = 900,
    side: str = "top",
    quality: str = "basic",
) -> str:
    """Render PCB 3D view to image.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Output directory
        width: Image width in pixels
        height: Image height in pixels
        side: View side: top, bottom, left, right, front, back
        quality: Render quality: basic, high
    """
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


# ---------------------------------------------------------------------------
# CLI symbol/footprint export tools (2)
# ---------------------------------------------------------------------------


@mcp.tool()
def export_symbol_svg(symbol_lib_path: str = SYM_LIB_PATH, output_dir: str = OUTPUT_DIR) -> str:
    """Export symbol library to SVG images.

    Args:
        symbol_lib_path: Path to .kicad_sym file
        output_dir: Output directory
    """
    try:
        out = output_dir or str(Path(symbol_lib_path).parent)
        os.makedirs(out, exist_ok=True)
        _run_cli(["sym", "export", "svg", "--output", out, symbol_lib_path])
        svgs = sorted(Path(out).glob("*.svg"))
        return json.dumps(
            {
                "path": out,
                "format": "svg",
                "files": [f.name for f in svgs],
                "count": len(svgs),
            },
            indent=2,
        )
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": "svg"}, indent=2)


@mcp.tool()
def export_footprint_svg(footprint_path: str, output_dir: str = OUTPUT_DIR) -> str:
    """Export footprint to SVG.

    Args:
        footprint_path: Path to .kicad_mod file
        output_dir: Output directory
    """
    try:
        out = output_dir or str(Path(footprint_path).parent)
        os.makedirs(out, exist_ok=True)
        _run_cli(["fp", "export", "svg", "--output", out, footprint_path])
        svgs = sorted(Path(out).glob("*.svg"))
        return json.dumps(
            {
                "path": out,
                "format": "svg",
                "files": [f.name for f in svgs],
                "count": len(svgs),
            },
            indent=2,
        )
    except (RuntimeError, FileNotFoundError) as e:
        return json.dumps({"error": str(e), "format": "svg"}, indent=2)


# ---------------------------------------------------------------------------
# CLI utility tools (3)
# ---------------------------------------------------------------------------


@mcp.tool()
def upgrade_symbol_lib(symbol_lib_path: str) -> str:
    """Upgrade a symbol library to current KiCad format.

    Args:
        symbol_lib_path: Path to .kicad_sym file
    """
    try:
        _run_cli(["sym", "upgrade", symbol_lib_path])
        return f"Successfully upgraded {symbol_lib_path}"
    except RuntimeError as e:
        return f"Error: {e}"


@mcp.tool()
def upgrade_footprint_lib(footprint_path: str) -> str:
    """Upgrade a footprint library to current KiCad format.

    Args:
        footprint_path: Path to .kicad_mod file or .pretty directory
    """
    try:
        _run_cli(["fp", "upgrade", footprint_path])
        return f"Successfully upgraded {footprint_path}"
    except RuntimeError as e:
        return f"Error: {e}"


@mcp.tool()
def run_jobset(jobset_path: str) -> str:
    """Run a KiCad jobset file.

    Args:
        jobset_path: Path to .kicad_jobset file
    """
    try:
        result = _run_cli(["jobset", "run", jobset_path])
        return f"Jobset completed successfully.\n{result.stdout}"
    except RuntimeError as e:
        return f"Jobset failed: {e}"


def main():
    """Entry point for mcp-server-kicad-export console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
