"""MCU pin-safety metadata — Phase 3 of the Suyash-feedback roadmap (#16),
extended for the Week-5 robotics ecosystem (task 7.1).

The reviewer's recurring failure was the agent wiring peripherals onto ESP32
boot-strapping / flash / input-only pins (feedback #5). This module is the
authoritative, deterministic source of per-GPIO capability and risk for the
MCU families in the library. Three consumers read it:

  - ``allocate_pin`` (tools.py, #17) — picks a *safe* pin for a role instead of
    letting the LLM free-choose a GPIO.
  - the verifier (R17, #16) — escalates when a peripheral signal lands on a
    boot-sensitive / flash / input-only / reserved pin anyway.
  - the verifier (R36) — errors when a GPIO of a 3.3 V-only, NOT-5V-tolerant
    family (Raspberry Pi, RP2040, ESP32) is tied directly to a ≥5 V rail.

Families covered: ESP32 / S3 / C3, Raspberry Pi 40-pin GPIO bank (CM4 / CM5 /
Pi SBCs — BCM numbering), RP2040 / RP2350 / Pico, and the Arduino AVR boards
(Uno/Nano 328-class + Mega 2560). Pin *names* differ per family (``IO21``,
``GPIO17``, ``GP26_ADC0``, ``D3_PWM``, ``A4_SDA``, ``ID_SC (BCM2711 GPIO 1)``)
so parsing is family-aware — always pass the MCU's MPN when you have it.

Capability tags (per pin): safe_gpio, strap_pin, boot_sensitive, input_only,
flash, reserved, adc, pwm, i2c, spi, uart.

Public API:
  capabilities(mpn, pin) -> set[str]
  risk(mpn, pin) -> {"level": "high"|"warn", "note": str} | None
  allocate(mpn, role, used, count=1) -> list[str]   # GPIO numbers
  gpio_number(pin, mpn=None) -> int | None          # family-aware parse
  five_v_tolerant(mpn) -> bool | None               # None = unknown family
  role_unavailable_reason(mpn, role) -> str | None  # e.g. ADC on a Pi
  is_supported(mpn) -> bool
"""

from __future__ import annotations

import re
from collections.abc import Callable

# ---------------------------------------------------------------------------
# Family GPIO tables. Keyed by GPIO number in each family's conventional
# numbering space (ESP32/Pi/RP2040: the GPIO number; Arduino: the D-number,
# with A0..An mapped above the digital range). Only the facts that drive a
# decision are encoded; everything output-capable and unencumbered is a
# safe_gpio. Sources: the ESP32/S3/C3 datasheets, the Raspberry Pi 40-pin
# header pinout + HAT specification (ID-EEPROM pins), the RP2040/RP2350
# datasheets, and the Arduino Uno/Nano/Mega pin maps.
# ---------------------------------------------------------------------------


class _Family:
    def __init__(
        self,
        *,
        max_gpio: int,
        boot_sensitive: set[int] = frozenset(),
        strapping: set[int] = frozenset(),
        input_only: set[int] = frozenset(),
        flash: set[int] = frozenset(),
        adc: set[int] = frozenset(),
        notes: dict[int, str] | None = None,
        # Week-5 (7.1) additions:
        reserved: set[int] = frozenset(),  # e.g. Pi ID-EEPROM ID_SD/ID_SC
        i2c: set[int] = frozenset(),
        spi: set[int] = frozenset(),
        uart: set[int] = frozenset(),
        pwm: set[int] | None = None,  # None = any output-capable pin (LEDC/RP2040 slices)
        console: set[int] = frozenset(),  # UART console pins buried in allocation
        console_note: str | None = None,  # set -> console pins also carry a warn risk
        five_v_tolerant: bool = False,
        logic_v: float = 3.3,
        adc_absent_note: str | None = None,
        parse: Callable[[str], int | None] | None = None,
    ):
        self.max_gpio = max_gpio
        self.boot_sensitive = set(boot_sensitive)
        self.strapping = set(strapping)
        self.input_only = set(input_only)
        self.flash = set(flash)
        self.adc = set(adc)
        self.notes = notes or {}
        self.reserved = set(reserved)
        self.i2c = set(i2c)
        self.spi = set(spi)
        self.uart = set(uart)
        self.pwm = set(pwm) if pwm is not None else None
        self.console = set(console)
        self.console_note = console_note
        self.five_v_tolerant = five_v_tolerant
        self.logic_v = logic_v
        self.adc_absent_note = adc_absent_note
        self._parse = parse

    def parse(self, pin: str) -> int | None:
        n = self._parse(pin) if self._parse else _parse_generic(pin)
        if n is None or n < 0 or n > self.max_gpio:
            return None
        return n


