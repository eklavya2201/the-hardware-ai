import { createFileRoute } from '@tanstack/react-router';
import {
  readEngineCookie,
  synqitApiBase,
} from '@/server/synqit';

/**
 * Same-origin proxy to the Synqit engine (FastAPI on AWS).
 *
 * The engine lives on its own host, so its session cookie is cross-site to the
 * studio: a browser will only send it if the engine sets SameSite=None, and
 * some block it outright. Rather than depend on that, every board call goes to
 * the studio's own origin and this handler forwards it, attaching the engine
 * session held in our `sq_engine` cookie. No CORS, no third-party cookie, and
 * the studio works the same whether the engine is on AWS or localhost.
 *
 * Responses stream, because the agent's `/chat` endpoint is server-sent events
 * and the part library index is 13 MB.
 */
export const Route = createFileRoute('/api/synqit/$')({
  server: { handlers: { GET: proxy, POST: proxy, PUT: proxy, PATCH: proxy, DELETE: proxy, OPTIONS: proxy } },
});

// Hop-by-hop headers, plus the ones we set ourselves.
const SKIP_REQUEST = new Set(['host', 'connection', 'cookie', 'content-length', 'origin', 'referer']);
const SKIP_RESPONSE = new Set(['set-cookie', 'content-encoding', 'content-length', 'transfer-encoding', 'connection']);

async function proxy({ request }: { request: Request }) {
  const url = new URL(request.url);
  const marker = '/api/synqit';
  const at = url.pathname.indexOf(marker);
  const path = at === -1 ? '/' : url.pathname.slice(at + marker.length) || '/';

  const target = `${synqitApiBase()}${path}${url.search}`;

  const headers = new Headers();
  request.headers.forEach((value, name) => {
    if (!SKIP_REQUEST.has(name.toLowerCase())) headers.set(name, value);
  });
  const session = readEngineCookie(request);
  if (session) headers.set('cookie', `session=${encodeURIComponent(session)}`);

  const body = request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(target, { method: request.method, headers, body, redirect: 'manual' });
  } catch (error) {
    // The engine being down is a 502 from us, not an opaque network failure in
    // the browser with no explanation.
    return Response.json(
      { detail: `Cannot reach the Synqit engine: ${error instanceof Error ? error.message : 'unknown error'}` },
      { status: 502 },
    );
  }

  const out = new Headers();
  upstream.headers.forEach((value, name) => {
    if (!SKIP_RESPONSE.has(name.toLowerCase())) out.set(name, value);
  });

  return new Response(upstream.body, { status: upstream.status, headers: out });
}
