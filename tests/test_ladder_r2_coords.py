"""Rung 2: board coordinates, judged by KiCad's RotatePoint and its load clamp.

Code under test: _shared._transform_local_to_board, which puts a footprint-local point on
the board; the board position get_footprint_pads prints for each pad; and the coordinate
atoms place_footprint, move_footprint, add_via and add_trace write.

KiCad holds a board coordinate as an int count of nanometres and reads one from the file
with parseBoardUnits, KiROUND( std::clamp( parseDouble() * 1e6, -INT_LIMIT, INT_LIMIT ) )
where INT_LIMIT = INT_MAX - 10 (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:89
and 195-220 at 9.0.8, 94 and 200-227 at 10.0.7). That is how it reads a footprint's (at)
(4533-4549 at 9.0.8, 5227-5243 at 10.0.7), a pad's (at) (5124-5140, 6013-6029), a track's
(start) and (end) (6352-6364, 7455-7467) and a via's (at) (6462-6468, 7575-7581). A
board with a coordinate past the clamp loads with no message, the item held at the clamp
(kicad-cli 9.0.8, measured 2026-10-11): bug 57.

A footprint's orientation is Normalize180 of its angle atom (FOOTPRINT::SetOrientation,
pcbnew/footprint.cpp:2641-2669 at 9.0.8, 3099-3137 at 10.0.7), and a pad sits at the
footprint's position plus RotatePoint of its local position by that orientation
(BOARD_ITEM::SetFPRelativePosition, pcbnew/board_item.cpp:348-359 at 9.0.8, 413-424 at
10.0.7, which the parser calls for the pad's (at)). RotatePoint swaps the coordinates
exactly on a quarter turn and otherwise rounds y sin + x cos and y cos - x sin with
KiROUND (libs/kimath/src/trigo.cpp:229-264 at both tags).

Judges, none of them the code under test:

- kicad_ref.trigo and kicad_ref.board_item, the ports of RotatePoint and
  SetFPRelativePosition, which test_ladder_r2_judges.py checks against pcbnew's own pad
  positions;
- kicad_ref.parse_units, the port of parseBoardUnits: a printed or written coordinate is
  right when KiCad's reading of its text is the nanometre the judge computes, whatever its
  spelling (the spelling is bug 61's, judged separately);
- pcbnew's pad.GetPosition(), through pcbnew_judge;
- kicad-cli's position export, which prints each footprint's position as KiCad holds it
  after loading the board: GetPosition() in millimetres through "%f", y negated
  (pcbnew/exporters/place_file_exporter.cpp:184-207 at 9.0.8, 193-216 at 10.0.7).

Resolution: every position must read as KiCad's nanometre exactly, at every angle. On the
awkward-angle board below all 60 printed pad positions equal pcbnew's, the tool's
unrounded value at worst 0.485 nm from pcbnew's (measured 2026-10-11, pcbnew 9.0.8). The
exception is bug 87, at the exact half-nanometre ties _half_nm_tie names: RotatePoint
rounds a product a hair to one side of the half, and the tool's floating-point
millimetres can land on the other, 1 nm away. The passing tests leave the ties out, and
two strict xfails hold them. A near tie, an exact value within the tool's rounding error
of a half, can miss the same way: pcbnew 9.0.8 holds a pad at local (38.613965, 0) mm on
a footprint at (-999, 0.000003) mm and 45 degrees at x = -971.695803 mm, where
get_footprint_pads prints -971.695804 (measured 2026-10-11). No exact predicate marks
the near ties, so the passing tests keep them; no generated case has reached one (the ci
profile, and nightly runs of each generated test of a rotated position, 2026-10-11).
Pure tests take both majors' KiROUND, which differ on a few doubles
(kicad_ref.eda_units); tests anchored on pcbnew or kicad-cli take the major they run.
"""

from __future__ import annotations

import csv
import io
import re
import tempfile
from fractions import Fraction
from pathlib import Path

import geom_gen as G
import pcbnew_judge as PJ
import pytest
import sexpr_gen
from conftest import _kicad_cli_major, requires_cli
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from kicad_ref import board_item as B
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U
from kicad_ref import parse_units as P
from kicad_ref import trigo
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import pcb
from mcp_server_kicad._shared import _run_cli, _transform_local_to_board

MM = U.PCB_IU_PER_MM  # nanometres per millimetre
MAJORS = (9, 10)

_BUG57 = (
    "bug 57: board tools write coordinates beyond KiCad's load clamp (INT_MAX - 10 nm, about"
    " 2147.48 mm) and KiCad silently moves the item; fixed by step 12c"
)
_BUG87 = (
    "bug 87: _shared._transform_local_to_board computes in float millimetres, so at an"
    " exact half-nanometre tie (a multiple of 30 degrees off the quarter turns, an odd offset"
    " on one axis and zero on the other) it can land 1 nm from KiCad's RotatePoint; fixed by"
    " step 7"
)

