"""Rung 2: number text, KiCad's reading of it, and the grids the tools place things on.

The code under test turns coordinate text into internal units and back
(_connectivity.iu, mm and kiround for schematics; pcb._mm and pcb._numish_text for
boards), snaps schematic positions to the 1.27 mm grid (_shared._snap_grid), lays out the
footprints a netlist import adds (_netlist_import.grid_slot), and spells the coordinates
add_text, add_label, move_footprint and add_trace write.

Judges, none of them the code under test:

- the port of FormatInternalUnits in tests/kicad_ref/eda_units.py, the text KiCad writes
  for a coordinate (common/eda_units.cpp:170-200 at 9.0.8, 194-225 at 10.0.7), with
  FormatAngle (162-167, 186-191) and KiROUND for both majors beside it;
- the ports of KiCad's number readers in tests/kicad_ref/parse_units.py: parseDouble,
  parseInternalUnits for schematics (clamped at INT_MAX * 0.7071 internal units) and
  parseBoardUnits for boards (clamped at INT_MAX - 10 nm). A printed coordinate is judged
  by KiCad's reading of it, so these tests accept any spelling KiCad reads the same way;
  only the writer tests, whose subject is the spelling (bug 61), compare text;
- the files KiCad saved: every coordinate in the demos and templates it ships;
- math: inverse and negation laws, idempotence, odd symmetry, monotonicity, and exact
  integer arithmetic for nearness and distance.

Generated values are ints counting internal units (100 nm in a schematic, 1 nm on a
board) wherever the code under test takes text or ints, so no rounding sits between a
generated case and its judge; floats appear only where a tool takes a float.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
from fractions import Fraction
from pathlib import Path

import geom_gen as G
import kicad_corpus as K
import pytest
import sexpr_gen
from conftest import _nightly_skip_reason
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U
from kicad_ref import parse_units as P

from mcp_server_kicad import _connectivity as C
from mcp_server_kicad import _netlist_import as NI
from mcp_server_kicad import pcb, schematic
from mcp_server_kicad._shared import _snap_grid

SCH = U.SCH_IU_PER_MM
PCB = U.PCB_IU_PER_MM
MM = PCB  # nanometres per millimetre

# ---------------------------------------------------------------------------
# reading files with the lexer port
# ---------------------------------------------------------------------------


def _head(item) -> bytes | None:
    if item.kind == L.GROUP and item.items and item.items[0].kind in L.ATOM_KINDS:
        return item.items[0].value
    return None


def _children(group, name: bytes) -> list:
    return [c for c in group.items if _head(c) == name]


def _child(group, name: bytes):
    """The one child list headed ``name``; RuntimeError when there is not exactly one."""
    found = _children(group, name)
    if len(found) != 1:
        raise RuntimeError(f"{len(found)} ({name.decode()} ...) lists at byte {group.start}")
    return found[0]


def _root(data: bytes):
    return L.tree(data, knows_bar=L.knows_bar_of(data))[0]


# ---------------------------------------------------------------------------
# number tokens
# ---------------------------------------------------------------------------


@st.composite
def _number_tokens(draw, low_mm: Fraction, high_mm: Fraction) -> str:
    """A NUMBER token for an exact decimal of magnitude ``low_mm`` to ``high_mm``, either
    sign, in a spelling KiCad's lexer classifies as a NUMBER (isNumber) and parseDouble
    reads: plain with 0 to 10 decimals (trailing zeros, a bare leading point, a leading
    zero, a bare trailing point), or a mantissa with an exponent (e or E, the exponent
    signed or not). Never a leading '+', which parseDouble refuses (bug 70 is the
    schematic readers accepting it)."""
    decimals = draw(st.integers(0, 10))
    scale = 10**decimals
    low, high = math.ceil(low_mm * scale), math.floor(high_mm * scale)
    assume(low <= high)
    digits = str(draw(st.integers(low, high)))
    style = draw(st.sampled_from(("plain", "exponent", "integer exponent")))
    if style == "plain":
        padded = digits.rjust(decimals + 1, "0")
        whole, fraction = padded[: len(padded) - decimals], padded[len(padded) - decimals :]
        fraction += "0" * draw(st.integers(0, 2))
        if whole == "0" and fraction and draw(st.booleans()):
            whole = ""  # ".5"
        elif draw(st.integers(0, 4)) == 0:
            whole = "0" + whole  # "0100.5"
        point = "." if fraction or draw(st.booleans()) else ""  # "100." when nothing follows
        text = whole + point + fraction
    elif style == "exponent":
        exponent = len(digits) - 1 - decimals
        mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        sign = "" if exponent < 0 else draw(st.sampled_from(("", "+")))
        text = mantissa + draw(st.sampled_from("eE")) + sign + str(exponent)
    else:
        text = digits + "e" + str(-decimals)
    token = ("-" if draw(st.booleans()) else "") + text
    if not L.is_number(token.encode()):
        raise RuntimeError(f"the token strategy wrote {token!r}, which is no NUMBER")
    return token


def _sch_readings(token: str) -> set[int]:
    """parseInternalUnits of ``token`` under KiCad 9 and KiCad 10: one value, or two where
    the majors' KiROUND part, at a double just below a half that 9.0.8's added 0.5 rounds
    up (tests/kicad_ref/eda_units.py). Where they part, either reading is KiCad's."""
    return {P.parse_sch_internal_units(token, major=major) for major in (9, 10)}


