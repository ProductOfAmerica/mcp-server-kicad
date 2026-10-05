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

import re
import shutil
import time
from pathlib import Path

import pytest
from conftest import requires_cli
from mcp.server.mcpserver.exceptions import ToolError
from netlist_oracle import bare_name, judge, model_disagreements, nets
from routing_checks import call_wptn
from routing_fixtures import demo_dir, standalone_copy

from mcp_server_kicad import _connectivity, _cst, schematic
from mcp_server_kicad.models import PinRefSpec

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


@pytest.fixture(scope="module")
def vme_wren(tmp_path_factory) -> Path:
    base = demo_dir()
    if base is None or not (base / "vme-wren" / "vme-wren.kicad_pro").is_file():
        pytest.skip("KiCad's demos have no vme-wren project on this host")
    dst = Path(tmp_path_factory.mktemp("vme")) / "vme-wren"
    shutil.copytree(base / "vme-wren", dst)
    return dst


@pytest.mark.parametrize(
    ("sheet", "ref"), [("fpga-hp-banks.kicad_sch", "IC14"), ("clocks.kicad_sch", "IC20")]
)
def test_one_part_placed_across_a_project_is_not_a_duplicate(vme_wren, sheet, ref):
    """Guard against over-refusal (there was no reference check before). In KiCad 9.0.8's
    vme-wren, IC14 is 20 symbols on 9 sheets under 4 library names, units 1 to 20 once each,
    and IC20 is 2 symbols on clocks under 2 (counted 2026-10-04). The library copies differ in
    name and bytes but agree on every pad, so each is one part. The walk parses the whole
    project once, cold."""
    path = vme_wren / sheet
    if ref not in path.read_text(encoding="utf-8"):
        pytest.skip(f"{ref} is not on this KiCad's {sheet}")
    root = _cst.parse(path.read_bytes()).lists[0]
    m = _connectivity.Model(root, str(path))
    m.check_unique(ref)  # raises Refusal("dup_ref") if the group is not one part


@pytest.fixture(scope="module")
def tiny_tapeout(tmp_path_factory) -> Path:
    base = demo_dir()
    src = base / "tiny_tapeout" if base is not None else None
    if src is None or not (src / "tinytapeout-demo.kicad_pro").is_file():
        pytest.skip("KiCad's demos have no tiny_tapeout project on this host")
    dst = Path(tmp_path_factory.mktemp("tt")) / "tiny_tapeout"
    dst.mkdir()
    for f in [*src.glob("*.kicad_sch"), *src.glob("*.kicad_pro")]:
        shutil.copy2(f, dst / f.name)
    return dst


@pytest.mark.parametrize("ref", ["U3", "U4"])
def test_a_reference_on_a_dead_path_is_not_a_duplicate(tiny_tapeout, ref):
    """dupref-report.md's one remaining unneeded refusal. On tinytapeout-demo.kicad_sch, U3
    (an AP2112K) carries a second entry from a project called tip-top, on a path this project
    does not have, that names it U4, the reference of the 74CBTLV3257's five units (read
    2026-10-04 in KiCad 9.0.8's copy). KiCad reads U3 at its live path, so neither reference is
    shared."""
    path = tiny_tapeout / "tinytapeout-demo.kicad_sch"
    data = path.read_bytes()
    if b'(project "tip-top"' not in data:
        pytest.skip("this KiCad's tiny_tapeout has no tip-top entry")
    m = _connectivity.Model(_cst.parse(data).lists[0], str(path))
    m.check_unique(ref)  # raises Refusal("dup_ref") if the reference is shared


def _demo_sheets() -> list[str]:
    base = demo_dir()
    return sorted(p.relative_to(base).as_posix() for p in base.rglob("*.kicad_sch")) if base else []


_LAST_ATOM = re.compile(rb'("(?:[^"\\]|\\.)*"|[^\s()"]+)\s*$')


