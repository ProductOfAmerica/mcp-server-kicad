"""Tests for CLI PCB export tools."""

import csv
import io
from fractions import Fraction
from pathlib import Path

import geom_gen as G
import pcbnew_judge as PJ
import pytest
from conftest import requires_cli
from kicad_ref import dsn_lexer as L
from kicad_ref import footprint as FP
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import pcb


@requires_cli
class TestExportGerbers:
    def test_returns_result(self, scratch_pcb, tmp_path):
        result = pcb.export_gerbers(str(scratch_pcb), str(tmp_path / "gerbers"))
        assert result.format == "gerber"

    def test_without_drill(self, scratch_pcb, tmp_path):
        result = pcb.export_gerbers(
            str(scratch_pcb), str(tmp_path / "gerbers"), include_drill=False
        )
        assert result.drill_files == []


@requires_cli
class TestExportGerberSingleLayer:
    def test_returns_result(self, scratch_pcb, tmp_path):
        result = pcb.export_gerbers(str(scratch_pcb), str(tmp_path), layers=["F.Cu"])
        assert result.format == "gerber"

    def test_creates_missing_output_dir(self, scratch_pcb, tmp_path):
        """Single-layer export must create output_dir, like the multi-layer path does."""
        result = pcb.export_gerbers(str(scratch_pcb), str(tmp_path / "fresh"), layers=["F.Cu"])
        assert result.format == "gerber"

    def test_writes_only_the_named_gerber(self, scratch_pcb, tmp_path):
        """No .gbrjob sidecar or scratch dir may survive, and the name is the promised one."""
        out = tmp_path / "out"
        result = pcb.export_gerbers(str(scratch_pcb), str(out), layers=["F.Cu"])
        assert [p.name for p in out.iterdir()] == ["scratch-F_Cu.gbr"]
        assert result.path.endswith("scratch-F_Cu.gbr")

    def test_bogus_layer_name_is_an_error(self, scratch_pcb, tmp_path):
        """kicad-cli exits 0 for an unknown layer and just writes no gerber."""
        with pytest.raises(ToolError, match="'Nope.Cu'"):
            pcb.export_gerbers(str(scratch_pcb), str(tmp_path / "out"), layers=["Nope.Cu"])


class TestExportGerberSingleLayerValidation:
    @pytest.mark.parametrize("layer", ["", "   "])
    def test_blank_layer_name_is_an_error(self, scratch_pcb, tmp_path, layer):
        """kicad-cli plots every enabled layer when --layers is blank; refuse before invoking."""
        with pytest.raises(ToolError):
            pcb.export_gerbers(str(scratch_pcb), str(tmp_path / "out"), layers=[layer])


@requires_cli
class TestExportGerbersLayerFilter:
    def test_multi_layer_filter(self, scratch_pcb, tmp_path):
        result = pcb.export_gerbers(
            str(scratch_pcb), str(tmp_path / "gerbers"), layers=["F.Cu", "B.Cu"]
        )
        assert result.format == "gerber"


@requires_cli
class TestExportPcbPdf:
    def test_returns_result(self, scratch_pcb, tmp_path):
        result = pcb.export_pcb(
            format="pdf",
            pcb_path=str(scratch_pcb),
            output_dir=str(tmp_path),
            layers=["F.Cu"],
        )
        assert result.format == "pdf"


@requires_cli
class TestExportPcbSvg:
    def test_returns_result(self, scratch_pcb, tmp_path):
        result = pcb.export_pcb(
            format="svg",
            pcb_path=str(scratch_pcb),
            output_dir=str(tmp_path),
            layers=["F.Cu"],
        )
        assert result.format == "svg"


MM = 1_000_000  # nanometres per millimetre

#: (reference, (x, y) in nm, angle in degrees, on the back): off the grid, negative and
#: rotated, so a position read in inches or with a sign slip cannot match by chance.
_PLACED = (
    ("R1", (110_490_000, 78_867_000), 0.0, False),
    ("R2", (-12_345_678, 5_432_109), 90.0, False),
    ("R3", (1_500_000, -2_250_001), 37.5, True),
    ("R4", (-200_000_001, -99_999_999), 180.0, True),
)


def _positions_board(tmp_path: Path) -> Path:
    """A board with the footprints of _PLACED, the back ones flipped by their layer."""
    items = []
    for ref, at, angle, back in _PLACED:
        text = G.footprint_text(ref, at, angle, pads=[("1", (0, 0), 0.0)])
        items.append(text.replace('(layer "F.Cu")', '(layer "B.Cu")', 1) if back else text)
    board = tmp_path / "placed.kicad_pcb"
    board.write_text(G.board_text(items), encoding="utf-8")
    return board


def _boards_own_footprints(board: Path) -> dict[str, tuple[int, int, float, str]]:
    """{reference: (x nm, y nm, orientation, layer)} as the board file holds each footprint,
    read with the port of KiCad's board parser (tests/kicad_ref/footprint.py)."""
    data = board.read_bytes()
    root = L.tree(data, knows_bar=L.knows_bar_of(data))[0]
    held = {}
    for group in FP.children(root, b"footprint"):
        read = FP.read_footprint(group, major=9, copper_count=2)
        ref, _value = FP.reference_and_value(group)
        held[ref] = (*read.position, read.orientation, read.layer)
    return held


