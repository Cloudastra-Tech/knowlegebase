import { describe, expect, it } from "vitest"

import { nextIndex, pageFor, shouldAutoStart, stepsFor, TOURS, type TourStep } from "@/lib/tour"

const member = { admin: false, phone: false }
const admin = { admin: true, phone: false }
const ids = (steps: TourStep[]) => steps.map((s) => s.id)

describe("the tours' steps", () => {
  it("gives members none of the admin tour, and admins all of it", () => {
    expect(stepsFor("admin", member)).toEqual([])
    expect(ids(stepsFor("admin", admin))).toEqual(["integrations", "models", "access", "insights"])
    expect(ids(stepsFor("members", member))).toEqual(ids(stepsFor("members", admin)))
  })

  it("leaves the sidebar's chats to the menu step on a phone", () => {
    expect(ids(stepsFor("members", member))).toContain("chats")
    expect(ids(stepsFor("members", { admin: false, phone: true }))).not.toContain("chats")
  })

  it("points every step with an element at a data-tour mark", () => {
    for (const step of [...TOURS.members, ...TOURS.admin]) {
      for (const selector of step.target ?? []) expect(selector).toMatch(/\[data-tour="[a-z-]+"\]/)
    }
  })
})

describe("moving between steps", () => {
  const steps = stepsFor("members", member)
  const at = (id: string) => steps.findIndex((s) => s.id === id)

  it("skips a step whose element isn't on screen, when the step says so", () => {
    const noPicker = (s: TourStep) => s.id !== "model"
    expect(nextIndex(steps, at("composer"), 1, noPicker)).toBe(at("sources"))
    expect(nextIndex(steps, at("sources"), -1, noPicker)).toBe(at("composer"))
    // With the picker there, it's shown.
    expect(nextIndex(steps, at("composer"), 1, () => true)).toBe(at("model"))
  })

  it("shows a step without its element in the middle, when the step says so", () => {
    // No answer on screen yet: the sources step is still shown, without a light.
    expect(nextIndex(steps, at("model"), 1, () => false)).toBe(at("sources"))
  })

  it("ends after the last step, and stays on the first going back", () => {
    expect(nextIndex(steps, steps.length - 1, 1, () => true)).toBe(-1)
    expect(nextIndex(steps, 0, -1, () => true)).toBe(-1)
  })
})

describe("the page a step needs", () => {
  const sources = TOURS.members.find((s) => s.id === "sources")!
  const prompts = TOURS.members.find((s) => s.id === "prompts")!
  const chats = TOURS.members.find((s) => s.id === "chats")!

  it("stays on any chat, new or saved, and goes back to the last one otherwise", () => {
    expect(pageFor(sources, "/chat", "/chat/abc")).toBeNull()
    expect(pageFor(sources, "/chat/abc", "/chat")).toBeNull()
    expect(pageFor(sources, "/settings/prompts", "/chat/abc")).toBe("/chat/abc")
  })

  it("goes to a step's own page, and nowhere for a step on every page", () => {
    expect(pageFor(prompts, "/chat", "/chat")).toBe("/settings/prompts")
    expect(pageFor(prompts, "/settings/prompts", "/chat")).toBeNull()
    expect(pageFor(chats, "/settings/prompts", "/chat")).toBeNull()
  })
})

describe("starting by itself", () => {
  const first = { seen: false, firstRun: true, path: "/chat", impersonating: false }

  it("starts once, on a first sign-in, on the chat", () => {
    expect(shouldAutoStart(first)).toBe(true)
    expect(shouldAutoStart({ ...first, path: "/chat/abc" })).toBe(true)
  })

  it("doesn't start when seen, after a first question, elsewhere, for support, or before it knows", () => {
    expect(shouldAutoStart({ ...first, seen: true })).toBe(false)
    expect(shouldAutoStart({ ...first, firstRun: false })).toBe(false)
    expect(shouldAutoStart({ ...first, path: "/settings" })).toBe(false)
    expect(shouldAutoStart({ ...first, impersonating: true })).toBe(false)
    expect(shouldAutoStart({ ...first, seen: undefined })).toBe(false)
  })
})
