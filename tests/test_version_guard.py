"""Tests for the format version guard (_check_format_version).

The guard refuses to load files whose (version N) header is newer than the
KiCad 9 formats, before any kiutils parse. Measured motivation in issue #9:
kiutils 1.4.8 crashes on KiCad 10 boards and silently rewrites KiCad 10
schematics while keeping their version claim.

Most classes carry no_kicad_validation: they write future-version stubs into
tmp_path, and the autouse ERC fixture would otherwise fail them via kicad-cli,
which itself rejects future versions.
"""

from __future__ import annotations

import re

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import footprint, schematic, symbol
from mcp_server_kicad._shared import _FORMAT_VERSION_LIMITS, _check_format_version

# Real versions each file family gets from a KiCad 10 save (measured in #9).
KICAD10_VERSIONS = {
    "kicad_sch": 20260306,
    "kicad_pcb": 20260206,
    "kicad_symbol_lib": 20251024,
    "footprint": 20260206,
}

SUFFIX = {
    "kicad_sch": ".kicad_sch",
    "kicad_pcb": ".kicad_pcb",
    "kicad_symbol_lib": ".kicad_sym",
    "footprint": ".kicad_mod",
}


# Minimal but ERC-irrelevant stubs: the guard fires before any parse, so
# refusal cases need only a plausible header.
def _stub(kind: str, version: int) -> str:
    name = ' "Stub"' if kind == "footprint" else ""
    return f'({kind}{name}\n\t(version {version})\n\t(generator "test")\n)\n'


# A parseable .kicad_sym body for the carve-out test: old-style content that
# kiutils 1.4.8 reads fine, stamped with a KiCad 10 version token.
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


@pytest.mark.no_kicad_validation
class TestVersionThresholds:
    @pytest.mark.parametrize("kind", sorted(_FORMAT_VERSION_LIMITS))
    def test_at_limit_passes(self, tmp_path, kind):
        f = tmp_path / f"at_limit{SUFFIX[kind]}"
        f.write_text(_stub(kind, _FORMAT_VERSION_LIMITS[kind]))
        _check_format_version(str(f))

    @pytest.mark.parametrize(
        ("kind", "version"),
        [(k, _FORMAT_VERSION_LIMITS[k] + 1) for k in sorted(_FORMAT_VERSION_LIMITS)]
        + [(k, KICAD10_VERSIONS[k]) for k in sorted(KICAD10_VERSIONS)],
    )
    def test_newer_version_refuses(self, tmp_path, kind, version):
        f = tmp_path / f"newer{SUFFIX[kind]}"
        f.write_text(_stub(kind, version))
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            _check_format_version(str(f))

    def test_exact_error_message(self, tmp_path):
        f = tmp_path / "future.kicad_sch"
        f.write_text(_stub("kicad_sch", 20260306))
        expected = (
            "future.kicad_sch: format version 20260306 is newer than the "
            "KiCad 9 formats this server supports (kicad_sch <= 20250114). "
            "Loading would silently drop KiCad 10+ data and a save could "
            "corrupt the file, so it is refused. Keep editing this file in "
            "KiCad 10+; parser upgrade tracked in #9."
        )
        with pytest.raises(ToolError, match=re.escape(expected)):
            _check_format_version(str(f))


@pytest.mark.no_kicad_validation
class TestGuardPassThrough:
    def test_version_on_root_line_refused(self, tmp_path):
        f = tmp_path / "oneline.kicad_sch"
        f.write_text("(kicad_sch (version 20250115) (generator eeschema))\n")
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            _check_format_version(str(f))

    def test_version_tab_indented_next_line_refused(self, tmp_path):
        f = tmp_path / "tabbed.kicad_sch"
        f.write_text("(kicad_sch\n\t(version 20250115)\n)\n")
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            _check_format_version(str(f))

    def test_no_version_token_passes(self, tmp_path):
        f = tmp_path / "legacy.kicad_sch"
        f.write_text("(kicad_sch (generator eeschema))\n")
        _check_format_version(str(f))

    def test_non_kicad_root_passes(self, tmp_path):
        f = tmp_path / "other.kicad_sch"
        f.write_text("(something_else (version 99999999))\n")
        _check_format_version(str(f))

    def test_empty_file_passes(self, tmp_path):
        f = tmp_path / "empty.kicad_sch"
        f.write_text("")
        _check_format_version(str(f))

    def test_nonexistent_path_passes(self, tmp_path):
        # Missing files keep today's behavior: the real loader raises, not
        # the guard.
        _check_format_version(str(tmp_path / "missing.kicad_sch"))


@pytest.mark.no_kicad_validation
class TestToolRefusal:
    def _future_sch(self, tmp_path):
        f = tmp_path / "future.kicad_sch"
        f.write_text(_stub("kicad_sch", 20260306))
        return f

    def test_read_only_tool_refuses(self, tmp_path):
        f = self._future_sch(tmp_path)
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            schematic.get_schematic_summary(schematic_path=str(f))

    def test_rmw_tool_refuses_and_leaves_file_untouched(self, tmp_path):
        # The representative guarded RMW tool tracks the migration: since
        # slice 11 every .kicad_sch writer is CST-native, so a board writer
        # (kiutils stays on boards) carries the flag.
        from mcp_server_kicad import pcb

        f = tmp_path / "future.kicad_pcb"
        f.write_text(_stub("kicad_pcb", 20260206))
        before = f.read_bytes()
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            pcb.move_footprint(reference="R1", x=10, y=10, pcb_path=str(f))
        assert f.read_bytes() == before

    def test_symbol_lib_tool_refuses(self, tmp_path):
        f = tmp_path / "future.kicad_sym"
        f.write_text(_stub("kicad_symbol_lib", 20251024))
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            symbol.list_lib_symbols(symbol_lib_path=str(f))

    def test_footprint_tool_refuses(self, tmp_path):
        f = tmp_path / "future.kicad_mod"
        f.write_text(_stub("footprint", 20260206))
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            footprint.get_footprint_info(footprint_path=str(f))

    def test_current_format_still_loads(self, empty_sch):
        # empty_sch is written at exactly 20250114: the ==limit boundary
        # through a real tool.
        result = schematic.get_schematic_summary(schematic_path=str(empty_sch))
        assert result is not None


class TestSystemLibCarveOut:
    def test_system_lib_newer_version_still_loads(self, tmp_path, monkeypatch):
        sym_dir = tmp_path / "syms"
        sym_dir.mkdir()
        lib = sym_dir / "TestLib.kicad_sym"
        lib.write_text(_SYM_LIB_KICAD10)
        monkeypatch.setenv("KICAD_SYMBOL_DIR", str(sym_dir))
        result = symbol.list_lib_symbols(symbol_lib_path=str(lib))
        assert "TestSym" in result
