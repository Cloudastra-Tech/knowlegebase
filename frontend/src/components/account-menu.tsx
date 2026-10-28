import type { ReactNode } from "react"
import { ChevronsUpDown, Eye, Keyboard, LogOut, Settings, SunMoon } from "lucide-react"
import { Link } from "react-router"

import { ThemeMenu } from "@/components/theme-menu"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem, useSidebar } from "@/components/ui/sidebar"
import { api, type Role } from "@/lib/api"
import { useAuth, useUser } from "@/lib/auth-context"
import { MOD_KEY, OPEN_SHORTCUTS } from "@/lib/chat-events"

const initialsOf = (name: string) =>
  name
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase()

// A submenu, on a desktop (portalled: dropdown-menu.tsx). A phone has no room beside
// the menu for one (it would run off the screen's edge) but has the height, so there
// its items are in the menu, under `heading` when they have none of their own.
function MenuGroup({
  inline,
  trigger,
  heading,
  className,
  children,
}: {
  inline: boolean
  trigger: ReactNode
  heading?: string
  className?: string
  children: ReactNode
}) {
  if (inline) {
    return (
      <>
        {heading && (
          <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">{heading}</DropdownMenuLabel>
        )}
        {children}
      </>
    )
  }
  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger>{trigger}</DropdownMenuSubTrigger>
      <DropdownMenuSubContent className={className}>{children}</DropdownMenuSubContent>
    </DropdownMenuSub>
  )
}

// The foot of the app's sidebar: who's signed in (the avatar, the name, the workspace
// and the role the app is viewed as), opening the account menu, short as ChatGPT's and
// Claude.ai's: who you are, Settings (everything personal: settings-view.tsx), View as
// (HR and admin accounts), Appearance (the theme and Reduce effects), the keyboard
// shortcuts, and Sign out. It's opened many times a day, so it opens as every menu does
// and nothing more. Only the avatar in the collapsed rail.
export function AccountMenu() {
  const user = useUser()
  const { role, setRole, signedOut } = useAuth()
  const { isMobile, state, setOpenMobile } = useSidebar()

  // Only an HR or admin account gets HR; it can also "view as" public.
  const canAdmin = user.role !== "public"
  const activeRole: Role = canAdmin ? role : "public"
  const initials = initialsOf(user.name) || "?"
  const roleLabel = activeRole === "hr" ? "HR / Admin" : canAdmin ? "Public" : "Member"
  const email = user.username.includes("@") ? user.username : `@${user.username}`
  // Settings opened from the phone's sheet closes it.
  const close = () => isMobile && setOpenMobile(false)

  async function signOut() {
    const out = await api.logout().catch(() => null)
    signedOut(true)
    // The admin set this provider to sign people out there too.
    if (out?.logout_url) window.location.assign(out.logout_url)
  }

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
            <DropdownMenuLabel className="flex flex-col gap-0.5 font-normal">
              <span className="truncate text-sm font-medium">{user.name}</span>
              <span className="truncate text-xs text-muted-foreground">{email}</span>
              <span className="truncate text-xs text-muted-foreground">{user.workspace} workspace</span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem asChild>
                <Link to="/settings" onClick={close}>
                  <Settings /> Settings
                </Link>
              </DropdownMenuItem>
              {canAdmin && (
                <MenuGroup
                  inline={isMobile}
                  heading="View as"
                  className="w-44"
                  trigger={
                    <>
                      <Eye />
                      <span className="flex-1">View as</span>
                      <span className="text-xs text-muted-foreground">
                        {activeRole === "hr" ? "HR / Admin" : "Public"}
                      </span>
                    </>
                  }
                >
                  <DropdownMenuRadioGroup value={role} onValueChange={(v) => setRole(v as Role)}>
                    <DropdownMenuRadioItem value="hr">HR / Admin</DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="public">Public user</DropdownMenuRadioItem>
                  </DropdownMenuRadioGroup>
                </MenuGroup>
              )}
              <MenuGroup
                inline={isMobile}
                className="w-52"
                trigger={
                  <>
                    <SunMoon /> Appearance
                  </>
                }
              >
                <ThemeMenu />
              </MenuGroup>
              {/* The palette's list of shortcuts (⌘/); a phone has no keyboard to use them. */}
              {!isMobile && (
                <DropdownMenuItem onSelect={() => window.dispatchEvent(new CustomEvent(OPEN_SHORTCUTS))}>
                  <Keyboard /> Keyboard shortcuts
                  <DropdownMenuShortcut className="font-mono tracking-normal">{MOD_KEY}/</DropdownMenuShortcut>
                </DropdownMenuItem>
              )}
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={signOut}>
              <LogOut /> Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
