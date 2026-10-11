"""Follow-up 2 re-run (designs M2, H, Hb, E) of probe-llm-workflow/run_designs.py with designs.py
(hand-written LLM plans) and run_derived.py (designs.py D1DER). Copied from follow-up 1's
pr57pt/coarse/rerun/probeC/llm-workflow/lw_designs.py; the plans, both policies, the replay pass and
the final plan check are unchanged and applied identically to every design.

Policy 'skill': a refused connect_pins -> wire_pins_to_net for that pair under the plan's net name;
a refused wire_pins_to_net -> report and continue. Policy 'adaptive': as skill, plus the retries
run_designs.adaptive() makes (ambiguous name -> the pad numbers the refusal lists; drop the
refused pins and retry the rest; a pin refused for geometry retried alone with stub_length 5.08).
"Geometry" is each design's no-geometry-fits code (lwlib.reason_kind: touch for M2/H/Hb, blocked
for E). Each design runs on its own evolving file; each call is judged against the state just
before it. Every plan under skill and adaptive with the replay pass; D1DER under skill without
replay. The final check also records pinmap (plan pin text -> pad numbers) for the summary.
"""

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H  # noqa: E402

import compat54  # noqa: E402  (the frozen 54160c1 add_power_symbol, see build)
import lwlib as LW  # noqa: E402

R.begin(LW.CAT, __file__, tag="lw_designs" + "".join("_" + a for a in sys.argv[1:]))
SCRIPT = LW.PROBE + "run_designs.py + designs.py"

# ------------------------------------------------------------------ designs.py (copied verbatim)
D1 = {
    "name": "tiny_bme",
    "desc": "ATtiny25 + BME280 I2C node, 2 decaps, I2C pull-ups, LED, 4-pin header (A4)",
    "parts": [
        ("Connector_Generic:Conn_01x04", "J1", "HDR", 50.8, 101.6, 0, ""),
        ("Device:C", "C1", "100n", 76.2, 101.6, 0, ""),
        ("MCU_Microchip_ATtiny:ATtiny25V-10P", "U1", "ATtiny25", 101.6, 101.6, 0, ""),
        ("Device:R", "R1", "4k7", 139.7, 88.9, 0, ""),
        ("Device:R", "R2", "4k7", 152.4, 88.9, 0, ""),
        ("Sensor:BME280", "U2", "BME280", 177.8, 101.6, 0, ""),
        ("Device:C", "C2", "100n", 152.4, 114.3, 0, ""),
        ("Device:R", "R3", "330", 127.0, 127.0, 90, ""),
        ("Device:LED", "D1", "LED", 139.7, 127.0, 0, ""),
    ],
    "calls": [
        ("wire", "+3V3", [("J1", "1"), ("C1", "1"), ("U1", "VCC"), ("R1", "1"), ("R2", "1"),
                          ("U2", "VDD"), ("U2", "VDDIO"), ("U2", "CSB"), ("C2", "1")]),
        ("wire", "GND", [("J1", "2"), ("C1", "2"), ("U1", "GND"), ("U2", "GND"), ("U2", "SDO"),
                         ("C2", "2"), ("D1", "K")]),
        ("wire", "SDA", [("U1", "AREF/PB0"), ("R1", "2"), ("U2", "SDI")]),
        ("wire", "SCL", [("U1", "PB2"), ("R2", "2"), ("U2", "SCK")]),
        ("connect", "LED_DRV", ("U1", "PB1"), ("R3", "1")),
        ("connect", "LED_A", ("R3", "2"), ("D1", "A")),
        ("wire", "RESET", [("U1", "~{RESET}/PB5"), ("J1", "3")]),
        ("connect", "UART_TX", ("U1", "XTAL1/PB3"), ("J1", "4")),
    ],
}

