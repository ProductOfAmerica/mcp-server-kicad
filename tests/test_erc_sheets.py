"""run_erc and list_unconnected_pins on one sheet of a hierarchy report that sheet's own violations.

Bug 9: both tools ran ERC on the project's root and kept the entries whose sheet path held the
sheet's file name, but kicad-cli names a sheet path by the sheets' names, so a sheet beside
its project's root came back with nothing. Bug 79: a sheet in a folder below its project
found no root at all, and a file no sheet uses was taken for a sheet.

A sheet's own violations are the entries kicad-cli files under the sheet's instance paths and
the violations in the report's first entry whose item lives in the sheet's file. tests/erc_oracle.py
reads both from kicad-cli's own report and netlist, and shares with the filter under test only the
rule that the first entry holds the violations without a sheet path.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path

import erc_oracle
import kicad_corpus
import pytest
from conftest import requires_cli
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import project, schematic

_SHEETS = ("power", "chan", "mid", "deep", "sub")


@pytest.fixture(scope="session")
def judged(project_hierarchy):
    """kicad-cli's own ERC report and netlist of the fixture project, read once."""
    return erc_oracle.Project(project_hierarchy.root)


def _keys(violations: list[dict]) -> Counter:
    return erc_oracle.keys(violations)


def _pins(violations: list[dict]) -> Counter:
    """The item description of each pin_not_connected violation: which pin of which part."""
    return Counter(
        v["items"][0]["description"] for v in violations if v["type"] == "pin_not_connected"
    )


@requires_cli
@pytest.mark.parametrize("name", _SHEETS)
def test_run_erc_on_a_sheet_returns_its_own_violations(project_hierarchy, judged, tmp_path, name):
    sheet = getattr(project_hierarchy, name)
    want = judged.own(sheet)
    result = schematic.run_erc(str(sheet), str(tmp_path))
    assert _keys(result.violations) == _keys(want)
    assert result.violation_count == len(want)
    assert result.source == project_hierarchy.root.name  # run from the root, in whatever folder
    assert "root schematic" in (result.note or "")


@requires_cli
def test_the_fixture_has_something_to_find(project_hierarchy, judged):
    """A sheet's expected set that is empty for every sheet would pass a filter that drops
    everything, which is the defect."""
    counts = {name: len(judged.own(getattr(project_hierarchy, name))) for name in _SHEETS}
    assert counts["power"] > 0 and counts["chan"] > 0 and counts["deep"] > 0 and counts["sub"] > 0
    assert counts["mid"] == 0  # a sheet whose only content is the sheet below it
    # a sheet used twice keeps what kicad-cli files for each instance
    chan_entries = [
        e for e in judged.report["sheets"] if judged.names.get(e["path"]) == "chan.kicad_sch"
    ]
    assert len(chan_entries) == 2


@requires_cli
def test_run_erc_on_the_root_returns_every_violation(project_hierarchy, judged, tmp_path):
    result = schematic.run_erc(str(project_hierarchy.root), str(tmp_path))
    assert _keys(result.violations) == _keys(judged.everything())
    assert "root schematic" not in (result.note or "")


@requires_cli
@pytest.mark.parametrize("name", _SHEETS)
def test_list_unconnected_pins_on_a_sheet_returns_its_own_pins(
    project_hierarchy, judged, tmp_path, name
):
    sheet = getattr(project_hierarchy, name)
    result = schematic.list_unconnected_pins(str(sheet), str(tmp_path))
    want = _pins(judged.own(sheet))
    assert Counter(pin["detail"] for pin in result.pins) == want
    assert result.unconnected_count == sum(want.values())
    assert "root schematic" in (result.note or "")


