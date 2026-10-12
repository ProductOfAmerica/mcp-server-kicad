"""Rung 3's judges for footprint orientation edits, each checked against KiCad itself.

The ports kicad_ref.footprint, kicad_ref.pad and kicad_ref.pcb_text say what KiCad holds
of a footprint and what its own SetOrientation, Rotate and Flip do to it; here they answer
to pcbnew doing the same edit (FootprintLoad, SetPosition, then Rotate or SetOrientation;
a footprint pcbnew placed, then FOOTPRINT::Flip), in the doubles pcbnew holds in memory
and in the tokens SaveBoard writes, and to pcbnew reading a board. kicad_ref.board_layers,
the layer pairs a flip uses and the layers it treats as side specific, answers to pcbnew's
BOARD::FlipLayer and BOARD_ITEM::IsSideSpecific on boards whose layer tables differ.
edit_gen's builders answer to pcbnew loading what they write and saving it back
unchanged, and to pcbnew placing the same footprint on either side. pcbnew_fp_ops
answers to its own contract (a failed call is a RuntimeError), and the footprint port's
refusal of a non-finite angle to kicad-cli.

The swap: kicad_ref.exchange answers to pcbnew replaying ExchangeFootprint's steps through
its own Flip, SetOrientation, FindPadByNumber and IsOnCopperLayer, its lock reading to
pcbnew loading each lock spelling, and edit_gen's netlist builder to kicad-cli's export.
"""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import edit_gen as E
import footprint_judge as FJ
import geom_gen as G
import pcbnew_fp_ops as OPS
import pcbnew_judge as PJ
import pytest
import sexpr_gen
from conftest import requires_cli
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import board_item as B
from kicad_ref import board_layers as BL
from kicad_ref import exchange as X
from kicad_ref import footprint as F
from kicad_ref import parse_units as P
from kicad_ref import pcb_text as T

from mcp_server_kicad._shared import _run_cli

MM = 1_000_000

#: The rung 3 plan's curated stock footprints, less the zone-owning Tag-Connect, whose
#: zone is ported with the board-coordinates slice; Texas_DSBGA-5, whose library writes a
#: text at -90; and an M.2 edge connector, whose library writes Reference and Value twice
#: (kicad_ref.footprint's module docstring).
CURATED = (
    "R_0805_2012Metric",
    "L_Bourns_SDR0604",
    "D_SMA-SMB_Universal_Handsoldering",
    "D_MELF-RM10_Universal_Handsoldering",
    "AO_DFN-8-1EP_5.55x5.2mm_P1.27mm_EP4.12x4.6mm",
    "ST_HLGA-6_3.76x4.72mm_P1.65mm",
    "BarrelJack_CUI_PJ-079BH_Horizontal",
    "Texas_DSBGA-5_0.8875x1.3875mm_Layout2x3_P0.5mm",
    "M.2_22110-xx-B",
)
_ANGLES = (0.0, 90.0, 180.0, 270.0, 30.0, -90.0, 45.0, 37.5)
_SEMANTICS = ("rotate", "set")

#: A library footprint carrying what the rotation port has to get right: pad angles that
#: are 0, quarter turns, 45 and written outside [0, 360); a keep-upright Reference
#: justified left and top; an unlocked, mirrored Value justified right and bottom; a hidden
#: user field; an fp_text written at -90 and an unlocked one at 450; two graphics.
RICH = E.SynFootprint(
    "Rich",
    (
        E.SynPad("1", (-2 * MM, 0), 0.0),
        E.SynPad("2", (2 * MM, 0), 90.0),
        E.SynPad("3", (0, 2 * MM), 45.0),
        E.SynPad("4", (0, -2 * MM), -90.0),
        E.SynPad("5", (1_234_567, -765_432), 450.0),
    ),
    (
        E.SynText(
            "property",
            "Reference",
            "REF**",
            (0, -3 * MM),
            0.0,
            True,
            T.LEFT,
            T.TOP,
            False,
            "F.SilkS",
        ),
        E.SynText(
            "property", "Value", "V", (0, 3 * MM), 0.0, False, T.RIGHT, T.BOTTOM, True, "F.Fab"
        ),
        E.SynText(
            "property", "MPN", "X1", (MM, MM), 0.0, True, T.CENTER, T.CENTER, False, "F.Fab", True
        ),
        E.SynText(
            "fp_text",
            "user",
            "${REFERENCE}",
            (0, 0),
            -90.0,
            True,
            T.CENTER,
            T.CENTER,
            False,
            "F.Fab",
        ),
        E.SynText(
            "fp_text", "user", "KEEP", (-MM, MM), 450.0, False, T.LEFT, T.CENTER, False, "Cmts.User"
        ),
    ),
    (("fp_line", (-3 * MM, -MM), (3 * MM, -MM)), ("fp_arc", (-MM, 0), (0, -MM), (MM, 0))),
)


def _pcbnew_major() -> int:
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    return PJ.major()


def _quarter(angle: float) -> bool:
    return angle % 90 == 0


def _tolerance(*angles: float) -> int:
    """Exact positions on quarter turns, footprint_judge's D3 tolerance otherwise."""
    return 0 if all(_quarter(a) for a in angles) else FJ.ROTATION_TOLERANCE_NM


def _write_board(path: Path, items=(), version: str = "k9") -> Path:
    path.write_text(G.board_text(items, version=version), encoding="utf-8", newline="\n")
    return path


# ---------------------------------------------------------------------------
# what pcbnew holds, against what the port says it holds
# ---------------------------------------------------------------------------


def _port_records(fp: F.Footprint, major: int) -> tuple[list, list]:
    """(pads, texts) of ``fp`` in pcbnew_fp_ops' record shapes: board positions through
    the RotatePoint port (kicad_ref.board_item), angles as the port holds them."""
    pads = [
        (
            p.number,
            B.fp_relative_to_board(fp.position, fp.orientation, p.local, major=major),
            p.angle,
            p.angle - fp.orientation,
        )
        for p in fp.pads
    ]
    texts = [
        (
            t.key,
            None
            if t.local is None
            else B.fp_relative_to_board(fp.position, fp.orientation, t.local, major=major),
            tuple(t.style),
            t.layer,
        )
        for t in fp.texts
    ]
    return pads, texts


def _pcbnew_records(found: dict) -> tuple[list, list]:
    pads = [(n, tuple(xy), a, rel) for n, xy, a, rel in found["pads"]]
    texts = [
        (tuple(key), tuple(xy), (angle, upright, h, v, mirror), layer)
        for key, xy, angle, upright, h, v, mirror, layer in found["texts"]
    ]
    return pads, texts


def _held_problems(port: F.Footprint, found: dict, major: int, what: str) -> list[str]:
    """Every difference between the footprint the port holds and the one pcbnew holds in
    memory: orientation, and per pad and text every value, exactly (doubles by repr)."""
    problems = []
    if repr(port.orientation) != repr(found["orientation"]):
        problems.append(
            f"{what}: orientation {port.orientation!r}, pcbnew {found['orientation']!r}"
        )
    port_pads, port_texts = _port_records(port, major)
    held_pads, held_texts = _pcbnew_records(found)
    pairs, unpaired = FJ.pair(
        port_pads,
        held_pads,
        lambda p: p[0],
        lambda p: p[1],
        "pad",
        lambda a, b: abs(a[2] - b[2]),
    )
    problems += [f"{what}: {u}" for u in unpaired]
    for mine, theirs in pairs:
        if repr(mine) != repr(theirs):
            problems.append(f"{what}: pad port {mine}, pcbnew {theirs}")
    pairs, unpaired = FJ.pair(
        port_texts,
        held_texts,
        lambda t: (t[0], t[3]),
        lambda t: t[1],
        "text",
        lambda a, b: abs(a[2][0] - b[2][0]) + (0 if a[2] == b[2] else 1),
    )
    problems += [f"{what}: {u}" for u in unpaired]
    for mine, theirs in pairs:
        shown = mine if mine[1] is not None else (mine[0], theirs[1], *mine[2:])
        if repr(shown) != repr(theirs):
            problems.append(f"{what}: text port {mine}, pcbnew {theirs}")
    return problems


