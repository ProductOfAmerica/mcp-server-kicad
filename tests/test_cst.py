"""Tests for the byte-preserving CST substrate and its first consumer, add_label.

Decision record: docs/adr-cst-substrate.md. The property under test is the
substrate invariant: bytes the caller did not edit reach the disk unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import reparse, requires_cli
from kiutils.items.schitems import Connection
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import _cst, schematic
from mcp_server_kicad._shared import _resolve_system_lib, _run_cli

FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"


def _pure_insertion(before: bytes, after: bytes) -> bool:
    """True if *after* is *before* with one contiguous run of bytes inserted."""
    if len(after) < len(before):
        return False
    p = 0
    while p < len(before) and before[p] == after[p]:
        p += 1
    s = 0
    while s < len(before) - p and before[-1 - s] == after[-1 - s]:
        s += 1
    return p + s >= len(before)


class TestRoundTrip:
    def test_native_fixture(self):
        data = FIXTURE.read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data

    def test_kiutils_written_file(self, scratch_sch):
        data = Path(scratch_sch).read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data

    @pytest.mark.skipif(
        _resolve_system_lib("Device") is None, reason="KiCad system libraries not installed"
    )
    def test_stock_device_library(self):
        lib = _resolve_system_lib("Device")
        assert lib is not None
        data = Path(lib).read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data

    def test_self_check(self):
        _cst.demo()  # escape codec, edits, splices, malformed refusal


class TestMalformed:
    def test_unmatched_close_raises(self):
        with pytest.raises(SyntaxError):
            _cst.parse(b"(kicad_sch))")

    def test_unclosed_open_raises(self):
        with pytest.raises(SyntaxError):
            _cst.parse(b"(kicad_sch (paper")


class TestAddLabelPreservation:
    def test_pure_insertion_and_unmodeled_tokens_survive(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        # Today's kiutils save path drops both of these tokens; the CST path
        # is the reason they survive. This assertion is the slice's point.
        assert b"(embedded_fonts" in before and b"generator_version" in before
        schematic.add_label("CST_NET", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert b"(embedded_fonts" in after and b"generator_version" in after
        sch = reparse(kicad_native_sch)
        assert "CST_NET" in [lbl.text for lbl in sch.labels]

    def test_hostile_text_roundtrips(self, kicad_native_sch):
        hostile = 'A(B "q" \\end'
        schematic.add_label(hostile, 60, 90, schematic_path=str(kicad_native_sch))
        sch = reparse(kicad_native_sch)
        # kiutils 1.4.8 does not decode \\ and \" the way KiCad does, so assert
        # via the CST's own KiCad-faithful decoder plus kiutils label count.
        tree = _cst.parse(kicad_native_sch.read_bytes())
        texts = [n.atoms[1].text for n in tree.lists[0].find_all("label")]
        assert hostile in texts
        assert len(sch.labels) == 2  # original TEST_NET + the hostile one

    def test_anchor_fallback_no_labels(self, empty_sch):
        schematic.add_label("FIRST", 50, 50, schematic_path=str(empty_sch))
        sch = reparse(empty_sch)
        assert "FIRST" in [lbl.text for lbl in sch.labels]

    def test_rotation_and_position_survive_kiutils(self, kicad_native_sch):
        schematic.add_label("ROT", 96.19, 100.5, rotation=90, schematic_path=str(kicad_native_sch))
        sch = reparse(kicad_native_sch)
        lbl = next(x for x in sch.labels if x.text == "ROT")
        assert lbl.position.X == pytest.approx(96.19)
        assert lbl.position.Y == pytest.approx(100.5)
        assert lbl.position.angle == 90


class TestAddFamilyPreservation:
    """Slice 3: global_label, hierarchical_label, text, junctions via the same splice."""

    def test_global_label(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        schematic.add_global_label(
            "GNET", 60, 90, rotation=90, shape="bidirectional", schematic_path=str(kicad_native_sch)
        )
        assert _pure_insertion(before, kicad_native_sch.read_bytes())
        gl = next(g for g in reparse(kicad_native_sch).globalLabels if g.text == "GNET")
        assert gl.shape == "bidirectional"
        assert gl.position.angle == 90

    def test_hierarchical_label(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        schematic.add_hierarchical_label(
            "HNET", "output", 25.4, 30, schematic_path=str(kicad_native_sch)
        )
        assert _pure_insertion(before, kicad_native_sch.read_bytes())
        hl = next(h for h in reparse(kicad_native_sch).hierarchicalLabels if h.text == "HNET")
        assert hl.shape == "output"
        assert hl.position.X == pytest.approx(25.4)
        with pytest.raises(ToolError, match="invalid shape"):
            schematic.add_hierarchical_label(
                "Z", "sideways", 10, 10, schematic_path=str(kicad_native_sch)
            )

    def test_text(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        schematic.add_text("note here", 100.123456, 50, schematic_path=str(kicad_native_sch))
        assert _pure_insertion(before, kicad_native_sch.read_bytes())
        t = next(t for t in reparse(kicad_native_sch).texts if t.text == "note here")
        # add_text never rounded coordinates; the quirk is preserved.
        assert t.position.X == pytest.approx(100.123456)

    def test_junctions_two_points_one_write(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        schematic.add_junctions(
            [{"x": 50.8, "y": 50.8}, {"x": 96.19, "y": 100.5}],
            schematic_path=str(kicad_native_sch),
        )
        assert _pure_insertion(before, kicad_native_sch.read_bytes())
        sch = reparse(kicad_native_sch)
        assert len(sch.junctions) == 2
        # File order is list order: the last point supplied is junctions[-1].
        assert sch.junctions[-1].position.X == pytest.approx(96.19)

    def test_junctions_all_validated_before_any_write(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        with pytest.raises(ToolError, match="outside"):
            schematic.add_junctions(
                [{"x": 50, "y": 50}, {"x": 9999, "y": 50}],
                schematic_path=str(kicad_native_sch),
            )
        assert kicad_native_sch.read_bytes() == before


def _span_preserved(before: bytes, after: bytes) -> bool:
    """True when all bytes of the shorter side survive as a common prefix+suffix,
    i.e. the change is one contiguous span (insertion, deletion, or substitution)."""
    p = 0
    lo = min(len(before), len(after))
    while p < lo and before[p] == after[p]:
        p += 1
    s = 0
    while s < lo - p and before[-1 - s] == after[-1 - s]:
        s += 1
    return p + s >= lo


class TestRemoveFamilyPreservation:
    """Slice 4: removal/modify via CST one-span deletions and substitutions."""

    def test_remove_label_local_and_global(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_global_label("MIX", 60, 90, schematic_path=p)
        schematic.add_label("MIX", 60, 92, schematic_path=p)
        result = schematic.remove_label("MIX", schematic_path=p)
        assert "1 label(s)" in result and "1 global label(s)" in result
        sch = reparse(kicad_native_sch)
        assert not any(lbl.text == "MIX" for lbl in sch.labels)
        assert not any(gl.text == "MIX" for gl in sch.globalLabels)

    def test_remove_label_position_filter_no_over_deletion(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_label("NEAR", 49.61, 50, schematic_path=p)
        schematic.add_label("NEAR", 49.77, 50, schematic_path=p)
        result = schematic.remove_label("NEAR", x=49.77, y=50, schematic_path=p)
        assert "Removed 1" in result
        remaining = [lbl for lbl in reparse(kicad_native_sch).labels if lbl.text == "NEAR"]
        assert len(remaining) == 1
        assert abs(remaining[0].position.X - 49.61) < 0.01

    def test_remove_junction_single_span(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_junctions([{"x": 50.8, "y": 50.8}], schematic_path=p)
        before = kicad_native_sch.read_bytes()
        assert "Removed junction" in schematic.remove_junction(50.8, 50.8, schematic_path=p)
        after = kicad_native_sch.read_bytes()
        assert len(after) < len(before)
        assert _span_preserved(before, after)
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_junction(50.8, 50.8, schematic_path=p)
        assert kicad_native_sch.read_bytes() == after

    def test_remove_text(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_text("gone", 60, 90, schematic_path=p)
        before = kicad_native_sch.read_bytes()
        assert "Removed 1 text(s)" in schematic.remove_text("gone", schematic_path=p)
        assert _span_preserved(before, kicad_native_sch.read_bytes())
        assert not any(t.text == "gone" for t in reparse(kicad_native_sch).texts)

    def test_remove_hierarchical_label_by_uuid(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_hierarchical_label("DUP", "input", 60, 90, schematic_path=p)
        schematic.add_hierarchical_label("DUP", "output", 60, 92, schematic_path=p)
        second_uuid = reparse(kicad_native_sch).hierarchicalLabels[1].uuid
        result = schematic.remove_hierarchical_label("DUP", schematic_path=p, uuid=second_uuid)
        assert "Removed hierarchical label 'DUP'" in result
        remaining = reparse(kicad_native_sch).hierarchicalLabels
        assert len(remaining) == 1
        assert remaining[0].shape == "input"

    def test_modify_hierarchical_label_substitution(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_hierarchical_label("VIN", "input", 25.4, 30, schematic_path=p)
        before = kicad_native_sch.read_bytes()
        result = schematic.modify_hierarchical_label(
            "VIN", schematic_path=p, new_text="VIN_PROT", new_shape="output"
        )
        assert "VIN_PROT" in result and "output" in result
        hl = reparse(kicad_native_sch).hierarchicalLabels[0]
        assert hl.text == "VIN_PROT"
        assert hl.shape == "output"
        # Two atom substitutions land inside the label node: everything before
        # and after that node is untouched.
        after = kicad_native_sch.read_bytes()
        assert after != before
        assert b"generator_version" in after and b"(embedded_fonts" in after


def _wires_of(sch):
    return [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"]


class TestWiresPreservation:
    """Slice 5: add_wires/remove_wire via CST splices. Fixture wire: (50,100)-(80,100)."""

    def test_add_wires_pure_insertion(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        n = len(_wires_of(reparse(kicad_native_sch)))
        schematic.add_wires(
            [{"x1": 96.19, "y1": 20.5, "x2": 103.81, "y2": 20.5}],
            schematic_path=str(kicad_native_sch),
        )
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert b"generator_version" in after and b"(embedded_fonts" in after
        wires = _wires_of(reparse(kicad_native_sch))
        assert len(wires) == n + 1
        new = wires[-1]
        assert (new.points[0].X, new.points[0].Y) == (96.19, 20.5)
        assert (new.points[1].X, new.points[1].Y) == (103.81, 20.5)

    def test_auto_junction_on_t_connection(self, kicad_native_sch):
        schematic.add_wires(
            [{"x1": 65, "y1": 80, "x2": 65, "y2": 100}],
            schematic_path=str(kicad_native_sch),
        )
        sch = reparse(kicad_native_sch)
        assert len(_wires_of(sch)) == 2
        assert (65, 100) in [(j.position.X, j.position.Y) for j in sch.junctions]

    def test_no_junction_at_wire_endpoint(self, kicad_native_sch):
        schematic.add_wires(
            [{"x1": 80, "y1": 80, "x2": 80, "y2": 100}],
            schematic_path=str(kicad_native_sch),
        )
        sch = reparse(kicad_native_sch)
        assert len(_wires_of(sch)) == 2
        assert sch.junctions == []

    def test_remove_wire_reversed_and_span(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_wires([{"x1": 20, "y1": 20, "x2": 30, "y2": 20}], schematic_path=p)
        before = kicad_native_sch.read_bytes()
        n = len(_wires_of(reparse(kicad_native_sch)))
        assert "Removed 1 wire(s)." == schematic.remove_wire(30, 20, 20, 20, schematic_path=p)
        after = kicad_native_sch.read_bytes()
        assert _span_preserved(before, after)
        assert len(_wires_of(reparse(kicad_native_sch))) == n - 1
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_wire(30, 20, 20, 20, schematic_path=p)
        assert kicad_native_sch.read_bytes() == after

    def test_add_wires_all_validated_before_any_write(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        with pytest.raises(ToolError, match="outside the sheet boundary"):
            schematic.add_wires(
                [
                    {"x1": 20, "y1": 20, "x2": 30, "y2": 20},
                    {"x1": 9999, "y1": 20, "x2": 30, "y2": 20},
                ],
                schematic_path=str(kicad_native_sch),
            )
        assert kicad_native_sch.read_bytes() == before


class TestHybridRoutingPreservation:
    """Slices 6+7: routing trio on the substrate; slice 7 moved the pin reads
    to a CST walk, so the trio is guard-free and single-parse.
    Fixture R1 at (100,100): pin 1 at (100, 96.19), pin 2 at (100, 103.81)."""

    def test_no_connect_pure_insertion(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        result = schematic.no_connect_pin("R1", "1", schematic_path=str(kicad_native_sch))
        assert result == "No-connect on R1:1 at (100.0, 96.19)"
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert b"generator_version" in after and b"(embedded_fonts" in after
        nc = reparse(kicad_native_sch).noConnects[0]
        assert (nc.position.X, nc.position.Y) == (100.0, 96.19)

    def test_no_connect_idempotent_no_write(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.no_connect_pin("R1", "1", schematic_path=p)
        mid = kicad_native_sch.read_bytes()
        assert "already present" in schematic.no_connect_pin("R1", "1", schematic_path=p)
        assert kicad_native_sch.read_bytes() == mid

    def test_remove_no_connect_span(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.no_connect_pin("R1", "1", schematic_path=p)
        before = kicad_native_sch.read_bytes()
        assert "Removed 1 no-connect" in schematic.remove_no_connect("R1", "1", schematic_path=p)
        after = kicad_native_sch.read_bytes()
        assert _span_preserved(before, after)
        assert reparse(kicad_native_sch).noConnects == []
        with pytest.raises(ToolError, match="No no-connect"):
            schematic.remove_no_connect("R1", "1", schematic_path=p)
        assert kicad_native_sch.read_bytes() == after

    def test_connect_pins_straight_and_label(self, kicad_native_sch):
        p = str(kicad_native_sch)
        result = schematic.connect_pins("R1", "1", "R1", "2", schematic_path=p)
        assert result == "Connected R1:1 -> R1:2 via 1 wire segment"
        after = kicad_native_sch.read_bytes()
        assert b"generator_version" in after and b"(embedded_fonts" in after
        assert _cst.serialize(_cst.parse(after)) == after
        sch = reparse(kicad_native_sch)
        new = _wires_of(sch)[-1]
        assert (new.points[0].X, new.points[0].Y) == (100.0, 96.19)
        assert (new.points[1].X, new.points[1].Y) == (100.0, 103.81)
        assert any(lbl.text == "Net-(R1-1)" for lbl in sch.labels)

    @pytest.mark.no_kicad_validation
    def test_future_version_file_routing_tools_work(self, kicad_native_sch):
        # Slice 7: pin reads went CST, so the routing trio is guard-free.
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            schematic.get_schematic_summary(schematic_path=p)
        before = kicad_native_sch.read_bytes()
        schematic.no_connect_pin("R1", "1", schematic_path=p)
        assert _pure_insertion(before, kicad_native_sch.read_bytes())
        assert "Removed 1 no-connect" in schematic.remove_no_connect("R1", "1", schematic_path=p)
        schematic.connect_pins("R1", "1", "R1", "2", schematic_path=p)
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert len(root.find_all("wire")) == 2
        assert "Net-(R1-1)" in [n.atoms[1].text for n in root.find_all("label")]

    def test_pin_pos_cst_matches_kiutils(self, scratch_sch, kicad_native_sch):
        # Differential oracle: the CST walk must agree with the kiutils path
        # exactly, on both file shapes, through rotation and mirror.
        from mcp_server_kicad.schematic import _get_pin_pos, _get_pin_pos_cst

        for path in (scratch_sch, kicad_native_sch):
            sch = reparse(path)
            root = _cst.parse(path.read_bytes()).lists[0]
            for pin in ("1", "2"):
                assert _get_pin_pos_cst(root, "R1", pin) == _get_pin_pos(sch, "R1", pin), (
                    path.name,
                    pin,
                )
        # Rotation via a real tool, then mirror via a hand-spliced token; both
        # parsers reread the same file so the comparison stays apples-to-apples.
        schematic.move_component("R1", 90, 80, rotation=90, schematic_path=str(scratch_sch))
        for mutate_mirror in (False, True):
            if mutate_mirror:
                data = scratch_sch.read_bytes()
                assert b"(mirror" not in data
                scratch_sch.write_bytes(data.replace(b"(unit 1)", b"(mirror x) (unit 1)", 1))
            sch = reparse(scratch_sch)
            root = _cst.parse(scratch_sch.read_bytes()).lists[0]
            for pin in ("1", "2"):
                assert _get_pin_pos_cst(root, "R1", pin) == _get_pin_pos(sch, "R1", pin), (
                    mutate_mirror,
                    pin,
                )
        root = _cst.parse(scratch_sch.read_bytes()).lists[0]
        with pytest.raises(ValueError, match="Component X99 not found"):
            _get_pin_pos_cst(root, "X99", "1")
        with pytest.raises(ValueError, match="Pin 'NOPE' not found on R1"):
            _get_pin_pos_cst(root, "R1", "NOPE")


def _power_in_sch(tmp_path):
    """Schematic with a placed power_in VCC symbol #PWR01 at (100, 100)."""
    import uuid as _uuid

    from conftest import build_power_symbol, new_schematic
    from kiutils.items.common import Effects, Font, Position, Property
    from kiutils.items.schitems import SchematicSymbol

    sch = new_schematic()
    sch.libSymbols.append(build_power_symbol("VCC", "power_in"))
    vcc = SchematicSymbol()
    vcc.libId = "power:VCC"
    vcc.libName = "VCC"
    vcc.position = Position(X=100, Y=100, angle=0)
    vcc.uuid = str(_uuid.uuid4())
    vcc.unit = 1
    vcc.inBom = False
    vcc.onBoard = True
    vcc.properties = [
        Property(
            key="Reference",
            value="#PWR01",
            id=0,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=100, Y=96.19, angle=0),
        ),
        Property(
            key="Value",
            value="VCC",
            id=1,
            effects=Effects(font=Font(height=1.27, width=1.27)),
            position=Position(X=100, Y=103.81, angle=0),
        ),
    ]
    vcc.pins = {"1": str(_uuid.uuid4())}
    sch.schematicSymbols.append(vcc)
    path = tmp_path / "pwr_cst.kicad_sch"
    sch.filePath = str(path)
    sch.to_file()
    return path


