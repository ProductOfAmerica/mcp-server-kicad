"""Rung 1's judges, each checked against something other than itself.

The ports in tests/kicad_ref answer to KiCad: files KiCad saved, kicad-cli's reading of
one field per escape class and of each token rule, and pcbnew's own string writer.
tests/edit_judge.py answers to constructed edits whose right answer is known, and
tests/sexpr_gen.py's dialect to the lexer port. The conftest machinery the ladder adds
(the nightly rule and the oracle's handling of xfailed calls and of placeholders)
answers to real pytest runs, and netlist_fields to the shape kicad-cli's netlist was
measured to have.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import edit_judge as J
import pytest
import sexpr_gen as G
from conftest import (
    HAS_KICAD_CLI,
    _call_xfailed,
    _kicad_cli_major,
    _nightly_skip_reason,
    netlist_fields,
    parse_comp_fields,
    requires_cli,
)
from hypothesis import find, given, settings
from hypothesis import strategies as st
from hypothesis.errors import NoSuchExample
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U
from kicad_ref.richio import quotes, quotes_bytes

from mcp_server_kicad._freerouting import find_pcbnew_python, wx_app_prelude
from mcp_server_kicad._shared import _kicad_root

REPO = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"


# ---------------------------------------------------------------------------
# what KiCad ships
# ---------------------------------------------------------------------------

_SEXPR_SUFFIXES = {
    ".kicad_sch",
    ".kicad_pcb",
    ".kicad_sym",
    ".kicad_mod",
    ".kicad_wks",
    ".kicad_dru",
}
_TABLE_NAMES = {"sym-lib-table", "fp-lib-table"}
_NO_INSTALL = (
    "no KiCad demos or templates found through _kicad_root() (ci.yml installs no KiCad;"
    " the macOS cask ships templates only)"
)


def _share_dirs() -> list[Path]:
    """KiCad's shared data: share/kicad on Windows and Linux, SharedSupport on macOS."""
    root = _kicad_root()
    if root is None:
        return []
    return [d for d in (root / "share" / "kicad", root / "SharedSupport") if d.is_dir()]


def _shipped_files(cap: int) -> list[tuple[str, Path]]:
    """(name relative to the share directory, path) of every s-expression file under
    the demos and templates, up to ``cap`` bytes each."""
    out = []
    for share in _share_dirs():
        for sub in ("demos", "template"):
            base = share / sub
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if (
                    path.is_file()
                    and (path.suffix in _SEXPR_SUFFIXES or path.name in _TABLE_NAMES)
                    and path.stat().st_size <= cap
                ):
                    out.append((path.relative_to(share).as_posix(), path))
    return out


#: Teardrop members that read a value and then a ')': KiCad's board parser takes them
#: without their '(' (pcb_io_kicad_sexpr_parser.cpp:437-501 at 9.0.8, 668-731 at
#: 10.0.7: "if( token == T_LEFT ) token = NextTok();", then parseDouble and NeedRIGHT).
#: Its yes/no members without a '(' consume nothing more (parseMaybeAbsentBool,
#: 241-269 at 9.0.8), so they need no entry here.
_TEARDROP_VALUE_MEMBERS = frozenset(
    {
        b"best_length_ratio",
        b"max_length",
        b"best_width_ratio",
        b"max_width",
        b"curve_points",
        b"filter_ratio",
    }
)


def _balanced_for_kicads_board_parser(toks: list) -> bool:
    """Paren balance as the board parser counts it: inside a teardrops list, a value
    member without its '(' still closes with a ')'."""
    heads: list[bytes | None] = []
    after_left = False
    for tok in toks:
        if tok.kind == L.LEFT:
            heads.append(None)
            after_left = True
        elif tok.kind == L.RIGHT:
            if not heads:
                return False
            heads.pop()
            after_left = False
        elif after_left:
            heads[-1] = tok.value
            after_left = False
        elif heads and heads[-1] == b"teardrops" and tok.value in _TEARDROP_VALUE_MEMBERS:
            heads.append(tok.value)
    return not heads


def test_lexer_accepts_every_shipped_file():
    """KiCad loads every file it ships, so the port must lex each one, and find its
    parens balanced as KiCad's own parsers count them. Templates and demos up to
    300 KB; package-level corpus tests take the rest."""
    files = _shipped_files(300_000)
    if not files:
        pytest.skip(_NO_INSTALL)
    problems = []
    for name, path in files:
        data = path.read_bytes()
        knows_bar = L.knows_bar_of(data)
        try:
            toks = L.tokens(data, knows_bar=knows_bar)
        except L.Unterminated as exc:
            problems.append(f"{name}: {exc}")
            continue
        try:
            L.tree(data, knows_bar=knows_bar)
        except L.Unbalanced as exc:
            kind = L.file_kind(data)
            if kind not in ("board", "footprint") or not _balanced_for_kicads_board_parser(toks):
                problems.append(f"{name}: {exc}")
    assert not problems, f"{len(problems)} of {len(files)} shipped files:\n" + "\n".join(problems)


def test_format_internal_units_matches_kicad_files():
    """Every coordinate KiCad wrote reads back through KiROUND and prints the same text.

    The x and y atoms of at, xy, start, end, mid and center in every board, footprint,
    schematic and symbol library under the demos and templates (up to 300 KB), each at
    its file's scale, for both KiROUND ports.
    """
    files = _shipped_files(300_000)
    if not files:
        pytest.skip(_NO_INSTALL)
    scale = {
        "board": U.PCB_IU_PER_MM,
        "footprint": U.PCB_IU_PER_MM,
        "schematic": U.SCH_IU_PER_MM,
        "symbol_lib": U.SCH_IU_PER_MM,
    }
    heads = {b"at", b"xy", b"start", b"end", b"mid", b"center"}
    checked = 0
    wrong = []
    for name, path in files:
        data = path.read_bytes()
        iu = scale.get(L.file_kind(data))
        if iu is None:
            continue
        try:
            pending = list(L.tree(data, knows_bar=L.knows_bar_of(data)))
        except L.LexError:
            continue  # the teardrop files; test_lexer_accepts_every_shipped_file covers them
        while pending:
            item = pending.pop()
            if item.kind != L.GROUP:
                continue
            pending.extend(item.items)
            if not (item.items and item.items[0].kind == L.SYMBOL and item.items[0].value in heads):
                continue
            for tok in item.items[1:3]:
                if tok.kind != L.NUMBER:
                    continue
                checked += 1
                for major in (9, 10):
                    text = U.format_internal_units(
                        U.kiround(float(tok.value) * iu, major=major), iu
                    )
                    if text.encode() != tok.value:
                        wrong.append(
                            f"{name} @ {tok.start}: {tok.value!r} -> {text!r} (KiCad {major})"
                        )
    assert checked, "no coordinate atoms found in the shipped files"
    assert not wrong, f"{len(wrong)} of {checked} coordinates:\n" + "\n".join(wrong[:20])


# ---------------------------------------------------------------------------
# what kicad-cli reads
# ---------------------------------------------------------------------------


def _schematic_with_fields(fields: list[tuple[str, bytes]], extra_line: bytes = b"") -> bytes:
    """kicad_native.kicad_sch with properties (name, raw value) added to its placed R1,
    and ``extra_line`` as a line of its own just before them."""
    data = FIXTURE.read_bytes()
    eol = b"\r\n" if b"\r\n" in data[:200] else b"\n"
    anchor = data.rindex(b'(property "Description"')  # R1's own, after lib_symbols
    lines = extra_line + eol + b"\t\t" if extra_line else b""
    for name, value in fields:
        lines += (
            b"(property "
            + quotes(name)
            + b" "
            + value
            + b" (at 100 100 0) (effects (font (size 1.27 1.27)) (hide yes)))"
            + eol
            + b"\t\t"
        )
    return data[:anchor] + lines + data[anchor:]


def _string_value(raw: bytes) -> bytes:
    """The port's value for one quoted atom."""
    toks = L.tokens(b"(x " + raw + b")")
    assert [t.kind for t in toks] == [L.LEFT, L.SYMBOL, L.STRING, L.RIGHT], toks
    return toks[2].value


def _cli_major() -> int:
    major = _kicad_cli_major()
    if major < 9:
        raise RuntimeError(f"cannot read the major version of kicad-cli (got {major})")
    return major


