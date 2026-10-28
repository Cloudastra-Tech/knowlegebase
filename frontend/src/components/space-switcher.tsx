import type { ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router"
import { Building2, Check, ChevronsUpDown, Settings2, User, Users } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import type { SpaceSummary } from "@/lib/api"
import { queries } from "@/lib/queries"
import { spaceLabel } from "@/lib/spaces"
import { cn } from "@/lib/utils"

export function SpaceIcon({ space, className }: { space: SpaceSummary | null | undefined; className?: string }) {
  const Icon = !space ? Building2 : space.kind === "personal" ? User : Users
  return <Icon className={className} />
}

// The space a chat is asked in (chat.md C31): "All company" (everything you may
// see), your own space (your files), or a team space (its sources). In the
// history sidebar and as a chip in the composer; both open the same menu, and its
// "Manage spaces" is the way to the Spaces screen (a space is the workspace's, not
// your account's, so Spaces is in neither the account menu nor Settings).
export function SpaceSwitcher({
  value,
  onChange,
  trigger,
  align = "start",
  onManage,
}: {
  value: string | null
  onChange: (spaceId: string | null) => void
  trigger?: (current: SpaceSummary | null) => ReactNode
  align?: "start" | "end"
  onManage?: () => void // "Manage spaces" picked (the phone's sheet closes)
}) {
  const list = useQuery({ ...queries.spaces(), meta: { silent: true } })
  const spaces = list.data?.spaces ?? []
  const current = spaces.find((s) => s.id === value) ?? null
  const teams = spaces.filter((s) => s.kind === "team")
  const mine = spaces.find((s) => s.kind === "personal")

  const item = (space: SpaceSummary | null) => (
    <DropdownMenuItem key={space?.id ?? "all"} onSelect={() => onChange(space?.id ?? null)}>
      <SpaceIcon space={space} />
      <span className="min-w-0 flex-1 truncate">{spaceLabel(space)}</span>
      {(space?.id ?? null) === (current?.id ?? null) && <Check className="ml-auto" />}
    </DropdownMenuItem>
  )

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        {trigger ? (
          trigger(current)
        ) : (
          <Button variant="outline" className="w-full justify-start pointer-coarse:h-10">
            <SpaceIcon space={current} />
            <span className="min-w-0 flex-1 truncate text-left">{spaceLabel(current)}</span>
            <ChevronsUpDown className="text-muted-foreground" />
          </Button>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align={align} className="w-64">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
          Ask in
        </DropdownMenuLabel>
        {item(null)}
        {mine && item(mine)}
        {teams.length > 0 && <DropdownMenuSeparator />}
        {teams.map(item)}
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to={current ? `/spaces/${current.id}` : "/spaces"} onClick={onManage}>
            <Settings2 />
            Manage spaces
          </Link>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// The composer's chip: the space a question goes to, opening the switcher.
export function SpaceChip({
  value,
  onChange,
  disabled,
}: {
  value: string | null
  onChange: (spaceId: string | null) => void
  disabled?: boolean
}) {
  return (
    <SpaceSwitcher
      value={value}
      onChange={onChange}
      trigger={(current) => (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          aria-label={`Asking in: ${spaceLabel(current)}. Change space`}
          className={cn(
            "h-7 max-w-48 gap-1.5 rounded-full px-2.5 text-xs font-normal text-muted-foreground",
            current && "bg-muted text-foreground",
          )}
        >
          <SpaceIcon space={current} className="size-3.5" />
          <span className="truncate">{spaceLabel(current)}</span>
          <ChevronsUpDown className="size-3 opacity-60" />
        </Button>
      )}
    />
  )
}
