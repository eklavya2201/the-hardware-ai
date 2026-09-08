import { useEffect, useState } from 'react';
import { SYNQIT_API_BASE } from '@/components/auth/SynqitSignInButton';

/**
 * Read-only view of a Synqit project for the studio's native PCB and
 * schematic renderers. One fetch of GET /projects/{id} with the Synqit
 * cookie, cached per id for the session. Only the fields the views draw.
 */
export type SqPin = { number: string; name: string; side?: 'left' | 'right' | 'top' | 'bottom'; type?: string };
export type SqComponent = {
  refdes: string;
  mpn: string;
  value?: string | null;
  description?: string | null;
  footprint?: string | null;
  pins: SqPin[];
  x?: number;
  y?: number;
  rotation?: 0 | 90 | 180 | 270;
};
export type SqNet = { name: string; pins: [string, string][] };
export type SqPad = {
  pin_number: string;
  x: number;
  y: number;
  width: number;
  height: number;
  shape?: string;
  pad_type?: 'smd' | 'tht' | string;
  layer?: string;
};
export type SqPlaced = {
  refdes: string;
  mpn: string;
  x: number;
  y: number;
  rotation: 0 | 90 | 180 | 270;
  layer: 'top' | 'bottom';
  footprint: { name: string; width: number; height: number; pads: SqPad[]; bodyRects?: number[][] | null };
};
export type SqTrace = { net: string; layer: string; width: number; segments: { x1: number; y1: number; x2: number; y2: number }[] };
export type SqVia = { x: number; y: number; drill: number; diameter: number; net?: string };
export type SqRat = { from?: [string, string]; to?: [string, string]; net?: string; x1?: number; y1?: number; x2?: number; y2?: number };
export type SqBoard = {
  width: number;
  height: number;
  shape: 'rect' | 'round' | 'polygon' | string;
  outline_points: { x: number; y: number }[];
  layer_count: number;
  components: SqPlaced[];
  traces: SqTrace[];
  vias: SqVia[];
  rats: SqRat[];
};
export type SqMechanical = {
  outline?: { shape: string; width: number; height: number; corner_radius?: number; points?: { x: number; y: number }[] } | null;
  mountingHoles?: { id: string; x: number; y: number; diameter: number; plated?: boolean }[];
  cutouts?: { refdes?: string; edge: string; x: number; y: number; width: number; height: number }[];
};
export type SqProject = {
  id: string;
  name: string;
  ir: { components: SqComponent[]; nets: SqNet[] };
  board: SqBoard | null;
  mechanical: SqMechanical | null;
  issues?: { rule?: string; severity?: string; message?: string; refdes?: string; net?: string }[];
};

const cache = new Map<string, Promise<SqProject>>();

export function fetchSynqitProject(id: string, force = false): Promise<SqProject> {
  if (!force && cache.has(id)) return cache.get(id)!;
  const p = fetch(`${SYNQIT_API_BASE}/projects/${encodeURIComponent(id)}`, { credentials: 'include' }).then(
    async (res) => {
      if (res.status === 401) throw new Error('not_signed_in');
      if (!res.ok) throw new Error(`synqit ${res.status}`);
      return (await res.json()) as SqProject;
    },
  );
  cache.set(id, p);
  p.catch(() => cache.delete(id));
  return p;
}

export function useSynqitProjectData(id: string | null) {
  const [state, setState] = useState<{ data: SqProject | null; error: string | null; loading: boolean }>({
    data: null,
    error: null,
    loading: Boolean(id),
  });
  useEffect(() => {
    if (!id) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fetchSynqitProject(id).then(
      (data) => live && setState({ data, error: null, loading: false }),
      (err) => live && setState({ data: null, error: err instanceof Error ? err.message : String(err), loading: false }),
    );
    return () => {
      live = false;
    };
  }, [id]);
  return state;
}

/** Net colour by function, the hookup-guide convention hobbyists know. */
export type NetClass = 'power' | 'ground' | 'sda' | 'scl' | 'uart' | 'spi' | 'usb' | 'analog' | 'pwm' | 'signal';
export const NET_COLORS: Record<NetClass, string> = {
  power: '#ef4444',
  ground: '#94a3b8',
  sda: '#3b82f6',
  scl: '#eab308',
  uart: '#22c55e',
  spi: '#a855f7',
  usb: '#14b8a6',
  analog: '#f97316',
  pwm: '#ec4899',
  signal: '#9ca3af',
};