#: One field per escape class and per way FromUTF8 can treat ill-formed UTF-8:
#: (field name, quoted value as written). A surrogate or a code point past U+10FFFF
#: is not here: KiCad holds those per platform (wx_from_utf8 refuses to predict one),
#: and a lone surrogate stops kicad-cli 9.0.8 writing the netlist at all.
_ESCAPE_CLASSES = [
    ("E_QUOTE", rb'"A\"B"'),
    ("E_BSLASH", rb'"A\\B"'),
    ("E_N", rb'"A\nB"'),
    ("E_R", rb'"A\rB"'),
    ("E_T", rb'"A\tB"'),
    ("E_A", rb'"A\aB"'),
    ("E_B", rb'"A\bB"'),
    ("E_F", rb'"A\fB"'),
    ("E_V", rb'"A\vB"'),
    ("E_X2", rb'"A\x42C"'),
    ("E_X1", rb'"A\x4"'),
    ("E_X0", rb'"A\xgC"'),
    ("E_OCT3", rb'"A\102C"'),
    ("E_OCT_THEN_DIGIT", rb'"A\1017"'),  # three octal digits at most: A, A, 7
    ("E_OCT1", rb'"A\7B"'),
    ("E_UNKNOWN", rb'"A\BC"'),
    ("E_8", rb'"A\8B"'),
    ("RAW_TAB", b'"A\tB"'),
    ("RAW_UTF8", '"Ω→✓"'.encode()),
    ("INVALID_UTF8", rb'"A\xffB"'),
    ("E_X_AT_END", rb'"A\x"'),
    ("E_OCT_WRAPS", rb'"A\777B"'),
    ("E_BEFORE_MULTIBYTE", '"A\\ΩB"'.encode()),
    ("RAW_CR", b'"A\rB"'),
    ("RAW_FF", b'"A\x0cB"'),
    ("HEX_UTF8", rb'"\xf0\x9f\x98\x80"'),
    ("OVERLONG_TWO_BYTE", b'"A\xc0\xafB"'),
    ("OVERLONG_THREE_BYTE", b'"A\xe0\x80\xafB"'),
    ("OVERLONG_FOUR_BYTE", b'"A\xf0\x80\x80\xafB"'),
    ("LEAD_F5", b'"A\xf5\x80\x80\x80B"'),
    ("CUT_OFF_SEQUENCE", b'"A\xe2\x82B"'),
    ("CUT_OFF_AT_END", b'"A\xe2\x82"'),
]
#: Classes that put a NUL in the value, where KiCad 9 and 10 store different strings
#: (dsnlexer.h FromUTF8: 434-437 at 9.0.8 cuts at the NUL, 439-442 at 10.0.7 keeps it).
_NUL_CLASSES = [
    ("RAW_NUL", b'"A\x00B"'),
    ("E_OCT_NUL", rb'"A\0B"'),
    ("E_OCT_WRAPS_TO_NUL", rb'"A\400B"'),
]


def _check_fields_read_as_ported(tmp_path: Path, classes) -> None:
    major = _cli_major()
    path = tmp_path / "fields.kicad_sch"
    path.write_bytes(_schematic_with_fields(classes))
    got = netlist_fields(path)
    if got is None:
        raise RuntimeError("kicad-cli could not load the schematic the classes live in")
    wrong = []
    for name, raw in classes:
        want = L.wx_from_utf8(_string_value(raw), major=major)
        if ("R1", name) not in got:
            wrong.append(f"{name}: KiCad printed no such field")
        elif got[("R1", name)] != want:
            wrong.append(f"{name} {raw!r}: KiCad {got[('R1', name)]!r}, port {want!r}")
    assert not wrong, f"kicad-cli {major}:\n" + "\n".join(wrong)


@requires_cli
def test_lexer_reads_each_escape_class_as_kicad_does(tmp_path):
    """KiCad's value for each field is the port's token bytes through FromUTF8."""
    _check_fields_read_as_ported(tmp_path, _ESCAPE_CLASSES)


@requires_cli
def test_lexer_reads_a_nul_as_each_kicad_major_does(tmp_path):
    """The same for values holding a NUL, the one place KiCad 9 and 10 store different
    strings. Measured on kicad-cli 9.0.8; KiCad 10 is judged from its source until a
    KiCad 10 leg runs this."""
    _check_fields_read_as_ported(tmp_path, _NUL_CLASSES)


def _value_tokens(data: bytes, name: bytes) -> list[tuple[str, bytes]]:
    """(kind, value) of the tokens the port reads as property ``name``'s value."""
    toks = L.tokens(data, knows_bar=L.knows_bar_of(data))
    for i, tok in enumerate(toks):
        if tok.kind == L.STRING and tok.value == name and toks[i - 1].value == b"property":
            out = []
            for later in toks[i + 1 :]:
                if later.kind == L.LEFT:
                    return out
                out.append((later.kind, later.value))
    raise LookupError(name)


#: Hand-edited token shapes KiCad's writer never produces, with what kicad-cli 9.0.8
#: was measured to do with each (2026-10-10): load it and read the value, or refuse.
_TOKEN_RULES = {
    "comment_line": {
        "line": b'# (property "HIDDEN" "x" (',
        "fields": [("SEEN", b'"yes"')],
        "kicad": {"SEEN": "yes"},
    },
    "raw_lf_in_quotes": {"fields": [("P", b'"A\nB"')], "kicad": None},
    "bare_bar": {"fields": [("P", b"a|b")], "kicad": None},
    "bare_nul": {"fields": [("P", b"a\x00b")], "kicad": None},
    "bare_form_feed": {"fields": [("P", b"a\x0cb")], "kicad": {"P": "a\x0cb"}},
    "bare_vertical_tab": {"fields": [("P", b"a\x0bb")], "kicad": {"P": "a\x0bb"}},
    # FF and VT where a token starts: not whitespace there either, so they open the token.
    "leading_form_feed": {"fields": [("P", b"\x0cb")], "kicad": {"P": "\x0cb"}},
    "leading_vertical_tab": {"fields": [("P", b"\x0bb")], "kicad": {"P": "\x0bb"}},
    "glued_quote": {"fields": [("P", b'ab"cd"')], "kicad": {"P": 'ab"cd"'}},
}


@requires_cli
@pytest.mark.no_kicad_validation  # the hand-edited file is the subject; kicad-cli judges it here
@pytest.mark.parametrize("case", sorted(_TOKEN_RULES))
def test_lexer_token_rules_match_kicad(tmp_path, case):
    """Each token rule the port models, against kicad-cli on a file that exercises it."""
    spec = _TOKEN_RULES[case]
    data = _schematic_with_fields(spec["fields"], spec.get("line", b""))
    path = tmp_path / f"{case}.kicad_sch"
    path.write_bytes(data)
    got = netlist_fields(path)
    if spec["kicad"] is None:
        assert got is None, f"kicad-cli loaded the {case} file"
    else:
        assert got is not None, f"kicad-cli refused the {case} file"
        for name, value in spec["kicad"].items():
            assert got.get(("R1", name)) == value, (name, got.get(("R1", name)))
    # What the port reads, which must match KiCad's verdict above.
    if case == "comment_line":
        toks = L.tokens(data, knows_bar=True)
        L.tree(data, knows_bar=True)  # the '(' inside the comment is not a token
        assert all(t.value != b"HIDDEN" for t in toks)
    elif case == "raw_lf_in_quotes":
        with pytest.raises(L.Unterminated) as caught:
            L.tokens(data, knows_bar=True)
        assert caught.value.offset == data.index(b'"A\nB"')
    else:
        expected = {
            "bare_bar": [(L.SYMBOL, b"a"), (L.BAR, b"|"), (L.SYMBOL, b"b")],
            "bare_nul": [(L.SYMBOL, b"a"), (L.SYMBOL, b"b")],
            "bare_form_feed": [(L.SYMBOL, b"a\x0cb")],
            "bare_vertical_tab": [(L.SYMBOL, b"a\x0bb")],
            "leading_form_feed": [(L.SYMBOL, b"\x0cb")],
            "leading_vertical_tab": [(L.SYMBOL, b"\x0bb")],
            "glued_quote": [(L.SYMBOL, b'ab"cd"')],
        }[case]
        assert _value_tokens(data, b"P") == expected


def test_quotes_matches_pcbnew(tmp_path):
    """The Quotew port equals pcbnew's own STRING_FORMATTER().Quotew, run in KiCad's
    Python, for library-table values, every control byte, multibyte text and a NUL."""
    python, env = find_pcbnew_python()
    if python is None:
        pytest.skip("no Python that imports pcbnew (find_pcbnew_python found none)")
    samples = [
        "plain",
        "",
        'my"lib',
        "C:\\Users\\nick\\libs\\my.kicad_sym",
        "C:/Users/nick/libs/my.kicad_sym",
        "back\\slash",
        "tab\there",
        "line\nbreak",
        "cr\rhere",
        "sp ace",
        "(paren)",
        "#hash",
        "a|b",
        "\u03a9",
        "\u65e5\u672c\u8a9e",
        "\U0001f600",
        "A\x00B",
    ] + [f"c{chr(c)}c" for c in [*range(1, 0x20), 0x7F]]
    source = tmp_path / "samples.json"
    result = tmp_path / "quoted.json"
    source.write_text(json.dumps(samples), encoding="utf-8")
    code = wx_app_prelude() + (
        "import json, pcbnew; fmt = pcbnew.STRING_FORMATTER(); "
        "samples = json.load(open(sys.argv[1], encoding='utf-8')); "
        "json.dump([list(fmt.Quotew(s).encode('utf-8', 'surrogateescape')) for s in samples],"
        " open(sys.argv[2], 'w'))"
    )
    run = subprocess.run(
        [python, "-c", code, str(source), str(result)],
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
        timeout=120,
    )
    if run.returncode != 0 or not result.exists():
        raise RuntimeError(f"pcbnew's Python failed (rc {run.returncode}): {run.stderr[-500:]}")
    got = [bytes(q) for q in json.loads(result.read_text())]
    assert got == [quotes(s) for s in samples]


# ---------------------------------------------------------------------------
# the ports, against KiCad's source and each other
# ---------------------------------------------------------------------------


