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
