"""KiCad symbol library MCP server."""

import math
import os
from pathlib import Path

from mcp.server.fastmcp.exceptions import ToolError

import mcp_server_kicad._cst as _cst
from mcp_server_kicad._cst import _fill_at, _num, _numish
from mcp_server_kicad._shared import (
    _ADDITIVE,
    _DESTRUCTIVE,
    _EXPORT,
    _READ_ONLY,
    OUTPUT_DIR,
    SYM_LIB_PATH,
    _run_cli,
    build_server,
)
from mcp_server_kicad.models import MultiFileExportResult, RectangleSpec, SymbolPinSpec

mcp = build_server(
    "kicad-symbol",
    instructions=(
        "KiCad symbol library tools for browsing, inspecting, exporting,"
        " upgrading, and authoring symbol libraries.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER read, edit, or write .kicad_sym files directly. Use these"
        " MCP tools for all symbol library operations.\n"
        "- NEVER run kicad-cli commands directly. Use export_symbol_svg and"
        " upgrade_symbol_lib instead.\n"
        "- Use list_lib_symbols to browse, get_symbol_info to inspect pin"
        " details, add_symbol to create new symbols."
        " Do NOT grep inside .kicad_sym files."
    ),
)

_VALID_PIN_TYPES = {
    "input",
    "output",
    "bidirectional",
    "passive",
    "power_in",
    "power_out",
    "tri_state",
    "open_collector",
    "open_emitter",
    "unconnected",
    "free",
}


# ── CST substrate ─────────────────────────────────────────────────
#
# Both templates are verbatim `kicad-cli sym upgrade` output from KiCad
# 9.0.8, with only the parameter slots blanked (slice 17). What KiCad
# writes and kiutils did not: version 20241209 with a quoted generator and
# a (generator_version "9.0"), tab indent, properties with no (id N),
# (hide yes) inside effects instead of a bare hide, an (effects ...) block
# on every pin name and number, and the (exclude_from_sim no) /
# (embedded_fonts no) shape tokens.
#
# (pin_names (offset X)) is in the template because KiCad writes it
# whenever the offset differs from its 0.508 mm default, and strips it at
# the default; the builder does the same. kiutils emitted it only when its
# unused pinNames flag was set, so the pin_names_offset parameter was a
# silent no-op before this slice.

_SYM_LIB_TPL = (
    b"(kicad_symbol_lib\n"
    b"\t(version 20241209)\n"
    b'\t(generator "kicad_symbol_editor")\n'
    b'\t(generator_version "9.0")\n'
    b")\n"
)

_DEFAULT_PIN_NAMES_OFFSET = 0.508

