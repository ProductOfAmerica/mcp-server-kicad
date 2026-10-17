"""Rung 1's judge helpers, each held to something other than itself.

The conftest helpers that ask kicad-cli for a verdict (run_erc, assert_kicad_parseable,
assert_kicad_pcb_parseable, netlist_nodes) answer to kicad-cli on files it refuses. The
autouse oracle's wider scope answers to kicad-cli's library loaders and to the one
library-table rule KiCad 9's and KiCad 10's readers agree on. netlist_oracle's naming
answers to how KiCad prints a net name, and CST node extents answer to the DSNLEXER port.
Each test fails against the helper it replaced. A premise a test rests on, such as
kicad-cli refusing a file, is checked by calling kicad-cli here, not through the helper
under test, and raises RuntimeError when it does not hold.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
import sexpr_gen as G
from conftest import (
    _K9_BOARD_VERSION_MAX,
    _K9_SYMBOL_LIB_VERSION_MAX,
    KICAD_SYM_VERSION,
    _cli_can_load_library,
    _kicad_cli_library_problem,
    _kicad_cli_major,
    _lib_table_problem,
    _sheet_files,
    _validate_kicad_outputs,
    assert_kicad_parseable,
    assert_kicad_pcb_parseable,
    build_test_part_symbol,
    netlist_nodes,
    parse_netlist_nodes,
    requires_cli,
    run_erc,
)
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref.richio import quotes
from kiutils.symbol import SymbolLib
from mcp.server.mcpserver.exceptions import ToolError
from netlist_oracle import Netlist, is_named, judge, nets, parse_kicadxml

from mcp_server_kicad import _cst, project
from mcp_server_kicad._shared import _kicad_root, _run_cli

_FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"
_ROOT_UUID = b"f47ac10b-58cc-4372-a567-0e02b2c3d479"  # the fixture's own uuid
_ERC = ["sch", "erc", "--format", "json", "--severity-all"]
_DRC = ["pcb", "drc", "--format", "json", "--severity-all"]
_NETLIST = ["sch", "export", "netlist", "--format", "kicadxml"]


def _kicad_cli_output(args: list[str], path: Path) -> bytes | None:
    """What kicad-cli writes for ``args`` on ``path``, or None when it writes nothing.

    The premise check, run directly rather than through the helpers under test.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        out = Path(scratch) / "out"
        _run_cli([*args, "--output", str(out), str(path)], check=False)
        return out.read_bytes() if out.is_file() else None


def _truncate(path: Path, args: list[str]) -> None:
    """Cut ``path`` in half, and make sure kicad-cli can no longer load it."""
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    if _kicad_cli_output(args, path) is not None:
        raise RuntimeError(f"premise: kicad-cli still loads {path.name} cut in half")


# ---------------------------------------------------------------------------
# stale reports
# ---------------------------------------------------------------------------


@requires_cli
@pytest.mark.no_kicad_validation  # the truncated schematic is the subject
def test_run_erc_refuses_a_stale_report(kicad_native_sch):
    """A schematic kicad-cli loaded once and cannot load now must not pass on the first
    run's report. The old helper wrote its report beside the file, never deleted it, and
    returned whatever was there, so this truncated file passed (measured on kicad-cli
    9.0.8, 2026-10-10)."""
    assert run_erc(kicad_native_sch)["sheets"]
    _truncate(kicad_native_sch, _ERC)
    with pytest.raises(RuntimeError, match="kicad-cli ERC failed"):
        run_erc(kicad_native_sch)


@requires_cli
@pytest.mark.no_kicad_validation  # the truncated schematic is the subject
def test_assert_kicad_parseable_refuses_a_stale_report(kicad_native_sch):
    """The same through assert_kicad_parseable, which read the report run_erc left beside
    the file, and whose memo must key on the bytes kicad-cli was asked about."""
    assert_kicad_parseable(kicad_native_sch)
    _truncate(kicad_native_sch, _ERC)
    with pytest.raises(pytest.fail.Exception, match="kicad-cli cannot parse"):
        assert_kicad_parseable(kicad_native_sch)


@requires_cli
@pytest.mark.no_kicad_validation  # the truncated board is the subject
def test_pcb_parseable_refuses_a_stale_report(scratch_pcb):
    """The board twin: a board kicad-cli loaded once and cannot load now must fail."""
    assert_kicad_pcb_parseable(scratch_pcb)
    _truncate(scratch_pcb, _DRC)
    with pytest.raises(pytest.fail.Exception, match="kicad-cli cannot parse"):
        assert_kicad_pcb_parseable(scratch_pcb)


@requires_cli
@pytest.mark.no_kicad_validation  # the truncated schematic is the subject
def test_netlist_nodes_refuses_a_stale_netlist(kicad_native_sch):
    """The netlist twin: an export kicad-cli cannot make now must not read as the last one."""
    assert netlist_nodes(kicad_native_sch)
    _truncate(kicad_native_sch, _NETLIST)
    with pytest.raises(RuntimeError, match="netlist export failed"):
        netlist_nodes(kicad_native_sch)


# ---------------------------------------------------------------------------
# net names
# ---------------------------------------------------------------------------