class TestWirePinsToNetPreservation:
    """Slice 8: wire_pins_to_net on the substrate; PWR_FLAG is the first
    symbol emission (verbatim system-lib copy + fixed placed template)."""

    def test_stub_and_label_preservation(self, kicad_native_sch):
        p = str(kicad_native_sch)
        result = schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="NETX",
            direction="up",
            schematic_path=p,
        )
        assert result == "Wired 1 pins to 'NETX'."
        after = kicad_native_sch.read_bytes()
        assert b"generator_version" in after and b"(embedded_fonts" in after
        assert _cst.serialize(_cst.parse(after)) == after
        sch = reparse(kicad_native_sch)
        new = _wires_of(sch)[-1]
        assert (new.points[0].X, new.points[0].Y) == (100.0, 96.19)
        assert (new.points[1].X, new.points[1].Y) == (100.0, 93.65)
        lbl = next(lbl for lbl in sch.labels if lbl.text == "NETX")
        assert (lbl.position.X, lbl.position.Y, lbl.position.angle) == (100.0, 93.65, 90)

    def test_auto_pwr_flag_cst(self, tmp_path):
        path = _power_in_sch(tmp_path)
        schematic.wire_pins_to_net(
            pins=[{"reference": "#PWR01", "pin": "1"}],
            label_text="VCC_NET",
            schematic_path=str(path),
        )
        sch = reparse(path)
        flags = [
            s
            for s in sch.schematicSymbols
            if any(p.key == "Value" and p.value == "PWR_FLAG" for p in s.properties)
        ]
        assert len(flags) == 1
        flag = flags[0]
        assert any(p.key == "Reference" and p.value == "#FLG01" for p in flag.properties)
        assert flag.instances, "instances block must be present for annotation"
        assert any(ls.entryName == "PWR_FLAG" for ls in sch.libSymbols)

    def test_pwr_flag_lib_copy_verbatim(self, tmp_path):
        from mcp_server_kicad._shared import _extract_raw_symbol, _resolve_system_lib

        lib_path = _resolve_system_lib("power")
        if lib_path is None:
            pytest.skip("no KiCad system symbol library on this host")
        path = _power_in_sch(tmp_path)
        schematic.wire_pins_to_net(
            pins=[{"reference": "#PWR01", "pin": "1"}],
            label_text="VCC_NET",
            schematic_path=str(path),
        )
        want = _extract_raw_symbol(lib_path, "PWR_FLAG")
        got = _extract_raw_symbol(str(path), "power:PWR_FLAG")
        assert want is not None and got is not None
        # Verbatim except the name atom, which gains the lib prefix (the way
        # KiCad imports symbols; a bare name segfaults kicad-cli 9.0 on load).
        assert got.replace('"power:PWR_FLAG"', '"PWR_FLAG"', 1) == want

    @pytest.mark.no_kicad_validation
    def test_future_version_file_wire_pins_to_net_works(self, kicad_native_sch):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            schematic.get_schematic_summary(schematic_path=p)
        assert "Wired 1 pins" in schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}], label_text="K10NET", schematic_path=p
        )
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert "K10NET" in [n.atoms[1].text for n in root.find_all("label")]