@requires_cli
def test_list_unconnected_pins_returns_only_pin_not_connected(project_hierarchy, tmp_path):
    """Bug 9, second half: the power sheet holds an unwired resistor, a dangling label and a
    dangling wire. Filtering on the message took the label and the wire too ('not connected to
    anything')."""
    result = schematic.list_unconnected_pins(str(project_hierarchy.power), str(tmp_path))
    assert [pin["detail"] for pin in result.pins] == [
        "Symbol R1 Pin 1 [Passive, Line]",
        "Symbol R1 Pin 2 [Passive, Line]",
    ]


@requires_cli
@pytest.mark.parametrize("which", ["stray", "stray2"])
def test_a_file_no_sheet_uses_is_checked_on_its_own(project_hierarchy, tmp_path, which):
    """Bug 79: stray.kicad_sch sits beside the root and stray2 in a folder below it, and
    no sheet names either, so each is a root of its own and ERC is run on it as one."""
    path = getattr(project_hierarchy, which)
    alone = erc_oracle.violations_of(erc_oracle.erc_of(path))
    assert alone, "premise: the stray file holds an unwired resistor"

    run = schematic.run_erc(str(path), str(tmp_path))
    assert _keys(run.violations) == _keys(alone)
    assert run.source == path.name
    assert "root schematic" not in (run.note or "")
    assert "uses this file" in (run.note or "")

    listed = schematic.list_unconnected_pins(str(path), str(tmp_path))
    assert Counter(pin["detail"] for pin in listed.pins) == _pins(alone)


@requires_cli
def test_the_project_may_be_named(project_hierarchy, judged, tmp_path):
    h = project_hierarchy
    result = schematic.run_erc(str(h.sub), str(tmp_path), project_path=str(h.pro))
    assert _keys(result.violations) == _keys(judged.own(h.sub))
    root_run = schematic.run_erc(str(h.root), str(tmp_path), project_path=str(h.pro))
    assert _keys(root_run.violations) == _keys(judged.everything())


@requires_cli
def test_a_project_that_does_not_use_the_file_is_refused(project_hierarchy, tmp_path, monkeypatch):
    """project_path only says which project's hierarchy to look in. A file that hierarchy
    does not use cannot be given a place in it, so nothing runs."""
    h = project_hierarchy

    def never(*args, **kwargs):
        raise AssertionError("kicad-cli was run")

    monkeypatch.setattr(schematic, "_run_cli", never)
    for tool in (schematic.run_erc, schematic.list_unconnected_pins):
        with pytest.raises(ToolError, match=r"stray\.kicad_sch.*proj\.kicad_sch"):
            tool(str(h.stray), str(tmp_path), project_path=str(h.pro))


def test_a_project_that_is_not_there_is_refused(tmp_path):
    sheet = tmp_path / "a.kicad_sch"
    project.create_schematic(schematic_path=str(sheet))
    with pytest.raises(ToolError, match="project not found"):
        schematic.run_erc(str(sheet), project_path=str(tmp_path / "gone.kicad_pro"))


def test_a_project_without_its_root_schematic_is_refused(tmp_path):
    sheet = tmp_path / "a.kicad_sch"
    project.create_schematic(schematic_path=str(sheet))
    pro = tmp_path / "lonely.kicad_pro"
    pro.write_text("{}", encoding="utf-8")
    with pytest.raises(
        ToolError, match=r"lonely\.kicad_pro has no root schematic lonely\.kicad_sch"
    ):
        schematic.list_unconnected_pins(str(sheet), project_path=str(pro))


