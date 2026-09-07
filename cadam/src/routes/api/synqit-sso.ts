import { createFileRoute } from '@tanstack/react-router';
import { json, preflight } from '@/server/api';
import { logError } from '@/server/serverLog';
import { getServiceRoleSupabaseClient } from '@/server/supabaseClient';
import { verifyHandoffToken } from '@/server/synqit';

/**
 * Synqit SSO exchange. The browser arrives at /sso?token=<handoff token>
 * minted by the Synqit backend for its signed-in user; this route verifies
 * the token with Synqit (server to server), makes sure a Supabase user with
 * that email exists (confirmed, no password), and returns a one-time magic
 * link token hash the client redeems with `supabase.auth.verifyOtp`. Nothing
 * secret crosses the browser except the short-lived handoff token itself.
 */
export const Route = createFileRoute('/api/synqit-sso')({
  server: {
    handlers: {
      OPTIONS: preflight,
      POST: async ({ request }) => {
        let token = '';
        try {
          const body = (await request.json()) as { token?: unknown };
          token = typeof body?.token === 'string' ? body.token : '';
        } catch {
          token = '';
        }
        if (!token) return json({ error: 'missing_token' }, 400);

        try {
          const user = await verifyHandoffToken(token);
          const admin = getServiceRoleSupabaseClient();

          const created = await admin.auth.admin.createUser({
            email: user.email,
            email_confirm: true,
            user_metadata: {
              full_name: user.name,
              synqit_user_id: user.id,
              provider: 'synqit',
            },
          });
          if (
            created.error &&
            !/already|exists|registered/i.test(created.error.message)
          ) {
            throw created.error;
          }

          const link = await admin.auth.admin.generateLink({
            type: 'magiclink',
            email: user.email,
          });
          if (link.error) throw link.error;

          return json({
            token_hash: link.data.properties.hashed_token,
            email: user.email,
          });
        } catch (error) {
          logError(error, { functionName: 'synqit-sso', statusCode: 502 });
          return json({ error: 'sso_failed' }, 502);
        }
      },
    },
  },
});
