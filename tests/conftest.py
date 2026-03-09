"""Shared fixtures and helpers for mcp-server-kicad tests.

Provides:
    - scratch_sch: schematic with a Device:R lib symbol, placed R1, label, and wire
    - empty_sch: minimal valid empty schematic
    - scratch_sym_lib: .kicad_sym with a custom TestPart symbol
    - reparse: re-read a schematic from disk
    - run_erc: run kicad-cli ERC and return parsed JSON
    - assert_erc_clean: assert zero ERC violations
    - Builder helpers importable by test files for custom fixture creation
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid as _uuid
from pathlib import Path

import pytest

from kiutils.schematic import Schematic
from kiutils.symbol import Symbol, SymbolLib, SymbolPin
from kiutils.items.common import (
    Effects,
    Fill,
    Font,
    Position,
    Property,
    Stroke,
)
from kiutils.items.schitems import Connection, LocalLabel, SchematicSymbol
from kiutils.items.syitems import SyRect
from kiutils.board import Board
from kiutils.footprint import Footprint, Pad
from kiutils.items.brditems import Segment, Via
from kiutils.items.fpitems import FpText
from kiutils.items.gritems import GrLine, GrText
from kiutils.items.common import Net

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KICAD_SCH_VERSION = 20250114
KICAD_SCH_GENERATOR = "eeschema"
KICAD_SYM_VERSION = "20231120"


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


def new_schematic() -> Schematic:
    """Create a minimal valid empty schematic compatible with KiCad 9."""
    sch = Schematic.create_new()
    sch.version = KICAD_SCH_VERSION
    sch.generator = KICAD_SCH_GENERATOR
    sch.uuid = _gen_uuid()
    return sch


# ---------------------------------------------------------------------------
# Helpers (public — importable and used by tests directly)
# ---------------------------------------------------------------------------

def reparse(path: str | Path) -> Schematic:
    """Re-parse a schematic file from disk. Returns the Schematic object."""
    return Schematic.from_file(str(path))


def run_erc(path: str | Path) -> dict:
    """Run ``kicad-cli sch erc`` and return the parsed JSON report.

    The ERC output is written next to *path* with a ``.erc.json`` suffix.
    """
    path = str(path)
    erc_out = path + ".erc.json"
    result = subprocess.run(
        [
            "kicad-cli",
            "sch",
            "erc",
            "--format",
            "json",
            "--severity-all",
            "--output",
            erc_out,
            path,
        ],
        capture_output=True,
        text=True,
    )
    if not os.path.exists(erc_out):
        raise RuntimeError(
            f"kicad-cli ERC failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    with open(erc_out) as f:
        return json.load(f)


def assert_erc_clean(path: str | Path) -> None:
    """Run ERC and assert zero violations."""
    report = run_erc(path)
    violations = report.get("violations", [])
    assert violations == [], (
        f"Expected 0 ERC violations, got {len(violations)}:\n"
        + "\n".join(
            f"  {v.get('severity', '?')}: {v.get('description', '?')}"
            for v in violations
        )
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


def build_test_footprint(ref: str = "R1", value: str = "10K",
                         x: float = 100, y: float = 100) -> Footprint:
    """Build a minimal footprint with 2 pads."""
    fp = Footprint()
    fp.entryName = "R_0603"
    fp.libId = "Resistor_SMD:R_0603"
    fp.layer = "F.Cu"
    fp.position = Position(X=x, Y=y, angle=0)
    fp.properties = {"Reference": ref, "Value": value}
    fp.graphicItems = [
        FpText(type="reference", text=ref, layer="F.SilkS",
               effects=_default_effects(),
               position=Position(X=0, Y=-2)),
        FpText(type="value", text=value, layer="F.Fab",
               effects=_default_effects(),
               position=Position(X=0, Y=2)),
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
    board.version = KICAD_SCH_VERSION  # same version format
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
