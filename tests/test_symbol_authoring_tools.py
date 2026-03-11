"""Tests for symbol authoring tools on the symbol server."""

from kiutils.symbol import SymbolLib

from mcp_server_kicad import symbol
from mcp_server_kicad.symbol import _auto_body_rect

# ── Helper pin dicts ─────────────────────────────────────────────


def _two_pin_passive():
    """Return a minimal 2-pin passive pin list."""
    return [
        {"number": "1", "name": "IN", "type": "passive", "x": -5.08, "y": 0, "rotation": 0},
        {"number": "2", "name": "OUT", "type": "passive", "x": 5.08, "y": 0, "rotation": 180},
    ]


def _ic_pins():
    """Return an 8-pin IC-style pin list (buck converter example)."""
    return [
        {"number": "1", "name": "VIN", "type": "power_in", "x": -7.62, "y": 5.08, "rotation": 0},
        {"number": "2", "name": "EN", "type": "input", "x": -7.62, "y": 2.54, "rotation": 0},
        {"number": "3", "name": "BST", "type": "passive", "x": -7.62, "y": 0, "rotation": 0},
        {"number": "4", "name": "GND", "type": "power_in", "x": -7.62, "y": -2.54, "rotation": 0},
        {"number": "5", "name": "FB", "type": "input", "x": 7.62, "y": -2.54, "rotation": 180},
        {"number": "6", "name": "COMP", "type": "passive", "x": 7.62, "y": 0, "rotation": 180},
        {"number": "7", "name": "SW", "type": "output", "x": 7.62, "y": 2.54, "rotation": 180},
        {"number": "8", "name": "NC", "type": "passive", "x": 7.62, "y": 5.08, "rotation": 180},
    ]


# ── _auto_body_rect ──────────────────────────────────────────────


class TestAutoBodyRect:
    def test_two_horizontal_pins(self):
        pins = _two_pin_passive()
        x1, y1, x2, y2 = _auto_body_rect(pins)
        # Body should span between body-attachment points
        # Pin 1: (-5.08, 0) angle 0, length 2.54 → body end (-2.54, 0)
        # Pin 2: (5.08, 0) angle 180, length 2.54 → body end (2.54, 0)
        assert x1 <= -2.54
        assert x2 >= 2.54
        # Y dimension should have minimum size (2.54) since all pins at y=0
        assert y2 - y1 >= 2.54

    def test_vertical_pins(self):
        pins = [
            {"number": "1", "name": "~", "type": "passive", "x": 0, "y": 3.81, "rotation": 270},
            {"number": "2", "name": "~", "type": "passive", "x": 0, "y": -3.81, "rotation": 90},
        ]
        x1, y1, x2, y2 = _auto_body_rect(pins)
        # Body ends at (0, 1.27) and (0, -1.27)
        assert y1 <= -1.27
        assert y2 >= 1.27
        assert x2 - x1 >= 2.54  # minimum width

    def test_ic_pins(self):
        pins = _ic_pins()
        x1, y1, x2, y2 = _auto_body_rect(pins)
        # Left pins at x=-7.62, right pins at x=7.62, default length 2.54
        # Body ends: left at -5.08, right at 5.08
        assert x1 <= -5.08
        assert x2 >= 5.08

    def test_empty_pins(self):
        x1, y1, x2, y2 = _auto_body_rect([])
        assert (x1, y1, x2, y2) == (-2.54, -2.54, 2.54, 2.54)

    def test_single_pin(self):
        pins = [{"number": "1", "name": "A", "type": "input", "x": 0, "y": 0, "rotation": 0}]
        x1, y1, x2, y2 = _auto_body_rect(pins)
        # Should have minimum dimensions around the single body point
        assert x2 - x1 >= 2.54
        assert y2 - y1 >= 2.54


# ── add_symbol ───────────────────────────────────────────────────


