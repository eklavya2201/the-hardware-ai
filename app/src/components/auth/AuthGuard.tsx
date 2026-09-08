import { useEffect } from 'react';
import { useNavigate, useLocation } from '@tanstack/react-router';
import { useAuth } from '@/contexts/AuthContext';
// The module-level `toast`, not the hook: the SSO error handler lives inside
// the guard's redirect effect, and a hook-returned reference would have to
// join the effect's dependency array. The module function is identity-stable
// by construction, keeping the deps — and the non-SSO re-run behavior —
// exactly as they were before SSO mode existed.
import { Loader2 } from 'lucide-react';

interface AuthGuardProps {
  children: React.ReactNode;
}

export function AuthGuard({ children }: AuthGuardProps) {
  const { user, isLoading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  // In SSO mode the guard fires the provider redirect itself — once per
  // mount (StrictMode re-runs effects in dev).

  useEffect(() => {
    if (!isLoading && !user) {
      // Where to come back to after signing in. Pathname and search only —
      // never the hash, which can carry tokens.
      const next = location.pathname + location.searchStr;
      navigate({ to: '/signin', search: { next }, replace: true });
    }
  }, [
    user,
    navigate,
    isLoading,
    location.pathname,
    location.searchStr,
  ]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-blue-500" />
      </div>
    );
  }

  if (!user) return null;

  return <>{children}</>;
}
