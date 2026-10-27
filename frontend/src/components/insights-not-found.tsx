import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { format, subDays } from "date-fns"
import { Check, FilePen, Loader2, Lock, Undo2 } from "lucide-react"
import { toast } from "sonner"

import { DateRangePicker, type DateRange } from "@/components/date-range-picker"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { insights, type Surface, type UnansweredQuestion } from "@/lib/api"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

// Insights > Not found (chat.md C17): each question the assistant couldn't answer from
// the documents, newest first. HR marks one resolved once the documents cover it, or
// has the assistant draft a doc for it (the same draft flow as a content gap). The
// text follows the workspace's retention mode and the viewer's role; who asked is
// never shown.

const iso = (d: Date) => format(d, "yyyy-MM-dd")

const SURFACE: Record<Surface, string> = {
  web: "web chat",
  slack: "Slack",
  teams: "Teams",
  extension: "browser extension",
  email: "email",
  api: "API",
  mcp: "MCP",
}

function QuestionRow({
  q,
  resolved,
  onDraftDoc,
}: {
  q: UnansweredQuestion
  resolved: boolean
  onDraftDoc?: (questionId: number) => void
}) {
  const queryClient = useQueryClient()
  const toggle = useMutation({
    mutationFn: () => (resolved ? insights.reopen(q.id) : insights.resolve(q.id)),
    onSuccess: () => {
      toast.success(resolved ? "Back in the open list." : "Marked resolved.")
      void queryClient.invalidateQueries({ queryKey: ["not-found"] })
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't change it."),
  })
  return (
    <li className="flex flex-col gap-2 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className={q.question ? "break-words" : "text-muted-foreground italic"}>
          {q.question ?? "Question text isn't kept"}
        </p>
        <p className="text-xs text-muted-foreground">
          {timeAgo(q.at)} · {SURFACE[q.surface] ?? q.surface}
          {resolved && q.resolved_at ? ` · resolved ${timeAgo(q.resolved_at)}` : ""}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        {/* A draft for HR to review on the Documents screen; never published by itself. */}
        {onDraftDoc && q.question && !resolved && (
          <Button variant="outline" size="sm" onClick={() => onDraftDoc(q.id)}>
            <FilePen data-icon="inline-start" /> Draft a doc
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          disabled={toggle.isPending}
          onClick={() => toggle.mutate()}
        >
          {toggle.isPending ? (
            <Loader2 className="animate-spin" data-icon="inline-start" />
          ) : resolved ? (
            <Undo2 data-icon="inline-start" />
          ) : (
            <Check data-icon="inline-start" />
          )}
          {resolved ? "Reopen" : "Mark resolved"}
        </Button>
      </div>
    </li>
  )
}

export function NotFoundQuestions({ onDraftDoc }: { onDraftDoc?: (questionId: number) => void }) {
  const [today] = useState(() => new Date())
  const [range, setRange] = useState<DateRange | undefined>(() => ({
    from: subDays(today, 29),
    to: today,
  }))
  const [show, setShow] = useState<"open" | "resolved">("open")
  const from = range?.from ? iso(range.from) : undefined
  const to = range?.from ? iso(range.to ?? range.from) : undefined
  const resolved = show === "resolved"
  const { data } = useQuery(queries.notFound(resolved, from, to))

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <DateRangePicker
          value={range}
          onChange={setRange}
          className="min-w-0 basis-60 grow sm:basis-72 sm:grow-0"
        />
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={show}
          onValueChange={(v) => v && setShow(v as typeof show)}
          aria-label="Which questions"
        >
          <ToggleGroupItem value="open">Open</ToggleGroupItem>
          <ToggleGroupItem value="resolved">Resolved</ToggleGroupItem>
        </ToggleGroup>
      </div>
      <p className="text-sm text-muted-foreground">
        Questions the assistant couldn't answer from the documents, newest first. Mark one
        resolved once the documents cover it, or have the assistant draft a doc for it.
      </p>
      {data && !data.text_kept && (
        <Alert>
          <Lock />
          <AlertDescription>
            Questions are logged without their text here, so only when and where they were
            asked shows. An admin can change that under Overview, in the question log settings.
          </AlertDescription>
        </Alert>
      )}
      {!data ? (
        <Skeleton className="h-48 rounded-2xl" />
      ) : data.questions.length === 0 ? (
        <p className="rounded-2xl border border-dashed p-10 text-center text-sm text-muted-foreground">
          {resolved ? "Nothing marked resolved in these days." : "No unanswered questions in these days."}
        </p>
      ) : (
        <ul className="divide-y rounded-xl border" aria-label="Unanswered questions">
          {data.questions.map((q) => (
            <QuestionRow key={q.id} q={q} resolved={resolved} onDraftDoc={onDraftDoc} />
          ))}
        </ul>
      )}
      {data && data.hidden > 0 && (
        <p className="text-xs text-muted-foreground">
          {data.hidden === 1 ? "One more question" : `${data.hidden} more questions`} reached
          documents your role can't see, so {data.hidden === 1 ? "it isn't" : "they aren't"} listed.
        </p>
      )}
    </section>
  )
}