_GENERIC_RE = re.compile(r"^\s*(?:GPIO|IO)?\s*0*(\d{1,2})\s*$", re.IGNORECASE)


def _parse_generic(pin: str) -> int | None:
    m = _GENERIC_RE.match(str(pin))
    return int(m.group(1)) if m else None


# --- ESP32 -----------------------------------------------------------------

_ESP32 = _Family(
    max_gpio=39,
    boot_sensitive={0, 2, 12, 15},
    strapping={0, 2, 4, 5, 12, 15},
    input_only={34, 35, 36, 37, 38, 39},
    flash={6, 7, 8, 9, 10, 11},
    adc={32, 33, 34, 35, 36, 37, 38, 39, 0, 2, 4, 12, 13, 14, 15, 25, 26, 27},
    i2c={21, 22},
    spi={18, 19, 23},  # VSPI defaults
    uart={16, 17},  # UART2 (UART0 = console 1/3)
    console={1, 3},
    notes={
        0: "GPIO0 is a boot-mode strapping pin — must be HIGH to boot from flash; pulling it low enters download mode.",
        2: "GPIO2 is a strapping pin — must be LOW or floating at boot; an external pull-up can block boot.",
        12: "GPIO12 (MTDI) sets the flash voltage at boot — a pull-up can brick boot on 3.3 V flash parts.",
        15: "GPIO15 (MTDO) is a strapping pin — pulling it low silences the boot log and changes boot behaviour.",
        5: "GPIO5 is a strapping pin (SDIO timing) — drive carefully at boot.",
        4: "GPIO4 can latch boot state on some modules — verify before use.",
    },
)

_ESP32_S3 = _Family(
    max_gpio=48,
    boot_sensitive={0, 3, 45, 46},
    strapping={0, 3, 45, 46},
    flash={26, 27, 28, 29, 30, 31, 32},  # SPI flash/PSRAM on most S3 modules
    adc=set(range(1, 21)),
    i2c={8, 9},
    spi={11, 12, 13},
    uart={17, 18},
    console={43, 44},
    notes={
        0: "GPIO0 is the S3 boot/download strapping pin.",
        3: "GPIO3 selects the JTAG source at boot (strapping).",
        45: "GPIO45 sets VDD_SPI voltage at boot (strapping).",
        46: "GPIO46 is a strapping pin and input-only at reset.",
    },
)

_ESP32_C3 = _Family(
    max_gpio=21,
    boot_sensitive={2, 8, 9},
    strapping={2, 8, 9},
    flash={11, 12, 13, 14, 15, 16, 17},
    adc=set(range(0, 6)),
    i2c={5, 6},
    spi={2, 7, 10},
    uart={20, 21},
    console={20, 21},
    notes={
        2: "GPIO2 is a C3 boot strapping pin.",
        8: "GPIO8 must be HIGH at boot (strapping) on the C3.",
        9: "GPIO9 is the C3 download-mode strapping pin.",
    },
)

# --- Raspberry Pi 40-pin GPIO bank (CM4 / CM5 / Pi SBCs), BCM numbering ------
# 3.3 V-only I/O, NOT 5 V-tolerant. No on-chip ADC. GPIO0/1 are ID_SD/ID_SC,
# reserved for the HAT ID EEPROM per the HAT spec. GPIO2/3 carry fixed 1.8 kΩ
# pull-ups (I2C1). GPIO14/15 default to the Linux serial console.


