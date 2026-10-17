"""Rung 3, package 3 (the nets rows) and step 10's net reference tests: what a board's net
references are, how a tool writes one, and how the board update binds them, judged by KiCad.

Code under test: pcb._set_item_net (the one writer of an item's net reference) through
add_trace, add_via, add_thermal_vias and add_copper_zone; pcb._apply_netlist_cst, the board
update behind update_pcb_from_schematic, when a net leaves the schematic (its orphan pass sends
each item on that net to no net, unless a pad of a footprint that stays is on it), when it adds
one (_ensure_net_rows) and when it binds a pad (_bind_pad_net); and the read tools' reading of a
net atom (_net_name_of, _bound_names and _net_table). The
update is driven with a netlist edit_gen.netlist_text builds in kicad-cli's form (rung 3's D11),
with no schematic and no kicad-cli.

What each group pins. Step 7b's bug 21 (fixed): no net as the board update and the track
writers write it on a KiCad 10 board, the tests from test_k10_orphan_pass_writes_kicad_10_no_net
to test_k9_net_zero_stays_numeric. Step 10's: bug 3, a net number a tool writes on a KiCad 9
board is 0 or a row of its table; bug 19, a KiCad 9 zone is on the net its net_name names; bug
20, a quoted net atom is a name even when it is digits, and get_board_info counts net 0 on a
board with no table; bug 22, a pad whose net did not change keeps its bytes; bug 99, the orphan
and prune passes see the copper shapes that carry a net; bug 100, a name a board with no table
spells gets a row; bug 101, a new row never takes a number an item already refers to. Review
fixes: a net a pad of a footprint that stays is on has not left the schematic (a via or a zone
on such a net that was renamed stays where it is, where KiCad's own update moves it, pinned as
the tool's description states it); and a bare net number is bound as the KiCad that loads the
board's format binds it, read by the update before it swaps a footprint or moves a pad. The
judges are KiCad's own parser and writer, read at 9.0.8 and 10.0.7 and cited where each is
used; pcbnew, where this machine has it; the lexer port in tests/kicad_ref for what a file
holds; and the files KiCad ships.

KiCad 9's rule, read at 9.0.8. A net reference is a number, and the parser maps every number
through the board's table: getNetCode over the map parseNETINFO_ITEM fills, zeros in its gaps
(pcb_io_kicad_sexpr_parser.h:156-162, pcb_io_kicad_sexpr_parser.cpp:183-192 and 2693-2721). A
number past the table finds no net ("Invalid net ID"), a negative number is net 0
(pcbnew/board_connected_item.cpp:87-115), a pad whose name differs from its row's is orphaned
(parser 5219-5240) and a copper zone's net_name wins over its number (7387-7412). Its writer
numbers the nets in use consecutively from 0 (NETINFO_MAPPING::Update, pcbnew/netinfo_list.cpp:
292-329) and writes a segment, an arc and a via on no net as (net 0) (pcb_io_kicad_sexpr.cpp:
2436), a zone as (net 0) (net_name "") (2452-2454), and a pad and a copper shape with no net
token at all (1610-1615 and 1027-1028).

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
zone sent to no net loses its net_name as well. It makes a net where it first meets a name
(parseNet and a pad's branch, 321-339 and 6233-6244), numbers them 1, 2, ... in the order it
makes them (NETINFO_LIST::AppendNet and getFreeNetCode, netinfo_list.cpp:149-165 and 266-275)
and binds a bare number in a (net ...) to the net with that number, as it has no table to map
it through (parseNet, 296-312, and getNetCode, pcb_io_kicad_sexpr_parser.h:176-184): the
silent rebind of ADR-2 guardrail 5. A pad that holds a number alone it refuses, whatever the
board (Expecting( "net name" ), 6185-6212). pcb._set_item_net looked net 0 up among the
nets it numbers from 1 off a KiCad 10 board's references and refused it (bug 21): the update
aborted when a net left the schematic, and the three tools refused net 0.

Judges, none of them the code under test: those rules, with kicad_ref.richio's port of Quotew
for the bytes KiCad writes, and tests/edit_judge.py on KiCad's own lexer for what an edit
changed. A KiCad 9 board spells no net as (net 0), which test_k9_net_zero_stays_numeric pins.
Every board is written into tmp_path, where the autouse oracle loads it with kicad-cli (a KiCad
10 board only where kicad-cli is 10 or newer, CI's KiCad 10 legs). That load cannot judge a net
number that names no net, which kicad-cli 9.0.8 loads without complaint (measured 2026-10-17),
so the tests of such numbers read the file with the lexer port and pcbnew.
"""

from __future__ import annotations

import itertools
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import edit_gen as E
import edit_judge as J
import footprint_judge as FJ
import geom_gen as G
import kicad_corpus as K
import pcbnew_judge as PJ
import pcbnew_nets as PN
import pytest
import sexpr_gen
from conftest import assert_kicad_pcb_parseable, requires_cli
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import footprint as F
from kicad_ref import richio
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _cst, pcb
from mcp_server_kicad.models import PointSpec

MM = 1_000_000

#: edit_judge's location of a board's root list.
ROOT: J.Path = ((0, "kicad_pcb"),)

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
#: A segment clear of every pad and every other item the boards here hold. pcbnew's LoadBoard
#: puts an item whose net number names no net on the net of a pad it touches (measured
#: 2026-10-17: a segment on (net 2), no row, touching a pad on /GND, reads on /GND), so a test
#: that wants such an item read on no net keeps it clear.
_STRAY_SEGMENT = ("(start 10 10)", "(end 20 10)", "(width 0.25)", '(layer "F.Cu")')
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


#: The table a KiCad 9 board of these tests declares besides net 0: U1's nets and /SIG, whose
#: items the tests send to no net. A KiCad 10 board has no table, and nothing here needs one.
_ROWS = {"/GND": 1, "/VCC": 2, "/SIG": 3}


def _number(name: str, rows: Mapping[str, int] | None) -> int:
    """The number net ``name`` has in a KiCad 9 board's table (0 for no net, the name "")."""
    if name == "":
        return 0
    if rows is None or name not in rows:
        raise ValueError(f"the table {rows} holds no net {name!r}")
    return rows[name]


def _net_child(name: str, version: str, rows: Mapping[str, int] | None) -> str:
    """The (net ...) child of a segment, arc, via or shape on net ``name`` ("" for none) as the
    board's format writes it: KiCad 9's number, KiCad 10's quoted name."""
    if version == "k10":
        return f"(net {_quoted(name)})"
    return f"(net {_number(name, rows)})"


def _footprint(
    ref: str,
    at: tuple[int, int],
    pad_nets: dict[str, str],
    *,
    version: str = "k10",
    rows: Mapping[str, int] | None = None,
) -> str:
    """Footprint ``ref``, edit_gen's two-pad footprint as KiCad holds it at ``at`` (nm), linked
    to its symbol as the update links one with no symbol uuid (sheetname "/"), each pad of
    ``pad_nets`` on its net, written as the board's format writes a pad's net, straight after
    an smd rect pad's (layers ...): KiCad 10's (net "NAME") (format( PAD ),
    pcb_io_kicad_sexpr.cpp:1791-1858 at 10.0.7), KiCad 9's (net N "NAME") with N the net's
    number in ``rows`` (1432-1613 at 9.0.8). A pad on no net carries none."""
    if version == "k9" and pad_nets and rows is None:
        raise ValueError("a KiCad 9 pad's net needs the board's rows")
    text = E.board_footprint_text(
        E.footprint_with_pad_at(0.0), ref, at, 0.0, version=version, sheetname="/"
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
            name = pad_nets[pad]
            if version == "k10":
                out.append(f"\t\t\t(net {_quoted(name)})\n")
            else:
                out.append(f"\t\t\t(net {_number(name, rows)} {_quoted(name)})\n")
            placed.add(pad)
    if placed != set(pad_nets):
        raise RuntimeError(f"edit_gen's footprint holds no pad {sorted(set(pad_nets) - placed)}")
    return "".join(out)


def _u1(
    pad_nets: dict[str, str], *, version: str = "k10", rows: Mapping[str, int] | None = None
) -> str:
    """U1 at _U1_AT, the footprint of the netlist the update tests apply (_footprint)."""
    return _footprint("U1", _U1_AT, pad_nets, version=version, rows=rows)


def _track(
    head: str,
    children: Sequence[str],
    net: str,
    *,
    version: str = "k10",
    rows: Mapping[str, int] | None = None,
    number: int | None = None,
) -> str:
    """A segment, arc or via on ``net``: ``children`` (what comes before its net), its net and
    its uuid, in the order format( PCB_TRACK ) writes them (pcb_io_kicad_sexpr.cpp:2611-2865 at
    10.0.7, 2271-2440 at 9.0.8), each child on its own line as Prettify lays out a list of
    lists. On a KiCad 9 board the net is a number of ``rows``, or ``number`` when a test writes
    a number no row has."""
    body = "".join(f"\t\t{child}\n" for child in children)
    ref = f"(net {number})" if number is not None else _net_child(net, version, rows)
    return f'\t({head}\n{body}\t\t{ref}\n\t\t(uuid "{_uid()}")\n\t)\n'


def _zone(
    net: str,
    *,
    net_name: bool,
    version: str = "k10",
    rows: Mapping[str, int] | None = None,
    number: int | None = None,
) -> str:
    """An unfilled copper zone on F.Cu on ``net``, in the order format( ZONE ) writes one
    (pcb_io_kicad_sexpr.cpp:2868-3097 at 10.0.7): net, layer, uuid, hatch, the pad connection
    (thermal, the default, written as no word), minimum thickness, the fill settings (with the
    island removal 10.0.7 always writes, 0 for ALWAYS, the mode its parser gives a board's
    zone: pcbnew/zone_settings.h:68-73, pcb_io_kicad_sexpr_parser.cpp:8006-8007) and the
    outline, its points packed as Prettify packs them. With ``net_name``, a legacy
    (net_name ...) of the same name after the net, where KiCad 9's writer put it
    (pcb_io_kicad_sexpr.cpp:2452-2454 at 9.0.8), which KiCad 10 still reads. On a KiCad 9 board
    the zone always carries both, (net N) then (net_name "NAME"), with N its number in ``rows``
    or ``number`` when a test writes one no row has."""
    quoted = _quoted(net)
    if version == "k9":
        n = number if number is not None else _number(net, rows)
        head = f"\t\t(net {n})\n\t\t(net_name {quoted})\n"
    else:
        head = f"\t\t(net {quoted})\n" + (f"\t\t(net_name {quoted})\n" if net_name else "")
    return (
        f"\t(zone\n{head}"
        f'\t\t(layer "F.Cu")\n\t\t(uuid "{_uid()}")\n'
        "\t\t(hatch edge 0.5)\n"
        "\t\t(connect_pads\n\t\t\t(clearance 0.5)\n\t\t)\n"
        "\t\t(min_thickness 0.25)\n"
        "\t\t(fill\n\t\t\t(thermal_gap 0.5)\n\t\t\t(thermal_bridge_width 0.5)\n"
        "\t\t\t(island_removal_mode 0)\n\t\t)\n"
        "\t\t(polygon\n" + G._pts_block(_ZONE_OUTLINE, 3) + "\t\t)\n\t)\n"
    )


#: The points of the copper polygon a test puts on /SIG, in mm.
_SHAPE_POINTS = "(xy 120 120) (xy 140 120) (xy 140 140)"


def _shape(
    net: str,
    *,
    version: str = "k10",
    rows: Mapping[str, int] | None = None,
    number: int | None = None,
) -> str:
    """A gr_poly on F.Cu on ``net``, a copper shape: its points, stroke, fill, layer, net and
    uuid, the order and layout pcbnew 9.0.8 saves one in (harvested 2026-10-17 from a board
    its SaveBoard wrote: the net is (net N), a number of the table, on KiCad 9, and
    (net "NAME") on KiCad 10, both after the layer, before the uuid:
    pcb_io_kicad_sexpr.cpp:1027-1028 at 9.0.8, 1117-1118 at 10.0.7)."""
    ref = f"(net {number})" if number is not None else _net_child(net, version, rows)
    return (
        f"\t(gr_poly\n\t\t(pts\n\t\t\t{_SHAPE_POINTS}\n\t\t)\n"
        "\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n\t\t(fill no)\n"
        f'\t\t(layer "F.Cu")\n\t\t{ref}\n\t\t(uuid "{_uid()}")\n\t)\n'
    )


def _board(
    tmp_path: Path,
    items: Sequence[str],
    *,
    version: str = "k10",
    rows: Mapping[str, int] | None = None,
    net_zero: bool = True,
) -> Path:
    """A board around ``items``. A KiCad 9 board declares net 0 and then ``rows``, as KiCad
    writes the table; ``net_zero`` False leaves even net 0 out, a hand-made board with no table
    at all (KiCad writes (net 0 "") always). Version "k10dev" is a board of KiCad 10's
    development format 20250907, between KiCad 9's last and the format KiCad 10 settled on
    (20251028), which KiCad's own demos still include (StickHub): it keeps the table and the
    numbers KiCad 9 writes."""
    base = "k9" if version == "k10dev" else version
    text = E.layered_board_text(items, version=base)
    if base == "k9":
        zero = '\t(net 0 "")\n'
        table = "".join(
            f"\t(net {n} {_quoted(name)})\n"
            for name, n in sorted((rows or {}).items(), key=lambda row: row[1])
        )
        if text.count(zero) != 1:
            raise RuntimeError(f"edit_gen's KiCad 9 board holds {zero!r} {text.count(zero)} times")
        text = text.replace(zero, (zero if net_zero else "") + table, 1)
    if version == "k10dev":
        text = text.replace("(version 20241229)", "(version 20250907)", 1)
        text = text.replace('(generator_version "9.0")', '(generator_version "9.99")', 1)
    path = tmp_path / "board.kicad_pcb"
    path.write_text(text, encoding="utf-8", newline="\n")
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


def _apply_netlist(
    board: Path,
    components: Sequence[E.NetComponent],
    nets: Sequence[tuple[str, Sequence[tuple[str, str]]]],
    *,
    libraries: Sequence[Path] = (),
    delete_stale: bool = False,
    tool: str = "Eeschema 10.0.7",
) -> dict:
    """pcb._apply_netlist_cst on ``board`` with a netlist of ``components`` and ``nets`` (each
    net's name and its (reference, pin) nodes), the footprints they name resolved in the
    ``libraries`` (.pretty directories); returns the update's summary. A refusal fails the
    test: KiCad's own writer has a spelling for every item on no net."""
    netlist = board.parent / "netlist.xml"
    netlist.write_text(E.netlist_text(list(components), nets, tool=tool), encoding="utf-8")
    try:
        return pcb._apply_netlist_cst(
            str(netlist), str(board), [str(library) for library in libraries], delete_stale
        )
    except ToolError as exc:
        raise AssertionError(f"the update was refused: {exc}") from exc
    finally:
        pcb._BOARD_CACHE.pop(str(board.resolve()), None)


def _apply(
    board: Path,
    pad_nets: Mapping[str, str],
    *,
    components: Sequence[E.NetComponent] | None = None,
    delete_stale: bool = False,
    tool: str = "Eeschema 10.0.7",
) -> dict:
    """The update (_apply_netlist) of ``board`` from a netlist of ``components`` (U1 on the
    footprint it has, by default) in which U1's pads are on the nets ``pad_nets`` names, pad
    number to net."""
    return _apply_netlist(
        board,
        components if components is not None else [E.NetComponent("U1", "V", "Ladder:Syn")],
        _nets_of(pad_nets),
        delete_stale=delete_stale,
        tool=tool,
    )


def _nets_of(pad_nets: Mapping[str, str]) -> list[tuple[str, list[tuple[str, str]]]]:
    """The netlist's nets for U1's pads ``pad_nets`` (pad number to net), one net per name, in
    the order the names first appear."""
    by_net: dict[str, list[tuple[str, str]]] = {}
    for pad, net in pad_nets.items():
        by_net.setdefault(net, []).append(("U1", pad))
    return list(by_net.items())


def _update_without_sig(board: Path) -> dict:
    """The update with a netlist in which /SIG is gone: U1 on the footprint it has, its pads on
    the nets _PAD_NETS names, and no net /SIG."""
    return _apply(board, _PAD_NETS)


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


# ---------------------------------------------------------------------------
# what the tests below share
# ---------------------------------------------------------------------------

_TOOLS = ("add_trace", "add_via")


def _place(tool: str, net: int, board: Path) -> None:
    """``tool`` (add_trace or add_via) asking for ``net``, at a place clear of everything the
    boards here hold."""
    if tool == "add_trace":
        pcb.add_trace(10, 10, 20, 10, net=net, pcb_path=str(board))
    else:
        pcb.add_via(10, 10, net=net, pcb_path=str(board))


def _head_of(tool: str) -> str:
    return "segment" if tool == "add_trace" else "via"


def _refused(tool: str, net: int, board: Path) -> bool:
    """Whether ``tool`` refuses ``net`` (a ToolError), leaving ``board`` byte-identical; a
    tool that refuses and changes the file anyway fails the test."""
    before = board.read_bytes()
    try:
        _place(tool, net, board)
    except ToolError:
        assert board.read_bytes() == before, f"{tool} refused net {net} and changed the board"
        return True
    return False


def _uuid_of(item: J.Item) -> str:
    """The value of an item's (uuid ...) child, as KiCad's lexer reads it."""
    return next(c.items[1].value.decode() for c in item.items or () if c.head == "uuid")


def _kinds(item: J.Item) -> list[list[str]]:
    """The token kinds after the head of each (net ...) child of ``item``, as KiCad's lexer
    reads them: NUMBER for a bare number, STRING for a quoted token, SYMBOL for a bare word."""
    return [
        [token.kind for token in (child.items or ())[1:]]
        for child in item.items or ()
        if child.items is not None and child.head == "net"
    ]


def _need_pcbnew() -> None:
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)


