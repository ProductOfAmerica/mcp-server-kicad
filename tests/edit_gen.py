"""Footprints for the single-element edit tests: generated, written as KiCad writes them.

A ``SynFootprint`` is a library footprint described by value: pads (number, position,
the angle its library file writes, and what a flip changes: layers, trapezoid delta,
shape offset, chamfered corners, custom primitives), texts (fields and fp_text items with
a position, an angle, the keep-upright flag, justification, mirror and layer),
graphics (geom_gen shape tuples), the rule areas it owns and KiCad 10's points. From one:

- ``library_text`` writes it as a .kicad_mod in KiCad 9's library form (KiCad 10's when
  it holds a point) and ``write_library`` puts that in a .pretty directory;
  ``legacy_library_text`` and ``write_legacy_library`` do the same in KiCad 7's form
  (format 20221018, before 20230620 "PCB Fields"), whose Reference and Value are
  (fp_text reference ...) and (fp_text value ...);
- ``board_footprint_text`` writes the footprint KiCad holds once it has placed that
  library footprint at a position and an angle (kicad_ref.footprint.place, Rotate or
  SetOrientation), on the front or flipped onto the back, in the board form KiCad saves:
  the footprint's angle Normalize180 and left out at 0, pad and text angles absolute,
  every position footprint-relative but a zone's and a point's, which are board
  coordinates; ``with_lock`` gives it one of the lock spellings KiCad reads (LOCKS), and
  ``legacy_fields`` writes its Reference and Value as legacy fp_text.

Every number is spelled as KiCad spells it (FormatInternalUnits and FormatAngle through
geom_gen and kicad_ref), never through kiutils or the code under test; whether KiCad reads
these footprints as written is checked against pcbnew in test_ladder_r3_judges.py.

``synthetic_footprints()`` draws them, ``legacy_footprints()`` those KiCad 7's form holds.
``stock_footprint(name)`` finds a footprint KiCad ships by name across the stock .pretty
directories; ``stock_footprints()`` lists them all, and ``model_free_copy`` copies one
without its 3D models.

``layered_board_text`` writes a board whose layer table has every technical layer, two,
four or six copper layers and the user layers asked for, typed and named as given, in the
layout pcbnew 9.0.8 saves (harvested from a board it saved, 2026-10-11).

``FOOTPRINT_CONSTRUCTS`` and ``PAD_CONSTRUCTS`` hold, as KiCad writes them, the footprint
children and pad tokens the tools do not move or flip; ``with_construct`` adds one to a
footprint's text and ``construct_library`` writes a library footprint holding one.

``netlist_text`` writes the netlist the board update reads, in kicad-cli's kicadxml form,
each field as the running kicad-cli's major prints it (checked against a kicad-cli export
in test_ladder_r3_judges.py).
"""

from __future__ import annotations

import base64
import functools
import itertools
import struct
import zlib
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import geom_gen as G
import kicad_corpus as K
import pytest
from conftest import _kicad_cli_major
from hypothesis import strategies as st
from kicad_ref import board_layers as BL
from kicad_ref import dsn_lexer as L
from kicad_ref import footprint as F
from kicad_ref import pad as PAD
from kicad_ref import pcb_text as T
from kicad_ref.eda_units import format_angle

Point = tuple[int, int]
MM = 1_000_000


class SynPad(NamedTuple):
    """A pad: its number, position and the angle its library file writes (any value),
    and what a flip changes: its layers, a trapezoid delta (the pad is then written
    trapezoid), a shape offset, chamfered corners (written roundrect with KiCad's chamfer
    tokens) and custom primitives (shape tuples with gr_ heads in the pad's own frame;
    the pad is then written custom). ``kind`` is smd or thru_hole (a circle with a drill)."""

    number: str
    local: Point
    angle: float  # as the library file writes it, any value
    layers: tuple[str, ...] = ("F.Cu", "F.Mask", "F.Paste")
    delta: Point = (0, 0)
    offset: Point = (0, 0)
    chamfer: tuple[str, ...] = ()
    primitives: tuple[tuple, ...] = ()
    kind: str = "smd"


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


class SynZone(NamedTuple):
    """A rule area forbidding everything that a footprint owns: its layer names as
    written (one goes in (layer ...), several or a layer set name such as "*.Cu" in
    (layers ...)) and its polygons, the first the outline and any later one a hole, each
    in the library's frame, which is board coordinates for the footprint at the origin,
    unturned."""

    layers: tuple[str, ...]
    polygons: tuple[tuple[Point, ...], ...]


class SynPoint(NamedTuple):
    """A KiCad 10 point (PCB_POINT) a footprint owns: its position in the library's frame
    and its layer."""

    local: Point
    layer: str


class SynFootprint(NamedTuple):
    """``shape_layers`` gives the graphics' layers in order, F.Fab for any it leaves out.
    ``points`` are KiCad 10's, so a footprint holding one is written in KiCad 10's
    format."""

    name: str
    pads: tuple[SynPad, ...]
    texts: tuple[SynText, ...]
    shapes: tuple[tuple, ...] = ()
    shape_layers: tuple[str, ...] = ()
    zones: tuple[SynZone, ...] = ()
    points: tuple[SynPoint, ...] = ()


def _shape_layer(fp: SynFootprint, k: int) -> str:
    return fp.shape_layers[k] if k < len(fp.shape_layers) else "F.Fab"


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


def _primitive(depth: int, shape: tuple) -> str:
    """One custom pad primitive in format( PAD )'s order: its points, (width), and (fill)
    for a polygon, rectangle or circle (pcb_io_kicad_sexpr.cpp:1702-1795 at 9.0.8)."""
    t = "\t" * depth
    kind = shape[0]
    if kind == "gr_poly":
        body = G._pts_block(shape[1], depth + 1)
    elif kind == "gr_curve":
        body = f"{t}\t(pts {' '.join(f'(xy {_pt(p)})' for p in shape[1])})\n"
    elif kind == "gr_circle":
        body = f"{t}\t(center {_pt(shape[1])})\n{t}\t(end {_pt(shape[2])})\n"
    elif kind == "gr_arc":
        names = ("start", "mid", "end")
        body = "".join(f"{t}\t({name} {_pt(p)})\n" for name, p in zip(names, shape[1:4]))
    elif kind in ("gr_line", "gr_rect"):
        body = f"{t}\t(start {_pt(shape[1])})\n{t}\t(end {_pt(shape[2])})\n"
    else:
        raise ValueError(f"no primitive {kind!r}")
    width = "0" if kind == "gr_poly" else "0.1"
    fill = ""
    if kind in ("gr_poly", "gr_rect", "gr_circle"):
        fill = f"{t}\t(fill {'yes' if kind == 'gr_poly' else 'no'})\n"
    return f"{t}({kind}\n{body}{t}\t(width {width})\n{fill}{t})\n"


