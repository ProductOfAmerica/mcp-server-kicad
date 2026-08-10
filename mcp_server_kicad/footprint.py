"""KiCad footprint library MCP server."""

import os
from pathlib import Path

from mcp.server.fastmcp.exceptions import ToolError

import mcp_server_kicad._cst as _cst
from mcp_server_kicad._cst import _numish
from mcp_server_kicad._shared import (
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    FP_LIB_PATH,
    OUTPUT_DIR,
    _courtyard_bbox_cst,
    _keepout_dict,
    _run_cli,
    build_server,
)
from mcp_server_kicad.models import MultiFileExportResult

mcp = build_server(
    "kicad-footprint",
    instructions=(
        "KiCad footprint library tools for browsing, inspecting, exporting,"
        " and upgrading footprint libraries.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write .kicad_mod files directly. Use these"
        " MCP tools for all footprint library operations.\n"
        "- NEVER run kicad-cli commands directly. Use export_footprint_svg"
        " and upgrade_footprint_lib instead.\n"
        "- Use list_lib_footprints to browse, get_footprint_info to"
        " inspect. Do NOT grep inside .pretty directories."
    ),
)


# ── CST substrate ─────────────────────────────────────────────────

# Graphic heads the summary counts, and the noun each prints as. fp_text and
# the KiCad 9 (property ...) fields are absent on purpose: they were never
# counted.
_GRAPHIC_NAMES = {
    "fp_line": "line",
    "fp_rect": "rect",
    "fp_circle": "circle",
    "fp_arc": "arc",
    "fp_poly": "poly",
}


def _open_footprint(footprint_path: str):
    """Root node of a .kicad_mod, which IS the footprint node. Guard-free."""
    tree = _cst.parse(Path(footprint_path).read_bytes())
    root = tree.lists[0] if tree.lists else None
    if root is None or root.head != "footprint":
        raise ToolError(f"{footprint_path} is not a KiCad footprint.")
    return root


def _layer_list(node) -> list[str]:
    """Layer names of a (layers ...) or (layer ...) node, quoted or bare."""
    return [a.text for a in node.atoms[1:]] if node is not None else []


# ── Library browsing ──────────────────────────────────────────────


@mcp.tool(annotations=_READ_ONLY)
def list_lib_footprints(pretty_dir: str = FP_LIB_PATH) -> str:
    """List all footprints in a .pretty library directory.

    Args:
        pretty_dir: Path to .pretty directory containing .kicad_mod files
    """
    p = Path(pretty_dir)
    if not p.is_dir():
        raise ToolError(f"'{pretty_dir}' is not a directory.")
    mods = sorted(p.glob("*.kicad_mod"))
    if not mods:
        return "No footprints found."
    lines = [f.stem for f in mods]
    return "\n".join(lines)


@mcp.tool(annotations=_READ_ONLY, title="Footprint details from a .kicad_mod file")
def get_footprint_info(footprint_path: str) -> str:
    """Get pad and outline details for a footprint .kicad_mod file.

    Args:
        footprint_path: Path to .kicad_mod file
    """
    fp = _open_footprint(footprint_path)
    layer = fp.find("layer")
    lines = [f"Footprint: {fp.atoms[1].text}"]
    lines.append(f"  Layer: {layer.atoms[1].text if layer is not None else 'F.Cu'}")
    for pad in fp.find_all("pad"):
        at, size, layers = pad.find("at"), pad.find("size"), pad.find("layers")
        lines.append(
            f"  Pad {pad.atoms[1].text}: {pad.atoms[2].text} {pad.atoms[3].text} "
            f"@ ({_numish(at.atoms[1].text)}, {_numish(at.atoms[2].text)}) "
            f"size=({_numish(size.atoms[1].text)}, {_numish(size.atoms[2].text)}) "
            f"layers={_layer_list(layers)}"
        )

    # Courtyard bounding box
    crtyd = _courtyard_bbox_cst(fp)
    if crtyd is not None:
        lines.append(
            f"  Courtyard: {crtyd['layer']} {crtyd['width']:.1f} x {crtyd['height']:.1f} mm "
            f"(bbox: {crtyd['min_x']:.1f}, {crtyd['min_y']:.1f} to "
            f"{crtyd['max_x']:.1f}, {crtyd['max_y']:.1f})"
        )

    # Keep-out zones
    for zone in fp.find_all("zone"):
        ko = zone.find("keepout")
        if ko is None:
            continue
        ks = _keepout_dict(ko)
        zone_layers = _layer_list(zone.find("layers") or zone.find("layer"))
        layer_str = ", ".join(zone_layers) if zone_layers else "none"
        lines.append(
            f"  Keep-out zone: layers=[{layer_str}] "
            f"footprints={ks['footprints']} tracks={ks['tracks']} "
            f"vias={ks['vias']} pads={ks['pads']} copperpour={ks['copperpour']}"
        )
        polygon = zone.find("polygon")
        if polygon is not None:
            pts = polygon.find("pts")
            coords = [
                (round(_numish(p.atoms[1].text), 3), round(_numish(p.atoms[2].text), 3))
                for p in (pts.find_all("xy") if pts is not None else [])
            ]
            lines.append(f"    polygon: {coords}")

    # Graphics summary — group non-CrtYd, non-text items by layer
    layer_counts: dict[str, dict[str, int]] = {}
    for item in fp.lists:
        name = _GRAPHIC_NAMES.get(item.head)
        if name is None:
            continue
        item_layer = item.find("layer")
        if item_layer is None or item_layer.atoms[1].text.endswith(".CrtYd"):
            continue
        counts = layer_counts.setdefault(item_layer.atoms[1].text, {})
        counts[name] = counts.get(name, 0) + 1

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
        footprint_path: Path to a .kicad_mod file, or a .pretty library directory
        output_dir: Output directory
    """
    src = Path(footprint_path)
    out = output_dir or str(src.parent)
    os.makedirs(out, exist_ok=True)
    # kicad-cli takes the library directory, never the .kicad_mod itself, so a
    # single file means "point at the parent and name the footprint".
    args = ["fp", "export", "svg", "--output", out]
    if src.is_file():
        args += ["--fp", src.stem, str(src.parent)]
    else:
        args.append(str(src))
    _run_cli(args)
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

    Rewrites every footprint in the library, so this takes the library
    directory.  kicad-cli has no per-footprint option here, and a single
    .kicad_mod path is rejected.

    Args:
        footprint_path: Path to a .pretty library directory
    """
    _run_cli(["fp", "upgrade", footprint_path])
    return f"Successfully upgraded {footprint_path}"


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for mcp-server-kicad-footprint console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
