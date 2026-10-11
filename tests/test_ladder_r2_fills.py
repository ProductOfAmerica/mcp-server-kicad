"""Rung 2: zone fills written the way KiCad writes them.

pcb._filled_polygon_bytes builds the (filled_polygon ...) node fill_zones splices into a
board-level zone from the points pcbnew computed. KiCad itself writes a fill's points
through formatPolyPts and then KICAD_FORMAT::Prettify, whose list-of-points rule
decides where lines break: a point follows on the line, after one space, while the line
is shorter than 99 columns (xySpecialCaseColumnLimit, common/io/kicad/kicad_io_utils.cpp:98
and 219-224 at 9.0.8, 106 and 245-250 at 10.0.7). So a line can end past column 99: the
longest line of points in KiCad 9.0.8's demo and template boards is 125 columns
(measured 2026-10-11).

Judges, none of them the code under test: the port of that rule
(kicad_ref.kicad_io_utils.pack_xy, which reproduces every fill in the demo boards KiCad
saved: test_ladder_r2_judges.test_xy_packing_port_reproduces_kicads_fills) with the
number text of FormatInternalUnits (kicad_ref.eda_units); and those fills themselves,
re-emitted from their own points. A board-level zone's fill points sit four TABs deep,
the depth _filled_polygon_bytes writes, and only board-level zones are filled through it
(_splice_fills). The Windows installer ships the boards with CRLF line ends (all 39
demo and template boards of 9.0.8, measured 2026-10-11) where KiCad's writer ends lines
with LF, so a CR before an LF is dropped before comparing: the rule is about where lines
break.

Each check comes in two tests: the text of every point, which _filled_polygon_bytes
writes as KiCad does, and where the lines of points break, which is bug 62. The text
tests pass on their own, so an xfail on the layout cannot hide a regression in the text.
"""

from __future__ import annotations

import geom_gen as G
import kicad_corpus as K
import pytest
import sexpr_gen
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U
from kicad_ref import kicad_io_utils as IO
from kicad_ref import parse_units as P

from mcp_server_kicad import pcb

_BUG_62 = (
    "bug 62: _filled_polygon_bytes keeps each line of (xy) points within 99 columns where"
    " KiCad's Prettify adds a point while the line is shorter than 99; fixed by step 12c"
)


def _head(item) -> bytes | None:
    if item.kind == L.GROUP and item.items and item.items[0].kind in L.ATOM_KINDS:
        return item.items[0].value
    return None


def _child(group, name: bytes):
    found = [c for c in group.items if _head(c) == name]
    if not found:
        raise LookupError(f"no ({name.decode()} ...) in the list at byte {group.start}")
    return found[0]


def _xy_run(data: bytes, fill) -> tuple[list, bytes]:
    """(the (xy groups, the bytes from the first one's line start to the last one's ')')
    of a filled_polygon group the lexer port read from ``data``."""
    points = [g for g in _child(fill, b"pts").items if _head(g) == b"xy"]
    if not points:
        raise LookupError(f"a fill with no points at byte {fill.start}")
    line_start = data.rindex(b"\n", 0, points[0].start) + 1
    return points, data[line_start : points[-1].end]


def _emitted_run(layer: str, points: list[tuple[int, int]]) -> tuple[list[bytes], bytes]:
    """(point tokens, (xy run) of what _filled_polygon_bytes writes, read back by the
    lexer port."""
    data = pcb._filled_polygon_bytes(layer, points)
    fill = L.tree(data)[0]
    if _head(fill) != b"filled_polygon":
        raise LookupError(f"_filled_polygon_bytes wrote {data[:60]!r}")
    xys, run = _xy_run(data, fill)
    return [data[g.start : g.end] for g in xys], run


def _token(x: int, y: int) -> bytes:
    """A point as KiCad writes it: FormatInternalUnits of each coordinate."""
    mm = U.format_internal_units
    return f"(xy {mm(x, U.PCB_IU_PER_MM)} {mm(y, U.PCB_IU_PER_MM)})".encode()