#: Pad kinds written with a hole: a plated one, and a non-plated one whose circle is the
#: size of its drill, so it has no annular ring and no copper (PAD::IsOnCopperLayer,
#: pcbnew/pad.cpp:1129-1180 at 9.0.8).
_HOLES = ("thru_hole", "np_thru_hole")


def pad_shape(syn: SynPad) -> str:
    """The shape word a SynPad is written with."""
    if syn.primitives:
        return "custom"
    if syn.chamfer:
        return "roundrect"
    if syn.delta != (0, 0):
        return "trapezoid"
    return "circle" if syn.kind in _HOLES else "rect"


def _pad(depth: int, syn: SynPad, angle_atom: str | None, *, uuid: bool = False) -> str:
    """A pad in format( PAD )'s order: (at), (size), (rect_delta), (drill ... (offset)),
    (layers), a chamfered pad's corner tokens, a custom pad's (options) and (primitives),
    and with ``uuid`` its (uuid ...) last, where a pad without a padstack of its own has it
    (pcb_io_kicad_sexpr.cpp:1432-1820 at 9.0.8)."""
    t = "\t" * depth
    angle = f" {angle_atom}" if angle_atom is not None else ""
    shape = pad_shape(syn)
    size = {"custom": "0.5 0.5", "circle": "1.6 1.6"}.get(shape, "0.5 0.8")
    if syn.kind == "np_thru_hole":
        size = "0.8 0.8"
    lines = [
        f'{t}(pad "{syn.number}" {syn.kind} {shape}\n',
        f"{t}\t(at {_pt(syn.local)}{angle})\n",
        f"{t}\t(size {size})\n",
    ]
    if syn.delta != (0, 0):
        lines.append(f"{t}\t(rect_delta {_pt(syn.delta)})\n")
    if syn.kind in _HOLES or syn.offset != (0, 0):
        drill = " 0.8" if syn.kind in _HOLES else ""
        offset = ""
        if syn.offset != (0, 0):
            offset = f"\n{t}\t\t(offset {_pt(syn.offset)})\n{t}\t"
        lines.append(f"{t}\t(drill{drill}{offset})\n")
    names = " ".join(f'"{name}"' for name in syn.layers)
    lines.append(f"{t}\t(layers {names})\n")
    if syn.chamfer:
        words = " ".join(c for c in PAD.CHAMFER_ORDER if c in syn.chamfer)
        lines += [
            f"{t}\t(roundrect_rratio 0.25)\n",
            f"{t}\t(chamfer_ratio 0.2)\n",
            f"{t}\t(chamfer {words})\n",
        ]
    if syn.primitives:
        lines.append(f"{t}\t(options\n{t}\t\t(clearance outline)\n{t}\t\t(anchor rect)\n{t}\t)\n")
        lines.append(f"{t}\t(primitives\n")
        lines += [_primitive(depth + 2, shape) for shape in syn.primitives]
        lines.append(f"{t}\t)\n")
    if uuid:
        lines.append(f'{t}\t(uuid "{_uid()}")\n')
    lines.append(f"{t})\n")
    return "".join(lines)


def _library_style(syn: SynText) -> T.Style:
    return T.Style(syn.angle, syn.keep_upright, syn.h, syn.v, syn.mirror)


#: The restrictions of a rule area, in the order format( ZONE ) writes them.
_RULES = ("tracks", "vias", "pads", "copperpour", "footprints")


def zone_text(depth: int, layers: Sequence[str], polygons, version: str) -> str:
    """A rule area forbidding everything, at ``depth`` TABs, in format( ZONE )'s order and
    pcbnew's layout (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:2443-2637 at 9.0.8,
    2868-3074 at 10.0.7; the layout harvested from KiCad 9.0.8's stock
    Tag-Connect_TC2030-IDC-FP_2x03_P1.27mm_Vertical): KiCad 9's (net 0) (net_name "") and
    (filled_areas_thickness no), which KiCad 10 writes for no rule area; one layer in
    (layer ...), several or a layer set name in (layers ...); each polygon's points packed
    as Prettify packs them."""
    t = "\t" * depth
    if len(layers) == 1 and not any(mark in layers[0] for mark in "*&"):
        layer_line = f'{t}\t(layer "{layers[0]}")\n'
    else:
        layer_line = f"{t}\t(layers " + " ".join(f'"{name}"' for name in layers) + ")\n"
    lines = [f"{t}(zone\n"]
    if version == "k9":
        lines.append(f'{t}\t(net 0)\n{t}\t(net_name "")\n')
    lines += [
        layer_line,
        f'{t}\t(uuid "{_uid()}")\n',
        f"{t}\t(hatch edge 0.5)\n",
        f"{t}\t(connect_pads\n{t}\t\t(clearance 0)\n{t}\t)\n",
        f"{t}\t(min_thickness 0.25)\n",
    ]
    if version == "k9":
        lines.append(f"{t}\t(filled_areas_thickness no)\n")
    lines.append(
        f"{t}\t(keepout\n"
        + "".join(f"{t}\t\t({rule} not_allowed)\n" for rule in _RULES)
        + f"{t}\t)\n"
    )
    lines.append(
        f"{t}\t(fill\n{t}\t\t(thermal_gap 0.5)\n{t}\t\t(thermal_bridge_width 0.5)\n{t}\t)\n"
    )
    for polygon in polygons:
        lines.append(f"{t}\t(polygon\n" + G._pts_block(polygon, depth + 2) + f"{t}\t)\n")
    lines.append(f"{t})\n")
    return "".join(lines)


def point_text(depth: int, at: Point, layer: str) -> str:
    """A KiCad 10 point at ``depth`` TABs: format( PCB_POINT )'s (point (at) (size) (layer)
    (uuid)) (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:1157-1168 at 10.0.7), each
    child on its own line as Prettify lays out a list of lists, and 1 mm, the size a new
    PCB_POINT has (pcbnew/pcb_point.cpp:47-55 at 10.0.7)."""
    t = "\t" * depth
    return (
        f'{t}(point\n{t}\t(at {_pt(at)})\n{t}\t(size 1)\n{t}\t(layer "{layer}")\n'
        f'{t}\t(uuid "{_uid()}")\n{t})\n'
    )


#: The (version) and (generator_version) a library footprint is stamped with: KiCad
#: 9.0.8's, and KiCad 10.0.7's for a footprint holding a point, which KiCad 9 lacks
#: (points came with board format 20250901, pcb_io_kicad_sexpr.h:195 at 10.0.7).
_STAMPS = {"k9": ("20241229", "9.0"), "k10": ("20260206", "10.0")}


