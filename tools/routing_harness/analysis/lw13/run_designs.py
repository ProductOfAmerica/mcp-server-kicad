"""Execute each LLM plan with B, P and M under two refusal policies, judging every call.

Policies (schematic-design skill, 'Error Handling'):
  skill     connect_pins refused -> wire_pins_to_net for that pair under the plan's net name;
            wire_pins_to_net refused -> report and continue.
  adaptive  as skill, plus what an LLM reading the refusal would do: an ambiguous pin name that
            the refusal resolves into pad numbers is retried with those numbers; pins the refusal
            names are dropped and the rest of the batch is retried; each dropped pin refused for
            geometry is retried alone with stub_length 5.08 ('try different parameters').
Final check per run: the intended netlist (plan) against kicad-cli's final netlist.

B is the repo's own tools at hpaths.TARGET (see run_generated.py: the pressure test's figures need
PT_REPO set to a git archive of 54160c1). Results go to run_designs.json in the run's lw13 output
directory (hpaths.suite_out("lw13")).

usage: run_designs.py [design names...]   (default: all)
Run: uv run python tools/routing_harness/analysis/lw13/run_designs.py tiny_bme
"""

import json
import re
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__, suites=("layouts_stock",))
import llmlib as L  # noqa: E402
from designs import DESIGNS  # noqa: E402
from harness import fresh, place  # noqa: E402

OUT = str(hpaths.suite_out("lw13") / "run_designs.json")
REPLAY = True


def log(s):
    print(s, flush=True)


def pins_kw(pins):
    return [{"reference": r, "pin": x} for r, x in pins]


def refused_tags(impl, msg):
    """[(tag, reason)] the refusal names, for P and M message formats."""
    out = []
    if impl == "M":
        for line in msg.splitlines():
            m = re.match(r"- REFUSED (\S+?:\S+?): (.*)", line)
            if m:
                out.append((m.group(1), m.group(2)))
    elif impl == "P":
        body = msg.split("nothing was written. ", 1)[-1]
        for part in body.split(" | "):
            m = re.match(r"(\S+?:\S+?): refused \((.*)\)$", part.strip())
            if m:
                out.append((m.group(1), m.group(2)))
    return out


def reason_kind(reason):
    r = reason.lower()
    if "matches several pads" in r:
        return "ambiguous-name"
    if "no-connect" in r:
        return "nc"
    if "already on" in r and ("merge" in r):
        return "name"
    if "hierarchy port" in r or "text variable" in r:
        return "port/unknown"
    if "read differently" in r:
        return "cross-version"
    if "not placed" in r or "no pin numbered" in r or "not found" in r:
        return "lookup"
    return "geometry"


def do_wire(run, tag, N, pins, extra=None):
    kw = dict(pins=pins_kw(pins), label_text=N, schematic_path=run.p)
    if extra:
        kw.update(extra)
    reqs = [(f"{r}:{x}", r, x, N) for r, x in pins]
    return run.step(tag, "wire", kw, reqs, [N])


def adaptive(run, tag, N, pins, rec):
    if rec["status"] != "REFUSED" or run.impl == "B":
        return
    tags = refused_tags(run.impl, rec["msg"])
    # 1. ambiguous names -> numbers listed in the refusal
    expanded, changed = [], False
    for r, x in pins:
        hit = next((reason for t, reason in tags if t == f"{r}:{x}"), None)
        m = re.search(r"matches several pads \(([^)]*)\)", hit or "")
        if m:
            expanded += [(r, n.strip()) for n in m.group(1).split(",")]
            changed = True
        else:
            expanded.append((r, x))
    if changed:
        rec2 = do_wire(run, tag + " [retry: numbers]", N, expanded)
        if rec2["status"] != "REFUSED":
            return
        pins, tags = expanded, refused_tags(run.impl, rec2["msg"])
    # 2. drop the refused pins, retry the rest
    bad = {t: reason for t, reason in tags}
    keep = [(r, x) for r, x in pins if f"{r}:{x}" not in bad]
    dropped = [(r, x) for r, x in pins if f"{r}:{x}" in bad]
    if keep and dropped:
        do_wire(run, tag + f" [retry without {', '.join(t for t in bad)}]", N, keep)
    # 3. geometric refusals alone with a longer stub
    for r, x in dropped:
        if reason_kind(bad[f"{r}:{x}"]) == "geometry":
            do_wire(run, tag + f" [retry {r}:{x} stub 5.08]", N, [(r, x)], {"stub_length": 5.08})


def intended(design, p):
    nets = {}
    for c in design["calls"]:
        if c[0] == "wire":
            nets.setdefault(c[1], []).extend(c[2])
        elif c[0] == "connect":
            nets.setdefault(c[1], []).extend([c[2], c[3]])
        elif c[0] == "decap":
            nets.setdefault(c[6], []).append((c[1], "1"))
            nets.setdefault(c[7], []).append((c[1], "2"))
    out = {}
    for name, pins in nets.items():
        nodes = set()
        for r, x in pins:
            nodes |= {(r, n) for n in L.pin_numbers(p, r, x)}
        out[name] = nodes
    # union intended nets that share a pin (a pin listed under two plan nets)
    return out


