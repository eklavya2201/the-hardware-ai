import { useMemo, useState } from 'react';
import { usePanZoom, ZoomControls } from './usePanZoom';
import {
  classifyNet,
  hashedColorPaper,
  NET_COLORS_PAPER,
  shortPartName,
  type SqComponent,
  type SqNet,
} from '@/lib/synqitData';
import { FRAME_TITLE, PAD, layout, placeNetLabels } from './schematicLayout';

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
export function SchematicView({ components, nets }: { components: SqComponent[]; nets: SqNet[] }) {
  const { placed, frames } = useMemo(() => layout(components, nets), [components, nets]);
  const byRef = useMemo(() => new Map(placed.map((p) => [p.c.refdes, p])), [placed]);

  const bounds = useMemo(() => {
    // Net labels and part names spill outside both the symbols and the frames.
    const xs = [...placed.flatMap((p) => [p.x - 30, p.x + p.w + 30]), ...frames.flatMap((f) => [f.x - 8, f.x + f.w + 8])];
    const ys = [...placed.flatMap((p) => [p.y - 20, p.y + p.h + 20]), ...frames.flatMap((f) => [f.y - 8, f.y + f.h + 8])];
    const x = Math.min(...xs, 0);
    const y = Math.min(...ys, 0);
    return { x, y, w: Math.max(...xs, 100) - x, h: Math.max(...ys, 60) - y };
  }, [placed, frames]);
  const { svgRef, view, viewBox, handlers, zoomIn, zoomOut, reset, dragged } = usePanZoom(bounds);
  const [selected, setSelected] = useState<string | null>(null);

  const netInfo = useMemo(
    () =>
      nets.map((n) => {
        const pinNames = n.pins.map(([r, p]) => byRef.get(r)?.pinPos.get(p)?.name ?? '');
        const cls = classifyNet(n.name, pinNames);
        const color = cls === 'signal' ? hashedColorPaper(n.name) : NET_COLORS_PAPER[cls];
        const ends = n.pins.map(([r, p]) => byRef.get(r)?.pinPos.get(p)).filter(Boolean) as { x: number; y: number; side: string }[];
        const asWire = cls !== 'power' && cls !== 'ground' && n.pins.length === 2 && ends.length === 2;
        return { n, cls, color, ends, asWire };
      }),
    [nets, byRef],
  );

  // Type is sized against the visible width, so it stays legible at any zoom.
  const fs = Math.max(3, view.w / 150);

  // Every net label that is drawn as a name rather than a wire, with
  // overlaps resolved before anything reaches the canvas.
  const labels = useMemo(() => {
    const anchors = netInfo.flatMap(({ n, color, ends, asWire }) =>
      asWire
        ? []
        : ends.map((e, i) => ({ key: `${n.name}-${i}`, x: e.x, y: e.y, side: e.side, text: n.name, color })),
    );
    const byKey = new Map(anchors.map((a) => [a.key, a.color]));
    return placeNetLabels(anchors, fs).map((l) => ({ ...l, color: byKey.get(l.key) ?? "#4b5563" }));
  }, [netInfo, fs]);


  return (
    <div className="relative h-full w-full select-none bg-white">
      <svg
        ref={svgRef}
        className="h-full w-full cursor-grab active:cursor-grabbing"
        viewBox={viewBox}
        {...handlers}
        style={{ fontFamily: 'ui-monospace, monospace' }}
      >
        {/* block frames underneath everything, the way a sheet is drawn */}
        {frames.map((f) => (
          <g key={f.key}>
            <rect x={f.x} y={f.y} width={f.w} height={f.h} rx={2} fill="none" stroke="#9aa4b2" strokeWidth={0.55} />
            <text
              x={f.x + f.w / 2}
              y={f.y + FRAME_TITLE * 0.72}
              fontSize={fs * 1.05}
              fontWeight={600}
              fill="#64748b"
              textAnchor="middle"
              style={{ letterSpacing: '0.12em' }}
            >
              {f.title.toUpperCase()}
            </text>
          </g>
        ))}
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
        {labels.map((l) => (
          <g
            key={l.key}
            opacity={selected && selected !== l.text ? 0.25 : 1}
            onClick={() => !dragged() && setSelected(selected === l.text ? null : l.text)}
            style={{ cursor: "pointer" }}
          >
            {/* The leader follows the label to wherever it was pushed, so a
                moved name still points at its own pin. */}
            <line
              x1={l.x}
              y1={l.y}
              x2={l.side === "left" ? l.lx + l.w : l.side === "right" ? l.lx : l.lx + l.w / 2}
              y2={l.ly + l.h / 2}
              stroke={l.color}
              strokeWidth={0.7}
            />
            <text
              x={l.side === "left" ? l.lx + l.w : l.lx}
              y={l.ly + l.h / 2}
              fontSize={fs}
              fill={l.color}
              textAnchor={l.side === "left" ? "end" : l.side === "right" ? "start" : "middle"}
              dominantBaseline="middle"
            >
              {l.text}
            </text>
          </g>
        ))}
        {placed.map((p) => (
          <g key={p.c.refdes}>
            <rect x={p.x} y={p.y} width={p.w} height={p.h} rx={1.5} fill="#fdf6cf" stroke="#8a2b2b" strokeWidth={0.7} />
            <text x={p.x + p.w / 2} y={p.y - 2.5} fontSize={fs * 1.15} fontWeight={600} fill="#111827" textAnchor="middle">
              {p.c.refdes}
            </text>
            <text x={p.x + p.w / 2} y={p.y + p.h + fs + 1} fontSize={fs * 0.95} fill="#6b5a1f" textAnchor="middle">
              {shortPartName(p.c)}
            </text>
            {[...p.pinPos.entries()].map(([num, pin]) => {
              const inward = pin.side === 'left' ? 1 : pin.side === 'right' ? -1 : 0;
              const vin = pin.side === 'top' ? 1 : pin.side === 'bottom' ? -1 : 0;
              return (
                <g key={num}>
                  <line x1={pin.x} y1={pin.y} x2={pin.x + inward * PAD} y2={pin.y + vin * PAD} stroke="#8a2b2b" strokeWidth={0.55} />
                  <circle cx={pin.x} cy={pin.y} r={0.7} fill="#8a2b2b" />
                  {inward !== 0 && (
                    <text x={pin.x + inward * (PAD + 1.2)} y={pin.y} fontSize={fs * 0.9} fill="#1f2937" textAnchor={inward > 0 ? 'start' : 'end'} dominantBaseline="middle">
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
            <span className="inline-block h-1.5 w-3 rounded" style={{ background: NET_COLORS_PAPER[k] }} />
            {k === 'sda' ? 'SDA' : k === 'scl' ? 'SCL' : k.toUpperCase()}
          </span>
        ))}
      </div>
    </div>
  );
}