def footprint_version(fp: SynFootprint, version: str | None = None) -> str:
    """The format ``fp`` is written in: ``version``, or k10 when it holds a point and k9
    otherwise. A KiCad 9 footprint holding a point is refused (ValueError)."""
    if version is None:
        version = "k10" if fp.points else "k9"
    if version not in _STAMPS:
        raise ValueError(f"no footprint format {version!r}")
    if version == "k9" and fp.points:
        raise ValueError("KiCad 9 has no points; write the footprint as k10")
    return version


def library_text(fp: SynFootprint, *, version: str | None = None) -> str:
    """``fp`` as a .kicad_mod in KiCad 9's library form (KiCad 10's for ``version`` "k10",
    footprint_version): fields, (attr), fp_text items, graphics, points, pads, zones, in
    the order format( FOOTPRINT ) writes them, every angle as the library writes it (any
    value, FormatAngle's spelling; a pad's left out at 0)."""
    version = footprint_version(fp, version)
    stamp, generator = _STAMPS[version]
    fields = [s for s in fp.texts if s.head == "property"]
    others = [s for s in fp.texts if s.head != "property"]
    body = [
        f'(footprint "{fp.name}"\n',
        f"\t(version {stamp})\n",
        '\t(generator "pcbnew")\n',
        f'\t(generator_version "{generator}")\n',
        '\t(layer "F.Cu")\n',
    ]
    for syn in fields:
        body.append(_text(1, syn, syn.text, format_angle(syn.angle), _library_style(syn)))
    body.append("\t(attr smd)\n")
    for syn in others:
        body.append(_text(1, syn, syn.text, format_angle(syn.angle), _library_style(syn)))
    body += [
        G.shape_text(shape, layer=_shape_layer(fp, k), depth=1) for k, shape in enumerate(fp.shapes)
    ]
    body += [point_text(1, point.local, point.layer) for point in fp.points]
    for syn in fp.pads:
        body.append(_pad(1, syn, None if syn.angle == 0 else format_angle(syn.angle)))
    body += [zone_text(1, zone.layers, zone.polygons, version) for zone in fp.zones]
    body.append("\t(embedded_fonts no)\n)\n")
    return "".join(body)


def write_library(
    directory, fp: SynFootprint, *, library: str = "Ladder", version: str | None = None
) -> Path:
    """Write <directory>/<library>.pretty/<fp.name>.kicad_mod; return the .pretty path."""
    pretty = Path(directory) / f"{library}.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    (pretty / f"{fp.name}.kicad_mod").write_text(
        library_text(fp, version=version), encoding="utf-8", newline="\n"
    )
    return pretty


#: The format KiCad 7.0 writes, the last before 20230620 "PCB Fields"
#: (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.h:143 and 147 at 9.0.8, 149 and 153 at
#: 10.0.7), from which on a footprint's Reference and Value are (property ...) fields.
LEGACY_VERSION = "20221018"
_LEGACY_TYPES = {"Reference": "reference", "Value": "value"}


def _legacy_text(syn: SynText) -> str:
    """A text in KiCad 7's form: (fp_text TYPE "text" (at x y [angle] [unlocked]) (layer
    "name") [hide], its (effects ...) on one line, then (tstamp ...); a Reference or a
    Value field is the fp_text of type reference or value, any other field is refused
    (ValueError). The angle is left out at 0, as KiCad 7's writer left it out."""
    if syn.head == "property":
        if syn.name not in _LEGACY_TYPES:
            raise ValueError(f"KiCad 7's format has no field {syn.name!r} to write")
        kind = _LEGACY_TYPES[syn.name]
    else:
        kind = syn.name
    angle = "" if syn.angle == 0 else f" {format_angle(syn.angle)}"
    unlocked = "" if syn.keep_upright else " unlocked"
    words = T.justify_words(_library_style(syn))
    justify = f" (justify {' '.join(words)})" if words else ""
    return (
        f'  (fp_text {kind} "{syn.text}" (at {_pt(syn.local)}{angle}{unlocked})'
        f' (layer "{syn.layer}"){" hide" if syn.hidden else ""}\n'
        f"      (effects (font (size 1 1) (thickness 0.15)){justify})\n"
        f"    (tstamp {_uid()})\n  )\n"
    )


def _legacy_shape(shape: tuple, layer: str) -> str:
    """A graphic in KiCad 7's form: its points on the opening line (a polygon's (pts ...)
    one point a line, then a blank line), then (stroke ...), (fill none) for a rectangle,
    circle or polygon, (layer ...) and (tstamp ...) on one line."""
    kind = shape[0]
    heads = {
        "fp_line": ("start", "end"),
        "fp_rect": ("start", "end"),
        "fp_circle": ("center", "end"),
        "fp_arc": ("start", "mid", "end"),
        "fp_poly": (),
    }
    if kind not in heads:
        raise ValueError(f"no KiCad 7 form for the shape {kind!r} here")
    fill = " (fill none)" if kind[3:] in ("rect", "circle", "poly") else ""
    tail = f'(stroke (width 0.05) (type solid)){fill} (layer "{layer}") (tstamp {_uid()}))\n'
    if kind == "fp_poly":
        points = "".join(f"      (xy {_pt(p)})\n" for p in shape[1])
        return f"  (fp_poly\n    (pts\n{points}    )\n\n    {tail}"
    points = " ".join(f"({name} {_pt(p)})" for name, p in zip(heads[kind], shape[1:]))
    return f"  ({kind} {points}\n    {tail}"


def _legacy_pad(syn: SynPad) -> str:
    """A pad in KiCad 7's one-line form: (pad "number" kind shape (at x y [angle]) (size w
    h) [(drill d)] (layers ...) (tstamp ...)). A trapezoid delta, a shape offset,
    chamfered corners and primitives are refused (ValueError): not modelled."""
    if syn.delta != (0, 0) or syn.offset != (0, 0) or syn.chamfer or syn.primitives:
        raise ValueError(f"pad {syn.number!r}: only a plain pad is written in KiCad 7's form")
    shape = pad_shape(syn)
    size = {"np_thru_hole": "0.8 0.8", "thru_hole": "1.6 1.6"}.get(syn.kind, "0.5 0.8")
    drill = " (drill 0.8)" if syn.kind in _HOLES else ""
    angle = "" if syn.angle == 0 else f" {format_angle(syn.angle)}"
    names = " ".join(f'"{name}"' for name in syn.layers)
    return (
        f'  (pad "{syn.number}" {syn.kind} {shape} (at {_pt(syn.local)}{angle})'
        f" (size {size}){drill} (layers {names}) (tstamp {_uid()}))\n"
    )