@requires_cli
@pytest.mark.kicad_placeholder("a/a.kicad_sch", "a/mid.kicad_sch")
def test_a_broken_sheet_in_a_project_above_does_not_stop_a_file_below_it(tmp_path):
    """experiments/standalone.kicad_sch has no project beside it, and the project in the
    folder above has a sheet cut short, as an interrupted save leaves it. Nothing says that
    sheet uses the file, and kicad-cli opens the file on its own, so it is checked on its own:
    the tools used to refuse it for another project's damage. The judge is kicad-cli's ERC
    run on the file."""
    top = tmp_path / "a"
    project.create_project(directory=str(top), name="a")
    mid, leaf = top / "mid.kicad_sch", top / "leaf.kicad_sch"
    project.create_schematic(schematic_path=str(mid))
    project.create_schematic(schematic_path=str(leaf))
    for parent, name, child in ((top / "a.kicad_sch", "Mid", mid), (mid, "Leaf", leaf)):
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name=name,
            sheet_file=str(child),
            pins=[],
            project_path=str(top / "a.kicad_pro"),
        )
    data = mid.read_bytes()
    mid.write_bytes(data[: data.index(b"(sheet") + 200])  # cut inside its sheet block
    lone = top / "experiments" / "standalone.kicad_sch"
    lone.parent.mkdir()
    project.create_schematic(schematic_path=str(lone))
    schematic.place_component("Device:R", "R1", "10K", 50.8, 50.8, schematic_path=str(lone))
    alone = erc_oracle.violations_of(erc_oracle.erc_of(lone))
    assert alone, "premise: the file holds an unwired resistor"

    located = project.is_root_schematic(schematic_path=str(lone))
    run = schematic.run_erc(str(lone), str(tmp_path / "out"))
    listed = schematic.list_unconnected_pins(str(lone), str(tmp_path / "out"))

    assert (located.is_root, located.root_path) == (True, None)
    assert _keys(run.violations) == _keys(alone)
    assert run.source == lone.name
    assert "uses this file" not in (run.note or "")
    assert Counter(pin["detail"] for pin in listed.pins) == _pins(alone)


@requires_cli
@pytest.mark.parametrize(
    "listed",
    [None, [{"filename": "a\u0000b.kicad_sch"}]],
    ids=["null", "a-name-no-file-can-have"],
)
def test_a_project_file_above_with_no_usable_sheet_list_does_not_stop_a_file_below_it(
    tmp_path, listed
):
    """up/u.kicad_pro holds a schematic.top_level_sheets that names no sheet file. The tools
    read that file for every schematic at or below up/, and neither the project's own
    u.kicad_sch nor work/y, which nothing in the project names, may be refused for it. y is
    checked on its own. The judge is kicad-cli's ERC of y."""
    up = tmp_path / "up"
    project.create_project(directory=str(up), name="u")
    pro = up / "u.kicad_pro"
    data = json.loads(pro.read_text(encoding="utf-8"))
    data.setdefault("schematic", {})["top_level_sheets"] = listed
    pro.write_text(json.dumps(data, indent=2), encoding="utf-8")
    lone = up / "work" / "y.kicad_sch"
    lone.parent.mkdir()
    project.create_schematic(schematic_path=str(lone))
    schematic.place_component("Device:R", "R1", "10K", 50.8, 50.8, schematic_path=str(lone))
    alone = erc_oracle.violations_of(erc_oracle.erc_of(lone))
    assert alone, "premise: the file holds an unwired resistor"

    out = str(tmp_path / "out")
    located = project.is_root_schematic(schematic_path=str(lone))
    run = schematic.run_erc(str(lone), out)
    listed_pins = schematic.list_unconnected_pins(str(lone), out)

    assert project.is_root_schematic(schematic_path=str(up / "u.kicad_sch")).is_root is True
    assert (located.is_root, located.root_path) == (True, None)
    assert _keys(run.violations) == _keys(alone)
    assert run.source == lone.name
    assert Counter(pin["detail"] for pin in listed_pins.pins) == _pins(alone)


