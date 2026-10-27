import { useState } from "react"
import { toast } from "sonner"
import { ChevronDown, Loader2 } from "lucide-react"

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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api, type Integration } from "@/lib/api"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const pretty = (value: unknown) => JSON.stringify(value, null, 2)

// Connect an Amazon S3 bucket (connectors/s3.py): a bucket, its region and an optional
// prefix, then either a role in the customer's account that this server assumes with
// the workspace's external ID (preferred) or an IAM user's access keys (also the way
// to reach an S3-compatible store). The server lists the bucket before saving.
export function S3ConnectDialog({
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
  const s3 = item.s3
  const rolesOn = !!s3?.roles
  const [mode, setMode] = useState<"role" | "keys">(rolesOn ? "role" : "keys")
  const [bucket, setBucket] = useState("")
  const [region, setRegion] = useState("")
  const [prefix, setPrefix] = useState("")
  const [roleArn, setRoleArn] = useState("")
  const [accessKey, setAccessKey] = useState("")
  const [secretKey, setSecretKey] = useState("")
  const [endpoint, setEndpoint] = useState("")
  const [saving, setSaving] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const [wasOpen, setWasOpen] = useState(false)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setBucket(s3?.bucket ?? "")
      setRegion(s3?.region ?? "")
      setPrefix(s3?.prefix ?? "")
      setEndpoint(s3?.endpoint ?? "")
      setMode(rolesOn ? "role" : "keys")
      setSecretKey("")
      setProblem(null)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setProblem(null)
    try {
      const where = { bucket, region, prefix }
      const res = await api.setupIntegration(
        item.id,
        mode === "role"
          ? { ...where, role_arn: roleArn }
          : { ...where, access_key_id: accessKey, secret_access_key: secretKey, endpoint },
      )
      toast.success(`${item.name} connected. Syncing…`)
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
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
        <form onSubmit={submit} className="grid min-w-0 grid-cols-1 gap-4">
          <DialogHeader>
            <DialogTitle>Connect {item.name}</DialogTitle>
            <DialogDescription>
              Read-only: the sync lists the bucket and reads the files in it, nothing else.
              Credentials are stored encrypted and never shown again.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="min-w-0 gap-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="s3-bucket">Bucket</FieldLabel>
                <Input
                  id="s3-bucket"
                  value={bucket}
                  onChange={(e) => setBucket(e.target.value)}
                  placeholder="acme-handbook"
                  autoComplete="off"
                  required
                  autoFocus
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="s3-region">Region</FieldLabel>
                <Input
                  id="s3-region"
                  value={region}
                  onChange={(e) => setRegion(e.target.value)}
                  placeholder="eu-west-1"
                  autoComplete="off"
                  required
                />
              </Field>
            </div>
            <Field>
              <FieldLabel htmlFor="s3-prefix">Prefix (optional)</FieldLabel>
              <Input
                id="s3-prefix"
                value={prefix}
                onChange={(e) => setPrefix(e.target.value)}
                placeholder="handbook/"
                autoComplete="off"
              />
              <FieldDescription>Only files under this folder of the bucket are synced.</FieldDescription>
            </Field>
            <Tabs value={mode} onValueChange={(v) => setMode(v as "role" | "keys")} className="min-w-0">
              <TabsList className="w-full">
                <TabsTrigger value="role" disabled={!rolesOn}>
                  IAM role
                </TabsTrigger>
                <TabsTrigger value="keys">Access keys</TabsTrigger>
              </TabsList>
              <TabsContent value="role" className="grid min-w-0 grid-cols-1 gap-3 pt-2">
                <p className="text-sm text-muted-foreground">
                  Create a role in your AWS account that trusts this server with the external ID
                  below, and give it the read policy. Nothing secret is stored: the server asks
                  AWS for hour-long credentials at each sync.
                </p>
                <Field>
                  <FieldLabel>External ID</FieldLabel>
                  <CodeBlock label="external ID" code={s3?.external_id ?? ""} />
                </Field>
                {s3?.trust_policy && (
                  <Field>
                    <FieldLabel>Trust policy</FieldLabel>
                    <CodeBlock label="trust policy" code={pretty(s3.trust_policy)} />
                  </Field>
                )}
                <Field>
                  <FieldLabel>Read policy</FieldLabel>
                  <CodeBlock label="read policy" code={pretty(s3?.read_policy ?? {})} />
                </Field>
                <Field>
                  <FieldLabel htmlFor="s3-role">Role ARN</FieldLabel>
                  <Input
                    id="s3-role"
                    value={roleArn}
                    onChange={(e) => setRoleArn(e.target.value)}
                    placeholder="arn:aws:iam::123456789012:role/knowledge-read"
                    autoComplete="off"
                    required={mode === "role"}
                  />
                </Field>
              </TabsContent>
              <TabsContent value="keys" className="grid min-w-0 grid-cols-1 gap-3 pt-2">
                {!rolesOn && (
                  <p className="text-sm text-muted-foreground">
                    This server isn't set up to assume roles, so connect with the access keys of an
                    IAM user that has the read policy below.
                  </p>
                )}
                <Field>
                  <FieldLabel htmlFor="s3-key">Access key ID</FieldLabel>
                  <Input
                    id="s3-key"
                    value={accessKey}
                    onChange={(e) => setAccessKey(e.target.value)}
                    autoComplete="off"
                    required={mode === "keys"}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="s3-secret">Secret access key</FieldLabel>
                  <Input
                    id="s3-secret"
                    type="password"
                    value={secretKey}
                    onChange={(e) => setSecretKey(e.target.value)}
                    autoComplete="off"
                    required={mode === "keys"}
                  />
                </Field>
                {!rolesOn && (
                  <Field>
                    <FieldLabel>Read policy</FieldLabel>
                    <CodeBlock label="read policy" code={pretty(s3?.read_policy ?? {})} />
                  </Field>
                )}
                <Collapsible defaultOpen={!!endpoint}>
                  <CollapsibleTrigger asChild>
                    <Button type="button" variant="ghost" size="sm" className="w-fit px-1">
                      S3-compatible store? <ChevronDown />
                    </Button>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="pt-2">
                    <Field>
                      <FieldLabel htmlFor="s3-endpoint">Endpoint</FieldLabel>
                      <Input
                        id="s3-endpoint"
                        value={endpoint}
                        onChange={(e) => setEndpoint(e.target.value)}
                        placeholder="https://s3.example.com"
                        inputMode="url"
                        autoComplete="off"
                      />
                      <FieldDescription>
                        MinIO, Cloudflare R2 and other stores that speak the S3 API. Must be a public
                        https address.
                      </FieldDescription>
                    </Field>
                  </CollapsibleContent>
                </Collapsible>
              </TabsContent>
            </Tabs>
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
              Connect
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