@given(st.text(st.characters(exclude_categories=("Cs",))))
def test_quotes_reads_back_through_the_lexer(text):
    """Quotes and the lexer invert each other: the lexer reads back exactly the bytes
    Quotew wrote (up to the NUL Quotew cuts at), and FromUTF8 gives back the text."""
    raw = quotes(text)
    toks = L.tokens(b"(x " + raw + b")")
    assert [t.kind for t in toks] == [L.LEFT, L.SYMBOL, L.STRING, L.RIGHT]
    assert (toks[2].start, toks[2].end) == (3, 3 + len(raw))
    kept = text.split("\x00", 1)[0]
    assert toks[2].value == kept.encode("utf-8")
    for major in (9, 10):
        assert L.wx_from_utf8(toks[2].value, major=major) == kept


def test_quotes_bytes_escapes_exactly_four_bytes():
    """richio.cpp:509-545 at 9.0.8: LF, CR, backslash and quote escaped, nothing else."""
    every = bytes(range(256))
    out = quotes_bytes(every)
    assert (
        out
        == b'"'
        + every.replace(b"\\", b"\\\\")
        .replace(b'"', b'\\"')
        .replace(b"\n", b"\\n")
        .replace(b"\r", b"\\r")
        + b'"'
    )


@pytest.mark.parametrize("major", [9, 10])
def test_kiround_rounds_half_away_from_zero(major):
    """KiROUND as libs/kimath/include/math/util.h computes it at each major."""
    for value, want in [
        (0.5, 1),
        (-0.5, -1),
        (1.5, 2),
        (-1.5, -2),
        (2.5, 3),
        (-2.5, -3),
        (2.4999, 2),
        (-2.4999, -2),
        (0.0, 0),
        (-0.0, 0),
        (1e-9, 0),
    ]:
        assert U.kiround(value, major=major) == want, value
    # The clamp: 9.0.8 gives INT_MAX - 1 and INT_MIN + 1, 10.0.7 the limits themselves.
    high, low = {9: (2**31 - 2, -(2**31) + 1), 10: (2**31 - 1, -(2**31))}[major]
    assert U.kiround(3e9, major=major) == high
    assert U.kiround(-3e9, major=major) == low
    with pytest.raises(ValueError):
        U.kiround(float("nan"), major=major)


def test_kiround_majors_differ_only_where_the_code_does():
    """9.0.8 adds 0.5 in double arithmetic: 0.49999999999999994 + 0.5 rounds to 1.0, so
    it returns 1, where 10.0.7's llround returns 0. And 9.0.8's clamp tests the sum, so
    2147483646.5 sums to exactly INT_MAX and passes while 2147483646.6 is clamped."""
    assert U.kiround(0.49999999999999994, major=9) == 1
    assert U.kiround(0.49999999999999994, major=10) == 0
    assert U.kiround(2147483646.5, major=9) == 2**31 - 1
    assert U.kiround(2147483646.6, major=9) == 2**31 - 2
    assert U.kiround(2147483646.6, major=10) == 2**31 - 1
    # The low side mirrors it: -2147483647.5 sums to exactly INT_MIN and passes.
    assert U.kiround(-2147483647.5, major=9) == -(2**31)
    assert U.kiround(-2147483647.6, major=9) == -(2**31) + 1
    # 10.0.7 clamps anything inside long long, however far past int.
    assert U.kiround(2.0**62, major=10) == 2**31 - 1


def test_format_internal_units_follows_eda_units():
    """eda_units.cpp:170-200 at 9.0.8: {:.10f} stripped at or below 0.0001 mm, else {:.10g}."""
    pcb, sch = U.PCB_IU_PER_MM, U.SCH_IU_PER_MM
    assert U.format_internal_units(0, pcb) == "0"
    assert U.format_internal_units(-45, pcb) == "-0.000045"
    assert U.format_internal_units(100, pcb) == "0.0001"
    assert U.format_internal_units(101, pcb) == "0.000101"
    assert U.format_internal_units(1_000_000, pcb) == "1"
    assert U.format_internal_units(1_320_313, pcb) == "1.320313"
    assert U.format_internal_units(-(2**31), pcb) == "-2147.483648"
    assert U.format_internal_units(1_016_000, sch) == "101.6"
    assert U.format_internal_units(1, sch) == "0.0001"
    assert U.format_internal_units(70, pcb) == "0.00007"  # fixed below 0.0001, not 7e-05
    assert U.format_internal_units(2**31 - 1, pcb) == "2147.483647"
    with pytest.raises(ValueError):
        U.format_internal_units(2**31, pcb)
    with pytest.raises(ValueError):
        U.format_internal_units(-(2**31) - 1, pcb)


@pytest.mark.parametrize(
    "token,want",
    [
        (b"1", True),
        (b"-1", True),
        (b"+1.5", True),
        (b".5", True),
        (b"1.", True),
        (b"1e5", True),
        (b"1.e5", True),
        (b"-.5E-3", True),
        (b"1e", False),
        (b"1e+", False),
        (b"e5", False),
        (b".", False),
        (b"-", False),
        (b"", False),
        (b"--1", False),
        (b"1.2.3", False),
        (b"0x10", False),
        (b"1x", False),
        # isDigit is '0' to '9' and nothing beside them (dsnlexer.cpp:473-476 at 9.0.8,
        # 469-472 at 10.0.7):
        # '/' and ':' sit on either side of the digits in ASCII, '-' and '/' on either
        # side of the point.
        (b"0", True),
        (b"9", True),
        (b"/", False),
        (b":", False),
        (b"1/", False),
        (b"1:", False),
        (b"/1", False),
        (b":1", False),
        (b"1-5", False),
        (b"1/5", False),
        (b"1e/", False),
        (b"1e:", False),
        (b"1./", False),
        (b"1.:", False),
        (b"./", False),
        (b".:", False),
    ],
)
def test_is_number_follows_isnumber(token, want):
    """dsnlexer.cpp:495-542 at 9.0.8, traced by hand for each token."""
    assert L.is_number(token) is want


def test_wx_from_utf8_models_each_major():
    """KiCad 9 cuts at the first NUL (dsnlexer.h:434-437 at 9.0.8); KiCad 10 keeps it
    (439-442 at 10.0.7). wx's decoder checks lead bytes and continuation bytes only
    (strconv.cpp:922-1069 in wx 3.2.8), so an overlong of three or four bytes decodes."""
    assert L.wx_from_utf8(b"A\x00B", major=9) == "A"
    assert L.wx_from_utf8(b"A\x00B", major=10) == "A\x00B"
    for major in (9, 10):
        assert L.wx_from_utf8("Ω".encode(), major=major) == "Ω"
        assert L.wx_from_utf8(b"A\xe0\x80\xafB", major=major) == "A/B"
        for rejected in (b"\xff", b"A\xc0\xafB", b"A\xf5\x80\x80\x80B", b"A\xe2\x82B", b"\x80"):
            assert L.wx_from_utf8(rejected, major=major) == ""
        # A sequence the value ends inside: fewer bytes left than its lead byte promises
        # (strconv.cpp:1010 in wx 3.2.8).
        assert L.wx_from_utf8(b"A\xe2\x82", major=major) == ""
        for per_platform in (b"A\xed\xa0\x80B", b"A\xf4\x90\x80\x80B"):
            with pytest.raises(ValueError):
                L.wx_from_utf8(per_platform, major=major)
        # The edges of what is refused: surrogates are D800 to DFFF, the last code point
        # is 10FFFF, and their neighbours decode like any other character.
        with pytest.raises(ValueError):
            L.wx_from_utf8(b"A\xed\xbf\xbfB", major=major)  # U+DFFF
        for edge in ("퟿", "", "\U0010ffff"):
            assert L.wx_from_utf8(edge.encode(), major=major) == edge


def test_knows_bar_follows_each_parser():
    """The thresholds SetKnowsBar is called with (see dsn_lexer's docstring)."""
    assert not L.knows_bar_for("schematic", 20240619)
    assert L.knows_bar_for("schematic", 20240620)
    assert not L.knows_bar_for("symbol_lib", 20240528)
    assert L.knows_bar_for("symbol_lib", 20240529)
    for kind in ("board", "footprint"):
        assert not L.knows_bar_for(kind, 20240705)
        assert L.knows_bar_for(kind, 20240706)
    assert not L.knows_bar_for("board", None)  # read as 20201115
    assert L.knows_bar_for("footprint", None)  # never set: the lexer default
    assert L.knows_bar_for("schematic", None)  # read as the current format
    assert L.knows_bar_for("other", 1)


