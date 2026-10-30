// The product tour (components/product-tour.tsx): what each tour's steps say, where
// each one points, and the rules for moving between them. No DOM here but the
// lookups the caller passes in, so the rules can be tested on their own.
//
// Two tours: "members" (everyone) and "admin" (admins viewing as HR). A step points
// at an element marked data-tour="…" on a page; a step whose element isn't on
// screen (the model picker with one model, an answer's sources before any answer)
// is either skipped or shown in the middle without a light, as the step says.
// Whether a member has seen each tour is kept on the server (kb/services/onboarding.py,
// PUT /api/onboarding/tours/{tour}), so it doesn't start again in another browser.

import { MOD_KEY } from "@/lib/chat-events"
import { isChatPath } from "@/lib/routes"

export type TourId = "members" | "admin"
export type TourOutcome = "finished" | "skipped"

// The account menu's Take the tour and Admin tour (detail: the tour), as a window
// event, as the menu's other requests are (lib/chat-events.ts).
export const START_TOUR = "kb:start-tour"
export function startTour(tour: TourId) {
  window.dispatchEvent(new CustomEvent<TourId>(START_TOUR, { detail: tour }))
}

// What a step's words and choices may depend on.
export interface TourContext {
  admin: boolean // an admin account, viewing the app as HR
  phone: boolean // the sidebar is a sheet behind the menu button
}

export interface TourStep {
  id: string
  title: string
  body: string | ((ctx: TourContext) => string)
  // The page the step needs: "chat" (wherever the chat is: a new one or a saved one),
  // or a path. None: wherever the tour is.
  page?: "chat" | string
  // Where the light goes: selectors tried in order, the first element on screen wins
  // (the last one on screen, with pick "last": the newest answer's sources).
  target?: string[]
  pick?: "first" | "last"
  // No element on screen: skip the step, or show it in the middle without a light.
  missing: "skip" | "center"
  // Shown only when this says so.
  when?: (ctx: TourContext) => boolean
  // Which side of the element the card prefers (it flips to stay on screen).
  side?: "top" | "right" | "bottom" | "left"
}

const PHONE_MENU = '[data-tour="phone-menu"]'

export const TOURS: Record<TourId, TourStep[]> = {
  members: [
    {
      id: "composer",
      title: "Ask in plain words",
      body: "Type a question the way you'd ask a colleague. Answers come from your company's documents, and only from the ones you're allowed to read.",
      page: "chat",
      target: ['[data-tour="composer"]'],
      missing: "center",
      side: "top",
    },
    {
      id: "model",
      title: "Pick the model",
      body: "Your workspace offers more than one model. The one you pick here writes the answers in this chat.",
      page: "chat",
      target: ['[data-tour="model-picker"]'],
      missing: "skip",
      side: "top",
    },
    {
      id: "sources",
      title: "Check where it came from",
      body: "Every answer lists the documents it used. Hover a numbered source to preview the passage, or click it to open the document with the exact lines highlighted.",
      page: "chat",
      target: ['[data-tour="sources"]'],
      pick: "last",
      missing: "center",
    },
    {
      id: "prompts",
      title: "Save the questions you repeat",
      body: "Keep a prompt with blanks to fill in, then type / in the chat box to use it. Turn off Search knowledge for one that doesn't need your documents, like rewriting text.",
      page: "/settings/prompts",
      target: ['[data-tour="prompts"]'],
      missing: "center",
    },
    {
      id: "chats",
      title: "Find a past chat",
      body: `Every chat is kept here. Search them with ${MOD_KEY} K, or rename, pin, share or delete one from its menu.`,
      target: ['[data-tour="chat-history"]', '[data-tour="search-chats"]'],
      missing: "skip",
      side: "right",
      when: (ctx) => !ctx.phone, // on a phone, the next step says where they are
    },
    {
      id: "account",
      title: "Settings, and this tour again",
      body: (ctx) =>
        ctx.phone
          ? "Your chats, your settings and the theme are in the menu. So is Take the tour, to see this again."
          : "Your settings, the theme and Reduce effects are in this menu. So is Take the tour, to see this again.",
      target: ['[data-tour="account"]', PHONE_MENU],
      missing: "center",
      side: "right",
    },
  ],
  admin: [
    {
      id: "integrations",
      title: "Connect your sources",
      body: "Connect Google Drive, Notion and the other tools your team writes in, or upload files. They're indexed and kept in sync, and answers only come from what's connected here.",
      page: "/integrations",
      target: ['[data-tour="integrations"] > :first-child', '[data-tour="integrations"]'],
      missing: "center",
    },
    {
      id: "models",
      title: "Choose the models",
      body: "Add your AI providers and switch on the models people can pick in the chat. The model that searches your documents is set here too.",
      page: "/admin/assistant",
      target: ['[data-tour="models"] [data-slot="panel-header"]', '[data-tour="models"]'],
      missing: "center",
    },
    {
      id: "access",
      title: "Decide who sees what",
      body: "Members and their roles, groups that decide which documents each person can read, and how people sign in. Access starts here.",
      page: "/admin/members",
      target: ['[data-tour="access-tabs"]'],
      missing: "center",
    },
    {
      id: "insights",
      title: "See what people couldn't find",
      body: "Not found lists the questions your documents couldn't answer, and Feedback the ratings people gave answers. Close a gap with a verified answer or a new document.",
      page: "/admin/insights",
      target: ['[data-tour="insights-tabs"]'],
      missing: "center",
    },
  ],
}

// The steps of a tour for this person: none of the admin tour for anyone else, and
// none whose `when` says no.
export function stepsFor(tour: TourId, ctx: TourContext): TourStep[] {
  if (tour === "admin" && !ctx.admin) return []
  return TOURS[tour].filter((s) => !s.when || s.when(ctx))
}

export const bodyOf = (step: TourStep, ctx: TourContext) =>
  typeof step.body === "function" ? step.body(ctx) : step.body

// The step to show going from `from` in direction `dir` (+1 Next, -1 Back): the next
// one whose element is there or that shows without one. `present` says whether a
// step's element is on screen. -1: none left that way (Next: the tour is over; Back:
// stay where it is).
export function nextIndex(
  steps: TourStep[],
  from: number,
  dir: 1 | -1,
  present: (step: TourStep) => boolean,
): number {
  for (let i = from + dir; i >= 0 && i < steps.length; i += dir) {
    const step = steps[i]
    if (step.missing === "center" || !step.target || present(step)) return i
  }
  return -1
}

// Does the step need another page than `path`? Then where to go ("chat": the chat
// the person was last at, `chatAt`).
export function pageFor(step: TourStep, path: string, chatAt: string): string | null {
  if (!step.page) return null
  if (step.page === "chat") {
    return isChatPath(path) ? null : chatAt
  }
  return path === step.page ? null : step.page
}

// Auto-start: once, for a member who hasn't seen the members' tour and hasn't asked
// anything yet (a first sign-in), on the chat, and never for Cloudastra support
// looking in on their behalf.
export function shouldAutoStart(opts: {
  seen: boolean | undefined
  firstRun: boolean | undefined
  path: string
  impersonating: boolean
}): boolean {
  return (
    opts.seen === false &&
    opts.firstRun === true &&
    !opts.impersonating &&
    isChatPath(opts.path)
  )
}
