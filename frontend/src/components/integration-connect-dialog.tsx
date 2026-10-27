import { useState } from "react"
import { toast } from "sonner"
import { ChevronDown } from "lucide-react"

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { api, type Integration } from "@/lib/api"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

// What the address + token form says, per connector.
const TOKEN_COPY: Record<
  string,
  {
    description: string
    address: string | null // null: no address (HubSpot)
    placeholder: string
    token: string
    cloudflare: boolean
    email?: boolean // Zendesk: the agent or admin the token belongs to
    region?: boolean // Intercom: the address field is the workspace's hosting region
    hint?: string
    tokenHint?: string
    or?: string // the line between "Sign in with ..." and the token form
  }
> = {
  outline: {
    description:
      "Read-only. Create an API token in Outline (Settings → API & Apps). It is stored encrypted and used only to sync the collections you pick.",
    address: "Wiki address",
    placeholder: "https://wiki.example.com",
    token: "API token",
    cloudflare: true,
  },
  confluence_dc: {
    description:
      "Read-only, for self-hosted Confluence 7.9 or later. Create a personal access token in Confluence (your avatar → Settings → Personal access tokens). It is stored encrypted and used only to sync the spaces you pick.",
    address: "Confluence address",
    placeholder: "https://confluence.example.com",
    token: "Personal access token",
    cloudflare: false,
  },
  jira_dc: {
    description:
      "Read-only, for self-hosted Jira 8.14 or later. Create a personal access token in Jira (your avatar → Profile → Personal Access Tokens). It is stored encrypted and used only to sync the projects you pick.",
    address: "Jira address",
    placeholder: "https://jira.example.com",
    token: "Personal access token",
    cloudflare: false,
  },
  zendesk: {
    description:
      "Read-only. Use an API token of a Zendesk agent or admin (Admin Center → Apps and integrations → APIs → Zendesk API). It is stored encrypted and used only to sync your Help Center.",
    address: "Zendesk subdomain",
    placeholder: "acme",
    hint: "The first part of your Zendesk address: acme for acme.zendesk.com.",
    token: "API token",
    cloudflare: false,
    email: true,
  },
  freshdesk: {
    description:
      "Read-only. Use an agent's API key (your profile picture → Profile settings → View API key). It is stored encrypted and used only to sync your knowledge base.",
    address: "Freshdesk subdomain",
    placeholder: "acme",
    hint: "The first part of your Freshdesk address: acme for acme.freshdesk.com.",
    token: "API key",
    cloudflare: false,
  },
  intercom: {
    description:
      "Read-only. Create a private app in your Intercom workspace (Developer Hub) with the Read articles permission (and Read conversations, to sync conversations), then paste its access token. It is stored encrypted.",
    address: "Region",
    placeholder: "US",
    hint: "Where your Intercom workspace is hosted: app.intercom.com is US, app.eu.intercom.com is EU, app.au.intercom.com is Australia.",
    token: "Access token",
    region: true,
    cloudflare: false,
    or: "or use a private app's token",
  },
  coda: {
    description:
      "Read-only. Create an API token in Coda (Account settings → API settings → Generate API token; a read-only one is enough). It is stored encrypted and used only to sync the docs you pick.",
    address: null,
    placeholder: "",
    token: "API token",
    tokenHint: "Only docs this account has opened can be picked.",
    cloudflare: false,
  },
  hubspot: {
    description:
      "Read-only. Create a private app in HubSpot (Settings → Integrations → Private apps) with the cms.knowledge_base.articles.read scope, then paste its access token. It is stored encrypted.",
    address: null,
    placeholder: "",
    token: "Private app access token",
    tokenHint: "Needs Service Hub Professional or Enterprise.",
    cloudflare: false,
    or: "or use a private app's token",
  },
}

const REGIONS = [
  { value: "US", label: "United States" },
  { value: "EU", label: "Europe" },
  { value: "AU", label: "Australia" },
]

