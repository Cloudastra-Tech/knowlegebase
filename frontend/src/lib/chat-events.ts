import { THINKING_EVENT, type ThinkingDetail } from "@/components/ascii/events"

// What the rest of the app asks of the chat (the command palette, the shortcuts),
// as window events, so asking doesn't load the chat's code before it's opened.
export const FOCUS_COMPOSER = "kb:focus-composer"
export const TOGGLE_SIDEBAR = "kb:toggle-sidebar"
// The sidebar's Search chats: the command palette, with chats alone. With a folder
// (the new chat's home), the chats in it until something is typed.
export const OPEN_SEARCH = "kb:open-search"
export type SearchDetail = { folderId?: string }
// Put a saved prompt in the composer (detail: its id), asking for its variables.
export const INSERT_PROMPT = "kb:insert-prompt"
// New chats ask in this space (detail: its id, or null for all company).
export const CHOOSE_SPACE = "kb:choose-space"

// "⌘" on Apple devices, "Ctrl" elsewhere: how shortcuts are shown.
export const MOD_KEY =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl"

// Ask the chat to do something, once a navigation to it has drawn it.
export function tellChat(event: string, detail?: unknown) {
  setTimeout(() => window.dispatchEvent(new CustomEvent(event, { detail })), 50)
}

// The assistant started (or stopped) working on an answer: the chat's background
// field breathes and ripples meanwhile (components/chat-backdrop.tsx). Sent again on
// each new step, for another ripple.
export function setThinking(active: boolean) {
  window.dispatchEvent(new CustomEvent<ThinkingDetail>(THINKING_EVENT, { detail: { active } }))
}
