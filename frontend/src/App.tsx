import { lazy, useEffect, useState } from "react"
import { ChevronDown, LogOut } from "lucide-react"
import { toast } from "sonner"

import { IndexProgressBar, IndexStatusText } from "@/components/index-progress"
import { LazyPage } from "@/components/lazy-page"
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
  ApiError,
  session,
  setForbiddenHandler,
  setUnauthorizedHandler,
  type Role,
  type User,
} from "@/lib/api"

type Screen = "chat" | "knowledge" | "docs" | "integrations" | "sign-in"

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
const SignInView = lazy(() =>
  import("@/components/sign-in-view").then((m) => ({ default: m.SignInView })),
)
const LoginPage = lazy(() => import("@/components/login-page").then((m) => ({ default: m.LoginPage })))

// How often to retry restoring a session while the server can't be reached (~1 min).
const RESTORE_ATTEMPTS = 10

// The screens a public user may see; anything else falls back to Chat.
const publicScreen = (s: Screen): Screen => (s === "knowledge" ? s : "chat")

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [checking, setChecking] = useState(
    () => !!session.token || window.location.hash.startsWith("#sso"),
  )
  const [role, setRole] = useState<Role>(session.role)
  const [screen, setScreen] = useState<Screen>("chat")
  const [openDrivePicker, setOpenDrivePicker] = useState(false)
  // Signed out: the landing page, or the sign-in page at #login (or #login?error=...),
  // opened on its Create a workspace tab at #signup.
  const authHash = () => /^#(login|signup)/.test(window.location.hash)
  const [wantsLogin, setWantsLogin] = useState(authHash)

  useEffect(() => {
    const onHash = () => setWantsLogin(authHash())
    window.addEventListener("hashchange", onHash)
    return () => window.removeEventListener("hashchange", onHash)
  }, [])

  function goTo(hash: string) {
    if (hash) window.location.hash = hash
    else history.replaceState(null, "", window.location.pathname)
    setWantsLogin(/^(login|signup)/.test(hash))
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
    let inFlight = false
    const recheck = () => {
      const token = session.token
      if (!token || inFlight) return               // one check at a time
      inFlight = true
      api
        .me()
        .then((u) => {
          if (session.token !== token) return       // signed out meanwhile
          setUser(u)
          if (u.role === "public") setScreen(publicScreen)
          else if (u.role !== "admin") setScreen((s) => (s === "sign-in" ? "chat" : s))
        })
        .catch((err) => {
          // Access removed altogether. (/auth/* 401s skip the unauthorized handler.)
          if (err instanceof ApiError && err.status === 401 && session.token === token) {
            session.setToken(null)
            setUser(null)
            setScreen("chat")
          }
        })
        .finally(() => {
          inFlight = false
        })
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
      const welcome = hashParams().get("welcome") === "1"
      history.replaceState(null, "", window.location.pathname)
      api
        .ssoExchange(code ?? "")
        .then(({ token, user }) => {
          session.setToken(token)
          signedIn(user)
          if (welcome && user.role === "admin") {
            session.setRole("hr")
            setRole("hr")
            setScreen("sign-in")
          }
        })
        .catch(() => {
          window.location.hash = "login?error=expired"
        })
        .finally(() => setChecking(false))
      return
    }
    if (!session.token) return
    // Only the server saying "no" (401) ends the session. If it can't be reached
    // (restarting, a deploy, a network blip), keep it and try again for a while.
    let timer: number | undefined
    const restore = (attempt: number) => {
      api
        .me()
        .then((u) => {
          signedIn(u)
          setChecking(false)
        })
        .catch((err) => {
          if (err instanceof ApiError && err.status === 401) {
            session.setToken(null)
            setChecking(false)
          } else if (attempt < RESTORE_ATTEMPTS) {
            timer = window.setTimeout(() => restore(attempt + 1), Math.min(500 * 2 ** attempt, 8000))
          } else {
            setChecking(false)      // still signed in; the next reload tries again
          }
        })
    }
    restore(0)
    return () => window.clearTimeout(timer)
  }, [])

  function changeRole(next: Role) {
    session.setRole(next)
    setRole(next)
    if (next !== "hr") setScreen(publicScreen)
  }

  function openScreen(next: Screen) {
    setScreen(next)
    setOpenDrivePicker(false)    // the picker opens by itself only on arrival from Google
  }

  async function signOut() {
    const out = await api.logout().catch(() => null)
    session.setToken(null)
    setUser(null)
    setScreen("chat")
    // The admin set this provider to sign people out there too.
    if (out?.logout_url) window.location.assign(out.logout_url)
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
      <LazyPage fullScreen>
        {wantsLogin ? (
          <LoginPage onLogin={onLogin} onBack={() => goTo("")} />
        ) : (
          <LandingPage onSignIn={() => goTo("login")} onGetStarted={() => goTo("signup")} />
        )}
      </LazyPage>
    )
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

  const navItems: { id: Screen; label: string }[] = [
    { id: "chat", label: "Chat" },
    { id: "knowledge", label: "Knowledge" },
    ...(activeRole === "hr"
      ? [
          { id: "docs" as Screen, label: "Documents" },
          { id: "integrations" as Screen, label: "Integrations" },
        ]
      : []),
    // Who may sign in: admins only (HR manages documents, not access).
    ...(activeRole === "hr" && user.role === "admin"
      ? [{ id: "sign-in" as Screen, label: "Access" }]
      : []),
  ]

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
              <button
                key={item.id}
                onClick={() => openScreen(item.id)}
                className={cn(
                  "relative flex h-full shrink-0 items-center whitespace-nowrap text-sm transition-colors",
                  screen === item.id
                    ? "text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {item.label}
                {screen === item.id && (
                  <span className="absolute inset-x-0 bottom-0 h-px bg-foreground" />
                )}
              </button>
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
        <div className={screen === "chat" ? "h-full" : "hidden"}>
          <LazyPage>
            <ChatView role={activeRole} />
          </LazyPage>
        </div>
        {screen === "knowledge" && (
          <div className="h-full overflow-y-auto">
            <LazyPage>
              <KnowledgeView key={activeRole} />
            </LazyPage>
          </div>
        )}
        {screen === "docs" && activeRole === "hr" && (
          <div className="h-full overflow-y-auto">
            <LazyPage>
              <DocsView />
            </LazyPage>
          </div>
        )}
        {screen === "integrations" && activeRole === "hr" && (
          <div className="h-full overflow-y-auto">
            <LazyPage>
              <IntegrationsView
                onOpenDocuments={() => openScreen("docs")}
                openDrivePicker={openDrivePicker}
              />
            </LazyPage>
          </div>
        )}
        {screen === "sign-in" && activeRole === "hr" && user.role === "admin" && (
          <div className="h-full overflow-y-auto">
            <LazyPage>
              <SignInView
                workspace={user.workspace}
                me={user.username}
                onOpenIntegrations={() => openScreen("integrations")}
              />
            </LazyPage>
          </div>
        )}
      </main>
    </div>
    </IndexStatusProvider>
  )
}