# ---------------------------------------------------------------------------
# reading boards with the lexer port
# ---------------------------------------------------------------------------


def _head(item) -> bytes | None:
    if item.kind == L.GROUP and item.items and item.items[0].kind in L.ATOM_KINDS:
        return item.items[0].value
    return None


def _children(group, name: bytes) -> list:
    return [c for c in group.items if _head(c) == name]


def _child(group, name: bytes):
    """The first child list headed ``name``; LookupError when there is none."""
    found = _children(group, name)
    if not found:
        raise LookupError(f"no ({name.decode()} ...) in the list at byte {group.start}")
    return found[0]


def _root(data: bytes):
    return L.tree(data, knows_bar=L.knows_bar_of(data))[0]


def _reference(fp) -> str:
    for prop in _children(fp, b"property"):
        if len(prop.items) > 2 and prop.items[1].value == b"Reference":
            return prop.items[2].value.decode()
    raise LookupError(f"the footprint at byte {fp.start} has no Reference")


def _footprints(root, ref: str) -> list:
    return [fp for fp in _children(root, b"footprint") if _reference(fp) == ref]


def _kicad_reads(token, major: int) -> int:
    """parseBoardUnits of a lexer token: NeedNUMBER refuses any token but a NUMBER, so a
    quoted coordinate is refused even when its text is a number."""
    if token.kind != L.NUMBER:
        raise P.ParseRefusal(f"need a number, got the {token.kind} {token.value!r}")
    return P.parse_board_units(token.value, major=major)


def _placement(fp, *, major: int) -> tuple[tuple[int, int], float]:
    """A footprint's position (nm) and orientation (degrees) as KiCad ``major`` holds them
    after reading its (at): parseBoardUnits for x and y, and Normalize180 of parseDouble
    for the angle, which is 0 when the (at) has none."""
    at = _child(fp, b"at")
    position = (_kicad_reads(at.items[1], major), _kicad_reads(at.items[2], major))
    if len(at.items) > 3:
        return position, trigo.normalize180(P.parse_double(at.items[3].value))
    return position, 0.0


def _pad_locals(fp, *, major: int) -> dict[str, tuple[int, int]]:
    """{pad number: its footprint-local (x, y) in nm} as KiCad ``major`` reads them."""
    out = {}
    for pad in _children(fp, b"pad"):
        at = _child(pad, b"at")
        out[pad.items[1].value.decode()] = (
            _kicad_reads(at.items[1], major),
            _kicad_reads(at.items[2], major),
        )
    return out


def _forget(path: Path) -> None:
    """Drop the parsed board pcb._open_pcb_cst cached for ``path`` (keyed on the resolved
    path): every generated example's board lives in a temporary directory that is gone
    after it, and a nightly run makes thousands of them."""
    pcb._BOARD_CACHE.pop(str(path.resolve()), None)


# ---------------------------------------------------------------------------
# what a position is at KiCad's resolution
# ---------------------------------------------------------------------------


def _as_read(nm: int) -> float:
    """A board coordinate as a tool reads it from a file KiCad wrote: float() of the text
    FormatInternalUnits writes for ``nm``."""
    return float(G.mm(nm))


def _half_nm_tie(angle: float, local: tuple[int, int]) -> bool:
    """Whether RotatePoint turns ``local`` (nm) by ``angle`` (degrees) onto an exact
    half-nanometre tie on one axis, decided in exact arithmetic: bug 87's cases.

    Off the quarter turns RotatePoint rounds y sin + x cos and y cos - x sin with KiROUND
    (libs/kimath/src/trigo.cpp:229-264 at 9.0.8). At a multiple of 30 degrees one of sin
    and cos is exactly +-1/2 and the other +-sqrt(3)/2, so a rotated component is k + 1/2
    exactly when the coordinate on sqrt(3)/2 is 0 and the one on 1/2 is odd: either way
    round, an odd offset on one axis and zero on the other. KiCad's sin and cos of these
    angles are doubles a hair off 1/2 (EDA_ANGLE::Sin and Cos special-case only the
    multiples of 45, libs/kimath/include/geometry/eda_angle.h:178-214 at 9.0.8; sin 30 is
    0.49999999999999994), so KiROUND rounds the tie from a hair to one side, and a value
    computed another way can land on the other.

    No other angle makes an exact tie. A rational rotated component makes the other the
    square root of a rational (a turn keeps x^2 + y^2), which puts sin and cos in one
    quadratic field; for a rational number of degrees, which every double is, that leaves
    the multiples of 30 and 45, and at 45 a rational component is 0.
    """
    degrees = Fraction(angle)
    if degrees.denominator != 1 or degrees.numerator % 30 or not degrees.numerator % 90:
        return False
    x, y = local
    return (x == 0) != (y == 0) and (x + y) % 2 == 1


