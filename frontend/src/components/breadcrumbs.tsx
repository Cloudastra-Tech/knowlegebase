import { Fragment, memo, type ReactNode } from "react"
import { ArrowLeft, Check, ChevronDown } from "lucide-react"
import { Link } from "react-router"

import {
  Breadcrumb,
  BreadcrumbEllipsis,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import type { Crumb, CrumbLink, Trail } from "@/lib/breadcrumbs"
import { useChatState } from "@/lib/chat-bridge"
import { markInstantNav } from "@/lib/instant-nav"
import { cn } from "@/lib/utils"

// The breadcrumbs over a screen's title (lib/breadcrumbs.ts has where each screen
// sits): small and muted, every ancestor a link, the page itself plain text. A crumb
// lands at once, like ⌘K (lib/instant-nav.ts), and the chat stays as it was (it's
// kept mounted, App.tsx); "Chat" goes back to the conversation it was at. Past three
// crumbs the middle ones fold into a menu. On a phone, a detail view (a space, a
// draft) shows "← Spaces" instead: its logical parent, never history, so a deep link
// has one too.

// "/chat" is the conversation the chat is at, as the sidebar's Chat is.
function useResolve() {
  const { conversationId } = useChatState()
  return (to: string) => (to === "/chat" && conversationId ? `/chat/${conversationId}` : to)
}

const ITEM = "min-w-0"
const MIDDLE = "max-w-48 truncate"
const SEPARATOR = "text-muted-foreground/70"

function CrumbMenu({
  label,
  ariaLabel,
  items,
  current,
}: {
  label: ReactNode
  ariaLabel?: string
  items: CrumbLink[]
  current?: string
}) {
  const resolve = useResolve()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={ariaLabel}
        className="flex items-center gap-0.5 rounded-sm outline-none transition-colors duration-(--dur-hover) ease-hover hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:text-foreground"
      >
        {label}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-44">
        {items.map((item) => (
          <DropdownMenuItem key={item.to} asChild>
            <Link
              to={resolve(item.to)}
              onClick={markInstantNav}
              aria-current={item.label === current ? "page" : undefined}
            >
              <span className="flex-1 truncate">{item.label}</span>
              {item.label === current && <Check className="text-muted-foreground" />}
            </Link>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function CrumbItem({ crumb, last, current, middle }: { crumb: Crumb; last: boolean; current: string; middle: boolean }) {
  const resolve = useResolve()
  if (last) {
    return (
      <BreadcrumbItem className={ITEM}>
        <BreadcrumbPage className="truncate">{crumb.label}</BreadcrumbPage>
      </BreadcrumbItem>
    )
  }
  return (
    <BreadcrumbItem className={cn(ITEM, middle && "shrink")}>
      {crumb.menu ? (
        <CrumbMenu
          items={crumb.menu}
          current={current}
          label={
            <>
              {crumb.label}
              <ChevronDown aria-hidden className="size-3" />
            </>
          }
        />
      ) : (
        <BreadcrumbLink asChild className={cn(middle && MIDDLE)}>
          <Link to={resolve(crumb.to ?? "/")} onClick={markInstantNav} title={middle ? crumb.label : undefined}>
            {crumb.label}
          </Link>
        </BreadcrumbLink>
      )}
    </BreadcrumbItem>
  )
}

export function Breadcrumbs({ trail, className }: { trail: Trail; className?: string }) {
  const { crumbs, back } = trail
  const resolve = useResolve()
  // The row keeps its height with nothing in it, so a title never moves between screens.
  if (!crumbs.length) return <div data-slot="breadcrumbs" aria-hidden className={cn("h-5", className)} />
  const current = crumbs[crumbs.length - 1].label
  // Deep: the first crumb, the rest folded into "…", then the last two.
  const folded = crumbs.length > 3 ? crumbs.slice(1, -2) : []
  const shown = folded.length ? [crumbs[0], ...crumbs.slice(-2)] : crumbs

  return (
    <div data-slot="breadcrumbs" className={cn("flex h-5 min-w-0 items-center text-sm leading-5", className)}>
      {back && (
        <Link
          to={resolve(back.to)}
          onClick={markInstantNav}
          aria-label={`Back to ${back.label}`}
          className="-ml-1 flex min-w-0 items-center gap-1 rounded-sm px-1 text-muted-foreground outline-none transition-colors duration-(--dur-hover) ease-hover hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring sm:hidden"
        >
          <ArrowLeft aria-hidden className="size-4 shrink-0" />
          <span className="truncate">{back.label}</span>
        </Link>
      )}
      <Breadcrumb className={cn("min-w-0", back && "max-sm:hidden")}>
        <BreadcrumbList className="flex-nowrap gap-1 text-sm">
          {shown.map((crumb, i) => (
            <Fragment key={`${i}-${crumb.label}`}>
              {i > 0 && <BreadcrumbSeparator className={SEPARATOR} />}
              {folded.length > 0 && i === 1 && (
                <>
                  <BreadcrumbItem>
                    <CrumbMenu
                      ariaLabel="More of the path"
                      items={folded.map((c) => ({ label: c.label, to: c.to ?? "/" }))}
                      label={<BreadcrumbEllipsis className="size-4" />}
                    />
                  </BreadcrumbItem>
                  <BreadcrumbSeparator className={SEPARATOR} />
                </>
              )}
              <CrumbItem
                crumb={crumb}
                last={i === shown.length - 1}
                current={current}
                middle={i > 0 && i < shown.length - 1}
              />
            </Fragment>
          ))}
        </BreadcrumbList>
      </Breadcrumb>
    </div>
  )
}

// The chat's header: where the chat is asked (a space, or All company) and its title,
// the space from sm up and not in print. Drawn again on every streamed token with the
// rest of the chat, so it's memoised and reads no chat state.
export const ChatBreadcrumbs = memo(function ChatBreadcrumbs({ crumbs }: { crumbs: Crumb[] }) {
  if (!crumbs.length) return <div data-slot="breadcrumbs" aria-hidden className="h-5" />
  return (
    <Breadcrumb className="flex h-5 min-w-0 items-center">
      <BreadcrumbList className="flex-nowrap gap-1 text-sm">
        {crumbs.map((crumb, i) => {
          const last = i === crumbs.length - 1
          return (
            <Fragment key={i}>
              {i > 0 && <BreadcrumbSeparator className={cn(SEPARATOR, "max-sm:hidden print:hidden")} />}
              <BreadcrumbItem className={cn(ITEM, !last && "shrink-0 max-sm:hidden print:hidden")}>
                {last ? (
                  <BreadcrumbPage className="truncate font-medium">{crumb.label}</BreadcrumbPage>
                ) : (
                  <BreadcrumbLink asChild className={MIDDLE}>
                    <Link to={crumb.to ?? "/"} onClick={markInstantNav}>
                      {crumb.label}
                    </Link>
                  </BreadcrumbLink>
                )}
              </BreadcrumbItem>
            </Fragment>
          )
        })}
      </BreadcrumbList>
    </Breadcrumb>
  )
})
