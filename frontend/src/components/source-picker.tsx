import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Check, FileText, Lock, Plus, X } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import type { VerifiedSource } from "@/lib/api"
import { queries } from "@/lib/queries"
import { cn } from "@/lib/utils"

// Pick the documents a verified answer rests on, from what the assistant can search
// (the Knowledge list). `value` holds document keys; `known` names keys the list may
// not have any more (a document removed since).
export function SourcePicker({
  id,
  value,
  onChange,
  known = [],
  max = 10,
}: {
  id?: string
  value: string[]
  onChange: (keys: string[]) => void
  known?: VerifiedSource[]
  max?: number
}) {
  const [open, setOpen] = useState(false)
  const items = useQuery(queries.knowledge()).data?.items ?? []
  const byKey = new Map(items.map((i) => [i.doc_key, i]))
  const named = new Map(known.map((s) => [s.doc_key, s]))
  const toggle = (key: string) =>
    onChange(value.includes(key) ? value.filter((k) => k !== key) : [...value, key].slice(0, max))

  return (
    <div className="flex flex-col gap-2">
      {value.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {value.map((key) => {
            const item = byKey.get(key)
            const title = item?.title ?? named.get(key)?.title ?? key
            const restricted = (item?.access ?? named.get(key)?.access) === "restricted"
            const gone = !item && !!named.get(key)?.missing
            return (
              <li
                key={key}
                className="flex items-center gap-2 rounded-lg border border-border/70 px-2.5 py-1.5 text-sm"
              >
                <FileText className="size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">{title}</span>
                {restricted && (
                  <Badge variant="outline" className="gap-1 font-normal text-muted-foreground">
                    <Lock /> HR only
                  </Badge>
                )}
                {gone && (
                  <Badge variant="outline" className="font-normal text-destructive">
                    Not in the index
                  </Badge>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Remove ${title}`}
                  onClick={() => toggle(key)}
                >
                  <X />
                </Button>
              </li>
            )
          })}
        </ul>
      )}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button id={id} type="button" variant="outline" size="sm" className="self-start" disabled={value.length >= max}>
            <Plus data-icon="inline-start" /> Add a source
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[min(24rem,calc(100vw-2rem))] p-0">
          <Command>
            <CommandInput placeholder="Search documents…" />
            <CommandList>
              <CommandEmpty>No documents match.</CommandEmpty>
              <CommandGroup>
                {items.map((i) => (
                  <CommandItem
                    key={i.doc_key}
                    value={`${i.title} ${i.doc_key}`}
                    onSelect={() => toggle(i.doc_key)}
                  >
                    <Check className={cn("size-4", value.includes(i.doc_key) ? "opacity-100" : "opacity-0")} />
                    <span className="min-w-0 flex-1 truncate">{i.title}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">{i.source}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  )
}
