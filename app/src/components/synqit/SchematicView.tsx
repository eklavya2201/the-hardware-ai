import { useMemo, useState } from 'react';
import { usePanZoom, ZoomControls } from './usePanZoom';
import {
  classifyNet,
  hashedColor,
  NET_COLORS,
  shortPartName,
  type SqComponent,
  type SqNet,
} from '@/lib/synqitData';

/**
 * Native, read-only schematic in the studio theme. Symbols are boxes with
 * pins on the sides the IR declares; two-pin signal nets are drawn as wires,
 * everything else (power, ground, buses, nets with 3+ pins) as coloured net
 * labels at each pin, the way a readable hand-drawn schematic does it. Colour
 * follows the net's function (power red, ground slate, SDA blue, SCL yellow,
 * UART green, SPI purple, USB teal), so the same convention as the PCB side.
 *
 * Layout: the IR's own x/y when present (the Synqit editor placed them),
 * otherwise a grid ordered ICs → connectors → passives.
 */
const PIN_PITCH = 7;
const BOX_MIN_W = 42;
const PAD = 3;

type Placed = { c: SqComponent; x: number; y: number; w: number; h: number; pinPos: Map<string, { x: number; y: number; side: string; name: string }> };

function rank(refdes: string): number {
  const p = refdes.replace(/[0-9]+$/, '');
  return { U: 0, Y: 1, J: 2, SW: 3, D: 4, L: 5, Q: 6, R: 7, C: 8 }[p] ?? 9;
}

function layout(components: SqComponent[]): Placed[] {
  // Always the compact grid: the IR's x/y are the Synqit editor's sprawling
  // canvas coordinates and read badly at panel size.
  const hasXY = false;
  const sorted = [...components].sort((a, b) => rank(a.refdes) - rank(b.refdes) || a.refdes.localeCompare(b.refdes, undefined, { numeric: true }));
  const placed: Placed[] = [];
  let gx = 0;
  let gy = 0;
  let rowH = 0;
  const cols = Math.max(3, Math.ceil(Math.sqrt(sorted.length * 1.6)));
  sorted.forEach((c, i) => {
    const left = c.pins.filter((p) => (p.side ?? (Number(p.number) % 2 ? 'left' : 'right')) === 'left');
    const right = c.pins.filter((p) => (p.side ?? (Number(p.number) % 2 ? 'left' : 'right')) === 'right');
    const top = c.pins.filter((p) => p.side === 'top');
    const bottom = c.pins.filter((p) => p.side === 'bottom');
    const longest = Math.max(4, ...c.pins.map((p) => (p.name || p.number).length));
    const w = Math.max(BOX_MIN_W, longest * 2 * 3.2 + 14, (Math.max(top.length, bottom.length) + 1) * PIN_PITCH);
    const h = Math.max(PIN_PITCH * (Math.max(left.length, right.length) + 1), 20);
    let x: number;
    let y: number;
    if (hasXY) {
      x = (c.x ?? 0) * 0.5;
      y = (c.y ?? 0) * 0.5;
    } else {
      if (i % cols === 0 && i) {
        gx = 0;
        gy += rowH + 40;
        rowH = 0;
      }
      x = gx;
      y = gy;
      gx += w + 60;
      rowH = Math.max(rowH, h);
    }
    const pinPos = new Map<string, { x: number; y: number; side: string; name: string }>();
    left.forEach((p, k) => pinPos.set(p.number, { x: x - PAD, y: y + PIN_PITCH * (k + 1), side: 'left', name: p.name || p.number }));
    right.forEach((p, k) => pinPos.set(p.number, { x: x + w + PAD, y: y + PIN_PITCH * (k + 1), side: 'right', name: p.name || p.number }));
    top.forEach((p, k) => pinPos.set(p.number, { x: x + PIN_PITCH * (k + 1), y: y - PAD, side: 'top', name: p.name || p.number }));
    bottom.forEach((p, k) => pinPos.set(p.number, { x: x + PIN_PITCH * (k + 1), y: y + h + PAD, side: 'bottom', name: p.name || p.number }));
    placed.push({ c, x, y, w, h, pinPos });
  });
  return placed;
}