def _kicadxml(*nets_: tuple[str, list[tuple[str, str]]], sheets=("/",)) -> bytes:
    """A kicadxml netlist shaped like kicad-cli's: a design header listing the sheet
    paths, then each (printed name, nodes) as a net."""
    header = "".join(
        f'<sheet number="{i}" name="{name}" tstamps="/"/>' for i, name in enumerate(sheets, 1)
    )
    body = "".join(
        f'<net code="{i}" name="{name}" class="Default">'
        + "".join(f'<node ref="{ref}" pin="{pin}" pintype="passive"/>' for ref, pin in nodes)
        + "</net>"
        for i, (name, nodes) in enumerate(nets_, 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<export version="E">'
        f"<design>{header}</design><nets>{body}</nets></export>\n"
    ).encode()


def test_netlist_nodes_refuses_names_printed_twice():
    """Two different node sets are two nets whatever they print; keyed by printed name,
    the old helper let the second silently replace the first. Refused when the names
    match as printed, and when they match once one leading slash is dropped."""
    clean = _kicadxml(("/A/B", [("R2", "1")]), ("unconnected-(R1-Pad2)", [("R1", "2")]))
    assert parse_netlist_nodes(clean) == {
        "A/B": {("R2", "1"): "passive"},
        "unconnected-(R1-Pad2)": {("R1", "2"): "passive"},
    }
    for first, second in (("/A/B", "/A/B"), ("/VCC", "VCC")):
        xml = _kicadxml((first, [("R2", "1")]), (second, [("R1", "1")]))
        with pytest.raises(RuntimeError, match="two nets key as") as caught:
            parse_netlist_nodes(xml)
        assert "('R1', '1')" in str(caught.value) and "('R2', '1')" in str(caught.value)


def _native() -> tuple[bytes, bytes]:
    """kicad_native.kicad_sch and its line ending (CRLF in a Windows working tree)."""
    data = _FIXTURE.read_bytes()
    return data, (b"\r\n" if b"\r\n" in data else b"\n")


def _sheet_block(name: bytes, file: str, uuid: bytes, eol: bytes) -> bytes:
    """A (sheet ...) on the root as KiCad 9 writes one, ``file`` quoted as KiCad quotes it;
    its instance path is the root's (as in the complex_hierarchy demo KiCad ships)."""
    return _sheet_with_fields([("Sheetname", name.decode()), ("Sheetfile", file)], uuid, eol)


def _sheet_with_fields(fields: list[tuple], uuid: bytes, eol: bytes) -> bytes:
    """_sheet_block's (sheet ...) with ``fields`` for its properties, in order, each
    (name, value) or (name, value, N) for one carrying an (id N)."""
    font = b"(effects (font (size 1.27 1.27)))"
    properties = [
        b"\t\t(property "
        + quotes(field[0])
        + b" "
        + quotes(field[1])
        + b"".join(b" (id %d)" % n for n in field[2:])
        + b" (at 25.4 36.83 0) "
        + font
        + b")"
        for field in fields
    ]
    return eol.join(
        [
            b"\t(sheet (at 25.4 25.4) (size 25.4 10.16)",
            b"\t\t(stroke (width 0.1) (type default)) (fill (color 0 0 0 0.0000))",
            b'\t\t(uuid "' + uuid + b'")',
            *properties,
            b'\t\t(instances (project "kicad_native" (path "/' + _ROOT_UUID + b'" (page "2"))))',
            b"\t)",
            b"",
        ]
    )


def _with_sheets(data: bytes, eol: bytes, *sheets: tuple[bytes, str, bytes]) -> bytes:
    """``data`` with a (sheet ...) for each (name, file, uuid), before its sheet_instances."""
    at = data.index(b"\t(sheet_instances")
    return data[:at] + b"".join(_sheet_block(n, f, u, eol) for n, f, u in sheets) + data[at:]


def _label_on_pin(data: bytes, text: bytes) -> bytes:
    """The fixture's label renamed ``text`` and moved onto R1's pin 1, at (100, 96.19)."""
    renamed = data.replace(b'(label "TEST_NET"', b'(label "' + text + b'"')
    return renamed.replace(b"(at 60 100 0)", b"(at 100 96.19 0)")


def _as_sheet_file(data: bytes, ref: bytes, n: int, sheet_uuid: bytes) -> bytes:
    """The fixture as the file of the root's sheet ``sheet_uuid``: a uuid of its own, its
    R1 renamed ``ref`` with a uuid of its own, and its instance path through that sheet."""
    own = b"c41d0000-0000-4000-8000-%012d" % n
    symbol = b"a1b2c3d4-0003-0003-0003-%012d" % (100 + n)
    return (
        data.replace(_ROOT_UUID, own, 1)  # the file's own (uuid ...) comes first
        .replace(b'"R1"', b'"' + ref + b'"')
        .replace(b"a1b2c3d4-0003-0003-0003-000000000001", symbol)
        .replace(
            b'(path "/' + _ROOT_UUID + b'"', b'(path "/' + _ROOT_UUID + b"/" + sheet_uuid + b'"'
        )
    )


_SHEET_A = b"5eed0000-0000-4000-8000-000000000002"


@requires_cli
def test_netlist_nodes_refuses_names_printed_twice_in_a_real_design(tmp_path):
    """The collision kicad-cli itself prints: a root label A{slash}B on R1 and sheet A's
    local label B on R2 are two nets, both printed /A/B (measured on kicad-cli 9.0.8,
    2026-10-10). netlist_nodes must refuse them, and the netlist oracle, which keys by node
    set, must carry the design's sheet paths with them."""
    data, eol = _native()
    root = tmp_path / "kicad_native.kicad_sch"
    root.write_bytes(
        _with_sheets(_label_on_pin(data, b"A{slash}B"), eol, (b"A", "child.kicad_sch", _SHEET_A))
    )
    (tmp_path / "child.kicad_sch").write_bytes(
        _as_sheet_file(_label_on_pin(data, b"B"), b"R2", 1, _SHEET_A)
    )
    xml = _kicad_cli_output(_NETLIST, root)
    if xml is None or len(re.findall(rb'<net code="\d+" name="/A/B"', xml)) != 2:
        raise RuntimeError("premise: kicad-cli does not print two nets named /A/B for this design")
    with pytest.raises(RuntimeError, match="two nets key as 'A/B'"):
        netlist_nodes(root)
    found = nets(root)
    assert found.sheets == ("/", "/A/")
    named = {frozenset(nodes): name for _c, name, _k, nodes in found}
    assert named[frozenset({("R1", "1")})] == named[frozenset({("R2", "1")})] == "/A/B"


def test_is_named_needs_a_real_sheet_path():
    """KiCad names a local net by its sheet's path plus the label and prints it unescaped,
    so a printed name is N as N itself or after one of the design's sheet paths, never
    because it merely ends in "/N" (the old endswith match said yes to /A/VCC on a design
    with no sheet A)."""
    root, with_a = ("/",), ("/", "/A/")
    assert not is_named("/A/VCC", "VCC", root)
    assert is_named("/A/VCC", "VCC", with_a)
    assert is_named("/C1/N", "N", ("/", "/C1/")) and not is_named("/C1/N", "N", root)
    assert is_named("/A/B/N", "N", ("/", "/A/", "/A/B/")) and not is_named("/A/B/N", "N", with_a)
    assert is_named("/A/B", "A/B", root)  # a root label A{slash}B
    assert is_named("/A/B", "A/B", with_a) and is_named("/A/B", "B", with_a)  # the ambiguity
    assert is_named("VCC", "VCC", root) and is_named("VCC", "VCC", with_a)  # a global name
    assert not is_named("/XVCC", "VCC", root) and not is_named("/VCC2", "VCC", root)
    assert not is_named("VCC", "/VCC", root)
    parsed = parse_kicadxml(_kicadxml(("/C1/N", [("R1", "1")]), sheets=("/", "/C1/")))
    assert parsed == [("1", "/C1/N", "Default", frozenset({("R1", "1")}))]
    assert parsed.sheets == ("/", "/C1/")
    # In use the printed names come out of a parsed netlist, so they are equal to N without
    # being the caller's own string object.
    parsed = parse_kicadxml(
        _kicadxml(("VCC", [("R1", "1")]), ("/A/VCC", [("R2", "1")]), sheets=with_a)
    )
    assert [is_named(name, "VCC", parsed.sheets) for _c, name, _k, _n in parsed] == [True, True]


def test_judge_flags_a_merge_with_a_slash_named_net():
    """Joining R1 to a root net printed /A/VCC, asked to put it on VCC, joins a net that is
    not named VCC when the design has no sheet A: a bad merge, which the old judge allowed
    because /A/VCC ends in /VCC. With a sheet A, /A/VCC is that sheet's VCC."""
    r1, r2 = ("R1", "1"), ("R2", "1")
    before = [
        ("1", "unconnected-(R1-Pad1)", "Default", frozenset({r1})),
        ("2", "/A/VCC", "Default", frozenset({r2})),
    ]
    after = [("1", "/A/VCC", "Default", frozenset({r1, r2}))]
    v = judge(before, after, [{r1}], "VCC")
    assert v.bad_merges == [("/A/VCC", ["/A/VCC", "unconnected-(R1-Pad1)"])]
    assert not v.delivered
    sheets = ("/", "/A/")
    v = judge(Netlist(before, sheets), Netlist(after, sheets), [{r1}], "VCC")
    assert not v.wrong and v.delivered, v.problems()


# ---------------------------------------------------------------------------
# every sheet of a hierarchy
# ---------------------------------------------------------------------------


def _hierarchy(where: Path, sheets: list[tuple[bytes, str]]) -> Path:
    """kicad_native.kicad_sch in ``where`` with a sheet per (name, Sheetfile text). Each
    file named that does not exist yet is written as the fixture turned into a sheet file,
    at the path KiCad reads the text as (backslashes as '/'), except a name starting
    "missing". Returns the root."""
    data, eol = _native()
    where.mkdir(parents=True)
    blocks = [
        (name, file, b"5eed0000-0000-4000-8000-%012d" % i)
        for i, (name, file) in enumerate(sheets, 10)
    ]
    root = where / "kicad_native.kicad_sch"
    root.write_bytes(_with_sheets(data, eol, *blocks))
    for i, (_name, file, uuid) in enumerate(blocks):
        target = where / file.replace("\\", "/")
        if file.startswith("missing") or target == root or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_as_sheet_file(data, b"R%d" % (2 + i), i, uuid))
    return root


