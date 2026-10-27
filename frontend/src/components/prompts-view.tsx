import { useState, type FormEvent } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router"
import { Building2, MessageSquareText, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { AsciiDivider } from "@/components/ascii/ascii-divider"
import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { Panel } from "@/components/glass/panel"
import { ScreenHeader } from "@/components/screen-header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { spaces as spacesApi, type SavedPrompt } from "@/lib/api"
import { INSERT_PROMPT, tellChat } from "@/lib/chat-events"
import { variablesOf } from "@/lib/prompts"
import { queries } from "@/lib/queries"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const BODY_CHARS = 2000

// Saved prompts (chat.md C22): your own, and the workspace's, which its admins
// keep. Typing "/" at the start of the chat box, or ⌘K, puts one in; a
// {{variable}} is asked for first.
export function PromptsView() {
  const queryClient = useQueryClient()
  const data = useQuery(queries.prompts()).data
  const [editing, setEditing] = useState<SavedPrompt | "new" | null>(null)
  const shared = data?.prompts.filter((p) => p.workspace) ?? []
  const mine = data?.prompts.filter((p) => !p.workspace) ?? []

  async function remove(p: SavedPrompt) {
    try {
      await spacesApi.deletePrompt(p.id)
      toast.success(`Deleted “${p.title}”.`)
      queryClient.invalidateQueries({ queryKey: ["prompts"] })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  // A list to act on: opaque, under a mono section label.
  const section = (title: string, list: SavedPrompt[], empty: string, note?: string) => (
    <section className="min-w-0">
      <AsciiDivider as="h3" label={title} className="mb-3" />
      <Panel tone="solid" className="divide-y overflow-hidden p-0">
        {list.length ? (
          list.map((p) => (
            <PromptRow key={p.id} prompt={p} onEdit={() => setEditing(p)} onDelete={() => remove(p)} />
          ))
        ) : (
          <AsciiEmptyState art="inbox" size="sm" headingLevel={4} title={empty} description={note} />
        )}
      </Panel>
    </section>
  )

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <ScreenHeader
        icon={MessageSquareText}
        title="Saved prompts"
        description={
          <>
            Questions you ask often. Type <kbd className="font-mono">/</kbd> in the chat box to use
            one; write <span className="font-mono">{"{{customer}}"}</span> for a part to fill in.
          </>
        }
        actions={
          <Button size="sm" onClick={() => setEditing("new")} disabled={!data}>
            <Plus data-icon="inline-start" /> New prompt
          </Button>
        }
      />
      {!data ? (
        <Skeleton className="mt-6 h-48 rounded-2xl" />
      ) : (
        <div className="mt-6 grid grid-cols-1 gap-6">
          {section("Yours", mine, "None yet.")}
          {section(
            "Your workspace's",
            shared,
            "None yet.",
            data.can_manage_workspace ? "Add one for everyone here." : undefined,
          )}
        </div>
      )}
      <PromptDialog
        prompt={editing}
        canShare={data?.can_manage_workspace ?? false}
        onOpenChange={(o) => !o && setEditing(null)}
      />
    </div>
  )
}

function PromptRow({
  prompt,
  onEdit,
  onDelete,
}: {
  prompt: SavedPrompt
  onEdit: () => void
  onDelete: () => void
}) {
  const navigate = useNavigate()
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      {prompt.workspace ? (
        <Building2 className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      ) : (
        <MessageSquareText className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{prompt.title}</p>
        <p className="line-clamp-2 text-sm text-muted-foreground">{prompt.body}</p>
        {prompt.variables.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {prompt.variables.map((v) => (
              <Badge key={v} variant="secondary" className="font-normal">
                {v}
              </Badge>
            ))}
          </div>
        )}
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          navigate("/chat")
          tellChat(INSERT_PROMPT, prompt.id)
        }}
      >
        Use
      </Button>
      {prompt.editable && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-8" aria-label={`${prompt.title}: more`}>
              <MoreHorizontal className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={onEdit}>
              <Pencil /> Edit
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onDelete} className="text-destructive">
              <Trash2 /> Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}

function PromptDialog({
  prompt,
  canShare,
  onOpenChange,
}: {
  prompt: SavedPrompt | "new" | null
  canShare: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const existing = prompt && prompt !== "new" ? prompt : null
  const [shown, setShown] = useState<string | null>(null)
  const [title, setTitle] = useState("")
  const [body, setBody] = useState("")
  const [shared, setShared] = useState(false)
  const [busy, setBusy] = useState(false)
  const key = prompt === null ? null : existing ? existing.id : "new"
  if (key !== shown) {
    setShown(key)
    setTitle(existing?.title ?? "")
    setBody(existing?.body ?? "")
    setShared(existing?.workspace ?? false)
  }
  const variables = variablesOf(body)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      if (existing) await spacesApi.editPrompt(existing.id, { title, body })
      else await spacesApi.createPrompt({ title, body, workspace: shared })
      queryClient.invalidateQueries({ queryKey: ["prompts"] })
      onOpenChange(false)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={prompt !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit} className="flex flex-col gap-6">
          <DialogHeader>
            <DialogTitle>{existing ? "Edit prompt" : "New prompt"}</DialogTitle>
            <DialogDescription>
              Write <span className="font-mono">{"{{name}}"}</span> where something changes each
              time: you'll be asked for it before the prompt goes in the chat box.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            <Field>
              <FieldLabel htmlFor="prompt-title">Name</FieldLabel>
              <Input
                id="prompt-title"
                autoComplete="off"
                maxLength={80}
                placeholder="Summarise a policy for a new joiner"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="prompt-body">Prompt</FieldLabel>
              <Textarea
                id="prompt-body"
                rows={5}
                maxLength={BODY_CHARS}
                placeholder="Summarise {{policy}} for someone who joined this week."
                value={body}
                onChange={(e) => setBody(e.target.value)}
              />
              <FieldDescription>
                {variables.length
                  ? `Asks for: ${variables.join(", ")}.`
                  : "No {{variables}}: it goes in as written."}
              </FieldDescription>
            </Field>
            {canShare && !existing && (
              <Field orientation="horizontal">
                <FieldContent>
                  <FieldLabel htmlFor="prompt-shared">For the whole workspace</FieldLabel>
                  <FieldDescription>Everyone here sees it; only admins can change it.</FieldDescription>
                </FieldContent>
                <Switch id="prompt-shared" checked={shared} onCheckedChange={setShared} />
              </Field>
            )}
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={busy} disabled={!title.trim() || !body.trim()}>
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