def test_file_version_reads_like_the_parsers():
    """parseHeader takes the version only as the first item; a footprint's parser takes
    any top-level (version N) and keeps the largest."""
    assert L.file_version(b"(kicad_sch (version 20250114) (generator x))") == 20250114
    assert L.file_kind(b"(kicad_sch (version 20250114))") == "schematic"
    assert L.file_version(b"(kicad_pcb (generator x) (version 20241229))") is None
    assert L.file_version(b'(footprint "R" (version 20241229) (generator x))') == 20241229
    assert L.file_version(b'(footprint "R" (layer F.Cu) (version 20240101) (x (version 9)))') == (
        20240101
    )
    assert L.file_version(b'(footprint "R" (version 20240101) (version 20241229))') == 20241229
    # Only a direct child counts, even one that follows lists nested two deep.
    nested = b'(footprint "R" (x (y) (version 20260206)) (version 20241229))'
    assert L.file_version(nested) == 20241229
    assert L.file_version(b"(module R (layer F.Cu))") is None
    assert L.file_kind(b"(module R (layer F.Cu))") == "footprint"
    assert L.file_kind(b"(sym_lib_table (version 7))") == "other"
    # parseInt is strtol( CurText(), nullptr, 10 ) (pcb_io_kicad_sexpr_parser.h:354-357 at
    # 9.0.8), which takes a sign; isNumber lets one through.
    assert L.file_version(b"(kicad_sch (version +20250114))") == 20250114
    assert L.file_version(b"(kicad_pcb (version -1))") == -1
    # A NUMBER token with no leading digits: strtol converts nothing and returns 0.
    assert L.file_version(b"(kicad_sch (version .5))") == 0
    assert L.file_version(b'(footprint "R" (version -.5))') == 0
    # (version (y) N) is no version: NeedNUMBER meets '('; a list nested in a footprint's
    # member is marked, wherever it sits in that member.
    assert L.file_version(b'(footprint "R" (version (y) 20241229))') is None
    # Until a version is read the lexer keeps its default, the bar known (dsnlexer.cpp:49
    # at 9.0.8), so x|"(version 30000000)" is x, a bar and a string holding no list.
    barred = b'(footprint "R" (x x|"(version 30000000)") (version 20241229))'
    assert L.file_version(barred) == 20241229


def test_tree_reports_where_parens_fail():
    with pytest.raises(L.Unbalanced) as caught:
        L.tree(b"(a))")
    assert (caught.value.offset, caught.value.unclosed) == (3, 0)
    with pytest.raises(L.Unbalanced) as caught:
        L.tree(b"((a) (b")
    assert (caught.value.offset, caught.value.unclosed) == (7, 2)
    with pytest.raises(L.Unterminated) as caught:
        L.tokens(b'(a "bc\n")')
    assert caught.value.offset == 3
    # A backslash as the input's last byte escapes nothing: "if( ++head >= limit ) break;
    # // throw exception at L_unterminated" (dsnlexer.cpp:658-659 at 9.0.8).
    with pytest.raises(L.Unterminated) as caught:
        L.tokens(b'(a "bc\\')
    assert caught.value.offset == 3


def test_lexer_reads_line_by_line_from_the_first_byte():
    """FILE_LINE_READER hands the lexer one line at a time, each ending after its LF
    (richio.cpp:249-280 at 9.0.8), the first line included even when it is only that LF:
    a '#' line after it is a comment, and a string cannot run on past it."""
    toks = L.tokens(b"\n#c (x\n(a)")
    assert [(t.kind, t.value) for t in toks] == [(L.LEFT, b"("), (L.SYMBOL, b"a"), (L.RIGHT, b")")]
    with pytest.raises(L.Unterminated):
        L.tokens(b'\n(a "b\nc")')


def test_lexer_defaults_and_headers_read_as_kicads_parsers_start():
    """DSNLEXER::init turns the bar on (dsnlexer.cpp:49 at 9.0.8 and 10.0.7), so the port's
    entry points know it unless told otherwise. A parser first needs '(' and then its file
    keyword as a bare token (a quoted "kicad_sch" is a string, not the keyword), and it reads
    the header with the bar known. A footprint's version comes from its own list only: the
    parser is done at that list's closing paren."""
    barred = b"(a x|y)"
    want = [L.LEFT, L.SYMBOL, L.SYMBOL, L.BAR, L.SYMBOL, L.RIGHT]
    assert [t.kind for t in L.tokens(barred)] == want
    assert [t.kind for t in L.iter_tokens(barred)] == want
    assert [i.kind for i in L.tree(barred)[0].items] == want[1:-1]
    assert L.file_kind(b"(kicad_pcb (version 20241229))") == "board"
    assert L.file_kind(b"(kicad_symbol_lib (version 20241209))") == "symbol_lib"
    # Kind and version together pick the bar: a board before 20240706 has it off
    # (pcb_io_kicad_sexpr_parser.cpp:1423 at 9.0.8), a symbol library from 20240529 on.
    assert not L.knows_bar_of(b"(kicad_pcb (version 20240101) (a x|y))")
    assert L.knows_bar_of(b"(kicad_symbol_lib (version 20240529))")
    assert L.file_kind(b'("kicad_sch" (version 20250114))') == "other"
    assert L.file_kind(b"kicad_sch (version 20250114)") == "other"
    assert L.file_kind(b"(kicad_sch|x)") == "schematic"
    assert L.file_version(b"(kicad_sch| (version 20250114))") is None
    assert L.file_version(b'(footprint "R" (layer F.Cu)) (x (version 20260206))') is None


def test_the_ports_refuse_what_they_do_not_model():
    """Each port raises rather than guess outside what it was read from: text instead of
    bytes for the lexer, a major with no FromUTF8 or KiROUND model, and a value KiCad 10's
    std::llround leaves unspecified. A file that does not lex has no kind and no version."""
    with pytest.raises(TypeError, match="bytes"):  # the refusal, not some other TypeError
        L.tokens("(a b)")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        L.wx_from_utf8(b"x", major=8)
    for value in (float("inf"), -float("inf"), 2.0**63, -(2.0**63)):
        with pytest.raises(ValueError):
            U.kiround(value, major=10)
    with pytest.raises(ValueError):
        U.kiround(1.0, major=8)
    assert U.kiround(2.0**62, major=9) == 2**31 - 2  # 9.0.8 clamps instead
    assert L.file_kind(b"") == "other"
    assert L.file_kind(b'"(kicad_sch') == "other"
    assert L.file_kind(b"(kicad_sch") == "schematic"
    assert L.file_version(b'(kicad_sch (version "20250114') is None
    assert L.file_version(b'(footprint "R" (version 20241229) "') is None


# ---------------------------------------------------------------------------
# edit_judge
# ---------------------------------------------------------------------------


def _span(before: bytes, after: bytes) -> int:
    """The size of the region a common-prefix-and-suffix byte diff calls changed."""
    p = 0
    while p < min(len(before), len(after)) and before[p] == after[p]:
        p += 1
    s = 0
    while s < min(len(before), len(after)) - p and before[-1 - s] == after[-1 - s]:
        s += 1
    return max(len(before) - p - s, len(after) - p - s)


def test_edit_judge_catches_what_the_byte_helpers_missed():
    """Each edit below fooled the old byte-diff helpers (measured in the rungs-1-2
    inventory); its right answer is known by construction."""
    x = b"(kicad_sch (a 12))"
    # A no-op is not an insertion, a removal or a confined change.
    for judge in (J.assert_inserted, J.assert_removed):
        with pytest.raises(AssertionError, match="no-op"):
            judge(x, x)
    with pytest.raises(AssertionError, match="no-op"):
        J.assert_confined(x, x, ((0, "kicad_sch"),))
    # Splitting the atom 12 into 1(b)2 is one span of bytes but not an insertion.
    with pytest.raises(AssertionError):
        J.assert_inserted(x, b"(kicad_sch (a 1(b)2))")
    # Bytes inserted inside a string change the string.
    with pytest.raises(AssertionError):
        J.assert_inserted(b'(kicad_sch (label "AB"))', b'(kicad_sch (label "A) (x 1) (y "B"))')
    # A removal is not an insertion, and an insertion is not a removal.
    two, one = b"(r\n\t(a)\n\t(b)\n)", b"(r\n\t(a)\n)"
    with pytest.raises(AssertionError):
        J.assert_inserted(two, one)
    with pytest.raises(AssertionError):
        J.assert_removed(one, two)
    assert [e.label for e in J.assert_removed(two, one, heads=["b"])] == ["b"]
    # Next to identical neighbours, wherever the bytes went, it is one (b) inserted.
    base = b"(r\n\t(b)\n\t(b)\n)"
    for cut in (2, base.index(b"\n\t(b)\n)"), len(base) - 2):
        grown = base[:cut] + b"\n\t(b)" + base[cut:]
        edits = J.assert_inserted(base, grown, heads=["b"])
        assert [J.item_at(grown, e.location).raw for e in edits] == [b"(b)"]
    with pytest.raises(AssertionError, match="labelled"):
        J.assert_inserted(base, base[:2] + b"\n\t(b)" + base[2:], heads=["c"])
    # A 7-byte value change, and the same change plus a stray one 9 bytes away: a
    # 40-byte budget passes both; atom_changes tells them apart.
    sym = b'(symbol\n\t(property "Value" "10K" (at 102.032 101.2699 0))\n)'
    right = sym.replace(b'"10K"', b'"22K"')
    stray = right.replace(b"102.032", b"102.033")
    assert _span(sym, right) <= 40 and _span(sym, stray) <= 40
    value = J.AtomChange(((0, "symbol"), (1, "property")), 2, b'"10K"', b'"22K"')
    assert J.atom_changes(sym, right) == [value]
    assert J.atom_changes(sym, stray) == [
        value,
        J.AtomChange(((0, "symbol"), (1, "property"), (3, "at")), 1, b"102.032", b"102.033"),
    ]
    assert J.assert_confined(sym, stray, ((0, "symbol"), (1, "property")))
    with pytest.raises(AssertionError, match="outside"):
        J.assert_confined(sym, stray, ((0, "symbol"), (1, "property"), (3, "at")))
    # A separator change is a change too.
    with pytest.raises(AssertionError, match="separator|outside the changed atoms"):
        J.atom_changes(sym, right.replace(b"\n)", b" )"))
    # A list whose head changed is a list replaced, not an atom change.
    with pytest.raises(AssertionError, match="not only atom changes"):
        J.atom_changes(b"(r (x 1))", b"(r (y 1))")
    # A before that does not lex leaves nothing to judge.
    with pytest.raises(J.JudgeError):
        J.assert_inserted(b"(r", b"(r (a)")


