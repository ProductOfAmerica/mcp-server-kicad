"""perf-03 / perf-04 / perf-06 verifier calls on the standalone stock copies (fixtures/verify/
<name>.base.kicad_sch, byte-identical to verify-probe-perf/fixtures/), on M2 and K through rlib.

Parts (argv[1], one parts file each):
  A   vp_stock.py A: the six named examples (wptn VP_STOCK, connect_pins).
  B   vp_stock.py B: wire_pins_to_net(VP_STOCK) on every eligible existing pin of every unnamed
      multi-pin net (vp_fastp.stock_pins) on ampli_ht, tinytapeout-demo, fpga-hp-banks; one pin per
      call. vp_m_refusal_tt.py repeats the tinytapeout-demo calls; vp_directions.py's pins map
      onto calls here or in A (recorded as duplicates in notes, a new variant only if absent).
  C   vp_stock.py C: connect_pins on the probe's 18 prepare.json pairs (ampli_ht, tinytapeout).
  X   extra plain tool calls inside P-only verifier scripts: vp_nojunction.py (wptn C201:2 'N' on
      ampli_ht; its tinytapeout R1:2->R2:2 connect is A's call), vp_fastp.py identity calls
      (wptn of ALL stock pins of a sheet in ONE call, VP_FAST), vp_perfslice.py's repo-tool call
      (wire_pins_to_net of pick_pins(50) 'PT_NET', auto_pwr_flag=False; run on the standalone copy
      so it can be judged: the call is the same, only instance paths differ from the raw demo).
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402

import json  # noqa: E402

import pfix as F  # noqa: E402

PART = sys.argv[1] if len(sys.argv) > 1 else "A"
SHEET = sys.argv[2] if len(sys.argv) > 2 else None  # B only: one sheet per process (2 h task limit)
PTAG = PART + (f"_{SHEET}" if SHEET else "")
R.begin("perf", __file__, tag=f"perf_vp_stock_{PTAG}")
PINS = F.load_pins()
S = "verify-probe-perf/vp_stock.py"
DUPS = []


def base(name):
    return F.fx(f"verify/{name}.base.kicad_sch", f"{name}.base")


EXAMPLES = [  # vp_stock.py A, verbatim
    ("ampli_ht", "wptn", {"pins": [{"reference": "C201", "pin": "2"}], "label_text": "VP_STOCK"},
     "perf-04 example", "perf-03", ["perf-04"]),
    ("tinytapeout-demo", "wptn", {"pins": [{"reference": "D1", "pin": "2"}], "label_text": "VP_STOCK"},
     "perf-03 example", "perf-03", ["perf-04"]),
    ("tinytapeout-demo", "wptn", {"pins": [{"reference": "R11", "pin": "2"}], "label_text": "VP_STOCK"},
     "perf-03 (R11:2)", "perf-03", ["perf-04"]),
    ("ampli_ht", "conn", {"ref1": "D201", "pin1": "1", "ref2": "D202", "pin2": "1"},
     "perf-03 connect example", "perf-03", ["perf-04"]),
    ("ampli_ht", "conn", {"ref1": "C201", "pin1": "2", "ref2": "C203", "pin2": "1"},
     "perf-04 connect example", "perf-03", ["perf-04"]),
    ("tinytapeout-demo", "conn", {"ref1": "R1", "pin1": "2", "ref2": "R2", "pin2": "2"},
     "perf-04 connect (SW4 pad 8)", "perf-03", ["perf-04"]),
]
TOOL = {"wptn": "wire_pins_to_net", "conn": "connect_pins"}
A_KEYS = {(name, tool, json.dumps(kw, sort_keys=True)): f"vp_stock:A:{why}"
          for name, tool, kw, why, _c, _a in EXAMPLES}
DIRECTIONS = [("ampli_ht", "C201", "2"), ("tinytapeout-demo", "D1", "2"), ("tinytapeout-demo", "R11", "2"),
              ("ampli_ht", "R202", "1"), ("ampli_ht", "D201", "1"), ("tinytapeout-demo", "R1", "2")]

if PART == "A":
    fx = {}
    for name, tool, kw, why, case, also in EXAMPLES:
        if name not in fx:
            fx[name] = base(name)
        R.run_case(case, f"vp_stock:A:{why}", TOOL[tool], fx[name], kw, script=S, also=also,
                   note=f"{why}; also vp_nojunction.py's call when it is the tinytapeout R1:2->R2:2 connect"
                   if "SW4" in why else why)
    DUPS.append({"variant": "vp_nojunction:stock tinytapeout connect R1:2->R2:2",
                 "same_as": "vp_stock:A:perf-04 connect (SW4 pad 8)"})

elif PART == "B":
    for name in [n for n in ("ampli_ht", "tinytapeout-demo", "fpga-hp-banks") if SHEET in (None, n)]:
        p = base(name)
        sweep = PINS["stock_pins"][name]
        print(f"# sweep {name}: {len(sweep)} pins", flush=True)
        for q in sweep:
            kw = {"pins": [q], "label_text": "VP_STOCK"}
            key = (name, "wptn", json.dumps(kw, sort_keys=True))
            v = f"vp_stock:B:{name}:{q['reference']}:{q['pin']}"
            if key in A_KEYS:
                DUPS.append({"variant": v, "same_as": A_KEYS[key]})
                continue
            R.run_case("perf-03", v, "wire_pins_to_net", p, kw, script=S, also=["perf-04"],
                       note="sweep; vp_m_refusal_tt.py repeats this call" if name == "tinytapeout-demo"
                       else "sweep")
        if name == "tinytapeout-demo":
            for q in sweep:
                DUPS.append({"variant": f"vp_m_refusal_tt:{q['reference']}:{q['pin']}",
                             "same_as": f"vp_stock:B:{name}:{q['reference']}:{q['pin']}"
                             if (name, "wptn", json.dumps({"pins": [q], "label_text": "VP_STOCK"},
                                                           sort_keys=True)) not in A_KEYS
                             else A_KEYS[(name, "wptn", json.dumps({"pins": [q], "label_text": "VP_STOCK"},
                                                                   sort_keys=True))]})
    # vp_directions.py pins: duplicates of A or B calls, else a new call
    for name, ref, pin in [d for d in DIRECTIONS if SHEET in (None, d[0])]:
        q = {"reference": ref, "pin": pin}
        kw = {"pins": [q], "label_text": "VP_STOCK"}
        key = (name, "wptn", json.dumps(kw, sort_keys=True))
        if key in A_KEYS:
            DUPS.append({"variant": f"vp_directions:{name}:{ref}:{pin}", "same_as": A_KEYS[key]})
        elif q in PINS["stock_pins"].get(name, []):
            DUPS.append({"variant": f"vp_directions:{name}:{ref}:{pin}",
                         "same_as": f"vp_stock:B:{name}:{ref}:{pin}"})
        else:
            R.run_case("perf-03", f"vp_directions:{name}:{ref}:{pin}", "wire_pins_to_net", base(name),
                       kw, script="verify-probe-perf/vp_directions.py",
                       note="vp_directions inspects this pin's outward direction; the M2/K counterpart"
                       " is wire_pins_to_net(VP_STOCK) on it")

elif PART == "C":
    for name in ("ampli_ht", "tinytapeout-demo"):
        p = base(name)
        for q in PINS["prepare"][name]["pairs"]:
            a, b = (q["a"]["ref"], q["a"]["pin"]), (q["b"]["ref"], q["b"]["pin"])
            kw = {"ref1": a[0], "pin1": a[1], "ref2": b[0], "pin2": b[1]}
            key = (name, "conn", json.dumps(kw, sort_keys=True))
            v = f"vp_stock:C:{name}:{a[0]}:{a[1]}->{b[0]}:{b[1]}"
            if key in A_KEYS:
                DUPS.append({"variant": v, "same_as": A_KEYS[key]})
                continue
            R.run_case("perf-03", v, "connect_pins", p, kw, script=S,
                       note=f"probe pair (dist {q['dist_mm']} mm) re-judged on the standalone copy")

elif PART == "X":
    p = base("ampli_ht")
    R.run_case("perf-04", "vp_nojunction:stock ampli_ht wptn C201:2 N", "wire_pins_to_net", p,
               {"pins": [{"reference": "C201", "pin": "2"}], "label_text": "N"},
               script="verify-probe-perf/vp_nojunction.py",
               note="P-only fix-variant script; its plain tool call on the stock fixture")
    for name in ("ampli_ht", "tinytapeout-demo", "fpga-hp-banks"):
        sp = PINS["stock_pins"][name]
        R.run_case("perf-01", f"vp_fastp:{name}.base:all{len(sp)}-one-call", "wire_pins_to_net", base(name),
                   {"pins": sp, "label_text": "VP_FAST"}, script="verify-probe-perf/vp_fastp.py",
                   note="vp_fastp identity check: every stock pin of the sheet in ONE call")
    for name in ("tinytapeout-demo", "fpga-hp-banks", "fpga-power", "ampli_ht"):
        p = base(name)
        kw = {"pins": PINS["pick_pins"][name], "label_text": "PT_NET", "auto_pwr_flag": False}
        v = f"vp_perfslice:{name}:pick50"
        F.time3(v, "wire_pins_to_net", p, kw)
        R.run_case("perf-06", v, "wire_pins_to_net", p, kw, script="verify-probe-perf/vp_perfslice.py",
                   note="the perf slice's repo-tool call (pick_pins 50, PT_NET, auto_pwr_flag=False) on"
                   " the standalone copy")

F.attach_timings()
(F.OUTD / f"dups_vp_stock_{PTAG}.json").write_text(json.dumps(DUPS, indent=1), encoding="utf-8")
R.end()
