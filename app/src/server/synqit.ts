import { env } from './env';

/**
 * Server-side client for the Synqit backend (FastAPI, `/api/auth/...`).
 * Set SYNQIT_API_BASE (e.g. https://api.synqit.in/api); defaults to the
 * local uvicorn dev server.
 *
 * The engine runs on its own host, so its session cookie is cross-site to the
 * studio and a browser may refuse to send it. Everything therefore goes
 * through the studio's own origin (`/api/synqit/*`), and the engine session
 * is held in the studio-domain cookie below. See routes/api/synqit/$.ts.
 */
export type SynqitUser = {
  id: string;
  email: string;
  name: string;
  is_admin?: boolean;
  policies_accepted?: boolean;
};

export function synqitApiBase(): string {
  return (env('SYNQIT_API_BASE') || 'http://localhost:8000/api').replace(/\/$/, '');
}

export async function verifyHandoffToken(token: string): Promise<SynqitUser> {
  const res = await fetch(`${synqitApiBase()}/auth/handoff/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  if (!res.ok) {
    throw new Error(`synqit handoff verify failed: ${res.status}`);
  }
  const data = (await res.json()) as Partial<SynqitUser>;
  if (!data || typeof data.email !== 'string' || typeof data.id !== 'string') {
    throw new Error('synqit handoff verify: malformed response');
  }
  return {
    id: data.id,
    email: data.email,
    name: typeof data.name === 'string' && data.name ? data.name : data.email,
    is_admin: data.is_admin,
    policies_accepted: data.policies_accepted,
  };
}

/** Studio-domain cookie holding the engine's own session token. */
export const ENGINE_COOKIE = 'sq_engine';

/** Prod serves the studio over https, so the cookie can be Secure there. */
function cookieAttrs(secure: boolean): string {
  return [
    'Path=/',
    'HttpOnly',
    'SameSite=Lax',
    secure ? 'Secure' : '',
    'Max-Age=2592000',
  ]
    .filter(Boolean)
    .join('; ');
}

export function setEngineCookie(token: string, secure: boolean): string {
  return `${ENGINE_COOKIE}=${encodeURIComponent(token)}; ${cookieAttrs(secure)}`;
}

export function clearEngineCookie(secure: boolean): string {
  return `${ENGINE_COOKIE}=; ${cookieAttrs(secure).replace('Max-Age=2592000', 'Max-Age=0')}`;
}

export function readEngineCookie(request: Request): string | null {
  const raw = request.headers.get('cookie');
  if (!raw) return null;
  for (const part of raw.split(';')) {
    const [name, ...rest] = part.trim().split('=');
    if (name === ENGINE_COOKIE) return decodeURIComponent(rest.join('='));
  }
  return null;
}

/**
 * The engine sets its session as `session=<token>`. Pull it back out of an
 * upstream Set-Cookie so we can re-issue it on our own domain.
 */
export function sessionFromSetCookie(headers: Headers): string | null {
  const raw = headers.getSetCookie?.() ?? [headers.get('set-cookie') ?? ''];
  for (const line of raw) {
    const match = /(?:^|;\s*)session=([^;]*)/.exec(line);
    if (match && match[1]) return decodeURIComponent(match[1]);
  }
  return null;
}
