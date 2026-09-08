import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import posthog from 'posthog-js';
import {
  AuthContext,
  getLevel,
  type BillingStatus,
  type StudioUser,
} from '@/contexts/AuthContext';
import { apiJson } from '@/services/api';
import { getProfile, StudioAuthError } from '@/lib/studioData';

/**
 * Who is signed in, from the engine.
 *
 * There is one account system: the engine's. The browser holds an httpOnly
 * session cookie it cannot read, so "am I signed in" is a question for the
 * server — `getProfile()` — rather than something decoded from a token here.
 * That is also why there is no session object on the context any more: nothing
 * in the app needs a bearer token, because every call to our own server is
 * same-origin and carries the cookie.
 */
const billingStatusSchema = z.object({
  user: z.object({ hasTrialed: z.boolean() }),
  subscription: z
    .object({
      level: z.enum(['standard', 'pro', 'max']),
      status: z.string().nullable(),
      currentPeriodEnd: z.string().nullable(),
    })
    .nullable(),
  tokens: z.object({
    free: z.number(),
    subscription: z.number(),
    purchased: z.number(),
    total: z.number(),
  }),
});

/** What a self-hosted deployment without the billing service should report. */
const LOCAL_BILLING_STATUS: BillingStatus = {
  user: { hasTrialed: false },
  subscription: null,
  tokens: { free: 1_000_000, subscription: 0, purchased: 0, total: 1_000_000 },
};

const SESSION_URL = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/api/synqit-session`;
const LOGIN_URL = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/api/synqit-login`;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [user, setUser] = useState<StudioUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const loadUser = useMemo(
    () => async () => {
      try {
        const profile = await getProfile();
        setUser(profile);
      } catch (error) {
        if (!(error instanceof StudioAuthError)) {
          console.warn('Could not read the profile:', error);
        }
        setUser(null);
      } finally {
        setIsLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void loadUser();
  }, [loadUser]);

  useEffect(() => {
    if (user) posthog.identify(user.id, { email: user.email });
  }, [user]);

  const { data: billing, isLoading: isBillingLoading } = useQuery({
    queryKey: ['billing', 'status'],
    enabled: !!user,
    refetchInterval: 30000,
    queryFn: async (): Promise<BillingStatus> => {
      try {
        return await apiJson('billing-status', {}, billingStatusSchema);
      } catch {
        // No billing service on a self-hosted deployment is not an error.
        return LOCAL_BILLING_STATUS;
      }
    },
  });

  useEffect(() => {
    if (!user || isBillingLoading) return;
    posthog.setPersonProperties({
      email: user.email,
      subscription: getLevel(billing),
      has_trialed: billing?.user.hasTrialed ?? false,
      name: user.full_name ?? '',
    });
  }, [user, isBillingLoading, billing]);

  const signIn = async (email: string, password: string) => {
    await postCredentials({ email, password });
    await loadUser();
  };

  const signUp = async (email: string, password: string, name: string) => {
    await postCredentials({ email, password, name, signup: true });
    await loadUser();
  };

  const signOut = async () => {
    await fetch(SESSION_URL, { method: 'DELETE', credentials: 'include' });
    setUser(null);
    queryClient.clear();
    await navigate({ to: '/' });
  };

  return (
    <AuthContext.Provider
      value={{ user, billing: billing ?? null, isLoading, signIn, signUp, signOut }}
    >
      {children}
    </AuthContext.Provider>
  );
}

async function postCredentials(body: {
  email: string;
  password: string;
  name?: string;
  signup?: boolean;
}) {
  const res = await fetch(LOGIN_URL, {
    method: 'POST',
    credentials: 'include',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = (await res.json().catch(() => ({}))) as { error?: string };
  if (!res.ok) throw new Error(data.error || 'Could not sign in.');
}