def legacy_library_text(fp: SynFootprint) -> str:
    """``fp`` as a .kicad_mod in KiCad 7's form (format LEGACY_VERSION), laid out as KiCad
    7.0's writer laid it out (the KiCad 7 footprints KiCad 9.0.8's tiny_tapeout demo ships,
    CUI_UJC-HP-3-SMT-TR and SolderJumper-2_Bridged_Throughole1 among them, read
    2026-10-14): the opening line with (version) and (generator), (layer), (attr smd),
    the Reference and the Value as (fp_text reference ...) and (fp_text value ...), the
    only form that format has for them, the fp_text items, the graphics and the pads (a
    pad's layers in the order it gives them), each with its (tstamp ...). A field other
    than Reference and Value, a zone, a point and a pad detail are refused (ValueError):
    that format has no text for the field, and the rest is not modelled. KiCad's reading
    of these footprints is checked against pcbnew in test_ladder_r3_judges.py."""
    if fp.zones or fp.points:
        raise ValueError("a zone or a point is not written in KiCad 7's form here")

    def field(syn: SynText, name: str) -> bool:
        return syn.head == "property" and syn.name == name

    fields = [syn for name in _LEGACY_TYPES for syn in fp.texts if field(syn, name)]
    others = [syn for syn in fp.texts if not any(field(syn, name) for name in _LEGACY_TYPES)]
    body = [
        f'(footprint "{fp.name}" (version {LEGACY_VERSION}) (generator pcbnew)\n',
        '  (layer "F.Cu")\n',
        "  (attr smd)\n",
    ]
    body += [_legacy_text(syn) for syn in (*fields, *others)]
    body += [_legacy_shape(shape, _shape_layer(fp, k)) for k, shape in enumerate(fp.shapes)]
    body += [_legacy_pad(syn) for syn in fp.pads]
    body.append(")\n")
    return "".join(body)


def write_legacy_library(directory, fp: SynFootprint, *, library: str = "Legacy") -> Path:
    """Write <directory>/<library>.pretty/<fp.name>.kicad_mod in KiCad 7's form
    (legacy_library_text); return the .pretty path."""
    pretty = Path(directory) / f"{library}.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    (pretty / f"{fp.name}.kicad_mod").write_text(
        legacy_library_text(fp), encoding="utf-8", newline="\n"
    )
    return pretty


def as_legacy(fp: SynFootprint) -> SynFootprint:
    """``fp`` with the texts KiCad 7's form can hold, its Reference and Value fields and
    its fp_text items, renamed "Legacy" (legacy_library_text)."""
    keep = tuple(s for s in fp.texts if s.head != "property" or s.name in _LEGACY_TYPES)
    return fp._replace(name="Legacy", texts=keep)


def library_footprint(fp: SynFootprint) -> F.Footprint:
    """``fp`` as KiCad reads its library file."""
    data = library_text(fp).encode()
    return F.read_footprint(L.tree(data, knows_bar=L.knows_bar_of(data))[0], major=10)


def _shape_tuple(shape: F.Shape) -> tuple:
    """A kicad_ref.footprint Shape as the shape tuple geom_gen.shape_text and _primitive
    write: (kind, points) for a polygon or a curve, (kind, *points) otherwise."""
    if shape.kind[3:] in ("poly", "curve"):
        return (shape.kind, shape.points)
    return (shape.kind, *shape.points)


def board_footprint_text(
    fp: SynFootprint,
    ref: str,
    at_nm: Point,
    angle: float,
    *,
    semantics: str = "rotate",
    value: str = "V",
    library: str = "Ladder",
    version: str | None = None,
    side: str = "F.Cu",
    table: BL.LayerTable | None = None,
    path: str = "",
    sheetname: str = "",
    sheetfile: str = "",
    legacy_fields: bool = False,
) -> str:
    """The footprint KiCad holds after placing ``fp`` at ``at_nm`` and turning it to
    ``angle`` (``semantics`` "rotate" or "set", kicad_ref.footprint.place), as KiCad saves
    it on a board (depth 1); the Reference field reads ``ref`` and the Value ``value``.
    Positions stay footprint-relative as the library writes them (KiCad's own rounding of
    them off the quarter turns is not modelled, footprint_judge D3). Zones and points are
    written in board coordinates as KiCad holds them after the placement, turned with the
    rounding of the KiCad that writes ``version`` (footprint_version), and a zone's
    layers with the names its library writes. Every pad carries a (uuid ...), as KiCad's
    writer gives one to every pad on a board.

    ``side`` "B.Cu" places it on the back: SetLayerAndFlip( B_Cu ) after SetPosition and
    before the turn, on the board whose layer table is ``table`` (by default
    layered_board_text's, without user layers), so every pad, text, graphic, zone and
    point is written as KiCad's flip leaves it (kicad_ref.footprint.flip): local y
    negated, layers taken to their opposites (a layer set name such as *.Cu as written),
    a text's mirror toggled on a side specific layer, a pad's details mirrored.
    ``path``, ``sheetname`` and ``sheetfile`` write the footprint's link to its symbol,
    as the netlist update leaves it, where KiCad's writer puts them, before (attr).

    With ``legacy_fields`` the Reference and the Value are written as a legacy
    (fp_text reference ...) and (fp_text value ...) after (attr ...), where KiCad 7's
    writer put them, each laid out as KiCad 9 lays out a text: a footprint KiCad reads
    with the same two fields (kicad_ref.footprint, checked against pcbnew in
    test_ladder_r3_judges.py), at any format version."""
    version = footprint_version(fp, version)
    major = 10 if version == "k10" else 9
    library_fp = library_footprint(fp)
    if side == "F.Cu":
        placed = F.place(library_fp, at_nm, angle, semantics=semantics, major=major)
        opposite: dict[str, str] = {}
    elif side == "B.Cu":
        board = table if table is not None else _standard_table()
        opposite = BL.opposites(board, major=major)
        placed = F.place(
            library_fp,
            at_nm,
            angle,
            semantics=semantics,
            flipped=lambda f: F.board_flip(f, board, major=major),
            major=major,
        )
    else:
        raise ValueError(f"a footprint sits on F.Cu or B.Cu, not {side!r}")

    def names(written: Sequence[str]) -> tuple[str, ...]:
        return tuple(opposite.get(name, name) for name in written)

    # library_text writes the fields first and the fp_text items after them, which is the
    # order kicad_ref.footprint reads the placed footprint's texts in.
    in_file = [s for s in fp.texts if s.head == "property"]
    in_file += [s for s in fp.texts if s.head != "property"]
    fields = [(s, t) for s, t in zip(in_file, placed.texts) if s.head == "property"]
    others = [(s, t) for s, t in zip(in_file, placed.texts) if s.head != "property"]
    fp_angle = F.fp_angle_token(placed.orientation)
    lines = [
        f'\t(footprint "{library}:{fp.name}"\n',
        f'\t\t(layer "{side}")\n',
        f'\t\t(uuid "{_uid()}")\n',
        f"\t\t(at {_pt(at_nm)}{'' if fp_angle is None else ' ' + fp_angle})\n",
    ]
    legacy = []
    for syn, text in fields:
        shown = ref if syn.name == "Reference" else value if syn.name == "Value" else syn.text
        moved = syn._replace(local=text.local, layer=text.layer)
        if legacy_fields and syn.name in _LEGACY_TYPES:
            moved = moved._replace(head="fp_text", name=_LEGACY_TYPES[syn.name])
            legacy.append(_text(2, moved, shown, T.angle_token(text.style), text.style))
            continue
        lines.append(_text(2, moved, shown, T.angle_token(text.style), text.style))
    for head, word in (("path", path), ("sheetname", sheetname), ("sheetfile", sheetfile)):
        if word:
            lines.append(f'\t\t({head} "{word}")\n')
    lines.append("\t\t(attr smd)\n")
    lines += legacy
    for syn, text in others:
        moved = syn._replace(local=text.local, layer=text.layer)
        lines.append(_text(2, moved, syn.text, T.angle_token(text.style), text.style))
    if side == "F.Cu":
        lines += [
            G.shape_text(shape, layer=_shape_layer(fp, k), depth=2)
            for k, shape in enumerate(fp.shapes)
        ]
    else:
        lines += [
            G.shape_text(_shape_tuple(shape), layer=shape.layer or "", depth=2)
            for shape in placed.shapes
        ]
    lines += [point_text(2, point.position, point.layer or "") for point in placed.points]
    for syn, pad in zip(fp.pads, placed.pads):
        moved = syn._replace(
            local=pad.local,
            layers=names(syn.layers),
            delta=pad.delta,
            offset=pad.offset,
            chamfer=tuple(c for c in PAD.CHAMFER_ORDER if c in pad.chamfer),
            primitives=tuple(_shape_tuple(s) for s in pad.primitives),
        )
        lines.append(_pad(2, moved, PAD.angle_token(pad.angle), uuid=True))
    for syn, zone in zip(fp.zones, placed.zones):
        lines.append(zone_text(2, names(syn.layers), zone.polygons, version))
    lines.append("\t\t(embedded_fonts no)\n\t)\n")
    return "".join(lines)


