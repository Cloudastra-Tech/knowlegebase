import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, Outlet, useLocation } from "react-router"
import { RouterProvider } from "react-router/dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { AccountMenu } from "@/components/account-menu"
import { ProductTour } from "@/components/product-tour"
import { SidebarProvider } from "@/components/ui/sidebar"
import type { AccountRole, Onboarding, Role, User } from "@/lib/api"
import { AuthContext } from "@/lib/auth-context"
import { startTour } from "@/lib/tour"
import { mockApi, withProviders } from "@/test/render"

// The tour over a stand-in for the app: the account menu and the chat history in a
// sidebar, and on each page the elements the steps point at (data-tour), as the real
// screens mark them. jsdom lays nothing out, so a marked element reports a size.

const TOURS_UNSEEN = { members: { seen: false, finished_at: null, skipped_at: null } }

function Page({ picker }: { picker: boolean }) {
  const { pathname } = useLocation()
  return (
    <main>
      <p data-testid="path">{pathname}</p>
      {pathname.startsWith("/chat") && (
        <>
          <div data-tour="composer">
            <textarea aria-label="Your question" />
          </div>
          {picker && <button data-tour="model-picker">Model</button>}
        </>
      )}
      {pathname === "/settings/prompts" && <div data-tour="prompts">Prompts</div>}
      {pathname === "/integrations" && (
        <div data-tour="integrations">
          <div>Google Drive</div>
        </div>
      )}
      {pathname === "/admin/assistant" && <section data-tour="models">Models</section>}
      {pathname === "/admin/members" && <div data-tour="access-tabs">Members</div>}
      {pathname === "/admin/insights" && <div data-tour="insights-tabs">Insights</div>}
    </main>
  )
}

function setup({
  role = "public",
  welcome = { first_run: true, tours: TOURS_UNSEEN },
  picker = false,
  path = "/chat",
}: {
  role?: AccountRole
  welcome?: Partial<Onboarding>
  picker?: boolean
  path?: string
} = {}) {
  const user: User = { username: "pat@example.com", name: "Pat", workspace: "acme", role }
  const saved: { tour: string; outcome: string }[] = []
  mockApi({
    "GET /api/onboarding": { suggestions: [], sample: { on: false, available: false, files: [] }, ...welcome },
    "PUT /api/onboarding/tours/members": (body: { outcome: string }) => {
      saved.push({ tour: "members", outcome: body.outcome })
      return { members: { seen: true, finished_at: 1, skipped_at: null } }
    },
    "PUT /api/onboarding/tours/admin": (body: { outcome: string }) => {
      saved.push({ tour: "admin", outcome: body.outcome })
      return { members: { seen: true, finished_at: 1, skipped_at: null }, admin: { seen: true, finished_at: 1, skipped_at: null } }
    },
  })
  const auth = {
    user,
    role: (role === "public" ? "public" : "hr") as Role,
    setRole: () => {},
    signedIn: () => {},
    signedOut: () => {},
    leftOnPurpose: false,
  }
  const Shell = () => (
    <AuthContext value={auth}>
      <SidebarProvider>
        <ProductTour chatAt="/chat" />
        <aside>
          <div data-tour="chat-history">Your chats</div>
          <AccountMenu />
        </aside>
        <Outlet />
      </SidebarProvider>
    </AuthContext>
  )
  const router = createMemoryRouter([{ Component: Shell, children: [{ path: "*", element: <Page picker={picker} /> }] }], {
    initialEntries: [path],
  })
  render(withProviders(<RouterProvider router={router} />))
  return { saved, router }
}

// A step is shown: its title is the dialog's name, and it isn't on its way to it.
async function step(title: string) {
  const dialog = await screen.findByRole("dialog", { name: title }, { timeout: 6000 })
  await waitFor(() => expect(dialog.querySelector("[aria-busy]")).toBeNull(), { timeout: 6000 })
  return dialog
}

beforeEach(() => {
  // A size for the marked elements, so they count as on screen.
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const marked = this.hasAttribute("data-tour") || !!this.parentElement?.hasAttribute("data-tour")
    return marked ? new DOMRect(100, 100, 200, 40) : new DOMRect(0, 0, 0, 0)
  })
})

