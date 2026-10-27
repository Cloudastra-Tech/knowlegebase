import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClientProvider } from "@tanstack/react-query"
import { MotionConfig } from "motion/react"
import { ThemeProvider } from "next-themes"
import { createBrowserRouter } from "react-router"
import { RouterProvider } from "react-router/dom"

import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { queryClient } from "@/lib/query-client"
import { routes } from "@/routes"
import "./index.css"

const router = createBrowserRouter(routes)

// Installable as an app (platform.md PL-26): public/sw.js keeps the app shell only,
// never API responses. Not in development, where Vite serves changing files.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Not installable then (e.g. served over plain HTTP); the site works the same.
    })
  })
}

// The theme: dark unless the person picks Light or System. It's a class on <html>
// (public/theme-init.js sets it before the first paint), so dialogs and menus, which
// render into <body>, follow it too. next-themes' own pre-paint <script> can't run in a
// page React renders in the browser, so it's marked as data (no dev warning); theme-init.js
// does its job. Motion leaves out movement with reduced motion.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem
      disableTransitionOnChange
      scriptProps={{ type: "text/plain" }}
    >
      <MotionConfig reducedMotion="user">
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>
            <RouterProvider router={router} />
            <Toaster richColors position="top-right" />
          </TooltipProvider>
        </QueryClientProvider>
      </MotionConfig>
    </ThemeProvider>
  </StrictMode>,
)
