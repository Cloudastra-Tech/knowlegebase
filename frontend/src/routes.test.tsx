import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { session, type User } from "@/lib/api"
import { mockApi, renderApp } from "@/test/render"

const ADMIN: User = { username: "ada", name: "Ada Admin", workspace: "acme", role: "admin" }
const PUBLIC: User = { username: "pat", name: "Pat Public", workspace: "acme", role: "public" }

const PASSWORD_ONLY = {
  providers: [],
  password: true,
  email_sso: false,
  signup: false,
  signup_invite: false,
}
const IDLE = {
  status: "idle",
  step: 0,
  total: 0,
  message: "",
  pending: false,
  summary: null,
  error: null,
  finished_at: null,
}
// What the signed-in app loads whatever the screen.
const SIGNED_IN = {
  "GET /api/index/status": IDLE,
  "GET /api/knowledge": { workspace: "acme", items: [], outline: false },
  "GET /api/integrations": { workspace: "acme", integrations: [] },
}

describe("old hash addresses", () => {
  it("sends /#login?error=... to /login and shows why", async () => {
    mockApi({ "GET /api/auth/providers": PASSWORD_ONLY })
    const router = renderApp("/#login?error=expired")

    expect(await screen.findByText("That sign-in expired. Please try again.")).toBeInTheDocument()
    expect(router.state.location.pathname).toBe("/login")
    expect(router.state.location.search).toBe("?error=expired")
    expect(router.state.location.hash).toBe("")
  })

  it("swaps /#sso?code=... for a session, without the code in the URL", async () => {
    const exchanged: unknown[] = []
    mockApi({
      ...SIGNED_IN,
      "POST /api/auth/sso/exchange": (body: unknown) => {
        exchanged.push(body)
        return { token: "tok-1", user: ADMIN }
      },
    })
    const router = renderApp("/#sso?code=one-time")

    await waitFor(() => expect(router.state.location.pathname).toBe("/chat"))
    expect(exchanged).toEqual([{ code: "one-time" }]) // once, though StrictMode-safe
    expect(session.token).toBe("tok-1")
    expect(router.state.location.search + router.state.location.hash).toBe("")
    expect(await screen.findByRole("link", { name: "Access" })).toBeInTheDocument()
  })

  it("sends a new workspace's admin from /#sso?welcome=1 to Access", async () => {
    mockApi({
      ...SIGNED_IN,
      "GET /api/admin/sign-in": { domains: [], connections: [], events: [] },
      "POST /api/auth/sso/exchange": { token: "tok-2", user: ADMIN },
    })
    const router = renderApp("/#sso?welcome=1&code=c")

    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/sign-in"))
  })

  it("sends a used /#sso code to the sign-in page", async () => {
    mockApi({
      "GET /api/auth/providers": PASSWORD_ONLY,
      "POST /api/auth/sso/exchange": new Response('{"detail": "expired"}', { status: 400 }),
    })
    const router = renderApp("/#sso?code=used")

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"))
    expect(router.state.location.search).toBe("?error=expired")
  })

  it("sends /#sso-test to the test window's page, without restoring a session", async () => {
    const fetch = mockApi({})
    const router = renderApp("/#sso-test")

    expect(await screen.findByText("Test finished")).toBeInTheDocument()
    expect(router.state.location.pathname).toBe("/sso-test")
    expect(fetch).not.toHaveBeenCalled()
  })

  it("keeps the landing page's own #integrations section", async () => {
    mockApi({})
    const router = renderApp("/#integrations")

    expect(await screen.findAllByText(/one question away/)).not.toHaveLength(0)
    expect(router.state.location.pathname).toBe("/")
    expect(router.state.location.hash).toBe("#integrations")
  })
})

describe("signed out", () => {
  it("sends a deep link to sign-in, then back to it", async () => {
    mockApi({
      ...SIGNED_IN,
      "GET /api/auth/providers": PASSWORD_ONLY,
      "POST /api/auth/login": { token: "tok-3", user: ADMIN },
    })
    const router = renderApp("/knowledge")

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"))
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("Username or email"), "ada")
    await user.type(screen.getByLabelText("Password"), "secret")
    await user.click(screen.getByRole("button", { name: "Sign in with password" }))

    await waitFor(() => expect(router.state.location.pathname).toBe("/knowledge"))
    expect(await screen.findByText("Knowledge base")).toBeInTheDocument()
    expect(session.token).toBe("tok-3")
  })

  it("restores a stored session on reload, at the same URL", async () => {
    session.setToken("tok-4")
    mockApi({ ...SIGNED_IN, "GET /api/auth/me": ADMIN })
    const router = renderApp("/integrations")

    expect(await screen.findByText("Integrations", { selector: "h2" })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe("/integrations")
  })
})

describe("roles", () => {
  it("keeps a public account off the HR screens", async () => {
    session.setToken("tok-5")
    mockApi({ ...SIGNED_IN, "GET /api/auth/me": PUBLIC })
    const router = renderApp("/documents")

    await waitFor(() => expect(router.state.location.pathname).toBe("/chat"))
    expect(screen.queryByRole("link", { name: "Documents" })).not.toBeInTheDocument()
  })
})
