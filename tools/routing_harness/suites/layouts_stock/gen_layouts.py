"""Random skill-compliant LLM layouts (schematic-plan + schematic-design + plan reviewer rules).

Per layout: one non-derived MCU, its decoupling caps, two I2C pull-ups, an LED with series
resistor, a 4-pin header (+3V3, GND, TX, RX) and, half the time, a BME280 with its decap.
Knobs sampled per layout (logged with the results):
  decap_arr   col-left | col-right | row-top | row-bottom   (caps next to the IC body)
  decap_gap   5.08 .. 12.7 mm from the IC body edge (reviewer: 'IC-to-decap <= 25.4 mm')
  decap_pitch 10.16 | 12.7 | 15.24 mm (skill minimum 10.16 vertical / 12.7 horizontal)
  cap_rot     0 | 90                        r_rot (pull-ups, LED resistor) 0 | 90
  led_rot     0 | 180  (0: cathode faces the resistor, 180: anode does)
  conn_rot    0 | 180  (header pins face left | right)
  naming      names (skill example style: 'U1:VDD', 'D1:K') | numbers (every pad listed)
Placement is validated like the plan reviewer: page bounds with margins, body bounding boxes
(lib graphics + pins, from the stock library) must not overlap, minimum spacings.
Wiring follows the skill: power nets first, one wire_pins_to_net per net, connect_pins for a
pair within 25.4 mm, wire_pins_to_net otherwise.
"""

import random
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import mine_impl as M  # noqa: E402
from mcp_server_kicad import _cst  # noqa: E402

SYM = hpaths.KICAD_SYMBOLS
G = 1.27

ICS = {
    "attiny25": dict(lib="MCU_Microchip_ATtiny:ATtiny25V-10P", val="ATtiny25",
                     rails={"+3V3": [("VCC", ["8"])]}, gnd=[("GND", ["4"])],
                     sda="AREF/PB0", scl="PB2", led="PB1", tx="XTAL1/PB3", rx="XTAL2/PB4"),
    "atmega48": dict(lib="MCU_Microchip_ATmega:ATmega48PV-10P", val="ATmega48",
                     rails={"+3V3": [("VCC", ["7"]), ("AVCC", ["20"])]},
                     gnd=[("GND", ["8", "22"])],
                     sda="PC4", scl="PC5", led="PB5", tx="PD1", rx="PD0"),
    "stm32g030": dict(lib="MCU_ST_STM32G0:STM32G030F6Px", val="STM32G030",
                      rails={"+3V3": [("VDD", ["4"])]}, gnd=[("VSS", ["5"])],
                      sda="PB7/PB8", scl="PB9/PC14", led="PA5", tx="PA2", rx="PA3"),
    "stm32f103": dict(lib="MCU_ST_STM32F1:STM32F103C_8-B_Tx", val="STM32F103",
                      rails={"+3V3": [("VBAT", ["1"]), ("VDD", ["24", "36", "48"]),
                                      ("VDDA", ["9"])]},
                      gnd=[("VSS", ["23", "35", "47"]), ("VSSA", ["8"])],
                      sda="PB7", scl="PB6", led="PC13", tx="PA9", rx="PA10"),
    "rp2040": dict(lib="MCU_RaspberryPi:RP2040", val="RP2040",
                   rails={"+3V3": [("IOVDD", ["1", "10", "22", "33", "42", "49"]),
                                   ("USB_VDD", ["48"]), ("ADC_AVDD", ["43"]),
                                   ("VREG_VIN", ["44"])],
                          "+1V1": [("DVDD", ["23", "50"]), ("VREG_VOUT", ["45"])]},
                   gnd=[("GND", ["57"])],
                   sda="GPIO4", scl="GPIO5", led="GPIO25", tx="GPIO0", rx="GPIO1"),
    "esp32c3": dict(lib="MCU_Espressif:ESP32-C3", val="ESP32-C3",
                    rails={"+3V3": [("VDD3P3", ["2", "3"]), ("VDD3P3_RTC", ["11"]),
                                    ("VDD3P3_CPU", ["17"]), ("VDDA", ["31", "32"])]},
                    gnd=[("GND", ["33"])],
                    sda="GPIO18", scl="GPIO19", led="GPIO10", tx="U0TXD", rx="U0RXD"),
}

_lib_cache = {}


