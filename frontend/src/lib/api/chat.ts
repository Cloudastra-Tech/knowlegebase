// Asking the assistant (/api/chat, /api/chat/stream) and the saved conversations
// (/api/conversations, feedback on answers).

import { ApiError, json, request, retryAfterOf, seg, session, unauthorized } from "@/lib/api/client"
import type {
  ChatReply,
  Conversation,
  ConversationSummary,
  Feedback,
  FeedbackReason,
  Passage,
  StreamEvent,
} from "@/lib/api/types"

export interface StreamOptions {
  conversationId?: string // saved there, answered with the conversation so far
  retryOf?: string // ask the question of this failed or stopped answer again
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
    { conversationId, retryOf, signal }: StreamOptions = {},
  ): Promise<ChatReply> {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.token}`,
        "X-Role": session.role,
      },
      body: JSON.stringify({ question, conversation_id: conversationId, retry_of: retryOf }),
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

  // A cited passage with the text around it; 404 when it's gone from the index or
  // the role the app is viewed as can't see it.
  passage: (chunkId: string) => request<Passage>(`/passages/${seg(chunkId)}`),

  conversations: () =>
    request<{ conversations: ConversationSummary[] }>("/conversations").then(
      (r) => r.conversations,
    ),
  createConversation: () => request<ConversationSummary>("/conversations", json("POST", {})),
  conversation: (id: string) => request<Conversation>(`/conversations/${seg(id)}`),
  renameConversation: (id: string, title: string) =>
    request<ConversationSummary>(`/conversations/${seg(id)}`, json("PATCH", { title })),
  deleteConversation: (id: string) =>
    request<{ ok: true }>(`/conversations/${seg(id)}`, json("DELETE")),

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
