import { useRef, useState, type FormEvent } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "react-router"
import {
  ArrowLeft,
  ChevronRight,
  FileText,
  Folder,
  Layers,
  Loader2,
  MessageSquare,
  Plug,
  Plus,
  Trash2,
  Upload,
  X,
} from "lucide-react"
import { toast } from "sonner"

import { SpaceIcon } from "@/components/space-switcher"
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
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  spaces as spacesApi,
  type SourceMatch,
  type SpaceDetail,
  type SpaceRole,
  type SpaceSource,
} from "@/lib/api"
import { useUser } from "@/lib/auth-context"
import { CHOOSE_SPACE, tellChat } from "@/lib/chat-events"
import { queries } from "@/lib/queries"
import { fileSize, spaceLabel } from "@/lib/spaces"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const INSTRUCTIONS_CHARS = 1500

const MATCH: Record<SourceMatch, { label: string; icon: typeof Folder }> = {
  provider: { label: "Source", icon: Plug },
  folder: { label: "Folder", icon: Folder },
  doc_key: { label: "Document", icon: FileText },
}

// Spaces (chat.md C31): /spaces lists yours, /spaces/:id is one of them. A team
// space's owners choose what it searches, write its instructions and add people;
// your own space keeps your files. Asking in one searches only what's in it that
// you may see.
export function SpacesView({ spaceId }: { spaceId?: string }) {
  return spaceId ? <SpaceScreen spaceId={spaceId} /> : <SpaceList />
}

// Make new chats ask in this space, and open one.
function useAskIn() {
  const navigate = useNavigate()
  return (id: string) => {
    navigate("/chat")
    tellChat(CHOOSE_SPACE, id)
  }
}

function SpaceList() {
  const data = useQuery(queries.spaces()).data
  const [creating, setCreating] = useState(false)
  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Layers className="size-6" /> Spaces
          </h2>
          <p className="text-sm text-muted-foreground">
            Ask in a space to search only its sources. Being in one never shows you a document you
            can't otherwise see.
          </p>
        </div>
        {data?.can_create && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus data-icon="inline-start" /> New team space
          </Button>
        )}
      </div>
      {!data ? (
        <Skeleton className="mt-6 h-48 rounded-2xl" />
      ) : (
        <section className="mt-6 divide-y rounded-2xl border border-border/70 bg-background">
          {data.spaces.map((s) => (
            <Link
              key={s.id}
              to={`/spaces/${s.id}`}
              className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/50"
            >
              <SpaceIcon space={s} className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">{spaceLabel(s)}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {s.kind === "personal"
                    ? "Your files, searched only by you"
                    : s.description || (s.my_role === "owner" ? "You own it" : "You're a member")}
                </p>
              </div>
              {s.kind === "team" && (
                <Badge variant="outline" className="shrink-0 font-normal">
                  {s.my_role === "owner" ? "Owner" : "Member"}
                </Badge>
              )}
              <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
            </Link>
          ))}
        </section>
      )}
      <NewSpaceDialog open={creating} onOpenChange={setCreating} />
    </div>
  )
}

function NewSpaceDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setBusy(true)
    try {
      const made = await spacesApi.createSpace({ name, description })
      queryClient.invalidateQueries({ queryKey: ["spaces"] })
      onOpenChange(false)
      setName("")
      setDescription("")
      navigate(`/spaces/${made.id}`)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="flex flex-col gap-6">
          <DialogHeader>
            <DialogTitle>New team space</DialogTitle>
            <DialogDescription>
              Then choose what it searches and who's in it. You'll be its owner.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            <Field>
              <FieldLabel htmlFor="space-name">Name</FieldLabel>
              <Input
                id="space-name"
                autoComplete="off"
                placeholder="Sales"
                maxLength={60}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="space-description">Description</FieldLabel>
              <Input
                id="space-description"
                autoComplete="off"
                placeholder="Pricing decks and CRM notes"
                maxLength={200}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy && <Loader2 className="animate-spin" data-icon="inline-start" />}
              Create space
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function SpaceScreen({ spaceId }: { spaceId: string }) {
  const space = useQuery({ ...queries.space(spaceId), retry: false })
  const askIn = useAskIn()
  const data = space.data
  const owner = data?.my_role === "owner"

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <Button asChild variant="ghost" size="sm" className="-ml-2 mb-2 text-muted-foreground">
        <Link to="/spaces">
          <ArrowLeft data-icon="inline-start" /> Spaces
        </Link>
      </Button>
      {space.isError ? (
        <p className="text-sm text-muted-foreground">
          That space doesn't exist, or you're not in it.
        </p>
      ) : !data ? (
        <Skeleton className="h-48 rounded-2xl" />
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
                <SpaceIcon space={data} className="size-6 shrink-0" />
                <span className="truncate">{spaceLabel(data)}</span>
              </h2>
              <p className="text-sm text-muted-foreground">
                {data.kind === "personal"
                  ? "Only you can search your space: your files, and any sources you add."
                  : data.description || "A team space: its sources, for its members."}
              </p>
            </div>
            <Button size="sm" onClick={() => askIn(data.id)}>
              <MessageSquare data-icon="inline-start" /> Ask in this space
            </Button>
          </div>
          <Tabs defaultValue={data.kind === "personal" ? "files" : "sources"} className="mt-6">
            <TabsList>
              {data.kind === "personal" && <TabsTrigger value="files">Files</TabsTrigger>}
              <TabsTrigger value="sources">Sources</TabsTrigger>
              <TabsTrigger value="instructions">Instructions</TabsTrigger>
              {data.kind === "team" && <TabsTrigger value="members">Members</TabsTrigger>}
            </TabsList>
            {data.kind === "personal" && (
              <TabsContent value="files" className="mt-4">
                <FilesTab space={data} />
              </TabsContent>
            )}
            <TabsContent value="sources" className="mt-4">
              <SourcesTab space={data} owner={owner} />
            </TabsContent>
            <TabsContent value="instructions" className="mt-4">
              <InstructionsTab space={data} owner={owner} />
            </TabsContent>
            {data.kind === "team" && (
              <TabsContent value="members" className="mt-4">
                <MembersTab space={data} owner={owner} />
              </TabsContent>
            )}
          </Tabs>
          {owner && data.kind === "team" && <DeleteSpace space={data} />}
        </>
      )}
    </div>
  )
}

function useReload() {
  const queryClient = useQueryClient()
  return () => {
    queryClient.invalidateQueries({ queryKey: ["spaces"] })
    queryClient.invalidateQueries({ queryKey: ["source-options"] })
  }
}

