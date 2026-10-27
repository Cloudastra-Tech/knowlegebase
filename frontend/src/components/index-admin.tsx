import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { CircleAlert, CircleCheck, Clock, ListChecks, MoreHorizontal, RotateCcw } from "lucide-react"
import { toast } from "sonner"

import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
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
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type WorkspaceJob } from "@/lib/api"
import { describeReason, jobKind } from "@/lib/index-reasons"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

// Admins' index tools on the Documents screen: rebuild the whole index from scratch
// (POST /api/reindex?full=true), and the workspace's recent background jobs.

const STATUS: Record<WorkspaceJob["status"], string> = {
  queued: "Waiting",
  running: "Running",
  done: "Done",
  failed: "Failed",
}

function JobIcon({ status }: { status: WorkspaceJob["status"] }) {
  if (status === "running") return <AsciiSpinner label="" className="text-muted-foreground" />
  if (status === "failed") return <CircleAlert className="size-4 text-destructive" />
  if (status === "queued") return <Clock className="size-4 text-muted-foreground" />
  return <CircleCheck className="size-4 text-success" />
}

function detailOf(job: WorkspaceJob) {
  const parts = []
  if (job.details.full) parts.push("from scratch")
  if (job.details.provider) parts.push(job.details.provider.replace(/_/g, " "))
  if (job.attempts > 1) parts.push(`try ${job.attempts} of ${job.max_attempts}`)
  return parts.join(" · ")
}

function JobRow({ job }: { job: WorkspaceJob }) {
  const when = job.finished_at ?? job.started_at ?? job.created_at
  // An index job's error in plain words first (the same as the header's).
  const plain =
    job.reason && job.status !== "done"
      ? describeReason(job.reason, job.status !== "failed").sentence
      : null
  return (
    <li className="flex gap-3 py-3">
      <span className="mt-0.5" aria-hidden>
        <JobIcon status={job.status} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-sm font-medium">{jobKind(job.kind)}</span>
          <Badge
            variant={job.status === "failed" ? "destructive" : "outline"}
            className="font-normal"
          >
            {STATUS[job.status]}
          </Badge>
          <span className="font-mono text-xs text-muted-foreground tabular-nums">{timeAgo(when)}</span>
        </div>
        {detailOf(job) && <p className="text-sm text-muted-foreground">{detailOf(job)}</p>}
        {job.progress && <p className="text-sm text-muted-foreground">{job.progress}</p>}
        {plain && <p className="mt-1 text-sm text-destructive">{plain}</p>}
        {job.error && (
          <Collapsible>
            <CollapsibleTrigger asChild>
              <Button variant="link" size="sm" className="h-auto p-0 text-xs text-muted-foreground">
                Error details
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <p className="mt-1 rounded-lg bg-muted px-2 py-1.5 font-mono text-xs break-words">
                {job.error}
              </p>
            </CollapsibleContent>
          </Collapsible>
        )}
      </div>
    </li>
  )
}

// The workspace's last background jobs, newest first; looked at again every few
// seconds while one is waiting or running.
export function JobsSheet({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { data, isError } = useQuery({
    ...queries.jobs(),
    enabled: open,
    refetchInterval: (q) =>
      open && q.state.data?.some((j) => j.status === "queued" || j.status === "running")
        ? 3000
        : false,
  })
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 sm:max-w-md">
        <SheetHeader className="border-b">
          <SheetTitle>Background jobs</SheetTitle>
          <SheetDescription>
            Indexing, syncs, digests and drafts in this workspace, newest first. Finished jobs
            are kept for a week.
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-4">
          {isError ? (
            <AsciiEmptyState art="broken" size="sm" title="Couldn't load the jobs." />
          ) : !data ? (
            <div className="space-y-3 py-4">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : data.length === 0 ? (
            <AsciiEmptyState art="inbox" size="sm" title="No jobs yet." />
          ) : (
            <ul className="divide-y" aria-label="Jobs">
              {data.map((job) => (
                <JobRow key={job.id} job={job} />
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

// "Rebuild index from scratch": every document is read and embedded again, which costs
// about what indexing them the first time did, so it asks first.
export function RebuildIndexDialog({
  open,
  onOpenChange,
  documents,
  onStarted,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  documents: number
  onStarted: () => void
}) {
  const [busy, setBusy] = useState(false)
  async function rebuild() {
    setBusy(true)
    try {
      await api.reindex(true)
      toast.success("Rebuilding the index. Answers come from the current one until it's done.")
      onOpenChange(false)
      onStarted()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't start the rebuild.")
    } finally {
      setBusy(false)
    }
  }
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Rebuild the index from scratch?</AlertDialogTitle>
          <AlertDialogDescription>
            Every document is read and embedded again by your AI provider, which costs about as
            much as indexing them the first time
            {documents > 0 ? ` (${documents} uploaded document${documents === 1 ? "" : "s"}, plus synced sources)` : ""}
            , and can take a while. Answers keep coming from the current index until the new one
            is ready. Usually "Re-index now" is enough: it embeds only what changed.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={busy}
            onClick={(e) => {
              e.preventDefault()
              void rebuild()
            }}
          >
            {busy && (
              <span data-icon="inline-start" className="inline-flex">
                <AsciiSpinner label="" size="xs" />
              </span>
            )}
            Rebuild from scratch
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

// The admin's menu beside "Re-index now".
export function IndexAdminMenu({
  documents,
  indexing,
  jobsOpen,
  onJobsOpenChange,
  onStarted,
}: {
  documents: number
  indexing: boolean
  jobsOpen: boolean
  onJobsOpenChange: (open: boolean) => void
  onStarted: () => void
}) {
  const [rebuilding, setRebuilding] = useState(false)
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="icon" aria-label="More index actions">
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuItem onSelect={() => onJobsOpenChange(true)}>
            <ListChecks /> Background jobs
          </DropdownMenuItem>
          <DropdownMenuItem disabled={indexing} onSelect={() => setRebuilding(true)}>
            <RotateCcw /> Rebuild index from scratch…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <RebuildIndexDialog
        open={rebuilding}
        onOpenChange={setRebuilding}
        documents={documents}
        onStarted={onStarted}
      />
      <JobsSheet open={jobsOpen} onOpenChange={onJobsOpenChange} />
    </>
  )
}
