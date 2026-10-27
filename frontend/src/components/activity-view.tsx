import { useEffect, useState, type ReactNode } from "react"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { Download, Search, ShieldAlert, ShieldCheck } from "lucide-react"
import { toast } from "sonner"

import { DateRangePicker, type DateRange } from "@/components/date-range-picker"
import { Panel } from "@/components/glass/panel"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { signIn, type AuditEvent, type AuditFilters } from "@/lib/api"
import { rangeToEpoch } from "@/lib/date-range"
import { timeAgo } from "@/lib/utils"

// What each action means, in the past tense.
const ACTION: Record<string, string> = {
  "sign_in.policy": "Changed “require single sign-on”",
  "sign_in.group_admin_days": "Changed how long admin from groups lasts",
  "domain.add": "Added a domain",
  "domain.verify": "Verified a domain",
  "domain.remove": "Removed a domain",
  "connection.create": "Added a sign-in provider",
  "connection.update": "Changed a sign-in provider",
  "connection.roles": "Changed roles from groups",
  "connection.test": "Tested a sign-in provider",
  "connection.delete": "Deleted a sign-in provider",
  "member.update": "Changed a member's access",
  "member.remove": "Removed a member",
  "member.sign_out": "Signed a member out everywhere",
  "member.add": "Added a member",
  "member.password_set": "Reset a password",
  "member.role": "Set a member's role",
  "member.disable": "Turned a member's access off",
  "member.enable": "Turned a member's access on",
  "scim.token.create": "Made a SCIM token",
  "scim.token.revoke": "Revoked a SCIM token",
  "scim.roles": "Changed roles from SCIM groups",
  "scim.user.create": "Provisioned a member",
  "scim.user.update": "Updated a provisioned member",
  "scim.user.deactivate": "Turned a member's access off (SCIM)",
  "scim.user.reactivate": "Turned a member's access on (SCIM)",
  "scim.user.delete": "Deleted a member (SCIM)",
  "scim.user.anonymise": "Anonymised a deleted member",
  "scim.group.create": "Pushed a group",
  "scim.group.update": "Changed a group or its members",
  "scim.group.delete": "Removed a group",
  "invite.create": "Invited someone",
  "invite.resend": "Resent an invite",
  "invite.revoke": "Withdrew an invite",
  "invite.accept": "Accepted an invite",
  "doc.create": "Created a document",
  "doc.update": "Edited a document",
  "doc.delete": "Deleted a document",
  "doc.upload": "Uploaded documents",
  "index.reindex": "Started a re-index",
  "integration.connect_start": "Started connecting an integration",
  "integration.connect": "Connected an integration",
  "integration.folders": "Chose what to sync",
  "integration.access": "Changed who can ask about synced files",
  "integration.sync": "Synced an integration",
  "integration.disconnect": "Disconnected an integration",
  "conversation.create": "Started a conversation",
  "conversation.rename": "Renamed a conversation",
  "conversation.delete": "Deleted a conversation",
  "conversation.organize": "Pinned or filed a conversation",
  "conversation.fork": "Branched a conversation into a new one",
  "conversation_folder.create": "Made a chat folder",
  "conversation_folder.rename": "Renamed a chat folder",
  "conversation_folder.delete": "Deleted a chat folder",
  "share.create": "Shared a chat by link",
  "share.revoke": "Turned a share link off",
  "chat_settings.update": "Changed how long chats are kept or sharing",
  "chat.retention_purge": "Deleted chats past retention",
  "feedback.set": "Rated an answer",
  "feedback.delete": "Took back a rating",
  "workspace.create": "Created the workspace",
  "session.sign_out_everywhere": "Signed out everywhere",
  "audit.export": "Exported the audit log",
  "question_log.settings": "Changed how questions are logged",
  "insights.export": "Exported insights",
  "eval_case.create": "Added an answer to the test set",
  "eval_case.delete": "Removed an answer from the test set",
  "webhook.create": "Added a webhook",
  "webhook.update": "Changed a webhook",
  "webhook.delete": "Deleted a webhook",
  "webhook.test": "Sent a webhook a test event",
  "webhook.rotate_secret": "Rotated a webhook's secret",
  "webhook.redeliver": "Sent a webhook delivery again",
  "webhook.auto_disabled": "Turned a failing webhook off",
}

