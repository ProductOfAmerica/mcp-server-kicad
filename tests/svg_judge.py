"""Where kicad-cli plots a schematic's texts: the anchors in its SVG export.

KiCad writes every text of an SVG twice (SVG_PLOTTER::Text, common/plotters/
SVG_plotter.cpp:833-944 at 9.0.8, 845-955 at 10.0.7): once as an invisible <text> element
and once as stroked glyphs under <g class="stroked-text"><desc>TEXT</desc>. This judge
reads the invisible element, which carries the plotted anchor exactly. The glyphs are no
measure of a field's position: KiCad draws text upright, so the glyph box does not turn
with the symbol. Measured on kicad-cli 9.0.8 (2026-10-14) over the 96 fields of
test_field_port_draws_where_kicad_cli_draws's sheet, the glyph box centre sat 0.0555 to
0.0857 mm from the anchor across the text, on the same side in every one of the 12 states.

- A placed symbol's field is plotted at GetBoundingBox().Centre(), centred both ways
  (SCH_FIELD::Plot, eeschema/sch_field.cpp:1528-1544 and 1573 at 9.0.8, 1310-1326 and 1355
  at 10.0.7): the centre of its text box in the symbol's frame, turned by the symbol's
  transform (GetBoundingBox, 704-729 at 9.0.8, 468-493 at 10.0.7). For centred text that is
  the field's drawn position, within the integer halving of the box.
- The <text> element sits at that anchor moved down by half the text height
  (text_pos.y += aSize.y / 2, SVG_plotter.cpp:870 at 9.0.8, 882 at 10.0.7); a turned text
  is wrapped in <g transform="rotate(-angle ax ay)"> whose centre is the anchor itself
  (893-902 and 927-928 at 9.0.8, 905-914 and 938-939 at 10.0.7).

anchors(path, texts, text_height=...) exports the root sheet with kicad-cli (no drawing
sheet) into a scratch directory and returns {text: [Anchor]} for the texts asked for, in
internal units. Every text asked for must be plotted that high: a turned one's offset is
checked against half of it. A structure other than the above (a mirrored text, a turn that
is not a quarter, a turned text off its anchor) raises RuntimeError: the SVG's shape is
unverified on kicad-cli 10, so a change there shows as an error, never as a pass or a
defect.

This module imports nothing from mcp_server_kicad but the kicad-cli runner.
"""

from __future__ import annotations

import html
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from mcp_server_kicad._shared import _run_cli

#: Internal units per millimetre in a schematic, and so in its SVG's millimetres.
IU_PER_MM = 10_000
#: How far (internal units) a plotted anchor may sit from a predicted one. The anchor is the
#: centre of an integer box, so within 1 of a centred field's drawn position, and printed to
#: 1 (0.0001 mm); 0.001 mm leaves room for a plotter that rounds differently.
TOLERANCE = 10

_TEXT = re.compile(
    r'(?:<g transform="rotate\((?P<angle>-?[\d.]+) (?P<cx>-?[\d.]+) (?P<cy>-?[\d.]+)\)">\s*)?'
    r'<text x="(?P<x>-?[\d.]+)" y="(?P<y>-?[\d.]+)"(?P<attrs>[^>]*)>(?P<text>[^<]*)</text>'
)


class Anchor(NamedTuple):
    """A plotted text's anchor in internal units, and whether it is drawn vertically."""

    x: int
    y: int
    vertical: bool


def _iu(text: str) -> int:
    return round(float(text) * IU_PER_MM)


def export_svg(path: Path, out: Path) -> str:
    """kicad-cli's SVG of the root sheet of ``path``, drawing sheet left out, written into the
    directory ``out``. RuntimeError when kicad-cli writes none, which is how it answers a
    schematic it cannot load."""
    result = _run_cli(
        ["sch", "export", "svg", "--exclude-drawing-sheet", "--output", str(out), str(path)],
        check=False,
    )
    svg = out / (path.stem + ".svg")
    if not svg.is_file():
        raise RuntimeError(
            f"kicad-cli wrote no SVG for {path.name} (rc={result.returncode}):"
            f" {result.stderr.strip()[:400]}"
        )
    return svg.read_text(encoding="utf-8")


def parse_anchors(svg: str, texts: Iterable[str], *, text_height: int) -> dict[str, list[Anchor]]:
    """{text: [Anchor]} of every plotted text of ``svg`` whose content is one of ``texts``,
    each plotted ``text_height`` internal units high."""
    wanted = set(texts)
    half = text_height // 2  # aSize.y / 2 in C++ integer division, for a positive height
    found: dict[str, list[Anchor]] = {text: [] for text in wanted}
    for m in _TEXT.finditer(svg):
        text = html.unescape(m["text"])
        if text not in wanted:
            continue
        if "transform=" in m["attrs"]:
            raise RuntimeError(f"{text!r} is plotted mirrored, which this judge does not read")
        x, y = _iu(m["x"]), _iu(m["y"])
        if m["angle"] is None:
            found[text].append(Anchor(x, y - half, False))
            continue
        if float(m["angle"]) not in (-90.0, 90.0):
            raise RuntimeError(f"{text!r} is plotted turned by {m['angle']}, not a quarter turn")
        cx, cy = _iu(m["cx"]), _iu(m["cy"])
        if abs(x - cx) > 1 or abs(y - cy - half) > 1:
            raise RuntimeError(
                f"{text!r} is plotted at ({x}, {y}) inside a turn about ({cx}, {cy}), not half"
                f" of {text_height} below its anchor: the premise of its text height or the"
                " SVG's structure has changed"
            )
        found[text].append(Anchor(cx, cy, True))
    return found


def anchors(path: str | Path, texts: Iterable[str], *, text_height: int) -> dict[str, list[Anchor]]:
    """parse_anchors of kicad-cli's SVG of the schematic at ``path``."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        svg = export_svg(Path(path), Path(scratch))
    return parse_anchors(svg, texts, text_height=text_height)
