import { describe, expect, it } from "vitest"

import { isChatPath } from "@/lib/routes"

describe("isChatPath", () => {
  it("is a new chat or a saved one, nothing else", () => {
    expect(isChatPath("/chat")).toBe(true)
    expect(isChatPath("/chat/abc")).toBe(true)
    expect(isChatPath("/chats")).toBe(false)
    expect(isChatPath("/chatter/abc")).toBe(false)
    expect(isChatPath("/settings/prompts")).toBe(false)
    expect(isChatPath("/")).toBe(false)
  })
})
