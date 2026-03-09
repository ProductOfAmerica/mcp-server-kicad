"""Tests for new schematic manipulation tools."""

from conftest import reparse

from mcp_server_kicad import schematic


class TestMoveComponent:
    def test_move_existing(self, scratch_sch):
        # 200.66 == 158*1.27, on grid
        result = schematic.move_component("R1", 200.66, 200.66, schematic_path=str(scratch_sch))
        assert "Moved" in result
        sch = reparse(str(scratch_sch))
        r1 = next(
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R1" for p in s.properties)
        )
        assert r1.position.X == 200.66
        assert r1.position.Y == 200.66

    def test_move_with_rotation(self, scratch_sch):
        schematic.move_component("R1", 200, 200, rotation=90, schematic_path=str(scratch_sch))
        sch = reparse(str(scratch_sch))
        r1 = next(
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R1" for p in s.properties)
        )
        assert r1.position.angle == 90

    def test_move_missing(self, scratch_sch):
        result = schematic.move_component("R999", 200, 200, schematic_path=str(scratch_sch))
        assert "not found" in result


class TestEditComponentValue:
    def test_edit_value(self, scratch_sch):
        result = schematic.edit_component_value("R1", value="4.7K", schematic_path=str(scratch_sch))
        assert "Updated" in result
        sch = reparse(str(scratch_sch))
        r1 = next(
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R1" for p in s.properties)
        )
        val = next(p.value for p in r1.properties if p.key == "Value")
        assert val == "4.7K"

    def test_edit_reference(self, scratch_sch):
        schematic.edit_component_value("R1", new_reference="R99", schematic_path=str(scratch_sch))
        sch = reparse(str(scratch_sch))
        refs = [
            next((p.value for p in s.properties if p.key == "Reference"), None)
            for s in sch.schematicSymbols
        ]
        assert "R99" in refs

    def test_edit_missing(self, scratch_sch):
        result = schematic.edit_component_value("R999", value="1K", schematic_path=str(scratch_sch))
        assert "not found" in result


class TestAddGlobalLabel:
    def test_basic(self, scratch_sch):
        result = schematic.add_global_label("VCC", 50, 50, schematic_path=str(scratch_sch))
        assert "VCC" in result
        sch = reparse(str(scratch_sch))
        assert any(gl.text == "VCC" for gl in sch.globalLabels)

    def test_with_shape(self, scratch_sch):
        schematic.add_global_label(
            "SDA", 60, 60, shape="bidirectional", schematic_path=str(scratch_sch)
        )
        sch = reparse(str(scratch_sch))
        gl = next(g for g in sch.globalLabels if g.text == "SDA")
        assert gl.shape == "bidirectional"


class TestListGlobalLabels:
    def test_empty(self, scratch_sch):
        result = schematic.list_global_labels(str(scratch_sch))
        assert "No global labels" in result

    def test_with_labels(self, scratch_sch):
        schematic.add_global_label("VCC", 50, 50, schematic_path=str(scratch_sch))
        result = schematic.list_global_labels(str(scratch_sch))
        assert "VCC" in result


class TestAddNoConnect:
    def test_basic(self, scratch_sch):
        # 76.2 == 60*1.27, on grid
        result = schematic.add_no_connect(76.2, 76.2, schematic_path=str(scratch_sch))
        assert "76.2" in result
        sch = reparse(str(scratch_sch))
        assert len(sch.noConnects) == 1


class TestAddPowerSymbol:
    def test_basic(self, scratch_sch):
        result = schematic.add_power_symbol(
            "power:VCC", "VCC1", 100, 80, schematic_path=str(scratch_sch)
        )
        assert "VCC1" in result
        sch = reparse(str(scratch_sch))
        refs = [
            next((p.value for p in s.properties if p.key == "Reference"), None)
            for s in sch.schematicSymbols
        ]
        assert "VCC1" in refs


class TestAddText:
    def test_basic(self, scratch_sch):
        result = schematic.add_text("Hello World", 50, 120, schematic_path=str(scratch_sch))
        assert "Hello World" in result
        sch = reparse(str(scratch_sch))
        assert any(t.text == "Hello World" for t in sch.texts)
