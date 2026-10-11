"""Footprints for the single-element edit tests: generated, written as KiCad writes them.

A ``SynFootprint`` is a library footprint described by value: pads (number, position,
the angle its library file writes), texts (fields and fp_text items with a position, an
angle, the keep-upright flag, justification, mirror and layer) and graphics (geom_gen
shape tuples). From one:

- ``library_text`` writes it as a .kicad_mod in KiCad 9's library form and
  ``write_library`` puts that in a .pretty directory;
- ``board_footprint_text`` writes the footprint KiCad holds once it has placed that
  library footprint at a position and an angle (kicad_ref.footprint.place, Rotate or
  SetOrientation), in the board form KiCad saves: the footprint's angle Normalize180 and
  left out at 0, pad and text angles absolute, every position footprint-relative.

Every number is spelled as KiCad spells it (FormatInternalUnits and FormatAngle through
geom_gen and kicad_ref), never through kiutils or the code under test; whether KiCad reads
these footprints as written is checked against pcbnew in test_ladder_r3_judges.py.

``synthetic_footprints()`` draws them. ``stock_footprint(name)`` finds a footprint KiCad
ships by name across the stock .pretty directories; ``stock_footprints()`` lists them all.
"""

from __future__ import annotations

import functools
import itertools
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import geom_gen as G
import kicad_corpus as K
import pytest
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import footprint as F
from kicad_ref import pad as PAD
from kicad_ref import pcb_text as T
from kicad_ref.eda_units import format_angle

Point = tuple[int, int]
MM = 1_000_000


class SynPad(NamedTuple):
    number: str
    local: Point
    angle: float  # as the library file writes it, any value


class SynText(NamedTuple):
    """A field (``head`` "property", ``name`` its name) or an fp_text (``head``
    "fp_text", ``name`` its type, "user"), with the angle its library file writes."""

    head: str
    name: str
    text: str
    local: Point
    angle: float
    keep_upright: bool
    h: int
    v: int
    mirror: bool
    layer: str
    hidden: bool = False


class SynFootprint(NamedTuple):
    name: str
    pads: tuple[SynPad, ...]
    texts: tuple[SynText, ...]
    shapes: tuple[tuple, ...] = ()


_uid_counter = itertools.count(1)


def _uid() -> str:
    return f"{next(_uid_counter):08x}-0000-4000-8000-00000000e7e7"


def _pt(p: Point) -> str:
    return f"{G.mm(p[0])} {G.mm(p[1])}"


def _effects(t: str, style: T.Style) -> str:
    words = T.justify_words(style)
    justify = f"{t}\t(justify {' '.join(words)})\n" if words else ""
    return (
        f"{t}(effects\n{t}\t(font\n{t}\t\t(size 1 1)\n{t}\t\t(thickness 0.15)\n{t}\t)\n"
        f"{justify}{t})\n"
    )


def _text(depth: int, syn: SynText, text: str, angle_atom: str, style: T.Style) -> str:
    """A field or fp_text in format( PCB_TEXT )'s order: (at), (unlocked), (layer),
    (hide), (uuid), (effects)."""
    t = "\t" * depth
    if syn.head == "property":
        opening = f'{t}(property "{syn.name}" "{text}"\n'
    else:
        opening = f'{t}(fp_text {syn.name} "{text}"\n'
    return (
        opening
        + f"{t}\t(at {_pt(syn.local)} {angle_atom})\n"
        + ("" if style.keep_upright else f"{t}\t(unlocked yes)\n")
        + f'{t}\t(layer "{syn.layer}")\n'
        + (f"{t}\t(hide yes)\n" if syn.hidden else "")
        + f'{t}\t(uuid "{_uid()}")\n'
        + _effects(t + "\t", style)
        + f"{t})\n"
    )


def _pad(depth: int, syn: SynPad, angle_atom: str | None) -> str:
    t = "\t" * depth
    angle = f" {angle_atom}" if angle_atom is not None else ""
    return (
        f'{t}(pad "{syn.number}" smd rect\n'
        f"{t}\t(at {_pt(syn.local)}{angle})\n"
        f"{t}\t(size 0.5 0.8)\n"
        f'{t}\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
        f"{t})\n"
    )


def _library_style(syn: SynText) -> T.Style:
    return T.Style(syn.angle, syn.keep_upright, syn.h, syn.v, syn.mirror)


def library_text(fp: SynFootprint) -> str:
    """``fp`` as a KiCad 9 .kicad_mod: fields, (attr), fp_text items, graphics, pads, in the
    order format( FOOTPRINT ) writes them, every angle as the library writes it (any
    value, FormatAngle's spelling; a pad's left out at 0)."""
    fields = [s for s in fp.texts if s.head == "property"]
    others = [s for s in fp.texts if s.head != "property"]
    body = [
        f'(footprint "{fp.name}"\n',
        "\t(version 20241229)\n",
        '\t(generator "pcbnew")\n',
        '\t(generator_version "9.0")\n',
        '\t(layer "F.Cu")\n',
    ]
    for syn in fields:
        body.append(_text(1, syn, syn.text, format_angle(syn.angle), _library_style(syn)))
    body.append("\t(attr smd)\n")
    for syn in others:
        body.append(_text(1, syn, syn.text, format_angle(syn.angle), _library_style(syn)))
    body += [G.shape_text(shape, layer="F.Fab", depth=1) for shape in fp.shapes]
    for syn in fp.pads:
        body.append(_pad(1, syn, None if syn.angle == 0 else format_angle(syn.angle)))
    body.append("\t(embedded_fonts no)\n)\n")
    return "".join(body)


