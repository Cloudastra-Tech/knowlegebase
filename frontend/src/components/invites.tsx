import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { MailPlus, MoreHorizontal } from "lucide-react"
import { toast } from "sonner"

import { AsciiDivider } from "@/components/ascii/ascii-divider"
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
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { signIn, type AccountRole, type Invite } from "@/lib/api"
import { queries } from "@/lib/queries"
import { ROLE_LABEL } from "@/lib/roles"
import { timeAgo } from "@/lib/utils"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

function inDays(epoch: number) {
  const days = Math.ceil((epoch - Date.now() / 1000) / 86400)
  return days <= 1 ? "within a day" : `in ${days} days`
}

// "Invite": an email with a link to join, as the role picked here (admin).
export function InviteDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<AccountRole>("public")
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)

  async function send(e: React.FormEvent) {
    e.preventDefault()
    setSending(true)
    setError(null)
    try {
      const invite = await signIn.invite(email.trim(), role)
      toast.success(`Invite sent to ${invite.email}.`)
      void queryClient.invalidateQueries({ queryKey: queries.invites().queryKey })
      setEmail("")
      setRole("public")
      onOpenChange(false)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setSending(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={send} className="flex flex-col gap-6">
          <DialogHeader>
            <DialogTitle>Invite someone</DialogTitle>
            <DialogDescription>
              They get an email with a link to join this workspace. It works once and expires
              after a week.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            <Field data-invalid={!!error || undefined}>
              <FieldLabel htmlFor="invite-email">Email</FieldLabel>
              <Input
                id="invite-email"
                type="email"
                autoComplete="off"
                placeholder="name@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                aria-invalid={!!error || undefined}
                maxLength={254}
                required
                autoFocus
              />
              {error && <FieldError>{error}</FieldError>}
            </Field>
            <Field>
              <FieldLabel htmlFor="invite-role">Role</FieldLabel>
              <Select value={role} onValueChange={(v) => setRole(v as AccountRole)}>
                <SelectTrigger id="invite-role" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(ROLE_LABEL) as AccountRole[]).map((r) => (
                    <SelectItem key={r} value={r}>
                      {ROLE_LABEL[r]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FieldDescription>
                Set by you, so it wins over their groups. On a domain you verified with single
                sign-on required, they join through your provider.
              </FieldDescription>
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={sending} disabled={!email.trim()}>
              {!sending && <MailPlus data-icon="inline-start" />}
              Send invite
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function InviteRow({ invite, onChange }: { invite: Invite; onChange: () => void }) {
  const expired = invite.status === "expired"

  async function resend() {
    try {
      await signIn.resendInvite(invite.id)
      toast.success(`Sent ${invite.email} a new link.`)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function revoke() {
    try {
      await signIn.revokeInvite(invite.id)
      toast.success(`Withdrew the invite to ${invite.email}.`)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
      <div className="min-w-0 flex-1 basis-56">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{invite.email}</span>
          <Badge variant={expired ? "destructive" : "mono"}>{expired ? "Expired" : "Invited"}</Badge>
        </div>
        <p className="truncate text-sm text-muted-foreground">
          {ROLE_LABEL[invite.role]} · by {invite.invited_by} · sent {timeAgo(invite.sent_at)}
          {!expired && ` · expires ${inDays(invite.expires_at)}`}
        </p>
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="icon" variant="ghost" className="size-8" aria-label={`More for the invite to ${invite.email}`}>
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-44">
          <DropdownMenuItem onSelect={resend}>Resend</DropdownMenuItem>
          <DropdownMenuItem className="text-destructive" onSelect={revoke}>
            Withdraw
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

// Invites not yet accepted, with resend and withdraw. Nothing when there are none.
export function PendingInvites() {
  const queryClient = useQueryClient()
  const invites = useQuery(queries.invites()).data?.invites ?? []
  if (!invites.length) return null
  const reload = () => void queryClient.invalidateQueries({ queryKey: queries.invites().queryKey })
  return (
    <div className="mt-4">
      <AsciiDivider as="h4" label="Invited" />
      <div className="mt-2 divide-y rounded-xl border bg-card">
        {invites.map((i) => (
          <InviteRow key={i.id} invite={i} onChange={reload} />
        ))}
      </div>
    </div>
  )
}
