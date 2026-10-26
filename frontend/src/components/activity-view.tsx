import { useEffect, useState, type ReactNode } from "react"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { Download, Loader2, Search, ShieldAlert, ShieldCheck } from "lucide-react"
import { toast } from "sonner"

import { DateRangePicker, type DateRange } from "@/components/date-range-picker"
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
  "workspace.create": "Created the workspace",
  "session.sign_out_everywhere": "Signed out everywhere",
  "audit.export": "Exported the audit log",
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
  ["audit.", "Audit log"],
]

function details(e: AuditEvent) {
  return Object.entries(e.details)
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ")
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
        ? `${count} ${count === 1 ? "change" : "changes"}, chain intact`
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
        <DateRangePicker value={range} onChange={setRange} className="min-w-0 basis-56 grow sm:grow-0" />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="sm:ml-auto" disabled={exporting}>
              {exporting ? (
                <Loader2 className="animate-spin" data-icon="inline-start" />
              ) : (
                <Download data-icon="inline-start" />
              )}
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
        <p className="rounded-xl border px-4 py-6 text-center text-sm text-muted-foreground">
          {filters.actor || filters.action || filters.from
            ? "No changes match."
            : "No changes yet. Changes to sign-in, members, documents and integrations show here."}
        </p>
      ) : (
        <div className="rounded-xl border">
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
                  <TableCell className="pl-4 align-top whitespace-nowrap" title={when(e.at)}>
                    <span className="block">{timeAgo(e.at)}</span>
                    <span className="block text-xs text-muted-foreground">{when(e.at)}</span>
                  </TableCell>
                  <TableCell className="align-top">
                    <span className="block max-w-56 truncate" title={e.actor ?? undefined}>
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
                      <span className="block max-w-64 truncate text-xs text-muted-foreground" title={e.target_id}>
                        {e.target_id}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="pr-4 align-top">
                    <span className="line-clamp-2 max-w-80 min-w-48 text-xs whitespace-normal text-muted-foreground" title={details(e)}>
                      {details(e) || "—"}
                    </span>
                    {e.ip && <span className="block text-xs text-muted-foreground">from {e.ip}</span>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {log.hasNextPage && (
        <Button
          variant="outline"
          className="self-center"
          disabled={log.isFetchingNextPage}
          onClick={() => void log.fetchNextPage()}
        >
          {log.isFetchingNextPage && <Loader2 className="animate-spin" data-icon="inline-start" />}
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
  return (
    <section className="rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-medium">Activity</h3>
          <p className="text-sm text-muted-foreground">
            {tab === "changes"
              ? "Every change an admin or HR made here, newest first. Kept for good, and chained so an edited entry shows."
              : "Sign-ins through your providers and with passwords, and changes providers made by themselves."}
          </p>
        </div>
        <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
          <TabsList>
            <TabsTrigger value="changes">Admin changes</TabsTrigger>
            <TabsTrigger value="sign-ins">Sign-ins</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      <div className="mt-4">{tab === "changes" ? <AdminChanges /> : signIns}</div>
    </section>
  )
}
