import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDown, CircleCheck, PlugZap, RefreshCw, TriangleAlert } from "lucide-react"
import { Link } from "react-router"
import { toast } from "sonner"

import { AsciiDivider } from "@/components/ascii/ascii-divider"
import { StatusDot } from "@/components/glass/status-dot"
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
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
  FieldTitle,
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
  type ModelProviderChange,
  type ModelProviderSettings as Settings,
  type ProviderInfo,
} from "@/lib/api"
import { useIndexStatus } from "@/lib/index-status-context"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

// The workspace's model providers (Assistant screen, admins): one for answers (chat)
// and one for search (embeddings), each the server's or one of the registry's
// (`providers`, so a new one shows up by itself). A provider that takes an API key
// takes the workspace's own, write-only: the screen only ever says "Key saved".
// Bedrock runs in the company's own AWS account (a role the server assumes with the
// ExternalId generated here, or access keys, sealed). Test connection makes one tiny
// chat call and one embedding with what's typed, saved or not. A change of search
// model rebuilds the index, so it's confirmed first. Nothing ever falls back to
// another provider: a chosen one that fails fails the question with its reason.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const DEFAULT = "default"
const SAME = "__default__" // the embedding size Select's "model default"

type Side = "chat" | "embeddings"

// One plain line per provider and side. A provider the screen has no words for yet
// is described from what the registry says of it.
const ABOUT: Record<string, Partial<Record<Side, string>>> = {
  openai: {
    chat: "OpenAI's models, with this workspace's own API key.",
    embeddings: "OpenAI's embedding model, with this workspace's own API key.",
  },
  bedrock: {
    chat: "Models on Amazon Bedrock in your own AWS account and region: questions stay there.",
    embeddings: "Bedrock's embedding model in your own AWS account and region: documents stay there.",
  },
  anthropic: { chat: "Claude, through Anthropic's API, with this workspace's own API key." },
  local: { embeddings: "Runs on this server's CPU: nothing leaves the server." },
}
// What the local models download the first time (deploy.md, "Local embeddings").
const LOCAL_DOWNLOAD: Record<string, string> = { "BAAI/bge-small-en-v1.5": "about 67 MB" }

function describe(p: ProviderInfo | undefined, side: Side) {
  if (!p) return ""
  const known = ABOUT[p.name]?.[side]
  if (known) return known
  if (p.in_house) return `${p.label}, in your own cloud account.`
  if (p.api_key) return `${p.label}, with this workspace's own API key.`
  return `${p.label}.`
}

// How an index names its embeddings (models.embed_name): OpenAI's model alone, else
// <provider>:<model>:<dimensions> (Python's "None" when the size is unknown).
function embedName(provider: string, model: string | null, dims: number | null) {
  return provider === "openai" ? (model ?? "") : `${provider}:${model}:${dims ?? "None"}`
}

function parseEmbedName(name: string | null, known: string[]) {
  const text = name ?? ""
  const head = text.split(":", 1)[0]
  if (!known.includes(head) || head === "openai" || text.length === head.length)
    return { provider: "openai", model: text }
  const rest = text.slice(head.length + 1)
  const cut = rest.lastIndexOf(":")
  return { provider: head, model: cut > 0 ? rest.slice(0, cut) : rest }
}

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

