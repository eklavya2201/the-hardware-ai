import { useMemo, useState } from 'react';
import { usePanZoom, ZoomControls } from './usePanZoom';
import {
  shortPartName,
  type SqBoard,
  type SqComponent,
  type SqMechanical,
  type SqPlaced,
} from '@/lib/synqitData';

/**
 * Native, read-only PCB view in the studio theme. Draws exactly what the
 * board JSON says: outline, mounting holes, part bodies with refdes, pads,
 * traces per layer, vias and unrouted ratsnest. IR frame is Y-up with the
 * origin at the board's bottom-left; SVG is Y-down, so everything is drawn
 * inside one group flipped in Y. Wheel zooms, drag pans, double-click fits.
 *
 * Physical colours stay literal on purpose (soldermask, copper): they are
 * materials, not chrome.
 */
const COPPER_TOP = '#d9a441';
const COPPER_BOTTOM = '#5b8def';
const MASK = '#17372a';
const MASK_EDGE = '#2f6b4c';
const SILK = '#e5e7eb';
const HOLE = '#111111';

type Layer = 'top' | 'bottom';

function rotatePad(px: number, py: number, rot: number): [number, number] {
  switch (((rot % 360) + 360) % 360) {
    case 90:
      return [-py, px];
    case 180:
      return [-px, -py];
    case 270:
      return [py, -px];
    default:
      return [px, py];
  }
}

