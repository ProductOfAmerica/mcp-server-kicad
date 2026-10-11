"""Rerun of probe-llm-workflow/star_probe.py and verify-probe-llm-workflow/v_lw01_star.py on M2, H, Hb and E (follow-up 2 copy of the follow-up 1 script).

star_probe: U1 STM32G030F6Px at (101.6, 101.6); R1 at (127 + dxr, 88.9 + dyr) rot rr; C1 at
(127 + dxc, 101.6); combos over dxr (12.7, 20.32), dyr (-7.62, 0, 10.16), dxc (7.62, 15.24),
rr (0, 90), dropping those whose body boxes overlap (gen_layouts.bbox/overlaps, margin 0).
Sequence per design: connect_pins(U1:PA1, C1:1); connect_pins(R1:1, U1:PA1); if that second call
is REFUSED, the skill fallback wire_pins_to_net([R1:1, U1:PA1], 'ADC').
v_lw01_star: the probe's star_1 / star_3 placements plus the verifier's own grid; a placement
whose pin ends coincide is skipped (pins_ok); first connect not OK -> stop; second connect OK ->
stop; otherwise the same fallback.
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

R.begin(LW.CAT, __file__, tag="lw_star" + "".join("_" + a for a in sys.argv[1:]))
which = set(sys.argv[1:]) or {"probe", "verify"}


def fixture(name, r_xy, rr, c_xy):
    p = H.fresh(name)
    H.place(p, "MCU_ST_STM32G0:STM32G030F6Px", "U1", 101.6, 101.6, value="STM32G030")
    H.place(p, "Device:R", "R1", *r_xy, rot=rr, value="1k")
    H.place(p, "Device:C", "C1", *c_xy, value="100n")
    return p


C1K = dict(ref1="U1", pin1="PA1", ref2="C1", pin2="1")
C2K = dict(ref1="R1", pin1="1", ref2="U1", pin2="PA1")
WK = dict(pins=LW.pins_kw([("R1", "1"), ("U1", "PA1")]), label_text="ADC")
PROBE_DONE = {}  # pkey(r_xy, rr, c_xy) -> (k, {design: (status 1st connect, status 2nd connect)})


def pkey(r_xy, rr, c_xy):
    """Placement key rounded to 0.1 um (star_probe's sums carry float noise, e.g. 101.60000000000001)."""
    return (tuple(round(v, 4) for v in r_xy), rr, tuple(round(v, 4) for v in c_xy))

# ------------------------------------------------------------------ star_probe.py
if "probe" in which:
    combos = []
    for dxr, dyr, dxc, rr in itertools.product((12.7, 20.32), (-7.62, 0.0, 10.16), (7.62, 15.24),
                                               (0, 90)):
        r_xy = (127 + dxr, 88.9 + dyr)
        c_xy = (127 + dxc, 88.9 + 12.7)
        boxes = [LW.bbox("MCU_ST_STM32G0:STM32G030F6Px", 101.6, 101.6, 0),
                 LW.bbox("Device:R", *r_xy, rr), LW.bbox("Device:C", *c_xy, 0)]
        if any(LW.overlaps(a, b, 0.0) for i, a in enumerate(boxes) for b in boxes[i + 1:]):
            continue
        combos.append((r_xy, rr, c_xy))
    print(f"{len(combos)} placements", flush=True)
    for k, (r_xy, rr, c_xy) in enumerate(combos):
        base = fixture(f"star_{k}", r_xy, rr, c_xy)
        LW.header(f"star_probe star_{k}: R1 at {r_xy} rot{rr}, C1 at {c_xy}")
        seqs = {}
        sts = {}
        for dz in R.DESIGNS:
            s = LW.Seq("LW-01", f"star_probe:star_{k}", dz, base, also=["LW-02"],
                       script=LW.PROBE + "star_probe.py",
                       note=f"R1 at {r_xy} rot{rr}, C1 at {c_xy}")
            r1 = s.call("connect_pins", C1K, "connect U1:PA1->C1:1")
            r2 = s.call("connect_pins", C2K, "connect R1:1->U1:PA1")
            if r2["status"] == "REFUSED":
                s.call("wire_pins_to_net", WK, "wire ADC [R1:1, U1:PA1]", attempt="fallback")
            seqs[dz] = s
            sts[dz] = (r1["status"], r2["status"])
        PROBE_DONE[pkey(r_xy, rr, c_xy)] = (k, sts)
        LW.close_all(seqs)

# ------------------------------------------------------------------ v_lw01_star.py
if "verify" in which:
    def pins_ok(p, refs):
        pts = {}
        for ref in refs:
            for num, lst in R.M2.pin_positions(str(p), ref).items():
                for x, y, _ in lst:
                    pts.setdefault((round(x, 4), round(y, 4)), []).append((ref, num))
        return all(len(v) == 1 for v in pts.values())

    cases = [("probe_star_1", (139.7, 81.28), 90, (134.62, 101.6)),
             ("probe_star_3", (139.7, 81.28), 90, (142.24, 101.6))]
    k = 0
    for (cx, cy), (rx, ry), rr in itertools.product(
            [(132.08, 99.06), (137.16, 99.06), (144.78, 104.14)],
            [(137.16, 78.74), (142.24, 83.82), (149.86, 88.9), (154.94, 93.98)], (0, 90)):
        if abs(cx - rx) < 7.62 and abs(cy - ry) < 7.62:
            continue
        cases.append((f"grid_{k}", (rx, ry), rr, (cx, cy)))
        k += 1
    for tag, r_xy, rr, c_xy in cases:
        base = fixture(f"v01_{tag}", r_xy, rr, c_xy)
        if not pins_ok(base, ("U1", "R1", "C1")):
            print(f"-- v_lw01_star {tag}: skipped (pin ends coincide), as the original", flush=True)
            continue
        done = PROBE_DONE.get(pkey(r_xy, rr, c_xy))
        if done and all(a == "OK" and b != "NOOP" for a, b in done[1].values()):
            # Same fixture and, for both designs, the same calls as star_probe star_<k> (the v_lw01
            # policy differs only when the first connect is not OK or the second is a NOOP).
            print(f"-- v_lw01_star {tag}: same fixture and calls as star_probe star_{done[0]} for both"
                  f" designs {done[1]}; run once there (rule 4)", flush=True)
            continue
        LW.header(f"v_lw01_star {tag}: R1 {r_xy} rot{rr}, C1 {c_xy}")
        seqs = {}
        for dz in R.DESIGNS:
            s = LW.Seq("LW-01", f"v_lw01_star:{tag}", dz, base, also=["LW-02"],
                       script=LW.VERIFY + "v_lw01_star.py",
                       note=f"R1 at {r_xy} rot{rr}, C1 at {c_xy}")
            r1 = s.call("connect_pins", C1K, "connect U1:PA1-C1:1")
            if r1["status"] == "OK":
                r2 = s.call("connect_pins", C2K, "connect R1:1-U1:PA1")
                if r2["status"] != "OK":
                    s.call("wire_pins_to_net", WK, "fallback wire [R1:1,U1:PA1] ADC", attempt="fallback")
            seqs[dz] = s
        LW.close_all(seqs)
R.end()