def _operations_problems(cases: list[tuple[Path, str, float]], work: Path) -> tuple[int, list]:
    """For each (library footprint file, semantics, angle): pcbnew places it on one board
    per (semantics, angle); what pcbnew holds in memory equals the port's place(), exactly,
    and the tokens SaveBoard writes equal the port's writer spellings (positions within
    footprint_judge's D3 tolerance off the quarter turns). Returns (footprints checked,
    problems)."""
    major = _pcbnew_major()
    board_in = _write_board(work / "empty.kicad_pcb")
    groups: dict[tuple[str, float], list[tuple[str, Path, tuple[int, int]]]] = {}
    for index, (path, how, angle) in enumerate(cases):
        at = (20 * MM * (1 + index % 10), 20 * MM * (1 + index // 10))
        groups.setdefault((how, angle), []).append((f"U{index + 1}", path, at))
    jobs, outs = [], []
    for k, ((how, angle), items) in enumerate(groups.items()):
        out = work / f"op{k}.kicad_pcb"
        outs.append(out)
        jobs.append(
            OPS.place_job(
                board_in,
                [OPS.place_item(p.parent, p.stem, ref, at, (how, angle)) for ref, p, at in items],
                out,
            )
        )
    results = OPS.run(jobs)
    problems, checked = [], 0
    for ((how, angle), items), result, out in zip(groups.items(), results, outs):
        saved = out.read_bytes()
        for ref, path, at in items:
            library = FJ.read_library(path.read_bytes())
            port = F.place(library, at, angle, semantics=how)
            what = f"{path.stem} {how} {angle}"
            problems += _held_problems(port, result[ref], major, what)
            written = FJ.tokens(FJ.footprint_group(saved, ref))
            problems += [
                f"{what}: {p}"
                for p in FJ.token_problems(
                    written, FJ.expected_tokens(port), tolerance_nm=_tolerance(angle)
                )
            ]
            checked += 1
    return checked, problems


def test_footprint_port_matches_pcbnew_operations(tmp_path):
    """The curated stock footprints and RICH, placed by pcbnew at 0, 90, 180, 270, 30, -90,
    45 and 37.5 degrees through Rotate and through SetOrientation: the orientation, every
    pad's board position, absolute and footprint-relative angle, and every text's board
    position, angle, keep-upright flag, justification, mirror and layer pcbnew holds equal
    kicad_ref.footprint.place's, bit for bit; and SaveBoard writes the footprint's, the
    pads' and the texts' angle atoms, the unlocked words and the justify words the port's
    writer spellings give."""
    library = E.write_library(tmp_path / "lib", RICH)
    paths = [E.stock_footprint(name) for name in CURATED] + [library / "Rich.kicad_mod"]
    cases = [(path, how, angle) for how in _SEMANTICS for angle in _ANGLES for path in paths]
    checked, problems = _operations_problems(cases, tmp_path)
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])
    if checked != len(cases):
        raise RuntimeError(f"checked {checked} footprints of {len(cases)}")


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@given(
    st.lists(
        st.tuples(st.integers(0, 10**9), st.sampled_from(_SEMANTICS), G.angles()),
        min_size=1,
        max_size=8,
    )
)
def test_nightly_footprint_port_matches_pcbnew(picks):
    """The same over sampled stock footprints (none owning a zone) and generated angles."""
    stock = E.stock_footprints_for_turns()
    if not stock:
        pytest.skip("no stock footprint libraries")
    cases = [(stock[k % len(stock)], how, angle) for k, how, angle in picks]
    with tempfile.TemporaryDirectory() as work:
        _, problems = _operations_problems(cases, Path(work))
    assert not problems, "\n".join(problems[:30])


# ---------------------------------------------------------------------------
# reading a board
# ---------------------------------------------------------------------------


_READ_ANGLES = (0.0, 90.0, -90.0, 180.0, 45.0, 37.5, 359.9, 0.001, 12.3456789)


def _read_problems(footprints: list[tuple[str, str]], work: Path) -> list[str]:
    """footprints: (reference, footprint text) written on one board; pcbnew's describe of
    it equals the port's reading of the same text, exactly."""
    major = _pcbnew_major()
    board = _write_board(work / "read.kicad_pcb", [text for _, text in footprints])
    found = OPS.run([OPS.describe_job(board)])[0]
    data = board.read_bytes()
    problems = []
    for ref, _ in footprints:
        problems += _held_problems(FJ.read(data, ref, major=major), found[ref], major, ref)
    return problems


def _builder_footprints(cases) -> list[tuple[str, str]]:
    out = []
    for index, (fp, angle, how) in enumerate(cases):
        ref = f"U{index + 1}"
        at = (30 * MM * (1 + index % 8), 30 * MM * (1 + index // 8) + 123_457)
        out.append((ref, E.board_footprint_text(fp, ref, at, angle, semantics=how)))
    return out


def _hand_picked_builder_cases() -> list:
    bases = [
        RICH,
        E.footprint_with_pad_at(180.0),
        E.footprint_with_pad_at(-90.0, rotation_text=270.0),
    ]
    return [(fp, angle, how) for fp in bases for angle in _READ_ANGLES for how in _SEMANTICS]


def test_port_reads_pads_and_texts_as_pcbnew_does(tmp_path):
    """edit_gen's KiCad-form footprints (RICH and two small ones) at nine angles, through
    Rotate and SetOrientation, on one board: pcbnew's orientation, pad board positions,
    absolute and footprint-relative pad angles, text board positions, angles, keep-upright
    flags, justification, mirror flags and layers equal kicad_ref.footprint.read_footprint's
    reading of the same tokens, bit for bit."""
    problems = _read_problems(_builder_footprints(_hand_picked_builder_cases()), tmp_path)
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@given(
    st.lists(
        st.tuples(E.synthetic_footprints(), G.angles(), st.sampled_from(_SEMANTICS)),
        min_size=1,
        max_size=6,
    )
)
def test_nightly_port_reads_pads_and_texts_as_pcbnew_does(cases):
    """The same over generated footprints and angles."""
    with tempfile.TemporaryDirectory() as work:
        problems = _read_problems(_builder_footprints(cases), Path(work))
    assert not problems, "\n".join(problems[:30])


def test_builder_footprints_load_in_pcbnew_unchanged(tmp_path):
    """The premise of building inputs with edit_gen (rung 3's D10). A library footprint it
    writes (RICH and two small ones), loaded by FootprintLoad and saved on a board at
    (50, 40) mm unturned, reads as the port's reading of the library file there, exactly;
    and each KiCad-form footprint it writes, loaded and saved again by pcbnew, reads as
    before, positions within footprint_judge's D3 tolerance off the quarter turns."""
    _pcbnew_major()
    bases = [
        RICH,
        E.footprint_with_pad_at(180.0),
        E.footprint_with_pad_at(-90.0, rotation_text=270.0),
        FLIPPY,
    ]
    libraries = [
        E.write_library(tmp_path / f"lib{k}", fp) / f"{fp.name}.kicad_mod"
        for k, fp in enumerate(bases)
    ]
    at = (50 * MM, 40 * MM)
    items = [
        OPS.place_item(path.parent, path.stem, f"L{k + 1}", at) for k, path in enumerate(libraries)
    ]
    board_in = _write_board(tmp_path / "empty.kicad_pcb")
    placed_out = tmp_path / "placed.kicad_pcb"
    footprints = _builder_footprints(_hand_picked_builder_cases())
    built = _write_board(tmp_path / "built.kicad_pcb", [text for _, text in footprints])
    resaved = tmp_path / "resaved.kicad_pcb"
    moves = [OPS.move_item(ref, FJ.read(built.read_bytes(), ref).position) for ref, _ in footprints]
    OPS.run([OPS.place_job(board_in, items, placed_out), OPS.move_job(built, moves, resaved)])
    problems = []
    for k, path in enumerate(libraries):
        want = F.place(FJ.read_library(path.read_bytes()), at, 0.0, semantics="set")
        got = FJ.read(placed_out.read_bytes(), f"L{k + 1}")
        problems += [
            f"{path.stem}: {p}" for p in FJ.differences(got, want, shapes=True, details=True)
        ]
    for (ref, _), (_, angle, _) in zip(footprints, _hand_picked_builder_cases()):
        before = FJ.read(built.read_bytes(), ref)
        after = FJ.read(resaved.read_bytes(), ref)
        problems += [
            f"{ref} at {angle}: {p}"
            for p in FJ.differences(after, before, tolerance_nm=_tolerance(angle))
        ]
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])


# ---------------------------------------------------------------------------
# laws
# ---------------------------------------------------------------------------


@settings(max_examples=sexpr_gen.budget())
@given(E.synthetic_footprints(), G.half_degree_angles(), G.half_degree_angles())
@example(fp=RICH, a=90.0, b=270.0)
def test_footprint_port_laws(fp, a, b):
    """On library footprints at angles that are multiples of half a degree (so the
    arithmetic is exact): four quarter turns through Rotate are the identity in the
    upright form (kicad_ref.footprint.canonical), since they turn upright a keep-upright
    text the library writes at 180 or more; SetOrientation to a then to b is
    SetOrientation to b; KeepUpright is idempotent; Rotate by 0 changes nothing; and
    Rotate and SetOrientation to one angle agree in the upright form only, so a judge
    tells KiCad's two paths apart by comparing texts as KiCad holds them
    (footprint_judge's D2)."""
    library = E.library_footprint(fp)
    at = (10 * MM, 20 * MM)
    start = F.set_position(library, at)
    turned = start
    for _ in range(4):
        turned = F.rotate(turned, 90.0)
    assert F.canonical(turned) == F.canonical(start), "four quarter turns"
    assert F.set_orientation(F.set_orientation(start, a), b) == F.set_orientation(start, b)
    once = F.keep_upright(F.set_orientation(start, a))
    assert F.keep_upright(once) == once, "KeepUpright twice"
    assert F.rotate(start, 0.0) == start, "Rotate by 0"
    assert F.canonical(F.place(library, at, a, semantics="rotate")) == F.canonical(
        F.place(library, at, a, semantics="set")
    )


# ---------------------------------------------------------------------------
# the flip
# ---------------------------------------------------------------------------

#: The user layers of the boards the flip judges use: a front and back pair KiCad matches
#: by the user names' text after the first '.', and a plain user layer.
USER_LAYERS = (
    ("User.1", "front", "F.Assy"),
    ("User.2", "back", "B.Assy"),
    ("User.3", "user", None),
)

