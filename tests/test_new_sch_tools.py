"""Tests for new schematic manipulation tools."""

import re
from pathlib import Path

import geom_gen as G
import netlist_oracle as NO
import pytest
import routing_fixtures as RF
import sch_judge as S
from conftest import reparse, requires_cli, run_erc
from mcp.server.mcpserver.exceptions import ToolError
from test_ladder_r2_pins import lexed_root, port_pin_ends, report_iu

from mcp_server_kicad import schematic

#: A printed point "(x, y)", as KiCad's reports and the routing tools print one.
_POINT = re.compile(r"\((-?[0-9.]+), (-?[0-9.]+)\)")
#: KiCad's own names for a net no label names (netlist_oracle's rule).
_AUTO_NET = re.compile(r"(^|/)(Net|unconnected)-\(")


def named_pins(message: str, reference: str) -> dict[str, list[str]]:
    """{pin number: the lines of ``message`` naming it} for each pin of ``reference`` the
    message names as <reference>:<number>."""
    token = re.compile(rf"(?<![\w:]){re.escape(reference)}:([^\s,;:()]+)")
    found: dict[str, list[str]] = {}
    for line in message.splitlines():
        for number in token.findall(line):
            found.setdefault(number, []).append(line)
    return found


def pin_ends(data: bytes, reference: str) -> dict[str, tuple[int, int]]:
    """{pin number: end} of the one placed ``reference``, by the transform port."""
    (ends,) = port_pin_ends(data, reference)
    return {end.number: (end.x, end.y) for end in ends}


def net_keys(path: Path, reference: str, pins) -> dict:
    """Per pin of ``reference``, how kicad-cli's netlist places it: whether it is on an
    unconnected net, the net's name when a label names it, and every node on its net; None
    for a pin the netlist leaves out."""
    nets = NO.nets(path)
    keys = {}
    for number in pins:
        homes = [net for net in nets if (reference, number) in net[3]]
        if len(homes) > 1:
            raise RuntimeError(f"kicad-cli lists {reference}:{number} on {len(homes)} nets")
        if not homes:
            keys[number] = None
            continue
        ((_, name, _, nodes),) = homes
        auto = _AUTO_NET.search(name)
        keys[number] = (name.startswith("unconnected-("), None if auto else name, nodes)
    return keys


def far_ends(data: bytes, point) -> set:
    """The other end of every wire or bus of ``data`` that has an end at ``point``."""
    out = set()
    for line in S.lines(data):
        if line.start == point:
            out.add(line.end)
        if line.end == point:
            out.add(line.start)
    return out


def report_problems(message: str, reference: str, before: bytes, after: bytes) -> list[str]:
    """What the result says about each pin it names that the files do not bear out. The pin's
    line must print its end before and after (the transform port on each file), and every
    point it prints must be one of those or the far end of a wire or bus ending there in the
    file of that moment (sch_judge, KiCad's lexer), so every contact it names is in the file
    at that end."""
    old, new = pin_ends(before, reference), pin_ends(after, reference)
    problems = []
    for number, lines in named_pins(message, reference).items():
        if number not in old:
            problems.append(f"{reference}:{number} is no pin of {reference}")
            continue
        e0, e1 = old[number], new[number]
        allowed = {e0, e1} | far_ends(before, e0) | far_ends(after, e1)
        printed = {(report_iu(x), report_iu(y)) for line in lines for x, y in _POINT.findall(line)}
        if not {e0, e1} <= printed:
            problems.append(f"{reference}:{number}: does not print its ends {e0} and {e1}")
        if printed - allowed:
            problems.append(f"{reference}:{number}: prints {sorted(printed - allowed)}, no contact")
    return problems


