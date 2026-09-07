import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { supabase } from '@/lib/supabase';
import { apiUrl } from '@/services/api';
import { synqitHandoffUrl } from '@/components/auth/SynqitSignInButton';

type SsoSearch = { token?: string; next?: string };

/**
 * Landing route for the Synqit handoff: /sso?token=...&next=/path
 * Exchanges the handoff token for a Supabase session (see api/synqit-sso),
 * then continues to `next` inside the app.
 */
export const Route = createFileRoute('/sso')({
  validateSearch: (search: Record<string, unknown>): SsoSearch => ({
    token: typeof search.token === 'string' ? search.token : undefined,
    next: typeof search.next === 'string' ? search.next : undefined,
  }),
  component: SsoView,
});

function SsoView() {
  const { token, next } = Route.useSearch();
  const navigate = useNavigate();
  const [status, setStatus] = useState<'working' | 'error'>('working');
  const [detail, setDetail] = useState('');
  const started = useRef(false);
  const target = next && next.startsWith('/') ? next : '/';

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!token) {
      // No token: start the handoff from the Synqit side.
      window.location.replace(synqitHandoffUrl(target));
      return;
    }
    (async () => {
      try {
        const res = await fetch(apiUrl('synqit-sso'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
        });
        const data = (await res.json()) as {
          token_hash?: string;
          error?: string;
        };
        if (!res.ok || !data.token_hash) {
          throw new Error(data.error || `exchange failed (${res.status})`);
        }
        const { error } = await supabase.auth.verifyOtp({
          type: 'magiclink',
          token_hash: data.token_hash,
        });
        if (error) throw error;
        navigate({ to: target, replace: true });
      } catch (err) {
        setStatus('error');
        setDetail(err instanceof Error ? err.message : String(err));
      }
    })();
  }, [token, target, navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-sq-bg-dark p-4 text-sq-text-primary">
      <div className="w-full max-w-md rounded-lg bg-sq-bg-secondary-dark p-8 text-center shadow-md">
        <img
          src={`${import.meta.env.BASE_URL}/synqit-wordmark.svg`}
          alt="Synqit"
          className="mx-auto mb-6 h-8"
        />
        {status === 'working' ? (
          <p className="text-sq-text-secondary">Signing you in with your Synqit account…</p>
        ) : (
          <>
            <p className="mb-2 font-semibold">Could not sign in with Synqit</p>
            <p className="mb-6 text-sm text-sq-text-secondary">{detail}</p>
            <a
              href={`${import.meta.env.BASE_URL.replace(/\/$/, '')}/signin`}
              className="text-sq-blue hover:underline"
            >
              Use another sign-in method
            </a>
          </>
        )}
      </div>
    </div>
  );
}
