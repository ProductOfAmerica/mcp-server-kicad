"""The schematic readers take a number token as KiCad's parser does.

Judges, none of them the code under test: kicad_ref.parse_units (NeedNUMBER, parseDouble and
parseInternalUnits' clamp, checked against kicad-cli's load verdicts in
test_ladder_r2_judges.py) and kicad-cli itself, which refuses each sheet below.

These tests go through the tools. The readers' own laws over generated tokens are
test_ladder_r2_numbers.py and test_ladder_r2_transform.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import HAS_KICAD_CLI, netlist_fields
from kicad_ref import parse_units as P
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _connectivity as C
from mcp_server_kicad import schematic

_NATIVE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"
_R1_AT = b"(at 100 100 0) (unit 1)"


def _sheet(tmp_path: Path, placement: bytes) -> Path:
    """The native fixture with R1's (at ...) (unit 1) replaced by ``placement``."""
    data = _NATIVE.read_bytes()
    assert data.count(_R1_AT) == 1
    path = tmp_path / "hand.kicad_sch"
    path.write_bytes(data.replace(_R1_AT, placement + b" (unit 1)"))
    return path


def _kicad_refuses(path: Path) -> None:
    """The premise of a test about a sheet KiCad cannot load: kicad-cli, where installed,
    writes no netlist for it. RuntimeError when it loads it."""
    if HAS_KICAD_CLI and netlist_fields(path) is not None:
        raise RuntimeError(f"the premise is a sheet kicad-cli refuses, and it loads {path.name}")


#: R1 placements kicad-cli refuses, each a token the readers took: a leading '+', a literal out
#: of a double's range, one that underflows to zero, an infinity, a quoted coordinate and a
#: mirror axis that is not x or y.
_REFUSED = [
    pytest.param(b"(at +100 100 0)", id="plus_x"),
    pytest.param(b"(at 100 +100 0)", id="plus_y"),
    pytest.param(b"(at 100 100 +90)", id="plus_angle"),
    pytest.param(b"(at 1e400 100 0)", id="overflow_x"),
    pytest.param(b"(at 1e-400 100 0)", id="underflow_x"),
    pytest.param(b"(at 100 100 inf)", id="inf_angle"),
    pytest.param(b'(at "100" 100 0)', id="quoted_x"),
    pytest.param(b"(at 100 100 0) (mirror z)", id="mirror_z"),
]


@pytest.mark.no_kicad_validation  # the refused sheets are the subject; kicad-cli judges each
@pytest.mark.parametrize("placement", _REFUSED)
def test_wire_pins_to_net_refuses_a_sheet_kicad_cannot_load(tmp_path, placement):
    """A placement KiCad's parser refuses is reported as such, with the file as it was, where
    the readers took the token and wired R1 (a '+', a quoted coordinate, a mirror axis z) or
    died on it with an exception no client can read (an infinity, an overflow)."""
    path = _sheet(tmp_path, placement)
    _kicad_refuses(path)
    before = path.read_bytes()
    with pytest.raises(ToolError, match=r"\[unloadable\]"):
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}], label_text="N1", schematic_path=str(path)
        )
    assert path.read_bytes() == before


def test_get_pin_positions_clamps_a_coordinate_as_kicad_does(tmp_path):
    """R1 placed at x = 200000 mm, which kicad-cli loads by clamping to INT_MAX * 0.7071
    internal units: pin 1 sits at R1's x, so the tool reports the clamped x, not 200000."""
    path = _sheet(tmp_path, b"(at 200000 100 0)")
    if HAS_KICAD_CLI:
        assert netlist_fields(path) is not None, "the premise is a sheet kicad-cli loads"
    clamped = P.parse_sch_internal_units(b"200000", major=9) / 10000
    result = schematic.get_pin_positions("R1", schematic_path=str(path))
    found = re.search(r"Pin 1 \(~\): \(([^,]+),", result)
    assert found, result
    x = float(found[1])
    assert x == round(clamped, 2), result


#: Tokens judged by the parser port: an underflow to zero is refused and a subnormal is read
#: (test_ladder_r2_judges.py checks both against kicad-cli).
_TOKENS = ["1e-400", "-1e-400", "0.0000e-400", "1e-310", "-1e-310", "0e-400", "100.", ".5"]


@pytest.mark.parametrize("token", _TOKENS)
def test_iu_reads_a_token_as_kicads_parser_does(token):
    try:
        want = {P.parse_sch_internal_units(token, major=major) for major in (9, 10)}
    except P.ParseRefusal:
        with pytest.raises(C.Refusal):
            C.iu(token)
    else:
        assert C.iu(token) in want
