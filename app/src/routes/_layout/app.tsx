import { createFileRoute } from '@tanstack/react-router';
import { PromptView } from '@/views/PromptView';

/**
 * The studio's home. `?prompt=` carries a brief written on the landing page:
 * PromptView runs it when someone is signed in, and puts it in the composer
 * when they are not, so the sentence they typed is never lost to a sign-up.
 */
export const Route = createFileRoute('/_layout/app')({
  // Returning {} rather than { prompt: undefined } keeps the key optional --
  // otherwise every navigate({ to: '/app' }) in the app is required to pass a
  // search object.
  validateSearch: (search: Record<string, unknown>): { prompt?: string } =>
    typeof search.prompt === 'string' && search.prompt ? { prompt: search.prompt } : {},
  component: PromptView,
});
