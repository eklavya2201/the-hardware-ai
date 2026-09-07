import { createFileRoute } from '@tanstack/react-router';
import { json, preflight } from '@/server/api';
import { logError } from '@/server/serverLog';
import { nexarConfigured, partPromptBlock, searchParts } from '@/server/nexar';

/**
 * GET /api/parts?q=<mpn or description>&limit=5
 * Octopart search through Nexar. Public within the app (no per-user data),
 * rate-limited by Nexar's own quota. Returns parts plus the ready-made prompt
 * block the composer inserts.
 */
export const Route = createFileRoute('/api/parts')({
  server: {
    handlers: {
      OPTIONS: preflight,
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const q = (url.searchParams.get('q') || '').trim();
        const limit = Math.min(10, Math.max(1, Number(url.searchParams.get('limit')) || 5));
        if (!nexarConfigured()) {
          return json(
            { error: 'parts_not_configured', message: 'Set NEXAR_CLIENT_ID and NEXAR_CLIENT_SECRET (Octopart / Nexar API).' },
            503,
          );
        }
        if (q.length < 2) return json({ error: 'query_too_short' }, 400);
        try {
          const parts = await searchParts(q, limit);
          return json({ parts: parts.map((p) => ({ ...p, promptBlock: partPromptBlock(p) })) });
        } catch (error) {
          logError(error, { functionName: 'parts', statusCode: 502, additionalContext: { q } });
          return json({ error: 'parts_lookup_failed' }, 502);
        }
      },
    },
  },
});