def _scenario(tmp_path: Path, name: str, symbols, wires=(), labels=(), no_connects=()) -> Path:
    """A sheet of Device:R from tests/fixtures/routing.kicad_sym placed by place_component,
    with raw wires, labels and no-connect flags at exact points (mm)."""
    path = RF.fresh(tmp_path, name)
    for ref, x, y in symbols:
        RF.place(path, "R", ref, x, y)
    for wire in wires:
        RF.wire(path, *wire)
    for text, x, y in labels:
        RF.label(path, text, x, y)
    for x, y in no_connects:
        RF.no_connect(path, x, y)
    return Path(path)


def _splice_wires(path: Path, wires) -> None:
    """Raw wires, ends in internal units, before the root list's closing parenthesis."""
    data = path.read_bytes()
    close = lexed_root(data).end - 1
    text = "".join(
        f"\t(wire (pts (xy {G.sch_mm(x1)} {G.sch_mm(y1)}) (xy {G.sch_mm(x2)} {G.sch_mm(y2)}))"
        f' (stroke (width 0) (type default)) (uuid "00000000-0000-4000-8000-{k:012x}"))\n'
        for k, (x1, y1, x2, y2) in enumerate(wires)
    )
    path.write_bytes(data[:close] + text.encode() + data[close:])


def _erc_left_flag(path: Path) -> set[str]:
    """Which of "R1 pin 2 unconnected" and "a no-connect flag dangling" kicad-cli's ERC
    reports, by violation type."""
    found = set()
    for sheet in run_erc(path).get("sheets", []):
        for violation in sheet.get("violations", []):
            kind = violation.get("type")
            texts = [item.get("description", "") for item in violation.get("items", [])]
            if kind == "pin_not_connected" and any("Symbol R1 Pin 2 " in t for t in texts):
                found.add(kind)
            if kind == "no_connect_dangling":
                found.add(kind)
    return found


_WIRED = (("R1", 101.6, 76.2), ("R2", 127.0, 76.2))
_R1_TO_R2 = ((101.6, 72.39, 127.0, 72.39),)
#: The step 9 design's contact scenarios, rebuilt here: each sheet, the move made on it,
#: and the pins whose kicad-cli net that move changes, the premise each case rests on.
_SCENARIOS = {
    "S1_translate_off_a_wire": (dict(symbols=_WIRED, wires=_R1_TO_R2), (114.3, 101.6, None), {"1"}),
    "S2_turn_in_place": (dict(symbols=_WIRED, wires=_R1_TO_R2), (101.6, 76.2, 90), {"1"}),
    "S3_leave_a_label": (
        dict(symbols=(("R1", 101.6, 76.2),), labels=(("NETX", 101.6, 80.01),)),
        (127.0, 76.2, None),
        {"2"},
    ),
    "S4_turn_onto_the_other_net": (
        dict(
            symbols=(*_WIRED, ("R3", 76.2, 76.2)),
            wires=(*_R1_TO_R2, (101.6, 80.01, 76.2, 80.01)),
        ),
        (101.6, 76.2, 180),
        {"1", "2"},
    ),
    "S5_land_on_a_pin": (
        dict(symbols=(("R1", 50.8, 50.8), ("R4", 101.6, 101.6))),
        (101.6, 109.22, None),
        {"1"},
    ),
    "S6_nothing_attached": (dict(symbols=(("R1", 50.8, 50.8),)), (76.2, 76.2, None), set()),
    "S8_land_on_a_wire_interior": (
        dict(symbols=(("R1", 50.8, 50.8),), wires=((76.2, 97.79, 152.4, 97.79),)),
        (101.6, 101.6, None),
        set(),
    ),
}


