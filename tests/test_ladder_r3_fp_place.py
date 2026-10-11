"""Rung 3, package 1: placing and turning a footprint, judged by KiCad.

Code under test: pcb.place_footprint and pcb.move_footprint (the footprint's own angle,
its pads' and texts' angles, how the footprint's angle is spelled) and the library copy
behind place_footprint (pcb._copy_lib_footprint_cst).

KiCad's rule (kicad_ref.footprint, which test_ladder_r3_judges.py checks against pcbnew):
placing a library footprint at angle r is FootprintLoad, SetPosition and Rotate( r ). Every
pad's absolute angle becomes Normalize( library angle + r ), every text's Normalize(
angle + r ), and then KeepUpright turns a keep-upright text at 180 or more half a turn and
negates its justification; footprint-relative positions and graphics stay as the library
writes them; the footprint's angle is Normalize180( r ), written by FormatAngle and left
out at 0. Turning a placed footprint to r turns every pad and text by r minus the angle it
holds (SetOrientation, or Rotate by that change).

Judges, none of them the code under test:

- kicad_ref.footprint (place, set_orientation, rotate, the writer's spellings), reading
  every file with the lexer port; comparisons follow footprint_judge's rules D1 to D3:
  KiCad's reading of the tokens, the comparison form that accepts both of KiCad's paths
  (Rotate and SetOrientation), positions exact except within two nanometres where pcbnew
  turned and re-derived them off the quarter turns;
- pcbnew placing and turning the same library footprint (pcbnew_fp_ops), per footprint;
- pcbnew reading the tool's board (a pad's footprint-relative angle);
- math: one orientation reached two ways, a composition, an identity;
- the writer's rule for the footprint's own angle atom (format( FOOTPRINT ),
  pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:1127-1131 at 9.0.8, 1231-1235 at
  10.0.7).

Stock footprints are read from the install at test time (skipped without one); the zone
owning Tag-Connect of the rung 3 plan's curated set waits for the board-coordinates slice.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import edit_gen as E
import edit_judge as J
import footprint_judge as FJ
import geom_gen as G
import pcbnew_fp_ops as OPS
import pcbnew_judge as PJ
import pytest
import sexpr_gen
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import footprint as F
from kicad_ref.eda_units import format_angle
from kicad_ref.trigo import normalize, normalize180

from mcp_server_kicad import pcb

MM = 1_000_000


_VERSIONS = st.sampled_from(("k9", "k10"))
#: The rung 3 plan's curated stock footprints without the zone-owning Tag-Connect, and
#: Texas_DSBGA-5, whose library writes its ${REFERENCE} text at -90.
CURATED = (
    "R_0805_2012Metric",
    "L_Bourns_SDR0604",
    "D_SMA-SMB_Universal_Handsoldering",
    "D_MELF-RM10_Universal_Handsoldering",
    "AO_DFN-8-1EP_5.55x5.2mm_P1.27mm_EP4.12x4.6mm",
    "ST_HLGA-6_3.76x4.72mm_P1.65mm",
    "BarrelJack_CUI_PJ-079BH_Horizontal",
    "Texas_DSBGA-5_0.8875x1.3875mm_Layout2x3_P0.5mm",
)
_DSBGA = "Texas_DSBGA-5_0.8875x1.3875mm_Layout2x3_P0.5mm"
#: The angles the rung 3 plan places the curated set at, and the two that leave it at 0.
_PLACE_ANGLES = (90.0, 180.0, 270.0, 30.0, -90.0, 45.0, 37.5)
_ZERO_ANGLES = (0.0, 360.0)
#: N14's angles, and the angle its move rows start from.
_TOKEN_ANGLES = (90.0, 180.0, 270.0, 45.0, 37.5)
_MOVE_START = 30.0


def _forget(path: Path) -> None:
    """Drop the board pcb._open_pcb_cst cached for ``path`` (a generated example's board
    lives in a temporary directory that is gone after it)."""
    pcb._BOARD_CACHE.pop(str(Path(path).resolve()), None)


def _board(path: Path, items=(), version: str = "k9") -> Path:
    path.write_text(G.board_text(items, version=version), encoding="utf-8", newline="\n")
    return path


def _tolerance(*angles: float) -> int:
    """Positions exact on quarter turns, within footprint_judge's D3 tolerance otherwise."""
    return 0 if all(a % 90 == 0 for a in angles) else FJ.ROTATION_TOLERANCE_NM


