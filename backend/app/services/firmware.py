"""AI-generated firmware for the MCU on the schematic — PRD §7.7.

Single-shot OpenAI call. We send:
  - The chosen MCU's MPN (so the LLM picks the right PlatformIO board/platform).
  - Every connected peripheral with the EXACT GPIO pin numbers from the
    schematic IR. The point of this whole feature: firmware that works without
    the user re-typing pin numbers from the schematic into their code editor.

The LLM returns a strict JSON object with a list of files (platformio.ini,
src/main.cpp, README.md, optional drivers). We validate, store on the Project,
and let the frontend render a file tree.

We do not auto-compile here. PlatformIO needs Python + a working toolchain
which is out of scope. The user runs `pio run` themselves.

Target classes (Week-5 task 7.4). A Raspberry Pi is a computer, not a bare
MCU, so the generator splits by TARGET CLASS:

  - ``mcu-baremetal`` — ESP32 / RP2040 / AVR / STM32: a PlatformIO Arduino
    C++ project (the original path).
  - ``linux-sbc`` — Raspberry Pi CM4/CM5 (or a design built around the
    RPi_HAT_Header_40P): a Linux Python 3 project using gpiozero (libgpiod
    backend) + smbus2/spidev, addressed by BCM GPIO number, run with
    ``python3 main.py`` on the Pi — no toolchain, no flashing.

A form-factor design (7.3) has no MCU *component* — the host header is the
target: RPi_HAT_Header_40P ⇒ linux-sbc (code runs on the Pi the HAT plugs
into), Arduino_Shield_Headers ⇒ mcu-baremetal targeting the Uno underneath.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from textwrap import dedent

from app.models import Firmware, FirmwareFile, Project
from app.services import library, pin_safety


class FirmwareGenerationError(Exception):
    """Raised when the firmware generator can't produce output. Caller turns
    this into a 4xx with a clear message."""


# ---- MCU → PlatformIO mapping --------------------------------------------

# board / platform / framework triples. The LLM is told these in the system
# prompt and instructed to pick the matching one. Listed here for reference
# and for the validation step that errors on unrecognized targets.
PLATFORMIO_TARGETS: dict[str, dict[str, str]] = {
    "ESP32-WROOM-32": {"platform": "espressif32", "board": "esp32dev", "framework": "arduino"},
    "ESP32-S3-WROOM-1-N8": {
        "platform": "espressif32", "board": "esp32-s3-devkitc-1", "framework": "arduino"
    },
    "ESP32-C3-MINI-1": {
        "platform": "espressif32", "board": "esp32-c3-devkitm-1", "framework": "arduino"
    },
    "RP2040": {"platform": "raspberrypi", "board": "pico", "framework": "arduino"},
    "STM32F411CEU6": {
        "platform": "ststm32", "board": "blackpill_f411ce", "framework": "arduino"
    },
    "STM32F103C8T6": {
        "platform": "ststm32", "board": "bluepill_f103c8", "framework": "arduino"
    },
    "STM32G031G8U6": {
        "platform": "ststm32", "board": "genericSTM32G031G8", "framework": "arduino"
    },
    "ATmega328P-AU": {"platform": "atmelavr", "board": "ATmega328P", "framework": "arduino"},
    "ATmega32U4-AU": {"platform": "atmelavr", "board": "leonardo", "framework": "arduino"},
    "ATtiny85-20SU": {"platform": "atmelavr", "board": "attiny85", "framework": "arduino"},
    # Week-5 robotics ecosystem (7.4).
    "RaspberryPi_Pico": {"platform": "raspberrypi", "board": "pico", "framework": "arduino"},
    "RP2040W": {"platform": "raspberrypi", "board": "rpipicow", "framework": "arduino"},
    "RP2350": {"platform": "raspberrypi", "board": "rpipico2", "framework": "arduino"},
    "Arduino_Uno_R3": {"platform": "atmelavr", "board": "uno", "framework": "arduino"},
    "Arduino_Nano_V3": {"platform": "atmelavr", "board": "nanoatmega328", "framework": "arduino"},
    "Arduino_Mega2560": {"platform": "atmelavr", "board": "megaatmega2560", "framework": "arduino"},
    "Arduino_Shield_Headers": {"platform": "atmelavr", "board": "uno", "framework": "arduino"},
}

# Target-class split (7.4): a Raspberry Pi runs Linux — firmware for it is a
# Python program on the OS, not a flashed C++ image.
TargetClass = str  # "mcu-baremetal" | "linux-sbc"

_LINUX_SBC_TOKENS = ("RASPBERRYPI_CM", "RPI_HAT", "HAT_HEADER")

# Form-factor host headers (7.3): a HAT/shield design has no MCU component —
# the header IS the target, standing in for the host board underneath.
HOST_HEADERS: dict[str, str] = {
    "RPi_HAT_Header_40P": "linux-sbc",
    "Arduino_Shield_Headers": "mcu-baremetal",
}


def target_class(mpn: str) -> TargetClass:
    """Which firmware world an MCU/host lives in. Raspberry Pi SBC/CM parts
    (and the HAT header) are ``linux-sbc``; the Pico/RP2040 family is a real
    MCU and stays ``mcu-baremetal``, as does everything else."""
    u = mpn.upper()
    if "PICO" in u or "RP2040" in u or "RP2350" in u:
        return "mcu-baremetal"
    if "RASPBERRY" in u or any(t in u for t in _LINUX_SBC_TOKENS):
        return "linux-sbc"
    return "mcu-baremetal"


SYSTEM_PROMPT = """\
You are an embedded firmware engineer. Given a schematic IR (components +
nets), produce a complete, compilable PlatformIO project that:
  1. Targets the EXACT MCU on the schematic.
  2. Uses the EXACT GPIO pin numbers from the schematic. Do not invent pins.
  3. Initializes every connected peripheral (sensors, displays, motors, etc.).
  4. In `loop()`, demonstrates basic functionality — usually: read each sensor,
     print to Serial at 115200 baud once per second.

