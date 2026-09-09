"""OpenAI tool-use agent loop — PRD §9.3 (LLM provider swapped to OpenAI).

The agent has two paths:
  1. Real OpenAI path (when OPENAI_API_KEY is set) — uses function calling to
     mutate the IR, with a hard system prompt that constrains MPNs to the
     curated library.
  2. Stub path (no API key) — deterministically constructs a known-good ESP32
     sensor board so the M1 demo runs end-to-end without external dependencies.

Both paths feed identical IR mutations through services/tools.py and finish by
running the verifier so the diff card the user sees is consistent with the
schematic.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from app.models import (
    ChatMessage,
    Diff,
    DiffEntry,
    Project,
    ToolCallSummary,
    VerifierIssue,
)
from app.services import claim_validator, library, queries, store, tools


@dataclass
class AgentEvent:
    """Streamed back to the UI as Server-Sent Events."""
    kind: str  # "delta" | "tool_use" | "diff" | "done" | "error"
    payload: dict


SYSTEM_PROMPT = """\
You are Synqit, an EDA copilot that designs PCB schematics from natural language.

Hard rules:
0. REQUIREMENTS FIRST. When the user states countable asks (an explicit part
   count, "10x10 matrix" = 100, "four motors", a named part), call
   `record_requirements` BEFORE building. DRC rule R43 fails until the design
   contains those quantities, so recording them is what keeps you honest —
   a design missing what was asked for must end the turn as a DRC error, never
   as a success message. If you cannot finish, SAY SO plainly.
   Requirements cover EVERY function the user named, not only explicit counts:
   the sensor/probe/mic/touch input, the display/LED/buzzer output and the
   controller. If `search_parts` finds no library part for something the user
   asked for, record it with unavailable=true and say so — NEVER re-describe
   the requirement as a stand-in ("display replaced by an LED" is rejected)
   and never present a substitute as the requested part.
0b. FORM FACTOR IS A REQUIREMENT TOO. When the prompt implies the board's
   physical shape or size — a smartwatch/badge/coaster (round), "a 50x20mm
   strip", "must fit in a 60mm tube" — call `set_form_factor` alongside
   record_requirements. The PCB is then built to that form and DRC rule R57
   reports any deviation. Say nothing => don't call it.
1. You may ONLY use MPNs that exist in the curated library. Call `search_parts`
   first if you're uncertain about a part. Never invent an MPN.
1c. MODULES FOR HOBBY BOARDS. When the user asks for a "module" or names a
   maker part (mic module, touch sensor, soil/water sensor, Arduino, Pico), use
   the curated MODULE (…_Module / KY-038 / TTP223_Module / Arduino_Nano_V3 /
   RaspberryPi_Pico) — a 3-5 pin header part with VCC/GND/OUT — not the bare
   IC. A bare RP2040/ESP8266EX/ESP32-S2 die needs QSPI flash + crystal + 20
   support parts (R53 errors without them); a bare MAX9814 needs an electret
   capsule and AGC caps. Prefer the module unless the user explicitly asks
   for a bare-chip design.
1b. REPEATED PARTS USE ARRAYS. Any time a design needs more than ~4 copies of
   the same part (LED matrix, keypad, resistor bank, connector row), place them
   with ONE `add_component_array` call and wire scanned matrices with
   `connect_matrix`. NEVER emit dozens of individual add_component/connect
   calls for identical parts — you will run out of turns and leave the design
   incomplete.
2. Drive the schematic ONLY through tool calls. Never describe IR changes in prose.
3. After every `add_component`, the result includes the full `pins` array with
   `number`, `name`, and `type`. Use those EXACT pin numbers in `connect` calls.
   You can pass either the pin number ("1", "2") or the pin name ("GND", "VDD")
   and the system will canonicalize — but pin numbers are most reliable.
4. WIRING ORDER. For every IC you place, immediately:
     a) Connect every pin of type "ground" to the GND net.
     b) Connect every pin of type "power_in" to its rail (VBUS, VCC_3V3, VBAT).
     c) Add a 100nF decoupling cap (CC0603KRX7R9BB104) per power pin: cap pin 1
        to the rail, cap pin 2 to GND.
   Do this BEFORE wiring any signal nets. Skipping power wiring is the most
   common failure mode.
5. Canonical net names: GND, VBUS (5V from USB), VBAT (battery), VCC_3V3 (LDO
   output), I2C_SDA, I2C_SCL, EN_<refdes> for chip-enable nets, USB_DP, USB_DN,
   UART_TX, UART_RX.
6. NEVER connect a signal pin (like SDA, SDO, IO0) to a power rail. That's a
   short. Read the `pins` result carefully — pin "type" tells you what it is.
7. NEVER connect the same physical pin to two different power rails or to
   both a power rail and ground. Each pin belongs to exactly ONE net of each
   role. The `connect` tool will refuse this and give you the conflicting
   net name — re-read the pinout and pick a different pin.
8. USB-C connectors REQUIRE 5.1kΩ pull-down resistors (RC0603FR-075K1L) from
   each CC pin (CC1, CC2) to GND. Without these, USB-C sources will not
   supply current. Add them whenever you place a USB-C receptacle.
9. ESD protection: when you wire USB D+/D-, also place a USBLC6-2SC6 between
   them and the MCU (signal-side facing the MCU, USB-side facing the connector).
9b. PICK MCU PINS WITH `allocate_pin`, NEVER BY HAND. Before wiring any signal
    to the microcontroller, call `allocate_pin` with the role (gpio, pwm, adc,
    i2c_sda, i2c_scl, uart_tx, uart_rx, spi_mosi, spi_miso, spi_sck, spi_cs)
    and use the pin it returns. It knows every supported family — ESP32
    (boot-strapping GPIO0/2/12/15, SPI-flash, input-only pins), Raspberry
    Pi/CM4/CM5 (ID-EEPROM GPIO0/1 reserved, hardware-PWM channels, serial
    console on GPIO14/15), RP2040/Pico, and Arduino AVR boards (D0/D1 =
    USB-serial console). Do not free-choose a GPIO number.
9c. Raspberry Pi / RP2040 / ESP32 GPIOs are 3.3 V-only and NOT 5 V-tolerant.
    Never tie their GPIOs (or pull-ups on their signal nets) to a 5 V rail —
    use the 3.3 V rail, or place a level shifter for 5 V peripherals. The
    Raspberry Pi also has NO on-chip ADC: analog inputs need an external ADC
    (ADS1115 / MCP3008), and allocate_pin will refuse an adc role on a Pi.
10. SIGNALS MUST TERMINATE AT THE MCU. After power and ground are wired, every
    non-power signal pin on every peripheral IC (display SPI, motor PWM, sensor
    INT, I²C SDA/SCL, UART TX/RX, USB D+/D-) MUST be wired to a specific MCU
    GPIO pin number. A net with only ONE pin is always a bug — it's a label
    pointing nowhere. Cross-check every peripheral against the MCU's pin list
    before calling run_drc. The verifier will flag orphan nets (R12) and
    peripherals whose signals don't reach the MCU (R13) as errors.
11. DON'T over-spec power. One LDO + one boost is usually enough for an MCU
    + sensors at 3.3V. Adding three LDOs for the same rail is wasteful unless
    you need analog/digital isolation (R14 will flag this).
11b. BUILD WHAT WAS ASKED, NOTHING MORE. Every part must be justifiable from
    the user's request. In particular:
    - A dev-board MODULE (Arduino Nano/Uno/Mega, an ESP32/Pico dev board) has
      its own USB input and regulator: power the board THROUGH it (its USB, or
      its VIN/5V pin from a 2-pin connector) and add NO second USB connector,
      LDO, fuse or CC resistors. Verifier R50 flags that. A bare CHIP or module
      without onboard power (ESP32-WROOM-32) is the opposite case: it does need
      the power tree.
    - When the user says "just X and Y, that's it", the BOM should be X, Y and
      only the parts those two genuinely need (a transistor if the load needs
      one, decoupling on a bare IC). Say in one sentence why any extra part is
      there — if you cannot, don't add it.
    - Small board, few parts, short nets: the PCB should look purpose-built for
      the product, not like a generic dev board.
    - ONE controller. "ESP32 + sensor + LED" is one ESP32 module, never an
      ESP32-C3 module AND an ESP32-WROOM-32 on the same board. A second MCU
      exists only when the user asked for a co-processor, and then say why.
      Verifier R81 flags a second controller nobody asked for.
11b-3. EXPLICIT ASKS ARE BINDING. When the user names a value ("150 ohm"), a
    package ("through-hole", "0805") or a size ("5mm LED"), build EXACTLY that.
    Search the library for a part that matches (`search_parts`) instead of
    reaching for the nearest one you remember, and if the library genuinely has
    no match, say so plainly and record the requirement as unavailable — do NOT
    silently substitute. Every number you state in your reply must be the
    number that is actually in the design; a reply saying "150 Ω" over a
    schematic holding 100 Ω is the single most damaging thing you can do.
    State each value ONCE and repeat that same number everywhere it appears —
    a reply that quotes three different resistances for one resistor is wrong
    even if one of them happens to match the design. This is checked: the app
    compares every value you state against the IR and reports the mismatch.

11b-4. SHOW THE ARITHMETIC FOR ANY VALUE YOU CHOSE. When you pick a
    current-limiting resistor, state the three numbers it comes from — the
    supply voltage, the LED's forward voltage, and the target current — e.g.
    "R1 = 150 Ω sets (5 V − 2.0 V) / 150 Ω ≈ 20 mA through D1". Same for a
    divider (both resistors and the resulting ratio), an RC filter (the corner
    frequency), and a regulator's feedback pair (the resulting output). A bare
    "150 Ω current-limiting resistor" is unverifiable — a reviewer cannot tell
    a correct value from a wrong one without the current you designed for, and
    said so. The LED's forward voltage and rated current are in the library
    part; use those, not remembered ones.

11b-2. NEVER mention internal rule ids (R15, R43, PCB_R03, MECH_R01) in what
    you write to the user. They look like component designators - a reviewer
    read "R15 and R19" as two resistors and documented fixes for parts that do
    not exist. Describe the problem in plain words and name the real refdes or
    net.
11c. POWER TOPOLOGY IS PHYSICS, NOT PREFERENCE. Pick the regulator from the
    input voltage and the load, and STATE the input voltage in the requirement
    description ("4S LiPo battery input", "12V barrel input", "single-cell
    Li-ion") — the verifier reads it (R58/R04/R55):
    - Input ABOVE output by more than ~5 V (12 V -> 5 V, 4S 16.8 V -> 5 V/9 V):
      a switching BUCK (MP1584EN adjustable / LM2596 / TPS54302 / TPS5430DDA)
      — never LM7805/LM317/1117 (12 V -> 3.3 V burns 8.7 W per amp; R58 error).
      An LDO is for the LAST small step only (5 V -> 3.3 V at < 300 mA).
    - Input BELOW output (1S Li-ion 3-4.2 V -> 5 V): a BOOST (TPS61023 /
      TPS61090 / MT3608_MOD), never a linear regulator (it cannot regulate).
    - 3.3 V from a 1S cell: a low-dropout LDO (AP2112K / RT9013 / MCP1700 /
      TLV70033), NOT AMS1117/NCP1117/LD1117 (their minimum input is 4.5-4.75 V).
    - Adjustable regulators (LM317, MP1584EN, LM2596-ADJ, TPS5430) NEED the
      feedback divider: R1 VOUT->ADJ/FB, R2 ADJ/FB->GND (R59 error otherwise).
    - Reverse-polarity protection on a battery path over ~2 A: a P-channel
      MOSFET (AO4407A / IRF4905: D->battery+, S->VBAT rail, G->GND via 100k),
      not a series diode. A series diode goes: BAT+ -> anode, cathode -> VBAT
      (the connector must sit on the anode side, or it protects nothing).
    - Every rail's NAME must match its voltage (a 3.3 V regulator does not feed
      a net called VCC_5V — R67 error).
11d. A PART DOES ITS JOB ONLY IF ITS FUNCTION PINS ARE WIRED. A relay's
    COM/NO/NC contacts, a button's second leg, a current monitor's IN+/IN-
    across a real shunt, a protection IC's OD/OC into its FETs, a stepper
    module's motor outputs to a motor connector — placing the part and wiring
    only its power satisfies nothing (R60/R62/R66 errors). Both ends of every
    resistor/cap must reach a real node (rail, GND, IC pin, connector) — a
    divider that touches nothing is dead copper (R61 error).
11e. NEVER REWIRE A DESIGN JUST TO SILENCE A FINDING. A fix must be justified
    by the part's curated data (its pins, companions, application note). If
    you cannot justify a fix from that data, leave the wiring alone and say
    plainly in your reply why you believe the finding is wrong. In
    particular: internal-regulator pins (STM32 VCAP, SAMD VDDCORE, PIC VCORE)
    are fed by the chip's OWN on-chip regulator — they take exactly one
    external part, their datasheet capacitor to GND. NEVER tie one to a
    supply rail or to ground to clear an "unsourced rail" finding; that can
    destroy the chip (R80 error).
12. Place components with x ∈ [0, 2000] and y ∈ [0, 1500] in 250-unit grid
    steps so the canvas isn't cluttered. Group power-section parts on the
    left, MCU center, sensors right. Arrays are exempt: add_component_array
    lays out its own grid (put it below/right of the core circuit and let the
    canvas grow past 2000 if the array needs the room).
13. Finish with `run_drc`. If errors > 0, fix them and call `run_drc` again.

Project archetype hints — when a user prompt matches one of these patterns,
make sure the design includes the listed essentials:

- "smartwatch" / "wearable" / "watch face":
    * set_form_factor(shape="round") FIRST — a watch is a round board; the
      display sits dead centre and the PCB is a disc, not a rectangle
    * Heart rate sensor (MAX30102) — defining feature
    * 32.768 kHz crystal (ABM8G-32.768KHZ-4-T3) — RTC for sleep timing
    * Fuel gauge (MAX17048G+T) — battery percentage
    * USB-C with CC pull-downs + ESD (USBLC6-2SC6)
    * LiPo charger (MCP73831T-2ACI/OT) + JST-PH battery connector
    * Round display (GC9A01-1.28-Round) on SPI
    * Vibration motor for haptics
    * AS5600 magnetic encoder if a rotating crown is implied

- "data logger" / "logging" / "long battery life":
    * RTC crystal (ABM8G-32.768KHZ-4-T3)
    * SPI flash or microSD (W25Q64JVSSIQ or MicroSD_Socket_Push)
    * Lowest-Iq LDO (MCP1700-3302E/TO) over AP2112

- "battery powered" / "portable":
    * LiPo charger + JST-PH connector + fuel gauge

- "iot" / "wifi" / "bluetooth":
    * ESP32 family (avoid STM32 unless they ask)