def lib_symbol(lib_id):
    lib, name = lib_id.split(":")
    if lib not in _lib_cache:
        root = _cst.parse((SYM / f"{lib}.kicad_sym").read_bytes()).lists[0]
        _lib_cache[lib] = {s.atoms[1].text: s for s in root.find_all("symbol")}
    return _lib_cache[lib][name]


def extent_points(lib_id):
    """Lib-coordinate points (y up) of pins (both ends) and body graphics, unit 1 style 1."""
    s = lib_symbol(lib_id)
    pts = []
    for sub in s.find_all("symbol"):
        ids = sub.atoms[1].text.rsplit("_", 2)
        if len(ids) == 3 and (ids[1] not in ("0", "1") or ids[2] not in ("0", "1")):
            continue
        for p in sub.find_all("pin"):
            at = p.find("at")
            x, y = float(at.atoms[1].text), float(at.atoms[2].text)
            a = float(at.atoms[3].text) if len(at.atoms) > 3 else 0
            ln = float(p.find("length").atoms[1].text)
            dx, dy = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[int(a) % 360]
            pts += [(x, y), (x + dx * ln, y + dy * ln)]
        for r in sub.find_all("rectangle"):
            for k in ("start", "end"):
                n = r.find(k)
                pts.append((float(n.atoms[1].text), float(n.atoms[2].text)))
        for pl in sub.find_all("polyline"):
            for xy in pl.find("pts").find_all("xy"):
                pts.append((float(xy.atoms[1].text), float(xy.atoms[2].text)))
        for c in sub.find_all("circle"):
            cx, cy = (float(v.text) for v in c.find("center").atoms[1:3])
            rr = float(c.find("radius").atoms[1].text)
            pts += [(cx - rr, cy - rr), (cx + rr, cy + rr)]
    return pts


def bbox(lib_id, x, y, rot):
    t = M._ROT[rot]
    xs, ys = [], []
    for lx, ly in extent_points(lib_id):
        dx, dy = M._apply(t, lx, -ly)
        xs.append(x + dx)
        ys.append(y + dy)
    return min(xs), min(ys), max(xs), max(ys)


def pins_at(lib_id, x, y, rot):
    """{number: (x, y)} KiCad-true pin ends (rotate then mirror; no mirror here)."""
    s = lib_symbol(lib_id)
    t = M._ROT[rot]
    out = {}
    for sub in s.find_all("symbol"):
        ids = sub.atoms[1].text.rsplit("_", 2)
        if len(ids) == 3 and (ids[1] not in ("0", "1") or ids[2] not in ("0", "1")):
            continue
        for p in sub.find_all("pin"):
            at = p.find("at")
            dx, dy = M._apply(t, float(at.atoms[1].text), -float(at.atoms[2].text))
            out[p.find("number").atoms[1].text] = (round(x + dx, 4), round(y + dy, 4))
    return out


def pin_num(lib_id, name):
    s = lib_symbol(lib_id)
    for sub in s.find_all("symbol"):
        for p in sub.find_all("pin"):
            if p.find("name").atoms[1].text == name:
                return p.find("number").atoms[1].text
    raise KeyError(name)


def snap(v):
    return round(round(v / G) * G, 4)


def overlaps(a, b, margin=1.27):
    return not (a[2] + margin <= b[0] or b[2] + margin <= a[0] or a[3] + margin <= b[1]
                or b[3] + margin <= a[1])


