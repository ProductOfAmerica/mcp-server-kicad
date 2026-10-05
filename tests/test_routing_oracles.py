"""The routing tests' own instruments: the netlist judge and the write checks.

Every routing test leans on these, so they get tests of their own first. Each verdict kind of
netlist_oracle.judge is fed a synthetic before/after pair it must flag, and a clean pair it must
pass; the write checks are fed a file changed in each way they must refuse. KiCad-free apart
from one smoke test of the real export.
"""

from __future__ import annotations

import pytest
from conftest import requires_cli
from netlist_oracle import Net, Node, judge, nets, parse_kicadxml
from routing_checks import assert_only_added, lint_new_geometry

from mcp_server_kicad import _cst

R1: Node = ("R1", "1")
R2: Node = ("R2", "1")
R3: Node = ("R3", "1")


def net(code: int, name: str, *nodes: Node, cls: str = "Default") -> Net:
    return (str(code), name, cls, frozenset(nodes))


class TestJudge:
    def test_a_clean_write_is_delivered(self):
        before = [net(1, "unconnected-(R1-Pad1)", R1), net(2, "/B", R2)]
        after = [net(1, "/N", R1), net(2, "/B", R2)]
        v = judge(before, after, [{R1}], "N")
        assert not v.wrong, v.problems()
        assert v.delivered

    def test_joining_an_unrequested_net_is_a_bad_merge(self):
        before = [net(1, "unconnected-(R1-Pad1)", R1), net(2, "unconnected-(R2-Pad1)", R2)]
        after = [net(1, "/N", R1, R2)]
        v = judge(before, after, [{R1}], "N")
        assert v.bad_merges and v.wrong

    def test_two_user_names_joined_count_even_inside_the_request(self):
        # R1 was on /M and R2 on /N: putting R1 on N is the request, but /M and /N carried
        # two user names, so the edit joined two named nets.
        before = [net(1, "/M", R1), net(2, "/N", R2)]
        after = [net(1, "/N", R1, R2)]
        v = judge(before, after, [{R1}], "N")
        assert not v.bad_merges
        assert v.named_merge == [("/N", ["/M", "/N"])]
        assert v.wrong

    def test_a_split_is_flagged(self):
        before = [net(1, "/A", R1, R2)]
        after = [net(1, "/A", R1), net(2, "unconnected-(R2-Pad1)", R2)]
        assert judge(before, after, [], None, wrote=True).splits

    def test_a_rename_outside_the_request_is_flagged(self):
        before = [net(1, "/A", R3), net(2, "unconnected-(R1-Pad1)", R1)]
        after = [net(1, "/B", R3), net(2, "/N", R1)]
        v = judge(before, after, [{R1}], "N")
        assert v.renames == [("/A", "/B")]

    def test_renaming_the_requested_pads_own_named_net_is_flagged(self):
        # The routing review's mutation: with the names refusal disabled, R1's net /ZOLD was
        # renamed /ANEW and the judge passed it, because the rename sat inside the join.
        before = [net(1, "/ZOLD", R1)]
        after = [net(1, "/ANEW", R1)]
        v = judge(before, after, [{R1}], "ANEW")
        assert v.renames == [("/ZOLD", "/ANEW")] and v.wrong

    def test_a_net_name_holding_a_slash_is_matched_whole(self):
        # kicadxml prints "A{slash}B" as /A/B; splitting at the last "/" read the name as "B",
        # so joining R1 to the existing A/B net counted as a bad merge.
        before = [net(1, "unconnected-(R1-Pad1)", R1), net(2, "/A/B", R2)]
        after = [net(1, "/A/B", R1, R2)]
        v = judge(before, after, [{R1}], "A/B")
        assert not v.wrong and v.delivered, v.problems()

    def test_nets_sharing_a_printed_name_are_told_apart_by_nodes(self):
        before = [net(1, "/X", R1), net(2, "/X", R2)]
        assert not judge(before, before, [], None).wrong
        merged = [net(1, "/X", R1, R2)]
        assert judge(before, merged, [], None).bad_merges

    def test_a_class_change_outside_the_join_is_flagged(self):
        before = [net(1, "/A", R1)]
        after = [net(1, "/A", R1, cls="HV")]
        assert judge(before, after, [], None).class_changes

    def test_taking_the_joined_nets_class_is_allowed(self):
        before = [net(1, "unconnected-(R1-Pad1)", R1), net(2, "/N", R2, cls="HV")]
        after = [net(1, "/N", R1, R2, cls="HV")]
        v = judge(before, after, [{R1}], "N")
        assert not v.class_changes and v.delivered

    def test_a_pad_in_two_nets_is_flagged_and_not_delivered(self):
        before = [net(1, "unconnected-(R1-Pad1)", R1)]
        after = [net(1, "/N", R1), net(2, "/OTHER", R1)]
        v = judge(before, after, [{R1}], "N")
        assert v.pads_multi == [R1] and v.req_pad_multi == [R1]
        assert not v.delivered

    def test_vanished_and_appeared_nodes_are_flagged(self):
        before = [net(1, "/A", R1)]
        after = [net(1, "/A", R2)]
        v = judge(before, after, [], None)
        assert v.vanished == [R1] and v.appeared == [R2]

    def test_delivery_needs_the_requested_name(self):
        before = [net(1, "unconnected-(R1-Pad1)", R1)]
        after = [net(1, "/OTHER", R1)]
        assert not judge(before, after, [{R1}], "N").delivered

    def test_delivery_through_a_net_already_named_n(self):
        # A two-name net may print the other name; holding N's former nodes still delivers.
        before = [net(1, "unconnected-(R1-Pad1)", R1), net(2, "VCC", R2)]
        after = [net(1, "/AAA", R1, R2)]
        assert judge(before, after, [{R1}], "VCC").delivered

    def test_connect_pins_shape(self):
        before = [net(1, "unconnected-(R1-Pad1)", R1), net(2, "unconnected-(R2-Pad1)", R2)]
        after = [net(1, "Net-(R1-Pad1)", R1, R2)]
        v = judge(before, after, [{R1, R2}], None)
        assert not v.wrong and v.delivered

    def test_parse_kicadxml(self):
        xml = (
            '<export><nets><net code="2" name="/N" class="HV">'
            '<node ref="R1" pin="1" pintype="passive"/></net></nets></export>'
        )
        assert parse_kicadxml(xml) == [("2", "/N", "HV", frozenset({R1}))]


