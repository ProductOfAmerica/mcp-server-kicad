"""Shared fixtures and helpers for mcp-server-kicad tests.

Provides:
    - scratch_sch: schematic with a Device:R lib symbol, placed R1, label, and wire
    - empty_sch: minimal valid empty schematic
    - scratch_sym_lib: .kicad_sym with a custom TestPart symbol
    - reparse: re-read a schematic from disk
    - run_erc: run kicad-cli ERC and return parsed JSON
    - assert_kicad_parseable: assert kicad-cli can parse the file
    - requires_cli: skip marker for tests that shell out to kicad-cli
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
import uuid as _uuid
from functools import lru_cache
from pathlib import Path

import pytest
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
from kiutils.items.schitems import Connection, LocalLabel, SchematicSymbol
from kiutils.items.syitems import SyRect
from kiutils.schematic import Schematic
from kiutils.symbol import Symbol, SymbolLib, SymbolPin

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

# Schematic bodies already validated this session, keyed on their UUID-normalised
# digest.  See ``_validate_kicad_output``.
_UUID_RE = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_VALIDATED: set[bytes] = set()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _diff_spans(before: bytes, after: bytes) -> tuple[int, int]:
    """Byte counts of the single differing region on each side, after
    stripping the common prefix and suffix."""
    p = 0
    lo = min(len(before), len(after))
    while p < lo and before[p] == after[p]:
        p += 1
    s = 0
    while s < lo - p and before[-1 - s] == after[-1 - s]:
        s += 1
    return len(before) - p - s, len(after) - p - s


def _pure_insertion(before: bytes, after: bytes) -> bool:
    """*after* is *before* with one contiguous run of bytes inserted."""
    return len(after) >= len(before) and _diff_spans(before, after)[0] == 0


def _span_preserved(before: bytes, after: bytes) -> bool:
    """One contiguous insertion OR deletion: the shorter side is untouched."""
    return min(_diff_spans(before, after)) == 0


def _confined(before: bytes, after: bytes, limit: int = 200) -> bool:
    """All differences sit inside one span of <= limit bytes on each side."""
    return max(_diff_spans(before, after)) <= limit


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
    result = _run_cli(
        ["sch", "erc", "--format", "json", "--severity-all", "--output", erc_out, path],
        check=False,
    )
    if not os.path.exists(erc_out):
        raise RuntimeError(
            f"kicad-cli ERC failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    with open(erc_out) as f:
        return json.load(f)


def assert_kicad_parseable(path: str | Path) -> None:
    """Assert that kicad-cli can parse the file without errors.

    Ignores ERC violations: this only fails when the file itself is
    malformed (missing S-expression fields, invalid syntax, etc.).
    """
    try:
        run_erc(path)
    except RuntimeError as exc:
        pytest.fail(f"kicad-cli cannot parse {Path(path).name}: {exc}")


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
    of them the K10 fixtures. The K10 boards still get validated, on the macOS
    and Windows runners where Chocolatey and the app bundle install KiCad 10.
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
    path = str(path)
    drc_out = path + ".drc.json"
    result = _run_cli(
        ["pcb", "drc", "--format", "json", "--severity-all", "--output", drc_out, path],
        check=False,
    )
    if not os.path.exists(drc_out):
        pytest.fail(
            f"kicad-cli cannot parse {Path(path).name} "
            f"(rc={result.returncode}): {result.stderr.strip()[:400]}"
        )


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


@pytest.fixture(autouse=True)
def _validate_kicad_output(request, tmp_path: Path):
    """After each test, validate every generated KiCad file via kicad-cli.

    Catches format-level bugs (e.g. kiutils omitting required fields)
    that kiutils round-trip tests miss because kiutils reads back its
    own malformed output without error.

    This is the project's output oracle, and the reason it is worth its runtime
    is that it turns every valid tool call the suite already makes into a
    corpus. Boards were outside it until 2026-08-12, which is exactly why
    add_trace(layer="banana") could write a board kicad-cli refuses to load and
    nothing in the suite noticed.

    Tests that intentionally produce invalid files can opt out with::

        @pytest.mark.no_kicad_validation

    Reach for that marker only when the malformed file *is* the subject, as in
    test_cst.py. Using it to silence a tool writing a bad file is how the
    45-degree rotation defect survived.
    """
    yield
    if request.node.get_closest_marker("no_kicad_validation"):
        return
    if not HAS_KICAD_CLI:
        return
    for pattern, magic, check, loadable in (
        ("*.kicad_sch", b"(kicad_sch", assert_kicad_parseable, lambda _: True),
        ("*.kicad_pcb", b"(kicad_pcb", assert_kicad_pcb_parseable, _cli_can_load_board),
    ):
        for out_file in tmp_path.rglob(pattern):
            data = out_file.read_bytes()
            # Skip dummy/empty files and anything that isn't a real KiCad file
            # (e.g. config tests that create placeholder paths).
            if not data.startswith(magic) or not loadable(data):
                continue
            # Fixtures differ only in freshly generated UUIDs, so the same file
            # is otherwise re-validated hundreds of times per run.  A UUID cannot
            # turn a parseable file unparseable, so normalise them out and
            # validate each distinct file once.  Measured 2026-08-10 for
            # schematics: 376 spawns -> 186.
            seen = hashlib.sha256(_UUID_RE.sub(b"U", data)).digest()
            if seen in _VALIDATED:
                continue
            check(out_file)
            _VALIDATED.add(seen)


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