// A provider's API key: write-only. Typed into an uncontrolled field (so the value
// is never copied into the page's markup), sent once, then only "Key saved".
function ApiKeyField({
  provider,
  saved,
  onChange,
  onRemove,
}: {
  provider: ProviderInfo
  saved: boolean
  onChange: (key: string) => void
  onRemove: () => void
}) {
  const [replacing, setReplacing] = useState(false)
  const id = `mp-key-${provider.name}`
  const typing = !saved || replacing
  return (
    <Field>
      {typing ? (
        <FieldLabel htmlFor={id}>{provider.label} API key</FieldLabel>
      ) : (
        <FieldTitle>{provider.label} API key</FieldTitle>
      )}
      {typing ? (
        <div className="flex items-center gap-2">
          <Input
            id={id}
            type="password"
            className="sm:w-96"
            onChange={(e) => onChange(e.target.value)}
            placeholder="Paste the key"
            autoComplete="new-password"
            spellCheck={false}
          />
          {saved && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                onChange("")
                setReplacing(false)
              }}
            >
              Keep saved key
            </Button>
          )}
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 inline-flex items-center gap-1.5 text-sm">
            <CircleCheck className="size-4 text-success" aria-hidden />
            Key saved
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={`Replace the ${provider.label} key`}
            onClick={() => setReplacing(true)}
          >
            Replace
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Remove the ${provider.label} key`}
            onClick={onRemove}
          >
            Remove
          </Button>
        </div>
      )}
      <FieldDescription>
        Encrypted with this workspace's key and never shown again. A provider chosen here never
        uses the server's key.
      </FieldDescription>
    </Field>
  )
}

function ProviderForm({ settings, onRebuild }: { settings: Settings; onRebuild: () => void }) {
  const queryClient = useQueryClient()
  const { refresh: refreshIndex } = useIndexStatus()
  const providers = settings.providers ?? []
  const info = (name: string) => providers.find((p) => p.name === name)
  const known = providers.map((p) => p.name)
  const defaults = settings.provider_defaults ?? {}

  const [chat, setChat] = useState(settings.chat_provider ?? DEFAULT)
  const [embed, setEmbed] = useState(settings.embed_provider ?? DEFAULT)
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({})
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
  const [confirming, setConfirming] = useState<ModelProviderChange | null>(null)
  const [removing, setRemoving] = useState<ProviderInfo | null>(null)

  const serverChat = settings.server.chat_provider ?? settings.server.provider
  const serverEmbed = settings.server.embed_provider ?? settings.server.provider
  const bedrock = chat === "bedrock" || embed === "bedrock"
  const inUse = [...new Set([chat, embed])].filter((p) => p !== DEFAULT)
  const keyed = inUse.map(info).filter((p): p is ProviderInfo => !!p?.api_key)
  const embedDefault = embed !== DEFAULT ? defaults[embed]?.embed_model : null
  const sizes = embed === "bedrock" ? settings.embed_dimensions[embedModel || embedDefault || ""] : undefined

  function pickChat(value: string) {
    setChat(value)
    setTested(null)
    // Another provider's model ids mean nothing to this one.
    const back = value === settings.chat_provider
    setChatModel(back ? (settings.chat_model ?? "") : "")
    setFastModel(back ? (settings.fast_model ?? "") : "")
  }
  function pickEmbed(value: string) {
    setEmbed(value)
    setTested(null)
    const back = value === settings.embed_provider
    setEmbedModel(back ? (settings.embed_model ?? "") : "")
    setEmbedDims(back ? settings.embed_dims : null)
  }

  function change(): ModelProviderChange {
    const out: ModelProviderChange = { chat_provider: chat, embed_provider: embed }
    const keys = Object.fromEntries(
      keyed.map((p) => [p.name, apiKeys[p.name]?.trim()]).filter(([, k]) => k),
    )
    if (Object.keys(keys).length) out.api_keys = keys
    if (bedrock) {
      Object.assign(out, { region: region.trim() || null, auth, role_arn: roleArn.trim() || null })
      if (auth === "keys" && (keyId || secret)) {
        out.access_key_id = keyId.trim()
        out.secret_access_key = secret
      }
    }
    if (chat !== DEFAULT)
      Object.assign(out, { chat_model: chatModel.trim() || null, fast_model: fastModel.trim() || null })
    if (embed !== DEFAULT) {
      out.embed_model = embedModel.trim() || null
      if (embed === "bedrock") out.embed_dims = embedDims
    }
    return out
  }

  // What the index would be built with after `body`, as the server works it out
  // (model_providers_store._spec_of): a side on default takes the server's provider
  // and models; one switched to another provider starts from its defaults.
  function nextEmbedName(body: ModelProviderChange) {
    const p = body.embed_provider ?? settings.embed_provider ?? DEFAULT
    const switched = p !== settings.embed_provider
    if (p === DEFAULT) {
      const d = serverEmbed ? defaults[serverEmbed] : undefined
      return serverEmbed && d ? embedName(serverEmbed, d.embed_model, d.embed_dims) : DEFAULT
    }
    const d = defaults[p] ?? { embed_model: null, embed_dims: null }
    const model =
      ("embed_model" in body ? body.embed_model : switched ? null : settings.embed_model) || d.embed_model
    const dims =
      ("embed_dims" in body ? body.embed_dims : switched ? null : settings.embed_dims) ||
      (model === d.embed_model ? d.embed_dims : (settings.embed_dimensions[model ?? ""]?.[0] ?? null))
    return embedName(p, model, dims)
  }
  // Asked first only when there's an index to rebuild and the search model moves.
  const rebuilds = (body: ModelProviderChange) =>
    settings.index_embeddings !== null && nextEmbedName(body) !== nextEmbedName({})

  const save = useMutation({
    mutationFn: (body: ModelProviderChange) => assistant.saveModelProvider(body),
    onSuccess: (saved) => {
      setConfirming(null)
      setRemoving(null)
      queryClient.setQueryData(queries.modelProvider().queryKey, saved)
      void queryClient.invalidateQueries({ queryKey: ["assistant", "settings"] })
      if (saved.reindex) {
        toast.success("Saved. Search is being rebuilt with the new model.")
        refreshIndex()
        onRebuild()
      } else {
        toast.success("Saved. The next question uses it.")
      }
    },
    onError: (err, body) => {
      // 409: this switches the embedding model after all. Ask before rebuilding.
      if (err instanceof ApiError && err.status === 409) setConfirming(body)
      else toast.error(errorText(err))
    },
  })
  function submit(body: ModelProviderChange) {
    if (rebuilds(body)) setConfirming(body)
    else save.mutate(body)
  }

  const test = useMutation({
    mutationFn: () => assistant.testModelProvider(change()),
    onSuccess: (result) => {
      setTested(result)
      void queryClient.invalidateQueries({ queryKey: queries.modelProvider().queryKey })
    },
    // A 400 is the server's reason the settings can't be tried (a key not pasted...).
    onError: (err) => setTested({ ok: false, error: errorText(err) }),
  })

  // The search provider the confirmation is about, and the one the index has now.
  const pending = confirming ?? {}
  const nextEmbed = (pending.embed_provider ?? embed) === DEFAULT ? serverEmbed : (pending.embed_provider ?? embed)
  const nextLabel = (nextEmbed && info(nextEmbed)?.label) || "the new provider"
  const indexProvider = parseEmbedName(settings.index_embeddings, known).provider
  const localModel = nextEmbed === "local" ? (defaults.local?.embed_model ?? "") : ""

  const effectiveEmbed = settings.effective.embed_provider ?? settings.effective.provider
  const sidesOnRemoving = removing
    ? [settings.chat_provider === removing.name && "answers", settings.embed_provider === removing.name && "search"]
        .filter(Boolean)
        .join(" and ")
    : ""

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        submit(change())
      }}
    >
      <FieldGroup className="gap-5">
        <div className="grid gap-1.5 text-sm text-muted-foreground">
          <p className="flex flex-wrap items-center gap-1.5">
            Answering with
            <Badge variant="secondary" className="font-normal">
              {info(settings.effective.provider)?.label ?? settings.effective.provider}
            </Badge>
            <span className="min-w-0 font-mono text-xs break-all">{settings.effective.chat_model}</span>
          </p>
          <p className="flex flex-wrap items-center gap-1.5">
            Searching with
            <Badge variant="secondary" className="font-normal">
              {info(effectiveEmbed)?.label ?? effectiveEmbed}
            </Badge>
            <span className="min-w-0 font-mono text-xs break-all">
              {parseEmbedName(settings.effective.embed_model, known).model}
            </span>
          </p>
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <ProviderPicker
            id="mp-chat-provider"
            label="Answers (chat)"
            side="chat"
            value={chat}
            onChange={pickChat}
            settings={settings}
            server={serverChat}
          />
          <ProviderPicker
            id="mp-embed-provider"
            label="Search (embeddings)"
            side="embeddings"
            value={embed}
            onChange={pickEmbed}
            settings={settings}
            server={serverEmbed}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          The workspace never falls back to another provider: if the one chosen fails, the
          question fails with its reason.
        </p>

        {(keyed.length > 0 || bedrock) && <AsciiDivider label="credentials" />}
        {keyed.map((p) => (
          <ApiKeyField
            key={p.name}
            provider={p}
            saved={!!settings.api_keys_saved?.[p.name]}
            onChange={(key) => setApiKeys((all) => ({ ...all, [p.name]: key }))}
            onRemove={() => setRemoving(p)}
          />
        ))}

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
                  className="flex items-start gap-3 rounded-xl border border-border/70 p-3 font-normal has-data-[state=checked]:border-signal/50"
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
                  className="flex items-start gap-3 rounded-xl border border-border/70 p-3 font-normal has-data-[state=checked]:border-signal/50"
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
          </>
        )}

        {(chat !== DEFAULT || embed !== DEFAULT) && (
          <Collapsible
            defaultOpen={!!(settings.chat_model || settings.fast_model || settings.embed_model || settings.embed_dims)}
          >
            <CollapsibleTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="w-fit px-1 [&[data-state=open]>svg]:rotate-180"
              >
                Advanced: model names <ChevronDown />
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="pt-3">
              <div className="grid gap-4 sm:grid-cols-2">
                {chat !== DEFAULT && (
                  <>
                    <Field>
                      <FieldLabel htmlFor="mp-chat">Chat model</FieldLabel>
                      <Input
                        id="mp-chat"
                        value={chatModel}
                        onChange={(e) => setChatModel(e.target.value)}
                        placeholder={defaults[chat]?.chat_model ?? ""}
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
                        placeholder={defaults[chat]?.fast_model ?? ""}
                        autoComplete="off"
                      />
                      <FieldDescription>The guards' checks, titles, summaries.</FieldDescription>
                    </Field>
                  </>
                )}
                {embed !== DEFAULT && (
                  <Field>
                    <FieldLabel htmlFor="mp-embed">Embedding model</FieldLabel>
                    <Input
                      id="mp-embed"
                      value={embedModel}
                      onChange={(e) => {
                        setEmbedModel(e.target.value)
                        setEmbedDims(null)
                      }}
                      placeholder={embedDefault ?? ""}
                      autoComplete="off"
                    />
                    <FieldDescription>Changing it rebuilds search.</FieldDescription>
                  </Field>
                )}
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
            </CollapsibleContent>
          </Collapsible>
        )}

        {tested &&
          (tested.ok ? (
            <div role="status" className="grid gap-1">
              <StatusDot status="live" label="Connected" className="text-sm" />
              <p className="text-xs text-muted-foreground">
                {tested.chat_model} replied “{tested.reply}” ({tested.tokens} tokens), and{" "}
                {tested.embed_model} made a {tested.dimensions.toLocaleString()}-number embedding.
              </p>
            </div>
          ) : (
            <Alert variant="destructive">
              <TriangleAlert />
              <AlertTitle>Couldn't connect</AlertTitle>
              <AlertDescription className="break-words">{tested.error.slice(0, 500)}</AlertDescription>
            </Alert>
          ))}

        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" loading={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
          <Button type="button" variant="outline" loading={test.isPending} onClick={() => test.mutate()}>
            {!test.isPending && <PlugZap data-icon="inline-start" />}
            {test.isPending ? "Testing…" : "Test connection"}
          </Button>
        </div>
        {(settings.updated_by || settings.tested_at) && (
          <p className="text-xs text-muted-foreground">
            {settings.updated_by && `Last changed by ${settings.updated_by} · ${timeAgo(settings.updated_at)}`}
            {settings.updated_by && settings.tested_at && " · "}
            {settings.tested_at && `Last tested ${timeAgo(settings.tested_at)}`}
          </p>
        )}
      </FieldGroup>

      <AlertDialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Rebuild search?</AlertDialogTitle>
            <AlertDialogDescription>
              Search moves to {nextLabel}, so every document is embedded again. The current index
              keeps answering until the new one is ready
              {indexProvider !== nextEmbed
                ? ", by keyword search only, since questions are never sent to the provider you're leaving"
                : ""}
              .{" "}
              {nextEmbed === "local"
                ? `It may take a while, and costs no embedding calls: it runs on this server${
                    LOCAL_DOWNLOAD[localModel]
                      ? `, which downloads the model (${LOCAL_DOWNLOAD[localModel]}) once`
                      : ""
                  }.`
                : `It may take a while, and it costs embedding calls with ${nextLabel}.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault()
                if (confirming) save.mutate({ ...confirming, confirm_reindex: true })
              }}
            >
              Save and rebuild
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={removing !== null} onOpenChange={(open) => !open && setRemoving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove the {removing?.label} key?</AlertDialogTitle>
            <AlertDialogDescription>
              A provider chosen here only ever uses this workspace's own key, so removing it puts{" "}
              {sidesOnRemoving || "it"} back on the server default. To keep {removing?.label}, replace
              the key instead.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault()
                if (!removing) return
                const p = removing.name
                const body: ModelProviderChange = { api_keys: { [p]: null } }
                if (settings.chat_provider === p) body.chat_provider = DEFAULT
                if (settings.embed_provider === p) body.embed_provider = DEFAULT
                setRemoving(null)
                submit(body)
              }}
            >
              Remove key
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </form>
  )
}