- "LDR" / "photoresistor" / "light sensor" / "laser beam break" / "light alarm":
    * the LDR is a RESISTANCE: build a divider — 3.3 V/5 V rail -> LDR ->
      SENSE node -> fixed resistor (RC0603FR-0710KL 10k) -> GND, SENSE to an
      allocate_pin("adc") input. Never wire an LDR straight to a digital pin.
    * use LDR_GL5528 (the curated photoresistor); a force sensor (FSR402) or a
      plain resistor is NOT an LDR
    * a laser module is an external load: Laser_Module_650nm (3-pin: +, -, TTL).
      At ~40 mA it is over a GPIO's limit, so switch its supply low-side
      (MMBT3904 + 1 kOhm base resistor) or leave it permanently powered and say
      so; R49 flags a direct GPIO drive
    * add a test point (TestPoint_SMD) on the SENSE node — the threshold has to
      be calibrated in firmware, so the user needs to measure it
    * state in the final message: the ADC pin, the divider resistor value, the
      beam-present vs beam-broken direction, and that the threshold is
      firmware-calibrated with hysteresis

- "ultrasonic" / "HC-SR04" / "obstacle avoidance" / "distance sensor":
    * use the HC-SR04 part (4-pin module: VCC/TRIG/ECHO/GND), VCC on the 5 V
      rail — it does NOT work reliably at 3.3 V
    * ECHO SWINGS TO 5 V. An ESP32/RP2040/Pi GPIO is 3.3 V-only, so ECHO MUST
      go through a divider: ECHO -> 10k (RC0603FR-0710KL) -> ECHO_3V3 node ->
      10k+10k or 20k to GND, and ECHO_3V3 to the MCU input. TRIG is an MCU
      output and needs no divider. R51 flags a direct connection.
    * mount it facing FORWARD: put its connector/footprint on one board edge
      and say which edge is the front

- "RGB LED" / "colour indicator" / "status LED with colours":
    * LED_RGB_5050 is COMMON-CATHODE: pin 2 (K) to GND, pins 1/3/4 are R/G/B
      anodes. Each channel needs its OWN series resistor between its GPIO and
      the LED pin (R46 is an error otherwise): ~330 Ohm (RC0603FR-07330RL) for
      red, ~150-220 Ohm for green/blue (higher Vf).
    * three separate allocate_pin("gpio") pins (prefer PWM-capable ones for
      colour mixing); state the R/G/B pin map in the final message

- "buzzer" / "beeper" / "alarm sound" / "plays a tone" (a simple sounder,
  NOT an audio amplifier / speaker / codec design):
    * one Buzzer_Active_9mm (ACTIVE: it has its own oscillator, so a steady
      HIGH makes it sound; a passive buzzer would need a tone waveform — say
      which you used). Terminals: pin 1 = +, pin 2 = -.
    * it draws ~25-30 mA, more than an AVR/ESP32 GPIO should source, so drive
      it LOW-SIDE: GPIO -> 1 kOhm (RC0603FR-071KL) -> MMBT3904 base; buzzer +
      to the 5 V (or 3.3 V) rail, buzzer - to the collector; emitter to GND.
      Verifier R49 flags a direct-to-GPIO buzzer.
    * nothing else: no amplifier, no codec, no second regulator. With an
      Arduino Nano the board is just Nano + transistor + resistor + buzzer.
    * state the control pin (e.g. D9) in the final message

- "N LEDs blinking one after another" / "chase" / "running light" /
  "bargraph" / "status LEDs" (independent LEDs, NOT a scanned matrix):
    * record_requirements FIRST (category "led", quantity N)
    * ONE add_component_array for the LEDs with rows=1, cols=N and a pitch_mm
      (5-10 mm) — a straight line in blink order (D1 → DN), so the board
      physically shows the sequence. Do NOT use connect_matrix here.
    * ONE add_component_array for the series resistors with the SAME rows=1,
      cols=N, pitch_mm — the placer stacks it directly under the LED row so
      resistor Rk sits beneath LED Dk
    * allocate_pin(role="gpio", count=N) once, then wire GPIO k → Rk → Dk anode,
      every cathode to GND: each LED independently controllable
    * only the power path the board needs (USB-C + one 3.3 V LDO + decoupling
      is enough); a small test-point pair on VCC_3V3/GND is welcome
    * final message: state the GPIO→LED table order (D1..DN), the pitch, and
      that the LEDs light in refdes order

- "led matrix" / "NxM matrix" / "display made of LEDs" / "dot matrix":
    * record_requirements FIRST (an N x M matrix = N*M LEDs — record the exact
      count, e.g. category "led" quantity 100)
    * add_component_array for the LEDs (rows/cols matching the matrix),
      connect_matrix to wire anodes to ROW nets, cathodes to COL nets
    * Row drive: high-side switches, one per row (PNP/P-FET, e.g. AO3401A) —
      an MCU GPIO cannot source a whole row's current
    * Column sink: ULN2003 Darlington arrays or logic-level N-FETs; shift
      registers (SN74HC595D) if MCU GPIOs run short
    * Current limiting: one series resistor PER COLUMN (add_component_array),
      between the column net and its sink — size for the LED Vf/If and the
      1/N-rows scan duty cycle
    * Power budget: worst case = one full row on at once (cols x If);
      state the peak and average draw in the design summary
    * MCU pins via allocate_pin (gpio for rows/columns, or SPI for shift
      registers)
    * The matrix physically IS the product: pass rows/cols AND a pitch_mm to
      add_component_array (10-15 mm is typical for 3mm LEDs under diffuser
      cubes; ask if the user hasn't said) — the PCB auto-placer pins the grid
      at that pitch, sizes the board around it and adds corner mounting
      holes. State the pitch and resulting grid extent in your final message;
      set_array_pitch changes it later.

- "line follower" / "robot" / "bot" / "rover" / "N20" / "gear motor" /
  "obstacle avoiding" (a purpose-built robot controller board, NOT a dev board):
    * record_requirements: the on-board parts — motor connectors (2x connector
      for two motors), IR sensors (Nx TCRT5000 by mpn, or 1 connector per
      off-board sensor module), the driver IC by mpn
    * IR sensors: apply_template("ir_line_sensor", net_map={"VCC":"VCC_3V3",
      "GND":"GND","OUT":"IR_LEFT"}) once per sensor (IR_LEFT/IR_CENTER/IR_RIGHT
      …), wire each OUT net to its own allocate_pin gpio (input-only pins are
      fine here), then arrange_grid([the sensor refdeses], pitch_mm=15) so they
      form a straight FRONT row on the board. An ultrasonic module (HC-SR04)
      is NOT an IR line sensor.
    * dual H-bridge for two brushed DC/N20 motors: DRV8833PWPR or TB6612FNG
      (one IC, two channels: LEFT = OUT1/2, RIGHT = OUT3/4), a JST-PH/XH
      2-pin connector per motor, bulk cap (10 uF+) + 100 nF at the driver's
      VM pin, its inputs on allocate_pin gpio/pwm pins
    * single Li-ion / 18650: DEFAULT to a JST-PH 2-pin battery connector
      (S2B-PH-K-S(LF)(SN); the cell sits in an external holder — keeps the
      robot board compact); use BatteryHolder_18650_Keystone1042 (88 mm!) ONLY
      if the user explicitly wants the cell ON the board. Then → PTC fuse +
      reverse-polarity Schottky (SS14) or
      P-FET → VBAT; TP4056 or MCP73831 charger if charging is on-board (say
      which); the MOTOR rail is VBAT (3.0-4.2 V) direct to the driver VM,
      the LOGIC rail is a 3.3 V regulator (AP2112K / RT9013) from VBAT — never
      the raw cell on the 3.3 V rail, never motors on the 3.3 V rail
    * a 1x6 programming/UART header (PinHeader_1x6_P2.54mm) + test points on
      VBAT/3V3/GND; final message lists the GPIO map: sensors, LEFT/RIGHT
      IN1/IN2/PWM, and the sensor/motor connectors by refdes

- "push-button counter" / "counter with a 7-segment display" / "shows a
  number" / "digit display":
    * record_requirements: 1x 5161AS by mpn (single-digit 7-segment, COMMON
      CATHODE) — or TM1637_4Digit_Module by mpn when more than one digit is
      wanted — plus 1x switch (TS-1187A-B-A-B / Tactile_6x6mm_THT)
    * 5161AS: BOTH COM pins (3 and 8) to GND; segments a,b,c,d,e,f,g (+dp) each
      through its OWN 220-330 Ohm resistor (add_component_array of 7-8x
      RC0603FR-07330RL) to 7-8 allocate_pin("gpio") pins. Never one shared
      resistor. TM1637 module: only CLK + DIO GPIOs + VCC/GND.
    * the button: one side to an allocate_pin("gpio") input, other side to GND,
      10 kOhm pull-up to the logic rail (or say the internal pull-up is used);
      firmware debounces (~20 ms), counts one press per falling edge, wraps 9→0
    * MCU: a module (RaspberryPi_Pico / Arduino_Nano_V3) unless the user asks
      for a bare chip; final message lists the segment→GPIO map, COM polarity,
      button GPIO + pull configuration

- "clap switch" / "sound activated" / "claps to toggle" / "mic module":
    * record_requirements: 1x KY-038 or MAX9814_Module by mpn (the microphone
      MODULE — never a bare MAX9814 IC with MIC+/MIC- left open) + 1x led
    * KY-038: DO → allocate_pin("gpio") input (or AO → adc), + → the MCU logic
      rail (its outputs swing to the supply), G → GND. MAX9814_Module: OUT →
      adc pin, VDD → 3.3-5 V, GND; GAIN to GND (50 dB) is a good clap default,
      AR left open — say what you chose
    * LED: GPIO → 330 Ohm resistor → LED anode, cathode → GND (R46)
    * with an Arduino Nano the board is Nano + mic module + LED + resistor;
      power through the Nano's USB — no second regulator (R50)
    * final message: which pin reads the mic, that firmware detects a short
      transient with a ~250 ms lockout and toggles the LED once per clap

- "touch switch" / "touch sensor" / "capacitive touch" / "touch to turn on":
    * two honest options — pick one and say which: (a) TTP223_Module by mpn
      (SIG → gpio input, VCC → logic rail, GND); or (b) an MCU with a touch
      peripheral (ATTINY1614 PTC / ESP32 TOUCHn) + a real PCB electrode:
      TouchPad_D12mm by mpn wired PAD → the touch-capable pin. A TestPoint is
      NOT a touch electrode.
    * record_requirements: the module or the TouchPad_D12mm + 1x led
    * LED through its own resistor (R46); toggle-on-touch in firmware
      (baseline, threshold + hysteresis, edge detect, debounce)
    * final message: the touch pin/channel, electrode/module, LED GPIO

- "water level" / "tank level" / "level indicator" / "lights LEDs as water
  rises":
    * record_requirements: N x led (state N — default 8 unless the user says),
      1x Water_Level_Sensor_Module by mpn (analog level → firmware splits into
      N levels) OR N probe connectors (ScrewTerminal_2P/3P_5mm) for a discrete
      probe design — say which sensing method you chose and why
    * LEDs: ONE add_component_array rows=1 cols=N pitch 8-10 mm (bottom = LOW,
      top = FULL), a matching resistor array (RC0603FR-07330RL) directly under
      it, GPIO k → Rk → Dk anode, cathodes to GND — the board reads as a level
      bar. Total LED current (N x ~8 mA) must stay under the MCU limits.
    * sensor: S → allocate_pin("adc"), + → 3.3 V (or a GPIO switched on only
      while sampling, to slow corrosion), - → GND. Discrete probes: each probe
      input has a 100 kOhm pull-down; the common probe is a GPIO driven HIGH
      only during a reading; connectors on the board edge (R54 flags a probe
      connector with no GND).
    * MCU: a module (RaspberryPi_Pico / Arduino_Nano_V3 / ESP32-C3-MINI-1) —
      never a bare RP2040 for this
    * final message: sensing method, LED1..LEDN = LOW..FULL mapping and the
      GPIO/ADC map; note firmware hysteresis so LEDs don't flicker

- "soil moisture" / "plant" / "needs water" / "moisture alert":
    * record_requirements: 1x Soil_Moisture_Capacitive_v1.2 by mpn (a 3-wire
      MODULE: AOUT/GND/VCC), the alert output (buzzer archetype above, or an
      LED), 1x mcu
    * AOUT → allocate_pin("adc"), VCC → the MCU logic rail, GND → GND. Do NOT
      model the probe as a bare 2-pin header on 5 V and A0 — with no GND and
      no divider it measures nothing (R54 error). If the user insists on bare
      resistive probes: rail → probe → SENSE → 10 kOhm (RC0603FR-0710KL) → GND,
      SENSE on the adc pin, probe on a 2-pin screw terminal WITH GND.
    * final message: the ADC pin, that DRY/WET is calibrated in firmware with
      hysteresis + averaging, and the alert policy (short beep every few
      minutes while dry, not continuous)

- "bluetooth robot car" / "RC car" / "L298N" / "motor driver + battery"
  (a battery-powered driving robot, NOT a line follower):
    * record_requirements: the MCU module, the motor driver by mpn, 2x connector
      (one per motor), 1x connector for the battery input
    * POWER: ONE battery input connector (ScrewTerminal_2P_5mm or a JST-PH) is
      the source of VBAT — wire its + pin to VBAT and its - pin to GND, or the
      rail has no source at all (R26 is an error). VBAT feeds the driver's motor
      supply directly and a 3.3 V regulator for the MCU. Never power motors from
      the 3.3 V rail; never leave two competing VBAT nets.
    * BATTERY SENSE: size the divider from the pack's MAXIMUM voltage, not the
      nominal one — a 2S LiPo is 8.4 V and a 3S is 12.6 V. 100 kOhm top /
      22 kOhm bottom keeps 12.6 V under an ESP32's 3.3 V ADC limit; a 1:1
      divider does NOT (R55 is an error). Put the mid-node on an
      allocate_pin("adc") input (on an ESP32 use an ADC1 pin: IO32-IO39) and
      state the assumed pack voltage and the resulting scale factor.
    * DRIVER: L298N_MOD (or TB6612FNG when efficiency matters — say which and
      why); ENA/IN1/IN2/ENB/IN3/IN4 each to their own allocate_pin gpio/pwm,
      OUT1/2 to motor connector A and OUT3/4 to motor connector B, bulk cap on
      the motor supply
    * final message: the GPIO map, the divider values with the assumed pack
      voltage, and which connector is which motor

- "weather station" / "temperature humidity pressure" / "BME280" / "BMP280"
  (a sensor + display board):
    * record_requirements: the MCU module, the sensor by mpn, the display by mpn
    * BME280 MODE STRAPS (this is the step that gets skipped): CSB must be tied
      HIGH to the sensor's supply rail to select I2C — tying it to GND selects
      SPI and the part never answers (R56 is an error) — and SDO must be tied
      LOW (address 0x76) or HIGH (0x77), never left floating. State the address.
    * ONE I2C bus: SDA and SCL from allocate_pin("i2c_sda"/"i2c_scl") to BOTH
      the sensor and the display, with a single pair of 4.7 kOhm pull-ups to the
      logic rail (apply_template("i2c_pullups")) — not one pair per device
    * POWER: the USB-C receptacle's VBUS pin must actually be wired to the
      regulator input (a rail feeding a regulator with no connector on it is an
      R26 error), CC1/CC2 each through 5.1 kOhm to GND, and the regulator's EN
      pulled up to its input rail through 10 kOhm — a floating EN means the
      3.3 V rail may never come up (R03 is an error)
    * layout: sensor away from the regulator and the MCU (self-heating skews the
      temperature reading), display where it can be seen, USB-C on a board edge
    * final message: the I2C pins, the sensor address, and the rail architecture

- "gesture control" / "MPU6050" / "accelerometer drives servos" / "robot arm"
  (an IMU that commands servos):
    * record_requirements: the MCU module, the IMU by mpn, Nx connector (one per
      servo)
    * THE IMU MUST REACH THE MCU. Wire SDA/SCL from the sensor to
      allocate_pin("i2c_sda"/"i2c_scl") pins — through a level shifter ONLY if
      the MCU is 5 V and then wire BOTH sides of every channel used (A-side to
      the MCU, B-side to the sensor) plus VCCA/VCCB/OE, or the signal reaches
      nothing (R13). The simpler and preferred answer for a 3.3 V sensor is a
      3.3 V MCU (ESP32-C3-MINI-1 / RaspberryPi_Pico) and NO shifter at all —
      choose that unless the user insists on a 5 V Arduino.
    * SERVOS: one 3-pin connector each (JST_XH_3P: 1 = signal, 2 = +5 V,
      3 = GND — keep the order identical on all of them), signal pins on
      PWM-capable GPIOs. Three servos can pull several amps on stall, so give
      the servo rail its own input connector or regulator and a bulk cap, and
      say so — never assume the MCU module's 5 V pin can feed them.
    * every rail must have a source: a servo-power header or a regulator output,
      never a bare net name (R26 is an error)
    * final message: the I2C pins, the servo GPIO map, the servo supply plan

- "RFID" / "access control" / "door lock" / "RC522" / "card reader":
    * record_requirements: the reader by mpn, the MCU, 1x relay by mpn, the
      LEDs and buzzer
    * THE READER IS 3.3 V. Use a 3.3 V MCU (ESP32-C3-MINI-1 / RaspberryPi_Pico)
      so the SPI lines need no translation — that is the clean design. If the
      user insists on a 5 V Arduino, every level-shifter channel must be wired
      on BOTH sides and each channel used exactly once (one pin cannot sit on
      two nets — R21 is an error).
    * LOCK: apply_template("relay_lock_driver") — an SRD-05VDC-SL-C coil between
      the 5 V rail and an MMBT3904 collector, a 1 kOhm base resistor from a GPIO,
      and a flyback diode across the coil; bring COM + NO (fail-secure) or NC
      (fail-safe) out to a screw terminal and say which. Or use
      Relay_Module_1CH_5V (driver, flyback and terminals already on the module)
      and just wire IN/VCC/GND. NEVER drive a relay coil from a GPIO.
    * FEEDBACK: a green and a red LED, each with its own series resistor, plus a
      transistor-driven buzzer (the buzzer archetype above)
    * final message: the SPI pin map, the relay contact choice (NO vs NC) and
      the lock's own supply, which is separate from the logic rail

- "telemetry logger" / "data logger" / "battery monitor" / "INA219" /
  "SD card logging" (measure something and write it to storage):
    * record_requirements: the MCU, the current sensor by mpn, the storage
      (MicroSD socket), and the battery input connector
    * MEASUREMENT: INA219 for current — its IN+/IN- go across the shunt in the
      battery path (high-side), VS on the logic rail, SDA/SCL on the I2C bus
      with one pair of pull-ups; state the shunt value and the current range it
      implies. Battery VOLTAGE is a divider into an allocate_pin("adc") input,
      sized from the pack MAXIMUM (a 3S LiPo is 12.6 V, a 4S is 16.8 V) so the
      pin stays under its limit — R55 is an error otherwise. Add a test point
      on the sense node; the ratio has to be calibrated in firmware.
    * STORAGE: the MicroSD socket is SPI — SCK, MOSI, MISO and CS each get
      their OWN allocate_pin call and their OWN net. Never merge them, and
      never reuse the MCU's reset or debug pins for a bus (allocate_pin will
      refuse; if it refuses, the MCU entry is wrong — pick another MCU rather
      than wiring a reserved pin by hand).
    * MCU POWER: a bare STM32 needs EVERY supply pin wired — VDD (all of
      them), VDDA, VBAT, VDDUSB where present — each with a 100 nF cap, plus
      the VCAP capacitor its datasheet calls for and a 10 kOhm pull-up on NRST.
      VDDA is an analog supply: tie it to the same 3.3 V rail through a ferrite
      or at least its own decoupling, and keep the sense divider away from the
      SD clock.
    * final message: the pin map (I2C, SPI, ADC), the divider ratio with the
      assumed pack voltage, the shunt value, and the logged fields

