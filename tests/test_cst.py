"""Tests for the byte-preserving CST substrate and its first consumer, add_label.

Decision record: docs/adr-cst-substrate.md. The property under test is the
substrate invariant: bytes the caller did not edit reach the disk unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import _confined, _pure_insertion, _span_preserved, reparse, requires_cli
from kiutils.items.schitems import Connection
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import _cst, project, schematic
from mcp_server_kicad._shared import _node_uuid, _resolve_system_lib, _run_cli

FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"


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
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
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
    from conftest import make_power_sch

    return Path(make_power_sch(tmp_path))


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
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
        assert "Wired 1 pins" in schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}], label_text="K10NET", schematic_path=p
        )
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert "K10NET" in [n.atoms[1].text for n in root.find_all("label")]


class TestPlaceComponentPreservation:
    """Slice 10: place_component and the root-instance helpers on the substrate."""

    def test_place_from_custom_lib_pure_insertions(self, kicad_native_sch, scratch_sym_lib):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        result = schematic.place_component(
            lib_id="Test:TestPart",
            reference="U1",
            value="TP",
            x=150,
            y=150,
            symbol_lib_path=str(scratch_sym_lib),
            schematic_path=p,
        )
        assert "Placed U1 (TP) at (149.86, 149.86)" == result
        after = kicad_native_sch.read_bytes()
        assert b"generator_version" in after and b"(embedded_fonts" in after
        assert _cst.serialize(_cst.parse(after)) == after
        assert before[: len(before) // 3] == after[: len(before) // 3]
        sch = reparse(kicad_native_sch)
        u1 = next(
            s
            for s in sch.schematicSymbols
            if any(pr.key == "Reference" and pr.value == "U1" for pr in s.properties)
        )
        assert u1.libId == "Test:TestPart"
        assert u1.position.X == 149.86 and u1.position.Y == 149.86
        assert set(u1.pins) == {"1", "2"}
        assert u1.instances and u1.instances[0].paths[0].reference == "U1"
        assert any(pr.key == "Value" and pr.value == "TP" for pr in u1.properties)

    def test_place_reuses_existing_lib_symbol(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.place_component(
            lib_id="Device:R", reference="R2", value="4.7K", x=60, y=60, schematic_path=p
        )
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        entries = root.find("lib_symbols").find_all("symbol")
        assert len(entries) == 1  # reused, not duplicated
        from mcp_server_kicad._shared import _sym_property_cst

        r2 = next(s for s in root.find_all("symbol") if _sym_property_cst(s, "Reference") == "R2")
        assert r2.find("lib_name").atoms[1].text == "Device:R"
        assert r2.find("lib_id").atoms[1].text == "Device:R"

    def test_place_missing_symbol_error_bytes_untouched(self, kicad_native_sch, scratch_sym_lib):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        with pytest.raises(ToolError, match="not found in Test library"):
            schematic.place_component(
                lib_id="Test:Nonexistent",
                reference="U9",
                value="X",
                x=60,
                y=60,
                symbol_lib_path=str(scratch_sym_lib),
                schematic_path=p,
            )
        assert kicad_native_sch.read_bytes() == before

    def test_place_rotation_and_mirror_differential(self, kicad_native_sch, scratch_sym_lib):
        from mcp_server_kicad.schematic import _get_pin_pos, _get_pin_pos_cst

        p = str(kicad_native_sch)
        schematic.place_component(
            lib_id="Test:TestPart",
            reference="U1",
            value="TP",
            x=63.5,
            y=63.5,
            rotation=90,
            mirror="x",
            symbol_lib_path=str(scratch_sym_lib),
            schematic_path=p,
        )
        sch = reparse(kicad_native_sch)
        u1 = next(
            s
            for s in sch.schematicSymbols
            if any(pr.key == "Reference" and pr.value == "U1" for pr in s.properties)
        )
        assert u1.position.angle == 90
        assert u1.mirror == "x"
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        for pin in ("IN", "OUT"):
            assert _get_pin_pos_cst(root, "U1", pin) == _get_pin_pos(sch, "U1", pin), pin

    def test_root_symbol_instances_upsert_and_remove(self, kicad_native_sch):
        # A .kicad_pro sibling makes this file its own root: the helpers write
        # a symbol_instances section here via the CST now.
        pro = kicad_native_sch.with_suffix(".kicad_pro")
        pro.write_text("{}")
        p = str(kicad_native_sch)
        schematic.place_component(
            lib_id="Device:R", reference="R2", value="4.7K", x=60, y=60, schematic_path=p
        )
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        si = root.find("symbol_instances")
        assert si is not None
        entry = si.find("path")
        assert entry.find("reference").atoms[1].text == "R2"
        assert entry.find("value").atoms[1].text == "4.7K"
        schematic.set_component_property("R2", "Value", "9.1K", schematic_path=p)
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        entry = root.find("symbol_instances").find("path")
        assert entry.find("value").atoms[1].text == "9.1K"
        schematic.remove_component("R2", schematic_path=p)
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        # Last entry removed drops the whole section, like the kiutils writer.
        assert root.find("symbol_instances") is None


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


class TestProjectToolsPreservation:
    """Slice 11: project.py sheet/annotate/composite tools on the substrate."""

    @staticmethod
    def _hierarchy(tmp_path, kicad_native_sch):
        parent = kicad_native_sch
        child = tmp_path / "child.kicad_sch"
        project.create_schematic(str(child))
        return parent, child

    def test_create_schematic_native_shape(self, tmp_path):
        out = tmp_path / "fresh.kicad_sch"
        project.create_schematic(str(out))
        data = out.read_bytes()
        assert b"generator_version" in data and b"(embedded_fonts no)" in data
        assert _cst.serialize(_cst.parse(data)) == data
        with pytest.raises(ToolError, match="already exists"):
            project.create_schematic(str(out))

    def test_add_hierarchical_sheet_preserves_both_files(self, tmp_path, kicad_native_sch):
        parent, child = self._hierarchy(tmp_path, kicad_native_sch)
        p_before = parent.read_bytes()
        project.add_hierarchical_sheet(
            str(parent), "sub", str(child), [{"name": "A", "direction": "input"}]
        )
        p_after = parent.read_bytes()
        assert b"generator_version" in p_after and b"(embedded_fonts" in p_after
        assert p_after[: len(p_before) // 3] == p_before[: len(p_before) // 3]
        psch = reparse(parent)
        assert len(psch.sheets) == 1
        sheet = psch.sheets[0]
        assert sheet.sheetName.value == "sub"
        assert sheet.fileName.value == "child.kicad_sch"
        assert [pn.name for pn in sheet.pins] == ["A"]
        csch = reparse(child)
        assert [hl.text for hl in csch.hierarchicalLabels] == ["A"]
        assert any(lbl.text == "A" for lbl in csch.labels)

    def test_sheet_pin_modify_move_reorder(self, tmp_path, kicad_native_sch):
        parent, child = self._hierarchy(tmp_path, kicad_native_sch)
        child2 = tmp_path / "child2.kicad_sch"
        project.create_schematic(str(child2))
        project.add_hierarchical_sheet(str(parent), "s1", str(child), [])
        project.add_hierarchical_sheet(str(parent), "s2", str(child2), [], x=76.2, y=25.4)
        uuids = [s.uuid for s in reparse(parent).sheets]

        before = parent.read_bytes()
        project.add_sheet_pin(uuids[0], "P1", "input", schematic_path=str(parent))
        assert _pure_insertion(before, parent.read_bytes())
        before = parent.read_bytes()
        project.remove_sheet_pin(uuids[0], "P1", schematic_path=str(parent))
        assert _span_preserved(before, parent.read_bytes())
        with pytest.raises(ToolError, match="not found on sheet"):
            project.remove_sheet_pin(uuids[0], "P1", schematic_path=str(parent))

        before = parent.read_bytes()
        project.modify_hierarchical_sheet(
            uuids[0], schematic_path=str(parent), sheet_name="renamed"
        )
        assert _confined(before, parent.read_bytes(), limit=30)
        before = parent.read_bytes()
        project.modify_hierarchical_sheet(uuids[0], schematic_path=str(parent), width=30)
        assert _confined(before, parent.read_bytes(), limit=30)
        sheet = next(s for s in reparse(parent).sheets if s.uuid == uuids[0])
        assert sheet.sheetName.value == "renamed"
        assert sheet.width == 30

        project.move_hierarchical_sheet(uuids[0], 101.6, 50.8, schematic_path=str(parent))
        sheet = next(s for s in reparse(parent).sheets if s.uuid == uuids[0])
        assert (sheet.position.X, sheet.position.Y) == (101.6, 50.8)

        project.reorder_sheet_pages([uuids[1], uuids[0]], schematic_path=str(parent))
        assert [s.uuid for s in reparse(parent).sheets] == [uuids[1], uuids[0]]
        with pytest.raises(ToolError, match="Sheet UUIDs not found"):
            project.reorder_sheet_pages(["nope"], schematic_path=str(parent))

    def test_annotate_cst(self, kicad_native_sch, scratch_sym_lib):
        p = str(kicad_native_sch)
        schematic.place_component(
            "Test:TestPart",
            "U1",
            "TP",
            40,
            40,
            symbol_lib_path=str(scratch_sym_lib),
            schematic_path=p,
        )
        schematic.set_component_property("U1", "Reference", "U?", schematic_path=p)
        result = project.annotate_schematic(schematic_path=p)
        assert result == "Annotated 1 components: U2" or result == "Annotated 1 components: U1"
        after = kicad_native_sch.read_bytes()
        assert b"generator_version" in after and b"(embedded_fonts" in after
        sch = reparse(kicad_native_sch)
        refs = [
            next((pr.value for pr in s.properties if pr.key == "Reference"), "")
            for s in sch.schematicSymbols
        ]
        assert not any("?" in r for r in refs)
        assert project.annotate_schematic(schematic_path=p) == "No unannotated components found"
        assert kicad_native_sch.read_bytes() == after

    def test_duplicate_sheet_uuid_scope(self, tmp_path, kicad_native_sch):
        parent, child = self._hierarchy(tmp_path, kicad_native_sch)
        schematic.place_component(
            "Device:R",
            "R7",
            "1K",
            60,
            60,
            schematic_path=str(child),
        )
        project.add_hierarchical_sheet(
            str(parent), "s1", str(child), [{"name": "A", "direction": "input"}]
        )
        src_uuid = reparse(parent).sheets[0].uuid
        project.duplicate_sheet(src_uuid, "copy", schematic_path=str(parent))
        psch = reparse(parent)
        assert len(psch.sheets) == 2
        new_file = tmp_path / psch.sheets[1].fileName.value
        assert new_file.exists()
        src_root = _cst.parse(child.read_bytes()).lists[0]
        dst_root = _cst.parse(new_file.read_bytes()).lists[0]
        assert _node_uuid(src_root) != _node_uuid(dst_root)
        src_sym = src_root.find("symbol")
        dst_sym = dst_root.find("symbol")
        assert _node_uuid(src_sym) != _node_uuid(dst_sym)
        # kiutils parity: nested pin uuids in the copy are NOT regenerated
        src_pin_uuids = [pn.find("uuid").atoms[1].text for pn in src_sym.find_all("pin")]
        dst_pin_uuids = [pn.find("uuid").atoms[1].text for pn in dst_sym.find_all("pin")]
        assert src_pin_uuids == dst_pin_uuids

    def test_flatten_counts(self, tmp_path, kicad_native_sch):
        parent, child = self._hierarchy(tmp_path, kicad_native_sch)
        schematic.place_component("Device:R", "R7", "1K", 60, 60, schematic_path=str(child))
        project.add_hierarchical_sheet(
            str(parent), "s1", str(child), [{"name": "A", "direction": "input"}]
        )
        result = project.flatten_hierarchy(schematic_path=str(parent))
        assert "2 components" in result
        flat = tmp_path / (parent.stem + "_flat.kicad_sch")
        froot = _cst.parse(flat.read_bytes()).lists[0]
        assert froot.find("sheet") is None
        assert froot.find("hierarchical_label") is None
        assert len(froot.find_all("symbol")) == 2


class TestMemoryGate:
    """ADR-1 board gate: the hardened repr against real KiCad demo boards.

    Baseline before slice 12A: 37.4x on the 6MB Video board. Hardened repr
    measured 2026-08-09: 11.35x retained on Video, 8.85x on the 71MB
    vme-wren. ~11x is the floor of a one-object-per-token design in
    CPython (~32B/object + 33B/bytes overhead), so the gate was revised
    to 12x with the user's sign-off (ADR): 68MB for the benchmark board
    is acceptable for a server process. The flat-token-array design
    stays on record as the next step if real memory pain appears.
    """

    @staticmethod
    def _demo_boards():
        from mcp_server_kicad._shared import _kicad_root

        roots = []
        root = _kicad_root()
        if root is not None:
            roots += [root / "share" / "kicad" / "demos", root / "SharedSupport" / "demos"]
        boards = []
        for r in roots:
            if r.is_dir():
                boards += [
                    b for b in r.rglob("*.kicad_pcb") if 2_000_000 <= b.stat().st_size <= 20_000_000
                ]
        return sorted(boards, key=lambda b: b.stat().st_size)

    def test_largest_demo_board_under_gate(self):
        import gc
        import tracemalloc

        boards = self._demo_boards()
        if not boards:
            pytest.skip("no 2-20MB KiCad demo board on this host")
        board = boards[-1]
        data = board.read_bytes()
        tracemalloc.start()
        tree = _cst.parse(data)
        gc.collect()
        retained, _peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert _cst.serialize(tree) == data
        multiple = retained / len(data)
        assert multiple <= 12, f"{board.name}: {multiple:.2f}x retained (gate <=12x)"

    def test_demo_board_roundtrips(self):
        boards = self._demo_boards()
        if not boards:
            pytest.skip("no >=2MB KiCad demo board on this host")
        data = boards[0].read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data


class TestReadToolsRelaxed:
    """Slice 12B: the 13 schematic read tools are CST-native and guard-free."""

    @pytest.mark.no_kicad_validation
    def test_future_version_file_reads_work(self, kicad_native_sch):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        summary = schematic.get_schematic_summary(schematic_path=p)
        assert summary.components == 1 and summary.wires == 1 and summary.labels == 1
        comps = schematic.list_schematic_components(schematic_path=p)
        assert [c.reference for c in comps] == ["R1"]
        assert comps[0].lib_id == "Device:R"
        labels = schematic.list_schematic_labels(schematic_path=p)
        assert [(lbl.text, lbl.x, lbl.y) for lbl in labels] == [("TEST_NET", 60.0, 100.0)]
        wires = schematic.list_schematic_wires(schematic_path=p)
        assert (wires[0].x1, wires[0].y1, wires[0].x2, wires[0].y2) == (50.0, 100.0, 80.0, 100.0)
        pins = schematic.get_pin_positions("R1", schematic_path=p)
        assert "Pin 1" in pins and "(100.0, 96.19)" in pins

    def test_net_connections_cst(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}], label_text="NETX", schematic_path=p
        )
        result = schematic.get_net_connections("NETX", schematic_path=p)
        assert result.label_count == 1
        assert any(c["reference"] == "R1" and c["pin"] == "1" for c in result.connections)


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
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=str(kicad_native_sch)).components >= 0
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
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
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
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
        assert "Moved R1" in schematic.move_component("R1", 63.5, 63.5, schematic_path=p)
        assert "22K" in schematic.set_component_property("R1", "Value", "22K", schematic_path=p)
        assert "A3" in schematic.set_page_size("A3", schematic_path=p)
        assert "VCC" in schematic.add_lib_symbol(str(scratch_power_lib), "VCC", schematic_path=p)
        assert schematic.remove_component("R1", schematic_path=p) == "Removed R1"
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert root.find("symbol") is None  # placed R1 gone; lib entries stay nested
        assert "VCC" in [s.atoms[1].text for s in root.find("lib_symbols").find_all("symbol")]

    @pytest.mark.no_kicad_validation
    def test_future_version_file_project_tools_work(self, tmp_path, kicad_native_sch):
        child = tmp_path / "child.kicad_sch"
        project.create_schematic(str(child))
        for f in (kicad_native_sch, child):
            f.write_bytes(f.read_bytes().replace(b"(version 20250114)", b"(version 20260306)"))
        p = str(kicad_native_sch)
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
        project.add_hierarchical_sheet(p, "sub", str(child), [{"name": "A", "direction": "input"}])
        s_uuid = _cst.parse(kicad_native_sch.read_bytes()).lists[0].find("sheet")
        uuid = _node_uuid(s_uuid)
        project.add_sheet_pin(uuid, "B", "output", schematic_path=p)
        assert "renamed" in project.modify_hierarchical_sheet(
            uuid, schematic_path=p, sheet_name="renamed"
        )
        assert "Removed hierarchical sheet" in project.remove_hierarchical_sheet(p, uuid=uuid)

    @pytest.mark.no_kicad_validation
    def test_future_version_file_place_component_works(self, kicad_native_sch, scratch_sym_lib):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
        result = schematic.place_component(
            lib_id="Test:TestPart",
            reference="U1",
            value="TP",
            x=63.5,
            y=63.5,
            symbol_lib_path=str(scratch_sym_lib),
            schematic_path=p,
        )
        assert "Placed U1" in result
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert any(
            n.find("lib_id") is not None and n.find("lib_id").atoms[1].text == "Test:TestPart"
            for n in root.find_all("symbol")
        )


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

    def test_place_component_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        # System Device lib on a KiCad 10 runner is K10-format: the copied
        # entry and the minted schematic stay format-consistent.
        result = schematic.place_component(
            lib_id="Device:C", reference="C1", value="100nF", x=63.5, y=63.5, schematic_path=p
        )
        assert "Placed C1" in result
        sch_root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert any(
            n.find("lib_id") is not None and n.find("lib_id").atoms[1].text == "Device:C"
            for n in sch_root.find_all("symbol")
        )

    def test_add_power_symbol_on_real_kicad10(self, kicad_native_sch):
        # Closes the slice-8 deferred measurement: the auto-PWR_FLAG path on a
        # real KiCad 10 file, both symbols copied from the runner's K10 libs.
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        result = schematic.add_power_symbol("power:VCC", "#PWR01", 60, 90, schematic_path=p)
        assert "#PWR01" in result and "#FLG01" in result
        sch_root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        lib_names = [s.atoms[1].text for s in sch_root.find("lib_symbols").find_all("symbol")]
        assert "power:VCC" in lib_names  # system copy, prefixed
        # The PWR_FLAG rides through the explicit symbol_lib_path branch, which
        # copies bare (today's shape); its lib_name fallback keeps KiCad happy.
        assert "PWR_FLAG" in lib_names

    def test_hierarchy_on_real_kicad10(self, tmp_path, kicad_native_sch):
        self._mint(kicad_native_sch)
        child = tmp_path / "k10child.kicad_sch"
        project.create_schematic(str(child))
        _run_cli(["sch", "upgrade", "--force", str(child)])
        p = str(kicad_native_sch)
        project.add_hierarchical_sheet(p, "sub", str(child), [{"name": "A", "direction": "input"}])
        sheet = _cst.parse(kicad_native_sch.read_bytes()).lists[0].find("sheet")
        uuid = _node_uuid(sheet)
        project.add_sheet_pin(uuid, "B", "output", schematic_path=p)
        assert "renamed" in project.modify_hierarchical_sheet(
            uuid, schematic_path=p, sheet_name="renamed"
        )
        schematic.place_component("Device:C", "C1", "100nF", 63.5, 63.5, schematic_path=p)
        schematic.set_component_property("C1", "Reference", "C?", schematic_path=p)
        assert "Annotated 1 components" in project.annotate_schematic(schematic_path=p)

    def test_project_reads_on_real_kicad10(self, tmp_path, kicad_native_sch):
        # Slice 16: the six project.py reads walk the CST, so they see a real
        # KiCad 10 hierarchy instead of refusing it. Pin B is deliberately
        # left without a child label so the issue engine has something to find.
        self._mint(kicad_native_sch)
        child = tmp_path / "k10child.kicad_sch"
        project.create_schematic(str(child))
        _run_cli(["sch", "upgrade", "--force", str(child)])
        p = str(kicad_native_sch)
        project.add_hierarchical_sheet(p, "sub", str(child), [{"name": "A", "direction": "input"}])
        uuid = _node_uuid(_cst.parse(kicad_native_sch.read_bytes()).lists[0].find("sheet"))
        project.add_sheet_pin(uuid, "B", "output", schematic_path=p)

        hierarchy = project.list_hierarchy(schematic_path=p)
        assert hierarchy.sheet_count == 1
        assert hierarchy.sheets[0]["file_name"] == "k10child.kicad_sch"
        assert hierarchy.sheets[0]["pin_count"] == 2
        assert hierarchy.sheets[0]["hierarchical_label_count"] == 1

        info = project.get_sheet_info(uuid, schematic_path=p)
        assert {pin["name"]: pin["matched"] for pin in info.pins} == {"A": True, "B": False}

        trace = project.trace_hierarchical_net("A", schematic_path=p)
        assert "k10child.kicad_sch" in trace.sheets_touched
        assert {"sheet_pin", "hierarchical_label"} <= {c["type"] for c in trace.connections}

        nets = project.list_cross_sheet_nets(schematic_path=p)
        assert {n["name"]: n["label_matched"] for n in nets.hierarchical_nets} == {
            "A": True,
            "B": False,
        }
        # No .kicad_pro beside the fixture, so nothing writes a symbol_instances
        # section: the empty-section path is what a KiCad 8+ file gives anyway.
        assert project.get_symbol_instances(schematic_path=p).instances == []

        schematic.set_component_property("R1", "Reference", "R?", schematic_path=p)
        issues = project.validate_hierarchy(schematic_path=p)
        assert issues.status == "issues_found"
        assert {"type": "orphaned_pin", "sheet_name": "sub", "pin": "B"} in issues.issues
        assert any(i["type"] == "unannotated_ref" and i["reference"] == "R?" for i in issues.issues)
        assert "Annotated 1 components" in project.annotate_schematic(schematic_path=p)
        after = project.validate_hierarchy(schematic_path=p)
        assert not any(i["type"] == "unannotated_ref" for i in after.issues)

    def test_duplicate_and_flatten_on_real_kicad10(self, tmp_path, kicad_native_sch):
        self._mint(kicad_native_sch)
        child = tmp_path / "k10child.kicad_sch"
        project.create_schematic(str(child))
        _run_cli(["sch", "upgrade", "--force", str(child)])
        p = str(kicad_native_sch)
        project.add_hierarchical_sheet(p, "s1", str(child), [{"name": "A", "direction": "input"}])
        src_uuid = _cst.parse(kicad_native_sch.read_bytes()).lists[0].find("sheet")
        project.duplicate_sheet(_node_uuid(src_uuid), "copy", schematic_path=p)
        assert len(_cst.parse(kicad_native_sch.read_bytes()).lists[0].find_all("sheet")) == 2
        result = project.flatten_hierarchy(schematic_path=p)
        assert "components" in result

    def test_read_tools_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}], label_text="K10NET", schematic_path=p
        )
        summary = schematic.get_schematic_summary(schematic_path=p)
        assert summary.components == 1 and summary.wires == 2
        assert [c.reference for c in schematic.list_schematic_components(schematic_path=p)] == [
            "R1"
        ]
        assert "Pin 1" in schematic.get_pin_positions("R1", schematic_path=p)
        result = schematic.get_net_connections("K10NET", schematic_path=p)
        assert any(c["reference"] == "R1" for c in result.connections)