_LIB_SYMBOL_TPL = _cst.parse(
    b'(symbol "NAME"\n'
    b"\t\t(power)\n"
    b"\t\t(pin_names\n"
    b"\t\t\t(offset 0.508)\n"
    b"\t\t)\n"
    b"\t\t(exclude_from_sim no)\n"
    b"\t\t(in_bom yes)\n"
    b"\t\t(on_board yes)\n"
    b'\t\t(property "Reference" "U"\n'
    b"\t\t\t(at 0 -1.27 0)\n"
    b"\t\t\t(effects\n"
    b"\t\t\t\t(font\n"
    b"\t\t\t\t\t(size 1.27 1.27)\n"
    b"\t\t\t\t)\n"
    b"\t\t\t)\n"
    b"\t\t)\n"
    b'\t\t(property "Value" "NAME"\n'
    b"\t\t\t(at 0 1.27 0)\n"
    b"\t\t\t(effects\n"
    b"\t\t\t\t(font\n"
    b"\t\t\t\t\t(size 1.27 1.27)\n"
    b"\t\t\t\t)\n"
    b"\t\t\t)\n"
    b"\t\t)\n"
    b'\t\t(property "Footprint" ""\n'
    b"\t\t\t(at 0 0 0)\n"
    b"\t\t\t(effects\n"
    b"\t\t\t\t(font\n"
    b"\t\t\t\t\t(size 1.27 1.27)\n"
    b"\t\t\t\t)\n"
    b"\t\t\t\t(hide yes)\n"
    b"\t\t\t)\n"
    b"\t\t)\n"
    b'\t\t(property "Datasheet" ""\n'
    b"\t\t\t(at 0 0 0)\n"
    b"\t\t\t(effects\n"
    b"\t\t\t\t(font\n"
    b"\t\t\t\t\t(size 1.27 1.27)\n"
    b"\t\t\t\t)\n"
    b"\t\t\t\t(hide yes)\n"
    b"\t\t\t)\n"
    b"\t\t)\n"
    b'\t\t(symbol "NAME_0_1"\n'
    b"\t\t\t(rectangle\n"
    b"\t\t\t\t(start 0 0)\n"
    b"\t\t\t\t(end 0 0)\n"
    b"\t\t\t\t(stroke\n"
    b"\t\t\t\t\t(width 0.254)\n"
    b"\t\t\t\t\t(type default)\n"
    b"\t\t\t\t)\n"
    b"\t\t\t\t(fill\n"
    b"\t\t\t\t\t(type background)\n"
    b"\t\t\t\t)\n"
    b"\t\t\t)\n"
    b"\t\t)\n"
    b'\t\t(symbol "NAME_1_1"\n'
    b"\t\t\t(pin passive line\n"
    b"\t\t\t\t(at 0 0 0)\n"
    b"\t\t\t\t(length 2.54)\n"
    b'\t\t\t\t(name "~"\n'
    b"\t\t\t\t\t(effects\n"
    b"\t\t\t\t\t\t(font\n"
    b"\t\t\t\t\t\t\t(size 1.27 1.27)\n"
    b"\t\t\t\t\t\t)\n"
    b"\t\t\t\t\t)\n"
    b"\t\t\t\t)\n"
    b'\t\t\t\t(number "1"\n'
    b"\t\t\t\t\t(effects\n"
    b"\t\t\t\t\t\t(font\n"
    b"\t\t\t\t\t\t\t(size 1.27 1.27)\n"
    b"\t\t\t\t\t\t)\n"
    b"\t\t\t\t\t)\n"
    b"\t\t\t\t)\n"
    b"\t\t\t)\n"
    b"\t\t)\n"
    b"\t\t(embedded_fonts no)\n"
    b"\t)"
).lists[0]


def _open_sym_lib(symbol_lib_path: str):
    """(tree, root) for a .kicad_sym file; guard-free, works on any version."""
    tree = _cst.parse(Path(symbol_lib_path).read_bytes())
    root = tree.lists[0] if tree.lists else None
    if root is None or root.head != "kicad_symbol_lib":
        raise ToolError(f"{symbol_lib_path} is not a KiCad symbol library.")
    return tree, root


def _child_text(node, name: str) -> str:
    """Text of the first atom under child list *name*, or "" when absent."""
    child = node.find(name)
    return child.atoms[1].text if child is not None else ""


def _repeat(parent, model, count: int) -> list:
    """Replace *model* with *count* copies of itself, in place."""
    made = []
    ref = model
    for _ in range(count):
        clone = model.copy()
        parent.insert_after(ref, clone)
        made.append(clone)
        ref = clone
    parent.remove_child(model)
    return made


# ── Library browsing ──────────────────────────────────────────────


@mcp.tool(annotations=_READ_ONLY)
def list_lib_symbols(symbol_lib_path: str = SYM_LIB_PATH) -> str:
    """List all symbols in a .kicad_sym library file.

    Args:
        symbol_lib_path: Path to .kicad_sym file
    """
    _, root = _open_sym_lib(symbol_lib_path)
    lines = []
    for entry in root.find_all("symbol"):
        # Pins live in the unit sub-symbols, never on the entry itself.
        pin_count = sum(len(unit.find_all("pin")) for unit in entry.find_all("symbol"))
        lines.append(f"{entry.atoms[1].text} ({pin_count} pins)")
    return "\n".join(lines) if lines else "No symbols found."


@mcp.tool(annotations=_READ_ONLY, title="Symbol details from a library file")
def get_symbol_info(symbol_name: str, symbol_lib_path: str = SYM_LIB_PATH) -> str:
    """Get detailed pin and property info for a symbol in a library.

    Args:
        symbol_name: Symbol name (e.g. "LM7805")
        symbol_lib_path: Path to .kicad_sym file
    """
    _, root = _open_sym_lib(symbol_lib_path)
    for entry in root.find_all("symbol"):
        if entry.atoms[1].text != symbol_name:
            continue
        lines = [f"Symbol: {symbol_name}"]
        for prop in entry.find_all("property"):
            lines.append(f"  {prop.atoms[1].text}: {prop.atoms[2].text}")
        for unit in entry.find_all("symbol"):
            for pin in unit.find_all("pin"):
                at = pin.find("at")
                angle = _numish(at.atoms[3].text) if len(at.atoms) > 3 else 0
                lines.append(
                    f"  Pin {_child_text(pin, 'number')}: {_child_text(pin, 'name')} "
                    f"({pin.atoms[1].text}) "
                    f"@ ({_numish(at.atoms[1].text)}, {_numish(at.atoms[2].text)}) rot={angle}"
                )
        return "\n".join(lines)
    raise ToolError(f"'{symbol_name}' not found in {symbol_lib_path}.")