def _ties_on(data: bytes) -> bool:
    """Whether a pad on the board ``data`` sits at a half-nanometre tie, its footprint's
    orientation and its own local position read as KiCad reads them. The written angle
    is FormatAngle's ten digits, so an angle a hair off 30 degrees is read as 30."""
    for fp in _children(_root(data), b"footprint"):
        _, orientation = _placement(fp, major=10)
        if any(_half_nm_tie(orientation, local) for local in _pad_locals(fp, major=10).values()):
            return True
    return False


#: Footprint positions within +-1000 mm and pad offsets within +-50 mm: inside KiCad's
#: range, with room for any rotation of the offset.
_FP_AT = st.tuples(G.nm_coordinates(1_000 * MM), G.nm_coordinates(1_000 * MM))
_OFFSETS = st.tuples(G.nm_coordinates(50 * MM), G.nm_coordinates(50 * MM))
_VERSIONS = st.sampled_from(("k9", "k10"))
#: Bug 87's ties as _half_nm_tie decides them: a multiple of 30 degrees within +-3600 off
#: the quarter turns, and an odd offset within +-100 mm on one axis with zero on the other.
_TIE_ANGLES = st.integers(-120, 120).filter(lambda k: k % 3 != 0).map(lambda k: 30.0 * k)
_ODD_NM = st.integers(-(50 * MM), 50 * MM - 1).map(lambda n: 2 * n + 1)
_TIE_OFFSETS = st.one_of(_ODD_NM.map(lambda n: (n, 0)), _ODD_NM.map(lambda n: (0, n)))

# ---------------------------------------------------------------------------
# footprint-local to board
# ---------------------------------------------------------------------------


@settings(max_examples=sexpr_gen.budget())
@example(fp=(100 * MM, 50_123_457), angle=37.5, local=(-5 * MM, MM))
@example(fp=(-7, 3), angle=-90.0, local=(1_234_567, -7_654_321))
@example(fp=(0, 0), angle=45.0, local=(MM, 0))
@example(fp=(0, 0), angle=3600.0, local=(12_500_000, -333_333))
@given(
    fp=_FP_AT,
    angle=G.angles(),
    local=st.tuples(G.nm_coordinates(100 * MM), G.nm_coordinates(100 * MM)),
)
def test_local_to_board_is_rotatepoint(fp, angle, local):
    """_transform_local_to_board, given a footprint position and a local point as the text
    KiCad writes for them, lands on RotatePoint's nanometre: KiROUND of its result in nm
    is the footprint position plus rotate_point of the local point, under both majors.
    Exact half-nanometre ties are left out (bug 87, the next test). The textbook
    rotation, whose signs are the transpose of RotatePoint's, mirrors every rotated
    offset and is far off."""
    assume(not _half_nm_tie(angle, local))
    bx, by = _transform_local_to_board(
        _as_read(fp[0]), _as_read(fp[1]), angle, _as_read(local[0]), _as_read(local[1])
    )
    for major in MAJORS:
        rx, ry = trigo.rotate_point(local[0], local[1], angle, major=major)
        want = (fp[0] + rx, fp[1] + ry)
        got = (U.kiround(bx * MM, major=major), U.kiround(by * MM, major=major))
        assert got == want, (major, (bx, by), got, want)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG87)
@settings(max_examples=sexpr_gen.budget())
@example(fp=(0, 0), angle=30.0, local=(-5, 0))
@example(fp=(100 * MM, 50_123_457), angle=30.0, local=(-11, 0))
@given(fp=_FP_AT, angle=_TIE_ANGLES, local=_TIE_OFFSETS)
def test_local_to_board_rounds_half_nanometre_ties_as_kicad_does(fp, angle, local):
    """The test above at bug 87's ties: KiROUND of _transform_local_to_board's result in
    nm is the footprint position plus rotate_point of the local point, under both majors.
    The tool lands on the far side of the half for about half of the ties (47.7% of
    200,000 drawn as these strategies draw them, measured 2026-10-11): at (0, 0) and 30
    degrees, local (-5, 0) nm comes out at y = 3 nm where RotatePoint gives 2."""
    if not _half_nm_tie(angle, local):
        raise RuntimeError(f"the strategies drew angle {angle} and local {local}: no tie")
    bx, by = _transform_local_to_board(
        _as_read(fp[0]), _as_read(fp[1]), angle, _as_read(local[0]), _as_read(local[1])
    )
    for major in MAJORS:
        rx, ry = trigo.rotate_point(local[0], local[1], angle, major=major)
        want = (fp[0] + rx, fp[1] + ry)
        got = (U.kiround(bx * MM, major=major), U.kiround(by * MM, major=major))
        assert got == want, (major, (bx, by), got, want)


# ---------------------------------------------------------------------------
# get_footprint_pads
# ---------------------------------------------------------------------------