def _kicads_reading(fp: F.Footprint) -> F.Footprint:
    """What KiCad reads back from the tokens it writes for ``fp`` in the comparison form
    (rule D2): KeepUpright applied before the writing, as FOOTPRINT::Rotate applies it,
    since FormatAngle's ten digits taken before and after it part by float noise (359.90234375
    written then turned reads 179.90234379999993, turned then written 179.9023438)."""
    return F.written(F.canonical(fp))


def _place(board: Path, ref: str, at_nm, angle, pretty: Path, name: str) -> None:
    try:
        pcb.place_footprint(
            ref,
            "V",
            at_nm[0] / MM,
            at_nm[1] / MM,
            rotation=angle,
            library=str(pretty),
            footprint=name,
            pcb_path=str(board),
        )
    finally:
        _forget(board)


def _move(board: Path, ref: str, at_nm, rotation=None) -> None:
    try:
        pcb.move_footprint(
            ref, at_nm[0] / MM, at_nm[1] / MM, rotation=rotation, pcb_path=str(board)
        )
    finally:
        _forget(board)


# ---------------------------------------------------------------------------
# placing a generated library footprint
# ---------------------------------------------------------------------------

_AT = (50 * MM, 40 * MM)


def _placed(fp: E.SynFootprint, angle: float, version: str) -> bytes:
    """The board after place_footprint puts ``fp`` (from a generated .pretty) at _AT and
    ``angle`` as U1 on an empty board of ``version``."""
    with tempfile.TemporaryDirectory() as work:
        pretty = E.write_library(work, fp)
        board = _board(Path(work) / "place.kicad_pcb", version=version)
        _place(board, "U1", _AT, angle, pretty, fp.name)
        return board.read_bytes()


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(180.0), angle=90.0, version="k9")
@given(fp=E.synthetic_footprints(), angle=G.angles(), version=_VERSIONS)
def test_place_matches_kicads_placement(fp, angle, version):
    """A generated library footprint (pads at their own library angles, keep-upright and
    unlocked texts with any justification, graphics, no zone) placed by place_footprint
    at any angle: KiCad's reading of the placed footprint is KiCad's own placement,
    kicad_ref.footprint.place with Rotate, in the comparison form. A pad at 180 placed at
    90 must read at 270, where the tool used to write 90."""
    data = _placed(fp, angle, version)
    got = FJ.read(data, "U1")
    want = _kicads_reading(
        F.place(E.library_footprint(fp), got.position, angle, semantics="rotate")
    )
    problems = FJ.differences(got, want)
    assert not problems, "\n".join(problems)


@settings(max_examples=sexpr_gen.budget(4))
@given(fp=E.synthetic_footprints(), angle=G.angles(), version=_VERSIONS)
def test_place_keeps_local_geometry(fp, angle, version):
    """The same domain: the footprint sits where it was asked (KiCad's reading of its (at)
    is the requested nanometre) and every pad, text and graphic keeps the position its
    library writes, as KiCad's SetOrientation keeps every child's footprint-relative
    position (kicad_ref.footprint)."""
    data = _placed(fp, angle, version)
    got = FJ.read(data, "U1")
    library = E.library_footprint(fp)
    assert got.position == _AT, got.position
    pads, problems = FJ.pair(got.pads, library.pads, lambda p: p.number, lambda p: p.local, "pad")
    problems += [
        f"pad {a.number}: {a.local} where the library has {b.local}"
        for a, b in pads
        if a.local != b.local
    ]
    texts, found = FJ.pair(got.texts, library.texts, lambda t: t.key, lambda t: t.local, "text")
    problems += found
    problems += [
        f"text {a.key}: {a.local} where the library has {b.local}"
        for a, b in texts
        if a.local != b.local
    ]
    if sorted(got.shapes) != sorted(library.shapes):
        problems.append(f"graphics {got.shapes} where the library has {library.shapes}")
    assert not problems, "\n".join(problems)


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(180.0), angle=90.0, version="k9")
@given(fp=E.synthetic_footprints(), angle=G.angles(), version=_VERSIONS)
def test_place_at_r_equals_place_at_0_then_rotate_to_r(fp, angle, version):
    """Placing at r and placing at 0 then moving to rotation r reach one orientation from
    one library state, so KiCad reads the two footprints alike (comparison form)."""
    with tempfile.TemporaryDirectory() as work:
        pretty = E.write_library(work, fp)
        direct = _board(Path(work) / "direct.kicad_pcb", version=version)
        _place(direct, "U1", _AT, angle, pretty, fp.name)
        two_step = _board(Path(work) / "two_step.kicad_pcb", version=version)
        _place(two_step, "U1", _AT, 0, pretty, fp.name)
        _move(two_step, "U1", _AT, rotation=angle)
        problems = FJ.differences(
            FJ.read(direct.read_bytes(), "U1"), FJ.read(two_step.read_bytes(), "U1")
        )
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# turning a footprint KiCad placed
# ---------------------------------------------------------------------------


