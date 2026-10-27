import { describe, expect, it } from "vitest"

import { cn } from "./utils"

describe("cn", () => {
  it("keeps the theme's font sizes next to a text colour", () => {
    expect(cn("font-mono text-ascii", "text-muted-foreground")).toBe("font-mono text-ascii text-muted-foreground")
    expect(cn("text-reading", "text-foreground")).toBe("text-reading text-foreground")
  })

  it("lets one font size replace another", () => {
    expect(cn("text-sm", "text-reading")).toBe("text-reading")
    expect(cn("text-ascii", "text-xs")).toBe("text-xs")
  })
})
