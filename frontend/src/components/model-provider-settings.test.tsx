import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"
import { describe, expect, it } from "vitest"

import { ModelProviderSettings } from "@/components/model-provider-settings"
import { json, mockApi, withProviders } from "@/test/render"

// What Radix's Select needs and jsdom doesn't have.
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.releasePointerCapture ??= () => {}
Element.prototype.scrollIntoView ??= () => {}

const PROVIDERS = [
  { name: "openai", label: "OpenAI", chat: true, embeddings: true, in_house: false, api_key: true },
  { name: "bedrock", label: "AWS Bedrock", chat: true, embeddings: true, in_house: true, api_key: false },
  { name: "local", label: "Local (on this server)", chat: false, embeddings: true, in_house: false, api_key: false },
  { name: "anthropic", label: "Anthropic", chat: true, embeddings: false, in_house: false, api_key: true },
].map((p) => ({ vision: false, prompt_caching: "none", tool_calls: true, ...p }))

const m = (id: string, label: string, tier: string) => ({ id, label, tier, vision: true, tool_calls: true })
const CATALOGUE = {
  openai: [m("gpt-5", "GPT-5", "best"), m("gpt-4o-mini", "GPT-4o mini", "fast")],
  bedrock: [m("amazon.nova-pro-v1:0", "Amazon Nova Pro", "balanced")],
  anthropic: [
    m("claude-opus-5-5", "Claude Opus 5.5", "best"),
    m("claude-sonnet-5-5", "Claude Sonnet 5.5", "balanced"),
  ],
}
const enabled = (provider: string, model: string, label: string, tier: string, isDefault = false) => ({
  provider,
  provider_label: PROVIDERS.find((p) => p.name === provider)?.label,
  model,
  label,
  tier,
  vision: true,
  tool_calls: true,
  default: isDefault,
})

const SETTINGS = {
  provider: "default",
  chat_provider: "default",
  embed_provider: "default",
  region: null,
  auth: "role",
  role_arn: null,
  external_id: "kb-0123456789abcdef0123456789abcdef",
  keys_saved: false,
  access_key_hint: null,
  api_keys_saved: {},
  api_key_hints: {},
  chat_models: [],
  default_model: null,
  catalogue: CATALOGUE,
  max_models: 24,
  chat_model: null,
  fast_model: null,
  embed_model: null,
  embed_dims: null,
  tested_at: null,
  updated_by: null,
  updated_at: 1,
  effective: {
    provider: "openai",
    embed_provider: "openai",
    chat_model: "gpt-4o-mini",
    fast_model: "gpt-4o-mini",
    embed_model: "text-embedding-3-small",
  },
  server: { provider: "openai", chat_provider: "openai", embed_provider: "openai", region: null },
  providers: PROVIDERS,
  provider_defaults: {
    openai: { chat_model: "gpt-4o-mini", fast_model: "gpt-4o-mini", embed_model: "text-embedding-3-small", embed_dims: null },
    bedrock: { chat_model: "amazon.nova-pro-v1:0", fast_model: "amazon.nova-lite-v1:0", embed_model: "amazon.titan-embed-text-v2:0", embed_dims: 1024 },
    local: { chat_model: null, fast_model: null, embed_model: "BAAI/bge-small-en-v1.5", embed_dims: 384 },
    anthropic: { chat_model: "claude-sonnet-5-5", fast_model: "claude-haiku-5-5", embed_model: null, embed_dims: null },
  },
  defaults: {
    chat_model: "amazon.nova-pro-v1:0",
    fast_model: "amazon.nova-lite-v1:0",
    embed_model: "amazon.titan-embed-text-v2:0",
    embed_dims: 1024,
  },
  embed_dimensions: { "amazon.titan-embed-text-v2:0": [1024, 512, 256] },
  index_embeddings: "text-embedding-3-small",
  trust_policy: { Statement: [{ Condition: { StringEquals: { "sts:ExternalId": "kb-0123" } } }] },
  permissions_policy: { Statement: [{ Action: ["bedrock:InvokeModel"] }] },
}
// Anthropic and OpenAI keys saved; Opus (the default) and GPT-5 enabled.
const TWO = {
  ...SETTINGS,
  chat_provider: "anthropic",
  chat_model: "claude-opus-5-5",
  api_keys_saved: { anthropic: true, openai: true },
  api_key_hints: { anthropic: "9xyz", openai: "4abc" },
  chat_models: [
    enabled("anthropic", "claude-opus-5-5", "Claude Opus 5.5", "best", true),
    enabled("openai", "gpt-5", "GPT-5", "best"),
  ],
  default_model: { provider: "anthropic", model: "claude-opus-5-5" },
}
const ROLE = "arn:aws:iam::123456789012:role/kb-bedrock"
const KEY = "sk-ant-test-0000000000000000000000"

