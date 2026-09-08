import { useQueries, useQuery } from '@tanstack/react-query';
import { fileUrl } from '@/lib/studioData';

/**
 * An attached image, by id.
 *
 * There is no status to poll and no signed URL to mint any more: an image
 * exists the moment it is uploaded, and the engine serves it from a
 * same-origin URL the browser can put straight in an `img` tag. The old store
 * needed a row to say whether generation had finished, and a download to build
 * a data URL, because the bucket was on another origin.
 */
export type ImageRecord = { id: string; status: 'success'; url: string };

function record(id: string): ImageRecord {
  return { id, status: 'success', url: fileUrl(id) };
}

export function useImageData(id: string) {
  const dataQuery = useQuery({
    queryKey: ['imageData', id],
    enabled: !!id,
    queryFn: async () => record(id),
  });

  const urlQuery = useQuery({
    queryKey: ['image', id],
    enabled: !!id,
    queryFn: async () => ({ id, url: fileUrl(id) }),
  });

  return { data: dataQuery, url: urlQuery };
}

export function useImagesData(ids: string[]) {
  const dataQueries = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['imageData', id],
      enabled: !!id,
      queryFn: async () => record(id),
    })),
  });

  const urlQueries = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['image', id],
      enabled: !!id,
      queryFn: async () => ({ id, url: fileUrl(id) }),
    })),
  });

  return { data: dataQueries, url: urlQueries };
}
