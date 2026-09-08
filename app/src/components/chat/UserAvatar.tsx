import { useAuth } from '@/contexts/AuthContext';
import { useProfile, useAvatarUrl } from '@/services/profileService';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { getInitials } from '@/lib/utils';

export function UserAvatar({ className }: { className?: string }) {
  const { user } = useAuth();
  const { data: profile } = useProfile();
  const { data: avatarUrl } = useAvatarUrl(profile?.avatar_path);

  // The engine holds the account, so its avatar is the source of truth; a
  // separately uploaded one is the fallback.
  const src = user?.avatar_url || avatarUrl || undefined;

  return (
    <Avatar className={className}>
      <AvatarImage src={src} />
      <AvatarFallback>{getInitials(profile?.full_name || null)}</AvatarFallback>
    </Avatar>
  );
}