D2 = {
    "name": "stm32_usb",
    "desc": "STM32F103 + USB-micro + LDO, 4 decaps in a 10.16 mm column, BOOT0/NRST resistors,"
            " reset button, LED, SWD header (A4)",
    "parts": [
        ("Connector:USB_B_Micro", "J1", "USB", 25.4, 63.5, 0, ""),
        ("Device:C", "C1", "10u", 50.8, 76.2, 0, ""),
        ("Regulator_Linear:AP1117-15", "U2", "AP1117-3.3", 63.5, 63.5, 0, ""),
        ("Device:C", "C2", "10u", 88.9, 76.2, 0, ""),
        ("Device:C", "C3", "100n", 114.3, 76.2, 0, ""),
        ("Device:C", "C4", "100n", 114.3, 86.36, 0, ""),
        ("Device:C", "C5", "100n", 114.3, 96.52, 0, ""),
        ("Device:C", "C6", "100n", 114.3, 106.68, 0, ""),
        ("MCU_ST_STM32F1:STM32F103C_8-B_Tx", "U1", "STM32F103C8T6", 139.7, 101.6, 0, ""),
        ("Device:R", "R1", "10k", 101.6, 76.2, 0, ""),
        ("Device:R", "R2", "10k", 101.6, 127.0, 0, ""),
        ("Switch:SW_Push", "SW1", "RESET", 101.6, 144.78, 0, ""),
        ("Device:R", "R3", "330", 177.8, 63.5, 90, ""),
        ("Device:LED", "D1", "LED", 190.5, 63.5, 180, ""),
        ("Connector_Generic:Conn_01x04", "J2", "SWD", 190.5, 101.6, 0, ""),
    ],
    "calls": [
        ("wire", "VBUS", [("J1", "VBUS"), ("C1", "1"), ("U2", "VI")]),
        ("wire", "+3V3", [("U2", "VO"), ("C2", "1"), ("C3", "1"), ("C4", "1"), ("C5", "1"),
                          ("C6", "1"), ("U1", "VBAT"), ("U1", "VDD"), ("U1", "VDDA"),
                          ("R2", "1"), ("J2", "1")]),
        ("wire", "GND", [("J1", "GND"), ("J1", "Shield"), ("C1", "2"), ("U2", "GND"), ("C2", "2"),
                         ("C3", "2"), ("C4", "2"), ("C5", "2"), ("C6", "2"), ("U1", "VSS"),
                         ("U1", "VSSA"), ("R1", "2"), ("SW1", "2"), ("D1", "K"), ("J2", "4")]),
        ("wire", "USB_DP", [("J1", "D+"), ("U1", "PA12")]),
        ("wire", "USB_DM", [("J1", "D-"), ("U1", "PA11")]),
        ("connect", "BOOT0", ("U1", "BOOT0"), ("R1", "1")),
        ("wire", "NRST", [("U1", "NRST"), ("R2", "2"), ("SW1", "1")]),
        ("connect", "LED_DRV", ("U1", "PA0"), ("R3", "1")),
        ("connect", "LED_A", ("R3", "2"), ("D1", "A")),
        ("wire", "SWDIO", [("U1", "PA13"), ("J2", "2")]),
        ("wire", "SWCLK", [("U1", "PA14"), ("J2", "3")]),
    ],
}

D3 = {
    "name": "g030_leds",
    "desc": "STM32G030 + 4 LEDs with series resistors in 12.7 mm rows, button + pull-up,"
            " decap via auto_place_decoupling_cap, UART header (A4)",
    "parts": [
        ("Connector_Generic:Conn_01x04", "J1", "UART", 50.8, 101.6, 0, ""),
        ("MCU_ST_STM32G0:STM32G030F6Px", "U1", "STM32G030F6P6", 101.6, 101.6, 0, ""),
        ("Device:R", "R1", "330", 152.4, 86.36, 90, ""),
        ("Device:R", "R2", "330", 152.4, 96.52, 90, ""),
        ("Device:R", "R3", "330", 152.4, 106.68, 90, ""),
        ("Device:R", "R4", "330", 152.4, 116.84, 90, ""),
        ("Device:LED", "D1", "RED", 165.1, 86.36, 180, ""),
        ("Device:LED", "D2", "GRN", 165.1, 96.52, 180, ""),
        ("Device:LED", "D3", "BLU", 165.1, 106.68, 180, ""),
        ("Device:LED", "D4", "YEL", 165.1, 116.84, 180, ""),
        ("Switch:SW_Push", "SW1", "USER", 63.5, 127.0, 0, ""),
        ("Device:R", "R5", "10k", 76.2, 114.3, 0, ""),
    ],
    "calls": [
        ("decap", "C1", "Device:C", "100n", 101.6, 66.04, "+3V3", "GND", 0),
        ("wire", "+3V3", [("U1", "VDD"), ("J1", "1"), ("R5", "1")]),
        ("wire", "GND", [("U1", "VSS"), ("J1", "2"), ("SW1", "2"), ("D1", "K"), ("D2", "K"),
                         ("D3", "K"), ("D4", "K")]),
        ("connect", "LED1", ("U1", "PA0"), ("R1", "1")),
        ("connect", "LED2", ("U1", "PA1"), ("R2", "1")),
        ("connect", "LED3", ("U1", "PA2"), ("R3", "1")),
        ("connect", "LED4", ("U1", "PA3"), ("R4", "1")),
        ("connect", "LED1_A", ("R1", "2"), ("D1", "A")),
        ("connect", "LED2_A", ("R2", "2"), ("D2", "A")),
        ("connect", "LED3_A", ("R3", "2"), ("D3", "A")),
        ("connect", "LED4_A", ("R4", "2"), ("D4", "A")),
        ("wire", "BTN", [("U1", "PC15"), ("SW1", "1"), ("R5", "2")]),
        ("wire", "UART_TX", [("U1", "PA9/PA11"), ("J1", "3")]),
        ("wire", "UART_RX", [("U1", "PA10/PA12"), ("J1", "4")]),
    ],
}

