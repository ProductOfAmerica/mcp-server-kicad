"""Smoke tests to verify conftest fixtures produce valid schematics."""

import shutil
from pathlib import Path

import pytest

# conftest.py is auto-loaded by pytest. Import helpers via the tests package.
from conftest import (
    assert_erc_clean,
    assert_kicad_parseable,
    build_r_symbol,
    build_test_part_symbol,
    place_r1,
    reparse,
    run_erc,
)

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None


def test_scratch_sch_exists(scratch_sch: Path) -> None:
    assert scratch_sch.exists()
    assert scratch_sch.suffix == ".kicad_sch"


def test_scratch_sch_reparses(scratch_sch: Path) -> None:
    sch = reparse(scratch_sch)
    assert len(sch.schematicSymbols) == 1
    assert len(sch.labels) == 1

    # Verify the placed R1
    r1 = sch.schematicSymbols[0]
    ref = next(p.value for p in r1.properties if p.key == "Reference")
    assert ref == "R1"
    assert r1.libId == "Device:R"
    assert r1.position.X == 100
    assert r1.position.Y == 100

    # Verify label
    lbl = sch.labels[0]
    assert lbl.text == "TEST_NET"

    # Verify wire
    wires = [g for g in sch.graphicalItems if hasattr(g, "type") and g.type == "wire"]
    assert len(wires) == 1
    assert wires[0].points[0].X == 50
    assert wires[0].points[1].X == 80

    # Verify lib symbol
    assert len(sch.libSymbols) == 1
    rs = sch.libSymbols[0]
    assert rs.entryName == "R"
    assert len(rs.units) == 2


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
def test_scratch_sch_parseable(scratch_sch: Path) -> None:
    """scratch_sch is intentionally a simple fixture (unconnected pins, dangling
    label) so it won't pass ERC.  We only need to verify kicad-cli can parse it."""
    assert_kicad_parseable(scratch_sch)


def test_empty_sch_exists_and_reparses(empty_sch: Path) -> None:
    assert empty_sch.exists()
    sch = reparse(empty_sch)
    assert len(sch.schematicSymbols) == 0
    assert len(sch.labels) == 0


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
def test_empty_sch_erc_clean(empty_sch: Path) -> None:
    assert_erc_clean(empty_sch)


def test_scratch_sym_lib_reparses(scratch_sym_lib: Path) -> None:
    from kiutils.symbol import SymbolLib

    assert scratch_sym_lib.exists()
    lib = SymbolLib.from_file(str(scratch_sym_lib))
    assert len(lib.symbols) == 1
    tp = lib.symbols[0]
    assert tp.entryName == "TestPart"
    # Check pins on unit 1
    unit1 = [u for u in tp.units if u.unitId == 1][0]
    assert len(unit1.pins) == 2
    pin_names = {p.name for p in unit1.pins}
    assert pin_names == {"IN", "OUT"}


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
def test_run_erc_returns_dict(scratch_sch: Path) -> None:
    report = run_erc(scratch_sch)
    assert isinstance(report, dict)
    assert "violations" in report or "sheets" in report


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
def test_assert_erc_clean_catches_violations(tmp_path: Path) -> None:
    """assert_erc_clean must fail when there ARE violations.

    Regression test: KiCad 9 puts violations under sheets[N].violations,
    not at report["violations"].  The old code checked only the top level
    and therefore always passed.
    """
    from conftest import new_schematic

    # Build a schematic with an unconnected component -> guaranteed ERC errors
    sch = new_schematic()
    sch.libSymbols.append(build_r_symbol())
    sch.schematicSymbols.append(place_r1(100, 100))
    path = tmp_path / "erc_violations.kicad_sch"
    sch.filePath = str(path)
    sch.to_file()

    # Confirm kicad-cli actually finds violations
    report = run_erc(path)
    all_violations = []
    for sheet in report.get("sheets", []):
        all_violations.extend(sheet.get("violations", []))
    assert len(all_violations) > 0, "Expected ERC violations from unconnected resistor"

    # assert_erc_clean MUST raise AssertionError for this schematic
    with pytest.raises(AssertionError, match="ERC violations"):
        assert_erc_clean(path)


def test_builder_functions_importable() -> None:
    """Verify builder helpers are importable and return correct types."""
    from kiutils.items.schitems import SchematicSymbol
    from kiutils.symbol import Symbol

    r = build_r_symbol()
    assert isinstance(r, Symbol)
    assert r.entryName == "R"

    r1 = place_r1()
    assert isinstance(r1, SchematicSymbol)
    assert r1.libId == "Device:R"

    tp = build_test_part_symbol()
    assert isinstance(tp, Symbol)
    assert tp.entryName == "TestPart"
