"""Hierarchy writers: label spin, sheet pins, resize, new sheets, create_project (step 12a).

Judges, none of them the code under test:

- kicad-cli: the netlist (which pins join), the SVG (where a label or pin text is drawn), the
  sheet order of its netlist;
- tests/kicad_ref/sch_sheet.py, a port of KiCad's sheet pin load rule, Resize, GetMinWidth and
  GetMinHeight and AutoplaceFields, checked here against the sheets KiCad ships;
- the files KiCad ships, for the templates and the justify KiCad prints for each rotation.
"""

from __future__ import annotations

import json
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import svg_judge
from conftest import netlist_nodes, requires_cli
from kicad_corpus import PER_PUSH_CAP, shipped
from kicad_ref import dsn_lexer as L
from kicad_ref import sch_sheet as SS
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _cst, project, schematic
from mcp_server_kicad._shared import _run_cli

IU = SS.IU_PER_MM
CRLF = bytes([13, 10])  # the shipped demos are written with CRLF line ends
# The macOS cask ships templates but no demos, and ci.yml installs no KiCad at all.
no_demos = pytest.mark.skipif(
    not shipped(("demos",), (".kicad_sch",), ()), reason="no shipped demos in this KiCad install"
)

# ---------------------------------------------------------------------------------------------
# Reading files with KiCad's lexer
# ---------------------------------------------------------------------------------------------


def top(data: bytes):
    return L.tree(data)[0]


def kids(group, head: bytes) -> list:
    return [c for c in group.items[1:] if SS._head(c) == head]


def value(group) -> str:
    return group.items[1].value.decode()


def sheets_of(path: Path) -> list:
    return kids(top(path.read_bytes()), b"sheet")


def sheet_uuid(sheet) -> str:
    return value(SS._child(sheet, b"uuid"))


def sheet_name(sheet) -> str:
    return next(
        p.items[2].value.decode() for p in kids(sheet, b"property") if value(p) == "Sheetname"
    )


def pin_at(pin) -> tuple[int, int, int]:
    at = SS._child(pin, b"at")
    return SS.iu(at.items[1].value), SS.iu(at.items[2].value), int(float(at.items[3].value))


def pins_of(sheet) -> dict[str, tuple[int, int, int]]:
    return {value(p): pin_at(p) for p in kids(sheet, b"pin")}


def file_uuid(path: Path) -> str:
    return value(SS._child(top(path.read_bytes()), b"uuid"))


def instance_paths(group) -> list[tuple[str, str, str]]:
    """(project, path, page-or-reference) of a sheet's (instances) entries."""
    out = []
    inst = SS._child(group, b"instances")
    for pj in kids(inst, b"project") if inst is not None else []:
        for path in kids(pj, b"path"):
            page = SS._child(path, b"page")
            out.append((value(pj), value(path), value(page) if page is not None else ""))
    return out


# ---------------------------------------------------------------------------------------------
# A project to edit
# ---------------------------------------------------------------------------------------------


class Proj:
    def __init__(self, tmp_path: Path):
        self.dir = tmp_path / "p"
        project.create_project(directory=str(self.dir), name="p")
        self.pro = self.dir / "p.kicad_pro"
        self.root = self.dir / "p.kicad_sch"
        self.n = 0

    def child(self, name: str = "child") -> Path:
        path = self.dir / f"{name}.kicad_sch"
        project.create_schematic(schematic_path=str(path))
        return path

    def sheet(self, parent: Path, child: Path, name: str = "C", pins=(), **kw) -> str:
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name=name,
            sheet_file=str(child),
            pins=list(pins),
            project_path=str(self.pro),
            **kw,
        )
        return sheet_uuid(sheets_of(parent)[-1])

    def resistor_on(self, child: Path, ref: str, net: str, x: float, y: float = 60.96) -> None:
        """R at (x, y) with a hierarchical label named net on its pin 1 (3.81 above the
        origin). x is put on the 1.27 mm grid place_component snaps to."""
        x = round(round(x / 1.27) * 1.27, 4)
        schematic.place_component(
            "Device:R", ref, "1k", x, y, schematic_path=str(child), project_path=str(self.pro)
        )
        schematic.add_hierarchical_label(net, "input", x, y - 3.81, 0, schematic_path=str(child))

    def stub(self, parent: Path, sheet_uuid_: str, pin: str, net: str) -> None:
        """A 2.54 mm wire from the written position of a sheet pin, away from the sheet, and
        a local label on its end."""
        sheet = next(s for s in sheets_of(parent) if sheet_uuid(s) == sheet_uuid_)
        x, y, angle = pins_of(sheet)[pin]
        dx = -25400 if angle == 180 else 25400 if angle == 0 else 0
        dy = -25400 if angle == 90 else 25400 if angle == 270 else 0
        x1, y1 = (x + dx) / IU, (y + dy) / IU
        schematic.add_wires(
            [{"x1": x / IU, "y1": y / IU, "x2": x1, "y2": y1}], schematic_path=str(parent)
        )
        schematic.add_label(net, x1, y1, 0, schematic_path=str(parent))


