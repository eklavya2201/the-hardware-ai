import { createFileRoute } from '@tanstack/react-router';
import { LandingView } from '@/views/LandingView';

/**
 * The front door. The studio itself lives at /app -- this page is what
 * synqit.in serves to someone who has not opened it yet.
 */
export const Route = createFileRoute('/')({
  component: LandingView,
});