def _start_board(work: Path, fp: E.SynFootprint, theta0: float, version: str) -> Path:
    """A board holding ``fp`` as KiCad holds it at ``theta0`` (U1) and a second footprint
    (U2) the edit must leave alone."""
    items = [
        E.board_footprint_text(fp, "U1", _AT, theta0),
        E.board_footprint_text(E.footprint_with_pad_at(45.0), "U2", (90 * MM, 40 * MM), 30.0),
    ]
    return _board(work / "start.kicad_pcb", items, version)


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(180.0), theta0=0.0, angle=90.0, version="k9")
@given(fp=E.synthetic_footprints(), theta0=G.angles(), angle=G.angles(), version=_VERSIONS)
def test_move_rotation_matches_kicads_rotation(fp, theta0, angle, version):
    """A footprint as KiCad holds it at theta0 (pads at theta0 plus their library angles,
    texts turned), moved by move_footprint to rotation r: KiCad reads it as its own
    SetOrientation( r ) of the footprint it read, in the comparison form (so Rotate's
    KeepUpright is accepted too)."""
    with tempfile.TemporaryDirectory() as work:
        board = _start_board(Path(work), fp, theta0, version)
        start = FJ.read(board.read_bytes(), "U1")
        _move(board, "U1", _AT, rotation=angle)
        got = FJ.read(board.read_bytes(), "U1")
    problems = FJ.differences(got, _kicads_reading(F.set_orientation(start, angle)))
    assert not problems, "\n".join(problems)


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(180.0), theta0=37.5, to=(60 * MM, 70 * MM), version="k10")
@given(
    fp=E.synthetic_footprints(),
    theta0=G.angles(),
    to=st.tuples(G.nm_coordinates(500 * MM), G.nm_coordinates(500 * MM)),
    version=_VERSIONS,
)
def test_move_translation_changes_only_the_footprint_at(fp, theta0, to, version):
    """No zone, no rotation: a move changes the footprint's (at) x and y atoms and nothing
    else (edit_judge.atom_changes, over the whole board), the atoms read as the requested
    nanometres, and every child reads as before (FOOTPRINT::SetPosition moves nothing
    footprint-relative)."""
    with tempfile.TemporaryDirectory() as work:
        board = _start_board(Path(work), fp, theta0, version)
        before = board.read_bytes()
        _move(board, "U1", to)
        after = board.read_bytes()
    changes = J.atom_changes(before, after)
    at_path = _at_path(before, "U1")
    assert all(c.path == at_path and c.index in (1, 2) for c in changes), changes
    moved = FJ.read(after, "U1")
    assert moved.position == to, (moved.position, to)
    problems = FJ.differences(moved, FJ.read(before, "U1"), position=False, shapes=True)
    assert not problems, "\n".join(problems)