# ── Symbol authoring ─────────────────────────────────────────────


def _auto_body_rect(pins_data: list[SymbolPinSpec]) -> tuple[float, float, float, float]:
    """Compute a body rectangle from pin body-attachment points.

    Each pin extends from its position toward the body.  The body-end
    coordinate is ``position + length`` in the direction of the pin angle.
    The rectangle encloses all body-end points with a minimum size guarantee.
    """
    body_xs: list[float] = []
    body_ys: list[float] = []
    for p in pins_data:
        x, y = float(p.get("x", 0)), float(p.get("y", 0))
        length = float(p.get("length", 2.54))
        angle_rad = math.radians(float(p.get("rotation", 0)))
        body_xs.append(x + length * math.cos(angle_rad))
        body_ys.append(y + length * math.sin(angle_rad))

    if not body_xs:
        return (-2.54, -2.54, 2.54, 2.54)

    min_x, max_x = min(body_xs), max(body_xs)
    min_y, max_y = min(body_ys), max(body_ys)

    # Ensure minimum 2.54 mm in each dimension
    if max_x - min_x < 2.54:
        cx = (min_x + max_x) / 2
        min_x, max_x = cx - 1.27, cx + 1.27
    if max_y - min_y < 2.54:
        cy = (min_y + max_y) / 2
        min_y, max_y = cy - 1.27, cy + 1.27

    return (round(min_x, 4), round(min_y, 4), round(max_x, 4), round(max_y, 4))


