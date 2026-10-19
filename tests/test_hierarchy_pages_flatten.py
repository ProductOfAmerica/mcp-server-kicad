"""Sheet pages, duplicate_sheet and flatten_hierarchy against kicad-cli (step 12a, part 2).

Judges, none of them the code under test:

- kicad-cli: the sheet order of its netlist (how it reads page numbers), its components and
  node sets (which pins join), and whether it loads the output at all;
- KiCad's page comparison (SCH_SHEET::ComparePageNum), written out below from its source;
- the files KiCad ships, copied into a scratch folder, for the demos.

A node set is a net as a set of (reference, pin); net names are not compared, because the
hierarchy prints /A/N where the flat file prints a name of its own for the same net.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import edit_judge as J
import netlist_oracle
import pytest
from conftest import requires_cli
from kicad_corpus import shipped
from kicad_ref import sch_sheet as SS
from mcp.server.mcpserver.exceptions import ToolError
from test_hierarchy_writers import (
    CRLF,
    Proj,
    file_uuid,
    instance_paths,
    kids,
    no_demos,
    pins_of,
    sheet_name,
    sheet_order,
    sheet_uuid,
    sheets_of,
    top,
    value,
)

from mcp_server_kicad import _connectivity, _cst, project, schematic

# ---------------------------------------------------------------------------------------------
# Reading and editing files for the tests
# ---------------------------------------------------------------------------------------------


def page_key(label: str):
    """SCH_SHEET::ComparePageNum as a sort key (sch_sheet.cpp:1609-1642 at 9.0.8): integers
    numerically, before every other label; those by natural string order (StrNumCmp)."""
    if re.fullmatch(r"[0-9]+", label):
        return (0, int(label), ())
    return (1, 0, tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", label)))


def page_labels(path: Path) -> dict[str, str]:
    """{sheet name: the page label KiCad reads when path is the root}, by the lexer."""
    here = f"/{file_uuid(path)}"
    out = {}
    for sheet in sheets_of(path):
        pages = [page for _pj, p, page in instance_paths(sheet) if p == here]
        assert len(pages) == 1, (sheet_name(sheet), instance_paths(sheet))
        out[sheet_name(sheet)] = pages[0]
    return out


@pytest.fixture()
def stock_parts(stock_symbol_dir, monkeypatch):
    """Device:R and power:GND resolve from a stand-in folder, so no kicad-cli is needed."""
    monkeypatch.setenv("KICAD_SYMBOL_DIR", str(stock_symbol_dir))


def edit(path: Path, fn) -> None:
    """Apply fn to a file's root list and write it back: how a test makes a file the tools
    cannot (the CST is not a judge here, only a way to put the premise on disk)."""
    tree = _cst.parse(path.read_bytes())
    fn(tree.lists[0])
    path.write_bytes(_cst.serialize(tree))


def set_pages(path: Path, labels: dict[str, str]) -> None:
    """Give each named sheet block this page label in every instance entry it holds."""

    def go(root):
        for sheet in root.find_all("sheet"):
            name = next(
                p.atoms[2].text
                for p in sheet.find_all("property")
                if p.atoms[1].text == "Sheetname"
            )
            for pj in sheet.find("instances").find_all("project"):
                for entry in pj.find_all("path"):
                    if name in labels:
                        entry.find("page").atoms[1].set_text(labels[name])

    edit(path, go)


def to_legacy_paths(path: Path) -> None:
    """Rewrite each sheet's instance path to the shape earlier versions of the tools wrote:
    the sheet's own path, which KiCad never reads a page from."""

    def go(root):
        for sheet in root.find_all("sheet"):
            uid = sheet.find("uuid").atoms[1].text
            for pj in sheet.find("instances").find_all("project"):
                for entry in pj.find_all("path"):
                    entry.atoms[1].set_text(f"{entry.atoms[1].text}/{uid}")

    edit(path, go)


def node_sets(path: Path) -> set[frozenset]:
    """KiCad's nets as node sets, names ignored."""
    return {nodes for _code, _name, _cls, nodes in netlist_oracle.nets(path)}


