import type { SqComponent, SqNet } from '@/lib/synqitData';

/**
 * Which functional block each part belongs to.
 *
 * A flat grid of symbols is technically a schematic and practically unreadable.
 * A real sheet groups parts by what they do — the microcontroller and its
 * crystal in one frame, the regulator and its caps in another — and titles each
 * frame. This module does that grouping from the IR alone: no coordinates, no
 * hand annotation.
 *
 * Two passes. Anchors (ICs, connectors, switches, actives) are classified by
 * what they are. Floaters (resistors, capacitors, inductors, ferrites) have no
 * identity of their own, so they follow the anchor they share the most
 * *specific* nets with — each shared net counts 1/(parts on that net), which
 * makes a two-pin feedback net worth ten times a ground rail. Ties fall back to
 * whichever anchor is nearest in the netlist, because the agent emits a chip
 * and then its decoupling.
 */
export type BlockKey =
  | 'mcu' | 'power' | 'usb' | 'rf' | 'sensors' | 'memory' | 'display'
  | 'drivers' | 'audio' | 'indicators' | 'controls' | 'connectors' | 'passives' | 'misc';

export const BLOCK_TITLES: Record<BlockKey, string> = {
  mcu: 'Microcontroller',
  power: 'Power supply',
  usb: 'USB',
  rf: 'Wireless',
  sensors: 'Sensors',
  memory: 'Memory & storage',
  display: 'Display',
  drivers: 'Drivers & output',
  audio: 'Audio',
  indicators: 'LEDs & indicators',
  controls: 'Buttons & switches',
  connectors: 'Connectors & headers',
  passives: 'Decoupling & passives',
  misc: 'Misc',
};

/** Reading order on the sheet: the brain first, then what powers it. */
export const BLOCK_ORDER: BlockKey[] = [
  'mcu', 'power', 'usb', 'rf', 'sensors', 'memory', 'display',
  'drivers', 'audio', 'indicators', 'controls', 'connectors', 'passives', 'misc',
];

// Tested in this order — an ESP32 module matches both `mcu` and `rf`, and it
// belongs under Microcontroller.
const MATCHERS: [BlockKey, RegExp][] = [
  ['mcu', /\b(ESP32|ESP8266|STM32|ATMEGA|ATTINY|ATSAM|RP2040|RP2350|NRF52|NRF51|SAMD\d|PIC\d|MSP430|CH32|GD32|MICROCONTROLLER|\bMCU\b|PICO|ARDUINO|TEENSY|PRO ?MICRO|XIAO)\b/],
  ['power', /(REGULATOR|\bLDO\b|BUCK|BOOST|CHARGER|CHARGING|AMS1117|LM1117|LM780|AP2112|AP7361|MCP170|XC6206|HT7333|TPS6|TPS7|TPS5|MP1584|LM2596|MT3608|TP4056|BQ2\d|MAX17|\bBATTERY\b|LIPO|LI-?ION|POWER SUPPLY|DC-?DC|VREG|18650|COIN CELL|CR20)/],
  ['usb', /(\bUSB\b|TYPE-?C|USB-?C|MICRO-?B|CH340|CH9102|CP210|FT23|FT232)/],
  ['rf', /(\bLORA\b|SX12|NRF24|RFM9|ANTENNA|BLUETOOTH|\bBLE\b|ZIGBEE|CC25|SIM800|SIM7|\bGSM\b|\bGPS\b|NEO-?6|NEO-?M|\bWIFI\b|\bLTE\b|NB-?IOT)/],
  ['display', /(\bOLED\b|\bLCD\b|\bTFT\b|DISPLAY|SSD13|SH110|ST77|ILI9|E-?PAPER|E-?INK|SEGMENT|MAX72|HT16K33)/],
  ['memory', /(\bFLASH\b|EEPROM|SD ?CARD|MICROSD|\bMEMORY\b|W25Q|AT24C|24LC|\bFRAM\b|\bSRAM\b|PSRAM|\bTF CARD\b)/],
  ['sensors', /(\bIMU\b|ACCELEROM|GYRO|MAGNETOM|MPU6|MPU9|BNO0|LSM6|LSM3|LIS3|ICM2|BMP2|BMP3|BME2|BME6|BMI\d|\bDHT\d|SHT\d|HDC\d|TMP1|DS18B|LM35|MAX31|BAROMET|ALTIMET|PRESSURE|HUMIDITY|TEMPERATURE|PROXIMIT|VL53|TCS3|APDS|\bLDR\b|PHOTORES|HALL EFFECT|CURRENT SENSE|INA2|INA1|ACS71|LOAD CELL|HX711|\bSENSOR\b|ENCODER DISC|\bADC\b|ADS1|MCP3\d)/],
  ['drivers', /(\bMOTOR\b|DRIVER|H-?BRIDGE|MOSFET|\bDRV8|L298|L293|A4988|TMC2|TB6\d|\bRELAY\b|SERVO|IRF\d|IRL\d|AO3\d|SI23|\bTRIAC\b|OPTOCOUP|PC817|MOC30|\bIGBT\b|SOLENOID|\bPUMP\b|\bFAN\b|\bHEATER\b)/],
  ['audio', /(BUZZER|SPEAKER|\bAUDIO\b|AMPLIFIER|PAM8|MAX98|MAX44|MICROPHONE|MEMS MIC|INMP4|\bI2S\b|PIEZO)/],
  ['indicators', /(\bLED\b|WS2812|SK6812|NEOPIXEL|\bRGB\b|APA102|INDICATOR|\bLAMP\b)/],
  ['controls', /(\bSWITCH\b|\bBUTTON\b|TACTILE|ROTARY ENCODER|POTENTIOM|KEYSWITCH|\bMX\b|KAILH|JOYSTICK|\bSPDT\b|\bSPST\b|\bDIP SW)/],
  ['connectors', /(CONNECTOR|\bHEADER\b|\bJST\b|TERMINAL|SCREW ?TERM|PIN ?HEADER|SOCKET|\bDSUB\b|\bDB9\b|BARREL ?JACK|MOLEX|\bIDC\b|\bFFC\b|\bFPC\b|RJ45|\bSMA\b|\bPAD\b)/],
];