export function classifyNet(name: string, pinNames: string[] = []): NetClass {
  const n = name.toUpperCase();
  const p = pinNames.join(' ').toUpperCase();
  if (/^(GND|AGND|DGND|VSS|PGND)/.test(n) || /\bGND\b/.test(n)) return 'ground';
  if (/^(VCC|VDD|VBUS|VBAT|VIN|V?[0-9]V[0-9]|\+?[0-9]+V|3V3|5V|12V|VSYS|VOUT)/.test(n) || /^V[A-Z0-9_]*$/.test(n)) return 'power';
  if (/SDA/.test(n) || /SDA/.test(p)) return 'sda';
  if (/SCL/.test(n) || /SCL/.test(p)) return 'scl';
  if (/\b(TX|RX|TXD|RXD|UART)/.test(n) || /\b(TXD?|RXD?)\b/.test(p)) return 'uart';
  if (/(MOSI|MISO|SCK|SCLK|\bCS\b|SPI)/.test(n) || /(MOSI|MISO|SCK)/.test(p)) return 'spi';
  if (/(D\+|D-|DP|DM|USB)/.test(n)) return 'usb';
  if (/(ADC|AIN|ANALOG|SENSE)/.test(n)) return 'analog';
  if (/PWM/.test(n)) return 'pwm';
  return 'signal';
}

/** Stable palette colour for plain signal nets so the same net keeps its colour everywhere. */
export function hashedColor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const hue = h % 360;
  return `hsl(${hue} 55% 62%)`;
}

/**
 * A short, human name for a part — what an engineer says out loud, not the
 * order code. "ESP32-WROOM-32" -> "ESP32", "STM32F412RET6" -> "STM32F4",
 * a 10uF capacitor -> "10µF". Used on the PCB silkscreen and under schematic
 * symbols, where the full manufacturer part number is noise.
 */
const FAMILIES: [RegExp, string][] = [
  [/^ESP32[-_]?(C\d)/i, 'ESP32-$1'],
  [/^ESP32[-_]?(S\d)/i, 'ESP32-$1'],
  [/^(ESP32)/i, '$1'],
  [/^(ESP8266)/i, '$1'],
  [/^(STM32[A-Z]\d)/i, '$1'],
  [/^(ATMEGA\d+|ATTINY\d+)/i, '$1'],
  [/^(RP2040|RP2350)/i, '$1'],
  [/^(NRF\d{4})/i, '$1'],
  [/^(BNO\d{3}|BMP\d{3}|BME\d{3}|MPU[-_]?\d{4}|ICM[-_]?\d+|LIS\d[A-Z]*)/i, '$1'],
  [/^(SSD\d{4}|ILI\d{4})/i, '$1'],
  [/^(TLC\d{3}|NE555|LM\d{3,4}|TL\d{3})/i, '$1'],
  [/^(AMS1117|AP\d{4}|MCP\d{4}|TPS\d{4,5}|XC\d{4}|RT\d{4})/i, '$1'],
  [/^(CH\d{3}[A-Z]?|CP\d{4}|FT\d{3}[A-Z]?)/i, '$1'],
  [/^(DRV\d{4}|TB\d{4}|A4988|TMC\d{4})/i, '$1'],
  [/^(W25Q\d+|AT24C\d+|24LC\d+)/i, '$1'],
  [/^(TP\d{4}|MAX\d{3,4}|INA\d{3})/i, '$1'],
];

export function shortPartName(part: {
  mpn?: string | null;
  value?: string | null;
  description?: string | null;
  refdes?: string;
}): string {
  const mpn = (part.mpn || '').trim();
  const value = (part.value || '').trim();
  const prefix = (part.refdes || '').replace(/\d+$/, '').toUpperCase();

  // Passives: the value IS the name.
  if (
    value &&
    value.length <= 12 &&
    (['R', 'C', 'L', 'Y'].includes(prefix) ||
      /^[\d.]+\s*(µ|u|n|p|k|M|m)?(F|Ω|ohm|H|Hz)/i.test(value))
  ) {
    return value;
  }
  for (const [re, out] of FAMILIES) {
    const m = mpn.match(re);
    if (m) return out.replace(/\$(\d)/g, (_, i) => m[Number(i)] ?? '');
  }
  const hay = (mpn + ' ' + (part.description ?? '')).toUpperCase();
  if (/USB[-_ ]?C|TYPE[-_ ]?C/.test(hay)) return 'USB-C';
  if (/MICRO[-_ ]?USB/.test(hay)) return 'microUSB';
  const jst = /JST[-_ ]?(PH|XH|SH|ZH)/.exec(hay);
  if (jst) return 'JST-' + jst[1];
  if (/PIN\s?HEADER|PINHEADER|HEADER/.test(hay)) return 'header';
  if (/TACTILE|PUSH\s?BUTTON|SW_PUSH|SWITCH/.test(hay)) return 'button';
  if (/BUZZER/.test(hay)) return 'buzzer';
  if (/CRYSTAL|OSCILLATOR/.test(hay)) return value || 'crystal';
  if (/BATTERY|18650|LIPO/.test(hay)) return 'battery';
  if (/\bLED\b/.test(hay)) return 'LED';
  if (/DIODE|SCHOTTKY/.test(hay)) return 'diode';
  if (/MOSFET|TRANSISTOR/.test(hay)) return 'FET';

  if (!mpn) return part.refdes || '';
  const head = mpn.split(/[-_/\s]/)[0];
  return (head.length >= 3 ? head : mpn).slice(0, 12);
}
