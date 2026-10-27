import { useState, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

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
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  assistant,
  type AssistantChange,
  type AssistantSettings,
  type GlossaryEntry,
  type Tone,
} from "@/lib/api"
import { queries } from "@/lib/queries"

// /admin/assistant (admins): how the workspace's assistant answers (platform.md
// PL-29). Its name, tone and extra instructions go at the END of its instructions,
// after the safety and citation rules, which they can't change. The glossary teaches
// it the company's own terms. Saved changes apply to the next question.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const TONES: Record<Tone, string> = {
  default: "Default",
  friendly: "Friendly",
  formal: "Formal",
  concise: "Concise",
  detailed: "Detailed",
}

function Section({
  title,
  description,
  action,
  children,
}: {
  title: string
  description: ReactNode
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="min-w-0 rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-medium">{title}</h3>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        {action}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  )
}

// The form starts from the saved settings; once saved, they match again.
function SettingsForm({ settings }: { settings: AssistantSettings }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(settings.assistant_name ?? "")
  const [tone, setTone] = useState<Tone>(settings.tone)
  const [language, setLanguage] = useState(settings.language)
  const [instructions, setInstructions] = useState(settings.instructions)
  const limit = settings.limits.instructions

  const save = useMutation({
    mutationFn: (change: AssistantChange) => assistant.saveSettings(change),
    onSuccess: (saved, change) => {
      // As the server keeps them (spacing tidied), so the form shows no change.
      if ("assistant_name" in change) setName(saved.assistant_name ?? "")
      if ("instructions" in change) setInstructions(saved.instructions)
      toast.success("Saved. The next question uses it.")
      void queryClient.invalidateQueries({ queryKey: ["assistant"] })
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const changed =
    name !== (settings.assistant_name ?? "") ||
    tone !== settings.tone ||
    language !== settings.language ||
    instructions !== settings.instructions

  return (
    <>
      <Section
        title="Voice"
        description="How the assistant introduces itself and writes. The safety and citation rules always come first: nothing here can turn them off."
      >
        <form
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate({ assistant_name: name, tone, language, instructions })
          }}
        >
          <FieldGroup className="gap-5">
            <div className="grid gap-5 sm:grid-cols-3">
              <Field>
                <FieldLabel htmlFor="as-name">Name</FieldLabel>
                <Input
                  id="as-name"
                  value={name}
                  maxLength={settings.limits.name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Knowledge assistant"
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="as-tone">Tone</FieldLabel>
                <Select value={tone} onValueChange={(v) => setTone(v as Tone)}>
                  <SelectTrigger id="as-tone" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {settings.choices.tones.map((t) => (
                      <SelectItem key={t} value={t}>
                        {TONES[t] ?? t}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel htmlFor="as-language">Answer language</FieldLabel>
                <Select value={language} onValueChange={setLanguage}>
                  <SelectTrigger id="as-language" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {settings.choices.languages.map((l) => (
                      <SelectItem key={l} value={l}>
                        {l === "auto" ? "Same as the question" : l}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>
            <Field>
              <FieldLabel htmlFor="as-instructions">Company preferences</FieldLabel>
              <Textarea
                id="as-instructions"
                value={instructions}
                rows={6}
                maxLength={limit}
                onChange={(e) => setInstructions(e.target.value)}
                placeholder="Call customers “members”. Mention the HR portal for anything about payslips."
              />
              <FieldDescription>
                Added after the assistant's own rules, which it's told these can't change.{" "}
                <span className="tabular-nums">
                  {instructions.length.toLocaleString()} / {limit.toLocaleString()}
                </span>
              </FieldDescription>
            </Field>
            {changed && (
              <Button type="submit" className="self-start" disabled={save.isPending}>
                {save.isPending && <Loader2 className="animate-spin" data-icon="inline-start" />}
                Save
              </Button>
            )}
          </FieldGroup>
        </form>
      </Section>

      <Section
        title="Behaviour"
        description="Saved as soon as you switch them."
      >
        <FieldGroup className="gap-4">
          <SwitchRow
            id="as-wikipedia"
            label="Wikipedia"
            description={
              settings.allow_wikipedia_server
                ? "Look up public topics on Wikipedia when the documents don't cover them."
                : "Turned off on this server (ALLOW_WIKIPEDIA)."
            }
            checked={settings.allow_wikipedia}
            disabled={!settings.allow_wikipedia_server || save.isPending}
            onChange={(on) => save.mutate({ allow_wikipedia: on })}
          />
          <SwitchRow
            id="as-currency"
            label="Currency conversion"
            description={
              settings.allow_currency_server
                ? "Convert amounts with live exchange rates."
                : "Turned off on this server (ALLOW_CURRENCY)."
            }
            checked={settings.allow_currency}
            disabled={!settings.allow_currency_server || save.isPending}
            onChange={(on) => save.mutate({ allow_currency: on })}
          />
          <SwitchRow
            id="as-steps"
            label="Show steps to everyone"
            description="Members see what the assistant searched while it works. HR and admins always do."
            checked={settings.show_steps}
            disabled={save.isPending}
            onChange={(on) => save.mutate({ show_steps: on })}
          />
          <SwitchRow
            id="as-adapt"
            label="Reword verified answers"
            description="A verified answer may be reworded to fit the question. Facts, figures and links stay exactly as approved, or it's shown as written."
            checked={settings.adapt_verified}
            disabled={save.isPending}
            onChange={(on) => save.mutate({ adapt_verified: on })}
          />
        </FieldGroup>
      </Section>
    </>
  )
}

function SwitchRow({
  id,
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  id: string
  label: string
  description: string
  checked: boolean
  disabled?: boolean
  onChange: (on: boolean) => void
}) {
  return (
    <Field orientation="horizontal">
      <FieldContent>
        <FieldLabel htmlFor={id}>{label}</FieldLabel>
        <FieldDescription>{description}</FieldDescription>
      </FieldContent>
      <Switch id={id} checked={checked} disabled={disabled} onCheckedChange={onChange} />
    </Field>
  )
}

function TermDialog({
  entry,
  settings,
  onClose,
}: {
  entry: GlossaryEntry | "new" | null
  settings: AssistantSettings
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const existing = entry && entry !== "new" ? entry : null
  const [term, setTerm] = useState(existing?.term ?? "")
  const [definition, setDefinition] = useState(existing?.definition ?? "")
  const [synonyms, setSynonyms] = useState(existing?.synonyms.join(", ") ?? "")
  const save = useMutation({
    mutationFn: () => {
      const body = {
        term,
        definition,
        synonyms: synonyms.split(",").map((s) => s.trim()).filter(Boolean),
      }
      return existing ? assistant.updateTerm(existing.id, body) : assistant.addTerm(body)
    },
    onSuccess: () => {
      toast.success(existing ? "Term saved." : "Term added.")
      void queryClient.invalidateQueries({ queryKey: ["assistant", "settings"] })
      onClose()
    },
    onError: (err) => toast.error(errorText(err)),
  })
  return (
    <Dialog open={entry !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{existing ? "Edit term" : "Add a term"}</DialogTitle>
          <DialogDescription>
            A question that uses the term, or one of its other names, is searched with all of
            them, and the assistant is given the definition.
          </DialogDescription>
        </DialogHeader>
        <form
          id="term-form"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate()
          }}
        >
          <FieldGroup className="gap-4">
            <Field>
              <FieldLabel htmlFor="term">Term</FieldLabel>
              <Input
                id="term"
                value={term}
                maxLength={settings.limits.term}
                onChange={(e) => setTerm(e.target.value)}
                placeholder="PTO"
                required
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="term-definition">Definition</FieldLabel>
              <Textarea
                id="term-definition"
                value={definition}
                rows={3}
                maxLength={settings.limits.definition}
                onChange={(e) => setDefinition(e.target.value)}
                placeholder="Paid time off: annual leave and sick days together."
                required
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="term-synonyms">Other names</FieldLabel>
              <Input
                id="term-synonyms"
                value={synonyms}
                onChange={(e) => setSynonyms(e.target.value)}
                placeholder="paid time off, vacation"
              />
              <FieldDescription>
                Separated by commas, up to {settings.limits.synonyms}.
              </FieldDescription>
            </Field>
          </FieldGroup>
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="term-form" disabled={save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" data-icon="inline-start" />}
            {existing ? "Save" : "Add term"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Glossary({ settings }: { settings: AssistantSettings }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<GlossaryEntry | "new" | null>(null)
  const remove = useMutation({
    mutationFn: (id: number) => assistant.deleteTerm(id),
    onSuccess: () => {
      toast.success("Term removed.")
      void queryClient.invalidateQueries({ queryKey: ["assistant", "settings"] })
    },
    onError: (err) => toast.error(errorText(err)),
  })
  return (
    <Section
      title="Glossary"
      description="Your company's own terms and acronyms, so “PTO” also finds “paid time off”."
      action={
        <Button variant="outline" size="sm" onClick={() => setEditing("new")}>
          <Plus data-icon="inline-start" /> Add a term
        </Button>
      }
    >
      {settings.glossary.length === 0 ? (
        <p className="text-sm text-muted-foreground">No terms yet.</p>
      ) : (
        <ul className="divide-y divide-border/70 rounded-xl border border-border/70">
          {settings.glossary.map((g) => (
            <li key={g.id} className="flex items-start gap-3 px-3 py-2.5 text-sm">
              <div className="min-w-0 flex-1">
                <p className="font-medium">{g.term}</p>
                <p className="text-muted-foreground">{g.definition}</p>
                {g.synonyms.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {g.synonyms.map((s) => (
                      <Badge key={s} variant="secondary" className="font-normal">
                        {s}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${g.term}`}>
                    <MoreHorizontal />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onSelect={() => setEditing(g)}>
                    <Pencil /> Edit
                  </DropdownMenuItem>
                  <DropdownMenuItem variant="destructive" onSelect={() => remove.mutate(g.id)}>
                    <Trash2 /> Remove
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          ))}
        </ul>
      )}
      {editing !== null && (
        <TermDialog
          key={editing === "new" ? "new" : editing.id}
          entry={editing}
          settings={settings}
          onClose={() => setEditing(null)}
        />
      )}
    </Section>
  )
}

export function AssistantView() {
  const { data: settings } = useQuery(queries.assistantSettings())
  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <h2 className="text-2xl font-semibold tracking-tight">Assistant</h2>
      <p className="max-w-2xl text-sm text-muted-foreground">
        How the assistant answers in this workspace. Changes apply to the next question.
      </p>
      <div className="mt-6 flex flex-col gap-6">
        {!settings ? (
          <>
            <Skeleton className="h-64 w-full rounded-2xl" />
            <Skeleton className="h-48 w-full rounded-2xl" />
          </>
        ) : (
          <>
            <SettingsForm settings={settings} />
            <Glossary settings={settings} />
          </>
        )}
      </div>
    </div>
  )
}
