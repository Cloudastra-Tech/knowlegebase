import { useEffect, useState } from "react"
import { ChevronDown, LogOut } from "lucide-react"
import { toast } from "sonner"

import { ChatView } from "@/components/chat-view"
import { DocsView } from "@/components/docs-view"
import { IntegrationsView } from "@/components/integrations-view"
import { IndexProgressBar, IndexStatusText } from "@/components/index-progress"
import { LandingPage } from "@/components/landing-page"
import { LoginPage } from "@/components/login-page"
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
import { cn } from "@/lib/utils"
import { api, session, setUnauthorizedHandler, type Role, type User } from "@/lib/api"

type Screen = "chat" | "docs" | "integrations"

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [checking, setChecking] = useState(!!session.token)
  const [role, setRole] = useState<Role>(session.role)
  const [screen, setScreen] = useState<Screen>("chat")
  const [openDrivePicker, setOpenDrivePicker] = useState(false)
  // Signed out: the landing page, or the sign-in form at #login.
  const [wantsLogin, setWantsLogin] = useState(() => window.location.hash === "#login")

  useEffect(() => {
    const onHash = () => setWantsLogin(window.location.hash === "#login")
    window.addEventListener("hashchange", onHash)
    return () => window.removeEventListener("hashchange", onHash)
  }, [])

  function goTo(hash: string) {
    if (hash) window.location.hash = hash
    else history.replaceState(null, "", window.location.pathname)
    setWantsLogin(hash === "login")
    window.scrollTo(0, 0)
  }

  function onLogin(u: User) {
    goTo("")
    setUser(u)
  }

  // Back from Google's consent screen: /#integrations?connected=... or ?error=...
  useEffect(() => {
    if (!user || !window.location.hash.startsWith("#integrations")) return
    const params = new URLSearchParams(window.location.hash.split("?")[1] ?? "")
    history.replaceState(null, "", window.location.pathname)
    session.setRole("hr")
    setRole("hr")
    setScreen("integrations")
    if (params.get("connected")) {
      const choose = params.get("choose") === "1"
      setOpenDrivePicker(choose)
      toast.success(
        choose
          ? "Google Drive connected. Now choose which folders or files to sync."
          : "Google Drive connected. Syncing your selection…",
      )
    }
    const error = params.get("error")
    if (error) {
      const why: Record<string, string> = {
        access_denied: "Access to Google Drive was not granted.",
        expired: "The connection request expired. Please try again.",
        connect_failed: "Couldn't finish connecting Google Drive. Please try again.",
      }
      toast.error(why[error] ?? "Google Drive was not connected.")
    }
  }, [user])

  // Restore the session after a page refresh.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      session.setToken(null)
      setUser(null)
    })
    if (!session.token) return
    api
      .me()
      .then(setUser)
      .catch(() => session.setToken(null))
      .finally(() => setChecking(false))
  }, [])

  function changeRole(next: Role) {
    session.setRole(next)
    setRole(next)
    if (next !== "hr") setScreen("chat")
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
    return wantsLogin ? (
      <LoginPage onLogin={onLogin} onBack={() => goTo("")} />
    ) : (
      <LandingPage onSignIn={() => goTo("login")} />
    )
  }

  const initials = user.name
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase()

  const navItems: { id: Screen; label: string }[] = [
    { id: "chat", label: "Chat" },
    ...(role === "hr"
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
                  screen === item.id
                    ? "text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {item.label}
                {screen === item.id && (
                  <span className="absolute inset-x-0 -bottom-px h-px bg-foreground" />
                )}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1">
            <div className="mr-2">
              <IndexStatusText />
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 gap-1 rounded-full px-3 text-xs font-normal text-muted-foreground hover:text-foreground"
                >
                  {role === "hr" ? "HR / Admin" : "Public"}
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
                  <div className="text-xs font-normal text-muted-foreground">@{user.username}</div>
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
          <ChatView role={role} />
        </div>
        {screen === "docs" && role === "hr" && (
          <div className="h-full overflow-y-auto">
            <DocsView />
          </div>
        )}
        {screen === "integrations" && role === "hr" && (
          <div className="h-full overflow-y-auto">
            <IntegrationsView
              onOpenDocuments={() => setScreen("docs")}
              openDrivePicker={openDrivePicker}
            />
          </div>
        )}
      </main>
    </div>
    </IndexStatusProvider>
  )
}
