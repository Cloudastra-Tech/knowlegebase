import { lazy, Suspense, useEffect, useState } from "react"
import { ChevronDown, LogOut } from "lucide-react"
import { toast } from "sonner"

import { IndexProgressBar, IndexStatusText } from "@/components/index-progress"
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
import { Skeleton } from "@/components/ui/skeleton"
import { IndexStatusProvider } from "@/lib/index-status"
import { cn, hashParams } from "@/lib/utils"
import {
  api,
  session,
  setForbiddenHandler,
  setUnauthorizedHandler,
  type Role,
  type User,
} from "@/lib/api"

type Screen = "chat" | "knowledge" | "docs" | "integrations"

// Each page is its own download, so a visitor on the landing page doesn't load the
// chat (markdown rendering) and a public user never loads the HR screens.
const ChatView = lazy(() => import("@/components/chat-view").then((m) => ({ default: m.ChatView })))
const DocsView = lazy(() => import("@/components/docs-view").then((m) => ({ default: m.DocsView })))
const IntegrationsView = lazy(() =>
  import("@/components/integrations-view").then((m) => ({ default: m.IntegrationsView })),
)
const KnowledgeView = lazy(() =>
  import("@/components/knowledge-view").then((m) => ({ default: m.KnowledgeView })),
)
const LandingPage = lazy(() =>
  import("@/components/landing-page").then((m) => ({ default: m.LandingPage })),
)
const LoginPage = lazy(() => import("@/components/login-page").then((m) => ({ default: m.LoginPage })))

