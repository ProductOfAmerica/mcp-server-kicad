"""No input may produce a file KiCad cannot open.

The invariant says an edit that cannot be done correctly is refused with the
file intact. `_atomic_write` delivers the durability half; this file is the
validity half's regression net.

The property, which generalises past the parameters known today:

    For every write tool and every scalar parameter, a hostile value either
    raises ToolError, or produces a file kicad-cli can still load. Never a
    third outcome.

The third outcome is what shipped. Measured 2026-08-12: `add_trace(layer=
"banana")` wrote `(layer banana)` and the board then failed to load, and
`place_component(rotation=37)` did the same to a schematic. Both were accepted
silently by every test in the suite.

Two things make this test worth its runtime rather than decorative:

* **The baseline must succeed.** Every case first runs the unmutated call and
  fails loudly if it does not. Without that, a tool erroring for some unrelated
  reason makes every hostile value "refused" and the whole sweep passes while
  measuring nothing. That vacuity is the trap this sweep exists to avoid, so it
  is asserted rather than assumed.
* **Accepted is not the same as wrong.** A footprint at 37 degrees is legal
  KiCad, so the check is refused-or-valid, not refused. Anything a hostile
  value writes is run through the same `kicad-cli` oracle the rest of the suite
  uses, inline rather than at teardown so the failure names the parameter.

Proven non-vacuous 2026-08-12 by removing the layer guard from `add_trace` and
confirming the sweep goes red. Whole file runs in about 10s.

It earned that on its first run: `add_global_label(shape=...)` was unvalidated
while `add_hierarchical_label` had always checked the same vocabulary, so a
bad shape reached the file and kicad-cli refused the schematic.

Deliberately not a hardcoded list of *parameters*: a name-scoped guard is what
let mirror, output_units, layer and rotation through, in that order. The
parameters come from the live signature, so a new one is swept the day it is
added. The tools are listed, and `test_every_constrained_writer_is_swept` fails
when a write tool that takes one of these parameter names is not.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

import edit_gen as E
import kicad_corpus as K
import pytest
from conftest import (
    KICAD_SCH_VERSION,
    _cli_can_load_board,
    assert_kicad_parseable,
    assert_kicad_pcb_parseable,
    build_test_footprint,
    new_schematic,
    requires_cli,
)
from kicad_ref import dsn_lexer as L
from kiutils.board import Board
from kiutils.items.common import Net
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import pcb, schematic

pytestmark = requires_cli

#: Values no parameter on this surface should ever accept silently. Kept small:
#: cost is one tool call per (tool, parameter, value), and the file is only
#: validated when the call was accepted.
HOSTILE = ("banana", 37, -1)

#: Parameters that name a closed set or a board-defined one. These are the ones
#: where a wrong value reaches the file as a token rather than as escaped text. "net" is
#: one: a number written for it that no row of the board's table has reaches the file as a
#: well-formed token that names no net, and kicad-cli loads such a board.
CONSTRAINED = {
    "layer",
    "layers",
    "net",
    "rotation",
    "mirror",
    "shape",
    "format",
    "output_units",
    "direction",
    "stub_length",
}

#: Never mutate these: a path is not a token in the output, and a hostile path
#: only proves the file-not-found error works.
SKIP = {"pcb_path", "schematic_path", "project_path", "symbol_lib_path", "output_dir"}


def _board_k10(tmp_path: Path) -> str:
    """The sweep's board in KiCad 10's format, which has no table: R1 with its pad 1 on N1,
    as edit_gen writes a footprint KiCad holds, the net written by name after the pad's
    layers where KiCad 10 writes it."""
    text = E.board_footprint_text(
        E.footprint_with_pad_at(0.0), "R1", (100_000_000, 100_000_000), 0.0, version="k10"
    )
    out: list[str] = []
    pad: str | None = None
    for line in text.splitlines(keepends=True):
        out.append(line)
        if line.startswith('\t\t(pad "'):
            pad = line.split('"')[1]
        elif line == "\t\t)\n":
            pad = None
        elif pad == "1" and line.startswith("\t\t\t(layers "):
            out.append('\t\t\t(net "N1")\n')
    p = tmp_path / "sweep.kicad_pcb"
    p.write_text(
        E.layered_board_text(["".join(out)], version="k10"), encoding="utf-8", newline="\n"
    )
    return str(p)


def _board(tmp_path: Path) -> str:
    p = tmp_path / "sweep.kicad_pcb"
    b = Board.create_new()
    b.generator = "pcbnew"
    b.nets = [Net(number=0, name=""), Net(number=1, name="N1")]
    footprint = build_test_footprint()
    # Its pads are on Net1 and Net2, which the table above does not hold, a baseline that
    # already refers to a net no row has: _validate_board would blame every call for it.
    footprint.pads[0].net = Net(number=1, name="N1")
    footprint.pads[1].net = None
    b.footprints.append(footprint)
    b.filePath = str(p)
    b.to_file()
    return str(p)


# ---------------------------------------------------------------------------
# the net references a board holds
# ---------------------------------------------------------------------------

#: The board-level items that carry a (net ...): tracks, zones and the copper shapes
#: (AllConnectedItems, pcbnew/board.cpp:2898-2921 at 9.0.8, over the writers of
#: pcb_io_kicad_sexpr.cpp: 1027-1028 for a shape, 2436 for a track, 2448-2454 for a zone).
_NET_ITEMS = (
    b"segment",
    b"arc",
    b"via",
    b"zone",
    b"gr_line",
    b"gr_rect",
    b"gr_circle",
    b"gr_arc",
    b"gr_poly",
    b"gr_curve",
)
#: KiCad 9's last board format; above it a board writes its nets by name.
_LAST_NUMERIC_FORMAT = 20241229


def _children(group, head: bytes) -> list:
    return [c for c in group.items if c.kind == L.GROUP and c.items and c.items[0].value == head]


def _net_references(data: bytes):
    """Every (net ...) a board holds, read by KiCad's lexer (the port in tests/kicad_ref), as
    (the kind of item that holds it, the kind of its first token, its number or None): the
    board-level items of _NET_ITEMS and the pads of the footprints. A number is the integer
    strtol takes from a NUMBER token, None for a name (a quoted token or a bare word)."""
    root = L.tree(data, knows_bar=L.knows_bar_of(data))[0]
    holders = [
        (item.items[0].value.decode(), item)
        for head in _NET_ITEMS
        for item in _children(root, head)
    ]
    for footprint in _children(root, b"footprint"):
        holders += [("pad", pad) for pad in _children(footprint, b"pad")]
    for kind, item in holders:
        for net in _children(item, b"net"):
            if len(net.items) < 2:
                continue
            first = net.items[1]
            number = None
            if first.kind == L.NUMBER:
                digits = re.match(rb"[-+]?[0-9]+", first.value)
                number = int(digits.group()) if digits else 0
            yield kind, first.kind, number


def _net_reference_problems(data: bytes) -> list[str]:
    """Where the net references of a board are not nets, by the rules KiCad reads them with.
    On a board of KiCad 9's format or older, a numeric reference is 0 or the number of a row
    of the table: the parser maps every number through the rows, a number past the table finds
    no net and a negative number or a gap is net 0 (getNetCode and SetNetCode,
    pcb_io_kicad_sexpr_parser.h:156-162 and pcbnew/board_connected_item.cpp:87-115 at 9.0.8),
    so a number that is neither names no net. On a board above it that declares no table, a
    numeric reference is a legacy code that KiCad 10 binds to the net it made that many-th
    while loading, so it moves with the order of the pads and the tracks, and KiCad 10 writes
    its nets by name (ADR-2 guardrail 5 of docs/adr-cst-substrate.md; the writer stopped
    writing codes at format 20251028, pcb_io_kicad_sexpr.h:201 at 10.0.7): a number there is a
    mistake, even one that finds the net meant. Empty when every reference is sound."""
    root = L.tree(data, knows_bar=L.knows_bar_of(data))[0]
    rows = {
        int(row.items[1].value)
        for row in _children(root, b"net")
        if len(row.items) > 2 and row.items[1].kind == L.NUMBER
    }
    version = L.file_version(data) or 0
    problems = []
    for kind, _, number in _net_references(data):
        if number is None:
            continue
        if version > _LAST_NUMERIC_FORMAT and not rows:
            problems.append(f"a {kind} refers to net {number}, a number, on a board with no table")
        elif number != 0 and number not in rows:
            problems.append(f"a {kind} refers to net {number}, which no row of {sorted(rows)} has")
    return problems


def _validate_board(target: Path) -> None:
    """The oracle for a board the sweep wrote: kicad-cli loads it, where this machine's
    kicad-cli can, and its net references are sound (_net_reference_problems), which kicad-cli
    cannot tell: it loads a board that refers to a net no table has (measured 2026-10-17 on
    kicad-cli 9.0.8: it loads a segment on net 99, on net -7 and on a number in a gap)."""
    data = target.read_bytes()
    if _cli_can_load_board(data):
        assert_kicad_pcb_parseable(target)
    problems = _net_reference_problems(data)
    if problems:
        pytest.fail(f"{target.name} holds net references that name no net: {problems}")


def _sch(tmp_path: Path) -> str:
    p = tmp_path / "sweep.kicad_sch"
    s = new_schematic()
    s.version = KICAD_SCH_VERSION
    s.filePath = str(p)
    s.to_file()
    return str(p)


def _pcb_cases(tmp_path, version: str = "k9") -> list[tuple[Any, dict[str, Any]]]:
    p = _board(tmp_path) if version == "k9" else _board_k10(tmp_path)
    corners = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}]
    return [
        (pcb.add_trace, dict(x1=10, y1=10, x2=20, y2=10, layer="F.Cu", net=1, pcb_path=p)),
        (pcb.add_via, dict(x=30, y=30, layers=["F.Cu", "B.Cu"], net=1, pcb_path=p)),
        (pcb.add_pcb_text, dict(text="hi", x=10, y=10, layer="F.SilkS", pcb_path=p)),
        (pcb.add_pcb_line, dict(x1=0, y1=0, x2=10, y2=0, layer="Edge.Cuts", pcb_path=p)),
        (pcb.add_copper_zone, dict(net_name="N1", layer="F.Cu", corners=corners, pcb_path=p)),
        (pcb.add_keepout_zone, dict(corners=corners, layers=["F.Cu"], pcb_path=p)),
        (pcb.place_footprint, dict(reference="R9", value="1k", x=10, y=10, pcb_path=p)),
        # F.Cu, R1's own side, so the baseline is a plain move, which must
        # succeed: a baseline that raised would make every hostile value below
        # look "refused" while measuring nothing.
        (pcb.move_footprint, dict(reference="R1", x=20, y=20, layer="F.Cu", pcb_path=p)),
        (pcb.set_trace_width, dict(width=0.3, layer="F.Cu", pcb_path=p)),
        (pcb.remove_traces, dict(layer="B.Cu", pcb_path=p)),
    ]


def _sch_cases(tmp_path) -> list[tuple[Any, dict[str, Any]]]:
    s = _sch(tmp_path)
    place: dict[str, Any] = dict(
        lib_id="Device:R", reference="R1", value="1k", x=50, y=50, schematic_path=s
    )
    schematic.place_component(**place)  # move_component needs something to move
    return [
        (schematic.place_component, dict(place, reference="R2", x=60, y=60)),
        (schematic.add_label, dict(text="N1", x=50, y=50, schematic_path=s)),
        (schematic.add_global_label, dict(text="N2", x=55, y=55, schematic_path=s)),
        (
            schematic.add_hierarchical_label,
            dict(text="N3", shape="input", x=60, y=50, schematic_path=s),
        ),
        (schematic.add_text, dict(text="note", x=70, y=50, schematic_path=s)),
        (schematic.move_component, dict(reference="R1", x=80, y=80, schematic_path=s)),
        (
            schematic.wire_pins_to_net,
            dict(pins=[{"reference": "R1", "pin": "1"}], label_text="NW", schematic_path=s),
        ),
        (
            schematic.add_power_symbol,
            dict(lib_id="power:GND", reference="#PWR01", x=90, y=90, schematic_path=s),
        ),
        (
            schematic.auto_place_decoupling_cap,
            dict(
                lib_id="Device:C",
                reference="C1",
                value="100nF",
                x=100,
                y=100,
                power_net="VCC",
                ground_net="GND",
                schematic_path=s,
            ),
        ),
    ]


def _hostile_for(annotation) -> tuple:
    """Hostile values of the parameter's own type.

    Type-appropriate on purpose. Feeding 37 to a list[str] parameter only
    proves pydantic rejects an int at the MCP boundary, which is not what this
    file is about, and the noise buries the real finding.
    """
    text = str(annotation)
    if "list" in text:
        return (["banana"],)
    # A Literal of strings (mirror: Literal["", "x", "y"]) still wants a string.
    if "str" in text or ("Literal" in text and "'" in text):
        return ("banana",)
    return (37, -1)


def _sweep(cases):
    """(tool, baseline, param, hostile) for every constrained parameter."""
    for fn, base in cases:
        sig = inspect.signature(fn)
        for name, param in sig.parameters.items():
            if name in SKIP or name not in CONSTRAINED:
                continue
            for bad in _hostile_for(param.annotation):
                yield fn, base, name, bad


_PCB_SWEEP = "pcb"
_PCB_K10_SWEEP = "pcb-k10"
_SCH_SWEEP = "sch"


def _cases_of(which: str, tmp_path):
    if which == _SCH_SWEEP:
        return _sch_cases(tmp_path)
    return _pcb_cases(tmp_path, "k10" if which == _PCB_K10_SWEEP else "k9")


@pytest.mark.parametrize("which", [_PCB_SWEEP, _PCB_K10_SWEEP, _SCH_SWEEP])
def test_baseline_calls_succeed(which, tmp_path):
    """Liveness. Every hostile assertion below is vacuous if these do not pass."""
    for fn, base in _cases_of(which, tmp_path):
        fn(**base)


def _run_sweep(cases, is_board: bool):
    """Refused or valid. Accepted-and-valid is fine; accepted-and-broken is not.

    PCB rotations are the reason accepted cannot simply mean failure: a
    footprint at 37 degrees is legal KiCad, and get_footprint_bounds carries
    the trig to prove the project knows it.
    """
    validate = _validate_board if is_board else assert_kicad_parseable
    failures = []
    for fn, base, param, bad in _sweep(cases):
        target = Path(base.get("pcb_path") or base["schematic_path"])
        before = target.read_bytes()
        try:
            fn(**{**base, param: bad})
        except ToolError:
            continue  # refused: the good outcome
        except Exception as exc:  # noqa: BLE001 - a non-ToolError is its own defect
            failures.append(f"{fn.__name__}.{param}={bad!r} raised {type(exc).__name__}: {exc}")
            continue
        if target.read_bytes() == before:
            continue  # accepted but wrote nothing
        try:
            validate(target)
        except BaseException as exc:  # pytest.fail raises Failed, not Exception
            failures.append(f"{fn.__name__}.{param}={bad!r} was ACCEPTED and broke the file: {exc}")
        # Put the baseline back so one bad value cannot cascade.
        target.write_bytes(before)
    return failures


@pytest.mark.parametrize("which", [_PCB_SWEEP, _PCB_K10_SWEEP])
def test_hostile_values_never_reach_a_pcb(which, tmp_path):
    """Accepted-and-written is the outcome that shipped a broken board. The sweep runs on a
    board of KiCad 9's format and on one of KiCad 10's, and judges what a call wrote with
    _validate_board, which holds the net references to the rules KiCad reads them by."""
    failures = _run_sweep(_cases_of(which, tmp_path), is_board=True)
    assert failures == [], "\n".join(failures)


def test_hostile_values_never_reach_a_schematic(tmp_path):
    failures = _run_sweep(_sch_cases(tmp_path), is_board=False)
    assert failures == [], "\n".join(failures)


def test_every_constrained_writer_is_swept(tmp_path):
    """A new tool taking one of these parameters must join the sweep.

    Without this the sweep silently stops covering the surface as it grows,
    which is exactly how the original defects survived.
    """
    swept = {fn.__name__ for fn, _ in _pcb_cases(tmp_path) + _sch_cases(tmp_path)}
    missing = []
    for mod in (pcb, schematic):
        for name, tool in mod.mcp._tool_manager._tools.items():
            annotations = tool.annotations
            if getattr(annotations, "read_only_hint", None):
                continue
            # Export tools hand `format` to kicad-cli rather than writing a
            # token into a KiCad file, and their enums are pinned by
            # test_fixed_choice_params_publish_an_enum.
            if name.startswith("export_"):
                continue
            params = set(tool.parameters.get("properties", {}))
            if params & CONSTRAINED and name not in swept:
                missing.append(name)
    assert missing == [], (
        "these write tools take a constrained parameter but are not in the "
        f"sweep: {sorted(missing)}"
    )


def _tiny_board(version: int, rows: tuple[str, ...], items: str) -> bytes:
    """A board of format ``version`` with the table ``rows`` (each "(net N "NAME")") and the
    top-level ``items``, as much of one as the lexer needs."""
    table = "".join(f"\t{row}\n" for row in rows)
    return f'(kicad_pcb\n\t(version {version})\n\t(generator "pcbnew")\n{table}{items})\n'.encode()


_SEGMENT = (
    "\t(segment\n\t\t(start 0 0)\n\t\t(end 1 0)\n\t\t(width 0.25)\n"
    '\t\t(layer "F.Cu")\n\t\t{net}\n\t)\n'
)
_PAD = '\t(footprint "L:F"\n\t\t(pad "1" smd rect\n\t\t\t(at 0 0)\n\t\t\t{net}\n\t\t)\n\t)\n'
_SHAPE = "\t(gr_poly\n\t\t(pts\n\t\t\t(xy 0 0) (xy 1 0) (xy 1 1)\n\t\t)\n\t\t{net}\n\t)\n"


@pytest.mark.parametrize(
    ("version", "rows", "items", "problems"),
    [
        (20241229, ('(net 0 "")', '(net 1 "A")'), _SEGMENT.format(net="(net 1)"), 0),
        (20241229, ('(net 0 "")', '(net 1 "A")'), _SEGMENT.format(net="(net 0)"), 0),
        (20241229, ('(net 0 "")', '(net 1 "A")'), _SEGMENT.format(net="(net 2)"), 1),
        (20241229, ('(net 0 "")', '(net 1 "A")'), _SEGMENT.format(net="(net -1)"), 1),
        (20241229, ('(net 0 "")', '(net 1 "A")'), _PAD.format(net='(net 1 "A")'), 0),
        (20241229, ('(net 0 "")', '(net 1 "A")'), _PAD.format(net='(net 7 "B")'), 1),
        (20241229, ('(net 0 "")', '(net 1 "A")'), _SHAPE.format(net="(net 3)"), 1),
        (20241229, ('(net 0 "")', '(net 1 "A")'), _SHAPE.format(net="(net 1)"), 0),
        (20260206, (), _SEGMENT.format(net='(net "A")'), 0),
        (20260206, (), _SEGMENT.format(net='(net "5")'), 0),
        (20260206, (), _SEGMENT.format(net="(net 5)"), 1),
        (20260206, (), _PAD.format(net='(net 1 "A")'), 1),
        (20250907, ('(net 0 "")', '(net 1 "A")'), _SEGMENT.format(net="(net 1)"), 0),
        (20250907, ('(net 0 "")', '(net 1 "A")'), _SEGMENT.format(net="(net 2)"), 1),
    ],
)
def test_net_reference_judge_finds_exactly_the_references_that_name_no_net(
    version, rows, items, problems
):
    """The judge of the sweep above, judged on boards small enough to check by eye: a numeric
    reference is sound when it is 0 or a row of the table (KiCad 9's format and the development
    format between, 20250907, which declares a table), and on a board of KiCad 10's format that
    declares none a number is never sound while a quoted name, digits or not, always is."""
    data = _tiny_board(version, rows, items)
    assert len(_net_reference_problems(data)) == problems, _net_reference_problems(data)


def test_shipped_boards_satisfy_the_net_reference_rule():
    """The premise of the judge, KiCad's own files: every net reference of every demo and
    template board this install ships (up to the per-push size cap) is sound by
    _net_reference_problems, whatever its kind: a pad, a segment, an arc, a via, a zone or a
    copper shape. KiCad 9.0.8's demos and templates, 38 boards the lexer port reads, hold
    64,758 numeric net references (42,635 on segments, 13,497 on pads, 6,089 on vias, 2,020 on
    arcs, 513 on zones and 4 on copper shapes), and every one is 0 or a row of its board's
    table (counted 2026-10-17). A board the lexer port refuses (bug 54, RoyalBlue54L-Feather)
    is left out; skipped when this install's boards hold no numeric reference at all."""
    boards = K.shipped(
        subdirs=("demos", "template"), suffixes=(".kicad_pcb",), names=(), cap=K.PER_PUSH_CAP
    )
    if not boards:
        pytest.skip(K.NO_INSTALL)
    seen: dict[str, int] = {}
    problems: list[str] = []
    for name, path in boards:
        data = path.read_bytes()
        try:
            found = _net_reference_problems(data)
            kinds = [kind for kind, _, number in _net_references(data) if number is not None]
        except L.LexError:
            continue
        for kind in kinds:
            seen[kind] = seen.get(kind, 0) + 1
        problems += [f"{name}: {problem}" for problem in found]
    if not seen:
        pytest.skip("no board of this install holds a numeric net reference")
    assert not problems, f"{len(problems)} of {sum(seen.values())} references: {problems[:10]}"