def make_layout(ic_key, rng, idx, with_sensor=None):
    ic = ICS[ic_key]
    page_w, page_h = 297, 210
    knobs = dict(ic=ic_key,
                 decap_arr=rng.choice(["col-left", "col-right", "row-top", "row-bottom"]),
                 decap_gap=rng.choice([5.08, 7.62, 10.16, 12.7]),
                 decap_pitch=rng.choice([10.16, 12.7, 15.24]),
                 cap_rot=rng.choice([0, 0, 90]), r_rot=rng.choice([0, 90]),
                 led_rot=rng.choice([0, 180]), conn_rot=rng.choice([0, 180]),
                 naming=rng.choice(["names", "names", "numbers"]),
                 sensor=rng.random() < 0.5 if with_sensor is None else with_sensor)
    if ic_key == "rp2040":
        page_w, page_h = 420, 297
    cx, cy = snap(page_w * 0.42), snap(page_h * 0.45)
    parts = [(ic["lib"], "U1", ic["val"], cx, cy, 0, "")]
    ibb = bbox(ic["lib"], cx, cy, 0)
    ipins = pins_at(ic["lib"], cx, cy, 0)
    # decaps: one per rail pin group (distinct positions), at most 4
    groups = []
    for rail, lst in ic["rails"].items():
        for name, nums in lst:
            seen = set()
            for n in nums:
                if ipins[n] not in seen:
                    seen.add(ipins[n])
                    groups.append((rail, name, n))
    ncap = min(4, len(groups))
    caps = []
    gap, pitch, crot = knobs["decap_gap"], knobs["decap_pitch"], knobs["cap_rot"]
    cbb = bbox("Device:C", 0, 0, crot)
    cw, chh = cbb[2] - cbb[0], cbb[3] - cbb[1]
    for k in range(ncap):
        arr = knobs["decap_arr"]
        if arr == "col-left":
            x, y = ibb[0] - gap - cw / 2, ibb[1] + chh / 2 + k * pitch
        elif arr == "col-right":
            x, y = ibb[2] + gap + cw / 2, ibb[1] + chh / 2 + k * pitch
        elif arr == "row-top":
            x, y = (ibb[0] + ibb[2]) / 2 + (k - (ncap - 1) / 2) * pitch, ibb[1] - gap - chh / 2
        else:
            x, y = (ibb[0] + ibb[2]) / 2 + (k - (ncap - 1) / 2) * pitch, ibb[3] + gap + chh / 2
        caps.append((f"C{k + 1}", snap(x), snap(y)))
    for ref, x, y in caps:
        parts.append(("Device:C", ref, "100n", x, y, crot, ""))
    # I2C pull-ups near the SDA/SCL pin side, LED resistor + LED near the LED pin
    sda_xy = ipins[pin_num(ic["lib"], ic["sda"])]
    led_xy = ipins[pin_num(ic["lib"], ic["led"])]
    rrot = knobs["r_rot"]
    side_sda = -1 if sda_xy[0] < cx else 1
    side_led = -1 if led_xy[0] < cx else 1
    edge = ibb[0] if side_sda < 0 else ibb[2]
    off = rng.choice([7.62, 12.7, 17.78])
    for k, ref in enumerate(("R1", "R2")):
        parts.append(("Device:R", ref, "4k7", snap(edge + side_sda * (off + k * 12.7)),
                      snap(sda_xy[1] - 12.7), rrot, ""))
    edge = ibb[0] if side_led < 0 else ibb[2]
    off2 = rng.choice([10.16, 15.24, 20.32])
    rx_ = snap(edge + side_led * off2)
    ly = led_xy[1] + 7.62
    if side_led == side_sda:  # keep the LED group clear of the pull-up row
        ly = max(ly, sda_xy[1] + 7.62, ibb[1] + 12.7)
    parts.append(("Device:R", "R3", "330", rx_, snap(ly), rrot, ""))
    parts.append(("Device:LED", "D1", "LED", snap(rx_ + side_led * 12.7), snap(ly),
                  knobs["led_rot"], ""))
    # header on a side the pull-ups and the LED group do not use, else below the IC
    used = {side_sda, side_led}
    if 1 not in used:
        hx, hy = ibb[2] + 25.4, cy + 5.08
    elif -1 not in used:
        hx, hy = ibb[0] - 25.4, cy + 5.08
    else:
        hx, hy = cx - 25.4, ibb[3] + 15.24
    parts.append(("Connector_Generic:Conn_01x04", "J1", "HDR", snap(hx), snap(hy),
                  knobs["conn_rot"], ""))
    if knobs["sensor"]:
        sx = snap(ibb[2] + 63.5 if side_sda > 0 else ibb[0] - 63.5)
        sy = snap(sda_xy[1] - 25.4)
        parts.append(("Sensor:BME280", "U2", "BME280", sx, sy, 0, ""))
        sbb = bbox("Sensor:BME280", sx, sy, 0)
        parts.append(("Device:C", "C9", "100n", snap(sbb[0] - 7.62), sy, crot, ""))
    # reviewer checks: page bounds and body overlaps
    boxes = [(ref, bbox(lib, x, y, rot)) for lib, ref, _v, x, y, rot, _m in parts]
    for ref, b in boxes:
        if b[0] < 10 or b[1] < 10 or b[2] > page_w - 10 or b[3] > page_h - 10:
            return None, f"{ref} off page"
    for i, (ra, a) in enumerate(boxes):
        for rb, b in boxes[i + 1:]:
            if overlaps(a, b):
                return None, f"{ra}/{rb} overlap"
    # wiring plan
    names = knobs["naming"] == "names"

    def icpins(lst):
        out = []
        for name, nums in lst:
            out += [("U1", name)] if names else [("U1", n) for n in nums]
        return out

    calls = []
    cap_rail = {}
    for (ref, _x, _y), (rail, _n, _num) in zip(caps, groups):
        cap_rail[ref] = rail
    for rail, lst in ic["rails"].items():
        pins = icpins(lst) + [(ref, "1") for ref, r in cap_rail.items() if r == rail]
        if rail == "+3V3":
            pins += [("R1", "1"), ("R2", "1"), ("J1", "1")]
            if knobs["sensor"]:
                pins += ([("U2", "VDD"), ("U2", "VDDIO"), ("U2", "CSB")] if names
                         else [("U2", "8"), ("U2", "6"), ("U2", "2")]) + [("C9", "1")]
        calls.append(("wire", rail, pins))
    gpins = icpins(ic["gnd"]) + [(ref, "2") for ref in cap_rail] + [("J1", "2")]
    gpins += [("D1", "K" if names else "1")]
    if knobs["sensor"]:
        gpins += ([("U2", "GND"), ("U2", "SDO")] if names else [("U2", "1"), ("U2", "7"), ("U2", "5")])
        gpins += [("C9", "2")]
    calls.append(("wire", "GND", gpins))

    def p(name):
        return name if names else pin_num(ic["lib"], name)

    sda = [("U1", p(ic["sda"])), ("R1", "2")] + ([("U2", "SDI" if names else "3")] if knobs["sensor"] else [])
    scl = [("U1", p(ic["scl"])), ("R2", "2")] + ([("U2", "SCK" if names else "4")] if knobs["sensor"] else [])
    calls.append(("wire", "SDA", sda))
    calls.append(("wire", "SCL", scl))
    pinpos = {}
    for lib, ref, _v, x, y, rot, _m in parts:
        for num, xy in pins_at(lib, x, y, rot).items():
            pinpos[(ref, num)] = xy

    def near(a, b):
        pa = pinpos[(a[0], a[1] if a[1].isdigit() else pin_num(ic["lib"], a[1]))] if a[0] == "U1" else pinpos[a]
        pb = pinpos[b]
        return abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) <= 25.4

    led = ("U1", p(ic["led"]))
    if near(led, ("R3", "1")):
        calls.append(("connect", "LED_DRV", led, ("R3", "1")))
    else:
        calls.append(("wire", "LED_DRV", [led, ("R3", "1")]))
    calls.append(("connect", "LED_A", ("R3", "2"), ("D1", "A" if names else "2")))
    for net, pin, jp in (("UART_TX", ic["tx"], "3"), ("UART_RX", ic["rx"], "4")):
        a = ("U1", p(pin))
        if near(a, ("J1", jp)):
            calls.append(("connect", net, a, ("J1", jp)))
        else:
            calls.append(("wire", net, [a, ("J1", jp)]))
    d = {"name": f"L{idx:02d}_{ic_key}", "desc": str(knobs), "knobs": knobs, "parts": parts,
         "calls": calls, "page": (page_w, page_h)}
    return d, None


def layouts(n_per_ic, seed=20261001):
    rng = random.Random(seed)
    out, rejected = [], 0
    idx = 0
    for ic in ICS:
        made = 0
        tries = 0
        while made < n_per_ic and tries < 400:
            tries += 1
            d, why = make_layout(ic, rng, idx)
            if d is None:
                rejected += 1
                continue
            out.append(d)
            made += 1
            idx += 1
    return out, rejected


if __name__ == "__main__":
    ds, rej = layouts(int(sys.argv[1]) if len(sys.argv) > 1 else 6)
    print(f"{len(ds)} layouts, {rej} rejected by the reviewer checks")
    for d in ds:
        print(d["name"], d["knobs"], len(d["parts"]), "parts", len(d["calls"]), "calls")
