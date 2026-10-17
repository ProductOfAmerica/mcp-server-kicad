"""Rung 3, library tables: create_sym_lib_table writes KiCad's table and refuses what KiCad
refuses, and the fp-lib-table reader serves a row by the URI as either KiCad reads it.

Judges, none of them the code under test:

- tests/kicad_ref/lib_table.py, the port of LIB_TABLE_ROW::Format, SYMBOL_LIB_TABLE::Format
  and the nickname rule (checked against pcbnew and the tables KiCad ships in
  test_ladder_r3_judges.py);
- kicad-cli's ERC on a project that uses the table, whose lib_symbol_issues say whether the
  symbol library was found. The project is the native schematic fixture with its symbol's
  library renamed to a nickname no global table carries (KiCad falls back to the global
  table for a nickname it also holds, which hides a project table it could not read), and a
  library made by this package's own tools;
- for the reader, the two ways a table value is read: KiCad 9's lexer (tests/kicad_ref/
  dsn_lexer.py, then FromUTF8), and KiCad 10's grammar, whose QUOTED_TEXT keeps the bytes
  between the quotes as they stand (include/libraries/library_table_grammar.h:60 and
  common/libraries/library_table_parser.cpp:96-104 at 10.0.7).

Rung 1 holds the cross-version reading rule (test_create_sym_lib_table_output_reads_alike_
in_kicad_9_and_10); this file adds KiCad's verdict, the row's shape and the refusals.
"""

from __future__ import annotations

import collections
import json
import tempfile
from pathlib import Path

import edit_gen as E
import pytest
import sexpr_gen
from conftest import _kicad_cli_major, requires_cli
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import lib_table as T
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _cst, pcb, project, symbol
from mcp_server_kicad._shared import _run_cli

_FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"
_NICK = "StepElevenLib"

#: What an existing table holds, so that a refusal has something to leave alone.
_EXISTING = T.format_table([("existing", "${KIPRJMOD}/existing.kicad_sym")])


def _create(directory: Path | str, entries: list[dict]) -> str:
    return project.create_sym_lib_table(directory=str(directory), entries=entries)  # type: ignore[arg-type]


def _refusal(directory: Path | str, entries: list[dict]) -> ToolError:
    """The ToolError create_sym_lib_table raises, or an AssertionError when it writes."""
    try:
        _create(directory, entries)
    except ToolError as exc:
        return exc
    raise AssertionError(f"create_sym_lib_table accepted {entries!r}")


# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------

#: Text with no character the nickname rule or the encoding refuses: letters, digits,
#: spaces, punctuation and non-ASCII.
_LEGAL = st.text(
    st.one_of(
        st.sampled_from("abcXYZ019_-.+ (){}$#|"),
        st.characters(min_codepoint=0x80, exclude_categories=("Cs",)),
    ),
    min_size=1,
    max_size=8,
)
_ILLEGAL_CHARS = [":", "\\", "\x00", "\x01", "\t", "\n", "\r", "\x1f"]
_ILLEGAL = st.builds(
    lambda head, bad, tail: head + bad + tail,
    st.text(st.sampled_from("abc _-"), max_size=4),
    st.sampled_from(_ILLEGAL_CHARS),
    st.text(st.sampled_from("abc _-"), max_size=4),
)
_URI_BODY = st.text(
    st.one_of(
        st.sampled_from("abcxyz019_-. $(){}|#\t"),
        st.characters(min_codepoint=0x80, exclude_categories=("Cs",)),
    ),
    min_size=1,
    max_size=10,
)
_URI = _URI_BODY.map(lambda body: "${KIPRJMOD}/" + body + ".kicad_sym")


def _unique(names: list[str]) -> list[str]:
    return list(dict.fromkeys(names))


# ---------------------------------------------------------------------------
# the row and the table, as KiCad writes them
# ---------------------------------------------------------------------------


