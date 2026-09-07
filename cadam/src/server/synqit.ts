import { env } from './env';

/**
 * Server-side client for the Synqit backend (FastAPI, `/api/auth/...`).
 * Only the pieces the CAD app needs today: verifying an SSO handoff token.
 * Set SYNQIT_API_BASE (e.g. https://api.synqit.in/api); defaults to the
 * local uvicorn dev server.
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
