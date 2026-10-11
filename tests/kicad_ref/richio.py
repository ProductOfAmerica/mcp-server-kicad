"""KiCad's string writer, ported as an oracle.

OUTPUTFORMATTER::Quotes (common/richio.cpp:509-545 at 9.0.8, 470-506 at 10.0.7; the
bodies are identical) wraps a byte string in double quotes and escapes exactly four
bytes: LF as \\n, CR as \\r, backslash as \\\\ and the double quote as \\". Every
other byte, TAB and the other control bytes included, goes out raw.

OUTPUTFORMATTER::Quotew (richio.cpp:548-556 at 9.0.8, 509-517 at 10.0.7) is how KiCad
writes every string it saves: it encodes the wxString as UTF-8 and hands the buffer to
Quotes as a C string, so the text is cut at its first NUL.

Checked against pcbnew's own STRING_FORMATTER().Quotew by
tests/test_ladder_r1_judges.py::test_quotes_matches_pcbnew.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

_ESCAPED = {0x0A: b"\\n", 0x0D: b"\\r", 0x5C: b"\\\\", 0x22: b'\\"'}


def quotes_bytes(raw: bytes) -> bytes:
    """Quotes: ``raw`` in double quotes, with LF, CR, backslash and quote escaped."""
    out = bytearray(b'"')
    for byte in raw:
        escaped = _ESCAPED.get(byte)
        if escaped is None:
            out.append(byte)
        else:
            out += escaped
    out += b'"'
    return bytes(out)


def quotes(text: str) -> bytes:
    """Quotew: ``text`` as KiCad writes it, UTF-8, cut at its first NUL, then Quotes.

    Raises UnicodeEncodeError for a lone surrogate, which a KiCad string cannot hold.
    """
    return quotes_bytes(text.encode("utf-8").split(b"\x00", 1)[0])