function FilesTab({ space }: { space: SpaceDetail }) {
  const reload = useReload()
  const input = useRef<HTMLInputElement>(null)
  const limits = useQuery({ ...queries.attachmentLimits(), meta: { silent: true } }).data
  const [busy, setBusy] = useState(false)

  async function upload(files: File[]) {
    setBusy(true)
    try {
      await spacesApi.addSpaceFiles(space.id, files)
      toast.success(files.length === 1 ? `Added ${files[0].name}.` : `Added ${files.length} files.`)
      reload()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string, name: string) {
    try {
      await spacesApi.removeSpaceFile(space.id, id)
      toast.success(`Removed ${name}.`)
      reload()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <section className="rounded-2xl border border-border/70 bg-background">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 px-4 py-3">
        <p className="text-sm text-muted-foreground">
          Kept as text for your questions in this space, never added to the company's search.
        </p>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? <Loader2 className="animate-spin" data-icon="inline-start" /> : <Upload data-icon="inline-start" />}
          Add files
        </Button>
        <Input
          ref={input}
          type="file"
          multiple
          accept={limits?.types.join(",")}
          className="sr-only"
          tabIndex={-1}
          aria-hidden
          onChange={(e) => {
            const files = [...(e.target.files ?? [])]
            e.target.value = ""
            if (files.length) upload(files)
          }}
        />
      </div>
      {space.files?.length ? (
        <div className="divide-y">
          {space.files.map((f) => (
            <div key={f.id} className="flex items-center gap-3 px-4 py-2.5">
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-sm">{f.filename}</span>
              <span className="shrink-0 text-xs text-muted-foreground">{fileSize(f.size)}</span>
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label={`Remove ${f.filename}`}
                onClick={() => remove(f.id, f.filename)}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <p className="px-4 py-6 text-center text-sm text-muted-foreground">
          No files yet. Add PDFs, Word, Excel, PowerPoint or text files.
        </p>
      )}
    </section>
  )
}

function SourcesTab({ space, owner }: { space: SpaceDetail; owner: boolean }) {
  const reload = useReload()

  async function remove(s: SpaceSource) {
    try {
      await spacesApi.removeSource(space.id, s.id)
      reload()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <section className="rounded-2xl border border-border/70 bg-background">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 px-4 py-3">
        <p className="text-sm text-muted-foreground">
          {space.kind === "personal"
            ? "Company sources searched along with your files."
            : "What questions in this space search. Each person still sees only what they may."}
        </p>
        {owner && <AddSource space={space} onAdded={reload} />}
      </div>
      {space.sources.length ? (
        <div className="divide-y">
          {space.sources.map((s) => {
            const Icon = MATCH[s.match_type].icon
            return (
              <div key={s.id} className="flex items-center gap-3 px-4 py-2.5">
                <Icon className="size-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{s.match_value}</p>
                  <p className="text-xs text-muted-foreground">
                    {MATCH[s.match_type].label} · {s.documents} document{s.documents === 1 ? "" : "s"} you can see
                  </p>
                </div>
                {owner && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8"
                    aria-label={`Remove ${s.match_value}`}
                    onClick={() => remove(s)}
                  >
                    <X className="size-4" />
                  </Button>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        <p className="px-4 py-6 text-center text-sm text-muted-foreground">
          {space.kind === "personal"
            ? "None: questions here search only your files."
            : "No sources yet, so questions here find nothing."}
        </p>
      )}
      {space.hidden_sources > 0 && (
        <p className="border-t border-border/70 px-4 py-2.5 text-xs text-muted-foreground">
          {space.hidden_sources} more source{space.hidden_sources === 1 ? "" : "s"} you can't see
          {space.hidden_sources === 1 ? " is" : " are"} in this space; questions here never read
          {space.hidden_sources === 1 ? " it" : " them"} for you.
        </p>
      )}
    </section>
  )
}

function AddSource({ space, onAdded }: { space: SpaceDetail; onAdded: () => void }) {
  const [open, setOpen] = useState(false)
  const options = useQuery({ ...queries.sourceOptions(), enabled: open })
  const have = new Set(space.sources.map((s) => `${s.match_type}:${s.match_value}`))

  async function add(type: SourceMatch, value: string) {
    setOpen(false)
    try {
      await spacesApi.addSource(space.id, type, value)
      onAdded()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const groups: { type: SourceMatch; heading: string }[] = [
    { type: "provider", heading: "Sources" },
    { type: "folder", heading: "Folders" },
    { type: "doc_key", heading: "Documents" },
  ]
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="sm" variant="outline">
          <Plus data-icon="inline-start" /> Add source
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <Command>
          <CommandInput placeholder="Search sources, folders, documents…" />
          <CommandList>
            <CommandEmpty>{options.isPending ? "Loading…" : "Nothing found."}</CommandEmpty>
            {options.data &&
              groups.map(({ type, heading }) => {
                const items = options.data[type].filter((o) => !have.has(`${type}:${o.value}`))
                if (!items.length) return null
                const Icon = MATCH[type].icon
                return (
                  <CommandGroup key={type} heading={heading}>
                    {items.map((o) => (
                      <CommandItem
                        key={o.value}
                        value={`${type} ${o.value} ${o.label}`}
                        onSelect={() => add(type, o.value)}
                      >
                        <Icon />
                        <span className="min-w-0 flex-1 truncate">{o.label}</span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {o.documents !== undefined ? o.documents : o.provider}
                        </span>
                      </CommandItem>
                    ))}
                  </CommandGroup>
                )
              })}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

function InstructionsTab({ space, owner }: { space: SpaceDetail; owner: boolean }) {
  const reload = useReload()
  const [text, setText] = useState(space.instructions)
  const [busy, setBusy] = useState(false)
  const changed = text.trim() !== space.instructions.trim()

  async function save(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      await spacesApi.editSpace(space.id, { instructions: text })
      toast.success("Saved the space's instructions.")
      reload()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={save} className="rounded-2xl border border-border/70 bg-background p-5">
      <Field>
        <FieldLabel htmlFor="space-instructions">How the assistant answers in this space</FieldLabel>
        <Textarea
          id="space-instructions"
          rows={5}
          readOnly={!owner}
          maxLength={INSTRUCTIONS_CHARS}
          placeholder={owner ? "Answer as a sales engineer. Keep answers short." : "None."}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <FieldDescription>
          Added after the assistant's own rules, which it keeps: it still cites its sources and never
          shows anyone what they may not see. {text.length} / {INSTRUCTIONS_CHARS}
        </FieldDescription>
      </Field>
      {owner && (
        <div className="mt-4 flex justify-end">
          <Button type="submit" size="sm" disabled={busy || !changed}>
            {busy && <Loader2 className="animate-spin" data-icon="inline-start" />}
            Save
          </Button>
        </div>
      )}
    </form>
  )
}

function MembersTab({ space, owner }: { space: SpaceDetail; owner: boolean }) {
  const reload = useReload()
  const me = useUser().username
  const navigate = useNavigate()
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<SpaceRole>("member")
  const [busy, setBusy] = useState(false)

  async function add(e: FormEvent) {
    e.preventDefault()
    if (!email.trim()) return
    setBusy(true)
    try {
      await spacesApi.setMember(space.id, email.trim(), role)
      setEmail("")
      reload()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function change(username: string, to: SpaceRole) {
    try {
      await spacesApi.setMember(space.id, username, to)
      reload()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function remove(username: string) {
    try {
      await spacesApi.removeMember(space.id, username)
      if (username === me) {
        toast.success(`You left ${space.name}.`)
        navigate("/spaces")
      }
      reload()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <section className="rounded-2xl border border-border/70 bg-background">
      {owner && (
        <form onSubmit={add} className="flex flex-wrap items-end gap-2 border-b border-border/70 px-4 py-3">
          <Field className="min-w-48 flex-1">
            <FieldLabel htmlFor="space-member" className="sr-only">
              Email
            </FieldLabel>
            <Input
              id="space-member"
              type="email"
              autoComplete="off"
              placeholder="name@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>
          <Select value={role} onValueChange={(v) => setRole(v as SpaceRole)}>
            <SelectTrigger className="w-28" aria-label="Role in the space">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="member">Member</SelectItem>
              <SelectItem value="owner">Owner</SelectItem>
            </SelectContent>
          </Select>
          <Button type="submit" disabled={busy || !email.trim()}>
            Add
          </Button>
        </form>
      )}
      <div className="divide-y">
        {space.members.map((m) => (
          <div key={m.username} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
            <span className="min-w-0 flex-1 truncate text-sm">
              {m.username}
              {m.username === me && <span className="text-muted-foreground"> (you)</span>}
            </span>
            {owner ? (
              <Select value={m.role} onValueChange={(v) => change(m.username, v as SpaceRole)}>
                <SelectTrigger size="sm" className="w-28" aria-label={`${m.username}'s role`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="member">Member</SelectItem>
                  <SelectItem value="owner">Owner</SelectItem>
                </SelectContent>
              </Select>
            ) : (
              <Badge variant="outline" className="font-normal">
                {m.role === "owner" ? "Owner" : "Member"}
              </Badge>
            )}
            {(owner || m.username === me) && (
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground"
                onClick={() => remove(m.username)}
              >
                {m.username === me ? "Leave" : "Remove"}
              </Button>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function DeleteSpace({ space }: { space: SpaceDetail }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)

  async function remove() {
    try {
      await spacesApi.deleteSpace(space.id)
      queryClient.invalidateQueries({ queryKey: ["spaces"] })
      toast.success(`Deleted ${space.name}.`)
      navigate("/spaces")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <div className="mt-8 flex justify-end">
      <Button variant="ghost" size="sm" className="text-destructive" onClick={() => setOpen(true)}>
        <Trash2 data-icon="inline-start" /> Delete space
      </Button>
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{space.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Its sources, members and instructions go. Chats asked in it stay, and search
              everything their owners may see from then on.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={remove}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
