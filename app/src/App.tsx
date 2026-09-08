import { AuthProvider } from '@/contexts/AuthProvider';
import { TooltipProvider } from './components/ui/tooltip';
import { Toaster } from './components/ui/toaster';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Outlet } from '@tanstack/react-router';
import { MeshFilesProvider } from '@/contexts/MeshFilesContext';
import { PostHogProvider } from '@/contexts/PostHogProvider';
import { ErrorView } from '@/views/ErrorView';

const queryClient = new QueryClient();


function App({ error }: { error?: unknown }) {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <PostHogProvider>
          <MeshFilesProvider>
            <TooltipProvider delayDuration={0}>
              <Toaster />
              {error !== undefined ? <ErrorView error={error} /> : <Outlet />}
            </TooltipProvider>
          </MeshFilesProvider>
        </PostHogProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
