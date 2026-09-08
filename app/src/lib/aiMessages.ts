import type { AppUIMessage } from '@shared/chatAi';
import { asParametricParts } from '@shared/parametricParts';
import type { Message } from '@shared/types';

/**
 * Tree-friendly assistant/user message used throughout the chat view.
 *
 * `id`, `role`, `parts`, `metadata` come from the AI SDK side (live during
 * streaming). `parent_message_id` is required because `Tree` builds parent
 * pointers from it. Everything else (`rating`, `created_at`, `conversation_id`,
 * legacy content) is DB-only and only present once the row has been persisted
 * — so it stays optional. Renderers default these fields gracefully
 * (`message.rating ?? 0`, etc.) instead of relying on placeholders.
 */
export type ChatMessage = AppUIMessage & {
  parent_message_id: string | null;
  conversation_id?: string;
  created_at?: string;
  rating?: number;
  isLegacy?: boolean;
  legacyContent?: unknown;
};

export function messageRowToUIMessage(message: Message): AppUIMessage {
  return {
    id: message.id,
    role: message.role,
    metadata:
      message.metadata &&
      typeof message.metadata === 'object' &&
      !Array.isArray(message.metadata)
        ? message.metadata
        : {},
    parts: asParametricParts(message.parts),
  };
}

export function messageRowToChatMessage(message: Message): ChatMessage {
  const parts = asParametricParts(message.parts);
  const legacyContent = (message as Message & { content?: unknown }).content;
  const isLegacy = parts.length === 0 && legacyContent != null;
  return {
    ...messageRowToUIMessage(message),
    conversation_id: message.conversation_id,
    parent_message_id: message.parent_message_id,
    created_at: message.created_at,
    rating: message.rating,
    ...(isLegacy ? { isLegacy: true, legacyContent } : {}),
  };
}

