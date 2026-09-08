import type { SqMechanical, SqProject } from './synqitData';

/**
 * Client-side board brief: the same three outputs as Synqit's
 * GET /projects/{id}/cad-brief (entities, OpenSCAD prelude, prompt block),
 * built from GET /projects/{id} alone. Used when the Synqit backend does not
 * have the cad-brief endpoint yet (production today). The one thing the
 * server knows and we do not is each part's curated body height, so heights
 * here are estimated from the footprint name and category; the server brief
 * is preferred whenever it answers.
 */
const BOARD_THICKNESS = 1.6;

type Part = { refdes: string; mpn: string; category: string; layer: string; x: number; y: number; w: number; h: number; z: number };
type Entities = {
  project: { id: string; name: string };
  units: 'mm';
  frame: string;
  board: { shape: string; width: number; height: number; corner_radius: number; thickness: number; points: number[][] };
  holes: { id: string; x: number; y: number; diameter: number; standoff?: string | null }[];
  parts: Part[];
  tallest_top_part_mm: number;
  cutouts: { refdes: string; edge: string; x: number; y: number; width: number; height: number }[];
  enclosure: { kind: string; wall: number; standoff: number; clearance_top: number; clearance_bottom: number; lip: number };
  heights_estimated: boolean;
};

const r2 = (v: number) => Math.round(v * 100) / 100;

/** Rough body heights by footprint family. Server brief has the curated value. */
export function estimateHeight(footprintName: string, mpn: string): number {
  const f = `${footprintName} ${mpn}`.toUpperCase();
  if (/ESP32|WROOM|MODULE|RF_MODULE|NRF|LORA|SIM800|A7670/.test(f)) return 3.2;
  if (/USB_C|USB-C|USB_MICRO|MICRO-B|USB_B/.test(f)) return 3.3;
  if (/PINHEADER|PIN_HEADER|HEADER_2\.54|PINSOCKET|JST_XH|JST_PH|TERMINALBLOCK|SCREW_TERMINAL|BARREL|DC_JACK/.test(f)) return 8.5;
  if (/BUTTON|SW_PUSH|TACT|SW_SPST/.test(f)) return 4.3;
  if (/ELECTROLYTIC|CP_RADIAL|CAPACITOR_THT/.test(f)) return 11;
  if (/TO-220|TO220/.test(f)) return 16;
  if (/TO-92/.test(f)) return 5;
  if (/BATTERY|18650|HOLDER/.test(f)) return 20;
  if (/LED_D5|LED_D3|LED_THT/.test(f)) return 8.6;
  if (/BUZZER|SPEAKER/.test(f)) return 9;
  if (/RELAY/.test(f)) return 15;
  if (/DISPLAY|OLED|LCD/.test(f)) return 4;
  if (/CRYSTAL|OSC/.test(f)) return 1.2;
  if (/QFN|DFN|WLCSP|BGA/.test(f)) return 0.9;
  if (/TQFP|LQFP|QFP/.test(f)) return 1.2;
  if (/SOIC|SOP|TSSOP|MSOP|SSOP/.test(f)) return 1.5;
  if (/SOT-23|SOT23|SOT-323|SOT-363|SC-70/.test(f)) return 1.1;
  if (/SOT-223|SOT223|SOT-89|DPAK|D2PAK|TO-252|TO-263/.test(f)) return 2.0;
  if (/LED_SMD|5050|PLCC/.test(f)) return 1.6;
  if (/1206|1210|2010|2512/.test(f)) return 0.7;
  if (/0402|0201/.test(f)) return 0.4;
  if (/0603|0805/.test(f)) return 0.6;
  return 2.0;
}

function categoryOf(footprintName: string, refdes: string): string {
  const p = refdes.replace(/[0-9]+$/, '');
  return { U: 'ic', J: 'connector', R: 'resistor', C: 'capacitor', L: 'inductor', D: 'diode', Q: 'transistor', SW: 'switch', Y: 'crystal', BT: 'battery', LS: 'speaker' }[p] ?? (footprintName.split(':')[0].toLowerCase() || 'part');
}

export function buildClientBrief(project: SqProject): { entities: Entities; openscad: string; prompt_block: string } | null {
  const board = project.board;
  const mech: SqMechanical | null = project.mechanical;
  if (!board || !board.components.length) return null;

  const o = mech?.outline;
  const shape = o?.shape ?? (board.shape === 'round' ? 'circle' : board.shape === 'polygon' ? 'polygon' : 'rect');
  const width = r2(o?.width ?? board.width);
  const height = r2(o?.height ?? board.height);
  const corner = r2(o?.corner_radius ?? 0);
  const points = (o?.points?.length ? o.points : board.outline_points).map((p) => [r2(p.x), r2(p.y)]);

  const parts: Part[] = [];
  let tallest = 0;
  for (const pc of board.components) {
    const fp = pc.footprint;
    const rot = pc.rotation || 0;
    const swap = rot === 90 || rot === 270;
    const w = Math.max(0.6, swap ? fp.height : fp.width);
    const h = Math.max(0.6, swap ? fp.width : fp.height);
    const z = estimateHeight(fp.name, pc.mpn);
    if (pc.layer === 'top') tallest = Math.max(tallest, z);
    parts.push({ refdes: pc.refdes, mpn: pc.mpn, category: categoryOf(fp.name, pc.refdes), layer: pc.layer, x: r2(pc.x - w / 2), y: r2(pc.y - h / 2), w: r2(w), h: r2(h), z: r2(z) });
  }
  const holes = (mech?.mountingHoles ?? []).map((h) => ({ id: h.id, x: r2(h.x), y: r2(h.y), diameter: r2(h.diameter), standoff: 'M3' }));
  const cutouts = (mech?.cutouts ?? []).map((c) => ({ refdes: c.refdes ?? '', edge: c.edge, x: r2(c.x), y: r2(c.y), width: r2(c.width), height: r2(c.height) }));
  const enc = (mech as { enclosure?: Record<string, number | string> } | null)?.enclosure ?? {};
  const entities: Entities = {
    project: { id: project.id, name: project.name },
    units: 'mm',
    frame: 'origin bottom-left of the board, X right, Y up (toward the top edge), Z up from the board top surface',
    board: { shape, width, height, corner_radius: corner, thickness: BOARD_THICKNESS, points },
    holes,
    parts,
    tallest_top_part_mm: r2(tallest),
    cutouts,
    enclosure: {
      kind: String(enc.kind ?? 'box'),
      wall: Number(enc.wall_thickness ?? 2),
      standoff: Number(enc.standoff_height ?? 5),
      clearance_top: Number(enc.clearance_top ?? 12),
      clearance_bottom: Number(enc.clearance_bottom ?? 3),
      lip: Number(enc.lip ?? 1),
    },
    heights_estimated: true,
  };
  return { entities, openscad: prelude(entities), prompt_block: promptBlock(entities) };
}

