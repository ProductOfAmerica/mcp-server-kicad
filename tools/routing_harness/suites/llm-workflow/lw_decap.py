"""Rerun on M2, H, Hb and E (follow-up 2 copy of the follow-up 1 script) of the auto_place_decoupling_cap scenarios (LW-05, LW-11):
  decap_sweep.py      every (lib, rotation, dx, dy) of Device:C / Device:C_Small x 0/90/180/270 x
                      dx -2.54/0/2.54 x dy -5.08/-7.62/-10.16 (72), cap origin at VDD pin + (dx, dy),
                      power_net '+3V3', ground_net 'GND'. Base: STM32G030F6Px U1 at (101.6, 101.6), a
                      raw 2.54 mm VDD stub drawn pin-first to label '+3V3' (rot 90), a VSS stub to 'GND'.
  decap_reverse.py    the VDD stub drawn pin-first and label-first, placements (C rot 180 at
                      VDD+(0,-5.08)) and (C rot 0 at VDD+(2.54,-7.62)). Its pin-first fixture and both
                      calls equal decap_sweep's base and combos, so they run once (in decap_sweep).
  v_lw05_11_decap.py  C rot 180 at VDD+(0,-5.08) on the label-first and pin-first bases: the same
                      fixtures and call as decap_reverse / decap_sweep, so they run once there. Its
                      LW-11 attribution part: label-first base, C1 placed by the repo's
                      place_component, then wire_pins_to_net([C1:1], '+3V3') and ([C1:2], 'GND') each on
                      a fresh copy (run here). ALT-L / ALT-S are raw controls (not re-run).
"""

import itertools
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import H  # noqa: E402

import lwlib as LW  # noqa: E402

R.begin(LW.CAT, __file__, tag="lw_decap" + "".join("_" + a for a in sys.argv[1:]))
which = set(sys.argv[1:]) or {"sweep", "reverse", "v11"}


def base(tag, order):
    p = H.fresh(tag)
    H.place(p, "MCU_ST_STM32G0:STM32G030F6Px", "U1", 101.6, 101.6, value="STM32G030")
    pp = R.M2.pin_positions(p, "U1")
    vx, vy, _ = pp["4"][0]
    sx, sy, _ = pp["5"][0]
    if order == "pin-first":
        H.wire(p, vx, vy, vx, vy - 2.54)
    else:
        H.wire(p, vx, vy - 2.54, vx, vy)
    H.label(p, "+3V3", vx, vy - 2.54, rot=90)
    H.wire(p, sx, sy, sx, sy + 2.54)
    H.label(p, "GND", sx, sy + 2.54, rot=270)
    return p, (vx, vy)


def cap_kw(lib, rot, x, y):
    return dict(lib_id=lib, reference="C1", value="100n", x=x, y=y, power_net="+3V3",
                ground_net="GND", rotation=rot)


DUPS = {("Device:C", 180, 0.0, -5.08): "also decap_reverse.py pin-first and v_lw05_11_decap.py pin-first",
        ("Device:C", 0, 2.54, -7.62): "also decap_reverse.py pin-first"}

if "sweep" in which:
    p, (vx, vy) = base("decap_base", "pin-first")
    print(f"VDD pin {vx},{vy}; base {p}", flush=True)
    combos = list(itertools.product(("Device:C", "Device:C_Small"), (0, 90, 180, 270),
                                    (-2.54, 0.0, 2.54), (-5.08, -7.62, -10.16)))
    for lib, rot, dx, dy in combos:
        x, y = round(vx + dx, 4), round(vy + dy, 4)
        R.run_case("LW-05", f"decap_sweep:{lib.split(':')[1]} rot{rot} at VDD{dx:+g},{dy:+g}",
                   "auto_place_decoupling_cap", p, cap_kw(lib, rot, x, y), also=["LW-11"],
                   script=LW.PROBE + "decap_sweep.py", note=DUPS.get((lib, rot, dx, dy), ""))

if "reverse" in which:
    p, (vx, vy) = base("decap_rev_label-first", "label-first")
    for lib, rot, dx, dy in (("Device:C", 180, 0.0, -5.08), ("Device:C", 0, 2.54, -7.62)):
        note = "also v_lw05_11_decap.py label-first" if rot == 180 else ""
        R.run_case("LW-05", f"decap_reverse:label-first {lib} rot{rot} at VDD{dx:+g},{dy:+g}",
                   "auto_place_decoupling_cap", p,
                   cap_kw(lib, rot, round(vx + dx, 4), round(vy + dy, 4)),
                   also=["LW-11"] if rot == 180 else [], script=LW.PROBE + "decap_reverse.py",
                   note=note)

if "v11" in which:
    p, (vx, vy) = base("v11_label-first", "label-first")
    H.schematic.place_component(lib_id="Device:C", reference="C1", value="100n", x=vx,
                                y=round(vy - 5.08, 4), rotation=180, schematic_path=p)
    for pin, N in (("1", "+3V3"), ("2", "GND")):
        R.run_case("LW-11", f"v_lw05_11_decap:LW-11 wire [C1:{pin}] -> {N} after place_component",
                   "wire_pins_to_net", p, dict(pins=LW.pins_kw([("C1", pin)]), label_text=N),
                   script=LW.VERIFY + "v_lw05_11_decap.py",
                   note="cap placed by the repo's place_component (rot 180 at VDD+(0,-5.08)), label-first stub")
R.end()
