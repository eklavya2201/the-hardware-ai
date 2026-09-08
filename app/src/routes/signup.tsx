import { createFileRoute } from '@tanstack/react-router';
import { AuthView } from '@/views/AuthView';

export const Route = createFileRoute('/signup')({
  component: () => <AuthView mode="signup" />,
});