def _confined(before: bytes, after: bytes, limit: int = 200) -> bool:
    """True when all differences sit inside one contiguous span of <= limit
    bytes on each side: an in-place substitution with everything else intact."""
    p = 0
    lo = min(len(before), len(after))
    while p < lo and before[p] == after[p]:
        p += 1
    s = 0
    while s < lo - p and before[-1 - s] == after[-1 - s]:
        s += 1
    return (len(before) - p - s) <= limit and (len(after) - p - s) <= limit


class TestSymbolFamilyPreservation:
    """Slice 9: in-place symbol-family tools on the substrate."""

    def test_add_lib_symbol_pure_insertion(self, kicad_native_sch, scratch_power_lib):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        result = schematic.add_lib_symbol(str(scratch_power_lib), "VCC", schematic_path=p)
        assert result == "Added 'VCC' to lib_symbols."
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert any(ls.entryName == "VCC" for ls in reparse(kicad_native_sch).libSymbols)
        with pytest.raises(ToolError, match="already in lib_symbols"):
            schematic.add_lib_symbol(str(scratch_power_lib), "VCC", schematic_path=p)
        assert kicad_native_sch.read_bytes() == after
        with pytest.raises(ToolError, match="not found in"):
            schematic.add_lib_symbol(str(scratch_power_lib), "NOPE", schematic_path=p)
        assert kicad_native_sch.read_bytes() == after

    def test_move_component_substitution(self, kicad_native_sch):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        assert "Moved R1" in schematic.move_component("R1", 63.5, 63.5, schematic_path=p)
        after = kicad_native_sch.read_bytes()
        assert _confined(before, after, limit=40)
        sym = reparse(kicad_native_sch).schematicSymbols[0]
        assert (sym.position.X, sym.position.Y, sym.position.angle) == (63.5, 63.5, 0)
        schematic.move_component("R1", 63.5, 63.5, rotation=90, schematic_path=p)
        assert reparse(kicad_native_sch).schematicSymbols[0].position.angle == 90

    def test_set_component_property_existing_and_new(self, kicad_native_sch):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        assert "Set R1.Value = 22K" in schematic.set_component_property(
            "R1", "Value", "22K", schematic_path=p
        )
        after = kicad_native_sch.read_bytes()
        assert _confined(before, after, limit=20)
        sym = reparse(kicad_native_sch).schematicSymbols[0]
        assert any(pr.key == "Value" and pr.value == "22K" for pr in sym.properties)
        assert "(new property)" in schematic.set_component_property(
            "R1", "MPN", "RC0805", schematic_path=p
        )
        sym = reparse(kicad_native_sch).schematicSymbols[0]
        mpn = next(pr for pr in sym.properties if pr.key == "MPN")
        assert mpn.value == "RC0805"
        schematic.set_component_property("R1", "Reference", "R9", schematic_path=p)
        sym = reparse(kicad_native_sch).schematicSymbols[0]
        assert sym.instances[0].paths[0].reference == "R9"

    def test_remove_component_span(self, kicad_native_sch):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        assert schematic.remove_component("R1", schematic_path=p) == "Removed R1"
        after = kicad_native_sch.read_bytes()
        assert _span_preserved(before, after)
        assert reparse(kicad_native_sch).schematicSymbols == []
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_component("R1", schematic_path=p)
        assert kicad_native_sch.read_bytes() == after

    def test_set_page_size_swap(self, kicad_native_sch):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        assert "A3" in schematic.set_page_size("A3", schematic_path=p)
        mid = kicad_native_sch.read_bytes()
        assert _confined(before, mid, limit=40)
        assert reparse(kicad_native_sch).paper.paperSize == "A3"
        result = schematic.set_page_size(
            "User", width=200, height=150, portrait=True, schematic_path=p
        )
        assert "portrait" in result
        assert _confined(mid, kicad_native_sch.read_bytes(), limit=60)
        paper = reparse(kicad_native_sch).paper
        assert (paper.paperSize, paper.width, paper.height) == ("User", 200, 150)
        after = kicad_native_sch.read_bytes()
        assert b"generator_version" in after and b"(embedded_fonts" in after


