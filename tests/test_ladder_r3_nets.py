"""Rung 3, package 3 (the nets rows), its KiCad 10 no-net tests taken ahead as step 7b: no net
as the board update and the track writers write it on a KiCad 10 board, judged by KiCad 10.

Code under test: pcb._apply_netlist_cst, the board update behind update_pcb_from_schematic,
when a net leaves the schematic (its orphan pass sends each segment, arc, via and zone on that
net to net 0, no net), and add_trace, add_via and add_thermal_vias asked for net 0; the update
driven with a netlist edit_gen.netlist_text builds in kicad-cli's form (rung 3's D11), with no
schematic and no kicad-cli.

KiCad 10's rule, read at 10.0.7. Its writer gives a segment, an arc and a via
(net <Quotew( GetNetname() )>) whatever their net (pcbnew/pcb_io/kicad_sexpr/
pcb_io_kicad_sexpr.cpp:2860-2861), and net 0 is the net the board's net list registers under
the empty name (pcbnew/netinfo_list.cpp:44-45), so on no net they are written (net ""). It
gives a zone a (net ...) only on copper with a net above 0 (2872-2876) and never a
(net_name ...), and a pad a (net ...) only above 0 (1856-1858), so a zone or a pad on no net
carries no net token at all. Its reader takes a quoted token, the empty one included, as a net
name (common/dsnlexer.cpp:643-725 and 327-333) and looks it up with FindNet (parseNet,
pcb_io_kicad_sexpr_parser.cpp:296-342, called for an arc at 7393-7394, a segment at 7488-7489,
a via at 7609-7610 and a zone at 8033-8034), and FindNet of the empty name is net 0
(pcbnew/board.cpp:2729-2732). A zone written with no net keeps the one its constructor gives
it, NETINFO_LIST::OrphanedItem, net 0 (pcbnew/board_connected_item.cpp:44-46,
pcbnew/netinfo.h:255-263); but a legacy (net_name ...) that is not empty wins over it and finds
or creates a net of that name (pcb_io_kicad_sexpr_parser.cpp:8037-8041 and 8673-8697), so a
zone sent to no net loses its net_name as well. pcb._set_item_net looked net 0 up among the
nets it numbers from 1 off a KiCad 10 board's references and refused it (bug 21): the update
aborted when a net left the schematic, and the three tools refused net 0.

Judges, none of them the code under test: those rules, with kicad_ref.richio's port of Quotew
for the bytes KiCad writes, and tests/edit_judge.py on KiCad's own lexer for what an edit
changed. A KiCad 9 board spells no net (net 0), which the passing sibling pins. Every board is
written into tmp_path, where the autouse oracle loads it with kicad-cli: a KiCad 10 board only
where kicad-cli is 10 or newer, CI's KiCad 10 legs.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from pathlib import Path

import edit_gen as E
import edit_judge as J
import geom_gen as G
import pytest
from kicad_ref import richio
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import pcb

MM = 1_000_000

#: edit_judge's location of a board's root list.
ROOT: J.Path = ((0, "kicad_pcb"),)

_BUG_21 = (
    "bug 21: net 0 is refused on a KiCad 10 board, so the update aborts when a net leaves the"
    " schematic and add_trace, add_via and add_thermal_vias cannot write no net; fixed by step 7b"
)

#: How KiCad 10 writes the net of a segment, arc or via on no net: Quotew of net 0's name,
#: which is empty (the module docstring).
_NO_NET = richio.quotes("")

#: Where U1 sits, in nm: its pad 1 is at (49, 40) mm on the board, its pad 2 at (51, 40).
_U1_AT = (50 * MM, 40 * MM)
#: U1's pads and their nets, on the board and in the netlist alike: both stay in the schematic.
_PAD_NETS = {"1": "/GND", "2": "/VCC"}

#: The children before the net of the segment, arc or via a test puts on /SIG, in the order
#: format( PCB_TRACK ) writes them (pcb_io_kicad_sexpr.cpp:2622-2702 and 2825-2849 at 10.0.7).
_SIG_TRACKS = {
    "segment": ("(start 60 50)", "(end 70 50)", "(width 0.25)", '(layer "F.Cu")'),
    "arc": ("(start 60 50)", "(mid 65 45)", "(end 70 50)", "(width 0.25)", '(layer "F.Cu")'),
    "via": ("(at 70 50)", "(size 0.6)", "(drill 0.3)", '(layers "F.Cu" "B.Cu")'),
}
#: A segment on /GND, from U1's pad 1 to (40, 40): an item on a net the netlist keeps.
_GND_SEGMENT = ("(start 49 40)", "(end 40 40)", "(width 0.25)", '(layer "F.Cu")')
#: The outline of the zone a test puts on /SIG, in nm.
_ZONE_OUTLINE = ((60 * MM, 60 * MM), (70 * MM, 60 * MM), (70 * MM, 70 * MM), (60 * MM, 70 * MM))

#: The children KiCad 10 writes, in order, for a segment and for a through via with nothing
#: set but geometry and net (format( PCB_TRACK ), pcb_io_kicad_sexpr.cpp:2611-2865 at 10.0.7).
_WRITTEN = {
    "segment": ["start", "end", "width", "layer", "net", "uuid"],
    "via": ["at", "size", "drill", "layers", "net", "uuid"],
}

_uids = itertools.count(1)


def _uid() -> str:
    return f"{next(_uids):08x}-0000-4000-8000-0000000021b1"


def _quoted(name: str) -> str:
    """A net name as KiCad writes it (Quotew)."""
    return richio.quotes(name).decode("utf-8")


def _u1(pad_nets: dict[str, str], *, version: str = "k10") -> str:
    """U1, edit_gen's two-pad footprint as KiCad holds it at _U1_AT, linked to its symbol as
    the update links one with no symbol uuid (sheetname "/"), each pad of ``pad_nets`` on its
    net, written as KiCad 10 writes a pad's net: (net "NAME") straight after an smd rect pad's
    (layers ...) (format( PAD ), pcb_io_kicad_sexpr.cpp:1791-1858 at 10.0.7). A pad on no net
    carries none, which is also KiCad 9's spelling, the only one ``version`` "k9" allows."""
    if version != "k10" and pad_nets:
        raise ValueError("pad nets are written in KiCad 10's spelling only")
    text = E.board_footprint_text(
        E.footprint_with_pad_at(0.0), "U1", _U1_AT, 0.0, version=version, sheetname="/"
    )
    out: list[str] = []
    pad: str | None = None
    placed: set[str] = set()
    for line in text.splitlines(keepends=True):
        out.append(line)
        if line.startswith('\t\t(pad "'):
            pad = line.split('"')[1]
        elif line == "\t\t)\n":
            pad = None
        elif pad is not None and pad in pad_nets and line.startswith("\t\t\t(layers "):
            out.append(f"\t\t\t(net {_quoted(pad_nets[pad])})\n")
            placed.add(pad)
    if placed != set(pad_nets):
        raise RuntimeError(f"edit_gen's footprint holds no pad {sorted(set(pad_nets) - placed)}")
    return "".join(out)


