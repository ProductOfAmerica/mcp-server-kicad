"""Perf fixtures and pin selection for the probeC rerun, copied (not imported) from the originals:
  probe-perf/perfutil.py        repath (BIG sheet map)
  probe-perf/perf_prepare.py    free / unnamed pin eligibility and the 10 nearest pairs
  probe-perf/perf_augment.py    occupancy, find_spot, J9001 / J9003 / J9002 placement
  probe-perf/perf_k200.py       J9004..J9006 placement
  verify-probe-perf/vcommon.py  standalone_copy, content_max_x
  verify-probe-perf/vp_fixtures.py  .free (A0, J8001) and .free200 (J8001..J8004)
  verify-probe-perf/vp_fastp.py stock_pins
  verify-probe-perf/perfslice/bench_current_tool.py  pick_pins
P's own pin helpers (_target, _p_outward, _sym_angle) are copied below on the repo's functions,
so proposal_impl is never imported; mine_impl's model is replaced by m2_impl's (identical items).
Import rlib BEFORE this module. Persistent fixtures live in FIXTURES/probeC/perf/ (built by
build_fixtures.py, which prepare.py runs); scripts copy one into its own directory under rlib's
H.BASE before use. pins.json, committed beside this file, holds the pin selection. The pin
helpers (_pin_matches_cst, _ANGLE_TO_DIR, _get_pin_pos_cst) are core/compat54.py's frozen
54160c1 copies: the package's own _get_pin_pos_cst changed meaning after that commit.
"""

from __future__ import annotations

import json
import math
import shutil
import time
from pathlib import Path

import hpaths  # noqa: F401  (an entry script has put it on sys.path)
import rlib as R
from rlib import H, M2, SCH
from compat54 import _ANGLE_TO_DIR, _get_pin_pos_cst, _pin_matches_cst
from mcp_server_kicad import _cst

STOCK = Path(hpaths.KICAD_DEMOS)
HERE = Path(__file__).resolve().parent  # source directory: pins.json is read from here
FIX = hpaths.FIXTURES / "probeC" / "perf"  # prepare-time fixtures (build_fixtures.py), git-ignored
OUTD = R.OUT / "perf"  # this run's dups_*.json and timings_*.json
OUTD.mkdir(parents=True, exist_ok=True)
BIG = {  # probe order (perfutil.BIG)
    "fpga-power": "vme-wren/fpga-power.kicad_sch",
    "fpga-hp-banks": "vme-wren/fpga-hp-banks.kicad_sch",
    "tinytapeout-demo": "tiny_tapeout/tinytapeout-demo.kicad_sch",
    "ampli_ht": "complex_hierarchy/ampli_ht.kicad_sch",
}
SHEETS = {  # verifier order (vcommon.SHEETS)
    "tinytapeout-demo": "tiny_tapeout/tinytapeout-demo.kicad_sch",
    "fpga-hp-banks": "vme-wren/fpga-hp-banks.kicad_sch",
    "fpga-power": "vme-wren/fpga-power.kicad_sch",
    "ampli_ht": "complex_hierarchy/ampli_ht.kicad_sch",
}
IU = 10000
G = 12700
TIMING_NOTE = ("unjudged runs, fresh copy per run, design order rotated (M2 K, K M2, M2 K); measured"
               " while the machine was saturated by parallel agents (observed 100% CPU on 16 logical"
               " CPUs, about 48 python and 9 kicad-cli processes; at most 2 of them this script's),"
               " so absolute figures are inflated and noisy; compare M2 with K, not with the"
               " original probe's seconds")


def _iu(v) -> int:
    return M2._iu(v)


# ------------------------------------------------------------------------------ copies


