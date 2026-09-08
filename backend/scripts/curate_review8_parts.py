"""Curate the parts review round 8 (reviews #34-#38, 2026-08-20) asked for.

The RC-car ESC report (review #34) evaluates drivers by the 2S discharge range
and rejects the library's only brushed driver: "The older DRV8871 is less
attractive for this specific 2S application because its specified operating
range starts at 6.5 V." It recommends the DRV8874/DRV8876 family (4.5-37 V).

DRV8874 pin table transcribed from TI datasheet SLVSF66A (Section 5, "Pin
Configuration and Functions", PWP HTSSOP-16 package) — fetched and read
2026-08-20, not inferred. DRV8876 is the pin-to-pin 3.5 A / 700 mOhm variant
named by the same datasheet and is recorded as an alternative, not as its own
entry, because only the DRV8874 document was verified page-by-page.

The footprint `Package_SO:HTSSOP-16-1EP_4.4x5mm_P0.65mm_EP3.4x5mm` is already
in the verified cache and has a real GLB in the 3D manifest, so no cache or
model work is needed.

The macro-pad report (review #35) asked for a "SK6812 Mini-E or another
verified WS2812-compatible part" for per-key RGB. Curated as SK6812MINI on the
official KiCad `LED_SMD:LED_SK6812MINI_PLCC4_3.5x3.5mm_P1.75mm` land pattern
(pads transcribed from the upstream file, 0-delta) with the pin table from the
OPSCO SK6812MINI datasheet (Rev.01: 1=DOUT, 2=VSS, 3=DIN, 4=VDD) — the same
document the KiCad footprint's own descr cites. Deliberately NOT named Mini-E:
the Mini-E is the reverse-mount wing-lead variant whose footprint needs a board
cutout this pipeline does not model, and its pads are not compatible.

    python -m scripts.curate_review8_parts            # write
    python -m scripts.curate_review8_parts --dry-run  # show what would change
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import json  # noqa: E402

from app.models import LibraryPart  # noqa: E402
from app.services import library  # noqa: E402

CACHE = _BACKEND / "app" / "library" / "footprint_cache.json"

# LED_SMD:LED_SK6812MINI_PLCC4_3.5x3.5mm_P1.75mm — transcribed pad-for-pad from
# the official kicad-footprints file (four 1.6 x 0.85 rect pads at x ±1.75,
# y ±0.875; courtyard ±2.8 x ±2.0; 3.5 mm body). NOTE the cache frame is Y-UP
# (the committed WS2812B/MPU6050 entries negate the .kicad_mod's y-down
# coordinates), so pin 1 — top-left in the KiCad file at (-1.75, -0.875) —
# is stored at (-1.75, +0.875) here.
SK6812MINI_FP = {
    "name": "LED_SMD:LED_SK6812MINI_PLCC4_3.5x3.5mm_P1.75mm",
    "width": 3.5, "height": 3.5,
    "courtyard_w": 5.6, "courtyard_h": 4.0,
    "pin1_pad": "1",
    "confidence": "verified",
    "pads": [
        {"pin_number": "1", "x": -1.75, "y": 0.875, "width": 1.6, "height": 0.85,
         "shape": "rect", "pad_type": "smd", "layer": "top"},
        {"pin_number": "2", "x": -1.75, "y": -0.875, "width": 1.6, "height": 0.85,
         "shape": "rect", "pad_type": "smd", "layer": "top"},
        {"pin_number": "3", "x": 1.75, "y": -0.875, "width": 1.6, "height": 0.85,
         "shape": "rect", "pad_type": "smd", "layer": "top"},
        {"pin_number": "4", "x": 1.75, "y": 0.875, "width": 1.6, "height": 0.85,
         "shape": "rect", "pad_type": "smd", "layer": "top"},
    ],
}

DRV8874 = {
    "mpn": "DRV8874",
    "description": "4.5-37 V 6 A peak H-bridge brushed DC motor driver with integrated current sense (IPROPI), current regulation and full protection (OCP/UVLO/TSD/nFAULT); covers the full 2S LiPo discharge range",
    "category": "actuator",
    "package": "HTSSOP-16 PowerPAD",
    "footprint": "Package_SO:HTSSOP-16-1EP_4.4x5mm_P0.65mm_EP3.4x5mm",
    "symbol": "Driver_Motor:DRV8874",
    "pins": [
        {"number": "1", "name": "EN/IN1", "side": "left", "type": "input"},
        {"number": "2", "name": "PH/IN2", "side": "left", "type": "input"},
        {"number": "3", "name": "nSLEEP", "side": "left", "type": "input"},
        {"number": "4", "name": "nFAULT", "side": "left", "type": "output"},
        {"number": "5", "name": "VREF", "side": "left", "type": "input"},
        {"number": "6", "name": "IPROPI", "side": "left", "type": "output"},
        {"number": "7", "name": "IMODE", "side": "left", "type": "input"},
        {"number": "8", "name": "OUT1", "side": "right", "type": "output"},
        {"number": "9", "name": "PGND", "side": "bottom", "type": "ground"},
        {"number": "10", "name": "OUT2", "side": "right", "type": "output"},
        {"number": "11", "name": "VM", "side": "top", "type": "power_in"},
        {"number": "12", "name": "VCP", "side": "top", "type": "passive"},
        {"number": "13", "name": "CPH", "side": "top", "type": "passive"},
        {"number": "14", "name": "CPL", "side": "top", "type": "passive"},
        {"number": "15", "name": "GND", "side": "bottom", "type": "ground"},
        {"number": "16", "name": "PMODE", "side": "left", "type": "input"},
        {"number": "PAD", "name": "PAD", "side": "bottom", "type": "ground"},
    ],
    "voltageMin": 4.5,
    "voltageMax": 37.0,
    "currentTypMa": 3.0,
    "currentMaxMa": 6000.0,
    "operatingTempMinC": -40.0,
    "operatingTempMaxC": 125.0,
    "datasheetUrl": "https://www.ti.com/lit/ds/symlink/drv8874.pdf",
    "manufacturer": "Texas Instruments",
    "alternatives": ["DRV8876"],
    "interfaces": ["PWM"],
    "unitCost": 1.9,
    "firmwareNotes": "PMODE selects PH/EN, PWM or independent half-bridge control; drive EN/IN1 and PH/IN2 from GPIOs (PWM up to 100 kHz); nSLEEP high to run; read stall current on IPROPI (450 uA/A into a resistor to GND); nFAULT is open-drain and needs a pull-up.",
    "companions": [
        {"mpn": "CC0603KRX7R9BB104", "role": "decoupling", "pin": "VM",
         "quantity": 1,
         "notes": "0.1 uF ceramic from VM to GND close to the device (datasheet section 9)"},
        {"mpn": "CL31A476MQHNNNE", "role": "decoupling", "pin": "VM",
         "quantity": 1,
         "notes": "bulk capacitance at the VM supply entry (datasheet section 9.1)"},
        {"mpn": "CC0603KRX7R9BB104", "role": "charge_pump", "pin": "VCP",
         "quantity": 1,
         "notes": "datasheet asks 100 nF 16 V X5R/X7R between VCP and VM"},
        {"mpn": "RC0603FR-0710KL", "role": "pullup", "pin": "nFAULT",
         "quantity": 1,
         "notes": "nFAULT is open-drain; pull up to the logic rail"},
    ],
    "typicalApplication": (
        "Brushed DC motor driver for robots and RC vehicles: VM (pin 11) to the "
        "4.5-37 V motor supply (a 2S LiPo's whole 6.0-8.4 V range is in spec) "
        "with 0.1 uF ceramic + bulk capacitance to PGND/GND; 22 nF VM-rated "
        "X5R/X7R between CPH (13) and CPL (14) and 100 nF 16 V between VCP (12) "
        "and VM; GND (15), PGND (9) and the thermal PAD all to system ground. "
        "Drive EN/IN1 (1) and PH/IN2 (2) from MCU GPIOs, hold nSLEEP (3) high, "
        "pull nFAULT (4) up to the logic rail, set the current-regulation limit "
        "with a voltage on VREF (5), read load current on IPROPI (6) through a "
        "resistor to GND, and strap IMODE (7) / PMODE (16) per the control mode "
        "table. DRV8876 is the pin-to-pin 3.5 A variant."
    ),
}

SK6812MINI = {
    "mpn": "SK6812MINI",
    "description": "Addressable RGB LED, 3.5 mm PLCC4 (the compact sibling of the 5050 WS2812B/SK6812), integrated controller, single-wire 800 kHz data, 3.5-5.5 V — per-key keyboard/macro-pad RGB and dense pixel strips",
    "category": "display",
    "package": "PLCC4 3.5mm",
    "footprint": "LED_SMD:LED_SK6812MINI_PLCC4_3.5x3.5mm_P1.75mm",
    "symbol": "LED:SK6812MINI",
    "pins": [
        {"number": "1", "name": "DOUT", "side": "right", "type": "output"},
        {"number": "2", "name": "VSS", "side": "bottom", "type": "ground"},
        {"number": "3", "name": "DIN", "side": "left", "type": "input"},
        {"number": "4", "name": "VDD", "side": "top", "type": "power_in"},
    ],
    "voltageMin": 3.5,
    "voltageMax": 5.5,
    "currentTypMa": 1.0,
    "currentMaxMa": 40.0,
    "operatingTempMinC": -40.0,
    "operatingTempMaxC": 85.0,
    "datasheetUrl": "https://cdn-shop.adafruit.com/product-files/2686/SK6812MINI_REV.01-1-2.pdf",
    "manufacturer": "OPSCO",
    "alternatives": ["WS2812B"],
    "interfaces": ["single-wire"],
    "unitCost": 0.12,
    "firmwareNotes": "WS2812-protocol single-wire NRZ at 800 kbps; chain DOUT of one pixel to DIN of the next; drive DIN from a GPIO (use the neopixel/rmt driver); at 5 V VDD the DIN high threshold is 3.4 V, so a 3.3 V MCU may need a level shifter or a first pixel at lower VDD.",
    "companions": [
        {"mpn": "CC0603KRX7R9BB104", "role": "decoupling", "pin": "VDD",
         "quantity": 1,
         "notes": "100 nF at every pixel's VDD, the standard addressable-LED practice"},
        {"mpn": "RC0603FR-07330RL", "role": "series", "pin": "DIN",
         "quantity": 1,
         "notes": "series resistor on the first pixel's data line, close to the driver"},
    ],
    "typicalApplication": (
        "One pixel per key on a keyboard or macro pad, or a dense strip: VDD "
        "(pin 4) on the 5 V rail with 100 nF per pixel, VSS (pin 2) to GND, the "
        "controller GPIO through a ~330 R series resistor into the FIRST "
        "pixel's DIN (pin 3), then DOUT (pin 1) to the next pixel's DIN down "
        "the chain. Budget worst-case white at roughly 40 mA per pixel when "
        "sizing the 5 V rail; twelve pixels is ~0.5 A at full brightness. At "
        "VDD = 5 V the input high threshold is 3.4 V (datasheet), so shift a "
        "3.3 V MCU's data line or run the first pixel at a lower VDD."
    ),
}

# Synqit:Synqit_ProMicro_RP2040_Module — Pro Micro form factor (SparkFun
# DEV-17717): two 1x12 rows at the 15.24 mm Pro Micro row spacing, 2.54 mm
# pitch, board 17.78 x 33.02 mm (1.3" x 0.7", hookup guide), USB-C end past the
# pin-1/13 edge. Pin map transcribed from SparkFun's own Eagle schematic
# (J3/J4 castellated connectors, Hardware/SparkFun_ProMicro-RP2040.sch):
# left row (USB end down) TX0/GP0, RX0/GP1, GND, GND, GP2..GP7, TX1/GP8,
# RX1/GP9; right row (USB end down) RAW, GND, RST, 3V3, A3/GP29, A2/GP28,
# A1/GP27, A0/GP26, SCK/GP22, CIPO/GP20, COPI/GP23, CS/GP21.
PROMICRO_FP = {
    "name": "Synqit:Synqit_ProMicro_RP2040_Module",
    "width": 17.78, "height": 33.02,
    "courtyard_w": 18.28, "courtyard_h": 33.52,
    "pin1_pad": "1",
    "confidence": "verified",
    "pads": (
        [{"pin_number": str(i + 1), "x": 0.0, "y": -i * 2.54, "width": 1.7,
          "height": 1.7, "shape": "circle" if i else "rect", "pad_type": "tht",
          "drill": 1.0, "layer": "top"} for i in range(12)]
        + [{"pin_number": str(i + 13), "x": 15.24, "y": -i * 2.54, "width": 1.7,
            "height": 1.7, "shape": "circle", "pad_type": "tht",
            "drill": 1.0, "layer": "top"} for i in range(12)]
    ),
}

_PROMICRO_LEFT = ["GP0_TX0", "GP1_RX0", "GND", "GND", "GP2", "GP3", "GP4",
                  "GP5", "GP6", "GP7", "GP8_TX1", "GP9_RX1"]
_PROMICRO_RIGHT = ["RAW", "GND", "RST", "3V3", "GP29_A3", "GP28_A2", "GP27_A1",
                   "GP26_A0", "GP22_SCK", "GP20_MISO", "GP23_MOSI", "GP21_CS"]


def _promicro_pin(number: int, name: str, side: str) -> dict:
    if name == "GND":
        typ = "ground"
    elif name == "RAW":
        typ = "power_in"
    elif name == "3V3":
        typ = "power_out"
    elif name == "RST":
        typ = "input"
    else:
        typ = "bidirectional"
    return {"number": str(number), "name": name, "side": side, "type": typ}


PRO_MICRO_RP2040 = {
    "mpn": "Pro_Micro_RP2040",
    "description": "SparkFun Pro Micro RP2040 (DEV-17717): RP2040 dev module in the classic Pro Micro 2x12 footprint — USB-C, 16 MB flash, Qwiic, WS2812 on GP25; the standard keyboard/macro-pad controller form factor",
    "category": "mcu",
    "package": "MODULE",
    "footprint": "Synqit:Synqit_ProMicro_RP2040_Module",
    "symbol": "Synqit:Pro_Micro_RP2040",
    "pins": ([_promicro_pin(i + 1, n, "left") for i, n in enumerate(_PROMICRO_LEFT)]
             + [_promicro_pin(i + 13, n, "right") for i, n in enumerate(_PROMICRO_RIGHT)]),
    "voltageMin": 4.2,
    "voltageMax": 5.3,
    "currentTypMa": 25.0,
    "currentMaxMa": 600.0,
    "operatingTempMinC": -20.0,
    "operatingTempMaxC": 70.0,
    "datasheetUrl": "https://learn.sparkfun.com/tutorials/pro-micro-rp2040-hookup-guide",
    "manufacturer": "SparkFun",
    "alternatives": ["DEV-17717"],
    "interfaces": ["USB", "I2C", "SPI", "UART", "PWM"],
    "unitCost": 10.95,
    "onboardPower": True,
    # Seated on its headers: 8.5 mm standoff + 0.8 mm PCB + ~3.2 mm USB-C shell.
    # Over-estimating keeps the enclosure honest; under-estimating closes a lid
    # onto the module (the L298N 3.2 mm lesson).
    "bodyHeightMm": 12.5,
    "firmwareNotes": "RP2040 (Pico SDK / MicroPython / Arduino-Pico core); UF2 bootloader via the boot button; UART0 on GP0/GP1, UART1 on GP8/GP9, hardware SPI on GP20/22/23 (CS GP21), Qwiic I2C on GP16 (SDA)/GP17 (SCL), ADC on GP26-GP29, on-board WS2812 on GP25.",
    "typicalApplication": (
        "Drop-in controller for keyboards, macro pads and compact robots in the "
        "Pro Micro footprint: power it through its own USB-C, or feed RAW "
        "(pin 13) with up to 5.3 V — the on-board regulator makes 3.3 V, and "
        "3V3 (pin 16) can source downstream logic. All GPIO is 3.3 V. Matrix "
        "rows/columns on GP2-GP9, addressable-LED data on any GP through the "
        "WS2812 protocol, analog on GP26-GP29. RST (pin 15) is active-low with "
        "an on-board 10 k pull-up."
    ),
}

# Synqit:Synqit_MX_Switch_PCB — Cherry MX keyswitch, PCB mount with fixation
# pins. Every coordinate transcribed from the official Cherry MX Series
# datasheet's "Circuit Board Layouts" drilling plan (grid = 0.05 in), read as
# vector geometry from the PDF itself on 2026-08-21 and cross-checked against
# the grid scale (1 cell = 1.27 mm):
#   center fixing hole  Ø4.0 (0.157")  at (0, 0)          — NPTH
#   fixation pins       Ø1.7 (0.067")  at (±5.08, 0)      — NPTH
#   contact terminals   Ø1.5 (0.059")  at (-3.81, +2.54) and (+2.54, +5.08)
# Body 14 x 14 mm (0.551" square, the standard plate cutout). The cache holds
# the two ELECTRICAL pads only (the schema has no NPTH type); the authored
# .kicad_mod carries all three NPTH holes so the fab drill file is right.
MX_SWITCH_FP = {
    "name": "Synqit:Synqit_MX_Switch_PCB",
    "width": 14.0, "height": 14.0,
    "courtyard_w": 15.0, "courtyard_h": 15.0,
    "pin1_pad": "1",
    "confidence": "verified",
    "pads": [
        {"pin_number": "1", "x": -3.81, "y": 2.54, "width": 2.2, "height": 2.2,
         "shape": "circle", "pad_type": "tht", "drill": 1.5, "layer": "top"},
        {"pin_number": "2", "x": 2.54, "y": 5.08, "width": 2.2, "height": 2.2,
         "shape": "circle", "pad_type": "tht", "drill": 1.5, "layer": "top"},
    ],
}

MX_SWITCH = {
    "mpn": "MX1A-E1NW",
    "description": "Cherry MX mechanical keyswitch, click tactile, PCB mount with fixation pins — the standard MX-compatible key switch for keyboards and macro pads (14 mm body, 19.05 mm typical key spacing)",
    "category": "switch",
    "package": "MX",
    "footprint": "Synqit:Synqit_MX_Switch_PCB",
    "symbol": "Switch:SW_Push",
    "pins": [
        {"number": "1", "name": "1", "side": "left", "type": "passive"},
        {"number": "2", "name": "2", "side": "right", "type": "passive"},
    ],
    "voltageMax": 12.0,
    "currentMaxMa": 10.0,
    "operatingTempMinC": -10.0,
    "operatingTempMaxC": 70.0,
    "datasheetUrl": "https://cdn.sparkfun.com/datasheets/Components/Switches/MX%20Series.pdf",
    "manufacturer": "Cherry",
    "alternatives": ["MX1A-11NW", "MX1A-C1NW", "MX1A-21NW"],
    "unitCost": 0.9,
    "bodyHeightMm": 11.6,
    "firmwareNotes": "Scan in a diode-per-key matrix: rows as outputs, columns as inputs with pull-ups; one 1N4148-class diode in series with each switch prevents ghosting.",
    "typicalApplication": (
        "One switch per key on a keyboard or macro pad at 19.05 mm spacing, "
        "wired in a rows x columns matrix with one series diode per key "
        "(anode to the switch, cathode to the row) so multiple simultaneous "
        "presses cannot ghost. The two terminals are interchangeable "
        "contacts. Ratings are dry-circuit: 12 V / 10 mA max — never switch "
        "power with it. Variants: MX1A-E1NW click (this part), MX1A-11NW "
        "linear, MX1A-C1NW soft tactile, all pin-compatible."
    ),
}

TPS62140 = {
    "mpn": "TPS62140",
    "description": "3-17 V input, 2 A synchronous buck (DCS-Control), 0.9-6 V adjustable output, 2.5 MHz, VQFN-16 3x3 — the compact BEC/point-of-load class review #34 recommends for receiver rails",
    "category": "regulator",
    "package": "VQFN-16 3x3",
    "footprint": "Package_DFN_QFN:VQFN-16-1EP_3x3mm_P0.5mm_EP1.45x1.45mm",
    "symbol": "Regulator_Switching:TPS62140RGT",
    "pins": [
        {"number": "1", "name": "SW", "side": "right", "type": "output"},
        {"number": "2", "name": "SW", "side": "right", "type": "output"},
        {"number": "3", "name": "SW", "side": "right", "type": "output"},
        {"number": "4", "name": "PG", "side": "right", "type": "output"},
        {"number": "5", "name": "FB", "side": "bottom", "type": "input"},
        {"number": "6", "name": "AGND", "side": "bottom", "type": "ground"},
        {"number": "7", "name": "FSW", "side": "bottom", "type": "input"},
        {"number": "8", "name": "DEF", "side": "bottom", "type": "input"},
        {"number": "9", "name": "SS/TR", "side": "left", "type": "input"},
        {"number": "10", "name": "AVIN", "side": "left", "type": "power_in"},
        {"number": "11", "name": "PVIN", "side": "left", "type": "power_in"},
        {"number": "12", "name": "PVIN", "side": "left", "type": "power_in"},
        {"number": "13", "name": "EN", "side": "left", "type": "input"},
        {"number": "14", "name": "VOS", "side": "top", "type": "input"},
        {"number": "15", "name": "PGND", "side": "bottom", "type": "ground"},
        {"number": "16", "name": "PGND", "side": "bottom", "type": "ground"},
        {"number": "PAD", "name": "PAD", "side": "bottom", "type": "ground"},
    ],
    "voltageMin": 3.0,
    "voltageMax": 17.0,
    "currentTypMa": 0.017,
    "currentMaxMa": 2000.0,
    "operatingTempMinC": -40.0,
    "operatingTempMaxC": 125.0,
    "datasheetUrl": "https://www.ti.com/lit/ds/symlink/tps62140.pdf",
    "manufacturer": "Texas Instruments",
    "alternatives": ["TPS62140A", "TPS62142", "TPS62143"],
    "interfaces": [],
    "unitCost": 1.6,
    "firmwareNotes": "PG is open-drain (pull up to the logic rail) and reads high once VOUT is in regulation; EN high enables; FSW low = 2.5 MHz, high = 1.25 MHz; DEF low = nominal output.",
    "typicalApplication": (
        "2 A point-of-load buck from a 3-17 V input (2S LiPo, 12 V rail): AVIN "
        "(10) and PVIN (11, 12) to the input with a 10 uF ceramic to PGND, a "
        "2.2 uH (~3 A rated) inductor from SW (1-3) to the output node, 22 uF "
        "ceramic from the output to GND, VOS (14) to the output node, and for "
        "the adjustable TPS62140 an FB divider from the output to FB (5) to "
        "AGND (R1/R2 for VOUT = 0.8 V x (1 + R1/R2)). EN (13) high to run, "
        "PG (4) needs a pull-up, SS/TR (9) takes a soft-start cap (3.3 nF "
        "typical), AGND/PGND and the thermal pad all on the ground plane. "
        "TPS62142 (3.3 V) and TPS62143 (5.0 V) are the fixed-output variants "
        "— tie their FB to AGND."
    ),
    "companions": [
        {"mpn": "CL10A106MQ8NNNC", "role": "decoupling", "pin": "PVIN",
         "quantity": 1, "notes": "10 uF ceramic input capacitor at PVIN/AVIN"},
        {"mpn": "CL31A226KAHNNNE", "role": "output_cap", "pin": "SW",
         "quantity": 1, "notes": "22 uF ceramic output capacitor after the inductor"},
        {"mpn": "RC0603FR-07100KL", "role": "pullup", "pin": "PG",
         "quantity": 1, "notes": "PG is open-drain — 100 k pull-up to the logic rail"},
    ],
}

PARTS = [DRV8874, SK6812MINI, PRO_MICRO_RP2040, MX_SWITCH, TPS62140]

# The authored .kicad_mod for the module (fab export loads footprints by name,
# so the file must exist in Synqit.pretty, not only in the preview cache).
PRETTY = _BACKEND / "app" / "library" / "footprints" / "Synqit.pretty"


def _promicro_kicad_mod() -> str:
    lines = [
        '(footprint "Synqit_ProMicro_RP2040_Module"',
        "  (version 20240108)",
        '  (generator "synqit")',
        '  (layer "F.Cu")',
        '  (descr "SparkFun Pro Micro RP2040 (DEV-17717) as a plug-in module: 2x12 header rows at the 15.24 mm Pro Micro spacing, real board outline 17.78 x 33.02 mm (1.3 x 0.7 in per the SparkFun hookup guide), USB-C end past the pin-1/13 edge. Pin map from SparkFun_ProMicro-RP2040.sch J3/J4. (authored by Synqit)")',
        '  (tags "module mcu rp2040 pro-micro sparkfun dev-17717")',
        "  (attr through_hole)",
        '  (property "Reference" "REF**" (at 7.62 -5.00) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))',
        '  (property "Value" "Synqit_ProMicro_RP2040_Module" (at 7.62 30.50) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))',
    ]
    for i in range(12):
        shape = "rect" if i == 0 else "circle"
        lines.append(f'  (pad "{i + 1}" thru_hole {shape} (at 0.00 {i * 2.54:.2f}) (size 1.7 1.7) (drill 1.0) (layers "*.Cu" "*.Mask"))')
    for i in range(12):
        lines.append(f'  (pad "{i + 13}" thru_hole circle (at 15.24 {i * 2.54:.2f}) (size 1.7 1.7) (drill 1.0) (layers "*.Cu" "*.Mask"))')
    lines += [
        "  (fp_rect (start -1.27 -3.81) (end 16.51 29.21) (stroke (width 0.1) (type default)) (fill none) (layer \"F.Fab\"))",
        "  (fp_rect (start -1.52 -4.06) (end 16.76 29.46) (stroke (width 0.05) (type default)) (fill none) (layer \"F.CrtYd\"))",
        "  (fp_circle (center -1.9 0.0) (end -1.8 0.0) (stroke (width 0.2) (type default)) (fill solid) (layer \"F.SilkS\"))",
        ")",
    ]
    return "\n".join(lines) + "\n"


def _mx_switch_kicad_mod() -> str:
    """Cherry MX PCB-mount keyswitch. KiCad frame is y-DOWN, so the datasheet's
    y-up contact positions (-3.81, +2.54) / (+2.54, +5.08) become negative y
    here. NPTH holes carry no copper (pad type np_thru_hole)."""
    return "\n".join([
        '(footprint "Synqit_MX_Switch_PCB"',
        "  (version 20240108)",
        '  (generator "synqit")',
        '  (layer "F.Cu")',
        '  (descr "Cherry MX keyswitch, PCB mount with fixation pins. Drill plan transcribed from the official Cherry MX Series datasheet Circuit Board Layouts (grid 0.05 in): center 4.0 mm NPTH at origin, fixation pins 1.7 mm NPTH at +/-5.08, contacts 1.5 mm at (-3.81,-2.54) and (2.54,-5.08) in the y-down footprint frame; 14 x 14 mm body, 11.6 mm tall. (authored by Synqit)")',
        '  (tags "switch keyswitch cherry mx keyboard macropad")',
        "  (attr through_hole)",
        '  (property "Reference" "REF**" (at 0.00 -8.20) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))',
        '  (property "Value" "Synqit_MX_Switch_PCB" (at 0.00 8.20) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))',
        '  (pad "1" thru_hole circle (at -3.81 -2.54) (size 2.2 2.2) (drill 1.5) (layers "*.Cu" "*.Mask"))',
        '  (pad "2" thru_hole circle (at 2.54 -5.08) (size 2.2 2.2) (drill 1.5) (layers "*.Cu" "*.Mask"))',
        '  (pad "" np_thru_hole circle (at 0.00 0.00) (size 4.0 4.0) (drill 4.0) (layers "*.Cu" "*.Mask"))',
        '  (pad "" np_thru_hole circle (at -5.08 0.00) (size 1.7 1.7) (drill 1.7) (layers "*.Cu" "*.Mask"))',
        '  (pad "" np_thru_hole circle (at 5.08 0.00) (size 1.7 1.7) (drill 1.7) (layers "*.Cu" "*.Mask"))',
        '  (fp_rect (start -7.00 -7.00) (end 7.00 7.00) (stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
        '  (fp_rect (start -7.50 -7.50) (end 7.50 7.50) (stroke (width 0.05) (type default)) (fill none) (layer "F.CrtYd"))',
        '  (fp_circle (center -6.2 -6.2) (end -6.1 -6.2) (stroke (width 0.2) (type default)) (fill solid) (layer "F.SilkS"))',
        ")",
    ]) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    for mod_name, content in (
        ("Synqit_ProMicro_RP2040_Module.kicad_mod", _promicro_kicad_mod()),
        ("Synqit_MX_Switch_PCB.kicad_mod", _mx_switch_kicad_mod()),
    ):
        mod_path = PRETTY / mod_name
        if not mod_path.exists() or mod_path.read_text(encoding="utf-8") != content:
            print(f"  [FOOTPRINT] {mod_path.name}")
            if not a.dry_run:
                mod_path.write_text(content, encoding="utf-8", newline="\n")

    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    changed = False
    for entry in (SK6812MINI_FP, PROMICRO_FP, MX_SWITCH_FP):
        if cache.get(entry["name"]) != entry:
            print(f"  [CACHE] {entry['name']}")
            cache[entry["name"]] = dict(entry)
            changed = True
    if changed and not a.dry_run:
        CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")

    existing = {p.mpn for p in library.all_parts()}
    for raw in PARTS:
        part = LibraryPart.model_validate(raw)
        state = "UPDATE" if part.mpn in existing else "NEW"
        print(f"  [{state}] {part.mpn}  ({part.package} on {part.footprint})")
        if not a.dry_run:
            library.add_user_part(part)
    if a.dry_run:
        print("dry run - nothing written")
    else:
        print(f"library now {len(library.all_parts())} parts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
