# probeB copy of pr57pt/probe-buses/s09_demo_sweep.py.
# Selection code (which demo sheets, which pins, which shared entry points, which N) is the
# original's, unchanged; the runner differs: instead of calling P and M on one write-blocked copy,
# every request runs M2 and K each on its own fresh copy of the standalone sheet, judged by judge3
# (root = edited = the standalone copy, as the original's truth was the standalone export).
# Usage: uv run python tools/routing_harness/suites/probeB/scripts/buses/s09_demo_sweep.py A <shard> <nshards>   |   ... B
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
PART = sys.argv[1] if len(sys.argv) > 1 else "B"
SHARD = int(sys.argv[2]) if len(sys.argv) > 2 else 0
NSH = int(sys.argv[3]) if len(sys.argv) > 3 else 1
import rlib  # noqa: E402

rlib.set_script("s09_demo_sweep", __file__, shard=f"{PART}{SHARD}of{NSH}")

import glob  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from collections import defaultdict  # noqa: E402
from pathlib import Path  # noqa: E402

from bl import M, P, _cst, is_auto, nets_coded, strip  # noqa: E402

DEMOS = str(hpaths.KICAD_DEMOS)
PER_SHEET = 25

counter = 0
for f in sorted(glob.glob(os.path.join(DEMOS, "**", "*.kicad_sch"), recursive=True)):
    raw = open(f, "rb").read()
    if b"(bus " not in raw and b"(bus\n" not in raw and b"(bus\r" not in raw:
        continue
    d = Path(tempfile.mkdtemp(prefix="s09_"))
    cp = d / Path(f).name
    shutil.copy2(f, cp)
    try:
        truth = nets_coded(str(cp))
    except Exception as e:  # noqa: BLE001
        print(f"{f}: export failed {e}")
        continue
    xml = str(cp) + ".pt.netlist.xml"
    if os.path.exists(xml):
        os.remove(xml)
    where = {n: name for _, name, v in truth for n in v}
    root = _cst.parse(raw).lists[0]
    try:
        m = M._Model(root)
        m.ensure()
    except Exception as e:  # noqa: BLE001
        print(f"{f}: M model failed {type(e).__name__}: {e}")
        continue
    bus_roots = {m.P.find(it.id) for it in m.items if it.kind == "bus"}
    seen = set()
    picked = []
    for it in m.items:
        if it.kind != "pin" or it.ref.startswith("#") or m.P.find(it.id) not in bus_roots:
            continue
        key = (it.ref, it.num)
        if key in seen or key not in where or is_auto(where[key]):
            continue
        seen.add(key)
        picked.append((key, strip(where[key])))
    picked = picked[:PER_SHEET]
    rel = f.replace(DEMOS, "").replace("\\", "/").lstrip("/")
    if PART == "A":
        for (ref, num), X in picked:
            counter += 1
            if counter % NSH != SHARD:
                continue
            rlib.run_pair(vid=f"PartA:{rel}:{ref}:{num}", src_dir=d, root_rel=cp.name,
                          edited_rel=cp.name, tool="wire_pins_to_net",
                          kw=dict(pins=[{"reference": ref, "pin": num}], label_text=X),
                          note=f"Part A: own-name request, kicad-cli standalone net {where[(ref, num)]!r}")
        continue
    # Part B: real '<' shared entry points between two different named member nets
    ents = []
    for n in root.find_all("bus_entry"):
        at, sz = n.find("at"), n.find("size")
        a = (P._iu(at.atoms[1].text), P._iu(at.atoms[2].text))
        b = (a[0] + P._iu(sz.atoms[1].text), a[1] + P._iu(sz.atoms[2].text))
        ents.append((a, b))
    by_pt = defaultdict(list)
    for e in ents:
        for pt in e:
            by_pt[pt].append(e)
    pts_index = defaultdict(list)
    for it in m.items:
        if it.kind == "wire":
            pts_index[(it.x, it.y)].append(it)
            pts_index[(it.x2, it.y2)].append(it)
    for pt, es in by_pt.items():
        if len(es) != 2:
            continue
        others = [e[1] if e[0] == pt else e[0] for e in es]
        if others[0] == others[1]:
            continue
        nets = []
        for o in others:
            ws = pts_index.get(o, [])
            if not ws:
                nets.append(None)
                continue
            r = m.C.find(ws[0].id)
            pins = [p for p in m.items if p.kind == "pin" and not p.ref.startswith("#")
                    and m.C.find(p.id) == r and (p.ref, p.num) in where]
            nets.append(pins[0] if pins else None)
        if None in nets:
            continue
        pa, pb = nets
        A, Bn = where[(pa.ref, pa.num)], where[(pb.ref, pb.num)]
        if A == Bn or is_auto(A) or is_auto(Bn):
            continue
        tag = f"PartB:{rel}:{pt[0] / 1e4},{pt[1] / 1e4}"
        note = f"{pa.ref}:{pa.num} on {A!r}, {pb.ref}:{pb.num} on {Bn!r} (standalone export)"
        rlib.run_pair(vid=f"{tag}:wire_A_to_B", src_dir=d, root_rel=cp.name, edited_rel=cp.name,
                      tool="wire_pins_to_net",
                      kw=dict(pins=[{"reference": pa.ref, "pin": pa.num}], label_text=strip(Bn)),
                      note=note + "; expected REFUSED (pin is on A)")
        rlib.run_pair(vid=f"{tag}:connect_A_B", src_dir=d, root_rel=cp.name, edited_rel=cp.name,
                      tool="connect_pins",
                      kw=dict(ref1=pa.ref, pin1=pa.num, ref2=pb.ref, pin2=pb.num),
                      note=note + "; expected REFUSED (two named nets)")
rlib.flush()