# ---------------------------------------------------------------------------
# schematic numbers: _connectivity.mm, iu and kiround
# ---------------------------------------------------------------------------


@example(0)
@example(1)
@example(-1)
@example(-45)
@example(977_900)
@example(U.INT_MAX)
@example(U.INT_MIN)
@given(st.one_of(st.integers(U.INT_MIN, U.INT_MAX), G.iu_coordinates()))
def test_schematic_mm_writes_kicads_text(iu):
    """_connectivity.mm spells a schematic coordinate of ``iu`` internal units exactly as
    FormatInternalUnits does at the schematic scale (1e4 per mm), over the whole int
    range."""
    assert C.mm(iu) == U.format_internal_units(iu, SCH), iu


@example(0)
@example(1)
@example(-1)
@example(12_700)
@example(G.SCH_LIMIT)
@example(-G.SCH_LIMIT)
@given(G.iu_coordinates())
def test_schematic_iu_inverts_kicads_text(iu):
    """_connectivity.iu reads the text KiCad writes for ``iu`` internal units back as
    ``iu``, for every coordinate KiCad keeps (parseInternalUnits clamps anything larger,
    sch_io_kicad_sexpr_parser.cpp:618-638 at 9.0.8, 676-696 at 10.0.7)."""
    assert C.iu(U.format_internal_units(iu, SCH)) == iu, iu


#: Coordinates up to this many millimetres lie inside parseInternalUnits' clamp,
#: INT_MAX * 0.7071 internal units, about 151848.5687 mm.
_SCH_KEPT_MM = Fraction(151_848)
#: The first ten-thousandth of a millimetre KiCad clamps: 151848.5688 mm reads as
#: 1518485687 internal units, not 1518485688.
_SCH_CLAMPED_MM = Fraction("151848.5688")


@example("100.635")
@example("-0.0003")
@example("1.27E+2")
@example(".5")
@example("007")
@example("100.")
@example("0.00015")
@example("151848")
@example("-151848")
@given(_number_tokens(Fraction(0), _SCH_KEPT_MM))
def test_schematic_iu_reads_numbers_as_kicad_does(token):
    """For a NUMBER token inside the clamp, _connectivity.iu reads the internal units
    parseInternalUnits reads under KiCad 9 and KiCad 10."""
    kicad = _sch_readings(token)
    assert C.iu(token) in kicad, f"iu({token!r}) = {C.iu(token)}, KiCad reads {kicad}"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "bug 69: _connectivity.iu does not clamp as parseInternalUnits does"
        " (INT_MAX * 0.7071 internal units); fixed by step 12b"
    ),
)
@example("200000")
@example("-200000")
@given(_number_tokens(_SCH_CLAMPED_MM, Fraction(10**6)))
def test_schematic_iu_clamps_as_kicad_does(token):
    """Past the clamp KiCad keeps INT_MAX * 0.7071 internal units and still loads the
    file: kicad-cli loads a schematic whose symbol sits at x = 200000 mm
    (test_ladder_r2_judges.py::test_number_token_refusals_match_kicad_cli). iu must read
    what KiCad reads; today it reads 2000000000 for "200000"."""
    kicad = _sch_readings(token)
    assert C.iu(token) in kicad, f"iu({token!r}) = {C.iu(token)}, KiCad reads {kicad}"


