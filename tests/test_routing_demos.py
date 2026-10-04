"""The routing model and wire_pins_to_net on KiCad's own demo sheets.

wire_pins_to_net decides from two views of a sheet (mcp_server_kicad._connectivity,
docs/adr-routing-safety.md): the narrow view may join only what every KiCad reader joins, since
a no-op rests on it, and the possible view must hold every join a reader might make, since the
refusals rest on it. Both are claims about KiCad, which can change between versions, so
netlist_oracle.model_disagreements checks them against kicad-cli's netlist of the same bytes on
whatever KiCad the runner carries. test_routing_safety.py runs that check on its scenario
fixtures wherever it exports a netlist anyway; this file runs it on demo sheets, and sweeps a
sample of their pins through the tool.

Measured 2026-10-04 with kicad-cli 9.0.8 on all 93 of its demo sheets: no split and no miss,
about 60 s of CPU. The check has teeth: a narrow view that also joined plain wire crossings
showed 128 splits on 53 of those sheets, and a possible view without its same-name joins 943
misses on 79. The routing pressure test had found the same 0 and 0 for the prototype. To bound
the suite's runtime, the test below runs five sheets chosen for what they carry, and the sweep
samples 21 pins of three small ones; the full sweep is the harness gate's.

Copies live outside the per-test tmp_path, so the output oracle in conftest does not ERC them
again: every file judged here goes through kicad-cli's netlist export, which fails on a file it
cannot load.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import requires_cli
from netlist_oracle import bare_name, judge, model_disagreements, nets
from routing_checks import call_wptn
from routing_fixtures import demo_dir, standalone_copy

from mcp_server_kicad import _connectivity, _cst

pytestmark = requires_cli

#: What each sheet carries in KiCad 9.0.8's copy (counted 2026-10-04).
DIFFERENTIAL_SHEETS = [
    # 4 multi-unit parts with unit-0 pads, global labels, 12 no-connect flags
    "flat_hierarchy/pic_programmer.kicad_sch",
    # 26 buses and 24 bus entries, hierarchical labels
    "video/pal-ntsc.kicad_sch",
    # 160 sheet pins on 29 buses
    "video/video.kicad_sch",
    # a sheet used several times: 36 symbols with more than one instance path
    "multichannel/channel_strip.kicad_sch",
    # 4 wires the narrow view distrusts (a junction or bus-entry end on the interior, or an
    # overlap), 104 buses, 37 symbols with more than one instance path
    "vme-wren/vme_buffers_addr.kicad_sch",
]


def _demo(rel: str) -> Path:
    base = demo_dir()
    if base is None or not (base / rel).is_file():
        pytest.skip(f"KiCad's demos have no {rel} on this host")
    return base / rel


@pytest.mark.parametrize("rel", DIFFERENTIAL_SHEETS)
def test_the_model_agrees_with_kicad(tmp_path_factory, rel):
    dst = Path(tmp_path_factory.mktemp("diff")) / Path(rel).name
    standalone_copy(_demo(rel), dst)
    found = model_disagreements(dst)
    assert found is not None, f"the model refuses the whole of {rel}"
    splits, misses = found
    assert splits == [], f"the narrow view joins pins kicad-cli keeps apart: {splits[:3]}"
    assert misses == [], f"the possible view misses joins kicad-cli makes: {misses[:3]}"


#: Small sheets with ordinary parts, each sampled at SAMPLE evenly spaced pins.
SWEEP_SHEETS = [
    "ecc83/ecc83-pp.kicad_sch",
    "simulation/sallen_key/sallen_key.kicad_sch",
    "flat_hierarchy/pic_sockets.kicad_sch",
]
SAMPLE = 7
_BEFORE: dict[str, list] = {}


def _sampled_pin(sheet: Path, k: int) -> tuple[str, str]:
    root = _cst.parse(sheet.read_bytes()).lists[0]
    pads = sorted(
        {
            (it.ref, it.num or "")
            for it in _connectivity.Model(root).items
            if it.kind == "pin" and it.ref and not it.ref.startswith("#")
        },
        key=lambda p: (p[0], _connectivity._pad_order(p[1])),
    )
    return pads[k * len(pads) // SAMPLE]


@pytest.mark.parametrize("k", range(SAMPLE))
@pytest.mark.parametrize("rel", SWEEP_SHEETS)
def test_stock_sheet_sweep(tmp_path_factory, rel, k):
    """A sampled pin wired to a fresh name and, on another copy, to the sheet's largest named
    net: each call refuses with the file intact, changes nothing, or writes exactly the
    requested join as kicad-cli reads it (the pressure test's stock-sheet sweep, bounded)."""
    src = _demo(rel)
    base = Path(tmp_path_factory.mktemp("sweep"))
    first = standalone_copy(src, base / "probe.kicad_sch")
    if rel not in _BEFORE:
        _BEFORE[rel] = nets(first)
    before = _BEFORE[rel]
    ref, num = _sampled_pin(Path(first), k)
    named = [
        (len(nodes), bare_name(name))
        for _c, name, _k, nodes in before
        if name and not name.startswith(("Net-(", "unconnected-(")) and "/" not in name.strip("/")
    ]
    names = ["SWEEP"] + ([max(named)[1]] if named else [])
    for i, n in enumerate(names):
        path = standalone_copy(src, base / f"call{i}.kicad_sch")
        status, msg = call_wptn(path, [{"reference": ref, "pin": num}], n)
        v = judge(before, nets(path), [{(ref, num)}], n, wrote=status == "OK")
        assert not v.wrong, (ref, num, n, msg, v.problems())
        if status == "OK":
            assert v.delivered, (ref, num, n, msg)
            found = model_disagreements(path)
            assert found == ([], []), (ref, num, n, found)
