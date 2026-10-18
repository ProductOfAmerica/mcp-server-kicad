"""Where a schematic file sits in its project: place(), by KiCad's rule for loading a hierarchy.

KiCad loads a project from its root schematic: a sheet block's file name resolves against the
folder of the file that holds the block, an absolute name stays as it is, a file that is already
its own ancestor is not entered, and a file used by two blocks is a sheet used twice
(SCH_IO_KICAD_SEXPR::loadHierarchy, eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr.cpp:178-222
at 9.0.8). place() does the same walk on the sheet blocks and says where a file sits: a sheet
of the project, with the uuids of the blocks from the root down to it for each use; a root; or
unused by the project nearest to it.

Judges, none of them the code under test: kicad-cli's netlist of the project's root, which
names the file and the block-uuid path (``tstamps``) of every sheet instance it loads, on the
tests/hier_gen.py project and on every project KiCad ships under demos/; and, for the cases a
netlist cannot be asked about, KiCad's own load rule above.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import erc_oracle
import kicad_corpus
import pytest
from conftest import _cli_report, _nightly_skip_reason, requires_cli
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _connectivity


def _chains_the_netlist_gives(root: Path) -> dict[str, Counter[tuple[str, ...]]]:
    """{file name: the block-uuid chain of each of its instances}, from kicad-cli's netlist.
    ``tstamps`` is "/" for the root and "/<block>/<block>/" below it."""
    result, data = _cli_report(["sch", "export", "netlist", "--format", "kicadxml"], root, "n.xml")
    if data is None:
        raise RuntimeError(f"kicad-cli wrote no netlist (rc={result.returncode})")
    chains: dict[str, Counter[tuple[str, ...]]] = {}
    for sheet in ET.fromstring(data).iter("sheet"):
        source = sheet.find("title_block/source")
        name = "" if source is None else (source.text or "")
        chain = tuple(part for part in sheet.get("tstamps", "").split("/") if part)
        chains.setdefault(name, Counter())[chain] += 1
    return chains


@requires_cli
def test_every_instance_of_a_sheet_has_the_block_uuids_kicad_gives_it(project_hierarchy):
    h = project_hierarchy
    chains = _chains_the_netlist_gives(h.root)
    for name, path in h.sheets.items():
        placed = _connectivity.place(path)
        assert placed.kind == "sheet" and placed.root == h.root, name
        assert Counter(placed.instances) == chains[name], name
    assert len(_connectivity.place(h.chan).instances) == 2  # Ch1 and Ch2


@requires_cli
def test_files_the_project_does_not_use_are_unused_and_the_root_is_a_root(project_hierarchy):
    h = project_hierarchy
    assert _connectivity.place(h.root).kind == "root"
    for path in h.unused:
        placed = _connectivity.place(path)
        assert placed.kind == "unused" and placed.root == h.root and placed.instances == ()


@pytest.mark.no_kicad_validation
def test_a_file_with_no_project_is_a_root(tmp_path):
    lone = tmp_path / "lone.kicad_sch"
    lone.write_text("(kicad_sch)", encoding="utf-8")
    assert _connectivity.place(lone).kind == "root"


def _projects_shipped(everything: bool) -> list:
    """(id, root schematic) of every project under the demos, those whose schematics all fit
    under the per-push cap unless *everything*."""
    found = []
    for share in kicad_corpus.share_dirs():
        for pro in sorted((share / "demos").rglob("*.kicad_pro")):
            root = pro.with_suffix(".kicad_sch")
            if not root.is_file():
                continue
            files = list(root.parent.rglob("*.kicad_sch"))
            small = all(f.stat().st_size <= kicad_corpus.PER_PUSH_CAP for f in files)
            if everything != small:
                found.append(pytest.param(root, id=pro.relative_to(share / "demos").as_posix()))
    return found


def _every_file_of_a_shipped_project(root: Path) -> None:
    """place() calls a file a sheet exactly when kicad-cli's netlist of the root lists it as
    one, and names the root. A project's folder holds files no sheet of it uses (the NFC
    antenna of RoyalBlue54L-Feather is a project of its own)."""
    files = sorted(root.parent.rglob("*.kicad_sch"))
    names = [f.name for f in files]
    if len(set(names)) != len(names):
        pytest.skip("two schematics share a name, which the netlist cannot tell apart")
    used = set(erc_oracle.sheet_files(root).values()) - {root.name}
    for path in files:
        placed = _connectivity.place(path)
        if path == root:
            assert placed.kind == "root", path
        else:
            assert (placed.kind == "sheet") == (path.name in used), path
            if placed.kind == "sheet":
                assert placed.root == root, path


@requires_cli
@pytest.mark.parametrize(
    "root",
    _projects_shipped(False)
    or [
        pytest.param(None, id="no-install", marks=pytest.mark.skip(reason=kicad_corpus.NO_INSTALL))
    ],
)
def test_shipped_project_is_placed_as_kicad_loads_it(root):
    _every_file_of_a_shipped_project(root)


@requires_cli
@pytest.mark.nightly
@pytest.mark.parametrize(
    "root",
    _projects_shipped(True)
    if _nightly_skip_reason(os.environ.get("HYPOTHESIS_PROFILE")) is None
    else [pytest.param(None, id="nightly-only")],
)
def test_nightly_large_shipped_project_is_placed_as_kicad_loads_it(root):
    _every_file_of_a_shipped_project(root)


# ---------------------------------------------------------------------------
# hand-made hierarchies: the sheet blocks are all place() reads, so these files are references
# between names, not schematics for kicad-cli to load
# ---------------------------------------------------------------------------


def _sch(*blocks: tuple[str, str]) -> str:
    """A schematic holding a sheet block per (uuid, Sheetfile)."""
    body = "".join(
        f'(sheet (at 0 0) (size 10 10) (uuid "{u}") (property "Sheetname" "S{i}")'
        f' (property "Sheetfile" "{f}"))'
        for i, (u, f) in enumerate(blocks)
    )
    return f'(kicad_sch (version 20250114) (generator "eeschema") {body})'


def _project(folder: Path, files: dict[str, str]) -> Path:
    """A project 'p' in *folder* with the given files, returning its root schematic."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "p.kicad_pro").write_text("{}", encoding="utf-8")
    for relative, text in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder / "p.kicad_sch"