function PageLoading() {
  return (
    <div className="flex h-full min-h-40 items-center justify-center">
      <Skeleton className="h-10 w-48" />
    </div>
  )
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [checking, setChecking] = useState(
    () => !!session.token || window.location.hash.startsWith("#sso"),
  )
  const [role, setRole] = useState<Role>(session.role)
  const [screen, setScreen] = useState<Screen>("chat")
  const [openDrivePicker, setOpenDrivePicker] = useState(false)
  // Signed out: the landing page, or the sign-in form at #login (or #login?error=...).
  const [wantsLogin, setWantsLogin] = useState(() => window.location.hash.startsWith("#login"))

  useEffect(() => {
    const onHash = () => setWantsLogin(window.location.hash.startsWith("#login"))
    window.addEventListener("hashchange", onHash)
    return () => window.removeEventListener("hashchange", onHash)
  }, [])

  function goTo(hash: string) {
    if (hash) window.location.hash = hash
    else history.replaceState(null, "", window.location.pathname)
    setWantsLogin(hash.startsWith("login"))
    window.scrollTo(0, 0)
  }

  function onLogin(u: User) {
    goTo("")
    setUser(u)
  }

  // Back from Google's consent screen: /#integrations?connected=... or ?error=...
  // Handled once we know who's signed in (see signedIn below).
  function showIntegrationResult() {
    if (!window.location.hash.startsWith("#integrations")) return
    const params = hashParams()
    history.replaceState(null, "", window.location.pathname)
    session.setRole("hr")
    setRole("hr")
    setScreen("integrations")
    const connected = params.get("connected")
    if (connected) {
      const name = connected === "notion" ? "Notion" : "Google Drive"
      const choose = params.get("choose") === "1"
      setOpenDrivePicker(choose)
      toast.success(
        choose
          ? `${name} connected. Now choose which folders or files to sync.`
          : `${name} connected. Syncing…`,
      )
    }
    const error = params.get("error")
    if (error) {
      const why: Record<string, string> = {
        access_denied: "Access was not granted.",
        expired: "The connection request expired. Please try again.",
        connect_failed: "Couldn't finish connecting. Please try again.",
      }
      toast.error(why[error] ?? "The integration was not connected.")
    }
  }

  function signedIn(u: User) {
    setUser(u)
    showIntegrationResult()
  }

  // An admin can change someone's role while they're signed in. Re-read the
  // account when the server refuses something, and when the tab comes back.
  useEffect(() => {
    const recheck = () => {
      if (session.token) api.me().then(setUser).catch(() => {})
    }
    setForbiddenHandler(recheck)
    const onVisible = () => document.visibilityState === "visible" && recheck()
    document.addEventListener("visibilitychange", onVisible)
    return () => document.removeEventListener("visibilitychange", onVisible)
  }, [])

  // Restore the session after a page refresh — or, back from Google / Microsoft
  // at /#sso?code=..., swap the one-time code for a session.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      session.setToken(null)
      setUser(null)
    })
    if (window.location.hash.startsWith("#sso")) {
      const code = hashParams().get("code")
      history.replaceState(null, "", window.location.pathname)
      api
        .ssoExchange(code ?? "")
        .then(({ token, user }) => {
          session.setToken(token)
          signedIn(user)
        })
        .catch(() => {
          window.location.hash = "login?error=expired"
        })
        .finally(() => setChecking(false))
      return
    }
    if (!session.token) return
    api
      .me()
      .then(signedIn)
      .catch(() => session.setToken(null))
      .finally(() => setChecking(false))
  }, [])

  function changeRole(next: Role) {
    session.setRole(next)
    setRole(next)
    if (next !== "hr") setScreen((s) => (s === "knowledge" ? s : "chat"))
  }

  async function signOut() {
    await api.logout().catch(() => {})
    session.setToken(null)
    setUser(null)
    setScreen("chat")
  }

  if (checking) {
    return (
      <div className="flex min-h-svh items-center justify-center">
        <Skeleton className="h-10 w-48" />
      </div>
    )
  }

  if (!user) {
    return (
      <Suspense fallback={<PageLoading />}>
        {wantsLogin ? (
          <LoginPage onLogin={onLogin} onBack={() => goTo("")} />
        ) : (
          <LandingPage onSignIn={() => goTo("login")} />
        )}
      </Suspense>
    )
  }

  // Only an HR account gets HR; it can also "view as" public. Everyone else is public.
  const canAdmin = user.role === "hr"
  const activeRole: Role = canAdmin ? role : "public"
  // If the role drops while on an HR-only screen, show Chat rather than nothing.
  const current: Screen =
    activeRole === "hr" || screen === "chat" || screen === "knowledge" ? screen : "chat"

  const initials = user.name
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase()

  const navItems: { id: Screen; label: string }[] = [
    { id: "chat", label: "Chat" },
    { id: "knowledge", label: "Knowledge" },
    ...(activeRole === "hr"
      ? [
          { id: "docs" as Screen, label: "Documents" },
          { id: "integrations" as Screen, label: "Integrations" },
        ]
      : []),
  ]

  return (
    <IndexStatusProvider>
    <div className="flex h-svh flex-col">
      <header className="sticky top-0 z-20 border-b border-border/60 bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex h-12 max-w-6xl items-center gap-8 px-4">
          <span className="select-none text-sm font-semibold tracking-tight">
            Knowledge<span className="font-normal text-muted-foreground"> assistant</span>
          </span>

          <nav className="flex h-full items-center gap-6">
            {navItems.map((item) => (
              <button
                key={item.id}
                onClick={() => setScreen(item.id)}
                className={cn(
                  "relative flex h-full items-center text-sm transition-colors",
                  current === item.id
                    ? "text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {item.label}
                {current === item.id && (
                  <span className="absolute inset-x-0 -bottom-px h-px bg-foreground" />
                )}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1">
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
                  <DropdownMenuRadioGroup value={role} onValueChange={(v) => changeRole(v as Role)}>
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
              <DropdownMenuContent align="end" className="w-48">
                <DropdownMenuLabel>
                  <div className="text-sm font-medium">{user.name}</div>
                  <div className="text-xs font-normal text-muted-foreground">
                    {user.username.includes("@") ? user.username : `@${user.username}`}
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={signOut}>
                  <LogOut /> Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
        <IndexProgressBar />
      </header>

      <main className="min-h-0 flex-1">
        {/* Keep the chat mounted so switching screens doesn't lose the conversation. */}
        <div className={current === "chat" ? "h-full" : "hidden"}>
          <Suspense fallback={<PageLoading />}>
            <ChatView role={activeRole} />
          </Suspense>
        </div>
        {current === "knowledge" && (
          <div className="h-full overflow-y-auto">
            <Suspense fallback={<PageLoading />}>
              <KnowledgeView key={activeRole} />
            </Suspense>
          </div>
        )}
        {current === "docs" && (
          <div className="h-full overflow-y-auto">
            <Suspense fallback={<PageLoading />}>
              <DocsView />
            </Suspense>
          </div>
        )}
        {current === "integrations" && (
          <div className="h-full overflow-y-auto">
            <Suspense fallback={<PageLoading />}>
              <IntegrationsView
                onOpenDocuments={() => setScreen("docs")}
                openDrivePicker={openDrivePicker}
              />
            </Suspense>
          </div>
        )}
      </main>
    </div>
    </IndexStatusProvider>
  )
}