describe("ProductTour", () => {
  it("starts by itself on a first sign-in, skips what isn't there, and is kept as finished", async () => {
    const user = userEvent.setup()
    const { saved } = setup()
    let dialog = await step("Ask in plain words")
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(within(dialog).getByText("1 of 5")).toBeInTheDocument() // no model picker: 5, not 6
    await user.click(within(dialog).getByRole("button", { name: "Next" }))
    dialog = await step("Check where it came from") // no answer: shown without a light
    await user.click(within(dialog).getByRole("button", { name: "Next" }))
    dialog = await step("Save the questions you repeat")
    expect(screen.getByTestId("path")).toHaveTextContent("/settings/prompts") // it went there
    await user.click(within(dialog).getByRole("button", { name: "Next" }))
    dialog = await step("Find a past chat")
    await user.click(within(dialog).getByRole("button", { name: "Next" }))
    dialog = await step("Settings, and this tour again")
    expect(within(dialog).getByText("5 of 5")).toBeInTheDocument()
    expect(within(dialog).queryByRole("button", { name: "Skip" })).toBeNull()
    expect(within(dialog).queryByRole("button", { name: "Admin tour" })).toBeNull() // a member
    await user.click(within(dialog).getByRole("button", { name: "Done" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(saved).toEqual([{ tour: "members", outcome: "finished" }])
    expect(screen.getByTestId("path")).toHaveTextContent(/^\/chat$/) // back where it started
  }, 20_000)

  it("shows the model picker's step when there's a choice", async () => {
    setup({ picker: true })
    const dialog = await step("Ask in plain words")
    expect(within(dialog).getByText("1 of 6")).toBeInTheDocument()
  })

  it("goes with the arrow keys and closes on Esc, kept as skipped", async () => {
    const user = userEvent.setup()
    const { saved } = setup()
    await step("Ask in plain words")
    await user.keyboard("{ArrowRight}")
    await step("Check where it came from")
    await user.keyboard("{ArrowLeft}")
    await step("Ask in plain words")
    await user.keyboard("{Escape}")
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(saved).toEqual([{ tour: "members", outcome: "skipped" }])
  }, 15_000)

  it("doesn't start by itself once seen (in another browser too)", async () => {
    setup({ welcome: { first_run: true, tours: { members: { seen: true, finished_at: 1, skipped_at: null } } } })
    await act(() => new Promise((r) => setTimeout(r, 1500)))
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it("runs again from the account menu, focus back on it after Skip", async () => {
    const user = userEvent.setup()
    const { saved } = setup({ welcome: { first_run: false, tours: TOURS_UNSEEN } })
    await user.click(screen.getByRole("button", { name: /^Account,/ }))
    expect(screen.queryByRole("menuitem", { name: "Admin tour" })).toBeNull() // a member
    await user.click(screen.getByRole("menuitem", { name: "Take the tour" }))
    const dialog = await step("Ask in plain words")
    await user.click(within(dialog).getByRole("button", { name: "Skip" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(saved).toEqual([{ tour: "members", outcome: "skipped" }])
    await waitFor(() => expect(screen.getByRole("button", { name: /^Account,/ })).toHaveFocus())
  }, 15_000)

  it("gives the admin tour to admins only", async () => {
    setup({ welcome: { first_run: false, tours: TOURS_UNSEEN } })
    act(() => startTour("admin"))
    await act(() => new Promise((r) => setTimeout(r, 800)))
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it("walks an admin through the admin screens, from the menu", async () => {
    const user = userEvent.setup()
    const { saved } = setup({ role: "admin", welcome: { first_run: false, tours: { ...TOURS_UNSEEN, admin: TOURS_UNSEEN.members } } })
    await user.click(screen.getByRole("button", { name: /^Account,/ }))
    await user.click(screen.getByRole("menuitem", { name: "Take the tour" }))
    await user.keyboard("{ArrowRight}{ArrowDown}{Enter}") // into its submenu: The basics, then Admin tour
    let dialog = await step("Connect your sources")
    expect(screen.getByTestId("path")).toHaveTextContent("/integrations")
    for (const [title, path] of [
      ["Choose the models", "/admin/assistant"],
      ["Decide who sees what", "/admin/members"],
      ["See what people couldn't find", "/admin/insights"],
    ]) {
      await user.click(within(dialog).getByRole("button", { name: "Next" }))
      dialog = await step(title)
      expect(screen.getByTestId("path")).toHaveTextContent(path)
    }
    await user.click(within(dialog).getByRole("button", { name: "Done" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(saved).toEqual([{ tour: "admin", outcome: "finished" }])
  }, 20_000)

  it("offers the admin tour at the end of the members' one, to admins", async () => {
    const user = userEvent.setup()
    setup({ role: "admin", welcome: { first_run: false, tours: TOURS_UNSEEN } })
    act(() => startTour("members"))
    let dialog = await step("Ask in plain words")
    for (let i = 0; i < 4; i++) {
      await user.keyboard("{ArrowRight}")
      dialog = await step(["Check where it came from", "Save the questions you repeat", "Find a past chat", "Settings, and this tour again"][i])
    }
    await user.click(within(dialog).getByRole("button", { name: "Admin tour" }))
    await step("Connect your sources")
  }, 20_000)

  it("ends, as skipped, when the page changes under it", async () => {
    const { saved, router } = setup()
    await step("Ask in plain words")
    await act(() => router.navigate("/knowledge"))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(saved).toEqual([{ tour: "members", outcome: "skipped" }])
    expect(screen.getByTestId("path")).toHaveTextContent("/knowledge") // left where they went
  }, 15_000)
})