def pins_by_ref(path: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for sets in node_sets(path):
        for ref, pin in sets:
            out.setdefault(ref, set()).add(pin)
    return out


def copy_demo(tmp_path: Path, name: str, into: str = "demo") -> Path:
    """A scratch copy of a shipped demo project, schematics and libraries only."""
    src = next(
        p
        for n, p in shipped(("demos",), (".kicad_pro",), ())
        if n.startswith(f"demos/{name}/") and n.count("/") == name.count("/") + 2
    )
    dst = tmp_path / into
    shutil.copytree(
        src.parent,
        dst,
        ignore=shutil.ignore_patterns("*.kicad_pcb", "*.pretty", "*.xml", "*.kicad_prl"),
    )
    return dst / src.with_suffix(".kicad_sch").name


# ---------------------------------------------------------------------------------------------
# Bug 41: reorder_sheet_pages changes the page numbers KiCad orders sheets by
# ---------------------------------------------------------------------------------------------


def three_sheets(p: Proj) -> dict[str, str]:
    """A, B, C placed right to left, so their position order (B, C, A) is not their page
    order (A=2, B=3, C=4)."""
    uuids = {}
    for name, x in (("A", 127.0), ("B", 25.4), ("C", 76.2)):
        uuids[name] = p.sheet(p.root, p.child(name.lower()), name, x=x, y=25.4)
    return uuids


@requires_cli
def test_reorder_makes_kicad_cli_list_the_sheets_in_the_order_asked(tmp_path):
    p = Proj(tmp_path)
    u = three_sheets(p)
    assert sheet_order(p.root) == ["/", "/A/", "/B/", "/C/"], "premise: page order"
    before = p.root.read_bytes()
    project.reorder_sheet_pages([u["C"], u["A"]], schematic_path=str(p.root))
    assert sheet_order(p.root) == ["/", "/C/", "/A/", "/B/"]
    changes = J.atom_changes(before, p.root.read_bytes())
    assert changes and all(c.path[-1][1] == "page" for c in changes), changes


def test_reorder_writes_the_page_labels_kicad_sorts_by(tmp_path):
    p = Proj(tmp_path)
    u = three_sheets(p)
    project.reorder_sheet_pages([u["B"], u["C"]], schematic_path=str(p.root))
    labels = page_labels(p.root)
    assert sorted(labels, key=lambda n: page_key(labels[n])) == ["B", "C", "A"]
    assert len(set(labels.values())) == 3


@requires_cli
def test_reorder_repairs_the_instance_paths_earlier_versions_wrote(tmp_path):
    p = Proj(tmp_path)
    u = three_sheets(p)
    to_legacy_paths(p.root)
    if sheet_order(p.root) != ["/", "/B/", "/C/", "/A/"]:
        raise RuntimeError("premise: kicad-cli reads those pages by position, not by label")
    before = p.root.read_bytes()
    project.reorder_sheet_pages([u["A"], u["B"]], schematic_path=str(p.root))
    assert sheet_order(p.root) == ["/", "/A/", "/B/", "/C/"]
    changes = J.atom_changes(before, p.root.read_bytes())
    assert {c.path[-1][1] for c in changes} <= {"path", "page"}, changes
    assert all(
        entry[1] == f"/{file_uuid(p.root)}"
        for sheet in sheets_of(p.root)
        for entry in instance_paths(sheet)
    )


@requires_cli
def test_reorder_gives_distinct_pages_to_sheets_sharing_one(tmp_path):
    p = Proj(tmp_path)
    u = three_sheets(p)
    set_pages(p.root, {"B": "2"})  # A and B both read page 2; C reads 4
    if sheet_order(p.root) != ["/", "/B/", "/A/", "/C/"]:
        raise RuntimeError("premise: a tie falls to the sheets' positions")
    project.reorder_sheet_pages([u["A"], u["B"]], schematic_path=str(p.root))
    assert sheet_order(p.root) == ["/", "/A/", "/B/", "/C/"]
    assert page_labels(p.root) == {"A": "2", "B": "3", "C": "4"}


@requires_cli
def test_reorder_does_not_hand_out_a_page_a_deeper_sheet_holds(tmp_path):
    p = Proj(tmp_path)
    mid = p.child("mid")
    uid_y = p.sheet(p.root, mid, "Y")
    uid_x = p.sheet(p.root, p.child("x"), "X")
    p.sheet(mid, p.child("leaf"), "Leaf")
    set_pages(mid, {"Leaf": "2"})
    set_pages(p.root, {"X": "2", "Y": "2"})
    project.reorder_sheet_pages([uid_y, uid_x], schematic_path=str(p.root))
    assert page_labels(p.root) == {"Y": "3", "X": "4"}
    assert sheet_order(p.root) == ["/", "/Y/Leaf/", "/Y/", "/X/"]


@requires_cli
def test_reorder_keeps_labels_that_are_not_numbers(tmp_path):
    p = Proj(tmp_path)
    u = three_sheets(p)
    set_pages(p.root, {"A": "Intro", "B": "2", "C": "10"})
    if sheet_order(p.root) != ["/", "/B/", "/C/", "/A/"]:
        raise RuntimeError("premise: numbers sort before text, numerically")
    project.reorder_sheet_pages([u["A"], u["C"]], schematic_path=str(p.root))
    assert sheet_order(p.root) == ["/", "/A/", "/C/", "/B/"]
    assert sorted(page_labels(p.root).values()) == ["10", "2", "Intro"]


@no_demos
@requires_cli
def test_reorder_on_a_shipped_hierarchy(tmp_path):
    root = copy_demo(tmp_path, "complex_hierarchy")
    sheets = {sheet_name(s): sheet_uuid(s) for s in sheets_of(root)}
    assert sheet_order(root) == ["/", "/ampli_ht_vertical/", "/ampli_ht_horizontal/"]
    before = root.read_bytes()
    project.reorder_sheet_pages([sheets["ampli_ht_horizontal"]], schematic_path=str(root))
    assert sheet_order(root) == ["/", "/ampli_ht_horizontal/", "/ampli_ht_vertical/"]
    changes = J.atom_changes(before, root.read_bytes())
    assert changes and all(c.path[-1][1] == "page" for c in changes), changes


@requires_cli
def test_reorder_refuses_a_root_whose_pages_are_in_its_sheet_instances(tmp_path):
    """Before 20221110 a sheet carries no (instances): every page is in the root's
    (sheet_instances), at a path below the root (sch_io_kicad_sexpr_parser.cpp:2428-2518 at
    9.0.8)."""
    p = Proj(tmp_path)
    three_sheets(p)
    tree = _cst.parse(p.root.read_bytes())
    doc = tree.lists[0]
    doc.find("version").atoms[1].set_text("20211123")
    pages = ['(path "/" (page "1"))']
    for k, block in enumerate(doc.find_all("sheet")):
        pages.append(f'(path "/{block.find("uuid").atoms[1].text}" (page "{k + 2}"))')
        block.remove_child(block.find("instances"))
    doc.remove_child(doc.find("sheet_instances"))
    doc.append_child(_cst.parse(f"(sheet_instances {' '.join(pages)})".encode()).lists[0], b"\n\t")
    p.root.write_bytes(_cst.serialize(tree))
    if sheet_order(p.root) != ["/", "/A/", "/B/", "/C/"]:
        raise RuntimeError("premise: kicad-cli reads the pages from the root's table")
    before = p.root.read_bytes()
    with pytest.raises(ToolError, match="20221110"):
        project.reorder_sheet_pages([sheet_uuid(sheets_of(p.root)[0])], schematic_path=str(p.root))
    assert p.root.read_bytes() == before


def test_reorder_refuses_a_sub_sheet_and_writes_nothing(tmp_path):
    p = Proj(tmp_path)
    mid = p.child("mid")
    p.sheet(p.root, mid, "Mid")
    p.sheet(mid, p.child("leaf"), "Leaf")
    leaf = sheet_uuid(sheets_of(mid)[0])
    before = mid.read_bytes()
    with pytest.raises(ToolError, match="root"):
        project.reorder_sheet_pages([leaf], schematic_path=str(mid))
    assert mid.read_bytes() == before


# ---------------------------------------------------------------------------------------------
# duplicate_sheet: instance at the parent's path, KiCad's field places, references of its own
# ---------------------------------------------------------------------------------------------


def resistor(child: Path, p: Proj, ref: str, x: float, pin1: str, pin2: str, y: float = 60.96):
    """A resistor with a local label on each pin end (pin 1 is 3.81 above the origin)."""
    x = round(round(x / 1.27) * 1.27, 4)
    schematic.place_component(
        "Device:R", ref, "1k", x, y, schematic_path=str(child), project_path=str(p.pro)
    )
    schematic.add_label(pin1, x, y - 3.81, 0, schematic_path=str(child))
    schematic.add_label(pin2, x, y + 3.81, 0, schematic_path=str(child))


def two_resistor_sheet(tmp_path: Path):
    """Sheet C whose child holds R1 (N, M) and R2 (M, K); returns (project, child, sheet uuid)."""
    p = Proj(tmp_path)
    child = p.child()
    resistor(child, p, "R1", 50.8, "N", "M")
    resistor(child, p, "R2", 76.2, "M", "K")
    return p, child, p.sheet(p.root, child, "C")


def net(*pins: tuple[str, str]) -> frozenset:
    return frozenset(pins)


@requires_cli
def test_a_duplicate_is_a_second_set_of_parts_not_the_same_parts_twice(tmp_path):
    p, _child, uid = two_resistor_sheet(tmp_path)
    assert node_sets(p.root) == {
        net(("R1", "1")),
        net(("R1", "2"), ("R2", "1")),
        net(("R2", "2")),
    }, "premise"
    project.duplicate_sheet(uid, "Copy", schematic_path=str(p.root), project_path=str(p.pro))
    # the copy's parts are the first free numbers, in the order of their positions
    assert node_sets(p.root) == {
        net(("R1", "1")),
        net(("R1", "2"), ("R2", "1")),
        net(("R2", "2")),
        net(("R3", "1")),
        net(("R3", "2"), ("R4", "1")),
        net(("R4", "2")),
    }


def as_unit_of(path: Path, index: int, ref: str, unit: int) -> None:
    """Make the index-th placed symbol of a file unit `unit` of part `ref`: its top-level
    unit, its Reference property and its instance entries (place_component refuses a
    reference the file already holds, so a second unit is made this way)."""

    def go(root):
        sym = root.find_all("symbol")[index]
        sym.find("unit").atoms[1].set_text(str(unit))
        for prop in sym.find_all("property"):
            if prop.atoms[1].text == "Reference":
                prop.atoms[2].set_text(ref)
        for pj in sym.find("instances").find_all("project"):
            for entry in pj.find_all("path"):
                entry.find("unit").atoms[1].set_text(str(unit))
                entry.find("reference").atoms[1].set_text(ref)

    edit(path, go)


@requires_cli
def test_the_units_of_one_part_keep_one_reference_in_the_copy(tmp_path):
    p = Proj(tmp_path)
    child = p.child()
    for x, ref in ((50.8, "U1"), (101.6, "U2")):
        schematic.place_component(
            "Amplifier_Operational:LM2904",
            ref,
            "LM2904",
            x,
            60.96,
            schematic_path=str(child),
            project_path=str(p.pro),
        )
    as_unit_of(child, 1, "U1", 2)
    uid = p.sheet(p.root, child, "C")
    pins = pins_by_ref(p.root)
    assert set(pins) == {"U1"} and len(pins["U1"]) >= 6, "premise: both units are one part"
    project.duplicate_sheet(uid, "Copy", schematic_path=str(p.root), project_path=str(p.pro))
    after = pins_by_ref(p.root)
    assert set(after) == {"U1", "U2"}
    assert after["U1"] == after["U2"] == pins["U1"]


def reference_pairs(path: Path) -> list[tuple[str, list[tuple[str, str]]]]:
    """(Reference property, [(instance path, instance reference)]) per placed symbol."""
    out = []
    for sym in kids(top(path.read_bytes()), b"symbol"):
        prop = next(
            q.items[2].value.decode() for q in kids(sym, b"property") if value(q) == "Reference"
        )
        entries = []
        inst = SS._child(sym, b"instances")
        for pj in kids(inst, b"project") if inst is not None else []:
            for entry in kids(pj, b"path"):
                entries.append((value(entry), value(SS._child(entry, b"reference"))))
        out.append((prop, entries))
    return out


def copy_file_of(root: Path) -> Path:
    name = next(
        q.items[2].value.decode()
        for q in kids(sheets_of(root)[-1], b"property")
        if value(q) == "Sheetfile"
    )
    return root.parent / name


@pytest.mark.usefixtures("stock_parts")
def test_the_copy_has_one_entry_at_its_own_path_and_a_matching_property(tmp_path):
    p, child, uid = two_resistor_sheet(tmp_path)
    project.duplicate_sheet(uid, "Copy", schematic_path=str(p.root), project_path=str(p.pro))
    new_block = sheet_uuid(sheets_of(p.root)[-1])
    live = f"/{file_uuid(p.root)}/{new_block}"
    assert sorted(reference_pairs(copy_file_of(p.root))) == [
        ("R3", [(live, "R3")]),
        ("R4", [(live, "R4")]),
    ]
    assert [ref for ref, _ in reference_pairs(child)] == ["R1", "R2"], "the source is untouched"


@pytest.mark.usefixtures("stock_parts")
def test_the_copy_numbers_from_the_reference_a_symbol_has_at_its_live_path(tmp_path):
    """R1's entry at the sheet's path says R5, though its Reference property says R1, so R1
    is free for the copy."""
    p = Proj(tmp_path)
    child = p.child()
    schematic.place_component(
        "Device:R", "R1", "1k", 50.8, 50.8, schematic_path=str(child), project_path=str(p.pro)
    )
    uid = p.sheet(p.root, child, "C")

    def go(root):
        for entry in root.find("symbol").find("instances").find("project").find_all("path"):
            entry.find("reference").atoms[1].set_text("R5")

    edit(child, go)
    project.duplicate_sheet(uid, "Copy", schematic_path=str(p.root), project_path=str(p.pro))
    live = f"/{file_uuid(p.root)}/{sheet_uuid(sheets_of(p.root)[-1])}"
    assert reference_pairs(copy_file_of(p.root)) == [("R1", [(live, "R1")])]


@pytest.mark.usefixtures("stock_parts")
def test_duplicate_is_one_insertion_in_the_parent_at_the_parents_path(tmp_path):
    p, _child, uid = two_resistor_sheet(tmp_path)
    before = p.root.read_bytes()
    project.duplicate_sheet(uid, "Copy", schematic_path=str(p.root), project_path=str(p.pro))
    J.assert_inserted(before, p.root.read_bytes(), heads=["sheet"])
    copy = sheets_of(p.root)[-1]
    assert instance_paths(copy) == [("p", f"/{file_uuid(p.root)}", "3")]


@requires_cli
def test_kicad_cli_lists_the_copy_at_the_page_it_was_given(tmp_path):
    """Page 3 is free, so the copy takes it and lands between A (2) and B (4), though it sits
    left of A."""
    p = Proj(tmp_path)
    p.sheet(p.root, p.child("a"), "A", x=127.0, y=25.4)
    b = p.sheet(p.root, p.child("b"), "B", x=25.4, y=25.4)
    set_pages(p.root, {"B": "4"})
    assert sheet_order(p.root) == ["/", "/A/", "/B/"], "premise"
    project.duplicate_sheet(b, "Copy", schematic_path=str(p.root), project_path=str(p.pro))
    assert sheet_order(p.root) == ["/", "/A/", "/Copy/", "/B/"]


def test_the_copys_fields_are_where_kicad_autoplaces_them(tmp_path):
    p = Proj(tmp_path)
    uid = p.sheet(
        p.root,
        p.child(),
        "C",
        pins=[{"name": "A", "direction": "input"}, {"name": "B", "direction": "output"}],
    )
    project.duplicate_sheet(uid, "Copy", schematic_path=str(p.root), project_path=str(p.pro))
    source, copy = sheets_of(p.root)
    for group in (source, copy):
        want = SS.autoplace(SS.read_sheet(group), 1524)
        assert SS.field_at(group, b"Sheetname") == want.name_at, sheet_name(group)
        assert SS.field_at(group, b"Sheetfile") == want.file_at, sheet_name(group)
    assert sheet_uuid(copy) != sheet_uuid(source)
    src, dup = SS.read_sheet(source), SS.read_sheet(copy)
    assert [(q.y, q.justify) for q in dup.pins] == [(q.y, q.justify) for q in src.pins]
    assert all(
        q.x - dup.x == r.x - src.x and q.side == r.side
        for q, r in zip(dup.pins, src.pins, strict=True)
    )


@pytest.mark.usefixtures("stock_parts")
def test_the_copy_keeps_what_the_source_block_carries(tmp_path):
    """A field the user added to the source block comes along."""
    p, _child, uid = two_resistor_sheet(tmp_path)

    def go(root):
        sheet = root.find("sheet")
        extra = next(q for q in sheet.find_all("property") if q.atoms[1].text == "Sheetfile").copy()
        extra.atoms[1].set_text("Note")
        extra.atoms[2].set_text("keep me")
        sheet.insert_after(sheet.find_all("property")[-1], extra)

    edit(p.root, go)
    project.duplicate_sheet(uid, "Copy", schematic_path=str(p.root), project_path=str(p.pro))
    notes = [
        q.items[2].value.decode()
        for q in kids(sheets_of(p.root)[-1], b"property")
        if value(q) == "Note"
    ]
    assert notes == ["keep me"]


def test_a_source_with_sub_sheets_is_refused_and_nothing_is_written(tmp_path):
    p = Proj(tmp_path)
    mid = p.child("mid")
    uid = p.sheet(p.root, mid, "Mid")
    p.sheet(mid, p.child("leaf"), "Leaf")
    before = {f.name: f.read_bytes() for f in p.dir.iterdir()}
    with pytest.raises(ToolError, match="sub-sheet"):
        project.duplicate_sheet(uid, "Copy", schematic_path=str(p.root), project_path=str(p.pro))
    assert {f.name: f.read_bytes() for f in p.dir.iterdir()} == before


def test_a_failed_parent_write_leaves_no_copy_behind(tmp_path, monkeypatch):
    """The copy is written first; when the parent's write then fails, the copy goes too, so
    the same call can be made again."""
    p = Proj(tmp_path)
    uid = p.sheet(p.root, p.child("c"), "C")
    before = {f.name: f.read_bytes() for f in p.dir.iterdir()}
    real = project._atomic_write

    def fail_on_parent(path, data):
        if Path(path) == p.root:
            raise OSError("simulated failure on the parent's write")
        return real(path, data)

    monkeypatch.setattr(project, "_atomic_write", fail_on_parent)
    with pytest.raises(OSError, match="simulated"):
        project.duplicate_sheet(uid, "Copy", schematic_path=str(p.root), project_path=str(p.pro))
    monkeypatch.undo()
    assert {f.name: f.read_bytes() for f in p.dir.iterdir()} == before
    project.duplicate_sheet(uid, "Copy", schematic_path=str(p.root), project_path=str(p.pro))


@pytest.mark.kicad_placeholder("p/copy.kicad_sch")
def test_a_copy_file_another_call_made_meanwhile_is_neither_replaced_nor_removed(
    tmp_path, monkeypatch
):
    """The file name is free when the call starts and taken by the time it writes, as when two
    calls ask for one new_file_name at once: the call refuses and leaves the other's file."""
    p = Proj(tmp_path)
    uid = p.sheet(p.root, p.child("c"), "C")
    theirs = p.dir / "copy.kicad_sch"
    real = project._set_sheet_instances

    def other_call_writes_first(*args, **kwargs):
        theirs.write_bytes(b"the other call's copy")
        return real(*args, **kwargs)

    monkeypatch.setattr(project, "_set_sheet_instances", other_call_writes_first)
    before = p.root.read_bytes()
    with pytest.raises(ToolError, match="already exists"):
        project.duplicate_sheet(
            uid,
            "Copy",
            schematic_path=str(p.root),
            project_path=str(p.pro),
            new_file_name=theirs.name,
        )
    assert theirs.read_bytes() == b"the other call's copy"
    assert p.root.read_bytes() == before


# ---------------------------------------------------------------------------------------------
# flatten_hierarchy: the flat file's netlist is the hierarchy's
# ---------------------------------------------------------------------------------------------


def flatten(root: Path) -> tuple[Path, str]:
    out = root.with_name("flat.kicad_sch")
    message = project.flatten_hierarchy(schematic_path=str(root), output_path=str(out))
    return out, message


def assert_same_netlist(root: Path, flat: Path) -> None:
    want, got = node_sets(root), node_sets(flat)
    assert got == want, (
        "only in the hierarchy",
        sorted(sorted(s) for s in want - got),
        "only in the flat file",
        sorted(sorted(s) for s in got - want),
    )


def pin_edge(root: Path, sheet: str, pin: str) -> tuple[float, float]:
    x, y, _angle = pins_of(next(s for s in sheets_of(root) if sheet_uuid(s) == sheet))[pin]
    return x / SS.IU_PER_MM, y / SS.IU_PER_MM


def build_unconnected(p: Proj) -> None:
    """F1: parts with nothing on their pins, at the root and in the child."""
    child = p.child("c")
    p.sheet(p.root, child, "C", x=152.4, y=25.4)
    for path, ref in ((p.root, "R1"), (child, "R2")):
        schematic.place_component(
            "Device:R", ref, "1k", 50.8, 50.8, schematic_path=str(path), project_path=str(p.pro)
        )


def build_pin_wired_both_sides(p: Proj) -> None:
    """F2: a root pin wired to a sheet pin, the child's hierarchical label wired to a pin,
    and no label of one name on both sides."""
    child = p.child("c")
    uid = p.sheet(p.root, child, "C", x=152.4, y=50.8)
    project.add_sheet_pin(uid, "IN", "input", str(p.root))
    px, py = pin_edge(p.root, uid, "IN")
    schematic.place_component(
        "Device:R",
        "R1",
        "1k",
        101.6,
        py - 3.81,
        schematic_path=str(p.root),
        project_path=str(p.pro),
    )
    schematic.add_wires([{"x1": 101.6, "y1": py, "x2": px, "y2": py}], schematic_path=str(p.root))
    schematic.place_component(
        "Device:R", "R2", "1k", 101.6, 54.61, schematic_path=str(child), project_path=str(p.pro)
    )
    schematic.add_hierarchical_label("IN", "input", 76.2, 50.8, schematic_path=str(child))
    schematic.add_wires(
        [{"x1": 76.2, "y1": 50.8, "x2": 101.6, "y2": 50.8}], schematic_path=str(child)
    )


def build_same_local_label(p: Proj) -> None:
    """F3: a local label N on a resistor pin in each of two sheets: two nets."""
    for k, (name, x) in enumerate((("A", 152.4), ("B", 203.2)), 1):
        child = p.child(name.lower())
        resistor(child, p, f"R{k}", 50.8, "N", f"M{k}")
        p.sheet(p.root, child, name, x=x, y=25.4)


def build_reused_file(p: Proj) -> None:
    """F4: one file behind two sheet blocks, its resistor R1 in one and R2 in the other."""
    child = p.child("c")
    resistor(child, p, "R1", 50.8, "N", "M")
    p.sheet(p.root, child, "C1", x=152.4, y=25.4)
    p.sheet(p.root, child, "C2", x=203.2, y=25.4)
    second = sheet_uuid(sheets_of(p.root)[1])

    def go(root):
        for entry in root.find("symbol").find("instances").find("project").find_all("path"):
            if entry.atoms[1].text.endswith(second):
                entry.find("reference").atoms[1].set_text("R2")

    edit(child, go)


def build_nested(p: Proj) -> None:
    """F5: root, A, B, with a resistor in B."""
    a, b = p.child("a"), p.child("b")
    p.sheet(p.root, a, "A", x=152.4, y=25.4)
    p.sheet(a, b, "B", x=152.4, y=25.4)
    resistor(b, p, "R1", 50.8, "N", "M")


def build_four_rotations(p: Proj) -> None:
    """Labels at all four rotations on both sides of the boundary."""
    child = p.child("c")
    uid = p.sheet(p.root, child, "C", x=152.4, y=25.4)
    project.modify_hierarchical_sheet(uid, str(p.root), height=20.32)
    for k, rotation in enumerate((0, 90, 180, 270)):
        x = 40.64 + 20.32 * k
        schematic.place_component(
            "Device:R",
            f"R{k + 1}",
            "1k",
            x,
            60.96,
            schematic_path=str(child),
            project_path=str(p.pro),
        )
        schematic.add_hierarchical_label(
            f"P{rotation}", "input", x, 57.15, rotation, schematic_path=str(child)
        )
        schematic.add_label(f"Q{rotation}", x, 64.77, rotation, schematic_path=str(child))
        project.add_sheet_pin(uid, f"P{rotation}", "input", str(p.root))
        p.stub(p.root, uid, f"P{rotation}", f"L{rotation}")


def build_global_and_power(p: Proj) -> None:
    """A global label and a power symbol shared by two sheets."""
    for k, (name, x) in enumerate((("A", 152.4), ("B", 203.2)), 1):
        child = p.child(name.lower())
        schematic.place_component(
            "Device:R",
            f"R{k}",
            "1k",
            50.8,
            60.96,
            schematic_path=str(child),
            project_path=str(p.pro),
        )
        schematic.add_global_label("G", 50.8, 57.15, 0, "input", schematic_path=str(child))
        schematic.place_component(
            "power:GND",
            f"#PWR0{k}",
            "GND",
            50.8,
            64.77,
            schematic_path=str(child),
            project_path=str(p.pro),
        )
        p.sheet(p.root, child, name, x=x, y=25.4)


def build_units_across_sheets(p: Proj) -> None:
    """The two units of U1 on two sheets."""
    for k, name in enumerate(("A", "B"), 1):
        child = p.child(name.lower())
        schematic.place_component(
            "Amplifier_Operational:LM2904",
            "U1",
            "LM2904",
            76.2,
            60.96,
            schematic_path=str(child),
            project_path=str(p.pro),
        )
        p.sheet(p.root, child, name, x=127.0 + 50.8 * k, y=25.4)
    as_unit_of(p.dir / "b.kicad_sch", 0, "U1", 2)


def build_local_label_beside_a_global_one(p: Proj) -> None:
    """Sheet B has a global label G and a local label G, which join; sheet A has a local
    label G alone, which joins neither."""
    for ref, name, x in (("R1", "A", 152.4), ("R2", "B", 203.2)):
        child = p.child(name.lower())
        resistor(child, p, ref, 50.8, "G", "M" + ref)
        p.sheet(p.root, child, name, x=x, y=25.4)
    b = p.dir / "b.kicad_sch"
    schematic.place_component(
        "Device:R", "R3", "1k", 76.2, 60.96, schematic_path=str(b), project_path=str(p.pro)
    )
    schematic.add_global_label("G", 76.2, 57.15, 0, "input", schematic_path=str(b))


def build_local_label_beside_a_power_symbol(p: Proj) -> None:
    """A local label named like the power net on its own sheet, and another on a sheet
    that has no such power symbol."""
    for ref, name, x in (("R1", "A", 152.4), ("R2", "B", 203.2)):
        child = p.child(name.lower())
        resistor(child, p, ref, 50.8, "GND", "M" + ref)
        p.sheet(p.root, child, name, x=x, y=25.4)
    b = p.dir / "b.kicad_sch"
    schematic.place_component(
        "Device:R", "R3", "1k", 76.2, 60.96, schematic_path=str(b), project_path=str(p.pro)
    )
    schematic.place_component(
        "power:GND", "#PWR01", "GND", 76.2, 57.15, schematic_path=str(b), project_path=str(p.pro)
    )


def build_hierarchical_label_beside_a_global_one(p: Proj) -> None:
    """A pin wired to a root part, its hierarchical label P on a part of the child, and a
    global label P on another part of the child: one net."""
    child = p.child("c")
    uid = p.sheet(p.root, child, "C", x=152.4, y=50.8)
    project.add_sheet_pin(uid, "P", "input", str(p.root))
    px, py = pin_edge(p.root, uid, "P")
    schematic.place_component(
        "Device:R",
        "R1",
        "1k",
        101.6,
        py - 3.81,
        schematic_path=str(p.root),
        project_path=str(p.pro),
    )
    schematic.add_wires([{"x1": 101.6, "y1": py, "x2": px, "y2": py}], schematic_path=str(p.root))
    for ref, x in (("R2", 50.8), ("R3", 76.2)):
        schematic.place_component(
            "Device:R", ref, "1k", x, 60.96, schematic_path=str(child), project_path=str(p.pro)
        )
    schematic.add_hierarchical_label("P", "input", 50.8, 57.15, 0, schematic_path=str(child))
    schematic.add_global_label("P", 76.2, 57.15, 0, "input", schematic_path=str(child))


def build_pin_beside_a_local_label(p: Proj) -> None:
    """Sheet pin X wired to a root part; the child has a local label X and no hierarchical
    label X, so the pin joins nothing in the child."""
    child = p.child("c")
    uid = p.sheet(p.root, child, "C", x=152.4, y=50.8)
    project.add_sheet_pin(uid, "X", "input", str(p.root))
    px, py = pin_edge(p.root, uid, "X")
    schematic.place_component(
        "Device:R",
        "R1",
        "1k",
        101.6,
        py - 3.81,
        schematic_path=str(p.root),
        project_path=str(p.pro),
    )
    schematic.add_wires([{"x1": 101.6, "y1": py, "x2": px, "y2": py}], schematic_path=str(p.root))
    resistor(child, p, "R2", 50.8, "X", "M")


def make_pin_two_hidden_power(lib, hidden: bool) -> None:
    """Pin 2 of a resistor's definition becomes power_in VCC, hidden or not."""
    pin = [q for sub in lib.find_all("symbol") for q in sub.find_all("pin")][1]
    pin.atoms[1].set_text("power_in")
    pin.find("name").atoms[1].set_text("VCC")
    if hidden:
        pin.insert_after(pin.find("length"), _cst.parse(b"(hide yes)").lists[0])


def device_r(root):
    return next(
        s for s in root.find("lib_symbols").find_all("symbol") if s.atoms[1].text == "Device:R"
    )


def build_local_label_beside_a_hidden_power_pin(p: Proj) -> None:
    """R1's hidden power_in pin 2 is the global net VCC, which a local label VCC on pin 1 of
    the same sheet joins."""
    child = p.child("c")
    resistor(child, p, "R1", 50.8, "VCC", "M1")
    edit(child, lambda r: make_pin_two_hidden_power(device_r(r), True))
    p.sheet(p.root, child, "C", x=152.4, y=25.4)


BUILDERS = [
    pytest.param(build_unconnected, id="unconnected-pins"),
    pytest.param(build_pin_wired_both_sides, id="pin-wired-both-sides"),
    pytest.param(build_same_local_label, id="same-local-label"),
    pytest.param(build_reused_file, id="reused-file"),
    pytest.param(build_nested, id="nested"),
    pytest.param(build_four_rotations, id="four-rotations"),
    pytest.param(build_global_and_power, id="global-and-power"),
    pytest.param(build_units_across_sheets, id="units-across-sheets"),
    pytest.param(build_local_label_beside_a_global_one, id="local-beside-global"),
    pytest.param(build_local_label_beside_a_power_symbol, id="local-beside-power"),
    pytest.param(build_hierarchical_label_beside_a_global_one, id="hierarchical-beside-global"),
    pytest.param(build_pin_beside_a_local_label, id="pin-beside-local-label"),
    pytest.param(build_local_label_beside_a_hidden_power_pin, id="local-beside-hidden-power-pin"),
]


@requires_cli
@pytest.mark.parametrize("build", BUILDERS)
def test_the_flat_file_has_the_hierarchys_netlist(tmp_path, build):
    p = Proj(tmp_path)
    build(p)
    assert node_sets(p.root), "premise: the hierarchy has nets"
    files = {f.name: f.read_bytes() for f in p.dir.iterdir()}  # after kicad-cli's own files
    flat, message = flatten(p.root)
    assert "components" in message
    assert {f.name: f.read_bytes() for f in p.dir.iterdir() if f != flat} == files
    assert_same_netlist(p.root, flat)


@requires_cli
def test_every_flat_symbol_has_one_entry_at_the_flat_files_path(tmp_path):
    p = Proj(tmp_path)
    build_reused_file(p)
    flat, _ = flatten(p.root)
    here = f"/{file_uuid(flat)}"
    entries = [e for _prop, e in reference_pairs(flat)]
    assert all(len(e) == 1 and e[0][0] == here for e in entries), entries
    assert sorted(e[0][1] for e in entries) == ["R1", "R2"]
    assert not kids(top(flat.read_bytes()), b"symbol_instances")


@requires_cli
@pytest.mark.no_kicad_validation  # the subject is a sheet whose file is gone
def test_a_missing_sheet_file_is_an_empty_sheet(tmp_path):
    p = Proj(tmp_path)
    child = p.child("c")
    resistor(child, p, "R1", 50.8, "N", "M")
    uid = p.sheet(p.root, child, "C", x=152.4, y=25.4)
    project.add_sheet_pin(uid, "IN", "input", str(p.root))
    child.unlink()
    flat, message = flatten(p.root)
    assert "c.kicad_sch" in message
    assert_same_netlist(p.root, flat)


@no_demos
@requires_cli
@pytest.mark.parametrize("demo", ["simulation/subsheets", "complex_hierarchy", "pic_programmer"])
def test_a_shipped_hierarchy_flattens_to_its_own_netlist(tmp_path, demo):
    root = copy_demo(tmp_path, demo)
    flat, _ = flatten(root)
    assert_same_netlist(root, flat)


# ---------------------------------------------------------------------------------------------
# flatten_hierarchy: where it cannot keep the netlist it refuses and writes nothing
# ---------------------------------------------------------------------------------------------


def refused(p: Proj, match: str) -> None:
    before = {f.name: f.read_bytes() for f in p.dir.iterdir()}
    out = p.dir / "flat.kicad_sch"
    with pytest.raises(ToolError, match=match):
        project.flatten_hierarchy(schematic_path=str(p.root), output_path=str(out))
    assert {f.name: f.read_bytes() for f in p.dir.iterdir()} == before


def test_a_bus_on_a_sheet_pin_is_refused(tmp_path):
    p = Proj(tmp_path)
    uid = p.sheet(p.root, p.child("c"), "C")
    project.add_sheet_pin(uid, "D[0..3]", "input", str(p.root))
    refused(p, r"D\[0\.\.3\].*bus")


def test_a_bus_label_in_a_sub_sheet_is_refused(tmp_path):
    p = Proj(tmp_path)
    child = p.child("c")
    p.sheet(p.root, child, "C")
    schematic.add_label("D{A0 A1}", 40.64, 40.64, 0, schematic_path=str(child))
    refused(p, r"c\.kicad_sch.*bus")


@requires_cli
def test_an_overbar_name_is_not_a_bus(tmp_path):
    p = Proj(tmp_path)
    child = p.child("c")
    p.sheet(p.root, child, "C", pins=[{"name": "~{RESET}", "direction": "input"}])
    resistor(child, p, "R1", 50.8, "~{RESET}", "M")
    flat, _ = flatten(p.root)
    assert_same_netlist(p.root, flat)


def test_a_sheet_file_from_before_instances_moved_onto_symbols_is_refused(tmp_path):
    p = Proj(tmp_path)
    child = p.child("c")
    p.sheet(p.root, child, "C")
    edit(child, lambda root: root.find("version").atoms[1].set_text("20211014"))
    refused(p, r"c\.kicad_sch.*(version|older|format)")


def test_a_bus_alias_in_a_sub_sheet_is_refused(tmp_path):
    p = Proj(tmp_path)
    child = p.child("c")
    p.sheet(p.root, child, "C")

    def go(root):
        alias = _cst.parse(b'(bus_alias "DATA"\n\t(members "D0" "D1")\n)').lists[0]
        root.insert_before(root.find("lib_symbols"), alias)

    edit(child, go)
    refused(p, r"bus_alias.*c\.kicad_sch|c\.kicad_sch.*bus_alias")


def two_definitions(p: Proj, other: str, edit_definition) -> None:
    """R in two files, the second file's Device:R changed by edit_definition(lib symbol)."""
    first = p.root if other == "root" else p.child("a")
    second = p.child("c")
    for path, ref in ((first, "R1"), (second, "R2")):
        schematic.place_component(
            "Device:R", ref, "1k", 50.8, 50.8, schematic_path=str(path), project_path=str(p.pro)
        )
    if first != p.root:
        p.sheet(p.root, first, "A", x=127.0, y=25.4)
    p.sheet(p.root, second, "C", x=203.2, y=25.4)

    def go(root):
        edit_definition(
            next(
                s
                for s in root.find("lib_symbols").find_all("symbol")
                if s.atoms[1].text == "Device:R"
            )
        )

    edit(second, go)


def renumber_first_pin(lib) -> None:
    pin = next(q for sub in lib.find_all("symbol") for q in sub.find_all("pin"))
    pin.find("number").atoms[1].set_text("9")


def reword(lib) -> None:
    prop = next(q for q in lib.find_all("property") if q.atoms[1].text == "Description")
    prop.atoms[2].set_text("a resistor the library has since changed")


@pytest.mark.usefixtures("stock_parts")
@pytest.mark.parametrize("other", ["root", "sibling"])
def test_two_definitions_of_one_library_symbol_with_different_pins_are_refused(tmp_path, other):
    p = Proj(tmp_path)
    two_definitions(p, other, renumber_first_pin)
    refused(p, r"Device:R")


@requires_cli
@pytest.mark.parametrize("other", ["root", "sibling"])
def test_a_library_symbol_that_differs_only_in_its_fields_flattens(tmp_path, other):
    """A part whose library has been updated since one sheet was saved: the pins are the
    same, so the nets are."""
    p = Proj(tmp_path)
    two_definitions(p, other, reword)
    flat, _ = flatten(p.root)
    assert_same_netlist(p.root, flat)


@pytest.mark.usefixtures("stock_parts")
def test_two_definitions_differing_only_in_a_power_pin_being_hidden_are_refused(tmp_path):
    """A hidden power_in pin is a global net of its name and a visible one is not."""
    p = Proj(tmp_path)
    a, b = p.child("a"), p.child("b")
    for path, refs in ((a, ("R1",)), (b, ("R2", "R3"))):
        for k, ref in enumerate(refs):
            schematic.place_component(
                "Device:R",
                ref,
                "1k",
                50.8 + 25.4 * k,
                60.96,
                schematic_path=str(path),
                project_path=str(p.pro),
            )
    edit(a, lambda r: make_pin_two_hidden_power(device_r(r), False))
    edit(b, lambda r: make_pin_two_hidden_power(device_r(r), True))
    p.sheet(p.root, a, "A", x=152.4, y=25.4)
    p.sheet(p.root, b, "B", x=203.2, y=25.4)
    refused(p, r"Device:R")


_UNIT_2_WITH_HIDDEN_POWER_PIN = (
    b'(symbol "R_2_1"'
    b" (pin passive line (at 0 3.81 270) (length 1.27)"
    b' (name "~" (effects (font (size 1.27 1.27))))'
    b' (number "3" (effects (font (size 1.27 1.27)))))'
    b" (pin power_in line (at 10.16 0 180) (length 2.54) (hide yes)"
    b' (name "VCC" (effects (font (size 1.27 1.27))))'
    b' (number "4" (effects (font (size 1.27 1.27))))))'
)


@requires_cli
def test_a_hidden_power_pin_of_a_unit_another_sheet_places_does_not_keep_this_sheets_label(
    tmp_path,
):
    """One file used twice, its part placed as unit 1 at the first sheet and as unit 2 (which
    alone has a hidden power_in pin VCC) at the second: the first sheet's label VCC is local."""
    p = Proj(tmp_path)
    child = p.child("c")
    resistor(child, p, "R1", 50.8, "VCC", "M")
    edit(
        child,
        lambda r: device_r(r).append_child(
            _cst.parse(_UNIT_2_WITH_HIDDEN_POWER_PIN).lists[0], b"\n\t\t\t"
        ),
    )
    p.sheet(p.root, child, "C1", x=152.4, y=25.4)
    p.sheet(p.root, child, "C2", x=203.2, y=25.4)
    second = sheet_uuid(sheets_of(p.root)[1])

    def place_unit_two(root):
        for entry in root.find("symbol").find("instances").find("project").find_all("path"):
            if entry.atoms[1].text.endswith(second):
                entry.find("unit").atoms[1].set_text("2")

    edit(child, place_unit_two)
    flat, _ = flatten(p.root)
    assert_same_netlist(p.root, flat)


def test_a_root_label_named_like_a_global_net_of_another_sheet_is_refused(tmp_path):
    """On the flat file's one sheet the root's local label G would join the global net G that
    only the child had."""
    p = Proj(tmp_path)
    child = p.child("c")
    p.sheet(p.root, child, "C", x=152.4, y=25.4)
    schematic.add_global_label("G", 40.64, 40.64, 0, "input", schematic_path=str(child))
    schematic.add_label("G", 40.64, 40.64, 0, schematic_path=str(p.root))
    refused(p, r"'G'.*global")


@pytest.mark.no_kicad_validation  # the power symbol's definition is edited by hand
@pytest.mark.usefixtures("stock_parts")
@pytest.mark.parametrize("where", ["root", "sub-sheet"])
def test_a_local_power_symbol_is_refused(tmp_path, where):
    p = Proj(tmp_path)
    child = p.child("c")
    p.sheet(p.root, child, "C", x=152.4, y=25.4)
    target = p.root if where == "root" else child
    schematic.place_component(
        "power:GND",
        "#PWR01",
        "GND",
        50.8,
        50.8,
        schematic_path=str(target),
        project_path=str(p.pro),
    )

    def go(root):
        lib = next(
            s for s in root.find("lib_symbols").find_all("symbol") if s.atoms[1].text == "power:GND"
        )
        lib.find("power").append_child(_cst.Atom(b"local"), b" ")

    edit(target, go)
    refused(p, "local power")


@pytest.mark.parametrize("attribute", ["dnp", "in_bom", "on_board", "exclude_from_sim"])
def test_a_sheet_marked_out_of_the_design_is_refused(tmp_path, attribute):
    p = Proj(tmp_path)
    p.sheet(p.root, p.child("c"), "C")
    flipped = "yes" if attribute in ("dnp", "exclude_from_sim") else "no"
    edit(p.root, lambda root: root.find("sheet").find(attribute).atoms[1].set_text(flipped))
    refused(p, attribute)


def test_two_sheets_of_one_name_are_refused(tmp_path):
    p = Proj(tmp_path)
    p.sheet(p.root, p.child("a"), "Same", x=127.0)
    p.sheet(p.root, p.child("b"), "Same", x=203.2)
    refused(p, "Same")


@pytest.mark.no_kicad_validation
def test_an_unreadable_sheet_file_is_refused(tmp_path):
    p = Proj(tmp_path)
    child = p.child("c")
    p.sheet(p.root, child, "C")
    child.write_bytes(b"(kicad_sch (version 20250114)")
    refused(p, r"c\.kicad_sch")


def test_too_many_sheet_instances_are_refused(tmp_path, monkeypatch):
    p = Proj(tmp_path)
    child = p.child("c")
    p.sheet(p.root, child, "C1", x=127.0)
    p.sheet(p.root, child, "C2", x=203.2)
    monkeypatch.setattr(_connectivity, "_MAX_INSTANCES", 1)
    refused(p, "instances")


def hierarchical_demos() -> list[str]:
    """The shipped demo projects whose root schematic has sheets, as folders under demos/,
    without vme-wren, which is too large to flatten and judge in a test."""
    names = []
    for name, path in shipped(("demos",), (".kicad_pro",), ()):
        root = path.with_suffix(".kicad_sch")
        folder = name.split("/", 1)[1].rsplit("/", 1)[0]
        if root.is_file() and b"(sheet" + bytes([10]) in root.read_bytes().replace(
            CRLF, bytes([10])
        ):
            names.append(folder)
    return [n for n in names if n != "vme-wren"]


@pytest.mark.nightly
@no_demos
@requires_cli
def test_nightly_every_shipped_hierarchy_flattens_to_its_netlist_or_is_refused(tmp_path):
    flattened = 0
    for k, name in enumerate(hierarchical_demos()):
        root = copy_demo(tmp_path, name, into=f"demo{k}")
        try:
            flat, _ = flatten(root)
        except ToolError as refusal:
            assert "bus" in str(refusal), (name, str(refusal))
            assert not root.with_name("flat.kicad_sch").exists(), name
            continue
        assert_same_netlist(root, flat)
        flattened += 1
    assert flattened >= 3