@pytest.mark.no_kicad_validation
class TestKicadsLoadRule:
    def test_a_name_resolves_against_the_folder_of_the_file_holding_the_block(self, tmp_path):
        """b sits at sub/b.kicad_sch and names c as 'c.kicad_sch': that is sub/c.kicad_sch,
        not a file beside the root."""
        _project(
            tmp_path,
            {
                "p.kicad_sch": _sch(("u-a", "sub/b.kicad_sch")),
                "sub/b.kicad_sch": _sch(("u-b", "c.kicad_sch")),
                "sub/c.kicad_sch": _sch(),
                "c.kicad_sch": _sch(),
            },
        )
        inner = _connectivity.place(tmp_path / "sub" / "c.kicad_sch")
        assert (inner.kind, inner.instances) == ("sheet", (("u-a", "u-b"),))
        assert _connectivity.place(tmp_path / "c.kicad_sch").kind == "unused"

    def test_dots_in_a_name_are_taken_out(self, tmp_path):
        _project(
            tmp_path,
            {
                "p.kicad_sch": _sch(("u-a", "sub/b.kicad_sch")),
                "sub/b.kicad_sch": _sch(("u-b", "../shared/c.kicad_sch")),
                "shared/c.kicad_sch": _sch(),
            },
        )
        assert _connectivity.place(tmp_path / "shared" / "c.kicad_sch").instances == (
            ("u-a", "u-b"),
        )

    def test_an_absolute_name_stays_as_it_is(self, tmp_path):
        elsewhere = tmp_path / "elsewhere" / "far.kicad_sch"
        elsewhere.parent.mkdir()
        elsewhere.write_text(_sch(), encoding="utf-8")
        _project(tmp_path / "proj", {"p.kicad_sch": _sch(("u-a", elsewhere.as_posix()))})
        # no project folder above the file: found through the explicit root
        placed = _connectivity.place(elsewhere, tmp_path / "proj" / "p.kicad_sch")
        assert (placed.kind, placed.instances) == ("sheet", (("u-a",),))

    def test_a_windows_separator_is_a_folder_separator(self, tmp_path):
        _project(
            tmp_path,
            {"p.kicad_sch": _sch(("u-a", "sub\\\\b.kicad_sch")), "sub/b.kicad_sch": _sch()},
        )
        assert _connectivity.place(tmp_path / "sub" / "b.kicad_sch").kind == "sheet"

    def test_a_sheet_that_contains_its_ancestor_is_not_entered(self, tmp_path):
        """b names a, which holds the block for b, and the root: the walk does not go round
        again, so c below b is placed once."""
        _project(
            tmp_path,
            {
                "p.kicad_sch": _sch(("u-a", "a.kicad_sch")),
                "a.kicad_sch": _sch(("u-b", "b.kicad_sch")),
                "b.kicad_sch": _sch(
                    ("u-back", "a.kicad_sch"), ("u-root", "p.kicad_sch"), ("u-c", "c.kicad_sch")
                ),
                "c.kicad_sch": _sch(),
            },
        )
        assert _connectivity.place(tmp_path / "c.kicad_sch").instances == (("u-a", "u-b", "u-c"),)

    def test_a_sheet_many_folders_below_its_project_finds_it(self, tmp_path):
        """Five folders below the project, past the three levels the reference rules search."""
        _project(
            tmp_path,
            {
                "p.kicad_sch": _sch(("u1", "a/x.kicad_sch")),
                "a/x.kicad_sch": _sch(("u2", "b/y.kicad_sch")),
                "a/b/y.kicad_sch": _sch(("u3", "c/z.kicad_sch")),
                "a/b/c/z.kicad_sch": _sch(("u4", "d/w.kicad_sch")),
                "a/b/c/d/w.kicad_sch": _sch(("u5", "e/leaf.kicad_sch")),
                "a/b/c/d/e/leaf.kicad_sch": _sch(),
            },
        )
        placed = _connectivity.place(tmp_path / "a" / "b" / "c" / "d" / "e" / "leaf.kicad_sch")
        assert (placed.kind, placed.instances) == ("sheet", (("u1", "u2", "u3", "u4", "u5"),))

    def test_a_file_used_by_two_blocks_is_placed_at_each(self, tmp_path):
        _project(
            tmp_path,
            {
                "p.kicad_sch": _sch(("u-1", "a.kicad_sch"), ("u-2", "a.kicad_sch")),
                "a.kicad_sch": _sch(("u-b", "b.kicad_sch")),
                "b.kicad_sch": _sch(),
            },
        )
        placed = _connectivity.place(tmp_path / "b.kicad_sch")
        assert sorted(placed.instances) == [("u-1", "u-b"), ("u-2", "u-b")]

    def test_a_missing_sheet_file_is_an_empty_sheet(self, tmp_path):
        _project(tmp_path, {"p.kicad_sch": _sch(("u-a", "gone.kicad_sch")), "x.kicad_sch": _sch()})
        assert _connectivity.place(tmp_path / "x.kicad_sch").kind == "unused"

    def test_the_nearest_project_that_uses_the_file_wins(self, tmp_path):
        """inner/ is a project of its own; the outer project also names its root as a sheet."""
        _project(tmp_path, {"p.kicad_sch": _sch(("u-in", "inner/p.kicad_sch"))})
        inner = _project(tmp_path / "inner", {"p.kicad_sch": _sch(("u-leaf", "leaf.kicad_sch"))})
        (tmp_path / "inner" / "leaf.kicad_sch").write_text(_sch(), encoding="utf-8")
        assert _connectivity.place(inner).kind == "root"
        leaf = _connectivity.place(tmp_path / "inner" / "leaf.kicad_sch")
        assert (leaf.kind, leaf.root, leaf.instances) == ("sheet", inner, (("u-leaf",),))