_PAD_LINE = re.compile(
    r"\s*Pad (?P<number>\S+): .*? @ board \((?P<x>[^,()\s]+), (?P<y>[^,()\s]+)\)"
)


def _printed_board_positions(text: str) -> dict[str, tuple[str, str]]:
    """{pad number: (x text, y text)} of the board positions get_footprint_pads prints, one
    line per pad after its header line."""
    printed = {}
    for line in text.splitlines()[1:]:
        found = _PAD_LINE.match(line)
        assert found is not None, f"no board position in {line!r}"
        printed[found["number"]] = (found["x"], found["y"])
    return printed


def _printed_problems(
    printed: tuple[str, str], want: tuple[int, int], major: int, what: str
) -> list[str]:
    """Why the printed (x, y) does not read, as KiCad ``major`` reads text, as ``want``;
    empty when it does."""
    try:
        got = (
            P.parse_board_units(printed[0], major=major),
            P.parse_board_units(printed[1], major=major),
        )
    except P.ParseRefusal as exc:
        return [f"{what}: KiCad refuses the printed {printed} ({exc})"]
    if got != want:
        return [f"{what}: printed {printed} reads {got} nm, KiCad has {want} nm"]
    return []


def _pad_problems(text: str, data: bytes, ref: str) -> list[str]:
    """Each pad get_footprint_pads printed for ``ref``, against fp_relative_to_board of the
    footprint and pad as KiCad reads them from ``data``, under both majors."""
    (fp,) = _footprints(_root(data), ref)
    printed = _printed_board_positions(text)
    problems = []
    for major in MAJORS:
        at, orientation = _placement(fp, major=major)
        pads = _pad_locals(fp, major=major)
        if set(printed) != set(pads):
            problems.append(f"printed pads {sorted(printed)}, the footprint has {sorted(pads)}")
            continue
        for number, local in pads.items():
            want = B.fp_relative_to_board(at, orientation, local, major=major)
            what = f"KiCad {major}, {ref} at {orientation} degrees, pad {number} local {local}"
            problems += _printed_problems(printed[number], want, major, what)
    return problems


@settings(max_examples=sexpr_gen.budget(4))
@example(
    version="k9",
    at=(100 * MM, 50_123_457),
    angle=37.5,
    pads=[((-5 * MM, MM), 0.0), ((1_234_567, -7_654_321), 270.0)],
)
@example(version="k10", at=(-7, 3), angle=270.0, pads=[((1, 3 * MM), 0.5)])
@example(version="k9", at=(0, 0), angle=359.9, pads=[((12_500_000, 0), 0.0)])
@given(
    version=_VERSIONS,
    at=_FP_AT,
    angle=G.angles(),
    pads=st.lists(st.tuples(_OFFSETS, G.half_degree_angles()), min_size=1, max_size=4),
)
def test_get_footprint_pads_reports_kicads_pad_positions(version, at, angle, pads):
    """On a board KiCad could have saved (either header), with a footprint at a generated
    position and angle and 1 to 4 pads at generated offsets and angles of their own, each
    board (x, y) get_footprint_pads prints reads, as KiCad reads number text, as
    fp_relative_to_board of the footprint's position, the orientation KiCad stores
    (Normalize180 of the angle atom) and the pad's local position, all read back from the
    file, to the nanometre. A pad's own angle moves nothing. Boards with a pad at an
    exact half-nanometre tie, as KiCad reads the file, are left out (bug 87)."""
    numbered = [(str(k + 1), offset, own) for k, (offset, own) in enumerate(pads)]
    footprint = G.footprint_text("U1", at, angle, numbered, version=version)
    text = G.board_text([footprint], version=version)
    assume(not _ties_on(text.encode()))
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "pads.kicad_pcb"
        path.write_text(text, encoding="utf-8", newline="\n")
        try:
            printed = pcb.get_footprint_pads("U1", pcb_path=str(path))
        finally:
            _forget(path)
        data = path.read_bytes()
    problems = _pad_problems(printed, data, "U1")
    assert not problems, "\n".join(problems)


_PAD_POSITIONS = r"""
import pcbnew
board = pcbnew.LoadBoard(payload["board"])
result = {
    fp.GetReference(): {
        pad.GetNumber(): [pad.GetPosition().x, pad.GetPosition().y] for pad in fp.Pads()
    }
    for fp in board.GetFootprints()
}
"""


def _pcbnew_major() -> int:
    """pcbnew's major version, or a skip where no Python imports pcbnew."""
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    return PJ.major()