def net_of(path: Path, ref: str, pin: str) -> str | None:
    names = [n for n, nodes in netlist_nodes(path).items() if (ref, pin) in nodes]
    return names[0] if names else None


def sheet_order(path: Path) -> list[str]:
    """The sheet paths kicad-cli's netlist lists, in its order."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        out = Path(scratch) / "n.xml"
        _run_cli(
            ["sch", "export", "netlist", "--format", "kicadxml", "--output", str(out), str(path)]
        )
        sheets = ET.fromstring(out.read_bytes()).find("design").findall("sheet")
    return [s.get("name", "") for s in sheets]


# ---------------------------------------------------------------------------------------------
# The port, against the sheets KiCad ships
# ---------------------------------------------------------------------------------------------


def _project_pen(path: Path) -> int:
    """The project's schematic.drawing.default_line_thickness (mils) in IU, 6 mils when the
    folder has no single project file."""
    pros = list(path.parent.glob("*.kicad_pro"))
    if len(pros) == 1:
        try:
            mils = json.loads(pros[0].read_bytes())["schematic"]["drawing"][
                "default_line_thickness"
            ]
            return round(float(mils) * SS.IU_PER_MIL)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return SS.DEFAULT_PEN


def _demo_sheets():
    for name, path in shipped(("demos",), (".kicad_sch",), (), cap=PER_PUSH_CAP):
        data = path.read_bytes()
        if b"(sheet" not in data:
            continue
        for group in kids(top(data), b"sheet"):
            yield name, path, group


@no_demos
def test_port_pins_are_fixed_points_of_the_load_rule():
    """Every pin of a sheet KiCad saved sits where the load rule would leave it, with the
    justify its side prints."""
    seen = 0
    for name, _path, group in _demo_sheets():
        sheet = SS.read_sheet(group)
        for pin, node in zip(sheet.pins, kids(group, b"pin"), strict=True):
            seen += 1
            assert (pin.x, pin.y) == pin_at(node)[:2], (name, pin)
            assert pin.justify == SS.pin_justify(pin.side), (name, pin)
    assert seen > 100, "the shipped demos should hold hundreds of sheet pins"


@no_demos
def test_port_autoplace_reproduces_the_fields_kicad_placed():
    """AutoplaceFields puts both fields where KiCad saved them on the flagged sheets whose
    file name sits below the box. A flag does not undo a drag, so a few sheets may differ:
    measured 2026-10-18, none of 43 on KiCad 9.0.8's demos (12 more, in vme-wren, have the
    file name inside the box) and 1 of 62 on 10.0.7's."""
    checked = differing = 0
    for name, path, group in _demo_sheets():
        sheet = SS.read_sheet(group)
        if not sheet.autoplaced:
            continue
        _, fy, _ = SS.field_at(group, b"Sheetfile")
        if fy < sheet.y + sheet.h:
            continue
        want = SS.autoplace(sheet, _project_pen(path))
        checked += 1
        differing += (
            SS.field_at(group, b"Sheetname") != want.name_at
            or SS.field_at(group, b"Sheetfile") != want.file_at
            or SS.field_justify(group, b"Sheetname") != want.name_justify
            or SS.field_justify(group, b"Sheetfile") != want.file_justify
        )
    assert checked > 30
    assert differing <= checked // 20, f"{differing} of {checked} flagged sheets differ"


# ---------------------------------------------------------------------------------------------
# Templates are KiCad's own output
# ---------------------------------------------------------------------------------------------

_VALUES = re.compile(
    rb'"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|\b(?:input|output|bidirectional|tri_state|passive)\b'
)


def _masked(raw: bytes) -> bytes:
    return _VALUES.sub(b"*", raw)


def _demo_item(relative: str, head: bytes, text: bytes) -> bytes:
    """The bytes of the item `(head "text"` of a shipped demo, from its '(' to its ')'."""
    path = next(p for n, p in shipped(("demos",), (".kicad_sch",), ()) if n.endswith(relative))
    data = path.read_bytes().replace(CRLF, b"\n")
    start = data.index(b"(" + head + b' "' + text + b'"')
    return data[start : _end(data, start)]


def _end(data: bytes, start: int) -> int:
    depth = 0
    for k in range(start, len(data)):
        if data[k : k + 1] == b"(":
            depth += 1
        elif data[k : k + 1] == b")":
            depth -= 1
            if depth == 0:
                return k + 1
    raise AssertionError("unbalanced")


