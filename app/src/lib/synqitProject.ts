import { useSyncExternalStore } from 'react';
import { updateConversation } from '@/lib/studioData';

export { SYNQIT_APP_URL } from './synqitApi';

/**
 * Which Synqit board a conversation is working on.
 *
 * Scoped to the conversation, never global. It used to be a single
 * localStorage key — "one value per browser" — so opening a new conversation
 * showed the previous conversation's board in the PCB and Schematic panes.
 * Two unrelated designs looked like one, which is the kind of bug that makes
 * a tool untrustworthy the moment someone has two projects on the go.
 *
 * The link lives in two places on purpose. The conversation's `settings` on
 * the engine is the truth, so it survives a new browser and cannot be crossed
 * by a direct API call; localStorage is a per-conversation cache so the views
 * paint before that round trip lands.
 */
const KEY_PREFIX = 'synqit.project.';
const EVENT = 'synqit-project-change';

export type SynqitProjectRef = { id: string; name?: string } | null;

/** The conversation the studio is currently showing, from the URL. */
function currentConversationId(): string | null {
  if (typeof window === 'undefined') return null;
  const m = window.location.pathname.match(/\/editor\/([^/?#]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

function keyFor(conversationId: string | null): string | null {
  return conversationId ? KEY_PREFIX + conversationId : null;
}

function readFor(conversationId: string | null): SynqitProjectRef {
  const key = keyFor(conversationId);
  if (!key || typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as SynqitProjectRef) : null;
  } catch {
    return null;
  }
}

// Cached per conversation, so useSyncExternalStore gets a stable snapshot and
// switching conversations cannot serve the previous one's value.
let cachedId: string | null = null;
let cached: SynqitProjectRef = null;

function snapshot(): SynqitProjectRef {
  const id = currentConversationId();
  if (id !== cachedId) {
    cachedId = id;
    cached = readFor(id);
  }
  return cached;
}

/**
 * Link a board to a conversation. Writes through to the conversation's
 * settings so the link is not just this browser's opinion.
 */
export function setSynqitProject(
  ref: SynqitProjectRef,
  conversationId: string | null = currentConversationId(),
) {
  if (typeof window === 'undefined') return;
  const key = keyFor(conversationId);
  if (key) {
    try {
      if (ref) localStorage.setItem(key, JSON.stringify(ref));
      else localStorage.removeItem(key);
    } catch {
      /* storage may be unavailable; the in-memory value still updates */
    }
  }
  cachedId = conversationId;
  cached = ref;
  window.dispatchEvent(new Event(EVENT));

  if (conversationId) {
    // Best effort: the view already has what it needs, and a failed write
    // should not break the board that was just linked.
    void updateConversation(conversationId, {
      settings: { synqitProject: ref ?? undefined } as Record<string, unknown>,
    }).catch(() => undefined);
  }
}

/** The linked board, outside React — tool handlers need it too. */
export function getSynqitProject(): SynqitProjectRef {
  return snapshot();
}

/**
 * Adopt the link stored on the conversation. Called when a conversation
 * loads, so a different browser (or a cleared cache) still sees its board.
 * The stored value wins only when this browser has nothing cached.
 */
export function hydrateSynqitProject(
  conversationId: string,
  settings: Record<string, unknown> | null | undefined,
) {
  const stored = (settings?.synqitProject ?? null) as SynqitProjectRef;
  const local = readFor(conversationId);
  if (local || !stored?.id) {
    if (conversationId !== cachedId) {
      cachedId = conversationId;
      cached = local;
      window.dispatchEvent(new Event(EVENT));
    }
    return;
  }
  const key = keyFor(conversationId);
  if (key) {
    try {
      localStorage.setItem(key, JSON.stringify(stored));
    } catch {
      /* cache only */
    }
  }
  cachedId = conversationId;
  cached = stored;
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void) {
  const onChange = () => {
    // Force the next snapshot to re-read: the conversation may not have
    // changed, but its value has.
    cachedId = null;
    cb();
  };
  window.addEventListener(EVENT, onChange);
  window.addEventListener('popstate', onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener('popstate', onChange);
  };
}

export function useSynqitProject(): SynqitProjectRef {
  return useSyncExternalStore(subscribe, snapshot, () => null);
}