def _pcbnew_nets(board: Path) -> dict:
    """What pcbnew reads of ``board``'s nets (tests/pcbnew_nets.py); RuntimeError when it
    cannot."""
    return PN.read(board)


# ---------------------------------------------------------------------------
# bug 3: a net number on a KiCad 9 board is 0 or a row of its table
# ---------------------------------------------------------------------------


def _table_board(work: str | Path, numbers: Sequence[int]) -> Path:
    """A KiCad 9 board whose table holds net 0 and one row per number of ``numbers``, row n
    named /N<n>: the numbers may leave gaps, as a table does once a prune has run."""
    return _board(Path(work), [], version="k9", rows={f"/N{n}": n for n in numbers})


@settings(max_examples=sexpr_gen.budget(4))
@example(numbers=[1, 3], net=2, tool="add_trace")
@example(numbers=[1, 2], net=3, tool="add_via")
@example(numbers=[1, 2], net=-7, tool="add_trace")
@example(numbers=[1], net=999, tool="add_via")
@given(
    numbers=st.lists(st.integers(1, 12), min_size=1, max_size=5, unique=True),
    net=st.integers(-20, 40),
    tool=st.sampled_from(_TOOLS),
)
def test_k9_numeric_net_must_name_a_row(numbers, net, tool):
    """On a KiCad 9 board a net is the number of a row of its table: the parser maps every
    number through the rows (getNetCode, pcb_io_kicad_sexpr_parser.h:156-162 at 9.0.8, with
    the map parseNETINFO_ITEM fills, zeros in the gaps, pcb_io_kicad_sexpr_parser.cpp:183-192
    and 2693-2721), a number past the table finds no net ("Invalid net ID") and a negative
    number or a gap is net 0 (BOARD_CONNECTED_ITEM::SetNetCode, pcbnew/board_connected_item.cpp:
    87-115). So a number that is neither 0 nor a row names no net, and add_trace and add_via
    asked for one must refuse it and leave the board as it was. The tools wrote it ((net 999),
    (net -7) and the number past the last row all landed behind a success message)."""
    assume(net != 0 and net not in numbers)
    with tempfile.TemporaryDirectory() as work:
        board = _table_board(work, numbers)
        assert _refused(tool, net, board), (
            f"{tool} wrote net {net}, which is no row of the table {sorted([0, *numbers])}"
        )


@settings(max_examples=sexpr_gen.budget(4))
@example(numbers=[1, 3], pick=1, tool="add_trace")
@example(numbers=[1, 3], pick=0, tool="add_via")
@given(
    numbers=st.lists(st.integers(1, 12), min_size=1, max_size=5, unique=True),
    pick=st.integers(0, 5),
    tool=st.sampled_from(_TOOLS),
)
def test_k9_valid_net_lands_on_its_row(numbers, pick, tool):
    """The passing sibling: net 0 or a row of the table, the table's gaps and all, is written
    as that number, one whole item added and every other byte kept."""
    valid = [0, *sorted(numbers)]
    net = valid[pick % len(valid)]
    with tempfile.TemporaryDirectory() as work:
        board = _table_board(work, numbers)
        before = board.read_bytes()
        _place(tool, net, board)
        (item,) = _added(before, board.read_bytes(), _head_of(tool), 1)
        assert _net_tokens(item) == [[str(net).encode()]]
        assert _kinds(item) == [[L.NUMBER]]


@pytest.mark.parametrize(
    "case",
    ["valid", "unknown"],
)
def test_k9_nets_read_by_pcbnew(tmp_path, case):
    """pcbnew reads a KiCad 9 board's nets the way the tools must write them. "valid": the
    track add_trace writes on row 3 and the via add_via writes on row 1 of a table with rows 1
    and 3 are on the nets those rows name. "unknown": the premise first (RuntimeError when it
    fails): a segment written by hand on a number that is no row, past the table (99),
    negative (-7) or in the gap (2), reads in pcbnew on no net, as the rule above says; then
    add_trace must refuse each of those numbers, which a caller got behind a success message
    for a net the file does not hold."""
    _need_pcbnew()
    rows = {"/N1": 1, "/N3": 3}
    if case == "valid":
        board = _board(tmp_path, [], version="k9", rows=rows)
        before = board.read_bytes()
        pcb.add_trace(10, 10, 20, 10, net=3, pcb_path=str(board))
        (track,) = _added(before, board.read_bytes(), "segment", 1)
        mid = board.read_bytes()
        pcb.add_via(30, 10, net=1, pcb_path=str(board))
        (via,) = _added(mid, board.read_bytes(), "via", 1)
        read = _pcbnew_nets(board)["items"]
        assert read[_uuid_of(track)][1] == "/N3", read
        assert read[_uuid_of(via)][1] == "/N1", read
        return
    unknown = (99, -7, 2)
    items = [_track("segment", _STRAY_SEGMENT, "", version="k9", number=n) for n in unknown]
    board = _board(tmp_path, items, version="k9", rows=rows)
    data = board.read_bytes()
    read = _pcbnew_nets(board)["items"]
    named = {
        n: read[_uuid_of(J.item_at(data, loc))][1]
        for n, loc in zip(unknown, J.find_paths(data, lambda loc, item: item.head == "segment"))
    }
    if any(named.values()):
        raise RuntimeError(f"the premise fails: pcbnew reads a number no row has on a net: {named}")
    accepted = [n for n in unknown if not _refused("add_trace", n, board)]
    assert not accepted, f"add_trace wrote the numbers {accepted}, which name no net in pcbnew"


# ---------------------------------------------------------------------------
# a net is written in the board's dialect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", _TOOLS)
@pytest.mark.parametrize("version", ["k9", "k10"])
def test_net_dialect_follows_the_board_version(tmp_path, tool, version):
    """The passing sibling of bug 3's tests: add_trace and add_via write a number on a board of
    format 20241229 (KiCad 9's parser takes only parseInt( "net number" ) there, so a quoted
    name makes the whole board unloadable: pcb_io_kicad_sexpr_parser.cpp:6386 and 6497 at
    9.0.8) and a quoted name on a board of format 20260206 (KiCad 10's writer stopped writing
    net codes at format 20251028, pcb_io_kicad_sexpr.h:201 at 10.0.7, and its parseNet reads a
    name, 296-342). No assertion is made for the formats between, where KiCad's own choice
    is not determined."""
    if version == "k9":
        board = _board(tmp_path, [], version="k9", rows={"/GND": 1})
    else:
        board = _board(tmp_path, [_u1({"1": "/GND"})], version="k10")
    before = board.read_bytes()
    _place(tool, 1, board)
    (item,) = _added(before, board.read_bytes(), _head_of(tool), 1)
    if version == "k9":
        assert _kinds(item) == [[L.NUMBER]]
        assert _net_tokens(item) == [[b"1"]]
    else:
        assert _kinds(item) == [[L.STRING]]
        assert _net_tokens(item) == [[b'"/GND"']]


@pytest.mark.parametrize("name", ["/GND", 'A"B', "Net-(U1-Pad1)"])
@pytest.mark.parametrize("version", ["k9", "k10"])
def test_zone_net_is_written_in_the_boards_dialect(tmp_path, version, name):
    """The passing sibling: add_copper_zone on a net writes the zone's net as the board's
    format writes a zone's: on a KiCad 9 board (net N), N the net's row, and (net_name "NAME"),
    where format( ZONE ) puts both (pcb_io_kicad_sexpr.cpp:2448-2454 at 9.0.8); on a KiCad 10
    board (net "NAME") alone (2868-2876 at 10.0.7), with no net_name. The name is read back
    through KiCad's lexer, so a quote in it survives."""
    corners: list[PointSpec] = [{"x": 10, "y": 10}, {"x": 40, "y": 10}, {"x": 40, "y": 40}]
    if version == "k9":
        board = _board(tmp_path, [], version="k9", rows={"/OTHER": 1, name: 2})
    else:
        board = _board(tmp_path, [_u1({"1": "/OTHER", "2": name})], version="k10")
    before = board.read_bytes()
    pcb.add_copper_zone(name, "F.Cu", corners, pcb_path=str(board))
    (zone,) = _added(before, board.read_bytes(), "zone", 1)
    named = [
        (child.head, [token.value for token in child.items[1:]])
        for child in zone.items or ()
        if child.items is not None and child.head in ("net", "net_name")
    ]
    if version == "k9":
        assert named == [("net", [b"2"]), ("net_name", [name.encode()])]
    else:
        assert named == [("net", [name.encode()])]


# ---------------------------------------------------------------------------
# bug 19: a KiCad 9 zone is on the net its net_name names
# ---------------------------------------------------------------------------


def _edits(before: bytes, after: bytes) -> list[tuple]:
    """The structural edits that turn ``before`` into ``after`` (edit_judge.diff), each as
    (operation, the labels along its location, its old bytes, its new bytes), sorted."""
    return sorted(
        (e.op, tuple(label for _, label in e.location), e.old, e.new) for e in J.diff(before, after)
    )


