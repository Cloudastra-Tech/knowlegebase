import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ChevronDown,
  ExternalLink,
  Hash,
  Lock,
  Settings2,
  TriangleAlert,
  Unplug,
} from "lucide-react"

import { CodeBlock } from "@/components/api-keys-view"
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
import { Panel } from "@/components/glass/panel"
import { StatusDot } from "@/components/glass/status-dot"
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
import { Switch } from "@/components/ui/switch"
import { slack, type SlackApp, type SlackSettingsIn, type SlackStatus } from "@/lib/api"
import { queries } from "@/lib/queries"
import { cn, timeAgo } from "@/lib/utils"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const STATUS: Record<SlackStatus, { label: string; tone: "on" | "bad" | "off" }> = {
  installed: { label: "Installed", tone: "on" },
  ready: { label: "Not installed", tone: "off" },
  not_configured: { label: "Not set up", tone: "off" },
  revoked: { label: "Removed in Slack", tone: "bad" },
}

function StatusBadge({ status }: { status: SlackStatus }) {
  const { label, tone } = STATUS[status]
  return (
    <StatusDot
      status={tone === "on" ? "live" : tone === "bad" ? "error" : "off"}
      label={label}
      className={cn(
        "shrink-0 whitespace-nowrap",
        tone === "bad" ? "text-destructive" : tone === "off" && "text-muted-foreground",
      )}
    />
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="truncate font-mono text-sm tabular-nums">{value}</p>
    </div>
  )
}

const HOW_TO_ASK = [
  ["DM the app", "answered as you, with your role"],
  ["@Knowledge in a channel", "answered in the thread"],
  ["/ask", "only you see the answer"],
  ["Ask about this thread", "on any message's ⋮ menu"],
] as const