#: A library footprint carrying what the flip port has to get right: a chamfered pad at
#: 45, a trapezoid, a pad with a shape offset at 90, a through-hole pad on every copper
#: layer, a custom pad at 30 whose primitives are not symmetric in y, an SMD pad without
#: paste; a keep-upright Reference justified left and top, an unlocked, mirrored Value,
#: texts on F.Cu, on Cmts.User (mirrored), on the front user layer and on the plain one,
#: a keep-upright text written at -90; graphics of every kind, two on the front user
#: layer.
FLIPPY = E.SynFootprint(
    "Flippy",
    (
        E.SynPad("1", (-2 * MM, -MM), 45.0, chamfer=("top_left",)),
        E.SynPad("2", (2 * MM, -MM), 0.0, delta=(0, 200_000)),
        E.SynPad("3", (0, 3 * MM), 90.0, offset=(100_000, 200_000)),
        E.SynPad("4", (-3 * MM, 2 * MM), 0.0, layers=("*.Cu", "*.Mask"), kind="thru_hole"),
        E.SynPad(
            "5",
            (3 * MM, 2 * MM),
            30.0,
            primitives=(
                ("gr_poly", ((0, 0), (200_000, 0), (0, 150_000))),
                ("gr_line", (-100_000, 50_000), (100_000, 120_000)),
                ("gr_arc", (100_000, 0), (70_711, 70_711), (0, 100_000)),
                ("gr_circle", (50_000, 60_000), (120_000, 60_000)),
                ("gr_rect", (-80_000, -30_000), (60_000, 90_000)),
            ),
        ),
        E.SynPad("6", (0, -3 * MM), 0.0, layers=("F.Cu", "F.Mask")),
    ),
    (
        E.SynText(
            "property",
            "Reference",
            "REF**",
            (0, -4 * MM),
            0.0,
            True,
            T.LEFT,
            T.TOP,
            False,
            "F.SilkS",
        ),
        E.SynText(
            "property", "Value", "V", (0, 4 * MM), 0.0, False, T.RIGHT, T.BOTTOM, True, "F.Fab"
        ),
        E.SynText("fp_text", "user", "CU", (MM, MM), 0.0, True, T.CENTER, T.CENTER, False, "F.Cu"),
        E.SynText(
            "fp_text", "user", "CMT", (-MM, MM), 30.0, True, T.RIGHT, T.CENTER, True, "Cmts.User"
        ),
        E.SynText(
            "fp_text", "user", "ASSY", (MM, -MM), 0.0, True, T.CENTER, T.TOP, False, "User.1"
        ),
        E.SynText(
            "fp_text", "user", "PLAIN", (-MM, -MM), 0.0, False, T.LEFT, T.CENTER, False, "User.3"
        ),
        E.SynText(
            "fp_text",
            "user",
            "${REFERENCE}",
            (0, 0),
            -90.0,
            True,
            T.CENTER,
            T.CENTER,
            False,
            "F.Fab",
        ),
    ),
    (
        ("fp_line", (-3 * MM, -2 * MM), (3 * MM, -MM)),
        ("fp_arc", (-MM, 0), (0, -MM), (MM, 0)),
        ("fp_rect", (-2 * MM, -2 * MM), (MM, 3 * MM)),
        ("fp_circle", (MM, 2 * MM), (2 * MM, 2 * MM)),
        ("fp_poly", ((0, 0), (MM, 0), (MM, MM // 2))),
    ),
    ("F.Fab", "User.1", "F.SilkS", "User.1", "F.Fab"),
)

#: Stock footprints the flip needs besides the rotation set: drill offsets on pads at
#: 270, a text on Cmts.User, and custom pads.
FLIP_STOCK = (
    "Oscillator_SMD_Abracon_ABLNO",
    "Buzzer_Mallory_AST1240MLQ",
    "SolderJumper-2_P1.3mm_Open_TrianglePad1.0x1.5mm",
)
_SIDES = ("F.Cu", "B.Cu")
#: A stock footprint and angle the nightly flip judge found: flipped there, its F.Fab
#: rectangle becomes a polygon, one of whose corners pcbnew 9.0.8 writes a nanometre off
#: the port's, so the two outlines are read from different corners
#: (footprint_judge._alignments; 2026-10-11).
NUDGED_CORNER = (
    "PhoenixContact_GMSTB_2,5_10-GF-7,62_1x10_P7.62mm_Horizontal_ThreadedFlange",
    -770.982958300297,
)


def _layered_board(path: Path, *, copper: int = 2, users=USER_LAYERS) -> Path:
    path.write_text(
        E.layered_board_text(copper=copper, users=users), encoding="utf-8", newline="\n"
    )
    return path


def _flip_records(fp: F.Footprint, major: int) -> tuple[list, list]:
    """(pads, texts) of ``fp`` in pcbnew_fp_ops' record shapes, pad layers included."""
    pads = [
        (
            p.number,
            B.fp_relative_to_board(fp.position, fp.orientation, p.local, major=major),
            p.angle,
            p.angle - fp.orientation,
            tuple(sorted(p.layers)),
        )
        for p in fp.pads
    ]
    return pads, _port_records(fp, major)[1]


def _held_flip_problems(
    port: F.Footprint, found: dict, major: int, what: str, tolerance: int
) -> list[str]:
    """Where what pcbnew holds after a flip differs from the port's flip: the
    orientation and side, every pad's angle, footprint-relative angle and layers and every
    text's style and layer exactly (doubles by repr), positions within ``tolerance``."""
    problems = []
    if repr(port.orientation) != repr(found["orientation"]):
        problems.append(
            f"{what}: orientation {port.orientation!r}, pcbnew {found['orientation']!r}"
        )
    if port.layer != found["layer"]:
        problems.append(f"{what}: side {port.layer}, pcbnew {found['layer']}")
    port_pads, port_texts = _flip_records(port, major)
    held_pads = [
        (n, tuple(xy), a, rel, tuple(layers))
        for (n, xy, a, rel), layers in zip(found["pads"], found["pad_layers"])
    ]
    pairs, unpaired = FJ.pair(
        port_pads,
        held_pads,
        lambda p: p[0],
        lambda p: p[1],
        "pad",
        lambda a, b: (abs(a[2] - b[2]), a[4] != b[4]),
    )
    problems += [f"{what}: {u}" for u in unpaired]
    for mine, theirs in pairs:
        near = all(abs(a - b) <= tolerance for a, b in zip(mine[1], theirs[1]))
        if repr(mine[2:]) != repr(theirs[2:]) or not near:
            problems.append(f"{what}: pad port {mine}, pcbnew {theirs}")
    held_texts = _pcbnew_records(found)[1]
    pairs, unpaired = FJ.pair(
        port_texts,
        held_texts,
        lambda t: (t[0], t[3]),
        lambda t: t[1],
        "text",
        lambda a, b: abs(a[2][0] - b[2][0]) + (0 if a[2] == b[2] else 1),
    )
    problems += [f"{what}: {u}" for u in unpaired]
    for mine, theirs in pairs:
        near = mine[1] is None or all(abs(a - b) <= tolerance for a, b in zip(mine[1], theirs[1]))
        if repr(mine[2]) != repr(theirs[2]) or not near:
            problems.append(f"{what}: text port {mine}, pcbnew {theirs}")
    return problems


def _flip_problems(cases, work: Path, *, copper: int = 2, users=USER_LAYERS) -> tuple[int, list]:
    """For each (library footprint file, angle, side): pcbnew places it at the angle
    (Rotate) on a board of ``copper`` copper layers and the user layers ``users``, flips
    it first when the side is B.Cu, saves that start board, then flips every footprint
    on it (FOOTPRINT::Flip about its position, LEFT_RIGHT) and saves again. What pcbnew
    holds after the flip equals the port's flip of the start as the port reads it
    (_held_flip_problems); the angle atoms, unlocked words and justify words SaveBoard
    writes are the port's writer spellings; and the saved footprint reads as the port's
    footprint does once written on that board (kicad_ref.footprint.written: FormatAngle
    keeps ten digits, and a pad on every copper layer of the board is spelt *.Cu, which
    reads back as all 32), pad details and graphics included, positions within
    footprint_judge's D3 tolerance off the quarter turns. Returns (footprints checked,
    problems)."""
    major = _pcbnew_major()
    board_in = _layered_board(work / "layered.kicad_pcb", copper=copper, users=users)
    groups: dict[tuple[float, str], list[tuple[str, Path, tuple[int, int]]]] = {}
    for index, (path, angle, side) in enumerate(cases):
        at = (25 * MM * (1 + index % 8), 25 * MM * (1 + index // 8))
        groups.setdefault((angle, side), []).append((f"U{index + 1}", path, at))
    jobs, starts, outs = [], [], []
    for k, ((angle, side), items) in enumerate(groups.items()):
        start, out = work / f"start{k}.kicad_pcb", work / f"flip{k}.kicad_pcb"
        starts.append(start)
        outs.append(out)
        turn = [("rotate", angle)] + ([("flip", None)] if side == "B.Cu" else [])
        jobs.append(
            OPS.place_job(
                board_in,
                [OPS.place_item(p.parent, p.stem, ref, at, turn) for ref, p, at in items],
                start,
            )
        )
        jobs.append(
            OPS.move_job(
                start, [OPS.move_item(ref, at, ("flip", None)) for ref, _, at in items], out
            )
        )
    results = OPS.run(jobs)
    problems, checked = [], 0
    for k, ((angle, side), items) in enumerate(groups.items()):
        found = results[2 * k + 1]
        before, after = starts[k].read_bytes(), outs[k].read_bytes()
        table = FJ.layer_table(before)
        saved_copper = FJ.copper_count(after)
        tolerance = _tolerance(angle)
        for ref, path, _ in items:
            port = F.board_flip(FJ.read(before, ref, major=major), table, major=major)
            what = f"{path.stem} at {angle} on {side}"
            problems += _held_flip_problems(port, found[ref], major, what, tolerance)
            written = FJ.tokens(FJ.footprint_group(after, ref))
            problems += [
                f"{what}: {p}"
                for p in FJ.token_problems(
                    written, FJ.expected_tokens(port), tolerance_nm=tolerance
                )
            ]
            saved = FJ.read(after, ref, major=major)
            problems += [
                f"{what}: {p}"
                for p in FJ.differences(
                    saved,
                    F.written(port, copper_count=saved_copper),
                    tolerance_nm=tolerance,
                    shapes=True,
                    details=True,
                )
            ]
            checked += 1
    return checked, problems


def test_footprint_port_matches_pcbnew_flip(tmp_path):
    """The curated stock footprints, three more the flip needs (drill offsets, a text on
    Cmts.User, custom pads), RICH, FLIPPY and FLIPPY with a polygon whose outline holds an
    arc (edit_gen.POLY_WITH_ARC), placed by pcbnew at 0, 90, 180, 270, 30,
    -90, 45 and 37.5 degrees on the front, and on the back (flipped once more), and
    NUDGED_CORNER at its angle on the front, then
    flipped by pcbnew's FOOTPRINT::Flip on a board whose User.1 and User.2 are a front and
    back pair: pcbnew's footprint after the flip is kicad_ref.footprint.flip's of the
    start, in memory bit for bit, token for token as saved, and as KiCad reads the saved
    file (pad layers, offsets, deltas, chamfers and primitives, graphics by geometry)."""
    rich = E.write_library(tmp_path / "rich", RICH) / "Rich.kicad_mod"
    flippy = E.write_library(tmp_path / "flippy", FLIPPY) / "Flippy.kicad_mod"
    arcs = tmp_path / "arcs" / "Arcs.pretty" / "Flippy.kicad_mod"
    arcs.parent.mkdir(parents=True)
    arcs.write_text(
        E.with_footprint_child(E.library_text(FLIPPY), E.POLY_WITH_ARC, 1),
        encoding="utf-8",
        newline="\n",
    )
    paths = [E.stock_footprint(name) for name in (*CURATED, *FLIP_STOCK)]
    paths += [rich, flippy, arcs]
    cases = [(path, angle, side) for side in _SIDES for angle in _ANGLES for path in paths]
    cases.append((E.stock_footprint(NUDGED_CORNER[0]), NUDGED_CORNER[1], "F.Cu"))
    checked, problems = _flip_problems(cases, tmp_path)
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])
    if checked != len(cases):
        raise RuntimeError(f"checked {checked} footprints of {len(cases)}")


#: A library footprint with what inner copper layers carry through a flip: a text on
#: In1.Cu, a pad on every copper layer of a four-layer board named one by one, and one on
#: F.Cu and In1.Cu only.
INNER = E.SynFootprint(
    "Inner",
    (
        E.SynPad("1", (-MM, 0), 0.0, layers=("F.Cu", "In1.Cu", "In2.Cu", "B.Cu", "*.Mask")),
        E.SynPad("2", (MM, 0), 0.0, layers=("F.Cu", "In1.Cu", "F.Mask")),
    ),
    (
        E.SynText(
            "property", "Reference", "REF**", (0, -2 * MM), 0.0, True, 0, 0, False, "F.SilkS"
        ),
        E.SynText("fp_text", "user", "IN1", (0, 2 * MM), 0.0, True, 0, 0, False, "In1.Cu"),
    ),
)


@pytest.mark.parametrize("copper", [2, 4, 6])
def test_footprint_port_matches_pcbnew_flip_on_inner_copper(tmp_path, copper):
    """INNER, placed and flipped by pcbnew on boards of two, four and six copper layers:
    the inner layers its text and pads name land where kicad_ref.board_layers says this
    pcbnew's major puts them (KiCad 9 leaves them in place on a board it read; KiCad 10
    mirrors them through the board's inner stack), in memory and as saved (where pad 1,
    on every copper layer of the four-layer board, is spelt *.Cu)."""
    library = E.write_library(tmp_path / "inner", INNER) / "Inner.kicad_mod"
    cases = [(library, angle, "F.Cu") for angle in (0.0, 90.0, 30.0)]
    _, problems = _flip_problems(cases, tmp_path, copper=copper, users=())
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@given(
    st.lists(
        st.tuples(st.integers(0, 10**9), G.angles(), st.sampled_from(_SIDES)),
        min_size=1,
        max_size=8,
    )
)
def test_nightly_footprint_port_matches_pcbnew_flip(picks):
    """The same over sampled stock footprints (none owning a zone) and generated angles."""
    stock = E.stock_footprints_for_turns()
    if not stock:
        pytest.skip("no stock footprint libraries")
    cases = [(stock[k % len(stock)], angle, side) for k, angle, side in picks]
    with tempfile.TemporaryDirectory() as work:
        _, problems = _flip_problems(cases, Path(work))
    assert not problems, "\n".join(problems[:30])


#: Layer tables whose user layers KiCad pairs, or not, by the rules of recalcOpposites
#: (kicad_ref.board_layers), each as (canonical name, type, user name or None).
LAYER_TABLES = {
    "named pair": USER_LAYERS,
    "unnamed consecutive pair": (("User.1", "front", None), ("User.2", "back", None)),
    "one named consecutive pair": (("User.1", "front", "F.Assy"), ("User.2", "back", None)),
    "back then front, named": (("User.1", "back", "B.X"), ("User.2", "front", "F.X")),
    "back then front, unnamed": (("User.1", "back", None), ("User.2", "front", None)),
    "lonely front": (("User.1", "front", "F.Lonely"), ("User.2", "user", None)),
    "named across a gap": (
        ("User.1", "front", "F.Q"),
        ("User.2", "user", None),
        ("User.3", "back", "B.Q"),
    ),
    "unnamed across a gap": (
        ("User.1", "front", None),
        ("User.2", "user", None),
        ("User.3", "back", None),
    ),
    "two pairs": (
        ("User.1", "user", None),
        ("User.2", "front", None),
        ("User.3", "back", None),
        ("User.4", "front", "F.Z"),
        ("User.5", "back", "B.W"),
    ),
    "user names equal to the canonical ones": (
        ("User.1", "front", "User.1"),
        ("User.2", "back", "User.2"),
    ),
}
_LAYER_NAMES = (
    "F.Cu",
    "B.Cu",
    "In1.Cu",
    "In2.Cu",
    "In3.Cu",
    "In4.Cu",
    "In5.Cu",
    "F.SilkS",
    "B.SilkS",
    "F.Mask",
    "B.Paste",
    "F.Adhes",
    "B.CrtYd",
    "F.Fab",
    "Dwgs.User",
    "Cmts.User",
    "Eco1.User",
    "Eco2.User",
    "Edge.Cuts",
    "Margin",
    *(f"User.{k}" for k in range(1, 7)),
)


def test_layer_pairs_port_matches_pcbnew(tmp_path):
    """On boards of two, four and six copper layers with each of LAYER_TABLES' user
    layers: every layer's opposite (BOARD::FlipLayer) and whether a text on it is side
    specific (BOARD_ITEM::IsSideSpecific) are what kicad_ref.board_layers says for this
    pcbnew's major, and the board's copper count is the table's."""
    major = _pcbnew_major()
    boards = []
    for k, (label, users) in enumerate(LAYER_TABLES.items()):
        for copper in (2, 4, 6):
            boards.append(
                (
                    label,
                    copper,
                    _layered_board(
                        tmp_path / f"t{k}_{copper}.kicad_pcb", copper=copper, users=users
                    ),
                )
            )
    results = OPS.run([OPS.layers_job(path, _LAYER_NAMES) for _, _, path in boards])
    problems = []
    for (label, copper, path), found in zip(boards, results):
        table = FJ.layer_table(path.read_bytes())
        if found["copper_count"] != table.copper_count:
            problems.append(f"{label}, {copper} layers: pcbnew counts {found['copper_count']}")
        opposite = BL.opposites(table, major=major)
        for name in _LAYER_NAMES:
            want = [opposite[name], BL.is_side_specific(table, name)]
            if found[name] != want:
                problems.append(
                    f"{label}, {copper} layers, {name}: pcbnew {found[name]}, port {want}"
                )
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])


def test_port_reads_a_footprints_side_as_pcbnew_does(tmp_path):
    """A footprint whose (layer) names In1.Cu, B.Cu, F.Cu, or F.SilkS, on a four-layer
    board: pcbnew holds the first, third and fourth on F.Cu and the second on B.Cu, as
    kicad_ref.footprint reads them (the parser coerces any layer but B.Cu to F.Cu)."""
    major = _pcbnew_major()
    items = []
    for k, layer in enumerate(("In1.Cu", "B.Cu", "F.Cu", "F.SilkS")):
        text = G.footprint_text(f"U{k + 1}", (20 * MM * (k + 1), 20 * MM))
        if text.count('(layer "F.Cu")') != 1:
            raise RuntimeError('geom_gen\'s footprint no longer writes one (layer "F.Cu")')
        items.append(text.replace('(layer "F.Cu")', f'(layer "{layer}")', 1))
    board = tmp_path / "sides.kicad_pcb"
    board.write_text(E.layered_board_text(items, copper=4), encoding="utf-8", newline="\n")
    found = OPS.run([OPS.describe_job(board)])[0]
    data = board.read_bytes()
    problems = []
    for k in range(4):
        ref = f"U{k + 1}"
        port = FJ.read(data, ref, major=major).layer
        if found[ref]["layer"] != port:
            problems.append(f"{ref}: pcbnew {found[ref]['layer']}, port {port}")
    assert not problems, problems


#: A footprint whose texts carry no (layer): Reference, Value and a user field with
#: (effects), a second user field without, and an fp_text; "{side}" is its layer.
_LAYERLESS_TEXTS = """\t(footprint "T:L"
\t\t(layer "{side}")
\t\t(uuid "0000000{k}-0000-4000-8000-00000000d0d0")
\t\t(at {x} 40)
\t\t(property "Reference" "U{k}"
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
\t\t(property "MPN" "X1"
\t\t\t(at 1 0 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1 1)
\t\t\t\t\t(thickness 0.15)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Bare" "X2"
\t\t\t(at 2 0 0)
\t\t)
\t\t(fp_text user "T"
\t\t\t(at -1 0 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1 1)
\t\t\t\t\t(thickness 0.15)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
"""


def test_port_reads_layerless_texts_as_pcbnew_does(tmp_path):
    """Footprints on F.Cu and on B.Cu whose texts carry no (layer): pcbnew holds each
    text on the layer and with the mirror flag kicad_ref.footprint reads (Reference on
    F.SilkS and Value on F.Fab whatever the side; a user field on F.Fab or B.Fab by the
    side, mirrored on the back until an (effects) resets it; an fp_text on F.SilkS or
    B.SilkS by the side)."""
    major = _pcbnew_major()
    items = [
        _LAYERLESS_TEXTS.format(side=side, k=k + 1, x=30 * (k + 1))
        for k, side in enumerate(("F.Cu", "B.Cu"))
    ]
    board = tmp_path / "layerless.kicad_pcb"
    board.write_text(E.layered_board_text(items), encoding="utf-8", newline="\n")
    found = OPS.run([OPS.describe_job(board)])[0]
    data = board.read_bytes()
    problems = []
    for k in range(2):
        ref = f"U{k + 1}"
        port = {t.key: (t.layer, t.style.mirror) for t in FJ.read(data, ref, major=major).texts}
        for key, _, _, _, _, _, mirror, layer in found[ref]["texts"]:
            if port.get(tuple(key)) != (layer, mirror):
                problems.append(
                    f"{ref} {key}: pcbnew ({layer}, {mirror}), port {port.get(tuple(key))}"
                )
    assert not problems, "\n".join(problems)


@settings(max_examples=sexpr_gen.budget())
@given(
    E.synthetic_footprints(pad_details=True),
    G.half_degree_angles(),
    G.half_degree_angles(),
    st.sampled_from(tuple(LAYER_TABLES)),
)
@example(fp=FLIPPY, a=30.0, d=90.0, label="named pair")
def test_flip_port_laws(fp, a, d, label):
    """On library footprints at angles that are multiples of half a degree (so the
    arithmetic is exact), on boards with any of LAYER_TABLES' user layers: two flips are
    the identity in the upright form (kicad_ref.footprint.canonical), on both majors; and
    a flip then a turn to the angle the flip left plus d equals a turn to the start angle
    minus d then a flip, in the upright form (KiCad's group law: a flip conjugates a turn
    into its inverse). Both hold only up to KeepUpright: from a footprint placed at 0, a
    keep-upright text the library writes at 180 or more ends upright."""
    library = E.library_footprint(fp)
    table = BL.LayerTable(tuple(BL.Row(n, t, u or "") for n, t, u in LAYER_TABLES[label]))
    start = F.rotate(F.set_position(library, (10 * MM, 20 * MM)), a)
    for major in (9, 10):
        twice = F.board_flip(F.board_flip(start, table, major=major), table, major=major)
        assert F.canonical(twice) == F.canonical(start), f"two flips, KiCad {major}"
        flipped = F.board_flip(start, table, major=major)
        one = F.rotate(flipped, d)
        other = F.board_flip(F.rotate(start, -d), table, major=major)
        assert F.canonical(one) == F.canonical(other), f"flip then turn, KiCad {major}"


# ---------------------------------------------------------------------------
# zones and points: board coordinates
# ---------------------------------------------------------------------------

#: Stock footprints owning a zone: Tag-Connect's rectangle on F.Cu forbidding footprints,
#: and ESP-01's keepout on every copper layer named one by one.
ZONE_STOCK = ("Tag-Connect_TC2030-IDC-FP_2x03_P1.27mm_Vertical", "ESP-01")
#: How far the move rows of the zone judge move a footprint, and the turn they add.
_ZONE_MOVE = (7_654_321, -3_456_789)
_ZONE_TURN = 75.0


def _zone_port_problems(
    cases: list[tuple[Path, float]], work: Path, *, version: str = "k9"
) -> tuple[int, list]:
    """For each (library footprint file, angle): pcbnew places it on a layered board of
    ``version`` at the angle by Rotate and saves that start board; then, from the start,
    moves every footprint by _ZONE_MOVE and turns it to the angle plus _ZONE_TURN (SetPosition,
    then Rotate by the change) and saves, and flips every footprint (FOOTPRINT::Flip about
    its position, LEFT_RIGHT) and saves. Each saved footprint reads, its zones and points
    exactly and the rest as footprint_judge compares it, as the port's: place with
    Rotate; set_position then rotate; board_flip of the start as saved. Zone layers are
    compared on the board's copper layers, which is what pcbnew's writer enumerates
    (format( ZONE ) cuts a zone's layers to the board's enabled ones,
    pcb_io_kicad_sexpr.cpp:2459-2469 at 9.0.8, 2881-2891 at 10.0.7). Returns (footprints
    checked, problems)."""
    major = _pcbnew_major()
    board_in = work / "layered.kicad_pcb"
    board_in.write_text(
        E.layered_board_text(version=version, users=USER_LAYERS), encoding="utf-8", newline="\n"
    )
    groups: dict[float, list[tuple[str, Path, tuple[int, int]]]] = {}
    for index, (path, angle) in enumerate(cases):
        at = (25 * MM * (1 + index % 8), 25 * MM * (1 + index // 8))
        groups.setdefault(angle, []).append((f"U{index + 1}", path, at))
    jobs, runs = [], []
    for k, (angle, items) in enumerate(groups.items()):
        start = work / f"zstart{k}.kicad_pcb"
        moved, flipped = work / f"zmoved{k}.kicad_pcb", work / f"zflipped{k}.kicad_pcb"
        jobs.append(
            OPS.place_job(
                board_in,
                [
                    OPS.place_item(p.parent, p.stem, ref, at, ("rotate", angle))
                    for ref, p, at in items
                ],
                start,
            )
        )
        moves = [
            OPS.move_item(
                ref,
                (at[0] + _ZONE_MOVE[0], at[1] + _ZONE_MOVE[1]),
                ("rotate_to", angle + _ZONE_TURN),
            )
            for ref, _, at in items
        ]
        jobs.append(OPS.move_job(start, moves, moved))
        jobs.append(
            OPS.move_job(
                start, [OPS.move_item(ref, at, ("flip", None)) for ref, _, at in items], flipped
            )
        )
        runs.append((angle, items, start, moved, flipped))
    OPS.run(jobs)
    problems, checked = [], 0
    for angle, items, start, moved, flipped in runs:
        begun, shifted, turned = start.read_bytes(), moved.read_bytes(), flipped.read_bytes()
        table = FJ.layer_table(begun)
        copper = FJ.copper_count(begun)
        for ref, path, at in items:
            what = f"{path.stem} at {angle}"
            library = FJ.read_library(path.read_bytes())
            held = FJ.read(begun, ref, major=major)
            to = (at[0] + _ZONE_MOVE[0], at[1] + _ZONE_MOVE[1])
            expected = {
                "placed": (begun, F.place(library, at, angle, semantics="rotate", major=major)),
                "moved": (
                    shifted,
                    F.rotate(
                        F.set_position(held, to),
                        (angle + _ZONE_TURN) - held.orientation,
                        major=major,
                    ),
                ),
                "flipped": (turned, F.board_flip(held, table, major=major)),
            }
            for label, (data, port) in expected.items():
                saved = FJ.read(data, ref, major=major)
                if len(saved.zones) != len(library.zones) or len(saved.points) != len(
                    library.points
                ):
                    raise RuntimeError(f"{what} {label}: pcbnew saved another number of zones")
                problems += [
                    f"{what} {label}: {p}"
                    for p in FJ.differences(
                        saved,
                        F.written(port),
                        tolerance_nm=FJ.ROTATION_TOLERANCE_NM,
                        copper_count=copper,
                    )
                ]
            checked += 1
    return checked, problems


def test_zone_port_matches_pcbnew(tmp_path):
    """ZONE_STOCK and edit_gen.ZONED (zones on F.Cu, on every copper layer with a hole, on
    F.Cu and Dwgs.User, on F&B.Cu, some corners off the micrometre grid), placed by pcbnew
    at 0, 90, 180, 270, 30, -90, 45 and 37.5 degrees, then moved and turned, and flipped:
    every zone pcbnew writes is kicad_ref.footprint's, point for point and layer for layer
    (ZONE::Move by the delta, ZONE::Rotate about the footprint through the integer
    RotatePoint, ZONE::Flip mirrored about the footprint then turned by d, as board
    coordinates), and the rest of each footprint as the port's."""
    zoned = E.write_library(tmp_path / "zoned", E.ZONED) / "Zoned.kicad_mod"
    paths = [E.stock_footprint(name) for name in ZONE_STOCK] + [zoned]
    cases = [(path, angle) for angle in _ANGLES for path in paths]
    checked, problems = _zone_port_problems(cases, tmp_path)
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])
    if checked != len(cases):
        raise RuntimeError(f"checked {checked} footprints of {len(cases)}")


def test_point_port_matches_pcbnew(tmp_path):
    """KiCad 10 only (points came with board format 20250901, which pcbnew 9 cannot load):
    edit_gen.POINTED placed by pcbnew at the zone judge's angles, moved and turned, and
    flipped: every point pcbnew writes is at the board position kicad_ref.footprint puts
    it and on its layer (PCB_POINT::Rotate, PCB_POINT::Flip, pcbnew/pcb_point.cpp:138-149
    at 10.0.7)."""
    if _pcbnew_major() < 10:
        pytest.skip(f"pcbnew {PJ.major()} has no points (KiCad 10's board format 20250901)")
    pointed = E.write_library(tmp_path / "pointed", E.POINTED) / "Pointed.kicad_mod"
    cases = [(pointed, angle) for angle in _ANGLES]
    checked, problems = _zone_port_problems(cases, tmp_path, version="k10")
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])
    if checked != len(cases):
        raise RuntimeError(f"checked {checked} footprints of {len(cases)}")


