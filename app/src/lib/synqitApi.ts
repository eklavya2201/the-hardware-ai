import { supabase } from '@/lib/supabase';

/**
 * Where the studio talks to the Synqit engine.
 *
 * Always the studio's own origin. The engine runs on its own host (AWS), and
 * its session cookie is cross-site from here — a browser may refuse to send it
 * however the engine is configured. `routes/api/synqit/$.ts` forwards each call
 * server-side with the session it holds, so from the browser's point of view
 * the engine is part of this app: same origin, no CORS, no third-party cookie.
 */
export const SYNQIT_API_BASE = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/api/synqit`;

/** The engine's own site, for links a person clicks (not for data calls). */
export const SYNQIT_APP_URL = (
  (import.meta.env.VITE_SYNQIT_APP_URL as string | undefined) || 'https://synqit.in'
).replace(/\/$/, '');

/** The studio's session with the engine: sign in, check, sign out. */
export const SYNQIT_SESSION_URL = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/api/synqit-session`;

/** Thrown when the engine says "who are you?" — the caller shows a sign-in. */
export class SynqitAuthError extends Error {
  constructor(message = 'Sign in to Synqit to use the board tools.') {
    super(message);
    this.name = 'SynqitAuthError';
  }
}

/**
 * One call to the engine. Same-origin, so `credentials: 'include'` is just
 * "send our own cookie". A 401 is a missing engine session, never a bug in the
 * caller, so it gets its own error type.
 */
export async function synqitFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(`${SYNQIT_API_BASE}${path}`, { credentials: 'include', ...init });
  if (res.status === 401) throw new SynqitAuthError();
  return res;
}

export type SynqitSession = { signedIn: boolean; user?: { id: string; email: string; name?: string } };

export async function getSynqitSession(): Promise<SynqitSession> {
  const res = await fetch(SYNQIT_SESSION_URL, { credentials: 'include' });
  if (!res.ok) return { signedIn: false };
  return (await res.json()) as SynqitSession;
}

/**
 * Link the signed-in studio user to their engine account.
 *
 * There is no second password to type: the studio server proves who we are
 * with our own token and asks the engine to provision or open that person's
 * account. Safe to call whenever a board call comes back 401.
 */
export async function linkSynqitSession(): Promise<SynqitSession> {
  const token = (await supabase.auth.getSession()).data.session?.access_token;
  if (!token) throw new Error('Sign in to the studio first.');
  const res = await fetch(SYNQIT_SESSION_URL, {
    method: 'POST',
    credentials: 'include',
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = (await res.json().catch(() => ({}))) as SynqitSession & { error?: string };
  if (!res.ok) throw new Error(data.error || 'Could not connect to the Synqit engine.');
  return data;
}

export async function signOutOfSynqit(): Promise<void> {
  await fetch(SYNQIT_SESSION_URL, { method: 'DELETE', credentials: 'include' });
}
