import { useState, type FormEvent } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, Plus, Search, Trash2, UserPlus, Users } from "lucide-react"
import { toast } from "sonner"

import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { Panel } from "@/components/glass/panel"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
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
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { groups as groupsApi, type AccessGroup, type GroupSource } from "@/lib/api"
import { queries } from "@/lib/queries"
import { cn } from "@/lib/utils"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const SOURCE_LABEL: Record<GroupSource, string> = {
  builtin: "Built in",
  manual: "Made here",
  idp: "From sign-in",
  scim: "From SCIM",
}

// Where a read-only group is changed.
const SOURCE_NOTE: Partial<Record<GroupSource, string>> = {
  builtin: "Everyone whose role is HR or Admin. Change roles in Members.",
  idp: "The groups your identity provider sends at sign-in. Change them there.",
  scim: "Pushed by your identity provider through SCIM. Change them there.",
}

type Person = { username: string; name: string }

// Add or remove people of the workspace (a manual group's members).
function MembersPicker({
  everyone,
  value,
  onChange,
  disabled,
}: {
  everyone: Person[]
  value: string[]
  onChange: (usernames: string[]) => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const toggle = (u: string) =>
    onChange(value.includes(u) ? value.filter((v) => v !== u) : [...value, u])
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" size="sm" disabled={disabled}>
          <UserPlus data-icon="inline-start" /> Members
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(22rem,calc(100vw-2rem))] p-0">
        <Command>
          <CommandInput placeholder="Search people…" />
          <CommandList>
            <CommandEmpty>Nobody matches.</CommandEmpty>
            <CommandGroup>
              {everyone.map((p) => (
                <CommandItem
                  key={p.username}
                  value={`${p.name} ${p.username}`}
                  onSelect={() => toggle(p.username)}
                >
                  <Check
                    className={cn("size-4", value.includes(p.username) ? "opacity-100" : "opacity-0")}
                  />
                  <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  {p.name !== p.username && (
                    <span className="min-w-0 shrink truncate text-xs text-muted-foreground">
                      {p.username}
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

function NewGroupDialog({ onMade }: { onMade: () => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [saving, setSaving] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const made = await groupsApi.createGroup(name, description)
      toast.success(`Made the group ${made.name}. Now add its members.`)
      setOpen(false)
      setName("")
      setDescription("")
      onMade()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus /> New group
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>New group</DialogTitle>
            <DialogDescription>
              A group decides who may see documents shared with it, like Finance or Legal. It
              doesn't change anyone's role.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="group-name">Name</Label>
            <Input
              id="group-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Finance"
              maxLength={80}
              required
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="group-description">What it's for (optional)</Label>
            <Textarea
              id="group-description"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={300}
            />
          </div>
          <DialogFooter>
            <Button type="submit" loading={saving} disabled={!name.trim()}>
              Make the group
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function GroupCard({
  group,
  everyone,
  onChange,
}: {
  group: AccessGroup
  everyone: Person[]
  onChange: () => void
}) {
  const [saving, setSaving] = useState(false)
  const usernames = group.members.map((m) => m.username)

  async function setMembers(next: string[]) {
    setSaving(true)
    try {
      await groupsApi.setGroupMembers(group.id, next)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    try {
      await groupsApi.deleteGroup(group.id)
      toast.success(`Deleted ${group.name}. Documents shared with it are now hidden from its members.`)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <li className="rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="flex flex-wrap items-center gap-2 font-medium">
            <Users className="size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 truncate">{group.name}</span>
            <Badge variant="mono">{SOURCE_LABEL[group.source]}</Badge>
          </p>
          <p className="text-sm text-muted-foreground">
            {group.description || SOURCE_NOTE[group.source] || ""}
          </p>
        </div>
        {group.editable && (
          <div className="flex shrink-0 gap-2">
            <MembersPicker
              everyone={everyone}
              value={usernames}
              onChange={setMembers}
              disabled={saving}
            />
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button size="sm" variant="outline" className="text-destructive" aria-label={`Delete ${group.name}`}>
                  <Trash2 />
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete {group.name}?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Documents shared only with this group will be hidden from its members: nobody
                    gets access by it any more. Share them with someone else first if they still
                    need it.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={remove}>Delete</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        )}
      </div>
      {group.members.length ? (
        <ul className="mt-3 flex flex-wrap gap-1.5">
          {group.members.map((m) => (
            <li key={m.username}>
              <Badge variant="outline" className="max-w-64 font-normal">
                <span className="truncate">{m.name !== m.username ? `${m.name} · ${m.username}` : m.username}</span>
              </Badge>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">No members yet.</p>
      )}
    </li>
  )
}

// Access → Groups: who may see content. Roles decide which screens someone gets;
// groups decide which documents they can ask about.
export function GroupsView() {
  const queryClient = useQueryClient()
  const { data, isPending } = useQuery(queries.groups())
  const [query, setQuery] = useState("")
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queries.groups().queryKey })
    void queryClient.invalidateQueries({ queryKey: queries.principals().queryKey })
  }
  const all = data?.groups ?? []
  const q = query.trim().toLowerCase()
  const shown = all.filter(
    (g) =>
      !q ||
      g.name.toLowerCase().includes(q) ||
      g.members.some((m) => `${m.name} ${m.username}`.toLowerCase().includes(q)),
  )
  const manual = shown.filter((g) => g.source === "manual" || g.source === "builtin")
  const external = shown.filter((g) => g.source === "idp" || g.source === "scim")

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted-foreground">
          Share a document with a group on the Documents screen, and only its members can ask
          about it. A connection that follows its source's sharing maps its groups to the
          identity provider's groups below.
        </p>
        <NewGroupDialog onMade={refresh} />
      </div>
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search groups and people…"
          className="pl-9"
          aria-label="Search groups and people"
        />
      </div>
      {isPending ? (
        <div className="space-y-3">
          <Skeleton className="h-24 rounded-2xl" />
          <Skeleton className="h-24 rounded-2xl" />
        </div>
      ) : (
        <>
          <Panel title="Groups">
            {manual.length ? (
              <ul className="flex flex-col gap-3">
                {manual.map((g) => (
                  <GroupCard key={g.id} group={g} everyone={data?.members ?? []} onChange={refresh} />
                ))}
              </ul>
            ) : (
              <AsciiEmptyState
                art={q ? "search" : "inbox"}
                size="sm"
                headingLevel={4}
                title={q ? "No group matches." : "No groups yet."}
              />
            )}
          </Panel>
          <Panel title="From your identity provider">
            {external.length ? (
              <ul className="flex flex-col gap-3">
                {external.map((g) => (
                  <GroupCard key={g.id} group={g} everyone={[]} onChange={refresh} />
                ))}
              </ul>
            ) : q && all.some((g) => g.source === "idp" || g.source === "scim") ? (
              <AsciiEmptyState art="search" size="sm" headingLevel={4} title="No group matches." />
            ) : (
              <AsciiEmptyState
                art="plug"
                size="sm"
                headingLevel={4}
                title="None yet."
                description="They appear when people sign in through a provider that sends groups, or when your identity provider pushes them through SCIM."
              />
            )}
          </Panel>
        </>
      )}
    </div>
  )
}
