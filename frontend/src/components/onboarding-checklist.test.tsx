import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"

import { OnboardingChecklist } from "@/components/onboarding-checklist"
import type { Checklist, Onboarding } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

const step = (id: string, title: string, done = false, optional = false) => ({
  id,
  title,
  optional,
  done,
  done_at: done ? 1 : null,
})

const CHECKLIST = {
  steps: [
    step("domain", "Verify your email domain", true),
    step("source", "Connect your documents"),
    step("index", "Index them"),
    step("answer", "Get a first answer with a source"),
    step("colleagues", "3 colleagues sign in"),
    step("idp", "Add your company's identity provider", false, true),
    step("slack", "Connect Slack", false, true),
  ],
  complete: false,
  dismissed: false,
  sample: { on: false, available: true, files: [] },
} as Checklist

const WITH = (checklist: Checklist): Onboarding => ({
  first_run: false,
  suggestions: [],
  sample: checklist.sample,
  checklist,
})

function show(onAddProvider = () => {}) {
  render(
    withProviders(
      <MemoryRouter>
        <OnboardingChecklist onAddProvider={onAddProvider} />
      </MemoryRouter>,
    ),
  )
}

describe("OnboardingChecklist", () => {
  it("shows the steps the server worked out, and links to each one left", async () => {
    mockApi({ "GET /api/onboarding": WITH(CHECKLIST) })
    const add = vi.fn()
    show(add)
    expect(await screen.findByText("4 steps left", { exact: false })).toBeInTheDocument()
    const done = screen.getByText("Verify your email domain").closest("li")!
    expect(done.querySelector("[aria-label='Done']")).not.toBeNull()
    expect(screen.getByRole("link", { name: /Open Integrations/ })).toHaveAttribute(
      "href",
      "/integrations",
    )
    expect(screen.getByRole("link", { name: /Invite people/ })).toHaveAttribute("href", "/admin/members")
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }))
    expect(add).toHaveBeenCalled()
  })

  it("is put away for the whole workspace, on the server", async () => {
    const sent: unknown[] = []
    let current = CHECKLIST
    mockApi({
      "GET /api/onboarding": () => WITH(current),
      "PUT /api/onboarding/dismissed": (body: unknown) => {
        sent.push(body)
        current = { ...CHECKLIST, dismissed: true }
        return current
      },
    })
    show()
    await userEvent.click(await screen.findByRole("button", { name: "Done" }))
    await waitFor(() => expect(screen.queryByText("Set up your workspace")).not.toBeInTheDocument())
    expect(sent).toEqual([{ dismissed: true }])
  })

  it("adds the sample HR handbook and removes it in one click", async () => {
    let sample = { on: false, available: true, files: [] as string[] }
    const calls: string[] = []
    mockApi({
      "GET /api/onboarding": () => WITH({ ...CHECKLIST, sample }),
      "POST /api/onboarding/sample": () => {
        calls.push("add")
        sample = { on: true, available: true, files: ["Leave_and_holidays.txt"] }
        return sample
      },
      "DELETE /api/onboarding/sample": () => {
        calls.push("remove")
        sample = { on: false, available: true, files: [] }
        return sample
      },
    })
    show()
    await userEvent.click(await screen.findByRole("button", { name: "Try it with a sample HR handbook" }))
    await userEvent.click(await screen.findByRole("button", { name: "Remove sample documents" }))
    await screen.findByRole("button", { name: "Try it with a sample HR handbook" })
    expect(calls).toEqual(["add", "remove"])
  })

  it("isn't shown once every step that counts is done", async () => {
    mockApi({ "GET /api/onboarding": WITH({ ...CHECKLIST, complete: true }) })
    show()
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByText("Set up your workspace")).not.toBeInTheDocument()
  })
})
