import { useState } from "react"
import { toast } from "sonner"
import { ChevronDown, Loader2 } from "lucide-react"

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

// Connect a connector that takes a form instead of a sign-in: Outline (address +
// API token) or a website (a start address). The server checks it before saving.
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
  const [token, setToken] = useState("")
  const [cfId, setCfId] = useState("")
  const [cfSecret, setCfSecret] = useState("")
  const [maxPages, setMaxPages] = useState("200")
  const [maxDepth, setMaxDepth] = useState("3")
  const [saving, setSaving] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const website = item.auth === "url"
  const ids = `connect-${item.id}`

  const [wasOpen, setWasOpen] = useState(false)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setUrl(item.base_url ?? "")
      setToken("")
      setProblem(null)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setProblem(null)
    try {
      const res = await api.setupIntegration(
        item.id,
        website
          ? { url, max_pages: Number(maxPages) || undefined, max_depth: Number(maxDepth) || undefined }
          : { url, token, cf_client_id: cfId, cf_client_secret: cfSecret },
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
                : "Read-only. Create an API token in Outline (Settings → API & Apps). It is stored encrypted and used only to sync the collections you pick."}
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            <Field>
              <FieldLabel htmlFor={`${ids}-url`}>{website ? "Start address" : "Wiki address"}</FieldLabel>
              <Input
                id={`${ids}-url`}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder={website ? "https://docs.example.com" : "https://wiki.example.com"}
                inputMode="url"
                required
                autoFocus
              />
              {website && (
                <FieldDescription>A page, or a sitemap ending in .xml.</FieldDescription>
              )}
            </Field>
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
                <Field>
                  <FieldLabel htmlFor={`${ids}-token`}>API token</FieldLabel>
                  <Input
                    id={`${ids}-token`}
                    type="password"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    autoComplete="off"
                    required
                  />
                </Field>
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
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="animate-spin" />}
              {website ? "Add website" : "Connect"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