def _parse_rpi(pin: str) -> int | None:
    u = str(pin).upper()
    if "ID_SD" in u:
        return 0
    if "ID_SC" in u:
        return 1
    # (?<![A-Za-z0-9]) instead of \b so 'Camera_GPIO' never matches, while a
    # suffixed name like 'GPIO14_TXD0' still parses (underscore is a \w char,
    # which would defeat a trailing \b).
    m = re.search(r"(?<![A-Za-z0-9])GPIO\s*0*(\d{1,2})(?!\d)", u)
    if m:
        return int(m.group(1))
    m = re.match(r"^\s*0*(\d{1,2})\s*$", u)
    return int(m.group(1)) if m else None


_RPI = _Family(
    max_gpio=27,
    adc=set(),
    reserved={0, 1},
    i2c={2, 3},
    spi={7, 8, 9, 10, 11},  # SPI0: CE1, CE0, MISO, MOSI, SCLK
    uart={14, 15},
    pwm={12, 13, 18, 19},  # the hardware PWM0/PWM1 channels
    console={14, 15},
    console_note="is the default Linux serial console (UART0) — reassign the console before using it as GPIO.",
    five_v_tolerant=False,
    logic_v=3.3,
    adc_absent_note=(
        "The Raspberry Pi has no on-chip ADC — add an external ADC "
        "(e.g. ADS1115 on I2C or MCP3008 on SPI) for analog inputs."
    ),
    notes={
        0: "GPIO0 (ID_SD) is reserved for the HAT ID EEPROM — do not use as GPIO.",
        1: "GPIO1 (ID_SC) is reserved for the HAT ID EEPROM — do not use as GPIO.",
        2: "GPIO2 (SDA1) has a fixed 1.8 kΩ pull-up to 3.3 V — fine for I2C, poor for arbitrary GPIO.",
        3: "GPIO3 (SCL1) has a fixed 1.8 kΩ pull-up to 3.3 V — fine for I2C, poor for arbitrary GPIO.",
    },
    parse=_parse_rpi,
)

# --- RP2040 / RP2350 / Pico -------------------------------------------------
# 3.3 V-only I/O, NOT 5 V-tolerant. Every GPIO has a PWM slice; peripherals
# mux widely, so the sets below are the Pico-convention defaults.


def _parse_rp2040(pin: str) -> int | None:
    u = str(pin).upper()
    m = re.match(r"^\s*GP(?:IO)?\s*0*(\d{1,2})", u)
    if m:
        return int(m.group(1))
    m = re.match(r"^\s*0*(\d{1,2})\s*$", u)
    return int(m.group(1)) if m else None


_RP2040 = _Family(
    max_gpio=29,
    adc={26, 27, 28, 29},  # ADC0-3
    i2c={4, 5},  # I2C0 defaults (Pico convention)
    spi={16, 17, 18, 19},  # SPI0: RX, CSn, SCK, TX
    uart={0, 1},  # UART0 defaults
    pwm=None,  # every RP2040/RP2350 GPIO has a PWM slice
    console={0, 1},
    five_v_tolerant=False,
    logic_v=3.3,
    notes={
        29: "GPIO29 monitors VSYS/3 on Pico boards — prefer ADC0-2 (GPIO26-28) for external signals.",
    },
    parse=_parse_rp2040,
)

# --- Arduino AVR, Uno/Nano 328-class (D0-D13, A0-A7 -> 14-21) ----------------
# 5 V logic (5 V-tolerant by definition). D0/D1 are the USB-serial console.

_A_BASE_328 = 14


def _parse_avr328(pin: str) -> int | None:
    u = str(pin).upper().strip()
    m = re.match(r"^D\s*0*(\d{1,2})", u)
    if m:
        return int(m.group(1))
    m = re.match(r"^A\s*0*(\d)", u)
    if m:
        return _A_BASE_328 + int(m.group(1))
    if u.startswith("SDA"):
        return 18  # A4
    if u.startswith("SCL"):
        return 19  # A5
    if u.startswith("RXD"):
        return 0
    if u.startswith("TXD"):
        return 1
    m = re.match(r"^0*(\d{1,2})$", u)
    return int(m.group(1)) if m else None


