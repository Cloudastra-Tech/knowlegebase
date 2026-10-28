import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ExternalLink, FlaskConical, Loader2 } from "lucide-react"
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
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { insights, type EvalCase, type FeedbackReason, type RatedAnswer } from "@/lib/api"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

const REASONS: Record<FeedbackReason, string> = {
  wrong: "Wrong or made up",
  wrong_source: "Wrong source",
  missing: "Didn't find it, but it exists",
  outdated: "Out of date",
  refused: "Refused when it shouldn't",
  unclear: "Too long or unclear",
}

function Reasons({ item }: { item: RatedAnswer }) {
  if (!item.reasons.length) return null
  return (
    <div className="flex flex-wrap gap-1">
      {item.reasons.map((r) => (
        <Badge key={r} variant="secondary">
          {REASONS[r] ?? r}
        </Badge>
      ))}
    </div>
  )
}

// Promote a rated answer to the test set, with what it should have said.
function PromoteDialog({
  item,
  onClose,
}: {
  item: RatedAnswer | null
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [expected, setExpected] = useState("")
  const promote = useMutation({
    mutationFn: (messageId: string) => insights.addEvalCase(messageId, expected.trim() || null),
    onSuccess: () => {
      toast.success("Added to the test set.")
      void queryClient.invalidateQueries({ queryKey: ["eval-cases"] })
      setExpected("")
      onClose()
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't add it"),
  })
  return (
    <Dialog open={item !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add to the test set</DialogTitle>
          <DialogDescription>
            The question, this answer and its sources are copied into the test set, so it stays
            after the chat is deleted. Each release is checked against it.
          </DialogDescription>
        </DialogHeader>
        <p className="line-clamp-3 rounded-lg bg-muted/50 px-3 py-2 text-sm">{item?.question}</p>
        <Field>
          <FieldLabel htmlFor="expected-answer">What it should have said</FieldLabel>
          <Textarea
            id="expected-answer"
            value={expected}
            onChange={(e) => setExpected(e.target.value)}
            placeholder="Optional: the right answer, and where it's written"
            rows={4}
          />
          <FieldDescription>Leave it empty to fill in later in the test set.</FieldDescription>
        </Field>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!item || promote.isPending} onClick={() => item && promote.mutate(item.message_id)}>
            {promote.isPending && <Loader2 className="animate-spin" data-icon="inline-start" />}
            Add to test set
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AnswerSheet({
  item,
  promoted,
  canEdit,
  onClose,
  onPromote,
}: {
  item: RatedAnswer | null
  promoted: EvalCase | undefined
  canEdit: boolean
  onClose: () => void
  onPromote: (item: RatedAnswer) => void
}) {
  const queryClient = useQueryClient()
  const remove = useMutation({
    mutationFn: (id: number) => insights.deleteEvalCase(id),
    onSuccess: () => {
      toast.success("Removed from the test set.")
      void queryClient.invalidateQueries({ queryKey: ["eval-cases"] })
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't remove it"),
  })
  return (
    <Sheet open={item !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 sm:max-w-lg">
        <SheetHeader className="border-b">
          <SheetTitle>Rated answer</SheetTitle>
          <SheetDescription>
            {item ? `Thumbs down ${timeAgo(item.updated_at)}. Who asked isn't shown.` : ""}
          </SheetDescription>
        </SheetHeader>
        {item && (
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4 text-sm">
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Question</p>
              <p className="whitespace-pre-wrap break-words">{item.question}</p>
              {item.standalone_question && (
                <p className="text-xs text-muted-foreground">Searched for: {item.standalone_question}</p>
              )}
            </div>
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Answer</p>
              <p className="whitespace-pre-wrap break-words">{item.answer || "—"}</p>
            </div>
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-muted-foreground">Why it was rated down</p>
              <Reasons item={item} />
              {item.comment && <p className="text-muted-foreground">“{item.comment}”</p>}
              {!item.reasons.length && !item.comment && <p className="text-muted-foreground">No reason given.</p>}
            </div>
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Sources</p>
              {item.sources.length ? (
                <ul className="space-y-1">
                  {item.sources.map((s, i) => (
                    <li key={i} className="flex items-center gap-1.5">
                      <span className="min-w-0 truncate">{s.title}</span>
                      <span className="shrink-0 text-xs text-muted-foreground">· {s.provider}</span>
                      {s.url && (
                        <a href={s.url} target="_blank" rel="noreferrer" aria-label={`Open ${s.title}`}>
                          <ExternalLink className="size-3.5 text-muted-foreground" />
                        </a>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted-foreground">None cited.</p>
              )}
            </div>
          </div>
        )}
        {item && canEdit && (
          <SheetFooter className="border-t">
            {/* A case the weekly job suggested (origin feedback) is the admin's once added. */}
            {promoted && (
              <Button variant="outline" disabled={remove.isPending} onClick={() => remove.mutate(promoted.id)}>
                {remove.isPending && <Loader2 className="animate-spin" data-icon="inline-start" />}
                {promoted.origin === "admin" ? "Remove from test set" : "Dismiss the suggestion"}
              </Button>
            )}
            {(!promoted || promoted.origin !== "admin") && (
              <Button onClick={() => onPromote(item)}>
                <FlaskConical data-icon="inline-start" /> Add to test set
              </Button>
            )}
          </SheetFooter>
        )}
      </SheetContent>
    </Sheet>
  )
}

// The thumbs-down answers (chat.md C17): what was asked, what was said, why it was
// rated down, and the documents it cited. Answers built from documents the
// viewer's role can't see are left out by the server.
export function InsightsFeedback({ canEdit }: { canEdit: boolean }) {
  const rated = useQuery(queries.ratedAnswers()).data
  const cases = useQuery(queries.evalCases()).data ?? []
  const [reason, setReason] = useState<"all" | FeedbackReason>("all")
  const [open, setOpen] = useState<RatedAnswer | null>(null)
  const [promoting, setPromoting] = useState<RatedAnswer | null>(null)
  const promoted = new Map(cases.map((c) => [c.message_id, c]))
  const shown = (rated ?? []).filter((f) => reason === "all" || f.reasons.includes(reason))

  return (
    <section className="rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-medium">Thumbs down</h3>
          <p className="text-sm text-muted-foreground">
            Answers people rated down, newest first, with their reasons. People are told an admin
            can see the answer and their note.
            {canEdit ? " Add one to the test set to check later releases against it." : ""}
          </p>
        </div>
        <Select value={reason} onValueChange={(v) => setReason(v as typeof reason)}>
          <SelectTrigger aria-label="Reason" className="w-full sm:w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Every reason</SelectItem>
            {(Object.keys(REASONS) as FeedbackReason[]).map((r) => (
              <SelectItem key={r} value={r}>
                {REASONS[r]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="mt-4">
        {!rated ? (
          <Skeleton className="h-48 rounded-2xl" />
        ) : !shown.length ? (
          <p className="rounded-xl border px-4 py-6 text-center text-sm text-muted-foreground">
            {rated.length ? "No answers rated down for that reason." : "No answers rated down yet."}
          </p>
        ) : (
          <div className="divide-y rounded-xl border">
            {shown.map((f) => (
              <button
                key={f.message_id}
                type="button"
                onClick={() => setOpen(f)}
                className="flex w-full flex-col gap-1.5 px-4 py-3 text-left text-sm outline-none transition-colors hover:bg-muted/40 focus-visible:bg-muted/40"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="line-clamp-2 font-medium break-words">{f.question}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{timeAgo(f.updated_at)}</span>
                </div>
                <span className="line-clamp-2 text-muted-foreground break-words">{f.answer}</span>
                <div className="flex flex-wrap items-center gap-1">
                  <Reasons item={f} />
                  {promoted.has(f.message_id) && (
                    <Badge variant="outline">
                      {promoted.get(f.message_id)?.origin === "admin" ? "In test set" : "Suggested for the test set"}
                    </Badge>
                  )}
                  {f.sources.length > 0 && (
                    <span className="text-xs text-muted-foreground">
                      {f.sources.length} {f.sources.length === 1 ? "source" : "sources"}
                    </span>
                  )}
                </div>
                {f.comment && <span className="line-clamp-1 text-xs text-muted-foreground">“{f.comment}”</span>}
              </button>
            ))}
          </div>
        )}
      </div>

      <AnswerSheet
        item={open}
        promoted={open ? promoted.get(open.message_id) : undefined}
        canEdit={canEdit}
        onClose={() => setOpen(null)}
        onPromote={(item) => setPromoting(item)}
      />
      <PromoteDialog item={promoting} onClose={() => setPromoting(null)} />
    </section>
  )
}
