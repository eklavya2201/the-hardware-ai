"""Firmware target-class regression — Week-5 robotics task 7.4.

Guards the mcu-baremetal vs linux-sbc split without spending an LLM call:
class mapping per family, the form-factor host-header fallback (a HAT design
has no MCU — the header is the target), the PlatformIO roster for the Week-5
boards, the BCM pin rendering in the Linux context, and the no-key /
no-mapping failure modes.

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_firmware_targets
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
    from app.models import Project
    from app.services import firmware, tools

    print("Firmware target-class regression (7.4)\n")

    # --- class mapping -------------------------------------------------------
    tc = firmware.target_class
    check("CM4 is linux-sbc", tc("RaspberryPi_CM4") == "linux-sbc")
    check("CM5 is linux-sbc", tc("RaspberryPi_CM5") == "linux-sbc")
    check("HAT header is linux-sbc (host = the Pi)",
          tc("RPi_HAT_Header_40P") == "linux-sbc")
    check("Pico is mcu-baremetal (an RP2040 is a real MCU)",
          tc("RaspberryPi_Pico") == "mcu-baremetal")
    check("RP2040 is mcu-baremetal", tc("RP2040") == "mcu-baremetal")
    check("RP2350 is mcu-baremetal", tc("RP2350") == "mcu-baremetal")
    check("Uno is mcu-baremetal", tc("Arduino_Uno_R3") == "mcu-baremetal")
    check("shield headers are mcu-baremetal (host = the Uno)",
          tc("Arduino_Shield_Headers") == "mcu-baremetal")
    check("ESP32 is mcu-baremetal", tc("ESP32-WROOM-32") == "mcu-baremetal")

    # --- PlatformIO roster for the Week-5 boards -----------------------------
    for mpn, board in [
        ("RaspberryPi_Pico", "pico"),
        ("RP2350", "rpipico2"),
        ("Arduino_Uno_R3", "uno"),
        ("Arduino_Nano_V3", "nanoatmega328"),
        ("Arduino_Mega2560", "megaatmega2560"),
        ("Arduino_Shield_Headers", "uno"),
    ]:
        t = firmware.PLATFORMIO_TARGETS.get(mpn)
        check(f"PlatformIO mapping for {mpn} -> {board}",
              t is not None and t["board"] == board)

    # --- target resolution ----------------------------------------------------
    class _C:  # minimal stand-in for a Component
        def __init__(self, mpn):
            self.mpn = mpn
            self.refdes = "U1"

    t = firmware._target_for(_C("RaspberryPi_CM4"))
    check("linux-sbc target needs no PlatformIO mapping",
          t["class"] == "linux-sbc" and "platform" not in t)
    t = firmware._target_for(_C("RaspberryPi_Pico"))
    check("Pico resolves a baremetal PlatformIO target",
          t["class"] == "mcu-baremetal" and t["board"] == "pico")
    try:
        firmware._target_for(_C("STM32H743ZIT6"))
        check("unmapped baremetal MCU raises", False)
    except firmware.FirmwareGenerationError:
        check("unmapped baremetal MCU raises", True)

    # --- host-header fallback (7.3 form-factor designs have no MCU) ----------
    hat = Project(id="h", name="h", path="/h")
    tools.add_component(hat, mpn="RPi_HAT_Header_40P", refdes="J1")
    tools.add_component(hat, mpn="BME280", refdes="U2")
    host = firmware._find_mcu(hat)
    check("HAT design falls back to the host header",
          host is not None and host.mpn == "RPi_HAT_Header_40P")

    # An actual MCU still wins over a header.
    both = Project(id="b", name="b", path="/b")
    tools.add_component(both, mpn="RPi_HAT_Header_40P", refdes="J1")
    tools.add_component(both, mpn="RaspberryPi_CM4", refdes="U1")
    host = firmware._find_mcu(both)
    check("a real MCU wins over the header", host is not None and host.mpn == "RaspberryPi_CM4")

    # --- Linux context renders BCM numbers, not module pad numbers -----------
    tools.connect(hat, net_name="I2C_SDA", pins=[["J1", "3"], ["U2", "3"]])
    tools.connect(hat, net_name="I2C_SCL", pins=[["J1", "5"], ["U2", "4"]])
    host = firmware._find_mcu(hat)
    ctx = firmware._build_context(hat, host, firmware._target_for(host))
    check("context is labelled linux-sbc", "linux-sbc" in ctx)
    check("SDA renders as BCM 2 (not header pin 3)", "BCM 2" in ctx, ctx[:0])
    check("SCL renders as BCM 3", "BCM 3" in ctx)
    check("no PlatformIO block in the linux context", "platformio" not in ctx.lower())

    # A baremetal context is unchanged in shape.
    pico = Project(id="p", name="p", path="/p")
    tools.add_component(pico, mpn="RaspberryPi_Pico", refdes="U1")
    host = firmware._find_mcu(pico)
    ctx = firmware._build_context(pico, host, firmware._target_for(host))
    check("baremetal context keeps the PlatformIO target line",
          "PlatformIO target: platform=raspberrypi" in ctx)

    # Baremetal addresses the GPIO NUMBER, not the physical package pad.
    # ESP32 IO21 is physical pin 33 — the firmware must see GPIO 21, not 33
    # (GPIO36 is even input-only). Regression for the 7.4 live-test fix.
    esp = Project(id="e", name="e", path="/e")
    tools.add_component(esp, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(esp, mpn="BME280", refdes="U2")
    tools.connect(esp, net_name="I2C_SDA", pins=[["U1", "IO21"], ["U2", "3"]])
    tools.connect(esp, net_name="I2C_SCL", pins=[["U1", "IO22"], ["U2", "4"]])
    host = firmware._find_mcu(esp)
    ctx = firmware._build_context(esp, host, firmware._target_for(host))
    check("baremetal ESP32 pins use the GPIO number (21/22), not physical (33/36)",
          "GPIO 21 " in ctx and "GPIO 22 " in ctx
          and "GPIO 33 " not in ctx and "GPIO 36 " not in ctx,
          "physical pad leaked" if "GPIO 33 " in ctx else "ok")

    # --- edit prompts get the Linux note only for the Linux class ------------
    lin = firmware._edit_system_prompt(
        firmware.FEATURE_SYSTEM_PROMPT, {"class": "linux-sbc"}
    )
    bare = firmware._edit_system_prompt(
        firmware.FEATURE_SYSTEM_PROMPT, {"class": "mcu-baremetal"}
    )
    check("linux edit prompt carries the TARGET NOTE", "TARGET NOTE" in lin)
    check("baremetal edit prompt is unchanged", bare == firmware.FEATURE_SYSTEM_PROMPT)

    # --- no API key -> clean, user-fixable error ------------------------------
    try:
        firmware.generate(hat)
        check("generate() without a key raises FirmwareGenerationError", False)
    except firmware.FirmwareGenerationError as e:
        check("generate() without a key raises FirmwareGenerationError",
              "OPENAI_API_KEY" in str(e))

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