def _track(head: str, children: Sequence[str], net: str) -> str:
    """A segment, arc or via on ``net``: ``children`` (what comes before its net), its net and
    its uuid, in the order format( PCB_TRACK ) writes them (pcb_io_kicad_sexpr.cpp:2611-2865 at
    10.0.7), each child on its own line as Prettify lays out a list of lists."""
    body = "".join(f"\t\t{child}\n" for child in children)
    return f'\t({head}\n{body}\t\t(net {_quoted(net)})\n\t\t(uuid "{_uid()}")\n\t)\n'


def _zone(net: str, *, net_name: bool) -> str:
    """An unfilled copper zone on F.Cu on ``net``, in the order format( ZONE ) writes one
    (pcb_io_kicad_sexpr.cpp:2868-3097 at 10.0.7): net, layer, uuid, hatch, the pad connection
    (thermal, the default, written as no word), minimum thickness, the fill settings (with the
    island removal 10.0.7 always writes, 0 for ALWAYS, the mode its parser gives a board's
    zone: pcbnew/zone_settings.h:68-73, pcb_io_kicad_sexpr_parser.cpp:8006-8007) and the
    outline, its points packed as Prettify packs them. With ``net_name``, a legacy
    (net_name ...) of the same name after the net, where KiCad 9's writer put it
    (pcb_io_kicad_sexpr.cpp:2452-2454 at 9.0.8), which KiCad 10 still reads."""
    quoted = _quoted(net)
    legacy = f"\t\t(net_name {quoted})\n" if net_name else ""
    return (
        f"\t(zone\n\t\t(net {quoted})\n{legacy}"
        f'\t\t(layer "F.Cu")\n\t\t(uuid "{_uid()}")\n'
        "\t\t(hatch edge 0.5)\n"
        "\t\t(connect_pads\n\t\t\t(clearance 0.5)\n\t\t)\n"
        "\t\t(min_thickness 0.25)\n"
        "\t\t(fill\n\t\t\t(thermal_gap 0.5)\n\t\t\t(thermal_bridge_width 0.5)\n"
        "\t\t\t(island_removal_mode 0)\n\t\t)\n"
        "\t\t(polygon\n" + G._pts_block(_ZONE_OUTLINE, 3) + "\t\t)\n\t)\n"
    )


