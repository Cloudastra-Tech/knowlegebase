import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, Copy, KeyRound, Plus, TriangleAlert } from "lucide-react"
import { toast } from "sonner"

import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { Panel } from "@/components/glass/panel"
import { ScreenHeader } from "@/components/screen-header"
import { CopyField } from "@/components/sign-in-provider-dialog"
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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { apiKeys, type ApiKey, type ApiScope, type NewApiKey } from "@/lib/api"
import { queries } from "@/lib/queries"
import { cn, timeAgo } from "@/lib/utils"

// The personal "API keys" page, and the pieces the admins' "API & MCP" tab shares
// (api-access-view.tsx): the new-key dialog, the copy-once dialog, a key's row and
// the MCP connection snippets.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const SCOPES: Record<ApiScope, { label: string; description: string }> = {
  ask: { label: "Ask questions", description: "Cited answers, as the chat gives them." },
  search: {
    label: "Search",
    description: "Passages and the list of sources, without writing an answer.",
  },
  "docs:read": { label: "Read documents", description: "Uploaded documents the role may see." },
  "docs:write": { label: "Add and delete documents", description: "HR / Admin only." },
  "audit:read": { label: "Read the audit log", description: "Admins only." },
}

function inDays(epoch: number) {
  const days = Math.ceil((epoch - Date.now() / 1000) / 86400)
  return days <= 1 ? "within a day" : `in ${days} days`
}

const STATUS = {
  active: { label: "Active", className: "bg-success/10 text-success" },
  expired: { label: "Expired", className: "bg-warning/10 text-warning" },
  revoked: { label: "Revoked", className: "bg-muted text-muted-foreground" },
} as const

// A caution in the status tokens: the warning tint and glyph, the words in the ink.
const WARNING_ALERT =
  "border-warning/40 bg-warning/10 text-warning *:data-[slot=alert-title]:text-foreground *:data-[slot=alert-description]:text-foreground/90"

