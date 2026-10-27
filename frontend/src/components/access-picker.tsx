import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Check, ChevronsUpDown, Globe, Lock, User, Users, X } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import type { PrincipalKind, PrincipalOption, WhoCanSee } from "@/lib/api"
import { queries } from "@/lib/queries"
import { cn } from "@/lib/utils"

export const EVERYONE = "everyone"
export const HR_GROUP = "group:hr"

const ICONS: Record<PrincipalKind, typeof Globe> = {
  everyone: Globe,
  group: Users,
  user: User,
  domain: Globe,
  nobody: Lock,
}

// Who can see something, as small badges: "Everyone", a group's name, a person.
export function WhoCanSeeBadges({ who, className }: { who: WhoCanSee[]; className?: string }) {
  return (
    <span className={cn("flex flex-wrap gap-1", className)}>
      {who.map((w) => {
        const Icon = ICONS[w.kind] ?? Users
        return (
          <Badge
            key={w.principal}
            variant={w.kind === "nobody" ? "destructive" : w.kind === "everyone" ? "secondary" : "outline"}
            className="max-w-48 gap-1 font-normal"
          >
            <Icon className="size-3 shrink-0" />
            <span className="truncate">{w.label}</span>
          </Badge>
        )
      })}
    </span>
  )
}

// Who may see a document: everyone, or any mix of groups and people. Picking
// "Everyone" clears the rest (it already includes them); picking a group or a
// person clears "Everyone". At least one is always picked.
export function AccessPicker({
  id,
  value,
  onChange,
  disabled,
}: {
  id?: string
  value: string[]
  onChange: (acl: string[]) => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const options = useQuery(queries.principals()).data?.principals ?? []
  const byPrincipal = new Map(options.map((o) => [o.principal, o]))
  const shown: WhoCanSee[] = value.map(
    (p) =>
      byPrincipal.get(p) ?? {
        principal: p,
        kind: (p === EVERYONE ? "everyone" : p.split(":")[0]) as PrincipalKind,
        label: p === EVERYONE ? "Everyone in the workspace" : p.split(":").slice(1).join(":"),
      },
  )

  function toggle(p: string) {
    if (p === EVERYONE) return onChange([EVERYONE])
    const rest = value.filter((v) => v !== EVERYONE)
    const next = rest.includes(p) ? rest.filter((v) => v !== p) : [...rest, p]
    onChange(next.length ? next : [EVERYONE])
  }

  const group = (kind: PrincipalKind) => options.filter((o) => o.kind === kind)
  const item = (o: PrincipalOption) => {
    const Icon = ICONS[o.kind] ?? Users
    return (
      <CommandItem
        key={o.principal}
        value={`${o.label} ${o.detail ?? ""} ${o.principal}`}
        onSelect={() => toggle(o.principal)}
      >
        <Check className={cn("size-4", value.includes(o.principal) ? "opacity-100" : "opacity-0")} />
        <Icon className="size-4 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate">{o.label}</span>
        {o.detail && o.detail !== o.label && (
          <span className="min-w-0 shrink truncate text-xs text-muted-foreground">{o.detail}</span>
        )}
        {o.source && o.source !== "manual" && (
          <span className="shrink-0 text-xs text-muted-foreground">
            {o.source === "builtin" ? "roles" : o.source.toUpperCase()}
          </span>
        )}
      </CommandItem>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            id={id}
            type="button"
            variant="outline"
            role="combobox"
            aria-expanded={open}
            disabled={disabled}
            className="h-auto min-h-8 w-full justify-between py-1.5 font-normal"
          >
            <WhoCanSeeBadges who={shown} />
            <ChevronsUpDown className="shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[min(24rem,calc(100vw-2rem))] p-0">
          <Command>
            <CommandInput placeholder="Search groups and people…" />
            <CommandList>
              <CommandEmpty>Nobody matches.</CommandEmpty>
              <CommandGroup>{group("everyone").map(item)}</CommandGroup>
              <CommandSeparator />
              <CommandGroup heading="Groups">{group("group").map(item)}</CommandGroup>
              <CommandSeparator />
              <CommandGroup heading="People">{group("user").map(item)}</CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      {value.length > 0 && !value.includes(EVERYONE) && (
        <p className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
          Only these can ask about it.
          {value.length > 1 && (
            <Button
              type="button"
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs"
              onClick={() => onChange([HR_GROUP])}
            >
              <X className="size-3" /> Reset to HR and admins
            </Button>
          )}
        </p>
      )}
    </div>
  )
}
