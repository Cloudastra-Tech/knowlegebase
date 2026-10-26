import { useCallback, useMemo, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { KeyRound, MailPlus, MoreHorizontal, Search } from "lucide-react"
import { toast } from "sonner"

import { InviteDialog, PendingInvites } from "@/components/invites"
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
import { signIn, type AccountRole, type Member } from "@/lib/api"
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

function MemberRow({ m, me, onChange }: { m: Member; me: boolean; onChange: () => void }) {
  const [confirm, setConfirm] = useState(false)
  const [confirmSignOut, setConfirmSignOut] = useState(false)

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
          {me && <Badge variant="secondary" className="font-normal">You</Badge>}
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
                m.admin_kept ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground",
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
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem
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
            <DropdownMenuItem onSelect={() => setConfirmSignOut(true)}>
              Sign out everywhere
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-destructive" onSelect={() => setConfirm(true)}>
              Remove
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
    <section className="rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-medium">Members</h3>
          <p className="text-sm text-muted-foreground">
            Everyone who has signed in or been given a role. A role you pick here wins over
            their groups.
          </p>
        </div>
        <div className="flex w-full flex-wrap gap-2 sm:w-auto">
          <div className="relative min-w-0 flex-1 sm:flex-none">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label="Search members"
              placeholder="Search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full pl-8 sm:w-56"
            />
          </div>
          <Button onClick={() => setInviting(true)}>
            <MailPlus data-icon="inline-start" /> Invite
          </Button>
        </div>
      </div>
      <PendingInvites />
      <InviteDialog open={inviting} onOpenChange={setInviting} />
      <div className="mt-4 divide-y rounded-xl border">
        {shown.map((m) => (
          <MemberRow key={m.username} m={m} me={m.username === me} onChange={load} />
        ))}
        {!shown.length && (
          <p className="px-4 py-6 text-center text-sm text-muted-foreground">
            {members.length ? "Nobody matches." : "Nobody yet."}
          </p>
        )}
      </div>
    </section>
  )
}