// One key: its name and hint, what it may do, when it was used, and Revoke.
export function KeyRow({
  apiKey,
  showOwner = false,
  onRevoke,
}: {
  apiKey: ApiKey
  showOwner?: boolean
  onRevoke: () => void
}) {
  const [confirm, setConfirm] = useState(false)
  const active = apiKey.status === "active"
  const status = STATUS[apiKey.status]
  return (
    <div className={cn("flex flex-wrap items-center gap-3 px-4 py-3", !active && "opacity-60")}>
      <KeyRound className="size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 basis-56">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{apiKey.name}</span>
          <Badge className={cn("font-normal", status.className)}>{status.label}</Badge>
          {apiKey.kind === "service" && (
            <Badge variant="secondary" className="font-normal">
              Service · public only
            </Badge>
          )}
        </div>
        <p className="truncate font-mono text-xs text-muted-foreground">{apiKey.hint}</p>
        <p className="text-xs text-muted-foreground">
          {apiKey.scopes.map((s) => SCOPES[s]?.label ?? s).join(", ")}
          {showOwner && ` · by ${apiKey.created_by}`} ·{" "}
          {apiKey.last_used_at ? `used ${timeAgo(apiKey.last_used_at)}` : "never used"}
          {active && apiKey.expires_at && ` · expires ${inDays(apiKey.expires_at)}`}
        </p>
      </div>
      {active && (
        <Button size="sm" variant="outline" className="text-destructive" onClick={() => setConfirm(true)}>
          Revoke
        </Button>
      )}
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke “{apiKey.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Scripts and agents using it stop working from their next call. This can't be
              undone: make a new key if it's needed again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={onRevoke}>Revoke</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// "New key": a name, what it may do, and how long it lasts.
export function NewKeyDialog({
  open,
  onOpenChange,
  scopes,
  defaultScopes,
  expiryDays,
  defaultDays,
  service = false,
  create,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  scopes: ApiScope[] // the ones that can be picked
  defaultScopes: ApiScope[]
  expiryDays: number[]
  defaultDays: number
  service?: boolean
  create: (body: NewApiKey) => Promise<ApiKey & { key: string }>
  onCreated: (made: ApiKey & { key: string }) => void
}) {
  const [name, setName] = useState("")
  const [picked, setPicked] = useState<ApiScope[]>(defaultScopes)
  const [days, setDays] = useState(String(defaultDays))
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  function reset() {
    setName("")
    setPicked(defaultScopes)
    setDays(String(defaultDays))
    setError(null)
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const made = await create({ name: name.trim(), scopes: picked, days: Number(days) })
      reset()
      onOpenChange(false)
      onCreated(made)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  const toggle = (scope: ApiScope, on: boolean) =>
    setPicked((now) => (on ? [...now, scope] : now.filter((s) => s !== scope)))

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset()
        onOpenChange(next)
      }}
    >
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="flex flex-col gap-6">
          <DialogHeader>
            <DialogTitle>{service ? "New service key" : "New API key"}</DialogTitle>
            <DialogDescription>
              {service
                ? "For a page or tool used by many people. It answers as a public user of this workspace."
                : "It acts as you, with your role as it is at each call."}
            </DialogDescription>
          </DialogHeader>
          {service && (
            <Alert className={WARNING_ALERT}>
              <TriangleAlert />
              <AlertTitle>Anyone with it reads what public users can</AlertTitle>
              <AlertDescription>
                Restricted documents stay out of reach, but keep it off public websites. It
                stops working if you're no longer an admin.
              </AlertDescription>
            </Alert>
          )}
          <FieldGroup className="gap-4">
            <Field data-invalid={!!error || undefined}>
              <FieldLabel htmlFor="key-name">Name</FieldLabel>
              <Input
                id="key-name"
                autoComplete="off"
                placeholder={service ? "Intranet help page" : "My laptop's Claude Code"}
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={80}
                aria-invalid={!!error || undefined}
                required
                autoFocus
              />
              {error && <FieldError>{error}</FieldError>}
            </Field>
            <FieldSet>
              <FieldLegend variant="label">What it may do</FieldLegend>
              <FieldGroup data-slot="checkbox-group" className="gap-3">
                {scopes.map((scope) => (
                  <Field key={scope} orientation="horizontal">
                    <Checkbox
                      id={`scope-${scope}`}
                      checked={picked.includes(scope)}
                      onCheckedChange={(on) => toggle(scope, on === true)}
                    />
                    <FieldContent>
                      <FieldLabel htmlFor={`scope-${scope}`} className="font-normal">
                        {SCOPES[scope].label}
                      </FieldLabel>
                      <FieldDescription>{SCOPES[scope].description}</FieldDescription>
                    </FieldContent>
                  </Field>
                ))}
              </FieldGroup>
            </FieldSet>
            <Field>
              <FieldLabel htmlFor="key-expiry">Expires after</FieldLabel>
              <Select value={days} onValueChange={setDays}>
                <SelectTrigger id="key-expiry" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {expiryDays.map((d) => (
                    <SelectItem key={d} value={String(d)}>
                      {d === 365 ? "1 year" : `${d} days`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={saving} disabled={!name.trim() || !picked.length}>
              {!saving && <Plus data-icon="inline-start" />}
              Make key
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// A block of text to copy: a command or a config file. `wrap` breaks a long
// single token (an ID) across lines instead of scrolling it under the button.
export function CodeBlock({ label, code, wrap }: { label: string; code: string; wrap?: boolean }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error("Couldn't copy. Select the text and copy it instead.")
    }
  }
  return (
    <div className="relative min-w-0">
      {/* Focusable, so a keyboard can scroll it. */}
      <pre
        tabIndex={0}
        className={cn(
          "max-h-64 overflow-auto rounded-lg bg-muted p-3 pr-10 font-mono text-xs leading-relaxed outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
          wrap ? "break-all whitespace-pre-wrap" : "whitespace-pre",
        )}
      >
        {code}
      </pre>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="absolute top-1.5 right-1.5 size-7 bg-muted"
        onClick={copy}
        aria-label={`Copy ${label}`}
      >
        {copied ? <Check className="text-success" /> : <Copy />}
      </Button>
    </div>
  )
}

// How to connect an MCP client: Claude Code, Claude Desktop, Cursor.
export function McpSnippets({ mcpUrl, apiKey }: { mcpUrl: string; apiKey?: string }) {
  const key = apiKey ?? "kb_live_…"
  const name = "knowledge"
  const claudeCode = `claude mcp add --transport http ${name} ${mcpUrl} \\\n  --header "Authorization: Bearer ${key}"`
  // Claude Desktop starts local servers only: mcp-remote bridges to this one.
  const desktop = JSON.stringify(
    {
      mcpServers: {
        [name]: {
          command: "npx",
          args: ["-y", "mcp-remote", mcpUrl, "--header", "Authorization:${KB_AUTH}"],
          env: { KB_AUTH: `Bearer ${key}` },
        },
      },
    },
    null,
    2,
  )
  const cursor = JSON.stringify(
    { mcpServers: { [name]: { url: mcpUrl, headers: { Authorization: `Bearer ${key}` } } } },
    null,
    2,
  )
  return (
    <Tabs defaultValue="claude-code" className="w-full min-w-0">
      <TabsList>
        <TabsTrigger value="claude-code">Claude Code</TabsTrigger>
        <TabsTrigger value="claude-desktop">Claude Desktop</TabsTrigger>
        <TabsTrigger value="cursor">Cursor</TabsTrigger>
      </TabsList>
      <TabsContent value="claude-code" className="grid min-w-0 grid-cols-1 gap-2">
        <p className="text-xs text-muted-foreground">Run in a terminal:</p>
        <CodeBlock label="the Claude Code command" code={claudeCode} />
      </TabsContent>
      <TabsContent value="claude-desktop" className="grid min-w-0 grid-cols-1 gap-2">
        <p className="text-xs text-muted-foreground">
          Add to <span className="font-mono">claude_desktop_config.json</span> (Settings →
          Developer → Edit config), then restart Claude Desktop. Needs Node.js.
        </p>
        <CodeBlock label="the Claude Desktop config" code={desktop} />
      </TabsContent>
      <TabsContent value="cursor" className="grid min-w-0 grid-cols-1 gap-2">
        <p className="text-xs text-muted-foreground">
          Add to <span className="font-mono">~/.cursor/mcp.json</span> (or the project's{" "}
          <span className="font-mono">.cursor/mcp.json</span>):
        </p>
        <CodeBlock label="the Cursor config" code={cursor} />
      </TabsContent>
    </Tabs>
  )
}

// The key is only ever shown here, once: the server keeps a hash of it.
export function KeyCreatedDialog({
  made,
  mcpUrl,
  onClose,
}: {
  made: (ApiKey & { key: string }) | null
  mcpUrl?: string
  onClose: () => void
}) {
  const forMcp = !!mcpUrl && made?.kind === "personal"
  return (
    <Dialog open={!!made} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="grid-cols-[minmax(0,1fr)] sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Your new key</DialogTitle>
          <DialogDescription>
            Send it as <span className="font-mono">Authorization: Bearer …</span> with each
            request.
          </DialogDescription>
        </DialogHeader>
        <Alert className={WARNING_ALERT}>
          <TriangleAlert />
          <AlertTitle>Copy it now</AlertTitle>
          <AlertDescription>
            It won't be shown again. If it's lost, make a new one and revoke this one.
          </AlertDescription>
        </Alert>
        {made && <CopyField label={made.name} value={made.key} />}
        {made && forMcp && (
          <div className="grid min-w-0 grid-cols-1 gap-2">
            <p className="text-sm font-medium">Connect an MCP client with it</p>
            <McpSnippets mcpUrl={mcpUrl} apiKey={made.key} />
          </div>
        )}
        <DialogFooter>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// /api-keys: everyone's own keys, for scripts and agents that act as them.
export function ApiKeysView() {
  const queryClient = useQueryClient()
  const data = useQuery(queries.myApiKeys()).data
  const [creating, setCreating] = useState(false)
  const [made, setMade] = useState<(ApiKey & { key: string }) | null>(null)

  const reload = () => void queryClient.invalidateQueries({ queryKey: ["api-keys"] })

  async function revoke(key: ApiKey) {
    try {
      await apiKeys.revokeMyApiKey(key.id)
      toast.success(`Revoked “${key.name}”.`)
      reload()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const active = data?.keys.filter((k) => k.status === "active").length ?? 0
  const off = data && !data.settings.api_enabled && !data.settings.mcp_enabled

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <ScreenHeader
        icon={KeyRound}
        title="API keys"
        description="Ask the knowledge base from your own scripts, or from Claude, Cursor and other agents, as you."
        actions={
          <Button
            size="sm"
            onClick={() => setCreating(true)}
            disabled={!data || !!off || active >= (data?.max_keys ?? 0)}
          >
            <Plus data-icon="inline-start" /> New key
          </Button>
        }
      />

      {!data ? (
        <Skeleton className="mt-6 h-48 rounded-2xl" />
      ) : (
        <div className="mt-6 grid grid-cols-1 gap-6">
          {off && (
            <Alert className={WARNING_ALERT}>
              <TriangleAlert />
              <AlertTitle>Turned off in this workspace</AlertTitle>
              <AlertDescription>
                Your admins have turned off the API and the MCP server, so keys don't work.
              </AlertDescription>
            </Alert>
          )}
          {/* The keys: a list to act on, so opaque. */}
          <Panel tone="solid" className="overflow-hidden p-0">
            {data.keys.length ? (
              <div className="divide-y">
                {data.keys.map((k) => (
                  <KeyRow key={k.id} apiKey={k} onRevoke={() => revoke(k)} />
                ))}
              </div>
            ) : (
              <AsciiEmptyState
                art="key"
                title="No keys yet."
                description="A key acts as you: it sees what your role sees, at each call, so if your role changes, so does what it can do."
                className="px-5"
              />
            )}
          </Panel>

          <Panel title="Connect an agent (MCP)" className="min-w-0">
            <div className="grid min-w-0 grid-cols-1 gap-4">
              <p className="-mt-2 text-sm text-pretty text-muted-foreground">
                {data.settings.mcp_enabled
                  ? "Give Claude, Cursor or another MCP client your key, and it can search and ask the knowledge base with your permissions."
                  : "Your admins have turned the MCP server off."}
              </p>
              {data.settings.mcp_enabled && (
                <>
                  <CopyField label="MCP server URL" value={data.mcp_url} />
                  <McpSnippets mcpUrl={data.mcp_url} />
                </>
              )}
            </div>
          </Panel>

          <Panel title="REST API" className="min-w-0">
            <div className="grid min-w-0 grid-cols-1 gap-3">
              <p className="-mt-2 text-sm text-pretty text-muted-foreground">
                {data.settings.api_enabled
                  ? "Version 1: ask, search, sources, documents and (admins) the audit log."
                  : "Your admins have turned the API off."}
              </p>
              {data.settings.api_enabled && (
                <>
                  <CopyField label="Base URL" value={data.api_url} />
                  <CopyField label="OpenAPI description" value={data.openapi_url} />
                </>
              )}
            </div>
          </Panel>
        </div>
      )}

      {data && (
        <NewKeyDialog
          open={creating}
          onOpenChange={setCreating}
          scopes={data.scopes}
          defaultScopes={["ask", "search"]}
          expiryDays={data.expiry_days}
          defaultDays={data.default_days}
          create={apiKeys.createMyApiKey}
          onCreated={(k) => {
            setMade(k)
            reload()
          }}
        />
      )}
      <KeyCreatedDialog
        made={made}
        mcpUrl={data?.settings.mcp_enabled ? data.mcp_url : undefined}
        onClose={() => setMade(null)}
      />
    </div>
  )
}