// The action filter: every action under a prefix.
const KINDS: [string, string][] = [
  ["all", "All actions"],
  ["member.", "Members"],
  ["invite.", "Invites"],
  ["sign_in.", "Sign-in rules"],
  ["domain.", "Domains"],
  ["connection.", "Sign-in providers"],
  ["doc.", "Documents"],
  ["integration.", "Integrations"],
  ["index.", "Re-indexing"],
  ["conversation.", "Conversations"],
  ["share.", "Share links"],
  ["feedback.", "Answer ratings"],
  ["audit.", "Audit log"],
  ["question_log.", "Question logging"],
  ["insights.", "Insights"],
  ["eval_case.", "Test set"],
  ["webhook.", "Webhooks"],
]

function details(e: AuditEvent) {
  return Object.entries(e.details)
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ")
}

// What was changed: "invite #3", or the member, document, domain... itself.
function target(e: AuditEvent) {
  if (!e.target_id) return ""
  return e.target_type === "invite" ? `invite #${e.target_id}` : e.target_id
}

function when(epoch: number) {
  return new Date(epoch * 1000).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  })
}

function useDebounced<T>(value: T, ms = 350) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(t)
  }, [value, ms])
  return debounced
}

function ChainStatus() {
  const verify = useQuery({ queryKey: ["audit-verify"], queryFn: signIn.verifyAudit })
  if (!verify.data) return null
  const { ok, count } = verify.data
  return (
    <p
      className={
        ok
          ? "flex items-center gap-1.5 text-xs text-muted-foreground"
          : "flex items-center gap-1.5 text-xs text-destructive"
      }
    >
      {ok ? <ShieldCheck className="size-3.5" /> : <ShieldAlert className="size-3.5" />}
      {ok
        ? `${count.toLocaleString()} ${count === 1 ? "change" : "changes"}, chain intact`
        : `The log's hash chain is broken at entry ${verify.data.broken_at}: a row was changed or removed.`}
    </p>
  )
}