D1PS = dict(D1, name="tiny_bme_ps",
            desc="D1 with power:+3V3 and power:GND placed first by add_power_symbol",
            setup=[("power", "power:+3V3", "#PWR01", 63.5, 76.2),
                   ("power", "power:GND", "#PWR02", 63.5, 127.0)])

D1DER = dict(D1, name="tiny_bme_derived",
             desc="D1 with the derived MCU_Microchip_ATtiny:ATtiny85-20P",
             parts=[("MCU_Microchip_ATtiny:ATtiny85-20P",) + p[1:] if p[1] == "U1" else p
                    for p in D1["parts"]])

ALSO = {
    "tiny_bme": ["LW-08", "LW-09", "LW-13", "LW-15"],
    "stm32_usb": ["LW-07", "LW-08", "LW-09", "LW-13", "LW-15"],
    "g030_leds": ["LW-08", "LW-09", "LW-13", "LW-15"],
    "tiny_bme_ps": ["LW-06", "LW-08", "LW-09", "LW-13", "LW-15"],
    "tiny_bme_derived": [],
}


# ------------------------------------------------------------------ run_designs.py (adapted copy)


def do_wire(s, tag, N, pins, extra=None, attempt="first"):
    kw = dict(pins=LW.pins_kw(pins), label_text=N)
    if extra:
        kw.update(extra)
    return s.call("wire_pins_to_net", kw, tag, attempt=attempt)


def adaptive(s, tag, N, pins, rec):
    if rec["status"] != "REFUSED":
        return
    tags = LW.refused_tags(s.full[rec["step_label"]])
    expanded, changed = [], False
    for r, x in pins:
        hit = next((reason for t, _c, reason in tags if t == f"{r}:{x}"), None)
        import re

        m = re.search(r"matches several pads \(([^)]*)\)", hit or "")
        if m:
            expanded += [(r, n.strip()) for n in m.group(1).split(",")]
            changed = True
        else:
            expanded.append((r, x))
    if changed:
        rec2 = do_wire(s, tag + " [retry: numbers]", N, expanded, attempt="retry")
        if rec2["status"] != "REFUSED":
            return
        pins, tags = expanded, LW.refused_tags(s.full[rec2["step_label"]])
    bad = {t: (codes, reason) for t, codes, reason in tags}
    keep = [(r, x) for r, x in pins if f"{r}:{x}" not in bad]
    dropped = [(r, x) for r, x in pins if f"{r}:{x}" in bad]
    if keep and dropped:
        do_wire(s, tag + f" [retry without {', '.join(t for t in bad)}]", N, keep, attempt="retry")
    for r, x in dropped:
        if LW.reason_kind(*bad[f"{r}:{x}"]) == "geometry":
            do_wire(s, tag + f" [retry {r}:{x} stub 5.08]", N, [(r, x)], {"stub_length": 5.08},
                    attempt="retry")


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
            nodes |= {(r, n) for n in LW.pin_numbers(p, r, x)}
        out[name] = nodes
    return out


def final_check(design, p, gui=False):
    """Copy of run_designs.final_check (kicad-cli reading; gui=True: on the GUI-proxy copy)."""
    want = intended(design, p)
    target = R.J.gui_tree_copy(p) if gui else p
    got = LW.nets(target)
    if got is None:
        return {"unloadable": True}
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
        if j is not None and LW.strip(got[j][0]) != name and not name.startswith(("LED_", "UART_TX")):
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


def erc_rc(p):
    """kicad-cli 9.0.8 'sch erc' exit code on p (LW-06 extra; output under the rlib tmp)."""
    import subprocess

    from mcp_server_kicad._shared import _find_kicad_cli

    out = Path(R.J.TMP) / f"erc_{Path(p).stem}.json"
    r = subprocess.run([_find_kicad_cli(), "sch", "erc", "--format", "json", "--output", str(out),
                        str(p)], capture_output=True, text=True, errors="replace", timeout=180)
    return r.returncode