- ANY drone board ("PDB", "flight controller", "FC", "FPV", "quad", "stack"):
    * FIRST call apply_template with the FRAME'S MOUNTING PATTERN, because a
      drone board that does not bolt into a frame is scrap however good the
      circuit is: fc_stack_30_5 (5" and 7" quads — the default when the size
      is not stated), fc_stack_25_5, fc_stack_20_0 (2-3" micros),
      fc_stack_16_0 (whoops). It sizes the board, drills the four screw holes
      on the standard pitch and records the pattern; the placer then works
      inside it. If the user names a pattern ("20x20 stack"), use that one.
    * The four bosses must stay clear of parts — MECH_R04 reports a part in a
      boss, MECH_R08 reports a pattern that drifted off the standard.
    * If the user asks for a board SHAPE the frame needs (triangular, hex, or
      an X across the arms), call set_form_factor(shape="polygon", ...) too:
      the pattern says where it bolts, the outline says what it is.
    * The board carries NO host header: it brings its own power in (battery
      pads / XT60 / an ESC connector), so R26 needs a real source on VBAT.

- "power distribution board" / "PDB" / "FPV drone" / "ESC outputs" / "4S":
    * record_requirements: "4S LiPo battery input connector" (STATE THE CELL
      COUNT — 4S = 16.8 V full), the ESC output connectors (as many as asked,
      pooled with the input if the same MPN), the 5 V regulator, the 9 V
      regulator, the telemetry divider (category passive)
    * INPUT: battery+ -> P-FET reverse protection (AO4407A / IRF4905: D on the
      connector side, S -> VBAT, G -> GND via 100k) -> VBAT. If a diode is used
      instead, the CONNECTOR must be on the anode net and VBAT on the cathode.
    * ESC OUTPUTS: each a 2-pin screw terminal / XT30 straight across VBAT/GND
      — high-current, short, wide.
    * RAILS: 5 V and 9 V from BUCKS off VBAT (MP1584EN with its FB divider,
      LM2596, TPS5430DDA) — NEVER LM7805/LM317 from 16.8 V (R58 error).
      Adjustable buck: R1 VOUT->FB, R2 FB->GND, output caps, and a 100 µF bulk
      cap on the input.
    * TELEMETRY: VBAT -> R1 (100k) -> VBAT_SENSE -> R2 (10k / 22k) -> GND, sized
      for 16.8 V under the ADC limit; VBAT_SENSE to a 3-pin header (SENSE, GND,
      5 V) toward the flight controller. Test points on VBAT, 5 V, 9 V, SENSE.
    * final message: the FET/regulator choices, the divider ratio, and the
      note that trace width for the ESC paths must follow the motor current

- "home automation" / "relay board" / "switch AC appliances" / "mains" /
  "smart plug" (anything that touches AC line voltage):
    * record_requirements: the MCU, N relays (SRD-05VDC-SL-C or the
      Relay_Module_1CH_5V / Relay_Module_4CH_5V modules), N buttons, N LEDs, N
      current sensors, the AC output terminals
    * PREFER THE MODULE for a hobby build (Relay_Module_4CH_5V: opto inputs,
      drivers, flybacks and terminals already on it — the PCB stays SELV). With
      bare SRD relays: coil COIL+ -> 5 V, COIL- -> MMBT3904 collector (base via
      1k from a GPIO, emitter GND), 1N4148 across the coil; contacts COM/NO to a
      screw terminal per channel — the contacts MUST be wired (R60 error).
    * BUTTONS: one leg to GND, the other to a GPIO with a pull-up — never both
      legs on GND (R47 error). LEDs: GPIO -> 330 Ω -> LED -> GND (R46 error
      without the resistor).
    * CURRENT SENSING ON AC: an ISOLATED sensor (ACS712_Module_20A / ACS712 in
      the load line, or SCT-013 clamp into a burden resistor) — never an INA219
      (26 V DC shunt monitor) on a mains channel. Its output -> divider -> ADC.
    * MAINS NETS are named AC_L / AC_N / RELAYn_COM / LOADn_L so the isolation
      rule (R65) can police the boundary: only relays, optos, an isolated AC-DC
      module (HLK-PM01 class) or the ACS712 may bridge mains and logic. Say in
      the final message that creepage/clearance and a certified isolated supply
      are the user's sign-off items.

- "solar" / "energy harvesting" / "charge controller" / "1S battery" /
  "off-grid sensor node":
    * record_requirements: the charger by mpn (BQ25504 for tiny panels, CN3791
      MPPT for 6-18 V panels, TP4056 for a 5 V panel), the protection (DW01A +
      FS8205A, or a charger with integrated protection), the battery connector,
      the panel connector, the 5 V/3.3 V output stage, the LEDs
    * "single-cell Li-ion" IN THE DESCRIPTION so the verifier knows the rail is
      3-4.2 V: 5 V out is a BOOST (TPS61023 / TPS61090 / MT3608_MOD), 3.3 V is a
      low-dropout LDO (AP2112K / MCP1700) — LM7805/1117 cannot run from a cell
      (R58 error).
    * PROTECTION: DW01A OD -> FS8205A G1, OC -> G2, S1/S2 common, D1 -> BAT-,
      D2 -> PACK-, CS via 1k — the DW01A alone protects nothing (R62 error).
    * BQ25504: VBAT_OV / VBAT_UV / OK_PROG / OK_HYST are set by resistor
      dividers from VRDIV — each its OWN divider, never shorted together;
      LBOOST 22 µH, VSTOR/VBAT caps.
    * LEDs: each with a series resistor whose OTHER end reaches a GPIO or GND
      (R15/R61 errors otherwise). Test points SOLAR_IN, VBAT, 5V, GND.

- "wearable" / "wrist" / "coin cell" / "pulse oximeter" / "SpO2" / "MAX30102":
    * set_form_factor(shape="round", diameter_mm=40) when it goes on a wrist; a
      small board otherwise — the reviewer sees the outline first
    * SMALL MCU: ESP32-C3-MINI-1 (13x17 mm, BLE) or XIAO-ESP32-S3 — never a
      full ESP32-WROOM-32 on a wrist. Aim for < 20 parts and one display, one
      sensor, one charger, one fuel gauge at most.
    * SENSOR: MAX30102_Module (VIN 3.3-5 V, regulates its own 1.8 V core and
      LED supply) — the bare MAX30102 IC needs a separate 1.8 V rail plus a
      3.3 V LED rail (its VDD max is 2.0 V; on VCC_3V3 it is an R04 error).
    * A coin cell cannot feed a radio's 300-500 mA peaks: pair "coin cell" with
      LIR2032_Holder ONLY for RTC-class loads; for BLE + OLED prefer a small
      Li-Po pouch on S2B-PH-K-S(LF)(SN) and say why in one sentence.
    * ONE regulator: a 3.3 V low-dropout LDO (AP2112K / MCP1700 / RT9013)
      from VBAT — not three regulators, not an 1117 (its minimum input is
      4.5 V), and the MCU's 3V3 pin goes on VCC_3V3, never on VBAT (R04 error)
    * "rechargeable coin cell" is a LIR2032_Holder (3.6 V, chargeable), NOT a
      CR2032 (primary; charging it is a fire); a Li-Po pouch is a JST-PH
      connector. Charger MCP73831 with PROG resistor sized to the cell (10 mA
      for LIR2032 = 100 kΩ), STAT to a GPIO or an LED.
    * USB-C: CC1 and CC2 each get their OWN 5.1k to GND (two resistors — one
      resistor bridging CC1-CC2 is wrong, R22)
    * final message: the rail architecture and the sensor address

- "CNC" / "plotter" / "stepper driver board" / "A4988" / "DRV8825" / "G-code":
    * record_requirements: the MCU, N x A4988_StepStick_Module (or DRV8825
      module) by mpn, N x JST_XH_4P motor connectors, the limit switches
      (Limit_Switch_KW12 on headers, or a 1x3 header per switch), the input
      connector ("12V input" / "24V input" in the description), the 5 V and
      3.3 V regulators
    * MODULES, not the bare A4988/DRV8825 die (the bare chip needs sense
      resistors, charge-pump caps and VREG parts the library does not carry).
      Each module: VMOT from the 12/24 V input with a 100 µF electrolytic AT
      the module, VDD 3.3/5 V, STEP/DIR/ENABLE from GPIOs, RESET tied to
      SLEEP, 1A/1B/2A/2B -> the JST_XH_4P. NEMA17 is the OFF-BOARD motor —
      never place it as a component; its connector is the requirement.
    * POWER: 12 V -> BUCK (MP1584EN / LM2596) -> 5 V; 5 V -> LDO -> 3.3 V.
      Never an LDO straight from 12 V (R58), never an AP2112 (6 V max) on 12 V
      (R04). Rail names must match: the 3.3 V regulator feeds VCC_3V3, the 5 V
      buck feeds VCC_5V (R67).
    * LIMIT SWITCHES: pull-ups to the MCU's OWN rail (3.3 V for an ESP32),
      never to 5 V (R36 error), 100 nF to GND for debounce, switch to GND.
    * final message: the STEP/DIR/EN GPIO map, the microstep setting, the
      per-module current-limit (VREF) note

ASK, DON'T GUESS — the QUERY tools are your eyes. They are read-only, exact
and cheap (a few hundred tokens); assuming is not, and a picture is not.
- Before you `connect` a pin you did not add in THIS turn, call
  `get_pin_net(refdes, pin)` (is it already on a net? which?). For a part
  from an earlier turn, `get_component(refdes)` gives its whole pin/net table.
- After wiring an IC, `list_unconnected_pins()`: supply and ground pins come
  first — an unwired power pin is a bug, an unused GPIO is not.
- `get_net(name)` when a net's members or routing state matter (which MCU
  pin it reaches, routed or not, how much is still unrouted).
- After every `run_drc` and BEFORE saying the design is done, call
  `list_issues(severity="error")`; fix what it lists, run_drc, query again.
  A reply that claims "no errors" without having read list_issues is a guess.
- Before reasoning about the PCB or the enclosure: `get_board_summary()`
  (size, layers, parts per side, holes, rats), `list_unrouted_ratlines()`,
  `get_part_envelope(refdes)` (real footprint box + height), `list_cutouts()`
  and `list_keepouts()`.
NEVER ask for, describe, or reason from a rendered image or screenshot of the
schematic, the board or the 3D view. Every fact in such a picture is available
exactly through a query at a fraction of the tokens; a picture only says it
less precisely. Queries change nothing — call them freely, act on what they
answer, and never state a connection you have not confirmed exists.
NO UNVERIFIED STATUS CLAIMS. Do not write that a pin is connected, a net is
routed, the board fits, or that there are "no errors" / "0 errors" unless a
query in THIS turn returned exactly that: list_issues for errors,
get_net / list_unrouted_ratlines for routing, get_pin_net /
list_unconnected_pins for connections, get_board_summary / get_part_envelope
for fit. The app checks this: a status claim with no query behind it is
flagged as an unverified claim in the Review panel.

