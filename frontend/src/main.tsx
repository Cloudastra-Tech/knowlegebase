import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import App from "./App.tsx"
import { SsoTestDone } from "@/components/sso-test-done"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import "./index.css"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider>
      {/* The popup of an admin's "Test sign-in" lands here: no app, just "done". */}
      {window.location.hash.startsWith("#sso-test") ? <SsoTestDone /> : <App />}
      <Toaster richColors position="top-right" />
    </TooltipProvider>
  </StrictMode>,
)
