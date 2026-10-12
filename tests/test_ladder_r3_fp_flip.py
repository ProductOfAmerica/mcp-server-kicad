"""Rung 3, package 2: flipping a footprint and choosing its side, judged by KiCad.

Code under test: pcb.move_footprint, whose layer naming the other side flips the
footprint; pcb.place_footprint, whose layer B.Cu places it flipped; their refusal of a
side other than F.Cu or B.Cu; and the flip's transform behind both.

KiCad's rule (kicad_ref.footprint.flip with kicad_ref.board_layers, which
test_ladder_r3_judges.py checks against pcbnew): a footprint changes side by
FOOTPRINT::Flip about its own position, LEFT_RIGHT, which is what SetLayerAndFlip runs.
Its layer goes to the other side and its angle A to Normalize180( Normalize180( -A ) +
180 ); every local y is negated; a pad's angle P becomes Normalize( Normalize( -P ) + d ),
d being that 180 as the doubles compute it, its shape offset and trapezoid delta mirror
in y, its top and bottom chamfers swap, its primitives mirror and its layers go to their
opposites; a text's angle T becomes Normalize( ( 180 - T ) + d ) and then KeepUpright,
its layer goes to its opposite and its mirror toggles only on a side specific layer;
graphics mirror and change layer; a 3D model is left as it is. Which layer is whose
opposite is the board's: the F and B layers, the user layers typed front and back that
KiCad pairs, and inner copper, which KiCad 9 leaves in place on a board it read and
KiCad 10 mirrors through the inner stack. Placing on the back is FootprintLoad,
SetPosition, the flip, then the turn to the angle asked for.

Judges, none of them the code under test:

- kicad_ref.footprint (flip, place, the writer's spellings) with kicad_ref.board_layers,
  reading every file with the lexer port; comparisons follow footprint_judge's rules D1 to
  D3 (KiCad's reading; the comparison form; positions exact on quarter turns, within two
  nanometres where pcbnew turned them; graphics by geometry) and leave out the mandatory
  fields a file does not write (footprint_judge's written_fields_only);
- pcbnew flipping the same footprint (pcbnew_fp_ops), per footprint, and reading the
  tool's board;
- math: an involution, a composition, KiCad's group law;
- KiCad's source for what a footprint's side can be (FOOTPRINT::SetLayerAndFlip asserts
  F.Cu or B.Cu, pcbnew/footprint.cpp:2482-2488 at 9.0.8, 2921-2927 at 10.0.7; the parser
  reads any other footprint layer as F.Cu, pcbnew/pcb_io/kicad_sexpr/
  pcb_io_kicad_sexpr_parser.cpp:4510-4519 at 9.0.8, 5198-5207 at 10.0.7), and the
  invariant that a refused edit leaves the file byte-identical.

Generated tests build their boards in temporary directories (rung 3's D17); the
hand-picked ones write into tmp_path, where the autouse oracle loads every board with
kicad-cli. Footprints owning a zone are the board-coordinates slice's subject and are
judged here only by the refusal invariant.
"""

from __future__ import annotations

import base64
import shutil
import struct
import tempfile
import zlib
from pathlib import Path

import edit_gen as E
import footprint_judge as FJ
import geom_gen as G
import pcbnew_fp_ops as OPS
import pcbnew_judge as PJ
import pytest
import sexpr_gen
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import board_layers as BL
from kicad_ref import footprint as F
from kicad_ref import pcb_text as T
from kicad_ref.parse_units import parse_board_units
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import pcb

MM = 1_000_000


_VERSIONS = st.sampled_from(("k9", "k10"))
_HALF = G.half_degree_angles()
#: Written angles in (0, 180] that are multiples of half a degree (rung 3's D4: the
#: double-flip byte test keeps clear of bug 53's spellings).
_UPPER_HALF = st.integers(1, 360).map(lambda k: k / 2)
#: Written angles 0 and in (-180, 0), multiples of half a degree: bug 53's domain.
_LOWER_HALF = st.integers(-359, 0).map(lambda k: k / 2)
_AT = (50 * MM, 40 * MM)
_NEIGHBOUR = (90 * MM, 40 * MM)
#: The user layers of the boards whose flips cross user layers: a front and back pair
#: KiCad matches by the user names' text after the first '.', and a plain user layer.
USER_LAYERS = (
    ("User.1", "front", "F.Assy"),
    ("User.2", "back", "B.Assy"),
    ("User.3", "user", None),
)
#: Consecutive front and back user layers written without user names, which KiCad pairs
#: as well (kicad_ref.board_layers; measured with pcbnew 9.0.8).
UNNAMED_PAIR = (("User.1", "front", None), ("User.2", "back", None), ("User.3", "user", None))


def _major(version: str) -> int:
    """The KiCad that reads a board of ``version``."""
    return 10 if version == "k10" else 9


def _forget(path: Path) -> None:
    """Drop the board pcb._open_pcb_cst cached for ``path``."""
    pcb._BOARD_CACHE.pop(str(Path(path).resolve()), None)


def _board(path: Path, items=(), version: str = "k9", *, copper: int = 2, users=()) -> Path:
    path.write_text(
        E.layered_board_text(items, version=version, copper=copper, users=users),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _move(board: Path, ref: str, at_nm, *, rotation=None, layer: str = "") -> None:
    try:
        pcb.move_footprint(
            ref, at_nm[0] / MM, at_nm[1] / MM, rotation=rotation, layer=layer, pcb_path=str(board)
        )
    finally:
        _forget(board)


def _flip(board: Path, ref: str = "U1", at_nm=_AT, side: str = "B.Cu") -> None:
    """move_footprint to the footprint's own position on ``side``."""
    _move(board, ref, at_nm, layer=side)


def _performed_flip(board: Path, ref: str = "U1", at_nm=_AT, side: str = "B.Cu") -> None:
    """_flip, where a refusal is the defect: KiCad flips the footprint."""
    try:
        _flip(board, ref, at_nm, side)
    except ToolError as exc:
        raise AssertionError(f"move_footprint refused a flip KiCad performs: {exc}") from exc


def _place(board: Path, ref: str, at_nm, angle, pretty: Path, name: str, layer="F.Cu") -> None:
    try:
        pcb.place_footprint(
            ref,
            "V",
            at_nm[0] / MM,
            at_nm[1] / MM,
            rotation=angle,
            layer=layer,
            library=str(pretty),
            footprint=name,
            pcb_path=str(board),
        )
    finally:
        _forget(board)


def _start_board(
    work: Path, fp: E.SynFootprint, theta0: float, version: str, *, users=(), copper: int = 2
) -> Path:
    """A board holding ``fp`` as KiCad holds it placed at ``theta0`` (U1) and a second
    footprint (U2) the edit must leave alone."""
    items = [
        E.board_footprint_text(fp, "U1", _AT, theta0),
        E.board_footprint_text(E.footprint_with_pad_at(45.0), "U2", _NEIGHBOUR, 30.0),
    ]
    work.mkdir(parents=True, exist_ok=True)
    return _board(work / "start.kicad_pcb", items, version, copper=copper, users=users)


def _kicads_flip(data: bytes, start: F.Footprint, version: str) -> F.Footprint:
    """KiCad's flip of ``start`` on the board ``data`` (its layer table)."""
    return F.board_flip(start, FJ.layer_table(data), major=_major(version))


def _kicads_reading(fp: F.Footprint) -> F.Footprint:
    """What KiCad reads back from the tokens it writes for ``fp``, in the comparison form
    (test_ladder_r3_fp_place's helper of the same name)."""
    return F.written(F.canonical(fp))


def _flipped(fp, theta0, version, *, users=()) -> tuple[bytes, bytes]:
    """(before, after): the board holding ``fp`` at ``theta0`` and the board after
    move_footprint flips U1 to the back where it stands."""
    with tempfile.TemporaryDirectory() as work:
        board = _start_board(Path(work), fp, theta0, version, users=users)
        before = board.read_bytes()
        _flip(board)
        return before, board.read_bytes()


def _flip_problems(before: bytes, after: bytes, version: str, **compare) -> list[str]:
    """footprint_judge.differences between U1 after the tool's flip and KiCad's flip of
    U1 as it stood, leaving out the fields neither file writes."""
    major = _major(version)
    want = _kicads_reading(_kicads_flip(before, FJ.read(before, "U1", major=major), version))
    got = FJ.read(after, "U1", major=major)
    return FJ.differences(
        got,
        want,
        written_fields_only=True,
        copper_count=FJ.copper_count(after),
        **compare,
    )


# ---------------------------------------------------------------------------
# the flip, against KiCad's
# ---------------------------------------------------------------------------


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(45.0), theta0=30.0, version="k9")
@given(fp=E.synthetic_footprints(text_layers=E.SIDED_TEXT_LAYERS), theta0=_HALF, version=_VERSIONS)
def test_flip_mirrors_positions_layers_and_text_mirror(fp, theta0, version):
    """A footprint KiCad holds at any angle that is a multiple of half a degree (so its
    arithmetic is exact), with texts on side specific layers only, moved to the back where
    it stands: its side, its angle, every pad's local position and layers, every text's
    local position, layer and mirror flag, and every graphic's geometry and layer read as
    KiCad's flip of it (pad and text angles are judged below, bug 14's)."""
    before, after = _flipped(fp, theta0, version)
    problems = _flip_problems(before, after, version, angles=False, shapes=True)
    assert not problems, "\n".join(problems)


