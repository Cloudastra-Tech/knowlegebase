import { useState, type FormEvent } from "react"
import { Loader2, Plus, X } from "lucide-react"
import { toast } from "sonner"

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
import { signIn, type AccountRole, type GroupRule, type SignInConnection } from "@/lib/api"
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

// Which groups (from the provider) give which role. Opened with a new `key` each
// time, so it starts from the provider's saved rules.
export function GroupRulesDialog({
  conn,
  onOpenChange,
  onSaved,
}: {
  conn: SignInConnection | null
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const [claim, setClaim] = useState(conn?.group_claim ?? "")
  const [rules, setRules] = useState<GroupRule[]>(conn?.group_rules ?? [])
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const saml = conn?.protocol === "saml"

  function update(i: number, change: Partial<GroupRule>) {
    setRules((rs) => rs.map((r, j) => (j === i ? { ...r, ...change } : r)))
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!conn) return
    setError(null)
    setSaving(true)
    try {
      const kept = rules
        .map((r) => ({ ...r, group: r.group.trim() }))
        .filter((r) => r.group)
      await signIn.setGroupRules(conn.id, claim.trim(), kept)
      toast.success(
        kept.length
          ? `Saved. ${conn.label} sets roles from groups at each person's next sign-in.`
          : `Saved. ${conn.label} no longer sets roles.`,
      )
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={!!conn} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <form onSubmit={save} className="grid grid-cols-1 gap-5">
          <DialogHeader>
            <DialogTitle>Roles from groups: {conn?.label}</DialogTitle>
            <DialogDescription>
              People get the highest role their groups at the provider give them, each time
              they sign in. A role you set for someone on the Members tab wins over this.
            </DialogDescription>
          </DialogHeader>

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

          <div className="grid grid-cols-1 gap-2">
            <p className="text-sm font-medium">Rules</p>
            {rules.map((r, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  aria-label={`Group ${i + 1}`}
                  placeholder="Group name"
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
                No rules: everyone signs in as Public, unless you set their role yourself.
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
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="animate-spin" />}
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
