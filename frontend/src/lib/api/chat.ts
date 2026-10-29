// Asking the assistant (/api/chat, /api/chat/stream), the saved conversations
// (/api/conversations: history, folders, search, branches, feedback on answers),
// the workspace's chat settings, and share links (/api/shares, /api/shared).

import {
  ApiError,
  download,
  json,
  request,
  retryAfterOf,
  seg,
  session,
  unauthorized,
} from "@/lib/api/client"
import type {
  ChatFolder,
  ChatModels,
  ChatReply,
  ChatSearchResult,
  ChatSettings,
  Conversation,
  ConversationEdit,
  ConversationList,
  ConversationSummary,
  Feedback,
  FeedbackReason,
  ModelRef,
  Passage,
  SharedChat,
  ShareLink,
  StreamEvent,
} from "@/lib/api/types"

export interface StreamOptions {
  conversationId?: string // saved there, answered with the conversation so far
  retryOf?: string // ask the question of this failed or stopped answer again
  regenerateOf?: string // a new answer beside this finished one (a new branch)
  editOf?: string // the question replaces this earlier question (a new branch)
  model?: ModelRef // the answer model picked; refused (400) unless it's enabled
  promptId?: string // the saved prompt it came from (one set not to search answers directly)
  signal?: AbortSignal // aborting stops the answer (the server saves it as stopped)
}

export const chatApi = {
  chat: (question: string) =>
    request<ChatReply>("/chat", { method: "POST", body: JSON.stringify({ question }) }),

  // Live version of chat(): calls onEvent for each step / answer update as it
  // happens (Server-Sent Events), and resolves with the final reply. Aborting
  // `signal` rejects with an AbortError.
  async chatStream(
    question: string,
    onEvent: (e: StreamEvent) => void,
    { conversationId, retryOf, regenerateOf, editOf, model, promptId, signal }: StreamOptions = {},
  ): Promise<ChatReply> {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.token}`,
        "X-Role": session.role,
      },
      body: JSON.stringify({
        question,
        conversation_id: conversationId,
        retry_of: retryOf,
        regenerate_of: regenerateOf,
        edit_of: editOf,
        model,
        prompt_id: promptId,
      }),
      signal,
    })
    if (!res.ok || !res.body) {
      let message = `Request failed (${res.status})`
      try {
        const data = await res.json()
        if (typeof data.detail === "string") message = data.detail
      } catch {
        /* not JSON */
      }
      if (res.status === 401) unauthorized()
      throw new ApiError(res.status, message, retryAfterOf(res))
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let cut
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const line = buffer.slice(0, cut).trim()
        buffer = buffer.slice(cut + 2)
        if (!line.startsWith("data:")) continue
        const event = JSON.parse(line.slice(5)) as StreamEvent
        if (event.type === "done") return event
        if (event.type === "error") throw new ApiError(500, event.message)
        onEvent(event)
      }
    }
    throw new ApiError(500, "The answer stream ended unexpectedly.")
  },

  // The answer models the composer's picker offers (the workspace's enabled ones).
  chatModels: () => request<ChatModels>("/models"),

  // A cited passage with the text around it; 404 when it's gone from the index or
  // the role the app is viewed as can't see it.
  passage: (chunkId: string) => request<Passage>(`/passages/${seg(chunkId)}`),

  // The signed-in person's chats (most recently used first) and their folders.
  conversations: (limit = 200) => request<ConversationList>(`/conversations?limit=${limit}`),
  searchConversations: (q: string) =>
    request<{ results: ChatSearchResult[] }>(
      `/conversations/search?q=${encodeURIComponent(q)}`,
    ).then((r) => r.results),
  // spaceId: asked in that space (its sources only); none: everything you may see.
  createConversation: (spaceId?: string | null) =>
    request<ConversationSummary>("/conversations", json("POST", spaceId ? { space_id: spaceId } : {})),
  conversation: (id: string) => request<Conversation>(`/conversations/${seg(id)}`),
  updateConversation: (id: string, edit: ConversationEdit) =>
    request<ConversationSummary>(`/conversations/${seg(id)}`, json("PATCH", edit)),
  renameConversation: (id: string, title: string) =>
    request<ConversationSummary>(`/conversations/${seg(id)}`, json("PATCH", { title })),
  deleteConversation: (id: string) =>
    request<{ ok: true }>(`/conversations/${seg(id)}`, json("DELETE")),
  // The chat as shown, as a markdown file (answers you can't see now left out).
  exportConversation: (id: string) => download(`/conversations/${seg(id)}/export`),
  // "Branch in new chat": a new chat with a copy of this one up to the answer.
  forkConversation: (id: string, messageId: string) =>
    request<ConversationSummary>(
      `/conversations/${seg(id)}/fork`,
      json("POST", { message_id: messageId }),
    ),

  createFolder: (name: string) => request<ChatFolder>("/conversation-folders", json("POST", { name })),
  renameFolder: (id: string, name: string) =>
    request<ChatFolder>(`/conversation-folders/${seg(id)}`, json("PATCH", { name })),
  deleteFolder: (id: string) =>
    request<{ ok: true }>(`/conversation-folders/${seg(id)}`, json("DELETE")),

  // An answer as markdown, its citations as footnotes (Copy).
  answerMarkdown: (messageId: string) =>
    request<{ markdown: string }>(`/messages/${seg(messageId)}/markdown`).then((r) => r.markdown),

  chatSettings: () => request<ChatSettings>("/chat-settings"),
  setChatSettings: (retentionDays: number | null, sharingEnabled: boolean) =>
    request<ChatSettings>(
      "/admin/chat-settings",
      json("PUT", { retention_days: retentionDays, sharing_enabled: sharingEnabled }),
    ),

  createShare: (conversationId: string, messageId: string | null, expiresInDays: number | null) =>
    request<ShareLink>(
      "/shares",
      json("POST", {
        conversation_id: conversationId,
        message_id: messageId,
        expires_in_days: expiresInDays,
      }),
    ),
  shares: (conversationId: string) =>
    request<{ shares: ShareLink[] }>(
      `/shares?conversation_id=${encodeURIComponent(conversationId)}`,
    ).then((r) => r.shares),
  revokeShare: (id: string) => request<{ ok: true }>(`/shares/${seg(id)}`, json("DELETE")),
  openShare: (token: string) => request<SharedChat>(`/shared/${seg(token)}`),
  // "Continue in my chats": a chat of your own with what the link shows you.
  continueShare: (token: string) =>
    request<{ id: string; title: string; skipped: number }>(
      `/shared/${seg(token)}/continue`,
      json("POST"),
    ),

  giveFeedback: (
    messageId: string,
    rating: 1 | -1,
    reasons: FeedbackReason[] = [],
    comment: string | null = null,
  ) =>
    request<Feedback>(
      `/messages/${seg(messageId)}/feedback`,
      json("PUT", { rating, reasons, comment }),
    ),
  removeFeedback: (messageId: string) =>
    request<{ ok: true }>(`/messages/${seg(messageId)}/feedback`, json("DELETE")),
}
