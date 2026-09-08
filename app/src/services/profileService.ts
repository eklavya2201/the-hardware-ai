import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/contexts/AuthContext';
import { fileUrl, getProfile, uploadFile } from '@/lib/studioData';

/**
 * The profile, from the engine.
 *
 * There is one account, so there is one profile: the name and photo on the
 * engine account that owns the boards. This used to read a `profiles` mirror
 * in a second database and then override the name from identity-provider
 * claims, because the two could disagree.
 */
export type Profile = {
  user_id: string;
  full_name: string | null;
  avatar_path: string | null;
  notifications_enabled?: boolean;
};

export function useProfile() {
  const { user } = useAuth();

  return useQuery({
    queryKey: ['profile', user?.id],
    enabled: !!user?.id,
    queryFn: async (): Promise<Profile> => {
      const profile = await getProfile();
      return {
        user_id: profile.id,
        full_name: profile.full_name,
        avatar_path: profile.avatar_url,
      };
    },
  });
}

/**
 * The avatar is already a URL the browser can load — the engine serves it —
 * so there is nothing to sign or resolve. Kept as a hook so call sites did not
 * have to change.
 */
export function useAvatarUrl(avatarPath: string | null | undefined) {
  return useQuery({
    queryKey: ['avatar-url', avatarPath],
    enabled: !!avatarPath,
    queryFn: async () => avatarPath ?? null,
  });
}

export function useUpdateProfile() {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  return useMutation({
    // The engine owns the name; there is no separate profile row to patch.
    // Left in place so the settings form keeps its shape, and so this is one
    // obvious place to add the endpoint when names become editable again.
    mutationFn: async (_updates: Partial<Profile>) => {
      throw new Error('Editing the profile is not available yet.');
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profile', user?.id] });
    },
  });
}

export function useUploadAvatar() {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (file: File) => {
      if (!user) throw new Error('User not authenticated');

      const validTypes = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp'];
      if (!validTypes.includes(file.type)) {
        throw new Error('Invalid file type. Please upload a JPEG, PNG, or WebP image.');
      }
      if (file.size > 5 * 1024 * 1024) {
        throw new Error('File too large. Please upload an image smaller than 5MB.');
      }

      const stored = await uploadFile(file, { kind: 'image', filename: 'avatar' });
      return { avatar_path: fileUrl(stored.id) };
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['profile', user?.id] });
      queryClient.invalidateQueries({ queryKey: ['avatar-url', data.avatar_path] });
    },
  });
}
