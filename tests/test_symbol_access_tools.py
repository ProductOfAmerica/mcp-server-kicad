"""Tests for symbol library access tools on the symbol server."""

import pytest
from conftest import HAS_KICAD_CLI
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import symbol

# A KiCad 10 symbol library: same construct vocabulary, newer version stamp.
# Since slice 17 the reads are CST-native, so the stamp is just data.
_SYM_LIB_KICAD10 = """(kicad_symbol_lib
  (version 20251024)
  (generator "kicad_symbol_editor")
  (symbol "TestSym"
    (pin_names (offset 0))
    (in_bom yes)
    (on_board yes)
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "TestSym" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (symbol "TestSym_1_1"
      (pin passive line (at 0 0 0) (length 2.54)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
  )
)
"""


class TestListLibSymbols:
    def test_list_symbols(self, scratch_sym_lib):
        result = symbol.list_lib_symbols(str(scratch_sym_lib))
        assert "TestPart" in result

    def test_nonexistent(self):
        with pytest.raises(Exception):
            symbol.list_lib_symbols("/nonexistent.kicad_sym")


class TestGetSymbolInfo:
    def test_known(self, scratch_sym_lib):
        result = symbol.get_symbol_info("TestPart", str(scratch_sym_lib))
        assert "IN" in result
        assert "OUT" in result
        assert "passive" in result

    def test_unknown(self, scratch_sym_lib):
        """A missing symbol is a failure, so it must reach the client as one.

        Returning the sentence as a normal result gave isError false, which a
        client cannot tell apart from a symbol report.
        """
        with pytest.raises(ToolError, match="not found in"):
            symbol.get_symbol_info("NOPE", str(scratch_sym_lib))


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestExportSymbolSvg:
    def test_returns_result(self, scratch_sym_lib, tmp_path):
        result = symbol.export_symbol_svg(str(scratch_sym_lib), str(tmp_path))
        assert result.format == "svg"


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestUpgradeSymbolLib:
    def test_returns_result(self, scratch_sym_lib, tmp_path):
        import shutil as shutil_mod

        copy = str(tmp_path / "upgrade_test.kicad_sym")
        shutil_mod.copy(str(scratch_sym_lib), copy)
        result = symbol.upgrade_symbol_lib(copy)
        assert "success" in result.lower() or "upgraded" in result.lower()


class TestKicad10Header:
    """A 20251024 library reads like any other: no guard, no refusal."""

    @pytest.fixture()
    def k10_lib(self, tmp_path):
        lib = tmp_path / "k10.kicad_sym"
        lib.write_text(_SYM_LIB_KICAD10)
        return lib

    def test_list(self, k10_lib):
        assert symbol.list_lib_symbols(str(k10_lib)) == "TestSym (1 pins)"

    def test_info(self, k10_lib):
        result = symbol.get_symbol_info("TestSym", str(k10_lib))
        assert "Reference: U" in result
        assert "Pin 1: ~ (passive) @ (0, 0) rot=0" in result

    def test_add_symbol(self, k10_lib):
        symbol.add_symbol(
            name="Added",
            pins=[{"number": "1", "name": "A", "type": "input"}],
            symbol_lib_path=str(k10_lib),
        )
        listing = symbol.list_lib_symbols(str(k10_lib))
        assert "TestSym (1 pins)" in listing
        assert "Added (1 pins)" in listing
        # The 20251024 stamp is untouched: we never downgrade a file.
        assert "(version 20251024)" in k10_lib.read_text()


class TestUnitSuffixNames:
    """A symbol whose own name ends in _N_M keeps it.

    Measured on the stock Connector library: kiutils reads "Raspberry_Pi_2_3"
    as entryName "Raspberry_Pi" with unitId 2 and styleId 3, so the listing
    showed a name that get_symbol_info could not then find. The CST reads the
    name atom, so both agree with KiCad.
    """

    LIB = """(kicad_symbol_lib
  (version 20241209)
  (generator "kicad_symbol_editor")
  (symbol "Part_2_3"
    (property "Reference" "J" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (symbol "Part_2_3_0_1")
    (symbol "Part_2_3_1_1"
      (pin passive line (at 0 0 0) (length 2.54)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
  )
)
"""

    @pytest.fixture()
    def lib(self, tmp_path):
        path = tmp_path / "suffix.kicad_sym"
        path.write_text(self.LIB)
        return str(path)

    def test_listed_under_its_full_name(self, lib):
        assert symbol.list_lib_symbols(lib) == "Part_2_3 (1 pins)"

    def test_found_under_its_full_name(self, lib):
        assert symbol.get_symbol_info("Part_2_3", lib).startswith("Symbol: Part_2_3")
        with pytest.raises(ToolError, match="not found in"):
            symbol.get_symbol_info("Part", lib)