@mcp.tool(annotations=_ADDITIVE)
def add_symbol(
    name: str,
    pins: list[SymbolPinSpec],
    reference_prefix: str = "U",
    is_power: bool = False,
    pin_names_offset: float = 0.508,
    in_bom: bool = True,
    on_board: bool = True,
    footprint: str = "",
    datasheet: str = "~",
    rectangles: list[RectangleSpec] | None = None,
    symbol_lib_path: str = SYM_LIB_PATH,
) -> str:
    """Add a new symbol definition to a .kicad_sym library.

    Creates a complete symbol with pins and body graphics.  If the library
    file does not exist it will be created.

    Args:
        name: Symbol name (e.g. "MP4572GQB-P", "TLV75733PDBVR")
        pins: Pin definitions — list of dicts, each with keys:
            number (str): pin number, e.g. "1"
            name (str): pin name, e.g. "VIN" ("~" for unnamed)
            type (str): electrical type — "input", "output", "bidirectional",
              "passive", "power_in", "power_out", "tri_state",
              "open_collector", "open_emitter", "unconnected", "free"
            x (float): X position in mm (default 0)
            y (float): Y position in mm (default 0)
            rotation (float): angle 0/90/180/270 (default 0)
            length (float): pin length in mm (default 2.54)
        reference_prefix: Reference prefix e.g. "U", "R", "C" (default "U")
        is_power: True for power symbols (default False)
        pin_names_offset: Pin name label offset in mm (default 0.508)
        in_bom: Include in BOM (default True)
        on_board: Place on board (default True)
        footprint: Default footprint e.g. "Package_SO:SOIC-8" (default "")
        datasheet: Datasheet URL (default "~")
        rectangles: Optional body rectangle(s) — list of dicts with keys:
            x1, y1, x2, y2 (float): corner coordinates in mm
            fill (str): "none", "background", or "outline" (default "background")
            If omitted, a rectangle is auto-computed from pin positions.
        symbol_lib_path: Path to .kicad_sym file
    """
    if not name:
        raise ToolError("symbol name is required.")
    if not pins:
        raise ToolError("at least one pin is required.")
    if not symbol_lib_path:
        raise ToolError("symbol_lib_path is required.")

    # Validate pins
    for i, p in enumerate(pins):
        for key in ("number", "name", "type"):
            if key not in p:
                raise ToolError(f"pin {i} missing required key '{key}'.")
        if p["type"] not in _VALID_PIN_TYPES:
            raise ToolError(
                f"pin {i} has invalid type '{p['type']}'. Valid: {sorted(_VALID_PIN_TYPES)}"
            )

    # Load or create the library. Existing bytes are never reformatted: the
    # new symbol is one spliced span and everything else reaches disk as-is.
    lib_path = Path(symbol_lib_path)
    if lib_path.exists():
        tree, root = _open_sym_lib(str(lib_path))
        for existing in root.find_all("symbol"):
            if existing.atoms[1].text == name:
                raise ToolError(f"symbol '{name}' already exists in {symbol_lib_path}.")
    else:
        lib_path.parent.mkdir(parents=True, exist_ok=True)
        tree = _cst.parse(_SYM_LIB_TPL)
        root = tree.lists[0]

    node = _LIB_SYMBOL_TPL.copy()
    node.atoms[1].set_text(name)
    if not is_power:
        node.remove_child(node.find("power"))
    if pin_names_offset == _DEFAULT_PIN_NAMES_OFFSET:
        # KiCad omits the token at its own default; anything else it writes.
        node.remove_child(node.find("pin_names"))
    else:
        node.find("pin_names").find("offset").atoms[1].set_text(_num(pin_names_offset))
    node.find("in_bom").atoms[1].set_text("yes" if in_bom else "no")
    node.find("on_board").atoms[1].set_text("yes" if on_board else "no")

    values = {
        "Reference": reference_prefix,
        "Value": name,
        "Footprint": footprint,
        "Datasheet": datasheet,
    }
    for prop in node.find_all("property"):
        prop.atoms[2].set_text(values[prop.atoms[1].text])

    unit0, unit1 = node.find_all("symbol")
    unit0.atoms[1].set_text(f"{name}_0_1")
    unit1.atoms[1].set_text(f"{name}_1_1")

    if rectangles:
        rects = rectangles
    else:
        auto_x1, auto_y1, auto_x2, auto_y2 = _auto_body_rect(pins)
        rects = [RectangleSpec(x1=auto_x1, y1=auto_y1, x2=auto_x2, y2=auto_y2)]
    for rect_node, r in zip(_repeat(unit0, unit0.find("rectangle"), len(rects)), rects):
        start, end = rect_node.find("start"), rect_node.find("end")
        start.atoms[1].set_text(_num(r["x1"]))
        start.atoms[2].set_text(_num(r["y1"]))
        end.atoms[1].set_text(_num(r["x2"]))
        end.atoms[2].set_text(_num(r["y2"]))
        rect_node.find("fill").find("type").atoms[1].set_text(r.get("fill", "background"))

    for pin_node, p in zip(_repeat(unit1, unit1.find("pin"), len(pins)), pins):
        pin_node.atoms[1].set_text(p["type"])
        _fill_at(
            pin_node,
            float(p.get("x", 0)),
            float(p.get("y", 0)),
            float(p.get("rotation", 0)),
        )
        pin_node.find("length").atoms[1].set_text(_num(float(p.get("length", 2.54))))
        pin_node.find("name").atoms[1].set_text(p["name"])
        pin_node.find("number").atoms[1].set_text(p["number"])

    entries = root.find_all("symbol")
    if entries:
        root.insert_after(entries[-1], node)
    else:
        root.append_child(node, b"\n\t")
    lib_path.write_bytes(_cst.serialize(tree))

    return f"Added symbol '{name}' ({len(pins)} pins) to {symbol_lib_path}"


# ── Export & upgrade (wraps kicad-cli) ────────────────────────────


@mcp.tool(annotations=_EXPORT)
def export_symbol_svg(
    symbol_lib_path: str = SYM_LIB_PATH, output_dir: str = OUTPUT_DIR
) -> MultiFileExportResult:
    """Export symbol library to SVG images.

    Args:
        symbol_lib_path: Path to .kicad_sym file
        output_dir: Output directory
    """
    out = output_dir or str(Path(symbol_lib_path).parent)
    os.makedirs(out, exist_ok=True)
    _run_cli(["sym", "export", "svg", "--output", out, symbol_lib_path])
    svgs = sorted(Path(out).glob("*.svg"))
    return MultiFileExportResult(
        path=out,
        format="svg",
        files=[f.name for f in svgs],
        count=len(svgs),
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def upgrade_symbol_lib(symbol_lib_path: str) -> str:
    """Upgrade a symbol library to current KiCad format.

    Args:
        symbol_lib_path: Path to .kicad_sym file
    """
    _run_cli(["sym", "upgrade", symbol_lib_path])
    return f"Successfully upgraded {symbol_lib_path}"


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for mcp-server-kicad-symbol console script."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