def final_check(design, p):
    want = intended(design, p)
    try:
        got = L.nets(p)
    except L.Unloadable as e:
        return {"unloadable": str(e)}
    where = {}
    for j, (name, v) in enumerate(got):
        for n in v:
            where[n] = j
    owner = {}
    for name, nodes in want.items():
        for n in nodes:
            owner.setdefault(n, set()).add(name)
    incomplete, misnamed = {}, {}
    for name, nodes in want.items():
        comps = {}
        for n in nodes:
            comps.setdefault(where.get(n), []).append(n)
        if len(comps) > 1:
            biggest = max(comps.values(), key=len)
            incomplete[name] = sorted(n for v in comps.values() if v is not biggest for n in v)
        j = max(comps, key=lambda k: len(comps[k]))
        if j is not None and L.strip(got[j][0]) != name and not name.startswith(("LED_", "UART_TX")):
            misnamed[name] = got[j][0]
    shorts = []
    for j, (name, v) in enumerate(got):
        plan_nets = set()
        stray = []
        for n in v:
            if n in owner:
                plan_nets |= owner[n]
            else:
                stray.append(n)
        if len(plan_nets) > 1:
            shorts.append(f"{name}: plan nets {sorted(plan_nets)} joined")
        if plan_nets and stray:
            shorts.append(f"{name}: unplanned pins {sorted(stray)} joined plan net {sorted(plan_nets)}")
        if not plan_nets and len(stray) > 1:
            shorts.append(f"{name}: unplanned pins joined {sorted(stray)}")
    total = sum(len(v) for v in want.values())
    missing = sum(len(v) for v in incomplete.values())
    return {"planned_pins": total, "pins_not_on_plan_net": missing, "incomplete": incomplete,
            "shorts": shorts, "misnamed": misnamed}


def run_design(design, impl, policy, replay=None):
    p = fresh(f"{design['name']}_{impl}_{policy}")
    if tuple(design.get("page", (297, 210))) == (420, 297):
        L.B.set_page_size(size="A3", schematic_path=p)
    for lib, ref, val, x, y, rot, mir in design["parts"]:
        place(p, lib, ref, x, y, rot=rot, mirror=mir, value=val)
    for c in design.get("setup", []):
        if c[0] == "power":
            L.B.add_power_symbol(lib_id=c[1], reference=c[2], x=c[3], y=c[4], schematic_path=p)
    log(f"\n=== {design['name']} impl={impl} policy={policy} file={p}")
    run = L.Run(p, impl, log)
    for i, c in enumerate(design["calls"], 1):
        if c[0] == "wire" and policy == "unbatched":
            for r_, x_ in c[2]:
                do_wire(run, f"#{i} wire {c[1]} {r_}:{x_}", c[1], [(r_, x_)])
        elif c[0] == "wire":
            tag = f"#{i} wire {c[1]}"
            rec = do_wire(run, tag, c[1], c[2])
            if policy == "adaptive":
                adaptive(run, tag, c[1], c[2], rec)
        elif c[0] == "connect":
            name, (r1, p1), (r2, p2) = c[1], c[2], c[3]
            tag = f"#{i} connect {r1}:{p1}->{r2}:{p2}"
            kw = dict(ref1=r1, pin1=p1, ref2=r2, pin2=p2, schematic_path=p)
            rec = run.step(tag, "connect", kw, [(name, (r1, p1), (r2, p2))], [])
            if rec["status"] == "REFUSED":
                rec2 = do_wire(run, tag + f" [fallback wire {name}]", name, [(r1, p1), (r2, p2)])
                if policy == "adaptive":
                    adaptive(run, tag + " [fallback]", name, [(r1, p1), (r2, p2)], rec2)
        elif c[0] == "decap":
            _, ref, lib, val, x, y, pn, gn, rot = c
            tag = f"#{i} decap {ref}"
            kw = dict(lib_id=lib, reference=ref, value=val, x=x, y=y, power_net=pn,
                      ground_net=gn, rotation=rot, schematic_path=p)
            reqs = [(f"{ref}:1", ref, "1", pn), (f"{ref}:2", ref, "2", gn)]
            run.step(tag, "decap", kw, reqs, [pn, gn], extra_nodes=((ref, "1"), (ref, "2")))
    fc = final_check(design, p)
    log(f"  FINAL {design['name']} {impl}/{policy}: {json.dumps(fc)}")
    # Replay: an agent that lost track re-executes the whole wiring table once (first attempts
    # only). Every call should now be a no-op that leaves the file byte-identical.
    n0 = len(run.records)
    if (REPLAY if replay is None else replay) and not run.dead:
        log(f"  -- replay of the wiring table ({impl}/{policy})")
        for i, c in enumerate(design["calls"], 1):
            if c[0] == "wire":
                do_wire(run, f"R#{i} wire {c[1]}", c[1], c[2])
            elif c[0] == "connect":
                name, (r1, p1), (r2, p2) = c[1], c[2], c[3]
                kw = dict(ref1=r1, pin1=p1, ref2=r2, pin2=p2, schematic_path=p)
                run.step(f"R#{i} connect {r1}:{p1}->{r2}:{p2}", "connect", kw,
                         [(name, (r1, p1), (r2, p2))], [])
    replay = [{"tag": r["tag"], "status": r["status"], "changed": r["changed"],
               "problems": r["problems"], "msg": r["msg"][:400]} for r in run.records[n0:]]
    return {"design": design["name"], "impl": impl, "policy": policy, "file": p,
            "records": run.records[:n0], "final": fc, "replay": replay}


def main():
    want = set(sys.argv[1:])
    results = []
    t0 = time.time()
    for d in DESIGNS:
        if want and d["name"] not in want:
            continue
        for policy in ("skill", "adaptive"):
            for impl in ("B", "P", "M"):
                if impl == "B" and policy == "adaptive":
                    continue  # B never refuses for geometry; one run is enough
                results.append(run_design(d, impl, policy))
    json.dump(results, open(OUT, "w", encoding="utf-8"), indent=1, default=list)
    log(f"\nwrote {OUT} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
