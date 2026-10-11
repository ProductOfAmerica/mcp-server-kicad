"""How KiCad's parsers read a number token, ported as oracles.

parseDouble( aExpected ) (include/dsnlexer.h:531-535 at 9.0.8, 536-540 at 10.0.7)
first calls NeedNUMBER (common/dsnlexer.cpp:431-443 at 9.0.8, 427-439 at 10.0.7),
which refuses any token the lexer did not classify as a NUMBER (isNumber: an optional
sign, digits around an optional point, an optional exponent; dsn_lexer.is_number).
Then it converts the token's text:

- 9.0.8 (dsnlexer.cpp:859-916) uses std::from_chars on every compiler newer than GCC 11
  and clang 13, and on MSVC, which is what KiCad's 9.0 releases build with. C++17's
  from_chars takes no leading '+' and reports a value out of the double's range.
- 10.0.7 (dsnlexer.cpp:864-881) uses the bundled fast_float 8.2.2
  (thirdparty/fast_float) with chars_format::skip_white_space | general. Its
  parse_number_string accepts a leading '+' only under allow_leading_plus, which KiCad
  does not pass (fast_float/ascii_number.h:299-301 at 10.0.7), and from_chars_advanced
  reports result_out_of_range when the value overflows to infinity or a nonzero
  literal underflows to zero (fast_float/parse_number.h:284-288). Neither CMake file
  that builds it (thirdparty/CMakeLists.txt:49-50, thirdparty/fast_float/
  CMakeLists.txt) defines the deprecated FASTFLOAT_ALLOWS_LEADING_PLUS; a search of
  the whole tree was not possible, so that much is inference.

Both conversions round correctly, as Python's float() does, so a NUMBER token without
a '+' and inside the range reads as float(token). The port refuses the rest:
a non-NUMBER token, a leading '+', an infinite result, and a zero from a literal with
a nonzero digit. Builds of 9.0.8 may treat underflow and subnormal results differently
(the C++ standard leaves it to the library), so the generators keep away from both.
A pre-GCC-11 build would take the strtod branch, which accepts '+'; no KiCad 9 release
is known to be built that way, and that branch is not modelled.

Coordinates are read through these, with a clamp before KiROUND:

- boards, parseBoardUnits (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:
  195-220 at 9.0.8, 200-227 at 10.0.7): KiROUND( std::clamp( v * 1e6, -INT_LIMIT,
  INT_LIMIT ) ) with INT_LIMIT = INT_MAX - 10 (89 at 9.0.8, 94 at 10.0.7);
- schematics and symbol libraries, parseInternalUnits (eeschema/sch_io/kicad_sexpr/
  sch_io_kicad_sexpr_parser.cpp:618-638 at 9.0.8, 676-696 at 10.0.7): the same with
  1e4 and a limit of INT_MAX * 0.7071.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

import math
import re

from kicad_ref.dsn_lexer import is_number
from kicad_ref.eda_units import PCB_IU_PER_MM, SCH_IU_PER_MM, kiround

INT_MAX = 2**31 - 1
#: constexpr double INT_LIMIT = std::numeric_limits<int>::max() - 10 (int arithmetic first).
PCB_INT_LIMIT = float(INT_MAX - 10)
#: constexpr double int_limit = std::numeric_limits<int>::max() * 0.7071 (a double product).
SCH_INT_LIMIT = INT_MAX * 0.7071

_NONZERO_MANTISSA = re.compile(rb"^[-+]?[0-9]*\.?[0-9]*")


class ParseRefusal(ValueError):
    """A token or value KiCad's parser refuses, so the whole file fails to load."""


def _as_bytes(token: bytes | str) -> bytes:
    return token.encode("utf-8") if isinstance(token, str) else bytes(token)


def parse_double(token: bytes | str) -> float:
    """parseDouble( aExpected ): the double KiCad reads for ``token``, or ParseRefusal."""
    raw = _as_bytes(token)
    if not is_number(raw):
        raise ParseRefusal(f"need a number, got {raw!r} (NeedNUMBER)")
    if raw.startswith(b"+"):
        raise ParseRefusal(f"{raw!r}: from_chars takes no leading '+'")
    value = float(raw)
    if math.isinf(value):
        raise ParseRefusal(f"{raw!r} overflows a double (result_out_of_range)")
    if value == 0.0:
        mantissa = _NONZERO_MANTISSA.match(raw)
        if mantissa is not None and any(0x31 <= b <= 0x39 for b in mantissa.group()):
            raise ParseRefusal(f"{raw!r} underflows to zero (result_out_of_range)")
    return value


def _clamp(value: float, limit: float) -> float:
    """std::clamp( value, -limit, limit )."""
    if value < -limit:
        return -limit
    if limit < value:
        return limit
    return value


def parse_board_units(token: bytes | str, *, major: int) -> int:
    """parseBoardUnits( aExpected ): the nanometres KiCad ``major`` reads for ``token``."""
    return kiround(_clamp(parse_double(token) * PCB_IU_PER_MM, PCB_INT_LIMIT), major=major)


def parse_sch_internal_units(token: bytes | str, *, major: int) -> int:
    """parseInternalUnits( aExpected ): the 100 nm units KiCad ``major`` reads for ``token``."""
    return kiround(_clamp(parse_double(token) * SCH_IU_PER_MM, SCH_INT_LIMIT), major=major)
