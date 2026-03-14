"""Tests for shared helper functions in _shared.py."""

from __future__ import annotations

from pathlib import Path

import pytest
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
        assert sch.uuid is not None
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
        assert child_sch.uuid is not None
        assert root_sch.uuid is not None
        name, path = _resolve_hierarchy_path(pro_path, str(child_path), child_sch.uuid)
        assert name == "myproject"
        assert path == f"/{root_sch.uuid}/{sheet.uuid}"


class TestResolveRoot:
    def test_returns_root_from_project_path(self, tmp_path: Path):
        """When project_path is given, derive root .kicad_sch from it."""
        from mcp_server_kicad._shared import _resolve_root

        pro = tmp_path / "myproj.kicad_pro"
        pro.write_text("{}")
        root_sch = tmp_path / "myproj.kicad_sch"
        root_sch.write_text("")
        sub_sch = tmp_path / "child.kicad_sch"
        sub_sch.write_text("")

        result = _resolve_root(str(sub_sch), project_path=str(pro))
        assert result == str(root_sch)

    def test_returns_none_when_already_root_via_project(self, tmp_path: Path):
        from mcp_server_kicad._shared import _resolve_root

        pro = tmp_path / "myproj.kicad_pro"
        pro.write_text("{}")
        root_sch = tmp_path / "myproj.kicad_sch"
        root_sch.write_text("")

        result = _resolve_root(str(root_sch), project_path=str(pro))
        assert result is None

    def test_falls_back_to_glob_when_no_project_path(self, tmp_path: Path):
        from mcp_server_kicad._shared import _resolve_root

        pro = tmp_path / "myproj.kicad_pro"
        pro.write_text("{}")
        root_sch = tmp_path / "myproj.kicad_sch"
        root_sch.write_text("")
        sub_sch = tmp_path / "child.kicad_sch"
        sub_sch.write_text("")

        result = _resolve_root(str(sub_sch))
        assert result == str(root_sch)

    def test_returns_none_when_no_project_found(self, tmp_path: Path):
        from mcp_server_kicad._shared import _resolve_root

        sch = tmp_path / "standalone.kicad_sch"
        sch.write_text("")

        result = _resolve_root(str(sch))
        assert result is None


@pytest.mark.no_kicad_validation
class TestLoadSchCachesSystemLibSymbols:
    def test_caches_system_lib_symbol_on_load(self, tmp_path, monkeypatch):
        """_load_sch caches raw text for system lib symbols found in the schematic."""
        from mcp_server_kicad._shared import (
            _RAW_LIB_SYMBOLS,
            _load_sch,
        )

        # Create a fake system library file
        lib_content = """(kicad_symbol_lib
  (version 20231120)
  (generator "kicad_symbol_editor")
  (symbol "TestSym"
    (pin_names (offset 0))
    (in_bom yes)
    (on_board yes)
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "TestSym" (at 0 2.54 0) (effects (font (size 1.27 1.27))))
    (symbol "TestSym_0_1"
      (rectangle (start -2.54 -2.54) (end 2.54 2.54)
        (stroke (width 0) (type default))
        (fill (type none))
      )
    )
    (symbol "TestSym_1_1"
      (pin passive line (at -5.08 0 0) (length 2.54)
        (name "A" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
  )
)"""
        lib_dir = tmp_path / "symbols"
        lib_dir.mkdir()
        lib_file = lib_dir / "TestLib.kicad_sym"
        lib_file.write_text(lib_content)

        # Create a schematic with a lib_symbol named "TestLib:TestSym"
        from kiutils.symbol import Symbol

        sch = new_schematic()
        lib_sym = Symbol()
        lib_sym.entryName = "TestLib:TestSym"
        sch.libSymbols.append(lib_sym)
        sch_path = tmp_path / "test.kicad_sch"
        sch.filePath = str(sch_path)
        sch.to_file()

        # Monkeypatch system sym dirs to include our fake dir
        monkeypatch.setattr("mcp_server_kicad._shared._SYSTEM_SYM_DIRS", [lib_dir])

        # Clear cache
        _RAW_LIB_SYMBOLS.clear()

        try:
            _load_sch(str(sch_path))
            assert "TestSym" in _RAW_LIB_SYMBOLS
            assert '(symbol "TestSym"' in _RAW_LIB_SYMBOLS["TestSym"]
        finally:
            _RAW_LIB_SYMBOLS.clear()

    def test_does_not_overwrite_existing_cache(self, tmp_path, monkeypatch):
        """_load_sch does not overwrite already-cached symbols."""
        from kiutils.symbol import Symbol

        from mcp_server_kicad._shared import (
            _RAW_LIB_SYMBOLS,
            _load_sch,
        )

        sch = new_schematic()
        lib_sym = Symbol()
        lib_sym.entryName = "SomeLib:SomeSym"
        sch.libSymbols.append(lib_sym)
        sch_path = tmp_path / "test2.kicad_sch"
        sch.filePath = str(sch_path)
        sch.to_file()

        sentinel = '(symbol "SomeSym" ORIGINAL_CACHED)'
        _RAW_LIB_SYMBOLS["SomeSym"] = sentinel

        try:
            _load_sch(str(sch_path))
            # Should not have been overwritten
            assert _RAW_LIB_SYMBOLS["SomeSym"] == sentinel
        finally:
            _RAW_LIB_SYMBOLS.clear()