def _reused_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A project whose root holds 65 sheets of tile.kicad_sch, each holding 65 sheets of
    pixel.kicad_sch, and then a sheet Power (power.kicad_sch): 1 + 65 + 65 * 65 + 1 = 4,292
    sheet instances, more than _connectivity._MAX_INSTANCES. experiments/lone.kicad_sch is used
    by no sheet. Power and lone each hold an unwired resistor. Returns the root, power and
    lone."""
    top = tmp_path / "big"
    project.create_project(directory=str(top), name="p")
    root, tile, pixel, power = (top / f"{n}.kicad_sch" for n in ("p", "tile", "pixel", "power"))
    lone = top / "experiments" / "lone.kicad_sch"
    lone.parent.mkdir()
    for path in (tile, pixel, power, lone):
        project.create_schematic(schematic_path=str(path))
    for i in range(65):
        x, y = 25.4 + (i % 13) * 30.48, 25.4 + (i // 13) * 15.24
        project.add_hierarchical_sheet(str(root), f"T{i}", str(tile), [], x, y)
        project.add_hierarchical_sheet(str(tile), f"P{i}", str(pixel), [], x, y)
    project.add_hierarchical_sheet(str(root), "Power", str(power), [], 25.4, 200.66)
    for path, ref in ((power, "R1"), (lone, "R2")):
        schematic.place_component("Device:R", ref, "10K", 50.8, 50.8, schematic_path=str(path))
    return root, power, lone


@requires_cli
def test_a_project_of_thousands_of_sheet_instances_does_not_stop_the_files_it_is_asked_about(
    tmp_path,
):
    """4,292 instances is no reason to refuse Power, which the root names last, or the file
    in experiments/ that no sheet names: Power is checked from the root and gets its own
    violations, and the lone file is checked on its own. The judge is kicad-cli's ERC of the
    root, read by tests/erc_oracle.py, and of the lone file."""
    root, power, lone = _reused_project(tmp_path)
    want = erc_oracle.Project(root).own(power)
    assert want, "premise: Power holds an unwired resistor"
    alone = erc_oracle.violations_of(erc_oracle.erc_of(lone))
    assert alone, "premise: the lone file holds an unwired resistor"
    out = str(tmp_path / "out")

    placed = project.is_root_schematic(schematic_path=str(power))
    run = schematic.run_erc(str(power), out)
    listed = schematic.list_unconnected_pins(str(power), out)
    assert (placed.is_root, Path(str(placed.root_path))) == (False, root)
    assert _keys(run.violations) == _keys(want)
    assert run.source == root.name
    assert Counter(pin["detail"] for pin in listed.pins) == _pins(want)

    placed = project.is_root_schematic(schematic_path=str(lone))
    run = schematic.run_erc(str(lone), out)
    listed = schematic.list_unconnected_pins(str(lone), out)
    assert (placed.is_root, placed.root_path) == (True, None)
    assert _keys(run.violations) == _keys(alone)
    assert run.source == lone.name
    assert "uses this file" in (run.note or "")
    assert Counter(pin["detail"] for pin in listed.pins) == _pins(alone)


def _top_level_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A project whose .kicad_pro lists two top-level sheets, proj.kicad_sch and
    second.kicad_sch, the second holding a sheet of its own, sub.kicad_sch. Each file has an
    unwired resistor."""
    folder = tmp_path / "proj"
    project.create_project(directory=str(folder), name="proj")
    root, second, sub = (folder / f"{n}.kicad_sch" for n in ("proj", "second", "sub"))
    project.create_schematic(schematic_path=str(second))
    project.create_schematic(schematic_path=str(sub))
    project.add_hierarchical_sheet(
        parent_schematic_path=str(second), sheet_name="Sub", sheet_file=str(sub), pins=[]
    )
    for path, ref in ((root, "R1"), (second, "R2"), (sub, "R3")):
        schematic.place_component("Device:R", ref, "10K", 50.8, 50.8, schematic_path=str(path))

    def uuid_of(path: Path) -> str:
        found = re.search(r'\(uuid "([^"]+)"\)', path.read_text(encoding="utf-8"))
        assert found is not None
        return found.group(1)

    pro = folder / "proj.kicad_pro"
    data = json.loads(pro.read_text(encoding="utf-8"))
    data.setdefault("schematic", {})["top_level_sheets"] = [
        {"uuid": uuid_of(root), "name": "Root", "filename": "proj.kicad_sch"},
        {"uuid": uuid_of(second), "name": "Second", "filename": "second.kicad_sch"},
    ]
    pro.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return root, second, sub


