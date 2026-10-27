import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ChevronDown,
  Download,
  ExternalLink,
  Loader2,
  Lock,
  MessagesSquare,
  Settings2,
  Unplug,
} from "lucide-react"

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
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Skeleton } from "@/components/ui/skeleton"
import { teams, type TeamsApp, type TeamsMode, type TeamsStatus } from "@/lib/api"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const STATUS: Record<TeamsStatus, { label: string; tone: "on" | "wait" | "off" }> = {
  connected: { label: "Connected", tone: "on" },
  configured: { label: "Waiting for Teams", tone: "wait" },
  not_configured: { label: "Not set up", tone: "off" },
}

function StatusBadge({ status }: { status: TeamsStatus }) {
  const { label, tone } = STATUS[status]
  if (tone === "on")
    return (
      <Badge className="gap-1.5 bg-emerald-600/10 text-emerald-700 hover:bg-emerald-600/10 dark:text-emerald-400">
        <span className="size-1.5 rounded-full bg-emerald-500" /> {label}
      </Badge>
    )
  if (tone === "wait")
    return (
      <Badge variant="secondary" className="gap-1.5 font-normal">
        <span className="size-1.5 rounded-full bg-amber-500" /> {label}
      </Badge>
    )
  return (
    <Badge variant="outline" className="font-normal text-muted-foreground">
      {label}
    </Badge>
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="truncate text-sm font-medium">{value}</p>
    </div>
  )
}

const HOW_TO_ASK = [
  ["Chat with the app", "answered as you, with your role"],
  ["@Knowledge in a channel or group chat", "answered in the thread"],
] as const

async function savePackage() {
  try {
    const { blob, filename } = await teams.appPackage()
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    toast.error(errorText(err))
  }
}

