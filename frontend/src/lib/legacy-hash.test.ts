import { describe, expect, it } from "vitest"

import { legacyRedirect } from "@/lib/legacy-hash"

describe("legacyRedirect", () => {
  it.each([
    ["#login", "/login"],
    ["#login?error=sso_failed&ref=K7Q2-9XMA", "/login?error=sso_failed&ref=K7Q2-9XMA"],
    ["#signup", "/signup"],
    ["#signup?error=invite", "/signup?error=invite"],
    ["#sso-test", "/sso-test"],
    ["#integrations?connected=google_drive&choose=1", "/integrations?connected=google_drive&choose=1"],
    ["#integrations?error=access_denied", "/integrations?error=access_denied"],
  ])("sends /%s to %s", (hash, to) => {
    expect(legacyRedirect("/", hash)?.to).toBe(to)
  })

  it("carries the SSO code in state, not the URL", () => {
    expect(legacyRedirect("/", "#sso?welcome=1&code=abc")).toEqual({
      to: "/sso",
      state: { code: "abc", welcome: true },
    })
    expect(legacyRedirect("/", "#sso?code=abc")?.state).toEqual({ code: "abc", welcome: false })
  })

  it.each(["", "#how", "#features", "#security", "#integrations"])(
    "leaves the landing page's %s alone",
    (hash) => {
      expect(legacyRedirect("/", hash)).toBeNull()
    },
  )

  it("only looks at the site's root, where the server sent them", () => {
    expect(legacyRedirect("/chat", "#login")).toBeNull()
  })
})