@requires_cli
@pytest.mark.no_kicad_validation  # the unloadable sheet files are the subject
def test_assert_kicad_parseable_checks_every_sheet(tmp_path):
    """kicad-cli loads a root whose sheets fail and says nothing (EESCHEMA_HELPERS::
    LoadSchematic catches only the root's errors), so the old root-only helper passed a
    hierarchy KiCad's own editor refuses with "The entire schematic could not be loaded"
    (files-io.cpp:284-289 at 9.0.8): a truncated child, one nested a level down, a missing
    file, a sheet that is its own ancestor. A sound hierarchy, a child named with a
    backslash and a subdirectory, passes, and stops passing when that child breaks."""
    data, eol = _native()

    def refused(root: Path, broken: Path | None, why: str) -> None:
        if _kicad_cli_output(_ERC, root) is None:
            raise RuntimeError(f"premise: kicad-cli refuses the root itself ({why})")
        if broken is not None and _kicad_cli_output(_ERC, broken) is not None:
            raise RuntimeError(f"premise: kicad-cli loads {broken.name} standalone ({why})")
        with pytest.raises(pytest.fail.Exception):
            assert_kicad_parseable(root)

    root = _hierarchy(tmp_path / "truncated", [(b"A", "child.kicad_sch")])
    _truncate(root.parent / "child.kicad_sch", _ERC)
    refused(root, root.parent / "child.kicad_sch", "a truncated child")

    root = _hierarchy(tmp_path / "nested", [(b"A", "child.kicad_sch")])
    child = root.parent / "child.kicad_sch"
    grand = root.parent / "deeper" / "grand.kicad_sch"
    sheet_g = b"5eed0000-0000-4000-8000-000000000099"
    child.write_bytes(
        _with_sheets(child.read_bytes(), eol, (b"G", "deeper/grand.kicad_sch", sheet_g))
    )
    grand.parent.mkdir()
    grand.write_bytes(_as_sheet_file(data, b"R9", 9, sheet_g))
    _truncate(grand, _ERC)
    if _kicad_cli_output(_ERC, child) is None:
        raise RuntimeError("premise: kicad-cli refuses the middle sheet itself")
    refused(root, grand, "a truncated grandchild")

    root = _hierarchy(tmp_path / "missing", [(b"A", "missing.kicad_sch")])
    refused(root, None, "a missing child")

    root = _hierarchy(tmp_path / "cycle", [(b"A", "kicad_native.kicad_sch")])
    refused(root, None, "a sheet naming its own file")

    root = _hierarchy(tmp_path / "sound", [(b"A", "sub\\child.kicad_sch")])
    assert_kicad_parseable(root)
    _truncate(root.parent / "sub" / "child.kicad_sch", _ERC)
    refused(root, root.parent / "sub" / "child.kicad_sch", "a truncated child in a subdirectory")


