// Asking the assistant (/api/chat, /api/chat/stream).

import { ApiError, request, retryAfterOf, session, unauthorized } from "@/lib/api/client"
import type { ChatReply, StreamEvent } from "@/lib/api/types"

export const chatApi = {
  chat: (question: string) =>
    request<ChatReply>("/chat", { method: "POST", body: JSON.stringify({ question }) }),

  // Live version of chat(): calls onEvent for each step / answer update as it
  // happens (Server-Sent Events), and resolves with the final reply.
  async chatStream(question: string, onEvent: (e: StreamEvent) => void): Promise<ChatReply> {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.token}`,
        "X-Role": session.role,
      },
      body: JSON.stringify({ question }),
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
}
