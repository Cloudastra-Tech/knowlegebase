// The pages that are always dark (DECISIONS.md: the landing and sign-in), whatever
// theme the person picked: they call useForcedDark(), and main.tsx hands the result
// of useForcedTheme() to next-themes as `forcedTheme`. The class is on <html>, so
// dialogs, menus and toasts opened there, which render into <body>, are dark too.
// next-themes ignores a ThemeProvider inside another, hence this and not a nested one.
// public/theme-init.js does the same for these paths before the first paint.

import { useLayoutEffect, useSyncExternalStore } from "react"

let holders = 0 // pages mounted that want dark (a page inside another counts twice)
const listeners = new Set<() => void>()

const emit = () => listeners.forEach((l) => l())
const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => listeners.delete(l)
}
const snapshot = () => (holders > 0 ? "dark" : undefined)

export function useForcedTheme() {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}

export function useForcedDark() {
  useLayoutEffect(() => {
    holders++
    // Before next-themes' own effect runs: no frame in the person's light theme.
    const root = document.documentElement
    root.classList.remove("light")
    root.classList.add("dark")
    root.style.colorScheme = "dark"
    emit()
    return () => {
      holders--
      emit() // next-themes puts the person's own theme back
    }
  }, [])
}
