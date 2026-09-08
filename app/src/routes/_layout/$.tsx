import { createFileRoute, Navigate } from '@tanstack/react-router';

export const Route = createFileRoute('/_layout/$')({
  component: () => <Navigate to="/app" replace />,
});
