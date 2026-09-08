import { createFileRoute } from '@tanstack/react-router';
import { DocsView } from '@/views/DocsView';

export const Route = createFileRoute('/docs')({
  component: DocsView,
});