@no_demos
@pytest.mark.parametrize(
    ("template", "relative", "head", "text"),
    [
        ("_LABEL_TPL", "RoyalBlue54L-NFC-Antenna.kicad_sch", b"label", b"ANT"),
        ("_HLABEL_TPL", "multichannel/channel_strip.kicad_sch", b"hierarchical_label", b"OUT"),
        ("_GLABEL_TPL", "tiny_tapeout/tinytapeout-demo.kicad_sch", b"global_label", b"uio0"),
    ],
)
def test_label_templates_are_a_shipped_labels_tokens(template, relative, head, text):
    """Each label template has the tokens and whitespace of a label KiCad wrote, with only its
    values (and a global label's trailing field, which is left out) differing."""
    want = _demo_item(relative, head, text)
    if head == b"global_label":
        # keep everything up to the Intersheetrefs property, which the template omits
        cut = want.index(b"\n\t\t(property")
        want = want[:cut] + b"\n\t)"
        want = want.replace(b"\n\t\t(fields_autoplaced yes)", b"")
    got = _cst.serialize(getattr(schematic, template))
    assert _masked(got) == _masked(want)


@no_demos
def test_sheet_templates_are_a_shipped_sheets_tokens():
    """The sheet block template is the vme_buffers_addr block of vme_interface.kicad_sch with
    its pins removed, and the pin template one of its pins."""
    relative = "vme-wren/vme_interface.kicad_sch"
    path = next(p for n, p in shipped(("demos",), (".kicad_sch",), ()) if n.endswith(relative))
    data = path.read_bytes().replace(CRLF, b"\n")
    marker = data.index(b'(property "Sheetname" "vme_buffers_addr"')
    start = data.rindex(b"(sheet\n", 0, marker)
    block = data[start : _end(data, start)]
    pins = [m for m in re.finditer(rb'\n\t\t\(pin "[^"]*" \w+\n', block)]
    first = pins[0].start() + 3
    pin_end = _end(block, first)
    pin_text = block[first:pin_end]
    without_pins = block
    for m in reversed(pins):
        s = m.start() + 1
        e = _end(block, s)
        without_pins = without_pins[: s - 1] + without_pins[e:]
    assert _masked(_cst.serialize(project._SHEET_TPL)) == _masked(without_pins)
    assert _masked(_cst.serialize(project._SHEET_PIN_TPL)) == _masked(pin_text)


# ---------------------------------------------------------------------------------------------
# Bug 72: every label-like writer prints the justify KiCad prints for the rotation
# ---------------------------------------------------------------------------------------------

#: Horizontal justify for a rotation: SCH_LABEL_BASE::SetSpinStyle gives RIGHT (0) and UP (90)
#: H left, LEFT (180) and BOTTOM (270) H right, and V bottom (sch_label.cpp:309-341 at 9.0.8);
#: the global label's SetSpinStyle (1940-1944) and the hierarchical label's (2116-2120) then
#: set V centre, which prints nothing. The shipped demos hold local labels at 0 and 180,
#: hierarchical labels at 0 and 180 and a global label at 0, each written that way.
H_OF_ROTATION = {0: "left", 90: "left", 180: "right", 270: "right"}


def expected_justify(kind: bytes, rotation: int) -> tuple[str, ...]:
    h = H_OF_ROTATION[rotation]
    return (h, "bottom") if kind == b"label" else (h,)


def label_nodes(data: bytes):
    for g in top(data).items:
        if SS._head(g) in (b"label", b"global_label", b"hierarchical_label"):
            yield g


def check_label_justify(path: Path) -> int:
    n = 0
    for g in label_nodes(path.read_bytes()):
        rotation = int(float(SS._child(g, b"at").items[3].value))
        got = SS._justify(SS._child(g, b"effects"))
        kind = SS._head(g)
        assert kind is not None
        assert got == expected_justify(kind, rotation), (value(g), rotation, got)
        n += 1
    return n


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_label_tools_print_the_justify_of_their_rotation(tmp_path, rotation):
    sch = tmp_path / "a.kicad_sch"
    project.create_schematic(schematic_path=str(sch))
    schematic.add_label("L", 50.8, 50.8, rotation, schematic_path=str(sch))
    schematic.add_global_label("G", 50.8, 76.2, rotation, "input", schematic_path=str(sch))
    schematic.add_hierarchical_label("H", "input", 50.8, 101.6, rotation, schematic_path=str(sch))
    assert check_label_justify(sch) == 3


def _texts(svg: str) -> list[str]:
    return re.findall(r"<text[^>]*>[^<]*</text>", svg)


def _svg_texts(path: Path) -> list[str]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        return _texts(svg_judge.export_svg(path, Path(scratch)))


def _kicad_form(kind: str, text: str, x: str, y: str, rotation: int) -> bytes:
    """A label as KiCad writes it (RoyalBlue54L-NFC-Antenna.kicad_sch, tinytapeout-demo and
    channel_strip.kicad_sch of the shipped demos), with the justify its rotation gives."""
    justify = " ".join(expected_justify(kind.encode(), rotation))
    shape = "" if kind == "label" else "\n\t\t(shape input)"
    return (
        f'\t({kind} "{text}"{shape}\n\t\t(at {x} {y} {rotation})\n\t\t(effects\n\t\t\t(font\n'
        f"\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {justify})\n\t\t)\n"
        f'\t\t(uuid "00000000-0000-4000-8000-00000000000{rotation // 90}")\n\t)\n'
    ).encode()


