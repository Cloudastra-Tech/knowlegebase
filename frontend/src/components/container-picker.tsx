import { useEffect, useState } from "react"
import { toast } from "sonner"
import {
  BookMarked,
  BookOpen,
  Building2,
  CalendarDays,
  ChevronRight,
  Cloud,
  FileSpreadsheet,
  FileText,
  FileType,
  Folder,
  FolderGit2,
  FolderKanban,
  Globe,
  Hash,
  HardDrive,
  Library,
  Loader2,
  Plus,
  Search,
  Tag,
  Users,
  X,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import { api, type Container, type ConnectorOptions, type Integration } from "@/lib/api"
import { MEMBER_OPTIONS, TICKET_OPTIONS } from "@/lib/ticket-options"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

export function ContainerIcon({ item, className = "size-4" }: { item: Container; className?: string }) {
  const cls = `${className} shrink-0 text-muted-foreground`
  if (item.kind === "drive") return <HardDrive className={cls} />
  if (item.kind === "folder") return <Folder className={cls} />
  if (item.kind === "collection") return <BookOpen className={cls} />
  if (item.kind === "space") return <BookMarked className={cls} />
  if (item.kind === "site") return <Building2 className={cls} />
  if (item.kind === "onedrive") return <Cloud className={cls} />
  if (item.kind === "url") return <Globe className={cls} />
  if (item.kind === "project") return <FolderKanban className={cls} />
  if (item.kind === "category") return <Library className={cls} />
  if (item.kind === "section") return <BookOpen className={cls} />
  if (item.kind === "repo") return <FolderGit2 className={cls} />
  if (item.kind === "team") return <Users className={cls} />
  if (item.kind === "record_type" || item.kind === "label") return <Tag className={cls} />
  if (item.kind === "channel") return <Hash className={cls} />
  if (item.kind === "calendar") return <CalendarDays className={cls} />
  if (item.mimeType?.includes("spreadsheet")) return <FileSpreadsheet className={cls} />
  if (item.mimeType === "application/pdf") return <FileType className={cls} />
  return <FileText className={cls} />
}

// What the dialog says for each connector's picker.
// `all`: what an optional picker syncs with nothing ticked ("everything shared" otherwise).
const COPY: Record<
  string,
  { title: string; description: string; root: string; empty: string; all?: string }
> = {
  google_drive: {
    title: "Choose what to sync",
    description:
      "Tick whole folders (sub-folders and new files included) or single files. Only what you pick is synced.",
    root: "My Drive",
    empty: "No folders or supported files here.",
  },
  notion: {
    title: "Choose pages to sync",
    description:
      "Tick the pages to sync; their sub-pages come with them. With none ticked, every page shared with the integration is synced.",
    root: "Shared pages",
    empty: "No pages are shared with the integration yet. Share some in Notion first.",
  },
  confluence: {
    title: "Choose spaces to sync",
    description:
      "Tick the spaces to sync: their pages, blog posts and attachments. Nothing is synced until you pick one.",
    root: "Spaces",
    empty: "No spaces this account can read.",
  },
  confluence_dc: {
    title: "Choose spaces to sync",
    description:
      "Tick the spaces to sync: their pages, blog posts and attachments. Nothing is synced until you pick one.",
    root: "Spaces",
    empty: "No spaces this token can read.",
  },
  sharepoint: {
    title: "Choose sites and libraries",
    description:
      "Tick whole sites (every document library in them, new ones included) or open a site to tick single libraries. Only what you pick is synced.",
    root: "Sites",
    empty: "No sites or libraries here.",
  },
  jira: {
    title: "Choose projects to sync",
    description:
      "Tick the projects to sync: their issues with comments and key fields. Nothing is synced until you pick one.",
    root: "Projects",
    empty: "No projects this account can browse.",
  },
  jira_dc: {
    title: "Choose projects to sync",
    description:
      "Tick the projects to sync: their issues with comments and key fields. Nothing is synced until you pick one.",
    root: "Projects",
    empty: "No projects this token can browse.",
  },
  zendesk: {
    title: "Choose what to sync",
    description:
      "Tick whole categories or open one to tick sections (their sub-sections come with them). With none ticked, every published Help Center article is synced.",
    root: "Categories",
    empty: "No categories or sections here.",
    all: "every published Help Center article",
  },
  intercom: {
    title: "Choose what to sync",
    description:
      "Tick whole Help Centers or open one to tick collections (their sub-collections come with them). With none ticked, every published article is synced.",
    root: "Help Centers",
    empty: "No collections here.",
    all: "every published article",
  },
  freshdesk: {
    title: "Choose what to sync",
    description:
      "Tick whole categories or open one to tick folders (their sub-folders come with them). With none ticked, every published knowledge base article is synced.",
    root: "Categories",
    empty: "No folders here.",
    all: "every published knowledge base article",
  },
  hubspot: {
    title: "Choose what to sync",
    description:
      "Tick whole knowledge bases or open one to tick categories (their subcategories come with them). With none ticked, every published article is synced.",
    root: "Knowledge bases",
    empty: "No categories here.",
    all: "every published article",
  },
  salesforce: {
    title: "Choose article types",
    description:
      "Tick the article record types to sync. With none ticked, every published Knowledge article is synced.",
    root: "Record types",
    empty: "This org has no article record types: every published article is synced.",
    all: "every published Knowledge article",
  },
  github: {
    title: "Choose repositories",
    description:
      "Tick the repositories to sync: their READMEs and docs (Markdown, reStructuredText, text). Turn on code for a repository to sync its source files too.",
    root: "Repositories",
    empty: "No repositories are shared with the app. Add some in its installation settings on GitHub.",
  },
  linear: {
    title: "Choose teams to sync",
    description:
      "Tick the teams to sync: their issues with comments, and their projects, project updates and documents. Nothing is synced until you pick one.",
    root: "Teams",
    empty: "No teams this account can see.",
  },
  dropbox: {
    title: "Choose folders to sync",
    description:
      "Tick whole folders (sub-folders and new files included). Nothing is synced until you pick one.",
    root: "Dropbox",
    empty: "No folders here.",
  },
  box: {
    title: "Choose folders to sync",
    description:
      "Tick whole folders (sub-folders and new files included). Nothing is synced until you pick one.",
    root: "All files",
    empty: "No folders here.",
  },
  s3: {
    title: "Choose folders to sync",
    description:
      "Tick folders (prefixes) of the bucket to sync only those. With none ticked, everything under the connection's prefix is synced.",
    root: "Bucket",
    empty: "No folders here.",
    all: "everything under the connection's prefix",
  },
  asana: {
    title: "Choose projects to sync",
    description:
      "Tick whole workspaces (every project in them, new ones included) or open one to tick single projects: their tasks with comments, and project briefs. Nothing is synced until you pick one.",
    root: "Workspaces",
    empty: "No projects this account can see.",
  },
  clickup: {
    title: "Choose what to sync",
    description:
      "Tick a whole Workspace, or open it to tick Spaces, Folders or Lists: their tasks with comments, and the Docs inside them. Nothing is synced until you pick one.",
    root: "Workspaces",
    empty: "Nothing here.",
  },
  coda: {
    title: "Choose docs to sync",
    description:
      "Tick the docs to sync: every page in them, as text. Only docs the connected account has opened are listed. Nothing is synced until you pick one.",
    root: "Docs",
    empty: "No docs this token can open.",
  },
  teams_messages: {
    title: "Choose channels to sync",
    description:
      "Tick whole teams (every channel, new ones included) or open a team to tick single channels: their conversations, with replies. Chats are never read.",
    root: "Teams",
    empty: "No channels here.",
  },
  gmail: {
    title: "Choose labels to sync",
    description:
      "Tick the labels whose conversations you want to ask about (the last six months). Only you can ask about them. Spam, trash and drafts are never synced.",
    root: "Labels",
    empty: "No labels here.",
  },
  google_calendar: {
    title: "Choose calendars to sync",
    description:
      "Tick the calendars whose events you want to ask about: titles, times, places, attendees and descriptions. Only you can ask about them.",
    root: "Calendars",
    empty: "No calendars here.",
  },
  outline: {
    title: "Choose collections to sync",
    description: "Tick the wiki collections to sync. Nothing is synced until you pick one.",
    root: "Collections",
    empty: "No collections this token can read.",
  },
}

const copyFor = (item: Integration) =>
  COPY[item.id] ?? {
    title: `Choose what to sync from ${item.name}`,
    description: "Tick what should be searchable.",
    root: item.name,
    empty: "Nothing to pick here.",
  }

// Pick what a connection syncs: a folder tree (Drive), a list (Notion pages,
// Outline collections) or a list of addresses (a website). One dialog for all.
export function ContainerPicker({
  item,
  open,
  onOpenChange,
  onSaved,
}: {
  item: Integration
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  if (item.picker === "urls")
    return <UrlPicker item={item} open={open} onOpenChange={onOpenChange} onSaved={onSaved} />
  return <TreePicker item={item} open={open} onOpenChange={onOpenChange} onSaved={onSaved} />
}

function TreePicker({
  item,
  open,
  onOpenChange,
  onSaved,
}: {
  item: Integration
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const initial = item.containers ?? []
  const tree = item.picker === "tree"
  const copy = copyFor(item)
  const [path, setPath] = useState<Container[]>([]) // breadcrumb; [] = top level
  // What's inside the level being shown, tagged with its parent so a stale
  // listing never shows while the next one loads (null = loading).
  const [listing, setListing] = useState<{ parent?: string; items: Container[] } | null>(null)
  const [selected, setSelected] = useState<Container[]>(initial)
  const [filter, setFilter] = useState("")
  const [saving, setSaving] = useState(false)
  // The connector's own options (tickets or conversations, GitHub code file types), if any.
  const [options, setOptions] = useState<ConnectorOptions | undefined>(item.options)
  const [extensions, setExtensions] = useState("")

  // Each time the dialog opens: start from the saved picks, at the top level.
  const [wasOpen, setWasOpen] = useState(false)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setSelected(initial)
      setPath([])
      setListing(null)
      setFilter("")
      setOptions(item.options)
      setExtensions((item.options?.code_extensions ?? []).join(", "))
    }
  }
  const codeToggles = item.id === "github"
  const tickets = TICKET_OPTIONS[item.id]
  const members = MEMBER_OPTIONS[item.id]
  const anyCode = selected.some((s) => s.code)

  const parent = path.at(-1)?.id
  const items = listing && listing.parent === parent ? listing.items : null
  useEffect(() => {
    if (!open) return
    let cancelled = false
    ;(item.personal
      ? api.myIntegrationContainers(item.id)
      : api.integrationContainers(item.id, parent)
    )
      .then(({ items }) => !cancelled && setListing({ parent, items }))
      .catch((err) => {
        toast.error(errorText(err))
        if (!cancelled) setListing({ parent, items: [] })
      })
    return () => {
      cancelled = true
    }
  }, [open, parent, item.id, item.personal])

  const isPicked = (f: Container) => selected.some((s) => s.id === f.id)
  // Already covered because a folder above it in the breadcrumb is picked.
  const coveredByParent = path.some((p) => selected.some((s) => s.id === p.id))
  const shown = (items ?? []).filter(
    (f) => !filter || f.name.toLowerCase().includes(filter.toLowerCase()),
  )

  function toggle(f: Container) {
    setSelected((cur) =>
      isPicked(f) ? cur.filter((s) => s.id !== f.id) : [...cur, codeToggles ? { ...f, code: false } : f],
    )
  }

  function setCode(f: Container, code: boolean) {
    setSelected((cur) => cur.map((s) => (s.id === f.id ? { ...s, code } : s)))
  }

  async function save() {
    setSaving(true)
    try {
      const saved: ConnectorOptions | undefined = !options
        ? undefined
        : codeToggles
          ? { code_extensions: extensions.split(",").map((e) => e.trim()).filter(Boolean) }
          : options
      if (item.personal) await api.setMyIntegrationContainers(item.id, selected)
      else await api.setIntegrationContainers(item.id, selected, undefined, saved)
      toast.success(
        selected.length
          ? `Syncing ${selected.length} item${selected.length === 1 ? "" : "s"}…`
          : item.picker_required
            ? `Nothing selected. ${item.name} items were removed from search.`
            : `Syncing ${copy.all ?? `everything shared with ${item.name}`}…`,
      )
      onSaved()
      onOpenChange(false)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{copy.title}</DialogTitle>
          <DialogDescription>{copy.description}</DialogDescription>
        </DialogHeader>

        {tree ? (
          <div className="flex flex-wrap items-center gap-1 text-sm">
            <Button variant="ghost" size="xs" onClick={() => setPath([])}>
              {copy.root}
            </Button>
            {path.map((p, i) => (
              <span key={p.id} className="flex items-center gap-1">
                <ChevronRight className="size-3.5 text-muted-foreground" />
                <Button
                  variant="ghost"
                  size="xs"
                  className="max-w-40"
                  onClick={() => setPath(path.slice(0, i + 1))}
                >
                  <span className="truncate">{p.name}</span>
                </Button>
              </span>
            ))}
          </div>
        ) : (
          (items?.length ?? 0) > 8 && (
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter"
                aria-label="Filter"
                className="pl-8"
              />
            </div>
          )
        )}

        <div className="h-72 overflow-y-auto rounded-xl border">
          {items === null ? (
            <div className="space-y-2 p-3">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-2/3" />
            </div>
          ) : shown.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted-foreground">{copy.empty}</p>
          ) : (
            shown.map((f) => (
              <div
                key={f.id}
                className="flex items-center gap-3 border-b px-3 py-2 last:border-b-0 hover:bg-muted/50"
              >
                <Checkbox
                  id={`pick-${f.id}`}
                  checked={coveredByParent || isPicked(f)}
                  disabled={coveredByParent}
                  onCheckedChange={() => toggle(f)}
                  aria-label={`Sync ${f.name}`}
                />
                <ContainerIcon item={f} />
                {tree && f.has_children ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setPath([...path, f])}
                    className="h-auto min-w-0 flex-1 justify-between px-1 py-0.5 font-normal"
                    title="Open folder"
                  >
                    <span className="truncate">
                      {f.name}
                      {f.kind === "drive" && (
                        <span className="ml-1.5 text-xs text-muted-foreground">Shared Drive</span>
                      )}
                    </span>
                    <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                  </Button>
                ) : (
                  <label
                    htmlFor={`pick-${f.id}`}
                    className="min-w-0 flex-1 cursor-pointer truncate text-sm"
                  >
                    {f.name}
                    {f.private && (
                      <span className="ml-1.5 text-xs text-muted-foreground">Private</span>
                    )}
                  </label>
                )}
                {codeToggles && isPicked(f) && (
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Switch
                      size="sm"
                      id={`code-${f.id}`}
                      checked={!!selected.find((s) => s.id === f.id)?.code}
                      onCheckedChange={(v) => setCode(f, v)}
                      aria-label={`Also sync code from ${f.name}`}
                    />
                    <label htmlFor={`code-${f.id}`} className="text-xs text-muted-foreground">
                      Code
                    </label>
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        {tickets && options && (
          <Field orientation="horizontal" className="rounded-xl border p-3">
            <FieldContent>
              <FieldLabel htmlFor={`${item.id}-tickets`}>{tickets.toggle}</FieldLabel>
              <FieldDescription>{tickets.description}</FieldDescription>
            </FieldContent>
            <Switch
              id={`${item.id}-tickets`}
              checked={!!options[tickets.key]}
              onCheckedChange={(v) => setOptions({ ...options, [tickets.key]: v })}
            />
          </Field>
        )}
        {members && options && (
          <Field orientation="horizontal" className="rounded-xl border p-3">
            <FieldContent>
              <FieldLabel htmlFor={`${item.id}-members`}>{members.toggle}</FieldLabel>
              <FieldDescription>{members.description}</FieldDescription>
            </FieldContent>
            <Switch
              id={`${item.id}-members`}
              checked={!!options.members}
              onCheckedChange={(v) => setOptions({ ...options, members: v })}
            />
          </Field>
        )}
        {codeToggles && options && anyCode && (
          <Field>
            <FieldLabel htmlFor="github-extensions">Code file types</FieldLabel>
            <Input
              id="github-extensions"
              value={extensions}
              onChange={(e) => setExtensions(e.target.value)}
              placeholder=".py, .ts, .go"
            />
            <FieldDescription>
              For repositories with code on. Large files, lockfiles and vendored folders are
              skipped.
            </FieldDescription>
          </Field>
        )}

        <PickedChips
          selected={selected}
          onRemove={toggle}
          required={!!item.picker_required}
          all={copy.all}
        />

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving && <Loader2 className="animate-spin" />}
            Save and sync
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function PickedChips({
  selected,
  onRemove,
  required,
  all = "everything shared",
}: {
  selected: Container[]
  onRemove: (c: Container) => void
  required: boolean
  all?: string
}) {
  return (
    <div className="min-h-8">
      {selected.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {required ? "Nothing selected: nothing will be synced." : `Nothing selected: ${all} is synced.`}
        </p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {selected.map((s) => (
            <span
              key={s.id}
              className="flex max-w-60 items-center gap-1 rounded-full bg-muted py-1 pr-1 pl-2 text-xs"
            >
              <ContainerIcon item={s} className="size-3" />
              <span className="truncate">{s.name}</span>
              <Button
                variant="ghost"
                size="icon-xs"
                className="size-5 rounded-full"
                onClick={() => onRemove(s)}
                aria-label={`Remove ${s.name}`}
              >
                <X className="size-3" />
              </Button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// A website: the addresses to start from (pages or sitemaps) and how far to go.
function UrlPicker({
  item,
  open,
  onOpenChange,
  onSaved,
}: {
  item: Integration
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const [urls, setUrls] = useState<string[]>([])
  const [maxPages, setMaxPages] = useState("")
  const [maxDepth, setMaxDepth] = useState("")
  const [saving, setSaving] = useState(false)

  const [wasOpen, setWasOpen] = useState(false)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      const saved = (item.containers ?? []).map((c) => c.id)
      setUrls(saved.length ? saved : [""])
      setMaxPages(String(item.limits?.max_pages ?? 200))
      setMaxDepth(String(item.limits?.max_depth ?? 3))
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const picked = urls.map((u) => u.trim()).filter(Boolean)
      await api.setIntegrationContainers(
        item.id,
        picked.map((u) => ({ id: u, name: u, kind: "url" })),
        { max_pages: Number(maxPages) || undefined, max_depth: Number(maxDepth) || undefined },
      )
      toast.success(picked.length ? "Saved. Crawling…" : "No addresses: the site's pages were removed from search.")
      onSaved()
      onOpenChange(false)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={save} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Pages to crawl</DialogTitle>
            <DialogDescription>
              Start addresses (a page, or a sitemap ending in .xml). Only pages on the same site
              are followed, and robots.txt is obeyed.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-3">
            {urls.map((u, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  value={u}
                  onChange={(e) => setUrls(urls.map((x, j) => (j === i ? e.target.value : x)))}
                  placeholder="https://docs.example.com"
                  aria-label={`Start address ${i + 1}`}
                  inputMode="url"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => setUrls(urls.filter((_, j) => j !== i))}
                  aria-label={`Remove start address ${i + 1}`}
                >
                  <X />
                </Button>
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="w-fit"
              onClick={() => setUrls([...urls, ""])}
              disabled={urls.length >= 20}
            >
              <Plus /> Add an address
            </Button>
            <div className="grid grid-cols-2 gap-3">
              <Field>
                <FieldLabel htmlFor="crawl-max-pages">Most pages</FieldLabel>
                <Input
                  id="crawl-max-pages"
                  type="number"
                  min={1}
                  max={2000}
                  value={maxPages}
                  onChange={(e) => setMaxPages(e.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="crawl-max-depth">Links deep</FieldLabel>
                <Input
                  id="crawl-max-depth"
                  type="number"
                  min={1}
                  max={10}
                  value={maxDepth}
                  onChange={(e) => setMaxDepth(e.target.value)}
                />
              </Field>
            </div>
            <FieldDescription>
              A crawl stops at the page limit and says so; nothing is removed after a crawl that
              stopped early.
            </FieldDescription>
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="animate-spin" />}
              Save and crawl
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
