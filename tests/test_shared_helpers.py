"""Tests for _resolve_hierarchy_path helper."""

from __future__ import annotations

from pathlib import Path

from conftest import new_schematic

from mcp_server_kicad._shared import _resolve_hierarchy_path


class TestResolveHierarchyPath:
    def test_root_schematic_returns_own_uuid(self, tmp_path: Path):
        """When schematic IS the root, return project name and /{uuid}."""
        sch = new_schematic()
        sch_path = tmp_path / "myproject.kicad_sch"
        sch.filePath = str(sch_path)
        sch.to_file()

        pro_path = str(tmp_path / "myproject.kicad_pro")
        name, path = _resolve_hierarchy_path(pro_path, str(sch_path), sch.uuid)
        assert name == "myproject"
        assert path == f"/{sch.uuid}"

    def test_sub_sheet_returns_root_uuid_and_sheet_uuid(self, tmp_path: Path):
        """When schematic is a sub-sheet, return root project name and /{root_uuid}/{sheet_uuid}."""
        from kiutils.items.common import Effects, Font, Position, Property
        from kiutils.items.schitems import HierarchicalSheet

        root_sch = new_schematic()
        root_path = tmp_path / "myproject.kicad_sch"
        root_sch.filePath = str(root_path)

        sheet = HierarchicalSheet()
        sheet.uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        sheet.position = Position(X=25.4, Y=25.4)
        sheet.width = 25.4
        sheet.height = 10.16
        sheet.sheetName = Property(
            key="Sheetname",
            value="Power",
            id=0,
            effects=Effects(font=Font(height=1.27, width=1.27)),
            position=Position(X=25.4, Y=24.13, angle=0),
        )
        sheet.fileName = Property(
            key="Sheetfile",
            value="power-supply.kicad_sch",
            id=1,
            effects=Effects(font=Font(height=1.27, width=1.27)),
            position=Position(X=25.4, Y=36.83, angle=0),
        )
        root_sch.sheets.append(sheet)
        root_sch.to_file()

        child_sch = new_schematic()
        child_path = tmp_path / "power-supply.kicad_sch"
        child_sch.filePath = str(child_path)
        child_sch.to_file()

        pro_path = str(tmp_path / "myproject.kicad_pro")
        name, path = _resolve_hierarchy_path(pro_path, str(child_path), child_sch.uuid)
        assert name == "myproject"
        assert path == f"/{root_sch.uuid}/{sheet.uuid}"
