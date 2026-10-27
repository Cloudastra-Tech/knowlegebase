import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { Panel } from "@/components/glass/panel"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { insights, type LogMode, type LogSettings } from "@/lib/api"

const MODES: { value: LogMode; label: string; note: string }[] = [
  {
    value: "redacted",
    label: "Redacted text",
    note: "Questions and answers are kept with emails, phone and card numbers masked before they're stored.",
  },
  {
    value: "full",
    label: "Full text",
    note: "Questions and answers are kept exactly as asked and given.",
  },
  {
    value: "metadata",
    label: "Metadata only",
    note: "No text is kept: only the outcome, timing, cost and the documents cited. Content gaps need text, so they stop. Text logged so far is cleared now.",
  },
]
const DAYS = [30, 90, 180, 365]

// How much of each question the log keeps (admins change it; HR sees it). People
// are told in the chat when text is kept. Keyed by the saved settings (InsightsView),
// so it starts again from them once they change.
export function QuestionLogSettings({
  settings,
  canEdit,
}: {
  settings: LogSettings
  canEdit: boolean
}) {
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<LogMode>(settings.mode)
  const [days, setDays] = useState(String(settings.days))

  const save = useMutation({
    mutationFn: () => insights.setLogSettings(mode, Number(days)),
    onSuccess: () => {
      toast.success("Question logging updated.")
      void queryClient.invalidateQueries({ queryKey: ["log-settings"] })
      void queryClient.invalidateQueries({ queryKey: ["insights"] })
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't save"),
  })
  const changed = mode !== settings.mode || Number(days) !== settings.days
  const note = MODES.find((m) => m.value === mode)?.note

  return (
    <Panel title="Question logging" className="min-w-0">
      <p className="-mt-2 text-sm text-pretty text-muted-foreground">
        Every question is counted for these insights. This decides how much of its text is kept.
        Who asked is never shown here, and people see “Questions are logged for your admins for{" "}
        {settings.days} days” in the chat unless only metadata is kept.
      </p>
      <form
        className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-end"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <Field className="sm:w-56">
          <FieldLabel htmlFor="log-mode">Text kept</FieldLabel>
          <Select value={mode} onValueChange={(v) => setMode(v as LogMode)} disabled={!canEdit}>
            <SelectTrigger id="log-mode" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {MODES.map((m) => (
                <SelectItem key={m.value} value={m.value}>
                  {m.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field className="sm:w-40">
          <FieldLabel htmlFor="log-days">Kept for</FieldLabel>
          <Select value={days} onValueChange={setDays} disabled={!canEdit || mode === "metadata"}>
            <SelectTrigger id="log-days" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DAYS.map((d) => (
                <SelectItem key={d} value={String(d)}>
                  {d} days
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        {canEdit && changed && (
          <Button type="submit" variant="outline" loading={save.isPending}>
            Save
          </Button>
        )}
      </form>
      <FieldDescription className="mt-3">
        {note}
        {canEdit ? "" : " Only admins can change this."}
      </FieldDescription>
    </Panel>
  )
}
