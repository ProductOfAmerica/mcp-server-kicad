"""Rung 2, writers: the number text every tool that writes a coordinate or a distance emits.

A tool puts a number the caller gave it into a file. KiCad holds that number as a count of
internal units (100 nm in a schematic or a symbol library, 1 nm on a board or in a
footprint) and writes it back as FormatInternalUnits, so a text is the one KiCad writes
only when KiCad reads it and writes it again unchanged. A distance, a position and an
angle that a tool writes are judged here by exactly that, and the tool's result by the
value KiCad holds for what was written.

Each case calls one writer with requests that are off the file's grid (more decimals than
it holds) or carry float noise (0.1 + 0.2), on a file in KiCad's own format: the native
schematic fixture, or a KiCad 9 and a KiCad 10 board from geom_gen. The lexer port reads the
file before and after, edit_judge.diff names the items the call added and the atoms it
changed, and for every number in one of them:

- an x or y of (at), (xy), (start), (end), (mid) or (center), and a (size), (width),
  (thickness), (clearance), (min_thickness), (thermal_gap), (thermal_bridge_width), (drill),
  (length) or (offset): parseInternalUnits or parseBoardUnits under both majors, then
  FormatInternalUnits at the file's scale, gives the text back;
- the third atom of an (at): parseDouble then FormatAngle gives the text back;
- every number the result reports is the number KiCad holds for an atom written (an atom's
  count of units over the scale), so a result never says "100.123456" where the file holds
  100.1235.

Judges (none the code under test): tests/kicad_ref/parse_units.py, eda_units.py (the ports
of KiCad's readers and of FormatInternalUnits and FormatAngle, checked against the files
KiCad saves in test_ladder_r2_judges.py), the DSNLEXER port, and edit_judge. A writer whose
result echoes no number has none to judge.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import edit_judge as J
import geom_gen as G
import pytest
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U
from kicad_ref import parse_units as P

from mcp_server_kicad import pcb, project, schematic, symbol
from mcp_server_kicad.models import PointSpec

SCH = U.SCH_IU_PER_MM
PCB = U.PCB_IU_PER_MM
MM = PCB

_BUG_61 = (
    "bug 61: the writer spells a number through _cst._num, with digits below the file's"
    " resolution, float noise or an exponent, which KiCad never writes; fixed by step 11"
)

#: Positions (x, y), then the second coordinate of the heads that hold a pair.
_COORDINATE_HEADS = (b"at", b"xy", b"start", b"end", b"mid", b"center")
_DISTANCE_HEADS = (
    b"size",
    b"width",
    b"thickness",
    b"clearance",
    b"min_thickness",
    b"thermal_gap",
    b"thermal_bridge_width",
    b"drill",
    b"length",
    b"offset",
)
_SUFFIXES = (".kicad_sch", ".kicad_pcb", ".kicad_sym", ".kicad_mod")

# ---------------------------------------------------------------------------
# reading what a call wrote
# ---------------------------------------------------------------------------


def _read_files(work: Path) -> dict[str, bytes]:
    return {
        path.relative_to(work).as_posix(): path.read_bytes()
        for path in sorted(work.rglob("*"))
        if path.is_file() and path.suffix in _SUFFIXES
    }


def _numbers_of(raw: bytes):
    """(head, position in its list, token bytes) of every NUMBER inside one lexed item."""

    def walk(item):
        if item.kind != L.GROUP:
            return
        first = item.items[0] if item.items else None
        head = first.value if first is not None and first.kind in L.ATOM_KINDS else None
        for position, child in enumerate(item.items):
            if child.kind == L.GROUP:
                yield from walk(child)
            elif child.kind == L.NUMBER:
                yield head, position, raw[child.start : child.end]

    for item in L.tree(raw, knows_bar=True):
        yield from walk(item)


def _written(before: dict[str, bytes], after: dict[str, bytes]) -> list[tuple[bytes, int, bytes]]:
    """(head, position, token) of every number in an item the call inserted or an atom it
    changed, over all the files it touched."""
    out = []
    for name, data in after.items():
        old = before.get(name, b"")
        if old == data:
            continue
        for edit in J.diff(old, data):
            if edit.op == "insert" and edit.new is not None:
                out.extend(_numbers_of(edit.new))
            elif edit.op == "atom" and edit.new is not None and L.is_number(edit.new):
                out.append(
                    (edit.path[-1][1].encode() if edit.path[-1][1] else None, edit.index, edit.new)
                )
    return out


def _is_angle(head, position) -> bool:
    return head == b"at" and position == 3


def _is_unit_count(head, position) -> bool:
    if head in _COORDINATE_HEADS:
        return position in (1, 2)
    return head in _DISTANCE_HEADS


def _readings(token: bytes, scale: int) -> set[int]:
    parse = P.parse_sch_internal_units if scale == SCH else P.parse_board_units
    return {parse(token, major=major) for major in (9, 10)}


def _problems(written, scale: int) -> list[str]:
    problems = []
    for head, position, token in written:
        text = token.decode()
        where = f"({(head or b'?').decode()} ...) atom {position}: {text!r}"
        try:
            if _is_angle(head, position):
                value = P.parse_double(token)
                if U.format_angle(value) != text:
                    problems.append(f"{where} is not FormatAngle's text for {value!r}")
            elif _is_unit_count(head, position):
                for count in sorted(_readings(token, scale)):
                    if U.format_internal_units(count, scale) != text:
                        problems.append(
                            f"{where}: KiCad reads {count} units and writes"
                            f" {U.format_internal_units(count, scale)!r}"
                        )
        except P.ParseRefusal as exc:
            problems.append(f"{where}: KiCad refuses it ({exc})")
    return problems


def _held(written, scale: int) -> set[float]:
    """The millimetres KiCad holds for each coordinate and distance atom written."""
    out: set[float] = set()
    for head, position, token in written:
        if _is_unit_count(head, position):
            out |= {count / scale for count in _readings(token, scale)}
    return out


# ---------------------------------------------------------------------------
# what a result reports
# ---------------------------------------------------------------------------

_NUMBER = r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?"
_PAIR = re.compile(rf"\(({_NUMBER}), ({_NUMBER})\)")
_KEYED = re.compile(rf"\b(?:w|size|drill|width|height|x|y)=({_NUMBER})")


def _pairs(result) -> list[float]:
    """Every number of a "(x, y)" in a message."""
    return [float(n) for pair in _PAIR.findall(str(result)) for n in pair]


def _pairs_and_keyed(result) -> list[float]:
    return _pairs(result) + [float(n) for n in _KEYED.findall(str(result))]


# ---------------------------------------------------------------------------
# the cases
# ---------------------------------------------------------------------------

_FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"


class Case(NamedTuple):
    scale: int
    setup: Callable[[Path], dict]
    call: Callable[[dict], Any]
    reported: Callable[[Any], list[float]] | None = None


def _sch(work: Path) -> dict:
    path = work / "sheet.kicad_sch"
    path.write_bytes(_FIXTURE.read_bytes())
    return {"sch": str(path)}


def _sch_with(*calls: Callable[[str], Any]) -> Callable[[Path], dict]:
    """setup: the fixture, then each call run on it before the measured one."""

    def setup(work: Path) -> dict:
        ctx = _sch(work)
        for call in calls:
            call(ctx["sch"])
        return ctx

    return setup


def _sheet_uuid(path: str) -> str:
    return schematic._node_uuid(schematic._open_sch_cst(path)[1].find_all("sheet")[0])


def _hierarchy(work: Path, pins: str = "ABC", y: float = 63.5) -> dict:
    """The fixture as a parent with one sheet block with a pin per letter of ``pins`` at
    (76.2, y), and its empty child."""
    ctx = _sch(work)
    child = work / "child.kicad_sch"
    project.create_schematic(str(child))
    project.add_hierarchical_sheet(
        ctx["sch"],
        "Sub",
        str(child),
        [{"name": n, "direction": "input"} for n in pins],
        x=76.2,
        y=y,
    )
    ctx.update(child=str(child), uuid=_sheet_uuid(ctx["sch"]))
    return ctx


def _board(version: str, *items: str) -> Callable[[Path], dict]:
    def setup(work: Path) -> dict:
        path = work / "board.kicad_pcb"
        path.write_text(G.board_text(items, version=version), encoding="utf-8", newline="\n")
        return {"pcb": str(path)}

    return setup


_R1 = G.footprint_text("R1", (100 * MM, 80 * MM), pads=[("1", (0, 0), 0.0)])
#: A trace to change and a net the zones can name on a KiCad 10 board, which has no net table.
_TRACE_K10 = (
    '\t(segment\n\t\t(start 10 10)\n\t\t(end 20 10)\n\t\t(width 0.25)\n\t\t(layer "F.Cu")\n'
    '\t\t(net "GND")\n\t\t(uuid "00000000-0000-4000-8000-00000000b001")\n\t)\n'
)
_TRACE_K9 = _TRACE_K10.replace('(net "GND")', "(net 1)")
_NET_GND = '\t(net 1 "GND")\n'
_SQUARE: list[PointSpec] = [
    {"x": 10.1234567, "y": 10.00000004},
    {"x": 20.0, "y": 10.0},
    {"x": 20.1 + 0.2, "y": 20.1234567},
]

_CASES: dict[str, Case] = {}


def _case(name: str, case: Case) -> None:
    _CASES[name] = case


# Schematic writers.
_case(
    "add_text",
    Case(
        SCH,
        _sch,
        lambda c: schematic.add_text("note", 100.123456, 50.00004, 90, c["sch"]),
        _pairs,
    ),
)
_case(
    "add_label",
    Case(
        SCH,
        _sch,
        lambda c: schematic.add_label("NET", 60.1 + 0.2, 90.123456, 270, c["sch"]),
        _pairs,
    ),
)
_case(
    "add_global_label",
    Case(
        SCH,
        _sch,
        lambda c: schematic.add_global_label("G", 60.1 + 0.2, 90.123456, 180, "input", c["sch"]),
        _pairs,
    ),
)
_case(
    "add_hierarchical_label",
    Case(
        SCH,
        _sch,
        lambda c: schematic.add_hierarchical_label(
            "H", "input", 60.1 + 0.2, 90.123456, 0, c["sch"]
        ),
        _pairs,
    ),
)
_case(
    "modify_hierarchical_label",
    Case(
        SCH,
        _sch_with(lambda p: schematic.add_hierarchical_label("H", "input", 100.0, 100.0, 0, p)),
        lambda c: schematic.modify_hierarchical_label(
            "H", c["sch"], new_x=60.1 + 0.2, new_y=90.123456
        ),
        _pairs_and_keyed,
    ),
)
_case(
    "place_component",
    Case(
        SCH,
        _sch,
        lambda c: schematic.place_component(
            "Device:R", "R2", "10K", 150.5, 90.3, 90, "", "", c["sch"]
        ),
        _pairs,
    ),
)
_case(
    "move_component",
    Case(SCH, _sch, lambda c: schematic.move_component("R1", 120.7, 80.3, 90, c["sch"]), _pairs),
)
_case(
    "set_component_property",
    Case(SCH, _sch, lambda c: schematic.set_component_property("R1", "MPN", "x", c["sch"])),
)
_case(
    "no_connect_pin",
    Case(SCH, _sch, lambda c: schematic.no_connect_pin("R1", "1", c["sch"]), _pairs),
)
_case(
    "add_wires",
    Case(
        SCH,
        _sch,
        lambda c: schematic.add_wires(
            [{"x1": 10.123456, "y1": 20.00004, "x2": 30.1 + 0.2, "y2": 20.00004}], c["sch"]
        ),
    ),
)
_case(
    "connect_pins",
    Case(
        SCH,
        _sch_with(
            lambda p: schematic.place_component(
                "Device:R", "R2", "10K", 152.4, 119.38, 0, "", "", p
            )
        ),
        lambda c: schematic.connect_pins("R1", "1", "R2", "1", c["sch"]),
    ),
)


def _with_child(work: Path) -> dict:
    """The fixture and an empty child schematic beside it."""
    ctx = _sch(work)
    child = work / "child.kicad_sch"
    project.create_schematic(str(child))
    return {**ctx, "child": str(child)}


_case(
    "add_hierarchical_sheet",
    Case(
        SCH,
        _with_child,
        lambda c: project.add_hierarchical_sheet(
            c["sch"],
            "Sub",
            c["child"],
            [{"name": "A", "direction": "input"}, {"name": "B", "direction": "output"}],
            x=76.2 + 0.0004,
            y=63.5,
        ),
    ),
)
_case(
    "modify_hierarchical_sheet",
    Case(
        SCH,
        _hierarchy,
        lambda c: project.modify_hierarchical_sheet(
            c["uuid"], c["sch"], width=25.4 + 0.00000001, height=10.1 + 0.2
        ),
        _pairs_and_keyed,
    ),
)
_case(
    "add_sheet_pin",
    Case(
        SCH,
        lambda work: _hierarchy(work, "AB", 25.4),
        lambda c: project.add_sheet_pin(c["uuid"], "D", "input", c["sch"]),
    ),
)
_case(
    "move_hierarchical_sheet",
    Case(
        SCH,
        _hierarchy,
        lambda c: project.move_hierarchical_sheet(c["uuid"], 60.1 + 0.2, 90.123456, c["sch"]),
        _pairs,
    ),
)
_case(
    "duplicate_sheet",
    Case(
        SCH,
        _hierarchy,
        lambda c: project.duplicate_sheet(c["uuid"], "Twin", c["sch"]),
    ),
)


def _flat_setup(work: Path) -> dict:
    """A parent with a wire at y = 100.33 and a child with a label at y = 0.1: the offset
    flatten_hierarchy gives the child's content, 50 below the parent's lowest y, is then a
    sum whose double has more digits than 4 decimals."""
    ctx = _hierarchy(work)
    schematic.add_wires([{"x1": 10.0, "y1": 100.33, "x2": 20.0, "y2": 100.33}], ctx["sch"])
    schematic.add_label("C", 0.1, 0.1, 0, ctx["child"])
    ctx["out"] = str(work / "flat.kicad_sch")
    return ctx


_case(
    "flatten_hierarchy",
    Case(
        SCH,
        _flat_setup,
        lambda c: project.flatten_hierarchy(c["sch"], c["out"]),
    ),
)


# Symbol writer.
_case(
    "add_symbol",
    Case(
        SCH,
        lambda work: {"lib": str(work / "lib.kicad_sym")},
        lambda c: symbol.add_symbol(
            "Part",
            [
                {"number": "1", "name": "A", "type": "passive", "x": 1.1 + 2.2, "y": 0.1 + 0.2},
                {
                    "number": "2",
                    "name": "B",
                    "type": "passive",
                    "x": 0.0,
                    "y": 2.54,
                    "length": 0.1 + 0.2,
                },
            ],
            pin_names_offset=0.1 + 0.2,
            rectangles=[{"x1": 0.1 + 0.2, "y1": -1.27, "x2": 2.54, "y2": 3.81}],
            symbol_lib_path=c["lib"],
        ),
    ),
)

# Board writers, each on a KiCad 9 and a KiCad 10 board.
for _version in ("k9", "k10"):
    _trace = _TRACE_K9 if _version == "k9" else _TRACE_K10
    _gnd = _NET_GND if _version == "k9" else ""
    _case(
        f"place_footprint[{_version}]",
        Case(
            PCB,
            _board(_version),
            lambda c: pcb.place_footprint(
                "R9", "10K", 100.1234567, 60.1 + 0.2, 0.1 + 0.2, pcb_path=c["pcb"]
            ),
            _pairs,
        ),
    )
    _case(
        f"move_footprint[{_version}]",
        Case(
            PCB,
            _board(_version, _R1),
            lambda c: pcb.move_footprint("R1", 110.1234567, 60.1 + 0.2, 90.0, pcb_path=c["pcb"]),
            _pairs,
        ),
    )
    _case(
        f"add_trace[{_version}]",
        Case(
            PCB,
            _board(_version),
            lambda c: pcb.add_trace(1e-05, 2.5e-07, 10.1 + 0.2, 3.0, 0.1 + 0.2, pcb_path=c["pcb"]),
            _pairs_and_keyed,
        ),
    )
    _case(
        f"add_via[{_version}]",
        Case(
            PCB,
            _board(_version),
            lambda c: pcb.add_via(
                30.123456, 40.00004, 0.8 + 0.0000001, 0.1 + 0.2, pcb_path=c["pcb"]
            ),
            _pairs_and_keyed,
        ),
    )
    _case(
        f"add_pcb_text[{_version}]",
        Case(
            PCB,
            _board(_version),
            lambda c: pcb.add_pcb_text("t", 30.123456, 40.00004, "F.SilkS", 0.1 + 0.2, c["pcb"]),
            _pairs,
        ),
    )
    _case(
        f"add_pcb_line[{_version}]",
        Case(
            PCB,
            _board(_version),
            lambda c: pcb.add_pcb_line(
                10.123456, 10.00004, 50.1 + 0.2, 10.0, "Edge.Cuts", 0.05 + 0.0000001, c["pcb"]
            ),
            _pairs_and_keyed,
        ),
    )
    _case(
        f"add_copper_zone[{_version}]",
        Case(
            PCB,
            _board(_version, _gnd, _trace),
            lambda c: pcb.add_copper_zone(
                "GND",
                "F.Cu",
                _SQUARE,
                0.1 + 0.2,
                0.25 + 0.0000001,
                True,
                0.5 + 0.0000001,
                0.1 + 0.2,
                pcb_path=c["pcb"],
            ),
            lambda result: [result.clearance_mm],
        ),
    )
    _case(
        f"add_keepout_zone[{_version}]",
        Case(
            PCB,
            _board(_version),
            lambda c: pcb.add_keepout_zone(_SQUARE, pcb_path=c["pcb"]),
        ),
    )
    _case(
        f"set_trace_width[{_version}]",
        Case(
            PCB,
            _board(_version, _gnd, _trace),
            lambda c: pcb.set_trace_width(0.1 + 0.2, layer="F.Cu", pcb_path=c["pcb"]),
            lambda result: [result.new_width_mm],
        ),
    )
    _case(
        f"add_thermal_vias[{_version}]",
        Case(
            PCB,
            _board(_version, _R1),
            lambda c: pcb.add_thermal_vias(
                "R1", "1", 2, 2, 0.1 + 0.2, 0.8 + 0.0000001, 0.1 + 0.2, pcb_path=c["pcb"]
            ),
            lambda result: [result.center["x"], result.center["y"]],
        ),
    )

# update_pcb_from_schematic's import, from a netlist file (its kicad-cli step is not under
# test): the two footprints it adds land on slots below the footprint already there.
_NETLIST = """<?xml version="1.0" encoding="UTF-8"?>
<export version="E"><components>
<comp ref="{ref}"><value>10K</value><footprint>Ladder:Part</footprint>
<sheetpath names="/" tstamps="/"/><tstamps>{ref}-0000</tstamps></comp>
</components><nets></nets></export>
"""


def _import_setup(version: str) -> Callable[[Path], dict]:
    def setup(work: Path) -> dict:
        G.pretty_library(work, "Part", [("1", (0, 0), 0.0)])
        (work / "netlist.xml").write_text(_NETLIST.format(ref="R7"), encoding="utf-8")
        path = work / "board.kicad_pcb"
        board = G.board_text([G.footprint_text("R1", (100_300_001, 60_100_001))], version=version)
        path.write_text(board, encoding="utf-8", newline="\n")
        return {"pcb": str(path), "netlist": str(work / "netlist.xml"), "lib": str(work)}

    return setup


for _version in ("k9", "k10"):
    _case(
        f"update_pcb_from_schematic[{_version}]",
        Case(
            PCB,
            _import_setup(_version),
            lambda c: pcb._apply_netlist_cst(c["netlist"], c["pcb"], [c["lib"]]),
        ),
    )

#: The writers bug 61 reaches on the code as it stands: the others already round a
#: request before they write it (or only copy numbers KiCad wrote), and are kept as guards.
_BUG_61_WRITERS = frozenset(
    {
        "add_text",
        "modify_hierarchical_label",
        "modify_hierarchical_sheet",
        "add_sheet_pin",
        "move_hierarchical_sheet",
        "flatten_hierarchy",
        "add_symbol",
    }
    | {
        f"{name}[{version}]"
        for name in (
            "place_footprint",
            "move_footprint",
            "add_trace",
            "add_via",
            "add_pcb_text",
            "add_pcb_line",
            "add_copper_zone",
            "add_keepout_zone",
            "set_trace_width",
            "add_thermal_vias",
            "update_pcb_from_schematic",
        )
        for version in ("k9", "k10")
    }
)


def _params():
    for name, case in _CASES.items():
        marks = (
            [pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_61)]
            if name in _BUG_61_WRITERS
            else []
        )
        yield pytest.param(name, case, id=name, marks=marks)


@pytest.mark.parametrize(("name", "case"), list(_params()))
def test_writers_spell_numbers_as_kicad_does(tmp_path, name, case):
    """One call of one writer: every coordinate, distance and angle it adds or changes is a
    fixed point of KiCad's read then write at the file's scale, and every number the result
    reports is a number KiCad holds for an atom written (the module docstring)."""
    ctx = case.setup(tmp_path)
    before = _read_files(tmp_path)
    result = case.call(ctx)
    written = _written(before, _read_files(tmp_path))
    if not written:
        raise RuntimeError(f"premise: {name} wrote no number")
    problems = _problems(written, case.scale)
    if case.reported is not None:
        held = _held(written, case.scale)
        reported = case.reported(result)
        if not reported:
            raise RuntimeError(f"premise: the result of {name} reports no number: {result!r}")
        problems += [
            f"the result reports {r!r}, which KiCad does not hold for any atom written"
            for r in reported
            if r not in held
        ]
    assert not problems, "\n".join(problems)