def _zone_board(tmp_path: Path, zone: str) -> Path:
    """A KiCad 9 board holding U1 (pads on /GND and /VCC), a segment on /GND and ``zone``, its
    table _ROWS."""
    items = [
        _u1(_PAD_NETS, version="k9", rows=_ROWS),
        _track("segment", _GND_SEGMENT, "/GND", version="k9", rows=_ROWS),
        zone,
    ]
    return _board(tmp_path, items, version="k9", rows=_ROWS)


def test_k9_orphaned_zone_drops_its_net_name(tmp_path):
    """A KiCad 9 board holding a copper zone on /SIG, updated from a netlist without /SIG: the
    zone is counted in orphaned_zones and ends on no net the way KiCad 9 writes a zone on none,
    (net 0) and (net_name ""), and the update changes nothing else but the pruned row. KiCad 9
    reads a copper zone by its net_name: when it is not the name of the net its number names,
    the parser finds or creates the net of that name (pcb_io_kicad_sexpr_parser.cpp:7387-7412
    at 9.0.8), so a zone left with the old name is rebound to a /SIG of its own making; the
    tool wrote (net 0) and kept the name."""
    board = _zone_board(tmp_path, _zone("/SIG", net_name=True, version="k9", rows=_ROWS))
    before = board.read_bytes()
    summary = _update_without_sig(board)
    assert summary["orphaned_zones"] == 1, summary
    assert _edits(before, board.read_bytes()) == [
        ("atom", ("kicad_pcb", "zone", "net", "3"), b"3", b"0"),
        ("atom", ("kicad_pcb", "zone", "net_name", "/SIG"), b'"/SIG"', b'""'),
        ("delete", ("kicad_pcb", "net"), b'(net 3 "/SIG")', None),
    ]


def _zone_net_in_pcbnew(board: Path, uuid: str) -> tuple[list, list]:
    """(the zone's [code, name], the nets) as pcbnew reads ``board``."""
    read = _pcbnew_nets(board)
    return read["items"][uuid], read["nets"]


def test_k9_orphaned_zone_reads_on_no_net_in_pcbnew(tmp_path):
    """The same update, judged by pcbnew 9 reading the result: the zone is on no net (name "")
    and the board has no /SIG net; before the update it is on /SIG. Skipped on pcbnew 10,
    which reads a KiCad 9 zone by the number when its legacy name is empty: this is pcbnew 9's
    rule (the module docstring of tests/test_ladder_r3_judges.py keeps the same line)."""
    _need_pcbnew()
    if PJ.major() >= 10:
        pytest.skip("pcbnew 10 reads a KiCad 9 board through its legacy rules; this is pcbnew 9's")
    board = _zone_board(tmp_path, _zone("/SIG", net_name=True, version="k9", rows=_ROWS))
    uuid = _uuid_of(J.item_at(board.read_bytes(), _the(board.read_bytes(), "kicad_pcb", "zone")))
    zone_net, nets = _zone_net_in_pcbnew(board, uuid)
    if zone_net[1] != "/SIG":
        raise RuntimeError(f"the premise fails: pcbnew reads the zone on {zone_net} before it")
    _update_without_sig(board)
    zone_net, nets = _zone_net_in_pcbnew(board, uuid)
    assert zone_net[1] == "", f"the zone is still on {zone_net} in pcbnew 9"
    assert "/SIG" not in [name for _, name in nets], f"pcbnew 9 recreated /SIG: {nets}"


def test_a_zone_left_with_a_stale_net_name_is_orphaned(tmp_path):
    """The damage an earlier version left: a KiCad 9 zone on (net 0) whose net_name still reads
    /SIG, a net the table no longer holds. KiCad 9 reads that zone on a /SIG it makes up
    (pcbnew 9, below, when it is the judge), so the next update, from a netlist without /SIG,
    must find the zone on /SIG, count it in orphaned_zones and empty its net_name: the one
    change to the file. The orphan pass read the zone's net by its number and never saw it."""
    zone = _zone("/SIG", net_name=True, version="k9", number=0)
    board = _board(
        tmp_path,
        [
            _u1(_PAD_NETS, version="k9", rows=_ROWS),
            zone,
        ],
        version="k9",
        rows={"/GND": 1, "/VCC": 2},
    )
    before = board.read_bytes()
    zone_uuid = _uuid_of(J.item_at(before, _the(before, "kicad_pcb", "zone")))
    on_pcbnew_9 = PJ.skip_reason() is None and PJ.major() < 10
    if on_pcbnew_9:
        zone_net, nets = _zone_net_in_pcbnew(board, zone_uuid)
        if zone_net[1] != "/SIG":
            raise RuntimeError(f"the premise fails: pcbnew 9 reads the stale zone on {zone_net}")
    summary = _update_without_sig(board)
    assert summary["orphaned_zones"] == 1, summary
    assert _edits(before, board.read_bytes()) == [
        ("atom", ("kicad_pcb", "zone", "net_name", "/SIG"), b'"/SIG"', b'""'),
    ]
    if on_pcbnew_9:
        zone_net, nets = _zone_net_in_pcbnew(board, zone_uuid)
        assert zone_net[1] == "" and "/SIG" not in [name for _, name in nets], (zone_net, nets)


# ---------------------------------------------------------------------------
# bugs 99, 101 and 100: the copper shapes, the numbers and the rows of the update
# ---------------------------------------------------------------------------


def _rows_of(data: bytes) -> dict[int, str]:
    """A board's net table as KiCad's lexer reads it: the root (net N "NAME") rows."""
    return {
        int(row.items[1].value): row.items[2].value.decode()
        for row in F.children(FJ.root(data), b"net")
        if len(row.items) > 2
    }


def _pad_nets_of(data: bytes, ref: str) -> dict[str, list[str]]:
    """The tokens after the head of each pad's (net ...) of footprint ``ref``, by pad number,
    as KiCad's lexer reads them; a pad with no net is left out."""
    out = {}
    for pad in F.pad_groups(FJ.footprint_group(data, ref)):
        net = F.child(pad, b"net")
        if net is not None:
            out[pad.items[1].value.decode()] = [t.value.decode() for t in net.items[1:]]
    return out


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_a_copper_shape_on_a_net_that_left_goes_to_no_net(tmp_path, version):
    """A copper gr_poly on /SIG (KiCad ships such shapes, the RoyalBlue54L-NFC-Antenna board's
    four among them), on a board of either format, updated from a netlist without /SIG: the
    shape loses its (net ...), as KiCad writes a shape on net 0 (pcb_io_kicad_sexpr.cpp:
    1027-1028 at 9.0.8, 1117-1118 at 10.0.7), the update names it in warnings (there is no
    field to count it in), and a KiCad 9 board's /SIG row goes. KiCad's own update drops the
    unused net through its commit, which calls BOARD::Remove, and that sends every connected
    item of the net, drawings among them, to net 0 (pcbnew/board_commit.cpp:399-400 and
    board.cpp:1328-1341 and 2898-2921 at 9.0.8; board_commit.cpp:490-491 and board.cpp:
    1501-1514 and 3542-3574 at 10.0.7). The pass skipped the shape: it kept (net 3) after the
    row was pruned, a number no row has, and (net "/SIG") on a KiCad 10 board."""
    rows = _ROWS if version == "k9" else None
    items = [
        _u1(_PAD_NETS, version=version, rows=rows),
        _shape("/SIG", version=version, rows=rows),
    ]
    board = _board(tmp_path, items, version=version, rows=rows)
    before = board.read_bytes()
    summary = _apply(board, _PAD_NETS)
    shape_net = b"(net 3)" if version == "k9" else b'(net "/SIG")'
    expected: list[tuple] = [("delete", ("kicad_pcb", "gr_poly", "net"), shape_net, None)]
    if version == "k9":
        expected.append(("delete", ("kicad_pcb", "net"), b'(net 3 "/SIG")', None))
    assert _edits(before, board.read_bytes()) == sorted(expected)
    assert summary["orphaned_tracks"] == summary["orphaned_zones"] == 0, summary
    assert any("gr_poly" in w and "/SIG" in w for w in summary["warnings"]), summary["warnings"]


def test_a_copper_shape_on_a_net_that_left_reads_on_no_net_in_pcbnew(tmp_path):
    """The KiCad 9 half of the test above, judged by pcbnew 9: the shape reads on /SIG before
    the update (the premise, RuntimeError otherwise) and on no net after it, with no /SIG net
    in the board. Skipped on pcbnew 10, as the zone's test is. It reads on no net today too, by
    accident: the shape keeps (net 3) after the row is pruned, a number past the table, which
    pcbnew logs as an invalid net ID and reads on none (measured 2026-10-17),
    so this guards the reading the fix must keep and the test above judges the number."""
    _need_pcbnew()
    if PJ.major() >= 10:
        pytest.skip("pcbnew 10 reads a KiCad 9 board through its legacy rules; this is pcbnew 9's")
    board = _board(
        tmp_path,
        [_u1(_PAD_NETS, version="k9", rows=_ROWS), _shape("/SIG", version="k9", rows=_ROWS)],
        version="k9",
        rows=_ROWS,
    )
    uuid = _uuid_of(J.item_at(board.read_bytes(), _the(board.read_bytes(), "kicad_pcb", "gr_poly")))
    read = _pcbnew_nets(board)
    if read["items"][uuid][1] != "/SIG":
        raise RuntimeError(f"the premise fails: pcbnew reads the shape on {read['items'][uuid]}")
    _apply(board, _PAD_NETS)
    read = _pcbnew_nets(board)
    assert read["items"][uuid][1] == "", f"the shape is on {read['items'][uuid]} in pcbnew 9"
    assert "/SIG" not in [name for _, name in read["nets"]], read["nets"]


def test_a_copper_shape_never_follows_a_number_a_later_net_takes(tmp_path):
    """Two updates on a KiCad 9 board whose /SIG row is the table's last (2) and whose copper
    shape is on it. The first, from a netlist without /SIG, prunes the row; the second adds
    /NEW. A shape left on (net 2) after the first is on /NEW after the second, the new row
    taking the number the pruned one gave up: pcbnew 9 read it on no net, with "Invalid net
    ID", between the two and on /NEW after. After the fix the shape has left net 2 at the
    first update, and no net reference of the board is a number the shape could take."""
    rows = {"/GND": 1, "/SIG": 2}
    board = _board(
        tmp_path,
        [
            _u1({"1": "/SIG", "2": "/GND"}, version="k9", rows=rows),
            _shape("/SIG", version="k9", rows=rows),
        ],
        version="k9",
        rows=rows,
    )
    _apply(board, {"2": "/GND"})
    summary = _apply(board, {"1": "/NEW", "2": "/GND"})
    assert summary["nets_added"] == 1, summary
    data = board.read_bytes()
    shape = J.item_at(data, _the(data, "kicad_pcb", "gr_poly"))
    refs = [t.value for c in shape.items or () if c.head == "net" for t in c.items[1:]]
    named = [_rows_of(data).get(int(ref)) for ref in refs]
    assert named == [], f"the shape is on {named} after a net was added"


def test_a_new_net_row_never_takes_a_number_an_item_uses(tmp_path):
    """A KiCad 9 board whose table holds net 0 and /GND (1) and a segment on (net 2), a number
    no row has, as an earlier add_trace left it: pcbnew 9 reads the segment on no net. An
    update that adds /NEW must give it a number other than 2, since a row numbered 2 binds the
    segment to /NEW (the parser maps every number through the rows, getNetCode,
    pcb_io_kicad_sexpr_parser.h:156-162 at 9.0.8). The new row took 2: the segment was on no
    net before the update and on /NEW after it, silently."""
    stale = _track("segment", _STRAY_SEGMENT, "", version="k9", number=2)
    board = _board(
        tmp_path,
        [_u1({"1": "/GND"}, version="k9", rows={"/GND": 1}), stale],
        version="k9",
        rows={"/GND": 1},
    )
    segment_uuid = _uuid_of(
        J.item_at(board.read_bytes(), _the(board.read_bytes(), "kicad_pcb", "segment"))
    )
    on_pcbnew_9 = PJ.skip_reason() is None and PJ.major() < 10
    if on_pcbnew_9 and _pcbnew_nets(board)["items"][segment_uuid][1] != "":
        raise RuntimeError("the premise fails: pcbnew 9 reads the unrowed segment on a net")
    summary = _apply(board, {"1": "/GND", "2": "/NEW"})
    assert summary["nets_added"] == 1, summary
    rows = _rows_of(board.read_bytes())
    assert 2 not in rows, f"the new row is numbered 2, the segment's number: {rows}"
    if on_pcbnew_9:
        read = _pcbnew_nets(board)["items"][segment_uuid]
        assert read[1] == "", f"pcbnew 9 reads the segment on {read} after the update"