def _at_path(data: bytes, reference: str) -> J.Path:
    """edit_judge's path of footprint ``reference``'s own (at) list in the board ``data``."""
    board = FJ.root(data)
    for index, item in enumerate(board.items):
        if F.head(item) == b"footprint" and FJ.reference_of(item) == reference:
            at = next(k for k, c in enumerate(item.items) if F.head(c) == b"at")
            return ((0, "kicad_pcb"), (index, "footprint"), (at, "at"))
    raise RuntimeError(f"no footprint {reference} on the board")


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(180.0), theta0=30.0, angle=200.0, version="k9")
@example(fp=E.footprint_with_pad_at(-90.0), theta0=-90.0, angle=0.0, version="k10")
@example(fp=E.footprint_with_pad_at(180.0), theta0=-359.9, angle=-359.9, version="k9")
@given(fp=E.synthetic_footprints(), theta0=G.angles(), angle=G.angles(), version=_VERSIONS)
def test_move_rotation_changes_only_angles_inside_the_footprint(fp, theta0, angle, version):
    """No zone: a move that turns the footprint where it stands changes nothing outside it
    (edit_judge.assert_confined, U2 included) and, inside it, only the angle atom of an
    (at), the footprint's, a pad's or a text's, set, added or left out, and the words of a
    text's (justify ...), which KeepUpright negates in place. A turn to the angle KiCad
    reads the footprint at already (Normalize180 of the requested one) leaves the board
    byte-identical, and so can a turn smaller than any ten-digit spelling shows (theta0
    1.2527619600573416, written 1.25276196, turned to 1.2527619600573416), whose reading
    the tests above judge."""
    with tempfile.TemporaryDirectory() as work:
        board = _start_board(Path(work), fp, theta0, version)
        before = board.read_bytes()
        _move(board, "U1", _AT, rotation=angle)
        after = board.read_bytes()
    if normalize180(angle) == FJ.read(before, "U1").orientation:
        assert after == before, "a turn to the angle the footprint holds changed the board"
        return
    if after == before:
        return
    footprint = _at_path(before, "U1")[:2]
    edits = J.assert_confined(before, after, footprint)
    stray = [
        e.describe()
        for e in edits
        if not (e.path[-1][1] == "at" and e.index == 3)
        and not (e.path[-1][1] == "justify" and e.op == "atom" and e.index >= 1)
    ]
    assert not stray, stray


_HALF = G.half_degree_angles()


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(180.0), theta0=90.0, a=180.0, b=270.0, version="k9")
@example(fp=E.footprint_with_pad_at(-90.0), theta0=0.0, a=270.0, b=0.0, version="k10")
@given(fp=E.synthetic_footprints(), theta0=_HALF, a=_HALF, b=_HALF, version=_VERSIONS)
def test_rotations_compose(fp, theta0, a, b, version):
    """From a footprint as KiCad holds it at theta0 (angles that are multiples of half a
    degree, so the arithmetic is exact): rotation a and then b reads, in the comparison
    form, as rotation b straight away; and a rotation to the angle the footprint already
    holds changes nothing KiCad reads."""
    with tempfile.TemporaryDirectory() as work:
        two = _start_board(Path(work), fp, theta0, version)
        start = FJ.read(two.read_bytes(), "U1")
        one = Path(work) / "one.kicad_pcb"
        same = Path(work) / "same.kicad_pcb"
        shutil.copyfile(two, one)
        shutil.copyfile(two, same)
        _move(two, "U1", _AT, rotation=a)
        _move(two, "U1", _AT, rotation=b)
        _move(one, "U1", _AT, rotation=b)
        _move(same, "U1", _AT, rotation=start.orientation)
        problems = FJ.differences(FJ.read(two.read_bytes(), "U1"), FJ.read(one.read_bytes(), "U1"))
        problems += [
            f"turned to the angle it held: {p}"
            for p in FJ.differences(FJ.read(same.read_bytes(), "U1"), start)
        ]
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# the spelling of the footprint's own angle
# ---------------------------------------------------------------------------