def repath(src, dst):
    """perfutil.repath: first instance path -> /<file uuid>, further paths -> a dead path."""
    tree = _cst.parse(Path(src).read_bytes())
    root = tree.lists[0]
    u = root.find("uuid").atoms[1].text
    for s in root.find_all("symbol"):
        inst = s.find("instances")
        if inst is None:
            continue
        paths = [pth for pj in inst.find_all("project") for pth in pj.find_all("path")]
        for i, pth in enumerate(paths):
            pth.atoms[1].set_text(f"/{u}" if i == 0 else "/00000000-0000-0000-0000-00000000dead")
    Path(dst).write_bytes(_cst.serialize(tree))


def standalone_copy(src, dst, paper: str | None = None):
    """vcommon.standalone_copy."""
    tree = _cst.parse(Path(src).read_bytes())
    root = tree.lists[0]
    u = root.find("uuid").atoms[1].text
    for s in root.find_all("symbol"):
        inst = s.find("instances")
        if inst is None:
            continue
        first = True
        for proj in inst.find_all("project"):
            for path in proj.find_all("path"):
                path.atoms[1].set_text(f"/{u}" if first else "/ffffffff-0000-0000-0000-000000000000")
                first = False
    if paper:
        root.find("paper").atoms[1].set_text(paper)
    Path(dst).write_bytes(_cst.serialize(tree))
    return str(dst)


def content_max_x(path) -> float:
    """vcommon.content_max_x."""
    root = _cst.parse(Path(path).read_bytes()).lists[0]
    best = 0.0

    def walk(n):
        nonlocal best
        for c in n.lists:
            if c.head in ("at", "xy") and len(c.atoms) >= 3:
                best = max(best, float(c.atoms[1].text))
            walk(c)

    for c in root.lists:
        if c.head == "lib_symbols":
            continue
        if c.head == "sheet":
            at, size = c.find("at"), c.find("size")
            best = max(best, float(at.atoms[1].text) + float(size.atoms[1].text))
        walk(c)
    return best


def model(root):
    m = M2._Model(root)
    m.ensure()
    return m


def occupancy(root, m):
    """perf_augment.occupancy (identical in perf_k200)."""
    rects = []

    def box(x1, y1, x2, y2, pad):
        rects.append((min(x1, x2) - pad, min(y1, y2) - pad, max(x1, x2) + pad, max(y1, y2) + pad))

    for it in m.items:
        if it.kind in ("wire", "bus", "gline", "be"):
            box(it.x, it.y, it.x2, it.y2, 3 * IU)
        elif it.kind == "label":
            box(it.x - 15 * IU, it.y, it.x + 15 * IU, it.y, 3 * IU)
        else:
            box(it.x, it.y, it.x, it.y, 3 * IU)
    for s in m.syms:
        at = s.node.find("at")
        ox, oy = _iu(at.atoms[1].text), _iu(at.atoms[2].text)
        xs = [p.x for p in s.pins] + [ox]
        ys = [p.y for p in s.pins] + [oy]
        box(min(xs), min(ys), max(xs), max(ys), 8 * IU)
    for sh in root.find_all("sheet"):
        at, size = sh.find("at"), sh.find("size")
        x, y = _iu(at.atoms[1].text), _iu(at.atoms[2].text)
        box(x, y, x + _iu(size.atoms[1].text), y + _iu(size.atoms[2].text), 15 * IU)
    for tok in ("text", "text_box", "image"):
        for n in root.find_all(tok):
            at = n.find("at")
            if at is not None:
                x, y = _iu(at.atoms[1].text), _iu(at.atoms[2].text)
                box(x - 5 * IU, y - 5 * IU, x + 40 * IU, y + 5 * IU, 2 * IU)
    return rects


