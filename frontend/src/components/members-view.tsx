import { useCallback, useMemo, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { KeyRound, MailPlus, MoreHorizontal, Search } from "lucide-react"
import { toast } from "sonner"

import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { Panel } from "@/components/glass/panel"
import { InviteDialog, PendingInvites } from "@/components/invites"
import { PendingRequests } from "@/components/join-requests"
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { saveFile, signIn, workspace, type AccountRole, type Member } from "@/lib/api"
import { queries } from "@/lib/queries"
import { ROLE_LABEL } from "@/lib/roles"
import { cn, timeAgo } from "@/lib/utils"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

// "groups" in the picker = no override: the role comes from their groups (or Public).
const FROM_GROUPS = "groups"

function roleNote(m: Member) {
  if (m.role_source === "override")
    return m.group_role && m.group_role !== m.role
      ? `set by an admin (their groups give ${ROLE_LABEL[m.group_role]})`
      : "set by an admin"
  if (m.role_source === "groups") return "from their groups"
  return "default"
}

// SCIM: the identity provider manages them. It sets whether they may sign in, and
// would add them back if removed here, so those two are changed there (the API
// refuses them too). Turning their access off stays possible, for an emergency.
const SCIM_NOTE =
  "Managed by your identity provider (SCIM). Their access and membership are changed there; a role you pick here still wins over their groups."

function ScimBadge() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="mono" className="cursor-default" tabIndex={0}>
          SCIM
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-64">{SCIM_NOTE}</TooltipContent>
    </Tooltip>
  )
}