class TestMoveComponent:
    def test_move_existing(self, scratch_sch):
        # 200.66 == 158*1.27, on grid
        result = schematic.move_component("R1", 200.66, 200.66, schematic_path=str(scratch_sch))
        assert "Moved" in result
        sch = reparse(str(scratch_sch))
        r1 = next(
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R1" for p in s.properties)
        )
        assert r1.position.X == 200.66
        assert r1.position.Y == 200.66

    def test_move_with_rotation(self, scratch_sch):
        schematic.move_component("R1", 200, 200, rotation=90, schematic_path=str(scratch_sch))
        sch = reparse(str(scratch_sch))
        r1 = next(
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R1" for p in s.properties)
        )
        assert r1.position.angle == 90

    def test_move_missing(self, scratch_sch):
        with pytest.raises(ToolError, match="not found"):
            schematic.move_component("R999", 200, 200, schematic_path=str(scratch_sch))

    @requires_cli
    @pytest.mark.parametrize("case", list(_SCENARIOS))
    def test_names_the_pins_whose_connections_change(self, tmp_path, case):
        """The pins the result names as R1:<number> are exactly the pins whose net in
        kicad-cli's netlist the move changes (connected or not, the label naming it, the
        nodes on it), and each named pin's line prints its end before and after and only
        points where it met something (report_problems). A pin landing on a wire's interior
        joins nothing in kicad-cli and changes no contact, so nothing is named for it."""
        sheet, (x, y, rotation), premise = _SCENARIOS[case]
        path = _scenario(tmp_path, "move", **sheet)
        before = path.read_bytes()
        keys = net_keys(path, "R1", ("1", "2"))
        message = schematic.move_component("R1", x, y, rotation=rotation, schematic_path=str(path))
        changed = {n for n, key in net_keys(path, "R1", ("1", "2")).items() if key != keys[n]}
        if changed != premise:
            raise RuntimeError(f"the premise of {case} is kicad-cli changing {premise}: {changed}")
        assert set(named_pins(message, "R1")) == changed, (
            f"kicad-cli changed the nets of R1's pins {sorted(changed)}; the result says:\n"
            + message
        )
        problems = report_problems(message, "R1", before, path.read_bytes())
        assert not problems, "\n".join(problems) + "\n" + message

    @requires_cli
    def test_names_a_no_connect_flag_left_behind(self, tmp_path):
        """R1 moved off the no-connect flag on its pin 2, which no netlist shows: kicad-cli's
        ERC then reports pin 2 unconnected and the flag dangling, neither of which it did
        before (the premise, checked first). The result names R1:2 as leaving a no-connect
        flag at its old end."""
        path = _scenario(tmp_path, "nc", (("R1", 101.6, 76.2),), no_connects=((101.6, 80.01),))
        before = path.read_bytes()
        if _erc_left_flag(path):
            raise RuntimeError("the premise is an ERC clean of pin 2 and the flag before the move")
        message = schematic.move_component("R1", 127.0, 76.2, schematic_path=str(path))
        seen = _erc_left_flag(path)
        if seen != {"pin_not_connected", "no_connect_dangling"}:
            raise RuntimeError(f"kicad-cli's ERC does not see the flag left behind: {seen}")
        lines = named_pins(message, "R1").get("2", [])
        assert lines, f"the result does not name R1:2:\n{message}"
        assert any(re.search(r"no[-_ ]?connect", line, re.I) for line in lines), message
        problems = report_problems(message, "R1", before, path.read_bytes())
        assert not problems, "\n".join(problems) + "\n" + message

    @requires_cli
    @pytest.mark.parametrize(
        "number",
        ["²", "①", "9" * 4301],
        ids=["superscript_two", "circled_one", "4301_digits"],
    )
    def test_names_a_pin_whose_number_int_cannot_read(self, tmp_path, number):
        """U1 has two pins, numbered ``number`` and "2", and a wire joins the first to R2. The
        number is a superscript two, a circled one or 4301 digits: str.isdigit accepts each and
        int() refuses each. kicad-cli's netlist names U1:<number> on R2's net before the move
        and on a net of its own after (the premises, checked first; the export is also how
        kicad-cli loads the sheet both times). The call returns, KiCad reads U1 at the target
        with every field at its offset, and the result names that pin and no other, with the
        ends and contacts the files bear out."""
        lib = RF.custom_lib(
            tmp_path,
            "TWO",
            [
                (number, "A", "passive", -5.08, 0, 0, False),
                ("2", "B", "passive", 5.08, 0, 180, False),
            ],
        )
        path = Path(RF.fresh(tmp_path, "odd"))
        RF.place_custom(str(path), lib, "TWO", "U1", 101.6, 76.2)
        RF.place(str(path), "R", "R2", 88.9, 76.2, rot=90)
        u1_end = pin_ends(path.read_bytes(), "U1")[number]
        r2_end = max(pin_ends(path.read_bytes(), "R2").values())
        RF.wire(str(path), *(v / 10_000 for v in (*u1_end, *r2_end)))
        before = path.read_bytes()
        joined = net_keys(path, "U1", (number, "2"))
        if joined[number] is None or not any(ref == "R2" for ref, _ in joined[number][2]):
            raise RuntimeError(f"the premise is U1:{number} on R2's net in kicad-cli: {joined}")
        _, old = S.placed_symbol(before, "U1")
        message = schematic.move_component("U1", 127.0, 101.6, schematic_path=str(path))
        apart = net_keys(path, "U1", (number, "2"))
        if apart[number] is None or any(ref == "R2" for ref, _ in apart[number][2]):
            raise RuntimeError(f"the premise is U1:{number} off R2's net in kicad-cli: {apart}")
        _, new = S.placed_symbol(path.read_bytes(), "U1")
        assert new.position == (1_270_000, 1_016_000), f"KiCad reads U1 at {new.position}"
        assert {n: f.offset for n, f in new.fields.items()} == {
            n: f.offset for n, f in old.fields.items()
        }, "U1 moved and its fields did not keep their place on it"
        assert set(named_pins(message, "U1")) == {number}, message[:1000]
        problems = report_problems(message, "U1", before, path.read_bytes())
        assert not problems, "\n".join(problems) + "\n" + message[:1000]

    def test_report_is_capped(self, tmp_path):
        """A 100-pin part with a wire on every pin, moved clear of all of them: every pin
        leaves its wire and meets nothing (the premise, read from both files with the
        transform port and KiCad's lexer). The result names some of the pins, each a pin of
        the part, but not all 100, and gives the rest as a count on a line that names no pin
        and prints no point, the two adding up to 100: the output is bounded."""
        path = Path(RF.fresh(tmp_path, "big"))
        pins = [
            (
                str(k + 1),
                f"P{k + 1}",
                "passive",
                -12.7 if k < 50 else 12.7,
                round(62.23 - 2.54 * (k % 50), 4),
                0 if k < 50 else 180,
                False,
            )
            for k in range(100)
        ]
        RF.place_custom(str(path), RF.custom_lib(tmp_path, "BIG", pins), "BIG", "U1", 50.8, 101.6)
        ends = pin_ends(path.read_bytes(), "U1")
        _splice_wires(
            path, [(x, y, x + (25_400 if x > 508_000 else -25_400), y) for x, y in ends.values()]
        )
        before = path.read_bytes()
        message = schematic.move_component("U1", 152.4, 101.6, schematic_path=str(path))
        after = path.read_bytes()
        moved = pin_ends(after, "U1")
        if (
            len(ends) != 100
            or any(not far_ends(before, end) for end in ends.values())
            or any(far_ends(after, end) for end in moved.values())
        ):
            raise RuntimeError("the premise is 100 wired pins moved clear of every wire")
        named = set(named_pins(message, "U1"))
        assert named, f"the result names none of the 100 pins that left their wires:\n{message}"
        assert named <= set(ends), f"named {sorted(named - set(ends))}, which are no pins of U1"
        assert len(named) < 100, "the result lists every one of 100 pins: its output is unbounded"
        rest = str(100 - len(named))
        counts = [
            line
            for line in message.splitlines()
            if not named_pins(line, "U1")
            and not _POINT.search(line)
            and re.search(rf"(?<![\d.]){rest}(?![\d.])", line)
        ]
        assert counts, f"no line counts the {rest} pins not listed:\n{message}"

    @pytest.mark.parametrize("rotation", [None, 0])
    def test_no_change_writes_nothing(self, kicad_native_sch, rotation):
        """R1 of the native fixture called at its own position, (100, 100), off the 1.27 mm
        grid, and at its own angle or none: nothing changes, so nothing is written, byte for
        byte, and the result says nothing changed rather than that R1 moved."""
        before = kicad_native_sch.read_bytes()
        message = schematic.move_component(
            "R1", 100, 100, rotation=rotation, schematic_path=str(kicad_native_sch)
        )
        assert kicad_native_sch.read_bytes() == before, f"wrote the file; returned {message!r}"
        assert "Moved" not in message, message
        assert re.search(r"\b(nothing|unchanged|already)\b", message, re.I), message


