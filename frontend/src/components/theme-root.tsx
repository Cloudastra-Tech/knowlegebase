import type { ReactNode } from "react"
import { ThemeProvider } from "next-themes"

import { useForcedTheme } from "@/lib/forced-theme"

// The theme: dark unless the person picks Light or System. It's a class on <html>
// (public/theme-init.js sets it before the first paint), so dialogs and menus, which
// render into <body>, follow it too. next-themes' own pre-paint <script> can't run in a
// page React renders in the browser, so it's marked as data (no dev warning); theme-init.js
// does its job. The landing and sign-in pages force dark while they're shown
// (lib/forced-theme.ts).
export function ThemeRoot({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem
      disableTransitionOnChange
      forcedTheme={useForcedTheme()}
      scriptProps={{ type: "text/plain" }}
    >
      {children}
    </ThemeProvider>
  )
}