def test_edit_judge_checks_each_claim_on_its_own():
    """Every check behind the assert_* judges catches a case no other check catches."""
    before = b"(r (a) (b))"
    # Items inserted in two places are not one span, unless the caller allows it.
    two_places = b"(r (x) (a) (b) (y))"
    with pytest.raises(AssertionError, match="contiguous"):
        J.assert_inserted(before, two_places, heads=["x", "y"])
    edits = J.assert_inserted(before, two_places, heads=["x", "y"], contiguous=False)
    assert [e.label for e in edits] == ["x", "y"]
    # Even then, a separator changed elsewhere is a change.
    with pytest.raises(AssertionError, match="separator bytes changed"):
        J.assert_inserted(before, b"(r (a)  (b) (c))", heads=["c"], contiguous=False)
    # Between identical siblings only the bytes say which one can be new. "() ()()" is
    # "() " put in front or " ()" put after the first; the third "()", which the
    # structure alone would pick, leaves a space where "()()" had none.
    (new,) = J.assert_inserted(b"()()", b"() ()()", heads=[None])
    assert (new.index, new.new_at) in {(0, 0), (1, 3)}
    # A change outside the item is reported as such, and so is a separator outside it
    # changed along with an edit inside it.
    sym = b'(symbol\n\t(property "Value" "10K" (at 102.032 101.2699 0))\n)'
    value_path = ((0, "symbol"), (1, "property"))
    inside = sym.replace(b'"10K"', b'"22K"')
    with pytest.raises(AssertionError, match="changes outside"):
        J.assert_confined(sym, inside, value_path + ((3, "at"),))
    with pytest.raises(AssertionError, match="bytes outside"):
        J.assert_confined(sym, inside.replace(b"(symbol\n", b"(symbol \n"), value_path)


def test_edit_judge_places_and_refuses_by_construction():
    """The rest of the judges' contract, each case's answer known by construction."""
    # Bytes KiCad's lexer cannot read are no insertion: a stray ')' or a string left open.
    for broken in (b"(r (a)) )", b'(r (a) "b)'):
        with pytest.raises(AssertionError, match="does not lex"):
            J.assert_inserted(b"(r (a))", broken)
    # Located as dsn_lexer.Unbalanced documents: the stray ')' with none open, or the
    # end of the input with the '(' still open counted.
    with pytest.raises(L.Unbalanced) as caught:
        J.diff(b"(r (a))", b"(r (a)))")
    assert (caught.value.offset, caught.value.unclosed) == (7, 0)
    with pytest.raises(L.Unbalanced) as caught:
        J.diff(b"(r (a))", b"(r ((a)")
    assert (caught.value.offset, caught.value.unclosed) == (7, 2)
    # A head belongs to a list and a decoded value to a token, never the other way round
    # (callers pick children by head and then index their items), and a list's head is
    # its first atom's label, bytes that are not UTF-8 included (the module docstring).
    for _, item in J.walk(b'(r (a "b") x (c) ("\xff" 1) ((d) 2))'):
        if item.items is None:
            assert item.head is None and item.value is not None
        else:
            first = item.items[0] if item.items else None
            head = first.label if first is not None and first.items is None else None
            assert item.value is None and item.head == head
    # Every item, heads included, in document order with lists before their contents.
    assert [loc for loc, _ in J.walk(b"(r (a 1) x)")] == [
        ((0, "r"),),
        ((0, "r"), (0, "r")),
        ((0, "r"), (1, "a")),
        ((0, "r"), (1, "a"), (0, "a")),
        ((0, "r"), (1, "a"), (1, "1")),
        ((0, "r"), (2, "x")),
    ]
    # An atom changed at the very first byte of the input.
    assert J.atom_changes(b"a (b)", b"z (b)") == [J.AtomChange((), 0, b"a", b"z")]
    # Whitespace alone changed: nothing was inserted.
    with pytest.raises(AssertionError, match="nothing was inserted"):
        J.assert_inserted(b"(r (a))", b"(r  (a))")
    # An item inserted where it eats the separator beside it leaves no separator in place.
    with pytest.raises(AssertionError, match="separator"):
        J.assert_inserted(b"(r (a) (b))", b"(r (a)(c)(b))", heads=["c"])
    # Two items around the old separator are two spans, though in one gap.
    two_chunks = b"(r (a)\n(x) (y)\n(b))"
    with pytest.raises(AssertionError, match="2 chunks"):
        J.assert_inserted(b"(r (a) (b))", two_chunks, heads=["x", "y"])
    assert J.assert_inserted(b"(r (a) (b))", two_chunks, heads=["x", "y"], contiguous=False)
    # Two identical items inserted side by side: any choice of the two among the run
    # must still place exactly two whole items.
    edits = J.assert_inserted(b"(r z)", b"(r (b) (b) z)", heads=["b", "b"])
    assert [(e.path, e.index, e.new) for e in edits] == [
        (((0, "r"),), 1, b"(b)"),
        (((0, "r"),), 2, b"(b)"),
    ]
    # Before the first item of a document that starts with whitespace.
    (first,) = J.assert_inserted(b"  (a)", b"(n)  (a)", heads=["n"])
    assert (first.path, first.index, first.new_at) == ((), 0, 0)
    # Put right before the next item with no separator of its own: before's separator
    # stays in front of it, and the item is one span at offset 7.
    (glued,) = J.assert_inserted(b"(r (a) (b))", b"(r (a) (x)(b))", heads=["x"])
    assert (glued.index, glued.new_at) == (2, 7)
    # A new first item beside an identical one: before's "\n" stays in front of the old
    # (b) only if the new (b) is the first, so only that placement explains the bytes.
    (lead,) = J.assert_inserted(b"\n(b)", b" (b)\n(b)", heads=["b"])
    assert (lead.path, lead.index, lead.new_at) == ((), 0, 1)
    # The alignment reads the first of the two (b)s as new, but only the last leaves
    # before's "\n" between (b) and ')': the run is searched to its end.
    edits = J.assert_inserted(
        b"(r p (b)\n)", b"(r n p (b)\n(b)\t)", heads=["n", "b"], contiguous=False
    )
    assert [(e.index, e.new_at) for e in edits] == [(1, 3), (4, 11)]
    # No placement keeps before's " " after p: a refusal, whatever the run's length.
    with pytest.raises(AssertionError, match="separator"):
        J.assert_inserted(b"(r p (b))", b"(r n p\t(b)\t(b))", heads=["n", "b"], contiguous=False)
    # The item a confinement names, deleted, is a change outside it.
    with pytest.raises(AssertionError, match="no longer names an item"):
        J.assert_confined(b"(r (a 1) (b 2))", b"(r (b 2))", ((0, "r"), (1, "a")))
    with pytest.raises(LookupError):
        J.item_at(b"(r (a))", ())
    with pytest.raises(LookupError):
        J.item_at(b"(r (a))", ((0, "r"), (1, "b")))
    with pytest.raises(LookupError):
        J.item_at(b"(r (a))", ((0, "r"), (2, "a")))
    with pytest.raises(LookupError):  # through an atom, which holds no items
        J.item_at(b"(r (a))", ((0, "r"), (0, "r"), (0, "x")))
    with pytest.raises(LookupError):  # an index is a position, never counted from the end
        J.item_at(b"(r (a))", ((-1, "r"),))
    # With no heads asked for, any whole items pass; removals default to one span.
    (added,) = J.assert_inserted(b"(r (a))", b"(r (a) (b))")
    assert (added.op, added.old, added.old_at, added.new) == ("insert", None, None, b"(b)")
    (gone,) = J.assert_removed(b"(r (a) (b))", b"(r (a))")
    assert (gone.op, gone.new, gone.new_at, gone.old) == ("delete", None, None, b"(b)")
    with pytest.raises(AssertionError, match="contiguous"):
        J.assert_removed(b"(r (x) (a) (y))", b"(r (a))")
    # Among identical siblings the first placement tried can fail on the bytes while a
    # later one explains them: "\n" stays between the old pair only if the new (b) is
    # the second (" (b)" put after it at 7, "(b) " before the old second) or the third.
    (placed,) = J.assert_inserted(b"(r (b)\n(b))", b"(r (b)\n(b) (b))", heads=["b"])
    assert (placed.index, placed.new_at) in {(2, 7), (3, 11)}


_BUG_86 = (
    "bug 86: assert_inserted and assert_removed try as the new item only identical"
    " siblings next to each other, so an insertion whose items have identical siblings"
    " further along (a pair put before an identical pair) is refused though the bytes fit;"
    " fixed by step 11"
)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_86)
def test_judge_finds_new_items_put_before_identical_ones():
    """after is before with " (x) (y)" put straight after "(r" (by construction): the new
    pair is the first one, the only pair whose removal leaves before's "\n" and "\t". The
    alignment matches the leading pair and reads the trailing one as new, and the pair
    alternates, so no run of identical siblings holds the right choice."""
    before = b"(r\n(x)\t(y))"
    after = before[:2] + b" (x) (y)" + before[2:]
    edits = J.assert_inserted(before, after, heads=["x", "y"])
    assert [(e.index, e.new_at) for e in edits] == [(1, 3), (2, 7)]
    removed = J.assert_removed(after, before, heads=["x", "y"])
    assert [(e.index, e.old_at) for e in removed] == [(1, 3), (2, 7)]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_86)
