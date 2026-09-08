import { createFileRoute } from '@tanstack/react-router';
import { requireUser } from '@/server/api';
import { env } from '@/server/env';
import {
  clearEngineCookie,
  readEngineCookie,
  sessionFromSetCookie,
  setEngineCookie,
  synqitApiBase,
} from '@/server/synqit';

/**
 * The studio's session with the engine.
 *
 * One account, not two. A person signs in to the studio; the engine account
 * that owns their boards is provisioned for them the first time they need it,
 * server to server, and they never see it. This route is the exchange: it
 * proves who the caller is with their studio token, asks the engine for a
 * session for that email using a shared secret the browser never sees, and
 * keeps the result in an httpOnly cookie on the studio's own domain.
 *
 *   GET    -> whether we hold a live engine session
 *   POST   -> link (or re-link) the signed-in studio user to their engine account
 *   DELETE -> forget the session
 */
export const Route = createFileRoute('/api/synqit-session')({
  server: { handlers: { GET: whoami, POST: link, DELETE: unlink } },
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
  // `/auth/me` answers 200 with `null` when the cookie is stale, so an empty
  // body is a signed-out answer, not a signed-in one.
  const user = res.ok ? await res.json().catch(() => null) : null;
  if (!user) {
    return Response.json(
      { signedIn: false },
      { status: 401, headers: { 'set-cookie': clearEngineCookie(isSecure(request)) } },
    );
  }
  return Response.json({ signedIn: true, user });
}

async function link({ request }: { request: Request }) {
  const secret = env('STUDIO_SERVICE_SECRET');
  if (!secret) {
    return Response.json(
      { error: 'This deployment has no engine service secret configured.' },
      { status: 503 },
    );
  }
  // Checked before the token is read, because these are needed at *runtime* to
  // verify it -- being build-time values for the client bundle makes them easy
  // to leave out of the container's environment. Without this a misconfigured
  // deployment reports "sign in first" forever and nobody ever can.
  for (const name of ['VITE_SUPABASE_URL', 'VITE_SUPABASE_ANON_KEY']) {
    if (!env(name)) {
      return Response.json({ error: `${name} is not set on the server.` }, { status: 503 });
    }
  }

  if (!request.headers.get('Authorization')) {
    return Response.json({ error: 'Sign in to the studio first.' }, { status: 401 });
  }

  let email: string;
  let name: string | undefined;
  try {
    const user = await requireUser(request);
    if (!user.email) throw new Error('Unauthorized');
    email = user.email;
    name = (user.user_metadata?.full_name as string | undefined) ?? undefined;
  } catch {
    // Every way this can fail -- no token, expired token, Supabase unreachable
    // with a bad token -- is the caller not being signed in. It must never be
    // an unhandled 500, which is what a bare token check produced.
    return Response.json({ error: 'Sign in to the studio first.' }, { status: 401 });
  }

  let res: Response;
  try {
    res = await fetch(`${synqitApiBase()}/auth/service/session`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-service-secret': secret },
      body: JSON.stringify({ email, name }),
    });
  } catch (error) {
    return Response.json(
      { error: `Cannot reach the Synqit engine: ${error instanceof Error ? error.message : 'unknown error'}` },
      { status: 502 },
    );
  }

  if (!res.ok) {
    return Response.json(
      { error: `The engine refused the session (${res.status}).` },
      { status: res.status === 401 ? 500 : res.status },
    );
  }

  const session = sessionFromSetCookie(res.headers);
  if (!session) {
    return Response.json({ error: 'The engine returned no session.' }, { status: 502 });
  }

  return Response.json(
    { signedIn: true, user: await res.json().catch(() => null) },
    { headers: { 'set-cookie': setEngineCookie(session, isSecure(request)) } },
  );
}

function unlink({ request }: { request: Request }) {
  return Response.json(
    { signedIn: false },
    { headers: { 'set-cookie': clearEngineCookie(isSecure(request)) } },
  );
}