#: A root sheet's properties, (name, value) or (name, value, N) for an (id N), with the
#: format version the root is stamped at (None: the fixture's own): KiCad's own layout,
#: and layouts on which KiCad 9's rule and KiCad 10's part (conftest._k9_sheet_file,
#: _k10_sheet_file). A sheet with a single property is left out: KiCad 9 reads past the
#: end of its field list there.
_SHEET_FIELD_LAYOUTS: dict[str, tuple[int | None, list[tuple]]] = {
    "as_kicad_writes_it": (None, [("Sheetname", "A"), ("Sheetfile", "child.kicad_sch")]),
    "file_name_in_capitals": (None, [("Sheetname", "A"), ("SHEETFILE", "child.kicad_sch")]),
    "legacy_name_any_case": (None, [("Sheetname", "A"), ("sheet FILE", "child.kicad_sch")]),
    "any_name_second": (None, [("Sheetname", "A"), ("Foo", "child.kicad_sch")]),
    "user_field_first": (
        None,
        [("Sheetname", "A"), ("Foo", "bar"), ("SHEETFILE", "child.kicad_sch")],
    ),
    "no_sheet_name": (None, [("Sheetfile", "child.kicad_sch"), ("Foo", "bar")]),
    "sheet_name_twice": (None, [("Sheetname", "A"), ("Sheetname", "child.kicad_sch")]),
    "id_after_user_field": (
        None,
        [("Sheetname", "A"), ("Foo", "bar"), ("Sheetfile", "child.kicad_sch", 9)],
    ),
    "empty_file_name": (None, [("Sheetname", "A"), ("Sheetfile", "")]),
    "by_position": (20200310, [("Foo", "A"), ("Bar", "child.kicad_sch")]),
    "by_position_not_name": (20200310, [("Sheetfile", "child.kicad_sch"), ("Sheetname", "A")]),
}


#: Layouts KiCad 10 refuses as a whole: its parser requires a sheet name and a sheet file
#: property ("Missing sheet name property", sch_io_kicad_sexpr_parser.cpp:4002-4011 at
#: 10.0.7); KiCad 9.0.8 has no such check and loads them.
_K10_REFUSES = {"no_sheet_name"}


