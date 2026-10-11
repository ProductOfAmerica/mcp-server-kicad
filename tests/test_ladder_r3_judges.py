"""Rung 3's judges for footprint orientation edits, each checked against KiCad itself.

The ports kicad_ref.footprint, kicad_ref.pad and kicad_ref.pcb_text say what KiCad holds
of a footprint and what its own SetOrientation and Rotate do to it; here they answer to
pcbnew doing the same edit (FootprintLoad, SetPosition, then Rotate or SetOrientation),
in the doubles pcbnew holds in memory and in the tokens SaveBoard writes, and to pcbnew
reading a board. edit_gen's builders answer to pcbnew loading what they write and saving
it back unchanged. pcbnew_fp_ops answers to its own contract (a failed call is a
RuntimeError), and the footprint port's refusal of a non-finite angle to kicad-cli.

Only the parts step 7's rotation slice needs are ported and checked here: the flip, the
swap and footprint zones come with the slices that fix them.
"""

from __future__ import annotations

import tempfile
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
from kicad_ref import footprint as F
from kicad_ref import parse_units as P
from kicad_ref import pcb_text as T

from mcp_server_kicad._shared import _run_cli

MM = 1_000_000

#: The rung 3 plan's curated stock footprints, less the zone-owning Tag-Connect, whose
#: zone is ported with the board-coordinates slice; and Texas_DSBGA-5, whose library
#: writes a text at -90.
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
        problems += [f"{path.stem}: {p}" for p in FJ.differences(got, want, shapes=True)]
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
    arithmetic is exact): four quarter turns through Rotate are the identity; SetOrientation
    to a then to b is SetOrientation to b; KeepUpright is idempotent; Rotate by 0 changes
    nothing; and Rotate and SetOrientation to one angle agree in the comparison form (D2),
    the reason a test may judge either of KiCad's two paths by one port."""
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