def _pcbnew_problems(board: Path, found: dict, refs, major: int) -> tuple[int, list[str]]:
    """(pads checked, problems): each pad position get_footprint_pads prints for the
    footprints ``refs`` on ``board`` against pcbnew's pad.GetPosition() in ``found``, to
    the nanometre."""
    root = _root(board.read_bytes())
    problems = []
    checked = 0
    try:
        for ref in refs:
            pads = found[ref]
            printed = _printed_board_positions(pcb.get_footprint_pads(ref, pcb_path=str(board)))
            (fp,) = _footprints(root, ref)
            _, orientation = _placement(fp, major=major)
            if set(printed) != set(pads):
                problems.append(f"{ref}: printed pads {sorted(printed)}, pcbnew {sorted(pads)}")
                continue
            for number, position in pads.items():
                checked += 1
                what = f"{ref} at {orientation} degrees, pad {number}"
                problems += _printed_problems(
                    printed[number], (position[0], position[1]), major, what
                )
    finally:
        _forget(board)
    return checked, problems


def _check_against_pcbnew(board: Path, major: int) -> int:
    """Every pad position get_footprint_pads prints for every footprint on ``board`` reads
    as pcbnew's pad.GetPosition(), to the nanometre. Returns how many pads it checked."""
    found = PJ.run(_PAD_POSITIONS, {"board": str(board)})
    checked, problems = _pcbnew_problems(board, found, found, major)
    assert not problems, f"pcbnew {major}:\n" + "\n".join(problems)
    return checked


#: The rung 2 design's pcbnew board: twelve footprint angles, quarter turns among them,
#: and five pad offsets on each footprint.
_AWKWARD_ANGLES = (0, 90, -90, 180, 45, 30, 37.5, 359.9, 135, 12.3456789, -135.25, 0.001)
_AWKWARD_OFFSETS = (
    (-5 * MM, MM),
    (1_234_567, -7_654_321),
    (1, 3 * MM),
    (12_500_000, 0),
    (-333_333, -666_667),
)
#: Bug 87's ties on the same board, (reference, position in nm, angle, pad offset in nm),
#: each pad held by pcbnew 9.0.8 1 nm from where get_footprint_pads prints it (measured
#: 2026-10-11): T1's at y = 50.123462 mm, printed 50.123463.
_TIE_FOOTPRINTS = (
    ("T1", (100 * MM, 50_123_457), 30.0, (-11, 0)),
    ("T2", (123_456_789, -98_765_432), 150.0, (3, 0)),
    ("T3", (10 * MM, 20 * MM), 60.0, (0, 1)),
    ("T4", (10 * MM, 30 * MM), -30.0, (1, 0)),
)


@pytest.fixture(scope="module")
def pcbnew_pads(tmp_path_factory):
    """(board, pcbnew's major, pcbnew's pad positions) for one board holding the twelve
    awkward-angle footprints, U1 to U12, and _TIE_FOOTPRINTS: one pcbnew run per worker
    for the two tests below."""
    major = _pcbnew_major()
    items = [
        G.footprint_text(
            f"U{i + 1}",
            (100 * MM + 10 * MM * i, 50_123_457),
            angle,
            [(str(k + 1), offset, 0.0) for k, offset in enumerate(_AWKWARD_OFFSETS)],
        )
        for i, angle in enumerate(_AWKWARD_ANGLES)
    ]
    items += [
        G.footprint_text(ref, at, angle, [("1", local, 0.0)])
        for ref, at, angle, local in _TIE_FOOTPRINTS
    ]
    board = tmp_path_factory.mktemp("pads") / "pads.kicad_pcb"
    board.write_text(G.board_text(items), encoding="utf-8", newline="\n")
    return board, major, PJ.run(_PAD_POSITIONS, {"board": str(board)})


def test_pad_positions_match_pcbnew(pcbnew_pads):
    """On the awkward-angle board (twelve footprints, five pads each), every pad position
    get_footprint_pads prints reads as pcbnew's pad.GetPosition() for the board, to the
    nanometre (all 60 were exact when measured). None of the twelve has a pad at an exact
    half-nanometre tie; the board's tie footprints are the next test's (bug 87)."""
    board, major, found = pcbnew_pads
    if any(_half_nm_tie(a, offset) for a in _AWKWARD_ANGLES for offset in _AWKWARD_OFFSETS):
        raise RuntimeError("an awkward-angle pad sits at a half-nanometre tie")
    refs = [f"U{i + 1}" for i in range(len(_AWKWARD_ANGLES))]
    checked, problems = _pcbnew_problems(board, found, refs, major)
    assert not problems, f"pcbnew {major}:\n" + "\n".join(problems)
    if checked != 60:
        raise RuntimeError(f"pcbnew {major} read {checked} pads where the board holds 60")


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG87)
def test_pad_positions_at_half_nanometre_ties_match_pcbnew(pcbnew_pads):
    """Bug 87's ties, T1 to T4, on the same board and from the same pcbnew run: each pad
    position get_footprint_pads prints reads as pcbnew's pad.GetPosition(), to the
    nanometre."""
    board, major, found = pcbnew_pads
    if not all(_half_nm_tie(angle, local) for _, _, angle, local in _TIE_FOOTPRINTS):
        raise RuntimeError("a pad in _TIE_FOOTPRINTS is no half-nanometre tie")
    refs = [ref for ref, _, _, _ in _TIE_FOOTPRINTS]
    checked, problems = _pcbnew_problems(board, found, refs, major)
    if checked != len(_TIE_FOOTPRINTS):
        raise RuntimeError(f"pcbnew {major} read {checked} tie pads, not {len(refs)}")
    assert not problems, f"pcbnew {major}:\n" + "\n".join(problems)


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@given(
    st.lists(
        st.tuples(_FP_AT, G.angles(), st.lists(_OFFSETS, min_size=1, max_size=5)),
        min_size=1,
        max_size=6,
    )
)
def test_nightly_pad_positions_match_pcbnew(footprints):
    """The same over generated footprints, angles and pad offsets, one pcbnew run per
    board. Boards with a pad at an exact half-nanometre tie, as KiCad reads the file, are
    left out (bug 87)."""
    major = _pcbnew_major()
    items = [
        G.footprint_text(f"U{i + 1}", at, angle, [(str(k + 1), p, 0.0) for k, p in enumerate(pads)])
        for i, (at, angle, pads) in enumerate(footprints)
    ]
    text = G.board_text(items)
    assume(not _ties_on(text.encode()))
    with tempfile.TemporaryDirectory() as work:
        board = Path(work) / "pads.kicad_pcb"
        board.write_text(text, encoding="utf-8", newline="\n")
        _check_against_pcbnew(board, major)