_UNSIDED = (*E.UNSIDED_TEXT_LAYERS, "User.3")
_CMTS_TEXT = E.SynText(
    "fp_text", "user", "NOTE", (MM, MM), 0.0, True, T.CENTER, T.CENTER, False, "Cmts.User"
)
#: A footprint whose Reference is on F.SilkS and whose fp_text, unmirrored, is on
#: Cmts.User.
_CMTS_EXAMPLE = E.footprint_with_pad_at(0.0)._replace(
    texts=(E.footprint_with_pad_at(0.0).texts[0], _CMTS_TEXT)
)


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=_CMTS_EXAMPLE, theta0=0.0, version="k9")
@given(fp=E.synthetic_footprints(text_layers=_UNSIDED), theta0=_HALF, version=_VERSIONS)
def test_flip_leaves_unsided_text_mirrors_alone(fp, theta0, version):
    """Texts on Cmts.User, Dwgs.User, Eco1.User, Eco2.User, Margin and a plain user layer
    keep their layer and their mirror flag through a flip: PCB_TEXT::Flip toggles the
    mirror only on a side specific layer (BOARD_ITEM::IsSideSpecific). The tool toggled
    every text's."""
    before, after = _flipped(fp, theta0, version, users=(("User.3", "user", None),))
    problems = _flip_problems(before, after, version, angles=False)
    assert not problems, "\n".join(problems)


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(45.0), theta0=0.0, version="k9")
@given(
    fp=E.synthetic_footprints(text_layers=E.SIDED_TEXT_LAYERS), theta0=G.angles(), version=_VERSIONS
)
def test_flip_turns_pad_and_text_angles_as_kicad_does(fp, theta0, version):
    """A footprint KiCad holds at any angle, flipped: every pad's absolute angle and every
    text's angle, keep-upright flag and justification read as KiCad's flip of it, in the
    comparison form. A pad at 45 on a footprint at 0 lands at 135; the tool left it at 45."""
    before, after = _flipped(fp, theta0, version)
    problems = _flip_problems(before, after, version, shapes=True)
    assert not problems, "\n".join(problems)


_DETAILED = E.SynFootprint(
    "Syn",
    (
        E.SynPad("1", (-MM, 0), 0.0, chamfer=("top_left",)),
        E.SynPad("2", (MM, 0), 0.0, delta=(0, 200_000)),
        E.SynPad("3", (0, MM), 0.0, offset=(100_000, 200_000)),
    ),
    E.footprint_with_pad_at(0.0).texts,
)


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=_DETAILED, theta0=0.0, version="k9")
@given(
    fp=E.synthetic_footprints(pad_details=True, text_layers=E.SIDED_TEXT_LAYERS),
    theta0=_HALF,
    version=_VERSIONS,
)
def test_flip_mirrors_pad_shape_details(fp, theta0, version):
    """Pads with chamfered corners, a trapezoid delta, a shape offset or a layer set of
    their own, flipped: each pad's chamfered corners, delta, offset and layers are
    PAD::Flip's (top and bottom corners swapped, the delta and offset mirrored in y)."""
    before, after = _flipped(fp, theta0, version)
    problems = _flip_problems(before, after, version, angles=False, details=True)
    assert not problems, "\n".join(problems)


_CUSTOM = E.SynFootprint(
    "Syn",
    (
        E.SynPad(
            "1",
            (-MM, 0),
            0.0,
            primitives=(
                ("gr_poly", ((0, 0), (200_000, 0), (0, 150_000))),
                ("gr_arc", (100_000, 0), (70_711, 70_711), (0, 100_000)),
            ),
        ),
        E.SynPad("2", (MM, 0), 0.0),
    ),
    E.footprint_with_pad_at(0.0).texts,
)


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=_CUSTOM, theta0=30.0, version="k9")
@given(
    fp=E.synthetic_footprints(primitives=True, text_layers=E.SIDED_TEXT_LAYERS),
    theta0=_HALF,
    version=_VERSIONS,
)
def test_flip_mirrors_custom_pad_primitives(fp, theta0, version):
    """A footprint with a custom pad, flipped: the flip happens (KiCad performs it) and
    every primitive is PAD::FlipPrimitives' (mirrored in the pad's own y)."""
    with tempfile.TemporaryDirectory() as work:
        board = _start_board(Path(work), fp, theta0, version)
        before = board.read_bytes()
        _performed_flip(board)
        after = board.read_bytes()
    problems = _flip_problems(before, after, version, angles=False, details=True)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("theta0", [0.0, 30.0])
@pytest.mark.parametrize("version", ["k9", "k10"])
def test_flip_mirrors_polygon_arcs(tmp_path, theta0, version):
    """A graphic polygon whose outline holds an arc (edit_gen.POLY_WITH_ARC), flipped: every
    point of the outline, the arc's start, mid and end among them, is mirrored in y, as
    KiCad mirrors the polygon (SHAPE_POLY_SET::Mirror under EDA_SHAPE::flip; measured with
    pcbnew 9.0.8 on this polygon at 0 and 30, 2026-10-11, and checked by
    test_ladder_r3_judges.py). The tool mirrored only the (xy) points."""
    text = E.board_footprint_text(E.footprint_with_pad_at(0.0), "U1", _AT, theta0)
    board = _board(
        tmp_path / "arcs.kicad_pcb", [E.with_footprint_child(text, E.POLY_WITH_ARC, 2)], version
    )
    before = board.read_bytes()
    _flip(board)
    problems = _flip_problems(before, board.read_bytes(), version, angles=False, shapes=True)
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# two flips
# ---------------------------------------------------------------------------


def _without_justification(fp: E.SynFootprint) -> E.SynFootprint:
    texts = tuple(t._replace(h=T.CENTER, v=T.CENTER, mirror=False) for t in fp.texts)
    return fp._replace(texts=texts)


def _with_justification(fp: E.SynFootprint) -> E.SynFootprint:
    reference = fp.texts[0]._replace(h=T.LEFT)
    return fp._replace(texts=(reference, *fp.texts[1:]))


@pytest.mark.parametrize(
    "justified",
    [
        pytest.param(False, id="texts without justification"),
        pytest.param(True, id="justified texts"),
    ],
)
@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(180.0), theta0=30.0, version="k9")
@given(fp=E.synthetic_footprints(pad_details=True), theta0=_UPPER_HALF, version=_VERSIONS)
def test_flip_twice_is_byte_identical(justified, fp, theta0, version):
    """A footprint KiCad holds at a written angle in (0, 180] (a multiple of half a
    degree), flipped to the back and to the front again where it stands: the board is
    byte-identical, the footprint's own (at), every angle atom and every (justify ...)
    included, since the flip is its own inverse. "justified texts" has a text with a
    (justify ...), the Reference justified left as BarrelJack_CUI_PJ-079BH_Horizontal's
    is; the tool used to append that node again after a single space on the font's line.
    "texts without justification" has none."""
    fp = _with_justification(fp) if justified else _without_justification(fp)
    with tempfile.TemporaryDirectory() as work:
        board = _start_board(Path(work), fp, theta0, version)
        before = board.read_bytes()
        _flip(board, side="B.Cu")
        _flip(board, side="F.Cu")
        after = board.read_bytes()
    assert after == before, "two flips changed the board"


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(45.0), theta0=0.0, version="k10")
@given(fp=E.synthetic_footprints(pad_details=True), theta0=_HALF, version=_VERSIONS)
def test_flip_twice_reads_as_the_start(fp, theta0, version):
    """From any angle that is a multiple of half a degree, 0 included: after two flips KiCad
    reads the footprint as it read it before them, pads, texts and graphics included."""
    with tempfile.TemporaryDirectory() as work:
        board = _start_board(Path(work), fp, theta0, version)
        before = board.read_bytes()
        _flip(board, side="B.Cu")
        _flip(board, side="F.Cu")
        after = board.read_bytes()
    major = _major(version)
    problems = FJ.differences(
        FJ.read(after, "U1", major=major),
        FJ.read(before, "U1", major=major),
        shapes=True,
        details=True,
        copper_count=FJ.copper_count(after),
    )
    assert not problems, "\n".join(problems)


@settings(max_examples=sexpr_gen.budget(4))
@example(theta0=0.0, version="k9")
@example(theta0=-90.0, version="k10")
@given(theta0=_LOWER_HALF, version=_VERSIONS)
def test_two_flips_keep_kicads_angle_spelling(theta0, version):
    """A footprint KiCad wrote at 0 ((at 50 40), no angle atom) or at an angle in
    (-180, 0), flipped twice: its (at) carries the angle atom it had, as KiCad writes the
    same orientation. The tool wrote an explicit 0, and 270 for -90."""
    with tempfile.TemporaryDirectory() as work:
        board = _start_board(Path(work), E.footprint_with_pad_at(45.0), theta0, version)
        before = FJ.footprint_angle_token(FJ.footprint_group(board.read_bytes(), "U1"))
        _flip(board, side="B.Cu")
        _flip(board, side="F.Cu")
        after = FJ.footprint_angle_token(FJ.footprint_group(board.read_bytes(), "U1"))
    assert after == before, (before, after)


