import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowRight, BookOpen, CheckCircle2, Circle, Loader2, Plus, Trash2 } from "lucide-react"
import { Link } from "react-router"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { onboarding, type OnboardingStepId, type SampleDocs } from "@/lib/api"
import { queries } from "@/lib/queries"

// What each step means and where to go for it. Whether it's done comes from the
// server (kb/services/onboarding.py), worked out from the workspace's data, so it's
// the same for every admin in every browser.
const STEP_COPY: Record<OnboardingStepId, string> = {
  domain: "Add it under Email domains below, then add the DNS record it shows.",
  source:
    "Google Drive, Notion and more, or upload 3 files: what your colleagues' questions are answered from.",
  index: "The first index runs once documents are in. It takes a few minutes.",
  answer: "Ask something in the chat and get an answer that cites one of your documents.",
  colleagues: "Invite colleagues, or tell them to sign in with their work email.",
  idp: "Optional. If you use Okta, Microsoft Entra ID or any SAML provider, add it so access follows your directory.",
  slack: "Optional. People ask in Slack and get the same cited answers.",
}

// "Try it with a sample HR handbook": three made-up policies, indexed for everyone,
// and gone from every answer the moment they're removed. HR and admins.
export function SampleDocsToggle({
  sample,
  size = "sm",
}: {
  sample: SampleDocs
  size?: "sm" | "default"
}) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  if (!sample.available && !sample.on) return null

  async function toggle() {
    setBusy(true)
    try {
      if (sample.on) {
        await onboarding.removeSample()
        toast.success("Sample documents removed. No answer will cite them.")
      } else {
        await onboarding.addSample()
        toast.success("Sample HR handbook added. It's being indexed now.")
      }
      void queryClient.invalidateQueries({ queryKey: ["onboarding"] })
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] })
      void queryClient.invalidateQueries({ queryKey: queries.indexStatus().queryKey })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't change the sample documents.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Button size={size} variant="outline" onClick={toggle} disabled={busy}>
      {busy ? (
        <Loader2 className="animate-spin" data-icon="inline-start" />
      ) : sample.on ? (
        <Trash2 data-icon="inline-start" />
      ) : (
        <BookOpen data-icon="inline-start" />
      )}
      {sample.on ? "Remove sample documents" : "Try it with a sample HR handbook"}
    </Button>
  )
}

// The Access screen's "Set up your workspace": until every step that isn't optional
// is done, or an admin puts it away (for the whole workspace).
export function OnboardingChecklist({ onAddProvider }: { onAddProvider: () => void }) {
  const queryClient = useQueryClient()
  const checklist = useQuery(queries.onboarding()).data?.checklist
  const [hiding, setHiding] = useState(false)
  if (!checklist || checklist.dismissed || checklist.complete) return null

  async function hide() {
    setHiding(true)
    try {
      await onboarding.dismiss(true)
      void queryClient.invalidateQueries({ queryKey: ["onboarding"] })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't put the checklist away.")
      setHiding(false)
    }
  }

  const actions: Partial<Record<OnboardingStepId, React.ReactNode>> = {
    source: (
      <Button size="sm" variant="outline" asChild>
        <Link to="/integrations">
          Open Integrations <ArrowRight data-icon="inline-end" />
        </Link>
      </Button>
    ),
    answer: (
      <Button size="sm" variant="outline" asChild>
        <Link to="/chat">
          Ask a question <ArrowRight data-icon="inline-end" />
        </Link>
      </Button>
    ),
    colleagues: (
      <Button size="sm" variant="outline" asChild>
        <Link to="/admin/members">
          Invite people <ArrowRight data-icon="inline-end" />
        </Link>
      </Button>
    ),
    idp: (
      <Button size="sm" variant="outline" onClick={onAddProvider}>
        <Plus data-icon="inline-start" /> Add provider
      </Button>
    ),
    slack: (
      <Button size="sm" variant="outline" asChild>
        <Link to="/integrations">
          Connect Slack <ArrowRight data-icon="inline-end" />
        </Link>
      </Button>
    ),
  }
  const left = checklist.steps.filter((s) => !s.optional && !s.done).length

  return (
    <section
      aria-label="Set up your workspace"
      className="rounded-2xl border border-border/70 bg-background p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-medium">Set up your workspace</h3>
          <p className="text-sm text-muted-foreground">
            {left === 1 ? "One step left" : `${left} steps left`} to make it useful for everyone.
            Each one ticks itself off when it's done.
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={hide} disabled={hiding}>
          Done
        </Button>
      </div>
      <ol className="mt-4 flex flex-col gap-4">
        {checklist.steps.map((s) => (
          <li key={s.id} className="flex flex-wrap items-start gap-3" data-step={s.id}>
            {s.done ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" aria-label="Done" />
            ) : (
              <Circle className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="To do" />
            )}
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{s.title}</p>
              {!s.done && <p className="text-sm text-muted-foreground">{STEP_COPY[s.id]}</p>}
            </div>
            {!s.done && actions[s.id]}
          </li>
        ))}
      </ol>
      {(checklist.sample.available || checklist.sample.on) && (
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-4">
          <p className="min-w-0 flex-1 text-sm text-muted-foreground">
            {checklist.sample.on
              ? "The sample HR handbook is in this workspace. Remove it before real colleagues ask."
              : "No documents yet? Try the assistant on a made-up HR handbook, and remove it in one click."}
          </p>
          <SampleDocsToggle sample={checklist.sample} />
        </div>
      )}
    </section>
  )
}