@requires_cli
def test_a_sheet_below_a_second_top_level_sheet_is_checked_from_that_sheet(tmp_path):
    """The .kicad_pro lists two top-level sheets, as KiCad 10 writes it. A sheet below the
    second was reported a root and checked on its own; it is checked from the second, and
    only its own violations come back. The judge is kicad-cli's ERC and netlist of the
    second."""
    _, second, sub = _top_level_project(tmp_path)
    judged = erc_oracle.Project(second)
    want = judged.own(sub)
    assert want, "premise: kicad-cli reports violations that live in the sub-sheet"

    located = project.is_root_schematic(schematic_path=str(sub))
    run = schematic.run_erc(str(sub), str(tmp_path / "out"))
    listed = schematic.list_unconnected_pins(str(sub), str(tmp_path / "out"))

    assert located.is_root is False and Path(str(located.root_path)) == second
    assert _keys(run.violations) == _keys(want)
    assert run.source == second.name
    assert "root schematic" in (run.note or "")
    assert Counter(pin["detail"] for pin in listed.pins) == _pins(want)


@requires_cli
def test_a_second_top_level_sheet_is_checked_as_a_root(tmp_path):
    """It is a root of the project, not a file no sheet uses: ERC runs on it as the root it
    is and the note does not say the project leaves it unused."""
    _, second, _ = _top_level_project(tmp_path)
    judged = erc_oracle.Project(second)

    run = schematic.run_erc(str(second), str(tmp_path / "out"))

    assert project.is_root_schematic(schematic_path=str(second)).is_root is True
    assert _keys(run.violations) == _keys(judged.everything())
    assert "uses this file" not in (run.note or "")


def _violation(uuid: str, kind: str = "wire_dangling") -> dict:
    return {"type": kind, "items": [{"uuid": uuid, "description": "item", "pos": {"x": 0, "y": 0}}]}


@pytest.mark.kicad_placeholder("s.kicad_sch")
def test_a_sheets_violations_are_picked_by_instance_path_and_by_item(tmp_path):
    """A report as kicad-cli writes it: the first entry holds the violations without a sheet
    path, whatever sheet their item is on, and one entry for each instance of a sheet holds the
    rest. The sheet file here writes one uuid quoted and one bare, as kiutils does, and the
    report writes them in upper case."""
    sheet = tmp_path / "s.kicad_sch"
    sheet.write_text(
        '(kicad_sch (wire (uuid "aaaaaaaa-0000-0000-0000-000000000001"))'
        " (label (uuid aaaaaaaa-0000-0000-0000-000000000002))"
        ' (path "/root/not-a-uuid-list"))',
        encoding="utf-8",
    )
    report = {
        "sheets": [
            {
                "path": "/",
                "uuid_path": "/root",
                "violations": [
                    _violation("AAAAAAAA-0000-0000-0000-000000000001"),
                    _violation("aaaaaaaa-0000-0000-0000-000000000002", "label_dangling"),
                    _violation("bbbbbbbb-0000-0000-0000-000000000009"),  # another file's
                ],
            },
            {"path": "/A/", "uuid_path": "/root/b1", "violations": [_violation("x-in-a")]},
            {"path": "/B/", "uuid_path": "/root/b2", "violations": [_violation("x-in-b")]},
            {"path": "/A/C/", "uuid_path": "/root/b1/c1", "violations": [_violation("x-below")]},
            {"path": "/D/", "uuid_path": "/root/b3", "violations": [_violation("x-in-d")]},
        ]
    }
    # the sheet is used at /A/ and /D/ (blocks b1 and b3 of the root)
    own = schematic._sheet_violations(report, (("b1",), ("b3",)), sheet)
    assert [v["items"][0]["uuid"] for v in own] == [
        "AAAAAAAA-0000-0000-0000-000000000001",
        "aaaaaaaa-0000-0000-0000-000000000002",
        "x-in-a",
        "x-in-d",
    ]


