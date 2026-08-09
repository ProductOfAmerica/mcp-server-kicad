"""Tests for CLI PCB export tools."""

import pytest
from conftest import requires_cli
from mcp.server.fastmcp.exceptions import ToolError

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


@requires_cli
class TestExportPositions:
    def test_returns_result(self, scratch_pcb, tmp_path):
        result = pcb.export_positions(str(scratch_pcb), str(tmp_path))
        assert result.path


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
    def test_export_pcb_invalid_format(self):
        with pytest.raises(ToolError):
            pcb.export_pcb(format="xyz")

    def test_export_3d_invalid_format(self):
        with pytest.raises(ToolError):
            pcb.export_3d(format="obj")
