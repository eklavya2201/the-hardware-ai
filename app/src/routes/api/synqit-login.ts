import { createFileRoute } from '@tanstack/react-router';
import { createHmac } from 'node:crypto';
import { env } from '@/server/env';
import { getAnonSupabaseClient, getServiceRoleSupabaseClient } from '@/server/supabaseClient';
import {
  sessionFromSetCookie,
  setEngineCookie,
  synqitApiBase,
} from '@/server/synqit';

/**
 * Sign in with a Synqit account.
 *
 * The engine is the identity: every existing account lives in its user table,
 * not in the studio's Supabase project, so asking people to "sign in" against
 * Supabase locked out everyone who already had a Synqit login.
 *
 * So the engine checks the password. The studio then needs a Supabase user of
 * its own -- conversations, messages and files are stored there under row-level
 * security keyed to that id -- so one is created on first sign-in with a
 * password derived from the email and the shared service secret. That password
 * is never sent anywhere: the server signs in with it and hands the browser the
 * resulting session.
 *
 *   POST { email, password }          -> sign in
 *   POST { email, password, name, signup: true } -> create the Synqit account first
 */
export const Route = createFileRoute('/api/synqit-login')({
  server: { handlers: { POST: login } },
});

function isSecure(request: Request): boolean {
  return new URL(request.url).protocol === 'https:';
}

/** Deterministic, server-only Supabase password for a Synqit identity. */
function studioPassword(email: string, secret: string): string {
  return createHmac('sha256', secret).update(`studio:${email.toLowerCase()}`).digest('hex');
}

async function login({ request }: { request: Request }) {
  const secret = env('STUDIO_SERVICE_SECRET');
  if (!secret) {
    return Response.json({ error: 'This deployment has no service secret configured.' }, { status: 503 });
  }
  for (const name of ['VITE_SUPABASE_URL', 'VITE_SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY']) {
    if (!env(name)) return Response.json({ error: `${name} is not set on the server.` }, { status: 503 });
  }

  let email = '';
  let password = '';
  let name: string | undefined;
  let signup = false;
  try {
    const body = (await request.json()) as {
      email?: string; password?: string; name?: string; signup?: boolean;
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

  // 1. The engine decides who this is.
  let engineRes: Response;
  try {
    engineRes = await fetch(`${synqitApiBase()}/auth/${signup ? 'signup' : 'login'}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(signup ? { email, password, name: name ?? email.split('@')[0] } : { email, password }),
    });
  } catch (error) {
    return Response.json(
      { error: `Cannot reach Synqit: ${error instanceof Error ? error.message : 'unknown error'}` },
      { status: 502 },
    );
  }
  if (!engineRes.ok) {
    const detail = await engineRes.json().catch(() => ({})) as { detail?: string };
    return Response.json(
      { error: detail.detail || (engineRes.status === 401 ? 'That email and password did not match a Synqit account.' : `Synqit refused the sign-in (${engineRes.status}).`) },
      { status: engineRes.status === 401 ? 401 : 400 },
    );
  }
  const engineUser = (await engineRes.json().catch(() => null)) as { name?: string } | null;
  const engineSession = sessionFromSetCookie(engineRes.headers);
  if (!engineSession) {
    return Response.json({ error: 'Synqit signed in but returned no session.' }, { status: 502 });
  }

  // 2. Mirror the identity into Supabase so the studio has somewhere to keep
  //    conversations. Created once, then reused.
  const derived = studioPassword(email, secret);
  const anon = getAnonSupabaseClient();
  let { data: session, error } = await anon.auth.signInWithPassword({ email, password: derived });

  if (error) {
    const admin = getServiceRoleSupabaseClient();
    const created = await admin.auth.admin.createUser({
      email,
      password: derived,
      email_confirm: true,
      user_metadata: { full_name: engineUser?.name ?? name ?? email.split('@')[0] },
    });
    if (created.error && !/already/i.test(created.error.message)) {
      return Response.json({ error: `Could not set up the studio account: ${created.error.message}` }, { status: 500 });
    }
    ({ data: session, error } = await anon.auth.signInWithPassword({ email, password: derived }));
    if (error) {
      return Response.json({ error: `Could not open the studio session: ${error.message}` }, { status: 500 });
    }
  }

  if (!session?.session) {
    return Response.json({ error: 'No studio session was returned.' }, { status: 500 });
  }

  // The browser sets this with supabase.auth.setSession(); the engine session
  // rides along as an httpOnly cookie it never sees.
  return Response.json(
    {
      access_token: session.session.access_token,
      refresh_token: session.session.refresh_token,
    },
    { headers: { 'set-cookie': setEngineCookie(engineSession, isSecure(request)) } },
  );
}
