import { useEffect, useState } from "react"
import { BookOpenText, FolderOpen, LogOut, MessageSquare } from "lucide-react"

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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
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

  return (
    <div className="flex h-svh flex-col">
      <header className="border-b bg-background">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4">
          <div className="flex items-center gap-2">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <BookOpenText className="size-4" />
            </div>
            <span className="hidden font-semibold sm:inline">Knowledge Assistant</span>
          </div>

          <Tabs value={screen} onValueChange={(v) => setScreen(v as Screen)} className="ml-2">
            <TabsList>
              <TabsTrigger value="chat">
                <MessageSquare /> Chat
              </TabsTrigger>
              {role === "hr" && (
                <TabsTrigger value="docs">
                  <FolderOpen /> Documents
                </TabsTrigger>
              )}
            </TabsList>
          </Tabs>

          <div className="ml-auto flex items-center gap-2">
            <Select value={role} onValueChange={(v) => changeRole(v as Role)}>
              <SelectTrigger size="sm" className="w-36" aria-label="Role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="hr">HR / Admin</SelectItem>
                <SelectItem value="public">Public user</SelectItem>
              </SelectContent>
            </Select>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="rounded-full" aria-label="Account">
                  <Avatar className="size-8">
                    <AvatarFallback>{initials || "?"}</AvatarFallback>
                  </Avatar>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>
                  <div className="font-medium">{user.name}</div>
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
