import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowRight, BookOpen, Plus, Trash2 } from "lucide-react"
import { Link } from "react-router"
import { toast } from "sonner"

import { Panel } from "@/components/glass/panel"
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
    <Button size={size} variant="outline" onClick={toggle} loading={busy}>
      {busy ? null : sample.on ? (
        <Trash2 data-icon="inline-start" />
      ) : (
        <BookOpen data-icon="inline-start" />
      )}
      {sample.on ? "Remove sample documents" : "Try it with a sample HR handbook"}
    </Button>
  )
}

// A step's box, in mono: "[✓]" with the tick in the signal once it's done, "[ ]" until
// then. The brackets are decoration; the box is an image named Done or To do.
function Tick({ done }: { done: boolean }) {
  return (
    <span
      role="img"
      aria-label={done ? "Done" : "To do"}
      className="shrink-0 font-mono text-sm leading-5 whitespace-pre text-muted-foreground select-none"
    >
      [<span className={done ? "text-signal" : undefined}>{done ? "✓" : " "}</span>]
    </span>
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
    <Panel
      title="Set up your workspace"
      className="min-w-0"
      actions={
        <Button size="sm" variant="ghost" onClick={hide} disabled={hiding}>
          Done
        </Button>
      }
    >
      <p className="-mt-2 text-sm text-pretty text-muted-foreground">
        {left === 1 ? "One step left" : `${left} steps left`} to make it useful for everyone.
        Each one ticks itself off when it's done.
      </p>
      <ol className="mt-4 flex flex-col gap-4">
        {checklist.steps.map((s) => (
          <li key={s.id} className="flex flex-wrap items-start gap-3" data-step={s.id}>
            <Tick done={s.done} />
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
    </Panel>
  )
}