// Connecting a bot: the workspace's own Azure Bot (its App ID, tenant and client
// secret), or this server's bot answering the workspace's Microsoft tenant. The
// server checks their shape and keeps the secret sealed.
function BotDialog({
  app,
  mode,
  open,
  onOpenChange,
  onSaved,
}: {
  app: TeamsApp
  mode: TeamsMode
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (app: TeamsApp) => void
}) {
  const [appId, setAppId] = useState("")
  const [appTenant, setAppTenant] = useState("")
  const [secret, setSecret] = useState("")
  const [tenantId, setTenantId] = useState("")
  const [saving, setSaving] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const [wasOpen, setWasOpen] = useState(false)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setAppId(app.mode === "custom" ? (app.app_id ?? "") : "")
      setAppTenant(app.mode === "custom" ? (app.app_tenant ?? "") : "")
      setTenantId(app.mode === "central" ? (app.tenant_id ?? "") : "")
      setSecret("")
      setProblem(null)
    }
  }
  const keepsSecret = mode === "custom" && app.mode === "custom" && !!app.has_secret

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setProblem(null)
    try {
      const saved = await teams.setApp(
        mode === "custom"
          ? { mode, app_id: appId, app_tenant: appTenant, client_secret: secret }
          : { mode, tenant_id: tenantId },
      )
      toast.success("Saved. Now add the app in Teams.")
      onOpenChange(false)
      onSaved(saved)
    } catch (err) {
      setProblem(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
        <form onSubmit={submit} className="grid min-w-0 gap-4">
          <DialogHeader>
            <DialogTitle>
              {mode === "custom" ? "Connect your Azure Bot" : "Use this server's bot"}
            </DialogTitle>
            <DialogDescription>
              {mode === "custom"
                ? "The bot lives in your Microsoft tenant. Its client secret is stored encrypted and never shown again."
                : "This server's bot answers the Microsoft tenant you name here, and no other workspace's."}
            </DialogDescription>
          </DialogHeader>
          {mode === "custom" ? (
            <>
              <ol className="grid min-w-0 gap-2 text-sm">
                <li>
                  <span className="font-medium">1.</span> In the Azure portal, create an{" "}
                  <span className="font-medium">Azure Bot</span> with the type{" "}
                  <span className="text-muted-foreground">Single Tenant</span>, and add the
                  Microsoft Teams channel.{" "}
                  <a
                    href="https://portal.azure.com/#create/Microsoft.AzureBot"
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 font-medium underline underline-offset-4"
                  >
                    Open Azure <ExternalLink className="size-3" />
                  </a>
                </li>
                <li>
                  <span className="font-medium">2.</span> From its Configuration page, and a client
                  secret made under Manage Password, paste:
                </li>
              </ol>
              <FieldGroup className="gap-3">
                <Field>
                  <FieldLabel htmlFor="teams-app-id">Microsoft App ID</FieldLabel>
                  <Input
                    id="teams-app-id"
                    value={appId}
                    onChange={(e) => setAppId(e.target.value)}
                    placeholder="00000000-0000-0000-0000-000000000000"
                    autoComplete="off"
                    required
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="teams-app-tenant">App Tenant ID</FieldLabel>
                  <Input
                    id="teams-app-tenant"
                    value={appTenant}
                    onChange={(e) => setAppTenant(e.target.value)}
                    placeholder="00000000-0000-0000-0000-000000000000"
                    autoComplete="off"
                    required
                  />
                  <FieldDescription>Your Microsoft tenant: the bot answers people in it.</FieldDescription>
                </Field>
                <Field>
                  <FieldLabel htmlFor="teams-secret">Client secret value</FieldLabel>
                  <Input
                    id="teams-secret"
                    type="password"
                    value={secret}
                    onChange={(e) => setSecret(e.target.value)}
                    placeholder={keepsSecret ? "Leave empty to keep the saved one" : undefined}
                    autoComplete="off"
                    required={!keepsSecret}
                  />
                </Field>
              </FieldGroup>
            </>
          ) : (
            <Field>
              <FieldLabel htmlFor="teams-tenant">Microsoft tenant ID</FieldLabel>
              <Input
                id="teams-tenant"
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                placeholder="00000000-0000-0000-0000-000000000000"
                autoComplete="off"
                required
              />
              <FieldDescription>Microsoft Entra admin center → Overview → Tenant ID.</FieldDescription>
            </Field>
          )}
          {problem && (
            <p role="alert" className="text-sm text-destructive">
              {problem}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="animate-spin" />}
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// Microsoft Teams on the Integrations screen: connect a bot, add the app in Teams,
// and set who gets answered. In channels and group chats it only answers from
// public documents.
export function TeamsAppCard() {
  const queryClient = useQueryClient()
  const { data: app, isError } = useQuery({ ...queries.teamsApp(), retry: false })
  const [dialog, setDialog] = useState<TeamsMode | null>(null)
  const [busy, setBusy] = useState(false)

  const update = (next: TeamsApp) => queryClient.setQueryData(queries.teamsApp().queryKey, next)

  async function run(work: () => Promise<TeamsApp>, done?: string) {
    setBusy(true)
    try {
      update(await work())
      if (done) toast.success(done)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  if (isError) return null // not for this role
  const set = app && app.status !== "not_configured"
  return (
    <div className="flex min-w-0 flex-col rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex items-start gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-muted">
          <MessagesSquare className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-medium">Microsoft Teams</p>
          <p className="text-sm text-muted-foreground">Ask in Teams and get the same cited answers.</p>
        </div>
        {app && <StatusBadge status={app.status} />}
      </div>

      {!app ? (
        <Skeleton className="mt-4 h-24 rounded-xl" />
      ) : (
        <div className="mt-4 flex flex-1 flex-col">
          <ul className="grid gap-1 text-sm">
            {HOW_TO_ASK.map(([how, what]) => (
              <li key={how} className="min-w-0">
                <span className="font-medium">{how}</span>{" "}
                <span className="text-muted-foreground">· {what}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 flex items-start gap-1.5 text-xs text-muted-foreground">
            <Lock className="mt-px size-3.5 shrink-0" />
            Answers in channels and group chats use public documents only. If you could see more,
            the rest comes to you in your chat with the app.
          </p>

          {set && (
            <>
              <div className="mt-4 grid grid-cols-1 gap-3 rounded-xl bg-muted/50 p-3 sm:grid-cols-3">
                <Stat label="Microsoft tenant" value={app.tenant_id || "—"} />
                <Stat label="Bot" value={app.mode === "custom" ? "Your own" : "This server's"} />
                <Stat
                  label="Last message"
                  value={app.last_activity_at ? timeAgo(app.last_activity_at) : "None yet"}
                />
              </div>
              <div className="mt-3 rounded-xl border border-border/70 p-3">
                <p className="text-sm font-medium" id="teams-unknown-label">
                  People Teams knows but this workspace doesn't
                </p>
                <RadioGroup
                  aria-labelledby="teams-unknown-label"
                  value={app.unknown_users}
                  onValueChange={(v) =>
                    run(() => teams.setSettings({ unknown_users: v as TeamsApp["unknown_users"] }))
                  }
                  disabled={!app.can_manage || busy}
                  className="mt-2 gap-2"
                >
                  <div className="flex items-center gap-2">
                    <RadioGroupItem value="refuse" id="teams-unknown-refuse" />
                    <Label htmlFor="teams-unknown-refuse" className="font-normal">
                      Ask them to get an account
                    </Label>
                  </div>
                  <div className="flex items-center gap-2">
                    <RadioGroupItem value="public" id="teams-unknown-public" />
                    <Label htmlFor="teams-unknown-public" className="font-normal">
                      Answer from public documents only
                    </Label>
                  </div>
                </RadioGroup>
                <p className="mt-2 text-xs text-muted-foreground">
                  Matched by their Microsoft email or sign-in name. Members who are turned off are
                  never answered.
                </p>
              </div>
            </>
          )}

          {!app.can_manage && !set && (
            <p className="mt-4 text-sm text-muted-foreground">
              An admin connects Microsoft Teams for this workspace.
            </p>
          )}

          {app.can_manage && (
            <div className="mt-4 flex flex-wrap gap-2">
              {set && (
                <Button size="sm" onClick={savePackage} disabled={busy}>
                  <Download /> Download Teams app
                </Button>
              )}
              <Button
                size="sm"
                variant={set ? "outline" : "default"}
                onClick={() => setDialog("custom")}
                disabled={busy}
              >
                <Settings2 /> {app.mode === "custom" && app.app_id ? "Change your bot" : "Connect your bot"}
              </Button>
              {app.central_available && (
                <Button size="sm" variant="outline" onClick={() => setDialog("central")} disabled={busy}>
                  Use this server's bot
                </Button>
              )}
              {set && (
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button size="sm" variant="ghost" className="text-destructive" disabled={busy}>
                      <Unplug /> Disconnect
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Disconnect Microsoft Teams?</AlertDialogTitle>
                      <AlertDialogDescription>
                        The bot stops answering and its secret is deleted here. Remove the app in
                        Teams too. Answers already posted stay in Teams; chats saved here stay too.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Keep it</AlertDialogCancel>
                      <AlertDialogAction onClick={() => run(teams.disconnect, "Teams disconnected.")}>
                        Disconnect
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              )}
            </div>
          )}

          {app.can_manage && set && (
            <Collapsible className="mt-3">
              <CollapsibleTrigger asChild>
                <Button type="button" variant="ghost" size="sm" className="w-fit px-1">
                  Messaging endpoint for Azure <ChevronDown />
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="mt-2 grid gap-2">
                <CopyField label="Messaging endpoint" value={app.messaging_endpoint} />
                <p className="text-xs text-muted-foreground">
                  Paste it in the Azure Bot's Configuration page, then upload the Teams app in the
                  Teams admin center (Manage apps → Upload new app).
                </p>
              </CollapsibleContent>
            </Collapsible>
          )}

          {dialog && (
            <BotDialog
              app={app}
              mode={dialog}
              open
              onOpenChange={(open) => !open && setDialog(null)}
              onSaved={update}
            />
          )}
        </div>
      )}
    </div>
  )
}