_AVR_328 = _Family(
    max_gpio=21,
    adc=set(range(14, 22)),  # A0-A7
    input_only={20, 21},  # A6/A7 (ADC6/ADC7) are analog-input-only pins
    i2c={18, 19},  # A4/A5
    spi={10, 11, 12, 13},  # SS, MOSI, MISO, SCK
    uart={0, 1},
    pwm={3, 5, 6, 9, 10, 11},
    console={0, 1},
    console_note="is shared with the USB-serial console — using it breaks upload/Serial.",
    five_v_tolerant=True,
    logic_v=5.0,
    notes={
        13: "D13 drives the onboard LED and is SPI SCK — avoid for inputs with external drivers.",
    },
    parse=_parse_avr328,
)

# --- Arduino Mega 2560 (D0-D53, A0-A15 -> 54-69) -----------------------------


def _parse_avr_mega(pin: str) -> int | None:
    u = str(pin).upper().strip()
    m = re.match(r"^AD\s*0*(\d{1,2})$", u)
    if m:
        return 54 + int(m.group(1))
    m = re.match(r"^A\s*0*(\d{1,2})$", u)
    if m:
        return 54 + int(m.group(1))
    m = re.match(r"^D?\s*0*(\d{1,2})$", u)
    if m:
        return int(m.group(1))
    if u.startswith("SDA"):
        return 20
    if u.startswith("SCL"):
        return 21
    return None


_AVR_MEGA = _Family(
    max_gpio=69,
    adc=set(range(54, 70)),  # A0-A15
    i2c={20, 21},
    spi={50, 51, 52, 53},  # MISO, MOSI, SCK, SS
    uart={0, 1, 14, 15, 16, 17, 18, 19},  # UART0-3
    pwm=set(range(2, 14)) | {44, 45, 46},
    console={0, 1},
    console_note="is shared with the USB-serial console — using it breaks upload/Serial.",
    five_v_tolerant=True,
    logic_v=5.0,
    parse=_parse_avr_mega,
)


# --- STM32 (F1 / F4 / G0 class): ports PA..PH, 16 pins each -------------------
# Numbering space: port letter index * 16 + pin index, so PA0 = 0, PB0 = 16,
# PC13 = 45, PH0 = 112. That keeps every port distinct in one integer space.


def _parse_stm32(pin: str) -> int | None:
    u = str(pin).upper().strip()
    # Library names carry their alternate function as a suffix: "PA13_SWDIO",
    # "PC14/OSC32_IN", "PA11_USBDM". Take the port/index prefix.
    m = re.match(r"^P([A-H])\s*0*(\d{1,2})\b", u)
    if not m:
        return None
    idx = int(m.group(2))
    if idx > 15:
        return None
    return (ord(m.group(1)) - ord("A")) * 16 + idx


def _pp(letter: str, *idx: int) -> set[int]:
    base = (ord(letter) - ord("A")) * 16
    return {base + i for i in idx}


_STM32 = _Family(
    max_gpio=128,
    # SWDIO / SWCLK carry the debug port: taking them costs the only way back
    # into a bricked chip. The HSE oscillator pins are not GPIO on a board with
    # a crystal.
    reserved=_pp("A", 13, 14) | _pp("H", 0, 1),
    # BOOT1 is sampled at reset; USB D-/D+ belong to the USB peripheral.
    boot_sensitive=_pp("B", 2) | _pp("A", 11, 12),
    strapping=_pp("B", 2),
    # ADC1 inputs common to the F4/F1/G0 parts we carry.
    adc=_pp("A", 0, 1, 2, 3, 4, 5, 6, 7) | _pp("B", 0, 1) | _pp("C", 0, 1, 2, 3, 4, 5),
    i2c=_pp("B", 6, 7, 8, 9),
    spi=_pp("A", 4, 5, 6, 7),
    uart=_pp("A", 2, 3, 9, 10),
    pwm=None,  # timer outputs are mapped across most ports
    five_v_tolerant=False,  # analog-capable pins are NOT 5 V tolerant
    logic_v=3.3,
    notes={
        (ord("C") - ord("A")) * 16 + 13: (
            "PC13 sits in the backup domain: limited drive (about 3 mA, 2 MHz) - "
            "fine for an LED or a button, not for a bus."
        ),
        (ord("C") - ord("A")) * 16 + 14: (
            "PC14 is OSC32_IN - using it as GPIO rules out a 32.768 kHz crystal."
        ),
        (ord("C") - ord("A")) * 16 + 15: (
            "PC15 is OSC32_OUT - using it as GPIO rules out a 32.768 kHz crystal."
        ),
    },
    parse=_parse_stm32,
)

