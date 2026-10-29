// The composer, as the empty chat's art sees it (redacted-drift.tsx): focused, and
// when it was last typed in. Plain module state the chat writes from its event
// handlers and the art reads each frame, so nothing re-renders for it.
let focused = false
let typedAt = -Infinity

export const composerSignal = {
  focus(on: boolean) {
    focused = on
  },
  typed() {
    typedAt = performance.now()
  },
  // Focused, or typed in within the last 1.5s.
  active() {
    return focused || performance.now() - typedAt < 1500
  },
}
