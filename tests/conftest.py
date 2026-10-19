"""Shared fixtures and helpers for mcp-server-kicad tests.

Provides:
    - scratch_sch: schematic with a Device:R lib symbol, placed R1, label, and wire
    - empty_sch: minimal valid empty schematic
    - scratch_sym_lib: .kicad_sym with a custom TestPart symbol
    - stock_symbol_dir: stand-in for KiCad's stock symbol folder (set KICAD_SYMBOL_DIR)
    - reparse: re-read a schematic from disk through kiutils (not KiCad's reader)
    - run_erc: run kicad-cli ERC and return parsed JSON
    - assert_kicad_parseable: assert kicad-cli can load a schematic and every sheet below it
    - netlist_nodes: KiCad's connectivity, keyed by printed net name
    - netlist_fields: KiCad's reading of every placed symbol's user fields
    - requires_cli: skip marker for tests that shell out to kicad-cli
    - Hypothesis profiles (HYPOTHESIS_PROFILE) and the nightly marker's skip rule
    - the autouse output oracle over every KiCad file a test leaves in tmp_path
    - StdioClient: drive a server as a subprocess over stdio, as a host does
    - Builder helpers importable by test files for custom fixture creation
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid as _uuid
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import pytest
from hypothesis import Phase, settings
from kicad_ref import dsn_lexer as _dsn
from kiutils.board import Board
from kiutils.footprint import Footprint, Pad
from kiutils.items.brditems import Segment
from kiutils.items.common import (
    Effects,
    Fill,
    Font,
    Net,
    Position,
    Property,
    Stroke,
)
from kiutils.items.fpitems import FpText
from kiutils.items.gritems import GrLine
from kiutils.items.schitems import (
    Connection,
    LocalLabel,
    SchematicSymbol,
    SymbolProjectInstance,
    SymbolProjectPath,
)
from kiutils.items.syitems import SyRect
from kiutils.schematic import Schematic
from kiutils.symbol import Symbol, SymbolLib, SymbolPin

from mcp_server_kicad import _cst
from mcp_server_kicad._shared import _find_kicad_cli, _run_cli

HAS_KICAD_CLI = _find_kicad_cli() is not None

# Mark the tests that actually shell out.  Class-level ``pytestmark = []`` cannot
# cancel a module-level skip: pytest unions marks down Module -> Class -> Function
# and never subtracts them.
requires_cli = pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KICAD_SCH_VERSION = 20250114
KICAD_SCH_GENERATOR = "eeschema"
KICAD_SYM_VERSION = "20231120"

# Files kicad-cli has already loaded this session, keyed on a UUID-normalised digest
# of everything its verdict depends on.  See ``_validate_kicad_outputs``.
_UUID_RE = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_VALIDATED: set[bytes] = set()


# ---------------------------------------------------------------------------
# Test ladder: Hypothesis profiles, the nightly marker, xfailed calls
# ---------------------------------------------------------------------------

# HYPOTHESIS_PROFILE picks the profile; "ci" is the default everywhere. Hypothesis
# loads a built-in "ci" profile of its own whenever a CI variable is set, so each
# profile here is built on the plain default and loaded explicitly: a local run and
# a CI run check the same examples. "ci" is derandomized with no example database,
# so every run checks the same cases and a strict xfail cannot flip between runs.
# report_multiple_bugs=False keeps a failure an AssertionError rather than an
# exception group, which is what a strict xfail's raises=AssertionError matches.
_HYPOTHESIS_DEFAULT = settings.get_profile("default")
settings.register_profile(
    "ci",
    _HYPOTHESIS_DEFAULT,
    max_examples=200,
    derandomize=True,
    database=None,
    deadline=None,
    print_blob=True,
    report_multiple_bugs=False,
)
settings.register_profile(
    "nightly",
    _HYPOTHESIS_DEFAULT,
    max_examples=5000,
    derandomize=False,
    deadline=None,
    print_blob=True,
    report_multiple_bugs=False,
)
# For mutation testing: deterministic, and no shrinking, since killing a mutant
# needs a failing example, not a minimal one.
settings.register_profile(
    "mutation",
    _HYPOTHESIS_DEFAULT,
    max_examples=100,
    derandomize=True,
    database=None,
    phases=[Phase.explicit, Phase.generate],
    deadline=None,
    report_multiple_bugs=False,
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


def _nightly_skip_reason(profile: str | None) -> str | None:
    """Why a @pytest.mark.nightly test is skipped under ``profile``, or None to run it."""
    if profile == "nightly":
        return None
    return "nightly: a large generated KiCad run; set HYPOTHESIS_PROFILE=nightly to run it"


def pytest_collection_modifyitems(config, items):
    reason = _nightly_skip_reason(os.environ.get("HYPOTHESIS_PROFILE"))
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if item.get_closest_marker("nightly") is not None:
            item.add_marker(skip)


_CALL_REPORT = pytest.StashKey[pytest.TestReport]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when == "call":
        item.stash[_CALL_REPORT] = report
    return report


def _call_xfailed(report) -> bool:
    """True when a test's call phase failed as its xfail marker expects.

    pytest reports that as skipped with ``wasxfail``; a pass, a failure and an XPASS
    (failed under strict, passed with ``wasxfail`` otherwise) are not skipped.
    """
    return report is not None and report.skipped and hasattr(report, "wasxfail")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _gen_uuid() -> str:
    return str(_uuid.uuid4())


def _default_effects(size: float = 1.27, hide: bool = False) -> Effects:
    return Effects(font=Font(height=size, width=size), hide=hide)


def _default_stroke(width: float = 0) -> Stroke:
    return Stroke(width=width, type="default")


# ---------------------------------------------------------------------------
# Builder helpers (public — importable by test files)
# ---------------------------------------------------------------------------


def build_r_symbol() -> Symbol:
    """Build a Device:R library symbol definition (2-pin passive resistor).

    Unit 0/style 1: rectangle body (no pins).
    Unit 1/style 1: pin 1 at (0, 3.81) rot 270, pin 2 at (0, -3.81) rot 90.
    """
    sym = Symbol()
    sym.entryName = "R"
    sym.pinNamesOffset = 0
    sym.inBom = True
    sym.onBoard = True

    # Unit 0 — graphic body
    unit0 = Symbol()
    unit0.entryName = "R"
    unit0.unitId = 0
    unit0.styleId = 1
    unit0.graphicItems = [
        SyRect(
            start=Position(X=-1.016, Y=-2.54),
            end=Position(X=1.016, Y=2.54),
            stroke=Stroke(width=0.254, type="default"),
            fill=Fill(type="none"),
        )
    ]

    # Unit 1 — pins
    unit1 = Symbol()
    unit1.entryName = "R"
    unit1.unitId = 1
    unit1.styleId = 1
    unit1.pins = [
        SymbolPin(
            electricalType="passive",
            position=Position(X=0, Y=3.81, angle=270),
            length=1.27,
            name="~",
            number="1",
        ),
        SymbolPin(
            electricalType="passive",
            position=Position(X=0, Y=-3.81, angle=90),
            length=1.27,
            name="~",
            number="2",
        ),
    ]

    sym.units = [unit0, unit1]
    return sym


def place_r1(x: float = 100, y: float = 100) -> SchematicSymbol:
    """Build a placed R1 SchematicSymbol instance at (x, y)."""
    r1 = SchematicSymbol()
    r1.libId = "Device:R"
    r1.libName = "R"
    r1.position = Position(X=x, Y=y, angle=0)
    r1.uuid = _gen_uuid()
    r1.unit = 1
    r1.inBom = True
    r1.onBoard = True
    r1.properties = [
        Property(
            key="Reference",
            value="R1",
            id=0,
            effects=_default_effects(),
            position=Position(X=x, Y=y - 3.81, angle=0),
        ),
        Property(
            key="Value",
            value="10K",
            id=1,
            effects=_default_effects(),
            position=Position(X=x, Y=y + 3.81, angle=0),
        ),
        Property(
            key="Footprint",
            value="",
            id=2,
            effects=_default_effects(hide=True),
            position=Position(X=x, Y=y, angle=0),
        ),
        Property(
            key="Datasheet",
            value="~",
            id=3,
            effects=_default_effects(hide=True),
            position=Position(X=x, Y=y, angle=0),
        ),
    ]
    r1.pins = {"1": _gen_uuid(), "2": _gen_uuid()}
    return r1


def build_test_part_symbol() -> Symbol:
    """Build a custom 'TestPart' symbol (2-pin passive).

    Pin 1 "IN" at (-5.08, 0) rot 0, pin 2 "OUT" at (5.08, 0) rot 180.
    """
    sym = Symbol()
    sym.entryName = "TestPart"
    sym.pinNamesOffset = 0
    sym.inBom = True
    sym.onBoard = True

    # Unit 0 — graphic body
    unit0 = Symbol()
    unit0.entryName = "TestPart"
    unit0.unitId = 0
    unit0.styleId = 1
    unit0.graphicItems = [
        SyRect(
            start=Position(X=-3.81, Y=-2.54),
            end=Position(X=3.81, Y=2.54),
            stroke=Stroke(width=0.254, type="default"),
            fill=Fill(type="none"),
        )
    ]

    # Unit 1 — pins
    unit1 = Symbol()
    unit1.entryName = "TestPart"
    unit1.unitId = 1
    unit1.styleId = 1
    unit1.pins = [
        SymbolPin(
            electricalType="passive",
            position=Position(X=-5.08, Y=0, angle=0),
            length=1.27,
            name="IN",
            number="1",
        ),
        SymbolPin(
            electricalType="passive",
            position=Position(X=5.08, Y=0, angle=180),
            length=1.27,
            name="OUT",
            number="2",
        ),
    ]

    sym.units = [unit0, unit1]
    return sym


def build_power_symbol(name: str, pin_type: str = "power_in") -> Symbol:
    """Build a minimal power symbol (VCC, GND, PWR_FLAG, etc.).

    Pin 1 at (0, 0) angle 90.  ``pin_type`` is typically ``"power_in"``
    for VCC/GND or ``"power_out"`` for PWR_FLAG.
    """
    sym = Symbol()
    sym.entryName = name
    sym.isPower = True
    sym.pinNamesOffset = 0
    sym.inBom = False
    sym.onBoard = True

    unit0 = Symbol()
    unit0.entryName = name
    unit0.unitId = 0
    unit0.styleId = 1

    unit1 = Symbol()
    unit1.entryName = name
    unit1.unitId = 1
    unit1.styleId = 1
    unit1.pins = [
        SymbolPin(
            electricalType=pin_type,
            position=Position(X=0, Y=0, angle=90),
            length=0,
            name="~",
            number="1",
        ),
    ]

    sym.units = [unit0, unit1]
    return sym


def new_schematic() -> Schematic:
    """Create a minimal valid empty schematic compatible with KiCad 9."""
    sch = Schematic.create_new()
    sch.version = KICAD_SCH_VERSION
    sch.generator = KICAD_SCH_GENERATOR
    sch.uuid = _gen_uuid()
    return sch


def make_power_sch(tmp_path, pin_type="power_in", ref="#PWR01", value="VCC") -> str:
    """Schematic with one placed power symbol (pin 1 at (100, 100)). Returns path."""
    sch = new_schematic()
    sch.libSymbols.append(build_power_symbol(value, pin_type))

    sym = SchematicSymbol()
    sym.libId = f"power:{value}"
    sym.libName = value
    sym.position = Position(X=100, Y=100, angle=0)
    sym.uuid = _gen_uuid()
    sym.unit = 1
    sym.inBom = False
    sym.onBoard = True
    sym.properties = [
        Property(
            key="Reference",
            value=ref,
            id=0,
            effects=_default_effects(hide=True),
            position=Position(X=100, Y=96.19, angle=0),
        ),
        Property(
            key="Value",
            value=value,
            id=1,
            effects=_default_effects(),
            position=Position(X=100, Y=103.81, angle=0),
        ),
        Property(
            key="Footprint",
            value="",
            id=2,
            effects=_default_effects(hide=True),
            position=Position(X=100, Y=100, angle=0),
        ),
        Property(
            key="Datasheet",
            value="~",
            id=3,
            effects=_default_effects(hide=True),
            position=Position(X=100, Y=100, angle=0),
        ),
    ]
    sym.pins = {"1": _gen_uuid()}
    sch.schematicSymbols.append(sym)

    path = str(tmp_path / "power.kicad_sch")
    sch.filePath = path
    sch.to_file()
    return path


#: A lib pin for the multi-unit builders: (number, name, x, y, angle).
_PinSpec = tuple[str, str, float, float, float]


def _gate_unit(name: str, unit_id: int, style_id: int, pins: list[_PinSpec]) -> Symbol:
    """One ``<name>_<unit>_<style>`` sub-symbol holding *pins*."""
    unit = Symbol()
    unit.entryName = name
    unit.unitId = unit_id
    unit.styleId = style_id
    unit.pins = [
        SymbolPin(
            electricalType="passive",
            position=Position(X=px, Y=py, angle=angle),
            length=2.54,
            name=pin_name,
            number=number,
        )
        for number, pin_name, px, py, angle in pins
    ]
    return unit


def build_dual_gate_symbol() -> Symbol:
    """Build a 'DualGate' symbol: two gate units plus shared unit-0 power pins.

    Unit 0: pin 5 "GND" at (0, -7.62) and pin 6 "VCC" at (0, 7.62), which KiCad
    draws on every unit.  Unit 1: pin 1 "1A" at (-5.08, 0), pin 2 "1Y" at
    (5.08, 0).  Unit 2: pin 3 "2A" and pin 4 "2Y" at the same body-local
    coordinates as unit 1's pair, which is exactly what makes a unit mix-up
    visible: the sibling gate's input lands on top of this gate's input.
    """
    sym = Symbol()
    sym.entryName = "DualGate"
    sym.pinNamesOffset = 0
    sym.inBom = True
    sym.onBoard = True
    sym.units = [
        _gate_unit("DualGate", 0, 1, [("5", "GND", 0, -7.62, 90), ("6", "VCC", 0, 7.62, 270)]),
        _gate_unit("DualGate", 1, 1, [("1", "1A", -5.08, 0, 0), ("2", "1Y", 5.08, 0, 180)]),
        _gate_unit("DualGate", 2, 1, [("3", "2A", -5.08, 0, 0), ("4", "2Y", 5.08, 0, 180)]),
    ]
    return sym


def build_demorgan_symbol() -> Symbol:
    """Build a 'NandGate' symbol shaped like the stock 74xx:74LS00, in miniature.

    Unit 1 has two body styles: ``_1_1`` (the gate) and ``_1_2`` (its De Morgan
    alternate), each carrying pins 1 "A", 2 "B" and 3 "Y".  The alternate
    places them 2.54 further out than the gate does, so which style a reader
    picked is visible in the coordinates; a stock symbol draws both at the
    same place, where the only symptom is every pin reported twice.  Unit 2
    is the power unit: ``_2_0`` carries pins 7 "GND" and 14 "VCC" in body
    style 0, which KiCad draws whichever style is placed, and ``_2_1`` is
    graphics only, like ``74LS00_5_0`` and ``74LS00_5_1``.
    """
    sym = Symbol()
    sym.entryName = "NandGate"
    sym.pinNamesOffset = 1.016
    sym.inBom = True
    sym.onBoard = True
    gate: list[_PinSpec] = [
        ("1", "A", -7.62, 2.54, 0),
        ("2", "B", -7.62, -2.54, 0),
        ("3", "Y", 7.62, 0, 180),
    ]
    alt: list[_PinSpec] = [
        ("1", "A", -10.16, 5.08, 0),
        ("2", "B", -10.16, -5.08, 0),
        ("3", "Y", 10.16, 0, 180),
    ]
    power: list[_PinSpec] = [("7", "GND", 0, -7.62, 90), ("14", "VCC", 0, 7.62, 270)]
    body = _gate_unit("NandGate", 2, 1, [])
    body.graphicItems = [
        SyRect(
            start=Position(X=-2.54, Y=-5.08),
            end=Position(X=2.54, Y=5.08),
            stroke=Stroke(width=0.254, type="default"),
            fill=Fill(type="none"),
        )
    ]
    sym.units = [
        _gate_unit("NandGate", 1, 1, gate),
        _gate_unit("NandGate", 1, 2, alt),
        _gate_unit("NandGate", 2, 0, power),
        body,
    ]
    return sym


def place_unit(
    lib_name: str,
    x: float,
    y: float,
    unit: int,
    numbers: tuple[str, ...],
    *,
    sheet_uuid: str,
    project: str,
    reference: str = "U1",
) -> SchematicSymbol:
    """Place one unit of a multi-unit part at (x, y) on the root sheet *sheet_uuid*.

    The symbol carries the ``(instances ...)`` block KiCad writes, which the
    netlist oracle depends on: kicad-cli 9.0.8 leaves every unconnected pin of
    a symbol without instance data out of the netlist, so a no-connected pin is
    absent there rather than marked ``+no_connect`` (measured 2026-10-01).
    """
    sym = SchematicSymbol()
    sym.libId = f"Device:{lib_name}"
    sym.libName = lib_name
    sym.position = Position(X=x, Y=y, angle=0)
    sym.uuid = _gen_uuid()
    sym.unit = unit
    sym.inBom = True
    sym.onBoard = True
    sym.properties = [
        Property(
            key="Reference",
            value=reference,
            id=0,
            effects=_default_effects(),
            position=Position(X=x, Y=y - 10.16, angle=0),
        ),
        Property(
            key="Value",
            value=lib_name,
            id=1,
            effects=_default_effects(),
            position=Position(X=x, Y=y + 10.16, angle=0),
        ),
        Property(
            key="Footprint",
            value="",
            id=2,
            effects=_default_effects(hide=True),
            position=Position(X=x, Y=y, angle=0),
        ),
        Property(
            key="Datasheet",
            value="~",
            id=3,
            effects=_default_effects(hide=True),
            position=Position(X=x, Y=y, angle=0),
        ),
    ]
    sym.pins = {number: _gen_uuid() for number in numbers}
    sym.instances = [
        SymbolProjectInstance(
            name=project,
            paths=[
                SymbolProjectPath(
                    sheetInstancePath=f"/{sheet_uuid}", reference=reference, unit=unit
                )
            ],
        )
    ]
    return sym


def make_dual_unit_sch(tmp_path, units: tuple[int, ...] = (1, 2)) -> str:
    """U1 (DualGate) with the given *units* placed, and a label on gate 1's input.

    Unit 1 sits at (100, 100), so pin 1 is at (94.92, 100) and pin 2 at
    (105.08, 100); unit 2 sits at (150, 100), so pin 3 is at (144.92, 100) and
    pin 4 at (155.08, 100).  The shared pins 5 and 6 are at (x, 107.62) and
    (x, 92.38) of every placed unit.  The label GATE1_IN is on pin 1.
    Returns the path.
    """
    path = tmp_path / "dual_unit.kicad_sch"
    sch = new_schematic()
    assert sch.uuid is not None  # new_schematic sets it; kiutils types it Optional
    sheet = {"sheet_uuid": sch.uuid, "project": path.stem}
    sch.libSymbols.append(build_dual_gate_symbol())
    if 1 in units:
        sch.schematicSymbols.append(
            place_unit("DualGate", 100, 100, 1, ("1", "2", "5", "6"), **sheet)
        )
    if 2 in units:
        sch.schematicSymbols.append(
            place_unit("DualGate", 150, 100, 2, ("3", "4", "5", "6"), **sheet)
        )
    sch.labels.append(
        LocalLabel(
            text="GATE1_IN",
            position=Position(X=94.92, Y=100, angle=0),
            effects=_default_effects(),
            uuid=_gen_uuid(),
        )
    )
    sch.filePath = str(path)
    sch.to_file()
    return str(path)


def make_demorgan_sch(tmp_path, body_style: int = 1) -> str:
    """U1 (NandGate): unit 1 at (100, 100) in *body_style*, power unit 2 at (150, 100).

    Pin 1 "A" is at (92.38, 97.46) when the gate is drawn in style 1 and at
    (89.84, 94.92) in style 2.  The label NAND_A is on whichever of the two
    the placed style draws, and the label PHANTOM is on the other, where
    nothing is drawn.  Style 2 is written as KiCad 9 writes it, ``(convert 2)``
    on the placed symbol, which KiCad 10 reads too.  Returns the path.
    """
    drawn, other = ((92.38, 97.46), (89.84, 94.92))[:: 1 if body_style == 1 else -1]
    path = tmp_path / "demorgan.kicad_sch"
    sch = new_schematic()
    assert sch.uuid is not None  # new_schematic sets it; kiutils types it Optional
    sheet = {"sheet_uuid": sch.uuid, "project": path.stem}
    sch.libSymbols.append(build_demorgan_symbol())
    sch.schematicSymbols.append(place_unit("NandGate", 100, 100, 1, ("1", "2", "3"), **sheet))
    sch.schematicSymbols.append(place_unit("NandGate", 150, 100, 2, ("7", "14"), **sheet))
    for text, (lx, ly) in (("NAND_A", drawn), ("PHANTOM", other)):
        sch.labels.append(
            LocalLabel(
                text=text,
                position=Position(X=lx, Y=ly, angle=0),
                effects=_default_effects(),
                uuid=_gen_uuid(),
            )
        )
    sch.filePath = str(path)
    sch.to_file()
    if body_style != 1:
        # The instance data repeats (unit 1), so edit the placed node itself.
        tree = _cst.parse(path.read_bytes())
        gate = next(
            s for s in tree.lists[0].find_all("symbol") if s.find("unit").atoms[1].text == "1"
        )
        convert = _cst.parse(b"(convert %d)" % body_style).lists[0]
        gate.insert_after(gate.find("unit"), convert, b" ")
        path.write_bytes(_cst.serialize(tree))
    return str(path)


# ---------------------------------------------------------------------------
# Helpers (public — importable and used by tests directly)
# ---------------------------------------------------------------------------


def reparse(path: str | Path) -> Schematic:
    """Re-read a schematic through kiutils. Returns the Schematic object.

    kiutils is a reader independent of the CST, which is all this is good for: it is
    not KiCad's reader. It decodes escapes and non-ASCII text differently from KiCad
    and drops tokens it does not model (docs/adr-cst-substrate.md, the codec rule and
    the slice-17 entry), so a test asserting what KiCad reads, text or structure,
    reads the file through the CST, the lexer port in tests/kicad_ref, or kicad-cli.
    """
    return Schematic.from_file(str(path))


def _cli_report(args: list[str], path: Path, name: str):
    """Run ``kicad-cli *args --output <report> path``; return (process, report bytes or None).

    The report goes to a directory created for this run alone, so it can only be this
    run's: kicad-cli writes none for a file it cannot load, and a report an earlier run
    left beside the file passed for its verdict (measured on kicad-cli 9.0.8,
    2026-10-10: a schematic truncated after one good ERC run read back that run's
    report).
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        out = Path(scratch) / name
        result = _run_cli([*args, "--output", str(out), str(path)], check=False)
        return result, (out.read_bytes() if out.is_file() else None)


