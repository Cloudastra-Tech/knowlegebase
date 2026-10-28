import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { MembersView } from "@/components/members-view"
import type { Member } from "@/lib/api"
import { mockApi, withProviders } from "@/test/render"

function member(username: string, more: Partial<Member> = {}): Member {
  return {
    username,
    name: username.split("@")[0],
    role: "public",
    role_source: "default",
    override_role: null,
    group_role: null,
    groups: [],
    groups_updated_at: null,
    admin_kept: false,
    disabled: false,
    has_password: false,
    last_login_at: null,
    created_at: 1,
    scim_managed: false,
    ...more,
  }
}

function renderMembers(members: Member[]) {
  mockApi({
    "GET /api/admin/members": { members },
    "GET /api/admin/members/invites": { invites: [] },
    "GET /api/admin/members/requests": { requests: [], policy: "invite_only", request_days: 14, deny_days: 7 },
  })
  render(withProviders(<MembersView me="ada@acme.example" />))
}

const row = (name: string) => screen.getByText(name).closest("div.px-4") as HTMLElement

describe("MembersView: members SCIM manages", () => {
  it("marks them, and leaves their access and removal to the identity provider", async () => {
    renderMembers([
      member("sam@acme.example", { scim_managed: true, disabled: true }),
      member("pat@acme.example"),
    ])
    await screen.findByText("sam")
    const sam = row("sam")
    expect(within(sam).getByText("SCIM")).toBeInTheDocument()
    expect(within(row("pat")).queryByText("SCIM")).not.toBeInTheDocument()

    const user = userEvent.setup()
    await user.hover(within(sam).getByText("SCIM"))
    expect(
      (await screen.findAllByText(/Managed by your identity provider \(SCIM\)/)).length,
    ).toBeGreaterThan(0)

    await user.click(within(sam).getByRole("button", { name: "More for sam@acme.example" }))
    expect(screen.getByRole("menuitem", { name: "Turn access back on" })).toHaveAttribute(
      "aria-disabled",
      "true",
    )
    expect(screen.getByRole("menuitem", { name: "Remove" })).toHaveAttribute("aria-disabled", "true")
    expect(screen.getByText("Remove them in your identity provider.")).toBeInTheDocument()
    // Signing out and data requests stay the admin's.
    expect(screen.getByRole("menuitem", { name: "Sign out everywhere" })).not.toHaveAttribute(
      "aria-disabled",
    )
  })

  it("lets an admin turn a SCIM member's access off (an emergency)", async () => {
    renderMembers([member("sam@acme.example", { scim_managed: true })])
    await screen.findByText("sam")
    await userEvent.setup().click(screen.getByRole("button", { name: "More for sam@acme.example" }))
    expect(screen.getByRole("menuitem", { name: "Turn access off" })).not.toHaveAttribute(
      "aria-disabled",
    )
  })
})