@functools.cache
def _standard_table() -> BL.LayerTable:
    """The layer table layered_board_text writes without user layers."""
    data = layered_board_text().encode()
    return BL.read_layer_table(L.tree(data, knows_bar=L.knows_bar_of(data))[0])


# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------

_TEXT_LAYERS = ("F.SilkS", "F.Fab", "Cmts.User", "F.SilkS")
#: Front layers a text can sit on that KiCad's flip treats as side specific (the F
#: technical layers and F.Cu; kicad_ref.board_layers), and layers it does not.
SIDED_TEXT_LAYERS = ("F.SilkS", "F.Fab", "F.Cu", "F.Mask", "F.Paste", "F.Adhes", "F.CrtYd")
UNSIDED_TEXT_LAYERS = ("Cmts.User", "Dwgs.User", "Eco1.User", "Eco2.User", "Margin")
_JUSTIFY = st.sampled_from((T.LEFT, T.CENTER, T.RIGHT))


def _distinct_points(count: int, span: int = 8 * MM):
    """``count`` distinct points (nm) within +-span, on a 0.01 mm grid mostly."""
    coordinate = st.one_of(
        st.integers(-span // 10_000, span // 10_000).map(lambda k: k * 10_000),
        st.integers(-span, span),
    )
    return st.lists(st.tuples(coordinate, coordinate), min_size=count, max_size=count, unique=True)


@st.composite
def synthetic_texts(
    draw,
    names: Sequence[tuple[str, str, str]],
    *,
    layers: Sequence[str] = _TEXT_LAYERS,
    justify: bool = True,
) -> tuple[SynText, ...]:
    """One text per (head, name, text) of ``names``, each at its own position, with a
    library angle that is a multiple of half a degree within +-720 (so a library angle
    outside [0, 360) occurs, as KiCad's stock libraries write -90), keep-upright or not,
    on one of ``layers``; with ``justify``, any justification and sometimes mirrored,
    else centred and unmirrored, so KiCad writes no (justify ...) for it."""
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
                h=draw(_JUSTIFY) if justify else T.CENTER,
                v=draw(_JUSTIFY) if justify else T.CENTER,
                mirror=justify and draw(st.integers(0, 4)) == 0,
                layer=draw(st.sampled_from(tuple(layers))),
                hidden=head == "property"
                and name not in ("Reference", "Value")
                and draw(st.booleans()),
            )
        )
    return tuple(out)


_CORNERS = st.frozensets(st.sampled_from(PAD.CHAMFER_ORDER), min_size=1)
#: A shape detail in nanometres: within 0.2 mm, mostly on a 0.01 mm grid, never 0 alone.
_DETAIL = st.one_of(st.integers(-20, 20).map(lambda k: k * 10_000), st.integers(-200_000, 200_000))


