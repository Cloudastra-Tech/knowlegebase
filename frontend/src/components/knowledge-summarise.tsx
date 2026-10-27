import { useMemo } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { FilePen, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { drafts, type KnowledgeItem } from "@/lib/api"

// "Summarise this source / folder" (platform.md PL-31), HR and admins: the assistant
// writes a cited overview from each document's summary, as a draft for review on the
// Documents screen. It's never published by itself.

const MAX_FOLDERS = 8 // per source, in the menu

function folderLabel(provider: string, folder: string) {
  if (provider === "uploads") return folder === "restricted" ? "HR-only files" : "Public files"
  return `Folder ${folder}`
}

export function SummariseMenu({
  items,
  source,
  onOpenDraft,
}: {
  items: KnowledgeItem[]
  source: string
  onOpenDraft?: (id: string) => void // the Documents screen's drafts tab
}) {
  const queryClient = useQueryClient()
  // Each source (provider) with its folders, as the list shows them.
  const sources = useMemo(() => {
    const out = new Map<string, { label: string; folders: Set<string> }>()
    for (const i of items) {
      if (source !== "all" && i.source !== source) continue
      const entry = out.get(i.provider) ?? { label: i.source, folders: new Set<string>() }
      if (i.folder) entry.folders.add(i.folder)
      out.set(i.provider, entry)
    }
    return [...out.entries()]
  }, [items, source])

  const start = useMutation({
    mutationFn: ({ provider, folder }: { provider: string; folder?: string }) =>
      drafts.summarise(provider, folder),
    onSuccess: (draft) => {
      void queryClient.invalidateQueries({ queryKey: ["drafts"] })
      toast.success("Writing a summary. It's in Documents, under Drafts.", {
        action: onOpenDraft ? { label: "Open", onClick: () => onOpenDraft(draft.id) } : undefined,
      })
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't start it."),
  })

  if (!sources.length) return null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" disabled={start.isPending}>
          {start.isPending ? (
            <Loader2 className="animate-spin" data-icon="inline-start" />
          ) : (
            <FilePen data-icon="inline-start" />
          )}
          Summarise
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        {sources.map(([provider, { label, folders }], n) => (
          <DropdownMenuGroup key={provider}>
            {n > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
              {label}
            </DropdownMenuLabel>
            <DropdownMenuItem onSelect={() => start.mutate({ provider })}>
              Summarise everything from {label}
            </DropdownMenuItem>
            {[...folders].slice(0, MAX_FOLDERS).map((folder) => (
              <DropdownMenuItem key={folder} onSelect={() => start.mutate({ provider, folder })}>
                <span className="truncate">Summarise {folderLabel(provider, folder)}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
