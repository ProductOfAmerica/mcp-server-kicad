"""Rung 3, library tables: create_sym_lib_table writes KiCad's table and refuses what KiCad
refuses.

Judges, none of them the code under test:

- tests/kicad_ref/lib_table.py, the port of LIB_TABLE_ROW::Format, SYMBOL_LIB_TABLE::Format
  and the nickname rule (checked against pcbnew and the tables KiCad ships in
  test_ladder_r3_judges.py);
- kicad-cli's ERC on a project that uses the table, whose lib_symbol_issues say whether the
  symbol library was found. The project is the native schematic fixture with its symbol's
  library renamed to a nickname no global table carries (KiCad falls back to the global
  table for a nickname it also holds, which hides a project table it could not read), and a
  library made by this package's own tools.

Rung 1 holds the cross-version reading rule (test_create_sym_lib_table_output_reads_alike_
in_kicad_9_and_10); this file adds KiCad's verdict, the row's shape and the refusals.
"""

from __future__ import annotations

import collections
import json
import tempfile
from pathlib import Path

import pytest
import sexpr_gen
from conftest import _kicad_cli_major, requires_cli
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import lib_table as T
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import project, symbol
from mcp_server_kicad._shared import _run_cli

_FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"
_NICK = "StepElevenLib"

_BUG_28 = (
    "bug 28: create_sym_lib_table accepts a nickname KiCad refuses, or two rows with one;"
    " fixed by step 11"
)
_BUG_4 = (
    "bug 4: create_sym_lib_table writes names and URIs raw into quoted atoms and keeps a"
    " backslash KiCad 9 reads as an escape and KiCad 10 as a backslash; fixed by step 11"
)

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


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_28)
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


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_28)
@settings(max_examples=sexpr_gen.budget())
@given(
    names=st.lists(_LEGAL, min_size=1, max_size=3, unique=True),
    where=st.integers(min_value=0, max_value=2),
    gap=st.integers(min_value=0, max_value=2),
)
@example(names=["a"], where=0, gap=0)
def test_lib_table_refuses_a_nickname_twice(names, where, gap):
    """Two rows with one nickname: KiCad's reader keeps the first and reports the second
    (SYMBOL_LIB_TABLE::Parse), the table dialog refuses the pair. Refused whatever stands
    between them, naming the later row, with the existing table byte-identical."""
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


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_4)
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


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_28)
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
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_4)
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
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_28)
def test_lib_table_duplicate_is_refused_as_kicad_cli_reports_it(tmp_path):
    """kicad-cli reports "Duplicate library nickname" for a table holding one nickname
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
