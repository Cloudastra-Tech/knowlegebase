import { lazy, useState } from "react"
import { ChevronDown, KeyRound, LogOut, MonitorSmartphone } from "lucide-react"
import { Link, Navigate, Outlet, useLocation } from "react-router"
import { toast } from "sonner"

import { IndexProgressBar, IndexStatusText } from "@/components/index-progress"
import { LazyPage } from "@/components/lazy-page"
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
import { cn } from "@/lib/utils"

// Each page is its own download (routes.tsx), so a visitor on the landing page doesn't
// load the chat (markdown rendering) and a public user never loads the HR screens.
const ChatView = lazy(() => import("@/components/chat-view").then((m) => ({ default: m.ChatView })))

// The signed-in app: the navbar, and the screen at the current URL below it (routes.tsx).
// Signed out, it sends people to the sign-in page, and back here once they're in.
export default function App() {
  const { user, role, setRole, signedOut, leftOnPurpose } = useAuth()
  const location = useLocation()
  const [confirmSignOutAll, setConfirmSignOutAll] = useState(false)

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

  // `section`: every URL under it is this screen (Access has a tab per URL).
  const navItems: { to: string; section: string; label: string }[] = [
    { to: "/chat", section: "/chat", label: "Chat" },
    { to: "/knowledge", section: "/knowledge", label: "Knowledge" },
    ...(activeRole === "hr"
      ? [
          { to: "/documents", section: "/documents", label: "Documents" },
          { to: "/integrations", section: "/integrations", label: "Integrations" },
          { to: "/admin/insights", section: "/admin/insights", label: "Insights" },
        ]
      : []),
    // Who may sign in: admins only (HR manages documents, not access).
    ...(activeRole === "hr" && user.role === "admin"
      ? [{ to: "/admin/sign-in", section: "/admin", label: "Access" }]
      : []),
  ]
  const under = (section: string) =>
    location.pathname === section || location.pathname.startsWith(`${section}/`)
  // The most specific section wins: /admin/insights is Insights, not Access (/admin).
  const current = navItems
    .map((item) => item.section)
    .filter(under)
    .sort((a, b) => b.length - a.length)[0]
  const isOpen = (section: string) => section === current

  return (
    <IndexStatusProvider>
    <div className="flex h-svh flex-col">
      <header className="sticky top-0 z-20 border-b border-border/60 bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex h-12 max-w-6xl items-center gap-4 px-4 sm:gap-8">
          <span className="shrink-0 select-none text-sm font-semibold tracking-tight">
            Knowledge
            <span className="hidden font-normal text-muted-foreground sm:inline"> assistant</span>
          </span>

          {/* On a phone the tabs scroll sideways instead of widening the page. */}
          <nav className="flex h-full min-w-0 items-center gap-5 overflow-x-auto [scrollbar-width:none] sm:gap-6">
            {navItems.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                aria-current={isOpen(item.section) ? "page" : undefined}
                className={cn(
                  "relative flex h-full shrink-0 items-center whitespace-nowrap text-sm transition-colors",
                  isOpen(item.section)
                    ? "text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {item.label}
                {isOpen(item.section) && (
                  <span className="absolute inset-x-0 bottom-0 h-px bg-foreground" />
                )}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex shrink-0 items-center gap-1">
            <div className="mr-2">
              <IndexStatusText />
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
                  aria-label="Account"
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
                  <Link to="/api-keys">
                    <KeyRound /> API keys
                  </Link>
                </DropdownMenuItem>
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

      <main className="min-h-0 flex-1">
        {/* Keep the chat mounted so switching screens doesn't lose the conversation. */}
        <div className={under("/chat") ? "h-full" : "hidden"}>
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
