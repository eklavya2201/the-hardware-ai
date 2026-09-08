import { readEngineCookie, synqitApiBase } from './synqit';

/**
 * The studio's data, from the server, as the person making the request.
 *
 * The browser never gets a database token: it holds an httpOnly cookie, and
 * this forwards that cookie to the engine. So every read and write here is
 * already scoped to the caller — there is no user id to remember to filter on,
 * and no service key that could accidentally read someone else's rows.
 */
export type StoreConversation = {
  id: string;
  user_id: string;
  title: string;
  type: string;
  privacy: string;
  settings: Record<string, unknown>;
  current_message_leaf_id: string | null;
};

export type StoreMessage = {
  id: string;
  conversation_id: string;
  user_id: string;
  role: string;
  parts: unknown[];
  metadata: Record<string, unknown>;
  parent_message_id: string | null;
  rating: number;
  created_at: string | null;
};

export type StoreUser = { id: string; email: string; name?: string | null };

export class NotSignedIn extends Error {
  constructor() {
    super('Not signed in.');
    this.name = 'NotSignedIn';
  }
}

/** A client bound to one request's session. */
export function studioStore(request: Request) {
  const session = readEngineCookie(request);

  async function call<T>(path: string, init?: RequestInit): Promise<T> {
    if (!session) throw new NotSignedIn();
    const res = await fetch(`${synqitApiBase()}${path}`, {
      ...init,
      headers: {
        ...(init?.headers ?? {}),
        cookie: `session=${encodeURIComponent(session)}`,
      },
    });
    if (res.status === 401) throw new NotSignedIn();
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))) as { detail?: string };
      throw new Error(detail.detail || `The engine answered ${res.status}.`);
    }
    return (await res.json()) as T;
  }

  const asJson = (method: string, body: unknown): RequestInit => ({
    method,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });

  return {
    hasSession: Boolean(session),

    async user(): Promise<StoreUser> {
      const me = await call<StoreUser | null>('/auth/me');
      if (!me?.id) throw new NotSignedIn();
      return me;
    },

    conversation(id: string) {
      return call<StoreConversation>(`/studio/conversations/${encodeURIComponent(id)}`);
    },

    patchConversation(id: string, patch: Record<string, unknown>) {
      return call<StoreConversation>(
        `/studio/conversations/${encodeURIComponent(id)}`,
        asJson('PATCH', patch),
      );
    },

    messages(conversationId: string) {
      return call<StoreMessage[]>(
        `/studio/conversations/${encodeURIComponent(conversationId)}/messages`,
      );
    },

    putMessage(
      conversationId: string,
      message: {
        id?: string;
        role: string;
        parts?: unknown[];
        metadata?: Record<string, unknown>;
        parent_message_id?: string | null;
        advance_leaf?: boolean;
      },
    ) {
      return call<StoreMessage>(
        `/studio/conversations/${encodeURIComponent(conversationId)}/messages`,
        asJson('PUT', message),
      );
    },

    patchMessage(
      conversationId: string,
      messageId: string,
      patch: { parts?: unknown[]; metadata?: Record<string, unknown> },
    ) {
      return call<StoreMessage>(
        `/studio/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}`,
        asJson('PATCH', patch),
      );
    },

    /** A stored file, as bytes — used to hand an attached image to the model. */
    async fileBytes(fileId: string): Promise<{ bytes: Uint8Array; contentType: string }> {
      if (!session) throw new NotSignedIn();
      const res = await fetch(
        `${synqitApiBase()}/studio/files/${encodeURIComponent(fileId)}`,
        { headers: { cookie: `session=${encodeURIComponent(session)}` } },
      );
      if (!res.ok) throw new Error(`Could not read file ${fileId} (${res.status}).`);
      return {
        bytes: new Uint8Array(await res.arrayBuffer()),
        contentType: res.headers.get('content-type') ?? 'application/octet-stream',
      };
    },
  };
}

export type StudioStore = ReturnType<typeof studioStore>;
