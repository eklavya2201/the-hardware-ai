import {
  assignBlocks,
  BLOCK_ORDER,
  BLOCK_TITLES,
  type BlockKey,
} from './schematicBlocks';
import type { SqComponent, SqNet, SqPin } from '@/lib/synqitData';

/**
 * Where every symbol and every block frame lands on the sheet. Pure geometry —
 * no React, no DOM — so the packing can be checked without a browser.
 */

/** ICs first, then connectors and actives, passives last. */
function rank(refdes: string): number {
  const p = refdes.replace(/[0-9]+$/, '');
  return ({ U: 0, Y: 1, J: 2, SW: 3, D: 4, L: 5, Q: 6, R: 7, C: 8 } as Record<string, number>)[p] ?? 9;
}

const PIN_PITCH = 7;
const BOX_MIN_W = 42;

const GAP_X = 46;        // room for a net label between neighbouring symbols
const GAP_Y = 34;
const FRAME_PAD = 13;    // breathing room inside a block frame
export const PAD = 3;
export const FRAME_TITLE = 13;  // strip above the contents for the block title
const FRAME_GAP = 22;    // between frames on the sheet

export type Placed = { c: SqComponent; x: number; y: number; w: number; h: number; pinPos: Map<string, { x: number; y: number; side: string; name: string }> };
export type Frame = { key: BlockKey; title: string; x: number; y: number; w: number; h: number };

type Box = { c: SqComponent; w: number; h: number; left: SqPin[]; right: SqPin[]; top: SqPin[]; bottom: SqPin[] };

function sideOf(p: SqPin): string {
  return p.side ?? (Number(p.number) % 2 ? 'left' : 'right');
}

/** How big a symbol has to be to fit its own pin names. */
function measure(c: SqComponent): Box {
  const left = c.pins.filter((p) => sideOf(p) === 'left');
  const right = c.pins.filter((p) => sideOf(p) === 'right');
  const top = c.pins.filter((p) => p.side === 'top');
  const bottom = c.pins.filter((p) => p.side === 'bottom');
  const longest = Math.max(4, ...c.pins.map((p) => (p.name || p.number).length));
  const w = Math.max(BOX_MIN_W, longest * 2 * 3.2 + 14, (Math.max(top.length, bottom.length) + 1) * PIN_PITCH);
  const h = Math.max(PIN_PITCH * (Math.max(left.length, right.length) + 1), 20);
  return { c, w, h, left, right, top, bottom };
}

/**
 * Shelf-pack boxes into rows no wider than `targetW`, returning each box's
 * offset plus the packed extent. Used twice: for symbols inside a block, and
 * for blocks on the sheet.
 */
function shelf<T extends { w: number; h: number }>(items: T[], targetW: number, gapX: number, gapY: number) {
  const out: { item: T; x: number; y: number }[] = [];
  let x = 0;
  let y = 0;
  let rowH = 0;
  let maxW = 0;
  for (const it of items) {
    if (x > 0 && x + it.w > targetW) {
      x = 0;
      y += rowH + gapY;
      rowH = 0;
    }
    out.push({ item: it, x, y });
    x += it.w + gapX;
    rowH = Math.max(rowH, it.h);
    maxW = Math.max(maxW, x - gapX);
  }
  return { out, w: maxW, h: y + rowH };
}

/** A row-ish aspect: wider than tall, the way a sheet reads. */
function targetWidth(items: { w: number; h: number }[], aspect: number, gap: number): number {
  const area = items.reduce((a, i) => a + (i.w + gap) * (i.h + gap), 0);
  return Math.max(Math.sqrt(area * aspect), ...items.map((i) => i.w));
}

function pinPositions(b: Box, x: number, y: number) {
  const m = new Map<string, { x: number; y: number; side: string; name: string }>();
  b.left.forEach((p, k) => m.set(p.number, { x: x - PAD, y: y + PIN_PITCH * (k + 1), side: 'left', name: p.name || p.number }));
  b.right.forEach((p, k) => m.set(p.number, { x: x + b.w + PAD, y: y + PIN_PITCH * (k + 1), side: 'right', name: p.name || p.number }));
  b.top.forEach((p, k) => m.set(p.number, { x: x + PIN_PITCH * (k + 1), y: y - PAD, side: 'top', name: p.name || p.number }));
  b.bottom.forEach((p, k) => m.set(p.number, { x: x + PIN_PITCH * (k + 1), y: y + b.h + PAD, side: 'bottom', name: p.name || p.number }));
  return m;
}

/**
 * Lay the sheet out as titled functional blocks. The IR's own x/y are the
 * Synqit editor's sprawling canvas coordinates and read badly at panel size,
 * so they are never used here — the grouping carries the readability instead.
 */
