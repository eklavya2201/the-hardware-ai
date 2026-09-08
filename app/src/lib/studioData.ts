import { SYNQIT_API_BASE } from '@/lib/synqitApi';

/**
 * The studio's data, from the engine.
 *
 * This replaces the hosted Supabase project: conversations, messages, uploaded
 * files and the profile now live in the same database as the boards, behind
 * the same session. Calls go to the studio's own origin and are forwarded
 * server-side with the engine cookie, so there is no token to attach here and
 * nothing for a browser to refuse.
 */

export type StudioConversation = {
  id: string;
  user_id: string;
  title: string;
  type: 'parametric' | 'creative';
  privacy: 'private' | 'public';
  settings: Record<string, unknown>;
  current_message_leaf_id: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type StudioMessage = {
  id: string;
  conversation_id: string;
  user_id: string;
  role: string;
  parts: unknown[];
  content: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
  parent_message_id: string | null;
  rating: number;
  created_at: string | null;
};

export type StudioProfile = {
  id: string;
  email: string;
  full_name: string | null;
  avatar_url: string | null;
  is_admin: boolean;
};

export type StudioFile = {
  id: string;
  kind: string;
  content_type: string;
  size_bytes: number;
  url: string;
  created_at: string | null;
};

export class StudioAuthError extends Error {
  constructor() {
    super('Not signed in.');
    this.name = 'StudioAuthError';
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${SYNQIT_API_BASE}/studio${path}`, {
    credentials: 'include',
    ...init,
  });
  if (res.status === 401) throw new StudioAuthError();
  if (res.status === 404 && path.includes('/messages/')) {
    throw new MessageNotFoundError(path.split('/').pop() ?? '');
  }
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(detail.detail || `The engine answered ${res.status}.`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function json(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  };
}

// --- conversations ----------------------------------------------------------

export function listConversations(limit = 50) {
  return call<StudioConversation[]>(`/conversations?limit=${limit}`);
}

export function getConversation(id: string) {
  return call<StudioConversation>(`/conversations/${encodeURIComponent(id)}`);
}

export function createConversation(input: {
  id?: string;
  title?: string;
  type?: string;
  privacy?: string;
  settings?: Record<string, unknown>;
}) {
  return call<StudioConversation>('/conversations', json('POST', input));
}

export function updateConversation(
  id: string,
  patch: Partial<Pick<StudioConversation, 'title' | 'privacy' | 'settings' | 'current_message_leaf_id'>>,
) {
  return call<StudioConversation>(`/conversations/${encodeURIComponent(id)}`, json('PATCH', patch));
}

export function deleteConversation(id: string) {
  return call<{ deleted: string }>(`/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

// --- messages ---------------------------------------------------------------

export function listMessages(conversationId: string) {
  return call<StudioMessage[]>(`/conversations/${encodeURIComponent(conversationId)}/messages`);
}

/**
 * Upsert, because a message is written and then rewritten as its tool calls
 * settle. The same id means the same row.
 */
export function putMessage(
  conversationId: string,
  message: {
    id?: string;
    role: string;
    parts?: unknown[];
    content?: Record<string, unknown> | null;
    metadata?: Record<string, unknown>;
    parent_message_id?: string | null;
    rating?: number;
    /** Whether this becomes the tip of the branch. Restores set it themselves. */
    advance_leaf?: boolean;
  },
) {
  return call<StudioMessage>(
    `/conversations/${encodeURIComponent(conversationId)}/messages`,
    json('PUT', message),
  );
}

/**
 * Update an existing message. Rejects with NotFound rather than creating one:
 * the studio rewrites an assistant row that the server may not have written
 * yet, and a phantom row would sit outside the branch.
 */
export function patchMessage(
  conversationId: string,
  messageId: string,
  patch: { parts?: unknown[]; metadata?: Record<string, unknown>; rating?: number },
) {
  return call<StudioMessage>(
    `/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}`,
    json('PATCH', patch),
  );
}

export class MessageNotFoundError extends Error {
  constructor(messageId: string) {
    super(`No message ${messageId}`);
    this.name = 'MessageNotFoundError';
  }
}

export function deleteMessage(conversationId: string, messageId: string) {
  return call<{ deleted: string }>(
    `/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}`,
    { method: 'DELETE' },
  );
}

// --- files ------------------------------------------------------------------

/** The URL a browser can load a stored file from, same-origin like everything else. */
export function fileUrl(fileId: string): string {
  return `${SYNQIT_API_BASE}/studio/files/${encodeURIComponent(fileId)}`;
}

export async function uploadFile(
  file: Blob,
  opts: {
    kind?: 'image' | 'mesh' | 'preview';
    conversationId?: string;
    filename?: string;
    /** Name the file so the server can find it again without being told. */
    fileId?: string;
  } = {},
): Promise<StudioFile> {
  const form = new FormData();
  form.append('file', file, opts.filename ?? 'upload');
  form.append('kind', opts.kind ?? 'image');
  if (opts.conversationId) form.append('conversation_id', opts.conversationId);
  if (opts.fileId) form.append('file_id', opts.fileId);
  const uploaded = await call<StudioFile>('/files', { method: 'POST', body: form });
  // The engine returns its own path; callers want something they can fetch.
  return { ...uploaded, url: fileUrl(uploaded.id) };
}

export function deleteFile(fileId: string) {
  return call<{ deleted: string }>(`/files/${encodeURIComponent(fileId)}`, { method: 'DELETE' });
}

// --- profile ----------------------------------------------------------------

export function getProfile() {
  return call<StudioProfile>('/profile');
}