def find_spot(rects, w_mm, h_mm, page_w, page_h, cx_off, cy_off, taken=()):
    """perf_augment.find_spot (perf_k200's is the same without `taken`)."""
    w, h = int(w_mm * IU), int(h_mm * IU)
    pw, ph = int(page_w * IU), int(page_h * IU)
    allr = list(rects) + list(taken)
    for y0 in range(20 * IU, ph - h - 20 * IU, 2 * G):
        for x0 in range(20 * IU, pw - w - 20 * IU, 2 * G):
            if x0 + w > pw - 200 * IU and y0 + h > ph - 55 * IU:
                continue  # title block corner
            x1, y1 = x0 + w, y0 + h
            if any(not (x1 < a or x0 > c or y1 < b or y0 > d) for a, b, c, d in allr):
                continue
            ox = round((x0 + int(cx_off * IU)) / G) * G
            oy = round((y0 + int(cy_off * IU)) / G) * G
            return ox, oy, (x0, y0, x1, y1)
    return None


ENLARGE = {"A4": "A3", "A3": "A2", "A2": "A1"}


def build_aug(src, dst):
    """perf_augment for one sheet: J9001 (Conn_02x25), J9003 (Conn_01x10 mirror y), J9002."""
    data = Path(src).read_bytes()
    paper_note = ""
    s1 = s2 = None
    for _attempt in range(3):
        root = _cst.parse(data).lists[0]
        pw, ph, _pname = SCH._page_size_cst(root)
        m = model(root)
        rects = occupancy(root, m)
        s1 = find_spot(rects, 64, 74, pw, ph, 30, 37, [])
        s2 = find_spot(rects, 58, 36, pw, ph, 12, 17, [s1[2]] if s1 else []) if s1 else None
        if s1 and s2:
            break
        tree = _cst.parse(data)
        paper = tree.lists[0].find("paper")
        new = ENLARGE[paper.atoms[1].text]
        paper_note += f" paper {paper.atoms[1].text}->{new} (no free spot)"
        paper.atoms[1].set_text(new)
        data = _cst.serialize(tree)
    else:
        raise SystemExit(f"{src}: no free spot even after enlarging")
    Path(dst).write_bytes(data)
    (x1, y1, _), (x2, y2, _) = s1, s2
    SCH.place_component(lib_id="Connector_Generic:Conn_02x25_Odd_Even", reference="J9001",
                        value="PERF50", x=x1 / IU, y=y1 / IU, schematic_path=str(dst))
    SCH.place_component(lib_id="Connector_Generic:Conn_01x10", reference="J9003", value="PERFL",
                        x=x2 / IU, y=y2 / IU, mirror="y", schematic_path=str(dst))
    SCH.place_component(lib_id="Connector_Generic:Conn_01x10", reference="J9002", value="PERFR",
                        x=x2 / IU + 30.48, y=y2 / IU, schematic_path=str(dst))
    return {"J9001": [x1, y1], "J9003": [x2, y2], "J9002": [x2 + int(30.48 * IU), y2],
            "paper": paper_note}


def build_k200(src_aug, dst):
    """perf_k200: J9004..J9006 on a copy of the augmented tinytapeout-demo."""
    shutil.copyfile(src_aug, dst)
    placed = {}
    for ref in ("J9004", "J9005", "J9006"):
        root = _cst.parse(Path(dst).read_bytes()).lists[0]
        pw, ph, _ = SCH._page_size_cst(root)
        m = model(root)
        spot = find_spot(occupancy(root, m), 64, 74, pw, ph, 30, 37)
        assert spot, f"no free spot for {ref}"
        SCH.place_component(lib_id="Connector_Generic:Conn_02x25_Odd_Even", reference=ref,
                            value="PERF50", x=spot[0] / IU, y=spot[1] / IU, schematic_path=str(dst))
        placed[ref] = [spot[0] / IU, spot[1] / IU]
    return placed


def build_free(rel, dst, refs):
    """vp_fixtures: standalone copy, paper A0, Conn_02x25 refs beyond the content's max x."""
    standalone_copy(STOCK / rel, dst, paper="A0")
    x0 = math.ceil((content_max_x(dst) + 40.0) / 2.54) * 2.54
    for i, ref in enumerate(refs):
        SCH.place_component(lib_id="Connector_Generic:Conn_02x25_Odd_Even", reference=ref,
                            value="VPERF", x=x0, y=60.0 + 80.0 * i, schematic_path=str(dst))
    return x0


