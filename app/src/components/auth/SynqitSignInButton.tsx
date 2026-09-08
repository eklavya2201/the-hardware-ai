import { Button } from '@/components/ui/button';

/**
 * The engine's public API, used only for the top-level SSO redirect. Every
 * data call goes through the studio's own origin instead — see lib/synqitApi.
 */
const SYNQIT_ENGINE_DIRECT = (
  (import.meta.env.VITE_SYNQIT_API_BASE as string | undefined) || 'http://localhost:8000/api'
).replace(/\/$/, '');

/**
 * "Continue with Synqit": hand the browser to the Synqit backend, which
 * checks its own session cookie and bounces back to /sso with a short-lived
 * signed handoff token. /sso exchanges that token for a Supabase session via
 * POST /api/synqit-sso. A user who is not signed in to Synqit is sent to the
 * Synqit login page first and returns here afterwards.
 */
export function synqitHandoffUrl(redirectPath = '/') {
  const base = import.meta.env.BASE_URL.replace(/\/$/, '');
  const returnTo = `${window.location.origin}${base}/sso?next=${encodeURIComponent(redirectPath)}`;
  // Handoff runs against the engine directly: it is a top-level browser
  // redirect, not a data call, so it does not go through the studio's proxy.
  return `${SYNQIT_ENGINE_DIRECT}/auth/handoff?return_to=${encodeURIComponent(returnTo)}`;
}

export function SynqitSignInButton({
  redirectPath = '/',
  className,
}: {
  redirectPath?: string;
  className?: string;
}) {
  return (
    <Button
      type="button"
      className={className ?? 'flex w-full items-center gap-2 p-6 md:hover:bg-sq-blue/10'}
      onClick={() => {
        window.location.href = synqitHandoffUrl(redirectPath);
      }}
    >
      <img
        src={`${import.meta.env.BASE_URL}synqit-logo.png`}
        alt=""
        className="h-4 w-4 object-contain"
      />
      <span>Continue with Synqit</span>
    </Button>
  );
}
