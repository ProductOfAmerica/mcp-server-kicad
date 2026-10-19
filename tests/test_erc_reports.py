"""run_erc and list_unconnected_pins report what kicad-cli's ERC reports, in millimetres.

Bug 8: kicad-cli writes the position of every ERC item in its JSON report with the board's
internal units in some releases, 100 times too small in millimetres. The tools return the
position in the sheet's millimetres whatever the release, by the report's own version.

Judges, none of them the code under test: kicad-cli's text report (--format report --units
mm), which prints the positions in the schematic's units on every release, and the relation
between a schematic's and a board's internal unit (tests/kicad_ref/eda_units.py).
"""

from __future__ import annotations

import json
import re
import tempfile
from collections import Counter
from pathlib import Path

import pytest
import sexpr_gen
from conftest import requires_cli
from hypothesis import given, settings
from hypothesis import strategies as st
from kicad_ref import eda_units as U

from mcp_server_kicad import project, schematic
from mcp_server_kicad._shared import _run_cli

_Position = tuple[str, float, float]


def _sheet_with_violations(tmp_path: Path) -> Path:
    """A schematic with an unwired resistor, a dangling wire and a dangling label, every
    coordinate on the 1.27 mm grid so that the text report's two decimals are exact."""
    path = tmp_path / "check.kicad_sch"
    project.create_schematic(schematic_path=str(path))
    schematic.place_component("Device:R", "R1", "10K", 101.6, 76.2, schematic_path=str(path))
    schematic.add_wires([{"x1": 127.0, "y1": 50.8, "x2": 152.4, "y2": 50.8}], str(path))
    schematic.add_label("FLOAT", 60.96, 30.48, schematic_path=str(path))
    return path


def _kicads_text_report(path: Path) -> list[_Position]:
    """(violation type, x, y) for every item kicad-cli's text report prints, in the sheet's
    millimetres. RuntimeError when kicad-cli writes no report: a premise, not the defect."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        out = Path(scratch) / "erc.rpt"
        result = _run_cli(
            ["sch", "erc", "--format", "report", "--units", "mm", "--severity-all"]
            + ["--output", str(out), str(path)],
            check=False,
        )
        if not out.is_file():
            raise RuntimeError(f"kicad-cli wrote no ERC report (rc={result.returncode})")
        text = out.read_text(encoding="utf-8", errors="replace")
    found: list[_Position] = []
    kind = ""
    for line in text.splitlines():
        head = re.match(r"\[(\w+)\]: ", line)
        if head:
            kind = head.group(1)
            continue
        item = re.match(r"\s+@\((-?[\d.]+) mm, (-?[\d.]+) mm\): ", line)
        if item:
            found.append((kind, float(item.group(1)), float(item.group(2))))
    return found


def _printed(violations: list[dict]) -> list[_Position]:
    """(type, x, y) of every item of the violations, at the two decimals the text prints."""
    return [
        (v["type"], round(i["pos"]["x"], 2), round(i["pos"]["y"], 2))
        for v in violations
        for i in v.get("items", [])
    ]


def _json_positions_are_wrong(path: Path) -> bool:
    """Whether the installed kicad-cli writes its JSON positions apart from its text
    report's, measured on the file itself. RuntimeError when it writes no report."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        out = Path(scratch) / "erc.json"
        _run_cli(
            ["sch", "erc", "--format", "json", "--severity-all", "--output", str(out), str(path)],
            check=False,
        )
        if not out.is_file():
            raise RuntimeError("kicad-cli wrote no ERC JSON")
        report = json.loads(out.read_bytes())
    raw = [
        (v["type"], round(i["pos"]["x"], 2), round(i["pos"]["y"], 2))
        for s in report["sheets"]
        for v in s["violations"]
        for i in v.get("items", [])
    ]
    return Counter(raw) != Counter(_kicads_text_report(path))


@requires_cli
def test_run_erc_prints_the_positions_of_kicads_text_report(tmp_path):
    """Bug 8: on kicad-cli 9.0.8 the 101.6, 72.39 mm pin came back as 1.016, 0.7239."""
    sheet = _sheet_with_violations(tmp_path)
    result = schematic.run_erc(str(sheet), str(tmp_path / "out"))
    assert result.violation_count > 0
    assert Counter(_printed(result.violations)) == Counter(
        (kind, round(x, 2), round(y, 2)) for kind, x, y in _kicads_text_report(sheet)
    )


@requires_cli
def test_list_unconnected_pins_prints_positions_the_text_report_prints(tmp_path):
    sheet = _sheet_with_violations(tmp_path)
    result = schematic.list_unconnected_pins(str(sheet), str(tmp_path / "out"))
    assert result.unconnected_count > 0
    got = Counter((round(pin["x"], 2), round(pin["y"], 2)) for pin in result.pins)
    text = Counter((round(x, 2), round(y, 2)) for _, x, y in _kicads_text_report(sheet))
    assert not got - text, f"positions the text report does not print: {got - text}"
    # the numbers get_pin_positions prints for R1 are the ones the unwired pins come back at
    printed = schematic.get_pin_positions("R1", str(sheet))
    pins = re.findall(r"Pin \S+ \(.*?\): \((-?[\d.]+), (-?[\d.]+)\)", printed)
    assert len(pins) == 2
    assert {(float(x), float(y)) for x, y in pins} <= set(got)