@requires_cli
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("kind", ["label", "global_label", "hierarchical_label"])
def test_label_tools_draw_as_kicad_draws_its_own(tmp_path, kind, rotation):
    """kicad-cli plots the tool's label exactly where it plots the same label written the way
    KiCad writes it."""
    tool = tmp_path / "tool.kicad_sch"
    project.create_schematic(schematic_path=str(tool))
    if kind == "label":
        schematic.add_label("TXT", 50.8, 50.8, rotation, schematic_path=str(tool))
    elif kind == "global_label":
        schematic.add_global_label("TXT", 50.8, 50.8, rotation, "input", schematic_path=str(tool))
    else:
        schematic.add_hierarchical_label(
            "TXT", "input", 50.8, 50.8, rotation, schematic_path=str(tool)
        )
    ref = tmp_path / "ref.kicad_sch"
    project.create_schematic(schematic_path=str(ref))
    blank = ref.read_bytes()
    cut = blank.index(b"\t(sheet_instances")
    ref.write_bytes(blank[:cut] + _kicad_form(kind, "TXT", "50.8", "50.8", rotation) + blank[cut:])
    assert _svg_texts(tool) == _svg_texts(ref)
    # the premise: a label with no justify is drawn somewhere else, at the rotations where the
    # justify decides the spin
    if rotation != 0:
        bare = re.sub(rb"\n\t\t\t\(justify [a-z ]+\)", b"", ref.read_bytes())
        bare_file = tmp_path / "bare.kicad_sch"
        bare_file.write_bytes(bare)
        assert _svg_texts(bare_file) != _svg_texts(ref), "justify did not change the drawing"


@requires_cli
def test_connecting_writers_print_the_justify_of_their_rotation(tmp_path):
    """wire_pins_to_net (every direction), connect_pins and auto_place_decoupling_cap write
    labels at all four rotations."""
    sch = tmp_path / "w.kicad_sch"
    project.create_schematic(schematic_path=str(sch))
    for k, ref in enumerate(("R1", "R2", "R3", "R4")):
        schematic.place_component("Device:R", ref, "1k", 40 + 30 * k, 60, schematic_path=str(sch))
    for k, direction in enumerate(("left", "right", "up", "down")):
        schematic.wire_pins_to_net(
            [{"reference": f"R{k + 1}", "pin": "1"}],
            f"N{k}",
            direction,
            schematic_path=str(sch),
        )
    schematic.connect_pins("R1", "2", "R2", "2", schematic_path=str(sch))
    assert check_label_justify(sch) >= 5
    rotations = {
        int(float(SS._child(g, b"at").items[3].value)) for g in label_nodes(sch.read_bytes())
    }
    assert {0, 90, 180, 270} <= rotations


# ---------------------------------------------------------------------------------------------
# Bug 34: add_sheet_pin
# ---------------------------------------------------------------------------------------------


def test_add_sheet_pin_refusals_leave_the_file_alone(tmp_path):
    p = Proj(tmp_path)
    child = p.child()
    uid = p.sheet(p.root, child)
    before = p.root.read_bytes()
    cases: list[dict[str, Any]] = [
        {"pin_name": "A", "connection_type": "input", "side": "top"},
        {"pin_name": "A", "connection_type": "input", "side": "bottom"},
        {"pin_name": "", "connection_type": "input"},
        {"pin_name": "A", "connection_type": "banana"},
        {"pin_name": "A", "connection_type": "input|x"},
    ]
    for kwargs in cases:
        with pytest.raises(ToolError):
            project.add_sheet_pin(uid, schematic_path=str(p.root), **kwargs)
        assert p.root.read_bytes() == before, kwargs


def test_each_edge_fills_from_its_own_first_slot(tmp_path):
    """A right pin after three left pins takes the right edge's first slot, not the fourth."""
    p = Proj(tmp_path)
    uid = p.sheet(p.root, p.child(), x=25.4, y=25.4)
    for name in ("L1", "L2", "L3"):
        project.add_sheet_pin(uid, name, "input", str(p.root), "left")
    project.add_sheet_pin(uid, "R1", "output", str(p.root), "right")
    sheet = sheets_of(p.root)[0]
    at, size = SS._child(sheet, b"at"), SS._child(sheet, b"size")
    x, y = SS.iu(at.items[1].value), SS.iu(at.items[2].value)
    w = SS.iu(size.items[1].value)
    pins = pins_of(sheet)
    assert pins["L1"] == (x, y + 25400, 180)
    assert pins["L3"] == (x, y + 3 * 25400, 180)
    assert pins["R1"] == (x + w, y + 25400, 0)