@example(0.5)
@example(-0.5)
@example(1.5)
@example(2.5)
@example(-2.5)
@example(-0.0)
@example(0.49999999999999994)
@example(-0.49999999999999994)
@example(float(U.INT_MAX - 2))
@given(
    st.floats(
        -float(U.INT_MAX - 1),
        float(U.INT_MAX - 1),
        exclude_min=True,
        exclude_max=True,
        allow_nan=False,
    )
)
def test_connectivity_kiround_is_kicads(value):
    """_connectivity.kiround rounds as KiCad's KiROUND does (libs/kimath/include/math/
    util.h, ported for both majors in tests/kicad_ref/eda_units.py) below |v| = INT_MAX -
    1, past which KiCad clamps; the coordinate readers clamp before they round, which is
    bug 69's subject. Where 9.0.8 and 10.0.7 part, at 0.49999999999999994 and its
    negation (a double just below a half, which 9.0.8's added 0.5 rounds up; none other
    found over every half-integer up to 2000 and every power of two, six ulps either
    side, and 1e6 random doubles, package A probe, 2026-10-11), either answer is
    KiCad's."""
    kicad = {U.kiround(value, major=major) for major in (9, 10)}
    assert C.kiround(value) in kicad, (value, C.kiround(value), kicad)


# ---------------------------------------------------------------------------
# board numbers: pcb._mm and pcb._numish_text
# ---------------------------------------------------------------------------


@example(0)
@example(1)
@example(-1)
@example(-45)
@example(999_999)
@example(1_000_001)
@example(U.INT_MAX)
@example(U.INT_MIN)
@given(st.one_of(st.integers(U.INT_MIN, U.INT_MAX), G.nm_coordinates()))
def test_board_mm_writes_kicads_text(nm):
    """pcb._mm spells ``nm`` nanometres exactly as FormatInternalUnits does at the board
    scale (1e6 per mm), over the whole int range."""
    assert pcb._mm(nm) == U.format_internal_units(nm, PCB), nm


@example(0)
@example(1)
@example(-1)
@example(45)
@example(U.INT_MAX)
@example(-U.INT_MAX)
@given(st.one_of(st.integers(-U.INT_MAX, U.INT_MAX), G.nm_coordinates()))
def test_numish_text_negates_kicads_text(nm):
    """A flip negates a coordinate KiCad wrote: pcb._flip_footprint_cst calls
    _numish_text(-float(text)). Whatever the coordinate (from -INT_MAX, so that -nm is an
    int too, to INT_MAX), the result is the text KiCad writes for -nm."""
    text = U.format_internal_units(nm, PCB)
    assert pcb._numish_text(-float(text)) == U.format_internal_units(-nm, PCB), text


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="bug 63: pcb._numish_text writes '-0' for a value just below zero; fixed by step 7",
)
@example(-1e-7)
@example(-4e-7)
@given(st.floats(-2147.483647, 2147.483647, allow_nan=False, allow_infinity=False))
def test_numish_text_never_writes_minus_zero(value):
    """FormatInternalUnits formats an int count of nanometres, so KiCad never writes "-0";
    neither may _numish_text, for any value on a board."""
    assert pcb._numish_text(value) != "-0", value


# ---------------------------------------------------------------------------
# every coordinate KiCad wrote, through the readers
# ---------------------------------------------------------------------------

_COORDINATE_HEADS = frozenset({b"at", b"xy", b"start", b"end", b"mid", b"center"})
#: The raw bytes of a coordinate list's opening, for the found-what-it-checks rule.
_COORDINATE_TEXT = re.compile(rb"\((?:at|xy|start|end|mid|center)[ \t]")


