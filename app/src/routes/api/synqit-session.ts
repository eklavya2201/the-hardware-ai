import { createFileRoute } from '@tanstack/react-router';
import {
  clearEngineCookie,
  readEngineCookie,
  synqitApiBase,
} from '@/server/synqit';

/**
 * The session, read and ended.
 *
 * Creating one is `/api/synqit-login`: the engine checks the password and this
 * server stores the session it returns. There is nothing to "link" any more —
 * signing in *is* the engine session, and the studio has no account of its own
 * to reconcile it with.
 *
 *   GET    -> who we are, or 401
 *   DELETE -> sign out
 */
export const Route = createFileRoute('/api/synqit-session')({
  server: { handlers: { GET: whoami, DELETE: signOut } },
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
  // `/auth/me` answers 200 with null when the cookie is stale, so an empty
  // body is a signed-out answer rather than a signed-in one.
  const user = res.ok ? await res.json().catch(() => null) : null;
  if (!user) {
    return Response.json(
      { signedIn: false },
      { status: 401, headers: { 'set-cookie': clearEngineCookie(isSecure(request)) } },
    );
  }
  return Response.json({ signedIn: true, user });
}

async function signOut({ request }: { request: Request }) {
  const session = readEngineCookie(request);
  // Tell the engine too, so the session is dead server-side and not merely
  // forgotten by this browser.
  if (session) {
    await fetch(`${synqitApiBase()}/auth/logout`, {
      method: 'POST',
      headers: { cookie: `session=${encodeURIComponent(session)}` },
    }).catch(() => undefined);
  }
  return Response.json(
    { signedIn: false },
    { headers: { 'set-cookie': clearEngineCookie(isSecure(request)) } },
  );
}