// Connect a connector that takes a form instead of a sign-in: Outline, Confluence or
// Jira Data Center (address + token), Zendesk (subdomain + email + token, or a
// sign-in when the server has an OAuth client) or a website (a start address). The
// server checks it before saving.
export function IntegrationConnectDialog({
  item,
  open,
  onOpenChange,
  onConnected,
}: {
  item: Integration
  open: boolean
  onOpenChange: (open: boolean) => void
  onConnected: (choose: boolean) => void
}) {
  const [url, setUrl] = useState("")
  const [email, setEmail] = useState("")
  const [token, setToken] = useState("")
  const [cfId, setCfId] = useState("")
  const [cfSecret, setCfSecret] = useState("")
  const [maxPages, setMaxPages] = useState("200")
  const [maxDepth, setMaxDepth] = useState("3")
  const [saving, setSaving] = useState<"form" | "sign_in" | false>(false)
  const [problem, setProblem] = useState<string | null>(null)
  const website = item.auth === "url"
  const copy = TOKEN_COPY[item.id] ?? TOKEN_COPY.outline
  const ids = `connect-${item.id}`

  const [wasOpen, setWasOpen] = useState(false)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setUrl(item.base_url ?? (copy.region ? "US" : ""))
      setToken("")
      setProblem(null)
    }
  }

  // With the server's OAuth client: sign in (Zendesk on the customer's own subdomain,
  // Intercom on their region's host, HubSpot on its own).
  async function signIn() {
    if (copy.address && !url.trim()) {
      setProblem(`Enter your ${copy.address.toLowerCase()} first.`)
      return
    }
    setSaving("sign_in")
    setProblem(null)
    try {
      const res = await api.connectIntegration(item.id, url)
      window.location.href = res.url // off to the provider's consent screen
    } catch (err) {
      setProblem(errorText(err))
      setSaving(false)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving("form")
    setProblem(null)
    try {
      const res = await api.setupIntegration(
        item.id,
        website
          ? { url, max_pages: Number(maxPages) || undefined, max_depth: Number(maxDepth) || undefined }
          : copy.cloudflare
            ? { url, token, cf_client_id: cfId, cf_client_secret: cfSecret }
            : copy.email
              ? { url, email, token }
              : copy.address
                ? { url, token }
                : { token },
      )
      toast.success(
        res.choose ? `${item.name} connected. Now choose what to sync.` : `${item.name} connected. Syncing…`,
      )
      onOpenChange(false)
      onConnected(res.choose)
    } catch (err) {
      setProblem(errorText(err)) // the server's words: what to fix in the form
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{website ? "Add a website" : `Connect ${item.name}`}</DialogTitle>
            <DialogDescription>
              {website
                ? "Public pages only. The crawler stays on this site, obeys robots.txt and re-checks pages on every sync."
                : copy.description}
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            {!website && copy.region ? (
              <Field>
                <FieldLabel htmlFor={`${ids}-url`}>{copy.address}</FieldLabel>
                <Select value={url || "US"} onValueChange={setUrl}>
                  <SelectTrigger id={`${ids}-url`} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {REGIONS.map((r) => (
                      <SelectItem key={r.value} value={r.value}>
                        {r.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {copy.hint && <FieldDescription>{copy.hint}</FieldDescription>}
              </Field>
            ) : (
              (website || copy.address) && (
                <Field>
                  <FieldLabel htmlFor={`${ids}-url`}>
                    {website ? "Start address" : copy.address}
                  </FieldLabel>
                  <Input
                    id={`${ids}-url`}
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder={website ? "https://docs.example.com" : copy.placeholder}
                    inputMode={website || copy.placeholder.startsWith("http") ? "url" : "text"}
                    required
                    autoFocus
                  />
                  {website && (
                    <FieldDescription>A page, or a sitemap ending in .xml.</FieldDescription>
                  )}
                  {!website && copy.hint && <FieldDescription>{copy.hint}</FieldDescription>}
                </Field>
              )
            )}
            {!website && item.sign_in && (
              <>
                <Button
                  type="button"
                  variant="outline"
                  onClick={signIn}
                  loading={saving === "sign_in"}
                  disabled={saving !== false}
                  className="w-full"
                >
                  Sign in with {item.name}
                </Button>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <Separator className="flex-1" />
                  {copy.or ?? "or use an API token"}
                  <Separator className="flex-1" />
                </div>
              </>
            )}
            {website ? (
              <div className="grid grid-cols-2 gap-3">
                <Field>
                  <FieldLabel htmlFor={`${ids}-pages`}>Most pages</FieldLabel>
                  <Input
                    id={`${ids}-pages`}
                    type="number"
                    min={1}
                    max={2000}
                    value={maxPages}
                    onChange={(e) => setMaxPages(e.target.value)}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor={`${ids}-depth`}>Links deep</FieldLabel>
                  <Input
                    id={`${ids}-depth`}
                    type="number"
                    min={1}
                    max={10}
                    value={maxDepth}
                    onChange={(e) => setMaxDepth(e.target.value)}
                  />
                </Field>
              </div>
            ) : (
              <>
                {copy.email && (
                  <Field>
                    <FieldLabel htmlFor={`${ids}-email`}>Agent or admin email</FieldLabel>
                    <Input
                      id={`${ids}-email`}
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      autoComplete="off"
                      required
                    />
                  </Field>
                )}
                <Field>
                  <FieldLabel htmlFor={`${ids}-token`}>{copy.token}</FieldLabel>
                  <Input
                    id={`${ids}-token`}
                    type="password"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    autoComplete="off"
                    required
                    autoFocus={!copy.address}
                  />
                  {copy.tokenHint && <FieldDescription>{copy.tokenHint}</FieldDescription>}
                </Field>
                {copy.cloudflare && (
                  <Collapsible>
                    <CollapsibleTrigger asChild>
                      <Button type="button" variant="ghost" size="sm" className="w-fit px-1">
                        Behind Cloudflare Access? <ChevronDown />
                      </Button>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="grid gap-3 pt-2">
                      <Field>
                        <FieldLabel htmlFor={`${ids}-cf-id`}>Service token client ID</FieldLabel>
                        <Input id={`${ids}-cf-id`} value={cfId} onChange={(e) => setCfId(e.target.value)} />
                      </Field>
                      <Field>
                        <FieldLabel htmlFor={`${ids}-cf-secret`}>Service token client secret</FieldLabel>
                        <Input
                          id={`${ids}-cf-secret`}
                          type="password"
                          value={cfSecret}
                          onChange={(e) => setCfSecret(e.target.value)}
                          autoComplete="off"
                        />
                      </Field>
                    </CollapsibleContent>
                  </Collapsible>
                )}
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
            <Button type="submit" loading={saving === "form"} disabled={saving !== false}>
              {website ? "Add website" : "Connect"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
