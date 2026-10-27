// The extension's calls to the Knowledge assistant: the public API (/api/v1, an API
// key in Authorization) and the sign-in that gets that key. No chrome.* here, so it
// runs in the unit tests (api.test.ts) as it does in the side panel.

export interface Source {
  n?: number | null // the [S#] marker that cites it
  kind?: string | null // "general_knowledge" for Wikipedia
  title: string
  provider?: string | null
  url?: string | null
  chunk_id?: string | null
  snippet?: string | null
}

export type AskState = "answered" | "partial" | "not_found" | "general_knowledge" | "blocked"

// What /api/v1/ask streams (Server-Sent Events), one "data:" line each.
export type AskEvent =
  | { type: "status"; text?: string; message?: string }
  | { type: "step_start"; id?: string; label?: string; tool?: string }
  | { type: "step_end"; id?: string; label?: string; result?: string }
  | { type: "answer"; text: string }
  | { type: "done"; answer: string; sources: Source[]; state: AskState }
  | { type: "error"; message: string }

export interface Connection {
  key: string
  expires_at: number
  name: string
  workspace: string
}

export const MAX_CONTEXT = 4000 // the server refuses more (kb/routers/api_v1.CONTEXT_CHARS)

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Fetch = typeof fetch

// The server's address as an origin: https, or http on this machine only.
export function normalizeHost(input: string): string {
  let text = input.trim()
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) text = `https://${text}`
  let url: URL
  try {
    url = new URL(text)
  } catch {
    throw new Error("That isn't a web address.")
  }
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
  if (url.protocol !== "https:" && !(url.protocol === "http:" && local))
    throw new Error("Use the https:// address of your Knowledge assistant.")
  return url.origin
}

async function failure(res: Response): Promise<ApiError> {
  let message = `The server said ${res.status}.`
  try {
    const body = await res.json()
    if (typeof body?.detail === "string") message = body.detail
    else if (Array.isArray(body?.detail) && body.detail[0]?.msg) message = body.detail[0].msg
  } catch {
    // not JSON: keep the status
  }
  if (res.status === 401) message = "The extension is no longer connected. Connect it again."
  return new ApiError(res.status, message)
}

// The code from the web app's Connect screen -> the extension's own API key.
export async function exchange(host: string, code: string, f: Fetch = fetch): Promise<Connection> {
  const res = await f(`${host}/api/v1/extension/exchange`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  })
  if (!res.ok) throw await failure(res)
  return res.json()
}

// "Disconnect": the server revokes this key. A key already gone is fine.
export async function disconnect(host: string, key: string, f: Fetch = fetch): Promise<void> {
  const res = await f(`${host}/api/v1/extension/key`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${key}` },
  })
  if (!res.ok && res.status !== 401 && res.status !== 404) throw await failure(res)
}

// Server-Sent Events, fed in pieces as they arrive: every complete "data:" event
// goes to onEvent; a piece that ends mid-event waits for the rest.
export function sseParser(onEvent: (event: AskEvent) => void) {
  let buffer = ""
  return (chunk: string) => {
    buffer += chunk.replace(/\r\n/g, "\n")
    let end
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      const data = block
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).replace(/^ /, ""))
        .join("\n")
      if (!data) continue
      try {
        onEvent(JSON.parse(data) as AskEvent)
      } catch {
        // a malformed event is skipped, not fatal
      }
    }
  }
}

// One question, streamed. `context`: the text the asker chose to send with it (the
// server gives it to the model as untrusted data). Resolves with the final answer.
export async function ask(
  host: string,
  key: string,
  question: string,
  options: { context?: string | null; onEvent?: (event: AskEvent) => void; signal?: AbortSignal },
  f: Fetch = fetch,
): Promise<Extract<AskEvent, { type: "done" }>> {
  const context = options.context?.trim().slice(0, MAX_CONTEXT) || undefined
  const res = await f(`${host}/api/v1/ask`, {
    method: "POST",
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ question, stream: true, ...(context ? { context } : {}) }),
    signal: options.signal,
  })
  if (!res.ok) throw await failure(res)
  if (!res.body) throw new ApiError(0, "The server sent no answer.")
  let done: Extract<AskEvent, { type: "done" }> | null = null
  let error: string | null = null
  const feed = sseParser((event) => {
    if (event.type === "done") done = event
    else if (event.type === "error") error = event.message
    options.onEvent?.(event)
  })
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  for (;;) {
    const { value, done: ended } = await reader.read()
    if (ended) break
    feed(decoder.decode(value, { stream: true }))
  }
  feed(decoder.decode() + "\n\n")
  if (error) throw new ApiError(502, error)
  if (!done) throw new ApiError(0, "The answer was cut off. Please try again.")
  return done
}

// Where a source's number links to: the source itself, or the app's passage view
// (sign-in and the reader's role are checked there).
export function sourceLink(source: Source, host: string): string | null {
  if (source.url && /^https?:\/\//i.test(source.url)) return source.url
  if (source.chunk_id) return `${host}/passages/${encodeURIComponent(source.chunk_id)}`
  return null
}

// The answer's [S#] markers as numbered markdown links to their sources; a marker
// with no such source is dropped.
export function citeMarkers(answer: string, sources: Source[], host: string): string {
  const byNumber = new Map(sources.filter((s) => typeof s.n === "number").map((s) => [s.n, s]))
  return answer.replace(/[ \t]*\[S(\d{1,3})\]/g, (_, n: string) => {
    const source = byNumber.get(Number(n))
    if (!source) return ""
    const link = sourceLink(source, host)
    return link ? ` [[${n}]](${link})` : ` [${n}]`
  })
}