PlatformIO target mapping (use the matching `[env]` block):

  ESP32-WROOM-32         → platform=espressif32  board=esp32dev               framework=arduino
  ESP32-S3-WROOM-1-N8    → platform=espressif32  board=esp32-s3-devkitc-1     framework=arduino
  ESP32-C3-MINI-1        → platform=espressif32  board=esp32-c3-devkitm-1     framework=arduino
  RP2040                 → platform=raspberrypi  board=pico                   framework=arduino
  STM32F411CEU6          → platform=ststm32      board=blackpill_f411ce       framework=arduino
  STM32F103C8T6          → platform=ststm32      board=bluepill_f103c8        framework=arduino
  ATmega328P-AU          → platform=atmelavr     board=ATmega328P             framework=arduino
  ATmega32U4-AU          → platform=atmelavr     board=leonardo               framework=arduino
  ATtiny85-20SU          → platform=atmelavr     board=attiny85               framework=arduino

For peripheral driver libraries, prefer well-known Arduino-Library-Manager
entries declared via `lib_deps` in platformio.ini. Examples:
  - BME280     → "adafruit/Adafruit BME280 Library@^2.2.4"
  - BMP280     → "adafruit/Adafruit BMP280 Library@^2.6.8"
  - SHT3x      → "robtillaart/SHT31"
  - MPU6050    → "electroniccats/MPU6050"
  - LIS3DH     → "adafruit/Adafruit LIS3DH"
  - SSD1306    → "adafruit/Adafruit SSD1306"
  - ST7789     → "adafruit/Adafruit ST7735 and ST7789 Library"
  - WS2812B    → "adafruit/Adafruit NeoPixel"
  - DS18B20    → "milesburton/DallasTemperature"
  - VL53L1X    → "pololu/VL53L1X"
  - MAX30102   → "sparkfun/SparkFun MAX3010x Pulse and Proximity Sensor Library"
  - INA219     → "adafruit/Adafruit INA219"
  - DS3231     → "adafruit/RTClib"

Output format (STRICT JSON, nothing else):

{
  "framework": "arduino",
  "target_mcu": "<MPN of the MCU>",
  "summary": "<one short sentence describing what this firmware does>",
  "files": [
    {"path": "platformio.ini", "content": "<full file contents>"},
    {"path": "src/main.cpp",  "content": "<full file contents>"},
    {"path": "README.md",     "content": "<full file contents>"}
  ],
  "readme_path": "README.md"
}