@settings(max_examples=sexpr_gen.budget())
@example(points=[(i * 1_234_567, i * 987_654) for i in range(40)])
@given(points=st.lists(st.tuples(G.nm_coordinates(), G.nm_coordinates()), min_size=1, max_size=80))
def test_filled_polygon_bytes_write_points_as_kicad_does(points):
    """For 1 to 80 points anywhere a board coordinate can be, the fill
    _filled_polygon_bytes writes holds each point, in order, as KiCad's text for it:
    FormatInternalUnits of each coordinate. Where the lines break is the next test's."""
    tokens, _ = _emitted_run("F.Cu", points)
    assert tokens == [_token(x, y) for x, y in points]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_62)
@settings(max_examples=sexpr_gen.budget())
@example(points=[(i * 1_234_567, i * 987_654) for i in range(40)])
@given(points=st.lists(st.tuples(G.nm_coordinates(), G.nm_coordinates()), min_size=1, max_size=80))
def test_filled_polygon_bytes_pack_points_as_kicad_does(points):
    """For the same points, _filled_polygon_bytes lays the run of points out as Prettify
    does at depth 4. The explicit example's points are 21 to 24 bytes after the first:
    KiCad lays its 40 points on 10 lines, the tool on 13. The point text is the test
    above's."""
    _, run = _emitted_run("F.Cu", points)
    assert run == IO.pack_xy([_token(x, y) for x, y in points], 4)


def _fills_of(zone) -> list:
    return [fill for fill in zone.items if _head(fill) == b"filled_polygon"]


def _fill_mismatches(data: bytes, top: list) -> tuple[int, list[str], list[str]]:
    """(fills checked, point text mismatches, run mismatches) for one board, given its
    bytes and the lexer port's tree: every fill of a board-level zone, re-emitted by
    _filled_polygon_bytes from its own points (read through parseBoardUnits) and layer,
    must hold KiCad's (xy) lists in KiCad's order, each byte for byte, and must be
    KiCad's run of points, where its lines break included. Fills of zones a footprint
    owns are counted, not checked: the tool never writes them. A board-level fill whose
    points are not four TABs deep, or a fill the lexer port did not find, is a premise
    that does not hold (RuntimeError)."""
    root = top[0]
    checked = 0
    text: list[str] = []
    runs: list[str] = []
    owned = sum(
        len(_fills_of(zone))
        for fp in root.items
        if _head(fp) == b"footprint"
        for zone in fp.items
        if _head(zone) == b"zone"
    )
    for zone in root.items:
        if _head(zone) != b"zone":
            continue
        for fill in _fills_of(zone):
            xys, kicad = _xy_run(data, fill)
            kicad = kicad.replace(b"\r\n", b"\n")
            if not kicad.startswith(b"\t\t\t\t(xy"):
                raise RuntimeError(f"a board-level fill at byte {fill.start} is not 4 TABs deep")
            layer = _child(fill, b"layer").items[1].value.decode()
            points = [
                (
                    P.parse_board_units(g.items[1].value, major=10),
                    P.parse_board_units(g.items[2].value, major=10),
                )
                for g in xys
            ]
            checked += 1
            tokens, run = _emitted_run(layer, points)
            where = f"fill at byte {fill.start} ({len(points)} points)"
            if tokens != [data[g.start : g.end] for g in xys]:
                text.append(where)
            if run != kicad:
                runs.append(where)
    if checked + owned != data.count(b"(filled_polygon"):
        raise RuntimeError(
            f"{data.count(b'(filled_polygon')} fills in the file, the lexer port found"
            f" {checked} in board-level zones and {owned} in footprints' zones"
        )
    return checked, text, runs