# ------------------------------------------------------------------------------ pin selection


def netlist(path):
    """kicad-cli 9.0.8 kicadxml: [(code, name, frozenset((ref, pin)))] (judge3.nets_full)."""
    res = R.J.nets_full(path)
    R.wipe_judge_scratch()
    return None if res is None else [(c, n, v) for c, n, _k, v in res]


def _sym_angle(sym) -> float:
    at = sym.find("at")
    return float(at.atoms[3].text) if len(at.atoms) > 3 else 0.0


def _p_outward(outward: float, comp_angle: float) -> float:
    if round(comp_angle) % 360 in (90, 270):
        return (outward + 180) % 360
    return outward


def _target(root, ref, pin_name):
    """proposal_impl._target on the repo's functions."""
    for sym in SCH._find_syms_cst(root, ref):
        lib_sym = SCH._find_lib_symbol_cst(root, sym.find("lib_id").atoms[1].text)
        if lib_sym is None:
            return None
        for sub in SCH._instance_units(lib_sym, SCH._sym_unit_cst(sym), SCH._sym_body_style_cst(sym)):
            for pin in sub.find_all("pin"):
                if _pin_matches_cst(pin, pin_name):
                    return sym, pin
    return None


def prepare(path, nets=None):
    """perf_prepare for one repathed sheet: free, unnamed, pairs, excluded."""
    nets = nets or netlist(path)
    root = _cst.parse(Path(path).read_bytes()).lists[0]
    m = model(root)
    ncs = [(it.x, it.y) for it in m.items if it.kind == "nc"]
    reasons = {}
    free, unnamed = [], []
    for _c, nname, v in nets:
        nodes = sorted(n for n in v if not n[0].startswith("#"))
        if nname.startswith("unconnected-(") and len(v) == 1 and len(nodes) == 1:
            cat = free
        elif nname.startswith("Net-(") and len(nodes) >= 2:
            cat = unnamed
        else:
            continue
        picked = None
        for ref, num in nodes:
            why = None
            copies = m.pads.get((ref, num), [])
            if len(copies) != 1:
                why = f"{len(copies)} copies"
            else:
                it = copies[0]
                if it.nc:
                    why = "no_connect type"
                elif it.etype == "power_in":
                    why = "power_in"
                elif it.amb:
                    why = "unit-ambiguous"
                elif any((it.x - x) ** 2 + (it.y - y) ** 2 <= M2.TOL2 for x, y in ncs):
                    why = "NC flag"
                else:
                    try:
                        bx, by, bout = _get_pin_pos_cst(root, ref, num)
                        tgt = _target(root, ref, num)
                    except ValueError as e:
                        why = f"repo lookup: {e}"
                    else:
                        if (_iu(bx), _iu(by)) != (it.x, it.y):
                            why = "repo position differs from KiCad"
                        elif tgt is None or tgt[1].find("number").atoms[1].text != num:
                            why = "repo name-or-number lookup resolves another pin"
                        else:
                            sym = tgt[0]
                            pdir = _ANGLE_TO_DIR[round(_p_outward(bout, _sym_angle(sym)) / 90) * 90 % 360]
                            picked = {"ref": ref, "pin": num, "x": it.x, "y": it.y, "net": nname,
                                      "net_size": len(nodes), "p_dir": pdir,
                                      "m_dir": M2._DIR_NAME.get(it.out),
                                      "b_dir": _ANGLE_TO_DIR[round(bout / 90) * 90 % 360]}
            if picked:
                break
            reasons[why] = reasons.get(why, 0) + 1
            if cat is free:
                break
        if picked:
            cat.append(picked)
    pool = free + unnamed
    cands = []
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            if a["ref"] == b["ref"] or a["net"] == b["net"]:
                continue
            d = math.hypot(a["x"] - b["x"], a["y"] - b["y"]) / 1e4
            cands.append((d, a, b))
    cands.sort(key=lambda r: r[0])
    used, pairs = set(), []
    for d, a, b in cands:
        ka, kb = (a["ref"], a["pin"]), (b["ref"], b["pin"])
        if ka in used or kb in used:
            continue
        used |= {ka, kb}
        pairs.append({"a": a, "b": b, "dist_mm": round(d, 2)})
        if len(pairs) == 10:
            break
    return {"free": free, "unnamed": unnamed, "pairs": pairs, "excluded": reasons}