def test_builder_zones_load_in_pcbnew_unchanged(tmp_path):
    """The premise of building zone boards with edit_gen: edit_gen.ZONED written in KiCad's
    board form at the zone judge's angles, loaded and saved again by pcbnew, reads as
    before, its zones exactly, on boards of both majors pcbnew loads."""
    major = _pcbnew_major()
    for version in ("k9", "k10") if major >= 10 else ("k9",):
        items = [
            E.board_footprint_text(
                E.ZONED, f"U{k + 1}", (25 * MM * (k + 1), 40 * MM), angle, version=version
            )
            for k, angle in enumerate(_ANGLES)
        ]
        built = tmp_path / f"built_{version}.kicad_pcb"
        built.write_text(
            E.layered_board_text(items, version=version), encoding="utf-8", newline="\n"
        )
        resaved = tmp_path / f"resaved_{version}.kicad_pcb"
        moves = [
            OPS.move_item(f"U{k + 1}", (25 * MM * (k + 1), 40 * MM)) for k in range(len(_ANGLES))
        ]
        OPS.run([OPS.move_job(built, moves, resaved)])
        copper = FJ.copper_count(built.read_bytes())
        problems = []
        for k, angle in enumerate(_ANGLES):
            before = FJ.read(built.read_bytes(), f"U{k + 1}", major=major)
            after = FJ.read(resaved.read_bytes(), f"U{k + 1}", major=major)
            problems += [
                f"{version} at {angle}: {p}"
                for p in FJ.differences(
                    after, before, tolerance_nm=_tolerance(angle), copper_count=copper
                )
            ]
        assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])