def test_every_net_the_import_binds_has_a_row(tmp_path):
    """A KiCad 9 board with no net table at all (hand-made: KiCad writes (net 0 "") always)
    holding a copper zone whose net_name is GND, imported with a netlist that puts U1's pad 1
    on GND and its pad 2 on SIG. Every pad's (net N "NAME") must name a row of the table
    after the import: KiCad 9 binds a number only through a row, and a pad whose number is no
    row's is on no net (pcb_io_kicad_sexpr_parser.cpp:5219-5240 at 9.0.8). The import took
    GND for declared because the zone spells it, wrote pad 1 as (net 0 "GND") and declared
    only SIG, so pcbnew 9 read pad 1 on no net."""
    zone = _zone("GND", net_name=True, version="k9", number=0)
    board = _board(tmp_path, [_u1({}, version="k9"), zone], version="k9", rows=None, net_zero=False)
    assert _rows_of(board.read_bytes()) == {}
    summary = _apply(board, {"1": "GND", "2": "SIG"})
    assert summary["pads_bound"] == 2, summary
    data = board.read_bytes()
    rows = _rows_of(data)
    bound = _pad_nets_of(data, "U1")
    unrowed = {
        pad: tokens
        for pad, tokens in bound.items()
        if len(tokens) != 2 or rows.get(int(tokens[0])) != tokens[1]
    }
    assert not unrowed, f"pads whose net is no row of the table {rows}: {unrowed}"
    if PJ.skip_reason() is None:
        read = _pcbnew_nets(board)["pads"]["U1"]
        assert {pad: net[1] for pad, net in read.items()} == {"1": "GND", "2": "SIG"}, read


# ---------------------------------------------------------------------------
# a net a footprint that stays is on has not left
# ---------------------------------------------------------------------------

#: Where R5 sits, in nm: its pad 1 is at (79, 40) mm on the board, where the segment on /X ends.
_R5_AT = (80 * MM, 40 * MM)
#: The table of a KiCad 9 board below: U1's nets, R5's /X, and /Y, which no pad is on.
_KEPT_ROWS = {"/GND": 1, "/VCC": 2, "/X": 3, "/Y": 4}
#: A segment on /X from R5's pad 1 to (70, 40).
_X_SEGMENT = ("(start 79 40)", "(end 70 40)", "(width 0.25)", '(layer "F.Cu")')

#: How R5, which the netlist leaves out, stays on the board: (the update's delete_stale, the
#: lock edit_gen.with_lock spells or None, the (attr ...) list that replaces (attr smd) or
#: None). KiCad's update removes none of them (board_netlist_updater.cpp:1427-1454 at 9.0.8).
_STAYS = {
    "stale": (False, None, None),
    "locked": (True, "(locked yes)", None),
    "board only": (True, None, "attr smd board_only"),
}


def _kept_board(tmp_path: Path, version: str, *, lock: str | None, attr: str | None) -> Path:
    """A board of ``version`` holding U1 (pads on /GND and /VCC) and R5 (pad 1 on /X, with the
    lock and the (attr ...) that make it stay), and on /X a segment ending on R5's pad 1, a
    via, a copper zone and a copper polygon, and on /Y, which no pad is on, a segment."""
    rows = _KEPT_ROWS if version == "k9" else None
    r5 = _footprint("R5", _R5_AT, {"1": "/X"}, version=version, rows=rows)
    if lock is not None:
        r5 = E.with_lock(r5, lock)
    if attr is not None:
        if r5.count("\t\t(attr smd)\n") != 1:
            raise RuntimeError("edit_gen's footprint no longer holds one (attr smd)")
        r5 = r5.replace("\t\t(attr smd)\n", f"\t\t({attr})\n")
    items = [
        _u1(_PAD_NETS, version=version, rows=rows),
        r5,
        _track("segment", _X_SEGMENT, "/X", version=version, rows=rows),
        _track("via", _SIG_TRACKS["via"], "/X", version=version, rows=rows),
        _zone("/X", net_name=False, version=version, rows=rows),
        _shape("/X", version=version, rows=rows),
        _track("segment", _STRAY_SEGMENT, "/Y", version=version, rows=rows),
    ]
    return _board(tmp_path, items, version=version, rows=rows)


def _pcbnew_item_nets(board: Path, version: str) -> list[str] | None:
    """The sorted net names pcbnew reads on the items (tracks, zones, shapes) of ``board``, or
    None when this machine's pcbnew cannot load a board of ``version``."""
    if PJ.skip_reason() is not None or PJ.major() < (10 if version == "k10" else 9):
        return None
    return sorted(name for _, name in _pcbnew_nets(board)["items"].values())


@pytest.mark.parametrize("stays", list(_STAYS))
@pytest.mark.parametrize("version", ["k9", "k10"])
def test_a_net_a_footprint_that_stays_is_on_stays_whole(tmp_path, version, stays):
    """The board of _kept_board, updated from a netlist that names U1 alone, so /X left the
    schematic and R5 is stale; R5 stays, unlocked under the default delete_stale, locked or
    marked board only under delete_stale (KiCad's update removes neither of those,
    board_netlist_updater.cpp:1427-1454 at 9.0.8, 2334-2387 at 10.0.7). Its pad is on /X, so
    /X is not an unused net: the update marks the net of every pad of every footprint it keeps
    current before it removes the nets that are not (the loop at board_netlist_updater.cpp:
    1475-1485 and RemoveUnusedNets at 1506, 9.0.8; 2406-2416 and 2438, 10.0.7), and only the
    removal of a net, which BOARD::Remove does through the commit, sends the items on it to net
    0 (board_commit.cpp:399-400 and board.cpp:1328-1341 at 9.0.8). So /X keeps its row, R5's
    pad, and the segment, via, zone and polygon on it, and the one change to the file is the
    segment on /Y, a net no pad is on, going to no net with its row on a KiCad 9 board. The
    pass sent all four items on /X to no net while R5's pad stayed on it (measured 2026-10-17:
    pcbnew 9.0.8 read the pad on /X, and the polygon on no net). No pad of a footprint the
    netlist names leaves /X here, so KiCad's reconnection of the vias and zones of a renamed
    net has nothing to move; the test below is the board where a pad does leave."""
    delete_stale, lock, attr = _STAYS[stays]
    board = _kept_board(tmp_path, version, lock=lock, attr=attr)
    before = board.read_bytes()
    held = _pcbnew_item_nets(board, version)
    if held is not None and held != ["/X"] * 4 + ["/Y"]:
        raise RuntimeError(f"the premise fails: pcbnew reads the items on {held}")
    summary = _apply(board, _PAD_NETS, delete_stale=delete_stale)
    assert summary["stale_footprints"] == ["R5"] and summary["stale_removed"] == [], summary
    assert summary["orphaned_tracks"] == 1 and summary["orphaned_zones"] == 0, summary
    assert summary["warnings"] == [], summary
    stray = ("kicad_pcb", "segment", "net")
    expected: list[tuple] = (
        [
            ("atom", (*stray, "4"), b"4", b"0"),
            ("delete", ("kicad_pcb", "net"), b'(net 4 "/Y")', None),
        ]
        if version == "k9"
        else [("atom", (*stray, "/Y"), b'"/Y"', _NO_NET)]
    )
    assert _edits(before, board.read_bytes()) == sorted(expected)
    assert summary["nets_removed"] == (1 if version == "k9" else 0), summary
    if held is not None:
        after = _pcbnew_item_nets(board, version)
        assert after == ["", *["/X"] * 4], f"pcbnew reads the items on {after} after the update"
        assert _pcbnew_nets(board)["pads"]["R5"]["1"][1] == "/X"


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_a_net_no_pad_that_stays_is_on_goes_to_no_net_with_all_on_it(tmp_path, version):
    """The passing sibling of the test above: the same board with R5 removed under delete_stale
    (unlocked, not board only), so no pad that stays is on /X or /Y. Everything on them goes to
    no net, written as the board's format writes none: the segments and the via are counted in
    orphaned_tracks, the zone in orphaned_zones and the polygon named in warnings, and a KiCad
    9 board loses both rows."""
    board = _kept_board(tmp_path, version, lock=None, attr=None)
    summary = _apply(board, _PAD_NETS, delete_stale=True)
    assert summary["stale_removed"] == ["R5"], summary
    assert summary["orphaned_tracks"] == 3 and summary["orphaned_zones"] == 1, summary
    assert len(summary["warnings"]) == 1 and "gr_poly" in summary["warnings"][0], summary
    assert summary["nets_removed"] == (2 if version == "k9" else 0), summary
    written = _net_tokens_by_kind(board.read_bytes())
    no_track = [b"(net 0)"] if version == "k9" else [b'(net "")']
    assert written["segment"] == [no_track] * 2 and written["via"] == [no_track]
    assert written["zone"] == ([[b"(net 0)", b'(net_name "")']] if version == "k9" else [[]])
    assert written["gr_poly"] == [[]]
    read = _pcbnew_item_nets(board, version)
    if read is not None:
        assert read == [""] * 5, f"pcbnew reads the items on {read} after the update"


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_a_renamed_net_a_footprint_that_stays_is_on_keeps_what_is_on_it(tmp_path, version):
    """Where this update parts from KiCad's own, pinned as the tool's description states it.
    U1's pad 2 and the pad of R5, which the netlist leaves out and which stays under the
    default delete_stale, are on /X, and a segment, a via, a copper zone and a copper polygon
    are on /X. The netlist names U1 alone and puts its pad 2 on /Y: /X is renamed. KiCad's
    update records the pair when it moves the pad (m_oldToNewNets, board_netlist_updater.cpp:
    932 at 9.0.8, 1202 at 10.0.7), and updateCopperZoneNets, which runs before the stale
    footprints are settled, moves the via and the zone to /Y whether or not a pad that stays
    holds /X (1005-1221 and the call at 1417, 9.0.8; 1676-1892 and 2323, 10.0.7); /X stays
    current through R5's pad and keeps the segment and the polygon. Neither kicad-cli nor
    pcbnew's API runs the updater, so that half rests on the source. This update reconnects
    nothing: its one change is U1's pad 2 on /Y (with a row for /Y on a KiCad 9 board), the
    segment, via, zone and polygon stay on /X with R5's pad, /X keeps its row, and nothing
    is orphaned or warned of. The day the update follows KiCad this test changes with the
    description and the ADR entry."""
    rows = {"/GND": 1, "/X": 2} if version == "k9" else None
    board = _board(
        tmp_path,
        [
            _footprint("U1", _U1_AT, {"1": "/GND", "2": "/X"}, version=version, rows=rows),
            _footprint("R5", _R5_AT, {"1": "/X"}, version=version, rows=rows),
            _track("segment", _X_SEGMENT, "/X", version=version, rows=rows),
            _track("via", _SIG_TRACKS["via"], "/X", version=version, rows=rows),
            _zone("/X", net_name=False, version=version, rows=rows),
            _shape("/X", version=version, rows=rows),
        ],
        version=version,
        rows=rows,
    )
    before = board.read_bytes()
    held = _pcbnew_item_nets(board, version)
    if held is not None and held != ["/X"] * 4:
        raise RuntimeError(f"the premise fails: pcbnew reads the items on {held}")
    summary = _apply_netlist(
        board,
        [E.NetComponent("U1", "V", "Ladder:Syn")],
        [("/GND", [("U1", "1")]), ("/Y", [("U1", "2")])],
    )
    assert summary["stale_footprints"] == ["R5"] and summary["stale_removed"] == [], summary
    assert summary["orphaned_tracks"] == summary["orphaned_zones"] == 0, summary
    assert summary["warnings"] == [], summary
    pad_net = ("kicad_pcb", "footprint", "pad", "net")
    expected: list[tuple] = [("atom", (*pad_net, "/X"), b'"/X"', b'"/Y"')]
    if version == "k9":
        expected += [
            ("atom", (*pad_net, "2"), b"2", b"3"),
            ("insert", ("kicad_pcb", "net"), None, b'(net 3 "/Y")'),
        ]
    assert _edits(before, board.read_bytes()) == sorted(expected)
    assert summary["nets_removed"] == 0, summary
    if held is not None:
        after = _pcbnew_item_nets(board, version)
        assert after == ["/X"] * 4, f"pcbnew reads the items on {after} after the update"
        pads = _pcbnew_nets(board)["pads"]
        assert (pads["R5"]["1"][1], pads["U1"]["2"][1]) == ("/X", "/Y"), pads


# ---------------------------------------------------------------------------
# bug 20: a quoted net atom is a name, even when it is all digits
# ---------------------------------------------------------------------------

#: Names a KiCad 10 board can give its nets, quoted as KiCad writes every net name (Quotew,
#: pcb_io_kicad_sexpr.cpp:1856-1858 and 2860-2861 at 10.0.7): digits, a leading zero, a sign,
#: net 0's number, and a decimal.
_DIGIT_NAMES = ("5", "007", "-7", "0", "1.5")


def _digit_board(tmp_path: Path) -> Path:
    """A KiCad 10 board with a net named by each of _DIGIT_NAMES, in this order of appearance:
    "5" and "007" on U1's pads, "-7" on a segment, "0" on a via, "1.5" on a zone."""
    items = [
        _u1({"1": "5", "2": "007"}),
        _track("segment", _STRAY_SEGMENT, "-7"),
        _track("via", _SIG_TRACKS["via"], "0"),
        _zone("1.5", net_name=False),
    ]
    return _board(tmp_path, items, version="k10")


