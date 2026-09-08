import { createFileRoute } from '@tanstack/react-router';
import {
  sessionFromSetCookie,
  setEngineCookie,
  synqitApiBase,
} from '@/server/synqit';

/**
 * Sign in, or create an account.
 *
 * The engine owns identity — every account lives in its user table — so it
 * checks the password and this server keeps the session it returns in an
 * httpOnly cookie on the studio's own domain. The password is forwarded once
 * and never stored.
 *
 * There is nothing to mirror any more. This route used to also provision a
 * Supabase user and hand the browser two sessions; the studio keeps its
 * conversations in the engine now, so one is enough.
 *
 *   POST { email, password }                     -> sign in
 *   POST { email, password, name, signup: true } -> create the account first
 */
export const Route = createFileRoute('/api/synqit-login')({
  server: { handlers: { POST: login } },
});

function isSecure(request: Request): boolean {
  return new URL(request.url).protocol === 'https:';
}

async function login({ request }: { request: Request }) {
  let email = '';
  let password = '';
  let name: string | undefined;
  let signup = false;
  try {
    const body = (await request.json()) as {
      email?: string;
      password?: string;
      name?: string;
      signup?: boolean;
    };
    email = (body.email ?? '').trim().toLowerCase();
    password = body.password ?? '';
    name = body.name?.trim() || undefined;
    signup = body.signup === true;
  } catch {
    return Response.json({ error: 'Expected a JSON body.' }, { status: 400 });
  }
  if (!email || !password) {
    return Response.json({ error: 'Email and password are required.' }, { status: 400 });
  }

  let res: Response;
  try {
    res = await fetch(`${synqitApiBase()}/auth/${signup ? 'signup' : 'login'}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(
        signup ? { email, password, name: name ?? email.split('@')[0] } : { email, password },
      ),
    });
  } catch (error) {
    return Response.json(
      { error: `Cannot reach Synqit: ${error instanceof Error ? error.message : 'unknown error'}` },
      { status: 502 },
    );
  }

  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))) as { detail?: string };
    return Response.json(
      {
        error:
          detail.detail ||
          (res.status === 401
            ? 'That email and password did not match a Synqit account.'
            : `Synqit refused the sign-in (${res.status}).`),
      },
      { status: res.status === 401 ? 401 : 400 },
    );
  }

  const session = sessionFromSetCookie(res.headers);
  if (!session) {
    return Response.json({ error: 'Synqit signed in but returned no session.' }, { status: 502 });
  }

  return Response.json(
    { user: await res.json().catch(() => null) },
    { headers: { 'set-cookie': setEngineCookie(session, isSecure(request)) } },
  );
}