# Preferred bus pins per family (datasheet/board-convention defaults), as GPIO
# numbers in the family's numbering space.
_BUS_DEFAULTS = {
    "esp32": {"i2c_sda": 21, "i2c_scl": 22, "uart_tx": 17, "uart_rx": 16,
              "spi_mosi": 23, "spi_miso": 19, "spi_sck": 18},
    "esp32s3": {"i2c_sda": 8, "i2c_scl": 9, "uart_tx": 17, "uart_rx": 18,
                "spi_mosi": 11, "spi_miso": 13, "spi_sck": 12},
    "esp32c3": {"i2c_sda": 5, "i2c_scl": 6, "uart_tx": 21, "uart_rx": 20,
                "spi_mosi": 7, "spi_miso": 2, "spi_sck": 10},
    "rpi": {"i2c_sda": 2, "i2c_scl": 3, "uart_tx": 14, "uart_rx": 15,
            "spi_mosi": 10, "spi_miso": 9, "spi_sck": 11, "spi_cs": 8,
            "pwm": 18},
    "rp2040": {"i2c_sda": 4, "i2c_scl": 5, "uart_tx": 0, "uart_rx": 1,
               "spi_mosi": 19, "spi_miso": 16, "spi_sck": 18, "spi_cs": 17},
    "avr328": {"i2c_sda": 18, "i2c_scl": 19, "uart_tx": 1, "uart_rx": 0,
               "spi_mosi": 11, "spi_miso": 12, "spi_sck": 13, "spi_cs": 10},
    "avr_mega": {"i2c_sda": 20, "i2c_scl": 21, "uart_tx": 1, "uart_rx": 0,
                 "spi_mosi": 51, "spi_miso": 50, "spi_sck": 52, "spi_cs": 53},
    # STM32: I2C1 on PB7/PB6, SPI1 on PA7/PA6/PA5 with PA4 as NSS,
    # USART1 on PA9/PA10 - the datasheet default mappings.
    "stm32": {"i2c_sda": 23, "i2c_scl": 22, "uart_tx": 9, "uart_rx": 10,
              "spi_mosi": 7, "spi_miso": 6, "spi_sck": 5, "spi_cs": 4},
}


def _family(mpn: str) -> tuple[str, _Family] | None:
    u = mpn.upper()
    # RP2040-class first: "RaspberryPi_Pico" contains RASPBERRY too.
    if any(k in u for k in ("RP2040", "RP2350", "PICO")):
        return "rp2040", _RP2040
    # Raspberry Pi SBC / Compute Module / HAT header -> the 40-pin BCM GPIO
    # bank. The HAT header carries the same bank from the *board's* side, so
    # HAT designs get the same capability/risk table (7.2/7.3).
    if (
        "RASPBERRY" in u
        or re.search(r"\bR?PI[_-]?CM\d", u)
        or "BCM27" in u
        or "BCM2712" in u
        or ("HAT" in u and "HEADER" in u)
        or u.startswith("RPI_HAT")
    ):
        return "rpi", _RPI
    if "ARDUINO" in u or "ATMEGA" in u:
        if "MEGA" in u.replace("ATMEGA", "") or "2560" in u:
            return "avr_mega", _AVR_MEGA
        if any(k in u for k in ("328", "32U4", "UNO", "NANO", "SHIELD")):
            return "avr328", _AVR_328
        return None  # other AVRs (ATtiny, 4809...) — no table yet
    if u.startswith("STM32") or "STM32" in u:
        return "stm32", _STM32
    if "ESP32" in u or "ESP-WROOM" in u or "XIAO-ESP32" in u:
        if "S3" in u:
            return "esp32s3", _ESP32_S3
        if "C3" in u:
            return "esp32c3", _ESP32_C3
        # S2 strapping ~ classic for our purposes; H2/C6 fall back to classic-ish.
        return "esp32", _ESP32
    return None


