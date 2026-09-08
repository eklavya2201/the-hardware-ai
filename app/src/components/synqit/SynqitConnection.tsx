import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';
import { getSynqitSession, linkSynqitSession, type SynqitSession } from '@/lib/synqitApi';
import { useAuth } from '@/contexts/AuthContext';

export const SYNQIT_AUTH_EVENT = 'synqit-signin-requested';

/** Anything that hits a 401 from the engine asks for a re-link through this. */
export function requestSynqitSignIn() {
  window.dispatchEvent(new Event(SYNQIT_AUTH_EVENT));
}

/**
 * Keeps the studio connected to the engine.
 *
 * One account, not two. Signing in to the studio is the only sign-in there is;
 * the engine account that owns the boards is provisioned server-side the first
 * time it is needed. So this component asks for nothing — it links on load and
 * again whenever a board call reports 401, and only puts something on screen
 * when linking actually fails.
 */
export function SynqitConnection() {
  const { user } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef<Promise<SynqitSession> | null>(null);

  const link = useCallback(async () => {
    if (!user) return;
    // A burst of 401s must not become a burst of link calls.
    if (inFlight.current) return inFlight.current.then(() => undefined);
    setBusy(true);
    const attempt = linkSynqitSession();
    inFlight.current = attempt;
    try {
      await attempt;
      setError(null);
      // Views cache per project id; make them re-read with the new session.
      window.dispatchEvent(new Event('synqit-project-change'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not connect to Synqit.');
    } finally {
      inFlight.current = null;
      setBusy(false);
    }
  }, [user]);

  // On load: link only if we do not already hold a session.
  useEffect(() => {
    if (!user) return;
    let live = true;
    getSynqitSession().then((s) => {
      if (live && !s.signedIn) void link();
    });
    return () => {
      live = false;
    };
  }, [user, link]);

  useEffect(() => {
    const onAsk = () => void link();
    window.addEventListener(SYNQIT_AUTH_EVENT, onAsk);
    return () => window.removeEventListener(SYNQIT_AUTH_EVENT, onAsk);
  }, [link]);

  if (!error) return null;

  return (
    <div className="fixed bottom-4 left-1/2 z-50 flex max-w-md -translate-x-1/2 items-start gap-3 rounded-lg border border-red-500/40 bg-sq-background-1 px-4 py-3 shadow-xl">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
      <div className="text-xs text-sq-text-secondary">
        <p className="text-sq-text-primary">Not connected to the Synqit engine</p>
        <p className="mt-1">{error}</p>
        <p className="mt-1">Boards, the part library and the schematic agent are unavailable until this works.</p>
      </div>
      <button
        type="button"
        onClick={() => void link()}
        disabled={busy}
        className="ml-auto shrink-0 rounded-md bg-sq-blue/15 px-2 py-1 text-xs text-sq-blue hover:bg-sq-blue/25"
      >
        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Retry'}
      </button>
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