def test_written_pins_are_fixed_points_of_the_load_rule(tmp_path):
    p = Proj(tmp_path)
    uid = p.sheet(p.root, p.child(), pins=[{"name": "A", "direction": "input"}])
    project.add_sheet_pin(uid, "B", "input", str(p.root), "left")
    project.add_sheet_pin(uid, "Q", "output", str(p.root), "right")
    group = sheets_of(p.root)[0]
    sheet = SS.read_sheet(group)
    assert len(sheet.pins) == 3
    for pin, node in zip(sheet.pins, kids(group, b"pin"), strict=True):
        assert (pin.x, pin.y) == pin_at(node)[:2]
        assert pin.justify == SS.pin_justify(pin.side)
        assert str(pin_at(node)[0] / IU).count("99999") == 0


@requires_cli
def test_added_sheet_pins_join_in_kicad_cli(tmp_path):
    """Three pins fit on the default 10.16 mm sheet and each joins its child's label; a fourth
    is refused with the file intact, the refusal names a height that fits, and after that
    resize the fourth pin joins as well."""
    p = Proj(tmp_path)
    child = p.child()
    uid = p.sheet(p.root, child)
    for k in range(1, 4):
        p.resistor_on(child, f"R{k}", f"P{k}", 30 + 25 * k)
        project.add_sheet_pin(uid, f"P{k}", "input", str(p.root))
        p.stub(p.root, uid, f"P{k}", f"N{k}")
    for k in range(1, 4):
        assert net_of(p.root, f"R{k}", "1") == f"N{k}"
    before = p.root.read_bytes()
    with pytest.raises(ToolError) as refused:
        project.add_sheet_pin(uid, "P4", "input", str(p.root))
    assert p.root.read_bytes() == before
    message = str(refused.value)
    assert "left" in message
    wanted = re.search(r"height of (\d+(?:\.\d+)?) mm", message)
    assert wanted, message
    project.modify_hierarchical_sheet(uid, str(p.root), height=float(wanted.group(1)))
    p.resistor_on(child, "R4", "P4", 130)
    project.add_sheet_pin(uid, "P4", "input", str(p.root))
    p.stub(p.root, uid, "P4", "N4")
    assert net_of(p.root, "R4", "1") == "N4"
    assert net_of(p.root, "R3", "1") == "N3"


def test_sheet_pin_text_is_justified_for_its_edge(tmp_path):
    p = Proj(tmp_path)
    uid = p.sheet(p.root, p.child(), pins=[{"name": "A", "direction": "input"}])
    project.add_sheet_pin(uid, "Q", "output", str(p.root), "right")
    for pin in kids(sheets_of(p.root)[0], b"pin"):
        side = SS.SIDE_OF_ANGLE[pin_at(pin)[2]]
        assert SS._justify(SS._child(pin, b"effects")) == SS.pin_justify(side)


# ---------------------------------------------------------------------------------------------
# Bug 35: modify_hierarchical_sheet is KiCad's resize
# ---------------------------------------------------------------------------------------------


def fields_where_kicad_puts_them(path: Path, pen: int = 1524) -> bool:
    group = sheets_of(path)[0]
    want = SS.autoplace(SS.read_sheet(group), pen)
    return (
        SS.field_at(group, b"Sheetname") == want.name_at
        and SS.field_at(group, b"Sheetfile") == want.file_at
        and SS.field_justify(group, b"Sheetname") == want.name_justify
        and SS.field_justify(group, b"Sheetfile") == want.file_justify
    )


def test_resize_moves_edge_pins_and_autoplaced_fields(tmp_path):
    p = Proj(tmp_path)
    uid = p.sheet(p.root, p.child(), pins=[{"name": "A", "direction": "input"}])
    project.add_sheet_pin(uid, "Q", "output", str(p.root), "right")
    assert fields_where_kicad_puts_them(p.root)
    project.modify_hierarchical_sheet(uid, str(p.root), width=40.64, height=30.48)
    group = sheets_of(p.root)[0]
    sheet = SS.read_sheet(group)
    assert (sheet.w, sheet.h) == (406400, 304800)
    right = [pin for pin in sheet.pins if pin.name == "Q"][0]
    assert right.x == sheet.x + sheet.w and right.side == "right"
    assert pins_of(group)["Q"][:2] == (right.x, right.y)
    assert pins_of(group)["A"][:2] == (sheet.x, sheet.y + 25400)
    assert fields_where_kicad_puts_them(p.root)


def test_a_sheet_resized_with_a_bottom_pin_keeps_it_on_its_edge(tmp_path):
    """A pin on the bottom edge follows the edge down, and the fields of an all-vertical sheet
    are autoplaced vertically."""
    p = Proj(tmp_path)
    uid = p.sheet(p.root, p.child())
    _add_edge_pin(p.root, uid, "B", x_mm=40.64, y_mm=35.56, angle=270)
    project.modify_hierarchical_sheet(uid, str(p.root), height=20.32)
    group = sheets_of(p.root)[0]
    sheet = SS.read_sheet(group)
    pin = sheet.pins[0]
    assert pin.side == "bottom" and pin.y == sheet.y + sheet.h
    assert pins_of(group)["B"][:2] == (pin.x, pin.y)
    assert SS.field_at(group, b"Sheetname")[2] == 90
    assert fields_where_kicad_puts_them(p.root)