@settings(max_examples=sexpr_gen.budget())
@given(st.lists(st.tuples(_LEGAL, _URI), max_size=4, unique_by=lambda row: row[0]))
@example([("lib", "${KIPRJMOD}/lib.kicad_sym")])
@example([("Ω lib", "${KIPRJMOD}/a b/Ω.kicad_sym"), ("x", "C:/Users/me/x.kicad_sym")])
@example([])
def test_lib_table_row_is_kicads_row(rows):
    """For nicknames KiCad accepts and URIs with no backslash, quote or line end, the file
    is the port's SYMBOL_LIB_TABLE::Format of the rows: the same bytes, with LF line ends."""
    assert all(T.legal_nickname(name) for name, _ in rows)
    with tempfile.TemporaryDirectory() as work:
        _create(work, [{"name": name, "uri": uri} for name, uri in rows])
        written = (Path(work) / "sym-lib-table").read_bytes()
    assert written == T.format_table(rows)


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


@settings(max_examples=sexpr_gen.budget())
@given(
    before=st.lists(_LEGAL, max_size=2, unique=True),
    bad=st.one_of(_ILLEGAL, st.just("")),
    after=st.lists(_LEGAL, max_size=2, unique=True),
)
@example(before=["a", "b"], bad="my:lib", after=["c"])
@example(before=[], bad="", after=[])
@example(before=[], bad="tab\there", after=["z"])
def test_lib_table_refuses_nicknames_kicad_refuses(before, bad, after):
    """A nickname the table dialog refuses (empty, or holding a character below U+0020, a
    backslash or a colon) is refused whatever its place in the batch: a ToolError that
    names its entry, and the table that was there is byte-identical. The port's
    legal_nickname is KiCad's rule (LIB_ID::FindIllegalLibraryNameChar)."""
    assert not T.legal_nickname(bad)
    names = _unique(before) + [bad] + [n for n in _unique(after) if n not in before]
    entries = [{"name": n, "uri": f"${{KIPRJMOD}}/{i}.kicad_sym"} for i, n in enumerate(names)]
    with tempfile.TemporaryDirectory() as work:
        table = Path(work) / "sym-lib-table"
        table.write_bytes(_EXISTING)
        refusal = _refusal(work, entries)
        assert table.read_bytes() == _EXISTING, "a refused call changed the existing table"
    index = names.index(bad)
    assert f"entries[{index}]" in str(refusal), str(refusal)
    assert "Nothing was written" in str(refusal), str(refusal)


@settings(max_examples=sexpr_gen.budget())
@given(
    names=st.lists(_LEGAL, min_size=1, max_size=3, unique=True),
    where=st.integers(min_value=0, max_value=2),
    gap=st.integers(min_value=0, max_value=2),
)
@example(names=["a"], where=0, gap=0)
def test_lib_table_refuses_a_nickname_twice(names, where, gap):
    """Two rows with one nickname are refused whatever stands between them, naming the later
    row, with the existing table byte-identical. KiCad 9's table dialog refuses the pair too
    (panel_sym_lib_table.cpp:516-532 at 9.0.8)."""
    twice = names[where % len(names)]
    others = [n for n in names if n != twice]
    names = [twice] + others[:gap] + [twice]
    entries = [{"name": n, "uri": f"${{KIPRJMOD}}/{i}.kicad_sym"} for i, n in enumerate(names)]
    with tempfile.TemporaryDirectory() as work:
        table = Path(work) / "sym-lib-table"
        table.write_bytes(_EXISTING)
        refusal = _refusal(work, entries)
        assert table.read_bytes() == _EXISTING, "a refused call changed the existing table"
    assert f"entries[{len(names) - 1}]" in str(refusal), str(refusal)


#: Values no spelling carries to KiCad 9, which decodes escapes in a table value, and KiCad
#: 10, which decodes none (library_table_grammar.h:60 at 10.0.7): a double quote, or a line
#: end or NUL that KiCad 9 reads only as an escape or not at all. A URI is also refused empty.
_NO_SPELLING = [
    pytest.param({"name": 'my"lib', "uri": "${KIPRJMOD}/x.kicad_sym"}, id="quote in a name"),
    pytest.param({"name": "lib", "uri": '${KIPRJMOD}/my"x.kicad_sym'}, id="quote in a URI"),
    pytest.param({"name": "lib", "uri": "${KIPRJMOD}/a\nb.kicad_sym"}, id="LF in a URI"),
    pytest.param({"name": "lib", "uri": "${KIPRJMOD}/a\rb.kicad_sym"}, id="CR in a URI"),
    pytest.param({"name": "lib", "uri": "${KIPRJMOD}/a\x00b.kicad_sym"}, id="NUL in a URI"),
    pytest.param({"name": "lib", "uri": ""}, id="empty URI"),
]