def _coordinate_tokens(top):
    """The x and y NUMBER tokens of every (at x y ...), (xy x y), (start x y), (end x y),
    (mid x y) and (center x y) list in a lexer-port tree."""
    pending = list(top)
    while pending:
        item = pending.pop()
        if item.kind != L.GROUP:
            continue
        pending.extend(item.items)
        if (
            item.items
            and item.items[0].kind == L.SYMBOL
            and item.items[0].value in _COORDINATE_HEADS
        ):
            yield from (tok for tok in item.items[1:3] if tok.kind == L.NUMBER)


def _number_problems(board: bool, top) -> tuple[int, list[str]]:
    """(coordinates checked, problems) for one file's lexer-port tree.

    A board coordinate ``t`` reads as nm (parseBoardUnits, both majors): pcb._mm(nm)
    must give KiCad's text for nm (FormatInternalUnits), and the flip's
    _numish_text(-float(t)) must give KiCad's text for -nm. A schematic coordinate reads
    as internal units: _connectivity.iu must read what parseInternalUnits reads, and
    _connectivity.mm must give KiCad's text for that value. KiCad's text is ``t`` itself
    wherever KiCad's writer wrote it, but not every shipped coordinate came from it:
    KiCad 10's jetson-agx-thor-baseboard usb.kicad_sch holds an image at
    "59.209102362204725", where saveBitmap writes FormatInternalUnits
    (sch_io_kicad_sexpr.cpp:936-952 at 9.0.8, 1046-1062 at 10.0.7), so the file's own
    text is not the judge."""
    checked, wrong = 0, []
    for tok in _coordinate_tokens(top):
        text = tok.value.decode()
        checked += 1
        where = f"byte {tok.start}: {text!r}"
        if board:
            for nm in {P.parse_board_units(text, major=major) for major in (9, 10)}:
                written = U.format_internal_units(nm, PCB)
                if pcb._mm(nm) != written:
                    wrong.append(
                        f"{where} reads as {nm} nm, which _mm writes {pcb._mm(nm)!r}"
                        f" where KiCad writes {written!r}"
                    )
                negated = U.format_internal_units(-nm, PCB)
                flipped = pcb._numish_text(-float(text))
                if flipped != negated:
                    wrong.append(f"{where} negated is {negated!r}; _numish_text gives {flipped!r}")
        else:
            iu = C.iu(text)
            kicad = _sch_readings(text)
            if iu not in kicad:
                wrong.append(f"{where} reads as {kicad} internal units in KiCad; iu gives {iu}")
            for value in kicad:
                written = U.format_internal_units(value, SCH)
                if C.mm(value) != written:
                    wrong.append(
                        f"{where} reads as {value} internal units, which mm writes"
                        f" {C.mm(value)!r} where KiCad writes {written!r}"
                    )
    return checked, wrong


def _check_corpus_file(path: Path, data: bytes, top) -> None:
    checked, wrong = _number_problems(path.suffix == ".kicad_pcb", top)
    if not checked and _COORDINATE_TEXT.search(data):
        raise RuntimeError(f"the lexer port found no coordinate in {path.name}, which holds some")
    assert not wrong, f"{len(wrong)} problems over {checked} coordinates:\n" + "\n".join(wrong[:20])


def _corpus_params(files):
    if files:
        return [pytest.param(path, id=name) for name, path in files]
    if K.share_dirs():
        return [pytest.param(None, id="install-without-files")]  # the test raises
    return [pytest.param(None, id="no-install", marks=pytest.mark.skip(reason=K.NO_INSTALL))]


_NUMBER_SUFFIXES = (".kicad_sch", ".kicad_pcb")
_NO_FILES = "a KiCad install was found, but no schematic or board in it"
_BUG_54 = "the lexer port finds this file unbalanced: bug 54, rung 1's policy"


