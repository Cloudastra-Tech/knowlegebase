import { lazy, useCallback, useEffect, useState, type CSSProperties } from "react"
import { Link, Navigate, Outlet, useLocation } from "react-router"
import { Headset, TriangleAlert } from "lucide-react"

import { AppBackdrop } from "@/components/ascii/app-backdrop"
import { AppSidebar, PhoneBar, SidebarEvents } from "@/components/app-sidebar"
import { CommandPalette } from "@/components/command-palette"
import { IndexProgressBar } from "@/components/index-progress"
import { LazyPage } from "@/components/lazy-page"
import { ProductTour } from "@/components/product-tour"
import { ShellStrip, TrialBanner } from "@/components/trial-banner"
import { SidebarProvider } from "@/components/ui/sidebar"
import type { Role } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { ChatBridgeProvider } from "@/components/chat-bridge-provider"
import { IndexStatusProvider } from "@/lib/index-status"
import { endInstantNav } from "@/lib/instant-nav"
import { cn } from "@/lib/utils"

// Each page is its own download (routes.tsx), so a visitor on the landing page doesn't
// load the chat (markdown rendering) and a public user never loads the HR screens.
const ChatView = lazy(() => import("@/components/chat-view").then((m) => ({ default: m.ChatView })))

// The chat while another screen is open: out of sight and out of reach (inert), but
// still laid out, so its transcript keeps its place for the way back. Its ASCII field
// is taken out of layout, so it rests (offscreen to its loop) and the screen's own
// AppBackdrop is the only one drawing.
const CHAT_AWAY =
  "pointer-events-none invisible absolute inset-0 overflow-hidden [&_[data-slot=chat-backdrop]]:hidden"

// The sidebar collapsed to its rail, remembered on this device.
const SIDEBAR_KEY = "kb.sidebar"
function storedOpen() {
  try {
    return localStorage.getItem(SIDEBAR_KEY) !== "collapsed"
  } catch {
    return true
  }
}

// About 260px open, a 56px rail closed (components/ui/sidebar.tsx reads these).
// --field-pane: the width the sidebar takes now, for the background field's mask
// (components/ascii/ascii.css), which runs under it and centres on the content.
const SIDEBAR_OPEN = {
  "--sidebar-width": "16.25rem",
  "--sidebar-width-icon": "3.5rem",
  "--field-pane": "var(--sidebar-width)",
} as CSSProperties
const SIDEBAR_RAIL = { ...SIDEBAR_OPEN, "--field-pane": "var(--sidebar-width-icon)" } as CSSProperties

// The signed-in app: the sidebar on the left (app-sidebar.tsx: the screens, the chats,
// the account), and the screen at the current URL beside it (routes.tsx). There is no
// top bar but a phone's. Signed out, it sends people to the sign-in page, and back here
// once they're in.
export default function App() {
  const { user, role, leftOnPurpose } = useAuth()
  const location = useLocation()
  const [sidebarOpen, setSidebarOpenState] = useState(storedOpen)
  const setSidebarOpen = useCallback((open: boolean) => {
    setSidebarOpenState(open)
    try {
      if (open) localStorage.removeItem(SIDEBAR_KEY)
      else localStorage.setItem(SIDEBAR_KEY, "collapsed")
    } catch {
      /* private mode: just not remembered */
    }
  }, [])
  // The conversation the chat was last at: Chat (the sidebar, the palette) goes back to
  // it from another screen, scrolled where it was, rather than to a new chat.
  const [chatAt, setChatAt] = useState("/chat")
  const onChat = location.pathname === "/chat" || location.pathname.startsWith("/chat/")
  if (onChat && location.pathname !== chatAt) setChatAt(location.pathname)
  // A keyboard navigation (lib/instant-nav.ts) is over once its screen has painted.
  useEffect(() => {
    const raf = requestAnimationFrame(() => requestAnimationFrame(endInstantNav))
    return () => cancelAnimationFrame(raf)
  }, [location.key])

  if (!user) {
    return leftOnPurpose ? (
      <Navigate to="/" replace />
    ) : (
      <Navigate to="/login" replace state={{ from: location }} />
    )
  }

  // Only an HR or admin account gets HR; it can also "view as" public. Everyone else is public.
  const activeRole: Role = user.role !== "public" ? role : "public"

  return (
    <IndexStatusProvider>
    <ChatBridgeProvider>
    <SidebarProvider
      open={sidebarOpen}
      onOpenChange={setSidebarOpen}
      style={sidebarOpen ? SIDEBAR_OPEN : SIDEBAR_RAIL}
      className="h-dvh min-h-0"
      data-print="flow"
    >
      <a
        href="#main"
        data-print="hide"
        className="sr-only rounded-md bg-background px-3 py-2 text-sm font-medium ring-2 ring-ring focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50"
      >
        Skip to content
      </a>
      {/* One ASCII field on every screen, fixed behind everything and under the sidebar,
          whose glass frosts it (centred on the content, not the window); it rests while
          anything scrolls and under a dialog (lib/ascii-loop.ts). Not on the chat, which
          draws its own, as wide (ChatBackdrop: faint under the text, breathing, a ripple
          per step and a glow from the composer while the assistant works). */}
      {!onChat && <AppBackdrop />}
      {/* ⌘K: search chats and go anywhere; ⌘⇧O new chat; ⌘/ the shortcuts; ⌘B the sidebar. */}
      <CommandPalette user={user} role={activeRole} chatAt={chatAt} />
      <SidebarEvents />
      {/* The product tour: by itself on a first sign-in, or from the account menu. */}
      <ProductTour chatAt={chatAt} />
      <AppSidebar chatAt={chatAt} />

      <div className="relative flex min-w-0 flex-1 flex-col" data-print="flow">
        <IndexProgressBar />
        {!onChat && <PhoneBar chatAt={chatAt} />}
        {user.impersonator && (
          <ShellStrip icon={Headset}>
            You're in this workspace as Cloudastra support ({user.impersonator}). Every request is
            in its audit log.
          </ShellStrip>
        )}
        <TrialBanner user={user} />
        {user.workspace_status === "deleting" && user.deleting_at && (
          <ShellStrip icon={TriangleAlert} tone="destructive">
            This workspace will be deleted on{" "}
            {new Date(user.deleting_at * 1000).toLocaleString(undefined, {
              dateStyle: "medium",
              timeStyle: "short",
            })}
            .{" "}
            <Link to="/admin/data" className="font-medium underline underline-offset-2">
              Export it or cancel
            </Link>
          </ShellStrip>
        )}

        <main id="main" tabIndex={-1} className="relative min-h-0 flex-1 outline-none" data-print="flow">
          {/* Keep the chat mounted so switching screens doesn't lose the conversation, nor
              where it was scrolled to (CHAT_AWAY). */}
          <div
            className={cn("h-full", !onChat && CHAT_AWAY)}
            inert={!onChat}
            data-print={onChat ? "flow" : "hide"}
          >
            <LazyPage>
              <ChatView role={activeRole} />
            </LazyPage>
          </div>
          {/* Every other screen (routes.tsx). */}
          <Outlet />
        </main>
      </div>
    </SidebarProvider>
    </ChatBridgeProvider>
    </IndexStatusProvider>
  )
}