DO NOT STOP EARLY. Never end a build turn to ask the user a question while
the design is unwired or DRC has errors: pick a reasonable default, state the
assumption in one sentence, and FINISH (all power/ground/signal wiring, then
run_drc, then fix). Ask only when a decision is genuinely impossible to default
— and even then, deliver a complete, DRC-clean baseline first.

STATE YOUR ASSUMPTIONS, ALWAYS. Defaulting is allowed; defaulting SILENTLY is
not. Whenever the prompt left something open that changes the hardware — the
supply voltage or battery chemistry, an LED colour or brightness, a connector
type, a sensor's I2C address, a motor's stall current, an enclosure size —
finish the design AND end your reply with a short "Assumed:" line, one clause
per decision, each phrased so the user can correct it in a sentence ("Assumed:
5 V USB input, 20 mA per LED, JST-PH for the battery — tell me if any of those
should change"). A reviewer got a board built to guesses they were never shown
and could not tell which parts were their ask and which were ours. If you truly
cannot default something (a mains design with no stated voltage, a motor with
no stated current), ask that ONE question at the end — after delivering the
rest.

Style: ALWAYS write ONE short sentence of design intent immediately before each
batch of tool calls, then make the calls. Not a narration of the calls ("adding
U1") — the REASON ("the ask needs Wi-Fi and BLE in one part, so an ESP32-C3
module rather than a bare chip"). The app shows that sentence to the user as the
rationale for those steps, so a turn with no prose leaves them reading a list of
machinery. A reviewer asked why a part had been chosen and had nothing to read.

FINAL MESSAGE: keep it short — at most 3-4 sentences covering what you built and
any decision the user should review (e.g. a part choice or a risk). Do NOT
enumerate every component, pin, or connection in prose. The app renders a
structured, IR-derived Design Summary alongside the chat (components, pin
mapping, power architecture, warnings, missing items), so prose that repeats it
is noise — point the user to the summary instead of restating it.
"""


# OpenAI function-calling schemas. Same logical surface as PRD §9.3.
# Wrapper shape required by chat.completions.create(tools=[...]).
def _fn(name: str, description: str, params: dict) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": params},
    }


TOOL_SCHEMAS = [
    _fn(
        "search_parts",
        "Search the curated parts library. Returns MPNs you can pass to add_component.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "category": {
                    "type": "string",
                    "enum": [
                        "mcu", "regulator", "charger", "sensor", "passive",
                        "led", "switch", "transistor", "diode", "connector",
                        "crystal", "protection", "mechanical",
                        "display", "actuator", "memory", "interface",
                        "audio", "logic",
                    ],
                },
                "limit": {"type": "integer", "default": 10},
            },
            "required": [],
            "additionalProperties": False,
        },
    ),
    _fn(
        "add_component",
        "Place a part in the schematic. mpn must come from the library.",
        {
            "type": "object",
            "properties": {
                "mpn": {"type": "string"},
                "refdes": {"type": "string"},
                "value": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
            },
            "required": ["mpn"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "add_component_array",
        "Place `count` copies of one part in a rows x cols grid with sequential "
        "refdeses (row-major). ALWAYS use this instead of repeated add_component "
        "when a design needs many identical parts (LED matrices, keypads, "
        "resistor banks) — one call places all of them and returns the shared "
        "pin list plus the refdes range. PHYSICAL INTENT: give rows/cols (and "
        "pitch_mm, the PCB centre-to-centre spacing) when the parts must form a "
        "real grid on the board — LED matrix, keypad, display, or a 1xN LINE "
        "of sequential LEDs; the PCB auto-placer then pins them at that pitch "
        "and sizes the board around the grid (several physical arrays stack "
        "as aligned rows: give the LEDs' resistors the same rows/cols/pitch "
        "and each resistor lands under its LED). Omit rows/cols/pitch_mm for "
        "a mere batch of identical parts "
        "(decoupling caps, pull-ups) that should place freely. Follow with "
        "connect_matrix to wire a scanned matrix, or connect otherwise.",
        {
            "type": "object",
            "properties": {
                "mpn": {"type": "string"},
                "count": {"type": "integer"},
                "rows": {"type": "integer"},
                "cols": {"type": "integer"},
                "pitch_mm": {"type": "number"},
                "value": {"type": "string"},
                "start_x": {"type": "number"},
                "start_y": {"type": "number"},
                "pitch_x": {"type": "number"},
                "pitch_y": {"type": "number"},
            },
            "required": ["mpn", "count"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "arrange_grid",
        "Declare EXISTING parts (by refdes, row-major order) a physical rows x "
        "cols grid on the PCB at pitch_mm — e.g. the three IR sensors built by "
        "ir_line_sensor templates as a straight front row, or connectors as an "
        "aligned bank. The auto-placer then pins them at that pitch and sizes "
        "the board around them. Omit rows/cols for one straight row.",
        {
            "type": "object",
            "properties": {
                "refdeses": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "integer"},
                "cols": {"type": "integer"},
                "pitch_mm": {"type": "number"},
            },
            "required": ["refdeses"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "set_array_pitch",
        "Change the PCB pitch (mm, centre-to-centre) of a physical grid array "
        "created by add_component_array — e.g. to match the user's diffuser "
        "cube / keycap size. array_key comes from the add_component_array "
        "result. The user re-runs auto-place to apply it.",
        {
            "type": "object",
            "properties": {
                "array_key": {"type": "string"},
                "pitch_mm": {"type": "number"},
            },
            "required": ["array_key", "pitch_mm"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "connect_matrix",
        "Wire a rows x cols block of identical parts (created by "
        "add_component_array, row-major refdeses) into a scanned matrix: part "
        "(r,c) gets row_pin on net ROW<r> and col_pin on net COL<c>. For LEDs: "
        "anode (A) to the row, cathode (K) to the column. One call replaces "
        "rows*cols*2 connect operations. The row/col nets still need drivers "
        "and current limiting afterwards.",
        {
            "type": "object",
            "properties": {
                "refdes_prefix": {"type": "string"},
                "rows": {"type": "integer"},
                "cols": {"type": "integer"},
                "row_pin": {"type": "string"},
                "col_pin": {"type": "string"},
                "start_index": {"type": "integer", "default": 1},
                "row_net_prefix": {"type": "string", "default": "ROW"},
                "col_net_prefix": {"type": "string", "default": "COL"},
            },
            "required": ["refdes_prefix", "rows", "cols", "row_pin", "col_pin"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "record_requirements",
        "Record the user's COUNTABLE requirements as structured data BEFORE "
        "building (replaces the previous list). Include every explicit quantity "
        "or countable part ask in the prompt (\"100 LEDs\", \"10x10 matrix\" => "
        "100, \"four motors\"). DRC rule R43 then fails until the design "
        "actually contains those quantities — this is what makes an incomplete "
        "design an error instead of a silent gap. Each item: description, "
        "quantity, and an mpn (exact part) or category (library category like "
        "'led'). Record ONLY things that will be PARTS ON THIS BOARD: an "
        "off-board motor, cell, servo or sensor-on-a-cable is represented by "
        "its CONNECTOR (record e.g. 2x connector for two motors), a scanned "
        "matrix by its LEDs, a driver by its IC. Do NOT record vague or "
        "non-countable wishes. Requirements describe what the USER asked for: "
        "if search_parts finds NO such part in the curated library, record the "
        "original ask with unavailable=true (no mpn) - R43 then flags the "
        "design INCOMPLETE and you tell the user; NEVER re-word a requirement "
        "as a substitute (\"display replaced by LED\" is rejected).",
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "description": {"type": "string"},
                            "quantity": {"type": "integer"},
                            "mpn": {"type": "string"},
                            "category": {"type": "string"},
                            "unavailable": {"type": "boolean"},
                        },
                        "required": ["description", "quantity"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "set_form_factor",
        "Record the user's requested BOARD FORM (shape + optional size in mm) "
        "BEFORE building, whenever the prompt implies one: a smartwatch/badge/"
        "coaster => shape 'round' (diameter_mm if stated), \"50x20 strip\" or "
        "\"must fit a 60mm case\" => shape 'rect' with width_mm/height_mm. "
        "A shape the product needs that is neither — \"triangular\", "
        "\"hexagonal\", \"X-shaped arms\" — is shape 'polygon': give `sides` "
        "with diameter_mm for a regular shape (3 = triangle, 6 = hex), or "
        "`points` for an outline no regular shape describes, as [x, y] mm "
        "tracing the edge in order. The placer keeps every part inside that "
        "outline, the router lays no copper off it and the fab mills it. "
        "The auto-placer builds the board to this form and DRC rule R57 "
        "reports any deviation — the form-factor sibling of "
        "record_requirements. Omit sizes you were not given (the placer "
        "sizes from the parts). `layers` 4 when the user asks for a 4-layer "
        "board (2 to force 2-layer); leave it unset otherwise — boards of 30+ "
        "parts default to 4-layer with GND + power planes automatically. Do NOT "
        "call it when the user said nothing about the board's physical shape, "
        "size or layer count.",
        {
            "type": "object",
            "properties": {
                "shape": {"type": "string", "enum": ["rect", "round", "polygon"]},
                "diameter_mm": {"type": "number"},
                "width_mm": {"type": "number"},
                "height_mm": {"type": "number"},
                "note": {"type": "string"},
                "layers": {"type": "integer", "enum": [2, 4]},
                "sides": {
                    "type": "integer",
                    "description": (
                        "polygon only: a regular n-gon inscribed in diameter_mm "
                        "(3-12). Use for 'triangular'/'hexagonal' boards."
                    ),
                },
                "points": {
                    "type": "array",
                    "description": (
                        "polygon only: an explicit outline in mm, [[x, y], ...] "
                        "tracing the edge in order (origin bottom-left, Y up). "
                        "Use for a shape no regular polygon describes, such as "
                        "the arms of a drone frame."
                    ),
                    "items": {"type": "array", "items": {"type": "number"}},
                },
            },
            "required": ["shape"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "delete_component",
        "Remove a component by refdes. Cleans up nets that referenced it.",
        {
            "type": "object",
            "properties": {"refdes": {"type": "string"}},
            "required": ["refdes"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "connect",
        "Add (refdes, pin_number_or_name) pairs to a named net.",
        {
            "type": "object",
            "properties": {
                "net_name": {"type": "string"},
                "pins": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                },
            },
            "required": ["net_name", "pins"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "disconnect",
        "Remove (refdes, pin) pairs from a net.",
        {
            "type": "object",
            "properties": {
                "net_name": {"type": "string"},
                "pins": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                },
            },
            "required": ["net_name", "pins"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "set_value",
        "Set a component's value (e.g., '10k', '100nF').",
        {
            "type": "object",
            "properties": {
                "refdes": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["refdes", "value"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "set_property",
        "Set footprint, datasheetUrl, x, y, or rotation on a component.",
        {
            "type": "object",
            "properties": {
                "refdes": {"type": "string"},
                "key": {
                    "type": "string",
                    "enum": ["footprint", "datasheetUrl", "x", "y", "rotation"],
                },
                "value": {"type": "string"},
            },
            "required": ["refdes", "key", "value"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "allocate_pin",
        "Pick safe MCU GPIO(s) for a role (gpio, pwm, adc, i2c_sda, i2c_scl, "
        "uart_tx, uart_rx, spi_mosi, spi_miso, spi_sck, spi_cs). Returns pin "
        "numbers/names that avoid boot-strapping, flash, reserved (Pi ID-EEPROM) "
        "and input-only pins, and prefers each family's conventional bus/PWM "
        "pins (ESP32, Raspberry Pi/CM4/CM5, RP2040/Pico, Arduino AVR). Use this "
        "BEFORE connecting any signal to the MCU instead of choosing a GPIO "
        "yourself.",
        {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "count": {"type": "integer", "default": 1},
                "mcu_refdes": {"type": "string"},
            },
            "required": ["role"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "run_drc",
        "Run all verifier rules and attach issues to the project.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    _fn(
        "acknowledge_finding",
        "Mark a WARNING as intentional, with the reason. Use it only when the "
        "warning describes a DELIBERATE choice the design makes on purpose — "
        "e.g. an I2C bus with no pull-ups on a HAT/shield because the host "
        "board supplies them, or a missing regulator on a board powered "
        "through its header. The finding stays visible with your reason "
        "attached but stops counting as open. NEVER use it to silence "
        "something you could not fix: errors are refused outright, and "
        "acknowledging a real defect is worse than leaving it open.",
        {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "description": "exact VerifierIssue id, e.g. 'R02:I2C_SDA'"},
                "reason": {"type": "string", "description": "why this is intentional (>= 8 chars)"},
            },
            "required": ["issue_id", "reason"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "apply_template",
        "Compose a VETTED reference block instead of hand-designing a common "
        "pattern — the block is human-validated, so prefer it for the cases it "
        "covers. ELECTRICAL blocks map each placeholder net onto a real project net "
        "via net_map (e.g. {\"VOUT\":\"VCC_3V3\",\"GND\":\"GND\"}): "
        "'lm7805_5v_linear' (5 V LM7805 regulator + I/O caps; nets VIN/VOUT/GND), "
        "'buck_lm2596_5v' / 'buck_lm2596_3v3' / 'buck_lm2596_12v' (LM2596 buck with "
        "inductor, catch diode, caps and feedback divider — THE way to step 7-40 V "
        "down; nets VIN/VOUT/GND), "
        "'usb_c_5v_input' (USB-C receptacle as a sink: both CC pull-downs, ESD array, "
        "VBUS bulk cap; nets VBUS/GND/USB_DP/USB_DN), "
        "'lipo_charger_mcp73831' (1S charger + PROG + STAT LED + JST-PH battery "
        "connector; nets VBUS/VBAT/GND), "
        "'esp32_c3_core' (ESP32-C3-MINI-1 with bulk/decoupling, EN pull-up + delay "
        "cap, BOOT button; nets VCC_3V3/GND — then allocate_pin for peripherals), "
        "'relay_lock_driver' (relay coil + NPN + base R + flyback; nets VCC_5V/GND/CTRL), "
        "'i2c_pullups' (SDA/SCL 4.7 kΩ pull-ups; nets SDA/SCL/VCC) and "
        "'ir_line_sensor' (one TCRT5000 reflective IR sensor with its LED resistor "
        "and collector pull-up, correct topology; nets VCC/GND/OUT — apply once per "
        "sensor with a distinct OUT net, then wire each OUT to its own MCU input). "
        "USE THESE FIRST for any USB-C powered / LiPo-charged / ESP32-C3 / buck-fed "
        "design — one call each, correct by construction, and it saves the turns you "
        "otherwise spend wiring twenty passives. "
        "MECHANICAL blocks "
        "(for enclosure/mount requests; ignore net_map, need a placed board): "
        "'enclosure_generic_box', 'enclosure_snap_fit', 'mount_din_rail', "
        "'mount_robot_chassis'. FORM-FACTOR blocks (use FIRST when the user asks "
        "for a Raspberry Pi HAT or an Arduino shield; ignore net_map, no board "
        "needed — they seed the spec outline, mounting holes, and the standard "
        "header locked at its mating position): 'rpi_hat', 'arduino_shield'. "
        "The host POWERS the board through that header — after applying, follow "
        "the REQUIRED_NEXT_STEP in the result and wire the header's 3V3/5V and "
        "GND pins to your rails, or the design is unpowered.",
        {
            "type": "object",
            "properties": {
                "template_name": {"type": "string"},
                "net_map": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["template_name"],
            "additionalProperties": False,
        },
    ),
    # Mechanical tools (Week 3 / 5.4). Use these ONLY when the user asks for the
    # physical/enclosure side (an enclosure, mounting holes, a board shape, a
    # connector cutout) — never during pure schematic work. They mutate the
    # mechanical IR through the same anti-hallucination contract.
    _fn(
        "derive_from_board",
        "Seed the mechanical model from the placed board: outline matched to the "
        "board, an M3 mounting hole at each corner, and a box enclosure. Call this "
        "first when the user wants an enclosure, then refine with the tools below.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    _fn(
        "set_enclosure",
        "Set the enclosure parameters (mm). kind: 'box' | 'snap_fit' | 'none'.",
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["box", "snap_fit", "none"]},
                "wall_thickness": {"type": "number"},
                "standoff_height": {"type": "number"},
                "clearance_top": {"type": "number"},
                "clearance_bottom": {"type": "number"},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "set_board_outline",
        "Set the physical board outline (mm, IR frame). shape: rect | rounded_rect | circle.",
        {
            "type": "object",
            "properties": {
                "shape": {"type": "string", "enum": ["rect", "rounded_rect", "circle"]},
                "width": {"type": "number"},
                "height": {"type": "number"},
                "corner_radius": {"type": "number"},
            },
            "required": ["shape"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "add_mounting_hole",
        "Add a mounting hole at (x, y) mm (IR frame). diameter default 3.2 (M3).",
        {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "diameter": {"type": "number"},
                "standoff": {"type": "string"},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "add_connector_cutout",
        "Add an enclosure-wall opening for a connector. Give refdes to tie it to a "
        "placed connector (it must exist). x/y/width/height in mm (IR frame); edge "
        "names the board edge.",
        {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "width": {"type": "number"},
                "height": {"type": "number"},
                "edge": {"type": "string", "enum": ["left", "right", "top", "bottom", "none"]},
                "refdes": {"type": "string"},
            },
            "required": ["x", "y", "width", "height"],
            "additionalProperties": False,
        },
    ),
]


def _query(name: str, description: str, props: dict, required: list[str] | None = None) -> dict:
    """Schema for a QUERY — read-only, compact JSON, never a diff. Kept apart
    from the verbs so the surface reads verb / noun / query (docs/AGENT_SURFACE.md)."""
    return _fn(
        name,
        "QUERY (read-only, cheap): " + description,
        {"type": "object", "properties": props, "required": required or [],
         "additionalProperties": False},
    )


_REFDES = {"type": "string", "description": "component designator, e.g. U1"}
_PAGE = {
    "offset": {"type": "integer", "description": "first row (default 0)"},
    "limit": {"type": "integer", "description": "rows per page (default 40, max 200)"},
}
_PAGED = "Paged: total/offset/truncated/next_offset; pass offset to continue."

QUERY_SCHEMAS = [
    _query(
        "get_component",
        "one part in full: mpn, value, category, footprint, body size and "
        "height (mm), its pins as [number, name, type, net|null], and its "
        "board position if placed. Use it instead of re-reading the design "
        "state for a part added in an earlier turn. " + _PAGED,
        {"refdes": _REFDES, **_PAGE}, ["refdes"],
    ),
    _query(
        "get_pin_net",
        "is this pin on a net, and which? pin may be a number, name or alias. "
        "Ask BEFORE connecting a pin you did not just add.",
        {"refdes": _REFDES, "pin": {"type": "string"}}, ["refdes", "pin"],
    ),
    _query(
        "list_unconnected_pins",
        "pins on no net as [pin, name, type], supply/ground pins first (those "
        "are the bugs; an unused GPIO is not). Optional refdes narrows to one "
        "part. " + _PAGED,
        {"refdes": _REFDES, **_PAGE},
    ),
    _query(
        "list_components",
        "compact roster [refdes, mpn, value, category, side]. " + _PAGED,
        dict(_PAGE),
    ),
    _query(
        "get_net",
        "one net: role (power/gnd/signal), pins as [refdes, pin, name, type], "
        "the MCU pin it reaches, routed or not, trace/via/rats counts and the "
        "unrouted length in mm. " + _PAGED,
        {"name": {"type": "string", "description": "net name, e.g. VCC_3V3"}, **_PAGE}, ["name"],
    ),
    _query(
        "list_nets",
        "compact roster [name, role, pin count, routed|null]. " + _PAGED,
        dict(_PAGE),
    ),
    _query(
        "list_issues",
        "verifier findings compactly (id, severity, rule, refdes/net, one-line "
        "message) with counts and open/acknowledged totals. Filter by severity "
        "or a rule prefix (R21, PCB_, MECH_). Call it after run_drc and before "
        "saying the design is done. " + _PAGED,
        {"severity": {"type": "string", "enum": ["error", "warning", "info"]},
         "rule": {"type": "string"}, **_PAGE},
    ),
    _query(
        "list_unrouted_ratlines",
        "unrouted connections on the board, longest first, each named by its "
        "pads (U1.12-R3.1) with length in mm. " + _PAGED,
        dict(_PAGE),
    ),
    _query(
        "get_board_summary",
        "the board at a glance: outline shape/size/thickness, layer count and "
        "planes, mounting holes, parts per side, parts bounding box, tallest "
        "part, routing counts, recorded form factor, enclosure. Read it before "
        "reasoning about placement, size or the enclosure.",
        {},
    ),
    _query(
        "get_part_envelope",
        "the rectangles a placed part occupies on the board (the same boxes the "
        "overlap rule and the STEP export use) plus its height; an unplaced part "
        "answers its footprint/courtyard size.",
        {"refdes": _REFDES}, ["refdes"],
    ),
    _query("list_holes", "mounting holes {id, x, y, d, plated, standoff}. " + _PAGED, dict(_PAGE)),
    _query(
        "get_hole",
        "one mounting hole by id, with the nearest placed part and its clearance in mm.",
        {"id": {"type": "string", "description": "hole id, e.g. mh-1a2b3c4d"}}, ["id"],
    ),
    _query("list_cutouts", "enclosure-wall openings {id, refdes, edge, box}. " + _PAGED, dict(_PAGE)),
    _query("list_keepouts", "keep-out zones {id, layer, box, reason}. " + _PAGED, dict(_PAGE)),
]
TOOL_SCHEMAS += QUERY_SCHEMAS


# --------------------------------------------------------------------------- #
# Queries on/off (benchmark switch) + the per-turn usage accumulator
# --------------------------------------------------------------------------- #
# SYNQIT_AGENT_QUERIES=0 hides the query tools and strips the "ask, don't
# guess" rule from the prompt — the pre-queries agent, kept so
# scripts/bench_agent_tokens.py can measure both modes on the same prompts.
# Default on. Nothing else reads it.
QUERIES_ENV = "SYNQIT_AGENT_QUERIES"
_QUERY_RULE_START = "ASK, DON'T GUESS"
_QUERY_RULE_END = "DO NOT STOP EARLY"


def queries_enabled() -> bool:
    return os.environ.get(QUERIES_ENV, "1").strip().lower() not in ("0", "false", "off", "no")


def query_rule_text() -> str:
    """The prompt text the query round added (budgeted: see regression_agent_queries)."""
    return SYSTEM_PROMPT[SYSTEM_PROMPT.index(_QUERY_RULE_START):SYSTEM_PROMPT.index(_QUERY_RULE_END)]


def active_system_prompt() -> str:
    return SYSTEM_PROMPT if queries_enabled() else SYSTEM_PROMPT.replace(query_rule_text(), "")


def active_tool_schemas() -> list[dict]:
    if queries_enabled():
        return TOOL_SCHEMAS
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] not in queries.QUERIES]


# Token/tool accounting for the CURRENT agent.run (reset at its start). Read by
# the benchmark; never shown to the model. The smallest hook that records
# what the OpenAI client already returns.
USAGE: dict[str, int] = {}


def reset_usage() -> None:
    USAGE.clear()
    USAGE.update(prompt_tokens=0, completion_tokens=0, cached_tokens=0, llm_calls=0,
                 tool_calls=0, tool_output_chars=0, queries=0, queries_denied=0,
                 tool_errors=0, state_guesses=0,
                 rules_fixed=0, rule_regressions=0, fixup_stalled=0)


reset_usage()


# A tool call the agent made on a false belief about state it could have read.
# These are the failures the read tools exist to prevent, so they are what a
# read-tool experiment has to count -- tokens never showed anything.
#
# "already on net" / "would short the rails": connect() refusing a pin the
# agent believed was free. R21 was the commonest defect in the prompt sweep,
# 12 hits in 12 builds. "does not exist": a refdes or pin it assumed was there.
_STATE_GUESS_MARKERS = (
    "is already on net",
    "would short the rails",
    "does not exist",
    "no such",
)


def _record_tool_error(message: str) -> None:
    USAGE["tool_errors"] += 1
    low = message.lower()
    if any(m in low for m in _STATE_GUESS_MARKERS):
        USAGE["state_guesses"] += 1


def _record_usage(completion) -> None:
    u = getattr(completion, "usage", None)
    if u is None:
        return
    USAGE["llm_calls"] += 1
    USAGE["prompt_tokens"] += int(getattr(u, "prompt_tokens", 0) or 0)
    USAGE["completion_tokens"] += int(getattr(u, "completion_tokens", 0) or 0)
    det = getattr(u, "prompt_tokens_details", None)
    USAGE["cached_tokens"] += int(getattr(det, "cached_tokens", 0) or 0) if det else 0


def has_api_key(llm=None) -> bool:
    return bool((llm.api_key if llm is not None else None) or os.environ.get("OPENAI_API_KEY"))


def _model_name(llm=None) -> str:
    return llm.model if llm is not None else os.environ.get("OPENAI_MODEL", "gpt-4o")


# Multi-shot self-correction. After the first agent turn finishes we look at
# the rule-based DRC results; if there are blocking errors we feed them back
# to the agent for up to MAX_FIXUP_TURNS additional turns. Caps cost while
# letting the agent close routing/decoupling/protection gaps that show up
# only once the design is mostly built.
MAX_FIXUP_TURNS = 3

# Assistant turns per _run_openai invocation. With the array/matrix tools even
# a 100-LED design fits comfortably; when the loop still runs dry the
# truncation is surfaced loudly (chat note + agent_truncated event + DRC), so
# raising this trades cost for completeness, never for honesty.
MAX_AGENT_TURNS = 30


# --------------------------------------------------------------------------- #
# Deterministic board-size extraction (review round 2026-08-20)
#
# "Custom ESP32-S3 dev board, 2 x 1 inch" built a 112.2 x 44.5 mm board -- 4.8x
# the requested area -- and shipped with **0 errors**, because R57 only fires
# when `ir.form_factor` was recorded and recording it was left to the model's
# discretion. A stated size is a REQUIREMENT, exactly like a stated cell count
# (see verifier._declared_input_voltage): parse it in code, record it, and let
# R57 judge the result. The model can still call set_form_factor itself -- an
# explicit call always wins, this only fills the gap when it forgets.
# --------------------------------------------------------------------------- #
_MM = r"(?:mm|millimet(?:er|re)s?)"
_IN = r"(?:in|inch|inches|\")"
_NUM = r"(\d+(?:\.\d+)?)"
_SIZE_RECT_RE = re.compile(
    rf"{_NUM}\s*(?:x|\u00d7|\*|by)\s*{_NUM}\s*({_MM}|{_IN})\b", re.IGNORECASE
)
_SIZE_ROUND_RE = re.compile(
    rf"(?:\u00f8|diameter\s*(?:of\s*)?|round[^.]{{0,20}}?)\s*{_NUM}\s*({_MM}|{_IN})\b",
    re.IGNORECASE,
)
# "a 30.5 x 30.5 mm mounting pattern" is a HOLE PITCH, not a board outline --
# sizing the board to it guarantees an over-subscribed board.
_PITCH_CONTEXT_RE = re.compile(
    r"(mount(ing)?\s+(pattern|pitch|holes?)|hole\s+(pattern|pitch|spacing)|stack\s+pattern|bolt\s+circle)",
    re.IGNORECASE,
)
_ROUND_WORD_RE = re.compile(r"\b(round|circular|disc|disk|dia(?:meter)?)\b", re.IGNORECASE)
_SIZE_SINGLE_RE = re.compile(rf"{_NUM}\s*({_MM}|{_IN})\b", re.IGNORECASE)
# A size that belongs to a PART, not the board: "a round 1.28 inch display",
# "0.96 inch OLED", "a 10 mm coin motor". Sizing the board to one of those is
# how a 42 mm watch board becomes a 32 mm one.
_PART_SIZE_RE = re.compile(
    r"\b(display|screen|oled|lcd|tft|panel|epaper|e-paper|sensor|motor|buzzer|speaker|"
    r"battery|cell|button|led|pitch|header|connector|fan|antenna)\b",
    re.IGNORECASE,
)
_SIZE_CONTEXT_RE = re.compile(
    r"(board|pcb|outline|form\s*factor|size|footprint|fit|card|module|dimensions?)", re.IGNORECASE
)
_MM_PER_INCH = 25.4
_MIN_SIZE_MM, _MAX_SIZE_MM = 8.0, 500.0


def _to_mm(value: str, unit: str) -> float:
    v = float(value)
    return v * _MM_PER_INCH if unit.lower().startswith(("in", '"')) else v


def declared_board_size(text: str) -> dict | None:
    """A board size stated in the user's own words -> set_form_factor kwargs.

    Returns None when nothing was stated, when the number is a mounting-hole
    pattern rather than an outline, or when the value is outside the sane
    range. Never guesses: no size in the prompt means no spec recorded."""
    # Split on real sentence breaks only: a bare "." inside 50.8 is a decimal
    # point, and splitting there hid every millimetre size with a decimal.
    for sentence in re.split(r"(?<!\d)\.(?!\d)|[;\n]", text):
        if not sentence or not _SIZE_CONTEXT_RE.search(sentence):
            continue
        if _PART_SIZE_RE.search(sentence) and not re.search(
            r"\b(board|pcb|outline|form\s*factor)\b", sentence, re.IGNORECASE
        ):
            continue  # the number sizes a component, not the board
        m = _SIZE_RECT_RE.search(sentence)
        if m and not _PITCH_CONTEXT_RE.search(sentence):
            w = _to_mm(m.group(1), m.group(3))
            h = _to_mm(m.group(2), m.group(3))
            if _MIN_SIZE_MM <= w <= _MAX_SIZE_MM and _MIN_SIZE_MM <= h <= _MAX_SIZE_MM:
                return {"shape": "rect", "width_mm": round(w, 2), "height_mm": round(h, 2),
                        "note": f"user asked for {m.group(1)} x {m.group(2)} {m.group(3)}"}
        mr = _SIZE_ROUND_RE.search(sentence)
        if mr is None and _ROUND_WORD_RE.search(sentence):
            # "a 36 mm round board" — the size comes BEFORE the word "round".
            mr = _SIZE_SINGLE_RE.search(sentence)
        if mr and not _PITCH_CONTEXT_RE.search(sentence):
            d = _to_mm(mr.group(1), mr.group(2))
            if _MIN_SIZE_MM <= d <= _MAX_SIZE_MM:
                return {"shape": "round", "diameter_mm": round(d, 2),
                        "note": f"user asked for {mr.group(1)} {mr.group(2)} diameter"}
    return None


def _record_part_constraints(project: Project, user_text: str) -> list[str]:
    """Record the explicit part constraints stated in the prompt (a value, a
    package/mount style, a size) so R73 can check the built design against
    them. Deterministic on purpose: the recurring failure is that the model
    states the right number in prose and then places a different part."""
    from app.models import PartConstraintRecord
    from app.services import constraints_text

    found = constraints_text.extract_constraints(user_text)
    if not found:
        return []
    existing = {
        (c.kind, c.target, c.value) for c in (project.ir.partConstraints or [])
    }
    added: list[str] = []
    for c in found:
        key = (c.kind, c.target, c.value)
        if key in existing:
            continue
        project.ir.partConstraints.append(PartConstraintRecord(
            kind=c.kind, target=c.target, value=c.value, text=c.text,
            tolerance=c.tolerance,
        ))
        existing.add(key)
        added.append(constraints_text.describe(c))
    return added


def _record_declared_size(project: Project, user_text: str) -> str | None:
    """Record a size stated in the prompt if the model did not. Returns a note
    for the chat when it fired, else None."""
    if getattr(project.ir, "form_factor", None) is not None:
        return None  # the model recorded one itself - its call wins
    spec = declared_board_size(user_text)
    if spec is None:
        return None
    try:
        tools.set_form_factor(project, **spec)
    except Exception:  # noqa: BLE001 - a malformed size must never kill the turn
        return None
    return spec["note"]


def run(project: Project, user_text: str, llm=None) -> Iterator[AgentEvent]:
    """Streams events. Mutates `project` in place; caller is responsible for upserting.

    `llm` is an optional services.llm_config.LlmRuntime: the calling user's own
    key/model (bring-your-own-model). None = the server's OPENAI_* env."""
    reset_usage()
    turn_start = len(project.chat)
    user_msg = ChatMessage(id=f"msg-{uuid.uuid4().hex[:8]}", role="user", text=user_text)
    project.chat.append(user_msg)
    yield AgentEvent("user_message", user_msg.model_dump())

    if has_api_key(llm):
        yield from _run_openai(project, user_text, llm)
        # Explicit part constraints ("150 Ω", "through-hole", "5mm LED") are
        # recorded from the user's own words BEFORE the build is judged, so R73
        # can compare the design against them.
        _record_part_constraints(project, user_text)
        # A size the user stated is a requirement, not a suggestion: record it
        # when the model didn't, so R57 judges the built board against it.
        size_note = _record_declared_size(project, user_text)
        if size_note:
            yield AgentEvent("note", {"text": f"Recorded the board size you asked for ({size_note})."})
        # Empty-turn guard: a build request that produced NO components is not a
        # design, it is a conversation. Retry once, explicitly. Measured live: 1
        # prompt in 16 came back with 0 parts because the model described the
        # board and asked permission to build it.
        if _looks_like_a_build_request(user_text) and not project.ir.components:
            yield from _run_openai(project, _EMPTY_TURN_RETRY)
        if project.ir.components:
            tools.run_drc(project)
        # Self-correction loop — only on the real OpenAI path.
        # `previous` carries the blocking-error id set from the turn before,
        # so the loop can tell progress from a stall.
        previous: frozenset[str] | None = None
        for fixup_turn in range(MAX_FIXUP_TURNS):
            errors = [
                i for i in project.issues
                if i.severity == "error"
                and i.rule
                and not i.rule.startswith("LLM_")
                and not i.rule.startswith("PCB_")
                # R35 flags missing/under-populated pin *data* — there is no
                # IR-level tool to add pins to a part, so re-prompting the agent
                # would only make it flail. It's surfaced in Review, not looped.
                and not i.rule.startswith("R35")
                # MECH_* are mechanical/enclosure DRC (Week-4 6.2) — the schematic
                # agent has no tool to resolve them (they're fixed via the
                # mechanical IR / placement), so looping on them would only flail.
                and not i.rule.startswith("MECH_")
            ]
            current = frozenset(i.id for i in errors if i.id)
            if not errors:
                break
            # Is the loop getting anywhere? Compare this turn's blocking set
            # with the last one. Three outcomes worth telling apart, and
            # until now all three were invisible: a loop that fixed
            # everything and a loop that changed nothing produced identical
            # downstream behaviour and identical prose.
            if previous is not None:
                fixed = previous - current
                introduced = current - previous
                USAGE["rules_fixed"] += len(fixed)
                USAGE["rule_regressions"] += len(introduced)
                if current == previous:
                    # Same errors, same ids: the turn achieved nothing and
                    # another identical prompt will achieve nothing either.
                    USAGE["fixup_stalled"] += 1
                    yield AgentEvent(
                        "fixup_verdict",
                        {"verdict": "stalled", "errors": len(current), "turn": fixup_turn},
                    )
                    break
                yield AgentEvent(
                    "fixup_verdict",
                    {
                        "verdict": "regressed" if introduced else "progressing",
                        "fixed": sorted(fixed),
                        "introduced": sorted(introduced),
                        "turn": fixup_turn,
                    },
                )
            previous = current
            fixup_prompt = _build_fixup_prompt(errors, fixup_turn + 1)
            yield from _run_openai(project, fixup_prompt)
        # Enrich newly-added parts with richer descriptions before the caller
        # upserts. Best-effort; never blocks the turn from completing.
        _enrich_descriptions(project, llm)
    else:
        yield from _run_stub(project, user_text)

    # Deterministically wire any decoupling caps left floating, then refresh DRC
    # so the verifier panel reflects the repair. Runs on both paths.
    repaired = tools.repair_floating_caps(project)
    if repaired:
        tools.run_drc(project)

    # Grounded-claims guard (Phase 2 / feedback #2): check the final assistant
    # prose against the IR. Any connection it *states* that isn't on a net is
    # downgraded to a Missing Item and annotated in the message, so chat can
    # never present an ungrounded connection as fact. Runs on both paths.
    _validate_final_claims(project)

    # Status claims ("0 errors", "fully routed", "fits") must be backed by a
    # query made in THIS turn — the verbs / nouns / queries rule's honesty half.
    if has_api_key(llm):
        _flag_unverified_status_claims(project, turn_start)

    # DRC-status guard (beta review #6): the message may not claim a clean
    # design while the verifier still reports blocking errors.
    _append_drc_status(project)

    # ...and if the turn STILL built nothing, say so rather than leaving prose
    # that reads like a delivered design.
    _append_empty_turn_notice(project, user_text)


# A request to BUILD something (as opposed to a question about the design).
_BUILD_VERBS = re.compile(
    r"\b(build|design|make|create|generate|add|draw|lay ?out|wire|route|give me|"
    r"i want|i need|can you (build|design|make|create))\b",
    re.IGNORECASE,
)

_EMPTY_TURN_RETRY = (
    "You produced no components. Do not describe the design and do not ask "
    "whether to proceed — the user already asked for it. Build it NOW with tool "
    "calls: record_requirements, then add the parts, wire every power, ground "
    "and signal net, then run_drc and fix what it reports. If some part is not "
    "in the library, record that requirement with unavailable=true and build "
    "everything else."
)


def _looks_like_a_build_request(user_text: str) -> bool:
    return bool(_BUILD_VERBS.search(user_text or ""))


def _append_empty_turn_notice(project: Project, user_text: str) -> None:
    """State plainly that nothing was built, so an explanatory answer can never
    be mistaken for a finished design."""
    if project.ir.components or not _looks_like_a_build_request(user_text):
        return
    if not project.chat or project.chat[-1].role == "user":
        return
    notice = (
        "Status. Nothing was built this turn — the schematic is still empty, so "
        "there is no design to review, verify or export yet. Ask again (or say "
        "\"go ahead\") and it will be built."
    )
    if "Nothing was built this turn" in (project.chat[-1].text or ""):
        return
    project.chat[-1].text = f"{(project.chat[-1].text or '').rstrip()}\n\n{notice}"


# Phrases the model uses to declare victory. Beta review #6 point 15: "the
# Copilot says the blocking DRC errors were resolved, but the Verifier shows 12
# errors and 28 warnings — that mismatch itself needs to be checked". Measured
# on stored projects: 11 of them claimed clean with errors still open.
_CLAIMS_CLEAN = re.compile(
    r"(drc|error)[^.]{0,60}(resolved|cleared|clean|fixed|gone|no longer)"
    r"|no (remaining |blocking |more )?(drc )?errors"
    r"|(design|schematic|board) is (now )?(drc[- ])?clean",
    re.IGNORECASE,
)


def _append_drc_status(project: Project) -> None:
    """Stamp the TRUE blocking-error count onto the final assistant message.

    Deterministic and IR-derived, like every other fact the UI shows: the prose
    is the model's, the count is the verifier's. A turn that ends with errors
    open says so in the same breath the model used to declare success — and a
    turn that ends clean says nothing extra."""
    errors = [
        i for i in project.issues
        if i.severity == "error" and i.rule
        and not i.rule.startswith(("LLM_", "PCB_", "MECH_"))
    ]
    if not errors:
        return
    last = next((m for m in reversed(project.chat) if m.role == "assistant"), None)
    if last is None:
        return
    rules = sorted({i.rule.split("_", 1)[0] for i in errors})
    contradiction = bool(_CLAIMS_CLEAN.search(last.text))
    lead = (
        "**Correction — the design is NOT clean.** "
        if contradiction else
        "**Status.** "
    )
    note = (
        f"{lead}{len(errors)} blocking DRC error"
        f"{'s' if len(errors) != 1 else ''} still open "
        f"({', '.join(rules[:6])}). Open the Review tab for the list; the design "
        f"is not ready to manufacture until they are cleared."
    )
    if note not in last.text:
        last.text = f"{last.text}\n\n{note}" if last.text else note


def _validate_final_claims(project: Project) -> None:
    """Validate the last assistant message's connection claims against the IR."""
    last = next(
        (m for m in reversed(project.chat) if m.role == "assistant"), None
    )
    if last is None:
        return
    annotated, claim_issues = claim_validator.validate(project, last.text)
    if not claim_issues:
        return
    last.text = annotated
    # Surface the ungrounded claims in the verifier panel / Review summary,
    # de-duplicating against anything already present.
    existing = {i.id for i in project.issues}
    project.issues.extend(i for i in claim_issues if i.id not in existing)
    project.drcErrorCount = sum(1 for i in project.issues if i.severity == "error")


# Status claims that need a query behind them (verbs / nouns / queries rule,
# 2026-09-07). Each: label, the claim pattern, the queries that can back it.
# Deliberately narrow phrases — a false flag on ordinary prose would teach the
# user to ignore the real ones.
_STATUS_CLAIMS: list[tuple[str, re.Pattern[str], frozenset[str]]] = [
    ("errors",
     re.compile(r"\b(?:no|zero|0|without(?: any)?)\s+(?:blocking\s+|drc\s+|remaining\s+|open\s+)?"
                r"(?:errors?|issues?|findings?)\b|\bdrc[- ]clean\b|\bpasses\s+drc\b|\bclean\s+drc\b",
                re.IGNORECASE),
     frozenset({"list_issues"})),
    ("routing",
     re.compile(r"\b(?:fully|completely|all|100\s*%)\s+routed\b|\bno\s+(?:unrouted|airwires?|rats)\b"
                r"|\b(?:0|zero)\s+(?:unrouted|airwires?|rats)\b", re.IGNORECASE),
     frozenset({"list_unrouted_ratlines", "get_net", "list_nets", "get_board_summary"})),
    ("connections",
     re.compile(r"\b(?:all|every)\s+(?:the\s+)?(?:pins?|signals?|nets?|connections?|power\s+pins?)\s+"
                r"(?:are\s+|is\s+)?(?:now\s+)?(?:connected|wired)\b"
                r"|\beverything\s+is\s+(?:connected|wired)\b|\bfully\s+(?:connected|wired)\b"
                r"|\bno\s+(?:unconnected|floating|unwired)\s+pins?\b", re.IGNORECASE),
     frozenset({"list_unconnected_pins", "get_pin_net", "get_component", "get_net"})),
    ("fit",
     re.compile(r"\b(?:fits|will\s+fit|fit\s+comfortably)\s+(?:in|inside|within|on|under|the|a)\b"
                r"|\bboard\s+fits\b|\beverything\s+fits\b", re.IGNORECASE),
     frozenset({"get_board_summary", "get_part_envelope"})),
]


def _flag_unverified_status_claims(project: Project, turn_start: int) -> None:
    """Flag a status claim in this turn's assistant text that no query in this
    turn backs — as a WARNING in the Review panel, never by rewriting the text.
    The IR-vs-prose sibling of claim_validator (connections, values) and R73
    (user-stated parts): those check WHAT was said against the design; this
    checks that a verdict was READ, not assumed."""
    turn = [m for m in project.chat[turn_start:] if m.role == "assistant"]
    if not turn:
        return
    text = "\n".join(m.text or "" for m in turn)
    asked = {c.tool for m in turn for c in m.toolCalls}
    new = []
    for label, pattern, backing in _STATUS_CLAIMS:
        hit = pattern.search(text)
        if hit is None or asked & backing:
            continue
        quote = hit.group(0)
        new.append(VerifierIssue(
            id=f"LLM_status:{label}",
            severity="warning",
            message=(f"The reply says \"{quote}\" but no {' / '.join(sorted(backing))} query was "
                     f"made this turn, so that is an assumption, not a reading. The Review "
                     f"panel shows the real state."),
            rule="LLM_unverified_status",
            source="llm",
        ))
    if not new:
        return
    existing = {i.id for i in project.issues}
    project.issues.extend(i for i in new if i.id not in existing)


# How the agent should fix each rule family, keyed by rule-id prefix. Surfaced in
# the fix-up prompt so the ERC pin-type-matrix errors (R24) and its companions —
# promoted to hard errors once erc_matrix.STRICT_ERRORS is on — get a concrete,
# tool-level remedy rather than a re-explanation. IPC rules (R27–R30) are
# advisory (warning/info) and never reach this error path, so they're omitted.
_FIX_HINTS = {
    "R24": "resolve the pin-type conflict: disconnect the offending connection, "
           "or move one pin to its own net (use disconnect / connect / allocate_pin). "
           "output↔output and power_out↔power_out mean a short — separate them.",
    "R25": "the net has only inputs and no driver — connect it to a driving output "
           "or add a pull-up/pull-down resistor.",
    "R26": "the rail has no source — connect it to the regulator/source output that "
           "should power it. Never 'source' a rail from an internal-regulator pin "
           "(VCAP/VDDCORE/VCORE) — those take only their own capacitor.",
    "R80": "disconnect the supply rail or ground from the internal-regulator pin "
           "(VCAP/VDDCORE/VCORE): it takes ONLY its datasheet capacitor, other end "
           "to GND — add that capacitor if it is missing.",
    "R21": "give each signal its own MCU pin (allocate_pin) so they aren't shorted.",
    "R11": "the pin is on two power/ground nets — disconnect the wrong one (a short).",
    "R20": "a rail has two sources — remove/relocate one so a single source drives it.",
    "R31": "the regulator is undersized for the rail's load — swap it for a "
           "higher-current regulator (search_parts for one rated above the load).",
    "R32": "the linear regulator dissipates too much heat — swap it for a switching "
           "regulator (buck) for this input/output voltage drop.",
    "R36": "the MCU pin is 3.3 V-only (not 5 V-tolerant) but sits on a ≥5 V rail — "
           "disconnect it from the 5 V net and rewire to a 3.3 V rail, or place a "
           "level shifter (TXS0108EPWR / BSS138) between them.",
    "R37": "a 5 V-logic MCU pin drives a 3.3 V-max part directly — add a level "
           "shifter (TXS0108EPWR / BSS138) between them on that signal, or swap "
           "in a 5 V-tolerant part.",
    "R43": "the design's part count doesn't match what the user asked for — "
           "if SHORT, add the missing parts with add_component_array (and "
           "connect_matrix for row/column matrices) and wire them; if there are "
           "EXTRAS (a duplicated block), delete_component the surplus refdeses "
           "and their private resistors/nets — never re-add.",
    "R46": "an LED sits across the supply with no series resistor -- insert one "
           "(RC0603FR-07330RL / RC0603FR-07150RL): disconnect the anode from the "
           "rail, connect rail -> R pin 1, R pin 2 -> LED anode.",
    "R19": "the motor driver is incomplete — its OUTPUT pins must each go to a "
           "motor connector pin (AO1 -> J.1, AO2 -> J.2, BO1 -> J2.1, BO2 -> J2.2); "
           "the connector must NOT sit on a power rail, and VM must be on the "
           "battery/motor rail.",
    "R51": "a 5 V peripheral output drives a 3.3 V-only MCU pin — insert a "
           "divider on that net: disconnect the MCU pin, connect the 5 V output "
           "to R1 (RC0603FR-0710KL 10k), R1's other end to the MCU pin AND to R2 "
           "(RC0603FR-0720KL / two 10k in series) whose far end goes to GND.",
    "R55": "a battery-sense divider does not attenuate enough — raise the top "
           "resistor so the pack's MAXIMUM voltage lands under the ADC limit "
           "(100k top / 22k bottom gives 2.2 V at 12.6 V), and state the assumed "
           "pack voltage in the final message.",
    "R56": "an I2C part's mode strap is wrong — connect its CSB/CS pin to the "
           "part's supply rail (HIGH selects I2C; tying it to GND selects SPI), "
           "and tie the address pin (SDO/AD0/ADDR) to GND or the rail so the "
           "address is defined.",
    "R03": "a regulator's enable pin is floating — pull EN up to the regulator's "
           "INPUT rail through 10 kOhm (RC0603FR-0710KL), or drive it from a GPIO.",
    "R53": "a bare MCU chip has no flash/crystal — either add W25Q128JVSIQ on the "
           "QSPI pins + Q_12MHz_3225 on XIN/XOUT (bare-chip design), or replace it "
           "with the module (RaspberryPi_Pico for RP2040, ESP32-WROOM-32 for ESP32) "
           "which is the right call for a hobby board.",
    "R54": "an external connector feeds an MCU input with no ground return — use a "
           "sensor MODULE with a VCC/GND/OUT header (Soil_Moisture_Capacitive_v1.2, "
           "Water_Level_Sensor_Module, KY-038, MAX9814_Module, TTP223_Module) wired "
           "OUT -> MCU pin, or give the probe connector a GND pin and put a fixed "
           "resistor (RC0603FR-0710KL) from the input net to GND as the divider leg.",
    "R52": "a transistor's base/gate reaches no driver — wire it to an "
           "allocate_pin GPIO through a ~1 kOhm resistor (RC0603FR-071KL), or "
           "bias it to a rail deliberately.",
    "R57": "the board doesn't match the requested form factor — the parts may "
           "not physically fit the requested size: delete_component whatever "
           "isn't strictly needed, or call set_form_factor with an achievable "
           "size and tell the user why; the user then re-runs auto-place.",
    "R58": "the regulator cannot work from its input rail — input BELOW the "
           "output: replace it with a boost (TPS61023 / TPS61090 / MT3608_MOD); "
           "input far ABOVE the output: replace the linear part with a buck "
           "(MP1584EN + FB divider / LM2596 / TPS5430DDA) and keep an LDO only "
           "for the last small step. delete_component the old part and rewire.",
    "R59": "an adjustable regulator's ADJ/FB pin has no divider — connect a "
           "resistor from VOUT to ADJ/FB (RC0603FR-07240RL / 10k for a buck) and "
           "one from ADJ/FB to GND sized for the target voltage; disconnect ADJ "
           "from GND first.",
    "R60": "the part's function pins are unwired — a relay's COM/NO to the load "
           "terminal, a sensor's inputs to what it measures, a driver module's "
           "outputs to the motor connector. Wire them or delete the part.",
    "R61": "a resistor/cap network touches nothing active — wire its ends to the "
           "source and the load (divider: rail -> R1 -> SENSE -> R2 -> GND, SENSE "
           "to an allocate_pin adc input or a connector).",
    "R62": "add FS8205A: DW01A OD -> G1, OC -> G2, S1 and S2 on the CS/BAT- side, "
           "D1 -> BAT-, D2 -> PACK-.",
    "R63": "rewire the N-channel switch low-side: load between the rail and the "
           "DRAIN, SOURCE to GND, gate from the GPIO through 1k.",
    "R64": "replace the series diode with a P-FET stage (AO4407A / IRF4905: "
           "D -> battery connector +, S -> VBAT, G -> GND through 100k) or, if a "
           "diode must stay, use a >= 5 A Schottky and accept the drop.",
    "R65": "a non-isolating part bridges MAINS and logic — remove it or move that "
           "function behind a relay / optocoupler (PC817, MOC3021) / isolated "
           "sensor (ACS712) / isolated AC-DC module.",
    "R66": "wire the sensor's IN+/IN- across a shunt (WSL2512R1000FEA) in the "
           "load path: rail -> IN+, IN- -> load, shunt between the two nets.",
    "R67": "the rail's name lies about its voltage — rename the net to the "
           "regulator's real output (VCC_3V3 for a 3.3 V part) or swap in a "
           "regulator that makes the named voltage; then re-check every part on it.",
    "R49": "a load draws more current than an MCU pin can supply -- drive it "
           "through a low-side switch: GPIO -> 1 kOhm (RC0603FR-071KL) -> "
           "MMBT3904 base, load between the rail and the collector, and add a "
           "flyback diode (1N5819) across a coil load.",
    "R47": "a 2-pin part has both pins on one net -- put the second pin on "
           "the other side of the load (motor: driver OUT1 -> pin 1, OUT2 -> pin 2; "
           "battery: BAT+ -> pin 1, GND -> pin 2).",
    "R45": "an enable / nSLEEP / active-low reset input is tied to GND, holding "
           "the part disabled — disconnect it from GND and pull it UP to the "
           "logic rail through a 10 kΩ resistor (RC0603FR-0710KL) or connect it "
           "to an allocate_pin GPIO.",
    "R44": "several GPIOs of one MCU sit on the same net — split it: one net "
           "per signal (disconnect the extra GPIOs, then connect each signal to "
           "its own allocate_pin GPIO through its own resistor/part).",
    "R73": "the design does not match a value / package / size the user stated "
           "in their own words — search_parts for one that DOES match (a "
           "through-hole ask needs a THT part, a 5 mm LED ask needs LED_5MM_*, "
           "a 150 ohm ask needs a 150 ohm resistor), delete_component the wrong "
           "one and add the right one. Never argue that the substitute is close "
           "enough — build what was asked for.",
    "R74": "the board carries a duplicated port nobody asked for — "
           "delete_component the extra one (keep the one that is actually "
           "wired) rather than leaving two power inputs to back-feed each other.",
    "R76": "the sense chain exists but its output goes nowhere — connect the "
           "named divider midpoint to an analog-capable MCU pin (allocate_pin "
           "role \"adc\", then connect), or give the bare sensor a fixed "
           "series resistor and read the junction. Never delete the sensor to "
           "silence this.",
    "R77": "a mode/address strap pin is floating — read the part's "
           "applicationNote (returned by add_component) and connect the pin to "
           "GND or the part's VDDIO net as the datasheet says for the interface "
           "in use (e.g. BNO085 PS0 and PS1 both to GND for I2C).",
    "R78": "the MCU bus landed on the part's auxiliary/secondary bus pins — "
           "disconnect them and connect the MCU SCL/SDA to the H_-prefixed host "
           "pins named in the finding. The ENV_/AUX_ pins are for the part's own "
           "external sensor and stay unconnected unless one is placed.",
    "R79": "a regulator/charger output has no (or too little) capacitance — add "
           "a 1 uF or larger ceramic (e.g. CC0603KPX7R9BB105) from the output "
           "net to GND. It is required by the datasheet, not optional.",
}


def _build_fixup_prompt(errors, turn_index: int) -> str:
    """Format the rule-based DRC errors into a corrective prompt for the agent."""
    lines = [
        "DRC found blocking errors after your previous turn. Fix them with "
        "tool calls only — do NOT explain. Check a pin with get_pin_net before "
        "moving it. After your fixes, call run_drc, then list_issues to read "
        "what is left. "
        "The design already contains everything in CURRENT DESIGN STATE — fix "
        "the listed errors on the EXISTING parts; never re-add parts, arrays "
        "or requirements that are already there.",
        "",
        f"Turn {turn_index} fix-up requests:",
    ]
    hints_used: set[str] = set()
    # Group by rule so a design with 9 identical orphan-net errors spends one
    # line on them instead of filling the whole budget: the model needs the
    # WHOLE picture to fix a systematic mistake, not the first 8 instances.
    by_rule: dict[str, list] = {}
    for err in errors:
        by_rule.setdefault(err.rule or "rule", []).append(err)
    shown = 0
    for rule, errs in by_rule.items():
        prefix = rule.split("_", 1)[0]
        if prefix in _FIX_HINTS:
            hints_used.add(prefix)
        for err in errs[:3]:
            ctx = ""
            if err.refdes:
                ctx += f" [refdes={err.refdes}"
                if err.pinNumber:
                    ctx += f" pin={err.pinNumber}"
                ctx += "]"
            elif err.net:
                ctx += f" [net={err.net}]"
            lines.append(f"- [{rule}]{ctx}: {err.message}")
            shown += 1
        if len(errs) > 3:
            rest = ", ".join(
                (e.refdes or e.net or "?") for e in errs[3:12]
            )
            more = f" (+{len(errs) - 12} more)" if len(errs) > 12 else ""
            lines.append(
                f"- [{rule}] SAME ERROR on {len(errs) - 3} more: {rest}{more} "
                f"— fix them all the same way."
            )
    if hints_used:
        lines.append("")
        lines.append("How to fix:")
        for prefix in sorted(hints_used):
            lines.append(f"- {prefix}: {_FIX_HINTS[prefix]}")
    lines.append("")
    lines.append(
        "A FIX MUST BE JUSTIFIED BY THE PART'S DATA, never by the wish to make "
        "the finding disappear: do not wire a pin to a rail or to ground unless "
        "the part's curated pins/companions/application note call for it. "
        "Internal-regulator pins (VCAP/VDDCORE/VCORE) take ONLY their capacitor "
        "to ground. If you cannot fix a finding honestly, leave the wiring as it "
        "is and explain why in your final message."
    )
    lines.append("")
    lines.append(
        "WRITING TO THE USER: the bracketed ids above (R15_floating_component, "
        "PCB_R03_unrouted_nets, ...) are INTERNAL rule names. Never put them in "
        "your reply — a reviewer read \"R15 and R19\" as resistor designators and "
        "wrote fix steps for parts that do not exist. Say what is wrong in plain "
        "words and name the real refdes or net: \"D3's cathode is not connected to "
        "anything\", not \"R15 error on D3\"."
    )
    return "\n".join(lines)


# -- Stub agent --------------------------------------------------------------


def _run_stub(project: Project, user_text: str) -> Iterator[AgentEvent]:
    """Deterministic ESP32 sensor board generator. Triggered on first non-trivial prompt.

    For follow-up prompts on a project that already has components, the stub
    just acknowledges — the real agent path is required for free-form edits.
    """
    del user_text
    intent = (
        "I'll lay down a low-power ESP32 sensor board: USB-C input → AP2112 LDO "
        "→ ESP32-WROOM-32 with EN pull-up and 3V3 decoupling, plus a BME280 on "
        "I2C with 4.7kΩ pull-ups."
    )

    if project.ir.components:
        msg = ChatMessage(
            id=f"msg-{uuid.uuid4().hex[:8]}",
            role="assistant",
            text=(
                "(Stub agent — set OPENAI_API_KEY for free-form edits.) "
                "Project already has components. Re-running DRC."
            ),
        )
        tools.dispatch(project, "run_drc", {})
        msg.toolCalls.append(ToolCallSummary(tool="run_drc", args={}))
        msg.diff = Diff(status="pending", changes=[])
        project.chat.append(msg)
        yield AgentEvent("assistant_message", msg.model_dump())
        yield AgentEvent("done", {})
        return

    yield AgentEvent("delta", {"text": intent})

    plan: list[tuple[str, dict]] = [
        ("add_component", {"mpn": "USB4085-GF-A", "refdes": "J1", "x": 0, "y": 0}),
        ("add_component", {"mpn": "AP2112K-3.3TRG1", "refdes": "U2", "x": 200, "y": 0}),
        ("add_component", {"mpn": "ESP32-WROOM-32", "refdes": "U1", "x": 500, "y": 0}),
        ("add_component", {"mpn": "BME280", "refdes": "U3", "x": 800, "y": 0}),
        ("add_component", {"mpn": "CC0603KRX7R9BB104", "refdes": "C1", "value": "100nF", "x": 460, "y": -150}),
        ("add_component", {"mpn": "CL10A105KB8NNNC", "refdes": "C2", "value": "1uF", "x": 280, "y": -150}),
        ("add_component", {"mpn": "CL10A106MQ8NNNC", "refdes": "C3", "value": "10uF", "x": 100, "y": -150}),
        ("add_component", {"mpn": "CC0603KRX7R9BB104", "refdes": "C4", "value": "100nF", "x": 800, "y": -150}),
        ("add_component", {"mpn": "RC0603FR-0710KL", "refdes": "R1", "value": "10k", "x": 460, "y": 150}),
        ("add_component", {"mpn": "RC0603FR-074K7L", "refdes": "R2", "value": "4.7k", "x": 720, "y": 200}),
        ("add_component", {"mpn": "RC0603FR-074K7L", "refdes": "R3", "value": "4.7k", "x": 770, "y": 200}),
        ("add_component", {"mpn": "RC0603FR-075K1L", "refdes": "R4", "value": "5.1k", "x": 60, "y": 200}),
        ("add_component", {"mpn": "RC0603FR-075K1L", "refdes": "R5", "value": "5.1k", "x": 110, "y": 200}),
        # R6 pulls the LDO's EN up to its own input rail. Without it the
        # regulator's enable floats and the 3V3 rail may never come up (the
        # defect beta review #14 found on a generated weather station).
        ("add_component", {"mpn": "RC0603FR-0710KL", "refdes": "R6", "value": "10k", "x": 200, "y": 150}),
        # C3 is the LDO's 10 uF input bulk cap — ON VBUS. It used to sit on a
        # private one-pin net "VBUS_BULK" (a label, not a rail), which the
        # tightened R12 now flags as a dangling cap: it was.
        ("connect", {"net_name": "VBUS", "pins": [["J1", "A4"], ["U2", "1"], ["R6", "1"], ["C3", "1"]]}),
        (
            "connect",
            {
                "net_name": "VCC_3V3",
                "pins": [
                    ["U2", "5"], ["U1", "2"], ["U3", "6"], ["U3", "8"],
                    ["C1", "1"], ["C2", "1"], ["C4", "1"], ["R1", "1"],
                    ["R2", "1"], ["R3", "1"],
                ],
            },
        ),
        (
            "connect",
            {
                "net_name": "GND",
                "pins": [
                    ["J1", "A1"], ["J1", "B12"], ["U1", "1"], ["U1", "15"],
                    ["U1", "38"], ["U2", "2"], ["U3", "5"], ["U3", "7"],
                    ["C1", "2"], ["C2", "2"], ["C3", "2"], ["C4", "2"],
                    ["R4", "2"], ["R5", "2"],
                ],
            },
        ),
        ("connect", {"net_name": "EN", "pins": [["U1", "3"], ["R1", "2"]]}),
        ("connect", {"net_name": "EN_U2", "pins": [["U2", "3"], ["R6", "2"]]}),
        # BME280 mode straps: CSB HIGH selects I2C (tying it low would select
        # SPI and the sensor would never answer), SDO low fixes address 0x76.
        ("connect", {"net_name": "VCC_3V3", "pins": [["U3", "1"]]}),
        ("connect", {"net_name": "GND", "pins": [["U3", "4"]]}),
        ("connect", {"net_name": "I2C_SDA", "pins": [["U1", "29"], ["U3", "2"], ["R2", "2"]]}),
        ("connect", {"net_name": "I2C_SCL", "pins": [["U1", "28"], ["U3", "3"], ["R3", "2"]]}),
        ("connect", {"net_name": "USB_CC1", "pins": [["J1", "A5"], ["R4", "1"]]}),
        ("connect", {"net_name": "USB_CC2", "pins": [["J1", "B5"], ["R5", "1"]]}),
        ("run_drc", {}),
    ]

    diff_entries: list[DiffEntry] = []
    tool_summaries: list[ToolCallSummary] = []
    for name, args in plan:
        try:
            result, diff = tools.dispatch(project, name, args)
            # The stub build has one stated intent for the whole plan, so every
            # step carries it — the offline demo shows the same rationale shape
            # the live path does, rather than a bare tool log.
            tool_summaries.append(
                ToolCallSummary(tool=name, args=args, rationale=intent)
            )
            yield AgentEvent("tool_use", {"name": name, "args": args, "result": result})
            if diff:
                diff_entries.append(diff)
        except tools.ToolError as e:
            _record_tool_error(str(e))
            yield AgentEvent("error", {"name": name, "args": args, "error": str(e)})

    tools.recompute_bom(project)

    assistant_msg = ChatMessage(
        id=f"msg-{uuid.uuid4().hex[:8]}",
        role="assistant",
        text=intent,
        toolCalls=tool_summaries,
        diff=Diff(status="pending", changes=diff_entries),
    )
    project.chat.append(assistant_msg)
    yield AgentEvent("assistant_message", assistant_msg.model_dump())
    yield AgentEvent("done", {})


# -- OpenAI agent ------------------------------------------------------------


def _make_openai_client(llm=None):
    """One OpenAI-SDK client for whatever the caller resolved: the user's own
    provider/key (bring-your-own-model) or the server's OPENAI_* env. Azure
    and every OpenAI-compatible endpoint (Gemini, Anthropic, custom) are
    handled in services.llm_config.make_client."""
    from app.services import llm_config  # noqa: PLC0415

    return llm_config.make_client(llm or llm_config.server_runtime())


def _run_openai(project: Project, user_text: str, llm=None) -> Iterator[AgentEvent]:
    client = _make_openai_client(llm)
    model = _model_name(llm)

    messages = _build_message_history(project, user_text)
    # Loop guard: at most queries.QUERY_BUDGET_PER_TURN questions per turn.
    budget = queries.QueryBudget()
    diff_entries: list[DiffEntry] = []
    tool_summaries: list[ToolCallSummary] = []
    final_text_parts: list[str] = []

    # Bounded loop so a stuck model can't exhaust budget. If the budget runs
    # out while the model is still calling tools, the truncation is surfaced
    # LOUDLY below — a silently half-built design shipped as done is exactly
    # the failure mode the LED-matrix beta review caught.
    truncated = True
    for _ in range(MAX_AGENT_TURNS):
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=active_tool_schemas(),
            tool_choice="auto",
        )
        _record_usage(completion)
        choice = completion.choices[0]
        msg = choice.message

        # The model's line of intent for THIS turn. Kept beside the calls it
        # explains rather than only flattened into the final text — that pairing
        # is what makes the trace a rationale instead of a tool log.
        step_intent = (msg.content or "").strip() or None
        if msg.content:
            final_text_parts.append(msg.content)
            yield AgentEvent("delta", {"text": msg.content})

        # Append the assistant turn (with tool_calls) to history before serving results.
        messages.append(_assistant_message_to_dict(msg))

        if not msg.tool_calls:
            truncated = False
            break

        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as e:
                args = {}
                yield AgentEvent(
                    "error",
                    {"name": name, "args": call.function.arguments, "error": f"bad JSON: {e}"},
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": f"Error: invalid arguments JSON ({e})",
                    }
                )
                continue

            tool_summaries.append(
                ToolCallSummary(tool=name, args=args, rationale=step_intent)
            )
            try:
                denied = budget.take(name)
                if denied is not None:
                    result, diff = denied, None
                    USAGE["queries_denied"] += 1
                else:
                    result, diff = tools.dispatch(project, name, args)
                if diff:
                    diff_entries.append(diff)
                yield AgentEvent(
                    "tool_use",
                    {"name": name, "args": args, "result": result},
                )
                content = json.dumps(result, default=str)
                USAGE["tool_calls"] += 1
                USAGE["tool_output_chars"] += len(content)
                if name in queries.QUERIES:
                    USAGE["queries"] += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": content,
                    }
                )
            except tools.ToolError as e:
                _record_tool_error(str(e))
                yield AgentEvent(
                    "error", {"name": name, "args": args, "error": str(e)}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": f"Error: {e}",
                    }
                )

    tools.recompute_bom(project)
    if not truncated:
        # A turn that finished clears the flag: whatever state the design is
        # in, it is no longer "cut off mid-build".
        project.ir.buildIncomplete = False

    text = "\n\n".join(p for p in final_text_parts if p).strip() \
        or "Schematic updated. See diff below."
    if truncated:
        # The loop ran out of turns while the model still wanted to call
        # tools — the design is incomplete. Never let that read as success:
        # say so in chat, run DRC so R43 (if requirements were recorded) is
        # current, and emit a dedicated event for the UI.
        note = (
            # Plain words: "build budget" and "turn" are OUR machinery, and a
            # user reading them cannot tell whether they were billed for
            # something or hit a limit (2026-08-20 review).
            "I stopped before this design was finished — there was more to do "
            "than I could complete in one go, so **the design is incomplete** "
            "and is not ready to order. Say \"continue\" and I'll carry on from "
            "here; the Review tab lists what is still missing."
        )
        text = f"{text}\n\n{note}" if text else note
        project.ir.buildIncomplete = True
        try:
            tools.run_drc(project)
        except Exception:  # noqa: BLE001 — never let a DRC crash mask the truncation
            pass
        yield AgentEvent("agent_truncated", {"turns": MAX_AGENT_TURNS})

    assistant_msg = ChatMessage(
        id=f"msg-{uuid.uuid4().hex[:8]}",
        role="assistant",
        text=text,
        toolCalls=tool_summaries,
        diff=Diff(status="pending", changes=diff_entries),
    )
    project.chat.append(assistant_msg)
    yield AgentEvent("assistant_message", assistant_msg.model_dump())
    yield AgentEvent("done", {})