@requires_cli
@pytest.mark.no_kicad_validation  # the hierarchies whose sheet loads no child are the subject
@pytest.mark.parametrize("layout", sorted(_SHEET_FIELD_LAYOUTS))
def test_sheet_files_names_the_file_kicad_cli_loads(tmp_path, layout):
    """_sheet_files names the file the local kicad-cli loads for a sheet, by that kicad-cli's
    major, where KiCad 9 and KiCad 10 choose differently: judged by kicad-cli's netlist of
    the root, which lists child.kicad_sch's R2 only when it loaded that file as the sheet.
    The old rule, KiCad 10's on every major, named the child where KiCad 9.0.8 loads "bar"
    and named nothing where it loads the child. assert_kicad_parseable passes exactly when
    the child loads: a sheet whose file is missing, or that names none, fails it."""
    version, fields = _SHEET_FIELD_LAYOUTS[layout]
    data, eol = _native()
    at = data.index(b"\t(sheet_instances")
    body = data[:at] + _sheet_with_fields(fields, _SHEET_A, eol) + data[at:]
    if version is not None:
        body = body.replace(b"(version 20250114)", b"(version %d)" % version, 1)
    root = tmp_path / "kicad_native.kicad_sch"
    root.write_bytes(body)
    child = tmp_path / "child.kicad_sch"
    child.write_bytes(_as_sheet_file(data, b"R2", 1, _SHEET_A))
    if _kicad_cli_output(_ERC, child) is None:
        raise RuntimeError("premise: kicad-cli refuses child.kicad_sch on its own")
    netlist = _kicad_cli_output(_NETLIST, root)
    if _kicad_cli_major() >= 10 and layout in _K10_REFUSES:
        assert netlist is None, f"KiCad 10 refuses a {layout} root, this kicad-cli loads it"
        with pytest.raises(pytest.fail.Exception):
            assert_kicad_parseable(root)
        return
    if netlist is None:
        raise RuntimeError(f"premise: kicad-cli refuses the {layout} root itself")
    loads_child = b'<comp ref="R2"' in netlist
    assert (_sheet_files(body, str(tmp_path)) == [str(child)]) is loads_child, layout
    if loads_child:
        assert_kicad_parseable(root)
    else:
        with pytest.raises(pytest.fail.Exception, match="cannot load"):
            assert_kicad_parseable(root)


# ---------------------------------------------------------------------------
# the oracle's wider scope
# ---------------------------------------------------------------------------


@requires_cli
@pytest.mark.no_kicad_validation  # the schematics kicad-cli refuses are the subject
def test_oracle_checks_a_schematic_whatever_its_first_bytes(tmp_path):
    """kicad-cli refuses the fixture with a UTF-8 byte order mark in front, with one stray
    byte in front, and emptied (rc 3 from kicad-cli 9.0.8, measured 2026-10-11; the
    premise is checked here), so the oracle must fail each. It used to check only a file
    starting "(kicad_sch", and passed all three without loading them. The fixture itself
    passes."""
    good = _FIXTURE.read_bytes()
    variants = {
        "good": good,
        "byte_order_mark": b"\xef\xbb\xbf" + good,
        "stray_byte": b"x" + good,
        "empty": b"",
    }
    for name, body in variants.items():
        folder = tmp_path / name
        folder.mkdir()
        path = folder / "kicad_native.kicad_sch"
        path.write_bytes(body)
        if (_kicad_cli_output(_ERC, path) is None) is (name == "good"):
            raise RuntimeError(f"premise: kicad-cli misjudges the {name} schematic")
        if name == "good":
            _validate_kicad_outputs(folder)
            continue
        with pytest.raises(pytest.fail.Exception, match="kicad-cli cannot parse kicad_native"):
            _validate_kicad_outputs(folder)


