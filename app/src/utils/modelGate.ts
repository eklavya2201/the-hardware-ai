import type { MeshFacts } from './meshFacts';

/**
 * A mechanical gate on a compiled model.
 *
 * The board side has 78 deterministic rules and will not call a design done
 * while they report errors. The CAD side had nothing: `meshFacts` measured the
 * bounding box, the lowest point, the triangle count and the number of
 * separate bodies, and then handed those numbers to the model inside an
 * English sentence asking it to "check the facts". That is a request, not a
 * gate, and it is why a bullock cart shipped with its wheels above the ground
 * — every number needed to catch it was already computed and nothing was
 * obliged to look.
 *
 * These are predicates over numbers we already have. No model call, no render
 * inspection, no new dependency.
 */
export type ModelFinding = {
  /** Stable id, so a finding can be counted and compared across turns. */
  id: string;
  severity: 'error' | 'warning';
  message: string;
};

export type ModelVerdict = {
  verdict: 'pass' | 'fail';
  findings: ModelFinding[];
};

/** A model that floats this far above the build plate is not resting on it. */
const FLOAT_TOLERANCE_MM = 0.5;

/** Below the plate by more than this is geometry that cannot print. */
const SUNK_TOLERANCE_MM = 0.5;

/** Anything thinner than this in one axis is a plane, not a solid. */
const DEGENERATE_MM = 0.01;

export function assessModel(facts: MeshFacts): ModelVerdict {
  const findings: ModelFinding[] = [];
  const { bbox_mm: b, min_mm: m, triangles, bodies } = facts;

  if (!triangles) {
    findings.push({
      id: 'CAD_EMPTY',
      severity: 'error',
      message: 'The model is empty — it compiled but produced no geometry.',
    });
    // Nothing else is meaningful about an empty mesh.
    return { verdict: 'fail', findings };
  }

  const flat = (['x', 'y', 'z'] as const).filter((axis) => b[axis] < DEGENERATE_MM);
  if (flat.length) {
    findings.push({
      id: 'CAD_DEGENERATE',
      severity: 'error',
      message:
        `The model has no thickness in ${flat.join(' and ')} ` +
        `(${flat.map((a) => `${a} = ${b[a].toFixed(2)} mm`).join(', ')}). ` +
        'A surface with zero thickness cannot be printed or manufactured.',
    });
  }

  if (bodies > 1) {
    findings.push({
      id: 'CAD_DISCONNECTED',
      severity: 'error',
      message:
        `The model is ${bodies} separate bodies that do not touch. If this is ` +
        'meant to be one part, the pieces are floating apart and it will not ' +
        'print as drawn; join them or move them into contact. If it is ' +
        `genuinely an assembly of ${bodies} pieces, say so explicitly in your ` +
        'reply so the count is a decision rather than an accident.',
    });
  }

  if (m.z > FLOAT_TOLERANCE_MM) {
    findings.push({
      id: 'CAD_FLOATING',
      severity: 'error',
      message:
        `The lowest point of the model sits ${m.z.toFixed(2)} mm above z = 0, ` +
        'so nothing rests on the ground plane. Anything meant to stand — feet, ' +
        'wheels, a base — must reach z = 0. Move the model down or extend ' +
        'whatever should be touching.',
    });
  } else if (m.z < -SUNK_TOLERANCE_MM) {
    findings.push({
      id: 'CAD_SUNK',
      severity: 'warning',
      message:
        `The model extends ${Math.abs(m.z).toFixed(2)} mm below z = 0. That is ` +
        'fine for a cut or a counterbore, and wrong if the object is meant to ' +
        'sit on a surface.',
    });
  }

  return {
    verdict: findings.some((f) => f.severity === 'error') ? 'fail' : 'pass',
    findings,
  };
}

/** One line per finding, for the tool result the model reads. */
export function describeVerdict(v: ModelVerdict): string {
  if (!v.findings.length) return '';
  return v.findings
    .map((f) => `${f.severity === 'error' ? 'ERROR' : 'WARNING'} ${f.id}: ${f.message}`)
    .join(' ');
}