def _angle_token_after(tool: str, angle: float, version: str) -> str | None:
    """The footprint's (at) angle atom after place_footprint puts a pad-less marker at
    ``angle``, or after move_footprint turns a footprint KiCad holds at 0 to ``angle``."""
    with tempfile.TemporaryDirectory() as work:
        if tool == "place_footprint":
            board = _board(Path(work) / "spell.kicad_pcb", version=version)
            try:
                pcb.place_footprint("U1", "V", 50, 40, rotation=angle, pcb_path=str(board))
            finally:
                _forget(board)
        else:
            board = _start_board(Path(work), E.footprint_with_pad_at(0.0), 0.0, version)
            _move(board, "U1", _AT, rotation=angle)
        return FJ.footprint_angle_token(FJ.footprint_group(board.read_bytes(), "U1"))


#: Decimals with at most four places in (-180, 180], 0 left out: Python's shortest text for
#: them, which the tools write today, is the text FormatAngle writes, so their spelling
#: cannot fail for bug 61's float noise (rung 3's D4).
_IN_RANGE = st.one_of(
    st.integers(-1799, 1800).map(lambda k: k / 10),
    st.integers(-1_799_999, 1_800_000).map(lambda k: k / 10_000),
).filter(lambda a: a != 0)


@pytest.mark.parametrize("tool", ["place_footprint", "move_footprint"])
@settings(max_examples=sexpr_gen.budget(4))
@example(angle=90.0, version="k9")
@example(angle=-37.5, version="k10")
@example(angle=180.0, version="k9")
@given(angle=_IN_RANGE, version=_VERSIONS)
def test_footprint_angle_in_range_is_spelled_as_kicad_writes(tool, angle, version):
    """An angle already in (-180, 180] and not 0: the footprint's (at) angle atom is
    FormatAngle( angle ), what format( FOOTPRINT ) writes for it."""
    assert _angle_token_after(tool, angle, version) == format_angle(angle)


def _out_of_range(angle: float) -> bool:
    return normalize180(angle) != angle or normalize180(angle) == 0


@pytest.mark.parametrize("tool", ["place_footprint", "move_footprint"])
@settings(max_examples=sexpr_gen.budget(4))
@example(angle=270.0, version="k9")
@example(angle=0.0, version="k10")
@example(angle=-180.0, version="k9")
@example(angle=360.0, version="k9")
@example(angle=540.0, version="k10")
@example(angle=-360.0, version="k9")
@given(angle=G.angles().filter(_out_of_range), version=_VERSIONS)
def test_footprint_angle_is_spelled_as_kicad_writes(tool, angle, version):
    """An angle Normalize180 changes, or one it takes to 0: the footprint's (at) holds no
    angle atom when the orientation is 0 and FormatAngle( Normalize180( angle ) )
    otherwise, as format( FOOTPRINT ) writes the orientation FOOTPRINT::SetOrientation
    stores. A 360 or -360 on a footprint at 0 leaves it with none."""
    stored = normalize180(angle)
    want = None if stored == 0 else format_angle(stored)
    assert _angle_token_after(tool, angle, version) == want


# ---------------------------------------------------------------------------
# the pad-less marker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("angle", [90.0, 270.0])
def test_marker_place_turns_its_texts(tmp_path, angle):
    """place_footprint without a library places a marker carrying Reference and Value.
    Placed at an angle its texts turn as KiCad's Rotate turns them, from the marker as
    placed at 0 (kicad_ref.footprint.place), in the comparison form: at 90 both read at 90,
    at 270 at 90 again after KeepUpright. The tool used to leave them at 0."""
    board = _board(tmp_path / "marker.kicad_pcb")
    try:
        pcb.place_footprint("U1", "V", 20, 20, pcb_path=str(board))
        pcb.place_footprint("U2", "V", 60, 20, rotation=angle, pcb_path=str(board))
    finally:
        _forget(board)
    data = board.read_bytes()
    library = FJ.read(data, "U1")._replace(position=(0, 0))
    got = FJ.read(data, "U2")
    want = _kicads_reading(F.place(library, got.position, angle, semantics="rotate"))
    problems = FJ.differences(got, want)
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# library angles, as KiCad loads them
# ---------------------------------------------------------------------------


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(-90.0, rotation_text=-90.0), version="k9")
@given(fp=E.synthetic_footprints(), version=_VERSIONS)
def test_place_writes_library_angles_as_kicad_loads_them(fp, version):
    """A library whose pads and texts are written at any multiple of half a degree within
    +-720, -90 among them as KiCad's stock libraries write it, placed at 0: every pad and
    text angle atom is what KiCad writes after FootprintLoad, the loader's Normalize( angle )
    through the writer (a pad's left out at 0, a text's always written). The tool used to
    copy the library's own atom, so -90 stayed -90 where KiCad writes 270."""
    data = _placed(fp, 0.0, version)
    group = FJ.footprint_group(data, "U1")
    got = FJ.tokens(group)
    want = FJ.expected_tokens(F.place(E.library_footprint(fp), _AT, 0.0, semantics="rotate"))
    problems = FJ.token_problems(got, want, footprint=False)
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# against pcbnew
# ---------------------------------------------------------------------------


