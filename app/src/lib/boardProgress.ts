import { useSyncExternalStore } from 'react';
import type { BoardProgress } from './synqitBoardAgent';

/**
 * What the board agent is doing, right now, for anything that wants to show it.
 *
 * Designing a board takes tens of seconds — parts search, wiring, 78 rules,
 * placement, routing — and until this existed the PCB and Schematic panes sat
 * on an empty state the whole time, which reads as frozen rather than working.
 * The driver already emitted phases; nothing was listening.
 */
export type BoardActivity = { active: boolean; phase: string; detail?: string; startedAt: number };

const IDLE: BoardActivity = { active: false, phase: '', startedAt: 0 };
let state: BoardActivity = IDLE;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

export function startBoardActivity(phase = 'starting') {
  state = { active: true, phase, startedAt: Date.now() };
  emit();
}

export function reportBoardActivity(p: BoardProgress) {
  state = { ...state, active: true, phase: p.phase, detail: p.detail };
  emit();
}

export function endBoardActivity() {
  state = IDLE;
  emit();
}

export function useBoardActivity(): BoardActivity {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => state,
    () => IDLE,
  );
}

/** Phases the driver emits, in the words a person would use. */
const LABELS: Record<string, string> = {
  creating: 'Creating the project',
  designing: 'Choosing parts and wiring the schematic',
  placing: 'Placing parts on the board',
  routing: 'Routing the copper',
};

export function activityLabel(a: BoardActivity): string {
  return LABELS[a.phase] ?? (a.phase ? a.phase.replace(/_/g, ' ') : 'Working');
}
