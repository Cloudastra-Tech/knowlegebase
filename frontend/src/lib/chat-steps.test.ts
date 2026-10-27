import { describe, expect, it } from "vitest"

import {
  detailOf,
  formatMs,
  staggerOf,
  statusOf,
  summarize,
  thinkingText,
  titleOf,
  type LiveStep,
} from "@/lib/chat-steps"

const docs = (over: Partial<LiveStep> = {}): LiveStep => ({
  id: "s1",
  tool: "search_docs",
  label: "Searching company docs",
  done: true,
  result: "found 2 relevant documents",
  ...over,
})
const wiki = (over: Partial<LiveStep> = {}): LiveStep => ({
  id: "s2",
  tool: "search_outline",
  label: "Searching the Outline wiki for “leave”",
  done: true,
  result: "3 matching pages",
  ...over,
})

describe("statusOf", () => {
  it("reads a step's status from its result", () => {
    expect(statusOf(docs())).toBe("done")
    expect(statusOf(docs({ result: "blocked by access rules" }))).toBe("blocked")
    expect(statusOf(docs({ result: "blocked: it would have sent company text outside" }))).toBe("blocked")
    expect(statusOf(docs({ result: "failed" }))).toBe("error")
  })

  it("is running without a result while streaming, and cancelled once the answer ended", () => {
    const open = docs({ result: undefined, done: false })
    expect(statusOf(open, { streaming: true })).toBe("running")
    expect(statusOf(open)).toBe("cancelled")
    // Stop marks every step done, without a result: still not a success.
    expect(statusOf({ ...open, done: true }, { state: "stopped" })).toBe("cancelled")
    expect(statusOf(open, { state: "error" })).toBe("error")
  })
})

describe("titles", () => {
  it("are present tense while running and past tense when done", () => {
    expect(titleOf(docs(), "running")).toBe("Searching company docs")
    expect(titleOf(docs(), "done")).toBe("Searched company docs")
    expect(titleOf(wiki(), "done")).toBe("Searched the Outline wiki")
    expect(
      titleOf({ id: "w", tool: "wikipedia", label: "Looking up “Alan Turing” on Wikipedia", done: true }, "done"),
    ).toBe("Looked up “Alan Turing” on Wikipedia")
    expect(
      titleOf({ id: "c", tool: "convert_currency", label: "Converting 24000 INR to USD", done: true }, "done"),
    ).toBe("Converted INR to USD")
    expect(
      titleOf({ id: "r", tool: "read_outline_doc", label: "Reading a wiki page", result: "Travel policy", done: true }, "done"),
    ).toBe("Read “Travel policy”")
    expect(titleOf({ id: "v", tool: "verified_answer", label: "Checking verified answers", done: true }, "done")).toBe(
      "Matched a verified answer",
    )
  })

  it("say what went wrong", () => {
    expect(titleOf(docs(), "cancelled")).toBe("Stopped while searching company docs")
    expect(titleOf(docs(), "blocked")).toBe("Blocked while searching company docs")
    expect(titleOf(docs(), "error")).toBe("Failed while searching company docs")
    expect(titleOf({ id: "x", tool: "new_tool", label: "Using new_tool", done: true }, "done")).toBe("Using new_tool")
  })

  it("describe the result and the time taken", () => {
    expect(detailOf(docs({ ms: 812 }), "done")).toBe("found 2 relevant documents · 0.8s")
    expect(detailOf(docs({ result: "blocked by access rules" }), "blocked")).toBe("by access rules")
    expect(detailOf(docs({ result: undefined, ms: undefined }), "running")).toBe("")
  })
})

describe("formatMs", () => {
  it("keeps one decimal under ten seconds", () => {
    expect(formatMs(1234)).toBe("1.2s")
    expect(formatMs(12_600)).toBe("13s")
  })
})

describe("summarize", () => {
  it("joins what was done, what was found and how long it took", () => {
    const a = docs({ startedAt: 100, ms: 800 })
    const b = wiki({ startedAt: 110, ms: 1100 })
    expect(summarize([a]).text).toBe("Searched company docs · 2 documents · 0.8s")
    expect(summarize([a, b]).text).toBe("Searched company docs and the wiki · 2 documents · 1.1s")
    expect(summarize([docs({ result: "nothing relevant found" })]).text).toBe(
      "Searched company docs · 0 documents",
    )
  })

  it("goes live while a step runs, and says when something went wrong", () => {
    const running = wiki({ result: undefined, done: false })
    expect(summarize([docs(), running], { streaming: true })).toEqual({
      status: "running",
      text: "Searching the Outline wiki for “leave”",
    })
    expect(summarize([docs(), running], { state: "stopped" }).text).toBe("Stopped")
    expect(summarize([docs({ result: "blocked by access rules" })]).status).toBe("blocked")
    expect(summarize([docs({ result: "failed" })]).text).toBe("A step failed")
  })
})

describe("thinkingText", () => {
  it("follows the work: reading, the running step, then writing", () => {
    expect(thinkingText([], "Thinking")).toBe("Reading the question")
    expect(thinkingText([], "Checking your question")).toBe("Reading the question")
    expect(thinkingText([docs({ result: undefined, done: false })])).toBe("Searching company docs")
    expect(thinkingText([docs()])).toBe("Writing the answer")
  })
})

describe("staggerOf", () => {
  it("staggers steps that start together, at most three deep", () => {
    const at = (t: number) => docs({ startedAt: t })
    expect(staggerOf([at(0), at(5), at(10), at(15), at(20)])).toEqual([0, 1, 2, 3, 3])
    expect(staggerOf([at(0), at(500), at(505)])).toEqual([0, 0, 1])
    expect(staggerOf([docs(), docs()])).toEqual([0, 0]) // saved: no timing
  })
})