def test_kicad10_net_names_are_names(tmp_path):
    """A KiCad 10 board whose nets are named 5, 007, -7, 0 and 1.5, each quoted as KiCad writes
    a name. KiCad's lexer reads a quoted token as a STRING and only an unquoted number as a
    NUMBER (common/dsnlexer.cpp:495-542 and 812-816 at 9.0.8, 491-538 and 815-819 at 10.0.7),
    and parseNet takes a NUMBER for a legacy code and anything else for a name
    (pcb_io_kicad_sexpr_parser.cpp:296-342 at 10.0.7), so these are five names. Every read tool
    reports them as such, list_pcb_traces' numbers resolve to them, and the writers write each
    back as a STRING token of its name: add_copper_zone on "5", add_trace and add_via on the
    number list_pcb_nets gives each. The tools read every one of the four digit strings as a
    number: list_pcb_nets answered one net, "1.5"."""
    board = _digit_board(tmp_path)
    path = str(board)
    assert [(n.number, n.name) for n in pcb.list_pcb_nets(path)] == [
        (i, name) for i, name in enumerate(_DIGIT_NAMES, start=1)
    ]
    assert [(t.type, t.net) for t in pcb.list_pcb_traces(path)] == [("segment", 3), ("via", 4)]
    pads = pcb.get_footprint_pads("U1", path)
    assert "net=5" in pads and "net=007" in pads, pads
    assert [z.net_name for z in pcb.list_pcb_zones(path)] == ["1.5"]
    assert pcb.set_trace_width(0.5, net_name="-7", pcb_path=path).traces_modified == 1
    before = board.read_bytes()
    corners: list[PointSpec] = [{"x": 10, "y": 10}, {"x": 40, "y": 10}, {"x": 40, "y": 40}]
    pcb.add_copper_zone("5", "F.Cu", corners, pcb_path=path)
    (zone,) = _added(before, board.read_bytes(), "zone", 1)
    assert _kinds(zone) == [[L.STRING]] and _net_tokens(zone) == [[b'"5"']]
    for tool, number, name in (
        ("add_trace", 3, "-7"),
        ("add_via", 4, "0"),
        ("add_trace", 2, "007"),
    ):
        before = board.read_bytes()
        _place(tool, number, board)
        (item,) = _added(before, board.read_bytes(), _head_of(tool), 1)
        assert _kinds(item) == [[L.STRING]], (tool, name, _kinds(item))
        assert _net_tokens(item) == [[_quoted(name).encode()]], (tool, name)


def test_kicad9_bare_net_numbers_read_as_numbers(tmp_path):
    """The passing sibling: on a KiCad 9 board a bare number is a row number even where a net
    is named by digits. The table holds row 1 named "2" and row 2 named "A": a segment on (net
    2) is on A, the pad written (net 1 "2") is on the net named 2, and the zone with (net 1)
    and (net_name "2") is on it too. A reading that took every digit string for a name would
    put the segment on the net named 2."""
    rows = {"2": 1, "A": 2}
    items = [
        _u1({"1": "2", "2": "A"}, version="k9", rows=rows),
        _track("segment", _STRAY_SEGMENT, "A", version="k9", rows=rows),
        _zone("2", net_name=True, version="k9", rows=rows),
    ]
    board = _board(tmp_path, items, version="k9", rows=rows)
    path = str(board)
    assert [(t.type, t.net) for t in pcb.list_pcb_traces(path)] == [("segment", 2)]
    assert [(n.number, n.name) for n in pcb.list_pcb_nets(path)] == [(1, "2"), (2, "A")]
    pads = pcb.get_footprint_pads("U1", path)
    assert "net=2" in pads and "net=A" in pads, pads
    assert [z.net_name for z in pcb.list_pcb_zones(path)] == ["2"]
    root = _cst.parse(board.read_bytes()).lists[0]
    assert pcb._net_name_of(root, root.find("segment")) == "A"


_ATOM_TEXT = st.text(
    st.characters(min_codepoint=0x20, max_codepoint=0x7E, exclude_characters='\\"'), max_size=8
)
_ATOM_SPELLINGS = st.one_of(
    sexpr_gen.bare_token(),
    sexpr_gen.quoted_atom(_ATOM_TEXT),
    sexpr_gen.quoted_atom(st.from_regex(r"[-+]?[0-9]{0,3}\.?[0-9]{0,3}", fullmatch=True)),
)


@settings(max_examples=sexpr_gen.budget(2))
@example(spelling=b'"5"')
@example(spelling=b'"-7"')
@example(spelling=b"1.5")
@example(spelling=b"5")
@example(spelling=b"GND")
@given(spelling=_ATOM_SPELLINGS)
def test_net_code_matches_kicads_lexer(spelling):
    """A pad's (net ...) on a KiCad 10 board, spelled with any atom a KiCad writer or a hand
    could put there, quoted or bare. KiCad's lexer (the port in tests/kicad_ref) says whether
    the atom is a NUMBER or a STRING or SYMBOL. KiCad 10 takes a STRING or SYMBOL for the net's
    name (the pad's branch of the parser, pcb_io_kicad_sexpr_parser.cpp:6185-6241 at 10.0.7),
    and the tools read it as that name. A NUMBER it takes for a legacy code and then requires a
    name after (6210, Expecting( "net name" )), so a pad holding a number alone is a board it
    refuses (test_kicad_refuses_a_pad_holding_a_net_number_alone): there is no reading for the
    tools to agree with, and none is asserted. What is judged for a NUMBER is its number, the
    integer strtol takes from it (parseInt), which pcb._net_code agrees with, as it gives none
    for a name. The tools took a quoted string of digits for a number, and a bare decimal such
    as 1.5 for a name."""
    item = _u1({"1": "PLACEHOLDER"})
    text = E.layered_board_text(
        [item.replace('(net "PLACEHOLDER")', f"(net {spelling.decode()})")], version="k10"
    )
    data = text.encode()
    token = [t for t in L.tokens(spelling, knows_bar=True)]
    assert len(token) == 1
    root = _cst.parse(data).lists[0]
    pad = root.find("footprint").find_all("pad")[0]
    if token[0].kind != L.NUMBER:
        assert pcb._net_name_of(root, pad) == token[0].value.decode(), (
            f"{spelling!r} is a {token[0].kind}"
        )
    atom = pad.find("net").atoms[1]
    number = L._parse_int(token[0].value) if token[0].kind == L.NUMBER else None
    assert pcb._net_code(atom) == number, f"{spelling!r} is a {token[0].kind}"


@requires_cli
@pytest.mark.kicad_placeholder("board.kicad_pcb")
def test_kicad_refuses_a_pad_holding_a_net_number_alone(tmp_path):
    """A pad whose (net ...) holds a bare number and nothing after it is a board KiCad does
    not load: the pad's branch of the parser reads the number as a legacy code and then needs
    a net name (NeedSYMBOLorNUMBER, pcb_io_kicad_sexpr_parser.cpp:5225 at 9.0.8;
    Expecting( "net name" ), 6210 at 10.0.7), so kicad-cli answers "Failed to load board"
    (measured 2026-10-17 on kicad-cli 9.0.8). That is why no reading of such a pad is
    asserted anywhere in this file. The board is KiCad 9's, which the KiCad 10 legs load too."""
    rows = {"/GND": 1, "/VCC": 2}
    pad = _u1(_PAD_NETS, version="k9", rows=rows).replace('(net 1 "/GND")', "(net 5)")
    board = _board(tmp_path, [pad], version="k9", rows=rows)
    with pytest.raises(pytest.fail.Exception, match="kicad-cli cannot parse board.kicad_pcb"):
        assert_kicad_pcb_parseable(board)


# ---------------------------------------------------------------------------
# a bare net number on a board that holds no table
# ---------------------------------------------------------------------------

#: The pads of the footprint the tests below put on nets: a KiCad that creates a net where it
#: first meets its name creates /B, then /A (pad 3 is on /B again).
_THREE_NETS = {"1": "/B", "2": "/A", "3": "/B"}
#: The bare numbers the segments below are written with: 0 is net 0, 1 and 2 are the nets a
#: loading KiCad 10 has created by the time it reaches them, 3 and 4 are past them.
_BARE = (0, 1, 2, 3, 4)


def _numbered_board(tmp_path: Path, version: str) -> Path:
    """A board of ``version`` that holds no table: U1 with three pads on _THREE_NETS, a pad's
    net written as the format writes it, and then one segment for each of _BARE, written
    (net N), clear of every pad, and a copper zone written (net 2) with no net_name."""
    text = E.board_footprint_text(_THREE_PADS, "U1", _U1_AT, 0.0, version=version, sheetname="/")
    names = sorted(set(_THREE_NETS.values()))
    pads = _with_pad_nets(text, _THREE_NETS, version, {name: n for n, name in enumerate(names, 1)})
    segments = []
    for k, number in enumerate(_BARE):
        y = 10 + 2 * k
        children = (f"(start 10 {y})", f"(end 20 {y})", "(width 0.25)", '(layer "F.Cu")')
        segments.append(_track("segment", children, "", version=version, number=number))
    zone = _zone("", net_name=False, version=version, number=2)
    if version == "k10":
        zone = zone.replace('(net "")', "(net 2)")
    return _board(tmp_path, [pads, *segments, zone], version=version, rows=None, net_zero=False)


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_a_bare_net_number_binds_as_the_kicad_that_loads_the_format_binds_it(tmp_path, version):
    """Segments written (net 0) to (net 4) and a zone written (net 2), on a board that holds no
    table and whose pads name /B, /A and /B again. A bare number is read as the KiCad that
    loads the board's format reads it. KiCad 9 makes a net only from a row of the table (and
    from a zone's net_name): a pad's net name only checks the name of the net its number finds,
    so on a board with no table, and no zone that names a net, every number but 0 finds none
    (pcb_io_kicad_sexpr_parser.cpp:2713-2720, 5216-5243 and 7387-7412 at 9.0.8), which pcbnew
    9.0.8 reads here (measured 2026-10-17). KiCad 10 makes a net where it first meets a name
    (parseNet, 321-339, and a pad, 6233-6244, at 10.0.7) and numbers them from 1 in the order
    it makes them (NETINFO_LIST::AppendNet and getFreeNetCode, pcbnew/netinfo_list.cpp:149-165
    and 266-275). It reads a bare number as a legacy code and binds FindNet( code ), the code
    unchanged when the board held no table (parseNet, 296-312, and getNetCode,
    pcb_io_kicad_sexpr_parser.h:176-184): the net it made that many-th, none for a number past
    them, net 0 for 0. That is the silent rebind of ADR-2 guardrail 5, and here /B is 1 and /A
    is 2, so a segment written (net 2) is on /A. The pcbnew of the KiCad 10 legs reads these
    boards that way, and only pcbnew 9 judges the KiCad 9 board, since pcbnew 10 loads it by its
    own rules. The tools read every number on a KiCad 10 board as no net, KiCad 9's reading, so
    for them the segment written (net 2) was on no net."""
    board = _numbered_board(tmp_path, version)
    data = board.read_bytes()
    created = list(dict.fromkeys(_THREE_NETS.values()))
    expected = [
        created[n - 1] if version == "k10" and 1 <= n <= len(created) else "" for n in _BARE
    ]
    zone_expected = expected[_BARE.index(2)]
    loads = PJ.skip_reason() is None and (PJ.major() == 9 if version == "k9" else PJ.major() >= 10)
    if loads:
        read = _pcbnew_nets(board)["items"]
        segments = J.find_paths(data, lambda loc, item: item.head == "segment")
        judged = [read[_uuid_of(J.item_at(data, loc))][1] for loc in segments]
        zone = J.item_at(data, _the(data, "kicad_pcb", "zone"))
        if judged != expected or read[_uuid_of(zone)][1] != zone_expected:
            raise RuntimeError(f"the premise fails: pcbnew reads the segments on {judged}")
    root = _cst.parse(data).lists[0]
    assert [pcb._net_name_of(root, seg) for seg in root.find_all("segment")] == expected
    assert [z.net_name for z in pcb.list_pcb_zones(str(board))] == [zone_expected]


def test_k10_update_sends_an_item_numbered_for_a_net_that_left_to_no_net(tmp_path):
    """A KiCad 10 board that holds no table, U1's pad 1 on /GND and pad 2 on /VCC, and a segment
    written (net 2), which KiCad 10 binds to /VCC, the second net it makes (the test above).
    Updated from a netlist that puts pad 1 on /NEW and pad 2 on /GND, /VCC left the schematic,
    so the segment goes to no net, (net ""), and orphaned_tracks counts it. The number has to
    be read against the board as it stands before the update moves any pad: read afterwards
    it is /GND, the second net the updated board makes, which the netlist keeps, and the
    segment would be left written (net 2) to bind to /GND, a net it was never on. The pass
    read no net there, and left it."""
    board = _board(
        tmp_path,
        [
            _u1(_PAD_NETS),
            _track("segment", _STRAY_SEGMENT, "", number=2),
        ],
        version="k10",
    )
    before = board.read_bytes()
    seg_uuid = _uuid_of(J.item_at(before, _the(before, "kicad_pcb", "segment")))
    on_pcbnew_10 = PJ.skip_reason() is None and PJ.major() >= 10
    if on_pcbnew_10 and _pcbnew_nets(board)["items"][seg_uuid][1] != "/VCC":
        raise RuntimeError("the premise fails: pcbnew 10 reads (net 2) on a net but /VCC")
    summary = _apply(board, {"1": "/NEW", "2": "/GND"})
    assert summary["orphaned_tracks"] == 1, summary
    moved = [
        edit
        for edit in _edits(before, board.read_bytes())
        if edit[1][:2] == ("kicad_pcb", "segment")
    ]
    assert moved == [("atom", ("kicad_pcb", "segment", "net", "2"), b"2", _NO_NET)]
    if on_pcbnew_10:
        assert _pcbnew_nets(board)["items"][seg_uuid][1] == "", "pcbnew 10 reads it on a net"


