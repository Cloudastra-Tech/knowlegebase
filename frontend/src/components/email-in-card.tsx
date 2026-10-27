import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Lock, Mail, TriangleAlert } from "lucide-react"

import { Panel } from "@/components/glass/panel"
import { StatusDot } from "@/components/glass/status-dot"
import { CopyField } from "@/components/sign-in-provider-dialog"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { emailIn, type EmailIn } from "@/lib/api"
import { queries } from "@/lib/queries"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

function StatusBadge({ app }: { app: EmailIn }) {
  if (app.enabled && app.configured)
    return <StatusDot status="live" label="On" className="shrink-0 whitespace-nowrap" />
  return (
    <StatusDot
      status="off"
      label={app.configured ? "Off" : "Not set up"}
      className="shrink-0 whitespace-nowrap text-muted-foreground"
    />
  )
}

// Email-in on the Integrations screen: members email the workspace's ask@ address and
// get a cited answer back in the thread. Off until an admin turns it on.
export function EmailInCard() {
  const queryClient = useQueryClient()
  const { data: app, isError } = useQuery({ ...queries.emailIn(), retry: false })
  const [saving, setSaving] = useState(false)

  async function toggle(on: boolean) {
    setSaving(true)
    try {
      queryClient.setQueryData(queries.emailIn().queryKey, await emailIn.setEnabled(on))
      toast.success(on ? "Email-in is on." : "Email-in is off.")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  if (isError) return null // not for this role
  return (
    <Panel className="flex min-w-0 flex-col">
      <div className="flex items-start gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-muted">
          <Mail className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-medium">Email</p>
          <p className="text-sm text-muted-foreground">
            Email a question and get a cited answer back in the thread.
          </p>
        </div>
        {app && <StatusBadge app={app} />}
      </div>

      {!app ? (
        <Skeleton className="mt-4 h-24 rounded-xl" />
      ) : (
        <div className="mt-4 flex flex-1 flex-col gap-3">
          {app.address && <CopyField label="Address to email" value={app.address} />}
          <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
            <Lock className="mt-px size-3.5 shrink-0" />
            Only members get answers, from their own email address, checked by the mail provider
            (SPF and DKIM). Answers use what the sender's role can see. Replies to an answer ask a
            follow-up.
          </p>

          {!app.configured && (
            <Alert>
              <TriangleAlert />
              <AlertTitle>Not set up on this server</AlertTitle>
              <AlertDescription>
                Whoever hosts the assistant sets the mail domain (<code className="font-mono">EMAIL_IN_DOMAIN</code>)
                and the inbound webhook's credentials (<code className="font-mono">EMAIL_IN_WEBHOOK_USER</code>,{" "}
                <code className="font-mono">EMAIL_IN_WEBHOOK_PASSWORD</code>), then points the provider's
                inbound webhook at <code className="font-mono break-all">{app.webhook_url}</code>.
              </AlertDescription>
            </Alert>
          )}

          <div className="rounded-xl border border-border/70 p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <Label htmlFor="email-in-on" className="text-sm font-medium">
                  Answer emails
                </Label>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {app.can_manage
                    ? "Off until you turn it on."
                    : "An admin turns email-in on for this workspace."}
                </p>
              </div>
              <Switch
                id="email-in-on"
                checked={app.enabled}
                onCheckedChange={toggle}
                disabled={!app.can_manage || saving || (!app.configured && !app.enabled)}
              />
            </div>
          </div>
        </div>
      )}
    </Panel>
  )
}
