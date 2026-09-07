import { useEffect, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { supabase } from '@/lib/supabase';
import { useToast } from '@/hooks/use-toast';

/**
 * Google sign-in the way the Synqit app does it: Google Identity Services
 * hands back an ID token in the browser and we exchange it for a session.
 * Synqit posts the credential to its own backend; here the exchange is
 * `supabase.auth.signInWithIdToken`, so the SAME Google OAuth client id
 * (VITE_GOOGLE_CLIENT_ID) serves both apps and no client secret is needed.
 *
 * Requirements: the Google client must list this origin under "Authorized
 * JavaScript origins", and the Supabase Google provider must be enabled with
 * that client id (local: supabase/config.toml, `skip_nonce_check = true`).
 */
const GSI_SRC = 'https://accounts.google.com/gsi/client';
const CLIENT_ID = (import.meta.env.VITE_GOOGLE_CLIENT_ID || '') as string;

let gsiLoader: Promise<void> | null = null;
function loadGsi(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve();
  if (window.google?.accounts?.id) return Promise.resolve();
  gsiLoader ??= new Promise<void>((resolve, reject) => {
    const s = document.createElement('script');
    s.src = GSI_SRC;
    s.async = true;
    s.defer = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error('Google Identity script failed to load'));
    document.head.appendChild(s);
  });
  return gsiLoader;
}

type Props = {
  redirectPath?: string;
  text?: GsiButtonConfiguration['text'];
  className?: string;
};

export function GoogleSignInButton({
  redirectPath = '/',
  text = 'continue_with',
  className,
}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!CLIENT_ID || !host.current) return;
    let cancelled = false;
    loadGsi()
      .then(() => {
        if (cancelled || !host.current || !window.google) return;
        window.google.accounts.id.initialize({
          client_id: CLIENT_ID,
          callback: async ({ credential }) => {
            setBusy(true);
            const { error } = await supabase.auth.signInWithIdToken({
              provider: 'google',
              token: credential,
            });
            setBusy(false);
            if (error) {
              toast({
                title: 'Google sign-in failed',
                description: error.message,
                variant: 'destructive',
              });
              return;
            }
            navigate({ to: redirectPath, replace: true });
          },
        });
        host.current.innerHTML = '';
        window.google.accounts.id.renderButton(host.current, {
          theme: 'filled_black',
          size: 'large',
          text,
          shape: 'rectangular',
          logo_alignment: 'left',
          width: Math.min(host.current.clientWidth || 360, 400),
        });
      })
      .catch((err: unknown) => {
        toast({
          title: 'Google sign-in unavailable',
          description: err instanceof Error ? err.message : String(err),
          variant: 'destructive',
        });
      });
    return () => {
      cancelled = true;
    };
  }, [navigate, redirectPath, text, toast]);

  if (!CLIENT_ID) {
    return (
      <p className="text-center text-xs text-sq-text-tertiary">
        Google sign-in is not configured (VITE_GOOGLE_CLIENT_ID).
      </p>
    );
  }
  return (
    <div className={className} aria-busy={busy}>
      <div ref={host} className="flex w-full justify-center" />
    </div>
  );
}
