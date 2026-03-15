"""KiCad footprint library MCP server."""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server_kicad._shared import (
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    FP_LIB_PATH,
    OUTPUT_DIR,
    Footprint,
    FpArc,
    FpCircle,
    FpLine,
    FpPoly,
    FpRect,
    FpText,
    _courtyard_bbox,
    _run_cli,
)
from mcp_server_kicad.models import MultiFileExportResult

mcp = FastMCP(
    "kicad-footprint",
    instructions=(
        "KiCad footprint library tools for browsing, inspecting, exporting,"
        " and upgrading footprint libraries.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write .kicad_mod files directly. Use these"
        " MCP tools for all footprint library operations.\n"
        "- NEVER run kicad-cli commands directly. Use export_footprint_svg"
        " and upgrade_fp_lib instead.\n"
        "- Use list_lib_footprints to browse, get_footprint_details to"
        " inspect. Do NOT grep inside .pretty directories."
    ),
)


# ── Library browsing ──────────────────────────────────────────────


@mcp.tool(annotations=_READ_ONLY)
def list_lib_footprints(pretty_dir: str = FP_LIB_PATH) -> str:
    """List all footprints in a .pretty library directory.

    Args:
        pretty_dir: Path to .pretty directory containing .kicad_mod files
    """
    p = Path(pretty_dir)
    if not p.is_dir():
        return f"'{pretty_dir}' is not a directory."
    mods = sorted(p.glob("*.kicad_mod"))
    if not mods:
        return "No footprints found."
    lines = [f.stem for f in mods]
    return "\n".join(lines)


@mcp.tool(annotations=_READ_ONLY)
def get_footprint_info(footprint_path: str) -> str:
    """Get pad and outline details for a footprint .kicad_mod file.

    Args:
        footprint_path: Path to .kicad_mod file
    """
    fp = Footprint.from_file(footprint_path)
    lines = [f"Footprint: {fp.entryName}"]
    lines.append(f"  Layer: {fp.layer}")
    for pad in fp.pads:
        lines.append(
            f"  Pad {pad.number}: {pad.type} {pad.shape} "
            f"@ ({pad.position.X}, {pad.position.Y}) "
            f"size=({pad.size.X}, {pad.size.Y}) layers={pad.layers}"
        )

    # Courtyard bounding box
    crtyd = _courtyard_bbox(fp)
    if crtyd is not None:
        lines.append(
            f"  Courtyard: {crtyd['layer']} {crtyd['width']:.1f} x {crtyd['height']:.1f} mm "
            f"(bbox: {crtyd['min_x']:.1f}, {crtyd['min_y']:.1f} to "
            f"{crtyd['max_x']:.1f}, {crtyd['max_y']:.1f})"
        )

    # Keep-out zones
    for zone in fp.zones:
        if zone.keepoutSettings is None:
            continue
        ks = zone.keepoutSettings
        layer_str = ", ".join(zone.layers) if zone.layers else "none"
        lines.append(
            f"  Keep-out zone: layers=[{layer_str}] "
            f"footprints={ks.footprints} tracks={ks.tracks} "
            f"vias={ks.vias} pads={ks.pads} copperpour={ks.copperpour}"
        )
        if zone.polygons:
            coords = [(round(c.X, 3), round(c.Y, 3)) for c in zone.polygons[0].coordinates]
            lines.append(f"    polygon: {coords}")

    # Graphics summary — group non-CrtYd, non-FpText items by layer
    type_names = {
        FpLine: "line",
        FpRect: "rect",
        FpCircle: "circle",
        FpArc: "arc",
        FpPoly: "poly",
    }
    layer_counts: dict[str, dict[str, int]] = {}
    for item in fp.graphicItems:
        if isinstance(item, FpText):
            continue
        item_layer: str | None = getattr(item, "layer", None)
        if item_layer is None or item_layer.endswith(".CrtYd"):
            continue
        for cls, name in type_names.items():
            if isinstance(item, cls):
                counts = layer_counts.setdefault(item_layer, {})
                counts[name] = counts.get(name, 0) + 1
                break

    if layer_counts:
        parts: list[str] = []
        for layer_name, counts in layer_counts.items():
            items_str = ", ".join(f"{c} {n}{'s' if c > 1 else ''}" for n, c in counts.items())
            parts.append(f"{layer_name} ({items_str})")
        lines.append(f"  Graphics: {', '.join(parts)}")

    return "\n".join(lines)


# ── Export & upgrade (wraps kicad-cli) ────────────────────────────


@mcp.tool(annotations=_EXPORT)
def export_footprint_svg(
    footprint_path: str, output_dir: str = OUTPUT_DIR
) -> MultiFileExportResult:
    """Export footprint to SVG.

    Args:
        footprint_path: Path to .kicad_mod file
        output_dir: Output directory
    """
    out = output_dir or str(Path(footprint_path).parent)
    os.makedirs(out, exist_ok=True)
    _run_cli(["fp", "export", "svg", "--output", out, footprint_path])
    svgs = sorted(Path(out).glob("*.svg"))
    return MultiFileExportResult(
        path=out,
        format="svg",
        files=[f.name for f in svgs],
        count=len(svgs),
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def upgrade_footprint_lib(footprint_path: str) -> str:
    """Upgrade a footprint library to current KiCad format.

    Args:
        footprint_path: Path to .kicad_mod file or .pretty directory
    """
    _run_cli(["fp", "upgrade", footprint_path])
    return f"Successfully upgraded {footprint_path}"


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for mcp-server-kicad-footprint console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