def _symbol_library(path: Path, version: str = KICAD_SYM_VERSION) -> Path:
    """A one-symbol library built by kiutils (TestPart, passive pins), which kicad-cli 9.0.8
    loads (measured 2026-10-10)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lib = SymbolLib(version=version, generator="kicad_symbol_editor")
    lib.symbols.append(build_test_part_symbol())
    lib.filePath = str(path)
    lib.to_file()
    return path


@requires_cli
@pytest.mark.no_kicad_validation  # the unloadable library is the subject
def test_oracle_refuses_an_unloadable_symbol_library(tmp_path):
    """kicad-cli sym upgrade loads a library and answers "Unable to load library" when it
    cannot; a pin of electrical type banana is such a library (measured on kicad-cli 9.0.8,
    2026-10-10). The old oracle never looked at a .kicad_sym."""
    good = _symbol_library(tmp_path / "good" / "lib.kicad_sym")
    _validate_kicad_outputs(good.parent)
    bad = tmp_path / "bad" / "lib.kicad_sym"
    bad.parent.mkdir()
    bad.write_bytes(good.read_bytes().replace(b"passive", b"banana", 1))
    if bad.read_bytes() == good.read_bytes():
        raise RuntimeError("premise: the library has no passive pin to break")
    with pytest.raises(pytest.fail.Exception, match=r"kicad-cli cannot load lib\.kicad_sym"):
        _validate_kicad_outputs(bad.parent)


def _stock_footprint() -> Path:
    root = _kicad_root()
    for sub in ("share/kicad/footprints", "SharedSupport/footprints"):
        found = root / sub / "Resistor_SMD.pretty" / "R_0805_2012Metric.kicad_mod" if root else None
        if found is not None and found.is_file():
            return found
    pytest.skip("no stock footprint libraries under the KiCad install (_kicad_root())")


@requires_cli
@pytest.mark.no_kicad_validation  # the unloadable footprints are the subject
def test_oracle_refuses_an_unloadable_footprint(tmp_path):
    """kicad-cli fp upgrade loads a footprint library and answers "Unable to load library"
    when a footprint in it fails: a pad of type banana, or a missing final paren. A copy of
    a footprint KiCad ships loads. The old oracle never looked at a .kicad_mod."""
    stock = _stock_footprint().read_bytes()
    variants = {
        "good": stock,
        "pad_type_banana": stock.replace(b" smd ", b" banana ", 1),
        "unbalanced": stock.rstrip()[:-1],
    }
    for name, body in variants.items():
        if name != "good" and body == stock:
            raise RuntimeError(f"premise: the stock footprint has nothing to break for {name}")
        library = tmp_path / f"{name}.pretty"
        library.mkdir()
        (library / "X.kicad_mod").write_bytes(body)
        if name == "good":
            _validate_kicad_outputs(library)
            continue
        with pytest.raises(pytest.fail.Exception, match=r"kicad-cli cannot load X\.kicad_mod"):
            _validate_kicad_outputs(library)


#: demos/complex_hierarchy/sym-lib-table as KiCad 9.0.8 ships it, byte for byte.
_DEMO_TABLE = (
    b"(sym_lib_table\r\n  (version 7)\r\n"
    b'  (lib (name "complex_hierarchy")(type "KiCad")'
    b'(uri "${KIPRJMOD}/complex_hierarchy.kicad_sym")(options "")(descr ""))\r\n)\r\n'
)


@pytest.mark.no_kicad_validation  # the tables the two readers disagree on are the subject
def test_oracle_refuses_a_lib_table_the_two_readers_disagree_on(tmp_path):
    """KiCad 9 decodes escapes in a table value and KiCad 10 decodes none
    (library_table_grammar.h:60 at 10.0.7), so only a value with no backslash and no
    double quote reads alike in both; a table KiCad 9's lexer refuses, or the CST does,
    fails too, and so does one that does not open with '(' and its keyword at its first
    byte or holds more than space after its ')', the frame of KiCad 10's LIB_TABLE_FILE
    (library_table_grammar.h:119-127), an empty file included. KiCad's own demo table
    passes. The oracle once never looked at a table, then looked only at one that
    started with its keyword."""
    assert _lib_table_problem(_DEMO_TABLE, "sym-lib-table") is None
    assert _lib_table_problem(_DEMO_TABLE.replace(b"\r\n", b"\n"), "sym-lib-table") is None
    name = b'(name "complex_hierarchy")'
    uri = b"${KIPRJMOD}/"
    cases = {
        "escaped quote": (_DEMO_TABLE.replace(name, b'(name "my\\"lib")'), "double quote"),
        "escaped backslash": (_DEMO_TABLE.replace(uri, b"${KIPRJMOD}\\\\"), "backslash"),
        "raw backslash": (_DEMO_TABLE.replace(uri, b"C:\\Users\\nick\\"), "backslash"),
        "glued quote": (_DEMO_TABLE.replace(name, b'(name ab"cd")'), "double quote"),
        # A value whose escape is its first byte: KiCad 9 reads a TAB, KiCad 10 "\t".
        "escape first": (_DEMO_TABLE.replace(b'(descr "")', b'(descr "\\tx")'), "backslash"),
        # Quotes that end bare values: both KiCads and the CST read ab" and x" (a bare token
        # runs to a separator, library_table_grammar.h:58 at 10.0.7), so the parens balance
        # and the double quote is what the rule refuses.
        "quotes ending bare values": (
            _DEMO_TABLE.replace(name + b'(type "KiCad")', b'(name ab")(type x")'),
            "double quote",
        ),
        "lone bare quote": (_DEMO_TABLE.replace(name, b'(name my"lib)'), "double quote"),
        "bug 4's quote": (_DEMO_TABLE.replace(name, b'(name "my"lib")'), "double quote"),
        "bare bar": (_DEMO_TABLE.replace(name, b"(name a|b)"), "bare '|'"),
        "unbalanced": (_DEMO_TABLE[:-3], "lexer refuses"),
        "unterminated": (_DEMO_TABLE.replace(b'(descr "")', b'(descr ")'), "lexer refuses"),
        # The frame: KiCad 9 skips what precedes the '(' and never reads past the ')',
        # where KiCad 10 wants the '(' first and only space after the ')'.
        "empty": (b"", "does not open"),
        "byte order mark": (b"\xef\xbb\xbf" + _DEMO_TABLE, "does not open"),
        "stray byte": (b"x" + _DEMO_TABLE, "does not open"),
        "leading line break": (b"\r\n" + _DEMO_TABLE, "does not open"),
        "quoted keyword": (
            _DEMO_TABLE.replace(b"(sym_lib_table", b'("sym_lib_table"'),
            "does not open",
        ),
        "the other table's keyword": (
            _DEMO_TABLE.replace(b"sym_lib_table", b"fp_lib_table"),
            "does not open",
        ),
        "bytes after the table": (_DEMO_TABLE + b"x\r\n", "follow the table"),
        "a comment after the table": (_DEMO_TABLE + b"# c\r\n", "follow the table"),
    }
    for case, (table, why) in cases.items():
        if table == _DEMO_TABLE:
            raise RuntimeError(f"premise: the {case} edit changed nothing")
        problem = _lib_table_problem(table, "sym-lib-table")
        assert problem is not None and why in problem, (case, problem)
    # Through the oracle itself, for either table name, with or without kicad-cli.
    for file_name in ("sym-lib-table", "fp-lib-table"):
        project_dir = tmp_path / file_name
        project_dir.mkdir()
        good = _DEMO_TABLE.replace(b"sym_lib_table", file_name.replace("-", "_").encode())
        (project_dir / file_name).write_bytes(good)
        _validate_kicad_outputs(project_dir)
        (project_dir / file_name).write_bytes(good.replace(name, b'(name "my\\"lib")'))
        with pytest.raises(pytest.fail.Exception, match="double quote"):
            _validate_kicad_outputs(project_dir)
        (project_dir / file_name).write_bytes(b"")
        with pytest.raises(pytest.fail.Exception, match="does not open"):
            _validate_kicad_outputs(project_dir)


def _shipped_tables() -> list[Path]:
    root = _kicad_root()
    shares = [root / "share" / "kicad", root / "SharedSupport"] if root else []
    return sorted(
        p
        for share in shares
        if share.is_dir()
        for p in share.rglob("*-lib-table")
        if p.name in ("sym-lib-table", "fp-lib-table") and p.is_file()
    )


def test_every_shipped_lib_table_obeys_the_rule():
    """Every library table KiCad ships reads alike in KiCad 9 and 10 by the oracle's rule,
    so the rule refuses nothing KiCad itself writes (49 tables on 9.0.8)."""
    tables = _shipped_tables()
    if not tables:
        pytest.skip("no library tables under the KiCad install (_kicad_root())")
    broken = {str(p): _lib_table_problem(p.read_bytes(), p.name) for p in tables}
    assert not {p: why for p, why in broken.items() if why}, broken


@requires_cli
def test_oracle_skips_a_newer_library_on_an_older_kicad_cli(tmp_path):
    """A KiCad 9 kicad-cli refuses a symbol library stamped with KiCad 10's 20251024
    (measured on kicad-cli 9.0.8, 2026-10-10), which is no defect of the library, so the
    oracle leaves it, and any library past KiCad 9's format, to a kicad-cli 10."""
    major = _kicad_cli_major()
    if major >= 10:
        pytest.skip(f"kicad-cli {major} loads KiCad 10 libraries; the gate is for kicad-cli 9")
    if major != 9:
        raise RuntimeError(f"premise: cannot read the kicad-cli major version (got {major})")
    library = _symbol_library(tmp_path / "k10" / "lib.kicad_sym", version="20251024")
    if _kicad_cli_library_problem(library) is None:
        raise RuntimeError("premise: kicad-cli 9 loads a library stamped 20251024")
    _validate_kicad_outputs(library.parent)
    assert not _cli_can_load_library(library.read_bytes(), _K9_SYMBOL_LIB_VERSION_MAX)
    stamped = library.read_bytes().replace(b"(version 20251024)", b"(version 20241209)", 1)
    assert _cli_can_load_library(stamped, _K9_SYMBOL_LIB_VERSION_MAX)
    footprint = b'(footprint "X"\n\t(version %d)\n\t(layer "F.Cu")\n)\n'
    assert not _cli_can_load_library(footprint % 20260206, _K9_BOARD_VERSION_MAX)
    assert _cli_can_load_library(footprint % 20241229, _K9_BOARD_VERSION_MAX)
    assert _cli_can_load_library(b"(module X (layer F.Cu))\n", _K9_BOARD_VERSION_MAX)