function MemberRow({ m, me, onChange }: { m: Member; me: boolean; onChange: () => void }) {
  const [confirm, setConfirm] = useState(false)
  const [confirmSignOut, setConfirmSignOut] = useState(false)
  const [confirmErase, setConfirmErase] = useState(false)
  const [typed, setTyped] = useState("")

  async function save(body: { role?: AccountRole | null; disabled?: boolean }, done: string) {
    try {
      await signIn.editMember(m.username, body)
      toast.success(done)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function remove() {
    try {
      await signIn.removeMember(m.username)
      toast.success(`Removed ${m.username}.`)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  // A data request (DSAR): everything held about them, as a JSON file.
  async function downloadData() {
    try {
      saveFile(await workspace.exportMember(m.username))
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  // Their questions, chats, keys, sessions and the member go; the audit log keeps what
  // happened under a pseudonym.
  async function erase() {
    try {
      const { pseudonym } = await workspace.eraseMember(m.username, typed.trim())
      toast.success(`Erased ${m.username}'s data. The audit log shows them as ${pseudonym}.`)
      setConfirmErase(false)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function signOutEverywhere() {
    try {
      const { ended } = await signIn.signOutMember(m.username)
      toast.success(
        ended
          ? `Signed ${m.username} out on every device.`
          : `${m.username} wasn't signed in anywhere.`,
      )
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const picked = m.override_role ?? FROM_GROUPS
  const fromGroupsLabel = `From groups (${ROLE_LABEL[m.group_role ?? "public"]})`

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
      <div className="min-w-0 flex-1 basis-56">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{m.name}</span>
          {me && <Badge variant="mono">You</Badge>}
          {m.scim_managed && <ScimBadge />}
          {m.disabled && <Badge variant="destructive">Access off</Badge>}
          {m.has_password && (
            <KeyRound className="size-3.5 text-muted-foreground" aria-label="Has a password" />
          )}
        </div>
        <p className="truncate text-sm text-muted-foreground">
          {m.username} · last sign-in {timeAgo(m.last_login_at)}
        </p>
        {m.groups.length > 0 && (
          <p className="truncate text-xs text-muted-foreground" title={m.groups.join(", ")}>
            Groups: {m.groups.join(", ")}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2">
        <div className="text-right">
          <Select
            value={picked}
            disabled={me}
            onValueChange={(v) =>
              save(
                { role: v === FROM_GROUPS ? null : (v as AccountRole) },
                v === FROM_GROUPS
                  ? `${m.username}'s role now comes from their groups.`
                  : `${m.username} is now ${ROLE_LABEL[v as AccountRole]}.`,
              )
            }
          >
            <SelectTrigger aria-label={`Role of ${m.username}`} className="w-40 sm:w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={FROM_GROUPS}>{fromGroupsLabel}</SelectItem>
              <SelectSeparator />
              {(Object.keys(ROLE_LABEL) as AccountRole[]).map((r) => (
                <SelectItem key={r} value={r}>
                  {ROLE_LABEL[r]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="mt-0.5 text-xs text-muted-foreground">{roleNote(m)}</p>
          {m.role_source === "groups" && m.groups_updated_at && (
            <p
              className={cn(
                "text-xs",
                m.admin_kept ? "text-warning" : "text-muted-foreground",
              )}
              title={
                m.admin_kept
                  ? "Their admin from groups has expired, but they're the only admin, so they keep it. Make someone else admin, or have them sign in through the role source."
                  : undefined
              }
            >
              Groups updated {timeAgo(m.groups_updated_at)}
            </p>
          )}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="icon"
              variant="ghost"
              className="size-8"
              disabled={me}
              aria-label={`More for ${m.username}`}
            >
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuItem
              disabled={m.scim_managed && m.disabled}
              onSelect={() =>
                save(
                  { disabled: !m.disabled },
                  m.disabled
                    ? `${m.username} can sign in again.`
                    : `${m.username}'s access is off, and they're signed out everywhere.`,
                )
              }
            >
              {m.disabled ? "Turn access back on" : "Turn access off"}
            </DropdownMenuItem>
            {m.scim_managed && m.disabled && (
              <p className="px-2 pb-1 text-xs text-muted-foreground">
                Turn it back on in your identity provider.
              </p>
            )}
            <DropdownMenuItem onSelect={() => setConfirmSignOut(true)}>
              Sign out everywhere
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={downloadData}>Download their data</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-destructive"
              disabled={m.scim_managed}
              onSelect={() => setConfirm(true)}
            >
              Remove
            </DropdownMenuItem>
            {m.scim_managed && (
              <p className="px-2 pb-1 text-xs text-muted-foreground">
                Remove them in your identity provider.
              </p>
            )}
            <DropdownMenuItem
              className="text-destructive"
              onSelect={() => {
                setTyped("")
                setConfirmErase(true)
              }}
            >
              Erase their data…
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {m.username}?</AlertDialogTitle>
            <AlertDialogDescription>
              They're signed out on every device now
              {m.has_password ? ", and their password is deleted" : ""}. If they can still
              sign in with your provider, they come back as a new member: to keep someone out,
              turn their access off instead.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={remove}>Remove</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog open={confirmErase} onOpenChange={setConfirmErase}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Erase {m.username}'s data?</AlertDialogTitle>
            <AlertDialogDescription>
              For a data request. Their questions, chats, feedback, API keys, invites and
              sessions are deleted, and they're removed from the workspace. The audit log keeps
              what happened, showing a pseudonym instead of them. This can't be undone: download
              their data first if they asked for a copy.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <Field>
            <FieldLabel htmlFor={`erase-${m.username}`}>
              Type <span className="font-semibold">{m.username}</span> to confirm
            </FieldLabel>
            <Input
              id={`erase-${m.username}`}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
            />
          </Field>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={typed.trim().toLowerCase() !== m.username}
              onClick={(e) => {
                e.preventDefault()
                void erase()
              }}
            >
              Erase
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog open={confirmSignOut} onOpenChange={setConfirmSignOut}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Sign {m.username} out everywhere?</AlertDialogTitle>
            <AlertDialogDescription>
              Every session they have ends now, on every device.{" "}
              {m.disabled
                ? "Their access is off, so they can't sign in again until it's back on."
                : "They can sign in again: to keep them out, turn their access off instead."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={signOutEverywhere}>Sign out everywhere</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// Everyone in the workspace, with their role and where it comes from (admin).
export function MembersView({ me }: { me: string }) {
  const queryClient = useQueryClient()
  const members = useQuery(queries.members()).data?.members ?? null
  const [query, setQuery] = useState("")
  const [inviting, setInviting] = useState(false)

  const load = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: queries.members().queryKey })
  }, [queryClient])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (members ?? []).filter(
      (m) => !q || m.username.includes(q) || m.name.toLowerCase().includes(q),
    )
  }, [members, query])

  if (!members) return <Skeleton className="h-48 rounded-2xl" />

  return (
    <Panel
      title="Members"
      actions={
        <Button onClick={() => setInviting(true)}>
          <MailPlus data-icon="inline-start" /> Invite
        </Button>
      }
    >
      <p className="-mt-2 text-sm text-pretty text-muted-foreground">
        Everyone who has signed in or been given a role. A role you pick here wins over their
        groups.
      </p>
      <div className="relative mt-4 sm:max-w-xs">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          aria-label="Search members"
          placeholder="Search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full pl-8"
        />
      </div>
      <PendingRequests />
      <PendingInvites />
      <InviteDialog open={inviting} onOpenChange={setInviting} />
      {/* A table in all but markup: opaque, and nothing staggers in. */}
      <div className="mt-4 divide-y rounded-xl border bg-card">
        {shown.map((m) => (
          <MemberRow key={m.username} m={m} me={m.username === me} onChange={load} />
        ))}
        {!shown.length &&
          (members.length ? (
            <AsciiEmptyState art="search" size="sm" headingLevel={4} title="Nobody matches." />
          ) : (
            <AsciiEmptyState art="inbox" size="sm" headingLevel={4} title="Nobody yet." />
          ))}
      </div>
    </Panel>
  )
}