# ---------------------------------------------------------------------------
# what the board writers write
# ---------------------------------------------------------------------------

#: Where _board_with_r1 puts R1, in nm.
_R1_AT = (50 * MM, 40 * MM)


def _board_with_r1(version: str) -> str:
    """A board ("k9" or "k10" header) holding footprint R1 at _R1_AT with one pad on net
    N1, so add_trace and add_via can name a net that exists on either header: net 1 is a
    row of a KiCad 9 board's net table and, on a KiCad 10 board, which has no table, the
    first name a pad carries (pcb._net_table). Net 0 stays out of it: a KiCad 10 board
    refuses net 0, which is bug 21's subject, not this file's."""
    layers = '\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
    net = '\t\t\t(net 1 "N1")\n' if version == "k9" else '\t\t\t(net "N1")\n'
    footprint = G.footprint_text("R1", _R1_AT, 0.0, [("1", (-MM, 0), 0.0)], version=version)
    if footprint.count(layers) != 1:
        raise RuntimeError("geom_gen's pad no longer holds one (layers ...) line to follow")
    table = ['\t(net 1 "N1")\n'] if version == "k9" else []
    return G.board_text([*table, footprint.replace(layers, layers + net)], version=version)


_TOOLS = ("place_footprint", "move_footprint", "add_via", "add_trace")


def _call(tool: str, path: Path, coords: tuple[float, float, float, float]) -> None:
    """Run ``tool`` on the board at ``path``: place_footprint puts a new R2 at (x, y),
    move_footprint moves R1 there, add_via puts a via there (net 1), and add_trace runs a
    track from (x, y) to (x2, y2) (net 1)."""
    x, y, x2, y2 = coords
    if tool == "place_footprint":
        pcb.place_footprint("R2", "1k", x, y, pcb_path=str(path))
    elif tool == "move_footprint":
        pcb.move_footprint("R1", x, y, pcb_path=str(path))
    elif tool == "add_via":
        pcb.add_via(x, y, net=1, pcb_path=str(path))
    elif tool == "add_trace":
        pcb.add_trace(x, y, x2, y2, net=1, pcb_path=str(path))
    else:
        raise ValueError(f"no such writer {tool!r}")


def _coordinate_atoms(tool: str, data: bytes, coords) -> list[tuple[str, L.Token, float]]:
    """(what, lexer token, requested mm) for every coordinate atom ``tool`` wrote into the
    board ``data``, found by the lexer port."""
    x, y, x2, y2 = coords
    root = _root(data)
    if tool in ("place_footprint", "move_footprint"):
        ref = "R2" if tool == "place_footprint" else "R1"
        found = _footprints(root, ref)
        assert len(found) == 1, f"the board holds {len(found)} footprints {ref}"
        at = _child(found[0], b"at")
        return [(f"{ref} (at) x", at.items[1], x), (f"{ref} (at) y", at.items[2], y)]
    if tool == "add_via":
        vias = _children(root, b"via")
        assert len(vias) == 1, f"the board holds {len(vias)} vias"
        at = _child(vias[0], b"at")
        return [("via (at) x", at.items[1], x), ("via (at) y", at.items[2], y)]
    segments = _children(root, b"segment")
    assert len(segments) == 1, f"the board holds {len(segments)} tracks"
    start, end = _child(segments[0], b"start"), _child(segments[0], b"end")
    return [
        ("track (start) x", start.items[1], x),
        ("track (start) y", start.items[2], y),
        ("track (end) x", end.items[1], x2),
        ("track (end) y", end.items[2], y2),
    ]


