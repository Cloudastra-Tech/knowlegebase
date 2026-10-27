import { describe, expect, it, vi } from "vitest"

import {
  ApiError,
  ask,
  citeMarkers,
  disconnect,
  exchange,
  normalizeHost,
  sseParser,
  type AskEvent,
} from "./api"

const HOST = "https://kb.example.com"
const KEY = "kb_live_0123456789ab_secret"

// A streamed response whose body arrives in these pieces.
function streamed(pieces: string[], status = 200) {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const piece of pieces) controller.enqueue(encoder.encode(piece))
      controller.close()
    },
  })
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } })
}

const event = (e: object) => `data: ${JSON.stringify(e)}\n\n`

describe("sseParser", () => {
  it("emits each event once it is complete, across pieces", () => {
    const seen: AskEvent[] = []
    const feed = sseParser((e) => seen.push(e))
    const whole = event({ type: "answer", text: "Hello" }) + event({ type: "status", text: "x" })
    feed(whole.slice(0, 10))
    expect(seen).toEqual([])
    feed(whole.slice(10, 40))
    feed(whole.slice(40))
    expect(seen.map((e) => e.type)).toEqual(["answer", "status"])
  })

  it("skips malformed events and comments", () => {
    const seen: AskEvent[] = []
    const feed = sseParser((e) => seen.push(e))
    feed(": keep-alive\n\ndata: {not json\n\n" + event({ type: "answer", text: "ok" }))
    expect(seen).toEqual([{ type: "answer", text: "ok" }])
  })
})

describe("ask", () => {
  it("sends the key, the question and the chosen context, and returns the answer", async () => {
    const done = { type: "done", answer: "Thirty days [S1].", sources: [], state: "answered" }
    const f = vi.fn(async () =>
      streamed([event({ type: "status", text: "Checking" }), event({ type: "answer", text: "Thi" }), event(done)]),
    )
    const events: string[] = []
    const got = await ask(
      HOST,
      KEY,
      "Refunds?",
      { context: "  selected  ", onEvent: (e) => events.push(e.type) },
      f as unknown as typeof fetch,
    )
    expect(got).toEqual(done)
    expect(events).toEqual(["status", "answer", "done"])
    const [url, init] = (f.mock.calls[0] as unknown as [string, RequestInit])
    expect(url).toBe(`${HOST}/api/v1/ask`)
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${KEY}`)
    expect(JSON.parse(init.body as string)).toEqual({
      question: "Refunds?",
      stream: true,
      context: "selected",
    })
  })

  it("sends no context unless there is some, and caps it", async () => {
    const done = event({ type: "done", answer: "a", sources: [], state: "answered" })
    const f = vi.fn(async () => streamed([done]))
    await ask(HOST, KEY, "q", { context: "   " }, f as unknown as typeof fetch)
    await ask(HOST, KEY, "q", { context: "x".repeat(5000) }, f as unknown as typeof fetch)
    const bodies = f.mock.calls.map((c) => JSON.parse((c as unknown as [string, RequestInit])[1].body as string))
    expect("context" in bodies[0]).toBe(false)
    expect(bodies[1].context).toHaveLength(4000)
  })

  it("turns a stream error event into an ApiError", async () => {
    const f = vi.fn(async () => streamed([event({ type: "error", message: "Something went wrong: X" })]))
    await expect(ask(HOST, KEY, "q", {}, f as unknown as typeof fetch)).rejects.toThrow(
      "Something went wrong: X",
    )
  })

  it("says a stream that ends without an answer was cut off", async () => {
    const f = vi.fn(async () => streamed([event({ type: "answer", text: "half" })]))
    await expect(ask(HOST, KEY, "q", {}, f as unknown as typeof fetch)).rejects.toThrow("cut off")
  })

  it("reports the server's refusals", async () => {
    const limited = vi.fn(async () =>
      Response.json({ detail: "You've asked 20 questions in the last minute." }, { status: 429 }),
    )
    const err = await ask(HOST, KEY, "q", {}, limited as unknown as typeof fetch).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(429)
    expect(err.message).toContain("20 questions")
    const gone = vi.fn(async () => Response.json({ detail: "A valid API key..." }, { status: 401 }))
    await expect(ask(HOST, KEY, "q", {}, gone as unknown as typeof fetch)).rejects.toThrow(
      "no longer connected",
    )
  })
})

describe("signing in and out", () => {
  it("swaps the code for a key, with no key sent", async () => {
    const f = vi.fn(async () =>
      Response.json({ key: KEY, expires_at: 1, name: "Pat", workspace: "acme" }),
    )
    expect((await exchange(HOST, "the-code", f as unknown as typeof fetch)).key).toBe(KEY)
    const [url, init] = (f.mock.calls[0] as unknown as [string, RequestInit])
    expect(url).toBe(`${HOST}/api/v1/extension/exchange`)
    expect(JSON.parse(init.body as string)).toEqual({ code: "the-code" })
    expect(JSON.stringify(init.headers)).not.toContain("Authorization")
  })

  it("disconnecting revokes the key, and a key already gone is fine", async () => {
    const f = vi.fn(async () => Response.json({ detail: "gone" }, { status: 401 }))
    await disconnect(HOST, KEY, f as unknown as typeof fetch)
    const [url, init] = (f.mock.calls[0] as unknown as [string, RequestInit])
    expect(url).toBe(`${HOST}/api/v1/extension/key`)
    expect(init.method).toBe("DELETE")
  })
})

describe("normalizeHost", () => {
  it("keeps the origin, adds https, and allows http on this machine only", () => {
    expect(normalizeHost(" kb.example.com/chat ")).toBe(HOST)
    expect(normalizeHost("http://localhost:5180/")).toBe("http://localhost:5180")
    expect(() => normalizeHost("http://kb.example.com")).toThrow("https")
    expect(() => normalizeHost("https://")).toThrow()
  })
})

describe("citeMarkers", () => {
  it("links each [S#] to its source and drops unknown ones", () => {
    const sources = [
      { n: 1, title: "Guide", url: "https://wiki.example/guide" },
      { n: 2, title: "Contract", chunk_id: "c 2" },
      { n: 3, title: "No link" },
    ]
    expect(citeMarkers("A [S1]. B [S2]. C [S3]. D [S9].", sources, HOST)).toBe(
      `A [[1]](https://wiki.example/guide). B [[2]](${HOST}/passages/c%202). C [3]. D.`,
    )
  })

  it("never links a source's javascript: address", () => {
    expect(citeMarkers("x [S1]", [{ n: 1, title: "t", url: "javascript:alert(1)" }], HOST)).toBe(
      "x [1]",
    )
  })
})