def test_judge_finds_the_new_item_across_other_insertions():
    """after is before with two chunks of whole items put in, every byte of before where it
    was (by construction): so it is a pure insertion of (k a), (k b) and the later (m),
    the only (m) that leaves before's "\n" ahead of the old one."""
    before = b"(r (m)\na\n)"
    after = before[:2] + b"\n(k a)" + before[2:7] + b"(k b)\t(m) " + before[7:]
    assert after == b"(r\n(k a) (m)\n(k b)\t(m) a\n)"
    edits = J.assert_inserted(before, after, heads=["k", "k", "m"], contiguous=False)
    assert [(e.index, e.new_at) for e in edits] == [(1, 3), (3, 13), (4, 19)]


_LONG = bytes(i % 251 for i in range(12_800))  # holds no 0xFF


def test_edit_judge_reads_each_file_with_its_own_bar_rule():
    """Every judge reads the bar as the file's own parser does unless told otherwise
    (the module docstring): a schematic older than 20240620 does not know it
    (sch_io_kicad_sexpr_parser.cpp:2642 at 9.0.8), so x|y there is one atom."""
    old = b"(kicad_sch (version 20231120) (a x|y))"
    a = ((0, "kicad_sch"), (2, "a"))
    bar = old.index(b"|")
    assert (
        J.boundaries(old) == J.boundaries(old, knows_bar=False) != J.boundaries(old, knows_bar=True)
    )
    assert not J.on_boundary(old, bar)
    assert [item.label for _, item in J.walk(old)][-2:] == ["a", "x|y"]
    assert J.item_at(old, a + ((1, "x|y"),)).raw == b"x|y"
    changed = old.replace(b"x|y", b"x|z")
    assert J.atom_changes(old, changed) == [J.AtomChange(a, 1, b"x|y", b"x|z")]
    assert [(e.op, e.index) for e in J.diff(old, changed)] == [("atom", 1)]
    longer = old.replace(b"x|y)", b"x|y q)")
    assert [e.index for e in J.assert_inserted(old, longer)] == [2]
    assert [e.index for e in J.assert_removed(longer, old)] == [2]
    assert [(e.index, e.label) for e in J.assert_confined(old, changed, a)] == [(1, "x|y")]
    assert J.find_paths(old, lambda loc, item: item.label == "x|y") == [a + ((1, "x|y"),)]
    # Touching bare tokens merge in lexer_view (D1), so the rule shows only where a bar
    # meets a quote: one bare token to this file's parser; "x", "|" and a string to a
    # parser that knows the bar.
    quoted = old.replace(b"x|y", b'x|"y"')
    view = J.lexer_view(quoted)
    assert view == J.lexer_view(quoted, knows_bar=False) != J.lexer_view(quoted, knows_bar=True)
    assert view[-3] == ("A", False, b'x|"y"', b'x|"y"')
    # The same file at 20250114 knows the bar, so there only y changed.
    new = old.replace(b"20231120", b"20250114")
    renamed = new.replace(b"x|y", b"x|z")
    assert J.atom_changes(new, renamed) == [J.AtomChange(a, 3, b"y", b"z")]
    assert [(e.index, e.label) for e in J.assert_confined(new, renamed, a)] == [(3, "y")]


@pytest.mark.parametrize(
    "k",
    [0, 1, 4095, 4096, 4097, 8191, 8192, 8193]
    + [len(_LONG) - n for n in (8193, 8192, 4097, 4096, 4095, 1, 0)],
)
def test_single_span_on_long_inputs(k):
    """single_span compares 4096 bytes at a time before going byte by byte; wherever the
    insertion falls against those blocks, from either end, it reports the range a plain
    byte-by-byte comparison gives (math). The inserted bytes occur nowhere in the input,
    so that range is exactly (k, k)."""
    after = _LONG[:k] + b"\xff\xff\xff" + _LONG[k:]

    def common(a: bytes, b: bytes) -> int:
        n = 0
        while n < min(len(a), len(b)) and a[n] == b[n]:
            n += 1
        return n

    lo, hi = len(_LONG) - common(_LONG[::-1], after[::-1]), common(_LONG, after)
    assert (lo, hi) == (k, k)
    assert J.single_span(_LONG, after) == (k, k)


@given(G.kicad_doc(), st.data())
def test_edit_judge_on_generated_splices(doc, data):
    """Edits built from KiCad-dialect documents, each with a known right answer: a
    whole item spliced after another is an insertion with that item's label; bytes
    spliced inside an atom are not; a non-head child removed with its separator is a
    removal; one atom swapped for another is exactly that atom change."""
    items = list(J.walk(doc, knows_bar=True))
    location, target = data.draw(st.sampled_from(items))
    parent, (index, label) = location[:-1], location[-1]

    sep = data.draw(G.whitespace)
    new = data.draw(G.kicad_item())
    grown = doc[: target.end] + sep + new + doc[target.end :]
    new_label = next(J.walk(new, knows_bar=True))[1].label
    (edit,) = J.assert_inserted(doc, grown, heads=[new_label])
    assert edit.path == parent and J.item_at(grown, edit.location).raw == new

    atoms = [item for _, item in items if item.items is None and item.end - item.start >= 2]
    if atoms:
        cut_atom = data.draw(st.sampled_from(atoms))
        k = data.draw(st.integers(cut_atom.start + 1, cut_atom.end - 1))
        with pytest.raises(AssertionError):
            J.assert_inserted(doc, doc[:k] + b"(zz)" + doc[k:])

    if index == 0 and target.items is None:
        return  # a head: removing or retexting it makes a different list
    previous_end = max([t.end for t in L.tokens(doc) if t.end <= target.start], default=0)
    shrunk = doc[:previous_end] + doc[target.end :]
    (removal,) = J.assert_removed(doc, shrunk, heads=[label])
    assert removal.path == parent and J.item_at(doc, removal.location).raw == target.raw
    if target.items is None:
        other = data.draw(G.atom().filter(lambda raw: raw != target.raw))
        changed = doc[: target.start] + other + doc[target.end :]
        assert J.atom_changes(doc, changed) == [J.AtomChange(parent, index, target.raw, other)]


def test_single_span_and_boundaries():
    """single_span gives every cut point a pure insertion allows, from the prefix and
    suffix matched independently; on_boundary uses KiCad's token offsets."""
    assert J.single_span(b"aXa", b"aXXa") == (1, 2)
    assert J.single_span(b"(a b)", b"(a c b)") == (2, 3)
    assert J.single_span(b"(a)", b"(a)") is None
    assert J.single_span(b"(ab)", b"(b)") is None
    assert J.single_span(b"(ab)", b"(xy)z") is None
    data = b'(a "b c")'
    assert J.on_boundary(data, 3, knows_bar=True)
    assert not J.on_boundary(data, 5, knows_bar=True)  # inside the string
    assert J.boundaries(data, knows_bar=True) == {0, 1, 2, 3, 8, 9}
    # 0 counts even where no token starts (the docstring), as does the end.
    assert J.boundaries(b"  (a) ", knows_bar=True) == {0, 2, 3, 4, 5, 6}


def _edits(edits) -> list[tuple]:
    """diff's edits as (op, path, index, label, old bytes, new bytes)."""
    return [(e.op, e.path, e.index, e.label, e.old, e.new) for e in edits]


def test_diff_reports_each_edit_where_its_content_is():
    """diff names every structural edit with its list's path and its index, by
    construction: an item inserted, an item deleted, an atom changed inside a nested
    list. Bars follow the file: a schematic at 20250114 knows the bar
    (sch_io_kicad_sexpr_parser.cpp:2642 at 9.0.8), so x|y is three tokens and only y
    changed; read without the bar it is one atom that changed."""
    before = b'(kicad_sch (version 20250114) (a 1) (b (c "x")) (d))'
    after = b'(kicad_sch (version 20250114) (n) (a 1) (b (c "y")))'
    root = ((0, "kicad_sch"),)
    edits = J.diff(before, after)
    assert _edits(edits) == [
        ("insert", root, 2, "n", None, b"(n)"),
        ("atom", root + ((3, "b"), (1, "c")), 1, "x", b'"x"', b'"y"'),
        ("delete", root, 4, "d", b"(d)", None),
    ]
    # Offsets on the side where the item exists, None on the other.
    assert [(e.old_at, e.new_at) for e in edits] == [
        (None, after.index(b"(n)")),
        (before.index(b'"x"'), after.index(b'"y"')),
        (before.index(b"(d)"), None),
    ]
    assert J.diff(before, before) == []
    barred = b"(kicad_sch (version 20250114) (a x|y))"
    changed = barred.replace(b"x|y", b"x|z")
    assert _edits(J.diff(barred, changed)) == [("atom", root + ((2, "a"),), 3, "y", b"y", b"z")]
    assert _edits(J.diff(barred, changed, knows_bar=False)) == [
        ("atom", root + ((2, "a"),), 1, "x|y", b"x|y", b"x|z")
    ]


