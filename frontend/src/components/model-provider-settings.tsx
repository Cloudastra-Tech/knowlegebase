import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDown, CircleCheck, Plus, PlugZap, RefreshCw, TriangleAlert } from "lucide-react"
import { Link } from "react-router"
import { toast } from "sonner"

import { StatusDot } from "@/components/glass/status-dot"
import { ProviderMark } from "@/components/provider-mark"
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
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
  SelectGroup,
  SelectItem,
  SelectLabel,
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
  type CatalogueModel,
  type ConnectionTest,
  type ModelProviderChange,
  type ModelProviderSettings as Settings,
  type ModelRef,
  type ProviderInfo,
} from "@/lib/api"
import { sameModel, TIER_LABEL } from "@/lib/chat-models"
import { useIndexStatus } from "@/lib/index-status-context"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

// The workspace's model providers (Assistant screen, admins). ANSWERS: the workspace
// holds credentials for several chat providers at once (API keys, write-only: only
// "Key saved ••••last4" comes back; or its own AWS for Bedrock), the admin ticks the
// models people may pick in the chat (each provider's curated list, or a custom id),
// and one of them is the default. With none, answers use the server's model. SEARCH:
// one embeddings provider, never picked per chat; changing it rebuilds the index, so
// it's confirmed first. Test connection lists OpenAI's and Anthropic's models (free);
// Bedrock answers one token and makes one short embedding, since a list wouldn't show
// the model access granted. Nothing ever falls back to another provider.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const DEFAULT = "default"
const SAME = "__default__" // the embedding size Select's "model default"
const keyOf = (m: ModelRef) => `${m.provider}\u0000${m.model}`
const refOf = (key: string): ModelRef => {
  const [provider, model] = key.split("\u0000")
  return { provider, model }
}

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
// is never copied into the page's markup), sent once, then only "Key saved ••••last4".
function ApiKeyField({
  provider,
  saved,
  hint,
  onChange,
}: {
  provider: ProviderInfo
  saved: boolean
  hint?: string
  onChange: (key: string) => void
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
            {hint && <span className="font-mono text-xs text-muted-foreground">••••{hint}</span>}
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
        </div>
      )}
      <FieldDescription>
        Encrypted with this workspace's key and never shown again. A provider set up here never uses
        the server's key.
      </FieldDescription>
    </Field>
  )
}

// One provider's answer models: its curated list (and any custom ids enabled), each
// with a checkbox, and a field to add another model id.
function ProviderModels({
  provider,
  catalogue,
  enabled,
  onToggle,
  onAdd,
  full,
}: {
  provider: ProviderInfo
  catalogue: CatalogueModel[]
  enabled: ModelRef[]
  onToggle: (model: string, on: boolean) => void
  onAdd: (model: string) => void
  full: boolean
}) {
  const [custom, setCustom] = useState("")
  const mine = enabled.filter((m) => m.provider === provider.name)
  const extra = mine.filter((m) => !catalogue.some((c) => c.id === m.model))
  const rows = [
    ...catalogue.map((c) => ({ id: c.id, label: c.label, tier: TIER_LABEL[c.tier] })),
    ...extra.map((m) => ({ id: m.model, label: m.model, tier: "Custom" })),
  ]
  const add = () => {
    const id = custom.trim()
    if (!id) return
    onAdd(id)
    setCustom("")
  }
  return (
    <div className="grid gap-2">
      <ul className="grid gap-1" aria-label={`${provider.label} models`}>
        {rows.map((row) => {
          const id = `mp-model-${provider.name}-${row.id}`
          const on = mine.some((m) => m.model === row.id)
          return (
            <li key={row.id} className="flex min-w-0 items-center gap-3 py-1">
              <Checkbox
                id={id}
                aria-label={row.label}
                checked={on}
                disabled={!on && full}
                onCheckedChange={(v) => onToggle(row.id, v === true)}
              />
              <Label htmlFor={id} className="min-w-0 flex-1 font-normal">
                <span className="truncate">{row.label}</span>
                <span className="hidden truncate font-mono text-xs text-muted-foreground sm:inline">
                  {row.id !== row.label ? row.id : ""}
                </span>
              </Label>
              <span className="text-xs text-muted-foreground">{row.tier}</span>
            </li>
          )
        })}
      </ul>
      <div className="flex items-center gap-2">
        <Input
          aria-label={`Another ${provider.label} model id`}
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault()
              add()
            }
          }}
          placeholder="Another model id"
          autoComplete="off"
          spellCheck={false}
          className="h-8 sm:w-72"
        />
        <Button type="button" variant="outline" size="sm" onClick={add} disabled={!custom.trim() || full}>
          Add
        </Button>
      </div>
    </div>
  )
}

