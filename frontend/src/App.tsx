import { lazy, useEffect, useState } from "react"
import {
  ChevronDown,
  CreditCard,
  KeyRound,
  Layers,
  LogOut,
  Mail,
  MessageSquareText,
  MonitorSmartphone,
  Plug,
} from "lucide-react"
import { Link, Navigate, Outlet, useLocation } from "react-router"
import { toast } from "sonner"

import { AppBackdrop } from "@/components/ascii/app-backdrop"
import { AppNav, type NavItem } from "@/components/app-nav"
import { CommandPalette } from "@/components/command-palette"
import { IndexProgressBar, IndexStatusText } from "@/components/index-progress"
import { LazyPage } from "@/components/lazy-page"
import { ThemeMenu } from "@/components/theme-menu"
import { ShellStrip, TrialBanner } from "@/components/trial-banner"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { api, type Role } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
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

// The signed-in app: the navbar, and the screen at the current URL below it (routes.tsx).
// Signed out, it sends people to the sign-in page, and back here once they're in.
export default function App() {
  const { user, role, setRole, signedOut, leftOnPurpose } = useAuth()
  const location = useLocation()
  const [confirmSignOutAll, setConfirmSignOutAll] = useState(false)
  // The conversation the chat was last at: Chat (the tab, the palette) goes back to it
  // from another screen, scrolled where it was, rather than to a new chat.
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

  async function signOut() {
    const out = await api.logout().catch(() => null)
    signedOut(true)
    // The admin set this provider to sign people out there too.
    if (out?.logout_url) window.location.assign(out.logout_url)
  }

  // Every session of this account ends (a lost laptop, a shared computer), this one too.
  async function signOutEverywhere() {
    try {
      await api.logoutEverywhere()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't sign out everywhere.")
      return
    }
    signedOut(true)
    toast.success("Signed out on every device.")
  }

  // Only an HR or admin account gets HR; it can also "view as" public. Everyone else is public.
  const canAdmin = user.role !== "public"
  const activeRole: Role = canAdmin ? role : "public"

  const initials = user.name
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase()

  const navItems: NavItem[] = [
    { to: chatAt, section: "/chat", label: "Chat" },
    { to: "/knowledge", section: "/knowledge", label: "Knowledge" },
    ...(activeRole === "hr"
      ? [
          { to: "/documents", section: "/documents", label: "Documents" },
          { to: "/integrations", section: "/integrations", label: "Integrations" },
          { to: "/admin/insights", section: "/admin/insights", label: "Insights" },
          { to: "/admin/verified", section: "/admin/verified", label: "Verified" },
        ]
      : []),
    // How the assistant answers, and who may sign in: admins only (HR manages
    // documents, not access).
    ...(activeRole === "hr" && user.role === "admin"
      ? [
          { to: "/admin/assistant", section: "/admin/assistant", label: "Assistant" },
          { to: "/admin/sign-in", section: "/admin", label: "Access" },
        ]
      : []),
  ]
  const under = (section: string) =>
    location.pathname === section || location.pathname.startsWith(`${section}/`)
  // The most specific section wins: /admin/insights is Insights, not Access (/admin).
  const current = navItems
    .map((item) => item.section)
    .filter(under)
    .sort((a, b) => b.length - a.length)[0]

  return (
    <IndexStatusProvider>
    <div className="flex h-dvh flex-col" data-print="flow">
      {/* One ASCII field on every screen, fixed behind everything; it rests while
          anything scrolls and under a dialog (lib/ascii-loop.ts). Not on the chat, which
          draws its own behind its column (ChatBackdrop: faint under the text, breathing,
          a ripple per step and a glow from the composer while the assistant works). */}
      {!onChat && <AppBackdrop />}
      {/* ⌘K: search chats and go anywhere; ⌘⇧O new chat; ⌘/ the shortcuts. */}
      <CommandPalette user={user} role={activeRole} chatAt={chatAt} />
      {/* Chrome-tier glass, the field blurred behind it. Nothing around it animates its
          opacity (the backdrop-root trap, DESIGN.md "Glass"). */}
      <header
        data-print="hide"
        className="glass z-20 rounded-none border-x-0 border-t-0 pt-[env(safe-area-inset-top)]"
      >
        <div className="mx-auto flex h-12 max-w-6xl items-center gap-4 px-4 sm:gap-8">
          {/* Not on a phone: next to the screen menu ("Knowledge ⌄") or a member's tabs
              it read "Knowledge Chat Knowledge". */}
          <span className="hidden shrink-0 select-none text-sm font-semibold tracking-tight sm:inline">
            Knowledge
            <span className="font-normal text-muted-foreground"> assistant</span>
          </span>

          <AppNav items={navItems} current={current} />

          <div className="ml-auto flex shrink-0 items-center gap-1">
            <div className="mr-2">
              <IndexStatusText admin={user.role === "admin" && activeRole === "hr"} />
            </div>
            {canAdmin && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 gap-1 rounded-full px-3 text-xs font-normal text-muted-foreground hover:text-foreground"
                  >
                    {activeRole === "hr" ? "HR / Admin" : "Public"}
                    <ChevronDown className="size-3.5 opacity-60" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-44">
                  <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                    View as
                  </DropdownMenuLabel>
                  <DropdownMenuRadioGroup value={role} onValueChange={(v) => setRole(v as Role)}>
                    <DropdownMenuRadioItem value="hr">HR / Admin</DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="public">Public user</DropdownMenuRadioItem>
                  </DropdownMenuRadioGroup>
                </DropdownMenuContent>
              </DropdownMenu>
            )}

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className="rounded-full outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`Account, ${initials || "?"}`}
                >
                  <Avatar className="size-7">
                    <AvatarFallback className="bg-foreground text-[11px] font-medium text-background">
                      {initials || "?"}
                    </AvatarFallback>
                  </Avatar>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-52">
                <DropdownMenuLabel>
                  <div className="text-sm font-medium">{user.name}</div>
                  <div className="text-xs font-normal text-muted-foreground">
                    {user.username.includes("@") ? user.username : `@${user.username}`}
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link to="/spaces">
                    <Layers /> Spaces
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link to="/prompts">
                    <MessageSquareText /> My prompts
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link to="/api-keys">
                    <KeyRound /> API keys
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link to="/digests">
                    <Mail /> Digests
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link to="/connections">
                    <Plug /> My connections
                  </Link>
                </DropdownMenuItem>
                {user.billing?.can_view && (
                  <DropdownMenuItem asChild>
                    <Link to="/billing">
                      <CreditCard /> Plan &amp; billing
                    </Link>
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <ThemeMenu />
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={signOut}>
                  <LogOut /> Sign out
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setConfirmSignOutAll(true)}>
                  <MonitorSmartphone /> Sign out everywhere
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <AlertDialog open={confirmSignOutAll} onOpenChange={setConfirmSignOutAll}>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Sign out everywhere?</AlertDialogTitle>
                  <AlertDialogDescription>
                    You're signed out on every device and browser where you're signed in,
                    this one included. Use it if you lost a device or signed in on a computer
                    that isn't yours.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={signOutEverywhere}>Sign out everywhere</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
        <IndexProgressBar />
      </header>
      {user.impersonator && (
        <ShellStrip glyph="@">
          You're in this workspace as Cloudastra support ({user.impersonator}). Every request is
          in its audit log.
        </ShellStrip>
      )}
      <TrialBanner user={user} />
      {user.workspace_status === "deleting" && user.deleting_at && (
        <ShellStrip glyph="!" tone="destructive">
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

      <main className="relative min-h-0 flex-1" data-print="flow">
        {/* Keep the chat mounted so switching screens doesn't lose the conversation, nor
            where it was scrolled to (CHAT_AWAY). */}
        <div
          className={cn("h-full", !under("/chat") && CHAT_AWAY)}
          inert={!under("/chat")}
          data-print={under("/chat") ? "flow" : "hide"}
        >
          <LazyPage>
            <ChatView role={activeRole} />
          </LazyPage>
        </div>
        {/* Every other screen (routes.tsx). */}
        <Outlet />
      </main>
    </div>
    </IndexStatusProvider>
  )
}
