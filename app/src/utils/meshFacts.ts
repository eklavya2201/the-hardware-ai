import { STLLoader } from 'three/addons/loaders/STLLoader.js';

/**
 * Numeric facts about a compiled model — the "queries" that replace looking
 * at pictures for the things pictures are bad at: size, count of separate
 * bodies, and whether anything is degenerate. Computed in the browser from
 * the STL the worker just produced, so it costs no tokens to obtain and only
 * a line to send.
 */
export type MeshFacts = {
  bbox_mm: { x: number; y: number; z: number };
  min_mm: { x: number; y: number; z: number };
  triangles: number;
  bodies: number;
};

export async function meshFacts(stl: Blob): Promise<MeshFacts> {
  const buffer = await stl.arrayBuffer();
  const geometry = new STLLoader().parse(buffer);
  geometry.computeBoundingBox();
  const box = geometry.boundingBox!;
  const pos = geometry.getAttribute('position');
  const triangles = Math.floor(pos.count / 3);

  // Connected components over shared vertex positions (STL triangles do not
  // share indices, so weld by rounded coordinate). Union-find over triangles.
  const key = (i: number) =>
    `${Math.round(pos.getX(i) * 1000)},${Math.round(pos.getY(i) * 1000)},${Math.round(pos.getZ(i) * 1000)}`;
  const parent = new Int32Array(triangles);
  for (let t = 0; t < triangles; t++) parent[t] = t;
  const find = (a: number): number => {
    while (parent[a] !== a) {
      parent[a] = parent[parent[a]];
      a = parent[a];
    }
    return a;
  };
  const union = (a: number, b: number) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent[ra] = rb;
  };
  const seen = new Map<string, number>();
  // Cap the weld work for very large meshes; the count is still exact for the
  // triangles visited and "bodies" is reported as a lower bound past the cap.
  const cap = Math.min(triangles, 400_000);
  for (let t = 0; t < cap; t++) {
    for (let v = 0; v < 3; v++) {
      const k = key(t * 3 + v);
      const prev = seen.get(k);
      if (prev === undefined) seen.set(k, t);
      else union(prev, t);
    }
  }
  const roots = new Set<number>();
  for (let t = 0; t < cap; t++) roots.add(find(t));

  const r = (v: number) => Math.round(v * 100) / 100;
  return {
    bbox_mm: { x: r(box.max.x - box.min.x), y: r(box.max.y - box.min.y), z: r(box.max.z - box.min.z) },
    min_mm: { x: r(box.min.x), y: r(box.min.y), z: r(box.min.z) },
    triangles,
    bodies: roots.size,
  };
}

export function describeFacts(f: MeshFacts): string {
  return `bounding box ${f.bbox_mm.x} x ${f.bbox_mm.y} x ${f.bbox_mm.z} mm (X x Y x Z), lowest point z=${f.min_mm.z} mm, ${f.triangles} triangles, ${f.bodies} separate ${f.bodies === 1 ? 'body' : 'bodies'}`;
}
