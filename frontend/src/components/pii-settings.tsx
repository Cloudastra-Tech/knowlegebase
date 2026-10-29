import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { AsciiDivider } from "@/components/ascii/ascii-divider"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { assistant, type AssistantSettings, type PiiEntity, type PiiPolicy } from "@/lib/api"

// The workspace's personal data policy (platform.md PL-13, pii.py), in the Assistant
// screen: which types are masked in answers and in the question log, which ones the
// ingest scan flags on the Knowledge screen, and whose emails stay in answers.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const PACKS: Record<string, string> = {
  global: "Everywhere",
  us: "United States",
  uk: "United Kingdom",
  in: "India",
}
const COLUMNS = [
  { key: "answers", label: "Answers", hint: "Masked in answers" },
  { key: "logs", label: "Log", hint: "Masked in the question log" },
  { key: "ingest", label: "Flag", hint: "Flagged on the Knowledge screen" },
] as const
type Column = (typeof COLUMNS)[number]["key"]

const same = (a: PiiPolicy, b: PiiPolicy) => JSON.stringify(a) === JSON.stringify(b)

export function PiiSettings({ settings }: { settings: AssistantSettings }) {
  const queryClient = useQueryClient()
  const [policy, setPolicy] = useState<PiiPolicy>(settings.pii_policy)
  const [domains, setDomains] = useState(settings.pii_policy.allowed_domains.join(", "))
  const [denseMin, setDenseMin] = useState(String(settings.pii_policy.dense_min))
  const labels = Object.fromEntries(settings.choices.pii_entities.map((e) => [e.id, e.label]))

  const draft: PiiPolicy = {
    ...policy,
    allowed_domains: domains
      .split(",")
      .map((d) => d.trim())
      .filter(Boolean),
    dense_min: Number(denseMin) || policy.dense_min,
  }
  const changed = !same(draft, settings.pii_policy)

  const save = useMutation({
    mutationFn: (next: PiiPolicy | null) => assistant.saveSettings({ pii_policy: next }),
    onSuccess: (saved) => {
      setPolicy(saved.pii_policy)
      setDomains(saved.pii_policy.allowed_domains.join(", "))
      setDenseMin(String(saved.pii_policy.dense_min))
      toast.success("Saved. The next answer uses it.")
      void queryClient.invalidateQueries({ queryKey: ["assistant"] })
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] })
    },
    onError: (err) => toast.error(errorText(err)),
  })

  function toggle(column: Column, entity: PiiEntity, on: boolean) {
    setPolicy((p) => ({
      ...p,
      [column]: on ? [...p[column], entity] : p[column].filter((e) => e !== entity),
    }))
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(draft)
      }}
    >
      <FieldGroup className="gap-5">
        {/* A grid of checkboxes read like a table: opaque. */}
        <div className="overflow-hidden rounded-xl border border-border/70 bg-card">
          <div className="grid grid-cols-[1fr_repeat(3,3.25rem)] items-center gap-x-2 border-b border-border/70 bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground sm:grid-cols-[1fr_repeat(3,5.5rem)]">
            <span>Type</span>
            {COLUMNS.map((c) => (
              <span key={c.key} className="text-center" title={c.hint}>
                {c.label}
              </span>
            ))}
          </div>
          {Object.entries(settings.choices.pii_packs).map(([pack, entities]) => (
            <div key={pack} className="border-b border-border/70 last:border-b-0">
              <AsciiDivider label={PACKS[pack] ?? pack} className="px-3 pt-3 pb-1" />
              {entities.map((entity) => (
                <div
                  key={entity}
                  className="grid grid-cols-[1fr_repeat(3,3.25rem)] items-center gap-x-2 px-3 py-1.5 text-sm sm:grid-cols-[1fr_repeat(3,5.5rem)]"
                >
                  <span className="min-w-0 leading-snug break-words">
                    {labels[entity] ?? entity}
                  </span>
                  {COLUMNS.map((c) => (
                    <span key={c.key} className="flex justify-center">
                      <Checkbox
                        aria-label={`${c.hint}: ${labels[entity] ?? entity}`}
                        checked={policy[c.key].includes(entity)}
                        onCheckedChange={(on) => toggle(c.key, entity, on === true)}
                      />
                    </span>
                  ))}
                </div>
              ))}
            </div>
          ))}
        </div>

        <Field orientation="horizontal">
          <FieldContent>
            <FieldLabel htmlFor="pii-own-domains">Keep your own colleagues' emails</FieldLabel>
            <FieldDescription>
              Emails at the workspace's verified domains stay in answers, so “who owns this?”
              gets an address.{" "}
              {settings.own_domains.length ? (
                <span className="inline-flex flex-wrap gap-1 align-middle">
                  {settings.own_domains.map((d) => (
                    <Badge key={d} variant="secondary" className="font-normal">
                      {d}
                    </Badge>
                  ))}
                </span>
              ) : (
                "No domain is verified yet (Sign-in settings)."
              )}
            </FieldDescription>
          </FieldContent>
          <Switch
            id="pii-own-domains"
            checked={policy.allow_own_domains}
            onCheckedChange={(on) => setPolicy((p) => ({ ...p, allow_own_domains: on }))}
          />
        </Field>

        <div className="grid gap-5 sm:grid-cols-[1fr_12rem]">
          <Field>
            <FieldLabel htmlFor="pii-domains">Also keep emails at</FieldLabel>
            <Input
              id="pii-domains"
              value={domains}
              onChange={(e) => setDomains(e.target.value)}
              placeholder="partner.com, agency.example"
            />
            <FieldDescription>
              Separated by commas, up to {settings.limits.pii_domains}.
            </FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor="pii-dense">Flag a document from</FieldLabel>
            <Input
              id="pii-dense"
              type="number"
              inputMode="numeric"
              min={1}
              value={denseMin}
              onChange={(e) => setDenseMin(e.target.value)}
            />
            <FieldDescription>values of the flagged types.</FieldDescription>
          </Field>
        </div>

        <div className="flex flex-wrap gap-2">
          {changed && (
            <Button type="submit" loading={save.isPending}>
              Save
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            disabled={save.isPending}
            onClick={() => save.mutate(null)}
          >
            Use the defaults
          </Button>
        </div>
      </FieldGroup>
    </form>
  )
}