export function PcbView({
  board,
  mechanical,
  components = [],
}: {
  board: SqBoard;
  mechanical: SqMechanical | null;
  /** IR components, for value and description when naming parts. */
  components?: SqComponent[];
}) {
  // Silkscreen labels: the part's short name ("ESP32", "10uF") rather than the
  // order code, with the reference designator as the alternative view.
  const [labelMode, setLabelMode] = useState<'name' | 'ref' | 'none'>('name');
  const names = useMemo(() => {
    const byRef = new Map(components.map((c) => [c.refdes, c]));
    const out = new Map<string, string>();
    for (const pc of board.components) {
      const ir = byRef.get(pc.refdes);
      out.set(
        pc.refdes,
        shortPartName({ mpn: pc.mpn, value: ir?.value, description: ir?.description, refdes: pc.refdes }),
      );
    }
    return out;
  }, [board.components, components]);
  const [show, setShow] = useState<{ top: boolean; bottom: boolean; rats: boolean }>({
    top: true,
    bottom: true,
    rats: true,
  });

  const W = board.width;
  const H = board.height;
  const margin = Math.max(W, H) * 0.08 + 2;
  const fit = useMemo(() => ({ x: -margin, y: -margin, w: W + 2 * margin, h: H + 2 * margin }), [W, H, margin]);
  const { svgRef, view, viewBox, handlers, zoomIn, zoomOut, reset } = usePanZoom(fit);

  const outlinePath = useMemo(() => {
    const o = mechanical?.outline;
    const r = o?.corner_radius ?? 0;
    if ((o?.shape === 'polygon' && o.points?.length) || (board.shape === 'polygon' && board.outline_points.length)) {
      const pts = o?.points?.length ? o.points : board.outline_points;
      return `M ${pts.map((p) => `${p.x} ${p.y}`).join(' L ')} Z`;
    }
    if (o?.shape === 'circle' || board.shape === 'round') {
      const d = W;
      return `M ${W / 2 - d / 2} ${H / 2} a ${d / 2} ${d / 2} 0 1 0 ${d} 0 a ${d / 2} ${d / 2} 0 1 0 ${-d} 0`;
    }
    const rr = Math.min(r, W / 2, H / 2);
    return `M ${rr} 0 H ${W - rr} A ${rr} ${rr} 0 0 1 ${W} ${rr} V ${H - rr} A ${rr} ${rr} 0 0 1 ${W - rr} ${H} H ${rr} A ${rr} ${rr} 0 0 1 0 ${H - rr} V ${rr} A ${rr} ${rr} 0 0 1 ${rr} 0 Z`;
  }, [board, mechanical, W, H]);


  const strokeScale = view.w / 400; // keep hairlines readable at any zoom
  const fontSize = Math.max(0.9, view.w / 60);

  const renderPart = (pc: SqPlaced) => {
    const fp = pc.footprint;
    const bw = fp.width || 1;
    const bh = fp.height || 1;
    const rot = pc.rotation || 0;
    const swap = rot === 90 || rot === 270;
    const w = swap ? bh : bw;
    const h = swap ? bw : bh;
    const isTop = pc.layer === 'top';
    if ((isTop && !show.top) || (!isTop && !show.bottom)) return null;
    const copper = isTop ? COPPER_TOP : COPPER_BOTTOM;
    return (
      <g key={pc.refdes} opacity={isTop ? 1 : 0.7}>
        <rect
          x={pc.x - w / 2}
          y={pc.y - h / 2}
          width={w}
          height={h}
          fill={isTop ? 'rgba(229,231,235,0.10)' : 'rgba(91,141,239,0.10)'}
          stroke={SILK}
          strokeWidth={0.12 * strokeScale + 0.05}
          strokeDasharray={isTop ? undefined : '0.6 0.4'}
        />
        {fp.pads.map((pad, i) => {
          const [rx, ry] = rotatePad(pad.x, pad.y, rot);
          const pswap = swap;
          const pw = pswap ? pad.height : pad.width;
          const ph = pswap ? pad.width : pad.height;
          const tht = pad.pad_type === 'tht';
          return (
            <g key={`${pc.refdes}-${pad.pin_number}-${i}`}>
              <rect
                x={pc.x + rx - pw / 2}
                y={pc.y + ry - ph / 2}
                width={pw}
                height={ph}
                rx={pad.shape === 'round' || pad.shape === 'circle' ? Math.min(pw, ph) / 2 : pad.shape === 'rounded' ? Math.min(pw, ph) * 0.25 : 0}
                fill={tht ? COPPER_TOP : copper}
              />
              {tht && <circle cx={pc.x + rx} cy={pc.y + ry} r={Math.min(pw, ph) * 0.3} fill={HOLE} />}
            </g>
          );
        })}
        {labelMode !== 'none' && (
          <text
            x={pc.x}
            y={pc.y}
            fontSize={Math.min(fontSize, Math.max(0.7, Math.min(h * 0.5, (w * 1.7) / Math.max(2, (labelMode === 'name' ? names.get(pc.refdes) ?? pc.refdes : pc.refdes).length))))}
            fill={SILK}
            textAnchor="middle"
            dominantBaseline="middle"
            transform={`translate(${pc.x} ${pc.y}) scale(1 -1) translate(${-pc.x} ${-pc.y})`}
            style={{ fontFamily: 'ui-monospace, monospace', pointerEvents: 'none' }}
          >
            {labelMode === 'name' ? names.get(pc.refdes) ?? pc.refdes : pc.refdes}
          </text>
        )}
      </g>
    );
  };

  const layers: Layer[] = ['bottom', 'top'];
  return (
    <div className="relative h-full w-full select-none bg-sq-neutral-700">
      <svg
        ref={svgRef}
        className="h-full w-full cursor-grab active:cursor-grabbing"
        viewBox={viewBox}
        {...handlers}
      >
        {/* flip to Y-up around the board's mid height */}
        <g transform={`translate(0 ${H}) scale(1 -1)`}>
          <path d={outlinePath} fill={MASK} stroke={MASK_EDGE} strokeWidth={0.3 * strokeScale + 0.1} />
          {layers.map((layer) =>
            show[layer]
              ? board.traces
                  .filter((t) => t.layer === layer)
                  .map((t, i) => (
                    <g key={`${layer}-${i}`} stroke={layer === 'top' ? COPPER_TOP : COPPER_BOTTOM} strokeWidth={t.width || 0.2} strokeLinecap="round" opacity={layer === 'top' ? 0.95 : 0.6}>
                      {t.segments.map((s, j) => (
                        <line key={j} x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2} />
                      ))}
                    </g>
                  ))
              : null,
          )}
          {board.vias.map((v, i) => (
            <g key={`via-${i}`}>
              <circle cx={v.x} cy={v.y} r={v.diameter / 2} fill={COPPER_TOP} />
              <circle cx={v.x} cy={v.y} r={v.drill / 2} fill={HOLE} />
            </g>
          ))}
          {board.components.map(renderPart)}
          {(mechanical?.mountingHoles ?? []).map((h) => (
            <g key={h.id}>
              <circle cx={h.x} cy={h.y} r={h.diameter / 2 + 0.6} fill={h.plated ? COPPER_TOP : 'none'} stroke={SILK} strokeWidth={0.15 * strokeScale + 0.05} />
              <circle cx={h.x} cy={h.y} r={h.diameter / 2} fill={HOLE} />
            </g>
          ))}
          {show.rats &&
            board.rats.map((r, i) =>
              r.x1 != null && r.x2 != null ? (
                <line key={`rat-${i}`} x1={r.x1} y1={r.y1} x2={r.x2} y2={r.y2} stroke="#f43f5e" strokeWidth={0.12 * strokeScale + 0.05} strokeDasharray="0.5 0.4" />
              ) : null,
            )}
        </g>
      </svg>
      <div className="pointer-events-none absolute bottom-3 right-3">
        <ZoomControls zoomIn={zoomIn} zoomOut={zoomOut} reset={reset} />
      </div>
      <div className="pointer-events-none absolute bottom-3 left-3 flex flex-col gap-1 text-[11px] text-sq-text-secondary">
        <div className="pointer-events-auto flex gap-1 rounded-md bg-sq-bg-dark/80 p-1 backdrop-blur">
          {(['top', 'bottom', 'rats'] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setShow((s) => ({ ...s, [k]: !s[k] }))}
              className={`rounded px-2 py-0.5 ${show[k] ? 'bg-sq-neutral-800 text-sq-text-primary' : 'opacity-60'}`}
            >
              {k === 'top' ? 'Top' : k === 'bottom' ? 'Bottom' : 'Rats'}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setLabelMode((m) => (m === 'name' ? 'ref' : m === 'ref' ? 'none' : 'name'))}
            title="Silkscreen labels: part name, reference designator, or none"
            className="rounded bg-sq-neutral-800 px-2 py-0.5 text-sq-text-primary"
          >
            {labelMode === 'name' ? 'Names' : labelMode === 'ref' ? 'Refs' : 'No labels'}
          </button>
        </div>
        <div className="rounded-md bg-sq-bg-dark/80 px-2 py-1 backdrop-blur">
          {W.toFixed(1)} × {H.toFixed(1)} mm · {board.layer_count} layers · {board.components.length} parts · {board.traces.length} traces · {board.vias.length} vias
          {board.rats.length ? ` · ${board.rats.length} unrouted` : ' · routed'}
        </div>
      </div>
    </div>
  );
}
