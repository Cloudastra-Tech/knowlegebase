import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { Button } from "@/components/ui/button"
import { Command, CommandDialog, CommandInput, CommandList } from "@/components/ui/command"
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card"
import { Sidebar, SidebarProvider } from "@/components/ui/sidebar"
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

// The Signal Glass primitives (DESIGN.md, "Motion" and "Components"): what they promise
// beyond the stock shadcn ones.

describe("the command palette", () => {
  it("opens and closes without animating, neither the dialog nor its overlay", () => {
    render(
      <CommandDialog open title="Search and go" description="Search your chats.">
        <Command>
          <CommandInput aria-label="Search" />
          <CommandList />
        </Command>
      </CommandDialog>,
    )
    expect(screen.getByRole("dialog", { name: "Search and go" })).toBeInTheDocument()
    for (const slot of ["dialog-content", "dialog-overlay"]) {
      const el = document.querySelector(`[data-slot="${slot}"]`)
      expect(el).not.toBeNull()
      expect(el!.className).not.toMatch(/animate-in|animate-out/)
    }
  })
})

describe("the sidebar", () => {
  it("toggles from the keyboard (⌘B) instantly, without the slide", () => {
    render(
      <SidebarProvider>
        <Sidebar aria-label="Chat history" />
      </SidebarProvider>,
    )
    const sidebar = document.querySelector('[data-slot="sidebar"]')!
    expect(sidebar).toHaveAttribute("data-state", "expanded")
    fireEvent.keyDown(window, { key: "b", ctrlKey: true })
    expect(sidebar).toHaveAttribute("data-state", "collapsed")
    expect(document.documentElement).toHaveAttribute("data-instant")
  })
})

describe("the glass tab rail", () => {
  it("moves its thumb under the active tab, and the thumb isn't a tab", async () => {
    // jsdom has no layout: the second tab sits 80px in, 60px wide.
    vi.spyOn(HTMLElement.prototype, "offsetLeft", "get").mockImplementation(function (this: HTMLElement) {
      return this.textContent === "Members" ? 80 : 0
    })
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (this: HTMLElement) {
      return this.textContent === "Members" ? 60 : 72
    })
    render(
      <Tabs defaultValue="sign-in" panels={false}>
        <TabsList variant="glass" aria-label="Access">
          <TabsTrigger value="sign-in">Sign-in</TabsTrigger>
          <TabsTrigger value="members">Members</TabsTrigger>
        </TabsList>
      </Tabs>,
    )
    const list = screen.getByRole("tablist", { name: "Access" })
    expect(screen.getAllByRole("tab")).toHaveLength(2)
    expect(list.querySelector('[data-slot="tabs-thumb"]')).toHaveAttribute("aria-hidden", "true")
    expect(list.style.getPropertyValue("--thumb-x")).toBe("0px")
    expect(list.style.getPropertyValue("--thumb-w")).toBe("72px")

    await userEvent.click(screen.getByRole("tab", { name: "Members" }))
    await waitFor(() => expect(list.style.getPropertyValue("--thumb-x")).toBe("80px"))
    expect(list.style.getPropertyValue("--thumb-w")).toBe("60px")
  })
})

describe("a hover card", () => {
  const card = () =>
    render(
      <HoverCard openDelay={20}>
        <HoverCardTrigger href="#s1">Source 1</HoverCardTrigger>
        <HoverCardContent>The passage, previewed</HoverCardContent>
      </HoverCard>,
    )

  it("previews on hover", async () => {
    card()
    await userEvent.hover(screen.getByText("Source 1"))
    expect(await screen.findByText("The passage, previewed")).toBeInTheDocument()
  })

  it("doesn't open for a trigger hidden behind a modal", async () => {
    render(
      <div aria-hidden="true">
        <HoverCard openDelay={20}>
          <HoverCardTrigger href="#s1">Source 1</HoverCardTrigger>
          <HoverCardContent>The passage, previewed</HoverCardContent>
        </HoverCard>
      </div>,
    )
    await userEvent.hover(screen.getByText("Source 1"))
    await new Promise((r) => setTimeout(r, 80))
    expect(screen.queryByText("The passage, previewed")).not.toBeInTheDocument()
  })

  it("stands down when its trigger is pressed, so it can't pop up over what the click opened", async () => {
    card()
    await userEvent.click(screen.getByText("Source 1"))
    await new Promise((r) => setTimeout(r, 80))
    expect(screen.queryByText("The passage, previewed")).not.toBeInTheDocument()
  })
})

describe("a loading button", () => {
  it("is busy and disabled, keeps its name, and shows the ASCII spinner silently", () => {
    render(<Button loading>Saving…</Button>)
    const button = screen.getByRole("button", { name: "Saving…" })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute("aria-busy", "true")
    expect(button.querySelector('[data-slot="ascii-spinner"]')).not.toBeNull()
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })
})

describe("a table cell", () => {
  it("sets IDs and timestamps in mono when asked, without passing the prop to the DOM", () => {
    render(
      <Table>
        <TableBody>
          <TableRow>
            <TableCell mono>kbscim_1a2b</TableCell>
            <TableCell>Ada</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    )
    const [id, name] = screen.getAllByRole("cell")
    expect(id).toHaveAttribute("data-mono", "true")
    expect(id).not.toHaveAttribute("mono")
    expect(name).not.toHaveAttribute("data-mono")
  })
})
