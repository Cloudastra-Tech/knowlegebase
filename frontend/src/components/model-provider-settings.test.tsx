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

describe("ModelProviderSettings", () => {
  it("builds each picker from the providers the server lists, by what they offer", async () => {
    mockApi({
      "GET /api/admin/assistant/models": {
        ...SETTINGS,
        // A provider the screen has never heard of shows up by itself.
        providers: [...PROVIDERS, { ...PROVIDERS[0], name: "acme", label: "Acme AI", embeddings: false }],
      },
    })
    const user = renderSettings()
    expect(await options(user, "Answers (chat)")).toEqual([
      "Server default (OpenAI)",
      "OpenAI",
      "AWS Bedrock",
      "Anthropic",
      "Acme AI",
    ])
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

  it("sends a typed key once and then only says it's saved, never showing it", async () => {
    const puts: Record<string, unknown>[] = []
    mockApi({
      "GET /api/admin/assistant/models": SETTINGS,
      "PUT /api/admin/assistant/models": (body: Record<string, unknown>) => {
        puts.push(body)
        return {
          ...SETTINGS,
          chat_provider: "anthropic",
          api_keys_saved: { anthropic: true },
          effective: { ...SETTINGS.effective, provider: "anthropic", chat_model: "claude-sonnet-5-5" },
          updated_at: 2,
        }
      },
    })
    const user = renderSettings()
    await choose(user, "Answers (chat)", "Anthropic")
    expect(screen.getByText("claude-sonnet-5-5 · fast claude-haiku-5-5")).toBeInTheDocument()
    const field = screen.getByLabelText("Anthropic API key")
    expect(field).toHaveAttribute("type", "password")
    await user.click(field)
    await user.paste(KEY)
    await user.click(screen.getByRole("button", { name: "Save" }))

    // Chat only: search stays where it is, so nothing asks about a rebuild.
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(screen.queryByText("Rebuild search?")).not.toBeInTheDocument()
    expect(puts[0]).toMatchObject({ chat_provider: "anthropic", api_keys: { anthropic: KEY } })
    expect(await screen.findByText("Key saved")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Replace the Anthropic key" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Remove the Anthropic key" })).toBeInTheDocument()
    expect(screen.queryByLabelText("Anthropic API key")).not.toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain(KEY)
    expect(document.body.innerHTML).not.toContain(KEY.slice(-4))
  })

  it("removes a saved key by moving its side back to the server default", async () => {
    const puts: unknown[] = []
    mockApi({
      "GET /api/admin/assistant/models": { ...SETTINGS, chat_provider: "anthropic", api_keys_saved: { anthropic: true } },
      "PUT /api/admin/assistant/models": (body: unknown) => {
        puts.push(body)
        return { ...SETTINGS, updated_at: 2 }
      },
    })
    const user = renderSettings()
    await user.click(await screen.findByRole("button", { name: "Remove the Anthropic key" }))
    const dialog = await screen.findByRole("alertdialog")
    expect(within(dialog).getByText(/removing it puts answers back on the server default/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: "Remove key" }))
    await waitFor(() =>
      expect(puts).toEqual([{ api_keys: { anthropic: null }, chat_provider: "default" }]),
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
    expect(puts[0]).toMatchObject({ embed_provider: "local", confirm_reindex: true })
    expect(await screen.findByText("Search is being rebuilt")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "See indexing progress" })).toHaveAttribute(
      "href",
      "/documents?jobs=1",
    )
  })

  it("still asks when the server says a save would rebuild (409)", async () => {
    const puts: Record<string, unknown>[] = []
    mockApi({
      "GET /api/admin/assistant/models": { ...SETTINGS, chat_provider: "bedrock", embed_provider: "bedrock", region: "eu-west-1", index_embeddings: "bedrock:amazon.titan-embed-text-v2:0:1024" },
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

  it("tests the chosen pair and shows success with a status dot", async () => {
    const tests: unknown[] = []
    mockApi({
      "GET /api/admin/assistant/models": SETTINGS,
      "POST /api/admin/assistant/models/test": (body: unknown) => {
        tests.push(body)
        return {
          ok: true,
          chat_model: "claude-sonnet-5-5",
          reply: "OK",
          tokens: 16,
          embed_model: "BAAI/bge-small-en-v1.5",
          dimensions: 384,
        }
      },
    })
    const user = renderSettings()
    await choose(user, "Answers (chat)", "Anthropic")
    await choose(user, "Search (embeddings)", "Local (on this server)")
    await user.click(screen.getByRole("button", { name: /Test connection/ }))
    const status = await screen.findByRole("status")
    expect(within(status).getByText("Connected")).toBeInTheDocument()
    expect(status.querySelector('[data-slot="status-dot"]')).toHaveAttribute("data-status", "live")
    expect(tests).toHaveLength(1)
    expect(tests[0]).toMatchObject({ chat_provider: "anthropic", embed_provider: "local" })
  })

  it("shows the server's reason when the test fails, and only that", async () => {
    mockApi({
      "GET /api/admin/assistant/models": SETTINGS,
      "POST /api/admin/assistant/models/test": {
        ok: false,
        error: "AnthropicAuthenticationError: invalid x-api-key",
      },
    })
    const user = renderSettings()
    await user.click(await screen.findByRole("button", { name: /Test connection/ }))
    const alert = await screen.findByRole("alert")
    expect(within(alert).getByText("Couldn't connect")).toBeInTheDocument()
    expect(within(alert).getByText("AnthropicAuthenticationError: invalid x-api-key")).toBeInTheDocument()
  })

  it("shows a refused test's reason (400) in the same notice", async () => {
    mockApi({
      "GET /api/admin/assistant/models": SETTINGS,
      "POST /api/admin/assistant/models/test": json({ detail: "Paste your Anthropic API key." }, 400),
    })
    const user = renderSettings()
    await choose(user, "Answers (chat)", "Anthropic")
    await user.click(screen.getByRole("button", { name: /Test connection/ }))
    expect(await screen.findByText("Paste your Anthropic API key.")).toBeInTheDocument()
  })
})
