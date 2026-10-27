import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router"
import {
  BadgeCheck,
  Globe,
  Loader2,
  Lock,
  MoreHorizontal,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react"
import { toast } from "sonner"

import { SourcePicker } from "@/components/source-picker"
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  assistant,
  type Access,
  type VerifiedAnswer,
  type VerifiedInput,
  type VerifiedList,
  type VerifiedStatus,
} from "@/lib/api"
import { queries } from "@/lib/queries"

// /admin/verified (HR and admins): canonical answers to the questions everyone asks
// (platform.md PL-27). A question that matches one closely gets it in the chat, with
// "Verified by <name> · <date>", instead of a written answer. Each has an owner and a
// review date; past it, it isn't served until someone marks it reviewed.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const verifiedDate = (epoch: number) =>
  new Date(epoch * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })

const STATUS: Record<VerifiedStatus, { label: string; className: string }> = {
  current: { label: "Current", className: "text-muted-foreground" },
  due: { label: "Review due", className: "border-foreground/30 text-foreground" },
  expired: { label: "Expired", className: "text-destructive" },
}

// The answer's Markdown as plain words, for the one-line preview.
const plainText = (md: string) =>
  md
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`#>]+/g, "")
    .replace(/\s+/g, " ")
    .trim()

function StatusBadge({ status }: { status: VerifiedStatus }) {
  return (
    <Badge variant="outline" className={`font-normal ${STATUS[status].className}`}>
      {STATUS[status].label}
    </Badge>
  )
}

function AccessLabel({ access }: { access: Access }) {
  return access === "restricted" ? (
    <span className="flex items-center gap-1">
      <Lock className="size-3.5" /> HR and admins
    </span>
  ) : (
    <span className="flex items-center gap-1">
      <Globe className="size-3.5" /> Everyone
    </span>
  )
}

// What the editor holds while it's open: a new answer (maybe from a draft) or an edit.
interface Editing {
  id: string | null
  form: VerifiedInput
  minAccess: Access
  known: VerifiedAnswer["sources"]
}

const blank = (list?: VerifiedList): VerifiedInput => ({
  title: "",
  variants: [""],
  answer_md: "",
  sources: [],
  access: "restricted",
  review_days: list?.default_review_days ?? 90,
})

function Editor({
  editing,
  list,
  onClose,
}: {
  editing: Editing | null
  list: VerifiedList | undefined
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<VerifiedInput>(() => editing?.form ?? blank(list))
  const set = (change: Partial<VerifiedInput>) => setForm((f) => ({ ...f, ...change }))
  const save = useMutation({
    mutationFn: () => {
      const body = { ...form, variants: form.variants.map((v) => v.trim()).filter(Boolean) }
      return editing?.id ? assistant.updateVerified(editing.id, body) : assistant.createVerified(body)
    },
    onSuccess: (saved) => {
      toast.success(
        saved.access !== form.access
          ? "Saved. It's for HR and admins only: a source or the answer it came from is."
          : editing?.id
            ? "Saved, and verified again today."
            : "Verified answer added.",
      )
      void queryClient.invalidateQueries({ queryKey: ["verified-answers"] })
      onClose()
    },
    onError: (err) => toast.error(errorText(err)),
  })
  const restrictedOnly = editing?.minAccess === "restricted"

  return (
    <Sheet open={editing !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 data-[side=right]:w-full sm:max-w-xl">
        <SheetHeader className="border-b">
          <SheetTitle>{editing?.id ? "Edit verified answer" : "New verified answer"}</SheetTitle>
          <SheetDescription>
            Questions like these get this answer, word for word, with your name and today's date.
            Saving it verifies it again.
          </SheetDescription>
        </SheetHeader>
        <form
          id="verified-form"
          className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate()
          }}
        >
          <FieldGroup className="gap-5">
            <Field>
              <FieldLabel htmlFor="va-title">Title</FieldLabel>
              <Input
                id="va-title"
                value={form.title}
                maxLength={200}
                onChange={(e) => set({ title: e.target.value })}
                placeholder="Annual leave allowance"
                required
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="va-q0">Questions it answers</FieldLabel>
              <FieldDescription>
                Add the ways people ask it. A question has to be very close to one of these (or
                the title) to get this answer.
              </FieldDescription>
              <div className="flex flex-col gap-2">
                {form.variants.map((v, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <Input
                      id={`va-q${i}`}
                      value={v}
                      maxLength={300}
                      aria-label={`Question ${i + 1}`}
                      onChange={(e) =>
                        set({ variants: form.variants.map((x, j) => (j === i ? e.target.value : x)) })
                      }
                      placeholder="How many days of leave do I get?"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Remove question ${i + 1}`}
                      disabled={form.variants.length === 1}
                      onClick={() => set({ variants: form.variants.filter((_, j) => j !== i) })}
                    >
                      <X />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="self-start"
                  disabled={form.variants.length >= 20}
                  onClick={() => set({ variants: [...form.variants, ""] })}
                >
                  <Plus data-icon="inline-start" /> Add a question
                </Button>
              </div>
            </Field>
            <Field>
              <FieldLabel htmlFor="va-answer">Answer</FieldLabel>
              <Textarea
                id="va-answer"
                value={form.answer_md}
                maxLength={8000}
                rows={8}
                onChange={(e) => set({ answer_md: e.target.value })}
                placeholder="Everyone gets 20 days of annual leave a year…"
                required
              />
              <FieldDescription>Markdown works: lists, bold, links.</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="va-sources">Sources</FieldLabel>
              <FieldDescription>
                The documents it rests on, listed under the answer. Someone who can't see one of
                them doesn't get this answer.
              </FieldDescription>
              <SourcePicker
                id="va-sources"
                value={form.sources}
                known={editing?.known ?? []}
                onChange={(sources) => set({ sources })}
              />
            </Field>
            <div className="grid gap-5 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="va-access">Who gets it</FieldLabel>
                <Select value={form.access} onValueChange={(v) => set({ access: v as Access })}>
                  <SelectTrigger id="va-access" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="public" disabled={restrictedOnly}>
                      Everyone
                    </SelectItem>
                    <SelectItem value="restricted">HR and admins only</SelectItem>
                  </SelectContent>
                </Select>
                {restrictedOnly && (
                  <FieldDescription>The answer it came from read HR-only documents.</FieldDescription>
                )}
              </Field>
              <Field>
                <FieldLabel htmlFor="va-review">Review every</FieldLabel>
                <Select
                  value={String(form.review_days ?? 90)}
                  onValueChange={(v) => set({ review_days: Number(v) })}
                >
                  <SelectTrigger id="va-review" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(list?.review_days ?? [30, 60, 90, 180, 365]).map((d) => (
                      <SelectItem key={d} value={String(d)}>
                        {d} days
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>
            <Field>
              <FieldLabel htmlFor="va-owner">Owner</FieldLabel>
              <Select value={form.owner ?? ""} onValueChange={(owner) => set({ owner })}>
                <SelectTrigger id="va-owner" className="w-full">
                  <SelectValue placeholder="You" />
                </SelectTrigger>
                <SelectContent>
                  {(list?.owners ?? []).map((o) => (
                    <SelectItem key={o.username} value={o.username}>
                      {o.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FieldDescription>
                Emailed when it's due for review, and again if it expires.
              </FieldDescription>
            </Field>
          </FieldGroup>
        </form>
        <SheetFooter className="border-t sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="verified-form" disabled={save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" data-icon="inline-start" />}
            {editing?.id ? "Save and verify" : "Add verified answer"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}

function AnswerRow({
  answer,
  onEdit,
  onDelete,
}: {
  answer: VerifiedAnswer
  onEdit: (a: VerifiedAnswer) => void
  onDelete: (a: VerifiedAnswer) => void
}) {
  const queryClient = useQueryClient()
  const review = useMutation({
    mutationFn: () => assistant.reviewVerified(answer.id),
    onSuccess: () => {
      toast.success("Marked reviewed. Its next review starts today.")
      void queryClient.invalidateQueries({ queryKey: ["verified-answers"] })
    },
    onError: (err) => toast.error(errorText(err)),
  })
  return (
    <li className="flex items-start gap-3 px-4 py-3.5">
      <BadgeCheck className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <button
            type="button"
            onClick={() => onEdit(answer)}
            className="min-w-0 truncate text-left text-sm font-medium hover:underline"
          >
            {answer.title}
          </button>
          <StatusBadge status={answer.status} />
        </div>
        <p className="line-clamp-2 text-sm text-muted-foreground">{plainText(answer.answer_md)}</p>
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <span>
            Verified by {answer.verified_by_name} · {verifiedDate(answer.verified_at)}
          </span>
          <span>
            {answer.status === "expired" ? "Expired" : "Review by"} {verifiedDate(answer.due_at)}
          </span>
          <span>Owner {answer.owner_name}</span>
          <AccessLabel access={answer.access} />
          <span>
            {answer.variants.length} question{answer.variants.length === 1 ? "" : "s"} ·{" "}
            {answer.sources.length} source{answer.sources.length === 1 ? "" : "s"}
          </span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {answer.status !== "current" && (
          <Button variant="outline" size="sm" disabled={review.isPending} onClick={() => review.mutate()}>
            {review.isPending ? <Loader2 className="animate-spin" data-icon="inline-start" /> : <RotateCcw data-icon="inline-start" />}
            <span className="hidden sm:inline">Mark reviewed</span>
            <span className="sm:hidden">Reviewed</span>
          </Button>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${answer.title}`}>
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => onEdit(answer)}>
              <Pencil /> Edit
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => review.mutate()}>
              <RotateCcw /> Mark reviewed
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={() => onDelete(answer)}>
              <Trash2 /> Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </li>
  )
}

const editingOf = (a: VerifiedAnswer): Editing => ({
  id: a.id,
  form: {
    title: a.title,
    variants: a.variants,
    answer_md: a.answer_md,
    sources: a.sources.map((s) => s.doc_key),
    access: a.access,
    owner: a.owner,
    review_days: a.review_days,
  },
  minAccess: a.min_access,
  known: a.sources,
})

export function VerifiedView() {
  const queryClient = useQueryClient()
  const { data: list } = useQuery(queries.verifiedAnswers())
  const [params, setParams] = useSearchParams()
  const [tab, setTab] = useState<"all" | "review">("all")
  const [editing, setEditing] = useState<Editing | null>(null)
  const [deleting, setDeleting] = useState<VerifiedAnswer | null>(null)

  const edit = (a: VerifiedAnswer) => setEditing(editingOf(a))

  // Arriving with ?from_message= (a chat answer), ?from_gap= (a content gap) or
  // ?answer= (the review email's link): open the editor on it, once.
  const opened = useRef(false)
  useEffect(() => {
    if (opened.current) return
    const message = params.get("from_message")
    const gap = params.get("from_gap")
    const answer = params.get("answer")
    if (!message && !gap && !answer) return
    opened.current = true
    setParams({}, { replace: true })
    if (answer) {
      queryClient
        .fetchQuery(queries.verifiedAnswers())
        .then((all) => {
          const found = all.answers.find((a) => a.id === answer)
          if (found) setEditing(editingOf(found))
          else toast.error("That verified answer isn't there any more.")
        })
        .catch((err) => toast.error(errorText(err)))
      return
    }
    assistant
      .verifiedDraft(message ? { messageId: message } : { gapId: Number(gap) })
      .then((draft) =>
        setEditing({
          id: null,
          form: { ...draft, variants: draft.variants.length ? draft.variants : [""], review_days: 90 },
          minAccess: draft.min_access,
          known: [],
        }),
      )
      .catch((err) => toast.error(errorText(err)))
  }, [params, setParams, queryClient])

  const remove = useMutation({
    mutationFn: (id: string) => assistant.deleteVerified(id),
    onSuccess: () => {
      toast.success("Verified answer deleted.")
      void queryClient.invalidateQueries({ queryKey: ["verified-answers"] })
    },
    onError: (err) => toast.error(errorText(err)),
  })

  const answers = list?.answers ?? null
  const queue = (answers ?? []).filter((a) => a.status !== "current")
  const shown = tab === "review" ? queue : (answers ?? [])

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-2xl font-semibold tracking-tight">Verified answers</h2>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Approved answers to the questions everyone asks. A question that matches one gets it,
            marked “Verified by” with the date, instead of a written answer. Past its review date
            it isn't used until it's reviewed.
          </p>
        </div>
        <Button
          onClick={() =>
            setEditing({ id: null, form: blank(list), minAccess: "public", known: [] })
          }
        >
          <Plus data-icon="inline-start" /> New verified answer
        </Button>
      </div>

      <Tabs panels={false} value={tab} onValueChange={(v) => setTab(v as typeof tab)} className="mt-6">
        <TabsList>
          <TabsTrigger value="all">All{answers ? ` (${answers.length})` : ""}</TabsTrigger>
          <TabsTrigger value="review">Review queue{answers ? ` (${queue.length})` : ""}</TabsTrigger>
        </TabsList>
      </Tabs>

      <div className="mt-4 overflow-hidden rounded-2xl border border-border/70">
        {answers === null ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : shown.length === 0 ? (
          <p className="p-10 text-center text-sm text-muted-foreground">
            {tab === "review"
              ? `Nothing to review. Answers show here ${list?.notice_days ?? 7} days before their review date.`
              : "No verified answers yet. Add one here, or from any answer in the chat (More › Save as verified answer) or a content gap on Insights."}
          </p>
        ) : (
          <ul className="divide-y divide-border/70">
            {shown.map((a) => (
              <AnswerRow key={a.id} answer={a} onEdit={edit} onDelete={setDeleting} />
            ))}
          </ul>
        )}
      </div>

      {editing && <Editor key={editing.id ?? "new"} editing={editing} list={list} onClose={() => setEditing(null)} />}

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.title}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Questions it answered get a written answer from the documents again. This can't be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => deleting && remove.mutate(deleting.id)}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
