import { useEffect, useState } from "react"
import { ChevronDown, LogOut } from "lucide-react"

import { ChatView } from "@/components/chat-view"
import { DocsView } from "@/components/docs-view"
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
import { cn } from "@/lib/utils"
import { api, session, setUnauthorizedHandler, type Role, type User } from "@/lib/api"

type Screen = "chat" | "docs"

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [checking, setChecking] = useState(!!session.token)
  const [role, setRole] = useState<Role>(session.role)
  const [screen, setScreen] = useState<Screen>("chat")

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

  if (!user) return <LoginPage onLogin={setUser} />

  const initials = user.name
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase()

  const navItems: { id: Screen; label: string }[] = [
    { id: "chat", label: "Chat" },
    ...(role === "hr" ? [{ id: "docs" as Screen, label: "Documents" }] : []),
  ]

  return (
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
      </main>
    </div>
  )
}