@pytest.mark.parametrize(
    "path",
    _corpus_params(K.shipped(suffixes=_NUMBER_SUFFIXES, names=(), cap=K.PER_PUSH_CAP)),
)
def test_coordinates_kicad_wrote_survive_the_number_readers(path):
    """Every x and y of at, xy, start, end, mid and center in a demo or template
    schematic or board KiCad saved (up to kicad_corpus.PER_PUSH_CAP), at the file's own
    scale: the code under test reads it as KiCad does and writes it back as the text
    KiCad wrote, and on a board the flip's negation of it is KiCad's text for the
    negated value. A file holding coordinate lists must yield coordinates."""
    if path is None:
        raise RuntimeError(_NO_FILES)
    data = path.read_bytes()
    try:
        top = K.lexed(path)
    except L.Unbalanced:
        pytest.skip(_BUG_54)
    _check_corpus_file(path, data, top)


def _nightly_corpus():
    """Every installed schematic and board, listed only under the nightly profile."""
    if _nightly_skip_reason(os.environ.get("HYPOTHESIS_PROFILE")) is not None:
        return [pytest.param(None, id="nightly-only")]
    return _corpus_params(K.shipped(subdirs=None, suffixes=_NUMBER_SUFFIXES, names=()))


@pytest.mark.nightly
@pytest.mark.parametrize("path", _nightly_corpus())
def test_nightly_coordinates_kicad_wrote_survive_the_number_readers(path):
    """The same over every schematic and board the install holds (vme-wren included,
    lexed without the cache)."""
    if path is None:
        raise RuntimeError(_NO_FILES)
    data = path.read_bytes()
    try:
        top = L.tree(data, knows_bar=L.knows_bar_of(data))
    except L.Unbalanced:
        pytest.skip(_BUG_54)
    _check_corpus_file(path, data, top)


# ---------------------------------------------------------------------------
# grid snapping: _shared._snap_grid
# ---------------------------------------------------------------------------

#: _snap_grid's grid, 1.27 mm, exactly and in schematic internal units.
_GRID = Fraction(127, 100)
_GRID_IU = 12_700
#: The largest schematic page KiCad's page settings offer, MAX_PAGE_SIZE_EESCHEMA_MILS
#: 120000 mils (include/page_info.h:40 at 9.0.8, 36 at 10.0.7), in mm.
_PAGE_MM = 3048.0
_PAGE_STEPS = 2400  # grid steps either way: 3048 mm / 1.27 mm

_snap_inputs = st.one_of(
    st.floats(-_PAGE_MM, _PAGE_MM, allow_nan=False, allow_infinity=False),
    # the half-grid decimals, where the two neighbours tie
    st.integers(-_PAGE_STEPS, _PAGE_STEPS - 1).map(lambda k: round((k + 0.5) * 1.27, 4)),
    # positions KiCad can hold, one internal unit apart
    st.integers(-30_480_000, 30_480_000).map(lambda iu: iu / SCH),
)


@example(x=97.155, other=97.79)
@example(x=-978.535, other=-977.9)
@example(x=0.635, other=-0.635)
@example(x=0.0, other=1.27)
@given(x=_snap_inputs, other=_snap_inputs)
def test_snap_grid_lands_on_the_grid_kicad_reads(x, other):
    """The snapped value reads, under KiCad 9 and KiCad 10, as a whole number of 1.27 mm
    steps at most half a step (plus the internal unit KiROUND may add) from where the
    caller asked; snapping it again changes nothing; snapping -x gives the negation; and
    snapping keeps order."""
    snapped = _snap_grid(x)
    for major in (9, 10):
        held = U.kiround(snapped * SCH, major=major)
        assert held % _GRID_IU == 0, (x, snapped, held)
        assert abs(held - x * SCH) <= _GRID_IU // 2 + 1, (x, snapped, held)
    assert _snap_grid(snapped) == snapped, (x, snapped)
    assert _snap_grid(-x) == -snapped, (x, snapped, _snap_grid(-x))
    low, high = sorted((x, other))
    assert _snap_grid(low) <= _snap_grid(high), (low, high)