class TestAddSymbol:
    def test_add_to_existing_lib(self, scratch_sym_lib):
        result = symbol.add_symbol(
            name="NewPart",
            pins=_two_pin_passive(),
            symbol_lib_path=str(scratch_sym_lib),
        )
        assert "Added symbol 'NewPart'" in result
        assert "2 pins" in result

        # Verify it's in the library alongside the existing symbol
        lib = SymbolLib.from_file(str(scratch_sym_lib))
        names = [s.entryName for s in lib.symbols]
        assert "TestPart" in names
        assert "NewPart" in names

    def test_add_creates_new_lib(self, tmp_path):
        lib_path = tmp_path / "brand_new.kicad_sym"
        assert not lib_path.exists()

        result = symbol.add_symbol(
            name="FirstPart",
            pins=_two_pin_passive(),
            symbol_lib_path=str(lib_path),
        )
        assert "Added symbol 'FirstPart'" in result
        assert lib_path.exists()

        lib = SymbolLib.from_file(str(lib_path))
        assert len(lib.symbols) == 1
        assert lib.symbols[0].entryName == "FirstPart"

    def test_creates_parent_dirs(self, tmp_path):
        lib_path = tmp_path / "sub" / "dir" / "lib.kicad_sym"
        result = symbol.add_symbol(
            name="Deep",
            pins=_two_pin_passive(),
            symbol_lib_path=str(lib_path),
        )
        assert "Added" in result
        assert lib_path.exists()

    def test_duplicate_name_rejected(self, scratch_sym_lib):
        result = symbol.add_symbol(
            name="TestPart",
            pins=_two_pin_passive(),
            symbol_lib_path=str(scratch_sym_lib),
        )
        assert "Error" in result
        assert "already exists" in result

    def test_empty_name(self, tmp_path):
        result = symbol.add_symbol(
            name="",
            pins=_two_pin_passive(),
            symbol_lib_path=str(tmp_path / "lib.kicad_sym"),
        )
        assert "Error" in result

    def test_empty_pins(self, tmp_path):
        result = symbol.add_symbol(
            name="NoPins",
            pins=[],
            symbol_lib_path=str(tmp_path / "lib.kicad_sym"),
        )
        assert "Error" in result

    def test_empty_lib_path(self):
        result = symbol.add_symbol(name="X", pins=_two_pin_passive(), symbol_lib_path="")
        assert "Error" in result

    def test_missing_pin_key(self, tmp_path):
        result = symbol.add_symbol(
            name="Bad",
            pins=[{"number": "1", "name": "A"}],  # missing "type"
            symbol_lib_path=str(tmp_path / "lib.kicad_sym"),
        )
        assert "Error" in result
        assert "type" in result

    def test_invalid_pin_type(self, tmp_path):
        result = symbol.add_symbol(
            name="Bad",
            pins=[{"number": "1", "name": "A", "type": "bogus"}],
            symbol_lib_path=str(tmp_path / "lib.kicad_sym"),
        )
        assert "Error" in result
        assert "bogus" in result

    def test_pin_defaults(self, tmp_path):
        """Pins with only required keys should use defaults for x/y/rotation/length."""
        lib_path = tmp_path / "defaults.kicad_sym"
        result = symbol.add_symbol(
            name="Minimal",
            pins=[{"number": "1", "name": "A", "type": "passive"}],
            symbol_lib_path=str(lib_path),
        )
        assert "Added" in result

        lib = SymbolLib.from_file(str(lib_path))
        pin = lib.symbols[0].units[1].pins[0]
        assert pin.position.X == 0
        assert pin.position.Y == 0
        assert pin.position.angle == 0
        assert pin.length == 2.54

    def test_custom_rectangles(self, tmp_path):
        lib_path = tmp_path / "rects.kicad_sym"
        result = symbol.add_symbol(
            name="CustomBody",
            pins=_two_pin_passive(),
            rectangles=[
                {"x1": -4, "y1": -3, "x2": 4, "y2": 3, "fill": "none"},
                {"x1": -3, "y1": -2, "x2": 3, "y2": 2, "fill": "background"},
            ],
            symbol_lib_path=str(lib_path),
        )
        assert "Added" in result

        lib = SymbolLib.from_file(str(lib_path))
        unit0 = lib.symbols[0].units[0]
        assert len(unit0.graphicItems) == 2

    def test_auto_rectangle_generated(self, tmp_path):
        lib_path = tmp_path / "auto.kicad_sym"
        symbol.add_symbol(
            name="AutoRect",
            pins=_two_pin_passive(),
            symbol_lib_path=str(lib_path),
        )

        lib = SymbolLib.from_file(str(lib_path))
        unit0 = lib.symbols[0].units[0]
        assert len(unit0.graphicItems) == 1  # one auto-generated rect

    def test_properties_set(self, tmp_path):
        lib_path = tmp_path / "props.kicad_sym"
        symbol.add_symbol(
            name="MP4572GQB-P",
            pins=_ic_pins(),
            reference_prefix="U",
            footprint="Package_SO:SOIC-8",
            datasheet="https://example.com/ds.pdf",
            symbol_lib_path=str(lib_path),
        )

        lib = SymbolLib.from_file(str(lib_path))
        sym = lib.symbols[0]
        props = {p.key: p.value for p in sym.properties}
        assert props["Reference"] == "U"
        assert props["Value"] == "MP4572GQB-P"
        assert props["Footprint"] == "Package_SO:SOIC-8"
        assert props["Datasheet"] == "https://example.com/ds.pdf"

    def test_power_symbol(self, tmp_path):
        lib_path = tmp_path / "power.kicad_sym"
        symbol.add_symbol(
            name="VCC_3V3",
            pins=[{"number": "1", "name": "VCC_3V3", "type": "power_in",
                   "x": 0, "y": 0, "rotation": 90, "length": 0}],
            is_power=True,
            in_bom=False,
            reference_prefix="#PWR",
            symbol_lib_path=str(lib_path),
        )

        lib = SymbolLib.from_file(str(lib_path))
        sym = lib.symbols[0]
        assert sym.isPower is True
        assert sym.inBom is False

    def test_ic_symbol_roundtrip(self, tmp_path):
        """Add an 8-pin IC and verify all pins survive a save/load cycle."""
        lib_path = tmp_path / "ic.kicad_sym"
        pins = _ic_pins()
        symbol.add_symbol(
            name="BuckConverter",
            pins=pins,
            symbol_lib_path=str(lib_path),
        )

        # Read back and verify
        info = symbol.get_symbol_info("BuckConverter", str(lib_path))
        for p in pins:
            assert p["name"] in info
            assert p["number"] in info

    def test_add_multiple_symbols(self, tmp_path):
        """Add multiple symbols to the same library sequentially."""
        lib_path = tmp_path / "multi.kicad_sym"

        symbol.add_symbol(
            name="PartA", pins=_two_pin_passive(), symbol_lib_path=str(lib_path)
        )
        symbol.add_symbol(
            name="PartB", pins=_two_pin_passive(), symbol_lib_path=str(lib_path)
        )
        symbol.add_symbol(
            name="PartC", pins=_two_pin_passive(), symbol_lib_path=str(lib_path)
        )

        listing = symbol.list_lib_symbols(str(lib_path))
        assert "PartA" in listing
        assert "PartB" in listing
        assert "PartC" in listing
