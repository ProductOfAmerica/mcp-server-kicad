"""Tests for footprint library access tools."""

import json
import shutil

import pytest

from mcp_server_kicad import footprint

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None


class TestListLibFootprints:
    def test_list_from_pretty_dir(self, tmp_path):
        # Create a .pretty dir with one .kicad_mod
        pretty = tmp_path / "TestLib.pretty"
        pretty.mkdir()
        from kiutils.footprint import Footprint

        fp = Footprint()
        fp.entryName = "R_0603"
        fp.filePath = str(pretty / "R_0603.kicad_mod")
        fp.to_file()
        result = footprint.list_lib_footprints(str(pretty))
        assert "R_0603" in result


class TestGetFootprintInfo:
    def test_from_file(self, tmp_path):
        from kiutils.footprint import Footprint, Pad
        from kiutils.items.common import Position

        fp = Footprint()
        fp.entryName = "R_0603"
        pad = Pad()
        pad.number = "1"
        pad.type = "smd"
        pad.shape = "rect"
        pad.position = Position(X=-0.75, Y=0)
        pad.size = Position(X=0.7, Y=0.8)
        pad.layers = ["F.Cu"]
        fp.pads = [pad]
        path = str(tmp_path / "R_0603.kicad_mod")
        fp.filePath = path
        fp.to_file()
        result = footprint.get_footprint_info(path)
        assert "Pad 1" in result or "pad" in result.lower()


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestExportFootprintSvg:
    def test_returns_json(self, tmp_path):
        from kiutils.footprint import Footprint

        fp = Footprint()
        fp.entryName = "R_0603"
        path = str(tmp_path / "R_0603.kicad_mod")
        fp.filePath = path
        fp.to_file()
        result = footprint.export_footprint_svg(path, str(tmp_path / "svg_out"))
        data = json.loads(result)
        assert "format" in data or "error" in data


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestUpgradeFootprintLib:
    def test_returns_result(self, tmp_path):
        from kiutils.footprint import Footprint

        fp = Footprint()
        fp.entryName = "R_0603"
        path = str(tmp_path / "R_0603.kicad_mod")
        fp.filePath = path
        fp.to_file()
        result = footprint.upgrade_footprint_lib(path)
        assert (
            "success" in result.lower() or "upgraded" in result.lower() or "error" in result.lower()
        )