def _add_edge_pin(parent: Path, uid: str, name: str, x_mm: float, y_mm: float, angle: int):
    """Write a sheet pin at an exact place the tools cannot make (a top or bottom pin), the
    way KiCad writes one."""
    data = parent.read_bytes()
    sheet = next(s for s in kids(top(data), b"sheet") if sheet_uuid(s) == uid)
    justify = " ".join(SS.pin_justify(SS.SIDE_OF_ANGLE[angle]))
    node = (
        f'\t\t(pin "{name}" input\n\t\t\t(at {x_mm} {y_mm} {angle})\n'
        f'\t\t\t(uuid "00000000-0000-4000-8000-0000000000{angle // 10:02d}")\n'
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n"
        f"\t\t\t\t(justify {justify})\n\t\t\t)\n\t\t)\n"
    ).encode()
    at = sheet.end - 1
    # the sheet's last child is its instances block; the pin goes before it
    inst = SS._child(sheet, b"instances")
    cut = data.rindex(b"\n", 0, inst.start) + 1
    assert at > cut
    parent.write_bytes(data[:cut] + node + data[cut:])


@requires_cli
def test_resizing_keeps_wired_pins_joined(tmp_path):
    """A wire on a pin follows it when the edge moves: the netlist is the same before and
    after, for a right pin (width), a left pin and a bottom pin (height)."""
    p = Proj(tmp_path)
    child = p.child()
    uid = p.sheet(p.root, child, pins=[{"name": "A", "direction": "input"}])
    project.add_sheet_pin(uid, "Q", "output", str(p.root), "right")
    _add_edge_pin(p.root, uid, "B", x_mm=38.1, y_mm=35.56, angle=270)
    p.resistor_on(child, "R1", "A", 40)
    p.resistor_on(child, "R2", "Q", 70)
    p.resistor_on(child, "R3", "B", 100)
    # pin A came with a stub and a label named A from add_hierarchical_sheet
    for name, net in (("Q", "NQ"), ("B", "NB")):
        p.stub(p.root, uid, name, net)
    before = {r: net_of(p.root, r, "1") for r in ("R1", "R2", "R3")}
    assert before == {"R1": "A", "R2": "NQ", "R3": "NB"}
    project.modify_hierarchical_sheet(uid, str(p.root), width=38.1)
    project.modify_hierarchical_sheet(uid, str(p.root), height=22.86)
    assert {r: net_of(p.root, r, "1") for r in ("R1", "R2", "R3")} == before


def test_a_no_connect_on_a_moved_pin_moves_with_it(tmp_path):
    p = Proj(tmp_path)
    uid = p.sheet(p.root, p.child())
    project.add_sheet_pin(uid, "Q", "output", str(p.root), "right")
    group = sheets_of(p.root)[0]
    x, y, _ = pins_of(group)["Q"]
    data = p.root.read_bytes()
    cut = data.index(b"\t(sheet_instances")
    uid0 = "00000000-0000-4000-8000-000000000001"
    nc = f'\t(no_connect\n\t\t(at {x / IU} {y / IU})\n\t\t(uuid "{uid0}")\n\t)\n'
    p.root.write_bytes(data[:cut] + nc.encode() + data[cut:])
    project.modify_hierarchical_sheet(uid, str(p.root), width=38.1)
    group = sheets_of(p.root)[0]
    sheet = SS.read_sheet(group)
    (pin,) = sheet.pins
    assert (pin.x, pin.y) == (sheet.x + sheet.w, y)  # the pin followed the edge
    assert pins_of(group)["Q"][:2] == (pin.x, pin.y)
    (flag,) = kids(top(p.root.read_bytes()), b"no_connect")
    at = SS._child(flag, b"at")
    assert (SS.iu(at.items[1].value), SS.iu(at.items[2].value)) == (pin.x, pin.y)


def test_a_resize_below_the_minimum_is_refused(tmp_path):
    p = Proj(tmp_path)
    uid = p.sheet(p.root, p.child())
    for name in ("A", "B", "C"):
        project.add_sheet_pin(uid, name, "input", str(p.root))
    group = sheets_of(p.root)[0]
    sheet = SS.read_sheet(group)
    minimum = SS.min_height(sheet)
    before = p.root.read_bytes()
    with pytest.raises(ToolError, match=r"\d"):
        project.modify_hierarchical_sheet(uid, str(p.root), height=(minimum - 1270) / IU)
    assert p.root.read_bytes() == before
    with pytest.raises(ToolError):
        project.modify_hierarchical_sheet(uid, str(p.root), width=0)
    with pytest.raises(ToolError):
        project.modify_hierarchical_sheet(uid, str(p.root), height=-5)
    assert p.root.read_bytes() == before
    project.modify_hierarchical_sheet(uid, str(p.root), height=minimum / IU)
    assert SS.read_sheet(sheets_of(p.root)[0]).h == minimum