@pytest.mark.no_kicad_validation
class TestWhatCannotBeTold:
    def test_an_unreadable_sheet_that_stopped_the_walk_before_the_file_is_named(self, tmp_path):
        _project(
            tmp_path,
            {
                "p.kicad_sch": _sch(("u-a", "broken.kicad_sch")),
                "broken.kicad_sch": "(kicad_sch (sheet (",
                "other.kicad_sch": _sch(),
            },
        )
        with pytest.raises(ToolError, match=r"broken\.kicad_sch could not be read"):
            _connectivity.place(tmp_path / "other.kicad_sch")

    def test_an_unreadable_sheet_does_not_matter_when_the_file_is_found_elsewhere(self, tmp_path):
        _project(
            tmp_path,
            {
                "p.kicad_sch": _sch(("u-a", "broken.kicad_sch"), ("u-b", "ok.kicad_sch")),
                "broken.kicad_sch": "(kicad_sch (sheet (",
                "ok.kicad_sch": _sch(),
            },
        )
        assert _connectivity.place(tmp_path / "ok.kicad_sch").instances == (("u-b",),)

    def test_a_hierarchy_of_too_many_instances_is_refused(self, tmp_path):
        """Thirteen uses of a file that has thirteen uses of the next, four deep, is 28,561
        instances of the last file."""
        uuids = [f"u{i}" for i in range(13)]
        files = {"p.kicad_sch": _sch(*[(u, "a.kicad_sch") for u in uuids])}
        for here, there in (("a", "b"), ("b", "c"), ("c", "d")):
            files[f"{here}.kicad_sch"] = _sch(*[(u, f"{there}.kicad_sch") for u in uuids])
        files["d.kicad_sch"] = _sch()
        files["unused.kicad_sch"] = _sch()
        _project(tmp_path, files)
        with pytest.raises(ToolError, match="more than 4096 sheet instances"):
            _connectivity.place(tmp_path / "unused.kicad_sch")