@pytest.mark.kicad_placeholder("s.kicad_sch")
def test_the_violations_without_a_sheet_path_are_found_in_the_first_entry_not_the_roots(tmp_path):
    """With the root on page 9 and Power on page 1, kicad-cli 9.0.8 listed /Power/ first, holding
    every sheet's dangling labels and wires, and / fifth with none (measured on tests/hier_gen.py's
    project). A filter that read the root's entry would find nothing for the sheet here."""
    sheet = tmp_path / "s.kicad_sch"
    sheet.write_text(
        '(kicad_sch (wire (uuid "aaaaaaaa-0000-0000-0000-000000000001")))', encoding="utf-8"
    )
    report = {
        "sheets": [
            {
                "path": "/Power/",
                "uuid_path": "/root/b9",
                "violations": [
                    _violation("aaaaaaaa-0000-0000-0000-000000000001"),  # no sheet path, in s
                    _violation("bbbbbbbb-0000-0000-0000-000000000009"),  # no sheet path, not in s
                    _violation("x-in-power"),
                ],
            },
            {"path": "/", "uuid_path": "/root", "violations": []},
            {"path": "/A/", "uuid_path": "/root/b1", "violations": [_violation("x-in-a")]},
        ]
    }
    own = schematic._sheet_violations(report, (("b1",),), sheet)
    assert [v["items"][0]["uuid"] for v in own] == [
        "aaaaaaaa-0000-0000-0000-000000000001",
        "x-in-a",
    ]


# ---------------------------------------------------------------------------
# KiCad's own projects
# ---------------------------------------------------------------------------


def _copy_project(root: Path, into: Path) -> Path:
    """The project of *root* with its schematics, project file and library tables."""
    for source in root.parent.rglob("*"):
        if source.suffix in {".kicad_sch", ".kicad_pro"} or source.name.endswith("lib-table"):
            target = into / source.relative_to(root.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return into / root.name


def _demo_roots(*projects: str) -> list:
    """(id, root schematic) of the named demo projects, "folder/project name", those whose
    schematics all fit under the per-push size cap."""
    found = []
    for share in kicad_corpus.share_dirs():
        for name in projects:
            root = share / "demos" / f"{name}.kicad_sch"
            ok = root.is_file() and all(
                f.stat().st_size <= kicad_corpus.PER_PUSH_CAP
                for f in root.parent.rglob("*.kicad_sch")
            )
            if ok:
                found.append(pytest.param(root, id=root.parent.name))
    return found


#: royalblue54L_feather keeps its sheets in sch/ (bug 79); multichannel uses one sheet file
#: more than once.
_DEMOS = _demo_roots("royalblue54L_feather/RoyalBlue54L-Feather", "multichannel/multichannel_mixer")


@requires_cli
@pytest.mark.parametrize(
    "root",
    _DEMOS or [pytest.param(None, id="no-demos", marks=pytest.mark.skip(reason="no demos"))],
)
def test_every_sheet_of_a_shipped_project_returns_its_own_violations(root, tmp_path):
    copy = _copy_project(root, tmp_path / "demo")
    project_report = erc_oracle.Project(copy)
    sheets = sorted(set(project_report.names.values()) - {copy.name})
    assert sheets, "premise: the demo has sheets below its root"
    for name in sheets:
        path = next(copy.parent.rglob(name))
        result = schematic.run_erc(str(path), str(tmp_path / "out"))
        want = project_report.own(path)
        assert _keys(result.violations) == _keys(want), name
        assert result.source == copy.name, name
        listed = schematic.list_unconnected_pins(str(path), str(tmp_path / "out"))
        assert Counter(p["detail"] for p in listed.pins) == _pins(want), name