def is_supported(mpn: str) -> bool:
    return _family(mpn) is not None


# Shield/HAT headers whose OFF-BOARD host is the design's actual MCU (7.3 form
# factors). On such a board there is no `category=="mcu"` component — the
# compute lives on the base board and reaches this one through the header, so
# the header's GPIO pins are the logic interface. `_family()` already resolves
# each to the host's family (shield → 5 V AVR, HAT → 3.3 V Pi), so the pin-map
# and the electrical rules can treat the header as an honorary MCU surface.
MCU_HOST_HEADERS = frozenset({"Arduino_Shield_Headers", "RPi_HAT_Header_40P"})


def is_mcu_host_header(mpn: str) -> bool:
    return mpn in MCU_HOST_HEADERS


def gpio_number(pin: str, mpn: str | None = None) -> int | None:
    """Parse a GPIO number from a pin name/number. Family-aware when ``mpn``
    is given ('GP26_ADC0'→26, 'D3_PWM'→3, 'A4_SDA'→18, 'ID_SC (BCM2711 GPIO
    1)'→1); falls back to the generic 'IO19'/'GPIO0'/'19' parse otherwise."""
    if mpn is not None:
        fam = _family(mpn)
        if fam is not None:
            return fam[1].parse(pin)
    return _parse_generic(pin)


def family_key(mpn: str) -> str | None:
    """Family id for an MPN ("esp32" / "avr328" / "rpi" / ...), or None when the
    part is not a supported MCU/host header. Public so rules can look up
    family-level facts (e.g. verifier R49's safe per-GPIO current)."""
    fam = _family(mpn)
    return fam[0] if fam else None


def five_v_tolerant(mpn: str) -> bool | None:
    """Whether this MCU family's GPIOs tolerate 5 V. None = unknown family.
    Raspberry Pi / RP2040 / ESP32 are 3.3 V-only (False); AVR boards run 5 V
    logic (True)."""
    fam = _family(mpn)
    return fam[1].five_v_tolerant if fam else None


def logic_voltage(mpn: str) -> float | None:
    fam = _family(mpn)
    return fam[1].logic_v if fam else None


def capabilities(mpn: str, pin: str) -> set[str]:
    fam = _family(mpn)
    if fam is None:
        return set()
    _, f = fam
    n = f.parse(pin)
    if n is None:
        return set()
    caps: set[str] = set()
    if n in f.boot_sensitive:
        caps.add("boot_sensitive")
    if n in f.strapping:
        caps.add("strap_pin")
    if n in f.input_only:
        caps.add("input_only")
    if n in f.flash:
        caps.add("flash")
    if n in f.reserved:
        caps.add("reserved")
    if n in f.adc:
        caps.add("adc")
    if n in f.i2c:
        caps.add("i2c")
    if n in f.spi:
        caps.add("spi")
    if n in f.uart:
        caps.add("uart")
    if f.pwm is None:
        if n not in f.input_only and n not in f.flash and n not in f.reserved:
            caps.add("pwm")  # LEDC / RP2040 slice can drive any output-capable GPIO
    elif n in f.pwm:
        caps.add("pwm")
    if (
        n not in f.strapping
        and n not in f.flash
        and n not in f.input_only
        and n not in f.reserved
    ):
        caps.add("safe_gpio")
    return caps


