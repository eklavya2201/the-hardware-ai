import { Button } from '@/components/ui/button';

/**
 * "Continue with Synqit": hand the browser to the Synqit backend, which
 * checks its own session cookie and bounces back to /sso with a short-lived
 * signed handoff token. /sso exchanges that token for a Supabase session via
 * POST /api/synqit-sso. A user who is not signed in to Synqit is sent to the
 * Synqit login page first and returns here afterwards.
 */
export const SYNQIT_API_BASE = (
  (import.meta.env.VITE_SYNQIT_API_BASE as string | undefined) ||
  'http://localhost:8000/api'
).replace(/\/$/, '');

export function synqitHandoffUrl(redirectPath = '/') {
  const base = import.meta.env.BASE_URL.replace(/\/$/, '');
  const returnTo = `${window.location.origin}${base}/sso?next=${encodeURIComponent(redirectPath)}`;
  return `${SYNQIT_API_BASE}/auth/handoff?return_to=${encodeURIComponent(returnTo)}`;
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
        src={`${import.meta.env.BASE_URL}/synqit-logo.png`}
        alt=""
        className="h-4 w-4 object-contain"
      />
      <span>Continue with Synqit</span>
    </Button>
  );
}