class TestEditComponentValue:
    def test_edit_value(self, scratch_sch):
        result = schematic.set_component_property(
            "R1", key="Value", value="4.7K", schematic_path=str(scratch_sch)
        )
        assert "R1" in result
        sch = reparse(str(scratch_sch))
        r1 = next(
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R1" for p in s.properties)
        )
        val = next(p.value for p in r1.properties if p.key == "Value")
        assert val == "4.7K"

    def test_edit_reference(self, scratch_sch):
        schematic.set_component_property(
            "R1", key="Reference", value="R99", schematic_path=str(scratch_sch)
        )
        sch = reparse(str(scratch_sch))
        refs = [
            next((p.value for p in s.properties if p.key == "Reference"), None)
            for s in sch.schematicSymbols
        ]
        assert "R99" in refs

    def test_edit_missing(self, scratch_sch):
        with pytest.raises(ToolError, match="not found"):
            schematic.set_component_property(
                "R999", key="Value", value="1K", schematic_path=str(scratch_sch)
            )


class TestAddGlobalLabel:
    def test_basic(self, scratch_sch):
        result = schematic.add_global_label("VCC", 50, 50, schematic_path=str(scratch_sch))
        assert "VCC" in result
        sch = reparse(str(scratch_sch))
        assert any(gl.text == "VCC" for gl in sch.globalLabels)

    def test_with_shape(self, scratch_sch):
        schematic.add_global_label(
            "SDA", 60, 60, shape="bidirectional", schematic_path=str(scratch_sch)
        )
        sch = reparse(str(scratch_sch))
        gl = next(g for g in sch.globalLabels if g.text == "SDA")
        assert gl.shape == "bidirectional"


