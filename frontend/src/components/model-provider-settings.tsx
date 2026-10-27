import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleCheck, Loader2, PlugZap, TriangleAlert } from "lucide-react"
import { toast } from "sonner"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { IndexStatusText } from "@/components/index-progress"
import { CopyField } from "@/components/sign-in-provider-dialog"
import {
  ApiError,
  assistant,
  type AwsAuth,
  type ConnectionTest,
  type ModelProvider,
  type ModelProviderChange,
  type ModelProviderSettings as Settings,
} from "@/lib/api"
import { useIndexStatus } from "@/lib/index-status-context"
import { queries } from "@/lib/queries"

// The workspace's model provider (Assistant screen, admins): the server's, or AWS
// Bedrock in the company's own AWS account ("Bring your own AWS": a role the server
// assumes with the ExternalId generated here, or access keys, sealed). Test
// connection makes one tiny chat call and one embedding with what's typed, saved or
// not. A change of embedding model rebuilds the index, so it's confirmed first.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const SAME = "__default__" // the embedding size Select's "model default"

function JsonBlock({ label, value }: { label: string; value: object }) {
  const text = JSON.stringify(value, null, 2)
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error("Couldn't copy. Select the text and copy it instead.")
    }
  }
  return (
    <div className="grid min-w-0 gap-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{label}</span>
        <Button type="button" variant="ghost" size="sm" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="max-h-56 overflow-auto rounded-md bg-muted px-3 py-2 font-mono text-xs leading-relaxed">
        {text}
      </pre>
    </div>
  )
}