export function SchematicView({ components, nets }: { components: SqComponent[]; nets: SqNet[] }) {
  const placed = useMemo(() => layout(components), [components]);
  const byRef = useMemo(() => new Map(placed.map((p) => [p.c.refdes, p])), [placed]);

  const bounds = useMemo(() => {
    const xs = placed.flatMap((p) => [p.x - 30, p.x + p.w + 30]);
    const ys = placed.flatMap((p) => [p.y - 20, p.y + p.h + 20]);
    const x = Math.min(...xs, 0);
    const y = Math.min(...ys, 0);
    return { x, y, w: Math.max(...xs, 100) - x, h: Math.max(...ys, 60) - y };
  }, [placed]);
  const { svgRef, view, viewBox, handlers, zoomIn, zoomOut, reset, dragged } = usePanZoom(bounds);
  const [selected, setSelected] = useState<string | null>(null);

  const netInfo = useMemo(
    () =>
      nets.map((n) => {
        const pinNames = n.pins.map(([r, p]) => byRef.get(r)?.pinPos.get(p)?.name ?? '');
        const cls = classifyNet(n.name, pinNames);
        const color = cls === 'signal' ? hashedColor(n.name) : NET_COLORS[cls];
        const ends = n.pins.map(([r, p]) => byRef.get(r)?.pinPos.get(p)).filter(Boolean) as { x: number; y: number; side: string }[];
        const asWire = cls !== 'power' && cls !== 'ground' && n.pins.length === 2 && ends.length === 2;
        return { n, cls, color, ends, asWire };
      }),
    [nets, byRef],
  );

  const fs = Math.max(2.2, view.w / 220);

  return (
    <div className="relative h-full w-full select-none bg-sq-neutral-700">
      <svg
        ref={svgRef}
        className="h-full w-full cursor-grab active:cursor-grabbing"
        viewBox={viewBox}
        {...handlers}
        style={{ fontFamily: 'ui-monospace, monospace' }}
      >
        {/* wires first, then labels, then symbols on top */}
        {netInfo.map(({ n, color, ends, asWire }) =>
          asWire ? (
            <path
              key={n.name}
              d={`M ${ends[0].x} ${ends[0].y} H ${(ends[0].x + ends[1].x) / 2} V ${ends[1].y} H ${ends[1].x}`}
              fill="none"
              stroke={color}
              strokeWidth={selected === n.name ? 1.4 : 0.7}
              opacity={selected && selected !== n.name ? 0.25 : 1}
              onClick={() => !dragged() && setSelected(selected === n.name ? null : n.name)}
              style={{ cursor: 'pointer' }}
            />
          ) : null,
        )}
        {netInfo.map(({ n, color, ends, asWire }) =>
          !asWire
            ? ends.map((e, i) => {
                const dir = e.side === 'left' ? -1 : e.side === 'right' ? 1 : 0;
                const vdir = e.side === 'top' ? -1 : e.side === 'bottom' ? 1 : 0;
                const lx = e.x + dir * 6;
                const ly = e.y + vdir * 6;
                return (
                  <g key={`${n.name}-${i}`} opacity={selected && selected !== n.name ? 0.25 : 1} onClick={() => !dragged() && setSelected(selected === n.name ? null : n.name)} style={{ cursor: 'pointer' }}>
                    <line x1={e.x} y1={e.y} x2={lx} y2={ly} stroke={color} strokeWidth={0.7} />
                    <text x={lx + dir * 1.2} y={ly + (vdir ? vdir * 3 : 0)} fontSize={fs} fill={color} textAnchor={dir < 0 ? 'end' : dir > 0 ? 'start' : 'middle'} dominantBaseline="middle">
                      {n.name}
                    </text>
                  </g>
                );
              })
            : null,
        )}
        {placed.map((p) => (
          <g key={p.c.refdes}>
            <rect x={p.x} y={p.y} width={p.w} height={p.h} rx={1.5} fill="#1f2326" stroke="#4b5563" strokeWidth={0.6} />
            <text x={p.x + p.w / 2} y={p.y - 2.5} fontSize={fs * 1.15} fontWeight={600} fill="#e5e7eb" textAnchor="middle">
              {p.c.refdes}
            </text>
            <text x={p.x + p.w / 2} y={p.y + p.h + fs + 1} fontSize={fs * 0.9} fill="#9ca3af" textAnchor="middle">
              {shortPartName(p.c)}
            </text>
            {[...p.pinPos.entries()].map(([num, pin]) => {
              const inward = pin.side === 'left' ? 1 : pin.side === 'right' ? -1 : 0;
              const vin = pin.side === 'top' ? 1 : pin.side === 'bottom' ? -1 : 0;
              return (
                <g key={num}>
                  <line x1={pin.x} y1={pin.y} x2={pin.x + inward * PAD} y2={pin.y + vin * PAD} stroke="#9ca3af" strokeWidth={0.5} />
                  <circle cx={pin.x} cy={pin.y} r={0.7} fill="#9ca3af" />
                  {inward !== 0 && (
                    <text x={pin.x + inward * (PAD + 1.2)} y={pin.y} fontSize={fs * 0.85} fill="#cbd5e1" textAnchor={inward > 0 ? 'start' : 'end'} dominantBaseline="middle">
                      {pin.name}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        ))}
      </svg>
      <div className="pointer-events-none absolute bottom-3 right-3 flex flex-col items-end gap-1">
        <ZoomControls zoomIn={zoomIn} zoomOut={zoomOut} reset={reset} />
      <div className="rounded-md bg-sq-bg-dark/80 px-2 py-1 text-[11px] text-sq-text-secondary backdrop-blur">
        {components.length} parts · {nets.length} nets{selected ? ` · ${selected}` : ''} · wheel zoom, drag pan, double-click fit
      </div>
      </div>
      <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap gap-x-3 gap-y-1 rounded-md bg-sq-bg-dark/80 px-2 py-1 text-[10px] text-sq-text-secondary backdrop-blur">
        {(['power', 'ground', 'sda', 'scl', 'uart', 'spi', 'usb'] as const).map((k) => (
          <span key={k} className="flex items-center gap-1">
            <span className="inline-block h-1.5 w-3 rounded" style={{ background: NET_COLORS[k] }} />
            {k === 'sda' ? 'SDA' : k === 'scl' ? 'SCL' : k.toUpperCase()}
          </span>
        ))}
      </div>
    </div>
  );
}
