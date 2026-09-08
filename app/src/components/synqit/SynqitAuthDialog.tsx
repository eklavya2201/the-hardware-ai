import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  getSynqitSession,
  signInToSynqit,
  SYNQIT_APP_URL,
  type SynqitSession,
} from '@/lib/synqitApi';

export const SYNQIT_AUTH_EVENT = 'synqit-signin-requested';

/** Anything that hits a 401 from the engine asks for the dialog through this. */
export function requestSynqitSignIn() {
  window.dispatchEvent(new Event(SYNQIT_AUTH_EVENT));
}

/**
 * Connect the studio to a Synqit account.
 *
 * The board tools — the part library, the schematic agent, placement, routing
 * — run on the engine and act on that person's own projects, so they need
 * their engine session. The credentials are posted once to the studio's own
 * server, which exchanges them for a session cookie; the password is never
 * stored and never reaches the browser again.
 */
export function SynqitAuthDialog() {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onAsk = () => {
      setError(null);
      setOpen(true);
    };
    window.addEventListener(SYNQIT_AUTH_EVENT, onAsk);
    return () => window.removeEventListener(SYNQIT_AUTH_EVENT, onAsk);
  }, []);

  if (!open) return null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signInToSynqit(email, password);
      setPassword('');
      setOpen(false);
      // Views cache per project id; make them re-read with the new session.
      window.dispatchEvent(new Event('synqit-project-change'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not sign in.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-xl border border-sq-neutral-600 bg-sq-background-1 p-5 shadow-2xl"
      >
        <h2 className="text-base font-semibold text-sq-text-primary">Connect your Synqit account</h2>
        <p className="mt-1 text-xs text-sq-text-secondary">
          Boards, the part library and the schematic agent run on your Synqit account. Sign in once and the
          studio designs there on your behalf.
        </p>

        <label className="mt-4 block text-xs text-sq-text-secondary">
          Email
          <input
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="mt-1 w-full rounded-md border border-sq-neutral-600 bg-sq-background-2 px-2 py-1.5 text-sm text-sq-text-primary outline-none focus:border-sq-blue"
          />
        </label>
        <label className="mt-3 block text-xs text-sq-text-secondary">
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="mt-1 w-full rounded-md border border-sq-neutral-600 bg-sq-background-2 px-2 py-1.5 text-sm text-sq-text-primary outline-none focus:border-sq-blue"
          />
        </label>

        {error && <p className="mt-3 text-xs text-red-400">{error}</p>}

        <div className="mt-5 flex items-center justify-between gap-2">
          <a
            href={`${SYNQIT_APP_URL}/signup`}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-sq-blue hover:underline"
          >
            Create a Synqit account
          </a>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Connect'}
            </Button>
          </div>
        </div>
      </form>
    </div>
  );
}

/** Whether the studio currently holds an engine session. */
export function useSynqitSession(): SynqitSession | null {
  const [session, setSession] = useState<SynqitSession | null>(null);
  useEffect(() => {
    let live = true;
    const read = () => {
      getSynqitSession().then((s) => live && setSession(s));
    };
    read();
    window.addEventListener('synqit-project-change', read);
    return () => {
      live = false;
      window.removeEventListener('synqit-project-change', read);
    };
  }, []);
  return session;
}