def _kicad_cli_major() -> int:
    result = _run_cli(["version"], check=False)
    try:
        return int(result.stdout.strip().split(".")[0])
    except (ValueError, IndexError):
        return 0


class TestGuardRelax:
    @pytest.mark.no_kicad_validation
    def test_future_version_file_add_label_works_other_tools_refuse(self, kicad_native_sch):
        # Simulate a KiCad-10-saved schematic: bump only the version claim.
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            schematic.get_schematic_summary(schematic_path=str(kicad_native_sch))
        before = kicad_native_sch.read_bytes()
        schematic.add_label("RELAXED", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        tree = _cst.parse(after)
        assert "RELAXED" in [n.atoms[1].text for n in tree.lists[0].find_all("label")]

    @pytest.mark.no_kicad_validation
    def test_future_version_file_whole_add_family_works(self, kicad_native_sch):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        schematic.add_global_label("G", 60, 90, schematic_path=p)
        schematic.add_hierarchical_label("H", "input", 60, 92, schematic_path=p)
        schematic.add_text("T", 60, 94, schematic_path=p)
        schematic.add_junctions([{"x": 60, "y": 96}], schematic_path=p)
        after = kicad_native_sch.read_bytes()
        assert len(after) > len(before)
        tree = _cst.parse(after)
        root = tree.lists[0]
        for token in ("global_label", "hierarchical_label", "text", "junction"):
            assert root.find(token) is not None, token

    @pytest.mark.no_kicad_validation
    def test_future_version_file_remove_and_modify_work(self, kicad_native_sch):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        schematic.add_label("R1X", 60, 90, schematic_path=p)
        schematic.add_hierarchical_label("H1X", "input", 60, 92, schematic_path=p)
        schematic.add_junctions([{"x": 60, "y": 96}], schematic_path=p)
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            schematic.get_schematic_summary(schematic_path=p)
        assert "1 label(s)" in schematic.remove_label("R1X", schematic_path=p)
        assert "output" in schematic.modify_hierarchical_label(
            "H1X", schematic_path=p, new_shape="output"
        )
        assert "Removed junction" in schematic.remove_junction(60, 96, schematic_path=p)
        assert "Added 1 wires" in schematic.add_wires(
            [{"x1": 20, "y1": 20, "x2": 30, "y2": 20}], schematic_path=p
        )
        assert "Removed 1 wire(s)." in schematic.remove_wire(20, 20, 30, 20, schematic_path=p)

    @pytest.mark.no_kicad_validation
    def test_future_version_file_symbol_tools_work(self, kicad_native_sch, scratch_power_lib):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            schematic.get_schematic_summary(schematic_path=p)
        assert "Moved R1" in schematic.move_component("R1", 63.5, 63.5, schematic_path=p)
        assert "22K" in schematic.set_component_property("R1", "Value", "22K", schematic_path=p)
        assert "A3" in schematic.set_page_size("A3", schematic_path=p)
        assert "VCC" in schematic.add_lib_symbol(str(scratch_power_lib), "VCC", schematic_path=p)
        assert schematic.remove_component("R1", schematic_path=p) == "Removed R1"
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert root.find("symbol") is None  # placed R1 gone; lib entries stay nested
        assert "VCC" in [s.atoms[1].text for s in root.find("lib_symbols").find_all("symbol")]


@requires_cli
class TestKicad10E2E:
    def test_add_label_on_real_kicad10_schematic(self, kicad_native_sch):
        if _kicad_cli_major() < 10:
            pytest.skip("needs kicad-cli 10+ to mint a current-format schematic")
        _run_cli(["sch", "upgrade", "--force", str(kicad_native_sch)])
        before = kicad_native_sch.read_bytes()
        assert b"(version 2026" in before[:80], before[:80]
        schematic.add_label("K10_NET", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        tree = _cst.parse(after)
        assert "K10_NET" in [n.atoms[1].text for n in tree.lists[0].find_all("label")]
        # The autouse _validate_kicad_output fixture then runs this runner's
        # kicad-cli ERC over the edited KiCad-10 file: the real acceptance gate.

    def _mint(self, path):
        if _kicad_cli_major() < 10:
            pytest.skip("needs kicad-cli 10+ to mint a current-format schematic")
        _run_cli(["sch", "upgrade", "--force", str(path)])
        before = path.read_bytes()
        assert b"(version 2026" in before[:80], before[:80]
        return before

    def test_global_label_on_real_kicad10(self, kicad_native_sch):
        before = self._mint(kicad_native_sch)
        schematic.add_global_label("K10_G", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert _cst.parse(after).lists[0].find("global_label") is not None

    def test_hierarchical_label_on_real_kicad10(self, kicad_native_sch):
        before = self._mint(kicad_native_sch)
        schematic.add_hierarchical_label(
            "K10_H", "input", 60, 90, schematic_path=str(kicad_native_sch)
        )
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert _cst.parse(after).lists[0].find("hierarchical_label") is not None

    def test_text_on_real_kicad10(self, kicad_native_sch):
        before = self._mint(kicad_native_sch)
        schematic.add_text("K10 note", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert _cst.parse(after).lists[0].find("text") is not None

    def test_junctions_on_real_kicad10(self, kicad_native_sch):
        before = self._mint(kicad_native_sch)
        schematic.add_junctions([{"x": 60, "y": 90}], schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert _cst.parse(after).lists[0].find("junction") is not None

    def test_remove_label_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        schematic.add_label("K10_RM", 60, 90, schematic_path=p)
        mid = kicad_native_sch.read_bytes()
        assert "1 label(s)" in schematic.remove_label("K10_RM", schematic_path=p)
        after = kicad_native_sch.read_bytes()
        assert _span_preserved(mid, after)
        assert "K10_RM" not in [
            n.atoms[1].text for n in _cst.parse(after).lists[0].find_all("label")
        ]

    def test_modify_hierarchical_label_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        schematic.add_hierarchical_label("K10_M", "input", 60, 90, schematic_path=p)
        schematic.modify_hierarchical_label(
            "K10_M", schematic_path=p, new_text="K10_M2", new_shape="output"
        )
        node = _cst.parse(kicad_native_sch.read_bytes()).lists[0].find("hierarchical_label")
        assert node.atoms[1].text == "K10_M2"
        assert node.find("shape").atoms[1].text == "output"

    def test_wires_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        # T-connects onto the fixture wire (50,100)-(80,100): auto-junction path
        # runs against a real KiCad 10 file.
        schematic.add_wires([{"x1": 65, "y1": 80, "x2": 65, "y2": 100}], schematic_path=p)
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert len(root.find_all("wire")) == 2
        assert root.find("junction") is not None
        assert "Removed 1 wire(s)." in schematic.remove_wire(65, 80, 65, 100, schematic_path=p)
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert len(root.find_all("wire")) == 1
        assert root.find("junction") is not None

    def test_routing_tools_on_real_kicad10(self, kicad_native_sch):
        # sch upgrade rewrites lib_symbols into the KiCad 10 shape, so this is
        # the live portability measurement for the CST pin walk and no_connect.
        before = self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        assert "at (100.0, 96.19)" in schematic.no_connect_pin("R1", "1", schematic_path=p)
        mid = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, mid)
        assert _cst.parse(mid).lists[0].find("no_connect") is not None
        assert "Removed 1 no-connect" in schematic.remove_no_connect("R1", "1", schematic_path=p)
        assert _span_preserved(mid, kicad_native_sch.read_bytes())
        schematic.connect_pins("R1", "1", "R1", "2", schematic_path=p)
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert len(root.find_all("wire")) == 2
        assert "Net-(R1-1)" in [n.atoms[1].text for n in root.find_all("label")]

    def test_wire_pins_to_net_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        result = schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}], label_text="K10NET", schematic_path=p
        )
        assert result == "Wired 1 pins to 'K10NET'."
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert len(root.find_all("wire")) == 2
        assert "K10NET" in [n.atoms[1].text for n in root.find_all("label")]

    def test_symbol_tools_on_real_kicad10(self, kicad_native_sch, scratch_power_lib):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        assert "Moved R1" in schematic.move_component("R1", 63.5, 63.5, schematic_path=p)
        assert "22K" in schematic.set_component_property("R1", "Value", "22K", schematic_path=p)
        assert "A3" in schematic.set_page_size("A3", schematic_path=p)
        assert "VCC" in schematic.add_lib_symbol(str(scratch_power_lib), "VCC", schematic_path=p)
        assert schematic.remove_component("R1", schematic_path=p) == "Removed R1"
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert root.find("symbol") is None
        assert root.find("paper").atoms[1].text == "A3"