@settings(max_examples=sexpr_gen.budget())
@given(
    E.synthetic_footprints(zones=True),
    G.half_degree_angles(),
    st.tuples(G.nm_coordinates(100 * MM), G.nm_coordinates(100 * MM)),
)
@example(fp=E.ZONED, a=30.0, d=(MM, -2 * MM))
def test_zone_port_laws(fp, a, d):
    """On library footprints owning zones, at angles that are multiples of half a degree:
    a flip takes every zone point (x, y) to (2 fx - x, y), the mirror about the
    footprint's y followed by the half turn d, which is exactly 180 there, and each layer
    to its opposite; two flips are the identity for every zone, on both majors; four
    quarter turns are; a move by d and back is; and a footprint placed at the origin
    unturned holds its zones where its library writes them."""
    library = E.library_footprint(fp)
    assert F.place(library, (0, 0), 0.0, semantics="rotate").zones == library.zones
    table = BL.LayerTable(tuple(BL.Row(n, t, u or "") for n, t, u in USER_LAYERS))
    start = F.rotate(F.set_position(library, (10 * MM, 20 * MM)), a)
    fx = start.position[0]
    for major in (9, 10):
        opposite = BL.opposites(table, major=major)
        flipped = F.board_flip(start, table, major=major)
        assert [(z.layers, z.polygons) for z in flipped.zones] == [
            (
                frozenset(opposite[layer] for layer in z.layers),
                tuple(tuple((2 * fx - x, y) for x, y in polygon) for polygon in z.polygons),
            )
            for z in start.zones
        ], f"one flip, KiCad {major}"
        twice = F.board_flip(flipped, table, major=major)
        assert twice.zones == start.zones, f"two flips, KiCad {major}"
        turned = start
        for _ in range(4):
            turned = F.rotate(turned, 90.0, major=major)
        assert turned.zones == start.zones, f"four quarter turns, KiCad {major}"
    there = F.set_position(start, (start.position[0] + d[0], start.position[1] + d[1]))
    assert F.set_position(there, start.position).zones == start.zones