def write_library(directory, fp: SynFootprint, *, library: str = "Ladder") -> Path:
    """Write <directory>/<library>.pretty/<fp.name>.kicad_mod; return the .pretty path."""
    pretty = Path(directory) / f"{library}.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    (pretty / f"{fp.name}.kicad_mod").write_text(library_text(fp), encoding="utf-8", newline="\n")
    return pretty


def library_footprint(fp: SynFootprint) -> F.Footprint:
    """``fp`` as KiCad reads its library file."""
    data = library_text(fp).encode()
    return F.read_footprint(L.tree(data, knows_bar=L.knows_bar_of(data))[0], major=10)


def board_footprint_text(
    fp: SynFootprint,
    ref: str,
    at_nm: Point,
    angle: float,
    *,
    semantics: str = "rotate",
    value: str = "V",
    library: str = "Ladder",
) -> str:
    """The footprint KiCad holds after placing ``fp`` at ``at_nm`` and turning it to
    ``angle`` (``semantics`` "rotate" or "set", kicad_ref.footprint.place), as KiCad saves
    it on a board (depth 1); the Reference field reads ``ref`` and the Value ``value``.
    Positions stay footprint-relative as the library writes them (KiCad's own rounding of
    them off the quarter turns is not modelled, footprint_judge D3)."""
    placed = F.place(library_footprint(fp), at_nm, angle, semantics=semantics)
    fields = [(s, t) for s, t in zip(fp.texts, placed.texts) if s.head == "property"]
    others = [(s, t) for s, t in zip(fp.texts, placed.texts) if s.head != "property"]
    fp_angle = F.fp_angle_token(placed.orientation)
    lines = [
        f'\t(footprint "{library}:{fp.name}"\n',
        '\t\t(layer "F.Cu")\n',
        f'\t\t(uuid "{_uid()}")\n',
        f"\t\t(at {_pt(at_nm)}{'' if fp_angle is None else ' ' + fp_angle})\n",
    ]
    for syn, text in fields:
        shown = ref if syn.name == "Reference" else value if syn.name == "Value" else syn.text
        lines.append(_text(2, syn, shown, T.angle_token(text.style), text.style))
    lines.append("\t\t(attr smd)\n")
    for syn, text in others:
        lines.append(_text(2, syn, syn.text, T.angle_token(text.style), text.style))
    lines += [G.shape_text(shape, layer="F.Fab", depth=2) for shape in fp.shapes]
    for syn, pad in zip(fp.pads, placed.pads):
        lines.append(_pad(2, syn, PAD.angle_token(pad.angle)))
    lines.append("\t\t(embedded_fonts no)\n\t)\n")
    return "".join(lines)


# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------

_TEXT_LAYERS = ("F.SilkS", "F.Fab", "Cmts.User", "F.SilkS")
_JUSTIFY = st.sampled_from((T.LEFT, T.CENTER, T.RIGHT))