def _assistant_message_to_dict(msg) -> dict:
    """Serialize an OpenAI ChatCompletionMessage back into the JSON shape the
    chat API accepts on the next request."""
    out: dict = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {
                    "name": c.function.name,
                    "arguments": c.function.arguments,
                },
            }
            for c in msg.tool_calls
        ]
    return out


def _build_message_history(project: Project, user_text: str) -> list[dict]:
    """Reconstruct a multi-turn message list from the project's chat log."""
    messages: list[dict] = [
        {
            "role": "system",
            "content": active_system_prompt() + "\n\n" + _library_index_for_prompt(),
        },
    ]
    for m in project.chat[:-1]:  # exclude the freshly-appended user message
        if m.role == "user":
            messages.append({"role": "user", "content": m.text})
        elif m.role == "assistant" and m.text:
            messages.append({"role": "assistant", "content": m.text})
    # The model otherwise sees only prose across turns — not the IR. On a
    # fix-up or follow-up turn it then re-adds parts it already placed (a
    # second 10-LED chain, seen live). Give it the actual state.
    state = _design_state_for_prompt(project)
    if state:
        messages.append({"role": "system", "content": state})
    messages.append({"role": "user", "content": user_text})
    return messages


def _design_state_for_prompt(project: Project) -> str:
    """Compact, IR-derived snapshot of what already exists — arrays as ranges,
    singles by refdes, nets with a few pins, recorded requirements."""
    ir = project.ir
    if not ir.components:
        return ""
    in_array: set[str] = set()
    lines = ["CURRENT DESIGN STATE (already in the IR — refer to these by refdes; "
             "do NOT add them again):"]
    for arr in getattr(ir, "arrays", None) or []:
        alive = [r for r in arr.refdeses if any(c.refdes == r for c in ir.components)]
        if not alive:
            continue
        in_array.update(alive)
        grid = f", physical {arr.rows}x{arr.cols} grid @ {arr.pitch_mm} mm" if arr.grid else ""
        lines.append(f"- array {arr.key}: {len(alive)}x {arr.mpn}{grid}")
    for c in ir.components:
        if c.refdes in in_array:
            continue
        val = f" {c.value}" if c.value else ""
        lines.append(f"- {c.refdes}: {c.mpn}{val}")
    if ir.nets:
        lines.append("Nets:")
        for n in ir.nets:
            head = ", ".join(f"{r}.{p}" for r, p in n.pins[:6])
            more = f" +{len(n.pins) - 6}" if len(n.pins) > 6 else ""
            lines.append(f"- {n.name} ({len(n.pins)}): {head}{more}")
    reqs = getattr(ir, "requirements", None) or []
    if reqs:
        lines.append("Recorded requirements: " + "; ".join(
            f"{r.quantity}x {r.description}" + (" (NOT IN LIBRARY - design incomplete)" if getattr(r, "unavailable", False) else "") for r in reqs
        ))
    form = getattr(ir, "form_factor", None)
    if form is not None:
        if form.shape == "round":
            dims = f" Ø{form.diameter_mm:g} mm" if form.diameter_mm else ""
        else:
            dims = (
                f" {form.width_mm:g}x{form.height_mm:g} mm"
                if form.width_mm and form.height_mm else ""
            )
        lines.append(f"Recorded form factor: {form.shape}{dims}"
                     + (f" ({form.note})" if form.note else ""))
    return "\n".join(lines)


