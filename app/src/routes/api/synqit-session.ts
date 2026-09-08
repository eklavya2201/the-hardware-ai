import { createFileRoute } from '@tanstack/react-router';
import {
  clearEngineCookie,
  readEngineCookie,
  sessionFromSetCookie,
  setEngineCookie,
  synqitApiBase,
} from '@/server/synqit';

/**
 * Sign in to the Synqit engine from inside the studio.
 *
 * The engine owns the boards, the part library and the agent; the studio owns
 * the conversation and the 3D. A person should not have to visit a second site
 * to connect the two, so this exchanges their Synqit credentials for an engine
 * session and keeps it in a studio-domain httpOnly cookie. The password is
 * forwarded once and never stored.
 *
 *   GET    -> who the engine thinks we are, or 401
 *   POST   -> { email, password } sign in
 *   DELETE -> forget the session
 */
export const Route = createFileRoute('/api/synqit-session')({
  server: { handlers: { GET: whoami, POST: signIn, DELETE: signOut } },
});

function isSecure(request: Request): boolean {
  return new URL(request.url).protocol === 'https:';
}

async function whoami({ request }: { request: Request }) {
  const session = readEngineCookie(request);
  if (!session) return Response.json({ signedIn: false }, { status: 401 });

  const res = await fetch(`${synqitApiBase()}/auth/me`, {
    headers: { cookie: `session=${encodeURIComponent(session)}` },
  });
  if (!res.ok) {
    return Response.json(
      { signedIn: false },
      { status: 401, headers: { 'set-cookie': clearEngineCookie(isSecure(request)) } },
    );
  }
  return Response.json({ signedIn: true, user: await res.json() });
}

async function signIn({ request }: { request: Request }) {
  let email = '';
  let password = '';
  try {
    const body = (await request.json()) as { email?: string; password?: string };
    email = (body.email ?? '').trim();
    password = body.password ?? '';
  } catch {
    return Response.json({ error: 'Expected a JSON body.' }, { status: 400 });
  }
  if (!email || !password) {
    return Response.json({ error: 'Email and password are required.' }, { status: 400 });
  }

  let res: Response;
  try {
    res = await fetch(`${synqitApiBase()}/auth/login`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
  } catch (error) {
    return Response.json(
      { error: `Cannot reach the Synqit engine: ${error instanceof Error ? error.message : 'unknown error'}` },
      { status: 502 },
    );
  }

  if (!res.ok) {
    const detail = await res.text();
    return Response.json(
      { error: res.status === 401 ? 'That email and password did not match a Synqit account.' : detail.slice(0, 200) },
      { status: res.status },
    );
  }

  const session = sessionFromSetCookie(res.headers);
  if (!session) {
    return Response.json({ error: 'The engine signed in but returned no session.' }, { status: 502 });
  }

  return Response.json(
    { signedIn: true, user: await res.json().catch(() => null) },
    { headers: { 'set-cookie': setEngineCookie(session, isSecure(request)) } },
  );
}

function signOut({ request }: { request: Request }) {
  return Response.json(
    { signedIn: false },
    { headers: { 'set-cookie': clearEngineCookie(isSecure(request)) } },
  );
}