def test_diff_pairs_the_most_similar_list():
    """Where two lists with the same head could each be the one that changed, diff pairs
    the more similar (its docstring: "the most similar pairing winning"), by children in
    common, whatever the lists' order or sizes. Known by construction: the list that
    shares all but one child with the new one changed, and the other was deleted."""
    r = ((0, "r"),)
    # The dissimilar candidate comes first.
    before = b"(r (k x y z) (k a b c))"
    after = b"(r (k a b c d))"
    assert _edits(J.diff(before, after)) == [
        ("delete", r, 1, "k", b"(k x y z)", None),
        ("insert", r + ((1, "k"),), 4, "d", None, b"d"),
    ]
    # The similar candidate is the larger: three children deleted from it beat one
    # small list taking nine children.
    before = b"(r (k 1 2 3 4 5 6 7 8 9 z1 z2 z3) (k))"
    after = b"(r (k 1 2 3 4 5 6 7 8 9))"
    inner = r + ((1, "k"),)
    assert _edits(J.diff(before, after)) == [
        ("delete", inner, 10, "z1", b"z1", None),
        ("delete", inner, 11, "z2", b"z2", None),
        ("delete", inner, 12, "z3", b"z3", None),
        ("delete", r, 2, "k", b"(k)", None),
    ]
    # Atoms: a string changed into a string and a keyword into a keyword, the leftover
    # keyword deleted, rather than pairing across kinds.
    assert _edits(J.diff(b'(r a "b" c)', b'(r "x" d)')) == [
        ("delete", r, 1, "a", b"a", None),
        ("atom", r, 2, "b", b'"b"', b'"x"'),
        ("atom", r, 3, "c", b"c", b"d"),
    ]
    # Three items removed around one kept: the bare a stays paired with its identical self.
    assert _edits(J.diff(b'(r b b a "a" (m a))', b"(r a (m a))")) == [
        ("delete", r, 1, "b", b"b", None),
        ("delete", r, 2, "b", b"b", None),
        ("delete", r, 4, "a", b'"a"', None),
    ]
    # Two edits either way: x and p both changed (x into a string), or x kept with "q"
    # inserted before it and p deleted. Only the second pairs no atoms of different kinds.
    assert _edits(J.diff(b"(r x p)", b'(r "q" x)')) == [
        ("insert", r, 1, "q", None, b'"q"'),
        ("delete", r, 2, "p", b"p", None),
    ]
    # Two atoms that trade places, every reading equally similar: each changed in place,
    # which rebuilds the new bytes from the old with nothing moved.
    assert J.atom_changes(b"(r x y)", b"(r y x)") == [
        J.AtomChange(r, 1, b"x", b"y"),
        J.AtomChange(r, 2, b"y", b"x"),
    ]


#: Children per list in the wide cases: 600 differing items a side make a middle of
#: about 360,000 cells, past the 250,000 at which edit_judge aligns a list greedily.
_WIDE = 600


def _wide(items: list[bytes]) -> bytes:
    """One list, (r ...), holding ``items`` one per line as KiCad lays out a sheet."""
    return b"(r\n\t" + b"\n\t".join(items) + b"\n)\n"


@pytest.mark.parametrize("width", [8, _WIDE], ids=["exact", "greedy"])
def test_edit_judge_on_lists_too_wide_to_align_exactly(width):
    """The same constructed edits at a width the exact alignment takes and at one past
    its limit, where the middle of a list is aligned greedily: each answer is known by
    construction, and both aligners must give it."""
    r = ((0, "r"),)
    atoms = [b"a%d" % i for i in range(width)]
    lists = [b"(k %d)" % i for i in range(width)]
    # Every atom changed in place: exactly those atom changes, in order.
    renamed = [b"b%d" % i for i in range(width)]
    assert J.atom_changes(_wide(atoms), _wide(renamed)) == [
        J.AtomChange(r, i + 1, atoms[i], renamed[i]) for i in range(width)
    ]
    # Every list's value changed: an atom change inside each list, at its own index.
    bumped = [b"(k %d)" % (i + 1000) for i in range(width)]
    assert J.atom_changes(_wide(lists), _wide(bumped)) == [
        J.AtomChange(r + ((i + 1, "k"),), 1, b"%d" % i, b"%d" % (i + 1000)) for i in range(width)
    ]
    # Every second atom replaced by a list, the others untouched: an atom and a list
    # are never one item changed, so each replacement is a deletion and an insertion.
    mixed = [b"(L %d)" % i if i % 2 == 0 else atoms[i] for i in range(width)]
    expected = []
    for i in range(0, width, 2):
        expected += [
            ("delete", r, i + 1, atoms[i].decode(), atoms[i], None),
            ("insert", r, i + 1, "L", None, mixed[i]),
        ]
    assert _edits(J.diff(_wide(atoms), _wide(mixed))) == expected
    with pytest.raises(AssertionError, match="not only atom changes"):
        J.atom_changes(_wide(atoms), _wide(mixed))
    # Items added at both ends: two whole items, in two places.
    grown = _wide([b"(first)"] + lists + [b"(last)"])
    edits = J.assert_inserted(_wide(lists), grown, heads=["first", "last"], contiguous=False)
    assert [(e.path, e.index, e.label) for e in edits] == [(r, 1, "first"), (r, width + 2, "last")]
    with pytest.raises(AssertionError, match="contiguous"):
        J.assert_inserted(_wide(lists), grown, heads=["first", "last"])
    removed = J.assert_removed(grown, _wide(lists), heads=["first", "last"], contiguous=False)
    assert [(e.path, e.index, e.label) for e in removed] == [
        (r, 1, "first"),
        (r, width + 2, "last"),
    ]
    # The same around a run of one item repeated: whatever share of the list it takes,
    # the run is matched as equal, never dropped as too common to anchor on.
    same = [b"(b)"] * width
    around = _wide([b"(first)"] + same + [b"(last)"])
    edits = J.assert_inserted(_wide(same), around, heads=["first", "last"], contiguous=False)
    assert [(e.index, e.label) for e in edits] == [(1, "first"), (width + 2, "last")]


def test_views_compare_the_cst_and_kicads_lexer():
    """lexer_view merges tokens that touch around a bar into one atom (decision D1),
    and cst_view reads any object with the CST's attributes."""
    data = b"(data |abc\n def|) (n x|y)"
    assert J.lexer_view(data, knows_bar=True) == [
        ("(",),
        ("A", False, b"data", b"data"),
        ("A", False, b"|abc", b"|abc"),
        ("A", False, b"def|", b"def|"),
        (")",),
        ("(",),
        ("A", False, b"n", b"n"),
        ("A", False, b"x|y", b"x|y"),
        (")",),
    ]
    assert J.lexer_view(b'(p "a\\x41")', knows_bar=True)[2] == ("A", True, b"aA", b'"a\\x41"')
    # A bare atom last in the input still counts, joined with what touches it.
    assert J.lexer_view(b"(a) z|y", knows_bar=True) == [
        ("(",),
        ("A", False, b"a", b"a"),
        (")",),
        ("A", False, b"z|y", b"z|y"),
    ]

    def atom(raw, text):
        return SimpleNamespace(kind="atom", children=None, raw=raw, text=text)

    doc = SimpleNamespace(
        kind="doc",
        children=[
            SimpleNamespace(kind="list", children=[atom(b"p", "p"), atom(b'"q"', "q")]),
            atom(b"z", "z"),
            # Text holding a byte that is not UTF-8, as surrogateescape decodes it: its
            # value is that byte again (the docstring's encode).
            atom(b'"\xff"', "\udcff"),
        ],
    )
    assert J.cst_view(doc) == [
        ("(",),
        ("A", False, b"p", b"p"),
        ("A", True, b"q", b'"q"'),
        (")",),
        ("A", False, b"z", b"z"),
        ("A", True, b"\xff", b'"\xff"'),
    ]


# ---------------------------------------------------------------------------
# sexpr_gen
# ---------------------------------------------------------------------------


def test_bare_tokens_never_open_a_comment():
    """No bare token starts with '#', which first on a line starts a comment. A support
    property, so it is searched for directly rather than left to turn up inside whole
    documents, where such a token is rare."""
    with pytest.raises(NoSuchExample):
        find(
            G.bare_token(),
            lambda raw: raw.startswith(b"#"),
            settings=settings(max_examples=G.budget() * 10, database=None, derandomize=True),
        )


@given(G.kicad_doc())
def test_dialect_strategy_stays_in_kicads_dialect(doc):
    """kicad_doc() writes only what KiCad's writers write: it lexes, balances, has no
    bar, comment line or NUL/FF/VT outside strings, separates its items except list
    from list, and every string is Quotew's output for its own text."""
    toks = L.tokens(doc, knows_bar=True)
    L.tree(doc, knows_bar=True)
    assert all(t.kind != L.BAR for t in toks)
    covered = bytearray(len(doc))
    for t in toks:
        covered[t.start : t.end] = b"\x01" * (t.end - t.start)
    between = {doc[i] for i in range(len(doc)) if not covered[i]}
    assert between <= set(b" \t\r\n"), between  # no comment line, no NUL, FF or VT
    for t in toks:
        if t.kind in (L.SYMBOL, L.NUMBER):
            assert not (set(t.value) & {0x00, 0x0B, 0x0C}) and not t.value.startswith(b"#")
        if t.kind == L.STRING:
            assert doc[t.start : t.end] == quotes(t.value.decode("utf-8"))
    for a, b in zip(toks, toks[1:]):
        if a.end == b.start:  # only after '(', before ')', or between ')' and '('
            assert a.kind == L.LEFT or b.kind == L.RIGHT or (a.kind, b.kind) == (L.RIGHT, L.LEFT)


