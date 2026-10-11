"""Rerun on M2, H, Hb and E (follow-up 2 copy of the follow-up 1 script) of the small llm-workflow scenarios:
  tee_probe.py            LW-12  add_wires tee on an unsplit GND rail, then wire_pins_to_net([R1:2], 'GND')
  v_lw12_tee.py           LW-12  the verifier's own tee geometry, same call
  v_lw04_07_dupnames.py   LW-04 D1/D2 (with the conditional replay), LW-07 D3/D3b, LW-08 D4
  v_lw06_pwrflag.py       LW-06  add_power_symbol first (and a control), then VDD and VSS calls;
                                 kicad-cli 'sch erc' and 'sch export netlist' exit codes recorded in
                                 lw_small_erc.json (extra; no record)
  v_lw09_zroute.py        LW-09  connect_pins Z1/Z2; if not OK, wire_pins_to_net fallback 'NETZ'.
                                 The raw 3-segment route is a control and is not re-run.
Fixture code copied from the originals (repo add_wires / add_power_symbol / place_component are
fixture building).
"""

import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H  # noqa: E402

import compat54  # noqa: E402  (the frozen 54160c1 add_power_symbol, see v06)
import lwlib as LW  # noqa: E402
from mcp_server_kicad._shared import _find_kicad_cli  # noqa: E402

R.begin(LW.CAT, __file__, tag="lw_small" + "".join("_" + a for a in sys.argv[1:]))
B = H.schematic
which = set(sys.argv[1:]) or {"tee", "v12", "v04", "v06", "v09"}

# ------------------------------------------------------------------ tee_probe.py
if "tee" in which:
    for order in ("label-end-first", "label-end-last"):
        p = H.fresh(f"tee_{order}")
        H.place(p, "Device:R", "R1", 139.7, 101.6, value="1k")
        H.place(p, "Device:R", "R9", 165.1, 101.6, value="1k")
        x1, x2 = (129.54, 165.1) if order == "label-end-first" else (165.1, 129.54)
        B.add_wires(wires=[{"x1": 165.1, "y1": 105.41, "x2": 165.1, "y2": 120.65}], schematic_path=p)
        B.add_wires(wires=[{"x1": x1, "y1": 120.65, "x2": x2, "y2": 120.65}], schematic_path=p)
        H.label(p, "GND", 129.54, 120.65)
        B.add_wires(wires=[{"x1": 139.7, "y1": 105.41, "x2": 139.7, "y2": 120.65}], schematic_path=p)
        R.run_case("LW-12", f"tee_probe:{order}", "wire_pins_to_net", p,
                   dict(pins=LW.pins_kw([("R1", "2")]), label_text="GND"),
                   script=LW.PROBE + "tee_probe.py", note="re-request R1:2 -> GND after an add_wires tee")

# ------------------------------------------------------------------ v_lw12_tee.py
if "v12" in which:
    for order in ("label-end-last", "label-end-first"):
        p = H.fresh(f"v12_{order}")
        H.place(p, "Device:R", "R1", 127.0, 88.9, value="1k")
        H.place(p, "Device:R", "R9", 152.4, 88.9, value="1k")
        B.add_wires(wires=[{"x1": 152.4, "y1": 92.71, "x2": 152.4, "y2": 106.68}], schematic_path=p)
        xa, xb = (152.4, 114.3) if order == "label-end-last" else (114.3, 152.4)
        B.add_wires(wires=[{"x1": xa, "y1": 106.68, "x2": xb, "y2": 106.68}], schematic_path=p)
        H.label(p, "GND", 114.3, 106.68)
        B.add_wires(wires=[{"x1": 127.0, "y1": 92.71, "x2": 127.0, "y2": 106.68}], schematic_path=p)
        R.run_case("LW-12", f"v_lw12_tee:{order}", "wire_pins_to_net", p,
                   dict(pins=LW.pins_kw([("R1", "2")]), label_text="GND"),
                   script=LW.VERIFY + "v_lw12_tee.py")

# ------------------------------------------------------------------ v_lw04_07_dupnames.py
if "v04" in which:
    BME = [("Sensor:BME280", "U2", 127.0, 101.6)]
    F103 = [("MCU_ST_STM32F1:STM32F103C_8-B_Tx", "U1", 139.7, 101.6)]

    def build(tag, parts):
        p = H.fresh(tag)
        for lib, ref, x, y in parts:
            H.place(p, lib, ref, x, y, value=ref)
        return p

    def scenario(case, also, tag, parts, pins, N, replay=True):
        """One call; replay (same call again) only for a design whose first call was OK."""
        base = build(f"v04_{tag}", parts)
        kw = dict(pins=LW.pins_kw(pins), label_text=N)
        LW.header(f"v_lw04_07_dupnames {tag}")
        seqs = {}
        for dz in R.DESIGNS:
            s = LW.Seq(case, f"v_lw04_07_dupnames:{tag}", dz, base, also=also,
                       script=LW.VERIFY + "v_lw04_07_dupnames.py")
            r = s.call("wire_pins_to_net", kw, f"wire {[f'{a}:{b}' for a, b in pins]} -> {N}")
            if replay and r["status"] == "OK":
                s.call("wire_pins_to_net", kw, f"replay {[f'{a}:{b}' for a, b in pins]} -> {N}",
                       attempt="replay")
            seqs[dz] = s
        st = {dz: s.recs[s.labels[0]]["status"] for dz, s in seqs.items()}
        LW.close_all(seqs)
        return st

    scenario("LW-04", [], "D1_bme_GND", BME, [("U2", "GND")], "GND")
    scenario("LW-04", [], "D2_f103_VDD", F103, [("U1", "VDD")], "+3V3")
    st = scenario("LW-07", [], "D3_f103_VSS", F103, [("U1", "VSS")], "GND", replay=False)
    refusers = tuple(dz for dz in R.DESIGNS if st[dz] == "REFUSED")
    if refusers:  # the original ran D3b for M when M refused D3
        base = build("v04_D3b", F103)
        R.run_case("LW-07", "v_lw04_07_dupnames:D3b_f103_VSS_by_number", "wire_pins_to_net", base,
                   dict(pins=LW.pins_kw([("U1", "23")]), label_text="GND"), designs=refusers,
                   attempt="retry",
                   script=LW.VERIFY + "v_lw04_07_dupnames.py",
                   note=f"run for the design(s) that refused D3: {refusers}")
    scenario("LW-08", [], "D4_gnd_batch",
             F103 + [("Device:C", "C1", 101.6, 76.2), ("Connector_Generic:Conn_01x02", "J1", 63.5, 101.6)],
             [("U1", "VSS"), ("C1", "2"), ("J1", "2")], "GND", replay=False)

