import { StrictMode, type ReactNode } from "react"
import { QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { createMemoryRouter } from "react-router"
import { RouterProvider } from "react-router/dom"
import { vi } from "vitest"

import { TooltipProvider } from "@/components/ui/tooltip"
import { createQueryClient } from "@/lib/query-client"
import { routes } from "@/routes"

// A fake backend: "METHOD /api/path" -> the JSON it answers (or a function of the
// request's body making it, or a Response). Anything else is a 404.
type Answer = unknown | ((body: unknown) => unknown)

export function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

export function mockApi(answers: Record<string, Answer>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init = {}) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url
    const key = `${(init.method ?? "GET").toUpperCase()} ${new URL(url, "http://test").pathname}`
    if (!(key in answers)) return json({ detail: "Not Found" }, 404)
    const answer = answers[key]
    const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined
    const out = typeof answer === "function" ? answer(body) : answer
    return out instanceof Response ? out : json(out)
  })
}

export function withProviders(children: ReactNode) {
  return (
    <QueryClientProvider client={createQueryClient()}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  )
}

// The whole app (routes.tsx) at `url`, in memory instead of the address bar. In
// StrictMode, as main.tsx renders it: effects run twice, as they do in development.
export function renderApp(url: string) {
  const router = createMemoryRouter(routes, { initialEntries: [url] })
  render(<StrictMode>{withProviders(<RouterProvider router={router} />)}</StrictMode>)
  return router
}
