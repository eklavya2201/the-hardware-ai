import { useQuery } from '@tanstack/react-query';
import { MeshData } from '@shared/types';
import { fileUrl } from '@/lib/studioData';

/**
 * An attached mesh, by id.
 *
 * No status polling: a mesh is a file the person attached, so it is ready the
 * moment it exists. The old store also tracked generated meshes, which had a
 * pending state worth polling; that lane is gone.
 */
export const useMeshData = ({ id }: { id: string }) => {
  const dataQuery = useQuery({
    queryKey: ['meshData', id],
    enabled: !!id,
    queryFn: async () =>
      ({ id, status: 'success', file_type: 'glb' }) as unknown as MeshData,
  });

  const blobQuery = useQuery({
    queryKey: ['mesh', id],
    enabled: !!id,
    refetchOnMount: false,
    queryFn: async () => {
      const res = await fetch(fileUrl(id), { credentials: 'include' });
      if (!res.ok) throw new Error(`Could not read mesh ${id} (${res.status}).`);
      return res.blob();
    },
  });

  return { data: dataQuery, blob: blobQuery };
};