# ------------------------------------------------------------------ v_lw06_pwrflag.py
if "v06" in which:
    CLI = _find_kicad_cli()
    ERC = []

    def rc(args):
        r = subprocess.run([CLI] + args, capture_output=True, text=True, errors="replace", timeout=180)
        return r.returncode

    for setup in ("add_power_symbol first", "control: no add_power_symbol"):
        tag = "ps" if setup.startswith("add") else "ctl"
        p = H.fresh(f"v06_{tag}")
        H.place(p, "MCU_ST_STM32G0:STM32G030F6Px", "U1", 101.6, 101.6, value="STM32G030")
        if tag == "ps":
            # compat54's copy of 54160c1's add_power_symbol (it places the PWR_FLAG HEAD's no longer does)
            compat54.add_power_symbol(lib_id="power:+3V3", reference="#PWR01", x=63.5, y=63.5, schematic_path=p)
            compat54.add_power_symbol(lib_id="power:GND", reference="#PWR02", x=63.5, y=139.7, schematic_path=p)
        LW.header(f"v_lw06_pwrflag {setup}")
        seqs = {}
        for dz in R.DESIGNS:
            s = LW.Seq("LW-06", f"v_lw06_pwrflag:{tag}", dz, p, script=LW.VERIFY + "v_lw06_pwrflag.py",
                       note=setup)
            s.call("wire_pins_to_net", dict(pins=LW.pins_kw([("U1", "VDD")]), label_text="+3V3"),
                   "wire [U1:VDD] -> +3V3")
            s.call("wire_pins_to_net", dict(pins=LW.pins_kw([("U1", "VSS")]), label_text="GND"),
                   "wire [U1:VSS] -> GND")
            out = Path(R.J.TMP) / f"v06_{tag}_{dz}.erc.json"
            erc = rc(["sch", "erc", "--format", "json", "--output", str(out), str(s.edited)])
            net = rc(["sch", "export", "netlist", "--format", "kicadxml", "--output",
                      str(Path(R.J.TMP) / f"v06_{tag}_{dz}.xml"), str(s.edited)])
            ERC.append({"setup": setup, "design": dz, "erc_rc": erc, "erc_rc_hex": hex(erc & 0xffffffff),
                        "netlist_rc": net})
            print(f"  ERC [{dz}] {setup}: sch erc rc={erc} ({hex(erc & 0xffffffff)}); netlist rc={net}",
                  flush=True)
            R.wipe_judge_scratch()
            seqs[dz] = s
        LW.close_all(seqs)
    (R.OUT / "lw_small_erc.json").write_text(json.dumps(ERC, indent=1), encoding="utf-8")

# ------------------------------------------------------------------ v_lw09_zroute.py
if "v09" in which:
    def z1():
        p = H.fresh("v09_z1")
        H.place(p, "MCU_ST_STM32G0:STM32G030F6Px", "U1", 101.6, 101.6, value="STM32G030")
        H.place(p, "Device:R", "R1", 139.7, 99.06, value="1k")
        return p, ("U1", "PA1"), ("R1", "2")

    def z2():
        p = H.fresh("v09_z2")
        H.place(p, "Device:R", "R3", 127.0, 101.6, rot=90, value="330")
        H.place(p, "Device:LED", "D1", 139.7, 101.6, rot=0, value="LED")
        return p, ("R3", "2"), ("D1", "A")

    for name, mk in (("Z1", z1), ("Z2", z2)):
        base, a, b = mk()
        LW.header(f"v_lw09_zroute {name}")
        seqs = {}
        for dz in R.DESIGNS:
            s = LW.Seq("LW-09", f"v_lw09_zroute:{name}", dz, base, script=LW.VERIFY + "v_lw09_zroute.py")
            r = s.call("connect_pins", dict(ref1=a[0], pin1=a[1], ref2=b[0], pin2=b[1]),
                       f"connect {a[0]}:{a[1]}->{b[0]}:{b[1]}")
            if r["status"] != "OK":
                s.call("wire_pins_to_net", dict(pins=LW.pins_kw([a, b]), label_text="NETZ"),
                       "fallback wire NETZ", attempt="fallback")
            seqs[dz] = s
        LW.close_all(seqs)
R.end()