// Setting up the workspace's own Slack app: create it from our manifest, paste its
// credentials. The server checks their shape and keeps the secrets sealed.
function OwnAppDialog({
  app,
  open,
  onOpenChange,
  onSaved,
}: {
  app: SlackApp
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (app: SlackApp) => void
}) {
  const [clientId, setClientId] = useState("")
  const [clientSecret, setClientSecret] = useState("")
  const [signingSecret, setSigningSecret] = useState("")
  const [manifest, setManifest] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const [wasOpen, setWasOpen] = useState(false)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setClientId(app.mode === "custom" ? (app.client_id ?? "") : "")
      setClientSecret("")
      setSigningSecret("")
      setProblem(null)
      setManifest(null)
      slack
        .manifest()
        .then((m) => setManifest(m.manifest))
        .catch((err) => setProblem(errorText(err)))
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setProblem(null)
    try {
      const saved = await slack.setApp({
        mode: "custom",
        client_id: clientId,
        client_secret: clientSecret,
        signing_secret: signingSecret,
      })
      toast.success("Your Slack app is saved. Now install it.")
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
            <DialogTitle>Use your own Slack app</DialogTitle>
            <DialogDescription>
              The app lives in your Slack and stays internal to it. Its secrets are stored encrypted
              and never shown again.
              {app.status === "installed" &&
                " Saving a different app removes the one installed now: install the new one after."}
            </DialogDescription>
          </DialogHeader>
          <ol className="grid min-w-0 gap-3 text-sm">
            <li className="grid min-w-0 gap-2">
              <span>
                <span className="font-medium">1.</span> Copy this manifest. It has this server's
                addresses and only the permissions the app uses.
              </span>
              {manifest ? (
                <CodeBlock label="manifest" code={manifest} />
              ) : (
                <Skeleton className="h-24 rounded-lg" />
              )}
            </li>
            <li>
              <span className="font-medium">2.</span> In Slack, create an app{" "}
              <span className="text-muted-foreground">from a manifest</span>, pick your workspace
              and paste it.{" "}
              <a
                href="https://api.slack.com/apps?new_app=1"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 font-medium underline underline-offset-4"
              >
                Open Slack apps <ExternalLink className="size-3" />
              </a>
            </li>
            <li>
              <span className="font-medium">3.</span> From its Basic Information page, paste the app
              credentials:
            </li>
          </ol>
          <FieldGroup className="gap-3">
            <Field>
              <FieldLabel htmlFor="slack-client-id">Client ID</FieldLabel>
              <Input
                id="slack-client-id"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                placeholder="1234567890.1234567890"
                autoComplete="off"
                required
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="slack-client-secret">Client Secret</FieldLabel>
              <Input
                id="slack-client-secret"
                type="password"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                autoComplete="off"
                required
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="slack-signing-secret">Signing Secret</FieldLabel>
              <Input
                id="slack-signing-secret"
                type="password"
                value={signingSecret}
                onChange={(e) => setSigningSecret(e.target.value)}
                autoComplete="off"
                required
              />
              <FieldDescription>
                Every request from Slack is checked with it before it's read.
              </FieldDescription>
            </Field>
          </FieldGroup>
          {problem && (
            <p role="alert" className="text-sm text-destructive">
              {problem}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
              Save app
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// What happens in Slack: who gets answered, and auto-answer in help channels.
function SlackSettings({ app, onSaved }: { app: SlackApp; onSaved: (app: SlackApp) => void }) {
  const [channels, setChannels] = useState(app.auto_answer_channels.join(", "))
  const [saving, setSaving] = useState(false)
  const editable = app.can_manage

  async function save(body: SlackSettingsIn, done?: string) {
    setSaving(true)
    try {
      const saved = await slack.setSettings(body)
      onSaved(saved)
      if (done) toast.success(done)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  const listed = channels
    .split(/[\s,]+/)
    .map((c) => c.trim())
    .filter(Boolean)
  const changed = listed.join(",") !== app.auto_answer_channels.join(",")

  return (
    <div className="mt-3 grid gap-3">
      <div className="rounded-xl border border-border/70 p-3">
        <p className="text-sm font-medium" id="slack-unknown-label">
          People Slack knows but this workspace doesn't
        </p>
        <RadioGroup
          aria-labelledby="slack-unknown-label"
          value={app.unknown_users}
          onValueChange={(v) => save({ unknown_users: v as SlackApp["unknown_users"] })}
          disabled={!editable || saving}
          className="mt-2 gap-2"
        >
          <div className="flex items-center gap-2">
            <RadioGroupItem value="refuse" id="slack-unknown-refuse" />
            <Label htmlFor="slack-unknown-refuse" className="font-normal">
              Ask them to get an account
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <RadioGroupItem value="public" id="slack-unknown-public" />
            <Label htmlFor="slack-unknown-public" className="font-normal">
              Answer from public documents only
            </Label>
          </div>
        </RadioGroup>
        <p className="mt-2 text-xs text-muted-foreground">
          Matched by their Slack email. Members who are turned off are never answered.
        </p>
      </div>

      <div className="rounded-xl border border-border/70 p-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <Label htmlFor="slack-auto" className="text-sm font-medium">
              Auto-answer in help channels
            </Label>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Replies in the thread only when the answer is confident and cited. Needs the
              channels:history scope added to the app.
            </p>
          </div>
          <Switch
            id="slack-auto"
            checked={app.auto_answer}
            onCheckedChange={(on) => save({ auto_answer: on })}
            disabled={!editable || saving}
          />
        </div>
        {app.auto_answer && (
          <Field className="mt-3">
            <FieldLabel htmlFor="slack-channels">Channel IDs</FieldLabel>
            <div className="flex min-w-0 gap-2">
              <Input
                id="slack-channels"
                value={channels}
                onChange={(e) => setChannels(e.target.value)}
                placeholder="C0123ABCD, C0456EFGH"
                disabled={!editable}
                className="min-w-0"
              />
              <Button
                size="sm"
                variant="outline"
                className="h-8 shrink-0"
                disabled={!editable || saving || !changed}
                onClick={() => save({ auto_answer_channels: listed }, "Help channels saved.")}
              >
                Save
              </Button>
            </div>
            <FieldDescription>In Slack: the channel's name → About → Channel ID.</FieldDescription>
          </Field>
        )}
      </div>
    </div>
  )
}

// The Slack app on the Integrations screen: choose the app, install it, and set
// how it answers. In channels it only ever answers from public documents.
export function SlackAppCard() {
  const queryClient = useQueryClient()
  const { data: app, isError } = useQuery({
    ...queries.slackApp(),
    retry: false,
  })
  const [busy, setBusy] = useState<"install" | "uninstall" | "mode" | null>(null)
  const [ownOpen, setOwnOpen] = useState(false)

  const update = (next: SlackApp) => queryClient.setQueryData(queries.slackApp().queryKey, next)

  async function install() {
    setBusy("install")
    try {
      const { url } = await slack.install()
      window.location.href = url // off to Slack's consent screen
    } catch (err) {
      toast.error(errorText(err))
      setBusy(null)
    }
  }

  async function uninstall() {
    setBusy("uninstall")
    try {
      update(await slack.uninstall())
      toast.success("Removed from Slack. Its access was revoked.")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(null)
    }
  }

  async function switchToCentral() {
    setBusy("mode")
    try {
      update(await slack.setApp({ mode: "central" }))
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(null)
    }
  }

  if (isError) return null // not for this role
  return (
    <Panel className="flex min-w-0 flex-col">
      <div className="flex items-start gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-muted">
          <Hash className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-medium">Slack</p>
          <p className="text-sm text-muted-foreground">
            Ask in Slack and get the same cited answers.
          </p>
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
            Answers posted where others can read them use public documents only. If you could see
            more, the rest comes to you in a DM.
          </p>

          {app.status === "revoked" && (
            <p className="mt-3 flex items-start gap-1.5 text-sm text-destructive">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
              The app was removed in Slack. Install it again to keep answering there.
            </p>
          )}

          {app.status === "installed" ? (
            <>
              <div className="mt-4 grid grid-cols-3 gap-3 rounded-xl bg-muted/50 p-3">
                <Stat label="Slack workspace" value={app.team_name || "—"} />
                <Stat label="App" value={app.mode === "custom" ? "Your own" : "This server's"} />
                <Stat label="Installed" value={timeAgo(app.installed_at)} />
              </div>
              <SlackSettings key={app.auto_answer_channels.join(",")} app={app} onSaved={update} />
            </>
          ) : (
            app.can_manage && (
              <div className="mt-4 rounded-xl border border-border/70 p-3">
                <p className="text-sm font-medium" id="slack-app-label">
                  Which Slack app
                </p>
                <RadioGroup
                  aria-labelledby="slack-app-label"
                  value={app.mode}
                  onValueChange={(v) => (v === "central" ? switchToCentral() : setOwnOpen(true))}
                  disabled={busy !== null}
                  className="mt-2 gap-2"
                >
                  <div className="flex items-center gap-2">
                    <RadioGroupItem
                      value="central"
                      id="slack-mode-central"
                      disabled={!app.central_available}
                    />
                    <Label htmlFor="slack-mode-central" className="font-normal">
                      This server's app
                      {!app.central_available && (
                        <span className="text-muted-foreground"> · not set up here</span>
                      )}
                    </Label>
                  </div>
                  <div className="flex items-center gap-2">
                    <RadioGroupItem value="custom" id="slack-mode-custom" />
                    <Label htmlFor="slack-mode-custom" className="font-normal">
                      Your own app, internal to your Slack
                    </Label>
                  </div>
                </RadioGroup>
                {app.mode === "custom" && app.client_id && (
                  <p className="mt-2 truncate text-xs text-muted-foreground">
                    Client ID <span className="font-mono">{app.client_id}</span>
                  </p>
                )}
              </div>
            )
          )}

          {!app.can_manage && app.status !== "installed" && (
            <p className="mt-4 text-sm text-muted-foreground">
              An admin installs the Slack app for this workspace.
            </p>
          )}

          {app.can_manage && (
            <div className="mt-4 flex flex-wrap gap-2">
              {app.status !== "installed" && (
                <Button
                  size="sm"
                  onClick={install}
                  loading={busy === "install"}
                  disabled={busy !== null || app.status === "not_configured"}
                >
                  {app.status === "revoked" ? "Install again" : "Add to Slack"}
                </Button>
              )}
              {app.mode === "custom" && (
                <Button size="sm" variant="outline" onClick={() => setOwnOpen(true)}>
                  <Settings2 /> {app.client_id ? "Change your app" : "Set up your app"}
                </Button>
              )}
              {app.status === "installed" && (
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-destructive"
                      disabled={busy !== null}
                    >
                      <Unplug /> Uninstall
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Remove the app from Slack?</AlertDialogTitle>
                      <AlertDialogDescription>
                        Its access to {app.team_name || "your Slack"} is revoked and it stops
                        answering there. Answers already posted stay in Slack; chats saved here stay
                        too.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Keep it</AlertDialogCancel>
                      <AlertDialogAction onClick={uninstall}>Uninstall</AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              )}
            </div>
          )}

          {app.can_manage && app.mode === "custom" && app.client_id && (
            <Collapsible className="mt-3">
              <CollapsibleTrigger asChild>
                <Button type="button" variant="ghost" size="sm" className="w-fit px-1">
                  Request URLs for your app <ChevronDown />
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="mt-2 grid gap-2">
                <CopyField label="Event subscriptions" value={app.urls.events} />
                <CopyField label="Interactivity" value={app.urls.interactions} />
                <CopyField label="/ask command" value={app.urls.commands} />
                <CopyField label="OAuth redirect URL" value={app.urls.redirect} />
              </CollapsibleContent>
            </Collapsible>
          )}

          <OwnAppDialog app={app} open={ownOpen} onOpenChange={setOwnOpen} onSaved={update} />
        </div>
      )}
    </Panel>
  )
}
