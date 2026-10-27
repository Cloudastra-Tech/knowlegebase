import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, Loader2, UserPlus, X } from "lucide-react"
import { toast } from "sonner"

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
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { signIn, type AccountRole, type JoinRequest } from "@/lib/api"
import { queries } from "@/lib/queries"
import { ROLE_LABEL } from "@/lib/roles"
import { timeAgo } from "@/lib/utils"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

function inDays(epoch: number) {
  const days = Math.ceil((epoch - Date.now() / 1000) / 86400)
  return days <= 1 ? "within a day" : `in ${days} days`
}

// "Approve": pick the role they join with.
function ApproveDialog({
  request,
  onClose,
  onDone,
}: {
  request: JoinRequest | null
  onClose: () => void
  onDone: () => void
}) {
  const [role, setRole] = useState<AccountRole>("public")
  const [saving, setSaving] = useState(false)

  async function approve(e: React.FormEvent) {
    e.preventDefault()
    if (!request) return
    setSaving(true)
    try {
      await signIn.approveRequest(request.id, role)
      toast.success(`${request.email} can sign in now, as ${ROLE_LABEL[role]}.`)
      onDone()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={!!request} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={approve} className="flex flex-col gap-6">
          <DialogHeader>
            <DialogTitle>Let {request?.name || request?.email} in?</DialogTitle>
            <DialogDescription>
              {request?.email} joins the workspace now. Their next sign-in works, and they're
              emailed to say so.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            <Field>
              <FieldLabel htmlFor="approve-role">Role</FieldLabel>
              <Select value={role} onValueChange={(v) => setRole(v as AccountRole)}>
                <SelectTrigger id="approve-role" className="w-full">
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
                {ROLE_LABEL.public} leaves their role to their groups, if your provider sets
                roles. Anything else is set by you, and wins over their groups.
              </FieldDescription>
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? <Loader2 className="animate-spin" data-icon="inline-start" /> : (
                <Check data-icon="inline-start" />
              )}
              Approve
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function RequestRow({
  request,
  onApprove,
  onDeny,
}: {
  request: JoinRequest
  onApprove: () => void
  onDeny: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
      <div className="min-w-0 flex-1 basis-56">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{request.name || request.email}</span>
          <Badge variant="secondary" className="font-normal">
            Asked to join
          </Badge>
        </div>
        <p className="truncate text-sm text-muted-foreground">
          {request.name ? `${request.email} · ` : ""}
          {request.provider ?? "single sign-on"} · {timeAgo(request.created_at)} · expires{" "}
          {inDays(request.expires_at)}
        </p>
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={onDeny} aria-label={`Deny ${request.email}`}>
          <X data-icon="inline-start" /> Deny
        </Button>
        <Button size="sm" onClick={onApprove} aria-label={`Approve ${request.email}`}>
          <Check data-icon="inline-start" /> Approve
        </Button>
      </div>
    </div>
  )
}

// People who signed in from a verified domain and asked to join (the 'request'
// joining policy, Access > Sign-in). Nothing when there are none.
export function PendingRequests() {
  const queryClient = useQueryClient()
  const data = useQuery(queries.joinRequests()).data
  const [approving, setApproving] = useState<JoinRequest | null>(null)
  const [denying, setDenying] = useState<JoinRequest | null>(null)
  const requests = data?.requests ?? []
  if (!requests.length) return null

  const reload = () => {
    void queryClient.invalidateQueries({ queryKey: queries.joinRequests().queryKey })
    void queryClient.invalidateQueries({ queryKey: queries.members().queryKey })
  }

  async function deny(request: JoinRequest) {
    try {
      await signIn.denyRequest(request.id)
      toast.success(`Denied ${request.email}.`)
      reload()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <div className="mt-4">
      <h4 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
        <UserPlus className="size-4" /> Pending requests
      </h4>
      <div className="mt-2 divide-y rounded-xl border">
        {requests.map((r) => (
          <RequestRow
            key={r.id}
            request={r}
            onApprove={() => setApproving(r)}
            onDeny={() => setDenying(r)}
          />
        ))}
      </div>
      <ApproveDialog
        key={approving?.id ?? "none"}
        request={approving}
        onClose={() => setApproving(null)}
        onDone={() => {
          setApproving(null)
          reload()
        }}
      />
      <AlertDialog open={!!denying} onOpenChange={(open) => !open && setDenying(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Deny {denying?.email}?</AlertDialogTitle>
            <AlertDialogDescription>
              They aren't emailed. If they sign in again within {data?.deny_days ?? 30} days
              they're told their request was declined, and no new one is made. You can still
              invite them.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (denying) void deny(denying)
                setDenying(null)
              }}
            >
              Deny
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