# ---------------------------------------------------------------------------
# a flip with a turn
# ---------------------------------------------------------------------------


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(45.0), theta=30.0, d=90.0, version="k9")
@given(fp=E.synthetic_footprints(), theta=_HALF, d=_HALF, version=_VERSIONS)
def test_flip_then_rotate_commutes_on_angles(fp, theta, d, version):
    """From a footprint at theta (multiples of half a degree, so the arithmetic is exact):
    a flip then a turn to theta' + d, theta' being the angle the flip left, reads as a
    turn to theta - d then a flip (KiCad's group law: a flip conjugates a turn into its
    inverse), in the footprint's angle and side and in every position, layer and mirror
    flag. Pad and text angles are bug 14's, judged above."""
    with tempfile.TemporaryDirectory() as work:
        first = _start_board(Path(work), fp, theta, version)
        second = Path(work) / "second.kicad_pcb"
        shutil.copyfile(first, second)
        major = _major(version)
        _flip(first)
        turned = FJ.read(first.read_bytes(), "U1", major=major).orientation
        _move(first, "U1", _AT, rotation=turned + d)
        _move(second, "U1", _AT, rotation=theta - d)
        _flip(second)
        problems = FJ.differences(
            FJ.read(first.read_bytes(), "U1", major=major),
            FJ.read(second.read_bytes(), "U1", major=major),
            angles=False,
            shapes=True,
            copper_count=2,
        )
    assert not problems, "\n".join(problems)


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(45.0), theta0=30.0, r=90.0, version="k9")
@given(fp=E.synthetic_footprints(), theta0=G.angles(), r=G.angles(), version=_VERSIONS)
def test_flip_and_rotate_in_one_call_equals_flip_then_rotate(fp, theta0, r, version):
    """move_footprint with the other side and a rotation writes, byte for byte, what a
    flip and then a turn to that rotation in two calls write: the rotation names the
    angle the footprint ends at (rung 3's D4)."""
    with tempfile.TemporaryDirectory() as work:
        one = _start_board(Path(work), fp, theta0, version)
        two = Path(work) / "two.kicad_pcb"
        shutil.copyfile(one, two)
        _move(one, "U1", _AT, rotation=r, layer="B.Cu")
        _flip(two)
        _move(two, "U1", _AT, rotation=r)
        assert one.read_bytes() == two.read_bytes()


# ---------------------------------------------------------------------------
# the board's layer pairs
# ---------------------------------------------------------------------------

_USER_TEXTS = E.SynFootprint(
    "Users",
    (E.SynPad("1", (-MM, 0), 0.0), E.SynPad("2", (MM, 0), 0.0)),
    (
        E.SynText(
            "property", "Reference", "REF**", (0, -2 * MM), 0.0, True, 0, 0, False, "F.SilkS"
        ),
        E.SynText("fp_text", "user", "ASSY", (MM, MM), 0.0, True, 0, 0, False, "User.1"),
        E.SynText("fp_text", "user", "PLAIN", (-MM, MM), 0.0, True, 0, 0, False, "User.3"),
    ),
    (("fp_line", (-2 * MM, -MM), (2 * MM, -MM)),),
    ("User.1",),
)


@pytest.mark.parametrize(
    "users", [USER_LAYERS, UNNAMED_PAIR], ids=["named pair", "unnamed consecutive pair"]
)
@pytest.mark.parametrize("version", ["k9", "k10"])
def test_flip_maps_front_and_back_user_layers(tmp_path, users, version):
    """N1. A board whose User.1 is typed front and User.2 back, a pair KiCad matches (by
    the user names' text after the first '.', or as consecutive front and back layers
    written without user names), and whose User.3 is a plain user layer: a footprint at 30
    with a text and a line on User.1 and a text on User.3, flipped. The User.1 items land
    on User.2 and the text there is mirrored; the User.3 text keeps its layer and its
    mirror (BOARD::recalcOpposites, BOARD_ITEM::IsSideSpecific; measured with pcbnew 9.0.8,
    test_ladder_r3_judges.py). The tool kept every layer and mirrored both texts."""
    board = _start_board(tmp_path, _USER_TEXTS, 30.0, version, users=users)
    before = board.read_bytes()
    _flip(board)
    problems = _flip_problems(before, board.read_bytes(), version, angles=False, shapes=True)
    assert not problems, "\n".join(problems)


_INNER_TEXT = E.SynFootprint(
    "Inner",
    (E.SynPad("1", (-MM, 0), 0.0), E.SynPad("2", (MM, 0), 0.0)),
    (
        E.SynText(
            "property", "Reference", "REF**", (0, -2 * MM), 0.0, True, 0, 0, False, "F.SilkS"
        ),
        E.SynText("fp_text", "user", "IN1", (0, 2 * MM), 0.0, True, 0, 0, False, "In1.Cu"),
    ),
)


def _inner_stack(copper: int) -> tuple[str, ...]:
    return tuple(f"In{k}.Cu" for k in range(1, copper - 1))


def _whole_stack_pad(copper: int) -> E.SynFootprint:
    """A footprint whose pad 1 names every copper layer of a ``copper``-layer board one
    by one, and whose pad 2 is on *.Cu."""
    layers = ("F.Cu", *_inner_stack(copper), "B.Cu", "*.Mask")
    return E.SynFootprint(
        "Stack",
        (
            E.SynPad("1", (-MM, 0), 0.0, layers=layers, kind="thru_hole"),
            E.SynPad("2", (MM, 0), 0.0, layers=("*.Cu", "*.Mask"), kind="thru_hole"),
        ),
        _INNER_TEXT.texts[:1],
    )


def _partial_stack_pad() -> E.SynFootprint:
    """A footprint whose pad 1 names F.Cu and In1.Cu only."""
    return E.SynFootprint(
        "Partial",
        (E.SynPad("1", (-MM, 0), 0.0, layers=("F.Cu", "In1.Cu", "F.Mask")),),
        _INNER_TEXT.texts[:1],
    )


def _refused_with_the_file_intact(board: Path, call) -> str | None:
    """Run ``call``; None when it raised a ToolError and left ``board`` byte-identical,
    else what went wrong."""
    before = board.read_bytes()
    try:
        call()
    except ToolError:
        return None if board.read_bytes() == before else "refused, but the board changed"
    except Exception as exc:
        return f"{type(exc).__name__} instead of a ToolError: {exc}"
    return "accepted"


@pytest.mark.parametrize("copper", [4, 6])
@pytest.mark.parametrize("version", ["k9", "k10"])
def test_inner_copper_flip_on_four_layer_boards(tmp_path, copper, version):
    """N2. On a board of four or more copper layers, KiCad 9 leaves an item on inner copper
    where it is when it flips a footprint (it pairs the layers before it knows the copper
    count, pcbnew/board.cpp:869-872 at 9.0.8) and KiCad 10 mirrors it through the inner
    stack (board.cpp:986-990 at 10.0.7), so no single file reads the same to both: a
    footprint with a text on In1.Cu, or a pad naming F.Cu and In1.Cu only, is refused with
    the board byte-identical. A pad naming every inner layer of the board, and one on
    *.Cu, read the same either way: that footprint flips, its inner layer names written
    unchanged."""
    problems = []
    for name, fp in (("text on In1.Cu", _INNER_TEXT), ("partial pad", _partial_stack_pad())):
        board = _start_board(tmp_path / name, fp, 0.0, version, copper=copper)
        found = _refused_with_the_file_intact(board, lambda b=board: _flip(b))
        if found:
            problems.append(f"{name}: {found}")
    board = _start_board(tmp_path / "stack", _whole_stack_pad(copper), 0.0, version, copper=copper)
    _flip(board)
    group = FJ.footprint_group(board.read_bytes(), "U1")
    written = [
        [t.value.decode() for t in FJ.child(pad, b"layers").items[1:]]
        for pad in F.pad_groups(group)
    ]
    inner = set(_inner_stack(copper))
    if {n for n in written[0] if n in inner} != inner:
        problems.append(f"whole-stack pad written {written[0]}")
    if "*.Cu" not in written[1]:
        problems.append(f"*.Cu pad written {written[1]}")
    assert not problems, problems


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_inner_copper_text_flips_in_place_on_two_layer_boards(tmp_path, version):
    """N2's two-layer sibling: on a board of two copper layers both KiCads leave In1.Cu in
    place (::FlipLayer moves inner copper only on four or more), so a footprint with a
    text on In1.Cu flips and the text stays on In1.Cu, mirrored (copper is side
    specific)."""
    board = _start_board(tmp_path, _INNER_TEXT, 0.0, version)
    before = board.read_bytes()
    _flip(board)
    problems = _flip_problems(before, board.read_bytes(), version, angles=False)
    for major in (9, 10):
        table = FJ.layer_table(before)
        if BL.flip_layer(table, "In1.Cu", major=major) != "In1.Cu":
            raise RuntimeError(f"the premise fails: KiCad {major} moves In1.Cu on two layers")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# texts written without a layer
# ---------------------------------------------------------------------------

