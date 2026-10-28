import { useEffect, type ComponentProps } from "react"
import { ChevronsUpDown, Menu, Search, SquarePen } from "lucide-react"
import { Link, useLocation, useNavigate } from "react-router"

import { AccountMenu } from "@/components/account-menu"
import { ChatHistory } from "@/components/chat-sidebar"
import { IndexStatusText } from "@/components/index-progress"
import { SpaceIcon, SpaceSwitcher } from "@/components/space-switcher"
import { Button } from "@/components/ui/button"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { currentSection, isUnder, screensFor, screenTitle, type NavItem } from "@/lib/app-screens"
import { useActiveRole, useUser } from "@/lib/auth-context"
import { useChatPort, useChatState } from "@/lib/chat-bridge"
import { FOCUS_COMPOSER, MOD_KEY, OPEN_SEARCH, tellChat, TOGGLE_SIDEBAR } from "@/lib/chat-events"
import { spaceLabel } from "@/lib/spaces"
import { cn } from "@/lib/utils"

// New chat from outside the chat: through the chat, so an answer being written stops
// (lib/chat-bridge.ts), or, before the chat's code has loaded, by its address.
function useNewChat() {
  const port = useChatPort()
  const navigate = useNavigate()
  const { isMobile, setOpenMobile } = useSidebar()
  return () => {
    const chat = port?.actions()
    if (chat) chat.newChat()
    else {
      navigate("/chat")
      tellChat(FOCUS_COMPOSER)
    }
    if (isMobile) setOpenMobile(false)
  }
}

// The palette's "Show or hide the sidebar" (the sidebar's own ⌘B works too).
export function SidebarEvents() {
  const { toggleSidebar } = useSidebar()
  useEffect(() => {
    window.addEventListener(TOGGLE_SIDEBAR, toggleSidebar)
    return () => window.removeEventListener(TOGGLE_SIDEBAR, toggleSidebar)
  }, [toggleSidebar])
  return null
}

// The panel button: collapses the sidebar to its rail (⌘B), or closes the phone's sheet.
function CollapseButton() {
  const { open, isMobile } = useSidebar()
  const label = isMobile ? "Close menu" : open ? "Collapse sidebar" : "Expand sidebar"
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <SidebarTrigger
          aria-label={label}
          aria-keyshortcuts={isMobile ? undefined : "Control+B Meta+B"}
          className="ml-auto text-muted-foreground hover:text-foreground pointer-coarse:size-10 group-data-[collapsible=icon]:ml-0"
        />
      </TooltipTrigger>
      <TooltipContent side="right" hidden={isMobile}>
        {open ? "Collapse" : "Expand"} <span className="font-mono text-muted-foreground">{MOD_KEY}B</span>
      </TooltipContent>
    </Tooltip>
  )
}

// One screen in the nav: its icon and name; the icon alone, with a tooltip, in the
// rail. The open one is lit, its icon in the signal.
function NavLink({ item, open }: { item: NavItem; open: boolean }) {
  const { isMobile, setOpenMobile } = useSidebar()
  const Icon = item.icon
  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        asChild
        isActive={open}
        tooltip={item.label}
        className="data-[active=true]:[&>svg]:text-signal pointer-coarse:h-10"
      >
        <Link
          to={item.to}
          aria-current={open ? "page" : undefined}
          onClick={() => isMobile && setOpenMobile(false)}
        >
          <Icon />
          <span>{item.label}</span>
        </Link>
      </SidebarMenuButton>
    </SidebarMenuItem>
  )
}

// The space new chats ask in (chat.md C31), as a sidebar row. Not in the rail.
function SpaceRow() {
  const chat = useChatState()
  const port = useChatPort()
  const { isMobile, setOpenMobile } = useSidebar()
  return (
    <SpaceSwitcher
      value={chat.spaceId}
      onChange={(id) => {
        port?.actions()?.chooseSpace(id)
        if (isMobile) setOpenMobile(false)
      }}
      trigger={(current) => (
        <SidebarMenuButton
          aria-label={`Asking in: ${spaceLabel(current)}. Change space`}
          className="text-muted-foreground pointer-coarse:h-10"
        >
          <SpaceIcon space={current} />
          <span className="min-w-0 flex-1 truncate">{spaceLabel(current)}</span>
          <ChevronsUpDown className="ml-auto opacity-60" />
        </SidebarMenuButton>
      )}
    />
  )
}