export function layout(components: SqComponent[], nets: SqNet[]): { placed: Placed[]; frames: Frame[] } {
  if (!components.length) return { placed: [], frames: [] };

  const blockOf = assignBlocks(components, nets);
  const groups = new Map<BlockKey, Box[]>();
  for (const c of components) {
    const key = blockOf.get(c.refdes) ?? 'misc';
    const list = groups.get(key) ?? [];
    list.push(measure(c));
    groups.set(key, list);
  }

  // Inside each block: ICs first, then connectors and actives, passives last.
  for (const list of groups.values()) {
    list.sort((a, b) => rank(a.c.refdes) - rank(b.c.refdes) || a.c.refdes.localeCompare(b.c.refdes, undefined, { numeric: true }));
  }

  const packedBlocks = BLOCK_ORDER.filter((k) => groups.has(k)).map((key) => {
    const boxes = groups.get(key)!;
    const inner = shelf(boxes, targetWidth(boxes, 2.4, GAP_X), GAP_X, GAP_Y);
    return {
      key,
      inner,
      w: inner.w + FRAME_PAD * 2,
      h: inner.h + FRAME_PAD * 2 + FRAME_TITLE,
    };
  });

  const sheet = shelf(packedBlocks, targetWidth(packedBlocks, 1.7, FRAME_GAP), FRAME_GAP, FRAME_GAP);

  const placed: Placed[] = [];
  const frames: Frame[] = [];
  for (const { item, x: bx, y: by } of sheet.out) {
    frames.push({ key: item.key, title: BLOCK_TITLES[item.key], x: bx, y: by, w: item.w, h: item.h });
    for (const { item: box, x: ix, y: iy } of item.inner.out) {
      const x = bx + FRAME_PAD + ix;
      const y = by + FRAME_PAD + FRAME_TITLE + iy;
      placed.push({ c: box.c, x, y, w: box.w, h: box.h, pinPos: pinPositions(box, x, y) });
    }
  }
  return { placed, frames };
}


/**
 * Where each net label sits, with overlaps resolved.
 *
 * Labels used to be placed at a fixed offset from their pin and drawn, with
 * no idea what else was on the canvas. Two pins a few millimetres apart put
 * two names on top of each other and both became unreadable.
 *
 * This is the standard fix from map and diagram rendering: give every label a
 * rectangle, and when two collide push the later one along its own leader
 * axis until it is clear. A left/right label slides vertically, a top/bottom
 * label slides horizontally, so a label never drifts across its own symbol.
 * The leader line then stretches to follow it, which is what tells the reader
 * which pin a moved label belongs to.
 */
export type LabelAnchor = {
  key: string;
  x: number;
  y: number;
  side: string;
  text: string;
};

export type PlacedLabel = LabelAnchor & {
  /** Where the text ends up, after collisions are resolved. */
  lx: number;
  ly: number;
  w: number;
  h: number;
};

const LEADER = 6;      // pin to label
const CHAR_W = 0.62;   // width of a monospace glyph, as a fraction of size
const STEP = 0.9;      // how far to nudge per attempt, in label heights
const MAX_NUDGE = 14;  // give up rather than push a label into the next block

function overlaps(a: PlacedLabel, b: PlacedLabel): boolean {
  // A hair of padding, so two labels never merely touch.
  const pad = 0.4;
  return (
    a.lx - pad < b.lx + b.w &&
    b.lx - pad < a.lx + a.w &&
    a.ly - pad < b.ly + b.h &&
    b.ly - pad < a.ly + a.h
  );
}

export function placeNetLabels(anchors: LabelAnchor[], fontSize: number): PlacedLabel[] {
  const h = fontSize * 1.15;
  const placed: PlacedLabel[] = [];

  // Deterministic order: same board, same picture, every render.
  const ordered = [...anchors].sort((a, b) => a.y - b.y || a.x - b.x || a.key.localeCompare(b.key));

  for (const a of ordered) {
    const dir = a.side === "left" ? -1 : a.side === "right" ? 1 : 0;
    const vdir = a.side === "top" ? -1 : a.side === "bottom" ? 1 : 0;
    const w = Math.max(1, a.text.length) * fontSize * CHAR_W;

    const baseX = a.x + dir * LEADER;
    const baseY = a.y + vdir * LEADER;
    // The text is anchored at its end for a left-facing label, so the box
    // extends the other way.
    const boxX = dir < 0 ? baseX - w : dir > 0 ? baseX : baseX - w / 2;

    let candidate: PlacedLabel | null = null;
    for (let n = 0; n <= MAX_NUDGE; n++) {
      // Alternate above and below (or left and right) so a crowded pin does
      // not push every one of its neighbours the same way.
      const offset = Math.ceil(n / 2) * STEP * h * (n % 2 === 0 ? 1 : -1);
      const slideY = vdir === 0 ? offset : 0;
      const slideX = vdir === 0 ? 0 : offset;
      const test: PlacedLabel = {
        ...a,
        lx: boxX + slideX,
        ly: baseY - h / 2 + slideY,
        w,
        h,
      };
      if (!placed.some((q) => overlaps(test, q))) {
        candidate = test;
        break;
      }
    }
    // Nowhere clear within the budget: draw it at its natural spot rather
    // than flinging it across the sheet. Rare, and readable-ish beats lost.
    placed.push(
      candidate ?? { ...a, lx: boxX, ly: baseY - h / 2, w, h },
    );
  }
  return placed;
}