def test_an_unchanged_size_writes_nothing(tmp_path):
    p = Proj(tmp_path)
    uid = p.sheet(p.root, p.child())
    sheet = SS.read_sheet(sheets_of(p.root)[0])
    before, stamp = p.root.read_bytes(), p.root.stat().st_mtime_ns
    project.modify_hierarchical_sheet(uid, str(p.root), width=sheet.w / IU, height=sheet.h / IU)
    assert p.root.read_bytes() == before and p.root.stat().st_mtime_ns == stamp


# ---------------------------------------------------------------------------------------------
# add_hierarchical_sheet: validation, instances, fields, child symbols
# ---------------------------------------------------------------------------------------------

BAD_PINS = [
    pytest.param([{"name": "A", "direction": "banana"}], id="direction"),
    pytest.param([{"name": "A", "direction": "input|x"}], id="direction-bar"),
    pytest.param(
        [{"name": "A", "direction": "input"}, {"name": "", "direction": "input"}], id="empty-name"
    ),
]


@pytest.mark.parametrize("pins", BAD_PINS)
def test_add_hierarchical_sheet_refuses_before_writing(tmp_path, pins):
    p = Proj(tmp_path)
    child = p.child()
    before = (p.root.read_bytes(), child.read_bytes())
    with pytest.raises(ToolError, match=r"pin|direction"):
        p.sheet(p.root, child, pins=pins)
    assert (p.root.read_bytes(), child.read_bytes()) == before


def test_add_hierarchical_sheet_refuses_a_child_that_is_no_schematic(tmp_path):
    p = Proj(tmp_path)
    lib = tmp_path / "lib.kicad_sym"
    project.create_symbol_library(symbol_lib_path=str(lib))
    before = (p.root.read_bytes(), lib.read_bytes())
    with pytest.raises(ToolError):
        p.sheet(p.root, lib, pins=[{"name": "A", "direction": "input"}])
    assert (p.root.read_bytes(), lib.read_bytes()) == before


def test_add_hierarchical_sheet_refuses_a_sheet_inside_itself(tmp_path):
    p = Proj(tmp_path)
    mid = p.child("mid")
    p.sheet(p.root, mid, "Mid")
    leaf = p.child("leaf")
    p.sheet(mid, leaf, "Leaf")
    for parent, child in ((mid, mid), (leaf, mid), (leaf, p.root)):
        before = (parent.read_bytes(), child.read_bytes())
        with pytest.raises(ToolError, match=r"itself|inside|ancestor|contain"):
            p.sheet(parent, child, "Loop", pins=[{"name": "A", "direction": "input"}])
        assert (parent.read_bytes(), child.read_bytes()) == before


@pytest.mark.no_kicad_validation
def test_add_hierarchical_sheet_refuses_an_unreadable_root(tmp_path):
    p = Proj(tmp_path)
    child = p.child()
    other = p.child("other")
    p.root.write_bytes(b"not a schematic")
    before = (other.read_bytes(), child.read_bytes())
    with pytest.raises(ToolError):
        p.sheet(other, child, pins=[{"name": "A", "direction": "input"}])
    assert (other.read_bytes(), child.read_bytes()) == before


def test_the_new_sheet_is_instanced_at_its_parents_path_with_the_lowest_free_page(tmp_path):
    p = Proj(tmp_path)
    for name, x in (("A", 127.0), ("B", 25.4), ("C", 76.2)):
        p.sheet(p.root, p.child(name.lower()), name, x=x, y=25.4)
    root_uuid = file_uuid(p.root)
    got = {sheet_name(s): instance_paths(s) for s in sheets_of(p.root)}
    assert got == {
        "A": [("p", f"/{root_uuid}", "2")],
        "B": [("p", f"/{root_uuid}", "3")],
        "C": [("p", f"/{root_uuid}", "4")],
    }, got


@requires_cli
def test_kicad_cli_orders_the_sheets_by_the_pages_written(tmp_path):
    """Sheets placed right to left still list in page order: KiCad reads the page at the
    parent's path."""
    p = Proj(tmp_path)
    for name, x in (("A", 127.0), ("B", 25.4), ("C", 76.2)):
        p.sheet(p.root, p.child(name.lower()), name, x=x, y=25.4)
    assert sheet_order(p.root) == ["/", "/A/", "/B/", "/C/"]


def test_a_sheet_added_in_a_subsheet_is_instanced_at_the_subsheets_path(tmp_path):
    p = Proj(tmp_path)
    mid = p.child("mid")
    p.sheet(p.root, mid, "Mid")
    mid_block = sheet_uuid(sheets_of(p.root)[0])
    p.sheet(mid, p.child("leaf"), "Leaf")
    (leaf,) = sheets_of(mid)
    assert instance_paths(leaf) == [("p", f"/{file_uuid(p.root)}/{mid_block}", "3")]


