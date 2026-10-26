import { lazy, useEffect, useRef, useState, type ReactNode } from "react"
import {
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useSearchParams,
  type Location,
} from "react-router"
import { toast } from "sonner"

import { LazyPage } from "@/components/lazy-page"
import type { AccessTab } from "@/components/sign-in-view"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type User } from "@/lib/api"
import { useActiveRole, useAuth, useUser } from "@/lib/auth-context"
import { legacyRedirect, type SsoReturn } from "@/lib/legacy-hash"

// What each URL shows (routes.tsx has the list of them).

// Each page is its own download, so a visitor on the landing page doesn't load the
// chat (markdown rendering) and a public user never loads the HR screens.
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

// The old hash addresses (/#login, /#sso?code=..., ...) go to their new URLs.
export function Root() {
  const location = useLocation()
  const legacy = legacyRedirect(location.pathname, location.hash)
  if (legacy) return <Navigate to={legacy.to} state={legacy.state} replace />
  return <Outlet />
}

export function Home() {
  const { user } = useAuth()
  const navigate = useNavigate()
  if (user) return <Navigate to="/chat" replace />

  const go = (to: string) => {
    navigate(to)
    window.scrollTo(0, 0)
  }
  return (
    <LazyPage fullScreen>
      <LandingPage onSignIn={() => go("/login")} onGetStarted={() => go("/signup")} />
    </LazyPage>
  )
}

// /login and /signup: one page, so switching between them keeps what's typed.
// Signed in, it goes on to the screen that sent them here (App.tsx), or the chat.
export function SignInPage() {
  const { user, signedIn } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  if (user) {
    const from = (location.state as { from?: Location } | null)?.from
    return <Navigate to={from ?? "/chat"} replace />
  }
  return (
    <LazyPage fullScreen>
      <LoginPage
        onLogin={(u) => signedIn(u)}
        onBack={() => {
          navigate("/")
          window.scrollTo(0, 0)
        }}
      />
    </LazyPage>
  )
}

// Back from Google / Microsoft / a workspace's provider: swap the one-time code for a
// session. A new workspace's admin goes on to its Access screen.
export function SsoReturnPage() {
  const { signedIn, setRole } = useAuth()
  const navigate = useNavigate()
  const sso = useLocation().state as SsoReturn | null
  // One exchange, even when StrictMode runs the effect twice: the code works once.
  const exchange = useRef<Promise<{ token: string; user: User }> | null>(null)

  useEffect(() => {
    let stopped = false
    exchange.current ??= api.ssoExchange(sso?.code ?? "")
    exchange.current
      .then(({ token, user }) => {
        if (stopped) return
        signedIn(user, token)
        const welcome = !!sso?.welcome && user.role === "admin"
        if (welcome) setRole("hr")
        navigate(welcome ? "/admin/sign-in" : "/chat", { replace: true })
      })
      .catch(() => {
        if (!stopped) navigate("/login?error=expired", { replace: true })
      })
    return () => {
      stopped = true
    }
  }, [sso, signedIn, setRole, navigate])

  return (
    <div className="flex min-h-svh items-center justify-center">
      <Skeleton className="h-10 w-48" />
    </div>
  )
}

// Screens a role can't see send it to the chat (e.g. after "View as: Public").
export function HrOnly({ admin = false }: { admin?: boolean }) {
  const user = useUser()
  const activeRole = useActiveRole()
  if (activeRole !== "hr" || (admin && user.role !== "admin")) return <Navigate to="/chat" replace />
  return <Outlet />
}

export function Screen({ children }: { children: ReactNode }) {
  return (
    <div className="h-full overflow-y-auto">
      <LazyPage>{children}</LazyPage>
    </div>
  )
}

export function DocsScreen() {
  return (
    <Screen>
      <DocsView />
    </Screen>
  )
}

export function KnowledgeScreen() {
  const activeRole = useActiveRole()
  return (
    <Screen>
      <KnowledgeView key={activeRole} />
    </Screen>
  )
}

const CONNECT_ERRORS: Record<string, string> = {
  access_denied: "Access was not granted.",
  expired: "The connection request expired. Please try again.",
  connect_failed: "Couldn't finish connecting. Please try again.",
}

// Back from Google's / Notion's consent screen: /integrations?connected=... or ?error=...
export function IntegrationsScreen() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  // The picker opens by itself only on arrival from Google.
  const [openDrivePicker] = useState(() => !!params.get("connected") && params.get("choose") === "1")
  const shown = useRef(false)

  useEffect(() => {
    if (shown.current) return // once, even when StrictMode runs the effect twice
    shown.current = true
    const connected = params.get("connected")
    const error = params.get("error")
    if (!connected && !error) return
    if (connected) {
      const name = connected === "notion" ? "Notion" : "Google Drive"
      toast.success(
        params.get("choose") === "1"
          ? `${name} connected. Now choose which folders or files to sync.`
          : `${name} connected. Syncing…`,
      )
    }
    if (error) toast.error(CONNECT_ERRORS[error] ?? "The integration was not connected.")
    setParams({}, { replace: true })
  }, [params, setParams])

  return (
    <Screen>
      <IntegrationsView
        onOpenDocuments={() => navigate("/documents")}
        openDrivePicker={openDrivePicker}
      />
    </Screen>
  )
}

export function AccessScreen({ tab }: { tab: AccessTab }) {
  const user = useUser()
  const navigate = useNavigate()
  // A click on a tab asks for it twice (mouse down, then focus) before the URL has
  // changed: one history entry per switch, not two.
  const asked = useRef(tab)
  useEffect(() => {
    asked.current = tab
  }, [tab])

  return (
    <Screen>
      <SignInView
        workspace={user.workspace}
        me={user.username}
        tab={tab}
        onTabChange={(next) => {
          if (next === asked.current) return
          asked.current = next
          navigate(`/admin/${next}`)
        }}
        onOpenIntegrations={() => navigate("/integrations")}
      />
    </Screen>
  )
}
