// The assistant started or stopped thinking: a window CustomEvent with detail
// { active: boolean }. The chat sends it (lib/chat-events.ts); the app's background
// field sends one slow ripple from the composer when it starts (AppBackdrop).
export const THINKING_EVENT = "kb:thinking"

export type ThinkingDetail = { active: boolean }
