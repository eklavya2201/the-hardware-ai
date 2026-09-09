/**
 * Deterministic check on the schematic sheet: parts land in a sensible block,
 * frames do not overlap, symbols do not overlap, and every symbol sits inside
 * its own frame.
 *
 *   node --experimental-strip-types --import ./scripts/ts-register.mjs \
 *        scripts/check-schematic-layout.mjs [board.json ...]
 *
 * A board file is a Synqit project as GET /projects/{id} returns it. With no
 * argument it runs the synthetic boards below, which is what CI does.
 */
import { readFileSync } from 'node:fs';
import { layout, FRAME_TITLE, placeNetLabels } from '../src/components/synqit/schematicLayout.ts';
import { assignBlocks } from '../src/components/synqit/schematicBlocks.ts';

let failures = 0;
const fail = (msg) => { console.log('  FAIL ' + msg); failures++; };

function pins(n, prefix = '') {
  return Array.from({ length: n }, (_, i) => ({ number: String(i + 1), name: `${prefix}${i + 1}` }));
}

/** A board big enough to make the packer work: 12 ICs, 200 passives. */
function syntheticLarge() {
  const components = [];
  const nets = [];
  const kinds = [
    ['ESP32-WROOM-32', 'Wi-Fi module based on the ESP32-D0WD', 38],
    ['AP2112K-3.3TRG1', '3.3V linear regulator in SOT-23-5', 5],
    ['W25Q128JVSIQ', '128Mbit serial NOR flash memory', 8],
    ['SSD1306', '128x64 OLED display controller', 30],
    ['MPU-6050', '6-axis IMU with gyroscope and accelerometer', 24],
    ['DRV8833PWP', 'Dual H-bridge motor driver', 16],
  ];
  kinds.forEach(([mpn, description, n], i) => {
    components.push({ refdes: `U${i + 1}`, mpn, description, footprint: 'X', pins: pins(n, 'IO') });
  });
  for (let i = 0; i < 100; i++) components.push({ refdes: `C${i + 1}`, mpn: 'CC0603KRX7R9BB104', value: '100nF', description: '100nF X7R ceramic capacitor', pins: pins(2) });
  for (let i = 0; i < 100; i++) components.push({ refdes: `R${i + 1}`, mpn: 'RC0603FR-0710KL', value: '10k', description: '10k 1% resistor', pins: pins(2) });
  nets.push({ name: 'VCC_3V3', pins: components.map((c) => [c.refdes, '1']) });
  nets.push({ name: 'GND', pins: components.map((c) => [c.refdes, '2']) });
  for (let i = 0; i < 60; i++) nets.push({ name: `SIG${i}`, pins: [[`R${i + 1}`, '2'], [`U${(i % 6) + 1}`, String((i % 8) + 3)]] });
  return { name: 'synthetic-large', ir: { components, nets } };
}

/** The degenerate shapes: nothing, one part, and passives with no anchor. */
const edgeCases = [
  { name: 'empty', ir: { components: [], nets: [] } },
  { name: 'single', ir: { components: [{ refdes: 'U1', mpn: 'ESP32-WROOM-32', description: 'Wi-Fi module', pins: pins(38) }], nets: [] } },
  { name: 'passives-only', ir: { components: [
      { refdes: 'R1', mpn: 'RC0603', value: '10k', pins: pins(2) },
      { refdes: 'C1', mpn: 'CC0603', value: '100nF', pins: pins(2) },
    ], nets: [{ name: 'GND', pins: [['R1', '2'], ['C1', '2']] }] } },
];

function check(board) {
  const { components, nets } = board.ir;
  const { placed, frames } = layout(components, nets);
  console.log(`\n${board.name}: ${components.length} parts -> ${frames.length} blocks`);

  if (placed.length !== components.length) fail(`${components.length - placed.length} parts vanished`);

  const assigned = assignBlocks(components, nets);
  for (const c of components) if (!assigned.get(c.refdes)) fail(`${c.refdes} has no block`);

  const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  for (let i = 0; i < frames.length; i++)
    for (let j = i + 1; j < frames.length; j++)
      if (hit(frames[i], frames[j])) fail(`frames overlap: ${frames[i].title} / ${frames[j].title}`);

  for (let i = 0; i < placed.length; i++)
    for (let j = i + 1; j < placed.length; j++)
      if (hit(placed[i], placed[j])) fail(`symbols overlap: ${placed[i].c.refdes} / ${placed[j].c.refdes}`);

  for (const s of placed) {
    const f = frames.find((f) => hit(f, s));
    if (!f) { fail(`${s.c.refdes} sits outside every frame`); continue; }
    if (!(s.x >= f.x && s.y >= f.y + FRAME_TITLE && s.x + s.w <= f.x + f.w && s.y + s.h <= f.y + f.h))
      fail(`${s.c.refdes} spills out of "${f.title}"`);
  }

  // Net labels must not sit on top of one another -- the bug that made the
  // schematic unreadable. Same anchors the view builds, same resolver.
  const anchors = [];
  for (const n of nets) {
    const ends = n.pins.map(([r, p]) => placed.find((q) => q.c.refdes === r)?.pinPos.get(p)).filter(Boolean);
    if (ends.length === 2 && n.pins.length === 2) continue; // drawn as a wire
    ends.forEach((e, i) => anchors.push({ key: `${n.name}-${i}`, x: e.x, y: e.y, side: e.side, text: n.name }));
  }
  const labels = placeNetLabels(anchors, 3);
  let clashes = 0;
  for (let i = 0; i < labels.length; i++)
    for (let j = i + 1; j < labels.length; j++) {
      const a = labels[i], b = labels[j];
      if (a.lx < b.lx + b.w && b.lx < a.lx + a.w && a.ly < b.ly + b.h && b.ly < a.ly + a.h) clashes++;
    }
  if (clashes) fail(`${clashes} net labels overlap`);
  console.log(`  ${labels.length} net labels, ${clashes} overlapping`);

  for (const f of frames) console.log(`  ${f.title.padEnd(24)} ${placed.filter((p) => hit(f, p)).map((p) => p.c.refdes).join(' ').slice(0, 90)}`);
}

const files = process.argv.slice(2);
const boards = files.length
  ? files.map((f) => ({ name: f, ...JSON.parse(readFileSync(f, 'utf8')) }))
  : [...edgeCases, syntheticLarge()];
boards.forEach(check);

console.log(failures ? `\n${failures} failure(s)` : `\nOK — ${boards.length} boards, no layout problems`);
process.exit(failures ? 1 : 0);
