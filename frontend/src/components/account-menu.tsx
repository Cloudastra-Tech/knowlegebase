import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  ChevronsUpDown,
  CreditCard,
  History,
  KeyRound,
  Layers,
  LogOut,
  Mail,
  MessageSquareText,
  MonitorSmartphone,
  Plug,
} from "lucide-react"
import { Link } from "react-router"
import { toast } from "sonner"

import { ChatSettingsDialog } from "@/components/chat-settings-dialog"
import { ThemeMenu } from "@/components/theme-menu"
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
import { Badge } from "@/components/ui/badge"
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
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem, useSidebar } from "@/components/ui/sidebar"
import { api, type Role } from "@/lib/api"
import { useAuth, useUser } from "@/lib/auth-context"
import { keptFor } from "@/lib/chat-history"
import { queries } from "@/lib/queries"

const initialsOf = (name: string) =>
  name
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase()

// The foot of the app's sidebar: who's signed in (the avatar, the name, the workspace
// and the role the app is viewed as), opening the account menu: View as (HR and admin
// accounts), the personal screens, how long chats are kept, the theme and Reduce
// effects, and signing out. Only the avatar in the collapsed rail.
export function AccountMenu() {
  const user = useUser()
  const { role, setRole, signedOut } = useAuth()
  const { isMobile, state, setOpenMobile } = useSidebar()
  const settings = useQuery({ ...queries.chatSettings(), meta: { silent: true } })
  const [confirmSignOutAll, setConfirmSignOutAll] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)

  // Only an HR or admin account gets HR; it can also "view as" public.
  const canAdmin = user.role !== "public"
  const activeRole: Role = canAdmin ? role : "public"
  const canChangeSettings = user.role === "admin" && activeRole === "hr"
  const initials = initialsOf(user.name) || "?"
  const roleLabel = activeRole === "hr" ? "HR / Admin" : canAdmin ? "Public" : "Member"
  // A personal screen opened from the phone's sheet closes it.
  const close = () => isMobile && setOpenMobile(false)

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

  const retention = settings.data
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            {/* Named by what it shows, after "Account," (no aria-label: the visible text
                has to be in the name). */}
            <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent">
              <span className="sr-only">Account, </span>
              <Avatar aria-hidden className="size-8">
                <AvatarFallback className="bg-foreground text-xs font-medium text-background">
                  {initials}
                </AvatarFallback>
              </Avatar>
              <span className="grid min-w-0 flex-1 text-left leading-tight">
                <span className="truncate text-sm font-medium">{user.name}</span>
                <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
                  <span className="truncate">{user.workspace}</span>
                  <Badge variant="mono" className="h-4 px-1.5">
                    {roleLabel}
                  </Badge>
                </span>
              </span>
              <ChevronsUpDown className="ml-auto text-muted-foreground" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            side={isMobile ? "top" : state === "collapsed" ? "right" : "top"}
            align={isMobile || state !== "collapsed" ? "start" : "end"}
            sideOffset={8}
            className="w-64"
          >
            <DropdownMenuLabel>
              <div className="text-sm font-medium">{user.name}</div>
              <div className="text-xs font-normal text-muted-foreground">
                {user.username.includes("@") ? user.username : `@${user.username}`}
              </div>
            </DropdownMenuLabel>
            {canAdmin && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                  View as
                </DropdownMenuLabel>
                <DropdownMenuRadioGroup value={role} onValueChange={(v) => setRole(v as Role)}>
                  <DropdownMenuRadioItem value="hr">HR / Admin</DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="public">Public user</DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
              </>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link to="/spaces" onClick={close}>
                <Layers /> Spaces
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link to="/prompts" onClick={close}>
                <MessageSquareText /> My prompts
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link to="/api-keys" onClick={close}>
                <KeyRound /> API keys
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link to="/digests" onClick={close}>
                <Mail /> Digests
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link to="/connections" onClick={close}>
                <Plug /> My connections
              </Link>
            </DropdownMenuItem>
            {user.billing?.can_view && (
              <DropdownMenuItem asChild>
                <Link to="/billing" onClick={close}>
                  <CreditCard /> Plan &amp; billing
                </Link>
              </DropdownMenuItem>
            )}
            {retention && (
              <>
                <DropdownMenuSeparator />
                {/* How long chats are kept, out of the sidebar's way; an admin changes it here. */}
                <DropdownMenuLabel className="text-xs font-normal text-pretty text-muted-foreground">
                  Chats are kept {keptFor(retention.retention_days)}
                  {retention.retention_days !== null && " after they were last used"}
                  {canChangeSettings ? "." : " (set by your admin)."}
                </DropdownMenuLabel>
                {canChangeSettings && (
                  <DropdownMenuItem onSelect={() => setSettingsOpen(true)}>
                    <History /> Chat settings
                  </DropdownMenuItem>
                )}
              </>
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
      </SidebarMenuItem>
      {retention && canChangeSettings && (
        <ChatSettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} settings={retention} />
      )}
      <AlertDialog open={confirmSignOutAll} onOpenChange={setConfirmSignOutAll}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Sign out everywhere?</AlertDialogTitle>
            <AlertDialogDescription>
              You're signed out on every device and browser where you're signed in, this one
              included. Use it if you lost a device or signed in on a computer that isn't yours.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={signOutEverywhere}>Sign out everywhere</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SidebarMenu>
  )
}
