import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { toast } from "sonner"

import { KeyCreatedDialog, KeyRow, McpSnippets, NewKeyDialog } from "@/components/api-keys-view"
import { AsciiDivider } from "@/components/ascii/ascii-divider"
import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { Panel } from "@/components/glass/panel"
import { CopyField } from "@/components/sign-in-provider-dialog"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { apiKeys, type ApiKey, type ApiSettings, type McpTool } from "@/lib/api"
import { queries } from "@/lib/queries"

// The Access screen's "API & MCP" tab (admin): the public API and the MCP server on
// or off, which MCP tools agents get, service keys, and every key in the workspace.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const TOOLS: Record<McpTool, { label: string; description: string }> = {
  search_knowledge: {
    label: "search_knowledge",
    description: "Passages with their titles and links, no answer written.",
  },
  ask: { label: "ask", description: "A cited answer, with the chat's guardrails." },
  list_sources: { label: "list_sources", description: "What the assistant can search." },
  read_document: {
    label: "read_document",
    description: "A whole document the person may see. Agents can copy it out in full.",
  },
}

function Section({
  title,
  description,
  action,
  children,
}: {
  title: string
  description: React.ReactNode
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <Panel title={title} description={description} actions={action} className="min-w-0">
      {children}
    </Panel>
  )
}

export function ApiAccessView() {
  const queryClient = useQueryClient()
  const data = useQuery(queries.adminApi()).data
  const [creating, setCreating] = useState(false)
  const [made, setMade] = useState<(ApiKey & { key: string }) | null>(null)

  const reload = () => void queryClient.invalidateQueries({ queryKey: ["api-keys"] })

  async function save(change: Partial<Pick<ApiSettings, "api_enabled" | "mcp_enabled" | "mcp_tools">>) {
    try {
      await apiKeys.setApiSettings(change)
      toast.success("Saved.")
      reload()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function revoke(key: ApiKey) {
    try {
      await apiKeys.revokeApiKey(key.id)
      toast.success(`Revoked “${key.name}”.`)
      reload()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  if (!data) return <Skeleton className="h-64 rounded-2xl" />
  const { settings } = data
  const service = data.keys.filter((k) => k.kind === "service")
  const personal = data.keys.filter((k) => k.kind === "personal")
  const activeService = service.filter((k) => k.status === "active").length
  const toggleTool = (tool: McpTool, on: boolean) =>
    save({
      mcp_tools: on ? [...settings.mcp_tools, tool] : settings.mcp_tools.filter((t) => t !== tool),
    })

  return (
    <div className="grid grid-cols-1 gap-6">
      <Section
        title="REST API"
        description="Scripts, intranet pages and helpdesks ask and search with an API key."
      >
        <FieldGroup className="gap-4">
          <Field orientation="horizontal">
            <FieldContent>
              <FieldLabel htmlFor="api-enabled">Public API (v1)</FieldLabel>
              <FieldDescription>
                Off: every key is refused at /api/v1, until it's on again.
              </FieldDescription>
            </FieldContent>
            <Switch
              id="api-enabled"
              checked={settings.api_enabled}
              onCheckedChange={(on) => save({ api_enabled: on })}
            />
          </Field>
          <CopyField label="Base URL" value={data.api_url} />
          <CopyField label="OpenAPI description" value={data.openapi_url} />
        </FieldGroup>
      </Section>

      <Section
        title="MCP server"
        description="Claude, Cursor and other agents query the knowledge base as the person whose key they hold."
      >
        <FieldGroup className="gap-4">
          <Field orientation="horizontal">
            <FieldContent>
              <FieldLabel htmlFor="mcp-enabled">MCP server</FieldLabel>
              <FieldDescription>
                Personal keys only: answers follow the person's role, never a shared key's.
              </FieldDescription>
            </FieldContent>
            <Switch
              id="mcp-enabled"
              checked={settings.mcp_enabled}
              onCheckedChange={(on) => save({ mcp_enabled: on })}
            />
          </Field>
          <div className="grid grid-cols-1 gap-3">
            <AsciiDivider as="h4" label="Tools agents get" />
            {data.tools.map((tool) => (
              <Field key={tool} orientation="horizontal" data-disabled={!settings.mcp_enabled || undefined}>
                <Checkbox
                  id={`tool-${tool}`}
                  checked={settings.mcp_tools.includes(tool)}
                  disabled={!settings.mcp_enabled}
                  onCheckedChange={(on) => toggleTool(tool, on === true)}
                />
                <FieldContent>
                  <FieldLabel htmlFor={`tool-${tool}`} className="font-mono text-xs font-normal">
                    {TOOLS[tool].label}
                  </FieldLabel>
                  <FieldDescription>{TOOLS[tool].description}</FieldDescription>
                </FieldContent>
              </Field>
            ))}
          </div>
          <CopyField label="MCP server URL" value={data.mcp_url} />
          <McpSnippets mcpUrl={data.mcp_url} />
        </FieldGroup>
      </Section>

      <Section
        title="Service keys"
        description="For a page or tool many people use. A service key reads as a public user only, never HR."
        action={
          <Button
            size="sm"
            onClick={() => setCreating(true)}
            disabled={!settings.api_enabled || activeService >= data.max_service_keys}
          >
            <Plus data-icon="inline-start" /> New service key
          </Button>
        }
      >
        {service.length ? (
          <div className="divide-y rounded-xl border bg-card">
            {service.map((k) => (
              <KeyRow key={k.id} apiKey={k} showOwner onRevoke={() => revoke(k)} />
            ))}
          </div>
        ) : (
          <AsciiEmptyState art="key" size="sm" headingLevel={4} title="No service keys." />
        )}
      </Section>

      <Section
        title="Members' keys"
        description="Personal keys act as their owner, with their role at each call. Revoke one if a laptop is lost or someone leaves."
      >
        {personal.length ? (
          <div className="divide-y rounded-xl border bg-card">
            {personal.map((k) => (
              <KeyRow key={k.id} apiKey={k} showOwner onRevoke={() => revoke(k)} />
            ))}
          </div>
        ) : (
          <AsciiEmptyState
            art="key"
            size="sm"
            headingLevel={4}
            title="Nobody has made a key yet."
            description="Everyone can, under API keys in their account menu."
          />
        )}
      </Section>

      <NewKeyDialog
        open={creating}
        onOpenChange={setCreating}
        scopes={data.service_scopes}
        defaultScopes={["ask", "search"]}
        expiryDays={data.expiry_days}
        defaultDays={data.default_days}
        service
        create={apiKeys.createServiceKey}
        onCreated={(k) => {
          setMade(k)
          reload()
        }}
      />
      <KeyCreatedDialog made={made} onClose={() => setMade(null)} />
    </div>
  )
}
