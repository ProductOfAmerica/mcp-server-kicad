"""Rung 1, corpus: the CST against the s-expression files KiCad ships.

Every file here ships with KiCad, which loads it, so each is a judge in its own right.
Three properties per file:

1. it round-trips: serialize(parse(file)) is the file, byte for byte;
2. the CST refuses it exactly when KiCad's lexer (the port in
   tests/kicad_ref/dsn_lexer.py) finds its parens unbalanced or a string unterminated,
   and then reports the same place;
3. otherwise the CST reads the items KiCad's lexer reads (edit_judge.cst_view against
   edit_judge.lexer_view), with the bar known or not as the file's own parser decides
   from its kind and version (dsn_lexer.knows_bar_of).

The refusals are policy, bug 54: KiCad 9.0.8 ships two RoyalBlue54L files holding
"(curved_edges no)filter_ratio 0.9)", which its board parser loads only because
parseTEARDROP_PARAMETERS treats a member's '(' as optional
(pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:437-502 at 9.0.8, 668-733 at
10.0.7). Their parens do not balance as tokens, and the CST refuses a file whose parens
do not balance rather than repair it, so these files are refused by design and the test
asserts the refusal is the lexer's.

Per push: the demos and templates under the install found through _kicad_root(), up to
20 MB each, one test item per file. Nightly: every s-expression file the install holds,
in items of one directory each (a directory over 25 MB is split).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import edit_judge as J
import kicad_corpus
import pytest
from conftest import _nightly_skip_reason
from kicad_ref import dsn_lexer as L

from mcp_server_kicad import _cst
from mcp_server_kicad._shared import _kicad_root

_SEXPR_SUFFIXES = frozenset(
    {".kicad_sch", ".kicad_pcb", ".kicad_sym", ".kicad_mod", ".kicad_wks", ".kicad_dru"}
)
_TABLE_NAMES = frozenset({"sym-lib-table", "fp-lib-table"})
#: Per push, a file above this is left to the nightly run (measured 2026-10-10 on 9.0.8:
#: only demos/vme-wren/vme-wren.kicad_pcb, 71.6 MB, is above it, and it alone takes
#: about a minute with a 2.7 GB peak in one process).
_PER_PUSH_CAP = 20_000_000
#: Nightly, a directory's files are checked in items of at most this many bytes, so one
#: big directory (symbols/ holds 236 MB on 9.0.8) does not become one long item.
_NIGHTLY_ITEM_BYTES = 25_000_000
_NO_INSTALL = "no KiCad install found through _kicad_root(); ci.yml installs no KiCad"


_share_dirs = kicad_corpus.share_dirs


def _is_sexpr(path: Path) -> bool:
    return path.suffix in _SEXPR_SUFFIXES or path.name in _TABLE_NAMES


def _demos_and_templates() -> list[tuple[str, Path]]:
    """(name under the share directory, path) of every s-expression file in the demos
    and templates, up to _PER_PUSH_CAP bytes each, in a stable order."""
    out = []
    for share in _share_dirs():
        for sub in ("demos", "template"):
            base = share / sub
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file() and _is_sexpr(path) and path.stat().st_size <= _PER_PUSH_CAP:
                    out.append((path.relative_to(share).as_posix(), path))
    return out


_SHIPPED = _demos_and_templates()

#: A line whose first non-blank byte is '#' is a comment KiCad's lexer skips whole
#: (dsnlexer.cpp 575-598 at 9.0.8, the blanks of kicad_ref.dsn_lexer's _SPACE); the CST
#: reads it as tokens, and a paren in it as structure (bug 65). KiCad 9.0.8 ships no such
#: file; KiCad 10's jetson-agx-thor-baseboard demo rules (.kicad_dru) hold several.
_COMMENT_LINE = re.compile(rb"(?m)^[ \t\r\x00]*#")
_BUG_65 = (
    "bug 65: the CST reads a '#' comment line of a shipped file as tokens, where KiCad's"
    " lexer skips the line; fixed by step 11"
)


def _has_comment_line(data: bytes) -> bool:
    return _COMMENT_LINE.search(data) is not None


def _installed_groups() -> list[tuple[str, list[Path]]]:
    """Every s-expression file under the share directories, grouped by directory, a
    directory split into consecutive items of at most _NIGHTLY_ITEM_BYTES."""
    by_dir: dict[str, list[Path]] = {}
    for share in _share_dirs():
        for path in sorted(share.rglob("*")):
            if path.is_file() and _is_sexpr(path):
                by_dir.setdefault(path.parent.relative_to(share).as_posix(), []).append(path)
    out = []
    for key in sorted(by_dir):
        items: list[list[Path]] = [[]]
        size = 0
        for path in by_dir[key]:
            n = path.stat().st_size
            if items[-1] and size + n > _NIGHTLY_ITEM_BYTES:
                items.append([])
                size = 0
            items[-1].append(path)
            size += n
        for i, paths in enumerate(items, 1):
            out.append((key if len(items) == 1 else f"{key} {i} of {len(items)}", paths))
    return out


def _nightly_params() -> list:
    if _nightly_skip_reason(os.environ.get("HYPOTHESIS_PROFILE")) is not None:
        # The nightly marker skips the item; walking the whole install (31,000 entries
        # on 9.0.8 for Windows) is not worth paying at every collection.
        return [pytest.param([], id="nightly-only")]
    groups = _installed_groups()
    if not groups:
        return [pytest.param([], id="no-install", marks=pytest.mark.skip(reason=_NO_INSTALL))]
    return [pytest.param(paths, id=key) for key, paths in groups]


# ---------------------------------------------------------------------------
# the three properties
# ---------------------------------------------------------------------------

_STRAY_CLOSE = re.compile(r"unmatched '\)' at byte (\d+)")
_UNCLOSED = re.compile(r"(\d+) unclosed '\('")


def _reading_difference(tree, data: bytes, knows_bar: bool) -> str | None:
    """None when the CST reads ``data`` as KiCad's lexer does; else where they part,
    kept short (a board holds millions of items)."""
    cst = J.cst_view(tree)
    kicad = J.lexer_view(data, knows_bar=knows_bar)
    if cst == kicad:
        return None
    at = next(
        (i for i, (a, b) in enumerate(zip(cst, kicad)) if a != b),
        min(len(cst), len(kicad)),
    )
    return (
        f"the readings part at event {at} of {len(cst)} (CST) and {len(kicad)} (KiCad):"
        f" CST {cst[at : at + 3]!r}, KiCad's lexer {kicad[at : at + 3]!r}"
    )


def _check_file(data: bytes, *, with_comment_lines: bool = False) -> None:
    """Round trip; refused exactly when, and where, KiCad's lexer refuses; else the
    same reading as KiCad's lexer, with the bar as the file's parser sets it. A file
    with a '#' comment line is bug 65's: only its round trip is checked here, unless
    with_comment_lines (the bug's own strict xfail)."""
    if not with_comment_lines and _has_comment_line(data):
        try:
            tree = _cst.parse(data)
        except SyntaxError:
            return  # a paren inside a comment line: bug 65's refusal, its test's subject
        assert _cst.serialize(tree) == data, "serialize(parse(file)) is not the file"
        return
    knows_bar = L.knows_bar_of(data)
    try:
        L.tree(data, knows_bar=knows_bar)
    except (L.Unbalanced, L.Unterminated) as exc:
        refusal: L.LexError | None = exc
    else:
        refusal = None
    try:
        tree = _cst.parse(data)
    except SyntaxError as exc:
        assert refusal is not None, f"the CST refuses a file KiCad's lexer reads: {exc}"
        if isinstance(refusal, L.Unbalanced):
            message = str(exc)
            if refusal.unclosed:
                found = _UNCLOSED.search(message)
                assert found and int(found.group(1)) == refusal.unclosed, (message, refusal)
            else:
                found = _STRAY_CLOSE.search(message)
                assert found and int(found.group(1)) == refusal.offset, (message, refusal)
        return
    assert refusal is None, f"the CST accepts a file KiCad's lexer refuses: {refusal}"
    assert _cst.serialize(tree) == data, "serialize(parse(file)) is not the file"
    difference = _reading_difference(tree, data, knows_bar)
    assert difference is None, difference


@pytest.mark.parametrize(
    "path",
    [pytest.param(path, id=name) for name, path in _SHIPPED]
    or [pytest.param(None, id="no-install", marks=pytest.mark.skip(reason=_NO_INSTALL))],
)
def test_shipped_file_round_trips_and_reads_as_kicads_lexer(path):
    """One demo or template file KiCad ships, against the three properties above."""
    _check_file(path.read_bytes())


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_65)
def test_shipped_files_with_comment_lines_read_as_kicads_lexer():
    """Every shipped demo or template with a '#' comment line, against all three
    properties: KiCad's lexer skips the line, the CST does not (bug 65)."""
    commented = [path for _name, path in _SHIPPED if _has_comment_line(path.read_bytes())]
    if not commented:
        pytest.skip("no shipped demo or template here holds a '#' comment line")
    for path in commented:
        _check_file(path.read_bytes(), with_comment_lines=True)


