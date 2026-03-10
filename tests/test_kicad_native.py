"""Tests using a KiCad-native schematic fixture.

Unlike the kiutils-built ``scratch_sch`` fixture, the ``kicad_native_sch``
fixture is a hand-written file that mirrors KiCad 9's output format:

- lib_symbol names with library prefix (``"Device:R"``)
- ``(dnp no)``, ``(fields_autoplaced)``, ``(instances ...)`` on placed symbols
- No ``(lib_name ...)``, no ``(id N)`` on properties

These tests verify that MCP tools work correctly on schematics originally
created by KiCad, not just those built by kiutils.
"""

from __future__ import annotations

from conftest import reparse

from mcp_server_kicad import schematic

# ---------------------------------------------------------------------------
# Smoke: fixture parses and has expected content
# ---------------------------------------------------------------------------


class TestKicadNativeFixture:
    def test_parses(self, kicad_native_sch):
        sch = reparse(str(kicad_native_sch))
        assert len(sch.schematicSymbols) == 1
        assert len(sch.labels) == 1

    def test_lib_symbol_has_prefix(self, kicad_native_sch):
        """The lib_symbol's libId preserves the 'Device:' prefix."""
        sch = reparse(str(kicad_native_sch))
        ls = sch.libSymbols[0]
        assert getattr(ls, "libId", None) == "Device:R"
        # entryName is always the bare name (kiutils strips the prefix)
        assert ls.entryName == "R"

    def test_placed_symbol_has_no_lib_name(self, kicad_native_sch):
        """KiCad-native placed symbols don't have lib_name."""
        sch = reparse(str(kicad_native_sch))
        sym = sch.schematicSymbols[0]
        assert sym.libName is None
        assert sym.libId == "Device:R"


# ---------------------------------------------------------------------------
# Read tools work on native files
# ---------------------------------------------------------------------------


class TestReadToolsNative:
    def test_list_components(self, kicad_native_sch):
        import json

        result = json.loads(schematic.list_schematic_items("components", str(kicad_native_sch)))
        refs = [c["reference"] for c in result]
        assert "R1" in refs

    def test_list_labels(self, kicad_native_sch):
        import json

        result = json.loads(schematic.list_schematic_items("labels", str(kicad_native_sch)))
        texts = [item["text"] for item in result]
        assert "TEST_NET" in texts

    def test_list_wires(self, kicad_native_sch):
        import json

        result = json.loads(schematic.list_schematic_items("wires", str(kicad_native_sch)))
        assert isinstance(result, list)

    def test_get_symbol_pins(self, kicad_native_sch):
        """get_symbol_pins should find a lib_symbol stored with prefix."""
        result = schematic.get_symbol_pins(
            symbol_name="R",
            schematic_path=str(kicad_native_sch),
        )
        assert "Pin 1" in result
        assert "Pin 2" in result

    def test_get_symbol_pins_with_prefix(self, kicad_native_sch):
        """get_symbol_pins should also accept the prefixed name."""
        result = schematic.get_symbol_pins(
            symbol_name="Device:R",
            schematic_path=str(kicad_native_sch),
        )
        assert "Pin 1" in result

    def test_get_pin_positions(self, kicad_native_sch):
        result = schematic.get_pin_positions(
            reference="R1",
            schematic_path=str(kicad_native_sch),
        )
        assert "R1" in result
        assert "Pin 1" in result


# ---------------------------------------------------------------------------
# Write tools work on native files (the original crash scenario)
# ---------------------------------------------------------------------------


