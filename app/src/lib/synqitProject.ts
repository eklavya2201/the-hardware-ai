import { useSyncExternalStore } from 'react';

/**
 * The Synqit project this studio session is working on. Set when a board is
 * inserted (Board button or ?project=), read by the right-hand panel tabs
 * (Schematic, PCB, BOM, Build guide) to embed the matching Synqit view.
 * Kept in localStorage so a reload keeps the link; one value per browser.
 */
const KEY = 'synqit.currentProject';
const EVENT = 'synqit-project-change';

export type SynqitProjectRef = { id: string; name?: string } | null;

function read(): SynqitProjectRef {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as SynqitProjectRef) : null;
  } catch {
    return null;
  }
}

let cached: SynqitProjectRef = read();
let cachedRaw: string | null = typeof window === 'undefined' ? null : localStorage.getItem(KEY);

export function setSynqitProject(ref: SynqitProjectRef) {
  if (typeof window === 'undefined') return;
  try {
    if (ref) localStorage.setItem(KEY, JSON.stringify(ref));
    else localStorage.removeItem(KEY);
  } catch {
    /* storage may be unavailable; the in-memory value still updates */
  }
  cached = ref;
  cachedRaw = ref ? JSON.stringify(ref) : null;
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  window.addEventListener('storage', cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener('storage', cb);
  };
}

function snapshot(): SynqitProjectRef {
  if (typeof window === 'undefined') return null;
  const raw = localStorage.getItem(KEY);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cached = read();
  }
  return cached;
}

export function useSynqitProject(): SynqitProjectRef {
  return useSyncExternalStore(subscribe, snapshot, () => null);
}

export const SYNQIT_APP_URL = (
  (import.meta.env.VITE_SYNQIT_APP_URL as string | undefined) || 'http://localhost:5173'
).replace(/\/$/, '');