#: A footprint KiCad holds at 0 whose Reference and Value carry (at) but no (layer), the
#: shape of the K10 board in test_cst_pcb.py.
_LAYERLESS = """\t(footprint "T:R"
\t\t(layer "F.Cu")
\t\t(uuid "00000001-0000-4000-8000-00000000f1f1")
\t\t(at 50 40)
\t\t(property "Reference" "U1"
\t\t\t(at 0 -2 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1 1)
\t\t\t\t\t(thickness 0.15)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Value" "V"
\t\t\t(at 0 2 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1 1)
\t\t\t\t\t(thickness 0.15)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(pad "1" smd rect
\t\t\t(at -0.75 0)
\t\t\t(size 0.5 0.8)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t)
\t)
"""


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_flip_gives_a_layerless_text_its_flipped_default(tmp_path, version):
    """N13. A footprint whose Reference and Value carry (at) but no (layer): KiCad reads
    them on F.SilkS and F.Fab whatever the footprint's side (the fields addMandatoryFields
    makes keep their layer; measured with pcbnew 9.0.8, 2026-10-11), and its flip takes
    them to B.SilkS and B.Fab and mirrors them. After the tool's flip KiCad reads them
    there, mirrored, and pcbnew, where it can load the board, holds them so. The tool wrote
    no layer, so KiCad read a mirrored text on the front of a back side part."""
    board = _board(tmp_path / "layerless.kicad_pcb", [_LAYERLESS], version)
    before = board.read_bytes()
    _flip(board)
    after = board.read_bytes()
    problems = _flip_problems(before, after, version, angles=False)
    got = {t.key[1]: (t.layer, t.style.mirror) for t in FJ.read(after, "U1").texts}
    for name, layer in (("Reference", "B.SilkS"), ("Value", "B.Fab")):
        if got.get(name) != (layer, True):
            problems.append(f"{name} reads {got.get(name)}, KiCad's flip gives ({layer}, True)")
    if not PJ.skip_reason() and (version == "k9" or PJ.major() >= 10):
        held = OPS.run([OPS.describe_job(board)])[0]["U1"]["texts"]
        for key, _, _, _, _, _, mirror, layer in held:
            want = {"Reference": "B.SilkS", "Value": "B.Fab"}.get(key[1])
            if want and (layer, mirror) != (want, True):
                problems.append(f"pcbnew holds {key[1]} on {layer}, mirror {mirror}")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# the side
# ---------------------------------------------------------------------------


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(45.0), version="k9")
@given(fp=E.synthetic_footprints(text_layers=E.SIDED_TEXT_LAYERS), version=_VERSIONS)
def test_place_on_the_back_matches_kicads_placement(fp, version):
    """place_footprint with layer B.Cu and rotation 0: KiCad's reading of the placed
    footprint is KiCad's own placement, FootprintLoad, SetPosition, Flip, then the turn
    to 0 (kicad_ref.footprint.place with the board's flip), in the comparison form: every
    pad, text and graphic on the back, mirrored, at the angles the flip gives (rung 3's
    D4: at 0, so bug 2 plays no part). The tool rewrote only the footprint's layer."""
    with tempfile.TemporaryDirectory() as work:
        pretty = E.write_library(work, fp)
        board = _board(Path(work) / "back.kicad_pcb", version=version)
        _place(board, "U1", _AT, 0.0, pretty, fp.name, layer="B.Cu")
        data = board.read_bytes()
    major = _major(version)
    table = FJ.layer_table(data)
    got = FJ.read(data, "U1", major=major)
    want = F.place(
        E.library_footprint(fp),
        got.position,
        0.0,
        semantics="rotate",
        flipped=lambda f: F.board_flip(f, table, major=major),
    )
    problems = FJ.differences(
        got,
        _kicads_reading(want),
        written_fields_only=True,
        shapes=True,
        copper_count=FJ.copper_count(data),
    )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("tool", ["place_footprint", "move_footprint"])
@settings(max_examples=sexpr_gen.budget(4))
@example(copper=4, inner=1, version="k9")
@given(copper=st.sampled_from((4, 6)), inner=st.integers(1, 4), version=_VERSIONS)
def test_side_must_be_front_or_back(tool, copper, inner, version):
    """An inner copper layer of a four or six layer board as the side, to place_footprint
    or to move_footprint: a ToolError, the board byte-identical. A footprint sits on F.Cu
    or B.Cu (FOOTPRINT::SetLayerAndFlip asserts it) and KiCad reads any other footprint
    layer as F.Cu (the parser; measured with pcbnew 9.0.8 in test_ladder_r3_judges.py).
    The tool accepted it."""
    layer = f"In{min(inner, copper - 2)}.Cu"
    with tempfile.TemporaryDirectory() as work:
        board = _start_board(Path(work), E.footprint_with_pad_at(0.0), 0.0, version, copper=copper)
        if tool == "place_footprint":
            pretty = E.write_library(work, E.footprint_with_pad_at(0.0))

            def call():
                _place(board, "U3", (70 * MM, 70 * MM), 0.0, pretty, "Syn", layer=layer)
        else:

            def call():
                _move(board, "U1", _AT, layer=layer)

        found = _refused_with_the_file_intact(board, call)
    assert found is None, f"{tool} with the side {layer}: {found}"


_FRONT = frozenset({"F.Cu", "F.Mask", "F.Paste"})


@pytest.mark.parametrize("tool", ["place_footprint", "move_footprint"])
@settings(max_examples=sexpr_gen.budget(4))
@example(theta0=0.0, version="k9")
@given(theta0=_HALF, version=_VERSIONS)
def test_front_side_requests_are_accepted(tool, theta0, version):
    """The F.Cu sibling: place_footprint on F.Cu places a footprint KiCad reads on the
    front, its pads on front layers; move_footprint of a front footprint to F.Cu where it
    stands changes no byte."""
    with tempfile.TemporaryDirectory() as work:
        fp = E.footprint_with_pad_at(45.0)
        board = _start_board(Path(work), fp, theta0, version)
        before = board.read_bytes()
        if tool == "place_footprint":
            pretty = E.write_library(work, fp)
            _place(board, "U3", (70 * MM, 70 * MM), theta0, pretty, fp.name, layer="F.Cu")
            placed = FJ.read(board.read_bytes(), "U3", major=_major(version))
            assert placed.layer == "F.Cu", placed.layer
            assert all(layers <= _FRONT for layers in (p.layers for p in placed.pads))
        else:
            _move(board, "U1", _AT, layer="F.Cu")
            assert board.read_bytes() == before


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


def _zone_footprint(theta0: float, version: str) -> str:
    """A footprint at ``theta0`` owning a keepout zone written in board coordinates, as
    KiCad stores one (geom_gen.footprint_text)."""
    x, y = _AT
    outline = [(x - MM, y - MM), (x + 2 * MM, y - MM), (x + 2 * MM, y + MM)]
    return G.footprint_text(
        "U1", _AT, theta0, pads=[("1", (-MM, 0), 0.0)], zones=[outline], version=version
    )


@settings(max_examples=sexpr_gen.budget(4))
@example(kind="zone", theta0=30.0, version="k9")
@example(kind="primitives", theta0=0.0, version="k10")
@given(kind=st.sampled_from(("zone", "primitives")), theta0=_HALF, version=_VERSIONS)
def test_a_refused_flip_leaves_the_board_byte_identical(kind, theta0, version):
    """A footprint owning a zone, or one with a custom pad, moved to the back: either the
    move is refused with a ToolError and the board byte-identical, or the flip is KiCad's
    in positions and layers (pads with their primitives, texts, graphics) and the zone's
    board points are mirrored about the footprint's x with its layer on the other side
    (ZONE::Flip top to bottom, then the turn by 180; measured with pcbnew 9.0.8 in the
    step 7 design, 2026-10-11)."""
    with tempfile.TemporaryDirectory() as work:
        if kind == "zone":
            board = _board(
                Path(work) / "start.kicad_pcb", [_zone_footprint(theta0, version)], version
            )
        else:
            board = _start_board(Path(work), _CUSTOM, theta0, version)
        before = board.read_bytes()
        try:
            _flip(board)
        except ToolError:
            assert board.read_bytes() == before, "refused, but the board changed"
            return
        after = board.read_bytes()
    problems = _flip_problems(before, after, version, angles=False, details=True)
    if kind == "zone":
        fx = _AT[0]
        start_zones = _zone_points(before)
        end_zones = _zone_points(after)
        want = [("B.Cu", [(2 * fx - x, y) for x, y in pts]) for _, pts in start_zones]
        if end_zones != want:
            problems.append(f"zones {end_zones} where KiCad's flip gives {want}")
    assert not problems, "\n".join(problems)


def _zone_points(data: bytes) -> list[tuple[str, list[tuple[int, int]]]]:
    """(layer, board points in nm) of each zone U1 owns, read by the lexer port."""
    out = []
    for zone in F.children(FJ.footprint_group(data, "U1"), b"zone"):
        layer = F.child(zone, b"layer")
        pts = F.child(F.child(zone, b"polygon"), b"pts")
        points = [
            (
                parse_board_units(xy.items[1].value, major=10),
                parse_board_units(xy.items[2].value, major=10),
            )
            for xy in F.children(pts, b"xy")
        ]
        out.append((layer.items[1].value.decode() if layer is not None else "", points))
    return out