function AdminChanges() {
  const [actor, setActor] = useState("")
  const [kind, setKind] = useState("all")
  const [range, setRange] = useState<DateRange | undefined>()
  const [exporting, setExporting] = useState(false)
  const debouncedActor = useDebounced(actor.trim())
  const filters: AuditFilters = {
    actor: debouncedActor || undefined,
    action: kind === "all" ? undefined : kind,
    ...rangeToEpoch(range),
  }

  const log = useInfiniteQuery({
    queryKey: ["audit", filters],
    queryFn: ({ pageParam }) => signIn.audit(filters, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,
  })
  const events = log.data?.pages.flatMap((p) => p.events) ?? null

  async function exportAs(format: "csv" | "jsonl") {
    setExporting(true)
    try {
      const { blob, filename } = await signIn.exportAudit(filters, format)
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't export the log")
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 basis-40 sm:flex-none">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="Who"
            placeholder="Who"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
            className="w-full pl-8 sm:w-44"
          />
        </div>
        <Select value={kind} onValueChange={setKind}>
          <SelectTrigger aria-label="Action" className="min-w-0 flex-1 basis-40 sm:w-44 sm:flex-none">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KINDS.map(([value, name]) => (
              <SelectItem key={value} value={value}>
                {name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <DateRangePicker value={range} onChange={setRange} className="min-w-0 basis-56 grow sm:w-64 sm:grow-0" />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="sm:ml-auto" loading={exporting}>
              {!exporting && <Download data-icon="inline-start" />}
              Export
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => exportAs("csv")}>CSV (spreadsheets)</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => exportAs("jsonl")}>
              JSON lines (with hashes)
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {!events ? (
        <Skeleton className="h-48 rounded-2xl" />
      ) : !events.length ? (
        // A log: no ASCII here, empty or not (DESIGN.md, "Where the ASCII never goes").
        <p className="rounded-xl border bg-card px-4 py-6 text-center text-sm text-muted-foreground">
          {filters.actor || filters.action || filters.from
            ? "No changes match."
            : "No changes yet. Changes to sign-in, members, documents and integrations show here."}
        </p>
      ) : (
        <>
        {/* Phones: one entry per row, stacked. */}
        <div className="divide-y rounded-xl border bg-card sm:hidden">
          {events.map((e) => (
            <div key={e.id} className="flex flex-col gap-0.5 px-4 py-3 text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-medium">{ACTION[e.action] ?? e.action}</span>
                <span className="shrink-0 font-mono text-xs text-muted-foreground" title={when(e.at)}>
                  {timeAgo(e.at)}
                </span>
              </div>
              <span className="truncate text-muted-foreground">
                <span className="font-mono text-xs">{e.actor ?? "unknown"}</span>
                {e.target_id && ` · ${target(e)}`}
              </span>
              {details(e) && (
                <span className="line-clamp-2 text-xs break-words text-muted-foreground">
                  {details(e)}
                </span>
              )}
            </div>
          ))}
        </div>
        <div className="hidden overflow-hidden rounded-xl border sm:block">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-40 pl-4">When</TableHead>
                <TableHead>Who</TableHead>
                <TableHead>What</TableHead>
                <TableHead className="pr-4">Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.map((e) => (
                <TableRow key={e.id}>
                  <TableCell mono className="pl-4 align-top whitespace-nowrap" title={when(e.at)}>
                    <span className="block">{timeAgo(e.at)}</span>
                    <span className="block text-muted-foreground">{when(e.at)}</span>
                  </TableCell>
                  <TableCell className="align-top">
                    <span className="block max-w-56 truncate font-mono text-xs" title={e.actor ?? undefined}>
                      {e.actor ?? "unknown"}
                    </span>
                    {e.actor_type !== "user" && (
                      <span className="block text-xs text-muted-foreground">
                        {e.actor_type === "operator" ? "server operator" : e.actor_type}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="align-top">
                    <span className="block font-medium">{ACTION[e.action] ?? e.action}</span>
                    {e.target_id && (
                      <span className="block max-w-64 truncate text-xs text-muted-foreground" title={target(e)}>
                        {target(e)}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="pr-4 align-top">
                    <span className="line-clamp-2 max-w-80 min-w-48 text-xs whitespace-normal text-muted-foreground" title={details(e)}>
                      {details(e) || "—"}
                    </span>
                    {e.ip && (
                      <span className="block text-xs text-muted-foreground">
                        from <span className="font-mono">{e.ip}</span>
                      </span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        </>
      )}
      {log.hasNextPage && (
        <Button
          variant="outline"
          className="self-center"
          loading={log.isFetchingNextPage}
          onClick={() => void log.fetchNextPage()}
        >
          Load more
        </Button>
      )}
      <ChainStatus />
    </div>
  )
}

// Who changed what in the workspace (the audit log), and recent sign-ins (admin).
export function ActivityView({ signIns }: { signIns: ReactNode }) {
  const [tab, setTab] = useState<"changes" | "sign-ins">("changes")
  // The audit log is read closely: an opaque panel, no glass and no ASCII.
  return (
    <Panel
      tone="solid"
      title="Activity"
      actions={
        <Tabs panels={false} value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
          <TabsList>
            <TabsTrigger value="changes">Admin changes</TabsTrigger>
            <TabsTrigger value="sign-ins">Sign-ins</TabsTrigger>
          </TabsList>
        </Tabs>
      }
    >
      <p className="-mt-2 text-sm text-pretty text-muted-foreground">
        {tab === "changes"
          ? "Every change an admin or HR made here, newest first. Kept for good, and chained so an edited entry shows."
          : "Sign-ins through your providers and with passwords, and changes providers made by themselves."}
      </p>
      <div className="mt-4">{tab === "changes" ? <AdminChanges /> : signIns}</div>
    </Panel>
  )
}