# ---------------------------------------------------------------------------
# what replaced _shared._extract_raw_symbol: CST node extents
# ---------------------------------------------------------------------------

#: A symbol library, CRLF throughout, holding every shape the deleted paren-counting
#: extractor got wrong: parens inside quoted text, either way round; a name followed
#: directly by '(' or by a TAB; a quoted mention of another symbol; a name holding a quote
#: and a backslash.
_EXTENTS_LIB = b"\r\n".join(
    [
        b"(kicad_symbol_lib",
        b"\t(version 20241209)",
        b'\t(generator "kicad_symbol_editor")',
        b'\t(generator_version "9.0")',
        b'\t(symbol "R_Small"',
        b'\t\t(property "Description" "Resistor (SMD" (at 0 0 0)',
        b"\t\t\t(effects (font (size 1.27 1.27)) (hide yes))",
        b"\t\t)",
        b"\t)",
        b'\t(symbol "C_Small"',
        b'\t\t(property "Description" "Cap)acitor" (at 0 0 0)',
        b"\t\t\t(effects (font (size 1.27 1.27)) (hide yes))",
        b"\t\t)",
        b"\t)",
        b'\t(symbol "D"(property "Description" "a paren after the name" (at 0 0 0)))',
        b'\t(symbol "E"\t(property "Description" "a TAB after the name" (at 0 0 0)))',
        b'\t(symbol "F"',
        b'\t\t(property "Description" "drawn after (symbol \\"G\\" by hand" (at 0 0 0))',
        b"\t)",
        b'\t(symbol "G"',
        b'\t\t(property "Description" "plain" (at 0 0 0))',
        b"\t)",
        b'\t(symbol "my\\"part\\\\x"',
        b'\t\t(property "Description" "a quote and a backslash in the name" (at 0 0 0))',
        b"\t)",
        b")",
        b"",
    ]
)