class TestListGlobalLabels:
    def test_empty(self, scratch_sch):
        result = schematic.list_schematic_global_labels(str(scratch_sch))
        assert result == []

    def test_with_labels(self, scratch_sch):
        schematic.add_global_label("VCC", 50, 50, schematic_path=str(scratch_sch))
        result = schematic.list_schematic_global_labels(str(scratch_sch))
        texts = [item.text for item in result]
        assert "VCC" in texts


class TestAddPowerSymbol:
    def test_basic(self, scratch_sch, stock_symbol_dir, monkeypatch):
        # power:VCC from the stand-in stock folder, so this does not need KiCad.
        monkeypatch.setenv("KICAD_SYMBOL_DIR", str(stock_symbol_dir))
        result = schematic.add_power_symbol(
            "power:VCC",
            "VCC1",
            100,
            80,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "VCC1" in result
        sch = reparse(str(scratch_sch))
        refs = [
            next((p.value for p in s.properties if p.key == "Reference"), None)
            for s in sch.schematicSymbols
        ]
        assert "VCC1" in refs


class TestAddText:
    def test_basic(self, scratch_sch):
        result = schematic.add_text("Hello World", 50, 120, schematic_path=str(scratch_sch))
        assert "Hello World" in result
        sch = reparse(str(scratch_sch))
        assert any(t.text == "Hello World" for t in sch.texts)