Rules:
  - File paths use forward slashes and are relative to project root.
  - main.cpp MUST `#include <Arduino.h>` first.
  - main.cpp MUST set Serial baud to 115200, wait briefly for it, then
    `Serial.println` the project name and pin assignments in `setup()`.
  - For I²C peripherals, call `Wire.begin(SDA_PIN, SCL_PIN)` with the actual
    pin numbers (omit args for AVR boards where I²C pins are fixed).
  - For SPI peripherals, declare CS / DC / RST as `const int` constants at the
    top of main.cpp, set to the actual GPIO numbers.
  - Don't reference pin numbers we didn't tell you about.
  - README.md must include: target MCU, libraries used (lib_deps), build/upload
    commands, pin-assignment table.
  - You MAY add additional files (e.g. include/config.h, lib/Sensors/Sensors.h)
    if it helps, but keep it minimal. main.cpp under 250 lines is ideal.
"""


LINUX_SBC_SYSTEM_PROMPT = """\
You are a Raspberry Pi software engineer. The target is a LINUX SBC (a
Raspberry Pi / Compute Module or a board that plugs into one) — NOT a bare
MCU. There is no flashing and no PlatformIO: produce a plain Python 3 project
the user copies onto the Pi and runs with `python3 main.py`.

Given a schematic IR (components + nets), produce a complete project that:
  1. Uses BCM GPIO NUMBERS exactly as given in the pin assignments below.
     Do not invent pins. Do not use physical header pin numbers.
  2. Uses `gpiozero` for GPIO (LEDs, buttons, motors via its Motor/PWM
     classes) — it rides the modern libgpiod/lgpio backend. Fall back to
     `lgpio` directly only for something gpiozero can't express.
  3. Uses `smbus2` for raw I2C (bus 1 — the Pi's I2C1 on GPIO2/3) or an
     `adafruit-circuitpython-*` driver (via adafruit-blinka) when a well-known
     one exists for the sensor. Uses `spidev` for SPI (bus 0, GPIO7-11).
  4. In the main loop, demonstrates basic functionality — read each sensor /
     exercise each actuator and print once per second.
  5. Remember the Pi has NO on-chip ADC — analog parts on the schematic go
     through the external ADC that is also on the schematic (e.g. ADS1115 on
     I2C or MCP3008 on SPI); read them through it.

Hardware facts you must respect:
  - All Pi GPIO is 3.3 V-only and NOT 5 V-tolerant.
  - Hardware PWM exists only on BCM 12/13/18/19; gpiozero's PWMOutputDevice
    does software PWM elsewhere (fine for LEDs, poor for servos — say so in
    the README if it applies).
  - GPIO14/15 are the serial console by default; GPIO0/1 (ID_SD/ID_SC) are
    reserved for the HAT ID EEPROM.
  - I2C/SPI must be enabled via `raspi-config` — put that in the README.

Output format (STRICT JSON, nothing else):

{
  "framework": "python-gpiozero",
  "target_mcu": "<MPN of the Pi / host part>",
  "summary": "<one short sentence describing what this program does>",
  "files": [
    {"path": "main.py",          "content": "<full file contents>"},
    {"path": "requirements.txt", "content": "<full file contents>"},
    {"path": "README.md",        "content": "<full file contents>"}
  ],
  "readme_path": "README.md"
}

Rules:
  - File paths use forward slashes and are relative to project root.
  - main.py declares every BCM pin as a named constant at the top, with a
    comment naming the peripheral and net.
  - requirements.txt lists exactly the libraries imported (gpiozero, smbus2,
    spidev, adafruit-blinka + adafruit-circuitpython-* as applicable).
  - README.md must include: the host (Pi model / HAT), `raspi-config`
    interfaces to enable, `pip install -r requirements.txt`, how to run, and
    the BCM pin-assignment table.
  - You MAY add small extra modules (e.g. sensors.py) if it helps; keep it
    minimal. main.py under 250 lines is ideal.
"""


# ---- Public API -----------------------------------------------------------


def has_api_key(llm=None) -> bool:
    return bool((llm.api_key if llm is not None else None) or os.environ.get("OPENAI_API_KEY"))


def generate(project: Project, llm=None) -> Firmware:
    """Generate firmware for the project. Raises FirmwareGenerationError on
    any failure mode the user can fix (no API key, no MCU, etc.)."""
    if not has_api_key(llm):
        raise FirmwareGenerationError(
            "OPENAI_API_KEY is not set. Firmware generation needs the LLM."
        )

    mcu = _find_mcu(project)
    if mcu is None:
        raise FirmwareGenerationError(
            "No MCU (or host header) found on the schematic. Add one before "
            "generating firmware."
        )

    tclass = target_class(mcu.mpn)
    target = _target_for(mcu)
    user_msg = _build_context(project, mcu, target)
    system_prompt = (
        LINUX_SBC_SYSTEM_PROMPT if tclass == "linux-sbc" else SYSTEM_PROMPT
    )

    from app.services.agent import _make_openai_client

    try:
        client = _make_openai_client(llm)
    except Exception as exc:  # noqa: BLE001
        raise FirmwareGenerationError(f"OpenAI client init failed: {exc!s}") from exc

    model = llm.model if llm is not None else os.environ.get("OPENAI_MODEL", "gpt-4o")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=8000,
        )
    except Exception as exc:  # noqa: BLE001
        raise FirmwareGenerationError(f"OpenAI API call failed: {exc!s}") from exc

    content = response.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise FirmwareGenerationError(f"LLM returned malformed JSON: {exc}") from exc

    files = _validate_files(data.get("files") or [])
    if not files:
        raise FirmwareGenerationError("LLM returned no files.")

    default_fw = "python-gpiozero" if tclass == "linux-sbc" else "arduino"
    return Firmware(
        framework=str(data.get("framework", default_fw))[:32],
        target_mcu=mcu.mpn,
        summary=str(data.get("summary", ""))[:300],
        files=files,
        readme_path=str(data.get("readme_path", "README.md"))[:200],
        generated_at=datetime.now(timezone.utc).isoformat(),
        model_used=model,
    )


# ---- Internals ------------------------------------------------------------


def _find_mcu(project: Project):
    """Pick the first component classified as 'mcu' in the library. A
    form-factor design (7.3) has no MCU — fall back to its host header
    (HAT ⇒ the Pi underneath, shield ⇒ the Uno underneath)."""
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if part and part.category == "mcu":
            return c
    for c in project.ir.components:
        if c.mpn in HOST_HEADERS:
            return c
    return None


def _target_for(mcu) -> dict[str, str]:
    """The build-target descriptor for an MCU/host component. Baremetal
    targets need a PlatformIO mapping (error if missing); a Linux SBC needs
    none — it's plain Python on the OS."""
    if target_class(mcu.mpn) == "linux-sbc":
        return {
            "class": "linux-sbc",
            "runtime": "Python 3 on Raspberry Pi OS (gpiozero / smbus2 / spidev)",
        }
    target = PLATFORMIO_TARGETS.get(mcu.mpn)
    if target is None:
        raise FirmwareGenerationError(
            f"No PlatformIO mapping for MCU {mcu.mpn!r}. Add it to "
            f"PLATFORMIO_TARGETS in services/firmware.py."
        )
    return {"class": "mcu-baremetal", **target}


def _bcm_label(mcu, pin) -> str | None:
    """BCM GPIO label for a Pi host pin ('GPIO17' → 'BCM 17'), None when the
    pin isn't a GPIO (power/ground/reserved-name pins)."""
    n = pin_safety.gpio_number(pin.name, mcu.mpn)
    return f"BCM {n}" if n is not None else None


def _build_context(project: Project, mcu, target: dict[str, str]) -> str:
    """Compact, structured summary of the schematic for the LLM."""
    mcu_part = library.get_part(mcu.mpn)
    linux_sbc = target.get("class") == "linux-sbc"

    # Map every (refdes, pin_number) to the net it's on.
    pin_to_net: dict[tuple[str, str], str] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            pin_to_net[(r, p)] = net.name

    # MCU's pin assignments — only show pins that ARE connected to a net.
    # For a Linux SBC the code addresses BCM GPIO numbers, not the module's
    # physical pad numbers, so lead with the BCM label there.
    mcu_pins: list[str] = []
    if mcu_part:
        for pin in mcu_part.pins:
            net = pin_to_net.get((mcu.refdes, pin.number))
            if not net:
                continue
            if linux_sbc:
                bcm = _bcm_label(mcu, pin)
                label = bcm or f"{pin.name} (not GPIO)"
                mcu_pins.append(
                    f"  - {label} ({pin.name}, {pin.type}) → net {net}"
                )
            else:
                # Arduino addresses the GPIO NUMBER, not the module's physical
                # package pin. For a known family, parse the GPIO from the pin
                # NAME (ESP32 IO21→21, Pico GP4→4, Uno D13→13); pin.number is
                # the physical pad (ESP32 IO21 = pin 33) and using it wires the
                # wrong GPIO (and GPIO36 is even input-only). Unknown families
                # (STM32 port names) fall back to the physical number as before.
                gid = pin_safety.gpio_number(pin.name, mcu.mpn)
                pin_id = str(gid) if gid is not None else pin.number
                mcu_pins.append(
                    f"  - GPIO {pin_id} ({pin.name}, {pin.type}) → net {net}"
                )

    # List peripherals with their connection summary + firmware-library hint.
    peripherals: list[str] = []
    required_libraries: list[str] = []
    for c in project.ir.components:
        if c.refdes == mcu.refdes:
            continue
        part = library.get_part(c.mpn)
        if part is None:
            continue
        if part.category in {"passive", "led", "diode", "transistor", "crystal", "protection", "mechanical", "switch", "connector"}:
            continue
        # Show the part's pin-to-net map.
        pin_summary = []
        for pin in part.pins:
            net = pin_to_net.get((c.refdes, pin.number))
            if not net:
                continue
            pin_summary.append(f"{pin.name}={net}")
        block = (
            f"  - {c.refdes} ({c.mpn}) [{part.category}]: "
            f"{part.description}\n      pins: {', '.join(pin_summary)}"
        )
        # Curated library tells the LLM exactly which Arduino library to use.
        if part.firmwareLibrary:
            block += f"\n      use library: {part.firmwareLibrary}"
            for entry in part.firmwareLibrary.split(","):
                entry = entry.strip()
                if entry and entry not in required_libraries:
                    required_libraries.append(entry)
        if part.firmwareNotes:
            block += f"\n      notes: {part.firmwareNotes}"
        peripherals.append(block)

    # Identify shared buses by net name.
    i2c_pins = [n.name for n in project.ir.nets if "I2C" in n.name.upper() or n.name.upper() in {"SDA", "SCL"}]
    spi_pins = [n.name for n in project.ir.nets if any(
        sub in n.name.upper() for sub in ("MOSI", "MISO", "SCK", "SCLK", "SPI")
    )]
    uart_pins = [n.name for n in project.ir.nets if any(
        sub in n.name.upper() for sub in ("UART_TX", "UART_RX", "TXD", "RXD")
    )]

    required_libs_block = (
        "\n".join(f"  - {lib}" for lib in required_libraries)
        if required_libraries
        else "  (none — only standard Arduino + Wire/SPI)"
    )

    if linux_sbc:
        target_line = f"Target class: linux-sbc — {target['runtime']}"
        libs_heading = "Driver libraries (Python — declare each in requirements.txt):"
        libs_hint = (
            "  (Python: gpiozero + smbus2/spidev; prefer adafruit-circuitpython-* "
            "drivers via adafruit-blinka where they exist)"
        )
        required_libs_block = libs_hint if not required_libraries else (
            required_libs_block
            + "\n  (these are Arduino lib names — map each to its Python/"
            "CircuitPython equivalent)"
        )
        closing = "Generate the Linux Python project per the system prompt rules."
    else:
        target_line = (
            f"PlatformIO target: platform={target['platform']}, "
            f"board={target['board']}, framework={target['framework']}"
        )
        libs_heading = (
            "REQUIRED lib_deps in platformio.ini (use these exact entries — do NOT\n"
            "substitute different libraries):"
        )
        closing = "Generate the PlatformIO project per the system prompt rules."

    return dedent(
        f"""\
        Project: {project.name}
        Target MCU: {mcu.refdes} = {mcu.mpn}
        {target_line}

        MCU pin assignments (from the schematic — use these EXACTLY):
        {chr(10).join(mcu_pins) if mcu_pins else "  (none — MCU has no nets connected)"}

        Connected peripherals to drive in firmware:
        {chr(10).join(peripherals) if peripherals else "  (none — schematic only has passives)"}

        {libs_heading}
        {required_libs_block}

        Shared buses present:
        - I2C nets: {", ".join(i2c_pins) if i2c_pins else "(none)"}
        - SPI nets: {", ".join(spi_pins) if spi_pins else "(none)"}
        - UART nets: {", ".join(uart_pins) if uart_pins else "(none)"}

        {closing}
        """
    )


SINGLE_FILE_SYSTEM_PROMPT = """\
You are an embedded firmware engineer. The user wants to regenerate ONE file
in an existing PlatformIO project. Same constraints as the full-project
generator: pin numbers come from the schematic IR and must be exact; match the
existing project's framework and library deps; do not change the file path.

Output STRICTLY a JSON object: {"content": "<full new file content>"}
"""


FEATURE_SYSTEM_PROMPT = """\
You are an embedded firmware engineer. The user has an existing PlatformIO
project and wants to ADD a feature (e.g. "add a watch face", "connect to
WiFi and POST sensor data", "implement a sleep mode that wakes on motion").

You receive every current file and the feature description. Output a JSON
object listing only the files you modified or added. Existing files you
didn't touch should NOT be in the output.

Rules:
1. You may modify any existing file or add new ones (e.g. src/feature.cpp,
   include/feature.h). Use forward slashes in paths.
2. DO NOT change the platformio.ini env target / board / framework. You MAY
   add lib_deps if the new feature genuinely needs a new library.
3. Use ONLY the pin numbers and peripherals already on the schematic. If a
   feature would need hardware that isn't there (e.g. WiFi without an ESP32
   on the board, or a buzzer that doesn't exist), refuse — see below.
4. Output the FULL new content of every file you touched. Don't use diffs or
   partial files.
5. Keep main.cpp under ~400 lines. If adding the feature would push it over,
   refactor: extract the feature into its own .cpp/.h pair and #include from
   main.cpp.
6. Add a brief comment block at the top of any new file describing what it
   does and which pins it uses.

Output format (strict JSON, nothing else):
{
  "summary": "<one short sentence describing what you added or changed>",
  "files": [
    {"path": "src/main.cpp", "content": "..."},
    {"path": "src/feature.h", "content": "..."}
  ]
}

If you can't add the feature given the current schematic, return:
{"summary": "Cannot add: <one-line reason>", "files": []}
"""

# Appended to the edit prompts when the target is a Linux SBC (7.4) so the
# Arduino-worded rules read correctly for the Python project shape.
_LINUX_EDIT_NOTE = """

TARGET NOTE: this project is a LINUX SBC Python project (Raspberry Pi —
gpiozero / smbus2 / spidev, BCM pin numbers). There is no platformio.ini and
no C++: where the rules above say platformio.ini, read requirements.txt;
where they say main.cpp / .cpp/.h, read main.py / .py modules. Never emit
C++ or change the project to another language.
"""


def _edit_system_prompt(base: str, target: dict[str, str]) -> str:
    if target.get("class") == "linux-sbc":
        return base + _LINUX_EDIT_NOTE
    return base


def add_feature(project: Project, description: str, llm=None) -> Firmware:
    """Apply a feature request to existing firmware. Returns the updated
    Firmware (NOT mutated in place — caller assigns to project.firmware)."""
    if project.firmware is None:
        raise FirmwareGenerationError(
            "No firmware to extend. Generate firmware first."
        )
    description = description.strip()
    if not description:
        raise FirmwareGenerationError("Feature description is empty.")
    if len(description) > 1000:
        raise FirmwareGenerationError(
            "Feature description is too long (>1000 chars). Tighten it up."
        )
    if not has_api_key(llm):
        raise FirmwareGenerationError("OPENAI_API_KEY is not set.")

    fw = project.firmware
    mcu = _find_mcu(project)
    if mcu is None:
        raise FirmwareGenerationError("No MCU on the schematic.")
    target = _target_for(mcu)

    files_block = "\n\n".join(
        f"=== {f.path} ===\n{f.content}" for f in fw.files
    )
    schematic_block = _build_context(project, mcu, target)

    user_msg = (
        f"Feature request: {description}\n\n"
        f"Current project files:\n{files_block}\n\n"
        f"Schematic context:\n{schematic_block}\n\n"
        f"Modify or add files to implement this feature. "
        f"Output ONLY the changed files in JSON."
    )

    from app.services.agent import _make_openai_client

    try:
        client = _make_openai_client(llm)
    except Exception as exc:  # noqa: BLE001
        raise FirmwareGenerationError(f"OpenAI client init failed: {exc!s}") from exc

    model = llm.model if llm is not None else os.environ.get("OPENAI_MODEL", "gpt-4o")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _edit_system_prompt(FEATURE_SYSTEM_PROMPT, target)},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=12000,
        )
    except Exception as exc:  # noqa: BLE001
        raise FirmwareGenerationError(f"OpenAI API call failed: {exc!s}") from exc

    content = response.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise FirmwareGenerationError(f"LLM returned malformed JSON: {exc}") from exc

    summary = str(data.get("summary", "")).strip()[:300]
    raw_files = data.get("files") or []
    changed_files = _validate_files(raw_files)

    if not changed_files:
        # No file changes — either the LLM said it can't, or it punted.
        msg = summary or "LLM returned no file changes for that feature."
        if msg.lower().startswith("cannot"):
            raise FirmwareGenerationError(msg)
        raise FirmwareGenerationError(
            f"LLM didn't change any files. Reason given: {msg!r}"
        )

    # Merge: replace existing files at these paths, add new ones at the end.
    by_path: dict[str, FirmwareFile] = {f.path: f for f in fw.files}
    for nf in changed_files:
        by_path[nf.path] = nf

    return Firmware(
        framework=fw.framework,
        target_mcu=fw.target_mcu,
        summary=summary or fw.summary,
        files=list(by_path.values()),
        readme_path=fw.readme_path,
        generated_at=datetime.now(timezone.utc).isoformat(),
        model_used=model,
    )