def _board(tmp_path: Path, items: Sequence[str], *, version: str = "k10") -> Path:
    path = tmp_path / "board.kicad_pcb"
    path.write_text(E.layered_board_text(items, version=version), encoding="utf-8", newline="\n")
    return path


def _the(data: bytes, *labels: str, where: Callable[[J.Item], bool] = lambda item: True) -> J.Path:
    """The location of the one list in ``data`` whose labels, outermost first, are ``labels``
    and that ``where`` accepts. Raises LookupError, never AssertionError."""
    found = J.find_paths(
        data,
        lambda loc, item: (
            item.items is not None and [label for _, label in loc] == list(labels) and where(item)
        ),
    )
    if len(found) != 1:
        raise LookupError(f"{len(found)} lists match {labels} where one was expected")
    return found[0]


def _on_sig(net: J.Item) -> bool:
    """Whether a (net ...) list names /SIG."""
    items = net.items
    return items is not None and len(items) == 2 and items[1].value == b"/SIG"


def _update_without_sig(board: Path) -> dict:
    """pcb._apply_netlist_cst on ``board`` with a netlist in which /SIG is gone: U1 on the
    footprint it has, its pads on the nets _PAD_NETS names, and no net /SIG. A refusal fails
    the test: KiCad's own writer has a spelling for every item on no net."""
    netlist = board.parent / "netlist.xml"
    netlist.write_text(
        E.netlist_text(
            [E.NetComponent("U1", "V", "Ladder:Syn")],
            [(net, [("U1", pad)]) for pad, net in _PAD_NETS.items()],
            tool="Eeschema 10.0.7",
        ),
        encoding="utf-8",
    )
    try:
        return pcb._apply_netlist_cst(str(netlist), str(board), [])
    except ToolError as exc:
        raise AssertionError(f"the update refused to send /SIG's items to no net: {exc}") from exc
    finally:
        pcb._BOARD_CACHE.pop(str(board.resolve()), None)


def _add(tool: str, board: Path) -> str:
    """Run ``tool`` on ``board`` asking for net 0 (add_thermal_vias on U1's pad 1, two by two,
    taking the pad's net); return the head of the items it adds. A refusal fails the test."""
    try:
        if tool == "add_trace":
            pcb.add_trace(10, 10, 20, 10, net=0, pcb_path=str(board))
            return "segment"
        if tool == "add_via":
            pcb.add_via(10, 10, net=0, pcb_path=str(board))
            return "via"
        pcb.add_thermal_vias("U1", pad_number="1", rows=2, cols=2, pcb_path=str(board))
        return "via"
    except ToolError as exc:
        raise AssertionError(f"{tool} refused net 0, which KiCad writes: {exc}") from exc


def _added(before: bytes, after: bytes, head: str, count: int) -> list[J.Item]:
    """The ``count`` items headed ``head`` a tool added to the board's root list, as KiCad's
    lexer reads them: whole items inserted, every other byte where it was."""
    edits = J.assert_inserted(before, after, heads=[head] * count)
    assert [edit.path for edit in edits] == [ROOT] * count
    return [J.item_at(after, edit.location) for edit in edits]


def _children(item: J.Item) -> list[str | None]:
    """The heads of an item's child lists, in order."""
    return [child.head for child in item.items or () if child.items is not None]


def _net_tokens(item: J.Item) -> list[list[bytes]]:
    """Each (net ...) child of an item, as the bytes of the tokens after its head."""
    return [
        [token.raw for token in (child.items or ())[1:]]
        for child in item.items or ()
        if child.items is not None and child.head == "net"
    ]


