"""Pin-safety family-table regression — Week-5 robotics task 7.1.

Guards the Raspberry Pi 40-pin / RP2040-Pico / Arduino-AVR capability tables
alongside the original ESP32 one: family-aware pin-name parsing, capability
tags, risk verdicts (ID-EEPROM reserved pins, serial-console pins), the
allocate() role defaults, the ADC-on-Pi refusal, the allocate_pin physical-pin
mapping, and the new R36 5-V-tolerance verifier rule. Also asserts the
known-good stub board stays clean (no false positives).

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_pin_safety
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        _failures.append(label)


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import Net, Project, SchematicIR
    from app.services import agent, library, pin_safety, tools, verifier

    print("Pin-safety family tables regression (7.1)\n")

    # --- family detection -------------------------------------------------
    check("CM4 resolves to the Pi family", pin_safety.is_supported("RaspberryPi_CM4"))
    check("CM5 resolves to the Pi family", pin_safety.is_supported("RaspberryPi_CM5"))
    check("Pico resolves to the RP2040 family", pin_safety.is_supported("RaspberryPi_Pico"))
    check("RP2350 supported", pin_safety.is_supported("RP2350"))
    check("Arduino Uno supported", pin_safety.is_supported("Arduino_Uno_R3"))
    check("Mega 2560 supported", pin_safety.is_supported("Arduino_Mega2560"))
    check("ESP32 still supported", pin_safety.is_supported("ESP32-WROOM-32"))
    # STM32 became a supported family in e1f0333 (review 17, real pin tables) —
    # this check now uses an MPN no family claims.
    check("unknown MCU unsupported", not pin_safety.is_supported("FAKE-MCU-9000"))
    check("HAT header resolves to the Pi family (7.2)",
          pin_safety.is_supported("RPi_HAT_Header_40P"))
    check("shield headers resolve to the AVR family (7.2)",
          pin_safety.is_supported("Arduino_Shield_Headers"))

    # --- family-aware pin-name parsing ------------------------------------
    gn = pin_safety.gpio_number
    check("Pico 'GP26_ADC0' -> 26", gn("GP26_ADC0", "RaspberryPi_Pico") == 26)
    check("Pico 'GP4' -> 4", gn("GP4", "RaspberryPi_Pico") == 4)
    check("CM4 'GPIO17' -> 17", gn("GPIO17", "RaspberryPi_CM4") == 17)
    check("CM4 'ID_SC (BCM2711 GPIO 1)' -> 1",
          gn("ID_SC (BCM2711 GPIO 1)", "RaspberryPi_CM4") == 1)
    check("CM4 'ID_SD (BCM2711 GPIO 0)' -> 0",
          gn("ID_SD (BCM2711 GPIO 0)", "RaspberryPi_CM4") == 0)
    check("CM4 'GPIO_VREF' does not parse", gn("GPIO_VREF", "RaspberryPi_CM4") is None)
    check("Uno 'D3_PWM' -> 3", gn("D3_PWM", "Arduino_Uno_R3") == 3)
    check("Nano 'A4_SDA' -> 18", gn("A4_SDA", "Arduino_Nano_V3") == 18)
    check("Uno 'SDA' -> 18 (A4)", gn("SDA", "Arduino_Uno_R3") == 18)
    check("Mega 'AD7' -> 61 (A7)", gn("AD7", "Arduino_Mega2560") == 61)
    check("Mega bare '52' -> 52", gn("52", "Arduino_Mega2560") == 52)
    check("ESP32 'IO21' -> 21 (unchanged)", gn("IO21", "ESP32-WROOM-32") == 21)
    check("generic parse without mpn still works", gn("GPIO19") == 19)
    check("HAT 'GPIO14_TXD0' -> 14 (underscore suffix, 7.2)",
          gn("GPIO14_TXD0", "RPi_HAT_Header_40P") == 14)
    check("HAT 'ID_SD_EEPROM' -> 0", gn("ID_SD_EEPROM", "RPi_HAT_Header_40P") == 0)
    check("bare ATmega 'D11_PB3_MOSI' -> 11 (7.2 enrichment)",
          gn("D11_PB3_MOSI", "ATmega328P-AU") == 11)
    check("bare ATmega 'A4_PC4_SDA' -> 18", gn("A4_PC4_SDA", "ATmega328P-AU") == 18)
    check("Nano A6 is input-only (ADC6)",
          "input_only" in pin_safety.capabilities("Arduino_Nano_V3", "A6")
          and "adc" in pin_safety.capabilities("Arduino_Nano_V3", "A6"))

    # --- capabilities & 5 V tolerance -------------------------------------
    caps = pin_safety.capabilities
    check("Pi GPIO2 tagged i2c", "i2c" in caps("RaspberryPi_CM4", "GPIO2"))
    check("Pi GPIO18 tagged pwm (hardware channel)",
          "pwm" in caps("RaspberryPi_CM4", "GPIO18"))
    check("Pi GPIO17 NOT tagged pwm (no hardware channel)",
          "pwm" not in caps("RaspberryPi_CM4", "GPIO17"))
    check("Pi ID_SD tagged reserved, not safe_gpio",
          {"reserved"} <= caps("RaspberryPi_CM4", "ID_SD (BCM2711 GPIO 0)")
          and "safe_gpio" not in caps("RaspberryPi_CM4", "ID_SD (BCM2711 GPIO 0)"))
    check("Pico GP26 tagged adc", "adc" in caps("RaspberryPi_Pico", "GP26_ADC0"))
    check("Pico every GPIO tagged pwm (slices)", "pwm" in caps("RaspberryPi_Pico", "GP7"))
    check("Uno D5 tagged pwm", "pwm" in caps("Arduino_Uno_R3", "D5_PWM"))
    check("Uno D7 NOT tagged pwm", "pwm" not in caps("Arduino_Uno_R3", "D7"))
    check("Pi is NOT 5 V-tolerant", pin_safety.five_v_tolerant("RaspberryPi_CM4") is False)
    check("Pico is NOT 5 V-tolerant", pin_safety.five_v_tolerant("RaspberryPi_Pico") is False)
    check("ESP32 is NOT 5 V-tolerant", pin_safety.five_v_tolerant("ESP32-WROOM-32") is False)
    check("Arduino Uno IS 5 V logic", pin_safety.five_v_tolerant("Arduino_Uno_R3") is True)
    check("unknown family -> None", pin_safety.five_v_tolerant("FAKE-MCU-9000") is None)

    # --- risk verdicts ------------------------------------------------------
    risk = pin_safety.risk
    r = risk("RaspberryPi_CM4", "ID_SD (BCM2711 GPIO 0)")
    check("Pi ID_SD is high risk (HAT ID EEPROM)", bool(r) and r["level"] == "high")
    r = risk("RaspberryPi_CM4", "GPIO14")
    check("Pi GPIO14 warns (serial console)", bool(r) and r["level"] == "warn")
    check("Pi GPIO17 is unremarkable", risk("RaspberryPi_CM4", "GPIO17") is None)
    r = risk("Arduino_Uno_R3", "D0_RX")
    check("Uno D0 warns (USB-serial console)", bool(r) and r["level"] == "warn")
    r = risk("ESP32-WROOM-32", "IO12")
    check("ESP32 GPIO12 stays high risk (unchanged)", bool(r) and r["level"] == "high")

    # --- allocate() role defaults ------------------------------------------
    alloc = pin_safety.allocate
    check("Pi i2c_sda -> GPIO2", alloc("RaspberryPi_CM4", "i2c_sda", set()) == ["2"])
    check("Pi uart_tx -> GPIO14", alloc("RaspberryPi_CM4", "uart_tx", set())[0] == "14")
    check("Pi pwm -> a hardware channel",
          alloc("RaspberryPi_CM4", "pwm", set())[0] in {"12", "13", "18", "19"})
    got = alloc("RaspberryPi_CM4", "gpio", set(), count=26)
    check("Pi gpio allocation never yields reserved 0/1",
          "0" not in got and "1" not in got, f"got {got}")
    check("Pi has no ADC to allocate", alloc("RaspberryPi_CM4", "adc", set()) == [])
    check("Pi adc role has an unavailable reason",
          bool(pin_safety.role_unavailable_reason("RaspberryPi_CM4", "adc")))
    check("Pico adc -> GPIO26 first", alloc("RaspberryPi_Pico", "adc", set())[0] == "26")
    check("Pico i2c_scl -> GP5", alloc("RaspberryPi_Pico", "i2c_scl", set()) == ["5"])
    check("Uno pwm -> a hardware PWM pin",
          alloc("Arduino_Uno_R3", "pwm", set())[0] in {"3", "5", "6", "9", "10", "11"})
    check("Uno gpio buries the D0/D1 console",
          alloc("Arduino_Uno_R3", "gpio", set())[0] not in {"0", "1"})
    check("ESP32 i2c_sda -> 21 (unchanged)",
          alloc("ESP32-WROOM-32", "i2c_sda", set()) == ["21"])
    check("used pins are skipped",
          alloc("RaspberryPi_Pico", "i2c_sda", {"GP4"}) != ["4"])

    # --- allocate_pin tool maps GPIO -> the component's physical pin --------
    pico_part = library.get_part("RaspberryPi_Pico")
    if pico_part is not None:
        proj = Project(id="t", name="t", path="/t")
        tools.dispatch(proj, "add_component", {"mpn": "RaspberryPi_Pico", "refdes": "U1"})
        result, _ = tools.allocate_pin(proj, role="i2c_sda")
        pins = result.get("pins") or []
        ok = bool(pins) and pins[0].get("name", "").upper().startswith("GP4")
        check("allocate_pin on a Pico returns GP4 (physical pin 6), not physical pin 4",
              ok and str(pins[0].get("number")) == "6", f"got {pins}")
        # ADC role on a Pi must refuse with the external-ADC hint.
        proj2 = Project(id="t2", name="t2", path="/t2")
        tools.dispatch(proj2, "add_component", {"mpn": "RaspberryPi_CM4", "refdes": "U1"})
        try:
            tools.allocate_pin(proj2, role="adc")
            check("allocate_pin(adc) on a Pi raises with external-ADC guidance", False)
        except tools.ToolError as e:
            check("allocate_pin(adc) on a Pi raises with external-ADC guidance",
                  "ADC" in str(e) or "ADS1115" in str(e), str(e)[:80])
    else:
        check("RaspberryPi_Pico present in library", False)

    # --- R36: 5 V rail on a 3.3 V-only GPIO ---------------------------------
    from app.models import Component, Pin

    def comp(refdes, mpn, *pins):
        return Component(
            refdes=refdes, mpn=mpn,
            pins=[Pin(number=num, name=name, type=t) for (num, name, t) in pins],
        )

    ir = SchematicIR(
        components=[
            comp("U1", "RaspberryPi_CM4",
                 ("50", "GPIO17", "bidirectional"),
                 ("58", "GPIO2", "bidirectional"),
                 ("1", "GND", "ground")),
            comp("U2", "Arduino_Uno_R3",
                 ("17", "D2", "bidirectional"),
                 ("5", "5V", "power_out")),
            comp("R1", "RC0603FR-0710KL",
                 ("1", "1", "passive"), ("2", "2", "passive")),
        ],
        nets=[
            Net(name="VCC_5V", pins=[("U1", "50"), ("U2", "17"), ("U2", "5"), ("R1", "1")]),
            Net(name="VCC_3V3", pins=[("U1", "58"), ("R1", "2")]),
        ],
    )
    bad = Project(id="bad", name="bad", path="/bad", ir=ir)
    issues = verifier.run_all(bad)
    r36 = [i for i in issues if (i.rule or "").startswith("R36")]
    check("R36 fires: Pi GPIO17 tied to the 5 V rail",
          any(i.refdes == "U1" and i.pinNumber == "50" and i.severity == "error"
              for i in r36),
          f"{[ (i.refdes, i.pinNumber) for i in r36 ]}")
    check("R36 exempt: Arduino D2 on 5 V (5 V-tolerant family)",
          not any(i.refdes == "U2" for i in r36))
    check("R36 silent on the 3.3 V rail",
          not any(i.net == "VCC_3V3" for i in r36))

    # --- stub board stays clean (no false positives) ------------------------
    good = Project(id="good", name="good", path="/good")
    for _ in agent.run(good, "low-power ESP32 sensor board"):
        pass
    g = verifier.run_all(good)
    spurious = [i for i in g if (i.rule or "").startswith("R36")]
    check("no R36 false positives on the good stub board",
          len(spurious) == 0, f"{[(i.refdes, i.net) for i in spurious][:5]}")
    r17_err = [i for i in g if (i.rule or "").startswith("R17") and i.severity == "error"]
    check("R17 stays advisory on the stub (no new errors)", len(r17_err) == 0)

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