@st.composite
def primitive_shapes(draw, span: int = 200_000) -> tuple[tuple, ...]:
    """One to three custom pad primitives (gr_poly, gr_line, gr_arc, gr_circle, gr_rect),
    integer points within +-``span`` nm of the pad's origin."""
    coordinate = st.integers(-span, span)
    point = st.tuples(coordinate, coordinate)
    out = []
    for kind in draw(
        st.lists(
            st.sampled_from(("gr_poly", "gr_line", "gr_arc", "gr_circle", "gr_rect")),
            min_size=1,
            max_size=3,
        )
    ):
        if kind == "gr_poly":
            out.append((kind, tuple(draw(G.convex_polygons(max_vertices=6, span=span // 2)))))
        elif kind == "gr_arc":
            out.append(
                (kind, *draw(G.arcs(min_radius=20_000, max_radius=span // 2, span=span // 2)))
            )
        elif kind == "gr_circle":
            centre = draw(point)
            out.append((kind, centre, (centre[0] + draw(st.integers(10_000, span)), centre[1])))
        else:
            start = draw(point)
            end = draw(point.filter(lambda q, a=start: q[0] != a[0] and q[1] != a[1]))
            out.append((kind, start, end))
    return tuple(out)


@st.composite
def flip_pad_details(draw, number: str, local: Point, angle: float) -> SynPad:
    """A pad carrying some of what PAD::Flip changes: a chamfered roundrect, a trapezoid,
    a shape offset (the details mostly off the x axis, so a flip that leaves them alone
    shows), a through-hole pad on *.Cu and *.Mask, or an SMD pad on F.Cu and F.Mask only."""
    kind = draw(st.sampled_from(("chamfer", "trapezoid", "offset", "plain", "tht")))
    if kind == "chamfer":
        return SynPad(number, local, angle, chamfer=tuple(sorted(draw(_CORNERS))))
    if kind == "trapezoid":
        delta = draw(st.tuples(_DETAIL, _DETAIL).filter(lambda d: d != (0, 0)))
        return SynPad(number, local, angle, delta=delta)
    if kind == "offset":
        offset = draw(st.tuples(_DETAIL, _DETAIL).filter(lambda d: d != (0, 0)))
        return SynPad(number, local, angle, offset=offset)
    if kind == "tht":
        return SynPad(number, local, angle, layers=("*.Cu", "*.Mask"), kind="thru_hole")
    return SynPad(number, local, angle, layers=("F.Cu", "F.Mask"))


#: Layer names a footprint's zone is written with, of those KiCad 9.0.8's stock footprints
#: write (counted 2026-10-11): one copper layer, the two outer ones listed, KiCad's layer
#: set names, and a copper layer with a technical or a drawing layer.
ZONE_LAYERS = (
    ("F.Cu",),
    ("B.Cu",),
    ("F.Cu", "B.Cu"),
    ("F&B.Cu",),
    ("*.Cu",),
    ("F.Cu", "F.Paste"),
    ("F.Cu", "Dwgs.User"),
)


def _hole_in(outline: Sequence[Point]) -> tuple[Point, ...]:
    """A polygon inside the convex ``outline``: its corners a third of the way from the
    vertices' mean to each of them."""
    cx = sum(x for x, _ in outline) // len(outline)
    cy = sum(y for _, y in outline) // len(outline)
    return tuple((cx + (x - cx) // 3, cy + (y - cy) // 3) for x, y in outline)


@st.composite
def synthetic_zones(draw, max_zones: int = 2) -> tuple[SynZone, ...]:
    """One to ``max_zones`` zones, each a convex outline within about 8 mm of the origin,
    sometimes with a hole inside it, on one of ZONE_LAYERS."""
    out = []
    for _ in range(draw(st.integers(1, max_zones))):
        outline = tuple(draw(G.convex_polygons(max_vertices=6, span=4 * MM)))
        polygons = [outline]
        if draw(st.booleans()):
            polygons.append(_hole_in(outline))
        out.append(SynZone(draw(st.sampled_from(ZONE_LAYERS)), tuple(polygons)))
    return tuple(out)


@st.composite
def synthetic_footprints(
    draw,
    *,
    max_pads: int = 4,
    shapes: bool = True,
    text_layers: Sequence[str] = _TEXT_LAYERS,
    justify: bool = True,
    pad_details: bool = False,
    primitives: bool = False,
    zones: bool = False,
) -> SynFootprint:
    """A library footprint: 1 to ``max_pads`` pads at distinct places, each at a library
    angle that is 0 or a multiple of half a degree within +-720; a Reference and a Value
    field, up to two more fields or fp_text items (on ``text_layers``, justified only with
    ``justify``); and up to two graphics. With ``pad_details`` the pads carry what PAD::Flip
    changes (flip_pad_details); with ``primitives`` the first pad is a custom pad; with
    ``zones`` it owns one or two zones (synthetic_zones)."""
    count = draw(st.integers(1, max_pads))
    places = draw(_distinct_points(count))
    pads = []
    for k, local in enumerate(places):
        angle = draw(st.one_of(st.just(0.0), G.half_degree_angles()))
        if pad_details:
            pads.append(draw(flip_pad_details(str(k + 1), local, angle)))
        else:
            pads.append(SynPad(str(k + 1), local, angle))
    if primitives:
        pads[0] = pads[0]._replace(
            primitives=draw(primitive_shapes()), chamfer=(), delta=(0, 0), kind="smd"
        )
    pads = tuple(pads)
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
    texts = draw(synthetic_texts(names, layers=text_layers, justify=justify))
    drawn: list[tuple] = []
    if shapes:
        for _ in range(draw(st.integers(0, 2))):
            drawn += draw(G.courtyard_shapes(span=6 * MM))
    owned = draw(synthetic_zones()) if zones else ()
    return SynFootprint("Syn", pads, texts, tuple(drawn), zones=owned)


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


def _mm_points(*points: tuple[float, float]) -> tuple[Point, ...]:
    return tuple((round(x * MM), round(y * MM)) for x, y in points)


#: A footprint owning zones of the kinds KiCad's stock footprints carry: a rectangle on
#: F.Cu round the origin, as Tag-Connect_TC2030-IDC-FP_2x03_P1.27mm_Vertical's; one on
#: every copper layer with a hole; one on F.Cu and Dwgs.User; one on KiCad's F&B.Cu. Some
#: corners sit off the micrometre grid, where a turn off the quarter turns rounds.
ZONED = SynFootprint(
    "Zoned",
    footprint_with_pad_at(45.0).pads,
    footprint_with_pad_at(45.0).texts,
    zones=(
        SynZone(
            ("F.Cu",), (_mm_points((1.27, -0.635), (-1.27, -0.635), (-1.27, 0.635), (1.27, 0.635)),)
        ),
        SynZone(
            ("*.Cu",),
            (
                _mm_points((-3, -3), (3.123457, -3), (3.123457, 2.765432), (-3, 2.765432)),
                _mm_points((-1, -1), (1, -1), (1, 1.000001)),
            ),
        ),
        SynZone(("F.Cu", "Dwgs.User"), (_mm_points((0.5, 0.5), (2.5, 0.700001), (1.1, 2.9)),)),
        SynZone(("F&B.Cu",), (_mm_points((-4, 3), (-3.5, 3), (-3.5, 3.5), (-4, 3.5)),)),
    ),
)

#: A library footprint for KiCad 7's form (legacy_library_text): a keep-upright Reference
#: justified left, an unlocked Value at 90 justified right and bottom whose text is the
#: footprint's name, as KiCad's libraries write it, a ${REFERENCE} text on F.Fab, two pads,
#: pad 2 at 90, and a courtyard rectangle.
LEGACY = SynFootprint(
    "Legacy",
    (SynPad("1", (-MM, 0), 0.0), SynPad("2", (MM, 0), 90.0)),
    (
        SynText(
            "property", "Reference", "REF**", (0, -2 * MM), 0.0, True, T.LEFT, 0, False, "F.SilkS"
        ),
        SynText(
            "property",
            "Value",
            "Legacy",
            (0, 2 * MM),
            90.0,
            False,
            T.RIGHT,
            T.BOTTOM,
            False,
            "F.Fab",
        ),
        SynText("fp_text", "user", "${REFERENCE}", (0, 0), 0.0, True, 0, 0, False, "F.Fab"),
    ),
    (("fp_rect", (-2 * MM, -MM), (2 * MM, MM)),),
    ("F.CrtYd",),
)


def legacy_footprints(**kwargs):
    """synthetic_footprints( **kwargs ) cut to what KiCad 7's form holds (as_legacy)."""
    return synthetic_footprints(**kwargs).map(as_legacy)


#: A KiCad 10 footprint owning two points, on F.Fab and on F.SilkS, one off the micrometre
#: grid, besides its pads and texts.
POINTED = SynFootprint(
    "Pointed",
    footprint_with_pad_at(0.0).pads,
    footprint_with_pad_at(0.0).texts,
    points=(
        SynPoint((MM, 2 * MM), "F.Fab"),
        SynPoint((-1_234_567, 500_001), "F.SilkS"),
    ),
)


# ---------------------------------------------------------------------------
# boards with a full layer table
# ---------------------------------------------------------------------------

#: The technical rows of the layer table pcbnew 9.0.8 saves (harvested from a board it
#: saved, 2026-10-11), in its order.
_TECH_ROWS = (
    '(9 "F.Adhes" user "F.Adhesive")',
    '(11 "B.Adhes" user "B.Adhesive")',
    '(13 "F.Paste" user)',
    '(15 "B.Paste" user)',
    '(5 "F.SilkS" user "F.Silkscreen")',
    '(7 "B.SilkS" user "B.Silkscreen")',
    '(1 "F.Mask" user)',
    '(3 "B.Mask" user)',
    '(17 "Dwgs.User" user "User.Drawings")',
    '(19 "Cmts.User" user "User.Comments")',
    '(21 "Eco1.User" user "User.Eco1")',
    '(23 "Eco2.User" user "User.Eco2")',
    '(25 "Edge.Cuts" user)',
    '(27 "Margin" user)',
    '(31 "F.CrtYd" user "F.Courtyard")',
    '(29 "B.CrtYd" user "B.Courtyard")',
    '(35 "F.Fab" user)',
    '(33 "B.Fab" user)',
)


def layered_board_text(
    items: Sequence[str] = (),
    *,
    version: str = "k9",
    copper: int = 2,
    users: Sequence[tuple[str, str, str | None]] = (),
) -> str:
    """A whole board around ``items`` (top-level items ending in a newline): geom_gen's
    header for ``version`` with a layer table of ``copper`` copper layers (In<k>.Cu at
    ordinal 2k + 2, as KiCad 9 numbers them), every technical layer, and the user layers
    ``users`` as (canonical name, type, user name or None)."""
    rows = ['(0 "F.Cu" signal)']
    rows += [f'({2 * k + 2} "In{k}.Cu" signal)' for k in range(1, copper - 1)]
    rows += ['(2 "B.Cu" signal)', *_TECH_ROWS]
    for name, kind, user in users:
        ordinal = 37 + 2 * int(name.split(".")[1])
        rows.append(f'({ordinal} "{name}" {kind}' + (f' "{user}")' if user else ")"))
    board = G.board_text(items, version=version)
    start = board.index("\t(layers\n")
    end = board.index("\t)\n", start) + len("\t)\n")
    table = "\t(layers\n" + "".join(f"\t\t{row}\n" for row in rows) + "\t)\n"
    return board[:start] + table + board[end:]


#: An fp_poly whose outline holds an arc between two corners, in the layout KiCad's
#: formatPolyPts writes (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:466-496 at
#: 9.0.8), with "{t}" for the indent of a footprint's own children.
POLY_WITH_ARC = (
    "{t}(fp_poly\n{t}\t(pts\n{t}\t\t(xy -1 -1) (xy 1 -1)\n{t}\t\t(arc\n"
    "{t}\t\t\t(start 1.5 -0.5)\n{t}\t\t\t(mid 1.7 0.2)\n{t}\t\t\t(end 1.2 1)\n"
    "{t}\t\t)\n{t}\t\t(xy -1 1)\n{t}\t)\n{t}\t(stroke\n{t}\t\t(width 0.1)\n"
    '{t}\t\t(type solid)\n{t}\t)\n{t}\t(fill no)\n{t}\t(layer "F.Fab")\n{t})\n'
)


def with_footprint_child(text: str, child: str, depth: int) -> str:
    """``text``, a footprint written at ``depth`` TABs (1 in a library, 2 on a board), with
    ``child`` (a template whose "{t}" is that indent) added before its (embedded_fonts no)."""
    t = "\t" * depth
    anchor = f"{t}(embedded_fonts no)\n"
    if text.count(anchor) != 1:
        raise RuntimeError(f"the footprint holds {text.count(anchor)} {anchor!r}")
    return text.replace(anchor, child.format(t=t) + anchor)


# ---------------------------------------------------------------------------
# constructs the tools do not port
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
FOOTPRINT_CONSTRUCTS = {
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
PAD_CONSTRUCTS = {
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
CONSTRUCT_HOST = SynFootprint(
    "Host",
    (
        SynPad("1", (-2 * MM, 0), 0.0, layers=("*.Cu", "*.Mask"), kind="thru_hole"),
        SynPad("2", (2 * MM, 0), 0.0),
    ),
    footprint_with_pad_at(0.0).texts,
)


def with_pad_token(text: str, token: str, depth: int) -> str:
    """``text``, a footprint written at ``depth`` TABs, with ``token`` (a template whose
    "{t}" is a pad child's indent) added before pad 1's close."""
    t = "\t" * depth
    opening = text.index(f'{t}(pad "1"')
    close = text.index(f"\n{t})\n", opening) + 1
    return text[:close] + token.format(t=t + "\t") + text[close:]


def with_construct(text: str, construct: str, depth: int) -> str:
    """``text``, a footprint written at ``depth`` TABs, with ``construct`` added: a
    footprint child before (embedded_fonts no), a pad token before pad 1's close."""
    if construct in FOOTPRINT_CONSTRUCTS:
        return with_footprint_child(text, FOOTPRINT_CONSTRUCTS[construct][1], depth)
    return with_pad_token(text, PAD_CONSTRUCTS[construct][1], depth)


def construct_version(construct: str) -> str:
    """The format, k9 or k10, a board or library holding ``construct`` is written in."""
    return {**FOOTPRINT_CONSTRUCTS, **PAD_CONSTRUCTS}[construct][0]


def construct_library(directory: Path, construct: str) -> Path:
    """A .pretty holding the host footprint with ``construct`` added, stamped at the
    KiCad version the construct needs; return the .pretty path."""
    text = with_construct(library_text(CONSTRUCT_HOST), construct, 1)
    if construct_version(construct) == "k10":
        text = text.replace("(version 20241229)", "(version 20260206)", 1)
        text = text.replace('(generator_version "9.0")', '(generator_version "10.0")', 1)
    pretty = directory / "Constructs.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    (pretty / "Host.kicad_mod").write_text(text, encoding="utf-8", newline="\n")
    return pretty


CONSTRUCTS = [*FOOTPRINT_CONSTRUCTS, *PAD_CONSTRUCTS]
#: The constructs refused whatever the edit, not only by a flip: KiCad moves them with
#: the footprint (in board coordinates, or as items the tool does not transform), and a
#: child no KiCad knows may hold board coordinates of its own.
MOVED_CONSTRUCTS = ("dimension", "image", "table", "barcode", "future_item")


# ---------------------------------------------------------------------------
# locks
# ---------------------------------------------------------------------------

#: The ways a footprint's lock is written that KiCad's parser reads (parseMaybeAbsentBool(
#: true), pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:239-266 and 4502-4504
#: at 9.0.8), each with whether it locks: the bare atom KiCad 6 and 7 wrote after the
#: library id, an empty list, and the yes, true, no and false words. KiCad 9's writer
#: writes (locked yes) before (layer) (pcb_io_kicad_sexpr.cpp:1114-1115 at 9.0.8).
LOCKS = {
    "bare locked": True,
    "(locked)": True,
    "(locked yes)": True,
    "(locked true)": True,
    "(locked no)": False,
    "(locked false)": False,
}


def with_lock(text: str, spelling: str) -> str:
    """``text``, a footprint board_footprint_text wrote, with the lock ``spelling`` (a key
    of LOCKS): the bare atom after its library id, any other on its own line before
    (layer)."""
    if spelling not in LOCKS:
        raise ValueError(f"no lock spelling {spelling!r}")
    first, rest = text.split("\n", 1)
    if not first.startswith("\t(footprint ") or not rest.startswith("\t\t(layer "):
        raise RuntimeError("the footprint does not open with its library id and (layer)")
    if spelling == "bare locked":
        return f"{first} locked\n{rest}"
    return f"{first}\n\t\t{spelling}\n{rest}"


# ---------------------------------------------------------------------------
# netlists
# ---------------------------------------------------------------------------


class NetComponent(NamedTuple):
    """One symbol of a netlist: its reference, value and footprint id, the uuid of the
    symbol ("" for none), the fields the symbol carries besides its footprint (each text
    as a KiCad 9 schematic writes it, which netlist_text prints as kicad-cli does), its
    footprint filters and the file of its sheet ("" for none)."""

    ref: str
    value: str
    footprint: str
    tstamp: str = ""
    fields: tuple[tuple[str, str], ...] = ()
    fp_filters: str = ""
    sheetfile: str = ""


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def netlist_text(
    components: Sequence[NetComponent],
    nets: Sequence[tuple[str, Sequence[tuple[str, str]]]] = (),
    *,
    tool: str = "Eeschema 9.0.8",
    major: int | None = None,
) -> str:
    """A netlist in the kicadxml form kicad-cli 9.0.8 exports (measured with ``sch export
    netlist --format kicadxml``, 2026-10-11), trimmed to what the board update reads: each
    component with its value, footprint, fields (Footprint first, as kicad-cli lists it),
    Sheetname and Sheetfile properties, footprint filters, root sheet path and uuid; each
    net (name, nodes as (ref, pin)) with its nodes. ``tool`` is the <design><tool>, whose
    major version says whether the netlist can mark a symbol excluded from position files
    (KiCad 10).

    Each component's ``fields`` print as the kicad-cli of ``major`` prints them from a
    KiCad 9 schematic (format 20250114, the suite's), ``major`` being by default the
    running kicad-cli's (conftest._kicad_cli_major): from KiCad 10 on, a field whose text
    is exactly "~" prints empty. KiCad 10 reads a property's "~" as empty text in a
    schematic older than format 20250318, "~ no longer means empty text" (parseSchField,
    eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp:2330-2333 at 10.0.7;
    eeschema/sch_file_versions.h:127 there), where KiCad 9 keeps the text as written
    (sch_io_kicad_sexpr_parser.cpp:2234 at 9.0.8), and both exporters print each field's
    text as read (addSymbolFields, eeschema/netlist_exporters/netlist_exporter_xml.cpp:
    187-194 and 233-241 at 9.0.8, 200-207 and 246-254 at 10.0.7)."""
    if major is None:
        major = _kicad_cli_major()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<export version="E">',
        "  <design>",
        f"    <tool>{_xml(tool)}</tool>",
        "  </design>",
        "  <components>",
    ]
    for comp in components:
        lines += [
            f'    <comp ref="{_xml(comp.ref)}">',
            f"      <value>{_xml(comp.value)}</value>",
            f"      <footprint>{_xml(comp.footprint)}</footprint>",
            "      <fields>",
        ]
        fields = [(name, "" if major >= 10 and text == "~" else text) for name, text in comp.fields]
        for name, value in (("Footprint", comp.footprint), *fields):
            if value:
                lines.append(f'        <field name="{_xml(name)}">{_xml(value)}</field>')
            else:
                lines.append(f'        <field name="{_xml(name)}"/>')
        lines.append("      </fields>")
        lines.append('      <property name="Sheetname" value=""/>')
        lines.append(f'      <property name="Sheetfile" value="{_xml(comp.sheetfile)}"/>')
        if comp.fp_filters:
            lines.append(f'      <property name="ki_fp_filters" value="{_xml(comp.fp_filters)}"/>')
        lines.append('      <sheetpath names="/" tstamps="/"/>')
        lines.append(f"      <tstamps>{_xml(comp.tstamp)}</tstamps>")
        lines.append("    </comp>")
    lines += ["  </components>", "  <nets>"]
    for code, (name, nodes) in enumerate(nets, start=1):
        lines.append(f'    <net code="{code}" name="{_xml(name)}" class="Default">')
        lines += [
            f'      <node ref="{_xml(ref)}" pin="{_xml(pin)}" pintype="passive"/>'
            for ref, pin in nodes
        ]
        lines.append("    </net>")
    lines += ["  </nets>", "</export>", ""]
    return "\n".join(lines)


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


def model_free_copy(path: Path, directory, *, library: str = "Stock") -> Path:
    """Copy the footprint file ``path`` into <directory>/<library>.pretty without its
    (model ...) children, each removed with the whitespace before it; return the copy's
    path. For tests whose subject is not the 3D model (rung 3's D4)."""
    data = path.read_bytes()
    root = L.tree(data, knows_bar=L.knows_bar_of(data))[0]
    out = bytearray()
    cursor = 0
    for item in root.items:
        if F.head(item) == b"model":
            start = item.start
            while start > 0 and data[start - 1 : start] in (b" ", b"\t", b"\n", b"\r"):
                start -= 1
            out += data[cursor:start]
            cursor = item.end
    out += data[cursor:]
    pretty = Path(directory) / f"{library}.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    copy = pretty / path.name
    copy.write_bytes(bytes(out))
    return copy


def has_zone(path: Path) -> bool:
    """Whether a footprint file owns a zone (bug 15's subject, kept out of rotation tests)."""
    data = path.read_bytes()
    if b"(zone" not in data:
        return False
    return any(
        F.head(item) == b"zone" for item in L.tree(data, knows_bar=L.knows_bar_of(data))[0].items
    )