def _kicad_nm(millimetres: float) -> set[int]:
    """The nanometres KiCad holds for a coordinate of ``millimetres`` written exactly:
    KiROUND of millimetres * 1e6, which is what parseBoardUnits computes for its text
    inside the clamp. Both majors' KiROUND, which part on a few doubles: either is KiCad's.
    Past the int range KiROUND saturates at INT_MAX - 1 or INT_MAX (INT_MIN + 1 or
    INT_MIN below), and no reading equals anything past the clamp, since parseBoardUnits
    clamps before it rounds."""
    value = millimetres * MM
    return {U.kiround(value, major=major) for major in MAJORS}


def _misread(tool: str, data: bytes, coords) -> list[str]:
    """Each coordinate atom ``tool`` wrote that KiCad, under either major, does not read as
    the nanometre requested."""
    problems = []
    for what, token, request in _coordinate_atoms(tool, data, coords):
        want = _kicad_nm(request)
        for major in MAJORS:
            try:
                got = _kicad_reads(token, major)
            except P.ParseRefusal as exc:
                problems.append(f"{what}: KiCad refuses {token.value!r} ({exc})")
                break
            if got not in want:
                problems.append(
                    f"{what}: KiCad {major} reads {token.value!r} as {got} nm"
                    f" ({U.format_internal_units(got, MM)} mm), where {request!r} mm was asked"
                )
    return problems


def _write(tool: str, version: str, coords, *, may_refuse: bool) -> bytes | None:
    """Run ``tool`` with ``coords`` on a fresh _board_with_r1 and return the board it wrote;
    None when it refused, which is allowed only when ``may_refuse`` and only with the board
    left byte-identical."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "board.kicad_pcb"
        path.write_text(_board_with_r1(version), encoding="utf-8", newline="\n")
        before = path.read_bytes()
        try:
            _call(tool, path, coords)
        except ToolError:
            if not may_refuse:
                raise
            assert path.read_bytes() == before, f"{tool}{coords} refused and changed the board"
            return None
        finally:
            _forget(path)
        return path.read_bytes()


def _millimetres(limit: float) -> st.SearchStrategy[float]:
    """A coordinate in mm within +-limit, as a caller passes one: often a whole nanometre,
    otherwise any double there (no subnormals, which builds of KiCad 9 parse differently)."""
    nm = round(limit * MM)
    return st.one_of(
        st.integers(-nm, nm).map(lambda n: n / MM),
        st.floats(-limit, limit, allow_nan=False, allow_infinity=False, allow_subnormal=False),
    )


_COORDS_IN_RANGE = st.tuples(*[_millimetres(2000.0)] * 4)
_COORDS_FAR = st.tuples(*[_millimetres(3000.0)] * 4)


@pytest.mark.parametrize("tool", _TOOLS)
@settings(max_examples=sexpr_gen.budget(4))
@example(version="k9", coords=(1.3203125, 1e-05, 10.1 + 0.2, 2.5e-07))
@example(version="k10", coords=(2000.0, -2000.0, -0.0, 1999.9999999))
@example(version="k9", coords=(12.3456785, -5e-07, 2.5e-06, 100.0000005))
@given(version=_VERSIONS, coords=_COORDS_IN_RANGE)
def test_board_writes_in_range_read_back_as_kicad_reads(tool, version, coords):
    """place_footprint, move_footprint, add_via and add_trace with coordinates within
    +-2000 mm, inside KiCad's load clamp: KiCad's reading (parseBoardUnits) of every
    coordinate atom the tool wrote is the nanometre requested, KiROUND of the request in
    nm, whatever the atom's spelling (1e-05 and 10.299999999999999 read right; their
    spelling is bug 61's). A request exactly half a nanometre between two (12.3456785 mm
    is 12345678.5 nm as a double) is KiROUND's, away from zero, not a round half to even."""
    data = _write(tool, version, coords, may_refuse=False)
    assert data is not None
    problems = _misread(tool, data, coords)
    assert not problems, f"{tool}{coords} on a {version} board:\n" + "\n".join(problems)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG57)
@pytest.mark.parametrize("tool", _TOOLS)
@settings(max_examples=sexpr_gen.budget(4))
@example(version="k9", coords=(3000.0, 10.0, 20.0, 10.0))
@given(version=_VERSIONS, coords=_COORDS_FAR)
def test_board_writes_stay_within_kicads_load_range(tool, version, coords):
    """The same four tools with coordinates within +-3000 mm, past KiCad's clamp at
    INT_MAX - 10 nm (2147.483637 mm) as well as inside it: each call either refuses with
    a ToolError and leaves the board byte-identical, or writes coordinates KiCad reads as
    requested. Writing one KiCad clamps is neither: KiCad holds the item at the clamp
    while the tool reports it where it was asked."""
    data = _write(tool, version, coords, may_refuse=True)
    if data is None:
        return
    problems = _misread(tool, data, coords)
    assert not problems, f"{tool}{coords} on a {version} board:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# what kicad-cli holds after a placement
