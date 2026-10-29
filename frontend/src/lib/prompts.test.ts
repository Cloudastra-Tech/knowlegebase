import { describe, expect, it } from "vitest"

import { fillPrompt, holdsPrompt, slashQuery, variablesOf } from "@/lib/prompts"

describe("saved prompts", () => {
  it("finds each variable once, in order", () => {
    expect(variablesOf("Summarise {{ policy }} for {{customer}}; cc {{customer}} {{1bad}}")).toEqual([
      "policy",
      "customer",
    ])
  })

  it("fills values in on one line and leaves the rest", () => {
    expect(fillPrompt("Reply to {{customer}} about {{topic}}", { customer: "Acme\n Ltd" })).toBe(
      "Reply to Acme Ltd about {{topic}}",
    )
    expect(fillPrompt("{{a}}", { a: "x".repeat(300) })).toHaveLength(200)
  })

  it("reads a slash command only at the start, before a new line", () => {
    expect(slashQuery("/onb")).toBe("onb")
    expect(slashQuery("/")).toBe("")
    expect(slashQuery("a /b")).toBeNull()
    expect(slashQuery("/a\nb")).toBeNull()
  })

  it("holds a prompt while its text is in the box whole, added to or re-spaced", () => {
    const body = "Summarise the refund policy\nfor a customer"
    expect(holdsPrompt(body, body)).toBe(true)
    expect(holdsPrompt(`Context: Acme. ${body} in two lines`, body)).toBe(true)
    expect(holdsPrompt("Summarise  the refund policy for a customer ", body)).toBe(true)
    expect(holdsPrompt("What is the refund policy?", body)).toBe(false) // replaced
    expect(holdsPrompt("Summarise the leave policy for a customer", body)).toBe(false) // edited into
    expect(holdsPrompt("", body)).toBe(false)
    expect(holdsPrompt("anything", "  ")).toBe(false)
  })
})
