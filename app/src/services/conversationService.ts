import { useAuth } from '@/contexts/AuthContext';
import { Conversation } from '@shared/types';
import { getConversation, updateConversation as patchConversation } from '@/lib/studioData';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from '@tanstack/react-router';

const defaultConversation: Conversation = {
  id: '',
  title: '',
  current_message_leaf_id: null,
  user_id: '',
  created_at: '',
  updated_at: '',
  privacy: 'private',
  type: 'parametric',
  settings: null,
};

export function useConversation() {
  const { id: conversationId } = useParams({
    from: '/_layout/_auth/editor/$id',
  });
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const { data: conversation, isLoading: isConversationLoading } =
    useQuery<Conversation>({
      queryKey: ['conversation', conversationId],
      enabled: !!conversationId,
      refetchOnMount: false,
      queryFn: async () => {
        if (!conversationId) {
          throw new Error('Conversation ID is required');
        }
        if (!user?.id) {
          throw new Error('User must be authenticated');
        }

        // Ownership is enforced by the engine: someone else's conversation is
        // a 404, so there is no user_id filter to remember here.
        return (await getConversation(conversationId)) as unknown as Conversation;
      },
    });

  const { mutate: updateConversation, mutateAsync: updateConversationAsync } =
    useMutation({
      mutationFn: async (conversation: Conversation) => {
        const updated = await patchConversation(conversation.id, {
          title: conversation.title,
          privacy: conversation.privacy,
          settings: (conversation.settings ?? {}) as Record<string, unknown>,
          current_message_leaf_id: conversation.current_message_leaf_id,
        });
        return updated as unknown as Conversation;
      },
      onMutate: async (conversation) => {
        // Cancel any outgoing refetches
        await queryClient.cancelQueries({
          queryKey: ['conversation', conversation.id],
        });

        // Snapshot the previous value
        const oldConversation = queryClient.getQueryData<Conversation>([
          'conversation',
          conversation.id,
        ]);

        // Optimistically update to the new value
        queryClient.setQueryData(
          ['conversation', conversation.id],
          conversation,
        );

        // Return a context object with the snapshotted value
        return { oldConversation };
      },
      onSuccess: (data) => {
        // Update the cache with the server response
        queryClient.setQueryData(['conversation', conversationId], data);

        // Only invalidate the conversations list, not the individual conversation
        // This prevents unnecessary refetch of the conversation we just updated
        queryClient.invalidateQueries({
          queryKey: ['conversations'],
        });
      },
      onError: (_error, conversation, context) => {
        // If the mutation fails, use the context returned from onMutate to roll back
        queryClient.setQueryData(
          ['conversation', conversation.id],
          context?.oldConversation,
        );
      },
    });

  return {
    conversation: conversation ?? defaultConversation,
    isConversationLoading,
    updateConversation,
    updateConversationAsync,
  };
}
