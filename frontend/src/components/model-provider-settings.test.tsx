import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { ModelProviderSettings } from "@/components/model-provider-settings"
import { json, mockApi, withProviders } from "@/test/render"

const SETTINGS = {
  provider: "bedrock",
  region: "eu-west-1",
  auth: "role",
  role_arn: null,
  external_id: "kb-0123456789abcdef0123456789abcdef",
  keys_saved: false,
  access_key_hint: null,
  chat_model: null,
  fast_model: null,
  embed_model: null,
  embed_dims: null,
  tested_at: null,
  updated_by: null,
  updated_at: 1,
  effective: {
    provider: "openai",
    chat_model: "gpt-4o-mini",
    fast_model: "gpt-4o-mini",
    embed_model: "text-embedding-3-small",
  },
  server: { provider: "openai", region: null },
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

describe("ModelProviderSettings", () => {
  it("tests the role as typed, then confirms the index rebuild before saving", async () => {
    const puts: unknown[] = []
    mockApi({
      "GET /api/admin/assistant/models": SETTINGS,
      "POST /api/admin/assistant/models/test": (body: unknown) => {
        expect(body).toMatchObject({ provider: "bedrock", region: "eu-west-1", role_arn: ROLE })
        return {
          ok: true,
          chat_model: "amazon.nova-pro-v1:0",
          reply: "OK",
          tokens: 16,
          embed_model: "amazon.titan-embed-text-v2:0",
          dimensions: 1024,
        }
      },
      "PUT /api/admin/assistant/models": (body: unknown) => {
        puts.push(body)
        if (!(body as { confirm_reindex?: boolean }).confirm_reindex)
          return json({ detail: "This changes the embedding model." }, 409)
        return { ...SETTINGS, role_arn: ROLE, updated_at: 2, reindex: true }
      },
    })
    render(withProviders(<ModelProviderSettings />))
    const user = userEvent.setup()

    // The ExternalId generated for the workspace, to put in the role's trust policy.
    expect(await screen.findByText(SETTINGS.external_id)).toBeInTheDocument()
    await user.click(screen.getByLabelText("Role ARN"))
    await user.paste(ROLE)
    await user.click(screen.getByRole("button", { name: /Test connection/ }))
    expect(await screen.findByText("Connected")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(await screen.findByText("Rebuild the search index?")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Save and rebuild" }))
    await waitFor(() => expect(puts).toHaveLength(2))
    expect(puts[1]).toMatchObject({ role_arn: ROLE, confirm_reindex: true })
  })
})