def test_symbol_extents_ignore_parens_in_quoted_text():
    """Each top-level symbol the CST finds spans exactly the bytes KiCad's lexer gives that
    list, CRs included, and carries the name the lexer reads. The deleted extractor counted
    parens inside quoted text, read in text mode and dropped every CR, and searched for
    the name with an f-string; it got every symbol here wrong (scratch measurement,
    2026-10-10)."""
    top = L.tree(_EXTENTS_LIB)
    assert len(top) == 1 and top[0].kind == L.GROUP
    want = [
        (item.items[1].value, _EXTENTS_LIB[item.start : item.end])
        for item in top[0].items
        if item.kind == L.GROUP and item.items[0].value == b"symbol"
    ]
    doc = _cst.parse(_EXTENTS_LIB)
    got = [
        (
            node.atoms[1].text.encode("utf-8", "surrogateescape"),
            _cst.serialize(node)[len(node.sep) :],
        )
        for node in doc.lists[0].find_all("symbol")
    ]
    assert [name for name, _ in want] == [
        b"R_Small",
        b"C_Small",
        b"D",
        b"E",
        b"F",
        b"G",
        b'my"part\\x',
    ]
    assert got == want
    assert all(body.count(b"\r\n") == body.count(b"\n") for _, body in got)


# ---------------------------------------------------------------------------
# bug 4: what create_sym_lib_table writes must read alike in KiCad 9 and 10
# ---------------------------------------------------------------------------

_HOSTILE = ['"', "\\", "\n", "\r", "\t", "\x00", "(", ")", "|", "#", ":", " ", "{", "$"]
_name_text = st.text(
    st.one_of(
        st.sampled_from("abcXYZ019_-.+"),
        st.sampled_from(_HOSTILE),
        st.characters(min_codepoint=0x80, exclude_categories=("Cs",)),
    ),
    max_size=10,
)
_segment = st.text(st.sampled_from("abcnrtxv0129_-. "), min_size=1, max_size=6)
_uri_text = st.one_of(
    st.lists(_segment, min_size=1, max_size=3).map(lambda s: "${KIPRJMOD}/" + "/".join(s)),
    st.lists(_segment, min_size=1, max_size=3).map(lambda s: "C:\\" + "\\".join(s)),
    _name_text,
).map(lambda u: u + ".kicad_sym")
_entries = st.lists(st.fixed_dictionaries({"name": _name_text, "uri": _uri_text}), max_size=3)


def _row_values(row) -> dict[bytes, list]:
    """A (lib ...) row's members as {key: value items}, from the lexer port's reading."""
    return {
        member.items[0].value: member.items[1:]
        for member in row.items[1:]
        if member.kind == L.GROUP and member.items and member.items[0].kind in L.ATOM_KINDS
    }


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="bug 4: create_sym_lib_table writes names and URIs raw into quoted atoms;"
    " fixed by step 11",
)
@settings(max_examples=G.budget())
@given(_entries)
@example([{"name": 'my"lib', "uri": "${KIPRJMOD}/my.kicad_sym"}])
@example([{"name": "lib", "uri": "C:\\Users\\nick\\x.kicad_sym"}])
@example([{"name": "two\nlines", "uri": "${KIPRJMOD}/x.kicad_sym"}])
def test_create_sym_lib_table_output_reads_alike_in_kicad_9_and_10(entries):
    """create_sym_lib_table either refuses, leaving an existing table byte-identical, or
    writes a table KiCad 9 and KiCad 10 read alike (conftest._lib_table_problem: KiCad 9's
    lexer and the CST accept it and no value holds a backslash or a double quote, the
    KiCad 10 raw-reader rule of library_table_grammar.h:60 at 10.0.7) whose rows decode, in
    both majors' FromUTF8, to exactly the entries given, a URI's backslashes as '/' the way
    KiCad's own writer stores them (lib_table_base.cpp:129-130 at 9.0.8), and whose result
    message counts the rows written."""
    with tempfile.TemporaryDirectory() as work:
        table = Path(work) / "sym-lib-table"
        table.write_bytes(_DEMO_TABLE)
        try:
            message = project.create_sym_lib_table(directory=work, entries=entries)
        except ToolError:
            assert table.read_bytes() == _DEMO_TABLE, "a refused call changed the existing table"
            return
        written = table.read_bytes()
    problem = _lib_table_problem(written, "sym-lib-table")
    assert problem is None, f"{problem}: {written!r}"
    (top,) = L.tree(written)
    rows = [i for i in top.items if i.kind == L.GROUP and i.items and i.items[0].value == b"lib"]
    assert len(rows) == len(entries), written
    for row, entry in zip(rows, entries):
        values = _row_values(row)
        for key, text in ((b"name", entry["name"]), (b"uri", entry["uri"].replace("\\", "/"))):
            held = values.get(key, [])
            assert len(held) == 1 and held[0].kind in L.ATOM_KINDS, (key, held, written)
            for major in (9, 10):
                assert L.wx_from_utf8(held[0].value, major=major) == text, (key, major, written)
    counted = re.search(r"(\d+) entr", message)
    assert counted is not None and int(counted.group(1)) == len(rows), message