def _curated_paths() -> list[Path]:
    return [E.stock_footprint(name) for name in CURATED]


def _slot(index: int) -> tuple[int, int]:
    return (30 * MM * (1 + index), 50 * MM)


class _Runs:
    """Everything the pcbnew tests compare, made once per worker: for each angle a board
    the tool placed the curated set on and the board pcbnew placed it on; pcbnew's reading
    of the tool's boards; and the move rows of N14, from a board pcbnew placed at
    _MOVE_START, turned by the tool and by pcbnew."""

    def __init__(self, work: Path):
        self.major = PJ.major()
        self.paths = _curated_paths()
        empty = _board(work / "empty.kicad_pcb")
        self.tool: dict[float, bytes] = {}
        self.kicad: dict[float, bytes] = {}
        jobs = []
        kicad_out = {}
        tool_boards = {}
        for angle in (*_ZERO_ANGLES, *_PLACE_ANGLES):
            board = _board(work / f"tool_{angle}.kicad_pcb")
            for index, path in enumerate(self.paths):
                _place(board, f"C{index}", _slot(index), angle, path.parent, path.stem)
            tool_boards[angle] = board
            out = work / f"kicad_{angle}.kicad_pcb"
            kicad_out[angle] = out
            items = [
                OPS.place_item(path.parent, path.stem, f"C{index}", _slot(index), ("rotate", angle))
                for index, path in enumerate(self.paths)
            ]
            jobs.append(OPS.place_job(empty, items, out))
        start = work / "move_start.kicad_pcb"
        items = [
            OPS.place_item(
                path.parent, path.stem, f"C{index}", _slot(index), ("rotate", _MOVE_START)
            )
            for index, path in enumerate(self.paths)
        ]
        jobs.append(OPS.place_job(empty, items, start))
        moved_out = {}
        for angle in _TOKEN_ANGLES:
            out = work / f"kicad_move_{angle}.kicad_pcb"
            moved_out[angle] = out
            moves = [
                OPS.move_item(f"C{index}", _slot(index), ("rotate_to", angle))
                for index in range(len(self.paths))
            ]
            jobs.append(OPS.move_job(start, moves, out))
        describe_at = {angle: len(jobs) + k for k, angle in enumerate((90.0, 45.0))}
        jobs += [OPS.describe_job(tool_boards[angle]) for angle in describe_at]
        results = OPS.run(jobs)
        self.tool = {angle: board.read_bytes() for angle, board in tool_boards.items()}
        self.kicad = {angle: out.read_bytes() for angle, out in kicad_out.items()}
        self.described = {angle: results[k] for angle, k in describe_at.items()}
        self.kicad_moved = {angle: out.read_bytes() for angle, out in moved_out.items()}
        self.tool_moved = {}
        for angle in _TOKEN_ANGLES:
            board = work / f"tool_move_{angle}.kicad_pcb"
            shutil.copyfile(start, board)
            for index in range(len(self.paths)):
                _move(board, f"C{index}", _slot(index), rotation=angle)
            self.tool_moved[angle] = board.read_bytes()


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    return _Runs(tmp_path_factory.mktemp("fp_place"))