@pytest.mark.parametrize("rel", _demo_sheets() or ["none"])
def test_the_byte_prefilter_misses_nothing_kicad_wrote(rel):
    """Soundness of the pre-filter that decides which sheets a reference check parses, against
    KiCad's own files: every reference a CST read finds on a placed symbol (Reference property
    and every instance entry) is matched by refs_pattern, and every sheet that holds a sheet
    block or an instance table is matched by the structure pattern. A miss would let a
    duplicate reference through unseen."""
    base = demo_dir()
    if base is None or not (base / rel).is_file():
        pytest.skip("no KiCad demos on this host")
    data = (base / rel).read_bytes()
    root = _cst.parse(data).lists[0]
    facts = _connectivity.sheet_facts(root)
    refs = {r for rec in facts.records for r in rec.every_ref()} | {r for _p, r, _u in facts.legacy}
    plain = {r for r in refs if _connectivity.refs_pattern({r}) is not None}
    pattern = _connectivity.refs_pattern(plain)
    if pattern is not None:
        found = set()
        for m in pattern.finditer(data):
            last = _LAST_ATOM.search(m.group(0))
            assert last is not None
            atom = last.group(1)
            found.add((atom[1:-1] if atom[:1] == b'"' else atom).decode("utf-8", "surrogateescape"))
        assert plain <= found, sorted(plain - found)[:5]
    if root.find_all("sheet") or root.find_all("symbol_instances"):
        assert _connectivity._STRUCTURE.search(data)


def _cold(monkeypatch) -> list[int]:
    """Empty the facts cache and record the size of every sheet parsed from here on."""
    _connectivity._FACTS.clear()
    parsed: list[int] = []
    real = _connectivity._file_facts

    def counting(data: bytes):
        parsed.append(len(data))
        return real(data)

    monkeypatch.setattr(_connectivity, "_file_facts", counting)
    return parsed


_FULL_READ: dict[Path, tuple[bool, frozenset[str]]] = {}


def _needed(project: Path, this: Path, refs: set) -> int:
    """Independently of the pre-filter, by a full CST read of every sheet in the project's
    directory: how many other sheets a reference check on *this* may need, those holding a sheet
    block or an instance table, and those with a symbol carrying one of *refs*."""
    n = 0
    for f in project.glob("*.kicad_sch"):
        if f.resolve() == this.resolve():
            continue
        if f not in _FULL_READ:
            root = _cst.parse(f.read_bytes()).lists[0]
            recs = _connectivity.sheet_facts(root).records
            structure = bool(root.find_all("sheet") or root.find_all("symbol_instances"))
            _FULL_READ[f] = (structure, frozenset().union(*(r.every_ref() for r in recs)))
        structure, carried = _FULL_READ[f]
        n += structure or bool(refs & carried)
    return n


def test_a_cold_reference_check_parses_only_the_sheets_it_needs(vme_wren, monkeypatch):
    """Before the byte pre-filter a cold call parsed every other sheet of vme-wren, 34 of
    them, which took about 6 s; now the walk parses the sheets that hold sheet blocks and the
    check those that can carry the reference: 9 for IC20 on clocks and 15 for IC14 on
    fpga-hp-banks in KiCad 9.0.8's copy (counted 2026-10-04). The bound is computed here from a
    full read, not from the pre-filter. One test for both, so the full read is paid once."""
    ran = 0
    for sheet, ref in (("clocks.kicad_sch", "IC20"), ("fpga-hp-banks.kicad_sch", "IC14")):
        path = vme_wren / sheet
        if ref not in path.read_text(encoding="utf-8"):
            continue  # a KiCad whose demo differs: nothing to measure for this case
        parsed = _cold(monkeypatch)
        m = _connectivity.Model(_cst.parse(path.read_bytes()).lists[0], str(path))
        m.check_unique(ref)
        assert len(parsed) <= _needed(vme_wren, path, {ref}), (sheet, len(parsed))
        ran += 1
    if not ran:
        pytest.skip("this KiCad's vme-wren has neither IC20 on clocks nor IC14 on fpga-hp-banks")


def _budget_pins(path: Path, ref: str | None) -> list[PinRefSpec]:
    m = _connectivity.Model(_cst.parse(path.read_bytes()).lists[0])
    if ref is not None:
        num = min((n for r, n in m.pads if r == ref), key=_connectivity._pad_order)
        return [{"reference": ref, "pin": num}]
    caps = sorted({r for r, _n in m.pads if re.fullmatch(r"C\d+", r)}, key=lambda r: int(r[1:]))
    return [{"reference": r, "pin": p} for r in caps[:25] for p in ("1", "2")]


