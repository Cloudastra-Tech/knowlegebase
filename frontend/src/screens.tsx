import { lazy, useEffect, useRef, useState, type ReactNode } from "react"
import {
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
  type Location,
} from "react-router"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"

import { AsciiThinking } from "@/components/ascii/ascii-thinking"
import type { InsightsTab } from "@/components/insights-view"
import { LazyPage } from "@/components/lazy-page"
import { ScreenStrip } from "@/components/screen-strip"
import type { AccessTab } from "@/components/sign-in-view"
import { Skeleton } from "@/components/ui/skeleton"
import { api, drafts, type User } from "@/lib/api"
import { useActiveRole, useAuth, useUser } from "@/lib/auth-context"
import { useIsMobile } from "@/hooks/use-mobile"
import { isInstant } from "@/lib/instant-nav"
import { legacyRedirect, type SsoReturn } from "@/lib/legacy-hash"
import { queries } from "@/lib/queries"
import { settingsSectionsFor } from "@/lib/settings-sections"

// What each URL shows (routes.tsx has the list of them).

// Each page is its own download, so a visitor on the landing page doesn't load the
// chat (markdown rendering) and a public user never loads the HR screens.
const DocsView = lazy(() => import("@/components/docs-view").then((m) => ({ default: m.DocsView })))
const IntegrationsView = lazy(() =>
  import("@/components/integrations-view").then((m) => ({ default: m.IntegrationsView })),
)
const InsightsView = lazy(() =>
  import("@/components/insights-view").then((m) => ({ default: m.InsightsView })),
)
const SpacesView = lazy(() =>
  import("@/components/spaces-view").then((m) => ({ default: m.SpacesView })),
)
const VerifiedView = lazy(() =>
  import("@/components/verified-view").then((m) => ({ default: m.VerifiedView })),
)
const AssistantView = lazy(() =>
  import("@/components/assistant-view").then((m) => ({ default: m.AssistantView })),
)
const KnowledgeView = lazy(() =>
  import("@/components/knowledge-view").then((m) => ({ default: m.KnowledgeView })),
)
const SourceSheet = lazy(() =>
  import("@/components/citations").then((m) => ({ default: m.SourceSheet })),
)
const ExtensionConnect = lazy(() =>
  import("@/components/extension-connect").then((m) => ({ default: m.ExtensionConnect })),
)
const SharedView = lazy(() =>
  import("@/components/shared-view").then((m) => ({ default: m.SharedView })),
)
const LandingPage = lazy(() =>
  import("@/components/landing-page").then((m) => ({ default: m.LandingPage })),
)
const SignInView = lazy(() =>
  import("@/components/sign-in-view").then((m) => ({ default: m.SignInView })),
)
const LoginPage = lazy(() => import("@/components/login-page").then((m) => ({ default: m.LoginPage })))
const InviteAccept = lazy(() =>
  import("@/components/invite-accept").then((m) => ({ default: m.InviteAccept })),
)
const SignupVerify = lazy(() =>
  import("@/components/signup-verify").then((m) => ({ default: m.SignupVerify })),
)
const JoinRequested = lazy(() =>
  import("@/components/join-requested").then((m) => ({ default: m.JoinRequested })),
)
const ClaimWorkspace = lazy(() =>
  import("@/components/claim-workspace").then((m) => ({ default: m.ClaimWorkspace })),
)
const DigestUnsubscribe = lazy(() =>
  import("@/components/digest-unsubscribe").then((m) => ({ default: m.DigestUnsubscribe })),
)
const SettingsView = lazy(() =>
  import("@/components/settings/settings-view").then((m) => ({ default: m.SettingsView })),
)
const OperatorView = lazy(() =>
  import("@/components/operator-view").then((m) => ({ default: m.OperatorView })),
)

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

// /operator: the platform operators' console, with a sign-in of its own (not a member's).
export function OperatorScreen() {
  return (
    <LazyPage fullScreen>
      <OperatorView />
    </LazyPage>
  )
}

