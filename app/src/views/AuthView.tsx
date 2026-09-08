import { useState } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { Loader2 } from 'lucide-react';
import { supabase } from '@/lib/supabase';
import BlueprintBackground from '@/components/marketing/BlueprintBackground';

const LINE = '#E5E4E0';

/**
 * Sign in and sign up, in the landing page's clothes rather than the studio's
 * dark chrome — this is the front of the product, and the dark card people
 * landed on after the light hero read like a different site.
 *
 * One set of credentials: your Synqit account. The engine checks the password
 * (that is where every existing account lives) and the server mirrors the
 * identity into the studio's own store behind the scenes. See
 * routes/api/synqit-login.ts.
 */
export function AuthView({ mode }: { mode: 'signin' | 'signup' }) {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isSignup = mode === 'signup';

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${import.meta.env.BASE_URL}api/synqit-login`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email, password, name: isSignup ? name : undefined, signup: isSignup }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        access_token?: string;
        refresh_token?: string;
        error?: string;
      };
      if (!res.ok || !data.access_token || !data.refresh_token) {
        throw new Error(data.error || 'Could not sign in.');
      }
      const { error: sessionError } = await supabase.auth.setSession({
        access_token: data.access_token,
        refresh_token: data.refresh_token,
      });
      if (sessionError) throw new Error(sessionError.message);
      await navigate({ to: '/app' });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not sign in.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#f9f9f8] text-[#16150F]">
      <BlueprintBackground />

      <header className="relative z-10 mx-auto flex h-16 max-w-[1280px] items-center px-6">
        <Link to="/" className="flex items-center gap-2 text-[20px] font-bold tracking-tight">
          <img src={`${import.meta.env.BASE_URL}synqit-logo.png`} alt="" className="h-6 w-6 object-contain" />
          Synqit
        </Link>
      </header>

      <main className="relative z-10 mx-auto flex max-w-[440px] flex-col px-6 pb-24 pt-10">
        <h1 className="text-[34px] font-bold leading-tight tracking-[-0.03em]">
          {isSignup ? 'Create your Synqit account.' : 'Welcome back.'}
        </h1>
        <p className="mt-3 text-[15px] leading-relaxed text-[#6B6760]">
          {isSignup
            ? 'One account for the studio and for the boards it designs.'
            : 'The same account you use on Synqit — boards, library and all.'}
        </p>

        <form
          onSubmit={submit}
          className="mt-8 rounded-[5px] border bg-white p-6 shadow-[0_2px_24px_rgba(0,0,0,0.06)]"
          style={{ borderColor: LINE }}
        >
          {isSignup && (
            <Field label="Name">
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoComplete="name"
                className="w-full rounded-[3px] border px-3 py-2.5 text-[15px] outline-none focus:border-[#B5532A]"
                style={{ borderColor: LINE }}
              />
            </Field>
          )}
          <Field label="Email">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="username"
              className="w-full rounded-[3px] border px-3 py-2.5 text-[15px] outline-none focus:border-[#B5532A]"
              style={{ borderColor: LINE }}
            />
          </Field>
          <Field label="Password">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete={isSignup ? 'new-password' : 'current-password'}
              className="w-full rounded-[3px] border px-3 py-2.5 text-[15px] outline-none focus:border-[#B5532A]"
              style={{ borderColor: LINE }}
            />
          </Field>

          {error && <p className="mt-4 text-[13px] text-[#B5532A]">{error}</p>}

          <button
            type="submit"
            disabled={busy}
            className="mt-6 flex w-full items-center justify-center rounded-[3px] bg-[#111110] px-8 py-3.5 font-mono text-[12px] uppercase tracking-wide text-white transition-transform active:scale-[0.98] disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : isSignup ? 'Create account' : 'Sign in'}
          </button>
        </form>

        <p className="mt-6 text-center text-[14px] text-[#6B6760]">
          {isSignup ? 'Already have an account? ' : 'No account yet? '}
          <Link to={isSignup ? '/signin' : '/signup'} className="text-[#B5532A] hover:underline">
            {isSignup ? 'Sign in' : 'Create one'}
          </Link>
        </p>
      </main>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="mt-4 block first:mt-0">
      <span className="font-mono text-[11px] uppercase tracking-wide text-[#6B6760]">{label}</span>
      <div className="mt-1.5">{children}</div>
    </label>
  );
}