function renderSettings() {
  render(
    withProviders(
      <MemoryRouter>
        <ModelProviderSettings />
      </MemoryRouter>,
    ),
  )
  return userEvent.setup()
}

async function choose(user: ReturnType<typeof userEvent.setup>, picker: string, option: string) {
  await user.click(await screen.findByRole("combobox", { name: picker }))
  await user.click(await screen.findByRole("option", { name: option }))
}

async function options(user: ReturnType<typeof userEvent.setup>, picker: string) {
  await user.click(await screen.findByRole("combobox", { name: picker }))
  const names = (await screen.findAllByRole("option")).map((o) => o.textContent)
  await user.keyboard("{Escape}")
  return names
}

async function addProvider(user: ReturnType<typeof userEvent.setup>, label: string) {
  await user.click(await screen.findByRole("button", { name: "Add a provider" }))
  await user.click(await screen.findByRole("menuitem", { name: label }))
}

describe("ModelProviderSettings", () => {
  it("offers the chat providers to add, and search from those with embeddings", async () => {
    mockApi({
      "GET /api/admin/assistant/models": {
        ...SETTINGS,
        // A provider the screen has never heard of shows up by itself.
        providers: [...PROVIDERS, { ...PROVIDERS[0], name: "acme", label: "Acme AI", embeddings: false }],
      },
    })
    const user = renderSettings()
    expect(await screen.findByText(/Answers use the server's model \(OpenAI, gpt-4o-mini\)/)).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Add a provider" }))
    expect((await screen.findAllByRole("menuitem")).map((i) => i.textContent)).toEqual([
      "OpenAI",
      "AWS Bedrock",
      "Anthropic",
      "Acme AI",
    ])
    await user.keyboard("{Escape}")
    expect(await options(user, "Search (embeddings)")).toEqual([
      "Server default (OpenAI)",
      "OpenAI",
      "AWS Bedrock",
      "Local (on this server)",
    ])
    expect(screen.getByText(/never falls back to another provider/)).toBeInTheDocument()
  })

  it("describes Local: nothing leaves the server, a 67 MB model downloaded once", async () => {
    mockApi({ "GET /api/admin/assistant/models": SETTINGS })
    const user = renderSettings()
    await choose(user, "Search (embeddings)", "Local (on this server)")
    expect(screen.getByText(/nothing leaves the server/)).toBeInTheDocument()
    expect(screen.getByText(/downloads a model of about 67 MB once/)).toBeInTheDocument()
    expect(screen.getByText("BAAI/bge-small-en-v1.5 · 384 numbers")).toBeInTheDocument()
    // Local takes no key and no AWS: nothing to fill in.
    expect(screen.queryByText("credentials")).not.toBeInTheDocument()
  })

  it("adds a provider, ticks its models, picks the default, and sends the key once", async () => {
    const puts: Record<string, unknown>[] = []
    mockApi({
      "GET /api/admin/assistant/models": SETTINGS,
      "PUT /api/admin/assistant/models": (body: Record<string, unknown>) => {
        puts.push(body)
        return {
          ...SETTINGS,
          chat_provider: "anthropic",
          api_keys_saved: { anthropic: true },
          api_key_hints: { anthropic: "0000" },
          chat_models: [
            enabled("anthropic", "claude-opus-5-5", "Claude Opus 5.5", "best", true),
            enabled("anthropic", "claude-sonnet-5-5", "Claude Sonnet 5.5", "balanced"),
          ],
          default_model: { provider: "anthropic", model: "claude-opus-5-5" },
          effective: { ...SETTINGS.effective, provider: "anthropic", chat_model: "claude-opus-5-5" },
          updated_at: 2,
        }
      },
    })
    const user = renderSettings()
    await addProvider(user, "Anthropic")
    // Its default model starts ticked; the rest of its list is there to tick.
    const models = screen.getByRole("list", { name: "Anthropic models" })
    expect(within(models).getByRole("checkbox", { name: /Claude Sonnet 5.5/ })).toBeChecked()
    await user.click(within(models).getByRole("checkbox", { name: /Claude Opus 5.5/ }))
    await choose(user, "Default model", "Claude Opus 5.5")
    const field = screen.getByLabelText("Anthropic API key")
    expect(field).toHaveAttribute("type", "password")
    await user.click(field)
    await user.paste(KEY)
    await user.click(screen.getByRole("button", { name: "Save" }))

    // Answers only: search stays where it is, so nothing asks about a rebuild.
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(screen.queryByText("Rebuild search?")).not.toBeInTheDocument()
    expect(puts[0]).toMatchObject({
      default_model: { provider: "anthropic", model: "claude-opus-5-5" },
      chat_models: [
        { provider: "anthropic", model: "claude-sonnet-5-5" },
        { provider: "anthropic", model: "claude-opus-5-5" },
      ],
      api_keys: { anthropic: KEY },
    })
    expect(await screen.findByText("Key saved")).toBeInTheDocument()
    expect(screen.getByText("••••0000")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Replace the Anthropic key" })).toBeInTheDocument()
    expect(screen.queryByLabelText("Anthropic API key")).not.toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain(KEY)
  })

  it("adds a custom model id, and the default follows what's enabled", async () => {
    const puts: Record<string, unknown>[] = []
    mockApi({
      "GET /api/admin/assistant/models": TWO,
      "PUT /api/admin/assistant/models": (body: Record<string, unknown>) => {
        puts.push(body)
        return { ...TWO, updated_at: 2 }
      },
    })
    const user = renderSettings()
    expect(await screen.findByText("••••9xyz")).toBeInTheDocument()
    await user.type(screen.getByRole("textbox", { name: "Another OpenAI model id" }), "gpt-9-preview{Enter}")
    const openai = screen.getByRole("list", { name: "OpenAI models" })
    expect(within(openai).getByRole("checkbox", { name: /gpt-9-preview/ })).toBeChecked()
    // Opus off: the default can't stay on it.
    const anthropic = screen.getByRole("list", { name: "Anthropic models" })
    await user.click(within(anthropic).getByRole("checkbox", { name: /Claude Opus 5.5/ }))
    await user.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(puts[0]).toMatchObject({
      default_model: { provider: "openai", model: "gpt-5" },
      chat_models: [
        { provider: "openai", model: "gpt-5" },
        { provider: "openai", model: "gpt-9-preview" },
      ],
    })
    expect(puts[0]).not.toHaveProperty("api_keys")
  })

  it("removes a saved provider with its key and models, keeping the rest", async () => {
    const puts: unknown[] = []
    mockApi({
      "GET /api/admin/assistant/models": TWO,
      "PUT /api/admin/assistant/models": (body: unknown) => {
        puts.push(body)
        return { ...SETTINGS, updated_at: 2 }
      },
    })
    const user = renderSettings()
    await user.click(await screen.findByRole("button", { name: "Remove Anthropic" }))
    const dialog = await screen.findByRole("alertdialog")
    expect(within(dialog).getByText(/its models leave the chat's model menu/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: "Remove" }))
    await waitFor(() =>
      expect(puts).toEqual([
        {
          api_keys: { anthropic: null },
          chat_models: [{ provider: "openai", model: "gpt-5" }],
          default_model: { provider: "openai", model: "gpt-5" },
        },
      ]),
    )
  })

  it("asks before a search change rebuilds the index, then links to its progress", async () => {
    const puts: Record<string, unknown>[] = []
    mockApi({
      "GET /api/admin/assistant/models": SETTINGS,
      "GET /api/index/status": { status: "indexing", step: 1, total: 4, message: "Embedding" },
      "PUT /api/admin/assistant/models": (body: Record<string, unknown>) => {
        puts.push(body)
        return { ...SETTINGS, embed_provider: "local", updated_at: 2, reindex: true }
      },
    })
    const user = renderSettings()
    await choose(user, "Search (embeddings)", "Local (on this server)")
    await user.click(screen.getByRole("button", { name: "Save" }))

    // Asked before anything is sent.
    const dialog = await screen.findByRole("alertdialog")
    expect(puts).toHaveLength(0)
    expect(within(dialog).getByText(/current index keeps answering until the new one is ready/)).toBeInTheDocument()
    expect(within(dialog).getByText(/by keyword search only/)).toBeInTheDocument()
    expect(within(dialog).getByText(/costs no embedding calls/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: "Save and rebuild" }))
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(puts[0]).toMatchObject({ embed_provider: "local", chat_provider: "default", confirm_reindex: true })
    expect(await screen.findByText("Search is being rebuilt")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "See indexing progress" })).toHaveAttribute(
      "href",
      "/documents?jobs=1",
    )
  })

  it("still asks when the server says a save would rebuild (409)", async () => {
    const puts: Record<string, unknown>[] = []
    mockApi({
      "GET /api/admin/assistant/models": {
        ...SETTINGS,
        chat_provider: "bedrock",
        embed_provider: "bedrock",
        region: "eu-west-1",
        chat_models: [enabled("bedrock", "amazon.nova-pro-v1:0", "Amazon Nova Pro", "balanced", true)],
        default_model: { provider: "bedrock", model: "amazon.nova-pro-v1:0" },
        index_embeddings: "bedrock:amazon.titan-embed-text-v2:0:1024",
      },
      "PUT /api/admin/assistant/models": (body: Record<string, unknown>) => {
        puts.push(body)
        if (!body.confirm_reindex) return json({ detail: "This changes the embedding model." }, 409)
        return { ...SETTINGS, updated_at: 2, reindex: true }
      },
    })
    const user = renderSettings()
    await user.click(await screen.findByLabelText("Role ARN"))
    await user.paste(ROLE)
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(await screen.findByText("Rebuild search?")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Save and rebuild" }))
    await waitFor(() => expect(puts).toHaveLength(2))
    expect(puts[1]).toMatchObject({ role_arn: ROLE, region: "eu-west-1", confirm_reindex: true })
  })

  it("tests by listing each provider's models, and fails a provider that doesn't list an enabled one", async () => {
    const tests: unknown[] = []
    mockApi({
      "GET /api/admin/assistant/models": TWO,
      "POST /api/admin/assistant/models/test": (body: unknown) => {
        tests.push(body)
        return {
          ok: false,
          providers: [
            { provider: "anthropic", label: "Anthropic", ok: true, models: 12, missing: [] },
            {
              provider: "openai",
              label: "OpenAI",
              ok: false,
              models: 80,
              missing: ["gpt-5"],
              error: "It lists 80 models, but not gpt-5: check the model id and that this key can use it.",
            },
            {
              provider: "bedrock",
              label: "AWS Bedrock",
              ok: true,
              models: null,
              missing: [],
              checked: ["amazon.nova-pro-v1:0"],
            },
          ],
        }
      },
    })
    const user = renderSettings()
    await user.click(await screen.findByRole("button", { name: /Test connection/ }))
    const status = await screen.findByRole("status")
    expect(within(status).getByText("Anthropic: connected")).toBeInTheDocument()
    expect(within(status).getByText(/lists 12 models\./)).toBeInTheDocument()
    expect(within(status).getByText("OpenAI: model not found")).toBeInTheDocument()
    expect(within(status).getByText(/lists 80 models, but not gpt-5/)).toBeInTheDocument()
    expect(within(status).getByText("It answered with amazon.nova-pro-v1:0.")).toBeInTheDocument()
    const dots = status.querySelectorAll('[data-slot="status-dot"]')
    expect([...dots].map((d) => d.getAttribute("data-status"))).toEqual(["live", "error", "live"])
    expect(screen.getByText(/Anthropic's models, for free/)).toBeInTheDocument()
    expect(tests[0]).toMatchObject({
      default_model: { provider: "anthropic", model: "claude-opus-5-5" },
      chat_models: [
        { provider: "anthropic", model: "claude-opus-5-5" },
        { provider: "openai", model: "gpt-5" },
      ],
    })
  })

  it("shows a provider's reason when its test fails, and only that", async () => {
    mockApi({
      "GET /api/admin/assistant/models": TWO,
      "POST /api/admin/assistant/models/test": {
        ok: false,
        providers: [
          {
            provider: "anthropic",
            label: "Anthropic",
            ok: false,
            models: null,
            missing: [],
            error: "AuthenticationError (authentication_error): invalid x-api-key",
          },
        ],
      },
    })
    const user = renderSettings()
    await user.click(await screen.findByRole("button", { name: /Test connection/ }))
    const status = await screen.findByRole("status")
    expect(within(status).getByText("Anthropic: couldn't connect")).toBeInTheDocument()
    expect(within(status).getByText(/invalid x-api-key/)).toBeInTheDocument()
    expect(status.querySelector('[data-slot="status-dot"]')).toHaveAttribute("data-status", "error")
  })

  it("shows a refused test's reason (400) in a notice", async () => {
    mockApi({
      "GET /api/admin/assistant/models": SETTINGS,
      "POST /api/admin/assistant/models/test": json({ detail: "Paste your Anthropic API key." }, 400),
    })
    const user = renderSettings()
    await addProvider(user, "Anthropic")
    await user.click(screen.getByRole("button", { name: /Test connection/ }))
    const alert = await screen.findByRole("alert")
    expect(within(alert).getByText("Couldn't connect")).toBeInTheDocument()
    expect(within(alert).getByText("Paste your Anthropic API key.")).toBeInTheDocument()
  })
})
