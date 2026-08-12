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
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    KICAD_SCH_VERSION,
    assert_kicad_parseable,
    assert_kicad_pcb_parseable,
    build_test_footprint,
    new_schematic,
    requires_cli,
)
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
#: where a wrong value reaches the file as a token rather than as escaped text.
CONSTRAINED = {"layer", "layers", "rotation", "mirror", "shape", "format", "output_units"}

#: Never mutate these: a path is not a token in the output, and a hostile path
#: only proves the file-not-found error works.
SKIP = {"pcb_path", "schematic_path", "project_path", "symbol_lib_path", "output_dir"}


def _board(tmp_path: Path) -> str:
    p = tmp_path / "sweep.kicad_pcb"
    b = Board.create_new()
    b.generator = "pcbnew"
    b.nets = [Net(number=0, name=""), Net(number=1, name="N1")]
    b.footprints.append(build_test_footprint())
    b.filePath = str(p)
    b.to_file()
    return str(p)


def _sch(tmp_path: Path) -> str:
    p = tmp_path / "sweep.kicad_sch"
    s = new_schematic()
    s.version = KICAD_SCH_VERSION
    s.filePath = str(p)
    s.to_file()
    return str(p)


def _pcb_cases(tmp_path) -> list[tuple[Any, dict[str, Any]]]:
    p = _board(tmp_path)
    corners = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}]
    return [
        (pcb.add_trace, dict(x1=10, y1=10, x2=20, y2=10, layer="F.Cu", net=1, pcb_path=p)),
        (pcb.add_via, dict(x=30, y=30, layers=["F.Cu", "B.Cu"], net=1, pcb_path=p)),
        (pcb.add_pcb_text, dict(text="hi", x=10, y=10, layer="F.SilkS", pcb_path=p)),
        (pcb.add_pcb_line, dict(x1=0, y1=0, x2=10, y2=0, layer="Edge.Cuts", pcb_path=p)),
        (pcb.add_copper_zone, dict(net_name="N1", layer="F.Cu", corners=corners, pcb_path=p)),
        (pcb.add_keepout_zone, dict(corners=corners, layers=["F.Cu"], pcb_path=p)),
        (pcb.place_footprint, dict(reference="R9", value="1k", x=10, y=10, pcb_path=p)),
        (pcb.move_footprint, dict(reference="R1", x=20, y=20, layer="B.Cu", pcb_path=p)),
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
_SCH_SWEEP = "sch"


@pytest.mark.parametrize("which", [_PCB_SWEEP, _SCH_SWEEP])
def test_baseline_calls_succeed(which, tmp_path):
    """Liveness. Every hostile assertion below is vacuous if these do not pass."""
    cases = _pcb_cases(tmp_path) if which == _PCB_SWEEP else _sch_cases(tmp_path)
    for fn, base in cases:
        fn(**base)


def _run_sweep(cases, is_board: bool):
    """Refused or valid. Accepted-and-valid is fine; accepted-and-broken is not.

    PCB rotations are the reason accepted cannot simply mean failure: a
    footprint at 37 degrees is legal KiCad, and get_footprint_bounds carries
    the trig to prove the project knows it.
    """
    validate = assert_kicad_pcb_parseable if is_board else assert_kicad_parseable
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


def test_hostile_values_never_reach_a_pcb(tmp_path):
    """Accepted-and-written is the outcome that shipped a broken board."""
    failures = _run_sweep(_pcb_cases(tmp_path), is_board=True)
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