#: Where U2 sits, in nm: its pad 1 is at (79, 40) mm on the board, its pad 2 at (81, 40) mm.
_U2_AT = (80 * MM, 40 * MM)


def _swap_library(directory: Path) -> Path:
    """The library NewLib, holding Syn2: edit_gen's two-pad footprint under another name. A
    netlist that names NewLib:Syn2 for a footprint the board holds as Ladder:Syn changes its
    footprint assignment, which the update answers with a swap, and the library's copy has no
    net on its pads."""
    return E.write_library(
        directory / "lib", E.footprint_with_pad_at(0.0)._replace(name="Syn2"), library="NewLib"
    )


def _kicad10_nets_made(data: bytes) -> list[str]:
    """The nets KiCad 10.0.7 holds, in the order it numbers them 1, 2, ..., once it has loaded
    the board ``data``, which holds no table. It makes a net where it first meets a name, on
    a pad's (net "NAME") or on an item's, in file order (parseNet and the pad's branch,
    pcb_io_kicad_sexpr_parser.cpp:296-342 and 6233-6244), and numbers them as it makes them
    (NETINFO_LIST::AppendNet and getFreeNetCode, pcbnew/netinfo_list.cpp:149-165 and
    266-275). A bare (net N) is then the N-th of them, as getNetCode returns N unchanged with
    no table (pcb_io_kicad_sexpr_parser.h:176-184): the silent rebind of ADR-2 guardrail 5.
    A hand port for the boards of the tests below, read through the lexer port and not
    through the code under test: a pad's net is (net "NAME"), an item's a name or a number.
    The legs that have pcbnew 10 hold the tests' premises to pcbnew's own reading of the
    segment."""
    made: list[str] = []
    for group in FJ.root(data).items:
        if F.head(group) is None:
            continue
        holders = F.pad_groups(group) if F.head(group) == b"footprint" else [group]
        for holder in holders:
            net = F.child(holder, b"net")
            if net is None or net.items[1].kind == L.NUMBER:
                continue
            name = net.items[1].value.decode()
            if name and name not in made:
                made.append(name)
    return made


def test_k10_update_through_a_swap_sends_an_item_numbered_for_a_net_that_left_to_no_net(tmp_path):
    """The test above with U1 swapped for NewLib:Syn2, its pad 1 on /NEW and its pad 2 on /GND:
    the segment written (net 2) is on /VCC, which left the schematic, so it goes to no net,
    (net ""), and orphaned_tracks counts it, as it does without the swap. The swapped
    footprint comes out of its library with no net on its pads, so a number read after the
    swap named no net at all: the pass left the segment written (net 2), which the updated
    board, whose second net is /GND, binds to /GND, a net it was never on. The update reads
    the numbers before it swaps anything."""
    library = _swap_library(tmp_path)
    board = _board(
        tmp_path, [_u1(_PAD_NETS), _track("segment", _STRAY_SEGMENT, "", number=2)], version="k10"
    )
    before = board.read_bytes()
    if _kicad10_nets_made(before) != ["/GND", "/VCC"]:
        raise RuntimeError("the premise fails: the board does not make /GND and /VCC in order")
    seg_uuid = _uuid_of(J.item_at(before, _the(before, "kicad_pcb", "segment")))
    on_pcbnew_10 = PJ.skip_reason() is None and PJ.major() >= 10
    if on_pcbnew_10 and _pcbnew_nets(board)["items"][seg_uuid][1] != "/VCC":
        raise RuntimeError("the premise fails: pcbnew 10 reads (net 2) on a net but /VCC")
    summary = _apply_netlist(
        board,
        [E.NetComponent("U1", "V", "NewLib:Syn2")],
        [("/NEW", [("U1", "1")]), ("/GND", [("U1", "2")])],
        libraries=[library],
    )
    after = board.read_bytes()
    if _kicad10_nets_made(after) != ["/NEW", "/GND"]:
        raise RuntimeError("the premise fails: the updated board does not make /NEW, /GND")
    assert summary["fpid_changed"] == ["U1"], summary
    assert summary["orphaned_tracks"] == 1, summary
    segment = [e for e in _edits(before, after) if e[1][:2] == ("kicad_pcb", "segment")]
    assert segment == [("atom", ("kicad_pcb", "segment", "net", "2"), b"2", _NO_NET)]
    if on_pcbnew_10:
        assert _pcbnew_nets(board)["items"][seg_uuid][1] == "", "pcbnew 10 reads it on a net"


def test_k10_update_through_a_swap_leaves_an_item_numbered_for_a_net_that_stays(tmp_path):
    """A KiCad 10 board that holds no table: U1 with pad 1 on /A and pad 2 on /B, U2 with pad 1
    on /B and pad 2 on /C, and a segment written (net 2), which KiCad 10 binds to /B, the
    second net it makes. Updated from a netlist that swaps U1 for NewLib:Syn2 and keeps its
    pads on /A and /B, with U2's on /B and /D, /B stays in the schematic and the segment stays
    on it: orphaned_tracks is 0 and nothing about the segment changes, and the updated board,
    whose second net is /B again, binds (net 2) to /B still. The swapped footprint comes out
    of its library with no net on its pads, so a number read after the swap was read against
    a board whose first nets were U2's, where (net 2) is /C, a net the netlist does not
    have: the pass sent the segment to no net, cut off from a live net it had been on. The
    update reads the numbers before it swaps anything."""
    library = _swap_library(tmp_path)
    board = _board(
        tmp_path,
        [
            _footprint("U1", _U1_AT, {"1": "/A", "2": "/B"}),
            _footprint("U2", _U2_AT, {"1": "/B", "2": "/C"}),
            _track("segment", _STRAY_SEGMENT, "", number=2),
        ],
        version="k10",
    )
    before = board.read_bytes()
    if _kicad10_nets_made(before) != ["/A", "/B", "/C"]:
        raise RuntimeError("the premise fails: the board does not make /A, /B and /C in order")
    seg_uuid = _uuid_of(J.item_at(before, _the(before, "kicad_pcb", "segment")))
    on_pcbnew_10 = PJ.skip_reason() is None and PJ.major() >= 10
    if on_pcbnew_10 and _pcbnew_nets(board)["items"][seg_uuid][1] != "/B":
        raise RuntimeError("the premise fails: pcbnew 10 reads (net 2) on a net but /B")
    summary = _apply_netlist(
        board,
        [E.NetComponent("U1", "V", "NewLib:Syn2"), E.NetComponent("U2", "V", "Ladder:Syn")],
        [("/A", [("U1", "1")]), ("/B", [("U1", "2"), ("U2", "1")]), ("/D", [("U2", "2")])],
        libraries=[library],
    )
    after = board.read_bytes()
    assert summary["fpid_changed"] == ["U1"], summary
    assert summary["orphaned_tracks"] == 0, summary
    segment = [e for e in _edits(before, after) if e[1][:2] == ("kicad_pcb", "segment")]
    assert segment == [], "the segment on a net that stays was changed"
    assert _kicad10_nets_made(after)[1:2] == ["/B"], "(net 2) binds to another net now"
    if on_pcbnew_10:
        assert _pcbnew_nets(board)["items"][seg_uuid][1] == "/B", "pcbnew 10 reads it off /B"


def _info_nets(board: Path) -> int:
    return int(pcb.get_board_info(str(board)).split("Nets: ")[1].splitlines()[0])


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_board_info_counts_the_unconnected_net_as_pcbnew_does(tmp_path, version):
    """get_board_info's Nets counts the nets pcbnew holds, and pcbnew always holds the
    unconnected net, net 0, which its net list registers under the empty name when it is built
    (NETINFO_LIST::NETINFO_LIST, pcbnew/netinfo_list.cpp:37-43 at 9.0.8, 40-46 at 10.0.7): a
    board with two named nets has three. A KiCad 9 table lists net 0 as its row 0, so the tool
    counted it there; a KiCad 10 board has no table and the tool counted two. When pcbnew
    can load the board the count is pcbnew's own (RuntimeError, as a premise, when it
    differs)."""
    named = 2
    if version == "k9":
        rows = {"/GND": 1, "/VCC": 2}
        board = _board(tmp_path, [_u1(_PAD_NETS, version="k9", rows=rows)], version="k9", rows=rows)
    else:
        board = _board(tmp_path, [_u1(_PAD_NETS)], version="k10")
    if PJ.skip_reason() is None and PJ.major() >= (10 if version == "k10" else 9):
        held = _pcbnew_nets(board)["count"]
        expect = named + 1
        if held != expect:
            raise RuntimeError(f"the premise fails: pcbnew holds {held} nets, not {expect}")
    assert _info_nets(board) == named + 1


# ---------------------------------------------------------------------------
# bug 22: a pad whose net did not change keeps its bytes
# ---------------------------------------------------------------------------

#: The children format( PAD ) writes before a pad's (net ...), in its order
#: (pcb_io_kicad_sexpr.cpp:1432-1613 at 9.0.8, 1635-1858 at 10.0.7; the last four before
#: (property ...) are 10.0.7's only): KiCad writes (net ...) straight after the last of them
#: a pad holds, then (pinfunction), (pintype), the margins and the rest. Counted 2026-10-17
#: over the demos and templates KiCad 9.0.8 ships: 13,497 pads carry a net, every one right
#: after the last of these and none with one of them after the net; 6,215 right after
#: (layers ...), 4,141 after remove_unused_layers and 3,141 after roundrect_rratio.
_BEFORE_NET = (
    "at",
    "size",
    "rect_delta",
    "drill",
    "backdrill",
    "tertiary_drill",
    "front_post_machining",
    "back_post_machining",
    "property",
    "layers",
    "remove_unused_layers",
    "keep_end_layers",
    "zone_layer_connections",
    "roundrect_rratio",
    "chamfer_ratio",
    "chamfer",
)

#: Three ways a board spells a pad's net: KiCad 9's (net N "NAME") on a board of format
#: 20241229, KiCad 10's (net "NAME") on one of 20260206, and the development format KiCad 10
#: wrote in between, which still writes (net N "NAME") and declares the table.
_DIALECTS = ("k9", "k10", "k10dev")


def _with_pad_nets(
    text: str, pad_nets: Mapping[str, str], dialect: str, rows: Mapping[str, int]
) -> str:
    """The board footprint ``text`` with each pad of ``pad_nets`` (pad number to net name) on
    its net, the (net ...) child where KiCad's writer puts it (_BEFORE_NET): KiCad 10's (net
    "NAME"), the others' (net N "NAME") with N the net's row in ``rows``."""
    out: list[str] = []
    pad: str | None = None
    last = -1
    for line in text.splitlines(keepends=True):
        if line.startswith('\t\t(pad "'):
            pad, last = line.split('"')[1], -1
        elif pad is not None and line == "\t\t)\n":
            if pad in pad_nets:
                name = pad_nets[pad]
                ref = (
                    f"(net {_quoted(name)})"
                    if dialect == "k10"
                    else f"(net {rows[name]} {_quoted(name)})"
                )
                out.insert(last + 1, f"\t\t\t{ref}\n")
            pad = None
        elif pad is not None and line.startswith("\t\t\t("):
            head = line[4:].split(" ", 1)[0].split(")", 1)[0].rstrip("\n")
            if head in _BEFORE_NET:
                last = len(out)
        out.append(line)
    return "".join(out)


def _kicad_form_board(
    work: str | Path, fp: E.SynFootprint, pad_nets: Mapping[str, str], dialect: str
) -> Path:
    """A board of the ``dialect`` holding U1, ``fp`` placed at 0 and linked to its symbol as
    the update links one with no symbol uuid, each pad of ``pad_nets`` on its net where
    KiCad writes it, the table (when the dialect has one) declaring exactly those nets."""
    names = sorted(set(pad_nets.values()))
    rows = {name: n for n, name in enumerate(names, start=1)}
    text = E.board_footprint_text(
        fp, "U1", _U1_AT, 0.0, version="k10" if dialect == "k10" else "k9", sheetname="/"
    )
    item = _with_pad_nets(text, pad_nets, dialect, rows)
    return _board(Path(work), [item], version=dialect, rows=None if dialect == "k10" else rows)


def _pad_bytes(data: bytes, ref: str = "U1") -> list[bytes]:
    """Each pad of footprint ``ref`` as it stands in the file, in order."""
    return [data[g.start : g.end] for g in F.pad_groups(FJ.footprint_group(data, ref))]


def _pad_net_path(data: bytes, number: str) -> J.Path:
    """The location of the (net ...) of pad ``number`` of the one footprint of ``data``."""
    found = J.find_paths(
        data,
        lambda loc, item: (
            item.head == "net"
            and [label for _, label in loc[:3]] == ["kicad_pcb", "footprint", "pad"]
            and J.item_at(data, loc[:-1]).items[1].value.decode() == number
        ),
    )
    if len(found) != 1:
        raise LookupError(f"{len(found)} nets on pad {number}")
    return found[0]