// One side's choice: Server default, then every provider that offers it, with a
// plain line about the one chosen and its default models under it.
function ProviderPicker({
  id,
  label,
  side,
  value,
  onChange,
  settings,
  server,
}: {
  id: string
  label: string
  side: Side
  value: string
  onChange: (value: string) => void
  settings: Settings
  server: string | null | undefined
}) {
  const providers = settings.providers ?? []
  const choices = providers.filter((p) => (side === "chat" ? p.chat : p.embeddings))
  const serverInfo = providers.find((p) => p.name === server)
  const shown = value === DEFAULT ? server : value
  const d = shown ? settings.provider_defaults?.[shown] : undefined
  const chosen = providers.find((p) => p.name === value)
  const download = value === "local" && d?.embed_model ? LOCAL_DOWNLOAD[d.embed_model] : undefined
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={DEFAULT}>
            Server default{serverInfo ? ` (${serverInfo.label})` : ""}
          </SelectItem>
          {choices.map((p) => (
            <SelectItem key={p.name} value={p.name}>
              {p.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <FieldDescription>
        {value === DEFAULT
          ? serverInfo
            ? `The provider this server is set up with: ${serverInfo.label}.`
            : "The provider this server is set up with."
          : describe(chosen, side)}
        {value === "local" &&
          (download
            ? ` It downloads a model of ${download} once.`
            : " It downloads its model once.")}
      </FieldDescription>
      {d && (
        <p className="font-mono text-xs break-all text-muted-foreground">
          {side === "chat"
            ? `${d.chat_model ?? ""}${d.fast_model ? ` · fast ${d.fast_model}` : ""}`
            : `${d.embed_model ?? ""}${d.embed_dims ? ` · ${d.embed_dims} numbers` : ""}`}
        </p>
      )}
    </Field>
  )
}

// After a save that rebuilds search: where to watch it (the index status the header
// shows, and the background jobs on Documents).
function RebuildNotice() {
  return (
    <Alert className="mb-5">
      <RefreshCw />
      <AlertTitle>Search is being rebuilt</AlertTitle>
      <AlertDescription className="flex flex-wrap items-center gap-x-3 gap-y-1">
        The current index answers until the new one is ready.
        <IndexStatusText admin />
        <Link to="/documents?jobs=1" className="font-medium text-foreground underline underline-offset-2">
          See indexing progress
        </Link>
      </AlertDescription>
    </Alert>
  )
}

export function ModelProviderSettings() {
  const { data } = useQuery(queries.modelProvider())
  const [rebuilding, setRebuilding] = useState(false)
  if (!data) return <Skeleton className="h-40 w-full rounded-xl" />
  return (
    <>
      {rebuilding && <RebuildNotice />}
      <ProviderForm key={data.updated_at ?? 0} settings={data} onRebuild={() => setRebuilding(true)} />
    </>
  )
}
