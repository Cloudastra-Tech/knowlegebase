import { useEffect, useState, type FormEvent, type ReactNode } from "react"
import { Check, Copy } from "lucide-react"
import { toast } from "sonner"

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
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  signIn,
  type NewProvider,
  type Protocol,
  type SamlConnectionInput,
  type SignInConnection,
} from "@/lib/api"
import { cn } from "@/lib/utils"

// A value someone copies into their identity provider's setup page.
export function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error("Couldn't copy. Select the text and copy it instead.")
    }
  }

  return (
    <div className="grid grid-cols-1 gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-md bg-muted px-2 py-1.5 font-mono text-xs select-all">
          {value}
        </code>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="size-7 shrink-0"
          onClick={copy}
          aria-label={`Copy ${label}`}
        >
          {copied ? <Check /> : <Copy />}
        </Button>
      </div>
    </div>
  )
}

// Something to know before going on, in the warning token: a mono "!" (a colour never
// stands alone) beside words in the body colour, which keep their contrast on glass.
export function Notice({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      data-slot="notice"
      className={cn("flex gap-2.5 rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm", className)}
    >
      <span aria-hidden className="font-mono leading-5 font-medium text-warning select-none">
        !
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

type SamlSource = "url" | "xml" | "manual" | "keep"

const KEEP_SOURCE = { value: "keep" as SamlSource, label: "Keep current" }
const SAML_SOURCES: { value: SamlSource; label: string }[] = [
  { value: "url", label: "Metadata URL" },
  { value: "xml", label: "Metadata XML" },
  { value: "manual", label: "Enter by hand" },
]

// Add an identity provider (OpenID Connect or SAML), or edit one. The parent gives
// it a new `key` each time it opens, so the form starts from `editing` (or blank).
export function ProviderDialog({
  open,
  onOpenChange,
  editing,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  editing: SignInConnection | null
  onSaved: (conn: SignInConnection, added: boolean) => void
}) {
  const [protocol, setProtocol] = useState<Protocol>(editing?.protocol ?? "oidc")
  const [label, setLabel] = useState(editing?.label ?? "")
  const [issuer, setIssuer] = useState(editing?.protocol === "oidc" ? editing.issuer : "")
  const [clientId, setClientId] = useState(editing?.client_id ?? "")
  const [clientSecret, setClientSecret] = useState("")
  const [logoutAtIdp, setLogoutAtIdp] = useState(editing?.logout_at_idp ?? false)
  const [source, setSource] = useState<SamlSource>(editing ? "keep" : "url")
  const [metadataUrl, setMetadataUrl] = useState(editing?.metadata_url ?? "")
  const [metadataXml, setMetadataXml] = useState("")
  const [entityId, setEntityId] = useState("")
  const [ssoUrl, setSsoUrl] = useState("")
  const [certificate, setCertificate] = useState("")
  const [emailAttribute, setEmailAttribute] = useState(editing?.email_attribute ?? "")
  const [planned, setPlanned] = useState<NewProvider | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // A new provider: get its ID and the addresses its IdP will ask for.
  useEffect(() => {
    if (!open || editing) return
    signIn
      .newProvider()
      .then(setPlanned)
      .catch((err) => setError(err instanceof Error ? err.message : "Couldn't start."))
  }, [open, editing])

  const adding = !editing
  // The addresses this app has at the provider: known before it's added.
  const ours = editing ?? planned

  function samlBody(): SamlConnectionInput {
    const body: SamlConnectionInput = { label: label.trim() }
    if (source === "url") body.metadata_url = metadataUrl.trim()
    if (source === "xml") body.metadata_xml = metadataXml.trim()
    if (source === "manual") {
      Object.assign(body, { entity_id: entityId.trim(), sso_url: ssoUrl.trim(), certificate })
    }
    if (adding || emailAttribute.trim() !== (editing?.email_attribute ?? ""))
      body.email_attribute = emailAttribute.trim()
    return body
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSaving(true)
    const issuerChanged = !adding && protocol === "oidc" && issuer.trim() !== editing.issuer
    if (issuerChanged && !clientSecret) {
      // The server refuses too: the saved secret mustn't go to a new issuer.
      setError("Enter the client secret again when you change the issuer.")
      setSaving(false)
      return
    }
    try {
      let conn: SignInConnection
      if (protocol === "oidc") {
        const body = {
          label: label.trim(),
          client_id: clientId.trim(),
          ...(clientSecret ? { client_secret: clientSecret } : {}),
          ...(adding || issuer.trim() !== editing?.issuer ? { issuer: issuer.trim() } : {}),
          ...(!adding && logoutAtIdp !== editing.logout_at_idp ? { logout_at_idp: logoutAtIdp } : {}),
        }
        conn = adding
          ? await signIn.addOidc({ ...body, id: planned?.id, enabled: false })
          : await signIn.editOidc(editing.id, body)
      } else {
        conn = adding
          ? await signIn.addSaml({ ...samlBody(), id: planned?.id, enabled: false })
          : await signIn.editSaml(editing.id, samlBody())
      }
      onSaved(conn, adding)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl">
        <form onSubmit={save} className="grid grid-cols-1 gap-5">
          <DialogHeader>
            <DialogTitle>{adding ? "Add identity provider" : `Edit ${editing.label}`}</DialogTitle>
            <DialogDescription>
              {adding
                ? "Your company's Okta, Entra ID, Google Workspace, Keycloak or any other " +
                  "OpenID Connect or SAML provider. It's added turned off, so you can test it first."
                : "Changes apply to the next sign-in."}
            </DialogDescription>
          </DialogHeader>

          {adding && (
            <Tabs panels={false} value={protocol} onValueChange={(v) => setProtocol(v as Protocol)}>
              <TabsList className="w-full">
                <TabsTrigger value="oidc">OpenID Connect</TabsTrigger>
                <TabsTrigger value="saml">SAML</TabsTrigger>
              </TabsList>
            </Tabs>
          )}

          {ours && (
            <div className="grid grid-cols-1 gap-3 rounded-lg border bg-muted/40 p-3">
              <p className="text-sm font-medium">
                {adding ? "1. Enter these at your provider" : "This app at your provider"}
              </p>
              {protocol === "oidc" ? (
                <CopyField label="Redirect URI (sign-in callback)" value={ours.redirect_uri!} />
              ) : (
                <>
                  <CopyField label="Entity ID (audience)" value={ours.sp_entity_id!} />
                  <CopyField label="ACS URL (single sign-on URL)" value={ours.acs_url!} />
                  <CopyField label="SP metadata URL" value={ours.sp_metadata_url!} />
                  {adding && (
                    <p className="text-xs text-muted-foreground">
                      The metadata URL works once the provider is added. It includes the
                      certificate your provider needs if it checks signed requests.
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          <FieldGroup className="gap-4">
            {adding && <p className="text-sm font-medium">2. Then enter your provider's details</p>}
            <Field>
              <FieldLabel htmlFor="provider-label">Name on the sign-in button</FieldLabel>
              <Input
                id="provider-label"
                placeholder="e.g. Okta"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                maxLength={60}
                required
              />
            </Field>

            {protocol === "oidc" ? (
              <>
                <Field>
                  <FieldLabel htmlFor="provider-issuer">Issuer URL</FieldLabel>
                  <Input
                    id="provider-issuer"
                    type="url"
                    className="font-mono"
                    placeholder="https://your-company.okta.com"
                    value={issuer}
                    onChange={(e) => setIssuer(e.target.value)}
                    required
                  />
                  <FieldDescription>
                    Where your provider publishes /.well-known/openid-configuration.
                  </FieldDescription>
                </Field>
                <Field>
                  <FieldLabel htmlFor="provider-client-id">Client ID</FieldLabel>
                  <Input
                    id="provider-client-id"
                    className="font-mono"
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    required
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="provider-client-secret">Client secret</FieldLabel>
                  <Input
                    id="provider-client-secret"
                    type="password"
                    autoComplete="off"
                    placeholder={
                      adding || issuer.trim() !== editing.issuer
                        ? ""
                        : "Leave empty to keep the current secret"
                    }
                    value={clientSecret}
                    onChange={(e) => setClientSecret(e.target.value)}
                    required={adding}
                  />
                </Field>
                {editing?.supports_logout && (
                  <Field>
                    <div className="flex items-start gap-3">
                      <Checkbox
                        id="provider-logout"
                        className="mt-0.5"
                        checked={logoutAtIdp}
                        onCheckedChange={(v) => setLogoutAtIdp(v === true)}
                      />
                      <div className="grid gap-1">
                        <Label htmlFor="provider-logout">Also sign out at {editing.label}</Label>
                        <FieldDescription>
                          Signing out here also ends their {editing.label} session, which
                          signs them out of other apps that use it too. Register this sign-out
                          redirect URI there:{" "}
                          <span className="font-mono text-xs break-all">{editing.post_logout_redirect_uri}</span>
                        </FieldDescription>
                      </div>
                    </div>
                  </Field>
                )}
              </>
            ) : (
              <>
                <Field>
                  <FieldLabel id="saml-source-label">Your provider's SAML metadata</FieldLabel>
                  <RadioGroup
                    aria-labelledby="saml-source-label"
                    value={source}
                    onValueChange={(v) => setSource(v as SamlSource)}
                    className="flex flex-wrap gap-x-5 gap-y-2"
                  >
                    {(adding ? SAML_SOURCES : [KEEP_SOURCE, ...SAML_SOURCES]).map(
                      (o) => (
                        <div key={o.value} className="flex items-center gap-2">
                          <RadioGroupItem value={o.value} id={`saml-source-${o.value}`} />
                          <Label htmlFor={`saml-source-${o.value}`} className="font-normal">
                            {o.label}
                          </Label>
                        </div>
                      ),
                    )}
                  </RadioGroup>
                </Field>
                {source === "url" && (
                  <Field>
                    <FieldLabel htmlFor="saml-metadata-url">Metadata URL</FieldLabel>
                    <Input
                      id="saml-metadata-url"
                      type="url"
                      className="font-mono"
                      placeholder="https://your-company.okta.com/app/…/sso/saml/metadata"
                      value={metadataUrl}
                      onChange={(e) => setMetadataUrl(e.target.value)}
                      required
                    />
                  </Field>
                )}
                {source === "xml" && (
                  <Field>
                    <FieldLabel htmlFor="saml-metadata-xml">Metadata XML</FieldLabel>
                    <Textarea
                      id="saml-metadata-xml"
                      rows={6}
                      placeholder="<md:EntityDescriptor …"
                      className="font-mono text-xs"
                      value={metadataXml}
                      onChange={(e) => setMetadataXml(e.target.value)}
                      required
                    />
                  </Field>
                )}
                {source === "manual" && (
                  <>
                    <Field>
                      <FieldLabel htmlFor="saml-entity-id">Identity provider entity ID (issuer)</FieldLabel>
                      <Input
                        id="saml-entity-id"
                        className="font-mono"
                        value={entityId}
                        onChange={(e) => setEntityId(e.target.value)}
                        required
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="saml-sso-url">Sign-in URL (HTTP-Redirect)</FieldLabel>
                      <Input
                        id="saml-sso-url"
                        type="url"
                        className="font-mono"
                        value={ssoUrl}
                        onChange={(e) => setSsoUrl(e.target.value)}
                        required
                      />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="saml-certificate">Signing certificate</FieldLabel>
                      <Textarea
                        id="saml-certificate"
                        rows={5}
                        placeholder="-----BEGIN CERTIFICATE-----"
                        className="font-mono text-xs"
                        value={certificate}
                        onChange={(e) => setCertificate(e.target.value)}
                        required
                      />
                    </Field>
                  </>
                )}
                <Field>
                  <FieldLabel htmlFor="saml-email-attribute">Email attribute (optional)</FieldLabel>
                  <Input
                    id="saml-email-attribute"
                    placeholder="email"
                    value={emailAttribute}
                    onChange={(e) => setEmailAttribute(e.target.value)}
                  />
                  <FieldDescription>
                    Leave empty to use the usual ones (email, mail, …) or an email-shaped NameID.
                  </FieldDescription>
                </Field>
              </>
            )}
            {error && <FieldError>{error}</FieldError>}
          </FieldGroup>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={saving} disabled={adding && !planned}>
              {adding ? "Add provider" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