def test_a_page_in_use_deeper_in_the_hierarchy_is_not_reused(tmp_path):
    p = Proj(tmp_path)
    mid = p.child("mid")
    p.sheet(p.root, mid, "Mid")  # page 2
    p.sheet(mid, p.child("leaf"), "Leaf")  # page 3
    p.sheet(p.root, p.child("late"), "Late")
    pages = {sheet_name(s): instance_paths(s)[0][2] for s in sheets_of(p.root)}
    assert pages == {"Mid": "2", "Late": "4"}


def test_the_new_sheet_has_the_fields_kicad_autoplaces_and_justified_pins(tmp_path):
    p = Proj(tmp_path)
    child = p.child()
    p.sheet(
        p.root,
        child,
        pins=[{"name": "A", "direction": "input"}, {"name": "B", "direction": "output"}],
    )
    assert fields_where_kicad_puts_them(p.root)
    group = sheets_of(p.root)[0]
    sheet = SS.read_sheet(group)
    assert [pin.justify for pin in sheet.pins] == [("left",), ("left",)]
    assert [(pin.x, pin.y) for pin in sheet.pins] == [
        (sheet.x, sheet.y + 25400),
        (sheet.x, sheet.y + 50800),
    ]
    for label in label_nodes(child.read_bytes()):
        assert SS._justify(SS._child(label, b"effects")) in (("right",), ("left", "bottom"))


def _instances_of_symbol(sym) -> dict[str, tuple[str, str]]:
    """{path: (reference, unit)} of a placed symbol's instances."""
    out = {}
    inst = SS._child(sym, b"instances")
    for pj in kids(inst, b"project") if inst is not None else []:
        for path in kids(pj, b"path"):
            unit = SS._child(path, b"unit")
            out[value(path)] = (
                value(SS._child(path, b"reference")),
                unit.items[1].value.decode() if unit is not None else "",
            )
    return out


@requires_cli
def test_child_symbols_get_an_instance_at_the_new_sheets_path(tmp_path):
    """A symbol placed in the child before the sheet existed has an entry for the child's own
    path only; adding the sheet gives it one at the path KiCad reads it by, with the unit it
    already has."""
    p = Proj(tmp_path)
    child = p.child()
    schematic.place_component(
        "Device:R", "R1", "1k", 50.8, 50.8, schematic_path=str(child), project_path=str(p.pro)
    )
    data = child.read_bytes()
    # unit 2 at the top level and in the entry, the way a placed second unit reads
    data = data.replace(b"(unit 1)", b"(unit 2)")
    child.write_bytes(data)
    before = _instances_of_symbol(kids(top(child.read_bytes()), b"symbol")[0])
    assert list(before.values()) == [("R1", "2")], before
    p.sheet(p.root, child, "C")
    sheet_block = sheet_uuid(sheets_of(p.root)[0])
    live = f"/{file_uuid(p.root)}/{sheet_block}"
    after = _instances_of_symbol(kids(top(child.read_bytes()), b"symbol")[0])
    assert after.get(live) == ("R1", "2"), after
    assert set(after) >= set(before)


@requires_cli
def test_child_symbols_are_in_the_netlist_after_the_sheet_is_added(tmp_path):
    """kicad-cli 9 leaves out the unconnected pins of a symbol that has no instance at the
    sheet's path; the connected pin of the same symbol is listed either way."""
    p = Proj(tmp_path)
    child = p.child()
    p.resistor_on(child, "R1", "P", 40)
    p.sheet(p.root, child, "C", pins=[{"name": "P", "direction": "input"}])
    listed = {pin for nodes in netlist_nodes(p.root).values() for pin in nodes}
    assert {("R1", "1"), ("R1", "2")} <= listed


# ---------------------------------------------------------------------------------------------
# Bug 40: create_project
# ---------------------------------------------------------------------------------------------


def test_create_project_writes_no_kicad_prl(tmp_path):
    out = project.create_project(directory=str(tmp_path / "fresh"), name="fresh")
    names = sorted(f.name for f in (tmp_path / "fresh").iterdir())
    assert names == ["fresh.kicad_pro", "fresh.kicad_sch"]
    assert "kicad_prl" not in out


def test_create_project_leaves_an_existing_prl_and_schematic_alone(tmp_path):
    d = tmp_path / "keep"
    d.mkdir()
    prl = d / "keep.kicad_prl"
    prl.write_bytes(b'{"board": {"active_layer": 31}, "meta": {"version": 3}}\n')
    sch = d / "keep.kicad_sch"
    project.create_schematic(schematic_path=str(sch))
    sch_bytes = sch.read_bytes()
    out = project.create_project(directory=str(d), name="keep")
    assert prl.read_bytes() == b'{"board": {"active_layer": 31}, "meta": {"version": 3}}\n'
    assert sch.read_bytes() == sch_bytes
    assert "kept" in out.lower() and "keep.kicad_sch" in out
    assert (d / "keep.kicad_pro").is_file()
