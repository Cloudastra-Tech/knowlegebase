import { useState, type FormEvent } from "react"
import { Plus, X } from "lucide-react"
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
import { Notice } from "@/components/sign-in-provider-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  ApiError,
  signIn,
  type AccountRole,
  type GroupRule,
  type SignInConnection,
} from "@/lib/api"
import { ROLE_LABEL } from "@/lib/roles"

export function RoleSelect({
  value,
  onChange,
  id,
  label,
}: {
  value: AccountRole
  onChange: (role: AccountRole) => void
  id?: string
  label: string
}) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as AccountRole)}>
      <SelectTrigger id={id} aria-label={label} className="w-28">
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
  )
}

// Whose rules: a sign-in provider, or SCIM provisioning (protocol "scim").
export type RulesTarget = Pick<SignInConnection, "id" | "label" | "group_claim" | "group_rules"> & {
  protocol: SignInConnection["protocol"] | "scim"
}

// Which groups (from the provider) give which role. Opened with a new `key` each
// time, so it starts from the provider's saved rules.
// A workspace takes roles from one provider (or SCIM), its role source
// (`roleSource`, or null). Rules on another one switch the source, once the
// admin confirms.
export function GroupRulesDialog({
  conn,
  roleSource,
  onOpenChange,
  onSaved,
}: {
  conn: RulesTarget | null
  roleSource: { id: string; label: string } | null
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const [claim, setClaim] = useState(conn?.group_claim ?? "")
  const [rules, setRules] = useState<GroupRule[]>(conn?.group_rules ?? [])
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [confirmSwitch, setConfirmSwitch] = useState(false)

  const saml = conn?.protocol === "saml"
  // SCIM's groups are pushed as they change, so its rules apply at once.
  const scim = conn?.protocol === "scim"
  const when = scim ? "straight away" : "at each person's next sign-in through it"
  const isSource = !!conn && roleSource?.id === conn.id
  const otherSource = !isSource ? roleSource : null
  const sourceName = otherSource?.label ?? "another provider"

  function update(i: number, change: Partial<GroupRule>) {
    setRules((rs) => rs.map((r, j) => (j === i ? { ...r, ...change } : r)))
  }

  const kept = () =>
    rules.map((r) => ({ ...r, group: r.group.trim() })).filter((r) => r.group)

  async function submit(makeRoleSource: boolean) {
    if (!conn) return
    setError(null)
    setSaving(true)
    try {
      const saved = kept()
      if (scim) await signIn.setScimRules(saved, makeRoleSource)
      else await signIn.setGroupRules(conn.id, claim.trim(), saved, makeRoleSource)
      toast.success(
        saved.length && makeRoleSource
          ? `Roles now come from ${conn.label}. The roles ${sourceName}'s groups gave are gone; ${conn.label}'s apply ${when}.`
          : saved.length
            ? `Saved. ${conn.label} now sets roles from groups.`
            : isSource
              ? `Saved. ${conn.label} no longer sets roles, and the roles it gave are gone.`
              : `Saved. ${conn.label} has no rules.`,
      )
      onSaved()
    } catch (err) {
      // Another provider became the role source since this page loaded.
      if (err instanceof ApiError && err.status === 409) setConfirmSwitch(true)
      else setError(err instanceof Error ? err.message : "Couldn't save.")
    } finally {
      setSaving(false)
    }
  }

  function save(e: FormEvent) {
    e.preventDefault()
    if (otherSource && kept().length) setConfirmSwitch(true)
    else submit(false)
  }

  return (
    <Dialog open={!!conn} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <form onSubmit={save} className="grid grid-cols-1 gap-5">
          <DialogHeader>
            <DialogTitle>Roles from groups: {conn?.label}</DialogTitle>
            <DialogDescription>
              People get the highest role their groups at the provider give them. Saving
              applies it now to everyone whose groups we know, and again{" "}
              {scim ? "whenever your provider changes a group" : "at each sign-in"}. A role you
              set for someone on the Members tab wins over this.
            </DialogDescription>
          </DialogHeader>

          {otherSource ? (
            <Notice>
              Roles currently come from {otherSource.label}. A workspace takes roles from one
              provider: saving rules here switches it to {conn?.label}.
            </Notice>
          ) : (
            !isSource && (
              <p className="rounded-lg bg-muted/40 p-3 text-sm text-muted-foreground">
                {scim
                  ? "Saving rules makes SCIM the role source: the groups your identity provider pushes set roles, and signing in leaves them as they are."
                  : `Saving rules makes ${conn?.label} the role source: only sign-ins through it set roles. Signing in through your other providers leaves roles as they are.`}
              </p>
            )
          )}

          {!scim && (
            <Field>
              <FieldLabel htmlFor="group-claim">
                {saml ? "Attribute with the groups" : "Claim with the groups"}
              </FieldLabel>
              <Input
                id="group-claim"
                placeholder="groups"
                value={claim}
                onChange={(e) => setClaim(e.target.value)}
              />
              <FieldDescription>
                {saml
                  ? "The SAML attribute name, e.g. groups, Role, or Entra's …/claims/groups. Test sign-in shows what your provider sends."
                  : "Empty means groups. A nested claim works too, e.g. realm_access.roles. Test sign-in shows what your provider sends."}
              </FieldDescription>
            </Field>
          )}

          <div className="grid grid-cols-1 gap-2">
            <p className="text-sm font-medium">Rules</p>
            {rules.map((r, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  aria-label={`Group ${i + 1}`}
                  placeholder={scim ? "Group name, as pushed" : "Group name"}
                  value={r.group}
                  onChange={(e) => update(i, { group: e.target.value })}
                  className="min-w-0 flex-1"
                />
                <span className="text-sm text-muted-foreground">gets</span>
                <RoleSelect
                  label={`Role for group ${i + 1}`}
                  value={r.role}
                  onChange={(role) => update(i, { role })}
                />
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="size-8 shrink-0"
                  aria-label={`Remove rule ${i + 1}`}
                  onClick={() => setRules((rs) => rs.filter((_, j) => j !== i))}
                >
                  <X />
                </Button>
              </div>
            ))}
            {!rules.length && (
              <p className="text-sm text-muted-foreground">
                {otherSource
                  ? `No rules: roles keep coming from ${otherSource.label}.`
                  : "No rules: everyone signs in as Public, unless you set their role yourself."}
              </p>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="w-fit"
              onClick={() => setRules((rs) => [...rs, { group: "", role: "hr" }])}
            >
              <Plus /> Add rule
            </Button>
          </div>
          {error && <FieldError>{error}</FieldError>}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
              Save
            </Button>
          </DialogFooter>
        </form>
        <AlertDialog open={confirmSwitch} onOpenChange={setConfirmSwitch}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                Roles currently come from {sourceName}. Switch to {conn?.label}?
              </AlertDialogTitle>
              <AlertDialogDescription>
                The roles {sourceName}'s groups gave are gone straight away, and {conn?.label}'s
                rules apply {when}. Roles you set on the Members tab stay.{" "}
                {roleSource?.id === "scim"
                  ? "Groups SCIM pushes no longer change anyone's role."
                  : `Signing in through ${sourceName} no longer changes anyone's role.`}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={() => submit(true)}>
                Switch to {conn?.label}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </DialogContent>
    </Dialog>
  )
}