def _library_index_for_prompt() -> str:
    """Compact MPN catalog of the CORE (human-curated) parts so the agent never
    has to guess part numbers for the things people actually ask for. The full
    library is summarised by category count and reached via `search_parts` —
    listing all of it was ~900k tokens at 23,941 parts (2026-09-06)."""
    all_parts = library.all_parts()
    core = sorted(library.core_parts(), key=lambda p: (p.category, p.mpn))
    counts = Counter(p.category for p in all_parts)
    cats = ", ".join(f"{c} {n}" for c, n in counts.most_common())
    lines = [
        f"Library: {len(all_parts)} parts ({cats}).",
        f"Core index below lists the {len(core)} curated parts. For ANY other part "
        "— passives by value, MCUs, regulators, diodes, crystals — call "
        "`search_parts`; the catalogue is far larger than this list.",
        "",
    ]
    for p in core:
        v = ""
        if p.voltageMin is not None or p.voltageMax is not None:
            v = f" ({p.voltageMin or '?'}–{p.voltageMax or '?'}V)"
        lines.append(f"- {p.mpn} [{p.category}] {p.description}{v}")
    return "\n".join(lines)


# Description enrichment ------------------------------------------------------


def _enrich_descriptions(project: Project, llm=None) -> None:
    """Generate richer, datasheet-style descriptions for components that don't
    have one yet (i.e. newly added parts).

    Best-effort: any failure leaves the short library description (filled in on
    read) untouched. Every claim is grounded strictly on facts from the curated
    library so the model can't invent specs — same anti-hallucination stance as
    the agent loop. One batched call per turn, only when there's something new.
    """
    targets = [c for c in project.ir.components if not c.description]
    if not targets:
        return

    facts: dict[str, dict] = {}
    for c in targets:
        if c.mpn in facts:
            continue
        part = library.get_part(c.mpn)
        if not part:
            continue
        facts[c.mpn] = {
            "mpn": c.mpn,
            "short": getattr(part, "description", "") or "",
            "category": getattr(part, "category", "") or "",
            "package": getattr(part, "package", "") or "",
            "pins": [p.name for p in (c.pins or [])][:24],
        }
    if not facts:
        return

    system = (
        "You write concise, datasheet-style descriptions for electronic parts in "
        "an EDA tool. For each part write 2-3 plain sentences (~40-55 words total) "
        "covering what it is, its key interface/voltage, and a typical use. Base "
        "every claim ONLY on the supplied facts — never invent specifications, I2C "
        "addresses, pin counts, or features not implied by them. No marketing "
        'language. Return strict JSON: {"descriptions": {"<mpn>": "<text>"}}.'
    )
    user = json.dumps({"parts": list(facts.values())}, ensure_ascii=False)

    try:
        client = _make_openai_client(llm)
        model = _model_name(llm)
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(completion.choices[0].message.content or "{}")
        descs = data.get("descriptions", {}) if isinstance(data, dict) else {}
    except Exception:
        return

    if not isinstance(descs, dict):
        return

    for c in project.ir.components:
        if c.description:
            continue
        text = descs.get(c.mpn)
        if isinstance(text, str) and text.strip():
            c.description = text.strip()


# Persistence helper used by the API handler ---------------------------------


def commit(project: Project) -> None:
    store.upsert(project)