def _fill_differences(files, lex) -> tuple[int, list[str], list[str]]:
    """(fills checked, fills whose point text differs, fills whose run of points
    differs) over every fill in ``files``, aggregated (rung 2 plan D9: a few fills
    re-emit identically today, so per-file strict xfails could pass by luck). A file the
    lexer port finds unbalanced is bug 54's, which rung 1's corpus test covers."""
    checked = 0
    text: list[str] = []
    runs: list[str] = []
    skipped = []
    for name, path in files:
        data = path.read_bytes()
        if b"(filled_polygon" not in data:
            continue
        try:
            top = lex(path, data)
        except L.Unbalanced:
            skipped.append(name)
            continue
        n, bad_text, bad_runs = _fill_mismatches(data, top)
        checked += n
        text += [f"{name}: {line}" for line in bad_text]
        runs += [f"{name}: {line}" for line in bad_runs]
    if not checked:
        raise RuntimeError(f"no zone fills found in {len(files)} boards (lexer refused {skipped})")
    return checked, text, runs


def _listing(wrong: list[str], checked: int, what: str) -> str:
    return f"{len(wrong)} of {checked} fills {what}:\n" + "\n".join(wrong[:20])


def _skip_without_demos(boards) -> None:
    if not K.share_dirs():
        pytest.skip(K.NO_INSTALL)
    if not any(name.startswith("demos/") for name, _ in boards):
        pytest.skip("the install ships no demos (the macOS cask ships templates only)")


def _lexed_fresh(path, data: bytes) -> list:
    return L.tree(data, knows_bar=L.knows_bar_of(data))


_PER_PUSH_BOARDS = K.shipped(suffixes=(".kicad_pcb",), names=(), cap=K.PER_PUSH_CAP)


def test_filled_polygon_bytes_write_kicads_fill_points():
    """Every zone fill in the demo and template boards KiCad saved (files up to
    kicad_corpus.PER_PUSH_CAP), re-emitted from its own points, holds KiCad's (xy) lists
    in KiCad's order, each byte for byte. Where the lines break is the next test's."""
    _skip_without_demos(_PER_PUSH_BOARDS)
    checked, text, _ = _fill_differences(_PER_PUSH_BOARDS, lambda path, data: K.lexed(path))
    assert not text, _listing(text, checked, "hold point text unlike KiCad's")


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_62)
def test_filled_polygon_bytes_reproduce_kicads_fills():
    """Every zone fill in the demo and template boards KiCad saved (files up to
    kicad_corpus.PER_PUSH_CAP), re-emitted from its own points, is KiCad's run of points,
    byte for byte. One aggregated item: 3 of the 416 fills in KiCad 9.0.8's per-push
    boards re-emit identically today (measured 2026-10-11). The point text is the test
    above's."""
    _skip_without_demos(_PER_PUSH_BOARDS)
    checked, _, runs = _fill_differences(_PER_PUSH_BOARDS, lambda path, data: K.lexed(path))
    assert not runs, _listing(runs, checked, "differ from KiCad's")


@pytest.mark.nightly
def test_nightly_filled_polygon_bytes_write_kicads_fill_points():
    """The point text over every board the install holds, the largest included (KiCad
    9.0.8's vme-wren is 71.6 MB), each lexed without the cache."""
    boards = K.shipped(subdirs=None, suffixes=(".kicad_pcb",), names=())
    _skip_without_demos(boards)
    checked, text, _ = _fill_differences(boards, _lexed_fresh)
    assert not text, _listing(text, checked, "hold point text unlike KiCad's")


@pytest.mark.nightly
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_62)
def test_nightly_filled_polygon_bytes_reproduce_kicads_fills():
    """The runs of points over every board the install holds, each lexed without the
    cache."""
    boards = K.shipped(subdirs=None, suffixes=(".kicad_pcb",), names=())
    _skip_without_demos(boards)
    checked, _, runs = _fill_differences(boards, _lexed_fresh)
    assert not runs, _listing(runs, checked, "differ from KiCad's")