def _placement_problems(runs: _Runs, angles) -> list[str]:
    problems = []
    for angle in angles:
        for index, path in enumerate(runs.paths):
            ref = f"C{index}"
            got = FJ.read(runs.tool[angle], ref, major=runs.major)
            want = FJ.read(runs.kicad[angle], ref, major=runs.major)
            found = FJ.differences(got, want, tolerance_nm=_tolerance(angle))
            problems += [f"{path.stem} at {angle}: {p}" for p in found]
    return problems


def test_place_matches_pcbnew(runs):
    """One item for the curated set at 90, 180, 270, 30, -90, 45 and 37.5 degrees: each
    footprint place_footprint wrote reads, in the comparison form, as the one pcbnew
    writes after FootprintLoad, SetPosition and Rotate (orientation, side, every pad's
    angle and position, every text's style, layer and position; positions within two
    nanometres off the quarter turns)."""
    problems = _placement_problems(runs, _PLACE_ANGLES)
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


def test_place_at_zero_matches_pcbnew(runs):
    """The same set at 0 and at 360 degrees, which leave every child where the library
    has it: the tool's footprints read as pcbnew's (the passing sibling of the test
    above)."""
    problems = _placement_problems(runs, _ZERO_ANGLES)
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@example(picks=[(0, 90.0)])
@given(st.lists(st.tuples(st.integers(0, 10**9), G.angles()), min_size=1, max_size=8))
def test_nightly_place_matches_pcbnew(picks):
    """Sampled stock footprints without a zone at generated angles, one pcbnew run per
    example: the tool's placement reads as pcbnew's. The explicit example is the first of
    the curated set's BarrelJack at 90 (pads at 180 in its library)."""
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    stock = E.stock_footprints_for_turns()
    if not stock:
        pytest.skip("no stock footprint libraries")
    barrel = E.stock_footprint("BarrelJack_CUI_PJ-079BH_Horizontal")
    chosen = [(barrel if k == 0 else stock[k % len(stock)], angle) for k, angle in picks]
    problems = []
    with tempfile.TemporaryDirectory() as work:
        empty = _board(Path(work) / "empty.kicad_pcb")
        tool = _board(Path(work) / "tool.kicad_pcb")
        out = Path(work) / "kicad.kicad_pcb"
        items = []
        for index, (path, angle) in enumerate(chosen):
            _place(tool, f"C{index}", _slot(index), angle, path.parent, path.stem)
            items.append(
                OPS.place_item(path.parent, path.stem, f"C{index}", _slot(index), ("rotate", angle))
            )
        OPS.run([OPS.place_job(empty, items, out)])
        for index, (path, angle) in enumerate(chosen):
            got = FJ.read(tool.read_bytes(), f"C{index}", major=PJ.major())
            want = FJ.read(out.read_bytes(), f"C{index}", major=PJ.major())
            problems += [
                f"{path.stem} at {angle}: {p}"
                for p in FJ.differences(got, want, tolerance_nm=_tolerance(angle))
            ]
    assert not problems, "\n".join(problems[:40])


#: How far apart two pad angles may be and still be one angle, in degrees. A pad angle
#: is written with FormatAngle's ten significant digits, so a footprint-relative angle
#: pcbnew derives by subtracting two written angles carries up to about 1e-7 degrees of
#: that rounding; anything a defect moves is a whole fraction of a turn.
_ANGLE_TOLERANCE = 1e-6


def _same_angle(a: float, b: float) -> bool:
    gap = abs(normalize(a) - normalize(b))
    return min(gap, 360.0 - gap) <= _ANGLE_TOLERANCE


def test_pcbnew_reads_pads_at_their_library_angle(runs):
    """One item: the curated set placed by place_footprint at 90 and 45 degrees, read by
    pcbnew: every pad's footprint-relative orientation (PAD::GetFPRelativeOrientation) is
    the angle its library gives it, as KiCad reads the library file. The tool used to
    write each pad at the footprint's angle, so a library pad at 180 read at 0."""
    problems = []
    for angle, found in runs.described.items():
        for index, path in enumerate(runs.paths):
            library = FJ.read_library(path.read_bytes())
            held = found[f"C{index}"]["pads"]
            pairs, unpaired = FJ.pair(
                [(p.number, p.local, p.angle) for p in library.pads],
                [(n, None, rel) for n, _, _, rel in held],
                lambda p: p[0],
                lambda p: None,
                "pad",
                lambda a, b: abs(a[2] - normalize(b[2])),
            )
            problems += [f"{path.stem} at {angle}: {u}" for u in unpaired]
            for (number, _, want), (_, _, rel) in pairs:
                if not _same_angle(rel, want):
                    problems.append(
                        f"{path.stem} at {angle}: pad {number!r} reads at {rel!r} relative to"
                        f" the footprint, its library angle is {want!r}"
                    )
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


