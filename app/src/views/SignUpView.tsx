import { Link, useNavigate, useLocation } from '@tanstack/react-router';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { useAuth } from '@/contexts/AuthContext';
import { supabase } from '@/lib/supabase';
import { GoogleSignInButton } from '@/components/auth/GoogleSignInButton';
import { SynqitSignInButton } from '@/components/auth/SynqitSignInButton';
import { useEffect } from 'react';
import { validateRedirectUrl } from '@/lib/utils';

function getAppRedirectUrl(path: string) {
  const basePath = import.meta.env.BASE_URL.replace(/\/$/, '');

  return `${window.location.origin}${basePath}${path}`;
}

export function SignUpView() {
  const { toast } = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const { session, user, isLoading: authLoading } = useAuth();

  // Get and validate redirect parameter from URL
  const searchParams = new URLSearchParams(location.searchStr);
  const rawRedirectPath = searchParams.get('redirect');
  const redirectPath = validateRedirectUrl(rawRedirectPath);

  // Redirect to home if already authenticated
  useEffect(() => {
    if (!authLoading && session && user) {
      navigate({ to: '/', replace: true });
    }
  }, [session, user, authLoading, navigate]);


  return (
    <div className="flex min-h-screen items-center justify-center bg-sq-bg-dark p-4">
      <div className="w-full max-w-md">
        <div className="rounded-lg bg-sq-bg-secondary-dark p-8 shadow-md">
          <div className="mb-4 flex flex-col items-center justify-center gap-2">
            <img
              src={`${import.meta.env.BASE_URL}/synqit-wordmark.svg`}
              alt="Synqit CAD Logo"
              className="h-8 w-auto"
            />
          </div>
          <div className="w-full py-2">
            <GoogleSignInButton redirectPath={redirectPath} />
            <SynqitSignInButton redirectPath={redirectPath} />
          </div>
          <div className="pt-4 text-center text-sm text-sq-text-secondary">
            <Link
              to="/signup-email"
              className="text-sq-text-primary hover:underline"
            >
              Sign up with email
            </Link>
            {' or '}
            <Link to="/signin" className="text-sq-blue hover:underline">
              Sign in
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