function ProviderForm({ settings }: { settings: Settings }) {
  const queryClient = useQueryClient()
  const { refresh: refreshIndex } = useIndexStatus()
  const [provider, setProvider] = useState<ModelProvider>(settings.provider)
  const [region, setRegion] = useState(settings.region ?? "")
  const [auth, setAuth] = useState<AwsAuth>(settings.auth)
  const [roleArn, setRoleArn] = useState(settings.role_arn ?? "")
  const [keyId, setKeyId] = useState("")
  const [secret, setSecret] = useState("")
  const [chatModel, setChatModel] = useState(settings.chat_model ?? "")
  const [fastModel, setFastModel] = useState(settings.fast_model ?? "")
  const [embedModel, setEmbedModel] = useState(settings.embed_model ?? "")
  const [embedDims, setEmbedDims] = useState<number | null>(settings.embed_dims)
  const [tested, setTested] = useState<ConnectionTest | null>(null)
  const [confirming, setConfirming] = useState(false)

  const bedrock = provider === "bedrock"
  const sizes = settings.embed_dimensions[embedModel || settings.defaults.embed_model]

  function change(): ModelProviderChange {
    const out: ModelProviderChange = { provider }
    if (!bedrock) return out
    Object.assign(out, {
      region: region.trim() || null,
      auth,
      role_arn: roleArn.trim() || null,
      chat_model: chatModel.trim() || null,
      fast_model: fastModel.trim() || null,
      embed_model: embedModel.trim() || null,
      embed_dims: embedDims,
    })
    if (auth === "keys" && (keyId || secret)) {
      out.access_key_id = keyId.trim()
      out.secret_access_key = secret
    }
    return out
  }

  const save = useMutation({
    mutationFn: (body: ModelProviderChange) => assistant.saveModelProvider(body),
    onSuccess: (saved) => {
      setKeyId("")
      setSecret("")
      setConfirming(false)
      queryClient.setQueryData(queries.modelProvider().queryKey, saved)
      void queryClient.invalidateQueries({ queryKey: ["assistant", "settings"] })
      if (saved.reindex) {
        toast.success("Saved. The index is being rebuilt with the new embedding model.")
        refreshIndex()
      } else {
        toast.success("Saved. The next question uses it.")
      }
    },
    onError: (err) => {
      // 409: this switches the embedding model. Ask before rebuilding the index.
      if (err instanceof ApiError && err.status === 409) setConfirming(true)
      else toast.error(errorText(err))
    },
  })
  const test = useMutation({
    mutationFn: () => assistant.testModelProvider(change()),
    onSuccess: (result) => {
      setTested(result)
      void queryClient.invalidateQueries({ queryKey: queries.modelProvider().queryKey })
    },
    onError: (err) => {
      setTested(null)
      toast.error(errorText(err))
    },
  })

  const serverName =
    settings.server.provider === "bedrock" ? "AWS Bedrock (this server's account)" : "OpenAI"

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(change())
      }}
    >
      <FieldGroup className="gap-5">
        <p className="flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
          Answering with
          <Badge variant="secondary" className="font-normal">
            {settings.effective.provider === "bedrock" ? "AWS Bedrock" : "OpenAI"}
          </Badge>
          <span className="min-w-0 break-all font-mono text-xs">{settings.effective.chat_model}</span>
        </p>

        <Field>
          <FieldLabel htmlFor="mp-provider">Provider</FieldLabel>
          <Select value={provider} onValueChange={(v) => setProvider(v as ModelProvider)}>
            <SelectTrigger id="mp-provider" className="w-full sm:w-96">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="default">Server default ({serverName})</SelectItem>
              <SelectItem value="bedrock">AWS Bedrock in your own AWS account</SelectItem>
            </SelectContent>
          </Select>
          <FieldDescription>
            {bedrock
              ? "Chat, the guards' checks and search embeddings all run on Bedrock in your account and region. Nothing is sent to OpenAI."
              : "The provider this server is set up with."}
          </FieldDescription>
        </Field>

        {bedrock && (
          <>
            <Field>
              <FieldLabel htmlFor="mp-region">AWS region</FieldLabel>
              <Input
                id="mp-region"
                className="sm:w-60"
                value={region}
                onChange={(e) => setRegion(e.target.value)}
                placeholder={settings.server.region ?? "us-east-1"}
                autoComplete="off"
              />
              <FieldDescription>Where your Bedrock models are enabled.</FieldDescription>
            </Field>

            <FieldSet>
              <FieldLegend variant="label">How the server signs in to AWS</FieldLegend>
              <RadioGroup
                value={auth}
                onValueChange={(v) => setAuth(v as AwsAuth)}
                className="grid gap-3 sm:grid-cols-2"
              >
                <Label
                  htmlFor="mp-auth-role"
                  className="flex items-start gap-3 rounded-xl border border-border/70 p-3 font-normal"
                >
                  <RadioGroupItem value="role" id="mp-auth-role" className="mt-0.5" />
                  <span className="grid gap-0.5">
                    <span className="font-medium">A role it assumes</span>
                    <span className="text-xs text-muted-foreground">
                      Recommended: no AWS secret is stored here.
                    </span>
                  </span>
                </Label>
                <Label
                  htmlFor="mp-auth-keys"
                  className="flex items-start gap-3 rounded-xl border border-border/70 p-3 font-normal"
                >
                  <RadioGroupItem value="keys" id="mp-auth-keys" className="mt-0.5" />
                  <span className="grid gap-0.5">
                    <span className="font-medium">Access keys</span>
                    <span className="text-xs text-muted-foreground">
                      An IAM user's keys, encrypted with this workspace's key.
                    </span>
                  </span>
                </Label>
              </RadioGroup>
            </FieldSet>

            {auth === "role" ? (
              <div className="grid min-w-0 gap-4">
                <Field>
                  <FieldLabel htmlFor="mp-role">Role ARN</FieldLabel>
                  <Input
                    id="mp-role"
                    value={roleArn}
                    onChange={(e) => setRoleArn(e.target.value)}
                    placeholder="arn:aws:iam::123456789012:role/knowledge-assistant-bedrock"
                    autoComplete="off"
                  />
                </Field>
                <CopyField label="External ID (put it in the role's trust policy)" value={settings.external_id} />
                <JsonBlock label="Trust policy for the role" value={settings.trust_policy} />
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field>
                  <FieldLabel htmlFor="mp-key-id">Access key ID</FieldLabel>
                  <Input
                    id="mp-key-id"
                    value={keyId}
                    onChange={(e) => setKeyId(e.target.value)}
                    placeholder={
                      settings.keys_saved ? `Saved (…${settings.access_key_hint ?? ""})` : "AKIA…"
                    }
                    autoComplete="off"
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="mp-secret">Secret access key</FieldLabel>
                  <Input
                    id="mp-secret"
                    type="password"
                    value={secret}
                    onChange={(e) => setSecret(e.target.value)}
                    placeholder={settings.keys_saved ? "Saved: type to replace" : ""}
                    autoComplete="new-password"
                  />
                </Field>
              </div>
            )}

            <JsonBlock label="Permissions the role or user needs" value={settings.permissions_policy} />

            <div className="grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="mp-chat">Chat model</FieldLabel>
                <Input
                  id="mp-chat"
                  value={chatModel}
                  onChange={(e) => setChatModel(e.target.value)}
                  placeholder={settings.defaults.chat_model}
                  autoComplete="off"
                />
                <FieldDescription>Writes the answers. It must support tool use.</FieldDescription>
              </Field>
              <Field>
                <FieldLabel htmlFor="mp-fast">Fast model</FieldLabel>
                <Input
                  id="mp-fast"
                  value={fastModel}
                  onChange={(e) => setFastModel(e.target.value)}
                  placeholder={settings.defaults.fast_model}
                  autoComplete="off"
                />
                <FieldDescription>The guards' checks, titles, summaries.</FieldDescription>
              </Field>
              <Field>
                <FieldLabel htmlFor="mp-embed">Embedding model</FieldLabel>
                <Input
                  id="mp-embed"
                  value={embedModel}
                  onChange={(e) => {
                    setEmbedModel(e.target.value)
                    setEmbedDims(null)
                  }}
                  placeholder={settings.defaults.embed_model}
                  autoComplete="off"
                />
                <FieldDescription>Changing it rebuilds the search index.</FieldDescription>
              </Field>
              {sizes && sizes.length > 1 && (
                <Field>
                  <FieldLabel htmlFor="mp-dims">Embedding size</FieldLabel>
                  <Select
                    value={embedDims ? String(embedDims) : SAME}
                    onValueChange={(v) => setEmbedDims(v === SAME ? null : Number(v))}
                  >
                    <SelectTrigger id="mp-dims" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={SAME}>Default ({sizes[0]})</SelectItem>
                      {sizes.map((n) => (
                        <SelectItem key={n} value={String(n)}>
                          {n} numbers
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              )}
            </div>
          </>
        )}

        {tested &&
          (tested.ok ? (
            <Alert>
              <CircleCheck />
              <AlertTitle>Connected</AlertTitle>
              <AlertDescription>
                {tested.chat_model} replied “{tested.reply}” ({tested.tokens} tokens), and{" "}
                {tested.embed_model} made a {tested.dimensions}-number embedding.
              </AlertDescription>
            </Alert>
          ) : (
            <Alert variant="destructive">
              <TriangleAlert />
              <AlertTitle>Couldn't connect</AlertTitle>
              <AlertDescription className="break-words">{tested.error}</AlertDescription>
            </Alert>
          ))}

        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" data-icon="inline-start" />}
            Save
          </Button>
          {bedrock && (
            <Button
              type="button"
              variant="outline"
              disabled={test.isPending}
              onClick={() => test.mutate()}
            >
              {test.isPending ? (
                <Loader2 className="animate-spin" data-icon="inline-start" />
              ) : (
                <PlugZap data-icon="inline-start" />
              )}
              Test connection
            </Button>
          )}
          <IndexStatusText />
        </div>
        {settings.tested_at && bedrock && (
          <p className="text-xs text-muted-foreground">
            Last tested {new Date(settings.tested_at * 1000).toLocaleString()}
          </p>
        )}
      </FieldGroup>

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Rebuild the search index?</AlertDialogTitle>
            <AlertDialogDescription>
              This changes the embedding model, so every document is embedded again with the new
              one. Questions keep being answered from the current index until the new one is
              ready{settings.index_embeddings?.startsWith("bedrock:") === bedrock
                ? ""
                : ", by keyword search only, since the old index belongs to the other provider"}
              . Progress shows in the index status.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => save.mutate({ ...change(), confirm_reindex: true })}>
              Save and rebuild
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </form>
  )
}

export function ModelProviderSettings() {
  const { data } = useQuery(queries.modelProvider())
  if (!data) return <Skeleton className="h-40 w-full rounded-xl" />
  return <ProviderForm key={data.updated_at ?? 0} settings={data} />
}
