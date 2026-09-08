"""Human review pass over the curation-bot proposal queue (2026-08-19, 34 rows).

Every proposal in `app/library/proposed_parts/` was read against its datasheet.
This script is the record of that review and the mechanism that applies it:

  * PROMOTIONS — pin table confirmed *extracted* from the datasheet (each entry
    below names the page/table that was checked), footprint remapped to a
    `Lib:Name` that is present in the committed `footprint_cache.json`, pin
    types / category / I2C address corrected, and a `typicalApplication`
    rewritten to name the supply range and the essential external parts.
    Promoted through `library.add_user_part` so `user_parts.json` keeps its
    ASCII-escaped encoding convention.
  * REJECTIONS — the review bar from CLAUDE.md: a pin table that was *inferred*
    rather than extracted (synthetic pin numbers, "may not match the
    datasheet", a 1-pin shell, a BGA/module with guessed numbering) is dropped
    whatever its confidence score, as is a part whose footprint is demonstrably
    the wrong land pattern.
  * LEFT IN QUEUE — the extraction is fine but the package needs a footprint
    only a box with KiCad installed can supply. These files are NOT deleted.

    python -m scripts.promote_tier1            # apply
    python -m scripts.promote_tier1 --dry-run  # report only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import LibraryPart  # noqa: E402
from app.services import curation  # noqa: E402
from app.services import library  # noqa: E402

CACHE = _BACKEND_ROOT / "app" / "library" / "footprint_cache.json"

TSSOP8 = "Package_SO:TSSOP-8_4.4x3mm_P0.65mm"
QFN24 = "Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.7mm"
SC70_6 = "Package_TO_SOT_SMD:SOT-363_SC-70-6"
HTSSOP20 = "Package_SO:HTSSOP-20-1EP_4.4x6.5mm_P0.65mm_EP3.4x6.5mm"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def pin(part: LibraryPart, number: str, **kw) -> None:
    for p in part.pins:
        if p.number == number:
            for k, v in kw.items():
                setattr(p, k, v)
            return
    raise SystemExit(f"{part.mpn}: no pin {number}")


def renumber(part: LibraryPart, mapping: dict[str, str]) -> None:
    """Reassign pin numbers by NAME (used where the extractor mirrored a
    package's second row)."""
    by_name = {p.name: p for p in part.pins}
    for name, num in mapping.items():
        if name not in by_name:
            raise SystemExit(f"{part.mpn}: no pin named {name}")
        by_name[name].number = num


def note(part: LibraryPart, text: str) -> None:
    if part.provenance is None:
        return
    line = f"[human review 2026-08-19] {text}"
    if line not in part.provenance.notes:
        part.provenance.notes = list(part.provenance.notes) + [line]


def load(mpn: str) -> LibraryPart | None:
    """The proposal if it is still queued, else the already-promoted live part —
    so this review script stays re-runnable after it has been applied once."""
    part = curation.load_proposed(mpn)
    if part is not None:
        return part
    return next((p for p in library.list_user_parts() if p.mpn == mpn), None)


# --------------------------------------------------------------------------- #
# per-part corrections
# --------------------------------------------------------------------------- #


def fix_atecc608a(p: LibraryPart) -> None:
    # Datasheet summary DS40001977B Figure 1 (8-lead SOIC): 1-3 NC, 4 GND,
    # 5 SDA, 6 SCL, 7 NC, 8 VCC — matches the extraction exactly.
    p.interfaces = ["I2C"]          # the SWI/UART tag described the other die option
    p.defaultI2cAddress = "0x60"
    p.typicalApplication = (
        "Power VCC (pin 8) from the host's 2.0-5.5 V logic rail with a 100 nF capacitor to GND "
        "(pin 4) and leave pins 1, 2, 3 and 7 unconnected. SDA (pin 5) and SCL (pin 6) join the "
        "host I2C bus with 4.7 kOhm pull-ups to that same rail; the device answers at 7-bit "
        "address 0x60. Allow for a wake pulse before the first command and budget ~14 mA peak "
        "while an ECC operation runs, so the rail needs local bulk capacitance if it is weak."
    )
    note(p, "defaultI2cAddress set to 0x60 (the 0xC0 write address the ATECC508A/608A family "
            "ships with, as used by the Adafruit/SparkFun breakouts) — the summary datasheet "
            "does not state it.")
    note(p, "interfaces trimmed to I2C: the SWI/UART timing note applies to the single-wire "
            "ordering option, not the -SSHDA I2C part.")


def fix_bq76920(p: LibraryPart) -> None:
    # SLUSBK2 Table 6-1 (20-TSSOP): 1 DSG, 2 CHG, 3 VSS, 4 SDA, 5 SCL, 6 TS1,
    # 7 CAP1, 8 REGOUT, 9 REGSRC, 10 BAT, 11 NC, 12-17 VC5..VC0, 18 SRP,
    # 19 SRN, 20 ALERT — matches.
    p.typicalApplication = (
        "3-5 cell Li-ion analog front end. Tie BAT and REGSRC (pins 10, 9) to the top of the "
        "6-25 V stack and VSS (pin 3) to its bottom, and bring each cell tap to VC0-VC5 (pins "
        "17 down to 12) through a 1 kOhm series resistor with a 100 nF cell filter. A low-value "
        "sense resistor between SRP and SRN (pins 18, 19) in the pack's negative leg sets the "
        "current scale, and CHG/DSG (pins 2, 1) drive the back-to-back protection N-FETs in that "
        "same leg. Decouple REGOUT and CAP1 (pins 8, 7) with 100 nF to VSS, pull SDA/SCL (pins "
        "4, 5) up to the host's logic rail with 4.7 kOhm, pull ALERT (pin 20) up through 1 MOhm, "
        "and hang a 10 kOhm NTC on TS1 (pin 6) - or 10 kOhm to VSS when thermistor sensing is "
        "unused. Default I2C address 0x08 (0x18 on some EEPROM options)."
    )


def fix_cd4051(p: LibraryPart) -> None:
    # SCHS047O Table 4-1 — matches pin for pin.
    p.typicalApplication = (
        "Single-supply use: VDD (pin 16) on a 3-20 V logic rail with a 100 nF decoupler, VSS "
        "(pin 8) and VEE (pin 7) both to ground. A/B/C (pins 11, 10, 9) select which of CH0-CH7 "
        "connects to COM (pin 3); INH (pin 6) disables every channel when high, so drive it from "
        "a GPIO or tie it low - never leave it floating. The analog signal must stay between VEE "
        "and VDD, and on-resistance is ~120 Ohm at 5 V, so feed a high-impedance load such as an "
        "ADC input rather than a current load. For bipolar signals VEE goes to the negative "
        "supply instead of ground while the logic inputs stay referenced to VSS."
    )


def fix_ina180(p: LibraryPart) -> None:
    # SBOS741H Figure 6-1 (pinout A, the INA180A devices): 1 OUT, 2 GND,
    # 3 IN+, 4 IN-, 5 VS — matches.
    p.typicalApplication = (
        "Put the shunt in the load path with IN+ (pin 3) on the supply side and IN- (pin 4) on "
        "the load side; the common-mode voltage may sit anywhere from -0.2 V to +26 V regardless "
        "of the 2.7-5.5 V supply on VS (pin 5), which needs a 100 nF capacitor to GND (pin 2). "
        "OUT (pin 1) is a ground-referenced analog voltage at 20 V/V, so pick the shunt such "
        "that the full-scale drop x 20 stays inside the ADC's input range. This pinout references "
        "the output to GND, so it measures unidirectional current only."
    )


def fix_ina240(p: LibraryPart) -> None:
    # SBOS662C Figure 6-1 (PW/TSSOP-8): 1 NC, 2 IN+, 3 IN-, 4 GND, 5 VS,
    # 6 REF2, 7 REF1, 8 OUT — matches. Only the footprint id was wrong: the
    # KiCad stock TSSOP-8 is named by its lead span (4.4 mm), not its body.
    p.footprint = TSSOP8
    note(p, "footprint remapped from the non-existent "
            "'Package_SO:TSSOP-8_3.0x4.4mm_P0.65mm' to the KiCad stock "
            f"'{TSSOP8}' (same 3.0 x 4.4 mm body, named by lead span).")


def fix_ir2104(p: LibraryPart) -> None:
    # IR2104(S) "Lead Assignments": 1 VCC, 2 IN, 3 SD, 4 COM, 5 LO, 6 VS,
    # 7 HO, 8 VB. The extraction had the second column reversed (5 VB, 6 HO,
    # 7 VS, 8 LO) — every high/low-side connection would have been mirrored.
    renumber(p, {"LO": "5", "VS": "6", "HO": "7", "VB": "8"})
    p.pins.sort(key=lambda x: int(x.number))
    p.typicalApplication = (
        "Half-bridge gate driver for one N-channel leg. VCC (pin 1) takes a 10-20 V gate-drive "
        "supply with a 100 nF decoupler to COM (pin 4), IN (pin 2) is the PWM input and SD "
        "(pin 3) must be pulled HIGH to enable - drive it from a GPIO or tie it to VCC, never "
        "leave it floating. LO (pin 5) drives the low-side gate, VS (pin 6) connects to the "
        "switch node, HO (pin 7) drives the high-side gate and VB (pin 8) is the bootstrap rail: "
        "a 100 nF-1 uF bootstrap capacitor from VB to VS plus a fast diode from VCC to VB. The "
        "internal 520 ns dead time and cross-conduction interlock mean the single IN signal "
        "drives both outputs, so no external dead-time network is needed."
    )
    note(p, "pin numbers 5-8 corrected (were VB/HO/VS/LO, i.e. the second column reversed) "
            "against the IR2104(S) Lead Assignments diagram: 5 LO, 6 VS, 7 HO, 8 VB.")


def fix_lan8720(p: LibraryPart) -> None:
    # DS00002165B Figure 2-1 (24-QFN pin assignments) + Table 2-1 — matches.
    p.footprint = QFN24
    p.interfaces = ["Ethernet"]
    pin(p, "2", type="bidirectional")    # LED2/nINTSEL — LED driver + reset strap
    pin(p, "3", type="bidirectional")    # LED1/REGOFF  — LED driver + reset strap
    pin(p, "6", type="power_out")        # VDDCR is the internal 1.2 V regulator OUTPUT
    pin(p, "24", type="passive")         # RBIAS sets the bias current through a resistor
    p.typicalApplication = (
        "RMII 10/100 Ethernet PHY. VDDIO (pin 9) and the analog rails VDD1A/VDD2A (pins 19, 1) "
        "run from 3.3 V; VDDCR (pin 6) is the internal 1.2 V regulator OUTPUT and must only be "
        "decoupled, never driven. Give every supply pin a local 100 nF and tie the exposed pad "
        "to ground. A 25 MHz crystal sits across XTAL1/XTAL2 (pins 5, 4), or feed a 50 MHz "
        "reference into CLKIN in REF_CLK-in mode. TXP/TXN (pins 21, 20) and RXP/RXN (pins 23, "
        "22) go through 1:1 magnetics to the RJ45 with the datasheet's termination network, and "
        "RBIAS (pin 24) needs a 12.1 kOhm 1% resistor to ground. TXD0/TXD1/TXEN (pins 17, 18, "
        "16), RXD0/RXD1 (pins 8, 7) and CRS_DV (pin 11) form the RMII link to the MAC, with "
        "MDIO/MDC (pins 12, 13) as the management bus - 10 kOhm pull-ups on MDIO, nINT (pin 14) "
        "and nRST (pin 15). Note that RXD0/RXD1/CRS_DV/RXER and the LED pins double as MODE and "
        "PHYAD configuration straps latched at reset, so any LED or pull-up on them changes the "
        "PHY's power-up mode."
    )
    note(p, f"footprint remapped to the cached KiCad stock '{QFN24}'.")
    note(p, "VDDCR retyped power_out (internal 1.2 V regulator output, not a supply input); "
            "RBIAS retyped passive; the two LED/strap pins retyped bidirectional.")
    note(p, "interfaces normalised to the schema's 'Ethernet' tag (were the non-schema "
            "'RMII'/'SMI').")


def fix_lm66100(p: LibraryPart) -> None:
    # SLVSEZ8A Section 5 Pin Functions (DCK/SC-70-6): 1 VIN, 2 GND, 3 CE,
    # 4 N/C, 5 ST, 6 VOUT — matches.
    p.footprint = SC70_6
    p.package = "SC-70-6 (SOT-363)"
    p.typicalApplication = (
        "Ideal-diode / ORing load switch. VIN (pin 1) accepts 1.5-5.5 V and VOUT (pin 6) feeds "
        "the load; give each a 1 uF ceramic to GND (pin 2). CE (pin 3) is ACTIVE-LOW and must "
        "not float - tie it to GND for a permanently-on ideal diode, drive it from a GPIO to use "
        "it as a load switch, or tie it to VOUT to get automatic reverse-current blocking. ST "
        "(pin 5) is an open-drain status output (low while the device is disabled) and needs a "
        "pull-up only if it is read; pin 4 is not internally connected. At ~79 mOhm on-resistance "
        "it replaces a Schottky in a USB-or-battery ORing pair with far less drop."
    )
    note(p, f"footprint remapped from the non-existent 'Package_TO_SOT_SMD:SC-70-6' to the "
            f"KiCad stock '{SC70_6}' (its 6-lead SC-70 land pattern), whose cache entry was "
            "derived in scripts/derive_cache_entries.py.")


def fix_mcp2551(p: LibraryPart) -> None:
    # DS20001667G Table 1-3 — matches.
    p.typicalApplication = (
        "High-speed CAN transceiver. VDD (pin 3) needs 4.5-5.5 V with a 100 nF decoupler to VSS "
        "(pin 2). TXD (pin 1) and RXD (pin 4) go to the controller's CAN peripheral; TXD carries "
        "an internal 25 kOhm pull-up so a floating input leaves the bus recessive. CANH (pin 7) "
        "and CANL (pin 6) are the differential bus and take a 120 Ohm termination at each "
        "physical end of the cable only. RS (pin 8) selects the mode: to VSS for full 1 Mbit/s "
        "slope, through a 10-100 kOhm resistor to VSS for slope control, or to VDD for standby. "
        "VREF (pin 5) outputs VDD/2 and may be left open. The RXD output swings to 5 V, so a "
        "3.3 V MCU needs a level shifter or a divider on that line."
    )


def fix_mlx90640(p: LibraryPart) -> None:
    # MLX90640 datasheet rev 12 Table 3: 1 SDA, 2 VDD, 3 GND, 4 SCL — matches.
    p.typicalApplication = (
        "32x24 far-infrared array in a TO-39 can. VDD (pin 2) takes 3.0-3.6 V with a 100 nF "
        "decoupler to GND (pin 3) and the part draws ~20 mA continuously, so budget the rail "
        "accordingly. SDA (pin 1) and SCL (pin 4) are the I2C interface at 7-bit address 0x33 "
        "with 4.7 kOhm pull-ups to the same 3.3 V rail; SDA can sink 40 mA, so pull-ups down to "
        "1 kOhm are allowed for the 1 MHz bus the higher frame rates need. Read the EEPROM "
        "calibration constants once at boot, and keep the can away from board heat sources - "
        "the ambient sensor inside it sets the temperature reference."
    )


def fix_pcf8563(p: LibraryPart) -> None:
    # PCF8563 SO8: 1 OSCI, 2 OSCO, 3 INT, 4 VSS, 5 SDA, 6 SCL, 7 CLKOUT,
    # 8 VDD — matches.
    p.package = "SOIC-8"
    p.category = "sensor"     # matches the library's PCF8523 / DS3231M RTC entries
    pin(p, "1", type="passive")   # crystal node
    pin(p, "2", type="passive")   # crystal node
    p.typicalApplication = (
        "Real-time clock / calendar. VDD (pin 8) accepts 1.0-5.5 V with a 100 nF decoupler to "
        "VSS (pin 4) and keeps time down to 1.0 V at ~0.25 uA, so it runs on a coin cell or "
        "supercap through a diode-OR. A 32.768 kHz watch crystal goes between OSCI (pin 1) and "
        "OSCO (pin 2) with its specified load capacitors - keep those traces short and guarded. "
        "SDA (pin 5) and SCL (pin 6) join the host I2C bus with 4.7 kOhm pull-ups; the device "
        "answers at 7-bit address 0x51. INT (pin 3) and CLKOUT (pin 7) are open-drain and need "
        "their own pull-ups, and may be left unconnected when alarms and the clock output are "
        "not used."
    )
    note(p, "category logic -> sensor to match the library's other RTCs (PCF8523, DS3231M); "
            "package string normalised SO8 -> SOIC-8; the two crystal pins retyped passive.")


def fix_ref5025(p: LibraryPart) -> None:
    # SBOS410O Figure 5-1 / Table 5-1 (REF50xxAI): 1 DNC, 2 VIN, 3 TEMP,
    # 4 GND, 5 TRIM/NR, 6 VOUT, 7 NC, 8 DNC — matches.
    pin(p, "5", type="passive")   # TRIM/NR is an RC node, not a logic input
    p.typicalApplication = (
        "2.5 V precision series voltage reference. VIN (pin 2) needs 2.7-18 V and at least "
        "200 mV of headroom above the output, with a 100 nF plus a 1-10 uF capacitor to GND "
        "(pin 4). VOUT (pin 6) requires a 1 uF or larger low-ESR capacitor for stability and "
        "can source or sink 10 mA. Leave DNC (pins 1, 8) and NC (pin 7) unconnected. TRIM/NR "
        "(pin 5) is optional: a 1 uF capacitor to ground filters the internal reference noise, "
        "or a trim network there adjusts VOUT by a few tenths of a percent. TEMP (pin 3) is a "
        "temperature-proportional output that must feed a high-impedance node - buffer it "
        "before an ADC."
    )


def fix_sn65hvd75(p: LibraryPart) -> None:
    # SLLS983 Section 6 Pin Functions (D/SOIC-8): 1 R, 2 RE, 3 DE, 4 D,
    # 5 GND, 6 A, 7 B, 8 VCC — matches.
    p.typicalApplication = (
        "3.3 V half-duplex RS-485 transceiver. VCC (pin 8) takes 3.0-3.6 V with a 100 nF "
        "decoupler to GND (pin 5). D (pin 4) is the driver input and R (pin 1) the receiver "
        "output, both 3.3 V logic. Tie DE (pin 3) and the active-low RE (pin 2) together to one "
        "GPIO so a high transmits and a low receives; that GPIO must not float. A (pin 6) and B "
        "(pin 7) are the bus pair, terminated with 120 Ohm at each physical end of the cable "
        "only, plus fail-safe bias resistors when a determinate idle level matters. Good for up "
        "to 20 Mbps and 256 nodes; the bus pins tolerate the usual RS-485 fault range but the "
        "logic pins do not - do not drive them from 5 V."
    )


def fix_tlc59711(p: LibraryPart) -> None:
    # SBVS181A "PIN CONFIGURATIONS" / TERMINAL FUNCTIONS (PWP, HTSSOP-20):
    # 1 IREF, 2 GND, 3-8 OUTR0/G0/B0/R1/G1/B1, 9 SDTI, 10 SCKI, 11 SCKO,
    # 12 SDTO, 13-18 OUTR2/G2/B2/R3/G3/B3, 19 VCC, 20 VREG — matches.
    p.footprint = HTSSOP20
    pin(p, "1", type="passive")   # IREF programs the full-scale current via a resistor
    for c in p.companions:        # the proposed 16k IREF resistor is not a library MPN
        if c.role == "current_limit" and c.pin == "IREF":
            c.mpn = "RC0603FR-072K7L"
            c.notes = ("2.7 kOhm from IREF to GND sets ~18 mA per channel "
                       "(I_OLC[mA] = 41 x 1.21 V / R_IREF[kOhm]; 820 Ohm is the 60 mA maximum)")
    for n in ("3", "4", "5", "6", "7", "8", "13", "14", "15", "16", "17", "18"):
        pin(p, n, type="output")  # constant-current SINKS, not supply outputs
    p.typicalApplication = (
        "12-channel constant-current RGB LED sink. VCC (pin 19) takes 4-17 V and the internal "
        "regulator produces 3.3 V on VREG (pin 20), which needs a 1 uF capacitor to GND (pin 2); "
        "alternatively tie VCC to VREG and run both from a 3.0-5.5 V rail. A resistor from IREF "
        "(pin 1) to ground sets the per-channel full-scale current - I_OLC[mA] = 41 x 1.21 V / "
        "R_IREF[kOhm], so 2.7 kOhm gives ~18 mA and 820 Ohm the 60 mA maximum; grounding IREF "
        "directly forces every output off. OUTR0-OUTB3 (pins 3-8 and 13-18) "
        "sink the LED cathodes while the anodes sit on their own supply of up to 17 V. SDTI "
        "(pin 9) and SCKI (pin 10) carry the two-wire data stream from the host - drive them "
        "with an SPI peripheral - and SDTO/SCKO (pins 12, 11) cascade into the next device. "
        "Solder the exposed PowerPAD to a ground copper pour; the part relies on it thermally."
    )
    note(p, f"footprint completed to the KiCad stock '{HTSSOP20}' (the proposal omitted the "
            "exposed-pad suffix), matching the library's other TI PowerPAD TSSOP parts.")
    note(p, "the twelve OUTxn pins retyped output (constant-current sinks) - they were "
            "power_out, which the ERC matrix reads as supply rails; IREF retyped passive.")


def fix_tps22918(p: LibraryPart) -> None:
    # SLVSD76C Section 5 Pin Functions (DBV/SOT-23-6): 1 VIN, 2 GND, 3 ON,
    # 4 CT, 5 QOD, 6 VOUT — matches.
    p.typicalApplication = (
        "2 A load switch. VIN (pin 1) accepts 1.0-5.5 V with a 1 uF ceramic to GND (pin 2), and "
        "VOUT (pin 6) wants at least 1 uF. ON (pin 3) is an active-high control that must not "
        "float - drive it from a GPIO or pull it to the rail. A capacitor from CT (pin 4) to "
        "ground slows the output slew for a high-capacitance load; leave it open for the default "
        "rise time. QOD (pin 5) discharges the output when the switch opens: tie it to VOUT for "
        "the internal pull-down, add a resistor between VOUT and QOD for a slower discharge, or "
        "leave it open to disable it. Quiescent current stays under 16 uA."
    )


def fix_ts5a3159(p: LibraryPart) -> None:
    # SCDS231 Section 5 Pin Functions (DBV/SOT-23-6): 1 NO, 2 GND, 3 NC,
    # 4 COM, 5 V+, 6 IN — matches.
    p.typicalApplication = (
        "Single-pole double-throw analog switch. V+ (pin 5) takes 1.65-5.5 V with a 100 nF "
        "decoupler to GND (pin 2), and every signal on COM/NO/NC must stay between GND and V+. "
        "IN (pin 6) is the digital select - high connects COM (pin 4) to NO (pin 1), low "
        "connects COM to NC (pin 3) - and has no internal pull, so drive it from a GPIO. "
        "On-resistance is about 1 Ohm at 5 V and the channels carry up to 200 mA continuously, "
        "so it can switch a small load as well as a signal, but it shares the same supply rails "
        "as the rest of the board and provides no isolation."
    )


def fix_max86150(p: LibraryPart) -> None:
    """Pre-existing library defect, not a proposal: MAX86150 pointed at
    `Package_LGA:Maxim_OESIP-22_3.3x5.6mm`, which exists in neither KiCad nor
    the committed cache, so it resolved to generic geometry and was the single
    export blocker / cache gap in `scripts.regression_footprints`.

    The real land pattern was recovered from Maxim/ADI drawing 90-100024 rev C
    (reference outline 21-100071 rev F) and authored as
    `Synqit.pretty/Synqit_MAX86150.kicad_mod` by
    `scripts/gen_footprints.py --max86150`, with the matching cache entry in
    `scripts/derive_cache_entries.py`. Nothing here is guessed: every dimension
    reproduces a labelled value on that drawing (rows 2.30 c-c, pads 0.90x0.30,
    end-centre pads 0.85x0.30, pin1->pin9 4.05, pin9->pin10 0.95, pin1->pin10
    5.0 BSC), and the pin numbering is corroborated by the datasheet's own N.C.
    set {1,2,3,6,10,11,12,22} falling exactly on the two end groups."""
    p.footprint = "Synqit:Synqit_MAX86150"
    p.package = "OLGA-22 / OESIP-22 (3.3mm x 5.6mm)"
    note(p, "footprint remapped from the non-existent "
            "'Package_LGA:Maxim_OESIP-22_3.3x5.6mm' to the Synqit-authored "
            "'Synqit:Synqit_MAX86150', transcribed from Maxim land-pattern drawing "
            "90-100024 rev C / package outline 21-100071 rev F. Was the library's only "
            "export blocker.")


LIBRARY_FIXES: list[tuple[str, object]] = [
    ("MAX86150", fix_max86150),
]

PROMOTIONS: list[tuple[str, object]] = [
    ("ATECC608A-SSHDA", fix_atecc608a),
    ("BQ76920PWR", fix_bq76920),
    ("CD4051BM", fix_cd4051),
    ("INA180A1IDBVR", fix_ina180),
    ("INA240A1PWR", fix_ina240),
    ("IR2104SPBF", fix_ir2104),
    ("LAN8720AI-CP", fix_lan8720),
    ("LM66100DCKR", fix_lm66100),
    ("MCP2551-I/SN", fix_mcp2551),
    ("MLX90640ESF", fix_mlx90640),
    ("PCF8563T", fix_pcf8563),
    ("REF5025AIDR", fix_ref5025),
    ("SN65HVD75", fix_sn65hvd75),
    ("TLC59711PWPR", fix_tlc59711),
    ("TPS22918DBVR", fix_tps22918),
    ("TS5A3159DBVR", fix_ts5a3159),
]

REJECTIONS: dict[str, str] = {
    "ADUM1201ARZ":
        "Pin names AND package inferred — provenance says the pinout 'was not explicitly shown "
        "in the provided text pages'. Fails the extracted-not-inferred bar.",
    "AMG8833":
        "Package designators J/K/L/M/N were 'treated as pin numbers for library purposes' — "
        "synthetic numbering — and the Panasonic Grid-EYE land pattern is an inferred name with "
        "no cache entry.",
    "ATSAMD51N20A":
        "Family (SAM D5x/E5x) datasheet, not the device pinout: 10 pins with 'synthetic pin "
        "numbers to satisfy schema' for a 120-ball TFBGA.",
    "ATSAME54N20A":
        "Same family datasheet: 12 partially-guessed pins for a 120-ball TFBGA; provenance says "
        "the ball map is not available in the extracted pages.",
    "HT16K33":
        "Provenance: 'the pin assignment page in the provided text is heavily garbled' and the "
        "28-SOP numbering was inferred from common usage. Footprint also absent from the cache.",
    "ISO1050DUBR":
        "Wrong land pattern: the DUB package is TI's wide-body isolation SOP-8 (9.5 x 10.4 mm "
        "per the datasheet's Device Information table), and the proposal maps it to a narrow "
        "3.9 x 4.9 mm SOIC-8 — that would collapse the creepage the 2500 Vrms barrier depends "
        "on. No suitable KiCad stock footprint is cached; re-curate with the real DUB pattern.",
    "MAX17055EWL":
        "WLP package and its bump map inferred ('EWL suffix not explicitly decoded'); the "
        "footprint is a guessed BGA name with no cache entry.",
    "NVIDIA_Jetson_Nano":
        "Zero pins — the 260-pin SODIMM pinout table was never in the extracted pages. A 1-pin "
        "(here 0-pin) shell is an automatic reject.",
    "OV2640":
        "CSP2-38 footprint name inferred with no cache entry, and no typical-application "
        "schematic was present in the extracted pages.",
    "SX1262IMLTRT":
        "Provenance: 'Pin names and mapping are inferred from general SX1262 knowledge; Table "
        "2-1 pinout was not present in the provided text.'",
    "TPS2596":
        "MPN is a family stem, not an orderable part (real devices are TPS259620/621/630/631) "
        "and pin 8 differs between them; footprint also not in the cache.",
}

LEFT_IN_QUEUE: dict[str, str] = {
    "DFPLAYER-MINI":
        "Extraction is usable but the footprint is wrong: a 2x8 20 mm module is not "
        "Package_DIP:DIP-16_W7.62mm. Needs the real module land pattern (KiCad box).",
    "DRV8302DCAR":
        "Pin table looks extracted, but the fpid is wrong — KiCad's 56-pin PowerPAD TSSOP is "
        "'Package_SO:HTSSOP-56-1EP_6.1x14mm_P0.5mm_EP3.61x6.35mm'. Needs that entry dumped/"
        "confirmed on a KiCad box before promotion.",
    "DWM1000":
        "Module land pattern 'RF_Module:DWM1000' is not a KiCad stock footprint and is not in "
        "the cache.",
    "HLK-LD2410":
        "Inferred 5-pad 1.27 mm module footprint with no cache entry.",
    "MPR121QR2":
        "Pin table matches the NXP part, but the QFN-20 3x3 variant (pitch and exposed-pad size) "
        "could not be confirmed and no QFN-20 entry exists in the cache.",
    "RV-3028-C7":
        "Micro Crystal's C7 package is not a standard DFN-8; "
        "'Package_DFN_QFN:DFN-8-1EP_3.2x1.5mm_P0.8mm' does not exist in KiCad's library.",
    "SX1278":
        "QFN-28 4x4 P0.45 — already on CLAUDE.md's 'still needs a KiCad box' list.",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apply the 2026-08-19 proposal-queue review")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    args = ap.parse_args(argv)

    cache = set(json.loads(CACHE.read_text(encoding="utf-8")))
    failures = 0

    print("=" * 78)
    print(f"PROMOTIONS ({len(PROMOTIONS)})")
    print("=" * 78)
    for mpn, fix in PROMOTIONS:
        part = load(mpn)
        if part is None:
            print(f"[MISS] {mpn}: not in the queue")
            failures += 1
            continue
        fix(part)
        if part.footprint not in cache:
            print(f"[FAIL] {mpn}: footprint {part.footprint} is NOT in footprint_cache.json")
            failures += 1
            continue
        part.curationStatus = "human_verified"
        if not args.dry_run:
            library.add_user_part(part)
            curation.delete_proposed(mpn)
        print(f"[OK]   {mpn:18s} {part.category:10s} {part.footprint}")

    print()
    print("=" * 78)
    print(f"PRE-EXISTING LIBRARY FIXES ({len(LIBRARY_FIXES)})")
    print("=" * 78)
    for mpn, fix in LIBRARY_FIXES:
        part = next((p for p in library.list_user_parts() if p.mpn == mpn), None)
        if part is None:
            print(f"[MISS] {mpn}: not in user_parts.json")
            failures += 1
            continue
        fix(part)
        if part.footprint not in cache:
            print(f"[FAIL] {mpn}: footprint {part.footprint} is NOT in footprint_cache.json")
            failures += 1
            continue
        if not args.dry_run:
            library.add_user_part(part)
        print(f"[OK]   {mpn:18s} {part.category:10s} {part.footprint}")

    print()
    print("=" * 78)
    print(f"REJECTED ({len(REJECTIONS)})")
    print("=" * 78)
    for mpn, reason in REJECTIONS.items():
        existed = curation.load_proposed(mpn) is not None
        if not args.dry_run and existed:
            curation.delete_proposed(mpn)
        print(f"[{'DROP' if existed else 'GONE'}] {mpn}\n       {reason}")

    print()
    print("=" * 78)
    print(f"LEFT IN QUEUE ({len(LEFT_IN_QUEUE)}) — needs a KiCad box, not deleted")
    print("=" * 78)
    for mpn, reason in LEFT_IN_QUEUE.items():
        print(f"[KEEP] {mpn}\n       {reason}")

    remaining = [p.mpn for p in curation.list_proposed()]
    print()
    print(f"queue now holds {len(remaining)}: {', '.join(sorted(remaining))}")
    if failures:
        print(f"\n{failures} promotion(s) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
