import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Pan/zoom for an SVG viewBox, shared by the PCB and schematic views.
 *
 * Why a native listener: React registers `wheel` as passive, so
 * `preventDefault()` inside `onWheel` is ignored and the browser also zooms
 * the whole page (ctrl+wheel, trackpad pinch). We attach our own
 * non-passive listener, swallow the event, and clamp the zoom between 0.25x
 * and 12x of the fitted view so the drawing can never vanish.
 */
export type ViewBox = { x: number; y: number; w: number; h: number };

export function usePanZoom(fit: ViewBox) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [view, setView] = useState<ViewBox>(fit);
  const viewRef = useRef(view);
  viewRef.current = view;
  const fitRef = useRef(fit);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null);

  // A new fit (different board / layout) resets the view.
  useEffect(() => {
    fitRef.current = fit;
    setView(fit);
  }, [fit.x, fit.y, fit.w, fit.h]); // eslint-disable-line react-hooks/exhaustive-deps

  const clamp = useCallback((v: ViewBox): ViewBox => {
    const f = fitRef.current;
    const minW = f.w / 12;
    const maxW = f.w * 4;
    if (v.w < minW || v.w > maxW) {
      const w = Math.min(maxW, Math.max(minW, v.w));
      const k = w / v.w;
      const cx = v.x + v.w / 2;
      const cy = v.y + v.h / 2;
      return { x: cx - w / 2, y: cy - (v.h * k) / 2, w, h: v.h * k };
    }
    return v;
  }, []);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const v = viewRef.current;
      const rect = svg.getBoundingClientRect();
      // pinch gestures arrive as ctrl+wheel with small deltas; scale by magnitude
      const step = Math.min(0.5, Math.abs(e.deltaY) / (e.ctrlKey ? 100 : 400) + 0.05);
      const k = e.deltaY > 0 ? 1 + step : 1 / (1 + step);
      const mx = v.x + ((e.clientX - rect.left) / rect.width) * v.w;
      const my = v.y + ((e.clientY - rect.top) / rect.height) * v.h;
      setView(clamp({ x: mx - (mx - v.x) * k, y: my - (my - v.y) * k, w: v.w * k, h: v.h * k }));
    };
    svg.addEventListener('wheel', onWheel, { passive: false });
    return () => svg.removeEventListener('wheel', onWheel);
  }, [clamp]);

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY, vx: viewRef.current.x, vy: viewRef.current.y, moved: false };
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    const svg = svgRef.current;
    if (!d || !svg) return;
    const rect = svg.getBoundingClientRect();
    const v = viewRef.current;
    const dx = ((e.clientX - d.x) / rect.width) * v.w;
    const dy = ((e.clientY - d.y) / rect.height) * v.h;
    if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 3) d.moved = true;
    setView({ ...v, x: d.vx - dx, y: d.vy - dy });
  };
  const onPointerUp = (e: React.PointerEvent) => {
    (e.currentTarget as Element).releasePointerCapture?.(e.pointerId);
    drag.current = null;
  };
  const zoomBy = (k: number) =>
    setView((v) => clamp({ x: v.x + (v.w - v.w * k) / 2, y: v.y + (v.h - v.h * k) / 2, w: v.w * k, h: v.h * k }));
  const reset = () => setView(fitRef.current);

  return {
    svgRef,
    view,
    viewBox: `${view.x} ${view.y} ${view.w} ${view.h}`,
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerLeave: onPointerUp, onDoubleClick: reset },
    zoomIn: () => zoomBy(1 / 1.3),
    zoomOut: () => zoomBy(1.3),
    reset,
    /** true while a drag moved, so click handlers on children can ignore it */
    dragged: () => Boolean(drag.current?.moved),
  };
}

/** Small zoom control shared by both views. */
export function ZoomControls({ zoomIn, zoomOut, reset }: { zoomIn: () => void; zoomOut: () => void; reset: () => void }) {
  return (
    <div className="pointer-events-auto flex overflow-hidden rounded-md border border-sq-neutral-800 bg-sq-bg-dark/85 text-[12px] text-sq-text-secondary backdrop-blur">
      <button type="button" onClick={zoomOut} className="px-2 py-1 hover:text-sq-text-primary" title="Zoom out">
        −
      </button>
      <button type="button" onClick={reset} className="border-x border-sq-neutral-800 px-2 py-1 hover:text-sq-text-primary" title="Fit (double-click also fits)">
        Fit
      </button>
      <button type="button" onClick={zoomIn} className="px-2 py-1 hover:text-sq-text-primary" title="Zoom in">
        +
      </button>
    </div>
  );
}