// The app's one sidebar, on every signed-in screen (ChatGPT's shape): the brand and
// the collapse button; New chat and Search chats; the space new chats ask in; the
// screens, then the admin ones; the chat history, scrolling on its own; the index
// status and the account. Collapsed (⌘B, kept on this device) it is a rail of icons
// with tooltips that never goes away; on a phone it is a sheet from the left.
// chatAt: the conversation the chat was last at (Chat goes back to it).
export function AppSidebar({ chatAt }: { chatAt: string }) {
  const user = useUser()
  const role = useActiveRole()
  const location = useLocation()
  const chat = useChatState()
  const port = useChatPort()
  const navigate = useNavigate()
  const newChat = useNewChat()
  const { isMobile, setOpenMobile, state } = useSidebar()
  const rail = !isMobile && state === "collapsed"
  const { main, admin } = screensFor(user, role, chatAt)
  const current = currentSection(location.pathname, [...main, ...admin])
  const onChat = isUnder(location.pathname, "/chat")

  return (
    <Sidebar collapsible="icon" data-print="hide">
      <SidebarHeader className="gap-1 group-data-[collapsible=icon]:px-3">
        <div className="flex h-10 items-center gap-2 pl-2 group-data-[collapsible=icon]:pl-0">
          <span className="truncate text-sm font-semibold tracking-tight select-none group-data-[collapsible=icon]:hidden">
            Knowledge
            <span className="font-normal text-muted-foreground"> assistant</span>
          </span>
          <CollapseButton />
        </div>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              tooltip={`New chat (${MOD_KEY}⇧O)`}
              aria-keyshortcuts="Control+Shift+O Meta+Shift+O"
              onClick={newChat}
              className="pointer-coarse:h-10"
            >
              <SquarePen />
              <span className="truncate">New chat</span>
              {/* The shortcut, quiet; aria-keyshortcuts says it to assistive technology. */}
              <span aria-hidden className="ml-auto font-mono text-xs text-muted-foreground max-md:hidden">
                {MOD_KEY}⇧O
              </span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              tooltip={`Search chats (${MOD_KEY}K)`}
              aria-haspopup="dialog"
              onClick={() => {
                if (isMobile) setOpenMobile(false)
                window.dispatchEvent(new CustomEvent(OPEN_SEARCH))
              }}
              className="pointer-coarse:h-10"
            >
              <Search />
              <span className="truncate">Search chats</span>
              <span aria-hidden className="ml-auto font-mono text-xs text-muted-foreground max-md:hidden">
                {MOD_KEY}K
              </span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem className="group-data-[collapsible=icon]:hidden">
            <SpaceRow />
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <nav aria-label="Screens" className="flex shrink-0 flex-col">
        <SidebarGroup className="py-1 group-data-[collapsible=icon]:px-3">
          <SidebarGroupContent>
            <SidebarMenu>
              {main.map((item) => (
                <NavLink key={item.section} item={item} open={item.section === current} />
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        {admin.length > 0 && (
          <SidebarGroup className="py-1 group-data-[collapsible=icon]:px-3">
            <SidebarGroupLabel>Admin</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {admin.map((item) => (
                  <NavLink key={item.section} item={item} open={item.section === current} />
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}
      </nav>

      {/* The chats scroll on their own, under a hairline; the rail has none. */}
      <SidebarContent
        role="region"
        aria-label="Chat history"
        className="border-t border-sidebar-border group-data-[collapsible=icon]:invisible"
      >
        <ChatHistory
          currentId={chat.conversationId}
          onChat={onChat}
          busy={chat.busy}
          onOpen={(id) => {
            const actions = port?.actions()
            if (actions) actions.open(id)
            else navigate(`/chat/${id}`)
          }}
          onDeleted={(id) => port?.actions()?.deleted(id)}
        />
      </SidebarContent>

      <SidebarFooter className="gap-1 border-t border-sidebar-border group-data-[collapsible=icon]:px-3">
        {/* Indexing, in words; the glyph alone in the rail. */}
        <div className="flex min-w-0 items-center px-2 empty:hidden group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
          <IndexStatusText admin={user.role === "admin" && role === "hr"} compact={rail} />
        </div>
        <AccountMenu />
      </SidebarFooter>
    </Sidebar>
  )
}

// On a phone, above a screen (the chat has its own, in its header): the menu, the
// screen's name and New chat.
export function PhoneBar({ chatAt }: { chatAt: string }) {
  const user = useUser()
  const role = useActiveRole()
  const location = useLocation()
  const newChat = useNewChat()
  const { main, admin } = screensFor(user, role, chatAt)
  return (
    <div
      data-print="hide"
      className="glass z-20 flex h-12 shrink-0 items-center gap-1 rounded-none border-x-0 border-t-0 px-2 pt-[env(safe-area-inset-top)] md:hidden"
    >
      <PhoneMenuButton />
      <p className="min-w-0 flex-1 truncate px-1 text-sm font-medium">
        {screenTitle(location.pathname, [...main, ...admin])}
      </p>
      <Button variant="ghost" size="icon" aria-label="New chat" onClick={newChat} className="size-10 text-muted-foreground">
        <SquarePen />
      </Button>
    </div>
  )
}

// Opens the phone's sheet (the sidebar).
export function PhoneMenuButton({ className, ...props }: ComponentProps<typeof Button>) {
  const { toggleSidebar } = useSidebar()
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="Open menu"
      onClick={toggleSidebar}
      className={cn("size-10 text-muted-foreground md:hidden", className)}
      {...props}
    >
      <Menu />
    </Button>
  )
}
