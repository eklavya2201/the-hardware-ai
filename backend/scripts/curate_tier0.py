"""Tier-0 library batch — the "boring parts every real board has" (expert
review 2026-08-18) plus the parts reviews #19-#23 explicitly needed. Every
entry is hand-curated from the vendor datasheet pinout (no LLM inference) and
resolves to a footprint that is in the committed cache. Idempotent upsert into
user_parts.json; re-run after edits.

    python -m scripts.curate_tier0
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import CompanionPart, LibraryPart, LibraryPin  # noqa: E402
from app.services import library  # noqa: E402

R0603 = "Resistor_SMD:R_0603_1608Metric"
R0805 = "Resistor_SMD:R_0805_2012Metric"
R1206 = "Resistor_SMD:R_1206_3216Metric"
R2512 = "Resistor_SMD:R_2512_6332Metric"
H1x2 = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
H1x3 = "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"
H1x4 = "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"
H1x5 = "Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical"
H1x8 = "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical"
H1x16 = "Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical"


def P(number: str, name: str, ptype: str = "passive", side: str = "left") -> LibraryPin:
    return LibraryPin(number=number, name=name, type=ptype, side=side)  # type: ignore[arg-type]


def two_pin(n1: str = "1", n2: str = "2") -> list[LibraryPin]:
    return [P("1", n1, "passive", "left"), P("2", n2, "passive", "right")]


def C(mpn: str, role: str, notes: str | None = None, pin: str | None = None, quantity: int = 1) -> CompanionPart:
    return CompanionPart(mpn=mpn, role=role, notes=notes, pin=pin, quantity=quantity)


PARTS: list[LibraryPart] = [
    # ---- passives the reviews were missing ------------------------------------
    LibraryPart(mpn="RC0603FR-070RL", description="0 Ω jumper resistor, 0603 (net tie / config link)",
                category="passive", package="0603", footprint=R0603, symbol="Device:R", pins=two_pin(),
                voltageMax=75, currentMaxMa=1000, unitCost=0.01, manufacturer="Yageo",
                datasheetUrl="https://www.yageo.com/upload/media/product/products/datasheet/rchip/PYu-RC_Group_51_RoHS_L_12.pdf",
                typicalApplication="Zero-ohm link: joins two nets that must be separable at assembly (analog/digital ground tie, option select)."),
    LibraryPart(mpn="RC0805FR-0710RL", description="10 Ω 0.125 W resistor, 0805 (gate / snubber / low-value)",
                category="passive", package="0805", footprint=R0805, symbol="Device:R", pins=two_pin(),
                voltageMax=150, powerMaxW=0.125, unitCost=0.01, manufacturer="Yageo"),
    LibraryPart(mpn="RC1206FR-071KL", description="1 kΩ 0.25 W resistor, 1206",
                category="passive", package="1206", footprint=R1206, symbol="Device:R", pins=two_pin(),
                voltageMax=200, powerMaxW=0.25, unitCost=0.01, manufacturer="Yageo"),
    LibraryPart(mpn="RC1206FR-07100RL", description="100 Ω 0.25 W resistor, 1206 (LED string / load)",
                category="passive", package="1206", footprint=R1206, symbol="Device:R", pins=two_pin(),
                voltageMax=200, powerMaxW=0.25, unitCost=0.01, manufacturer="Yageo"),
    LibraryPart(mpn="RC2512JK-071RL", description="1 Ω 1 W resistor, 2512 (power / bleeder / low-side sense)",
                category="passive", package="2512", footprint=R2512, symbol="Device:R", pins=two_pin(),
                voltageMax=200, powerMaxW=1.0, unitCost=0.03, manufacturer="Yageo"),
    LibraryPart(mpn="RC2512JK-0710KL", description="10 kΩ 1 W resistor, 2512 (bleeder / high-voltage divider top)",
                category="passive", package="2512", footprint=R2512, symbol="Device:R", pins=two_pin(),
                voltageMax=200, powerMaxW=1.0, unitCost=0.03, manufacturer="Yageo"),
    LibraryPart(mpn="WSL2512R1000FEA", description="0.1 Ω 1 % 1 W current-sense SHUNT resistor, 2512 (INA219/INA226 sense)",
                category="passive", package="2512", footprint=R2512, symbol="Device:R", pins=two_pin("RS+", "RS-"),
                powerMaxW=1.0, currentMaxMa=3160, unitCost=0.25, manufacturer="Vishay",
                datasheetUrl="https://www.vishay.com/docs/30100/wsl.pdf",
                typicalApplication="Shunt for a high-side monitor: rail -> RS+ / IN+ ... RS- / IN- -> load. 0.1 Ω gives 100 mV at 1 A "
                                   "(INA219 default range 3.2 A). Route IN+/IN- as a Kelvin pair from the pad edges."),
    LibraryPart(mpn="WSL2512R0100FEA", description="0.01 Ω 1 % 1 W current-sense SHUNT resistor, 2512 (10 A class, motor/ESC/battery)",
                category="passive", package="2512", footprint=R2512, symbol="Device:R", pins=two_pin("RS+", "RS-"),
                powerMaxW=1.0, currentMaxMa=10000, unitCost=0.30, manufacturer="Vishay",
                datasheetUrl="https://www.vishay.com/docs/30100/wsl.pdf",
                typicalApplication="Shunt for INA226/INA240 on a motor or battery rail: 10 mΩ gives 100 mV at 10 A, 1 W at 10 A."),
    LibraryPart(mpn="NCP18XH103F03RB", description="NTC thermistor 10 kΩ @25 °C, B=3380, 0603 (board / battery temperature sense)",
                category="sensor", package="0603", footprint=R0603, symbol="Device:Thermistor_NTC", pins=two_pin(),
                voltageMax=5.5, unitCost=0.05, manufacturer="Murata", interfaces=["Analog"],
                datasheetUrl="https://www.murata.com/-/media/webrenewal/support/library/catalog/products/thermistor/ntc/r44e.ashx",
                companions=[C("RC0603FR-0710KL", "pullup", "10 kΩ divider partner: rail -> 10k -> SENSE -> NTC -> GND")],
                typicalApplication="Temperature via ADC: rail -> 10 kΩ -> SENSE -> NTC -> GND, SENSE to an ADC pin; 100 nF on SENSE optional. "
                                   "Also the TEMP/TS input of a Li-ion charger (TP4056 TEMP, BQ2407x TS)."),
    LibraryPart(mpn="NTC_100K_3950_Bead", description="NTC thermistor 100 kΩ @25 °C, B=3950, glass bead on 2-pin lead (3D-printer hotend / heated bed)",
                category="sensor", package="Bead-2wire", footprint=H1x2, symbol="Device:Thermistor_NTC", pins=two_pin(),
                voltageMax=5.5, unitCost=0.30, interfaces=["Analog"], operatingTempMaxC=300,
                companions=[C("RC0603FR-074K7L", "pullup", "4.7 kΩ pull-up to 3.3 V/5 V is the Marlin/Klipper standard")],
                typicalApplication="Hotend/bed thermistor on a 2-pin header: 3.3 V -> 4.7 kΩ -> TEMP -> NTC -> GND, TEMP to an ADC pin "
                                   "with 100 nF to GND. Off-board bead; the header is what sits on the PCB."),
    LibraryPart(mpn="3296W-1-103LF", description="10 kΩ 25-turn trimmer potentiometer, 3296W (top-adjust, 3 pins inline 2.54 mm)",
                category="passive", package="3296W", footprint="Potentiometer_THT:Potentiometer_Bourns_3296W_Vertical",
                symbol="Device:R_Potentiometer_Trim",
                pins=[P("1", "CCW", "passive", "left"), P("2", "WIPER", "passive", "top"), P("3", "CW", "passive", "right")],
                powerMaxW=0.5, unitCost=0.35, manufacturer="Bourns", bodyHeightMm=10.0,
                datasheetUrl="https://www.bourns.com/docs/Product-Datasheets/3296.pdf",
                typicalApplication="Set-point / offset / contrast trim: CW to rail, CCW to GND, WIPER to the input. Never carry load current through the wiper."),
    LibraryPart(mpn="PTV09A-4020F-B103", description="10 kΩ rotary potentiometer, 9 mm PCB mount, linear (volume / setpoint knob)",
                category="passive", package="Pot-9mm", footprint=H1x3, symbol="Device:R_Potentiometer",
                pins=[P("1", "CCW", "passive", "left"), P("2", "WIPER", "passive", "top"), P("3", "CW", "passive", "right")],
                powerMaxW=0.05, unitCost=0.9, manufacturer="Bourns", bodyHeightMm=20.0,
                datasheetUrl="https://www.bourns.com/docs/Product-Datasheets/ptv09.pdf",
                typicalApplication="User knob: CW to rail, CCW to GND, WIPER to an ADC pin (add 100 nF to GND). Modelled on its 3-pin 2.54 mm footprint."),
    LibraryPart(mpn="EEU-FR1E101", description="100 µF 25 V electrolytic capacitor, radial Ø5×11 mm, P2.0 (bulk / motor rail)",
                category="passive", package="Radial-D5-P2", footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.00mm", symbol="Device:C_Polarized",
                pins=[P("1", "+", "passive", "left"), P("2", "-", "passive", "right")],
                voltageMax=25, unitCost=0.10, manufacturer="Panasonic", bodyHeightMm=11.0,
                typicalApplication="Bulk capacitance on a regulator input/output or motor rail. Polarised: pin 1 (+) to the rail, pin 2 (-) to GND."),
    LibraryPart(mpn="EEU-FR1E471", description="470 µF 25 V electrolytic capacitor, radial Ø8×11.5 mm, P3.5 (buck output / motor bulk)",
                category="passive", package="Radial-D8-P3.5", footprint="Capacitor_THT:CP_Radial_D8.0mm_P3.50mm", symbol="Device:C_Polarized",
                pins=[P("1", "+", "passive", "left"), P("2", "-", "passive", "right")],
                voltageMax=25, unitCost=0.18, manufacturer="Panasonic", bodyHeightMm=11.5),
    LibraryPart(mpn="EEU-FR1H102", description="1000 µF 50 V electrolytic capacitor, radial Ø10×20 mm, P5.0 (motor / audio / rectifier bulk)",
                category="passive", package="Radial-D10-P5", footprint="Capacitor_THT:CP_Radial_D10.0mm_P5.00mm", symbol="Device:C_Polarized",
                pins=[P("1", "+", "passive", "left"), P("2", "-", "passive", "right")],
                voltageMax=50, unitCost=0.45, manufacturer="Panasonic", bodyHeightMm=20.0),
    LibraryPart(mpn="EEU-FR1V221", description="220 µF 35 V electrolytic capacitor, radial Ø6.3×11 mm, P2.5",
                category="passive", package="Radial-D6.3-P2.5", footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm", symbol="Device:C_Polarized",
                pins=[P("1", "+", "passive", "left"), P("2", "-", "passive", "right")],
                voltageMax=35, unitCost=0.14, manufacturer="Panasonic", bodyHeightMm=11.0),
    LibraryPart(mpn="RLB0914-100KL", description="10 µH 3.5 A radial power inductor, Ø9.5 mm, 5 mm pitch (buck/boost coil, MP1584 / TPS5430 class)",
                category="passive", package="Radial-D9.5-P5", footprint="Inductor_THT:L_Radial_D9.5mm_P5.00mm_Fastron_09HCP", symbol="Device:L",
                pins=two_pin(), currentMaxMa=3500, unitCost=0.35, manufacturer="Bourns", bodyHeightMm=14.0,
                datasheetUrl="https://www.bourns.com/docs/Product-Datasheets/RLB0914.pdf",
                typicalApplication="Buck coil: SW/LX node -> L -> VOUT (output cap on the VOUT side). 10 µH suits 500 kHz-1.5 MHz bucks (MP1584EN, TPS5430); saturates at 3.5 A."),
    LibraryPart(mpn="RLB0914-330KL", description="33 µH 2.7 A radial power inductor, Ø9.5 mm, 5 mm pitch (LM2596 / 150 kHz buck coil)",
                category="passive", package="Radial-D9.5-P5", footprint="Inductor_THT:L_Radial_D9.5mm_P5.00mm_Fastron_09HCP", symbol="Device:L",
                pins=two_pin(), currentMaxMa=2700, unitCost=0.35, manufacturer="Bourns", bodyHeightMm=14.0,
                datasheetUrl="https://www.bourns.com/docs/Product-Datasheets/RLB0914.pdf",
                typicalApplication="LM2596 coil: Output(SW) -> L -> VOUT, SS34 catch diode from SW to GND (cathode on SW), 220-470 µF on VOUT. 2.7 A saturation."),
    LibraryPart(mpn="MMSZ5242BT1G", description="12 V 500 mW zener diode, SOD-123",
                category="diode", package="SOD-123", footprint="Diode_SMD:D_SOD-123", symbol="Device:D_Zener",
                pins=[P("1", "K", "passive", "left"), P("2", "A", "passive", "right")],
                voltageMax=12, powerMaxW=0.5, unitCost=0.03, manufacturer="onsemi"),
    LibraryPart(mpn="MMSZ5245BT1G", description="15 V 500 mW zener diode, SOD-123",
                category="diode", package="SOD-123", footprint="Diode_SMD:D_SOD-123", symbol="Device:D_Zener",
                pins=[P("1", "K", "passive", "left"), P("2", "A", "passive", "right")],
                voltageMax=15, powerMaxW=0.5, unitCost=0.03, manufacturer="onsemi"),
    LibraryPart(mpn="Fiducial_1mm", description="Fiducial mark, 1 mm copper dot / 2 mm mask opening (pick-and-place alignment — three per board)",
                category="mechanical", package="Fiducial", footprint="Fiducial:Fiducial_1mm_Mask2mm", symbol="Mechanical:Fiducial",
                pins=[], unitCost=0.0,
                typicalApplication="Place three, asymmetrically, near the board corners on the component side. Required by every assembly house."),

    # ---- analog / references / isolation ------------------------------------------
    LibraryPart(mpn="TL431AIDBZR", description="Programmable shunt voltage reference 2.495–36 V, 1 %, SOT-23 (feedback / precision ref)",
                category="logic", package="SOT-23", footprint="Package_TO_SOT_SMD:SOT-23", symbol="Reference_Voltage:TL431DBZ",
                pins=[P("1", "REF", "input", "left"), P("2", "A", "passive", "bottom"), P("3", "K", "passive", "top")],
                voltageMin=2.495, voltageMax=36, currentMaxMa=100, powerMaxW=0.3, unitCost=0.08, manufacturer="TI",
                datasheetUrl="https://www.ti.com/lit/ds/symlink/tl431.pdf",
                companions=[C("RC0603FR-071KL", "pullup", "cathode bias resistor from the rail (≥1 mA)")],
                typicalApplication="Shunt reference: rail -> R -> K, A to GND, REF tied to K for 2.495 V, or REF from a K-GND divider for Vk = 2.495·(1+R1/R2). "
                                   "Also the feedback element in isolated SMPS."),
    LibraryPart(mpn="REF3033AIDBZR", description="3.3 V ±0.2 % series voltage reference, 25 mA, SOT-23-3 (ADC reference)",
                category="logic", package="SOT-23", footprint="Package_TO_SOT_SMD:SOT-23", symbol="Reference_Voltage:REF3033",
                pins=[P("1", "IN", "power_in", "left"), P("2", "GND", "ground", "bottom"), P("3", "OUT", "power_out", "right")],
                voltageMin=3.4, voltageMax=5.5, currentMaxMa=25, unitCost=1.2, manufacturer="TI",
                datasheetUrl="https://www.ti.com/lit/ds/symlink/ref3033.pdf",
                companions=[C("CC0603KRX7R9BB104", "decoupling", "IN and OUT each")],
                typicalApplication="Precision ADC reference: 5 V -> IN, OUT (3.3 V) -> ADC VREF pin, 100 nF on IN and OUT."),
    LibraryPart(mpn="PC817X2NSZ1B", description="Optocoupler, single channel, 5 kV isolation, CTR 50–600 %, DIP-4",
                category="interface", package="DIP-4", footprint="Package_DIP:DIP-4_W7.62mm", symbol="Isolator:PC817",
                pins=[P("1", "A", "input", "left"), P("2", "K", "input", "left"), P("3", "E", "output", "right"), P("4", "C", "output", "right")],
                currentMaxMa=50, voltageMax=80, unitCost=0.12, manufacturer="Sharp", bodyHeightMm=4.6,
                datasheetUrl="https://global.sharp/products/device/lineup/data/pdf/datasheet/PC817XxNSZ1B_e.pdf",
                companions=[C("RC0603FR-071KL", "current_limit", "LED series resistor (~1 kΩ at 5 V / 330 Ω at 3.3 V)"),
                            C("RC0603FR-0710KL", "pullup", "collector pull-up on the isolated side")],
                typicalApplication="Isolated logic signal: GPIO -> 1 kΩ -> A, K to GND; on the far side C -> 10 kΩ pull-up to that domain's rail and to the input, E to that domain's GND. "
                                   "The two grounds must NOT be joined — that is the point."),
    LibraryPart(mpn="6N137", description="High-speed optocoupler 10 Mbit/s, open-collector output, 5 kV isolation, DIP-8",
                category="interface", package="DIP-8", footprint="Package_DIP:DIP-8_W7.62mm", symbol="Isolator:6N137",
                pins=[P("1", "NC", "passive", "left"), P("2", "A", "input", "left"), P("3", "K", "input", "left"), P("4", "NC", "passive", "left"),
                      P("5", "GND", "ground", "right"), P("6", "VO", "output", "right"), P("7", "VE", "input", "right"), P("8", "VCC", "power_in", "right")],
                voltageMin=4.5, voltageMax=5.5, currentMaxMa=50, unitCost=0.6, manufacturer="Broadcom/Lite-On", bodyHeightMm=4.6,
                datasheetUrl="https://www.broadcom.com/products/optocouplers/industrial-plastic/digital-optocouplers/10mbd/6n137",
                companions=[C("RC0603FR-07330RL", "current_limit", "LED series resistor"),
                            C("RC0603FR-0710KL", "pullup", "VO open-collector pull-up"),
                            C("CC0603KRX7R9BB104", "decoupling", "VCC-GND, within 5 mm")],
                typicalApplication="Isolated MIDI-in / DMX / fast serial: signal -> 330 Ω -> A, K return; VCC 5 V isolated side, VO open-collector with 10 kΩ pull-up to VCC, VE to VCC (enable)."),
    LibraryPart(mpn="MAX3232ESE+", description="RS-232 transceiver, 2 drivers / 2 receivers, 3–5.5 V, charge pump, SOIC-16",
                category="interface", package="SOIC-16", footprint="Package_SO:SOIC-16_3.9x9.9mm_P1.27mm", symbol="Interface_UART:MAX3232",
                pins=[P("1", "C1+", "passive", "left"), P("2", "V+", "passive", "left"), P("3", "C1-", "passive", "left"), P("4", "C2+", "passive", "left"),
                      P("5", "C2-", "passive", "left"), P("6", "V-", "passive", "left"), P("7", "T2OUT", "output", "right"), P("8", "R2IN", "input", "right"),
                      P("9", "R2OUT", "output", "right"), P("10", "T2IN", "input", "right"), P("11", "T1IN", "input", "right"), P("12", "R1OUT", "output", "right"),
                      P("13", "R1IN", "input", "right"), P("14", "T1OUT", "output", "right"), P("15", "GND", "ground", "bottom"), P("16", "VCC", "power_in", "top")],
                voltageMin=3.0, voltageMax=5.5, currentMaxMa=1, unitCost=0.9, manufacturer="Analog Devices", interfaces=["UART"],
                datasheetUrl="https://www.analog.com/media/en/technical-documentation/data-sheets/MAX3222-MAX3241.pdf",
                companions=[C("CC0603KRX7R9BB104", "filter", "5x 100 nF: C1+/C1-, C2+/C2-, V+ to GND, V- to GND, VCC decoupling", quantity=5)],
                typicalApplication="RS-232 port: MCU TX -> T1IN, T1OUT -> DB9 pin 3; DB9 pin 2 -> R1IN, R1OUT -> MCU RX. Four 100 nF charge-pump caps + VCC decoupling."),

    # ---- power / protection ---------------------------------------------------------
    LibraryPart(mpn="FS8205A", description="Dual N-channel MOSFET 20 V 6 A, TSSOP-8 — the DW01A battery-protection switch pair",
                category="transistor", package="TSSOP-8", footprint="Package_SO:TSSOP-8_4.4x3mm_P0.65mm", symbol="Transistor_FET:FS8205A",
                pins=[P("1", "S1", "passive", "left"), P("2", "G1", "input", "left"), P("3", "S2", "passive", "left"), P("4", "G2", "input", "left"),
                      P("5", "D2", "passive", "right"), P("6", "D2", "passive", "right"), P("7", "D1", "passive", "right"), P("8", "D1", "passive", "right")],
                voltageMax=20, currentMaxMa=6000, unitCost=0.08, manufacturer="Fortune",
                datasheetUrl="https://www.ic-fortune.com/upload/Download/FS8205A-DS-12_EN.pdf",
                typicalApplication="With DW01A: DW01A OD -> G1, OC -> G2; S1+S2 tied together to the DW01A CS/VSS side (BAT-); D1 -> battery negative, D2 -> pack negative (load/charger GND). "
                                   "Back-to-back so it blocks in both directions."),
    LibraryPart(mpn="AO4407A", description="P-channel MOSFET -30 V -12 A, 9 mΩ, SOIC-8 (reverse-polarity / high-side switch)",
                category="transistor", package="SOIC-8", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", symbol="Transistor_FET:AO4407A",
                pins=[P("1", "S", "passive", "left"), P("2", "S", "passive", "left"), P("3", "S", "passive", "left"), P("4", "G", "input", "left"),
                      P("5", "D", "passive", "right"), P("6", "D", "passive", "right"), P("7", "D", "passive", "right"), P("8", "D", "passive", "right")],
                voltageMax=30, currentMaxMa=12000, powerMaxW=3.1, unitCost=0.35, manufacturer="Alpha & Omega",
                datasheetUrl="https://www.aosmd.com/res/data_sheets/AO4407A.pdf",
                companions=[C("RC0603FR-07100KL", "pullup", "gate-to-source 100 kΩ (gate bleed)"),
                            C("MMSZ5242BT1G", "protection", "12 V zener G-S clamp when Vin can exceed ±20 V (4S/6S packs)")],
                typicalApplication="Reverse-polarity protection for a 2S–6S pack: battery+ -> D, S -> board VBAT rail, G -> GND through 100 kΩ (with a 12 V G-S zener above 20 V). "
                                   "Body diode conducts at power-up, then the channel takes over at ~9 mΩ — no diode drop."),
    LibraryPart(mpn="IRF4905PBF", description="P-channel MOSFET -55 V -74 A, 20 mΩ, TO-220 (high-current reverse-polarity / motor high-side)",
                category="transistor", package="TO-220", footprint="Package_TO_SOT_THT:TO-220-3_Vertical", symbol="Transistor_FET:IRF4905",
                pins=[P("1", "G", "input", "left"), P("2", "D", "passive", "top"), P("3", "S", "passive", "bottom")],
                voltageMax=55, currentMaxMa=74000, powerMaxW=200, unitCost=1.9, manufacturer="Infineon", bodyHeightMm=15.5,
                datasheetUrl="https://www.infineon.com/dgdl/irf4905pbf.pdf?fileId=5546d462533600a4015355e3d0d61a3d",
                companions=[C("RC0603FR-07100KL", "pullup", "gate-to-source 100 kΩ"), C("MMSZ5245BT1G", "protection", "15 V G-S zener clamp")],
                typicalApplication="Ideal-diode style reverse protection on a high-current battery path (drone PDB, robot): battery+ -> D, S -> VBAT, G -> GND via 100 kΩ, 15 V zener G-S. "
                                   "Vgs(th) needs ≥ 4 V of pack voltage."),
    LibraryPart(mpn="MOC3021M", description="Random-phase optotriac driver, 400 V, 5 kV isolation, DIP-6 (drives a power triac from a GPIO)",
                category="interface", package="DIP-6", footprint="Package_DIP:DIP-6_W7.62mm", symbol="Relay_SolidState:MOC3021M",
                pins=[P("1", "A", "input", "left"), P("2", "K", "input", "left"), P("3", "NC", "passive", "left"),
                      P("4", "MT2", "passive", "right"), P("5", "NC", "passive", "right"), P("6", "MT1", "passive", "right")],
                voltageMax=400, currentMaxMa=60, unitCost=0.4, manufacturer="onsemi", bodyHeightMm=4.6,
                datasheetUrl="https://www.onsemi.com/pdf/datasheet/moc3020m-d.pdf",
                companions=[C("RC0603FR-07330RL", "current_limit", "LED series resistor from the GPIO"),
                            C("BTA16-600B", "load_cap", "the power triac it fires"),
                            C("RC1206FR-07100RL", "filter", "gate resistor MT2 -> triac gate (330-470 Ω, 1206)")],
                typicalApplication="AC load switch: GPIO -> 330 Ω -> A, K -> GND (SELV side); MT1 -> triac gate via 330 Ω, MT2 -> triac MT2 (mains side). "
                                   "Keep the two sides ≥ 6 mm apart on the PCB (creepage)."),
    LibraryPart(mpn="BTA16-600B", description="Triac 16 A 600 V, insulated tab, TO-220 (AC load switching with MOC30xx)",
                category="actuator", package="TO-220", footprint="Package_TO_SOT_THT:TO-220-3_Vertical", symbol="Triac_Thyristor:BTA16-600B",
                pins=[P("1", "MT1", "passive", "left"), P("2", "MT2", "passive", "top"), P("3", "G", "input", "bottom")],
                voltageMax=600, currentMaxMa=16000, unitCost=0.9, manufacturer="ST", bodyHeightMm=15.5,
                datasheetUrl="https://www.st.com/resource/en/datasheet/bta16.pdf",
                typicalApplication="Mains load in series with MT2/MT1 (L -> load -> MT2, MT1 -> N); gate from an MOC3021 through 330 Ω; RC snubber (100 Ω + 100 nF X2) across MT1-MT2 for inductive loads. MAINS: creepage/clearance rules apply."),
    LibraryPart(mpn="HLW8012", description="Single-phase energy metering IC (V/I/P pulse outputs), SOP-8 (smart-plug power monitoring)",
                category="sensor", package="SOIC-8", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", symbol="Sensor_Energy:HLW8012",
                pins=[P("1", "VDD", "power_in", "top"), P("2", "IP", "input", "left"), P("3", "IN", "input", "left"), P("4", "VP", "input", "left"),
                      P("5", "GND", "ground", "bottom"), P("6", "SEL", "input", "right"), P("7", "CF1", "output", "right"), P("8", "CF", "output", "right")],
                voltageMin=4.5, voltageMax=5.5, currentMaxMa=3, unitCost=0.4, manufacturer="HLW", interfaces=["GPIO"],
                datasheetUrl="https://raw.githubusercontent.com/xoseperez/hlw8012/master/docs/HLW8012.pdf",
                companions=[C("WSL2512R0100FEA", "current_limit", "1 mΩ–10 mΩ manganin shunt in the LIVE line between IP/IN"),
                            C("CC0603KRX7R9BB104", "decoupling")],
                typicalApplication="Non-isolated energy meter referenced to LIVE: shunt in L between IP/IN, VP from a 5x470 kΩ / 1 kΩ divider off N; CF/CF1 pulses to the MCU ONLY through optocouplers (PC817) or an isolated MCU domain. The whole chip is at mains potential."),
    LibraryPart(mpn="ACS712ELCTR-20A-T", description="Hall-effect current sensor ±20 A, 100 mV/A, 2.1 kV isolation, SOIC-8 (per-channel AC/DC current)",
                category="sensor", package="SOIC-8", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", symbol="Sensor_Current:ACS712xLCTR-20A",
                pins=[P("1", "IP+", "passive", "left"), P("2", "IP+", "passive", "left"), P("3", "IP-", "passive", "left"), P("4", "IP-", "passive", "left"),
                      P("5", "GND", "ground", "bottom"), P("6", "FILTER", "passive", "right"), P("7", "VIOUT", "output", "right"), P("8", "VCC", "power_in", "top")],
                voltageMin=4.5, voltageMax=5.5, currentMaxMa=13, unitCost=1.5, manufacturer="Allegro", interfaces=["Analog"],
                datasheetUrl="https://www.allegromicro.com/-/media/files/datasheets/acs712-datasheet.pdf",
                companions=[C("CC0603KRX7R9BB104", "decoupling"), C("CL10C101JB8NNNC", "filter", "FILTER pin to GND (1 nF)")],
                typicalApplication="Isolated current sense for AC or DC: the load current flows IP+ -> IP- (isolated from the logic side); VIOUT = 2.5 V ± 100 mV/A into an ADC (divide 5 V output to 3.3 V for an ESP32). "
                                   "IP pins are on the mains side — creepage rules apply."),
    LibraryPart(mpn="ACS712_Module_20A", description="ACS712 20 A current-sensor MODULE (3-pin header VCC/OUT/GND + 2-pin screw terminal for the load)",
                category="sensor", package="Module-3pin", footprint=H1x3, symbol="Sensor_Current:ACS712_Module",
                pins=[P("1", "VCC", "power_in", "left"), P("2", "OUT", "output", "left"), P("3", "GND", "ground", "left")],
                voltageMin=4.5, voltageMax=5.5, currentMaxMa=13, unitCost=1.2, interfaces=["Analog"], outputTracksSupply=True, recommendedSupplyV=5.0,
                typicalApplication="Hobby current sensing: VCC 5 V, GND, OUT (2.5 V ± 100 mV/A) -> divider -> ADC. The load wires go to the module's own screw terminal (off-board)."),
    LibraryPart(mpn="Relay_Module_4CH_5V", description="4-channel 5 V relay MODULE (opto-isolated inputs, 10 A contacts, screw terminals) on a 6-pin header",
                category="actuator", package="Module-6pin", footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", symbol="Relay:Relay_Module_4CH",
                pins=[P("1", "GND", "ground", "left"), P("2", "IN1", "input", "left"), P("3", "IN2", "input", "left"), P("4", "IN3", "input", "left"),
                      P("5", "IN4", "input", "left"), P("6", "VCC", "power_in", "left")],
                voltageMin=4.75, voltageMax=5.25, currentMaxMa=320, unitCost=3.0, interfaces=["GPIO"],
                typicalApplication="Home-automation switching without designing the mains section: VCC 5 V, GND, IN1-4 from GPIOs (active LOW). Mains loads wire to the module's own screw terminals — the board stays SELV."),
    LibraryPart(mpn="A4988_StepStick_Module", description="A4988 stepper-driver MODULE (StepStick / Pololu-16 pinout, 2 A with heatsink) — for hobby CNC/3D-printer boards",
                category="actuator", package="Pololu-16", footprint="Module:Pololu_Breakout-16_15.2x20.3mm", symbol="Driver_Motor:Pololu_Breakout_A4988",
                pins=[P("1", "ENABLE", "input", "left"), P("2", "MS1", "input", "left"), P("3", "MS2", "input", "left"), P("4", "MS3", "input", "left"),
                      P("5", "RESET", "input", "left"), P("6", "SLEEP", "input", "left"), P("7", "STEP", "input", "left"), P("8", "DIR", "input", "left"),
                      P("9", "VMOT", "power_in", "right"), P("10", "GND", "ground", "right"), P("11", "2B", "output", "right"), P("12", "2A", "output", "right"),
                      P("13", "1A", "output", "right"), P("14", "1B", "output", "right"), P("15", "VDD", "power_in", "right"), P("16", "GND", "ground", "right")],
                voltageMin=8.0, voltageMax=35.0, currentMaxMa=2000, unitCost=1.5, interfaces=["GPIO"], bodyHeightMm=12.0,
                datasheetUrl="https://www.pololu.com/file/0J450/a4988_DMOS_microstepping_driver_with_translator.pdf",
                companions=[C("EEU-FR1E101", "decoupling", "≥ 100 µF electrolytic on VMOT right at the module (mandatory — LC spike kills the driver)"),
                            C("CC0603KRX7R9BB104", "decoupling", "VDD 100 nF")],
                typicalApplication="One module per axis on 2x8 header rows: VMOT 8-35 V + 100 µF, VDD 3.3/5 V, GND both; STEP/DIR/ENABLE from GPIOs; RESET tied to SLEEP; MS1-3 to VDD for 1/16 step; 1A/1B/2A/2B to a 4-pin JST-XH motor connector."),
    LibraryPart(mpn="DRV8825_StepStick_Module", description="DRV8825 stepper-driver MODULE (StepStick pinout, 2.2 A, 45 V) — pin-compatible with A4988 module",
                category="actuator", package="Pololu-16", footprint="Module:Pololu_Breakout-16_15.2x20.3mm", symbol="Driver_Motor:Pololu_Breakout_DRV8825",
                pins=[P("1", "ENABLE", "input", "left"), P("2", "M0", "input", "left"), P("3", "M1", "input", "left"), P("4", "M2", "input", "left"),
                      P("5", "RESET", "input", "left"), P("6", "SLEEP", "input", "left"), P("7", "STEP", "input", "left"), P("8", "DIR", "input", "left"),
                      P("9", "VMOT", "power_in", "right"), P("10", "GND", "ground", "right"), P("11", "B2", "output", "right"), P("12", "B1", "output", "right"),
                      P("13", "A1", "output", "right"), P("14", "A2", "output", "right"), P("15", "FAULT", "output", "right"), P("16", "GND", "ground", "right")],
                voltageMin=8.2, voltageMax=45.0, currentMaxMa=2200, unitCost=1.8, interfaces=["GPIO"], bodyHeightMm=12.0, alternatives=["A4988_StepStick_Module"],
                companions=[C("EEU-FR1E101", "decoupling", "≥ 100 µF electrolytic on VMOT at the module")],
                typicalApplication="Drop-in for the A4988 module (no VDD pin: pin 15 is FAULT, logic is powered internally). Same STEP/DIR/ENABLE wiring."),
    LibraryPart(mpn="JST_XH_4P", description="JST XH 4-pin 2.5 mm shrouded header (stepper motor / 4-wire sensor)",
                category="connector", package="JST-XH-4", footprint="Connector_JST:JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical", symbol="Connector_Generic:Conn_01x04",
                pins=[P(str(i), str(i), "passive", "left") for i in range(1, 5)],
                currentMaxMa=3000, unitCost=0.12, manufacturer="JST", bodyHeightMm=7.0,
                typicalApplication="Bipolar stepper: 1A/1B/2A/2B from the driver module. Off-board motor; this header is what sits on the PCB."),
    LibraryPart(mpn="LCD1602_I2C_Module", description="16x2 character LCD MODULE with PCF8574 I2C backpack, 4-pin header (GND/VCC/SDA/SCL)",
                category="display", package="Module-4pin", footprint=H1x4, symbol="Display_Character:LCD1602_I2C",
                pins=[P("1", "GND", "ground", "left"), P("2", "VCC", "power_in", "left"), P("3", "SDA", "bidirectional", "left"), P("4", "SCL", "input", "left")],
                voltageMin=4.5, voltageMax=5.5, currentMaxMa=30, unitCost=2.5, interfaces=["I2C"], defaultI2cAddress="0x27", bodyHeightMm=12.0,
                firmwareLibrary="marcoschwartz/LiquidCrystal_I2C", firmwareNotes="I2C addr 0x27 (0x3F on some backpacks); 5 V logic — pull-ups to 5 V, so an ESP32 needs a level shifter or 3.3 V-tolerant wiring",
                typicalApplication="Beginner display: VCC 5 V, GND, SDA/SCL to the MCU I2C (the backpack pulls up to 5 V — on a 3.3 V MCU use a level shifter or run the bus at 3.3 V without the backpack pull-ups)."),
    LibraryPart(mpn="LCD1602_Parallel", description="16x2 character LCD (HD44780), 16-pin 2.54 mm header, 4-bit parallel",
                category="display", package="LCD-16pin", footprint=H1x16, symbol="Display_Character:LCD-016N002L",
                pins=[P("1", "VSS", "ground", "left"), P("2", "VDD", "power_in", "left"), P("3", "VO", "input", "left"), P("4", "RS", "input", "left"),
                      P("5", "RW", "input", "left"), P("6", "E", "input", "left"), P("7", "D0", "bidirectional", "left"), P("8", "D1", "bidirectional", "left"),
                      P("9", "D2", "bidirectional", "left"), P("10", "D3", "bidirectional", "left"), P("11", "D4", "bidirectional", "left"), P("12", "D5", "bidirectional", "left"),
                      P("13", "D6", "bidirectional", "left"), P("14", "D7", "bidirectional", "left"), P("15", "LED+", "power_in", "left"), P("16", "LED-", "ground", "left")],
                voltageMin=4.5, voltageMax=5.5, currentMaxMa=25, unitCost=1.8, bodyHeightMm=12.0,
                companions=[C("3296W-1-103LF", "filter", "contrast trimmer on VO"), C("RC0603FR-07100RL", "current_limit", "backlight LED+ series resistor")],
                typicalApplication="4-bit mode: RS/E/D4-D7 from GPIOs, RW to GND, VO from a 10 kΩ trimmer wiper, LED+ via 100 Ω to 5 V. 5 V logic thresholds — fine from a 5 V AVR; from a 3.3 V MCU use the I2C module."),
    LibraryPart(mpn="Keypad_4x4_Membrane", description="4x4 membrane matrix keypad on an 8-pin 2.54 mm header (4 rows + 4 columns)",
                category="switch", package="Module-8pin", footprint=H1x8, symbol="Switch:Keypad_4x4",
                pins=[P("1", "ROW1", "passive", "left"), P("2", "ROW2", "passive", "left"), P("3", "ROW3", "passive", "left"), P("4", "ROW4", "passive", "left"),
                      P("5", "COL1", "passive", "left"), P("6", "COL2", "passive", "left"), P("7", "COL3", "passive", "left"), P("8", "COL4", "passive", "left")],
                unitCost=0.9,
                firmwareLibrary="chris--a/Keypad",
                typicalApplication="Scanned matrix: ROW1-4 as GPIO outputs, COL1-4 as inputs with pull-ups (internal or 10 kΩ). 8 GPIOs; or MCP23017 for I2C."),
    LibraryPart(mpn="Joystick_Module_2Axis", description="2-axis analog joystick MODULE with push button, 5-pin header (GND/+5V/VRX/VRY/SW)",
                category="switch", package="Module-5pin", footprint=H1x5, symbol="Switch:Joystick_2Axis",
                pins=[P("1", "GND", "ground", "left"), P("2", "VCC", "power_in", "left"), P("3", "VRX", "output", "left"), P("4", "VRY", "output", "left"), P("5", "SW", "output", "left")],
                voltageMin=3.0, voltageMax=5.5, currentMaxMa=5, unitCost=0.8, interfaces=["Analog", "GPIO"], outputTracksSupply=True,
                typicalApplication="Two 10 kΩ pots as dividers across VCC: VRX/VRY to ADC pins (power it from the MCU's own rail so the ADC range matches), SW to a GPIO with a pull-up."),
    LibraryPart(mpn="Limit_Switch_KW12", description="Micro limit switch (KW12-3 lever, SPDT: C/NO/NC) on a 3-pin 2.54 mm header — machine homing / end-stop",
                category="switch", package="Module-3pin", footprint=H1x3, symbol="Switch:SW_SPDT",
                pins=[P("1", "C", "passive", "left"), P("2", "NO", "passive", "left"), P("3", "NC", "passive", "left")],
                voltageMax=125, currentMaxMa=3000, unitCost=0.3,
                typicalApplication="End-stop: C to GND, NO (or NC for fail-safe) to the MCU input with a 10 kΩ pull-up to the MCU's rail; 100 nF to GND for debounce. Off-board switch, on-board header."),
    LibraryPart(mpn="Battery_Snap_9V", description="9 V battery snap connector on a 2-pin 2.54 mm header",
                category="connector", package="Snap-2wire", footprint=H1x2, symbol="Connector_Generic:Conn_01x02",
                pins=[P("1", "+", "power_out", "left"), P("2", "-", "ground", "left")],
                voltageMin=7.0, voltageMax=9.6, currentMaxMa=500, unitCost=0.2,
                typicalApplication="9 V (7-9.6 V) source: + to VIN through a reverse diode / P-FET, - to GND. Feed a 5 V regulator (linear is acceptable at <100 mA)."),
    LibraryPart(mpn="MAX30102_Module", description="MAX30102 pulse-oximeter / heart-rate breakout MODULE (GY-MAX30102: onboard 1.8 V + 3.3 V regulators, VIN 3.3-5 V, I2C), 7-pin header",
                category="sensor", package="Module-7pin", footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical", symbol="Sensor:MAX30102_Module",
                pins=[P("1", "VIN", "power_in", "left"), P("2", "GND", "ground", "left"), P("3", "SCL", "input", "left"), P("4", "SDA", "bidirectional", "left"),
                      P("5", "INT", "output", "left"), P("6", "IRD", "passive", "left"), P("7", "RD", "passive", "left")],
                voltageMin=3.3, voltageMax=5.5, currentMaxMa=20, unitCost=2.0, interfaces=["I2C"], defaultI2cAddress="0x57", bodyHeightMm=6.0,
                alternatives=["MAX30102"], firmwareLibrary="sparkfun/SparkFun MAX3010x Pulse and Proximity Sensor Library", firmwareNotes="I2C addr 0x57; INT active-low, needs a pull-up",
                companions=[C("RC0603FR-074K7L", "pullup", "SDA/SCL pull-ups (the module carries its own 4.7k on most boards — check; INT needs one)")],
                typicalApplication="Wearable HR/SpO2: VIN from the 3.3 V rail (or VBAT 3.7-4.2 V — the module regulates its own 1.8 V core and LED supply), GND, SDA/SCL on the MCU I2C, INT to a GPIO with a pull-up. "
                                   "IRD/RD are the LED cathodes — leave unconnected. Use this instead of the bare MAX30102 IC unless you are laying out the 1.8 V + LED rails yourself."),
    LibraryPart(mpn="LIR2032_Holder", description="LIR2032 rechargeable 3.6 V Li-ion coin cell (40 mAh) in a CR2032-size holder",
                category="connector", package="CR2032-Holder", footprint="Battery:BatteryHolder_Keystone_3034_1x20mm", symbol="Device:Battery_Cell",
                pins=[P("1", "+", "power_out", "left"), P("2", "-", "ground", "left")],
                voltageMin=3.0, voltageMax=4.2, currentMaxMa=40, unitCost=0.4, bodyHeightMm=5.4,
                typicalApplication="Rechargeable coin cell for wearables/RTC backup: charge with a coin-cell charger (MCP73831 at 10 mA PROG) — a CR2032 is PRIMARY and must never be charged."),
]


def main() -> int:
    import json
    from pathlib import Path as _P
    cache = json.loads((_BACKEND_ROOT / "app" / "library" / "footprint_cache.json").read_text(encoding="utf-8"))
    existing = {p.mpn for p in library.all_parts()}
    n_new = n_upd = 0
    for part in PARTS:
        assert part.footprint in cache, f"{part.mpn}: footprint {part.footprint} not in verified cache"
        library.add_user_part(part)
        if part.mpn in existing:
            n_upd += 1
        else:
            n_new += 1
    print(f"tier-0: {n_new} new, {n_upd} updated; library now {len(library.all_parts())} parts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
