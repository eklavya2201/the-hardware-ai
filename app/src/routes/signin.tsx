import { createFileRoute } from '@tanstack/react-router';
import { AuthView } from '@/views/AuthView';

export const Route = createFileRoute('/signin')({
  component: () => <AuthView mode="signin" />,
});
