import { useState } from "react"
import { Globe } from "lucide-react"
import { toast } from "sonner"

import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { signIn, type JoinPolicy, type SignInSettings } from "@/lib/api"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

// What each joining policy does with someone new who signs in from a verified domain
// (join_store.py). Members, invites and SCIM provisioning never meet it.
const JOIN_POLICIES: Record<JoinPolicy, { label: string; description: string }> = {
  auto: {
    label: "Join automatically",
    description: "Anyone who signs in with an email on a verified domain joins as a member.",
  },
  request: {
    label: "Ask an admin",
    description:
      "They send a request to join. Admins are emailed and approve or deny it under Members.",
  },
  invite_only: {
    label: "Invite only",
    description:
      "Only people you invite, or your identity provider provisions with SCIM, can join.",
  },
}
const ORDER: JoinPolicy[] = ["request", "auto", "invite_only"]
// "Workspace default" in a domain's picker (no policy of its own).
const DEFAULT = "default"

// ---- Who can join: the workspace's policy, and any verified domain's own --------------
export function JoiningPolicy({
  settings,
  onChange,
}: {
  settings: SignInSettings
  onChange: () => void
}) {
  const [saving, setSaving] = useState(false)
  const verified = settings.domains.filter((d) => d.verified)

  async function save(run: () => Promise<unknown>, done: string) {
    setSaving(true)
    try {
      await run()
      toast.success(done)
      onChange()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="grid grid-cols-1 gap-5">
      <RadioGroup
        aria-label="Joining policy"
        value={settings.join_policy}
        onValueChange={(v) =>
          save(
            () => signIn.setJoining(v as JoinPolicy),
            `New people now: ${JOIN_POLICIES[v as JoinPolicy].label.toLowerCase()}.`,
          )
        }
        disabled={saving}
        className="gap-3"
      >
        {ORDER.map((p) => (
          <div key={p} className="flex items-start gap-3">
            <RadioGroupItem value={p} id={`join-${p}`} className="mt-0.5" />
            <div className="grid gap-1">
              <Label htmlFor={`join-${p}`}>{JOIN_POLICIES[p].label}</Label>
              <p className="text-sm text-muted-foreground">{JOIN_POLICIES[p].description}</p>
            </div>
          </div>
        ))}
      </RadioGroup>

      {verified.length > 0 && (
        <div className="grid gap-2">
          <p className="text-sm font-medium">Per domain</p>
          <p className="text-sm text-muted-foreground">
            A domain can have its own policy, e.g. contractors' domain invite only.
          </p>
          <div className="divide-y rounded-xl border">
            {verified.map((d) => (
              <div key={d.domain} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
                <Globe className="size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate font-medium">{d.domain}</span>
                <Select
                  value={d.join_policy ?? DEFAULT}
                  disabled={saving}
                  onValueChange={(v) => {
                    const policy = v === DEFAULT ? null : (v as JoinPolicy)
                    save(
                      () => signIn.setDomainJoining(d.domain, policy),
                      policy
                        ? `${d.domain}: ${JOIN_POLICIES[policy].label.toLowerCase()}.`
                        : `${d.domain} follows the workspace's policy.`,
                    )
                  }}
                >
                  <SelectTrigger
                    className="w-full sm:w-56"
                    aria-label={`Joining policy for ${d.domain}`}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={DEFAULT}>
                      Workspace's ({JOIN_POLICIES[settings.join_policy].label.toLowerCase()})
                    </SelectItem>
                    <SelectSeparator />
                    {ORDER.map((p) => (
                      <SelectItem key={p} value={p}>
                        {JOIN_POLICIES[p].label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
