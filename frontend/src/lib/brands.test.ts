import { describe, expect, it } from "vitest"

import { brandOf, brandsOf } from "@/lib/brands"

describe("brands", () => {
  it("finds a source's platform by its provider's name, however it is written", () => {
    expect(brandOf("Outline")).toBe("outline")
    expect(brandOf("Google Drive")).toBe("googledrive")
    expect(brandOf("Google Docs")).toBe("googledrive")
    expect(brandOf("GitHub")).toBe("github")
    expect(brandOf("Slack")).toBe("slack")
  })

  it("has no mark for an upload, a website or nothing", () => {
    expect(brandOf("Upload")).toBeNull()
    expect(brandOf("Website")).toBeNull()
    expect(brandOf(undefined)).toBeNull()
  })

  it("lists each platform once, in the order first seen", () => {
    expect(brandsOf(["Slack", "Outline", "slack", "Upload", "Outline"])).toEqual(["slack", "outline"])
  })
})
