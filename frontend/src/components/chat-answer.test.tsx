import { render, screen, waitFor } from "@testing-library/react"
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
    expect(screen.getByText("General knowledge · Wikipedia")).toBeInTheDocument()
  })

  it("hides an answer the viewer's role can't see any more", () => {
    show({ state: "answered", text: "", withheld: true, sources: [REFUND] })
    expect(screen.getByRole("alert")).toHaveTextContent("Answer hidden")
    expect(screen.queryByText("refund.md")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Helpful" })).not.toBeInTheDocument()
  })
})

describe("the steps panel", () => {
  const steps = [{ id: "s1", label: "Searching company docs", result: "found 1 relevant document", done: true }]

  it("shows the raw tool input only when the server sent it (admins)", async () => {
    show({ state: "answered", text: "ok", steps, trace: [{ tool: "search_docs" }] })
    const user = userEvent.setup()
    await user.click(screen.getByText("1 step"))
    expect(screen.getByText("Searching company docs")).toBeInTheDocument()
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
    await user.click(screen.getByText("1 step"))
    expect(screen.getByText("How long does a refund take?")).toBeInTheDocument()
    await user.click(screen.getByText("Technical details"))
    expect(screen.getByText(/input: \{"question":"refund"\}/)).toBeInTheDocument()
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