#: A footprint with a chamfered pad (KiCad writes (roundrect_rratio), (chamfer_ratio) and
#: (chamfer ...) after the layers, so the net goes after those), a plain pad and a through
#: hole pad.
_CHAMFERED = E.footprint_with_pad_at(0.0)._replace(
    pads=(
        E.SynPad("1", (-MM, 0), 0.0, chamfer=("top_left", "bottom_right")),
        E.SynPad("2", (MM, 0), 0.0),
        E.SynPad("3", (0, 2 * MM), 0.0, layers=("*.Cu", "*.Mask"), kind="thru_hole"),
    )
)
_NET_NAMES = st.builds(
    lambda text: "/" + text,
    st.text(st.characters(min_codepoint=0x20, max_codepoint=0x7E), min_size=1, max_size=6),
)


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=_CHAMFERED, assignment=("/A", "/B", "/A", None), dialect="k9")
@example(fp=_CHAMFERED, assignment=("/A", None, "/B", None), dialect="k10")
@example(fp=_CHAMFERED, assignment=("/A", "/A", None, None), dialect="k10dev")
@given(
    fp=E.synthetic_footprints(pad_details=True),
    assignment=st.tuples(*[st.sampled_from((None, "/A", "/B"))] * 4),
    dialect=st.sampled_from(_DIALECTS),
)
def test_unchanged_reimport_leaves_pads_byte_identical(fp, assignment, dialect):
    """A board in KiCad's own form, each pad's net where KiCad's writer puts it, and the
    netlist it already matches: the update has nothing to do for any pad, so every pad's bytes
    are as they were, on a board of KiCad 9's format, of KiCad 10's and of the development
    format in between. The pass cleared each matched pad's net and put it back after
    (layers ...), which is not where KiCad writes it on a pad with a chamfer or a
    remove_unused_layers, and rewrote a development-format pad's (net N "NAME") as a name."""
    pad_nets = {pad.number: net for pad, net in zip(fp.pads, assignment) if net is not None}
    assume(pad_nets)
    with tempfile.TemporaryDirectory() as work:
        board = _kicad_form_board(work, fp, pad_nets, dialect)
        before = board.read_bytes()
        _apply(board, pad_nets)
        moved = [
            number
            for number, (old, new) in enumerate(
                zip(_pad_bytes(before), _pad_bytes(board.read_bytes())), 1
            )
            if old != new
        ]
    assert not moved, f"pads {moved} (by place) changed in an update with nothing to change"


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=_CHAMFERED, dialect="k9", names=("/A", "/B"))
@example(fp=_CHAMFERED, dialect="k10", names=("/A", "/B"))
@example(fp=_CHAMFERED, dialect="k10dev", names=("/A", "/B"))
@given(
    fp=E.synthetic_footprints(pad_details=True),
    dialect=st.sampled_from(_DIALECTS),
    names=st.tuples(_NET_NAMES, _NET_NAMES).filter(lambda pair: pair[0] != pair[1]),
)
def test_one_changed_net_changes_only_its_pads(fp, dialect, names):
    """Pads 1 and 2 of a footprint with at least three pads are on one net and pad 3 is on
    another; a netlist moves pad 2 to pad 3's net. Every net keeps a pad, so the table and
    every other pad are as they were, and the one change to the file is the (net ...) of pad
    2: its two atoms (its number and its name) on a KiCad 9 board, its name on a KiCad 10
    board. On a development-format board it loses its number and takes the name, as every
    net the tools write above format 20241229 is written by name (the board's table and its
    other pads keep the pair). Names are drawn without line ends, which the tools quote by
    hand (bug 67). The pass moved every pad it touched."""
    assume(len(fp.pads) >= 3)
    a, b = names
    start = {"1": a, "2": a, "3": b}
    wanted = {"1": a, "2": b, "3": b}
    with tempfile.TemporaryDirectory() as work:
        board = _kicad_form_board(work, fp, start, dialect)
        before = board.read_bytes()
        want = _pad_net_path(before, "2")
        _apply(board, wanted)
        edits = J.diff(before, board.read_bytes())
    rows = {a: 1, b: 2} if a < b else {b: 1, a: 2}
    if dialect == "k10":
        expected = [("atom", want, 1, _quoted(a).encode(), _quoted(b).encode())]
    elif dialect == "k10dev":
        expected = [
            ("delete", want, 1, str(rows[a]).encode(), None),
            ("atom", want, 2, _quoted(a).encode(), _quoted(b).encode()),
        ]
    else:
        expected = [
            ("atom", want, 1, str(rows[a]).encode(), str(rows[b]).encode()),
            ("atom", want, 2, _quoted(a).encode(), _quoted(b).encode()),
        ]
    assert [(e.op, e.path, e.index, e.old, e.new) for e in edits] == expected


def _demo_board(name: str) -> tuple[Path, str, str, str]:
    """(the demo's directory, its schematic, board and project) under the install's demos;
    skips when this install holds no such demo."""
    for share in K.share_dirs():
        folder = share / "demos" / name
        if folder.is_dir():
            return folder, f"{name}-pp.kicad_sch", f"{name}-pp.kicad_pcb", f"{name}-pp.kicad_pro"
    pytest.skip(f"this KiCad install holds no demo {name}: {K.NO_INSTALL}")


def _pad_records(data: bytes, ref: str) -> list[tuple[bytes, str | None]]:
    """Each pad of footprint ``ref`` in order, as (its bytes, the net its (net ...) names or
    None), read by KiCad's lexer: a name is the last token of the child."""
    out = []
    for group in F.pad_groups(FJ.footprint_group(data, ref)):
        net = F.child(group, b"net")
        out.append((data[group.start : group.end], net.items[-1].value.decode() if net else None))
    return out


@requires_cli
def test_reimporting_a_demos_own_netlist_leaves_its_pads_byte_identical(tmp_path):
    """kicad-cli's netlist of a copy of KiCad's ecc83 demo, applied to that demo's own
    board: every pad of a footprint the update did not swap, whose net the update did not
    change (judged by the net names KiCad's lexer reads on each side), is as KiCad saved it,
    byte for byte; and pcbnew reads each such pad on the net it read before. The update swaps
    footprints whose library nickname differs from the symbol's, which replaces them by
    design, and those are left out. The pass moved the (net ...) of all 21 pads of the other
    footprints in the demo (measured 2026-10-17 on KiCad 9.0.8's ecc83-pp)."""
    folder, schematic, board_name, project = _demo_board("ecc83")
    work = tmp_path / folder.name
    shutil.copytree(folder, work)
    board = work / board_name
    before = board.read_bytes()
    kept = tmp_path / "before.kicad_pcb"
    kept.write_bytes(before)
    result = pcb.update_pcb_from_schematic(
        schematic_path=str(work / schematic),
        pcb_path=str(board),
        project_path=str(work / project),
    )
    after = board.read_bytes()
    left_alone = [
        FJ.reference_of(fp)
        for fp in F.children(FJ.root(before), b"footprint")
        if FJ.reference_of(fp) not in {*result.fpid_changed, *result.added}
    ]
    if not left_alone:
        raise RuntimeError(f"the update swapped every footprint of the demo: {result}")
    moved, checked = [], 0
    for ref in left_alone:
        for number, (old, new) in enumerate(
            zip(_pad_records(before, ref), _pad_records(after, ref))
        ):
            if old[1] != new[1]:
                continue
            checked += 1
            if old[0] != new[0]:
                moved.append(f"{ref} pad {number}")
    assert checked, "no pad kept its net through the update"
    assert not moved, f"{len(moved)} of {checked} pads whose net did not change moved: {moved[:8]}"
    if PJ.skip_reason() is None:
        was, now = _pcbnew_nets(kept)["pads"], _pcbnew_nets(board)["pads"]

        def named(pads: dict) -> dict:
            return {number: net[1] for number, net in pads.items()}

        changed = [ref for ref in left_alone if named(was[ref]) != named(now[ref])]
        assert not changed, f"pcbnew reads other nets on the pads of {changed}"


_THREE_PADS = E.footprint_with_pad_at(0.0)._replace(
    pads=(
        E.SynPad("1", (-MM, 0), 0.0),
        E.SynPad("2", (MM, 0), 0.0),
        E.SynPad("3", (0, 2 * MM), 0.0),
    )
)


@pytest.mark.parametrize("dialect", ["k9", "k10"])
def test_a_pad_whose_pin_left_the_netlist_loses_its_net(tmp_path, dialect):
    """N11, a guard the pad pass keeps in step: U1's pad 3 is on /X, and the symbol the netlist
    names has pins 1 and 2 only. KiCad's update sets a pad the netlist does not list
    unconnected (BOARD_NETLIST_UPDATER::updateComponentPadConnections, pcbnew/netlist_reader/
    board_netlist_updater.cpp:837-871 at 9.0.8, 1105-1139 at 10.0.7) and its writer leaves out
    the net of a pad on net 0 (pcb_io_kicad_sexpr.cpp:1610-1615 at 9.0.8, 1856-1858 at
    10.0.7), so pad 3 ends with no (net ...); pads 1 and 2 are not touched, and a KiCad 9 board
    loses the /X row nothing uses any more. Judged by KiCad's lexer on what changed."""
    start = {"1": "/GND", "2": "/VCC", "3": "/X"}
    board = _kicad_form_board(tmp_path, _THREE_PADS, start, dialect)
    before = board.read_bytes()
    gone = _pad_net_path(before, "3")
    summary = _apply(board, {"1": "/GND", "2": "/VCC"})
    edits = J.diff(before, board.read_bytes())
    raw = b'(net 3 "/X")' if dialect == "k9" else b'(net "/X")'
    expected = [("delete", gone, raw)]
    if dialect == "k9":
        (row,) = J.find_paths(
            before,
            lambda loc, item: item.head == "net" and len(loc) == 2 and item.items[1].value == b"3",
        )
        expected.append(("delete", row, b'(net 3 "/X")'))
        assert summary["nets_removed"] == 1, summary
    assert sorted((e.op, e.location, e.old) for e in edits) == sorted(expected)


# ---------------------------------------------------------------------------
# an item that holds no (net ...) child yet
# ---------------------------------------------------------------------------


def _without_net(item: str) -> str:
    """``item`` without its (net ...) and (net_name ...) children, each on a line of its own."""
    return "".join(
        line
        for line in item.splitlines(keepends=True)
        if not line.lstrip().startswith(("(net ", "(net_name "))
    )


def _written_net_tokens(version: str, kind: str, net: int) -> tuple[list | None, list | None]:
    """The tokens of the (net ...) and of the (net_name ...) child that KiCad's writer gives an
    item of ``kind`` on net ``net`` (0, or /GND, number 1) on a board of ``version``, None for
    a child it leaves out: the module docstring's rules, from pcb_io_kicad_sexpr.cpp (2436,
    2452-2454 and 1027-1028 at 9.0.8; 2860-2861, 2872-2876 and 1117-1118 at 10.0.7)."""
    if version == "k9":
        if kind == "gr_poly" and net == 0:
            return None, None
        if kind == "zone":
            return [str(net).encode()], [b'"/GND"' if net else b'""']
        return [str(net).encode()], None
    if net:
        return [b'"/GND"'], None
    return ([_NO_NET], None) if kind in ("segment", "via") else (None, None)


@pytest.mark.parametrize("net", [0, 1], ids=["no net", "/GND"])
@pytest.mark.parametrize("kind", ["segment", "via", "gr_poly", "zone"])
@pytest.mark.parametrize("version", ["k9", "k10"])
def test_an_item_with_no_net_child_gets_one_where_kicad_writes_it(tmp_path, version, kind, net):
    """A segment, a via, a copper polygon or a zone that carries no (net ...) child, put on
    net 0 or on /GND by pcb._set_item_net: it gains the children KiCad's writer gives an item
    of its kind on that net (_written_net_tokens), none where KiCad writes none, and nothing
    else changes. The (net ...) goes straight before a track's or a shape's uuid and
    straight after a zone's head, followed on KiCad 9 by the zone's (net_name ...), as
    format( PCB_TRACK ), format( PCB_SHAPE ) and format( ZONE ) put them (pcb_io_kicad_sexpr.cpp:
    2436-2438, 1027-1030 and 2452-2454 at 9.0.8; 2860-2863, 1117-1120 and 2872-2876 at 10.0.7)."""
    rows = _ROWS if version == "k9" else None
    items = {
        "segment": _track("segment", _GND_SEGMENT, "/GND", version=version, rows=rows),
        "via": _track("via", _SIG_TRACKS["via"], "/GND", version=version, rows=rows),
        "gr_poly": _shape("/GND", version=version, rows=rows),
        "zone": _zone("/GND", net_name=True, version=version, rows=rows),
    }
    board = _board(
        tmp_path,
        [_u1(_PAD_NETS, version=version, rows=rows), _without_net(items[kind])],
        version=version,
        rows=rows,
    )
    before = board.read_bytes()
    tree = _cst.parse(before)
    root = tree.lists[0]
    pcb._set_item_net(root.find(kind), root, net)
    board.write_bytes(_cst.serialize(tree))
    after = board.read_bytes()

    want_net, want_name = _written_net_tokens(version, kind, net)
    if want_net is None:
        assert after == before
        return
    heads = ["net"] if want_name is None else ["net", "net_name"]
    assert [edit.path for edit in J.assert_inserted(before, after, heads=heads)] == [
        _the(before, "kicad_pcb", kind)
    ] * len(heads)
    item = J.item_at(after, _the(after, "kicad_pcb", kind))
    assert _net_tokens(item) == [want_net]
    named = [
        [token.raw for token in (child.items or ())[1:]]
        for child in item.items or ()
        if child.items is not None and child.head == "net_name"
    ]
    assert named == ([want_name] if want_name else [])
    kids = _children(item)
    if kind == "zone":
        assert kids[: len(heads)] == heads
    else:
        assert kids[kids.index("net") + 1] == "uuid"


