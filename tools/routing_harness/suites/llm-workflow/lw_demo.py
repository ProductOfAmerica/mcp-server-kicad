"""Rerun on M2, H, Hb and E (follow-up 2 copy of the follow-up 1 script) of the KiCad 9.0.8 demo-sheet scenarios:
  demo_probe.py   LW-14 (W rows: LW-03, also LW-14). Each sheet repathed with mine_realsheets.repath
                  (copied in lwlib) so a standalone export lists every net; every call starts from a
                  fresh copy of the repathed original. Pin selection copied from the original: the
                  first n_nc unconnected single-pin nets whose pin carries an NC flag, free unconnected
                  pins, and pins of mirrored rot-90/270 symbols on unnamed nets (first 6).
    A  NC-flagged pin -> 'TP<i>' with the flag in place; if that call is not OK, the repo's
       remove_no_connect, then the same call again (per design, on its own copy)
    E  NC flag removed by remove_no_connect, then the pin -> the sheet's power name / a signal name
    C  up to 4 NC-flagged pins of one IC, flags removed, one call to the power name
    W  mirrored rot-90/270 pin on an unnamed net -> 'WP<i>'
    F  free pin -> 'FREE<i>', and -> the power name
  v_lw03_mirror.py LW-03. Part A: vme-wren/clocks repathed with vlib.repath, wire_pins_to_net([C116:2],
                  'WP0'). Part B: Device:C and Device:Q_NPN at the four mirrored +-90 orientations, the
                  other pin (C1:1 / Q1:C) pre-wired to R9:1 with raw wires at KiCad-true positions, then
                  wire_pins_to_net([X1:<pin>], 'NEWNET').
usage: uv run python tools/routing_harness/suites/llm-workflow/lw_demo.py [demo | v03] [sheet rel paths...]
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H  # noqa: E402

import lwlib as LW  # noqa: E402
from mcp_server_kicad import _cst  # noqa: E402

args = sys.argv[1:]
part = args[0] if args and args[0] in ("demo", "v03") else None
sheets_arg = [a for a in args if a not in ("demo", "v03")]
tag = "lw_demo" + (f"_{part}" if part else "") + ("_" + "_".join(
    Path(s).stem for s in sheets_arg) if sheets_arg else "")
R.begin(LW.CAT, __file__, tag=tag)

SHEETS = {
    "interf_u/interf_u.kicad_sch": {"power": "GND", "signal": "PC-A1", "n_nc": 10},
    "vme-wren/clocks.kicad_sch": {"power": "GND", "signal": "P3V3_CLK", "n_nc": 10},
    "stickhub/StickHub.kicad_sch": {"power": "GND", "signal": "LED1", "n_nc": 2},
    "vme-wren/power-supply-1.kicad_sch": {"power": "GND", "signal": "5VREG", "n_nc": 4},
}
SCRIPT = LW.PROBE + "demo_probe.py"


def wkw(pins, N):
    return dict(pins=LW.pins_kw(pins), label_text=N)


def select(orig, cfg):
    nets = LW.nets(orig)
    R.wipe_judge_scratch()
    root = _cst.parse(Path(orig).read_bytes()).lists[0]
    m = R.M2._Model(root)
    m.ensure()
    ncs = {(it.x, it.y) for it in m.items if it.kind == "nc"}
    pos, mirrored = {}, set()
    for s in m.syms:
        at = s.node.find("at")
        ang = int(float(at.atoms[3].text)) if len(at.atoms) > 3 else 0
        if s.node.find("mirror") is not None and ang in (90, 270):
            mirrored.add(s.ref)
    for it in m.items:
        if it.kind == "pin":
            pos.setdefault((it.ref, it.num), []).append(it)
    ncpins, free, wrongpin = [], [], []
    for name, v in nets:
        nodes = sorted(n for n in v if not n[0].startswith("#"))
        if name.startswith("unconnected-(") and len(nodes) == 1:
            n = nodes[0]
            its = pos.get(n, [])
            if its and not any(it.nc for it in its):
                (ncpins if any((it.x, it.y) in ncs for it in its) else free).append(n)
        if not LW.strong(name):
            for n in nodes:
                if n[0] in mirrored and len(pos.get(n, [])) == 1:
                    wrongpin.append(n)
    return nets, ncpins[:cfg["n_nc"]], free, wrongpin[:6]


if part in (None, "demo"):
    for rel in (sheets_arg or list(SHEETS)):
        cfg = SHEETS[rel]
        stem = rel[:-len(".kicad_sch")]
        d0 = LW.fixture_dir("orig__" + rel.replace("/", "__").replace(" ", "_").replace(".kicad_sch", ""))
        orig = d0 / ("orig__" + rel.replace("/", "__").replace(" ", "_"))
        LW.repath_realsheets(LW.DEMOS / rel, orig)
        nets, ncpins, free, wrongpin = select(orig, cfg)
        print(f"\n######## {rel}: {len(nets)} nets; NC-flagged picked {ncpins}; free {free}; "
              f"mirrored-90 pins on unnamed nets {wrongpin}", flush=True)
        # A
        for i, (ref, num) in enumerate(ncpins):
            LW.header(f"demo A {rel} {ref}:{num}")
            seqs = {}
            for dz in R.DESIGNS:
                s = LW.Seq("LW-14", f"demo_probe:{stem}:A{i}", dz, orig, script=SCRIPT)
                r0 = s.call("wire_pins_to_net", wkw([(ref, num)], f"TP{i}"),
                            f"A {ref}:{num} -> TP{i} (NC flag still there)")
                rm = "(not needed)"
                if r0["status"] != "OK":
                    rm = LW.remove_nc(s.edited, ref, num)
                s.call("wire_pins_to_net", wkw([(ref, num)], f"TP{i}"),
                       f"A {ref}:{num} -> TP{i} second call", extra={"remedy": str(rm)[:200]},
                       attempt="retry")
                seqs[dz] = s
            LW.close_all(seqs)
        # E
        for i, (ref, num) in enumerate(ncpins):
            for kind, N in (("power", cfg["power"]), ("signal", cfg["signal"])):
                p = LW.copy_file_fixture(orig, f"E_{Path(rel).stem}_{i}_{kind}")
                rm = LW.remove_nc(p, ref, num)
                R.run_case("LW-14", f"demo_probe:{stem}:E{i}_{kind}", "wire_pins_to_net", p,
                           wkw([(ref, num)], N), script=SCRIPT,
                           note=f"E {ref}:{num} -> existing {kind} '{N}' after remove_no_connect [{str(rm)[:60]}]")
        # C
        byref = {}
        for ref, num in ncpins:
            byref.setdefault(ref, []).append((ref, num))
        for ref, lst in byref.items():
            if len(lst) < 2:
                continue
            lst = lst[:4]
            p = LW.copy_file_fixture(orig, f"C_{Path(rel).stem}_{ref}")
            for r_, n_ in lst:
                LW.remove_nc(p, r_, n_)
            R.run_case("LW-14", f"demo_probe:{stem}:C_{ref}", "wire_pins_to_net", p,
                       wkw(lst, cfg["power"]), script=SCRIPT,
                       note=f"C tie {[f'{a}:{b}' for a, b in lst]} -> '{cfg['power']}' after remove_no_connect")
        # W
        for i, (ref, num) in enumerate(wrongpin):
            p = LW.copy_file_fixture(orig, f"W_{Path(rel).stem}_{i}")
            R.run_case("LW-03", f"demo_probe:{stem}:W{i}", "wire_pins_to_net", p,
                       wkw([(ref, num)], f"WP{i}"), also=["LW-14"], script=SCRIPT,
                       note=f"W {ref}:{num} (mirrored, rot 90/270) -> WP{i}")
        # F
        for i, (ref, num) in enumerate(free):
            p = LW.copy_file_fixture(orig, f"F_{Path(rel).stem}_{i}")
            R.run_case("LW-14", f"demo_probe:{stem}:F{i}", "wire_pins_to_net", p,
                       wkw([(ref, num)], f"FREE{i}"), script=SCRIPT, note=f"F {ref}:{num} (free pin)")
            R.run_case("LW-14", f"demo_probe:{stem}:F{i}e", "wire_pins_to_net", p,
                       wkw([(ref, num)], cfg["power"]), script=SCRIPT,
                       note=f"F {ref}:{num} (free pin) -> existing '{cfg['power']}'")

if part in (None, "v03"):
    V03 = LW.VERIFY + "v_lw03_mirror.py"
    # Part A
    dA = LW.fixture_dir("v03_clocks_orig")
    origA = dA / "clocks.kicad_sch"
    LW.repath_vlib(LW.DEMOS / "vme-wren" / "clocks.kicad_sch", origA)
    print(f"A: KiCad-true C116 pins: {R.M2.pin_positions(str(origA), 'C116')}", flush=True)
    dR = LW.fixture_dir("v03_clocks_realsheets")
    origR = dR / "clocks.kicad_sch"
    LW.repath_realsheets(LW.DEMOS / "vme-wren" / "clocks.kicad_sch", origR)
    _, _, _, wp = select(origR, SHEETS["vme-wren/clocks.kicad_sch"])
    same_file = origA.read_bytes() == origR.read_bytes()
    if same_file and wp and wp[0] == ("C116", "2"):
        print("-- v_lw03_mirror A: vlib.repath and mine_realsheets.repath give byte-identical clocks"
              " files and demo_probe's W0 is the same call (C116:2 -> WP0); run once as"
              " demo_probe:vme-wren/clocks:W0 (case LW-03, also LW-14) (rule 4)", flush=True)
    else:
        R.run_case("LW-03", "v_lw03_mirror:A C116:2 -> WP0", "wire_pins_to_net", origA,
                   wkw([("C116", "2")], "WP0"), script=V03,
                   note=f"clocks repathed by vlib.repath (byte-identical to the demo repath: {same_file};"
                        f" demo W pins {wp})")
    # Part B
    ORIENTS = [(90, "x"), (270, "x"), (90, "y"), (270, "y")]
    PARTS = [("Device:C", "C1", "1", "2"), ("Device:Q_NPN", "Q1", "C", "B")]
    for lib, ref, other, ask_pin in PARTS:
        for rot, mir in ORIENTS:
            p = H.fresh(f"v03_{ref}_{rot}{mir}")
            H.place(p, lib, ref, 127.0, 101.6, rot=rot, mirror=mir, value="X")
            H.place(p, "Device:R", "R9", 165.1, 63.5, value="1k")
            true = R.M2.pin_positions(p, ref)
            r9 = R.M2.pin_positions(p, "R9")["1"][0]
            ox, oy, _ = true[other][0]
            H.wire(p, ox, oy, ox, r9[1])
            H.wire(p, ox, r9[1], r9[0], r9[1])
            n0 = LW.nets(p)
            R.wipe_judge_scratch()
            ok = any((ref, other) in v and ("R9", "1") in v for _n, v in n0)
            if not ok:
                print(f"  !! fixture wire missed {ref}:{other} (the original asserted this)", flush=True)
            R.run_case("LW-03", f"v_lw03_mirror:B {lib} rot{rot} mirror {mir} ask {ref}:{ask_pin}",
                       "wire_pins_to_net", p, wkw([(ref, ask_pin)], "NEWNET"), script=V03,
                       note=f"other pin {ref}:{other} pre-wired to R9:1; fixture check {'ok' if ok else 'FAILED'}")
R.end()