@requires_cli
def test_the_note_says_so_exactly_when_kicad_wrote_wrong_positions(tmp_path):
    """The positions are corrected from the report's version. The note names the correction
    when the installed kicad-cli's JSON differs from its text report, and only then, so the
    version table cannot drift from the release without this failing."""
    sheet = _sheet_with_violations(tmp_path)
    wrong = _json_positions_are_wrong(sheet)
    for note in (
        schematic.run_erc(str(sheet), str(tmp_path / "a")).note,
        schematic.list_unconnected_pins(str(sheet), str(tmp_path / "b")).note,
    ):
        assert ("100 times too small" in (note or "")) == wrong, note


@requires_cli
def test_a_length_quoted_in_a_description_is_kicads_own_text(tmp_path):
    """The note says lengths inside item descriptions carry the same error. The 25.4 mm wire
    reads 25.40 mm in the text report and, when the JSON positions are wrong, 0.2540 mm in
    the description the tool returns."""
    sheet = _sheet_with_violations(tmp_path)
    wrong = _json_positions_are_wrong(sheet)
    result = schematic.run_erc(str(sheet), str(tmp_path / "out"))
    quoted = {
        float(m.group(1))
        for v in result.violations
        for i in v["items"]
        if (m := re.search(r"length ([\d.]+) mm", i["description"]))
    }
    assert quoted == ({0.254} if wrong else {25.4})


def _report(version: str, iu: tuple[int, int], units: str = "mm") -> dict:
    """A report as kicad-cli writes it before the fix: the item at *iu* schematic units,
    divided by the board's units per millimetre."""
    pos = {"x": iu[0] / U.PCB_IU_PER_MM, "y": iu[1] / U.PCB_IU_PER_MM}
    violation = {"type": "pin_not_connected", "items": [{"pos": pos}, {"pos": dict(pos)}]}
    return {
        "kicad_version": version,
        "coordinate_units": units,
        "sheets": [{"path": "/", "violations": [violation]}],
    }


def _positions(report: dict) -> list[tuple[float, float]]:
    return [
        (i["pos"]["x"], i["pos"]["y"])
        for s in report["sheets"]
        for v in s["violations"]
        for i in v["items"]
    ]


#: The releases whose erc_report.cpp builds the JSON writer's units provider from pcbIUScale
#: (WriteJsonReport, line 113 at 9.0.0, 124 at 9.0.1 to 9.0.7, 137 at 9.0.8 to 9.0.9.1, 161
#: at 10.0.0 to 10.0.6) against schIUScale from 10.0.7 (line 161), read from each tag.
_SCALED = ["9.0.0", "9.0.1", "9.0.7", "9.0.8", "9.0.9", "9.0.9.1", "10.0.0", "10.0.5", "10.0.6"]
_RIGHT = ["10.0.7", "10.0.8", "10.1.0", "11.0.0", "11.2.3"]
_NO_OPINION = ["", "garbage", "10.0", "8.0.9", "9.0", "10.0.6-rc1", "10.0.x"]


@pytest.mark.parametrize("version", _SCALED)
def test_a_scaled_report_is_corrected_to_the_schematics_millimetres(version):
    iu = (1_016_000, -723_900)
    report = _report(version, iu)
    assert schematic._erc_positions_in_sheet_mm(report) is True
    assert _positions(report) == [(iu[0] / U.SCH_IU_PER_MM, iu[1] / U.SCH_IU_PER_MM)] * 2


@pytest.mark.parametrize("version", [*_RIGHT, *_NO_OPINION])
def test_any_other_report_is_left_as_kicad_wrote_it(version):
    report = _report(version, (1_016_000, -723_900))
    before = _positions(report)
    assert schematic._erc_positions_in_sheet_mm(report) is False
    assert _positions(report) == before


def test_a_report_in_other_units_is_left_alone():
    report = _report("9.0.8", (1_016_000, 0), units="in")
    before = _positions(report)
    assert schematic._erc_positions_in_sheet_mm(report) is False
    assert _positions(report) == before


@settings(max_examples=sexpr_gen.budget(1))
@given(
    st.integers(-(2**31), 2**31 - 1),
    st.integers(-(2**31), 2**31 - 1),
    st.sampled_from(_SCALED),
)
def test_every_schematic_unit_comes_back_exactly(x_iu, y_iu, version):
    """KiCad prints iu / PCB_IU_PER_MM in a double; the correction gives back iu /
    SCH_IU_PER_MM, the double KiCad's own schematic units give, whatever the integer."""
    report = _report(version, (x_iu, y_iu))
    schematic._erc_positions_in_sheet_mm(report)
    assert _positions(report) == [(x_iu / U.SCH_IU_PER_MM, y_iu / U.SCH_IU_PER_MM)] * 2
