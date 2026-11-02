import type { ReactNode } from "react"
import { ChevronsUpDown, Compass, Keyboard, LogOut, Settings, SunMoon } from "lucide-react"
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
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem, useSidebar } from "@/components/ui/sidebar"
import { api } from "@/lib/api"
import { useAuth, useUser } from "@/lib/auth-context"
import { MOD_KEY, OPEN_SHORTCUTS } from "@/lib/chat-events"
import { ROLE_LABEL } from "@/lib/roles"
import { startTour, type TourId } from "@/lib/tour"

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
// and the account's role), opening the account menu, short as ChatGPT's and
// Claude.ai's: who you are, Settings (everything personal: settings-view.tsx),
// Appearance (the theme and Reduce effects), the keyboard shortcuts, the product tour
// (and the admin one, for admins), and Sign out. It's opened many times a day, so it opens as every menu does and nothing more.
// Only the avatar in the collapsed rail.
export function AccountMenu() {
  const user = useUser()
  const { signedOut } = useAuth()
  const { isMobile, state, setOpenMobile } = useSidebar()

  const initials = initialsOf(user.name) || "?"
  const email = user.username.includes("@") ? user.username : `@${user.username}`
  // Settings opened from the phone's sheet closes it.
  const close = () => isMobile && setOpenMobile(false)
  // The tours (components/product-tour.tsx): the admin one shows admin screens.
  const adminTour = user.role === "admin"
  const tour = (id: TourId) => {
    close()
    startTour(id)
  }

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
            <SidebarMenuButton size="lg" data-tour="account" className="data-[state=open]:bg-sidebar-accent">
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
                    {ROLE_LABEL[user.role]}
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
              {/* One row: an admin picks the tour in its submenu (the basics' last
                  step offers the admin one too). */}
              {adminTour ? (
                <MenuGroup
                  inline={isMobile}
                  heading="Take the tour"
                  className="w-44"
                  trigger={
                    <>
                      <Compass /> Take the tour
                    </>
                  }
                >
                  <DropdownMenuItem onSelect={() => tour("members")}>The basics</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => tour("admin")}>Admin tour</DropdownMenuItem>
                </MenuGroup>
              ) : (
                <DropdownMenuItem onSelect={() => tour("members")}>
                  <Compass /> Take the tour
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
