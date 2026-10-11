"""LLM-style schematic plans, written the way skills/schematic-plan and skills/schematic-design
prescribe (repo main 54160c1):
  - placement: IC centred in its stage, decoupling caps 25.4 mm from their IC, passives in rows
    12.7 mm apart (horizontal) or columns 10.16 mm apart (vertical), the skill's minimum spacing;
  - wiring table: power nets first, one wire_pins_to_net call per net with every pin of that net,
    connect_pins for two pins within ~25 mm, pins named the way the skill's example names them
    (IC and diode pins by name, e.g. 'U1:VCC', 'D1:K'; passives by number);
  - connect_pins fallback (schematic-design 'Error Handling'): wire_pins_to_net for the pair,
    named after the plan's net.
Parts are non-derived stock symbols (place_component cannot place derived ones; digest S15).
The ATtiny25V-10P and STM32F103C_8-B_Tx used here are the parents of the derived ATtiny85-20P
and STM32F103C8Tx an LLM would usually pick.

Each call: ("wire", N, [(ref, pin), ...])
           ("connect", NAME, (ref1, pin1), (ref2, pin2))   NAME = plan's net name (fallback label)
           ("decap", ref, lib_id, value, x, y, power_net, ground_net, rotation)
           ("power", lib_id, ref, x, y)                     add_power_symbol (repo tool, setup)
"""

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
        # four 100n decaps 25.4 mm left of the MCU, 10.16 mm apart vertically (skill minimum)
        ("Device:C", "C3", "100n", 114.3, 76.2, 0, ""),
        ("Device:C", "C4", "100n", 114.3, 86.36, 0, ""),
        ("Device:C", "C5", "100n", 114.3, 96.52, 0, ""),
        ("Device:C", "C6", "100n", 114.3, 106.68, 0, ""),
        ("MCU_ST_STM32F1:STM32F103C_8-B_Tx", "U1", "STM32F103C8T6", 139.7, 101.6, 0, ""),
        ("Device:R", "R1", "10k", 101.6, 76.2, 0, ""),      # BOOT0 pull-down, left of decaps
        ("Device:R", "R2", "10k", 101.6, 127.0, 0, ""),     # NRST pull-up
        ("Switch:SW_Push", "SW1", "RESET", 101.6, 144.78, 0, ""),
        ("Device:R", "R3", "330", 177.8, 63.5, 90, ""),     # LED series
        ("Device:LED", "D1", "LED", 190.5, 63.5, 180, ""),  # anode faces R3 (careful plan)
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
        # series resistors horizontal, in a row 12.7 mm apart, 25.4 mm right of the MCU
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

DESIGNS = [D1, D2, D3]

# Variant of D1: the LLM follows schematic-design 'Power and Ground' and places power symbols
# with add_power_symbol before wiring (each also drops a PWR_FLAG at the same point), then wires
# the rails by the same names. Exercises the auto PWR_FLAG path after add_power_symbol (RX-13).
D1PS = dict(D1, name="tiny_bme_ps",
            desc="D1 with power:+3V3 and power:GND placed first by add_power_symbol",
            setup=[("power", "power:+3V3", "#PWR01", 63.5, 76.2),
                   ("power", "power:GND", "#PWR02", 63.5, 127.0)])
DESIGNS.append(D1PS)

# Variant of D1 with the derived ATtiny85-20P an LLM would usually pick (extends ATtiny25V-10P).
# place_component embeds it unflattened and the file stops loading in kicad-cli (digest S15).
D1DER = dict(D1, name="tiny_bme_derived",
             desc="D1 with the derived MCU_Microchip_ATtiny:ATtiny85-20P",
             parts=[("MCU_Microchip_ATtiny:ATtiny85-20P",) + p[1:] if p[1] == "U1" else p
                    for p in D1["parts"]])
DESIGNS.append(D1DER)
