import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router"
import { Link2Off, Lock, MessageSquarePlus } from "lucide-react"
import { toast } from "sonner"

import { Breadcrumbs } from "@/components/breadcrumbs"
import { AssistantMessage, type AnswerActions, type Message } from "@/components/chat-answer"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api, ApiError, type SharedChat } from "@/lib/api"
import { breadcrumbs } from "@/lib/breadcrumbs"
import { queries } from "@/lib/queries"

function messagesOf(shared: SharedChat): Message[] {
  return shared.messages.map((m) =>
    m.author === "user"
      ? { key: m.id, id: m.id, role: "user", text: m.content }
      : {
          key: m.id,
          id: m.id,
          role: "assistant",
          text: m.content,
          state: m.state ?? "pending",
          question: m.question ?? undefined,
          sources: m.sources,
          withheld: m.withheld,
          copy: m.copy,
          steps: (m.steps ?? []).map((s) => ({ ...s, done: true })),
        },
  )
}

const day = (t: number) =>
  new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })

// /s/:token: a chat or an answer someone in the workspace shared (C16). Read only.
// An answer built from documents the viewer's role can't read is withheld whole,
// with "Ask it yourself", which asks the question in a new chat with their own role.
// "Continue in my chats" copies what the viewer sees here into a chat of their own
// (the server leaves out withheld answers and their questions) to ask on from.
export function SharedView({ token }: { token: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const shared = useQuery(queries.sharedChat(token))
  const fork = useMutation({
    mutationFn: () => api.continueShare(token),
    onSuccess: ({ id, skipped }) => {
      void queryClient.invalidateQueries({ queryKey: ["conversations"] })
      if (skipped)
        toast.info(
          skipped === 1
            ? "One answer you can't see was left out."
            : `${skipped} answers you can't see were left out.`,
        )
      navigate(`/chat/${id}`)
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't copy the chat."),
  })
  const actions: AnswerActions = {
    onAskYourself: (question) => navigate(`/chat?q=${encodeURIComponent(question)}`),
  }

  if (shared.isPending) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 px-4 py-8">
        <Skeleton className="h-7 w-2/3" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }
  if (shared.isError) {
    const gone = shared.error instanceof ApiError && shared.error.status === 410
    return (
      <div className="mx-auto max-w-md px-4 py-16">
        <Alert>
          <Link2Off />
          <AlertTitle>{gone ? "This link doesn't work any more" : "Link not found"}</AlertTitle>
          <AlertDescription>
            <p>
              {gone
                ? "It expired or was turned off, or sharing chats is off in your workspace."
                : "It may be mistyped, or it was shared in another workspace."}
            </p>
            <Button variant="outline" size="sm" className="mt-3" onClick={() => navigate("/chat")}>
              Go to your chats
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    )
  }
  const data = shared.data
  const trail = breadcrumbs(`/s/${token}`, "", {
    name: data.kind === "answer" ? "A shared answer" : "A shared chat",
  })
  // Something to carry on from: a finished answer the viewer can see.
  const continuable = data.messages.some(
    (m) => m.author === "assistant" && !m.withheld && m.state !== "pending" && m.state !== "error",
  )
  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
      <header className="space-y-1 border-b pb-4">
        <Breadcrumbs trail={trail} />
        <h1 className="text-xl font-semibold tracking-tight text-balance">{data.title}</h1>
        <p className="text-sm text-muted-foreground">
          Shared by {data.shared_by} · {day(data.created_at)}
          {data.expires_at ? ` · link works until ${day(data.expires_at)}` : ""}
        </p>
        {continuable && (
          <div className="pt-2" data-print="hide">
            <Button variant="outline" size="sm" loading={fork.isPending} onClick={() => fork.mutate()}>
              {!fork.isPending && <MessageSquarePlus data-icon="inline-start" />}
              Continue in my chats
            </Button>
          </div>
        )}
      </header>
      {data.withheld > 0 && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Lock className="size-4 shrink-0" aria-hidden />
          {data.withheld === 1 ? "One answer is" : `${data.withheld} answers are`} hidden: you don't
          have access to some of the sources.
        </p>
      )}
      <div className="space-y-6" role="log" aria-label="Shared conversation">
        {messagesOf(data).map((m) =>
          m.role === "user" ? (
            <div key={m.key} className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-primary px-4 py-2.5 text-sm text-primary-foreground">
                {m.text}
              </div>
            </div>
          ) : (
            <AssistantMessage key={m.key} message={m} actions={actions} />
          ),
        )}
      </div>
    </div>
  )
}