@example(97.155)
@example(-978.5350000000001)
@example(-998.855)
@example(0.635)
@example(3047.365)
@given(_snap_inputs)
def test_snap_grid_picks_a_nearest_grid_point(x):
    """The snapped value is a multiple of 1.27 mm itself (the double nearest k * 1.27),
    and no other multiple is strictly nearer to where KiCad holds ``x``; at a tie, an odd
    multiple of half a step, either neighbour is accepted (the tool's own snap: KiCad
    has no load-time rule to follow).

    Nearness is decided in KiCad's integer internal units (KiROUND(x * 1e4), both
    majors), exactly. The binary value of x would not do: the double nearest a
    half-grid decimal such as 97.155 is never exactly the midpoint, and on 826 of the
    14,166 doubles at or within 4 ulps of the half-grid decimals within 1000 mm,
    _snap_grid's float arithmetic picks the neighbour that is farther, by at most
    1.7e-13 mm (package A probe, 2026-10-11), while KiCad holds every one of those
    positions as the exact half-grid integer. KiCad's own grid snapping,
    GRID_HELPER::computeNearest, also starts from the integer position
    (common/tool/grid_helper.cpp:437-442 at 9.0.8, 450-455 at 10.0.7)."""
    snapped = _snap_grid(x)
    step = round(Fraction(snapped) / _GRID)
    assert snapped == float(step * _GRID), (x, snapped)
    for major in (9, 10):
        held = U.kiround(x * SCH, major=major)
        gap = abs(step * _GRID_IU - held)
        assert gap <= abs((step - 1) * _GRID_IU - held), (x, snapped, held, major)
        assert gap <= abs((step + 1) * _GRID_IU - held), (x, snapped, held, major)


# ---------------------------------------------------------------------------
# the coordinate text the writers emit
# ---------------------------------------------------------------------------

_NATIVE_SCH = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"
#: The fixture's page is A4, 297 x 210 mm; the writers refuse a point off the page.
_page_x = st.one_of(st.floats(0.0, 297.0), st.integers(0, 2_970_000).map(lambda iu: iu / SCH))
_page_y = st.one_of(st.floats(0.0, 210.0), st.integers(0, 2_100_000).map(lambda iu: iu / SCH))
_text_rotations = st.sampled_from((0, 90, 180, 270))
#: Board coordinates inside KiCad's load clamp (bug 57 is the writers going past it).
_board_xy = st.one_of(
    st.floats(-2000.0, 2000.0, allow_nan=False, allow_infinity=False),
    G.nm_coordinates(2000 * MM).map(lambda nm: nm / MM),
)
_BUG_61 = (
    "bug 61: the writers spell a number through _cst._num, with digits below the file's"
    " resolution, float noise or an exponent, which KiCad never writes; fixed by step 11"
)


def _fixed_point_problems(tokens, iu_per_mm: int, parse) -> list[str]:
    """Coordinate tokens that are not fixed points of KiCad's read then write: ``parse``
    (parse_units' reader for the file's scale) under both majors, then
    FormatInternalUnits. A token KiCad refuses is a problem too, not a crash."""
    problems = []
    for tok in tokens:
        text = tok.value.decode()
        try:
            readings = {parse(text, major=major) for major in (9, 10)}
        except P.ParseRefusal as exc:
            problems.append(f"{text!r}: KiCad refuses it ({exc})")
            continue
        for value in sorted(readings):
            back = U.format_internal_units(value, iu_per_mm)
            if back != text:
                problems.append(f"{text!r}: KiCad reads {value} and writes {back!r}")
    return problems


def _angle_problems(tok) -> list[str]:
    """An angle token that is not FormatAngle's text for the angle parseDouble reads."""
    text = tok.value.decode()
    try:
        value = P.parse_double(text)
    except P.ParseRefusal as exc:
        return [f"angle {text!r}: KiCad refuses it ({exc})"]
    back = U.format_angle(value)
    return [] if back == text else [f"angle {text!r}: FormatAngle writes {value!r} as {back!r}"]


