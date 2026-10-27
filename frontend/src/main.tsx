import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClientProvider } from "@tanstack/react-query"
import { MotionConfig } from "motion/react"
import { createBrowserRouter } from "react-router"
import { RouterProvider } from "react-router/dom"

import { ThemeRoot } from "@/components/theme-root"
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

// The theme (components/theme-root.tsx); Motion leaves out movement with reduced motion.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeRoot>
      <MotionConfig reducedMotion="user">
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>
            <RouterProvider router={router} />
            <Toaster richColors position="top-right" />
          </TooltipProvider>
        </QueryClientProvider>
      </MotionConfig>
    </ThemeRoot>
  </StrictMode>,
)