# ---------------------------------------------------------------------------
# constructs the flip does not port (N4, the flip rows)
# ---------------------------------------------------------------------------


def _png_1x1() -> str:
    """A one-pixel PNG, base64: signature, IHDR, IDAT, IEND, each chunk with its CRC."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
    raw += chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
    return base64.b64encode(raw).decode()


#: Footprint children the flip does not port, each as KiCad writes it, with "{t}" for the
#: indent of the footprint's own children: a text box and a dimension copied from KiCad
#: 9.0.8's stock footprints (Raytac_MDBT42Q, Smolhaj_Scale_0.1, uuids replaced), and an
#: image, a table, a barcode and a footprint stackup in the layout of KiCad's writers
#: (format( PCB_REFERENCE_IMAGE ), format( PCB_TABLE ), pcbnew/pcb_io/kicad_sexpr/
#: pcb_io_kicad_sexpr.cpp:1035-1064 and 2090-2140 at 9.0.8; format( PCB_BARCODE ) and the
#: footprint's (stackup), 2199-2262 and 1371-1383 at 10.0.7). The KiCad 10 constructs
#: (barcode, stackup) sit on KiCad 10 boards and libraries, which only the KiCad 10 legs
#: load. A child no KiCad knows is refused by every KiCad parser, so its rows name
#: their files in kicad_placeholder: they are unloadable by design, and the subject is
#: the tool refusing them with every byte intact.
_FOOTPRINT_CONSTRUCTS = {
    "fp_text_box": (
        "k9",
        """{t}(fp_text_box "No Ground-Pad Area"
{t}\t(start -0.5 -4.2)
{t}\t(end 3.9 -1.1)
{t}\t(margins 0.35 0.35 0.35 0.35)
{t}\t(layer "Dwgs.User")
{t}\t(uuid "00000001-0000-4000-8000-00000000b0b0")
{t}\t(effects
{t}\t\t(font
{t}\t\t\t(size 0.4 0.4)
{t}\t\t\t(thickness 0.1)
{t}\t\t)
{t}\t\t(justify top)
{t}\t)
{t}\t(border yes)
{t}\t(stroke
{t}\t\t(width 0.1)
{t}\t\t(type solid)
{t}\t)
{t})
""",
    ),
    "dimension": (
        "k9",
        """{t}(dimension
{t}\t(type aligned)
{t}\t(layer "Dwgs.User")
{t}\t(uuid "00000002-0000-4000-8000-00000000b0b0")
{t}\t(pts
{t}\t\t(xy 0 0) (xy -5 0)
{t}\t)
{t}\t(height 2)
{t}\t(format
{t}\t\t(prefix "")
{t}\t\t(suffix "")
{t}\t\t(units 3)
{t}\t\t(units_format 1)
{t}\t\t(precision 4)
{t}\t)
{t}\t(style
{t}\t\t(thickness 0.1)
{t}\t\t(arrow_length 1.27)
{t}\t\t(text_position_mode 0)
{t}\t\t(arrow_direction outward)
{t}\t\t(extension_height 0.58642)
{t}\t\t(extension_offset 0.5)
{t}\t\t(keep_text_aligned yes)
{t}\t)
{t}\t(gr_text "5.0000 mm"
{t}\t\t(at -2.5 -3.15 0)
{t}\t\t(layer "Dwgs.User")
{t}\t\t(uuid "00000003-0000-4000-8000-00000000b0b0")
{t}\t\t(effects
{t}\t\t\t(font
{t}\t\t\t\t(size 1 1)
{t}\t\t\t\t(thickness 0.15)
{t}\t\t\t)
{t}\t\t)
{t}\t)
{t})
""",
    ),
    "image": (
        "k9",
        """{t}(image
{t}\t(at 1 1)
{t}\t(layer "F.Fab")
{t}\t(data "%s")
{t}\t(uuid "00000004-0000-4000-8000-00000000b0b0")
{t})
"""
        % _png_1x1(),
    ),
    "table": (
        "k9",
        """{t}(table
{t}\t(column_count 1)
{t}\t(layer "F.Fab")
{t}\t(border
{t}\t\t(external yes)
{t}\t\t(header yes)
{t}\t\t(stroke
{t}\t\t\t(width 0.1)
{t}\t\t\t(type solid)
{t}\t\t)
{t}\t)
{t}\t(separators
{t}\t\t(rows yes)
{t}\t\t(cols yes)
{t}\t\t(stroke
{t}\t\t\t(width 0.1)
{t}\t\t\t(type solid)
{t}\t\t)
{t}\t)
{t}\t(column_widths 5)
{t}\t(row_heights 2)
{t}\t(cells
{t}\t\t(table_cell "A"
{t}\t\t\t(start 0 0)
{t}\t\t\t(end 5 2)
{t}\t\t\t(margins 0.5 0.5 0.5 0.5)
{t}\t\t\t(span 1 1)
{t}\t\t\t(layer "F.Fab")
{t}\t\t\t(uuid "00000005-0000-4000-8000-00000000b0b0")
{t}\t\t\t(effects
{t}\t\t\t\t(font
{t}\t\t\t\t\t(size 1 1)
{t}\t\t\t\t\t(thickness 0.15)
{t}\t\t\t\t)
{t}\t\t\t\t(justify left top)
{t}\t\t\t)
{t}\t\t)
{t}\t)
{t})
""",
    ),
    "barcode": (
        "k10",
        """{t}(barcode
{t}\t(at 1 1 0)
{t}\t(layer "F.SilkS")
{t}\t(size 5 5)
{t}\t(text "X1")
{t}\t(text_height 1)
{t}\t(type qr)
{t}\t(ecc_level L)
{t}\t(hide no)
{t}\t(knockout no)
{t}\t(uuid "00000006-0000-4000-8000-00000000b0b0")
{t})
""",
    ),
    "stackup": (
        "k10",
        """{t}(stackup
{t}\t(layer "F.Cu")
{t}\t(layer "B.Cu")
{t})
""",
    ),
    "future_item": ("k9", "{t}(future_item\n{t}\t(at 0 0)\n{t})\n"),
}
#: Pad tokens the flip does not port: a padstack of its own (which KiCad's flip swaps
#: layer by layer, PADSTACK::FlipLayers) and KiCad 10's back drills and post machining,
#: in the layout of format( PAD ) (the padstack block, pcb_io_kicad_sexpr.cpp:1898-1929
#: at 9.0.8; the drills and post machining, 1745-1785 at 10.0.7), on a through-hole pad
#: 1.
_PAD_CONSTRUCTS = {
    "padstack": (
        "k9",
        """{t}(padstack
{t}\t(mode front_inner_back)
{t}\t(layer "Inner"
{t}\t\t(shape circle)
{t}\t\t(size 1.2 1.2)
{t}\t)
{t}\t(layer "B.Cu"
{t}\t\t(shape circle)
{t}\t\t(size 1.4 1.4)
{t}\t)
{t})
""",
    ),
    "backdrill": ("k10", '{t}(backdrill (size 0.3) (layers "B.Cu" "In2.Cu"))\n'),
    "tertiary_drill": ("k10", '{t}(tertiary_drill (size 0.3) (layers "F.Cu" "In1.Cu"))\n'),
    "front_post_machining": ("k10", "{t}(front_post_machining counterbore (size 1) (depth 0.3))\n"),
    "back_post_machining": ("k10", "{t}(back_post_machining countersink (size 1) (angle 90))\n"),
}
_CONSTRUCT_HOST = E.SynFootprint(
    "Host",
    (
        E.SynPad("1", (-2 * MM, 0), 0.0, layers=("*.Cu", "*.Mask"), kind="thru_hole"),
        E.SynPad("2", (2 * MM, 0), 0.0),
    ),
    E.footprint_with_pad_at(0.0).texts,
)


def _with_pad_token(text: str, token: str, depth: int) -> str:
    """``text``, a footprint written at ``depth`` TABs, with ``token`` (a template whose
    "{t}" is a pad child's indent) added before pad 1's close."""
    t = "\t" * depth
    opening = text.index(f'{t}(pad "1"')
    close = text.index(f"\n{t})\n", opening) + 1
    return text[:close] + token.format(t=t + "\t") + text[close:]


def _with_construct(text: str, construct: str, depth: int) -> str:
    """``text``, a footprint written at ``depth`` TABs, with ``construct`` added: a
    footprint child before (embedded_fonts no), a pad token before pad 1's close."""
    if construct in _FOOTPRINT_CONSTRUCTS:
        return E.with_footprint_child(text, _FOOTPRINT_CONSTRUCTS[construct][1], depth)
    return _with_pad_token(text, _PAD_CONSTRUCTS[construct][1], depth)


def _construct_version(construct: str) -> str:
    return {**_FOOTPRINT_CONSTRUCTS, **_PAD_CONSTRUCTS}[construct][0]


def _construct_board(path: Path, construct: str) -> Path:
    """A board (four copper layers) holding the host footprint at 30 as U1 with
    ``construct`` added, at the KiCad version the construct needs."""
    version = _construct_version(construct)
    text = E.board_footprint_text(_CONSTRUCT_HOST, "U1", _AT, 30.0)
    return _board(path, [_with_construct(text, construct, 2)], version, copper=4)


def _construct_library(directory: Path, construct: str) -> Path:
    """A .pretty holding the host footprint with ``construct`` added, stamped at the
    KiCad version the construct needs; return the .pretty path."""
    text = _with_construct(E.library_text(_CONSTRUCT_HOST), construct, 1)
    if _construct_version(construct) == "k10":
        text = text.replace("(version 20241229)", "(version 20260206)", 1)
        text = text.replace('(generator_version "9.0")', '(generator_version "10.0")', 1)
    pretty = directory / "Constructs.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    (pretty / "Host.kicad_mod").write_text(text, encoding="utf-8", newline="\n")
    return pretty


_CONSTRUCTS = [*_FOOTPRINT_CONSTRUCTS, *_PAD_CONSTRUCTS]
#: The constructs refused whatever the edit, not only by a flip: KiCad moves them with
#: the footprint (in board coordinates, or as items the tool does not transform), and a
#: child no KiCad knows may hold board coordinates of its own.
_MOVED_CONSTRUCTS = ("dimension", "image", "table", "barcode", "future_item")
_FLIPS = ("move to the back", "place on the back")
_MOVES = ("move", "place on the front")


def _construct_params():
    """(construct, operation): every construct under both flips, and the ones refused
    whatever the edit under a move and a placement on the front as well. The rows of a
    child no KiCad knows name their unloadable files in kicad_placeholder."""
    for construct in _CONSTRUCTS:
        for operation in _FLIPS + (_MOVES if construct in _MOVED_CONSTRUCTS else ()):
            marks = []
            if construct == "future_item":
                names = ["board.kicad_pcb"]
                if operation.startswith("place"):
                    names.append("lib/Constructs.pretty/Host.kicad_mod")
                marks = [pytest.mark.kicad_placeholder(*names)]
            yield pytest.param(construct, operation, id=f"{construct}-{operation}", marks=marks)


@pytest.mark.parametrize(("construct", "operation"), list(_construct_params()))
def test_unported_constructs_are_refused_with_the_file_intact(tmp_path, construct, operation):
    """N4: a footprint carrying a construct the tool does not port: a ToolError naming
    the construct, the board byte-identical. Under any edit (moved, placed, on either
    side): a dimension, image, table or barcode, whose board coordinates or item kinds
    the tool does not transform, and a child no KiCad knows. Under a flip (moved to the
    back, placed on the back): also a text box, which PCB_TEXTBOX::Flip turns in its own
    way, a KiCad 10 footprint stackup, and a pad with a padstack of its own or with KiCad
    10's back drills or post machining. The tool moved and flipped the rest around it."""
    board = _construct_board(tmp_path / "board.kicad_pcb", construct)
    if operation.startswith("place"):
        pretty = _construct_library(tmp_path / "lib", construct)
        side = "B.Cu" if operation == "place on the back" else "F.Cu"

        def call():
            _place(board, "U3", (70 * MM, 70 * MM), 0.0, pretty, "Host", layer=side)
    elif operation == "move to the back":

        def call():
            _flip(board)
    else:

        def call():
            _move(board, "U1", (60 * MM, 40 * MM))

    before = board.read_bytes()
    try:
        call()
    except ToolError as exc:
        assert board.read_bytes() == before, "refused, but the board changed"
        assert construct in str(exc), f"the refusal does not name {construct}: {exc}"
        return
    except Exception as exc:
        raise AssertionError(f"{type(exc).__name__} instead of a ToolError: {exc}") from exc
    raise AssertionError(f"{operation} of a footprint carrying {construct!r} was accepted")


#: A through-hole pad's tenting, front only, as KiCad 9 writes it (formatTenting,
#: pcb_io_kicad_sexpr.cpp:1935-1953 at 9.0.8) and as KiCad 10 does (2072-2080 at 10.0.7),
#: on boards of each version; KiCad 10 reads KiCad 9's words as well.
_TENTING = {
    "KiCad 9 words on a KiCad 9 board": ("k9", "{t}(tenting front)\n"),
    "KiCad 10 lists on a KiCad 10 board": ("k10", "{t}(tenting (front yes) (back no))\n"),
    "KiCad 9 words on a KiCad 10 board": ("k10", "{t}(tenting front)\n"),
}
_OPT_BOOL = {b"yes": True, b"no": False, b"none": None}


def _read_tenting(data: bytes, major: int) -> tuple:
    """(front, back) of U1's pad 1 as KiCad ``major`` reads its (tenting ...): KiCad 9's
    parseTenting sets a side for its word, each side off without one
    (pcb_io_kicad_sexpr_parser.cpp:6585-6603 at 9.0.8); KiCad 10's parseFrontBackOptBool
    reads (front yes|no|none) and (back ...), or KiCad 9's words, each side unset without
    one and both unset by none (7815-7880 at 10.0.7)."""
    pad = F.pad_groups(FJ.footprint_group(data, "U1"))[0]
    node = F.child(pad, b"tenting")
    if node is None:
        raise RuntimeError("pad 1 carries no (tenting ...)")
    unset = False if major < 10 else None
    front = back = unset
    for item in node.items[1:]:
        if item.kind == FJ.L.GROUP:
            value = _OPT_BOOL[item.items[1].value]
            if F.head(item) == b"front":
                front = value
            else:
                back = value
        elif item.value == b"front":
            front = True
        elif item.value == b"back":
            back = True
        elif item.value == b"none" and major >= 10:
            front = back = unset
    return front, back


@pytest.mark.parametrize("label", list(_TENTING))
def test_flip_carries_pad_tenting_as_the_boards_kicad_does(tmp_path, label):
    """A through-hole pad tented on the front only, flipped: on a KiCad 9 board its
    tenting reads as before, since KiCad 9.0.8's flip leaves a pad without a padstack of
    its own as it is (PADSTACK::FlipLayers, pcbnew/padstack.cpp:722-759 at 9.0.8); on a
    KiCad 10 board front and back swap, since 10.0.7's swaps the front and back mask
    properties of every pad (padstack.cpp:1490-1522 at 10.0.7). Read as each KiCad
    reads (tenting ...) (_read_tenting)."""
    version, token = _TENTING[label]
    major = _major(version)
    text = _with_pad_token(E.board_footprint_text(_CONSTRUCT_HOST, "U1", _AT, 30.0), token, 2)
    board = _board(tmp_path / "tenting.kicad_pcb", [text], version, copper=4)
    before = _read_tenting(board.read_bytes(), major)
    _performed_flip(board)
    after = _read_tenting(board.read_bytes(), major)
    assert after == (before if major < 10 else before[::-1]), (before, after)


#: An fp_text with (at) and (layer) but no (effects ...), "{t}" its indent.
_BARE_TEXT = """{t}(fp_text user "BARE"
{t}\t(at 1 1 30)
{t}\t(layer "F.SilkS")
{t}\t(uuid "00000007-0000-4000-8000-00000000b0b0")
{t})
"""


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_a_text_without_effects_is_refused_a_flip(tmp_path, version):
    """A footprint whose fp_text has no (effects ...), moved to the back: KiCad's flip
    mirrors that text, and a file can say so only inside an (effects ...), whose font
    the tool would have to invent; a ToolError naming the missing (effects ...), the
    board byte-identical."""
    text = E.board_footprint_text(E.footprint_with_pad_at(0.0), "U1", _AT, 30.0)
    board = _board(tmp_path / "bare.kicad_pcb", [E.with_footprint_child(text, _BARE_TEXT, 2)])
    before = board.read_bytes()
    with pytest.raises(ToolError, match=r"\(effects \.\.\.\)"):
        _flip(board)
    assert board.read_bytes() == before


def test_an_arc_by_centre_and_angle_is_refused_a_flip():
    """KiCad 5's fp_arc, whose (start) is its centre, (end) its start point and (angle)
    its sweep (the parser's legacy branch, pcb_io_kicad_sexpr_parser.cpp:2860-2888 at
    9.0.8), is refused a flip and named; an fp_arc by start, mid and end is not."""
    board = pcb._cst.parse(E.layered_board_text().encode()).lists[0]
    layers = pcb._board_flip_layers(board)
    arcs = {
        "legacy": b"(fp_arc (start 0 0) (end 1 0) (angle 90) (layer F.SilkS))",
        "start, mid, end": b"(fp_arc (start 1 0) (mid 0.7 0.7) (end 0 1) (layer F.SilkS))",
    }
    found = {}
    for label, arc in arcs.items():
        fp = pcb._cst.parse(b'(footprint "X" (layer "F.Cu") (at 0 0) ' + arc + b")").lists[0]
        found[label] = pcb._fp_unsupported(fp, layers, True)
    assert found["start, mid, end"] is None, found
    assert "fp_arc" in (found["legacy"] or ""), found


# ---------------------------------------------------------------------------
# against pcbnew
# ---------------------------------------------------------------------------

#: The rotation slice's curated stock footprints and two the flip needs besides: drill
#: offsets on pads at 270 (Oscillator_SMD_Abracon_ABLNO) and a text on Cmts.User
#: (Buzzer_Mallory_AST1240MLQ). All are copied without their 3D models (rung 3's D4: the
#: model is bug 52's subject), and none owns a zone.
FLIP_CURATED = (
    "R_0805_2012Metric",
    "L_Bourns_SDR0604",
    "D_SMA-SMB_Universal_Handsoldering",
    "D_MELF-RM10_Universal_Handsoldering",
    "AO_DFN-8-1EP_5.55x5.2mm_P1.27mm_EP4.12x4.6mm",
    "ST_HLGA-6_3.76x4.72mm_P1.65mm",
    "BarrelJack_CUI_PJ-079BH_Horizontal",
    "Texas_DSBGA-5_0.8875x1.3875mm_Layout2x3_P0.5mm",
    "Oscillator_SMD_Abracon_ABLNO",
    "Buzzer_Mallory_AST1240MLQ",
)
#: Custom pads, which the tool refused to flip (bug 14).
CUSTOM_PADS = "SolderJumper-2_P1.3mm_Open_TrianglePad1.0x1.5mm"
#: The footprint whose 3D model the model test flips.
MODEL_FOOTPRINT = "R_0805_2012Metric"
#: The angles the curated set is placed at before pcbnew and the tool flip it.
_FLIP_ANGLES = (0.0, 90.0, 180.0, 270.0, 30.0, 45.0)
#: N14's angles for the flip rows, and the angle those start from.
_TOKEN_ANGLES = (90.0, 180.0, 270.0, 45.0, 37.5)
_TOKEN_START = 30.0
#: The F1 footprint of the rung 3 design: texts on F.SilkS, Cmts.User and a plain User.1.
F1 = E.SynFootprint(
    "F1",
    (E.SynPad("1", (-MM, 0), 0.0), E.SynPad("2", (MM, 0), 0.0)),
    (
        E.SynText(
            "property", "Reference", "REF**", (0, -2 * MM), 0.0, True, 0, 0, False, "F.SilkS"
        ),
        E.SynText("property", "Value", "F1", (0, 2 * MM), 0.0, True, 0, 0, False, "F.SilkS"),
        E.SynText("fp_text", "user", "CMTS", (MM, MM), 0.0, True, 0, 0, False, "Cmts.User"),
        E.SynText("fp_text", "user", "USER1", (-MM, MM), 0.0, True, 0, 0, False, "User.1"),
    ),
)


def _slot(index: int) -> tuple[int, int]:
    return (30 * MM * (1 + index % 6), 30 * MM * (1 + index // 6))


def _tolerance(*angles: float) -> int:
    """Positions exact on quarter turns, within footprint_judge's D3 tolerance otherwise."""
    return 0 if all(a % 90 == 0 for a in angles) else FJ.ROTATION_TOLERANCE_NM


def _model_tokens(data: bytes, ref: str) -> list[list[tuple[str, bytes]]]:
    """The tokens, kind and value, of each (model ...) of footprint ``ref``."""
    out = []
    for model in F.children(FJ.footprint_group(data, ref), b"model"):
        tokens = FJ.L.tokens(data[model.start : model.end])
        out.append([(t.kind, t.value) for t in tokens if t.kind in FJ.L.ATOM_KINDS])
    return out


class _FlipRuns:
    """Everything the pcbnew tests compare, made once per worker in one pcbnew run, then
    edited by the tool: for each of _FLIP_ANGLES a board pcbnew placed the curated set and
    the custom pads on (models removed), pcbnew's flip of it and the tool's; pcbnew's
    flip then turn of the board at _TOKEN_START and the tool's one call; pcbnew's and the
    tool's placements on the back at 0 (models kept); F1 and _USER_TEXTS placed at 30 and
    flipped; and R_0805 with its model, placed at 30 and flipped."""

    def __init__(self, work: Path):
        self.major = PJ.major()
        originals = [E.stock_footprint(name) for name in FLIP_CURATED]
        copies = [
            E.model_free_copy(path, work / "stock")
            for path in (*originals, E.stock_footprint(CUSTOM_PADS))
        ]
        self.names = [path.stem for path in copies]
        self.custom = len(copies) - 1
        empty = _board(work / "empty.kicad_pcb")
        jobs: list[dict] = []
        starts: dict[float, Path] = {}
        kicad_flips: dict[float, Path] = {}
        for angle in _FLIP_ANGLES:
            start = work / f"start_{angle}.kicad_pcb"
            items = [
                OPS.place_item(c.parent, c.stem, f"C{i}", _slot(i), ("rotate", angle))
                for i, c in enumerate(copies)
            ]
            jobs.append(OPS.place_job(empty, items, start))
            out = work / f"kicad_flip_{angle}.kicad_pcb"
            moves = [OPS.move_item(f"C{i}", _slot(i), ("flip", None)) for i in range(len(copies))]
            jobs.append(OPS.move_job(start, moves, out))
            starts[angle], kicad_flips[angle] = start, out
        kicad_turned: dict[float, Path] = {}
        for angle in _TOKEN_ANGLES:
            out = work / f"kicad_flip_turn_{angle}.kicad_pcb"
            moves = [
                OPS.move_item(f"C{i}", _slot(i), [("flip", None), ("rotate_to", angle)])
                for i in range(self.custom)
            ]
            jobs.append(OPS.move_job(starts[_TOKEN_START], moves, out))
            kicad_turned[angle] = out
        kicad_back = work / "kicad_back.kicad_pcb"
        items = [
            OPS.place_item(
                p.parent, p.stem, f"C{i}", _slot(i), [("flip", None), ("rotate_to", 0.0)]
            )
            for i, p in enumerate(originals)
        ]
        jobs.append(OPS.place_job(empty, items, kicad_back))
        synthetic = {
            "F1": (F1, (("User.1", "user", None),)),
            "named pair": (_USER_TEXTS, USER_LAYERS),
            "unnamed consecutive pair": (_USER_TEXTS, UNNAMED_PAIR),
        }
        synthetic_runs = {}
        for k, (label, (fp, users)) in enumerate(synthetic.items()):
            pretty = E.write_library(work / f"syn{k}", fp)
            board_in = _board(work / f"syn{k}_empty.kicad_pcb", users=users)
            start, out = work / f"syn{k}_start.kicad_pcb", work / f"syn{k}_flip.kicad_pcb"
            jobs.append(
                OPS.place_job(
                    board_in, [OPS.place_item(pretty, fp.name, "U1", _AT, ("rotate", 30.0))], start
                )
            )
            jobs.append(OPS.move_job(start, [OPS.move_item("U1", _AT, ("flip", None))], out))
            synthetic_runs[label] = (start, out)
        model = E.stock_footprint(MODEL_FOOTPRINT)
        model_start, model_flip = work / "model_start.kicad_pcb", work / "model_flip.kicad_pcb"
        jobs.append(
            OPS.place_job(
                empty,
                [OPS.place_item(model.parent, model.stem, "U1", _AT, ("rotate", 30.0))],
                model_start,
            )
        )
        jobs.append(
            OPS.move_job(model_start, [OPS.move_item("U1", _AT, ("flip", None))], model_flip)
        )
        OPS.run(jobs)

        self.kicad_flips = {angle: path.read_bytes() for angle, path in kicad_flips.items()}
        self.starts = {angle: path.read_bytes() for angle, path in starts.items()}
        self.tool_flips: dict[float, bytes] = {}
        self.refused: dict[tuple[float, int], str] = {}
        for angle, start in starts.items():
            board = work / f"tool_flip_{angle}.kicad_pcb"
            shutil.copyfile(start, board)
            for i in range(len(copies)):
                try:
                    _flip(board, f"C{i}", _slot(i))
                except ToolError as exc:
                    self.refused[(angle, i)] = str(exc)
            self.tool_flips[angle] = board.read_bytes()
        self.kicad_turned = {angle: path.read_bytes() for angle, path in kicad_turned.items()}
        self.tool_turned: dict[float, bytes] = {}
        for angle in _TOKEN_ANGLES:
            board = work / f"tool_flip_turn_{angle}.kicad_pcb"
            shutil.copyfile(starts[_TOKEN_START], board)
            for i in range(self.custom):
                _move(board, f"C{i}", _slot(i), rotation=angle, layer="B.Cu")
            self.tool_turned[angle] = board.read_bytes()
        self.originals = originals
        self.kicad_back = kicad_back.read_bytes()
        tool_back = _board(work / "tool_back.kicad_pcb")
        for i, path in enumerate(originals):
            _place(tool_back, f"C{i}", _slot(i), 0.0, path.parent, path.stem, layer="B.Cu")
        self.tool_back = tool_back.read_bytes()
        self.synthetic: dict[str, tuple[bytes, bytes, bytes]] = {}
        for label, (start, out) in synthetic_runs.items():
            board = work / f"tool_{label.replace(' ', '_')}.kicad_pcb"
            shutil.copyfile(start, board)
            _flip(board)
            self.synthetic[label] = (start.read_bytes(), out.read_bytes(), board.read_bytes())
        self.model_start = model_start.read_bytes()
        self.model_kicad = model_flip.read_bytes()
        board = work / "tool_model.kicad_pcb"
        shutil.copyfile(model_start, board)
        self.model_refused = None
        try:
            _flip(board)
        except ToolError as exc:
            self.model_refused = str(exc)
        self.model_tool = board.read_bytes()


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    return _FlipRuns(tmp_path_factory.mktemp("fp_flip"))


def _pcbnew_flip_problems(runs: _FlipRuns, indices, **compare) -> list[str]:
    problems = []
    for angle in _FLIP_ANGLES:
        copper = FJ.copper_count(runs.tool_flips[angle])
        for i in indices:
            what = f"{runs.names[i]} at {angle}"
            if (angle, i) in runs.refused:
                problems.append(f"{what}: refused: {runs.refused[(angle, i)]}")
                continue
            got = FJ.read(runs.tool_flips[angle], f"C{i}", major=runs.major)
            want = FJ.read(runs.kicad_flips[angle], f"C{i}", major=runs.major)
            found = FJ.differences(
                got, want, tolerance_nm=_tolerance(angle), copper_count=copper, **compare
            )
            problems += [f"{what}: {p}" for p in found]
    return problems


def test_flip_matches_pcbnew(runs):
    """One item: the curated set and a footprint with custom pads, as pcbnew placed them at
    0, 90, 180, 270, 30 and 45 degrees (3D models removed), flipped by move_footprint and
    by pcbnew's FOOTPRINT::Flip: each footprint the tool wrote reads as pcbnew's in the
    comparison form (side, angle, every pad's angle, position, layers, offset, delta,
    chamfers and primitives, every text's style, layer and position, every graphic's
    geometry; positions within two nanometres off the quarter turns)."""
    problems = _pcbnew_flip_problems(
        runs, range(len(runs.names)), shapes=True, details=True, written_fields_only=True
    )
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


def test_flip_positions_match_pcbnew(runs):
    """The passing sibling over the same cases but the custom pads: the footprint's angle
    and side, every pad's, text's and graphic's position and layer read as pcbnew's."""
    problems = _pcbnew_flip_problems(
        runs, range(runs.custom), angles=False, mirrors=False, shapes=True
    )
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


@pytest.mark.parametrize("part", ["pads", "texts"])
def test_flip_and_rotate_match_pcbnew_token_for_token(runs, part):
    """N14, the flip rows: the curated set as pcbnew placed it at 30, moved to the back and
    to 90, 180, 270, 45 and 37.5 degrees in one move_footprint call, and by pcbnew's Flip
    then Rotate to the same angle: every pad's angle atom (part "pads"), or every text's
    angle atom, unlocked word and justify words (part "texts"), equals pcbnew's exactly;
    positions as KiCad reads them, within two nanometres off the quarter turns."""
    problems = []
    for angle in _TOKEN_ANGLES:
        for i in range(runs.custom):
            ref = f"C{i}"
            got = FJ.tokens(FJ.footprint_group(runs.tool_turned[angle], ref))
            want = FJ.tokens(FJ.footprint_group(runs.kicad_turned[angle], ref))
            other = "texts" if part == "pads" else "pads"
            got[other], want[other] = [], []
            found = FJ.token_problems(
                got, want, tolerance_nm=_tolerance(angle, _TOKEN_START), footprint=False
            )
            problems += [f"{runs.names[i]} flipped and turned to {angle}: {p}" for p in found]
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


def test_place_on_the_back_matches_pcbnew(runs):
    """One item: the curated set, 3D models kept, placed on the back at 0 by place_footprint
    and by pcbnew (FootprintLoad, SetPosition, Flip, then the turn to 0): each footprint
    the tool wrote reads as pcbnew's in the comparison form, the mandatory fields its
    library does not write aside (rung 3's D4: at 0)."""
    problems = []
    copper = FJ.copper_count(runs.tool_back)
    for i, path in enumerate(runs.originals):
        got = FJ.read(runs.tool_back, f"C{i}", major=runs.major)
        want = FJ.read(runs.kicad_back, f"C{i}", major=runs.major)
        found = FJ.differences(
            got, want, shapes=True, details=True, written_fields_only=True, copper_count=copper
        )
        problems += [f"{path.stem}: {p}" for p in found]
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


def _synthetic_problems(runs: _FlipRuns, label: str) -> list[str]:
    _, kicad, tool = runs.synthetic[label]
    return FJ.differences(
        FJ.read(tool, "U1", major=runs.major),
        FJ.read(kicad, "U1", major=runs.major),
        angles=False,
        shapes=True,
        copper_count=FJ.copper_count(tool),
    )


def test_flip_of_unsided_texts_matches_pcbnew(runs):
    """The F1 footprint (texts on F.SilkS, Cmts.User and a plain User.1) as pcbnew placed
    it at 30, flipped by the tool and by pcbnew: each text's layer and mirror flag are
    pcbnew's, which mirrors only the F.SilkS ones."""
    problems = _synthetic_problems(runs, "F1")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("label", ["named pair", "unnamed consecutive pair"])
def test_flip_of_user_layers_matches_pcbnew(runs, label):
    """N1's pcbnew sibling: _USER_TEXTS as pcbnew placed it at 30 on a board whose User.1
    and User.2 are a front and back pair (named, or consecutive and unnamed) and whose
    User.3 is plain, flipped by the tool and by pcbnew: every text's and graphic's layer
    and every text's mirror flag are pcbnew's."""
    problems = _synthetic_problems(runs, label)
    assert not problems, "\n".join(problems)


def test_flip_keeps_the_3d_model_as_pcbnew_does(runs):
    """R_0805_2012Metric with its 3D model, as pcbnew placed it at 30, flipped by the tool
    and by pcbnew: the flip happens (FOOTPRINT::Flip has no model code,
    pcbnew/footprint.cpp:2491-2550 at 9.0.8, 2930-2999 at 10.0.7) and the model block reads
    token for token as before the flip and as pcbnew wrote it after. The tool refused."""
    if runs.model_refused is not None:
        raise AssertionError(f"move_footprint refused the flip: {runs.model_refused}")
    before = _model_tokens(runs.model_start, "U1")
    if not before:
        raise RuntimeError(f"the installed {MODEL_FOOTPRINT} carries no (model ...)")
    assert _model_tokens(runs.model_tool, "U1") == before
    assert _model_tokens(runs.model_kicad, "U1") == before


def test_curated_flip_set_exercises_what_the_pcbnew_tests_need():
    """The premise of the pcbnew tests above: the installed curated set holds a chamfered
    pad, a trapezoid, a pad with a shape offset, a text on an unsided layer, a justified
    keep-upright text, and custom pads. Raises RuntimeError, never AssertionError, when
    the install no longer does."""
    libraries = [
        FJ.read_library(E.stock_footprint(name).read_bytes())
        for name in (*FLIP_CURATED, CUSTOM_PADS)
    ]
    pads = [p for fp in libraries for p in fp.pads]
    texts = [t for fp in libraries for t in fp.texts]
    needs = {
        "a chamfered pad": any(p.chamfer for p in pads),
        "a trapezoid": any(p.delta != (0, 0) for p in pads),
        "a shape offset": any(p.offset != (0, 0) for p in pads),
        "custom pad primitives": any(p.primitives for p in pads),
        "a text on Cmts.User": any(t.layer == "Cmts.User" for t in texts),
        "a justified keep-upright text": any(t.style.keep_upright and t.style.h for t in texts),
    }
    missing = [what for what, found in needs.items() if not found]
    if missing:
        raise RuntimeError(f"the curated flip set holds no {', '.join(missing)}")


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@example(picks=[(0, 30.0)])
@given(st.lists(st.tuples(st.integers(0, 10**9), G.angles()), min_size=1, max_size=8))
def test_nightly_flip_matches_pcbnew(picks):
    """Sampled stock footprints without a zone (models removed) as pcbnew placed them at
    generated angles, flipped by the tool and by pcbnew: the tool's footprints read as
    pcbnew's. The explicit example is BarrelJack_CUI_PJ-079BH_Horizontal at 30, whose
    pads stand at 210 and land at 330."""
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
        copies = [
            E.model_free_copy(path, Path(work) / f"lib{i}") for i, (path, _) in enumerate(chosen)
        ]
        jobs, starts, outs = [], [], []
        for i, (copy, (_, angle)) in enumerate(zip(copies, chosen)):
            start, out = Path(work) / f"start{i}.kicad_pcb", Path(work) / f"flip{i}.kicad_pcb"
            jobs.append(
                OPS.place_job(
                    empty,
                    [OPS.place_item(copy.parent, copy.stem, "C0", _AT, ("rotate", angle))],
                    start,
                )
            )
            jobs.append(OPS.move_job(start, [OPS.move_item("C0", _AT, ("flip", None))], out))
            starts.append(start)
            outs.append(out)
        OPS.run(jobs)
        for (path, angle), start, out in zip(chosen, starts, outs):
            try:
                _performed_flip(start, "C0", _AT)
            except AssertionError as exc:
                problems.append(f"{path.stem} at {angle}: {exc}")
                continue
            found = FJ.differences(
                FJ.read(start.read_bytes(), "C0", major=PJ.major()),
                FJ.read(out.read_bytes(), "C0", major=PJ.major()),
                tolerance_nm=_tolerance(angle),
                shapes=True,
                details=True,
                copper_count=2,
            )
            problems += [f"{path.stem} at {angle}: {p}" for p in found]
    assert not problems, "\n".join(problems[:40])