# ---------------------------------------------------------------------------
# the board update: items on a net that left the schematic
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_21)
@pytest.mark.parametrize("kind", ["segment", "arc", "via"])
def test_k10_orphan_pass_writes_kicad_10_no_net(tmp_path, kind):
    """A KiCad 10 board holding U1 (pads on /GND and /VCC), a segment on /GND and a segment,
    an arc or a via on /SIG, updated from a netlist that keeps /GND and /VCC and has no /SIG:
    the update completes, counts the item in orphaned_tracks, and the one change to the file
    is the item's net atom, "/SIG" become "", KiCad 10's no net for a segment, an arc and a via
    (the module docstring). The update refused the whole board with "Net 0 not found"."""
    board = _board(
        tmp_path,
        [
            _u1(_PAD_NETS),
            _track("segment", _GND_SEGMENT, "/GND"),
            _track(kind, _SIG_TRACKS[kind], "/SIG"),
        ],
    )
    before = board.read_bytes()
    net = _the(before, "kicad_pcb", kind, "net", where=_on_sig)
    summary = _update_without_sig(board)
    assert J.atom_changes(before, board.read_bytes()) == [J.AtomChange(net, 1, b'"/SIG"', _NO_NET)]
    assert summary["orphaned_tracks"] == 1, summary


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_21)
@pytest.mark.parametrize("net_name", [False, True], ids=["net", "net and net_name"])
def test_k10_orphaned_zone_loses_its_net(tmp_path, net_name):
    """The same board with a copper zone on /SIG in place of the track, written with a legacy
    net_name as well in the second case, updated from the same netlist: the update completes,
    counts the zone in orphaned_zones, and the one change to the file is the zone losing its
    (net ...) and any (net_name ...), whole items: KiCad 10 writes a zone on no net with
    neither, reads it on net 0, and would put it back on a /SIG of its own making for a
    net_name left behind (the module docstring). The update refused the whole board with
    "Net 0 not found"."""
    board = _board(
        tmp_path,
        [
            _u1(_PAD_NETS),
            _track("segment", _GND_SEGMENT, "/GND"),
            _zone("/SIG", net_name=net_name),
        ],
    )
    before = board.read_bytes()
    zone = _the(before, "kicad_pcb", "zone")
    summary = _update_without_sig(board)
    heads = ["net", "net_name"] if net_name else ["net"]
    removed = J.assert_removed(before, board.read_bytes(), heads=heads)
    assert [edit.path for edit in removed] == [zone] * len(heads)
    assert summary["orphaned_zones"] == 1, summary


# ---------------------------------------------------------------------------
# the track writers asked for net 0
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_21)
@pytest.mark.parametrize("tool", ["add_trace", "add_via"])
def test_k10_net_zero_is_written_as_kicad_10_writes_it(tmp_path, tool):
    """add_trace and add_via with net 0 on a KiCad 10 board whose U1 has pads on /GND and
    /VCC: the board gains one segment or via, every other byte kept, written with the children
    KiCad 10 writes for one and the net (net ""), its no net, not a net the board holds. Both
    tools refused net 0 with "Net 0 not found"."""
    board = _board(tmp_path, [_u1(_PAD_NETS)])
    before = board.read_bytes()
    head = _add(tool, board)
    (item,) = _added(before, board.read_bytes(), head, 1)
    assert _children(item) == _WRITTEN[head]
    assert _net_tokens(item) == [[_NO_NET]]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_21)
def test_thermal_vias_on_a_netless_k10_pad_are_on_no_net(tmp_path):
    """add_thermal_vias two by two under U1's pad 1, which is on no net (written with no net
    token, as KiCad 10 writes a pad on net 0), on a KiCad 10 board whose pad 2 is on /VCC: the
    board gains four vias, every other byte kept, each written as KiCad 10 writes a via on no
    net, with (net ""). The tool refused them with "Net 0 not found"."""
    board = _board(tmp_path, [_u1({"2": "/VCC"})])
    before = board.read_bytes()
    _add("add_thermal_vias", board)
    vias = _added(before, board.read_bytes(), "via", 4)
    assert [_children(via) for via in vias] == [_WRITTEN["via"]] * 4
    assert [_net_tokens(via) for via in vias] == [[[_NO_NET]]] * 4


@pytest.mark.parametrize("tool", ["add_trace", "add_via", "add_thermal_vias"])
def test_k9_net_zero_stays_numeric(tmp_path, tool):
    """The passing sibling: on a KiCad 9 board, whose net table numbers net 0 as 0, no net is
    (net 0), which KiCad 9 writes for a segment, an arc or a via on net 0 (format( PCB_TRACK ),
    pcb_io_kicad_sexpr.cpp:2436 at 9.0.8, net 0 numbered 0 by NETINFO_MAPPING::Update,
    pcbnew/netinfo_list.cpp:292-293 and 326-329 there): add_trace and add_via with net 0, and
    add_thermal_vias under U1's netless pad 1, each add items whose net is the number 0."""
    board = _board(tmp_path, [_u1({}, version="k9")], version="k9")
    before = board.read_bytes()
    head = _add(tool, board)
    count = 4 if tool == "add_thermal_vias" else 1
    items = _added(before, board.read_bytes(), head, count)
    assert [_net_tokens(item) for item in items] == [[[b"0"]]] * count
