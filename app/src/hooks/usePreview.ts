import { useAuth } from '@/contexts/AuthContext';
import { uploadFile } from '@/lib/studioData';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';

interface UsePreviewOptions {
  id: string | undefined;
  conversationId: string;
  generateBlob: () => Promise<Blob>;
  enabled?: boolean;
  userId?: string;
}

/**
 * "Get or create" cached preview image.
 * Checks Supabase storage at `images/{userId}/{convId}/preview-{id}` and
 * returns a data URL. Generates and uploads if not cached.
 */
export function usePreview({
  id,
  conversationId,
  generateBlob,
  enabled = true,
  userId: userIdProp,
}: UsePreviewOptions): UseQueryResult<string> {
  const { user } = useAuth();
  const userId = userIdProp ?? user?.id;

  return useQuery({
    queryKey: ['preview', conversationId, id],
    queryFn: async () => {
      if (!userId || !id) throw new Error('usePreview: missing userId or id');

      // Rendered here, then stored so the server can attach it to a later
      // turn. A failed upload is not worth failing the preview over: the
      // person still sees the thumbnail.
      const blob = await generateBlob();
      try {
        await uploadFile(blob, {
          kind: 'preview',
          conversationId,
          filename: `preview-${id}.png`,
        });
      } catch (error) {
        console.warn('[usePreview] upload failed, continuing:', error);
      }
      return blobToDataUrl(blob);
    },
    enabled: enabled && !!userId && !!id,
    staleTime: Infinity,
    retry: false,
  });
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}