@pytest.mark.parametrize(
    ("sheet", "ref", "cold_s", "warm_s"),
    [
        ("clocks.kicad_sch", "IC20", 6.0, 2.0),
        ("fpga-hp-banks.kicad_sch", "IC14", 10.0, 2.5),
        ("fpga-power.kicad_sch", None, 6.0, 2.5),
    ],
)
def test_a_call_on_vme_wren_stays_within_its_budget(vme_wren, sheet, ref, cold_s, warm_s):
    """wire_pins_to_net's CPU time on vme-wren, KiCad's largest demo (37 sheets, 22 MB), cold
    (the facts cache empty, as for a server's first call) and warm. Measured on 2026-10-04 on
    the development machine through the whole tool: IC20 on clocks 1.9 to 2.2 s cold and 0.6 s
    warm, and IC14 on fpga-hp-banks 3.4 to 3.7 s and 0.7 to 0.8 s, over two runs; these 50
    capacitor pins on fpga-power 1.7 s and 0.8 s. Before the pre-filter a cold call took 6.1 to
    6.7 s whatever it asked. The bounds are about three times the measurements, for
    slower CI machines; the parse count above is the precise guard.
    """
    path = vme_wren / sheet
    if ref is not None and ref not in path.read_text(encoding="utf-8"):
        pytest.skip(f"{ref} is not on this KiCad's {sheet}")
    pins = _budget_pins(path, ref)
    before = path.read_bytes()
    for limit, warm in ((cold_s, False), (warm_s, True)):
        if not warm:
            _connectivity._FACTS.clear()
        t0 = time.process_time()
        try:
            schematic.wire_pins_to_net(pins, "BUDGET_N", schematic_path=str(path))
        except ToolError:
            pass  # a refusal is as good as a write for timing
        spent = time.process_time() - t0
        path.write_bytes(before)
        assert spent <= limit, f"{'warm' if warm else 'cold'} call took {spent:.2f}s of CPU"


def test_smps_com_parts_are_known_by_the_reference_kicad_reads(tmp_path_factory):
    """KiCad 9.0.8's simulation/power_supplies/boost/smps-com.kicad_sch: eight resistors carry
    the Reference property R2 from a first entry left by another project, and KiCad reads them
    as R2 to R9 at the live path (read 2026-10-04). "R2" means one part and "R6" another, as in
    KiCad; matched by the property, "R2" was eight parts and "R6" was not found."""
    base = demo_dir()
    src = base / "simulation" / "power_supplies" / "boost" if base is not None else None
    if src is None or not (src / "smps-com.kicad_pro").is_file():
        pytest.skip("KiCad's demos have no smps-com project on this host")
    dst = Path(tmp_path_factory.mktemp("smps")) / "boost"
    shutil.copytree(src, dst)
    path = dst / "smps-com.kicad_sch"
    m = _connectivity.Model(_cst.parse(path.read_bytes()).lists[0], str(path))
    stale = [s for s in m.syms if m.records[id(s.node)].prop_ref == "R2"]
    if len(stale) < 2:
        pytest.skip("this KiCad's smps-com has no stale R2 property")
    for ref in ("R2", "R6"):
        ((_num, copies),) = m.resolve(ref, "1")
        assert len({id(c.sym) for c in copies}) == 1, (ref, len(copies))


def test_a_pin_on_a_slash_escaped_net_is_found_by_the_name_kicad_shows(tmp_path_factory):
    """KiCad 9.0.8's pic_programmer: R18:2 sits on a label stored as VPP{slash}MCLR, the net
    kicad-cli lists as /VPP/MCLR (read 2026-10-05). Asked for 'VPP/MCLR', the call refused
    [names], and the stored spelling it named was refused as bus syntax, so no call reached
    that net (the routing review's e4c_pic). Read as KiCad reads it, R18:2 is already on it."""
    base = demo_dir()
    src = base / "pic_programmer" if base is not None else None
    if src is None or not (src / "pic_programmer.kicad_pro").is_file():
        pytest.skip("KiCad's demos have no pic_programmer project on this host")
    dst = Path(tmp_path_factory.mktemp("pic")) / "pic_programmer"
    shutil.copytree(src, dst)
    path = dst / "pic_programmer.kicad_sch"
    if b'"VPP{slash}MCLR"' not in path.read_bytes():
        pytest.skip("this KiCad's pic_programmer has no VPP{slash}MCLR label")
    status, msg = call_wptn(str(path), [{"reference": "R18", "pin": "2"}], "VPP/MCLR")
    assert status == "NOOP", msg
    on = {node: name for _c, name, _k, nodes in nets(path) for node in nodes}
    assert on[("R18", "2")] == "/VPP/MCLR"