@pytest.mark.parametrize("entry", _NO_SPELLING)
def test_lib_table_refuses_values_no_spelling_carries_to_both_readers(tmp_path, entry):
    """Each of these is refused with a ToolError naming the entry (and its field), after
    two good rows, and the existing table is byte-identical."""
    table = tmp_path / "sym-lib-table"
    table.write_bytes(_EXISTING)
    good = [{"name": f"ok{i}", "uri": f"${{KIPRJMOD}}/ok{i}.kicad_sym"} for i in range(2)]
    refusal = _refusal(tmp_path, good + [entry])
    assert table.read_bytes() == _EXISTING, "a refused call changed the existing table"
    assert "entries[2]" in str(refusal), str(refusal)
    assert "Nothing was written" in str(refusal), str(refusal)


def test_a_refused_batch_leaves_the_table_and_the_directory_alone(tmp_path):
    """A batch whose third entry is refused changes no existing table, and creates no
    directory for a table it did not write."""
    batch = [
        {"name": "a", "uri": "${KIPRJMOD}/a.kicad_sym"},
        {"name": "b", "uri": "${KIPRJMOD}/b.kicad_sym"},
        {"name": "c:d", "uri": "${KIPRJMOD}/c.kicad_sym"},
    ]
    there = tmp_path / "there"
    there.mkdir()
    (there / "sym-lib-table").write_bytes(_EXISTING)
    refusal = _refusal(there, batch)
    assert (there / "sym-lib-table").read_bytes() == _EXISTING
    assert "entries[2]" in str(refusal), str(refusal)
    fresh = tmp_path / "fresh" / "sub"
    _refusal(fresh, batch)
    assert not (tmp_path / "fresh").exists(), "a refused call created the directory"


# ---------------------------------------------------------------------------
# kicad-cli resolves the libraries
# ---------------------------------------------------------------------------


def _lib_project(root: Path, nickname: str, lib_rel: str, table: bytes | None) -> Path:
    """A project in ``root``: the native schematic with its symbol in the library
    ``nickname``, a one-symbol library at ``lib_rel`` made by the package's own tools, and
    ``table`` as the project's sym-lib-table unless None. Returns the schematic."""
    root.mkdir(parents=True, exist_ok=True)
    sch = root / "kicad_native.kicad_sch"
    sch.write_bytes(_FIXTURE.read_bytes().replace(b"Device:R", nickname.encode() + b":R"))
    (root / "kicad_native.kicad_pro").write_text(
        '{"meta": {"filename": "kicad_native.kicad_pro", "version": 1}}\n', encoding="utf-8"
    )
    lib = root / lib_rel
    lib.parent.mkdir(parents=True, exist_ok=True)
    project.create_symbol_library(str(lib))
    symbol.add_symbol(
        "R",
        [
            {"number": "1", "name": "~", "type": "passive"},
            {"number": "2", "name": "~", "type": "passive"},
        ],
        reference_prefix="R",
        symbol_lib_path=str(lib),
    )
    if table is not None:
        (root / "sym-lib-table").write_bytes(table)
    return sch