# ---------------------------------------------------------------------------
# the conftest machinery, through real pytest runs
# ---------------------------------------------------------------------------

_INNER_TESTS = """
import pytest

_BROKEN = b'(kicad_sch (version 20250114) (generator "eeschema")'


def test_passes_writing_a_broken_schematic(tmp_path):
    (tmp_path / "broken.kicad_sch").write_bytes(_BROKEN)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="expected")
def test_xfails_writing_a_broken_schematic(tmp_path):
    (tmp_path / "broken.kicad_sch").write_bytes(_BROKEN)
    assert False


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="expected")
def test_xpasses_strictly():
    pass


@pytest.mark.xfail(strict=False, reason="expected")
def test_xpasses_laxly():
    pass


def test_fails():
    assert False


@pytest.mark.nightly
def test_nightly_only():
    pass


@pytest.mark.kicad_placeholder("sub/broken.kicad_sch")
def test_leaves_its_named_placeholder(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "broken.kicad_sch").write_bytes(_BROKEN)


@pytest.mark.kicad_placeholder("sub/broken.kicad_sch")
def test_leaves_a_broken_file_beside_its_placeholder(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "broken.kicad_sch").write_bytes(_BROKEN)
    (tmp_path / "other.kicad_sch").write_bytes(_BROKEN)


@pytest.mark.kicad_placeholder("broken.kicad_sch")
def test_leaves_its_placeholder_under_another_path(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "broken.kicad_sch").write_bytes(_BROKEN)


@pytest.mark.kicad_placeholder("broken.kicad_sch")
def test_fails_before_writing_its_placeholder():
    assert False
"""

_INNER_CONFTEST = """
import json
import os

import tests.conftest as ladder

_config = None
_reports = []
_stashed = {}


def pytest_configure(config):
    global _config
    _config = config


def pytest_runtest_logreport(report):
    data = _config.hook.pytest_report_to_serializable(config=_config, report=report)
    _reports.append(data)


def pytest_runtest_teardown(item):
    _stashed[item.name] = ladder._call_xfailed(item.stash.get(ladder._CALL_REPORT, None))


def pytest_sessionfinish(session):
    with open(os.environ["LADDER_INNER_OUT"], "w") as out:
        json.dump({"reports": _reports, "stashed": _stashed}, out, default=str)
"""


@lru_cache(maxsize=None)
def _inner_session(profile: str | None, select: str = "") -> str:
    """Run _INNER_TESTS under this repo's conftest (as a plugin) in a fresh pytest
    process; return what the inner conftest recorded, as JSON text."""
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        (root / "test_inner.py").write_text(_INNER_TESTS)
        (root / "conftest.py").write_text(_INNER_CONFTEST)
        (root / "pytest.ini").write_text(
            "[pytest]\nmarkers =\n    nightly: test\n    kicad_placeholder: test\n"
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
        env.pop("HYPOTHESIS_PROFILE", None)
        if profile is not None:
            env["HYPOTHESIS_PROFILE"] = profile
        env["PYTHONPATH"] = os.pathsep.join([str(REPO), str(REPO / "tests")])
        env["LADDER_INNER_OUT"] = str(root / "out.json")
        argv = [sys.executable, "-m", "pytest", "-p", "tests.conftest", "-p", "no:xdist"]
        argv += ["-p", "no:cacheprovider", "-q", "-c", str(root / "pytest.ini")]
        argv += [
            "--rootdir",
            str(root),
            "--basetemp",
            str(root / "tmp"),
            str(root / "test_inner.py"),
        ]
        if select:
            argv += ["-k", select]
        run = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=300)
        out = root / "out.json"
        if not out.exists():
            raise RuntimeError(f"the inner pytest run recorded nothing:\n{run.stdout[-2000:]}")
        return out.read_text()


def _reports(request, recorded: str) -> dict[tuple[str, str], pytest.TestReport]:
    rows = json.loads(recorded)["reports"]
    out = {}
    for row in rows:
        report = request.config.hook.pytest_report_from_serializable(
            config=request.config, data=row
        )
        out[(report.nodeid.rsplit("::", 1)[-1], report.when)] = report
    return out


def test_oracle_skips_only_xfailed_calls(request):
    """A test whose call failed as its strict xfail expects leaves the kicad-cli oracle
    out; a pass, a failure and an XPASS (strict or not) do not.

    Judged by real pytest reports from an inner run, by the stash this repo's
    makereport wrapper fills there, and, where kicad-cli exists, by the oracle itself:
    the passing test's unloadable schematic is an error at teardown and the xfailed
    test's is not.
    """
    recorded = _inner_session(None)
    reports = _reports(request, recorded)
    stashed = json.loads(recorded)["stashed"]
    expected = {
        "test_passes_writing_a_broken_schematic": False,
        "test_xfails_writing_a_broken_schematic": True,
        "test_xpasses_strictly": False,
        "test_xpasses_laxly": False,
        "test_fails": False,
    }
    for name, xfailed in expected.items():
        assert _call_xfailed(reports[(name, "call")]) is xfailed, name
        assert stashed[name] is xfailed, name
    assert _call_xfailed(None) is False
    if HAS_KICAD_CLI:
        assert reports[("test_passes_writing_a_broken_schematic", "teardown")].failed
        assert reports[("test_xfails_writing_a_broken_schematic", "teardown")].passed


def test_nightly_items_skip_without_the_profile(request):
    """A @pytest.mark.nightly test is skipped, naming HYPOTHESIS_PROFILE, unless that
    variable says nightly; then it runs."""
    for profile in (None, "ci", "mutation"):
        assert "HYPOTHESIS_PROFILE=nightly" in (_nightly_skip_reason(profile) or "")
    assert _nightly_skip_reason("nightly") is None
    skipped = _reports(request, _inner_session(None))[("test_nightly_only", "setup")]
    assert skipped.skipped and "HYPOTHESIS_PROFILE=nightly" in str(skipped.longrepr)
    ran = _reports(request, _inner_session("nightly", "nightly_only"))
    assert ran[("test_nightly_only", "call")].passed


def test_oracle_skips_only_the_placeholders_a_test_names(request):
    """kicad_placeholder takes exactly the files it names, by path under tmp_path, out of
    the oracle: the named broken schematic passes and an unnamed one beside it fails where
    kicad-cli exists. A test that passed without leaving a file its marker names fails at
    teardown, kicad-cli or not, so a marker cannot outlive its file or miss it by a path;
    a test whose call already failed is not failed again for it. Judged by real pytest
    reports from an inner run."""
    reports = _reports(request, _inner_session(None))
    assert reports[("test_leaves_its_named_placeholder", "teardown")].passed
    stale = reports[("test_leaves_its_placeholder_under_another_path", "teardown")]
    assert stale.failed and "kicad_placeholder names ['broken.kicad_sch']" in str(stale.longrepr)
    assert reports[("test_fails_before_writing_its_placeholder", "call")].failed
    assert reports[("test_fails_before_writing_its_placeholder", "teardown")].passed
    if HAS_KICAD_CLI:
        beside = reports[("test_leaves_a_broken_file_beside_its_placeholder", "teardown")]
        assert beside.failed and "other.kicad_sch" in str(beside.longrepr)


def test_netlist_fields_decodes_raw_xml():
    """kicadxml as kicad-cli 9.0.8 writes it (2026-10-10): control bytes raw, CR as
    &#xD;, an empty field self-closed. Entities decode once, only a component's own
    first <fields> counts, and library fields are not component fields."""
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n<export version="E">\n  <components>\n'
        b'    <comp ref="R1">\n      <value>10K</value>\n      <fields>\n'
        b'        <field name="P_T">A\tB</field>\n'
        b'        <field name="E_R">A&#xD;B</field>\n'
        b'        <field name="E_A">A\x07B</field>\n'
        b'        <field name="TWICE">a &amp;lt; b</field>\n'
        b'        <field name="ENTITIES">&lt;&gt;&quot;&apos;&#65;&#x42;</field>\n'
        b'        <field name="EMPTY"/>\n'
        b'        <field name="N&amp;M">x</field>\n'
        b'        <field name="UTF8">\xce\xa9</field>\n'
        b"      </fields>\n"
        b'      <property name="P_T" value="A&#x9;B"/>\n    </comp>\n'
        b'    <comp ref="U1">\n      <fields>\n        <field name="F">v</field>\n'
        b'      </fields>\n      <variants><variant name="V"><fields>'
        b'<field name="F">other</field></fields></variant></variants>\n    </comp>\n'
        b'  </components>\n  <libparts>\n    <libpart lib="Device" part="R">\n'
        b'      <fields>\n        <field name="LIB_ONLY">x</field>\n      </fields>\n'
        b"    </libpart>\n  </libparts>\n</export>\n"
    )
    with pytest.raises(ET.ParseError):
        ET.fromstring(xml)  # the raw BEL: why the parsing is regex on bytes
    assert parse_comp_fields(xml) == {
        ("R1", "P_T"): "A\tB",
        ("R1", "E_R"): "A\rB",
        ("R1", "E_A"): "A\x07B",
        ("R1", "TWICE"): "a &lt; b",
        ("R1", "ENTITIES"): "<>\"'AB",
        ("R1", "EMPTY"): "",
        ("R1", "N&M"): "x",
        ("R1", "UTF8"): "\u03a9",
        ("U1", "F"): "v",
    }