_PCBNEW_FOOTPRINTS = """
import pcbnew
board = pcbnew.LoadBoard(payload["board"])
result = [
    [f.GetReference(), f.GetPosition().x, f.GetPosition().y, f.GetOrientationDegrees(),
     f.GetLayerName()]
    for f in board.GetFootprints()
]
"""


def _exported_rows(csv_path: str) -> dict[str, dict[str, str]]:
    """The position file's rows by Ref, every column as printed."""
    text = Path(csv_path).read_text(encoding="utf-8")
    return {row["Ref"]: row for row in csv.DictReader(io.StringIO(text))}


def _problems(rows, held) -> list[str]:
    """How each exported row differs from the footprint's position as (x nm, y nm,
    orientation, layer): PosX and PosY must be that position in millimetres, to the
    nanometre, with Y negated; Rot the orientation in degrees; Side the layer."""
    problems = []
    if set(rows) != set(held):
        problems.append(f"rows {sorted(rows)} are not the footprints {sorted(held)}")
    for ref, (x, y, orientation, layer) in held.items():
        row = rows.get(ref)
        if row is None:
            continue
        if Fraction(row["PosX"]) * MM != x or Fraction(row["PosY"]) * MM != -y:
            problems.append(
                f"{ref} is at ({x / MM}, {y / MM}) mm; PosX and PosY print"
                f" ({row['PosX']}, {row['PosY']}), which should be ({x / MM}, {-y / MM})"
            )
        turn = (float(row["Rot"]) - orientation) % 360
        if min(turn, 360 - turn) > 1e-6:
            problems.append(f"{ref} has orientation {orientation}; Rot prints {row['Rot']}")
        if row["Side"] != ("bottom" if layer == "B.Cu" else "top"):
            problems.append(f"{ref} is on {layer}; Side prints {row['Side']}")
    return problems


@requires_cli
class TestExportPositions:
    def test_returns_result(self, scratch_pcb, tmp_path):
        result = pcb.export_positions(str(scratch_pcb), str(tmp_path))
        assert result.path

    def test_prints_the_boards_own_positions_in_millimetres(self, tmp_path):
        """Bug 84: kicad-cli's position file is in inches unless told otherwise, and the
        tool told it nothing, so R1 at (110.49, 78.867) mm came back as 4.35, -3.105."""
        board = _positions_board(tmp_path)
        result = pcb.export_positions(str(board), str(tmp_path / "out"))
        assert result.component_count == len(_PLACED)
        header = Path(result.path).read_text(encoding="utf-8").splitlines()[0]
        assert header == "Ref,Val,Package,PosX,PosY,Rot,Side"
        assert _problems(_exported_rows(result.path), _boards_own_footprints(board)) == []

    @pytest.mark.skipif(PJ.skip_reason() is not None, reason=PJ.skip_reason() or "")
    def test_prints_the_positions_pcbnew_holds(self, tmp_path):
        """The same rows against pcbnew's FOOTPRINT: GetPosition in nanometres, the
        orientation in degrees, GetLayerName."""
        board = _positions_board(tmp_path)
        result = pcb.export_positions(str(board), str(tmp_path / "out"))
        loaded = PJ.run(_PCBNEW_FOOTPRINTS, {"board": str(board)})
        held = {ref: (x, y, deg, layer) for ref, x, y, deg, layer in loaded}
        assert _problems(_exported_rows(result.path), held) == []


def test_the_position_export_description_states_the_unit():
    description = pcb.mcp._tool_manager._tools["export_positions"].description
    assert "millimetre" in description


@requires_cli
class TestExportStep:
    def test_returns_result(self, scratch_pcb, tmp_path):
        result = pcb.export_3d(format="step", pcb_path=str(scratch_pcb), output_dir=str(tmp_path))
        assert result.format == "step"


@requires_cli
class TestExportStl:
    def test_returns_result(self, scratch_pcb, tmp_path):
        result = pcb.export_3d(format="stl", pcb_path=str(scratch_pcb), output_dir=str(tmp_path))
        assert result.format == "stl"


@requires_cli
class TestExportGlb:
    def test_returns_result(self, scratch_pcb, tmp_path):
        result = pcb.export_3d(format="glb", pcb_path=str(scratch_pcb), output_dir=str(tmp_path))
        assert result.format == "glb"


@requires_cli
class TestExport3dRender:
    def test_returns_result(self, scratch_pcb, tmp_path):
        result = pcb.export_3d(format="render", pcb_path=str(scratch_pcb), output_dir=str(tmp_path))
        assert result.format == "png"


class TestExportPcbInvalidFormat:
    """The enum stops a model sending these; the runtime check stops everyone
    else, because Literal is only enforced by pydantic at the MCP boundary.
    Passing a value the annotation forbids is the whole point, hence the
    ignores."""

    def test_export_pcb_invalid_format(self):
        with pytest.raises(ToolError):
            pcb.export_pcb(format="xyz")  # type: ignore[arg-type]

    def test_export_3d_invalid_format(self):
        with pytest.raises(ToolError):
            pcb.export_3d(format="obj")  # type: ignore[arg-type]