def _erc(sch: Path) -> tuple[collections.Counter, str]:
    """(violation types, kicad-cli's stderr) of ERC on ``sch``; RuntimeError when kicad-cli
    cannot load it, which is a broken premise and no verdict."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        out = Path(scratch) / "erc.json"
        result = _run_cli(
            ["sch", "erc", "--format", "json", "--severity-all", "--output", str(out), str(sch)],
            check=False,
        )
        if not out.is_file():
            raise RuntimeError(f"premise: kicad-cli cannot load {sch.name}: {result.stderr[-300:]}")
        report = json.loads(out.read_text(encoding="utf-8"))
    types = collections.Counter(v["type"] for s in report["sheets"] for v in s["violations"])
    return types, result.stderr


def _require_missing_library_found_out(tmp_path: Path, nickname: str) -> None:
    """The premise every verdict below rests on: a table row naming a file that does not
    exist is reported by ERC as a lib_symbol_issue on this kicad-cli."""
    row = [{"name": nickname, "uri": "${KIPRJMOD}/nowhere/lib.kicad_sym"}]
    probe = tmp_path / "premise"
    sch = _lib_project(probe, nickname, "libs/lib.kicad_sym", None)
    _create(probe, row)
    types, _ = _erc(sch)
    if types["lib_symbol_issues"] < 1:
        raise RuntimeError(
            "premise: kicad-cli's ERC reports no lib_symbol_issues for a table row naming a"
            f" missing library: {dict(types)}"
        )


@requires_cli
def test_a_nickname_with_a_space_and_non_ascii_resolves_in_kicad_cli(tmp_path):
    """The table the tool writes for a nickname with a space and a non-ASCII letter and a
    ${KIPRJMOD} URI through a directory with a space is one kicad-cli finds the library
    through (guard: it holds before the table is built from KiCad's template, and after)."""
    nickname = "M\u00fcller Lib"
    _require_missing_library_found_out(tmp_path, nickname)
    root = tmp_path / "project"
    sch = _lib_project(root, nickname, "libs dir/lib.kicad_sym", None)
    _create(root, [{"name": nickname, "uri": "${KIPRJMOD}/libs dir/lib.kicad_sym"}])
    types, _ = _erc(sch)
    assert types["lib_symbol_issues"] == 0, dict(types)


@requires_cli
@pytest.mark.parametrize("spelling", ["kiprjmod", "absolute"])
def test_backslash_uri_resolves_in_kicad_cli(tmp_path, spelling):
    """A URI given with backslashes, ${KIPRJMOD}\\nick dir\\lib.kicad_sym or its absolute
    twin: the table written holds no backslash in any value (KiCad's writer turns each one
    into '/', LIB_TABLE_ROW::Format, and only a value with none reads alike in KiCad 9, which
    decodes \\n as a line end, and KiCad 10, which decodes nothing), and kicad-cli finds the
    library through it."""
    _require_missing_library_found_out(tmp_path, _NICK)
    root = tmp_path / "project"
    sch = _lib_project(root, _NICK, "nick dir/lib.kicad_sym", None)
    if spelling == "kiprjmod":
        uri = "${KIPRJMOD}\\nick dir\\lib.kicad_sym"
    else:
        uri = str(root / "nick dir" / "lib.kicad_sym").replace("/", "\\")
    _create(root, [{"name": _NICK, "uri": uri}])
    written = (root / "sym-lib-table").read_bytes()
    values = [
        written[tok.start : tok.end]
        for tok in L.tokens(written)
        if tok.kind in L.ATOM_KINDS and b"\\" in written[tok.start : tok.end]
    ]
    assert not values, f"the table holds a backslash in a value: {values!r}"
    types, _ = _erc(sch)
    assert types["lib_symbol_issues"] == 0, dict(types)


@requires_cli
def test_lib_table_duplicate_is_refused_as_kicad_cli_reports_it(tmp_path):
    """kicad-cli 9 reports "Duplicate library nickname" for a table holding one nickname
    twice, and keeps the first row (premise, measured here on a hand-written table: a RuntimeError
    when this kicad-cli does not); create_sym_lib_table refuses the same batch."""
    if _kicad_cli_major() != 9:
        pytest.skip(
            f"kicad-cli {_kicad_cli_major()} reads a table with its own grammar, whose handling of"
            " a duplicate nickname is not measured; the refusal is judged on every leg by"
            " test_lib_table_refuses_a_nickname_twice"
        )
    entries = [
        {"name": _NICK, "uri": "${KIPRJMOD}/libs/lib.kicad_sym"},
        {"name": _NICK, "uri": "${KIPRJMOD}/nowhere/lib.kicad_sym"},
    ]
    hand = tmp_path / "hand"
    sch = _lib_project(
        hand, _NICK, "libs/lib.kicad_sym", T.format_table([(e["name"], e["uri"]) for e in entries])
    )
    types, stderr = _erc(sch)
    if f"Duplicate library nickname '{_NICK}'" not in stderr:
        raise RuntimeError(f"premise: kicad-cli did not report the duplicate: {stderr[-300:]!r}")
    if types["lib_symbol_issues"] != 0:
        raise RuntimeError(f"premise: kicad-cli did not keep the first row: {dict(types)}")
    refusal = _refusal(tmp_path / "tool", entries)
    assert "entries[1]" in str(refusal), str(refusal)


# ---------------------------------------------------------------------------
# the fp-lib-table reader: a row's URI as either KiCad reads it
# ---------------------------------------------------------------------------


def _hand_written_fp_table(uri: str, name: str = "Lib") -> bytes:
    """A one-row table as a user or a script writes it: the URI's bytes exactly as given,
    where KiCad's own writer turns each backslash into '/'."""
    return (
        b'(fp_lib_table\n  (version 7)\n  (lib (name "%s")(type "KiCad")(uri "%s")'
        b'(options "")(descr ""))\n)\n' % (name.encode(), uri.encode())
    )


def _readings(table: bytes) -> tuple[str, str]:
    """(KiCad 9's, KiCad 10's) reading of the URI of the table's one row. KiCad 9 lexes the
    value and decodes its escapes (the lexer port, then FromUTF8); KiCad 10 keeps the bytes
    between the quotes as they stand."""
    tokens = L.tokens(table)
    for key, value in zip(tokens, tokens[1:]):
        if key.kind == L.SYMBOL and key.value == b"uri" and value.kind == L.STRING:
            as_written = table[value.start + 1 : value.end - 1].decode("utf-8")
            return L.wx_from_utf8(value.value, major=9), as_written
    raise RuntimeError("premise: the table holds no quoted uri")


def _path_of(reading: str, project_dir: Path) -> Path:
    """Where a reading of a URI points once ${KIPRJMOD} is the project directory."""
    return Path(reading.replace("${KIPRJMOD}", str(project_dir)))


def _names_a_directory(reading: str, project_dir: Path) -> bool:
    """Whether a reading of a URI names an existing directory. The empty reading, which is
    what KiCad 9 holds for a value whose bytes are not UTF-8, names none."""
    return bool(reading) and _path_of(reading, project_dir).is_dir()


#: A directory name after a backslash in a hand-written Windows path, by the escape its
#: first byte starts in KiCad 9's lexer: \f, \b, \a, \v, one to three octal digits, \x with no
#: hex digit after it, and \n, \r and \t.
_ESCAPED_DIRECTORIES = [
    pytest.param("footprints", id="form feed"),
    pytest.param("boards", id="backspace"),
    pytest.param("assets", id="bell"),
    pytest.param("vendor", id="vertical tab"),
    pytest.param("2026", id="octal"),
    pytest.param("xyz", id="x without digits"),
    pytest.param("new", id="line feed"),
    pytest.param("run", id="carriage return"),
    pytest.param("tmp", id="tab"),
]


@pytest.mark.kicad_placeholder("proj/fp-lib-table")  # a value the two majors read differently
@pytest.mark.parametrize("directory", _ESCAPED_DIRECTORIES)
def test_fp_lib_table_row_is_served_by_its_uri_as_written(tmp_path, directory):
    """A project row written by hand with backslashes, ${KIPRJMOD}\\<directory>\\Lib.pretty,
    names the directory KiCad 10 finds (it keeps the bytes as written) and not the one KiCad 9
    reads out of the escapes, so the library is served and its nickname does not fall through
    to another library of the same name."""
    proj = tmp_path / "proj"
    proj.mkdir()
    uri = f"${{KIPRJMOD}}\\{directory}\\Lib.pretty"
    table = proj / "fp-lib-table"
    table.write_bytes(_hand_written_fp_table(uri))
    kicad9, kicad10 = _readings(table.read_bytes())
    if kicad10 != uri or kicad9 == uri or _names_a_directory(kicad9, proj):
        raise RuntimeError(f"premise: KiCad 9 reads {kicad9!r} and KiCad 10 {kicad10!r}")
    library = _path_of(kicad10, proj)
    library.mkdir(parents=True)
    assert pcb._read_fp_lib_table(table, proj) == {"Lib": str(library)}


@pytest.mark.kicad_placeholder("proj/fp-lib-table")  # a value the two majors read differently
@pytest.mark.parametrize("also_as_written", [False, True])
@pytest.mark.parametrize(
    "uri",
    [
        pytest.param("${KIPRJMOD}/libs/\\x41.pretty", id="hex"),
        pytest.param("${KIPRJMOD}/libs/\\101.pretty", id="octal"),
        pytest.param("${KIPRJMOD}\\\\libs\\\\A.pretty", id="doubled backslashes"),
    ],
)
def test_fp_lib_table_row_is_served_by_kicad_9s_reading_first(tmp_path, uri, also_as_written):
    """A row whose escapes KiCad 9 decodes into a directory that exists is served by that
    directory, whether or not the bytes as written name another one too."""
    proj = tmp_path / "proj"
    proj.mkdir()
    table = proj / "fp-lib-table"
    table.write_bytes(_hand_written_fp_table(uri))
    kicad9, kicad10 = _readings(table.read_bytes())
    if kicad10 != uri or kicad9 in ("", uri):
        raise RuntimeError(f"premise: KiCad 9 reads {kicad9!r} and KiCad 10 {kicad10!r}")
    library = _path_of(kicad9, proj)
    library.mkdir(parents=True)
    if also_as_written:
        _path_of(kicad10, proj).mkdir(parents=True, exist_ok=True)
    assert pcb._read_fp_lib_table(table, proj) == {"Lib": str(library)}


@pytest.mark.kicad_placeholder("proj/fp-lib-table")  # a value the two majors read differently
def test_place_footprint_takes_the_project_row_written_with_backslashes(tmp_path):
    """The footprint placed comes from the library a project row names by a hand-written
    ${KIPRJMOD}\\footprints\\Resistor_SMD.pretty, not from the library of the same name beside
    the board, which the nickname reaches when the row is dropped from the table. The two hold
    R_X with different pad numbers, so the board says which one was copied."""
    proj = tmp_path / "proj"
    proj.mkdir()
    texts = E.footprint_with_pad_at(0.0).texts

    def write_library(directory: Path, first: str, second: str) -> None:
        pads = (E.SynPad(first, (-E.MM, 0), 0.0), E.SynPad(second, (E.MM, 0), 0.0))
        directory.mkdir(parents=True)
        text = E.library_text(E.SynFootprint("R_X", pads, texts))
        (directory / "R_X.kicad_mod").write_text(text, encoding="utf-8", newline="\n")

    uri = "${KIPRJMOD}\\footprints\\Resistor_SMD.pretty"
    table = proj / "fp-lib-table"
    table.write_bytes(_hand_written_fp_table(uri, "Resistor_SMD"))
    kicad9, kicad10 = _readings(table.read_bytes())
    if kicad10 != uri or _names_a_directory(kicad9, proj):
        raise RuntimeError(f"premise: KiCad 9 reads {kicad9!r} and KiCad 10 {kicad10!r}")
    write_library(_path_of(kicad10, proj), "11", "22")
    write_library(proj / "Resistor_SMD.pretty", "1", "2")
    board = proj / "b.kicad_pcb"
    board.write_bytes(pcb._EMPTY_PCB_TPL)
    try:
        pcb.place_footprint(
            "R1", "10K", 50, 50, library="Resistor_SMD", footprint="R_X", pcb_path=str(board)
        )
    finally:
        pcb._BOARD_CACHE.pop(str(board.resolve()), None)
    (footprint,) = _cst.parse(board.read_bytes()).lists[0].find_all("footprint")
    assert [pad.atoms[1].text for pad in footprint.find_all("pad")] == ["11", "22"]
