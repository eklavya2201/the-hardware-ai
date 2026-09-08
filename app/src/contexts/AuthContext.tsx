import { createContext, useContext } from 'react';

export type SubscriptionLevel = 'standard' | 'pro' | 'max';
export type PlanLevel = SubscriptionLevel | 'free';

/**
 * A person, as the engine knows them.
 *
 * There is one account system now: the engine's. This used to be a Supabase
 * `User`, which meant the studio carried a second identity that had to be kept
 * in step with the one that actually owns the boards.
 */
export type StudioUser = {
  id: string;
  email: string;
  full_name?: string | null;
  avatar_url?: string | null;
  is_admin?: boolean;
};

export type BillingStatus = {
  user: {
    hasTrialed: boolean;
  };
  subscription: {
    level: SubscriptionLevel;
    status: string | null;
    currentPeriodEnd: string | null;
  } | null;
  tokens: {
    free: number;
    subscription: number;
    purchased: number;
    total: number;
  };
};

const ACTIVE_STATUSES = new Set(['active', 'trialing']);

export function getLevel(billing: BillingStatus | null | undefined): PlanLevel {
  if (!billing?.subscription) return 'free';
  if (!ACTIVE_STATUSES.has(billing.subscription.status ?? '')) return 'free';
  return billing.subscription.level;
}

interface AuthContextType {
  user: StudioUser | null;
  billing: BillingStatus | null;
  isLoading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, name: string) => Promise<void>;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextType | undefined>(
  undefined,
);

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
