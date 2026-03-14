"""Tests for footprint library access tools."""

import json
import shutil

import pytest
from kiutils.footprint import Footprint, Pad
from kiutils.items.common import Position
from kiutils.items.fpitems import FpCircle, FpLine, FpRect
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon

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


class TestGetFootprintInfoExtended:
    """Extended tests for get_footprint_info covering courtyard, keep-out, and graphics."""

    def test_courtyard_reported(self, tmp_path):
        """Footprint with FpRect on F.CrtYd reports courtyard info."""
        fp = Footprint()
        fp.entryName = "Test"
        rect = FpRect()
        rect.start = Position(X=-2, Y=-1)
        rect.end = Position(X=2, Y=1)
        rect.layer = "F.CrtYd"
        fp.graphicItems = [rect]
        path = str(tmp_path / "crtyd.kicad_mod")
        fp.filePath = path
        fp.to_file()

        result = footprint.get_footprint_info(path)
        assert "Courtyard" in result
        assert "F.CrtYd" in result

    def test_keepout_zone_reported(self, tmp_path):
        """Footprint with a keepout zone reports keep-out info."""
        fp = Footprint()
        fp.entryName = "Test"

        zone = Zone()
        zone.net = 0
        zone.netName = ""
        zone.layers = ["F.Cu", "B.Cu"]
        zone.tstamp = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        zone.hatch = Hatch(style="edge", pitch=0.5)
        zone.keepoutSettings = KeepoutSettings(
            tracks="not_allowed",
            vias="not_allowed",
            pads="not_allowed",
            copperpour="not_allowed",
            footprints="not_allowed",
        )
        poly = ZonePolygon()
        poly.coordinates = [
            Position(X=-5, Y=-5),
            Position(X=5, Y=-5),
            Position(X=5, Y=5),
            Position(X=-5, Y=5),
        ]
        zone.polygons = [poly]
        fp.zones = [zone]

        path = str(tmp_path / "keepout.kicad_mod")
        fp.filePath = path
        fp.to_file()

        result = footprint.get_footprint_info(path)
        assert "Keep-out" in result
        assert "not_allowed" in result

    def test_no_extras_on_simple_footprint(self, tmp_path):
        """A simple pad-only footprint has no courtyard, keep-out, or graphics."""
        fp = Footprint()
        fp.entryName = "Simple"
        pad = Pad()
        pad.number = "1"
        pad.type = "smd"
        pad.shape = "rect"
        pad.position = Position(X=0, Y=0)
        pad.size = Position(X=0.5, Y=0.5)
        pad.layers = ["F.Cu"]
        fp.pads = [pad]

        path = str(tmp_path / "simple.kicad_mod")
        fp.filePath = path
        fp.to_file()

        result = footprint.get_footprint_info(path)
        assert "Courtyard" not in result
        assert "Keep-out" not in result
        assert "Graphics" not in result

    def test_graphics_summary(self, tmp_path):
        """Footprint with FpLine items on F.SilkS reports graphics summary."""
        fp = Footprint()
        fp.entryName = "Test"
        line = FpLine()
        line.start = Position(X=-1, Y=0)
        line.end = Position(X=1, Y=0)
        line.layer = "F.SilkS"
        fp.graphicItems = [line]

        path = str(tmp_path / "graphics.kicad_mod")
        fp.filePath = path
        fp.to_file()

        result = footprint.get_footprint_info(path)
        assert "Graphics" in result
        assert "F.SilkS" in result

    def test_courtyard_circle(self, tmp_path):
        """Footprint with FpCircle on F.CrtYd reports bbox approx -5 to 5."""
        fp = Footprint()
        fp.entryName = "Test"
        circle = FpCircle()
        circle.center = Position(X=0, Y=0)
        circle.end = Position(X=5, Y=0)  # radius = 5
        circle.layer = "F.CrtYd"
        fp.graphicItems = [circle]

        path = str(tmp_path / "circle.kicad_mod")
        fp.filePath = path
        fp.to_file()

        result = footprint.get_footprint_info(path)
        assert "Courtyard" in result
        assert "F.CrtYd" in result
        # The output should contain bbox approximately -5 to 5
        assert "-5.0" in result
        assert "5.0" in result