# ---------------------------------------------------------------------------
# placements as edit_gen writes them
# ---------------------------------------------------------------------------

_BUILDER_ANGLES = (0.0, 90.0, 180.0, 30.0, -90.0, 37.5)


def _builder_placement_problems(
    cases: list[tuple[E.SynFootprint, float, str]], work: Path
) -> list[str]:
    """For each (library footprint, angle, side): edit_gen.board_footprint_text writes it at
    the angle on that side of a board whose User.1 and User.2 are a front and back pair,
    and pcbnew places the same library footprint there (FootprintLoad, SetPosition, Flip
    for the back, then Rotate to the angle) and saves it. Where the two read apart: pads
    with their layers, offsets, deltas, chamfers and primitives, texts, graphics by
    geometry and zones exactly, positions within footprint_judge's D3 tolerance off the
    quarter turns, the mandatory fields the builder does not write aside."""
    major = _pcbnew_major()
    board_in = _layered_board(work / "layered.kicad_pcb")
    table = FJ.layer_table(board_in.read_bytes())
    built, items = [], []
    for index, (fp, angle, side) in enumerate(cases):
        at = (25 * MM * (1 + index % 6), 25 * MM * (1 + index // 6))
        ref = f"U{index + 1}"
        library = E.write_library(work / f"lib{index}", fp)
        built.append(E.board_footprint_text(fp, ref, at, angle, side=side, table=table))
        turn = [("rotate", angle)] if side == "F.Cu" else [("flip", None), ("rotate_to", angle)]
        items.append(OPS.place_item(library, fp.name, ref, at, turn))
    ours = work / "built.kicad_pcb"
    ours.write_text(E.layered_board_text(built, users=USER_LAYERS), encoding="utf-8", newline="\n")
    theirs = work / "placed.kicad_pcb"
    OPS.run([OPS.place_job(board_in, items, theirs)])
    copper = FJ.copper_count(theirs.read_bytes())
    problems = []
    for index, (fp, angle, side) in enumerate(cases):
        ref = f"U{index + 1}"
        found = FJ.differences(
            FJ.read(ours.read_bytes(), ref, major=major),
            FJ.read(theirs.read_bytes(), ref, major=major),
            tolerance_nm=_tolerance(angle),
            shapes=True,
            details=True,
            written_fields_only=True,
            copper_count=copper,
        )
        problems += [f"{fp.name} at {angle} on {side}: {p}" for p in found]
    return problems


def test_builder_footprints_are_pcbnews_placement(tmp_path):
    """The premise of building boards with edit_gen's board_footprint_text: RICH, FLIPPY and
    edit_gen.ZONED, which carry every pad detail, text kind and zone a flip changes,
    written at _BUILDER_ANGLES on the front and on the back, read as pcbnew's own
    placement there (_builder_placement_problems)."""
    cases = [
        (fp, angle, side)
        for fp in (RICH, FLIPPY, E.ZONED)
        for angle in _BUILDER_ANGLES
        for side in _SIDES
    ]
    problems = _builder_placement_problems(cases, tmp_path)
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@given(
    st.lists(
        st.tuples(
            E.synthetic_footprints(pad_details=True, primitives=True, zones=True),
            G.angles(),
            st.sampled_from(_SIDES),
        ),
        min_size=1,
        max_size=6,
    )
)
def test_nightly_builder_footprints_are_pcbnews_placement(cases):
    """The same over generated footprints, pad details, custom pads and zones included, at
    generated angles on either side."""
    with tempfile.TemporaryDirectory() as work:
        problems = _builder_placement_problems(cases, Path(work))
    assert not problems, "\n".join(problems[:30])


# ---------------------------------------------------------------------------
# the swap
# ---------------------------------------------------------------------------

#: Stock footprints a symbol's footprint changes between: SMD pairs whose pads stand at 0
#: in their libraries, a through-hole pair, new footprints whose library pads stand at 90
#: (D_SMC) and 270 (Diodes_DFN1006-3), a non-plated hole without a number to a plated
#: pad, and a footprint with numberless paste-only pads and two pads of one number
#: (TO-252-2).
SWAP_PAIRS = (
    ("R_0603_1608Metric", "R_0805_2012Metric"),
    ("SOT-23", "SOT-23-3"),
    ("PinHeader_1x02_P2.54mm_Vertical", "PinSocket_1x02_P2.54mm_Vertical"),
    ("D_SOD-123", "D_SMC"),
    ("SOT-23", "Diodes_DFN1006-3"),
    ("MountingHole_3.2mm_M3", "MountingHole_3.2mm_M3_Pad"),
    ("R_0805_2012Metric", "TO-252-2"),
)
_SWAP_ANGLES = (0.0, 90.0, 180.0, 150.0)


def _copper_text(name: str) -> tuple:
    return (
        E.SynText(
            "property", "Reference", "REF**", (0, -3 * MM), 0.0, True, 0, 0, False, "F.SilkS"
        ),
        E.SynText("property", "Value", name, (0, 3 * MM), 0.0, True, 0, 0, False, "F.Fab"),
        E.SynText("property", "MPN", "X1", (MM, 2 * MM), 0.0, True, 0, 0, False, "F.Fab", True),
        E.SynText("fp_text", "user", "${REFERENCE}", (0, 0), 0.0, True, 0, 0, False, "F.Fab"),
        E.SynText("fp_text", "user", "NOTE", (-MM, MM), 0.0, True, 0, 0, False, "Cmts.User"),
        E.SynText("fp_text", "user", "NOTE", (MM, -MM), 0.0, True, 0, 0, False, "F.SilkS"),
    )


#: Pads whose file gives one number to a copper pad and to a pad KiCad reads without one
#: (kicad_ref.footprint): pad 1 on copper and on the mask only, pad 2 a plated hole and a
#: non-plated one without an annular ring, so only the copper pad of each number carries
#: an old uuid; a new pad 3 with no old counterpart; a hidden user field; texts of one
#: content on two layers, which getMatchingTextItem tells apart by layer.
COPPER_OLD = E.SynFootprint(
    "CopperOld",
    (
        E.SynPad("1", (-2 * MM, 0), 0.0, layers=("F.Cu", "F.Mask")),
        E.SynPad("1", (-2 * MM, 2 * MM), 0.0, layers=("F.Mask",)),
        E.SynPad("2", (2 * MM, 0), 0.0, layers=("*.Cu", "*.Mask"), kind="np_thru_hole"),
        E.SynPad("2", (2 * MM, 2 * MM), 0.0, layers=("*.Cu", "*.Mask"), kind="thru_hole"),
    ),
    _copper_text("CopperOld"),
)
COPPER_NEW = E.SynFootprint(
    "CopperNew",
    (
        E.SynPad("1", (-MM, -MM), 0.0, layers=("F.Mask",)),
        E.SynPad("1", (-MM, MM), 90.0, layers=("F.Cu", "F.Mask")),
        E.SynPad("2", (MM, -MM), 0.0, layers=("*.Cu", "*.Mask"), kind="thru_hole"),
        E.SynPad("2", (MM, MM), 0.0, layers=("*.Cu", "*.Mask"), kind="np_thru_hole"),
        E.SynPad("3", (0, 2 * MM), 0.0),
    ),
    _copper_text("CopperNew"),
)


def _with_group(data: bytes, members: list[str]) -> bytes:
    """The board ``data`` with a group listing the footprints ``members`` (references) by
    uuid, in the layout KiCad 9.0.8's writer gives a group (harvested from its
    multichannel_mixer demo), before the board's closing parenthesis."""
    uuids = [X.read_ids(FJ.footprint_group(data, ref)).footprint for ref in members]
    if None in uuids:
        raise RuntimeError(f"a footprint among {members} has no uuid")
    listed = " ".join(f'"{u}"' for u in uuids)
    group = '\t(group ""\n\t\t(uuid "00000000-0000-4000-8000-00000000c0c0")\n'
    group += f"\t\t(members {listed})\n\t)\n"
    end = data.rindex(b")")
    return data[:end] + group.encode() + data[end:]


def _groups(data: bytes) -> dict[str, frozenset[str]]:
    """Each top-level group of a board, by its uuid, with its members' uuids."""
    out = {}
    for item in F.children(FJ.root(data), b"group"):
        members = F.child(item, b"members")
        uid = X.read_ids(item).footprint
        out[uid] = frozenset(
            t.value.decode() for t in (members.items[1:] if members is not None else ())
        )
    return out


def _exchange_problems(cases, work: Path) -> tuple[int, list]:
    """For each (old library footprint file, new library footprint file, angle, side):
    pcbnew places the old footprint at the angle on that side of a board whose User.1 and
    User.2 are a front and back pair, saves it, and the board gains a group listing every
    other footprint; then pcbnew replays the exchange of each footprint for the new one
    (pcbnew_fp_ops.exchange_job). Each footprint pcbnew saves reads as kicad_ref.exchange's
    of the old footprint as read and the new library file for pcbnew's major (as KiCad
    writes it; texts as KiCad holds them, pads with every detail, graphics by geometry,
    positions within the D3 tolerance off the quarter turns); its uuids are the ones
    carried_ids says it carries by KiCad 9.0.8's rules, which the replay follows;
    every group lists the same uuids as before and each still names a footprint; and
    pcbnew's IsOnCopperLayer of each new pad is kicad_ref.exchange.is_on_copper's.
    Returns (footprints checked, problems)."""
    major = _pcbnew_major()
    board_in = _layered_board(work / "layered.kicad_pcb")
    groups: dict[tuple[float, str], list] = {}
    for index, (old_path, new_path, angle, side) in enumerate(cases):
        at = (25 * MM * (1 + index % 8), 25 * MM * (1 + index // 8))
        groups.setdefault((angle, side), []).append((f"U{index + 1}", old_path, new_path, at))
    placed = []
    jobs = []
    for k, ((angle, side), items) in enumerate(groups.items()):
        start = work / f"swap_start{k}.kicad_pcb"
        turn = [("rotate", angle)] if side == "F.Cu" else [("flip", None), ("rotate_to", angle)]
        jobs.append(
            OPS.place_job(
                board_in,
                [OPS.place_item(p.parent, p.stem, ref, at, turn) for ref, p, _, at in items],
                start,
            )
        )
        placed.append(start)
    OPS.run(jobs)
    jobs, outs, grouped = [], [], []
    for k, ((angle, side), items) in enumerate(groups.items()):
        start = work / f"swap_grouped{k}.kicad_pcb"
        data = placed[k].read_bytes()
        start.write_bytes(_with_group(data, [ref for ref, *_ in items[1:]] or [items[0][0]]))
        out = work / f"swap_out{k}.kicad_pcb"
        jobs.append(
            OPS.exchange_job(
                start,
                [OPS.exchange_item(ref, new.parent, new.stem) for ref, _, new, _ in items],
                out,
            )
        )
        outs.append(out)
        grouped.append(start)
    results = OPS.run(jobs)
    problems, checked = [], 0
    for k, ((angle, side), items) in enumerate(groups.items()):
        before, after = grouped[k].read_bytes(), outs[k].read_bytes()
        table = FJ.layer_table(before)
        copper = FJ.copper_count(before)
        if _groups(after) != _groups(before):
            problems.append(f"{side} at {angle}: groups {_groups(after)}, were {_groups(before)}")
        footprint_ids = {
            X.read_ids(fp).footprint for fp in F.children(FJ.root(after), b"footprint")
        }
        for members in _groups(after).values():
            if not members <= footprint_ids:
                problems.append(f"{side} at {angle}: a group lists {members - footprint_ids}")
        for ref, old_path, new_path, _ in items:
            what = f"{old_path.stem} to {new_path.stem} on {side} at {angle}"
            old_group = FJ.footprint_group(before, ref)
            old = FJ.read(before, ref, major=major)
            new_group = FJ.root(new_path.read_bytes())
            library = F.read_footprint(new_group, major=major, copper_count=copper)
            port = X.exchange(old, library, table, major=major)
            saved_group = FJ.footprint_group(after, ref)
            saved = FJ.read(after, ref, major=major)
            problems += [
                f"{what}: {p}"
                for p in FJ.differences(
                    saved,
                    F.written(port),
                    tolerance_nm=_tolerance(angle),
                    shapes=True,
                    details=True,
                    copper_count=copper,
                )
            ]
            old_ids = X.read_ids(old_group)
            problems += [
                f"{what}: {p}"
                for p in FJ.uuid_problems(
                    saved,
                    X.read_ids(saved_group),
                    port,
                    X.carried_ids(old_group, old, new_group, port, copper),
                    {u for u in (*old_ids.pads, *old_ids.texts) if u},
                )
            ]
            held = results[k][ref]["pad_copper"]
            want = [X.is_on_copper(g, copper) for g in F.pad_groups(new_group)]
            if held != want:
                problems.append(f"{what}: pcbnew's pads on copper {held}, the port's {want}")
            checked += 1
    return checked, problems


def test_exchange_port_matches_a_pcbnew_replay(tmp_path):
    """SWAP_PAIRS, COPPER_OLD to COPPER_NEW and RICH to FLIPPY, the old footprint placed by
    pcbnew at 0, 90, 180 and 150 degrees on the front and on the back: pcbnew replaying
    ExchangeFootprint's steps (KiCad's own Flip, SetOrientation, FindPadByNumber and
    IsOnCopperLayer) leaves each footprint as kicad_ref.exchange.exchange puts it, with the
    uuids carried_ids says it carries, its group intact, and its pads on copper as
    is_on_copper says."""
    synthetic = []
    for k, (old, new) in enumerate(((COPPER_OLD, COPPER_NEW), (RICH, FLIPPY))):
        synthetic.append(
            (
                E.write_library(tmp_path / f"old{k}", old) / f"{old.name}.kicad_mod",
                E.write_library(tmp_path / f"new{k}", new, library="Swap")
                / f"{new.name}.kicad_mod",
            )
        )
    pairs = [(E.stock_footprint(a), E.stock_footprint(b)) for a, b in SWAP_PAIRS] + synthetic
    cases = [
        (old, new, angle, side) for side in _SIDES for angle in _SWAP_ANGLES for old, new in pairs
    ]
    checked, problems = _exchange_problems(cases, tmp_path)
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])
    if checked != len(cases):
        raise RuntimeError(f"checked {checked} footprints of {len(cases)}")


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@given(
    st.lists(
        st.tuples(
            st.integers(0, 10**9),
            st.integers(0, 10**9),
            G.angles(),
            st.sampled_from(_SIDES),
        ),
        min_size=1,
        max_size=6,
    )
)
def test_nightly_exchange_port_matches_a_pcbnew_replay(picks):
    """The same over sampled stock footprint pairs (none owning a zone) at generated angles
    on either side."""
    stock = E.stock_footprints_for_turns()
    if not stock:
        pytest.skip("no stock footprint libraries")
    cases = [
        (stock[a % len(stock)], stock[b % len(stock)], angle, side) for a, b, angle, side in picks
    ]
    with tempfile.TemporaryDirectory() as work:
        _, problems = _exchange_problems(cases, Path(work))
    assert not problems, "\n".join(problems[:30])


def test_lock_port_matches_pcbnew(tmp_path):
    """A footprint in each of edit_gen.LOCKS' spellings, and one with none: pcbnew holds
    it locked exactly when edit_gen.LOCKS says, and so does kicad_ref.exchange.is_locked
    (parseMaybeAbsentBool( true )); kicad-cli loads the board (the autouse oracle)."""
    _pcbnew_major()
    spellings = list(E.LOCKS)
    items = [
        E.with_lock(E.board_footprint_text(RICH, f"U{k + 1}", (20 * MM * (k + 1), 30 * MM), 0.0), s)
        for k, s in enumerate(spellings)
    ]
    items.append(E.board_footprint_text(RICH, "U0", (20 * MM, 60 * MM), 0.0))
    board = tmp_path / "locks.kicad_pcb"
    board.write_text(E.layered_board_text(items), encoding="utf-8", newline="\n")
    found = OPS.run([OPS.describe_job(board)])[0]
    data = board.read_bytes()
    problems = []
    for k, spelling in [(-1, None), *enumerate(spellings)]:
        ref = f"U{k + 1}"
        want = E.LOCKS.get(spelling, False)
        port = X.is_locked(FJ.footprint_group(data, ref))
        if (found[ref]["locked"], port) != (want, want):
            problems.append(f"{spelling}: pcbnew {found[ref]['locked']}, port {port}, want {want}")
    assert not problems, problems


def _netlist_reading(data: bytes) -> tuple[list, list]:
    """What the board update reads of a kicadxml netlist, by ElementTree: each component's
    reference, value, footprint, fields, the properties the update acts on, sheet path and
    uuids; each net's name and nodes. The netlists read are kicad-cli's and the builder's,
    both written by the test into its own directory, as conftest reads kicad-cli's."""
    root = ET.fromstring(data)
    acted = ("Sheetfile", "ki_fp_filters", "dnp", "exclude_from_bom", "exclude_from_board")
    comps = []
    for comp in root.findall("./components/comp"):
        sheetpath = comp.find("sheetpath")
        comps.append(
            (
                comp.get("ref"),
                comp.findtext("value"),
                comp.findtext("footprint"),
                tuple((f.get("name"), f.text or "") for f in comp.findall("./fields/field")),
                tuple(
                    (p.get("name"), p.get("value") or "")
                    for p in comp.findall("property")
                    if p.get("name") in acted
                ),
                None if sheetpath is None else (sheetpath.get("names"), sheetpath.get("tstamps")),
                comp.findtext("tstamps"),
            )
        )
    nets = [
        (net.get("name"), tuple((n.get("ref"), n.get("pin")) for n in net.findall("node")))
        for net in root.findall("./nets/net")
    ]
    return comps, nets


@requires_cli
def test_netlist_builder_matches_a_kicad_cli_export(tmp_path):
    """The premise of building netlists with edit_gen.netlist_text: the native schematic
    fixture with R1's footprint assigned, exported by kicad-cli as kicadxml, reads, in every
    element the board update reads, as the netlist the builder writes for the same symbol
    (its value, footprint, Datasheet and Description fields, filters, sheet file, uuid and
    its two unconnected pins' nets)."""
    source = (G.FIXTURES / "kicad_native.kicad_sch").read_text(encoding="utf-8")
    plain = '(property "Footprint" "" (at 98.222 100 90)'
    if source.count(plain) != 1:
        raise RuntimeError(f"the fixture holds {plain!r} {source.count(plain)} times")
    fpid = "Resistor_SMD:R_0805_2012Metric"
    schematic = tmp_path / "native.kicad_sch"
    schematic.write_text(
        source.replace(plain, plain.replace('""', f'"{fpid}"')), encoding="utf-8", newline="\n"
    )
    exported = tmp_path / "out" / "native.xml"
    exported.parent.mkdir()
    result = _run_cli(
        [
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "--output",
            str(exported),
            str(schematic),
        ],
        check=False,
    )
    if result.returncode != 0 or not exported.is_file():
        raise RuntimeError(f"kicad-cli exported no netlist (rc={result.returncode})")
    comps, nets = _netlist_reading(exported.read_bytes())
    if len(comps) != 1:
        raise RuntimeError(f"kicad-cli exported {len(comps)} components, not R1 alone")
    # The symbol's uuid is the fixture's, and the sheet file kicad-cli names relative to
    # where it runs, so both are taken from its export; the rest is the builder's own.
    tstamp, sheetfile = comps[0][6], dict(comps[0][4]).get("Sheetfile", "")
    built = E.netlist_text(
        [
            E.NetComponent(
                "R1",
                "10K",
                fpid,
                tstamp,
                (("Datasheet", "~"), ("Description", "Resistor")),
                "R_*",
                sheetfile,
            )
        ],
        [("unconnected-(R1-Pad1)", [("R1", "1")]), ("unconnected-(R1-Pad2)", [("R1", "2")])],
    )
    assert _netlist_reading(built.encode()) == (comps, nets)


# ---------------------------------------------------------------------------
# the judges' own contracts
# ---------------------------------------------------------------------------


def test_pcbnew_ops_raise_runtimeerror_on_a_failed_call():
    """A job naming a function pcbnew lacks raises RuntimeError, never AssertionError, so
    a broken pcbnew call can never pass for a defect a strict xfail expects."""
    _pcbnew_major()
    with pytest.raises(RuntimeError):
        OPS.run([OPS.call_job("NoSuchFunctionInPcbnew")])


_NON_FINITE = ("inf", "-inf", "nan")


@requires_cli
def test_footprint_port_refuses_a_non_finite_angle_as_kicad_cli_does():
    """A footprint whose (at) angle is inf, -inf or nan: kicad-cli writes no DRC report
    for the board, which is how it answers a file it cannot load (the parser takes the
    angle only as a NUMBER token and expects ')' otherwise; measured with pcbnew and
    kicad-cli 9.0.8 in the step 7 design, 2026-10-11), and the port refuses the same
    footprint. A kicad-cli that loads one is a broken premise of the tests that require
    the tools to refuse such an angle (RuntimeError)."""
    for token in _NON_FINITE:
        text = G.footprint_text("U1", (100 * MM, 100 * MM))
        plain = f"(at {G.mm(100 * MM)} {G.mm(100 * MM)})"
        if text.count(plain) != 1:
            raise RuntimeError(f"geom_gen's footprint holds {plain!r} {text.count(plain)} times")
        text = text.replace(plain, plain[:-1] + f" {token})")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
            board = _write_board(Path(work) / "angle.kicad_pcb", [text])
            report = Path(work) / "out" / "drc.json"
            report.parent.mkdir()
            result = _run_cli(
                ["pcb", "drc", "--format", "json", "--output", str(report), str(board)],
                check=False,
            )
            if report.is_file():
                raise RuntimeError(
                    f"kicad-cli loaded a footprint at angle {token} (rc={result.returncode})"
                )
            data = board.read_bytes()
        with pytest.raises(P.ParseRefusal):
            FJ.read(data, "U1")