def _dsbga_premise() -> None:
    """Premise of N12: the installed DSBGA-5 still writes a text at -90 (RuntimeError,
    never AssertionError, when a release changed it)."""
    group = FJ.root(E.stock_footprint(_DSBGA).read_bytes())
    atoms = [FJ.tokens(group)["texts"][k][1][0] for k in range(len(F.text_groups(group)))]
    if "-90" not in atoms:
        raise RuntimeError(f"the installed {_DSBGA} writes its texts at {atoms}, none at -90")


def test_place_writes_library_angles_as_pcbnew_saves_them(runs):
    """N12: Texas_DSBGA-5's library writes its ${REFERENCE} text at -90. The curated set
    placed at 0 by the tool: every pad and text angle atom is the one pcbnew writes after
    FootprintLoad and SaveBoard, 270 for that text."""
    _dsbga_premise()
    problems = []
    for index, path in enumerate(runs.paths):
        ref = f"C{index}"
        found = FJ.token_differences(
            FJ.footprint_group(runs.tool[0.0], ref),
            FJ.footprint_group(runs.kicad[0.0], ref),
            footprint=False,
        )
        problems += [f"{path.stem}: {p}" for p in found]
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


@pytest.mark.parametrize("operation", ["place", "move"])
@pytest.mark.parametrize("part", ["pads", "texts"])
def test_place_and_move_match_pcbnew_rotate_token_for_token(runs, operation, part):
    """N14, the rows that turn without flipping: the curated set (BarrelJack among it, a
    keep-upright Reference justified left) placed at 90, 180, 270, 45 and 37.5 degrees,
    and moved there from a placement at 30, by the tool and by pcbnew's FOOTPRINT::Rotate:
    every pad's angle atom (part "pads"), or every text's angle atom, unlocked word and
    justify words (part "texts"), equals pcbnew's exactly, not in the comparison form;
    positions as KiCad reads them, within two nanometres off the quarter turns. This is
    what decides Rotate over SetOrientation: they differ only for keep-upright texts that
    land at 180 or more. The footprint's own angle atom is bug 53's, judged above."""
    tool = runs.tool if operation == "place" else runs.tool_moved
    kicad = runs.kicad if operation == "place" else runs.kicad_moved
    problems = []
    for angle in _TOKEN_ANGLES:
        for index, path in enumerate(runs.paths):
            ref = f"C{index}"
            got = FJ.tokens(FJ.footprint_group(tool[angle], ref))
            want = FJ.tokens(FJ.footprint_group(kicad[angle], ref))
            other = "texts" if part == "pads" else "pads"
            got[other], want[other] = [], []
            tolerance = _tolerance(angle, _MOVE_START if operation == "move" else 0.0)
            found = FJ.token_problems(got, want, tolerance_nm=tolerance, footprint=False)
            problems += [f"{path.stem} {operation} to {angle}: {p}" for p in found]
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


def test_curated_set_exercises_what_the_pcbnew_tests_need():
    """The premise the pcbnew tests above rest on: the installed curated set holds a pad
    with a library angle of its own, a keep-upright text with a horizontal justification
    and a text written at -90. Raises RuntimeError, never AssertionError, when the install
    no longer does."""
    libraries = [FJ.read_library(path.read_bytes()) for path in _curated_paths()]
    if not any(p.angle != 0 for fp in libraries for p in fp.pads):
        raise RuntimeError("no curated pad has a library angle of its own")
    if not any(t.style.keep_upright and t.style.h != 0 for fp in libraries for t in fp.texts):
        raise RuntimeError("no curated keep-upright text carries a justification")
    _dsbga_premise()