# ---------------------------------------------------------------------------
# N1: binding a pad reproduces the bytes of KiCad's own pads
# ---------------------------------------------------------------------------

#: KiCad 10's writer stopped writing net codes at format 20251028
#: (pcb_io_kicad_sexpr.h:201 at 10.0.7). The formats after KiCad 9's last, 20241229, and
#: before that one are KiCad 10's development: a board there declares its table and writes a
#: pad's net as (net N "NAME") (KiCad 10.0.7's own StickHub demo, format 20250907, does), where
#: the tools write the name alone, the choice they make for every format above 20241229.
#: KiCad's own choice there is not determined, so a rebind is not asserted for such a board.
_DEVELOPMENT_FORMATS = range(20241230, 20251028)

#: A board is checked up to this many problems, so a failing run says where without listing
#: thirteen thousand pads.
_SHOWN = 12

#: A token of an s-expression with the whitespace left out: a quoted string, a paren or a
#: bare word.
_TOKEN = re.compile(rb'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+')


def _binding_report(path: Path) -> tuple[int, int, list[str]]:
    """(pads with a net, pads whose (net ...) does not directly follow (layers ...), the ways
    _bind_pad_net parts from KiCad's bytes) for the board at ``path``.

    For each pad of the board that carries one net, in a copy of it: binding the pad to the net
    it is already on must leave its bytes as KiCad wrote them, and (except in the development
    formats, _DEVELOPMENT_FORMATS) removing the net and binding it again must give KiCad's
    bytes back. Where KiCad wrapped the line before the net, as KiCad 7's writer does at a
    width limit (the Ubuntu package of KiCad 7.0.9's templates that the Linux 9.0 leg installs
    has 510 such pads), the net's separator is not its predecessor's and no tool could know it,
    so only the tokens must come back, in KiCad's order. The board's format version is pinned,
    which _bind_pad_net would otherwise look
    up per pad by scanning every item of the board (pinned, all 39 boards of KiCad 9.0.8's
    demos and templates take 20 s, measured 2026-10-17). A board the CST refuses (bug 54) is
    left out."""
    try:
        root = _cst.parse(path.read_bytes()).lists[0]
    except SyntaxError:
        return 0, 0, []
    version = int(root.find("version").atoms[1].text)
    pads = offset = 0
    problems: list[str] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pcb, "_board_version", lambda _root: version)
        for fp in root.find_all("footprint"):
            for pad in fp.find_all("pad"):
                nets = pad.find_all("net")
                if len(nets) != 1:
                    continue
                atoms = nets[0].atoms
                number = int(atoms[1].text) if len(atoms) == 3 else 0
                name = atoms[-1].text
                heads = [child.head for child in pad.lists]
                pads += 1
                offset += heads[heads.index("net") - 1] != "layers"
                where = f"{path.name} {fp.atoms[1].text} pad {pad.atoms[1].text}"
                original = _cst.serialize(pad)
                work = pad.copy()
                pcb._bind_pad_net(root, work, number, name)
                if _cst.serialize(work) != original:
                    problems.append(f"{where}: binding it to its own net changes it")
                if version in _DEVELOPMENT_FORMATS:
                    continue
                work = pad.copy()
                held = work.find("net")
                wrapped = held.sep != work.children[work.children.index(held) - 1].sep
                work.remove_child(held)
                pcb._bind_pad_net(root, work, number, name)
                rebuilt = _cst.serialize(work)
                same = (
                    _TOKEN.findall(rebuilt) == _TOKEN.findall(original)
                    if wrapped
                    else (rebuilt == original)
                )
                if not same:
                    problems.append(f"{where}: its net, removed and bound again, is not KiCad's")
    return pads, offset, problems


def _check_boards(boards: Sequence[tuple[str, Path]]) -> None:
    if not boards:
        pytest.skip(K.NO_INSTALL)
    pads = offset = 0
    problems: list[str] = []
    for _name, path in boards:
        board_pads, board_offset, found = _binding_report(path)
        pads, offset = pads + board_pads, offset + board_offset
        problems += found
    if not offset:
        pytest.skip(
            f"none of the {pads} pads with a net in this install's boards has its (net ...)"
            " anywhere but right after (layers ...), so nothing here can tell the two apart"
        )
    assert not problems, (
        f"{len(problems)} of {pads} pads part from KiCad's bytes ({offset} sit elsewhere than"
        f" after their layers), e.g.:\n" + "\n".join(problems[:_SHOWN])
    )


def test_pad_rebind_reproduces_kicads_own_pads():
    """N1. KiCad's own boards, the demos and templates its install ships up to the per-push
    size cap, are the judge of where a pad's (net ...) goes: for each pad that carries one, a
    copy of the pad bound to the net it is on is as KiCad wrote it, and the pad with its net
    taken off and bound again is as KiCad wrote it (_binding_report says how far a line KiCad
    wrapped is held to its bytes). KiCad writes the net right after the last
    child format( PAD ) puts before it (_BEFORE_NET), which is after (layers ...) on 6,215 of
    the 13,497 pads of KiCad 9.0.8's boards and after remove_unused_layers or roundrect_rratio
    on the other 7,282 (counted 2026-10-17). _bind_pad_net put it after (layers ...) always, so
    all 7,282 of those pads parted from KiCad's bytes and the 6,215 did not. Skipped when this
    install's boards have no pad whose net is anywhere but after its layers."""
    _check_boards(
        K.shipped(
            subdirs=("demos", "template"), suffixes=(".kicad_pcb",), names=(), cap=K.PER_PUSH_CAP
        )
    )


@pytest.mark.nightly
def test_nightly_pad_rebind_reproduces_kicads_own_pads():
    """N1 over every board the install ships, whatever its size (the per-push run keeps to
    those under the cap)."""
    _check_boards(K.shipped(subdirs=("demos", "template"), suffixes=(".kicad_pcb",), names=()))


# ---------------------------------------------------------------------------
# N8: no net, as the running pcbnew writes it
# ---------------------------------------------------------------------------

#: Runs under pcbnew's Python: two boards of the same six items, board B with each on the net
#: /SIG and board A with each on no net, both saved by pcbnew's own writer, so A shows how
#: the running pcbnew spells no net for each kind. The kinds, in this order: a segment, an
#: arc, a via, a copper zone, a rule area and a copper polygon. (The calls ran on pcbnew
#: 9.0.8 here and on pcbnew 10 on the Linux 10.0, macOS and Windows legs, 2026-10-17; a call
#: whose shape changed on a later pcbnew raises, which pcbnew_judge reports as a
#: RuntimeError, never as a pass.)
_HARVEST = r"""
import os
import sys
import pcbnew

display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
if sys.platform in ("win32", "darwin") or display:
    import wx

    wx.Log.SetActiveTarget(wx.LogStderr())

mm = pcbnew.FromMM


def build(path, on_net):
    board = pcbnew.NewBoard(path)
    net = pcbnew.NETINFO_ITEM(board, "/SIG")
    board.Add(net)

    def put(item):
        item.SetNetCode(0)
        board.Add(item)
        if on_net:
            item.SetNet(net)

    segment = pcbnew.PCB_TRACK(board)
    segment.SetStart(pcbnew.VECTOR2I(mm(10), mm(10)))
    segment.SetEnd(pcbnew.VECTOR2I(mm(15), mm(10)))
    segment.SetWidth(mm(0.25))
    segment.SetLayer(pcbnew.F_Cu)
    put(segment)

    arc = pcbnew.PCB_ARC(board)
    arc.SetStart(pcbnew.VECTOR2I(mm(30), mm(10)))
    arc.SetMid(pcbnew.VECTOR2I(mm(32), mm(12)))
    arc.SetEnd(pcbnew.VECTOR2I(mm(34), mm(10)))
    arc.SetWidth(mm(0.25))
    arc.SetLayer(pcbnew.F_Cu)
    put(arc)

    via = pcbnew.PCB_VIA(board)
    via.SetPosition(pcbnew.VECTOR2I(mm(40), mm(10)))
    via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    put(via)

    def outline(zone, x):
        poly = zone.Outline()
        poly.NewOutline()
        for dx, dy in ((0, 20), (5, 20), (5, 25)):
            poly.Append(mm(x + dx), mm(dy))

    zone = pcbnew.ZONE(board)
    zone.SetLayer(pcbnew.F_Cu)
    outline(zone, 10)
    put(zone)

    rule = pcbnew.ZONE(board)
    rule.SetLayer(pcbnew.F_Cu)
    rule.SetIsRuleArea(True)
    outline(rule, 20)
    board.Add(rule)

    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_POLY)
    shape.SetLayer(pcbnew.F_Cu)
    shape.SetPolyPoints(
        [
            pcbnew.VECTOR2I(mm(30), mm(20)),
            pcbnew.VECTOR2I(mm(35), mm(20)),
            pcbnew.VECTOR2I(mm(35), mm(25)),
        ]
    )
    put(shape)
    if not pcbnew.SaveBoard(path, board):
        raise RuntimeError("pcbnew could not save " + path)


build(payload["on_net"], True)
build(payload["no_net"], False)
result = {"version": pcbnew.GetMajorMinorVersion()}
"""

#: The net tokens of the kinds N8 compares, by name: the head of a board-level item, and
#: "rule area" for a zone with a (keepout ...).
_HARVEST_KINDS = ("segment", "arc", "via", "zone", "rule area", "gr_poly")


def _net_tokens_by_kind(data: bytes) -> dict[str, list[list[bytes]]]:
    """For each of _HARVEST_KINDS, the net tokens ((net ...) and (net_name ...) children, as
    their bytes) of each board-level item of that kind, in the order the file holds them."""
    out: dict[str, list[list[bytes]]] = {kind: [] for kind in _HARVEST_KINDS}
    for location, item in J.walk(data):
        if len(location) != 2 or item.items is None or item.head not in _HARVEST_KINDS:
            continue
        kind = item.head
        if kind == "zone" and any(child.head == "keepout" for child in item.items):
            kind = "rule area"
        out[kind].append(
            [
                child.raw
                for child in item.items
                if child.items is not None and child.head in ("net", "net_name")
            ]
        )
    return out


@pytest.fixture(scope="module")
def harvest(tmp_path_factory):
    """(the net tokens per kind of board A, the same of board B after the update's orphan pass
    has sent every item of /SIG to no net, the same of A after add_trace and add_via asked for
    net 0): A and B written by the running pcbnew (_HARVEST), the update driven over B with a
    netlist that holds nothing, so /SIG is gone."""
    _need_pcbnew()
    work = tmp_path_factory.mktemp("harvest")
    on_net, no_net = work / "on_net.kicad_pcb", work / "no_net.kicad_pcb"
    PJ.run(_HARVEST, {"on_net": str(on_net), "no_net": str(no_net)})
    netlist = work / "netlist.xml"
    netlist.write_text(E.netlist_text([], [], tool="Eeschema 10.0.7"), encoding="utf-8")
    updated = work / "updated.kicad_pcb"
    shutil.copyfile(on_net, updated)
    try:
        pcb._apply_netlist_cst(str(netlist), str(updated), [])
    finally:
        pcb._BOARD_CACHE.pop(str(updated.resolve()), None)
    written = work / "written.kicad_pcb"
    shutil.copyfile(no_net, written)
    pcb.add_trace(60, 60, 70, 60, net=0, pcb_path=str(written))
    pcb.add_via(80, 60, net=0, pcb_path=str(written))
    return (
        _net_tokens_by_kind(no_net.read_bytes()),
        _net_tokens_by_kind(updated.read_bytes()),
        _net_tokens_by_kind(written.read_bytes()),
    )


@pytest.mark.parametrize(
    "kind",
    ["segment", "arc", "via", "zone", "rule area", "gr_poly"],
)
def test_no_net_is_spelled_as_the_running_pcbnew_spells_it(harvest, kind):
    """The orphan pass sends each item of a net that left to no net spelled as the pcbnew of
    this machine spells it, judged by that pcbnew's own writer: board A, the six kinds on no
    net, saved by pcbnew; board B, the same on /SIG, saved by pcbnew; B after the update from a
    netlist without /SIG. The net tokens of each kind on B after the update are those of the
    kind on A: (net 0) for a segment, an arc and a via on a KiCad 9 board and (net "") on a
    KiCad 10 one, (net 0) (net_name "") for a zone on KiCad 9 and nothing for a zone on
    KiCad 10, nothing for a rule area and a copper polygon on either (pcb_io_kicad_sexpr.cpp:
    2436, 2448-2454 and 1027-1028 at 9.0.8; 2860-2876 and 1117-1118 at 10.0.7). The KiCad 9
    zone kept its net_name, and the polygon its net, which the pass never looked at."""
    spelled, updated, _ = harvest
    assert len(spelled[kind]) == len(updated[kind]) == 1, (spelled[kind], updated[kind])
    assert updated[kind] == spelled[kind], (
        f"after the update a {kind} carries {updated[kind]} where pcbnew writes {spelled[kind]}"
    )


@pytest.mark.parametrize("kind", ["segment", "via"])
def test_net_zero_is_spelled_as_the_running_pcbnew_spells_it(harvest, kind):
    """add_trace and add_via asked for net 0 on a board of the running pcbnew's format write
    the net tokens that pcbnew writes for a segment and a via on no net: the item each tool
    adds, last of its kind, carries what the one on board A carries."""
    spelled, _, written = harvest
    assert written[kind][-1] == spelled[kind][0], (written[kind], spelled[kind])