function prelude(e: Entities): string {
  const b = e.board;
  const L: string[] = [
    '// ---- Synqit board (generated; do not edit; call synqit_board()) ----',
    `/* [Synqit board: ${e.project.name}] */`,
    `board_width = ${b.width};      // mm, fixed by the PCB`,
    `board_height = ${b.height};    // mm, fixed by the PCB`,
    `board_thickness = ${b.thickness};`,
    `board_corner_radius = ${b.corner_radius};`,
    `tallest_part_height = ${e.tallest_top_part_mm};  // mm above the board top (estimated from footprints)`,
    `enclosure_wall = ${e.enclosure.wall};`,
    `enclosure_standoff = ${e.enclosure.standoff};`,
    `enclosure_clearance_top = ${e.enclosure.clearance_top};`,
    `enclosure_clearance_bottom = ${e.enclosure.clearance_bottom};`,
    `synqit_holes = [${e.holes.map((h) => `[${h.x}, ${h.y}, ${h.diameter}]`).join(', ')}];  // [x, y, diameter]`,
    `synqit_parts = [${e.parts.map((p) => `["${p.refdes}", ${p.x}, ${p.y}, ${p.w}, ${p.h}, ${p.z}, "${p.layer}"]`).join(', ')}];  // [refdes, x, y, w, h, height, layer]`,
    `synqit_cutouts = [${e.cutouts.map((c) => `["${c.refdes}", "${c.edge}", ${c.x}, ${c.y}, ${c.width}, ${c.height}]`).join(', ')}];  // [refdes, edge, x, y, width, height]`,
    '',
    'module synqit_board_outline() {',
  ];
  if (b.shape === 'circle') L.push('  translate([board_width/2, board_height/2]) circle(d = board_width, $fn = 96);');
  else if (b.shape === 'polygon' && b.points.length) L.push(`  polygon([${b.points.map(([x, y]) => `[${x}, ${y}]`).join(', ')}]);`);
  else
    L.push(
      '  r = board_corner_radius;',
      '  if (r > 0) hull() for (x = [r, board_width - r], y = [r, board_height - r]) translate([x, y]) circle(r = r, $fn = 32);',
      '  else square([board_width, board_height]);',
    );
  L.push(
    '}',
    '',
    '// PCB slab with mounting holes, plus every part as a box envelope (top parts up, bottom parts down).',
    'module synqit_board() {',
    '  color("darkgreen") difference() {',
    '    translate([0, 0, -board_thickness]) linear_extrude(board_thickness) synqit_board_outline();',
    '    for (h = synqit_holes) translate([h[0], h[1], -board_thickness - 1]) cylinder(d = h[2], h = board_thickness + 2, $fn = 32);',
    '  }',
    '  for (p = synqit_parts)',
    '    color(p[6] == "top" ? "dimgray" : "slategray")',
    '      translate([p[1], p[2], p[6] == "top" ? 0 : -board_thickness - p[5]]) cube([p[3], p[4], p[5]]);',
    '}',
    '',
    '// Cylinders on every mounting hole: use for standoffs (subtract a smaller one for the screw).',
    'module synqit_hole_pattern(d, h) { for (p = synqit_holes) translate([p[0], p[1], 0]) cylinder(d = d, h = h, $fn = 32); }',
    '// ---- end Synqit board ----',
  );
  return L.join('\n');
}

function promptBlock(e: Entities): string {
  const b = e.board;
  const conns = e.cutouts.map((c) => `${c.refdes} on the ${c.edge} edge (${c.width}x${c.height} mm)`).join(', ') || 'none recorded';
  return (
    `Synqit board "${e.project.name}": ${b.shape} ${b.width} x ${b.height} mm, ${b.thickness} mm thick, ${e.holes.length} mounting holes, ${e.parts.length} parts, tallest top-side part about ${e.tallest_top_part_mm} mm (heights estimated from footprints). ` +
    `Connector openings: ${conns}. Enclosure clearances: wall ${e.enclosure.wall} mm, standoff ${e.enclosure.standoff} mm, ${e.enclosure.clearance_top} mm above the tallest part, ${e.enclosure.clearance_bottom} mm below the board.\n` +
    'Rules: paste the OpenSCAD block below at the top of the script unchanged, call synqit_board() to place the PCB, never redraw the board or move its holes, keep every connector opening reachable from outside, and expose your own dimensions (wall, clearances, fillets) as parameters.'
  );
}