// /invite/:token: accept an admin's invite, signed in or not (it signs in as the
// invited account).
export function InvitePage() {
  const { signedIn } = useAuth()
  const navigate = useNavigate()
  const { token = "" } = useParams()
  return (
    <LazyPage fullScreen>
      <LoginPage onBack={() => navigate("/")}>
        <InviteAccept token={token} onJoined={(u, t) => signedIn(u, t)} />
      </LoginPage>
    </LazyPage>
  )
}

// /join-requested: a sign-in that asked to join a workspace (its joining policy is
// "ask an admin"). No session: they wait for an admin.
export function JoinRequestedPage() {
  const navigate = useNavigate()
  return (
    <LazyPage fullScreen>
      <LoginPage onBack={() => navigate("/")}>
        <JoinRequested />
      </LoginPage>
    </LazyPage>
  )
}

// /claim (?error=...): claim a workspace nobody can administer, with an operator's
// code and Google Workspace. Success comes back through /sso as its new admin.
export function ClaimPage() {
  const navigate = useNavigate()
  return (
    <LazyPage fullScreen>
      <LoginPage onBack={() => navigate("/")}>
        <ClaimWorkspace />
      </LoginPage>
    </LazyPage>
  )
}

// /signup/verify?token=: the link an email sign-up sent, then the domain's DNS record.
// The new workspace's admin goes on to its Access screen, as after any sign-up.
export function SignupVerifyPage() {
  const { signedIn, setRole } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  return (
    <LazyPage fullScreen>
      <LoginPage onBack={() => navigate("/")}>
        <SignupVerify
          token={params.get("token") ?? ""}
          onCreated={(u, t) => {
            signedIn(u, t)
            setRole("hr")
          }}
        />
      </LoginPage>
    </LazyPage>
  )
}

