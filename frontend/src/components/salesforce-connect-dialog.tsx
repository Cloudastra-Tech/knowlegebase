import { useState } from "react"
import { ChevronDown } from "lucide-react"

import { CodeBlock } from "@/components/api-keys-view"
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
import { api, type Integration } from "@/lib/api"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

// Connect Salesforce Knowledge (connectors/salesforce.py): the org's My Domain address and,
// unless this server ships its own packaged app, the consumer key and secret of an External
// Client App the customer created in their org (new Connected Apps are off by default since
// Spring '26). Then the browser signs in to Salesforce (web server flow with PKCE).
export function SalesforceConnectDialog({
  item,
  open,
  onOpenChange,
}: {
  item: Integration
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const ownApp = !item.server_app
  const [url, setUrl] = useState("")
  const [clientId, setClientId] = useState("")
  const [clientSecret, setClientSecret] = useState("")
  const [saving, setSaving] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const callback = `${window.location.origin}/api/integrations/salesforce/callback`

  const [wasOpen, setWasOpen] = useState(false)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setUrl(item.base_url ? `https://${item.base_url}` : "")
      setClientSecret("")
      setProblem(null)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setProblem(null)
    try {
      const res = await api.connectIntegration(
        item.id,
        url,
        ownApp ? { client_id: clientId, client_secret: clientSecret } : undefined,
      )
      window.location.href = res.url // off to Salesforce's sign-in and consent
    } catch (err) {
      setProblem(errorText(err)) // the server's words: what to fix in the form
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
        <form onSubmit={submit} className="grid min-w-0 grid-cols-1 gap-4">
          <DialogHeader>
            <DialogTitle>Connect {item.name}</DialogTitle>
            <DialogDescription>
              Read-only: published Knowledge articles, nothing else. You sign in to Salesforce
              next; the tokens are stored encrypted and revoked when you disconnect.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="min-w-0 gap-4">
            <Field>
              <FieldLabel htmlFor="sf-domain">My Domain address</FieldLabel>
              <Input
                id="sf-domain"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://acme.my.salesforce.com"
                inputMode="url"
                autoComplete="off"
                required
                autoFocus
              />
              <FieldDescription>
                Setup → My Domain. A sandbox looks like acme--dev.sandbox.my.salesforce.com.
              </FieldDescription>
            </Field>
            {ownApp && (
              <>
                <Collapsible>
                  <CollapsibleTrigger asChild>
                    <Button type="button" variant="ghost" size="sm" className="w-fit px-1">
                      How to create the External Client App <ChevronDown />
                    </Button>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="grid min-w-0 grid-cols-1 gap-3 pt-2 text-sm text-muted-foreground">
                    <ol className="list-decimal space-y-1 pl-5">
                      <li>Setup → External Client App Manager → New External Client App.</li>
                      <li>
                        Enable OAuth with the scopes "Manage user data via APIs (api)" and
                        "Perform requests at any time (refresh_token, offline_access)".
                      </li>
                      <li>Paste the callback URL below.</li>
                      <li>
                        Keep "Require Proof Key for Code Exchange (PKCE)" on, and turn on refresh
                        token rotation.
                      </li>
                      <li>Save, then copy the consumer key and secret here.</li>
                    </ol>
                    <Field>
                      <FieldLabel>Callback URL</FieldLabel>
                      <CodeBlock label="callback URL" code={callback} wrap />
                    </Field>
                  </CollapsibleContent>
                </Collapsible>
                <Field>
                  <FieldLabel htmlFor="sf-key">Consumer key</FieldLabel>
                  <Input
                    id="sf-key"
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    autoComplete="off"
                    required
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="sf-secret">Consumer secret</FieldLabel>
                  <Input
                    id="sf-secret"
                    type="password"
                    value={clientSecret}
                    onChange={(e) => setClientSecret(e.target.value)}
                    autoComplete="off"
                    required
                  />
                </Field>
              </>
            )}
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
              Sign in with Salesforce
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