# ---------------------------------------------------------------------------


def _cli_major() -> int:
    major = _kicad_cli_major()
    if major < 9:
        raise RuntimeError(f"cannot read the major version of kicad-cli (got {major})")
    return major


def _kicad_positions(board: Path) -> dict[str, tuple[float, float]]:
    """{reference: (x, y)} in mm, as kicad-cli's position export prints each footprint's
    position after loading ``board`` (y negated back to the board's own axis). The export
    goes to a directory made for this run, so a stale file cannot answer for it; kicad-cli
    writes none for a board it cannot load, which raises RuntimeError (a premise, never the
    defect)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        out = Path(scratch) / "positions.csv"
        result = _run_cli(
            ["pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both"]
            + ["--output", str(out), str(board)],
            check=False,
        )
        if not out.is_file():
            raise RuntimeError(
                f"kicad-cli wrote no position file (rc={result.returncode}):"
                f" {result.stderr.strip()[:400]}"
            )
        text = out.read_text(encoding="utf-8")
    return {
        row["Ref"]: (float(row["PosX"]), -float(row["PosY"]))
        for row in csv.DictReader(io.StringIO(text))
    }


def _held_problems(
    asked: dict[str, tuple[float, float]], held: dict[str, tuple[float, float]], major: int
) -> list[str]:
    """Each footprint kicad-cli does not hold where it was asked to be, to the nanometre."""
    problems = []
    for ref, (x, y) in asked.items():
        if ref not in held:
            problems.append(f"{ref} is missing from kicad-cli's position export")
            continue
        want = (U.kiround(x * MM, major=major), U.kiround(y * MM, major=major))
        hx, hy = held[ref]
        if (U.kiround(hx * MM, major=major), U.kiround(hy * MM, major=major)) != want:
            problems.append(
                f"{ref} was put at ({x!r}, {y!r}) mm; kicad-cli {major} holds it at ({hx}, {hy})"
            )
    return problems


def _placed_or_refused(board: Path, tool: str, *args) -> bool:
    """Run pcb.<tool>(*args) on ``board``: True when it wrote, False when it refused with a
    ToolError and left the board byte-identical."""
    before = board.read_bytes()
    try:
        getattr(pcb, tool)(*args, pcb_path=str(board))
    except ToolError:
        assert board.read_bytes() == before, f"{tool}{args} refused and changed the board"
        return False
    finally:
        _forget(board)
    return True


@requires_cli
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG57)
def test_kicad_reads_the_coordinate_a_placement_reports(tmp_path):
    """place_footprint at x = 3000 mm either refuses, leaving the board byte-identical, or
    kicad-cli's position export, which prints the position KiCad holds after loading the
    board, says 3000. Measured: the tool reports success and kicad-cli prints 2147.483637,
    the clamp."""
    major = _cli_major()
    board = tmp_path / "far.kicad_pcb"
    board.write_text(G.board_text([]), encoding="utf-8", newline="\n")
    if not _placed_or_refused(board, "place_footprint", "R1", "1k", 3000.0, 10.0):
        return
    problems = _held_problems({"R1": (3000.0, 10.0)}, _kicad_positions(board), major)
    assert not problems, "\n".join(problems)


@pytest.mark.nightly
@requires_cli
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG57)
@settings(max_examples=sexpr_gen.budget(25))
@example(placements=[(3000.0, 10.0)], move=(-3000.0, 5.0))
@given(
    placements=st.lists(st.tuples(_millimetres(3000.0), _millimetres(3000.0)), max_size=8),
    move=st.tuples(_millimetres(3000.0), _millimetres(3000.0)),
)
def test_nightly_kicad_reads_the_coordinates_placements_report(placements, move):
    """Generated coordinates within +-3000 mm through place_footprint (new footprints) and
    move_footprint (R1), one board and one kicad-cli run per example: every footprint a
    tool did not refuse is where kicad-cli's position export says KiCad holds it."""
    major = _cli_major()
    with tempfile.TemporaryDirectory() as work:
        board = Path(work) / "far.kicad_pcb"
        board.write_text(_board_with_r1("k9"), encoding="utf-8", newline="\n")
        asked = {"R1": (_R1_AT[0] / MM, _R1_AT[1] / MM)}
        for i, (x, y) in enumerate(placements):
            if _placed_or_refused(board, "place_footprint", f"P{i + 1}", "1k", x, y):
                asked[f"P{i + 1}"] = (x, y)
        if _placed_or_refused(board, "move_footprint", "R1", *move):
            asked["R1"] = move
        held = _kicad_positions(board)
    problems = _held_problems(asked, held, major)
    assert not problems, "\n".join(problems)