def run_erc(path: str | Path) -> dict:
    """Run ``kicad-cli sch erc`` and return the parsed JSON report.

    Raises RuntimeError when kicad-cli writes no report, which is how it answers a
    schematic it cannot load.
    """
    result, report = _cli_report(
        ["sch", "erc", "--format", "json", "--severity-all"], Path(path), "erc.json"
    )
    if report is None:
        raise RuntimeError(
            f"kicad-cli ERC failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return json.loads(report)


def parse_netlist_nodes(xml: bytes) -> dict[str, dict[tuple[str, str], str]]:
    """``{net name: {(reference, pin number): pin type}}`` from a kicadxml netlist.

    A root-sheet local label's net prints as ``/NAME``; one leading slash is dropped
    from the key. Raises RuntimeError when two nets would share a key, that is when
    they print the same name before or after that slash goes: kicadxml prints names
    unescaped, so a root label ``A{slash}B`` and sheet A's local label ``B`` both
    print ``/A/B`` (measured on kicad-cli 9.0.8, 2026-10-10), and a root label ``X``
    keys like a global ``X``. Keyed by name, one net would silently replace the
    other; tests/netlist_oracle.py compares by node set instead.
    """
    nets: dict[str, dict[tuple[str, str], str]] = {}
    printed: dict[str, str] = {}
    for net in ET.fromstring(xml).iter("net"):
        name = net.get("name", "")
        key = name[1:] if name.startswith("/") else name
        nodes = {
            (node.get("ref", ""), node.get("pin", "")): node.get("pintype", "")
            for node in net.findall("node")
        }
        if key in nets:
            raise RuntimeError(
                f"two nets key as {key!r}: {printed[key]!r} with {sorted(nets[key])} and"
                f" {name!r} with {sorted(nodes)}; compare by node set"
                " (tests/netlist_oracle.py) where names can collide"
            )
        nets[key], printed[key] = nodes, name
    return nets


def netlist_nodes(path: str | Path) -> dict[str, dict[tuple[str, str], str]]:
    """Run ``kicad-cli sch export netlist`` and return KiCad's own connectivity.

    parse_netlist_nodes of the export: ``{net name: {(reference, pin number): pin
    type}}``, refusing two nets that print alike. A pin under a no-connect flag
    carries the ``+no_connect`` suffix on its type. This is the oracle for anything
    that decides which placed symbol a pin belongs to.
    """
    result, xml = _cli_report(
        ["sch", "export", "netlist", "--format", "kicadxml"], Path(path), "netlist.xml"
    )
    if xml is None:
        raise RuntimeError(
            f"kicad-cli netlist export failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return parse_netlist_nodes(xml)


_COMP_RE = re.compile(rb'<comp ref="([^"]*)"[^>]*>(.*?)</comp>', re.S)
_FIELDS_RE = re.compile(rb"<fields>(.*?)</fields>", re.S)
_FIELD_RE = re.compile(rb'<field name="([^"]*)"\s*(?:/>|>(.*?)</field>)', re.S)
_ENTITY_RE = re.compile(r"&(amp|lt|gt|quot|apos|#[0-9]+|#x[0-9A-Fa-f]+);")
_NAMED_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


def _xml_text(raw: bytes) -> str:
    """Element or attribute text as written: UTF-8, entities decoded once."""

    def entity(match: re.Match) -> str:
        name = match.group(1)
        if name.startswith("#x"):
            return chr(int(name[2:], 16))
        if name.startswith("#"):
            return chr(int(name[1:]))
        return _NAMED_ENTITIES[name]

    return _ENTITY_RE.sub(entity, raw.decode("utf-8", "surrogateescape"))


def parse_comp_fields(xml: bytes) -> dict[tuple[str, str], str]:
    """``{(reference, field name): value}`` from a kicadxml netlist's raw bytes.

    Read with regexes, not an XML parser: KiCad writes control characters raw (BEL,
    BS, FF, VT measured on kicad-cli 9.0.8, 2026-10-10), which no conforming parser
    accepts, and escapes only & < > (plus CR as &#xD; and, in attributes, " TAB LF).
    Only each component's own first <fields> block counts; KiCad 10 may add more under
    <variants> after it. A field KiCad prints empty is written <field name="X"/> and
    reads as "".
    """
    out: dict[tuple[str, str], str] = {}
    for comp in _COMP_RE.finditer(xml):
        block = _FIELDS_RE.search(comp.group(2))
        if block is None:
            continue
        ref = _xml_text(comp.group(1))
        for field in _FIELD_RE.finditer(block.group(1)):
            out[(ref, _xml_text(field.group(1)))] = _xml_text(field.group(2) or b"")
    return out


def netlist_fields(path: str | Path) -> dict[tuple[str, str], str] | None:
    """KiCad's reading of every placed symbol's user fields, or None if it cannot load.

    Runs ``kicad-cli sch export netlist --format kicadxml`` into a fresh scratch file
    and returns parse_comp_fields of it. KiCad passes each value through
    UnescapeString and text-variable expansion before printing it, so a value holding
    "{" or "${" does not read back verbatim; choose values without them. A field KiCad
    does not print is absent from the result.
    """
    _, xml = _cli_report(
        ["sch", "export", "netlist", "--format", "kicadxml"], Path(path), "fields.xml"
    )
    return None if xml is None else parse_comp_fields(xml)


def _is_list(item, head: bytes) -> bool:
    """Whether a lexer-port item is a list whose first item is the atom ``head``."""
    return (
        item.kind == _dsn.GROUP
        and bool(item.items)
        and item.items[0].kind in _dsn.ATOM_KINDS
        and item.items[0].value == head
    )


#: A schematic at or below this format names a sheet's file by the position of its
#: property, not by its name (parseSheet: sch_io_kicad_sexpr_parser.cpp:3538-3547 at
#: 9.0.8, 3793-3802 at 10.0.7).
_POSITIONAL_SHEET_FIELDS_MAX = 20200310

#: A sheet's property as _sheet_files reads it: (name, value, the N an (id N) sets).
_SheetField = tuple[bytes, bytes, int | None]


def _sheet_field(item) -> _SheetField | None:
    """A (property ...) list of a sheet as a _SheetField; None for any other item.

    A leading bare ``private`` is skipped, as parseSchField skips it (2201-2209 at 9.0.8,
    2290-2296 at 10.0.7). The N is that of the last (id N) whose N is 2 or more, the only
    ones KiCad 9 applies to a sheet's field (2292-2300 at 9.0.8). A property without a name
    and a value is a parse error that fails the whole file, so it is left out.
    """
    if not _is_list(item, b"property"):
        return None
    parts = item.items[1:]
    if parts and parts[0].kind == _dsn.SYMBOL and parts[0].value == b"private":
        parts = parts[1:]
    if len(parts) < 2 or not {parts[0].kind, parts[1].kind} <= _dsn.ATOM_KINDS:
        return None
    ids = [
        _dsn._parse_int(p.items[1].value)
        for p in parts[2:]
        if _is_list(p, b"id") and len(p.items) > 1 and p.items[1].kind == _dsn.NUMBER
    ]
    return parts[0].value, parts[1].value, next((n for n in reversed(ids) if n >= 2), None)


def _k9_sheet_file(fields: list[_SheetField], positional: bool) -> bytes | None:
    """The value KiCad 9.0.8 takes as a sheet's file name, or None when it takes none.

    parseSchField gives each property an id (sch_io_kicad_sexpr_parser.cpp at 9.0.8): 2,
    the first after the two mandatory ones (2236-2245); 0 or 1 for exactly "Sheetname" or
    "Sheetfile" (2265-2273), or the legacy "Sheet name" or "Sheet file" in any case
    (2275-2279); then an (id N) replaces it (2292-2300). parseSheet numbers the
    properties by position at or below 20200310 (3538-3547) and moves a repeated id up
    until it is free (3560-3561). SCH_SHEET::SetFields sorts the fields by id, renumbers
    each field whose id is neither 0 nor 1 to its index, and reads the file name from
    index 1 (sch_sheet.cpp:405-425 at 9.0.8). So the field that sorts second names the
    file, whatever it is called: after a "Sheetname", a "sheetfile" or a "Foo" does, and
    with no "Sheetname" the field sorting after the "Sheetfile" does, not the "Sheetfile".
    With one field, index 1 is past the end of the list and the sheet loads no file.
    Measured on kicad-cli 9.0.8 for each case here (2026-10-11).
    """
    taken: set[int] = set()
    numbered: list[tuple[int, bytes]] = []
    for index, (name, value, given) in enumerate(fields):
        field_id = 2
        if name == b"Sheetname" or name.lower() == b"sheet name":
            field_id = 0
        elif name == b"Sheetfile" or name.lower() == b"sheet file":
            field_id = 1
        if given is not None:
            field_id = given
        if positional:
            field_id = 0 if index == 0 else 1
        while field_id in taken:
            field_id += 1
        taken.add(field_id)
        numbered.append((field_id, value))
    numbered.sort()
    return numbered[1][1] if len(numbered) > 1 else None


def _k10_sheet_file(fields: list[_SheetField], positional: bool) -> bytes | None:
    """The value KiCad 10.0.7 takes as a sheet's file name, or None when it takes none.

    parseSchField makes a property the file when its name is "Sheetfile" or the legacy
    "Sheet file", in any case (sch_io_kicad_sexpr_parser.cpp:2353-2366 at 10.0.7), and
    ignores an (id N) (2399-2400); at or below 20200310, parseSheet makes every property
    after the first the file (3793-3802). SCH_SHEET::SetFields reads the first of them
    (sch_sheet.cpp:415-421, FindField at sch_field.h:382-391), or with none an empty
    field the sheet adds (sch_sheet.cpp:381-388).
    """
    for index, (name, value, _given) in enumerate(fields):
        is_file = index > 0 if positional else name.lower() in (b"sheetfile", b"sheet file")
        if is_file:
            return value
    return None


def _sheet_files(data: bytes, directory: str) -> list[str | None]:
    """The file of each sheet the schematic ``data`` holds, resolved as KiCad resolves it.

    Read with the lexer port, not the CST under test: each (sheet ...) of the top-level
    list and its properties. Which property names the file is the rule of the major of
    the kicad-cli that loads it: _k9_sheet_file below 10, _k10_sheet_file from 10. None
    stands for a sheet that names no file, or an empty one, which KiCad cannot load.
    Names and values are compared as bytes, a name's case folded in ASCII only.
    Backslashes become '/' as SCH_SHEET::SetFileName stores them (sch_sheet.h:324-330 at
    9.0.8), and a relative name is joined to the referencing file's own directory, which
    KiCad's loader keeps on a stack while it descends (sch_io_kicad_sexpr.cpp:178-199 at
    9.0.8), then normalised by text the way wxFileName::MakeAbsolute removes "..". A
    file the port cannot read yields nothing: kicad-cli judges that file itself.
    """
    try:
        top = _dsn.tree(data, knows_bar=_dsn.knows_bar_of(data))
    except _dsn.LexError:
        return []
    version = _dsn.file_version(data)
    positional = version is not None and version <= _POSITIONAL_SHEET_FIELDS_MAX
    pick = _k10_sheet_file if _kicad_cli_major() >= 10 else _k9_sheet_file
    found: list[str | None] = []
    for doc in top[:1]:
        if doc.kind != _dsn.GROUP:
            continue
        for sheet in doc.items:
            if not _is_list(sheet, b"sheet"):
                continue
            fields = [f for f in map(_sheet_field, sheet.items) if f is not None]
            value = pick(fields, positional)
            if not value:
                found.append(None)
                continue
            name = value.decode("utf-8", "surrogateescape").replace("\\", "/")
            found.append(os.path.normpath(os.path.join(directory, name)))
    return found


def _schematic_tree(path: str | Path) -> list[tuple[str, bytes | None, str | None]]:
    """(file, key, problem) for the schematic ``path`` and every sheet file below it.

    Each file appears once with its key, in depth-first order from ``path``. ``key``
    digests the file and, recursively, every sheet under it, UUIDs normalised (_UUID_RE),
    because loading a schematic loads its sheets and so kicad-cli's verdict can depend on
    all of them. A problem says why a sheet cannot load at all: its file is missing, it
    names no file (reported against the file holding the sheet), or the file is its own
    ancestor, which KiCad's loader refuses (sch_io_kicad_sexpr.cpp:202-222 at 9.0.8).
    KiCad reports each as "The entire schematic could not be loaded"
    (eeschema/files-io.cpp:284-289 at 9.0.8), even though kicad-cli carries on without a
    word (EESCHEMA_HELPERS::LoadSchematic catches only the root's errors,
    eeschema_helpers.cpp:165-171 at 9.0.8).
    """
    out: list = []
    keys: dict[str, bytes] = {}

    def visit(file: str, ancestors: frozenset[str]) -> bytes:
        ident = os.path.normcase(file)
        if ident in ancestors:
            out.append((file, None, "is its own ancestor in the sheet hierarchy"))
            return b"cycle"
        if ident in keys:
            return keys[ident]
        try:
            data = Path(file).read_bytes()
        except OSError:
            keys[ident] = b"missing"
            out.append((file, None, "is referenced as a sheet but cannot be read"))
            return keys[ident]
        slot = len(out)
        out.append(None)
        digest = hashlib.sha256(_UUID_RE.sub(b"U", data))
        for child in _sheet_files(data, os.path.dirname(file)):
            if child is None:
                out.append((file, None, "holds a sheet that names no file"))
                digest.update(b"nameless")
                continue
            digest.update(visit(child, ancestors | {ident}))
        keys[ident] = digest.digest()
        out[slot] = (file, keys[ident], None)
        return keys[ident]

    visit(os.path.normpath(os.path.abspath(path)), frozenset())
    return out


def assert_kicad_parseable(path: str | Path) -> None:
    """Assert that kicad-cli loads the schematic and, each on its own, every sheet below it.

    ERC violations are ignored: the signal is whether kicad-cli writes a report at all,
    which it does not for a file it cannot load. Loading the root is not enough,
    because kicad-cli loads a root whose sheets fail and reports nothing about them
    (measured on kicad-cli 9.0.8, 2026-10-10: a root with a truncated child ran ERC
    with exit 0 and listed the child with no violations), so every sheet file in the
    hierarchy is loaded standalone too, and a missing one, a sheet naming none or a
    cycle fails outright (see _schematic_tree). Memoised on the keys _schematic_tree
    computes.
    """
    tree = _schematic_tree(path)
    name = Path(path).name
    problems = [f"{file} {problem}" for file, _, problem in tree if problem]
    if problems:
        pytest.fail(f"the sheet hierarchy of {name} cannot load:\n" + "\n".join(problems))
    for file, key, _ in tree:
        if key is None or key in _VALIDATED:  # None only beside a problem, failed above
            continue
        result, report = _cli_report(
            ["sch", "erc", "--format", "json", "--severity-all"], Path(file), "erc.json"
        )
        if report is None:
            role = "" if file == tree[0][0] else f", a sheet of {name}"
            pytest.fail(
                f"kicad-cli cannot parse {Path(file).name}{role}"
                f" (rc={result.returncode}): {result.stderr.strip()[:400]}"
            )
        _VALIDATED.add(key)


#: Highest board format the KiCad 9 line can load. Same boundary pcb.py gates
#: the numeric-net dialect on, and the same one that decides whether the local
#: kicad-cli can read a file at all.
_K9_BOARD_VERSION_MAX = 20241229
_VERSION_RE = re.compile(rb"\(version\s+(\d+)\)")


@lru_cache(maxsize=1)
def _kicad_cli_major() -> int:
    """Major version of the resolved kicad-cli, or 0 when it cannot be read."""
    try:
        out = _run_cli(["version"], check=False).stdout.strip()
    except Exception:
        return 0
    return int(out.split(".")[0]) if out[:1].isdigit() else 0


def _cli_can_load_board(data: bytes) -> bool:
    """A KiCad 9 kicad-cli cannot open a KiCad 10 board, and that is not a bug.

    Without this the oracle reports 26 false failures on a KiCad 9 machine, all
    of them the K10 fixtures. The K10 boards still get validated wherever
    kicad-cli is KiCad 10: the Linux 10.0 leg of kicad-suite.yml (from KiCad's
    PPA), and the macOS and Windows runners (from the Homebrew cask and from
    KiCad's own Windows installer).
    """
    m = _VERSION_RE.search(data[:400])
    if m is None or _kicad_cli_major() >= 10:
        return True
    return int(m.group(1)) <= _K9_BOARD_VERSION_MAX


def assert_kicad_pcb_parseable(path: str | Path) -> None:
    """The board twin of assert_kicad_parseable.

    Same trick: DRC violations are irrelevant, the signal is whether kicad-cli
    could load the board at all. A board it refuses produces no report file and
    prints "Failed to load board".
    """
    result, report = _cli_report(
        ["pcb", "drc", "--format", "json", "--severity-all"], Path(path), "drc.json"
    )
    if report is None:
        pytest.fail(
            f"kicad-cli cannot parse {Path(path).name} "
            f"(rc={result.returncode}): {result.stderr.strip()[:400]}"
        )


#: Newest symbol-library format a KiCad 9 kicad-cli loads: SEXPR_SYMBOL_LIB_FILE_VERSION
#: (eeschema/sch_file_versions.h:55 at 9.0.8; it is 20251024 at 10.0.7). Footprints
#: share the board format, so _K9_BOARD_VERSION_MAX bounds them (SEXPR_BOARD_FILE_VERSION,
#: pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.h:175 at 9.0.8).
_K9_SYMBOL_LIB_VERSION_MAX = 20241209


def _cli_can_load_library(data: bytes, k9_max: int) -> bool:
    """_cli_can_load_board for a symbol library or a footprint.

    A KiCad 9 kicad-cli is not asked about one stamped past KiCad 9's format.
    Measured on kicad-cli 9.0.8 (2026-10-10): it refuses a symbol library stamped
    20251024 and happens to load a footprint stamped 20260206, but KiCad 10 content
    can hold tokens KiCad 9 never had, so both are left to the legs where kicad-cli
    is 10 or newer.
    """
    if _kicad_cli_major() >= 10:
        return True
    version = _dsn.file_version(data)
    return version is None or version <= k9_max


def _kicad_cli_library_problem(path: Path) -> str | None:
    """None when kicad-cli loads the symbol library or footprint at ``path``, else why not.

    kicad-cli has no load-only command for a library, so the upgrade stands in: it
    loads the library and, with --force, saves it to the --output path, answering
    "Unable to load library" with exit 2 when the load fails (JobSymUpgrade,
    eeschema/eeschema_jobs_handler.cpp:1061-1145 at 9.0.8, 1205 at 10.0.7;
    JobExportFpUpgrade, pcbnew/pcbnew_jobs_handler.cpp:1707-1798 at 9.0.8, 2054 at
    10.0.7). It runs on a copy in a scratch directory, never on the file under test.
    A footprint goes into a .pretty of its own, since that is what fp upgrade loads.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        work = Path(scratch)
        if path.suffix == ".kicad_sym":
            source, out = work / "in.kicad_sym", work / "out.kicad_sym"
            shutil.copyfile(path, source)
            result = _run_cli(
                ["sym", "upgrade", "--force", "--output", str(out), str(source)], check=False
            )
            loaded = out.is_file()
        else:
            source, out = work / "in.pretty", work / "out.pretty"
            source.mkdir()
            shutil.copyfile(path, source / path.name)
            result = _run_cli(
                ["fp", "upgrade", "--force", "--output", str(out), str(source)], check=False
            )
            loaded = (out / path.name).is_file()
    if result.returncode == 0 and loaded:
        return None
    said = (result.stdout + result.stderr).strip()
    return f"rc={result.returncode}: {said[-400:]}"


#: The library tables the oracle reads, by file name, with the keyword each opens with.
_LIB_TABLE_KEYWORDS = {"sym-lib-table": b"sym_lib_table", "fp-lib-table": b"fp_lib_table"}
#: What KiCad 10's table grammar takes as space: PEGTL's space rule.
_PEGTL_SPACE = b" \t\n\r\x0b\x0c"


def _lib_table_problem(data: bytes, name: str) -> str | None:
    """Why KiCad 9 and KiCad 10 could read the library table ``name`` differently, or None.

    ``name`` is the file's name, sym-lib-table or fp-lib-table, which fixes the keyword
    it must open with. KiCad 9 reads a table with DSNLEXER, taking '(' and then that
    keyword as its first two tokens (eeschema/symbol_lib_table.cpp:145-151,
    common/fp_lib_table.cpp:91-97 at 9.0.8), never reading past the table's ')', and
    decoding escapes inside quotes (dsnlexer.cpp:640-733). KiCad 10 parses the whole file
    as LIB_TABLE_FILE: '(' as its first byte, the keyword, the table, then only space to
    the end; its QUOTED_TEXT is a quote, raw bytes and a quote, decoding nothing
    (include/libraries/library_table_grammar.h:60 and 119-127,
    common/libraries/library_table_parser.cpp:95-105 and 161-168 at 10.0.7). So the rule:
    the file opens with '(' and the keyword at its first byte, so an empty file fails, and
    holds only space after the table; it lexes as KiCad 9 reads it, with no bare '|' (a
    separator to KiCad 9's table lexer, which keeps the bar its init turns on, an ordinary
    byte to KiCad 10); it parses as this package's own table reader does, through the CST
    (pcb._read_fp_lib_table); and no value holds a backslash or a double quote, since only
    a value with neither reads alike in both. Every table KiCad 9.0.8 ships obeys it (49
    files, measured 2026-10-11).
    """
    keyword = _LIB_TABLE_KEYWORDS[name]
    knows_bar = _dsn.knows_bar_of(data)
    try:
        top = _dsn.tree(data, knows_bar=knows_bar)
        toks = _dsn.tokens(data, knows_bar=knows_bar)
    except _dsn.LexError as exc:
        return f"KiCad 9's lexer refuses it: {exc}"
    opening = [(tok.kind, tok.value, tok.start) for tok in toks[:2]]
    if opening != [(_dsn.LEFT, b"(", 0), (_dsn.SYMBOL, keyword, 1)]:
        return (
            f"it does not open with ({keyword.decode()} at its first byte, which KiCad 10"
            " requires and KiCad 9 reads as its first two tokens"
        )
    if len(top) != 1 or data[top[0].end :].strip(_PEGTL_SPACE):
        return (
            f"bytes other than space follow the table's ')' at byte {top[0].end - 1}, which"
            " KiCad 10 refuses and KiCad 9 never reads"
        )
    try:
        _cst.parse(data)
    except SyntaxError as exc:
        return f"the CST refuses it: {exc}"
    for tok in toks:
        raw = data[tok.start : tok.end]
        if tok.kind == _dsn.BAR:
            return f"a bare '|' at byte {tok.start}, which KiCad 9 lexes apart and KiCad 10 keeps"
        if tok.kind not in _dsn.ATOM_KINDS:
            continue
        body = raw[1:-1] if tok.kind == _dsn.STRING else raw
        if b"\\" in body or b'"' in body:
            return (
                f"the value {raw[:80]!r} at byte {tok.start} holds a backslash or a double"
                " quote; only a value with neither reads alike in KiCad 9, which decodes"
                " escapes, KiCad 10, which decodes none, and the CST"
            )
    return None


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------

#: Tools the unified server registers.  Asserted by every test that talks to a
#: server over the wire, because a subset silently shipping is issue #2.
EXPECTED_TOOL_COUNT = 109


class StdioClient:
    """Drive an MCP server as a subprocess over stdio, the way a host does.

    Importing the tool functions and calling them never exercises the protocol
    layer, and that is where the packaging and host-behaviour defects have
    lived.  Two test files need this, so the transport lives here rather than
    being written twice.

    ``initialize`` runs on entry, so ``server_info`` and ``instructions`` are
    populated by the time the context manager yields.
    """

    def __init__(self, argv: list[str], env: dict[str, str] | None = None):
        self.argv = argv
        self.env = env
        self.proc: subprocess.Popen[str] | None = None
        self.server_info: dict = {}
        self.instructions: str = ""
        self._id = 0

    def __enter__(self):
        self.proc = subprocess.Popen(
            self.argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=self.env,
        )
        init = self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        )["result"]
        self.notify("notifications/initialized")
        self.server_info = init["serverInfo"]
        # What a host puts in its system prompt.  build_server appends _ACCESS
        # to this, so it is the only place that text is observable from outside.
        self.instructions = init.get("instructions") or ""
        return self

    def __exit__(self, *exc):
        if not self.proc:
            return
        # Closing stdin is the stdio transport's shutdown signal, so give the
        # server a chance to exit on its own before killing it.  `uv run` spawns
        # python as a child, and terminating uv does not reap the grandchild on
        # Windows, which then keeps cryptography's _rust.pyd mapped.
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=15)

    def rpc(self, method: str, params: dict | None = None) -> dict:
        assert self.proc and self.proc.stdin and self.proc.stdout
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                stderr = self.proc.stderr.read() if self.proc.stderr else ""
                pytest.fail(f"server died during {method}:\n{stderr[-2000:]}")
            if line.strip():
                return json.loads(line)

    def notify(self, method: str) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def call(self, name: str, args: dict) -> str:
        result = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        assert not result.get("isError"), f"{name} failed: {json.dumps(result)[:500]}"
        return result["content"][0]["text"]

    def tools(self) -> list[dict]:
        found = self.rpc("tools/list")["result"]["tools"]
        assert len(found) == EXPECTED_TOOL_COUNT, (
            f"expected {EXPECTED_TOOL_COUNT} tools over stdio, got {len(found)}"
        )
        return found


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


#: The files the oracle hands to kicad-cli, by suffix.
_CLI_SUFFIXES = (".kicad_sch", ".kicad_pcb", ".kicad_sym", ".kicad_mod")


def _oracle_files(root: Path) -> dict[str, Path]:
    """Every file under ``root`` the oracle checks, keyed by its path relative to ``root``
    in '/' form: each library table by name, each schematic, board, symbol library and
    footprint by suffix."""
    return {
        path.relative_to(root).as_posix(): path
        for path in sorted(root.rglob("*"))
        if path.is_file() and (path.name in _LIB_TABLE_KEYWORDS or path.suffix in _CLI_SUFFIXES)
    }


def _validate_kicad_outputs(root: Path, skip: frozenset[str] = frozenset()) -> None:
    """The autouse oracle's checks, over every file under ``root`` that _oracle_files names.

    - ``sym-lib-table`` and ``fp-lib-table``: _lib_table_problem, which needs no KiCad;
    - ``*.kicad_sch``: assert_kicad_parseable, the file and every sheet below it;
    - ``*.kicad_pcb``: assert_kicad_pcb_parseable;
    - ``*.kicad_sym`` and ``*.kicad_mod``: _kicad_cli_library_problem.

    Whatever a file's first bytes, an empty file included: KiCad refuses a file with a
    byte order mark or a stray byte in front as surely as one cut short, and a tool
    leaving a truncated file is what the invariant forbids. ``skip`` names files, by
    their _oracle_files key, that are not checked on their own (the kicad_placeholder
    marker's); a schematic that has one as a sheet still loads it. A board or library
    newer than the local kicad-cli can load is left to a kicad-cli that can.
    """
    files = [path for key, path in _oracle_files(root).items() if key not in skip]
    for path in files:
        if path.name in _LIB_TABLE_KEYWORDS:
            if problem := _lib_table_problem(path.read_bytes(), path.name):
                pytest.fail(f"{path}: {problem}")
    if not HAS_KICAD_CLI:
        return
    for path in files:
        if path.name in _LIB_TABLE_KEYWORDS:
            continue
        if path.suffix == ".kicad_sch":
            assert_kicad_parseable(path)  # memoised per file of the hierarchy
            continue
        data = path.read_bytes()
        if path.suffix == ".kicad_pcb":
            loadable = _cli_can_load_board(data)
        elif path.suffix == ".kicad_sym":
            loadable = _cli_can_load_library(data, _K9_SYMBOL_LIB_VERSION_MAX)
        else:
            loadable = _cli_can_load_library(data, _K9_BOARD_VERSION_MAX)
        if not loadable:
            continue
        # Fixtures differ only in freshly generated UUIDs, so the same file is
        # otherwise re-validated hundreds of times per run. A UUID cannot turn a
        # loadable file unloadable, so normalise them out and validate each distinct
        # file once. Measured 2026-08-10 for schematics: 376 spawns -> 186.
        seen = hashlib.sha256(_UUID_RE.sub(b"U", data)).digest()
        if seen in _VALIDATED:
            continue
        if path.suffix == ".kicad_pcb":
            assert_kicad_pcb_parseable(path)
        elif problem := _kicad_cli_library_problem(path):
            pytest.fail(f"kicad-cli cannot load {path.name} ({path}): {problem}")
        _VALIDATED.add(seen)


@pytest.fixture(autouse=True)
def _validate_kicad_output(request, tmp_path: Path):
    """After each test, validate every KiCad file it left in tmp_path (_validate_kicad_outputs).

    Catches format-level bugs (e.g. kiutils omitting required fields)
    that kiutils round-trip tests miss because kiutils reads back its
    own malformed output without error.

    This is the project's output oracle, and the reason it is worth its runtime
    is that it turns every valid tool call the suite already makes into a
    corpus. Boards were outside it until 2026-08-12, which is exactly why
    add_trace(layer="banana") could write a board kicad-cli refuses to load and
    nothing in the suite noticed; symbol libraries, footprints and library tables
    were outside it until the test ladder, which is how create_sym_lib_table could
    write a table KiCad refuses (bug 4).

    A test that writes a placeholder, or a malformed input that is not its subject,
    names each such file by its path under tmp_path::

        @pytest.mark.kicad_placeholder("empty.kicad_pcb", "sub/stub.kicad_sch")

    and the oracle checks every other file. A test that passes without leaving a
    file its marker names fails, so a marker cannot outlive its file or miss it by a
    typo. Tests whose subject is the malformed file opt out entirely with::

        @pytest.mark.no_kicad_validation

    Reach for that marker only when the malformed file *is* the subject, as in
    test_cst.py. Using it to silence a tool writing a bad file is how the
    45-degree rotation defect survived.

    A test whose call phase failed as its strict xfail expects is not validated
    either: the xfail already records that the output is wrong, and removing the
    marker brings the check back. A pass or an XPASS is validated as usual.
    """
    yield
    if request.node.get_closest_marker("no_kicad_validation"):
        return
    call = request.node.stash.get(_CALL_REPORT, None)
    if _call_xfailed(call):
        return
    skip = _placeholder_names(request.node)
    if skip and call is not None and call.passed:
        unseen = sorted(skip.difference(_oracle_files(tmp_path)))
        if unseen:
            pytest.fail(
                f"kicad_placeholder names {unseen}, but the test left no such file among"
                f" those the oracle checks in {tmp_path}"
            )
    _validate_kicad_outputs(tmp_path, skip)


def _placeholder_names(node) -> frozenset[str]:
    """The files the kicad_placeholder markers on ``node`` name; a marker naming none fails."""
    names: set[str] = set()
    for marker in node.iter_markers("kicad_placeholder"):
        if not marker.args or not all(isinstance(name, str) for name in marker.args):
            pytest.fail(f"kicad_placeholder takes each placeholder's path, got {marker.args!r}")
        names.update(marker.args)
    return frozenset(names)


@pytest.fixture()
def scratch_sch(tmp_path: Path) -> Path:
    """Create a schematic with a Device:R symbol, placed R1, label, and wire.

    Contents:
        - lib_symbols: Device:R (2-pin passive, standard resistor body)
        - Placed R1 at (100, 100)
        - Net label "TEST_NET" at (50, 50)
        - Wire from (50, 50) to (80, 50)

    Returns the file path.
    """
    sch = new_schematic()

    # Library symbol
    sch.libSymbols.append(build_r_symbol())

    # Placed component
    sch.schematicSymbols.append(place_r1(100, 100))

    # Net label
    sch.labels.append(
        LocalLabel(
            text="TEST_NET",
            position=Position(X=50, Y=50, angle=0),
            effects=_default_effects(),
            uuid=_gen_uuid(),
        )
    )

    # Wire
    sch.graphicalItems.append(
        Connection(
            type="wire",
            points=[Position(X=50, Y=50), Position(X=80, Y=50)],
            stroke=_default_stroke(),
            uuid=_gen_uuid(),
        )
    )

    path = tmp_path / "scratch.kicad_sch"
    sch.filePath = str(path)
    sch.to_file()
    return path


@pytest.fixture()
def empty_sch(tmp_path: Path) -> Path:
    """Create a minimal valid empty schematic. Returns the file path."""
    sch = new_schematic()
    path = tmp_path / "empty.kicad_sch"
    sch.filePath = str(path)
    sch.to_file()
    return path


@pytest.fixture(scope="session")
def project_hierarchy(tmp_path_factory):
    """tests/hier_gen.py's project, built once per worker: for tests that only read it.

    Never modify it, and pass an output_dir of your own to a tool that writes a report. It
    sits outside tmp_path, so the autouse oracle does not see it; every sheet file is
    loaded on its own here, once, by assert_kicad_parseable.
    """
    import hier_gen

    built = hier_gen.build(tmp_path_factory.mktemp("hierarchy"))
    assert_kicad_parseable(built.root)
    return built


@pytest.fixture()
def scratch_power_lib(tmp_path: Path) -> Path:
    """Create a .kicad_sym with VCC, GND, and PWR_FLAG power symbols.

    Returns the file path.
    """
    lib = SymbolLib(version=KICAD_SYM_VERSION, generator="kicad_symbol_editor")
    lib.symbols.append(build_power_symbol("VCC", "power_in"))
    lib.symbols.append(build_power_symbol("GND", "power_in"))
    lib.symbols.append(build_power_symbol("PWR_FLAG", "power_out"))

    path = tmp_path / "power.kicad_sym"
    lib.filePath = str(path)
    lib.to_file()
    return path


@pytest.fixture()
def scratch_sym_lib(tmp_path: Path) -> Path:
    """Create a .kicad_sym with a custom 'TestPart' symbol.

    TestPart: 2-pin passive.
        Pin 1 "IN"  at (-5.08, 0) rot 0
        Pin 2 "OUT" at ( 5.08, 0) rot 180

    Returns the file path.
    """
    lib = SymbolLib(version=KICAD_SYM_VERSION, generator="kicad_symbol_editor")
    lib.symbols.append(build_test_part_symbol())

    path = tmp_path / "test_lib.kicad_sym"
    lib.filePath = str(path)
    lib.to_file()
    return path


@pytest.fixture()
def stock_symbol_dir(tmp_path: Path) -> Path:
    """A stand-in for KiCad's stock symbol folder: Device (R) and power (VCC, GND, PWR_FLAG).

    Point KICAD_SYMBOL_DIR at it with monkeypatch.setenv and a lib_id such as
    "Device:R" resolves here, on any host, because that folder is searched
    before KiCad's own. A test that places a stock part then no longer depends
    on KiCad being installed, and the symbol is still copied in under its
    prefixed name, exactly as it is from a real install.

    Returns the folder.
    """
    folder = tmp_path / "stock_symbols"
    folder.mkdir()

    device = SymbolLib(version=KICAD_SYM_VERSION, generator="kicad_symbol_editor")
    device.symbols.append(build_r_symbol())
    device.filePath = str(folder / "Device.kicad_sym")
    device.to_file()

    power = SymbolLib(version=KICAD_SYM_VERSION, generator="kicad_symbol_editor")
    for name, pin_type in (("VCC", "power_in"), ("GND", "power_in"), ("PWR_FLAG", "power_out")):
        power.symbols.append(build_power_symbol(name, pin_type))
    power.filePath = str(folder / "power.kicad_sym")
    power.to_file()
    return folder


@pytest.fixture()
def kicad_native_sch(tmp_path: Path) -> Path:
    """Copy of a hand-written KiCad 9 native schematic into a temp directory.

    Unlike ``scratch_sch`` (built by kiutils), this fixture mirrors the
    exact format KiCad itself writes:

    - lib_symbol named ``"Device:R"`` (with library prefix)
    - ``(dnp no)``, ``(fields_autoplaced)``, ``(instances ...)`` on placed symbols
    - No ``(lib_name ...)`` or ``(id N)`` on placed symbols/properties

    Contents: Device:R lib_symbol, placed R1 at (100, 100), label, wire.
    """
    src = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"
    dst = tmp_path / "kicad_native.kicad_sch"
    shutil.copy2(src, dst)
    return dst


def build_test_footprint(
    ref: str = "R1", value: str = "10K", x: float = 100, y: float = 100
) -> Footprint:
    """Build a minimal footprint with 2 pads."""
    fp = Footprint()
    fp.entryName = "R_0603"
    fp.libId = "Resistor_SMD:R_0603"
    fp.layer = "F.Cu"
    fp.position = Position(X=x, Y=y, angle=0)
    fp.properties = {"Reference": ref, "Value": value}
    fp.graphicItems = [
        FpText(
            type="reference",
            text=ref,
            layer="F.SilkS",
            effects=_default_effects(),
            position=Position(X=0, Y=-2),
        ),
        FpText(
            type="value",
            text=value,
            layer="F.Fab",
            effects=_default_effects(),
            position=Position(X=0, Y=2),
        ),
    ]
    pad1 = Pad()
    pad1.number = "1"
    pad1.type = "smd"
    pad1.shape = "rect"
    pad1.position = Position(X=-0.75, Y=0)
    pad1.size = Position(X=0.7, Y=0.8)
    pad1.layers = ["F.Cu", "F.Paste", "F.Mask"]
    pad1.net = Net(number=1, name="Net1")

    pad2 = Pad()
    pad2.number = "2"
    pad2.type = "smd"
    pad2.shape = "rect"
    pad2.position = Position(X=0.75, Y=0)
    pad2.size = Position(X=0.7, Y=0.8)
    pad2.layers = ["F.Cu", "F.Paste", "F.Mask"]
    pad2.net = Net(number=2, name="Net2")

    fp.pads = [pad1, pad2]
    return fp


@pytest.fixture()
def scratch_pcb(tmp_path: Path) -> Path:
    """Create a scratch PCB with one footprint, one trace, one net, and one edge line."""
    board = Board.create_new()
    # Do not set board.version: KICAD_SCH_VERSION is the *schematic* format
    # version, and stamping it on a board makes kicad-cli refuse to load the
    # file at all.  kiutils' own default is a version KiCad accepts.
    board.generator = "pcbnew"

    # Nets
    board.nets = [Net(number=0, name=""), Net(number=1, name="Net1"), Net(number=2, name="Net2")]

    # Footprint
    board.footprints.append(build_test_footprint())

    # Trace
    seg = Segment()
    seg.start = Position(X=99.25, Y=100)
    seg.end = Position(X=100.75, Y=100)
    seg.width = 0.25
    seg.layer = "F.Cu"
    seg.net = 1
    seg.tstamp = _gen_uuid()
    board.traceItems.append(seg)

    # Edge cut line
    line = GrLine()
    line.start = Position(X=90, Y=90)
    line.end = Position(X=110, Y=110)
    line.layer = "Edge.Cuts"
    line.width = 0.05
    line.tstamp = _gen_uuid()
    board.graphicItems.append(line)

    path = tmp_path / "scratch.kicad_pcb"
    board.filePath = str(path)
    board.to_file()
    return path


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, request):
    """No test reaches the internet. Opt out with @pytest.mark.allow_network.

    _download_jar calls api.github.com, and a test that reaches it is a test
    that fails when GitHub rate-limits a shared CI address. Measured
    2026-08-14: reordering autoroute's preflight so the jar resolves before the
    Java check made test_no_java reach the download, and it failed with "HTTP
    Error 403: rate limit exceeded" on one Python version of one matrix, hours
    after the change had gone green everywhere else.

    _freerouting calls urllib.request.urlopen through the module, so this
    patch reaches it. Until 2026-10-19 it imported urlopen by name, which kept
    the real function, and this guard never covered the download.

    Blocking it here rather than fixing that one test, because the next such
    test will be written by someone who does not know this happened. The
    failure is immediate and names the fixture, instead of arriving later as a
    rate limit somebody has to trace back.
    """
    if request.node.get_closest_marker("allow_network"):
        return
    import urllib.request

    def refuse(*args, **kwargs):
        raise AssertionError(
            "this test tried to open a network connection; mock the seam, or"
            " mark it @pytest.mark.allow_network if it genuinely needs one"
        )

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