def _write_schematic(tool, x: float, y: float, rotation: int) -> bytes:
    """The native fixture after ``tool("ladder", x, y, rotation)``, in a fresh directory."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "writer.kicad_sch"
        path.write_bytes(_NATIVE_SCH.read_bytes())
        tool("ladder", x, y, rotation=rotation, schematic_path=str(path))
        return path.read_bytes()


def _named(data: bytes, head: bytes, name: bytes):
    """The one top-level (head "name" ...) list of a file, read by the lexer port."""
    found = [
        g
        for g in _children(_root(data), head)
        if len(g.items) > 1 and g.items[1].kind == L.STRING and g.items[1].value == name
    ]
    if len(found) != 1:
        raise RuntimeError(f"{len(found)} ({head.decode()} {name.decode()!r} ...) lists written")
    return found[0]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_61)
@example(x=100.123456, y=50.0, rotation=0)
@settings(max_examples=sexpr_gen.budget(4))
@given(x=_page_x, y=_page_y, rotation=_text_rotations)
def test_add_text_writes_kicads_coordinate_text(x, y, rotation):
    """The x and y add_text writes are fixed points of KiCad's read then write:
    FormatInternalUnits(parseInternalUnits(t)) == t, the text KiCad itself writes for a
    text's position (saveText, eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr.cpp:
    1303-1308 at 9.0.8, 1464-1469 at 10.0.7). add_text at 100.123456 writes "100.123456",
    which KiCad reads as 1001235 internal units and writes "100.1235"."""
    at = _child(
        _named(_write_schematic(schematic.add_text, x, y, rotation), b"text", b"ladder"), b"at"
    )
    problems = _fixed_point_problems(at.items[1:3], SCH, P.parse_sch_internal_units)
    assert not problems, problems


@example(x=60.1 + 0.2, y=90.0, rotation=0)
@example(x=100.123456, y=0.00004, rotation=270)
@example(x=297.0, y=210.0, rotation=180)
@settings(max_examples=sexpr_gen.budget(4))
@given(x=_page_x, y=_page_y, rotation=_text_rotations)
def test_add_label_writes_kicads_coordinate_text(x, y, rotation):
    """The same fixed point for add_label, which rounds a position to 4 decimals before
    it writes it: the page bound keeps the value non-negative and the rounding makes it 0
    or at least 0.0001, so no exponent text arises.

    And KiCad holds the label where it was asked, to its resolution: within half an
    internal unit of the request, the most rounding to 0.0001 mm can move a value, plus
    1e-6 of one for the float product x * 1e4 (its error is under 1e-9 on this page).
    Without it, a writer that spelled a wrong number in KiCad's own form would pass."""
    at = _child(
        _named(_write_schematic(schematic.add_label, x, y, rotation), b"label", b"ladder"), b"at"
    )
    problems = _fixed_point_problems(at.items[1:3], SCH, P.parse_sch_internal_units)
    assert not problems, problems
    for tok, asked in zip(at.items[1:3], (x, y)):
        for major in (9, 10):
            held = P.parse_sch_internal_units(tok.value, major=major)
            assert abs(held - asked * SCH) <= 0.5 + 1e-6, (asked, tok.value, held, major)


def _footprint(data: bytes, reference: str):
    """The one top-level footprint whose Reference property is ``reference``."""
    found = [
        fp
        for fp in _children(_root(data), b"footprint")
        if any(
            len(p.items) > 2
            and p.items[1].value == b"Reference"
            and p.items[2].value == reference.encode()
            for p in _children(fp, b"property")
        )
    ]
    if len(found) != 1:
        raise RuntimeError(f"{len(found)} footprints {reference} on the board")
    return found[0]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_61)