def test_shipped_corpus_is_found():
    """Wherever kicad-cli resolves, its install's templates are collected, so the
    corpus test above cannot pass by collecting nothing. Templates rather than demos:
    the macOS cask ships no demo boards (the 2026-08-14 KiCad 10 probe entry in
    docs/adr-cst-substrate.md), and the Linux 9.0 leg takes Ubuntu's own templates
    because the 9.0 PPA ships none (kicad-suite.yml)."""
    root = _kicad_root()
    if root is None:
        pytest.skip("kicad-cli does not resolve, so there is no install to read; ci.yml has none")
    shares = _share_dirs()
    assert shares, f"KiCad resolves to {root}, which has neither share/kicad nor SharedSupport"
    templates = [name for name, _ in _SHIPPED if name.startswith("template/")]
    assert templates, f"no template file collected under {[str(s) for s in shares]}"


@pytest.mark.nightly
@pytest.mark.parametrize("paths", _nightly_params())
def test_nightly_every_installed_s_expression_file(paths):
    """The three properties over every .kicad_sch, .kicad_pcb, .kicad_sym, .kicad_mod,
    .kicad_wks, .kicad_dru, sym-lib-table and fp-lib-table the install holds,
    vme-wren.kicad_pcb included; one item per directory, every failing file listed."""
    failures = []
    for path in paths:
        try:
            _check_file(path.read_bytes())
        except AssertionError as exc:
            first_line = (str(exc).splitlines() or [repr(exc)])[0]
            failures.append(f"{path.name}: {first_line[:300]}")
    assert not failures, f"{len(failures)} of {len(paths)} files:\n" + "\n".join(failures)