def risk(mpn: str, pin: str) -> dict[str, str] | None:
    """A user-facing risk verdict for wiring a signal to this pin, or None."""
    fam = _family(mpn)
    if fam is None:
        return None
    _, f = fam
    n = f.parse(pin)
    if n is None:
        return None
    if n in f.flash:
        return {"level": "high", "note": f"GPIO{n} is wired to the SPI flash — not usable as a GPIO."}
    if n in f.reserved:
        return {"level": "high", "note": f.notes.get(n, f"GPIO{n} is reserved — not usable as a GPIO.")}
    if n in f.boot_sensitive:
        return {"level": "high", "note": f.notes.get(n, f"GPIO{n} is a boot-critical strapping pin.")}
    if n in f.strapping:
        return {"level": "warn", "note": f.notes.get(n, f"GPIO{n} is a strapping pin — drive carefully at boot.")}
    if f.console_note and n in f.console:
        return {"level": "warn", "note": f"Pin {n} {f.console_note}"}
    if n in f.input_only:
        return {"level": "warn", "note": f"GPIO{n} is input-only (no output driver, no internal pull-up)."}
    return None


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------

# Roles that only ever read a signal — input-only pins are fine for these.
_INPUT_ROLES = {"adc", "analog", "input", "sensor_in"}
_PWM_ROLES = {"pwm", "motor_pwm", "servo", "led_pwm"}


def role_unavailable_reason(mpn: str, role: str) -> str | None:
    """A deterministic explanation when a role is physically impossible on
    this family (e.g. any ADC role on a Raspberry Pi), else None."""
    fam = _family(mpn)
    if fam is None:
        return None
    _, f = fam
    if (role or "").lower().strip() in _INPUT_ROLES and not f.adc and f.adc_absent_note:
        return f.adc_absent_note
    return None


def _ordered_safe_pins(family_key: str, f: _Family) -> list[int]:
    """A stable, preference-ordered list of safe output-capable GPIOs. Lower
    numbers that are commonly broken out first; strapping/flash/reserved
    excluded, console pins buried last so we don't steal them."""
    pins = [
        n
        for n in range(0, f.max_gpio + 1)
        if n not in f.strapping
        and n not in f.flash
        and n not in f.input_only
        and n not in f.reserved
    ]
    pins.sort(key=lambda n: (n in f.console, n))
    return pins


def allocate(mpn: str, role: str, used: set[str], count: int = 1) -> list[str]:
    """Return up to ``count`` safe GPIO *numbers* (as strings) for ``role`` on
    this MCU, skipping anything already in ``used`` (pin names or numbers).
    Deterministic: same inputs always yield the same pins, so the agent can't
    drift.

    Unknown MCU families fall back to [] — the caller then lets the agent pick.
    """
    fam = _family(mpn)
    if fam is None:
        return []
    family_key, f = fam
    role_l = (role or "gpio").lower().strip()
    used_nums = {f.parse(str(u)) for u in used}
    used_nums.discard(None)

    out: list[str] = []

    # 1. Named bus role → its board-convention default pin first (if free).
    default = _BUS_DEFAULTS.get(family_key, {}).get(role_l)
    if default is not None and default not in used_nums:
        out.append(str(default))
        used_nums.add(default)

    if role_l in _INPUT_ROLES:
        if family_key.startswith("esp32"):
            # Prefer ADC1 pins (usable with Wi-Fi on); they're often
            # input-only, which is fine for a read-only role.
            adc1 = [n for n in (36, 39, 34, 35, 32, 33) if n in f.adc]
            rest = [n for n in sorted(f.adc) if n not in adc1]
            candidates = adc1 + rest
        else:
            candidates = [n for n in sorted(f.adc) if n not in f.reserved]
    elif role_l in _PWM_ROLES and f.pwm is not None:
        # Families with dedicated hardware-PWM channels (the Pi): those first,
        # then nothing else — software PWM on a Linux SBC is not a safe default.
        candidates = [n for n in sorted(f.pwm) if n not in f.reserved]
    else:
        candidates = _ordered_safe_pins(family_key, f)

    for n in candidates:
        if len(out) >= count:
            break
        if n in used_nums:
            continue
        out.append(str(n))
        used_nums.add(n)

    return out[:count]