class TestWriteToolsNative:
    def test_place_component_libname_matches_file(self, kicad_native_sch):
        """Placing a component must set libName to match the file's lib_symbol name.

        Regression: if lib_symbol is ``"Device:R"`` in the file but libName is
        set to ``"R"``, KiCad ERC crashes with a null-pointer dereference.
        """
        schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="4.7K",
            x=150,
            y=150,
            schematic_path=str(kicad_native_sch),
        )
        sch = reparse(str(kicad_native_sch))

        r2 = None
        for s in sch.schematicSymbols:
            ref = next((p.value for p in s.properties if p.key == "Reference"), None)
            if ref == "R2":
                r2 = s
                break

        assert r2 is not None
        # libName must match the lib_symbol's file-level name
        assert r2.libName == "Device:R"

    def test_place_component_finds_pins(self, kicad_native_sch):
        """Pin UUIDs must be assigned even when lib_symbol has a prefixed name."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R3",
            value="1K",
            x=200,
            y=200,
            schematic_path=str(kicad_native_sch),
        )
        sch = reparse(str(kicad_native_sch))

        r3 = None
        for s in sch.schematicSymbols:
            ref = next((p.value for p in s.properties if p.key == "Reference"), None)
            if ref == "R3":
                r3 = s
                break

        assert r3 is not None
        assert isinstance(r3.pins, dict)
        assert len(r3.pins) == 2
        assert "1" in r3.pins
        assert "2" in r3.pins

    def test_place_does_not_duplicate_lib_symbol(self, kicad_native_sch):
        """Placing another Device:R must not add a second lib_symbol."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R4",
            value="2.2K",
            x=250,
            y=250,
            schematic_path=str(kicad_native_sch),
        )
        sch = reparse(str(kicad_native_sch))
        r_symbols = [ls for ls in sch.libSymbols if ls.entryName == "R"]
        assert len(r_symbols) == 1

    def test_remove_component(self, kicad_native_sch):
        result = schematic.remove_component(
            reference="R1",
            schematic_path=str(kicad_native_sch),
        )
        assert "Removed" in result
        sch = reparse(str(kicad_native_sch))
        refs = [
            next((p.value for p in s.properties if p.key == "Reference"), None)
            for s in sch.schematicSymbols
        ]
        assert "R1" not in refs

    def test_add_wire(self, kicad_native_sch):
        schematic.add_wires(
            [{"x1": 100, "y1": 100, "x2": 200, "y2": 100}],
            schematic_path=str(kicad_native_sch),
        )
        sch = reparse(str(kicad_native_sch))
        from kiutils.items.schitems import Connection

        wires = [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"]
        assert len(wires) == 2

    def test_add_label(self, kicad_native_sch):
        schematic.add_label(
            text="VCC",
            x=100,
            y=80,
            schematic_path=str(kicad_native_sch),
        )
        sch = reparse(str(kicad_native_sch))
        label_texts = [lbl.text for lbl in sch.labels]
        assert "VCC" in label_texts

    def test_place_component_has_instances(self, kicad_native_sch):
        """Placed components must have an instances block for KiCad 9 annotation."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R5",
            value="100",
            x=152.4,
            y=152.4,
            schematic_path=str(kicad_native_sch),
        )
        sch = reparse(str(kicad_native_sch))

        r5 = None
        for s in sch.schematicSymbols:
            ref = next((p.value for p in s.properties if p.key == "Reference"), None)
            if ref == "R5":
                r5 = s
                break

        assert r5 is not None
        assert len(r5.instances) == 1
        inst = r5.instances[0]
        assert inst.name == "kicad_native"
        assert len(inst.paths) == 1
        assert inst.paths[0].reference == "R5"
        assert inst.paths[0].unit == 1
        assert inst.paths[0].sheetInstancePath == f"/{sch.uuid}"

    def test_place_component_grid_snaps(self, kicad_native_sch):
        """Off-grid coordinates must be snapped to 1.27mm multiples."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R6",
            value="220",
            x=150,  # not on 1.27mm grid
            y=150,
            schematic_path=str(kicad_native_sch),
        )
        sch = reparse(str(kicad_native_sch))

        r6 = None
        for s in sch.schematicSymbols:
            ref = next((p.value for p in s.properties if p.key == "Reference"), None)
            if ref == "R6":
                r6 = s
                break

        assert r6 is not None
        # 150 / 1.27 = 118.11 -> round to 118 -> 118 * 1.27 = 149.86
        assert r6.position.X == 149.86
        assert r6.position.Y == 149.86