@requires_cli
def test_nets_reads_a_real_export(kicad_native_sch):
    found = nets(kicad_native_sch)
    by_node = {node: (name, cls) for _c, name, cls, nodes in found for node in nodes}
    assert by_node[("R1", "1")] == ("unconnected-(R1-Pad1)", "Default")
    assert not list(kicad_native_sch.parent.glob("*.oracle.xml"))


_SCH = (
    b'(kicad_sch\n\t(version 20250114)\n\t(generator "eeschema")\n'
    b'\t(uuid "00000000-0000-0000-0000-000000000001")\n\t(paper "A4")\n'
    b"\t(lib_symbols\n\t)\n"
    b"\t(wire\n\t\t(pts\n\t\t\t(xy 0 0) (xy 10 0)\n\t\t)\n"
    b'\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "w1")\n\t)\n'
    b'\t(label "A"\n\t\t(at 10 0 0)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n'
    b'\t\t\t)\n\t\t)\n\t\t(uuid "l1")\n\t)\n'
    b'\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n)\n'
)


def _wire(x1, y1, x2, y2):
    pts = f"(pts (xy {x1} {y1}) (xy {x2} {y2}))"
    text = f'(wire {pts} (stroke (width 0) (type default)) (uuid "n"))'
    return _cst.parse(text.encode()).lists[0]


def _with(*nodes, data=_SCH) -> bytes:
    """_SCH with *nodes* spliced after the last sibling of their own kind."""
    tree = _cst.parse(data)
    root = tree.lists[0]
    for node in nodes:
        same = root.find_all(node.head)
        anchor = same[-1] if same else root.find("sheet_instances")
        if same:
            root.insert_after(anchor, node)
        else:
            root.insert_before(anchor, node)
    return _cst.serialize(tree)


def _label(x, y, text="N"):
    return _cst.parse(
        f'(label "{text}" (at {x} {y} 0) (effects (font (size 1.27 1.27))) (uuid "m"))'.encode()
    ).lists[0]


class TestOnlyAdded:
    def test_two_insertion_points_pass(self):
        after = _with(_wire(20, 0, 20, 5), _label(20, 5))
        assert [n.head for n in assert_only_added(_SCH, after)] == ["wire", "label"]

    def test_an_edited_existing_node_fails(self):
        with pytest.raises(AssertionError, match="unexpected change"):
            assert_only_added(_SCH, _SCH.replace(b'(label "A"', b'(label "B"'))

    def test_a_junction_fails(self):
        junction = _cst.parse(b'(junction (at 5 0) (diameter 0) (uuid "j"))').lists[0]
        with pytest.raises(AssertionError, match="unexpected change"):
            assert_only_added(_SCH, _with(junction))

    def test_reordered_nodes_fail(self):
        tree = _cst.parse(_SCH)
        root = tree.lists[0]
        wire, label = root.find("wire"), root.find("label")
        i, j = root.children.index(wire), root.children.index(label)
        root.children[i], root.children[j] = label, wire
        with pytest.raises(AssertionError):
            assert_only_added(_SCH, _cst.serialize(tree))


class TestLint:
    def test_a_clean_stub_passes(self):
        lint_new_geometry(_SCH, _with(_wire(20, 0, 20, 5), _label(20, 5)))

    def test_a_collinear_overlap_fails(self):
        with pytest.raises(AssertionError, match="overlaps"):
            lint_new_geometry(_SCH, _with(_wire(5, 0, 15, 0)))

    def test_a_crossing_fails(self):
        with pytest.raises(AssertionError, match="crosses"):
            lint_new_geometry(_SCH, _with(_wire(5, -5, 5, 5)))

    def test_an_end_on_a_wire_interior_fails(self):
        with pytest.raises(AssertionError, match="interior"):
            lint_new_geometry(_SCH, _with(_wire(5, 0, 5, 5)))

    def test_an_existing_item_on_the_new_wire_fails(self):
        # The existing label A sits at (10, 0); a new wire passing through it captures it.
        with pytest.raises(AssertionError, match="on new wire"):
            lint_new_geometry(_SCH, _with(_wire(10, -5, 10, 5)))

    def test_a_label_on_a_wire_interior_fails(self):
        with pytest.raises(AssertionError, match="new label"):
            lint_new_geometry(_SCH, _with(_label(5, 0)))

    def test_a_junction_fails(self):
        junction = _cst.parse(b'(junction (at 10 0) (diameter 0) (uuid "j"))').lists[0]
        with pytest.raises(AssertionError, match="junction"):
            lint_new_geometry(_SCH, _with(junction))