def regenerate_file(
    project: Project, path: str, instructions: str = "", llm=None
) -> str:
    """Re-generate just one file in an existing firmware project. Returns the
    new content. Raises FirmwareGenerationError on any user-fixable failure."""
    if project.firmware is None:
        raise FirmwareGenerationError("Project has no firmware to regenerate.")
    if not has_api_key(llm):
        raise FirmwareGenerationError("OPENAI_API_KEY is not set.")

    fw = project.firmware
    target_file = next((f for f in fw.files if f.path == path), None)
    if target_file is None:
        raise FirmwareGenerationError(f"File {path!r} not in firmware.")

    mcu = _find_mcu(project)
    if mcu is None:
        raise FirmwareGenerationError("No MCU on the schematic.")
    target = _target_for(mcu)

    # Other files give the LLM context about how this file connects to the rest.
    other_files = [
        f"=== {f.path} ===\n{f.content}\n"
        for f in fw.files
        if f.path != path
    ]

    user_msg = (
        f"Regenerate the file: {path}\n"
        f"User instructions: {instructions or '(none — improve / fix / refactor)'}\n\n"
        f"Current content of {path}:\n```\n{target_file.content}\n```\n\n"
        f"Other files in this project (READ-ONLY context, do not modify):\n"
        f"{chr(10).join(other_files) if other_files else '(none)'}\n\n"
        f"Schematic context:\n{_build_context(project, mcu, target)}\n"
    )

    from app.services.agent import _make_openai_client

    try:
        client = _make_openai_client(llm)
    except Exception as exc:  # noqa: BLE001
        raise FirmwareGenerationError(f"OpenAI client init failed: {exc!s}") from exc

    model = llm.model if llm is not None else os.environ.get("OPENAI_MODEL", "gpt-4o")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _edit_system_prompt(SINGLE_FILE_SYSTEM_PROMPT, target)},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=4000,
        )
    except Exception as exc:  # noqa: BLE001
        raise FirmwareGenerationError(f"OpenAI API call failed: {exc!s}") from exc

    content = response.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise FirmwareGenerationError(f"LLM returned malformed JSON: {exc}") from exc

    new_content = data.get("content")
    if not isinstance(new_content, str) or not new_content.strip():
        raise FirmwareGenerationError("LLM returned no content.")
    return new_content


def _validate_files(raw: list) -> list[FirmwareFile]:
    out: list[FirmwareFile] = []
    seen_paths: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip().lstrip("/").replace("\\", "/")
        content = item.get("content")
        if not path or not isinstance(content, str):
            continue
        if path in seen_paths:
            continue
        # Path safety: no '..', no absolute paths, reasonable length.
        if ".." in path.split("/") or len(path) > 200:
            continue
        seen_paths.add(path)
        out.append(FirmwareFile(path=path, content=content))
    return out
