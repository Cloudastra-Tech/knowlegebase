import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ChevronDown, Loader2, Lock, PanelRight, Unplug } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Skeleton } from "@/components/ui/skeleton"
import { apiKeys } from "@/lib/api"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

const HOW_TO_ASK = [
  ["Open the side panel", "from the toolbar, on any tab"],
  ["Select text, right-click", "Ask Knowledge about the selection"],
  ["Use the selection or not", "your choice, each question"],
] as const

const INSTALL = [
  "Get the extension folder from your admin (extension/dist, built from this app's repository).",
  "In Chrome open chrome://extensions, or edge://extensions in Edge, and turn on Developer mode.",
  "Choose Load unpacked and pick the folder. Pin it, open its side panel, and enter this site's address.",
  "Click Connect: this app asks you to confirm, then the extension can ask as you.",
]

// The browser extension on the Integrations screen: what it does, how to install
// it, and the extensions connected to your account (each is one of your API keys,
// asking as you with the ask scope only).
export function ExtensionCard() {
  const queryClient = useQueryClient()
  const status = useQuery(queries.extension())
  const [removing, setRemoving] = useState<number | null>(null)
  const data = status.data

  async function disconnect(id: number) {
    setRemoving(id)
    try {
      await apiKeys.revokeMyApiKey(id)
      toast.success("Disconnected. That extension can't ask as you any more.")
      await queryClient.invalidateQueries({ queryKey: ["extension"] })
      await queryClient.invalidateQueries({ queryKey: ["api-keys"] })
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setRemoving(null)
    }
  }

  return (
    <div className="flex min-w-0 flex-col rounded-2xl border border-border/70 bg-background p-5">
      <div className="flex items-start gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-muted">
          <PanelRight className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-medium">Browser extension</p>
          <p className="text-sm text-muted-foreground">
            Ask from any tab in Chrome or Edge, with cited answers.
          </p>
        </div>
        {data &&
          (data.connected.length > 0 ? (
            <Badge className="gap-1.5 bg-emerald-600/10 text-emerald-700 hover:bg-emerald-600/10 dark:text-emerald-400">
              <span className="size-1.5 rounded-full bg-emerald-500" /> Connected
            </Badge>
          ) : (
            <Badge variant="outline" className="font-normal text-muted-foreground">
              {data.configured ? "Not connected" : "Not set up"}
            </Badge>
          ))}
      </div>

      {status.isPending ? (
        <Skeleton className="mt-4 h-24 rounded-xl" />
      ) : status.isError ? (
        <p className="mt-4 text-sm text-destructive">{errorText(status.error)}</p>
      ) : (
        <div className="mt-4 flex flex-1 flex-col">
          <ul className="grid gap-1 text-sm">
            {HOW_TO_ASK.map(([how, what]) => (
              <li key={how} className="min-w-0">
                <span className="font-medium">{how}</span>{" "}
                <span className="text-muted-foreground">· {what}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 flex items-start gap-1.5 text-xs text-muted-foreground">
            <Lock className="mt-px size-3.5 shrink-0" />
            It never reads the page. Text you select goes along only when you choose, and it asks
            as you, with your role, through a key you can revoke here or under API keys.
          </p>

          {!data!.configured && (
            <p className="mt-3 text-sm text-muted-foreground">
              Your server doesn't accept the extension yet: an admin sets EXTENSION_ORIGINS to the
              extension's address (deploy.md).
            </p>
          )}
          {data!.configured && !data!.api_enabled && (
            <p className="mt-3 text-sm text-muted-foreground">
              Your workspace's admins have turned the API off, and the extension asks through it.
            </p>
          )}

          {data!.connected.length > 0 && (
            <ul className="mt-4 grid gap-2">
              {data!.connected.map((k) => (
                <li
                  key={k.id}
                  className="flex min-w-0 items-center gap-3 rounded-xl border border-border/70 p-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">Connected {timeAgo(k.created_at)}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {k.last_used_at ? `Last asked ${timeAgo(k.last_used_at)}` : "Not used yet"}
                      {k.expires_at && ` · until ${new Date(k.expires_at * 1000).toLocaleDateString()}`}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => disconnect(k.id)}
                    disabled={removing !== null}
                  >
                    {removing === k.id ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Unplug className="size-4" />
                    )}
                    Disconnect
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <Collapsible className="mt-4">
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="group -ml-2 gap-1.5">
                How to install
                <ChevronDown className="size-4 transition-transform group-data-[state=open]:rotate-180" />
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <ol className="mt-2 grid list-decimal gap-1.5 pl-5 text-sm text-muted-foreground">
                {INSTALL.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            </CollapsibleContent>
          </Collapsible>
        </div>
      )}
    </div>
  )
}