def stock_pins(base, nets=None):
    """vp_fastp.stock_pins on the frozen _get_pin_pos_cst and the copied _target."""
    root = _cst.parse(Path(base).read_bytes()).lists[0]
    mm_ = M2._Model(root)
    out = []
    for _c, name, v in (nets or netlist(base)):
        if not name.startswith("Net-("):
            continue
        for ref, num in sorted(v):
            cp = mm_.pads.get((ref, num), [])
            if ref.startswith("#") or len(cp) != 1 or cp[0].nc or cp[0].etype == "power_in":
                continue
            try:
                bx, by, _o = _get_pin_pos_cst(root, ref, num)
                tgt = _target(root, ref, num)
            except ValueError:
                continue
            if (_iu(bx), _iu(by)) != (cp[0].x, cp[0].y) or tgt[1].find("number").atoms[1].text != num:
                continue
            out.append({"reference": ref, "pin": num})
    return out


def pick_pins(path, n):
    """perfslice bench_current_tool.pick_pins: n placed pins spread over the sheet."""
    root = _cst.parse(open(path, "rb").read()).lists[0]
    cands = []
    for sym in root.find_all("symbol"):
        ref = SCH._sym_property_cst(sym, "Reference") or ""
        if ref.startswith("#") or sym.find("lib_name") is not None:
            continue
        ls = SCH._find_lib_symbol_cst(root, sym.find("lib_id").atoms[1].text)
        if ls is None:
            continue
        units = SCH._instance_units(ls, SCH._sym_unit_cst(sym), SCH._sym_body_style_cst(sym))
        for u in units:
            for p in u.find_all("pin"):
                num = p.find("number").atoms[1].text
                cands.append((ref, num))
    seen, uniq = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    step = max(1, len(uniq) // n)
    return [{"reference": r, "pin": p} for r, p in uniq[::step][:n]]


# ------------------------------------------------------------------------------ script helpers


def fx(rel: str, tag: str) -> Path:
    """Copy perf/fixtures/<rel> into its own directory H.BASE/<tag>/ and return the copy."""
    src = FIX / rel
    if not src.is_file():
        raise SystemExit(f"perf fixture {src} is missing: run tools/routing_harness/prepare.py first "
                         "(it runs suites/probeC/perf/build_fixtures.py, which builds it)")
    d = Path(H.BASE) / tag
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    dst = d / src.name
    shutil.copyfile(src, dst)
    return dst


def load_pins():
    return json.loads((HERE / "pins.json").read_text(encoding="utf-8"))


TIMINGS: dict = {}  # (variant, design) -> {"runs_s": [...], "statuses": [...]} (or per-step lists)
# Follow-up 2: four designs; the order is rotated across the three runs.
_ROT = (tuple(R.DESIGNS),) * 3  # follow-up 3: one fixed order (the run's designs), not rotated


def time3(variant, tool, base_file, kw):
    """Min of 3 unjudged runs per design on fresh copies (order rotated). Stored in TIMINGS."""
    base_file = Path(base_file)
    scratch = Path(H.BASE) / "_timing"
    scratch.mkdir(exist_ok=True)
    runs = {dz: [] for dz in _ROT[0]}
    for order in _ROT:
        for dz in order:
            d = scratch / f"t_{dz}"
            if d.exists():
                shutil.rmtree(d)
            d.mkdir()
            dst = d / base_file.name
            shutil.copyfile(base_file, dst)
            t0 = time.perf_counter()
            st, _msg = R.call(getattr(R.IMPLS[dz], tool), schematic_path=str(dst), **kw)
            runs[dz].append((time.perf_counter() - t0, st))
            shutil.rmtree(d, ignore_errors=True)
    for dz, rr in runs.items():
        TIMINGS[(variant, dz)] = {"runs_s": [round(t, 4) for t, _ in rr],
                                  "statuses": sorted({s for _, s in rr}),
                                  "min3_s": round(min(t for t, _ in rr), 4)}
    print(f"  [timing] {variant}: " + ", ".join(
        f"{dz} min {TIMINGS[(variant, dz)]['min3_s']:.3f} s {TIMINGS[(variant, dz)]['statuses']}"
        for dz in _ROT[0]), flush=True)
    return TIMINGS


def time3_chain(variant, base_file, steps):
    """Min of 3 unjudged chains per design: per-call minima and the chain total minimum."""
    base_file = Path(base_file)
    scratch = Path(H.BASE) / "_timing"
    scratch.mkdir(exist_ok=True)
    runs = {dz: [] for dz in _ROT[0]}
    for order in _ROT:
        for dz in order:
            d = scratch / f"c_{dz}"
            if d.exists():
                shutil.rmtree(d)
            d.mkdir()
            dst = d / base_file.name
            shutil.copyfile(base_file, dst)
            calls = []
            for stp in steps:
                t0 = time.perf_counter()
                st, _msg = R.call(getattr(R.IMPLS[dz], stp["tool"]), schematic_path=str(dst),
                                  **stp["kw"])
                calls.append((time.perf_counter() - t0, st))
            runs[dz].append(calls)
            shutil.rmtree(d, ignore_errors=True)
    for dz, chains in runs.items():
        totals = [sum(t for t, _ in c) for c in chains]
        per = [min(chains[r][i][0] for r in range(3)) for i in range(len(steps))]
        TIMINGS[(variant, dz)] = {"chain_runs_s": [round(t, 4) for t in totals],
                                  "chain_min3_s": round(min(totals), 4),
                                  "per_call_min3_s": [round(t, 4) for t in per],
                                  "statuses": [c[1] for c in chains[0]]}
    print(f"  [timing] {variant}: chain " + ", ".join(
        f"{dz} min {TIMINGS[(variant, dz)]['chain_min3_s']:.3f} s" for dz in _ROT[0]), flush=True)


def attach_timings():
    """Rewrite this script's parts file adding timing fields to the matching records."""
    f = R._STATE["file"]
    if f is None or not TIMINGS:
        return
    lines = Path(f).read_text(encoding="utf-8").splitlines()
    out = []
    for ln in lines:
        rec = json.loads(ln)
        t = TIMINGS.get((rec.get("variant"), rec.get("design")))
        if t and rec.get("kind") in ("main", "seq"):
            if "chain_min3_s" in t:
                i = rec.get("step") or 0
                rec["timing_min3_s"] = t["per_call_min3_s"][i] if i < len(t["per_call_min3_s"]) else None
                if i == 0:
                    rec["timing_chain_min3_s"] = t["chain_min3_s"]
                    rec["timing_chain_runs_s"] = t["chain_runs_s"]
            else:
                rec["timing_min3_s"] = t["min3_s"]
                rec["timing_runs_s"] = t["runs_s"]
                rec["timing_statuses"] = t["statuses"]
            rec["timing_note"] = TIMING_NOTE
        out.append(json.dumps(rec, default=R._jdefault))
    Path(f).write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    tj = OUTD / f"timings_{Path(f).stem}.json"  # one file per script (two scripts run at once)
    allt = {}
    for (v, dz), t in TIMINGS.items():
        allt.setdefault(v, {})[dz] = t
    tj.write_text(json.dumps(allt, indent=1), encoding="utf-8")