// /unsubscribe/:token: the one-click link in a digest email (no sign-in needed).
export function UnsubscribePage() {
  const navigate = useNavigate()
  const { token = "" } = useParams()
  return (
    <LazyPage fullScreen>
      <LoginPage onBack={() => navigate("/")}>
        <DigestUnsubscribe token={token} />
      </LoginPage>
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
      <AsciiThinking status="Signing you in" />
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

// Every screen but the chat: a box that scrolls, and the screen in it. Arriving by
// mouse, its content rises 6px and fades in (200ms, index.css "Screens arriving"),
// once its code has loaded; there's no exit. From the keyboard (the command palette,
// a shortcut: lib/instant-nav.ts) it's simply there. Decided as the screen mounts,
// so it holds even when the code arrives later. Once its title scrolls away, a frosted
// strip at the top keeps it in view (ScreenStrip).
export function Screen({ children }: { children: ReactNode }) {
  const [enter] = useState(() => !isInstant())
  const scroller = useRef<HTMLDivElement>(null)
  return (
    <div ref={scroller} className="h-full overflow-y-auto" data-print="flow">
      <ScreenStrip scroller={scroller} />
      <LazyPage>
        <div data-screen data-enter={enter || undefined} className="h-full" data-print="flow">
          {children}
        </div>
      </LazyPage>
    </div>
  )
}

export function DocsScreen() {
  const user = useUser()
  return (
    <Screen>
      <DocsView admin={user.role === "admin"} />
    </Screen>
  )
}

// /admin/verified (HR and admins): verified answers and their review queue.
export function VerifiedScreen() {
  return (
    <Screen>
      <VerifiedView />
    </Screen>
  )
}

// /admin/assistant (admins): the assistant's settings and glossary.
export function AssistantScreen() {
  return (
    <Screen>
      <AssistantView />
    </Screen>
  )
}

// /spaces, /spaces/:spaceId: your own space and your team spaces.
export function SpacesScreen() {
  const { spaceId } = useParams()
  const activeRole = useActiveRole()
  return (
    <Screen>
      <SpacesView key={`${activeRole}-${spaceId ?? ""}`} spaceId={spaceId} />
    </Screen>
  )
}

export function KnowledgeScreen() {
  const activeRole = useActiveRole()
  const navigate = useNavigate()
  return (
    <Screen>
      <KnowledgeView
        key={activeRole}
        onOpenDraft={(id) => navigate(`/documents?draft=${id}`)}
      />
    </Screen>
  )
}

// /extension/connect?redirect=...: the browser extension's sign-in (signed in first,
// like any screen here); Connect sends it back a one-time code.
export function ExtensionConnectScreen() {
  const [params] = useSearchParams()
  return (
    <Screen>
      <ExtensionConnect redirect={params.get("redirect") ?? ""} />
    </Screen>
  )
}

// /s/:token: a shared chat or answer, as the viewer's role may see it.
export function SharedScreen() {
  const { token = "" } = useParams()
  const activeRole = useActiveRole()
  return (
    <Screen>
      <SharedView key={`${activeRole}-${token}`} token={token} />
    </Screen>
  )
}

// /passages/:chunkId: one cited passage, opened from a link outside the app (a Slack
// answer's source with no link of its own). The server checks the role, as always.
export function PassageScreen() {
  const { chunkId = "" } = useParams()
  const navigate = useNavigate()
  const passage = useQuery(queries.passage(chunkId))
  const source = passage.data ? { ...passage.data, chunk_id: chunkId } : null
  return (
    <Screen>
      <div className="mx-auto max-w-xl px-4 py-10 text-sm text-muted-foreground">
        {passage.isPending ? (
          <Skeleton className="h-5 w-48" />
        ) : passage.isError ? (
          <p>
            This passage can't be opened: it's no longer in the knowledge base, or your role can't
            see it.
          </p>
        ) : (
          <p>Showing a cited passage.</p>
        )}
      </div>
      <SourceSheet source={source} onOpenChange={(open) => !open && navigate("/chat")} />
    </Screen>
  )
}

const CONNECT_ERRORS: Record<string, string> = {
  access_denied: "Access was not granted.",
  consent_denied: "Access was not granted, so nothing was connected.",
  cancelled: "Connecting was cancelled.",
  expired: "The connection request expired. Please try again.",
  no_refresh_token:
    "The provider didn't allow access while you're away. Remove the app's access in your account settings, then connect again.",
  connect_failed: "Couldn't finish connecting. Please try again.",
  wrong_account:
    "The approval came back for a different organisation than the one you signed in with, so nothing was connected.",
  admin_consent_required:
    "An administrator of your organisation has to approve the app. Ask one to connect it.",
  missing_scope:
    "The app wasn't given every permission it needs. Connect again and approve all of them.",
  no_site: "That Atlassian account has no site of that product (Confluence or Jira) the app can reach.",
  install_requested:
    "An owner of that GitHub organisation has to approve the app. Connect again once they have.",
  no_installation: "The GitHub app wasn't installed on an account, so nothing was connected.",
}
const CONNECTOR_NAMES: Record<string, string> = {
  google_drive: "Google Drive",
  notion: "Notion",
  confluence: "Confluence",
  sharepoint: "SharePoint & OneDrive",
  jira: "Jira",
  zendesk: "Zendesk",
  github: "GitHub",
  asana: "Asana",
  clickup: "ClickUp",
  teams_messages: "Microsoft Teams messages",
  gmail: "Gmail",
  google_calendar: "Google Calendar",
}
// Back from Slack's consent screen (kb/routers/slack.py): ?slack=installed or ?slack_error=...
const SLACK_ERRORS: Record<string, string> = {
  consent_denied: "The app wasn't allowed in Slack, so nothing was installed.",
  cancelled: "Installing in Slack was cancelled.",
  expired: "The install request expired. Please try again.",
  org_install: "Install the app in one Slack workspace, not across a whole Enterprise org.",
  team_taken: "That Slack workspace is already connected to another workspace here.",
  not_configured: "Choose the Slack app first.",
  connect_failed: "Couldn't finish installing in Slack. Please try again.",
}

// Back from a provider's consent screen: /integrations?connected=... or ?error=...
export function IntegrationsScreen() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  // The picker opens by itself only on arrival from a provider, when nothing is picked yet.
  const [openPickerFor] = useState(() =>
    params.get("choose") === "1" ? params.get("connected") : null,
  )
  const shown = useRef(false)

  useEffect(() => {
    if (shown.current) return // once, even when StrictMode runs the effect twice
    shown.current = true
    const connected = params.get("connected")
    const error = params.get("error")
    const slackDone = params.get("slack") === "installed"
    const slackError = params.get("slack_error")
    if (slackDone) toast.success("Installed in Slack. Try it: DM the app, or /ask.")
    if (slackError) toast.error(SLACK_ERRORS[slackError] ?? SLACK_ERRORS.connect_failed)
    if (slackDone || slackError) setParams({}, { replace: true })
    if (!connected && !error) return
    if (connected) {
      const name = CONNECTOR_NAMES[connected] ?? "The integration"
      toast.success(
        params.get("choose") === "1"
          ? `${name} connected. Now choose what to sync.`
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
        openPickerFor={openPickerFor}
      />
    </Screen>
  )
}

// /settings, /settings/:section (lib/settings-sections.ts). On a desktop /settings is
// General; on a phone it's the list of sections. Back from Google's consent screen for a
// member's own Gmail or Calendar: /settings/connections?connected=gmail&choose=1 or
// ?error=... (the old /connections redirects here with its query).
export function SettingsScreen() {
  const { section } = useParams()
  const user = useUser()
  const isMobile = useIsMobile()
  const [params] = useSearchParams()
  const [openPickerFor] = useState(() =>
    params.get("choose") === "1" ? params.get("connected") : null,
  )
  const known = settingsSectionsFor(user).find((s) => s.id === section)
  if (section && !known) return <Navigate to="/settings" replace />
  if (!section && !isMobile) return <Navigate to="/settings/general" replace />
  return (
    <Screen>
      {known?.id === "connections" && <ConnectReturn />}
      <SettingsView section={known?.id} openPickerFor={openPickerFor} />
    </Screen>
  )
}

// A toast for how connecting went, once, and the query cleared.
function ConnectReturn() {
  const [params, setParams] = useSearchParams()
  const shown = useRef(false)
  useEffect(() => {
    if (shown.current) return
    shown.current = true
    const connected = params.get("connected")
    const error = params.get("error")
    if (!connected && !error) return
    if (connected) {
      const name = CONNECTOR_NAMES[connected] ?? "It"
      toast.success(
        params.get("choose") === "1"
          ? `${name} connected. Now choose what to sync.`
          : `${name} connected. Syncing…`,
      )
    }
    if (error) toast.error(CONNECT_ERRORS[error] ?? "It was not connected.")
    setParams({}, { replace: true })
  }, [params, setParams])
  return null
}

// The personal screens' old addresses (lib/settings-sections.ts): to their section,
// keeping the query (?checkout=done from Stripe, ?connected= from Google).
export function MovedTo({ to }: { to: string }) {
  const { search, hash } = useLocation()
  return <Navigate to={{ pathname: to, search, hash }} replace />
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
      />
    </Screen>
  )
}

export function InsightsScreen({ tab }: { tab: InsightsTab }) {
  const user = useUser()
  const navigate = useNavigate()
  return (
    <Screen>
      <InsightsView
        tab={tab}
        canEdit={user.role === "admin"}
        onTabChange={(next) =>
          navigate(next === "overview" ? "/admin/insights" : `/admin/insights/${next}`)
        }
        onAddVerified={(gapId) => navigate(`/admin/verified?from_gap=${gapId}`)}
        onDraftDoc={async (gapId) => {
          try {
            const draft = await drafts.fromGap(gapId)
            toast.success("Writing a draft. It's in Documents, under Drafts.")
            navigate(`/documents?draft=${draft.id}`)
          } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't start the draft.")
          }
        }}
        onDraftQuestion={async (questionId) => {
          try {
            const draft = await drafts.fromQuestion(questionId)
            toast.success("Writing a draft. It's in Documents, under Drafts.")
            navigate(`/documents?draft=${draft.id}`)
          } catch (err) {
            toast.error(err instanceof Error ? err.message : "Couldn't start the draft.")
          }
        }}
      />
    </Screen>
  )
}
