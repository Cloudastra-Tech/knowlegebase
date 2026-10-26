// What the rest of the app asks of the chat (the command palette, the shortcuts),
// as window events, so asking doesn't load the chat's code before it's opened.
export const FOCUS_COMPOSER = "kb:focus-composer"
export const TOGGLE_SIDEBAR = "kb:toggle-sidebar"

// "⌘" on Apple devices, "Ctrl" elsewhere: how shortcuts are shown.
export const MOD_KEY =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl"

// Ask the chat to do something, once a navigation to it has drawn it.
export function tellChat(event: string) {
  setTimeout(() => window.dispatchEvent(new Event(event)), 50)
}
