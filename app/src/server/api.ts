import { readEngineCookie, synqitApiBase } from './synqit';

/** A person, as the engine knows them. */
export type StudioUser = {
  id: string;
  email: string;
  name?: string | null;
  is_admin?: boolean;
};

export const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers':
    'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
};

export function json(data: unknown, status = 200) {
  return Response.json(data, {
    status,
    headers: corsHeaders,
  });
}

export function preflight() {
  return new Response('ok', { headers: corsHeaders });
}

export function methodNotAllowed() {
  return json({ error: 'method_not_allowed' }, 405);
}

export function isUnauthorizedError(error: unknown) {
  return error instanceof Error && error.message === 'Unauthorized';
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/**
 * Who is calling, according to the engine.
 *
 * Identity is the engine's, not a second account system's: the browser holds
 * an httpOnly `sq_engine` cookie for the studio's own origin, and this asks
 * the engine who that is. There is no bearer token to attach, which is why
 * client code no longer reads a session before calling its own server.
 */
export async function requireUser(request: Request): Promise<StudioUser> {
  const session = readEngineCookie(request);
  if (!session) throw new Error('Unauthorized');
  const res = await fetch(`${synqitApiBase()}/auth/me`, {
    headers: { cookie: `session=${encodeURIComponent(session)}` },
  });
  // `/auth/me` answers 200 with null when the cookie is stale, so an empty
  // body is a signed-out answer rather than a signed-in one.
  const user = res.ok ? ((await res.json().catch(() => null)) as StudioUser | null) : null;
  if (!user?.email) throw new Error('Unauthorized');
  return user;
}
