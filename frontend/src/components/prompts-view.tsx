import { useState, type FormEvent } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router"
import {
  Building2,
  CornerDownLeft,
  MessageSquareText,
  MoreHorizontal,
  Pencil,
  Plus,
  SearchX,
  Trash2,
} from "lucide-react"
import { toast } from "sonner"

import {
  SettingsEmpty,
  SettingsError,
  SettingsExample,
  SettingsList,
  SettingsSection,
  SettingsSkeleton,
} from "@/components/settings/settings-section"
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
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
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
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { spaces as spacesApi, type SavedPrompt } from "@/lib/api"
import { INSERT_PROMPT, tellChat } from "@/lib/chat-events"
import { variablesOf } from "@/lib/prompts"
import { queries } from "@/lib/queries"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const BODY_CHARS = 2000

// Settings › Prompts (chat.md C22): saved prompts, your own and the workspace's, which
// its admins keep. Typing "/" at the start of the chat box, or ⌘K, puts one in; a
// {{variable}} is asked for first. Each is a row: what it's called and says, the parts
// to fill in, Use, and the rest in its menu.
export function PromptsView() {
  const queryClient = useQueryClient()
  const query = useQuery(queries.prompts())
  const data = query.data
  const [editing, setEditing] = useState<SavedPrompt | "new" | null>(null)
  const [deleting, setDeleting] = useState<SavedPrompt | null>(null)
  const shared = data?.prompts.filter((p) => p.workspace) ?? []
  const mine = data?.prompts.filter((p) => !p.workspace) ?? []
  const none = !!data && data.prompts.length === 0

  async function remove(p: SavedPrompt) {
    try {
      await spacesApi.deletePrompt(p.id)
      toast.success(`Deleted “${p.title}”.`)
      queryClient.invalidateQueries({ queryKey: ["prompts"] })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const list = (label: string, prompts: SavedPrompt[]) => (
    <div className="flex min-w-0 flex-col gap-2">
      <h4 className="text-sm font-medium text-muted-foreground">{label}</h4>
      <SettingsList label={label}>
        {prompts.map((p) => (
          <PromptRow key={p.id} prompt={p} onEdit={() => setEditing(p)} onDelete={() => setDeleting(p)} />
        ))}
      </SettingsList>
    </div>
  )

  return (
    <SettingsSection
      title="Prompts"
      description={
        <>
          Questions you ask often. Type <kbd className="font-mono">/</kbd> in the chat box to use one.
        </>
      }
      action={
        data && !none ? (
          <Button size="sm" onClick={() => setEditing("new")}>
            <Plus data-icon="inline-start" /> New prompt
          </Button>
        ) : undefined
      }
    >
      {query.isPending ? (
        <SettingsSkeleton label="Loading your prompts" />
      ) : !data ? (
        <SettingsError what="your prompts" onRetry={() => void query.refetch()} />
      ) : none ? (
        <SettingsEmpty
          icon={MessageSquareText}
          title="No saved prompts yet"
          description="Save a question you ask often, with blanks to fill in, and reuse it from the chat box with /."
          action={
            <Button size="sm" onClick={() => setEditing("new")}>
              <Plus data-icon="inline-start" /> New prompt
            </Button>
          }
          example={
            <SettingsExample>
              <p className="font-medium">Summarise a policy for a new joiner</p>
              <p className="mt-0.5 text-muted-foreground">
                Summarise <span className="font-mono text-foreground">{"{{policy}}"}</span> for someone
                who joined this week, in five bullets.
              </p>
            </SettingsExample>
          }
        />
      ) : (
        <>
          {mine.length > 0
            ? list("Yours", mine)
            : (
              <p className="text-sm text-muted-foreground">
                You haven't saved one of your own yet.{" "}
                <Button variant="link" size="sm" className="h-auto p-0" onClick={() => setEditing("new")}>
                  New prompt
                </Button>
              </p>
            )}
          {shared.length > 0 && list("For everyone in the workspace", shared)}
        </>
      )}
      <PromptDialog
        prompt={editing}
        canShare={data?.can_manage_workspace ?? false}
        onOpenChange={(o) => !o && setEditing(null)}
      />
      <AlertDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.title}”?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleting?.workspace
                ? "It's gone for everyone in the workspace. This can't be undone."
                : "This can't be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => deleting && remove(deleting)}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SettingsSection>
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
  const use = () => {
    navigate("/chat")
    tellChat(INSERT_PROMPT, prompt.id)
  }
  const Icon = prompt.workspace ? Building2 : MessageSquareText
  return (
    <li className="flex items-start gap-3 px-4 py-3">
      <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <Icon className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{prompt.title}</p>
        <p className="line-clamp-2 text-sm text-pretty text-muted-foreground">{prompt.body}</p>
        {(prompt.variables.length > 0 || !prompt.search) && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {!prompt.search && (
              <Badge variant="outline" className="font-normal text-muted-foreground">
                <SearchX /> No search
              </Badge>
            )}
            {prompt.variables.map((v) => (
              <Badge key={v} variant="secondary" className="font-mono font-normal">
                {v}
              </Badge>
            ))}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button variant="ghost" size="sm" onClick={use} className="max-sm:hidden">
          Use
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label={`${prompt.title}: actions`}>
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuGroup>
              <DropdownMenuItem onSelect={use}>
                <CornerDownLeft /> Use in a chat
              </DropdownMenuItem>
              {prompt.editable && (
                <DropdownMenuItem onSelect={onEdit}>
                  <Pencil /> Edit
                </DropdownMenuItem>
              )}
            </DropdownMenuGroup>
            {prompt.editable && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onSelect={onDelete}>
                  <Trash2 /> Delete
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </li>
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
  const [search, setSearch] = useState(true)
  const [busy, setBusy] = useState(false)
  const key = prompt === null ? null : existing ? existing.id : "new"
  if (key !== shown) {
    setShown(key)
    setTitle(existing?.title ?? "")
    setBody(existing?.body ?? "")
    setShared(existing?.workspace ?? false)
    setSearch(existing?.search ?? true)
  }
  const variables = variablesOf(body)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      if (existing) await spacesApi.editPrompt(existing.id, { title, body, search })
      else await spacesApi.createPrompt({ title, body, workspace: shared, search })
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
            <Field orientation="horizontal">
              <FieldContent>
                <FieldLabel htmlFor="prompt-search">Search knowledge</FieldLabel>
                <FieldDescription>
                  Answer from the workspace's documents, with sources. Turn off for prompts that
                  don't need them, like rewriting text.
                </FieldDescription>
              </FieldContent>
              <Switch id="prompt-search" checked={search} onCheckedChange={setSearch} />
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