/** When nothing matches, the reference designator still says a lot. */
const BY_PREFIX: Record<string, BlockKey> = {
  U: 'misc', IC: 'misc',
  Y: 'mcu', X: 'mcu',
  J: 'connectors', P: 'connectors', CN: 'connectors',
  SW: 'controls', S: 'controls',
  D: 'indicators', LED: 'indicators',
  Q: 'drivers', M: 'drivers', K: 'drivers',
  LS: 'audio', BZ: 'audio', SP: 'audio',
  BT: 'power', B: 'power', F: 'power', T: 'power',
};

/** Parts with no identity of their own — they follow their neighbours. */
const FLOATER = /^(R|C|L|FB|TP|MH)\d*$/;

function prefixOf(refdes: string): string {
  return refdes.replace(/\d+$/, '').toUpperCase();
}

/**
 * What the part *is*, in two strengths.
 *
 * `strong` is identity only — order code and value. `weak` adds the footprint
 * and the first sentence of the description, the "X is a Y" clause.
 *
 * Both softenings are load-bearing. The rest of a description is prose that
 * names other blocks ("...suitable for use with an Arduino or ESP32"), which
 * files a soil sensor under Microcontroller. And a module wears whatever
 * footprint it solders down as, so a soil sensor carries
 * `Connector_PinHeader_2.54mm:...` and lands under Connectors. Identity first,
 * the softer signals only if identity says nothing.
 */
function textsOf(c: SqComponent): { strong: string; weak: string } {
  const strong = [c.mpn, c.value].filter(Boolean).join(' ').toUpperCase();
  const firstSentence = (c.description ?? '').split(/\.\s/)[0];
  return { strong, weak: `${strong} ${c.footprint ?? ''} ${firstSentence}`.toUpperCase() };
}

function isFloater(c: SqComponent): boolean {
  return FLOATER.test(prefixOf(c.refdes) + '0');
}

function classifyAnchor(c: SqComponent): BlockKey {
  const { strong, weak } = textsOf(c);
  for (const [key, re] of MATCHERS) if (re.test(strong)) return key;
  for (const [key, re] of MATCHERS) if (re.test(weak)) return key;
  return BY_PREFIX[prefixOf(c.refdes)] ?? 'misc';
}

export function assignBlocks(components: SqComponent[], nets: SqNet[]): Map<string, BlockKey> {
  const index = new Map(components.map((c, i) => [c.refdes, i]));
  const out = new Map<string, BlockKey>();

  const anchors: SqComponent[] = [];
  const floaters: SqComponent[] = [];
  for (const c of components) (isFloater(c) ? floaters : anchors).push(c);

  for (const c of anchors) out.set(c.refdes, classifyAnchor(c));

  // Nothing to anchor to: every part is its own island, so one frame each by
  // designator alone rather than a single meaningless "Misc" box.
  if (!anchors.length) {
    for (const c of floaters) out.set(c.refdes, BY_PREFIX[prefixOf(c.refdes)] ?? 'passives');
    return out;
  }

  // A net with three parts or fewer says something specific: this resistor
  // belongs to that LED. A rail with a dozen parts says only "it is powered".
  // Score the two separately rather than blending them.
  const LOCAL_NET = 3;
  type Link = { local: number; bulk: number };
  const linksByRef = new Map<string, Map<string, Link>>();
  for (const n of nets) {
    const refs = [...new Set(n.pins.map(([r]) => r))];
    if (refs.length < 2) continue;
    const w = 1 / refs.length;
    const local = refs.length <= LOCAL_NET;
    for (const r of refs) {
      let m = linksByRef.get(r);
      if (!m) linksByRef.set(r, (m = new Map()));
      for (const other of refs) {
        if (other === r) continue;
        const link = m.get(other) ?? { local: 0, bulk: 0 };
        if (local) link.local += w;
        else link.bulk += w;
        m.set(other, link);
      }
    }
  }

  const pinCount = new Map(components.map((c) => [c.refdes, c.pins.length]));

  for (const f of floaters) {
    let best: BlockKey | null = null;
    let bestScore = 0;
    let bestPins = Infinity;
    let bestGap = Infinity;
    for (const [ref, link] of linksByRef.get(f.refdes) ?? []) {
      const block = out.get(ref);
      if (!block || link.local <= 0) continue; // a floater, or rails only
      const pins = pinCount.get(ref) ?? 0;
      const gap = Math.abs((index.get(ref) ?? 0) - (index.get(f.refdes) ?? 0));
      // Equally connected? Follow the smaller part. Three identical LED
      // resistors each touch the LED and the MCU on the same 3-pin nets; the
      // MCU touches everything, so the LED is the one that means something.
      const better =
        link.local > bestScore + 1e-9 ||
        (Math.abs(link.local - bestScore) < 1e-9 && (pins < bestPins || (pins === bestPins && gap < bestGap)));
      if (better) {
        best = block;
        bestScore = link.local;
        bestPins = pins;
        bestGap = gap;
      }
    }
    // Only rails: a 100nF part between 3V3 and GND is decoupling *something*,
    // and the netlist genuinely does not say what. Guessing scatters identical
    // parts across the sheet, so they get their own bank instead.
    out.set(f.refdes, best ?? 'passives');
  }

  return out;
}