@example(x=1.3203125, y=1e-05, rotation=0.1 + 0.2, version="k9")
@settings(max_examples=sexpr_gen.budget(4))
@given(
    x=_board_xy,
    y=_board_xy,
    rotation=st.one_of(st.none(), G.angles()),
    version=st.sampled_from(("k9", "k10")),
)
def test_move_footprint_writes_kicads_number_text(x, y, rotation, version):
    """The footprint's (at x y angle) after move_footprint: x and y are fixed points of
    parseBoardUnits then FormatInternalUnits, and the angle is FormatAngle's text for the
    angle parseDouble reads (the footprint writer, pcbnew/pcb_io/kicad_sexpr/
    pcb_io_kicad_sexpr.cpp:1127-1131 at 9.0.8, 1231-1235 at 10.0.7). Whether the angle
    is normalized as KiCad stores it is bug 53's subject, not this test's. Today
    1.3203125 is written as is (KiCad reads 1320313 nm and writes "1.320313"), 1e-05
    as "1e-05" and 0.1 + 0.2 degrees as "0.30000000000000004"."""
    board = G.board_text([G.footprint_text("R1", (100 * MM, 80 * MM))], version=version)
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "move.kicad_pcb"
        path.write_text(board, encoding="utf-8", newline="\n")
        pcb.move_footprint("R1", x, y, rotation=rotation, pcb_path=str(path))
        data = path.read_bytes()
    at = _child(_footprint(data, "R1"), b"at")
    problems = _fixed_point_problems(at.items[1:3], PCB, P.parse_board_units)
    problems += [p for tok in at.items[3:4] for p in _angle_problems(tok)]
    assert not problems, problems


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_61)
@example(start=(1e-05, 2.5e-07), end=(10.1 + 0.2, 3.0))
@settings(max_examples=sexpr_gen.budget(4))
@given(start=st.tuples(_board_xy, _board_xy), end=st.tuples(_board_xy, _board_xy))
def test_add_trace_writes_kicads_number_text(start, end):
    """The start and end add_trace writes are fixed points of parseBoardUnits then
    FormatInternalUnits (the segment writer, pcb_io_kicad_sexpr.cpp:2413-2416 at 9.0.8,
    2837-2840 at 10.0.7). On a KiCad 9 board: a KiCad 10 board has no net 0 for the trace
    to take, and add_trace refuses it there (bug 21). Today it writes "1e-05",
    "2.5e-07" and "10.299999999999999"."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "trace.kicad_pcb"
        path.write_text(G.board_text([], version="k9"), encoding="utf-8", newline="\n")
        pcb.add_trace(*start, *end, pcb_path=str(path))
        data = path.read_bytes()
    segment = _child(_root(data), b"segment")
    tokens = [*_child(segment, b"start").items[1:3], *_child(segment, b"end").items[1:3]]
    problems = _fixed_point_problems(tokens, PCB, P.parse_board_units)
    assert not problems, problems


# ---------------------------------------------------------------------------
# where a netlist import puts new footprints: _netlist_import.grid_slot
# ---------------------------------------------------------------------------


@example(anchor=(25_400_000, 25_400_000), pitch=10 * MM, count=40)
@example(anchor=(-1_000 * MM, 999_999_999), pitch=1, count=25)
@given(
    anchor=st.tuples(G.nm_coordinates(1_000 * MM), G.nm_coordinates(1_000 * MM)),
    pitch=st.integers(1, 100 * MM),
    count=st.integers(2, 40),
)
def test_grid_slots_stay_a_pitch_apart(anchor, pitch, count):
    """New footprints must not stack: the first ``count`` slots grid_slot gives for an
    anchor and pitch are pairwise at least a pitch apart where KiCad holds them, and the
    same k always gives the same slot.

    The import calls grid_slot in millimetres with pitch 10.0 and an anchor read from
    the board's footprint positions, or 25.4 mm on an empty board (pcb._fp_anchor), so
    anchors and pitches here are positions KiCad can hold: whole nanometres, passed as
    the millimetre floats a board file's text reads as. Each slot is judged where KiCad
    holds it, KiROUND(x * 1e6) nanometres under both majors (parseBoardUnits of the text
    a tool writes for it), and distances are compared exactly, in integers."""
    ax, ay, step = anchor[0] / MM, anchor[1] / MM, pitch / MM
    slots = [NI.grid_slot(k, ax, ay, step) for k in range(count)]
    assert [NI.grid_slot(k, ax, ay, step) for k in range(count)] == slots
    for major in (9, 10):
        held = [(U.kiround(x * MM, major=major), U.kiround(y * MM, major=major)) for x, y in slots]
        for i in range(count):
            for j in range(i + 1, count):
                dx, dy = held[i][0] - held[j][0], held[i][1] - held[j][1]
                assert dx * dx + dy * dy >= pitch * pitch, (i, j, held[i], held[j], major)