def run_design(design, design_name, policy, base, replay=True):
    scen = f"run_designs:{design['name']}:{policy}"
    s = LW.Seq("LW-04", scen, design_name, base, also=ALSO[design["name"]], script=SCRIPT)
    print(f"\n=== {design['name']} impl={design_name} policy={policy}", flush=True)
    p = s.edited
    for i, c in enumerate(design["calls"], 1):
        if c[0] == "wire":
            tag = f"#{i} wire {c[1]}"
            rec = do_wire(s, tag, c[1], c[2])
            if policy == "adaptive":
                adaptive(s, tag, c[1], c[2], rec)
        elif c[0] == "connect":
            name, (r1, p1), (r2, p2) = c[1], c[2], c[3]
            tag = f"#{i} connect {r1}:{p1}->{r2}:{p2}"
            rec = s.call("connect_pins", dict(ref1=r1, pin1=p1, ref2=r2, pin2=p2), tag)
            if rec["status"] == "REFUSED":
                rec2 = do_wire(s, tag + f" [fallback wire {name}]", name, [(r1, p1), (r2, p2)],
                               attempt="fallback")
                if policy == "adaptive":
                    adaptive(s, tag + " [fallback]", name, [(r1, p1), (r2, p2)], rec2)
        elif c[0] == "decap":
            _, ref, lib, val, x, y, pn, gn, rot = c
            tag = f"#{i} decap {ref}"
            s.call("auto_place_decoupling_cap", dict(lib_id=lib, reference=ref, value=val, x=x, y=y,
                                                     power_net=pn, ground_net=gn, rotation=rot), tag)
    fc = {"cli": final_check(design, p)}
    fc["pinmap"] = {}
    for c in design["calls"]:
        for r, x in (c[2] if c[0] == "wire" else [c[2], c[3]] if c[0] == "connect"
                     else [(c[1], "1"), (c[1], "2")]):
            try:
                fc["pinmap"][f"{r}:{x}"] = LW.pin_numbers(p, r, x)
            except Exception as e:  # noqa: BLE001
                fc["pinmap"][f"{r}:{x}"] = repr(e)
    try:
        fc["gui"] = final_check(design, p, gui=True)
    except Exception as e:  # noqa: BLE001
        fc["gui"] = {"error": repr(e)}
    R.wipe_judge_scratch()
    if design["name"] == "tiny_bme_ps":
        fc["erc_rc"] = erc_rc(p)
    print(f"  FINAL {design['name']} {design_name}/{policy}: {json.dumps(fc)}", flush=True)
    dead = LW.nets(p) is None
    R.wipe_judge_scratch()
    if replay and not dead:
        print(f"  -- replay of the wiring table ({design_name}/{policy})", flush=True)
        for i, c in enumerate(design["calls"], 1):
            if c[0] == "wire":
                do_wire(s, f"R#{i} wire {c[1]}", c[1], c[2], attempt="replay")
            elif c[0] == "connect":
                name, (r1, p1), (r2, p2) = c[1], c[2], c[3]
                s.call("connect_pins", dict(ref1=r1, pin1=p1, ref2=r2, pin2=p2),
                       f"R#{i} connect {r1}:{p1}->{r2}:{p2}", attempt="replay")
    return s, fc


def build(design, policy):
    p = H.fresh(f"{design['name']}_{policy}")
    for lib, ref, val, x, y, rot, mir in design["parts"]:
        H.place(p, lib, ref, x, y, rot=rot, mirror=mir, value=val)
    for c in design.get("setup", []):
        if c[0] == "power":
            # compat54's copy of 54160c1's add_power_symbol: it places the PWR_FLAG beside the symbol
            # that HEAD's no longer does, so the fixture stays the one the baselines were taken on
            compat54.add_power_symbol(lib_id=c[1], reference=c[2], x=c[3], y=c[4], schematic_path=p)
    return p


FINALS = []
only = set(sys.argv[1:])
jobs = [(d, pol, True) for d in (D1, D2, D3, D1PS) for pol in ("skill", "adaptive")]
jobs.append((D1DER, "skill", False))
for design, policy, replay in jobs:
    if only and design["name"] not in only:
        continue
    try:
        base = build(design, policy)
    except Exception as e:  # noqa: BLE001
        print(f"\n!!! {design['name']}: fixture build failed: {type(e).__name__}: {e}", flush=True)
        FINALS.append({"design": design["name"], "policy": policy, "build_error": repr(e)})
        continue
    seqs = {}
    for dz in R.DESIGNS:
        s, fc = run_design(design, dz, policy, base, replay=replay)
        seqs[dz] = s
        FINALS.append({"design": design["name"], "policy": policy, "impl": dz, "final": fc,
                       "calls": len(s.labels)})
    LW.close_all(seqs)
    (R.OUT / ("lw_designs" + "".join("_" + a for a in sys.argv[1:]) + "_final.json")).write_text(json.dumps(FINALS, indent=1, default=list),
                                                 encoding="utf-8")
R.end()