def _distinct_points(count: int, span: int = 8 * MM):
    """``count`` distinct points (nm) within +-span, on a 0.01 mm grid mostly."""
    coordinate = st.one_of(
        st.integers(-span // 10_000, span // 10_000).map(lambda k: k * 10_000),
        st.integers(-span, span),
    )
    return st.lists(st.tuples(coordinate, coordinate), min_size=count, max_size=count, unique=True)


@st.composite
def synthetic_texts(draw, names: Sequence[tuple[str, str, str]]) -> tuple[SynText, ...]:
    """One text per (head, name, text) of ``names``, each at its own position, with a
    library angle that is a multiple of half a degree within +-720 (so a library angle
    outside [0, 360) occurs, as KiCad's stock libraries write -90), any justification,
    keep-upright or not, sometimes mirrored."""
    points = draw(_distinct_points(len(names)))
    out = []
    for (head, name, text), local in zip(names, points):
        out.append(
            SynText(
                head=head,
                name=name,
                text=text,
                local=local,
                angle=draw(G.half_degree_angles()),
                keep_upright=draw(st.booleans()),
                h=draw(_JUSTIFY),
                v=draw(_JUSTIFY),
                mirror=draw(st.integers(0, 4)) == 0,
                layer=draw(st.sampled_from(_TEXT_LAYERS)),
                hidden=head == "property"
                and name not in ("Reference", "Value")
                and draw(st.booleans()),
            )
        )
    return tuple(out)


@st.composite
def synthetic_footprints(draw, *, max_pads: int = 4, shapes: bool = True) -> SynFootprint:
    """A library footprint: 1 to ``max_pads`` pads at distinct places, each at a library
    angle that is 0 or a multiple of half a degree within +-720; a Reference and a Value
    field, up to two more fields or fp_text items; and up to two graphics."""
    count = draw(st.integers(1, max_pads))
    places = draw(_distinct_points(count))
    pads = tuple(
        SynPad(str(k + 1), local, draw(st.one_of(st.just(0.0), G.half_degree_angles())))
        for k, local in enumerate(places)
    )
    extra = draw(
        st.lists(
            st.sampled_from(
                (
                    ("property", "MPN", "X1"),
                    ("property", "Datasheet", ""),
                    ("fp_text", "user", "${REFERENCE}"),
                    ("fp_text", "user", "KEEP"),
                )
            ),
            max_size=2,
            unique=True,
        )
    )
    names = [("property", "Reference", "REF**"), ("property", "Value", "N"), *extra]
    texts = draw(synthetic_texts(names))
    drawn: list[tuple] = []
    if shapes:
        for _ in range(draw(st.integers(0, 2))):
            drawn += draw(G.courtyard_shapes(span=6 * MM))
    return SynFootprint("Syn", pads, texts, tuple(drawn))


def footprint_with_pad_at(angle: float, *, rotation_text: float = 0.0) -> SynFootprint:
    """A two-pad footprint whose pad 1 has the library angle ``angle``, a keep-upright,
    left-justified Reference and an unlocked Value at ``rotation_text``: the explicit
    examples of the generated tests."""
    return SynFootprint(
        "Syn",
        (SynPad("1", (-MM, 0), angle), SynPad("2", (MM, 0), 0.0)),
        (
            SynText(
                "property",
                "Reference",
                "REF**",
                (0, -2 * MM),
                rotation_text,
                True,
                T.LEFT,
                T.CENTER,
                False,
                "F.SilkS",
            ),
            SynText(
                "property",
                "Value",
                "N",
                (0, 2 * MM),
                rotation_text,
                False,
                T.CENTER,
                T.CENTER,
                False,
                "F.Fab",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# stock footprints
# ---------------------------------------------------------------------------

#: KiCad's own files the lexer port refuses (bug 54): never loaded as subjects.
_REFUSED = ("RoyalBlue54L-Feather",)


def _stock_dirs() -> list[Path]:
    return [share / "footprints" for share in K.share_dirs() if (share / "footprints").is_dir()]


@functools.cache
def stock_footprints() -> list[Path]:
    """Every .kicad_mod in the stock libraries, sorted; [] without an install."""
    return sorted(
        path
        for base in _stock_dirs()
        for path in base.glob("*.pretty/*.kicad_mod")
        if not any(name in path.name for name in _REFUSED)
    )


def stock_footprint(name: str) -> Path:
    """The stock footprint ``name``, found across the stock .pretty directories. Skips
    without an install; RuntimeError when the install has footprints but not this one
    (a curated name that a release renamed is a premise to fix, never a pass)."""
    bases = _stock_dirs()
    if not bases:
        pytest.skip(K.NO_INSTALL)
    for base in bases:
        found = sorted(base.glob(f"*.pretty/{name}.kicad_mod"))
        if found:
            return found[0]
    raise RuntimeError(f"no stock footprint {name} in {[str(b) for b in bases]}")


@functools.cache
def stock_footprints_for_turns() -> list[Path]:
    """stock_footprints() less those owning a zone (the board-coordinates slice's subject)
    and those holding a hidden fp_text, which KiCad's parser turns into a field with a
    name of its own making (parsePCB_TEXT, pcbnew/pcb_io/kicad_sexpr/
    pcb_io_kicad_sexpr_parser.cpp:3419-3427 at 9.0.8, 3936-3944 at 10.0.7) and which
    kicad_ref.footprint therefore refuses to read (one stock footprint,
    Smolhaj_Scale_0.1, in KiCad 9.0.8's libraries, counted 2026-10-11)."""
    return [p for p in stock_footprints() if not has_zone(p) and not has_hidden_fp_text(p)]


def has_hidden_fp_text(path: Path) -> bool:
    """Whether a footprint file holds a hidden fp_text other than a legacy Reference or
    Value (kicad_ref.footprint.is_hidden)."""
    data = path.read_bytes()
    if b"(fp_text" not in data or b"hide" not in data:
        return False
    return any(
        F.head(item) == b"fp_text"
        and item.items[1].value not in (b"reference", b"value")
        and F.is_hidden(item)
        for item in L.tree(data, knows_bar=L.knows_bar_of(data))[0].items
    )


def has_zone(path: Path) -> bool:
    """Whether a footprint file owns a zone (bug 15's subject, kept out of rotation tests)."""
    data = path.read_bytes()
    if b"(zone" not in data:
        return False
    return any(
        F.head(item) == b"zone" for item in L.tree(data, knows_bar=L.knows_bar_of(data))[0].items
    )