function ProviderForm({ settings, onRebuild }: { settings: Settings; onRebuild: () => void }) {
  const queryClient = useQueryClient()
  const { refresh: refreshIndex } = useIndexStatus()
  const providers = settings.providers ?? []
  const info = (name: string) => providers.find((p) => p.name === name)
  const known = providers.map((p) => p.name)
  const defaults = settings.provider_defaults ?? {}
  const catalogue = settings.catalogue ?? {}
  const maxModels = settings.max_models ?? 24
  const savedModels: ModelRef[] = (settings.chat_models ?? []).map((m) => ({
    provider: m.provider,
    model: m.model,
  }))
  const savedDefault = settings.default_model ?? null
  // Providers that can hold this workspace's credentials for answers: a key, or AWS.
  const keyable = providers.filter((p) => p.chat && (p.api_key || p.name === "bedrock"))

  const [embed, setEmbed] = useState(settings.embed_provider ?? DEFAULT)
  const [added, setAdded] = useState<string[]>(() => {
    const saved = Object.keys(settings.api_keys_saved ?? {}).filter((p) => info(p)?.chat)
    const used = savedModels.map((m) => m.provider)
    return keyable.map((p) => p.name).filter((p) => saved.includes(p) || used.includes(p))
  })
  const [enabled, setEnabled] = useState<ModelRef[]>(savedModels)
  const [chosenDefault, setChosenDefault] = useState<ModelRef | null>(savedDefault)
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({})
  const [region, setRegion] = useState(settings.region ?? "")
  const [auth, setAuth] = useState<AwsAuth>(settings.auth)
  const [roleArn, setRoleArn] = useState(settings.role_arn ?? "")
  const [keyId, setKeyId] = useState("")
  const [secret, setSecret] = useState("")
  const [fastModel, setFastModel] = useState(settings.fast_model ?? "")
  const [embedModel, setEmbedModel] = useState(settings.embed_model ?? "")
  const [embedDims, setEmbedDims] = useState<number | null>(settings.embed_dims)
  const [tested, setTested] = useState<ConnectionTest | null>(null)
  const [confirming, setConfirming] = useState<ModelProviderChange | null>(null)
  const [removing, setRemoving] = useState<ProviderInfo | null>(null)

  const serverChat = settings.server.chat_provider ?? settings.server.provider
  const serverEmbed = settings.server.embed_provider ?? settings.server.provider
  // The default: the one chosen while it's enabled, else the first enabled one; none
  // enabled, the server's model.
  const defaultModel =
    enabled.find((m) => sameModel(m, chosenDefault)) ?? (enabled.length ? enabled[0] : null)
  const chatProvider = defaultModel?.provider ?? DEFAULT
  const bedrock = added.includes("bedrock") || embed === "bedrock"
  // Every provider that needs a key here: the answer providers added, and search's.
  const keyed = [...new Set([...added, embed])]
    .map(info)
    .filter((p): p is ProviderInfo => !!p?.api_key)
  const embedDefault = embed !== DEFAULT ? defaults[embed]?.embed_model : null
  const sizes = embed === "bedrock" ? settings.embed_dimensions[embedModel || embedDefault || ""] : undefined
  const full = enabled.length >= maxModels
  const labelOf = (m: ModelRef) =>
    catalogue[m.provider]?.find((c) => c.id === m.model)?.label ?? m.model

  function toggle(provider: string, model: string, on: boolean) {
    setTested(null)
    setEnabled((all) =>
      on
        ? all.some((m) => m.provider === provider && m.model === model)
          ? all
          : [...all, { provider, model }]
        : all.filter((m) => !(m.provider === provider && m.model === model)),
    )
  }
  function addProvider(p: ProviderInfo) {
    setTested(null)
    setAdded((all) => (all.includes(p.name) ? all : [...all, p.name]))
    // Start it with its default model ticked: one to pick, and a valid save.
    const first = defaults[p.name]?.chat_model ?? catalogue[p.name]?.[0]?.id
    if (first) toggle(p.name, first, true)
  }
  // A provider that was only added here (nothing saved yet) goes without asking.
  function dropProvider(p: ProviderInfo) {
    const saved = !!settings.api_keys_saved?.[p.name] || savedModels.some((m) => m.provider === p.name)
    if (saved) {
      setRemoving(p)
      return
    }
    setAdded((all) => all.filter((x) => x !== p.name))
    setEnabled((all) => all.filter((m) => m.provider !== p.name))
    setApiKeys((all) => ({ ...all, [p.name]: "" }))
  }

  function change(): ModelProviderChange {
    const out: ModelProviderChange = { embed_provider: embed }
    if (defaultModel) {
      out.default_model = defaultModel
      out.chat_models = enabled
      // The fast model belongs to the default's provider: another one starts from its own.
      out.fast_model = defaultModel.provider === settings.chat_provider ? fastModel.trim() || null : null
    } else {
      out.chat_provider = DEFAULT
    }
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
      void queryClient.invalidateQueries({ queryKey: queries.chatModels().queryKey })
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
    onError: (err) => setTested({ ok: false, providers: [], error: errorText(err) }),
  })

  // The search provider the confirmation is about, and the one the index has now.
  const pending = confirming ?? {}
  const nextEmbed = (pending.embed_provider ?? embed) === DEFAULT ? serverEmbed : (pending.embed_provider ?? embed)
  const nextLabel = (nextEmbed && info(nextEmbed)?.label) || "the new provider"
  const indexProvider = parseEmbedName(settings.index_embeddings, known).provider
  const localModel = nextEmbed === "local" ? (defaults.local?.embed_model ?? "") : ""

  // A provider's API key (answers and search share it), and the workspace's AWS
  // (Bedrock, for answers and search alike).
  const keyField = (p: ProviderInfo) => (
    <ApiKeyField
      key={`key-${p.name}`}
      provider={p}
      saved={!!settings.api_keys_saved?.[p.name]}
      hint={settings.api_key_hints?.[p.name]}
      onChange={(key) => setApiKeys((all) => ({ ...all, [p.name]: key }))}
    />
  )
  const awsFields = (
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
  )
  const effectiveEmbed = settings.effective.embed_provider ?? settings.effective.provider
  const addable = keyable.filter((p) => !added.includes(p.name))
  const serverChatInfo = serverChat ? info(serverChat) : undefined

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
            {savedModels.length > 1 && (
              <span className="text-xs">· people can pick from {savedModels.length} models</span>
            )}
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

        <FieldSet>
          <FieldLegend>Answers</FieldLegend>
          <FieldDescription>
            Add the providers this workspace has keys for, then tick the models people can pick in
            the chat. The default answers until someone picks another; guards and titles stay on
            the default's fast model.
          </FieldDescription>
          {added.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Answers use the server's model
              {serverChatInfo ? ` (${serverChatInfo.label}, ${defaults[serverChatInfo.name]?.chat_model ?? ""})` : ""}.
              Add a provider to bring your own and offer a choice.
            </p>
          )}
          <div className="grid gap-4">
            {added.map((name) => {
              const p = info(name)
              if (!p) return null
              return (
                <section
                  key={name}
                  aria-label={p.label}
                  className="grid gap-3 rounded-xl border border-border/70 p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="grid gap-0.5">
                      <h3 className="flex items-center gap-2 text-sm font-medium">
                        <ProviderMark provider={name} />
                        {p.label}
                      </h3>
                      <p className="text-xs text-muted-foreground">{describe(p, "chat")}</p>
                    </div>
                    {!(name === "bedrock" && embed === "bedrock") && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        aria-label={`Remove ${p.label}`}
                        onClick={() => dropProvider(p)}
                      >
                        Remove
                      </Button>
                    )}
                  </div>
                  {p.api_key && keyField(p)}
                  {name === "bedrock" && awsFields}
                  <ProviderModels
                    provider={p}
                    catalogue={catalogue[name] ?? []}
                    enabled={enabled}
                    onToggle={(model, on) => toggle(name, model, on)}
                    onAdd={(model) => toggle(name, model, true)}
                    full={full}
                  />
                </section>
              )
            })}
          </div>
          {addable.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" variant="outline" size="sm" className="w-fit">
                  <Plus data-icon="inline-start" />
                  Add a provider
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-56">
                {addable.map((p) => (
                  <DropdownMenuItem key={p.name} onSelect={() => addProvider(p)}>
                    {p.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {enabled.length > 0 && (
            <Field>
              <FieldLabel htmlFor="mp-default">Default model</FieldLabel>
              <Select
                value={defaultModel ? keyOf(defaultModel) : undefined}
                onValueChange={(v) => {
                  setChosenDefault(refOf(v))
                  setTested(null)
                }}
              >
                <SelectTrigger id="mp-default" className="w-full sm:w-96">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {added.map((name) =>
                    enabled.some((m) => m.provider === name) ? (
                      <SelectGroup key={name}>
                        <SelectLabel>{info(name)?.label ?? name}</SelectLabel>
                        {enabled
                          .filter((m) => m.provider === name)
                          .map((m) => (
                            <SelectItem key={keyOf(m)} value={keyOf(m)}>
                              {labelOf(m)}
                            </SelectItem>
                          ))}
                      </SelectGroup>
                    ) : null,
                  )}
                </SelectContent>
              </Select>
              <FieldDescription>
                {enabled.length === 1
                  ? "With one model enabled, the chat shows no model menu."
                  : `People pick from ${enabled.length} models in the chat; new chats start on this one.`}
              </FieldDescription>
            </Field>
          )}
        </FieldSet>

        <ProviderPicker
          id="mp-embed-provider"
          label="Search (embeddings)"
          side="embeddings"
          value={embed}
          onChange={(value) => {
            setEmbed(value)
            setTested(null)
            const back = value === settings.embed_provider
            setEmbedModel(back ? (settings.embed_model ?? "") : "")
            setEmbedDims(back ? settings.embed_dims : null)
          }}
          settings={settings}
          server={serverEmbed}
        />
        <p className="text-xs text-muted-foreground">
          Search uses one provider for everyone, and isn't picked per chat. The workspace never falls
          back to another provider: if the one chosen fails, the question fails with its reason.
        </p>

        {/* Search on a provider no answers use: its credentials here. */}
        {embed !== DEFAULT && !added.includes(embed) && (
          <div className="grid gap-4 rounded-xl border border-border/70 p-4">
            {embed === "bedrock" ? awsFields : info(embed)?.api_key ? keyField(info(embed)!) : null}
          </div>
        )}


        {(chatProvider !== DEFAULT || embed !== DEFAULT) && (
          <Collapsible defaultOpen={!!(settings.fast_model || settings.embed_model || settings.embed_dims)}>
            <CollapsibleTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="w-fit px-1 [&[data-state=open]>svg]:rotate-180"
              >
                Advanced: fast and embedding models <ChevronDown />
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="pt-3">
              <div className="grid gap-4 sm:grid-cols-2">
                {chatProvider !== DEFAULT && (
                  <Field>
                    <FieldLabel htmlFor="mp-fast">Fast model</FieldLabel>
                    <Input
                      id="mp-fast"
                      value={chatProvider === settings.chat_provider ? fastModel : ""}
                      onChange={(e) => setFastModel(e.target.value)}
                      disabled={chatProvider !== settings.chat_provider}
                      placeholder={defaults[chatProvider]?.fast_model ?? ""}
                      autoComplete="off"
                    />
                    <FieldDescription>
                      The guards' checks, titles, summaries. {info(chatProvider)?.label}'s, whichever
                      model a chat picks.
                    </FieldDescription>
                  </Field>
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

        {tested && <TestResult tested={tested} />}

        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" loading={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
          <Button type="button" variant="outline" loading={test.isPending} onClick={() => test.mutate()}>
            {!test.isPending && <PlugZap data-icon="inline-start" />}
            {test.isPending ? "Testing…" : "Test connection"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Testing lists OpenAI's and Anthropic's models, for free. AWS Bedrock answers one word and
          makes one short embedding: a fraction of a cent.
        </p>
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
            <AlertDialogTitle>Remove {removing?.label}?</AlertDialogTitle>
            <AlertDialogDescription>
              Its key is deleted and its models leave the chat's model menu
              {removing && settings.embed_provider === removing.name ? "; search goes back to the server default" : ""}
              . Chats that used one of them start on the default model next time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault()
                if (!removing) return
                const p = removing.name
                const left = savedModels.filter((m) => m.provider !== p)
                const body: ModelProviderChange = {}
                if (removing.api_key) body.api_keys = { [p]: null }
                if (left.length) {
                  body.chat_models = left
                  body.default_model = left.find((m) => sameModel(m, savedDefault)) ?? left[0]
                } else body.chat_provider = DEFAULT
                if (settings.embed_provider === p) body.embed_provider = DEFAULT
                setRemoving(null)
                submit(body)
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </form>
  )
}

// Test connection's result: a line per provider, with the enabled models it didn't list.
function TestResult({ tested }: { tested: ConnectionTest }) {
  if (tested.error)
    return (
      <Alert variant="destructive">
        <TriangleAlert />
        <AlertTitle>Couldn't connect</AlertTitle>
        <AlertDescription className="break-words">{tested.error.slice(0, 500)}</AlertDescription>
      </Alert>
    )
  return (
    <ul role="status" className="grid gap-2">
      {tested.providers.map((p) => (
        <li key={p.provider} className="grid gap-0.5">
          <StatusDot
            status={p.ok ? "live" : "error"}
            label={
              p.ok
                ? `${p.label}: connected`
                : p.missing.length
                  ? `${p.label}: model not found`
                  : `${p.label}: couldn't connect`
            }
            className="text-sm"
          />
          <p className="pl-4 text-xs break-words text-muted-foreground">
            {!p.ok
              ? p.error
              : p.checked?.length
                ? `It answered with ${p.checked.join(" and ")}.`
                : p.models === null
                  ? "Runs on this server: nothing to reach."
                  : `It lists ${p.models.toLocaleString()} models.`}
          </p>
        </li>
      ))}
    </ul>
  )
}

// The search side's choice: Server default, then every provider with embeddings, with
// a plain line about the one chosen and its default model under it.
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
        <SelectTrigger id={id} className="w-full sm:w-96">
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
