import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowDown, ArrowUp, Ban, MoreHorizontal, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { Panel } from "@/components/glass/panel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { assistant, type DocRule, type KnowledgeItem, type RuleAction, type RuleMatch } from "@/lib/api"

// Source rules (platform.md PL-28), HR and admins: boost or demote a document, a
// folder or everything from one source in the search, or block it (its chunks leave
// the index). The rules a search applied are in an answer's technical details.

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")
const WEIGHTS: Record<"boost" | "demote", number[]> = {
  boost: [1.25, 1.5, 2, 3],
  demote: [0.75, 0.5, 0.25, 0.1],
}
const PROVIDERS: Record<string, string> = { uploads: "uploaded files" }

function ruleText(rule: Pick<DocRule, "action" | "weight">) {
  if (rule.action === "block") return "Blocked"
  return `${rule.action === "boost" ? "Boosted" : "Demoted"} ×${rule.weight}`
}

function matchText(rule: Pick<DocRule, "match_type" | "match_value">, items: KnowledgeItem[]) {
  if (rule.match_type === "doc_key") {
    return items.find((i) => i.doc_key === rule.match_value)?.title ?? rule.match_value
  }
  if (rule.match_type === "folder") return `Folder ${rule.match_value}`
  const source = items.find((i) => i.provider === rule.match_value)?.source
  return `Everything from ${source ?? PROVIDERS[rule.match_value] ?? rule.match_value}`
}

function useRuleChange() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (change: {
      add?: { match_type: RuleMatch; match_value: string; action: RuleAction }
      update?: { id: number; action: RuleAction; weight?: number }
      remove?: number
    }) => {
      if (change.add) return assistant.addDocRule(change.add)
      if (change.update) return assistant.changeDocRule(change.update.id, change.update.action, change.update.weight)
      return assistant.deleteDocRule(change.remove!)
    },
    onSuccess: (_, change) => {
      const action = change.add?.action ?? change.update?.action
      toast.success(
        change.remove !== undefined
          ? "Rule removed."
          : action === "block"
            ? "Blocked. Its passages leave the search index in a moment."
            : "Rule saved. The next question uses it.",
      )
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] })
      void queryClient.invalidateQueries({ queryKey: ["index-status"] })
    },
    onError: (err) => toast.error(errorText(err)),
  })
}

export function RuleBadge({ rule }: { rule: NonNullable<KnowledgeItem["rule"]> }) {
  return (
    <Badge
      variant="outline"
      className={`shrink-0 font-normal ${rule.action === "block" ? "text-destructive" : "text-muted-foreground"}`}
      title={`${ruleText(rule)}: ${rule.match_type === "doc_key" ? "this document" : rule.match_type} ${rule.match_type === "doc_key" ? "" : rule.match_value}`}
    >
      {ruleText(rule)}
    </Badge>
  )
}

// The row's "…" menu: a rule for this document, its folder, or everything from its source.
export function RuleActions({ item, rules }: { item: KnowledgeItem; rules: DocRule[] }) {
  const change = useRuleChange()
  const targets: { match_type: RuleMatch; match_value: string; label: string }[] = [
    { match_type: "doc_key", match_value: item.doc_key, label: "This document" },
    ...(item.folder
      ? [{ match_type: "folder" as const, match_value: item.folder, label: `Folder ${item.folder}` }]
      : []),
    {
      match_type: "provider",
      match_value: item.provider,
      label: `Everything from ${item.source === "Uploaded" ? "uploads" : item.source}`,
    },
  ]
  const existing = (t: (typeof targets)[number]) =>
    rules.find((r) => r.match_type === t.match_type && r.match_value === t.match_value)
  const apply = (t: (typeof targets)[number], action: RuleAction) => {
    const rule = existing(t)
    if (rule) change.mutate({ update: { id: rule.id, action } })
    else change.mutate({ add: { match_type: t.match_type, match_value: t.match_value, action } })
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`Source rules for ${item.title}`} disabled={change.isPending}>
          <MoreHorizontal />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        {targets.map((t, i) => {
          const rule = existing(t)
          return (
            <DropdownMenuGroup key={t.match_type}>
              {i > 0 && <DropdownMenuSeparator />}
              <DropdownMenuLabel className="truncate text-xs font-normal text-muted-foreground">
                {t.label}
                {rule ? ` · ${ruleText(rule)}` : ""}
              </DropdownMenuLabel>
              {rule?.action !== "boost" && (
                <DropdownMenuItem onSelect={() => apply(t, "boost")}>
                  <ArrowUp /> Boost in answers
                </DropdownMenuItem>
              )}
              {rule?.action !== "demote" && (
                <DropdownMenuItem onSelect={() => apply(t, "demote")}>
                  <ArrowDown /> Demote in answers
                </DropdownMenuItem>
              )}
              {rule?.action !== "block" && (
                <DropdownMenuItem variant="destructive" onSelect={() => apply(t, "block")}>
                  <Ban /> Block from answers
                </DropdownMenuItem>
              )}
              {rule && (
                <DropdownMenuItem onSelect={() => change.mutate({ remove: rule.id })}>
                  <Trash2 /> Remove rule
                </DropdownMenuItem>
              )}
            </DropdownMenuGroup>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// Every rule, with its weight to change or the rule to remove.
export function RulesPanel({ rules, items }: { rules: DocRule[]; items: KnowledgeItem[] }) {
  const change = useRuleChange()
  if (!rules.length) return null
  return (
    <Panel title="Source rules" className="mt-6">
      <p className="-mt-3 text-sm text-muted-foreground">
        Boosted sources rank higher in answers, demoted ones lower; blocked ones aren't searched
        at all. Rules apply to the next question; admins see the ones used in an answer's
        technical details.
      </p>
      <ul className="mt-4 divide-y divide-border/70 rounded-xl border border-border/70 bg-card">
        {rules.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5 text-sm">
            <span className="min-w-0 flex-1 truncate">{matchText(r, items)}</span>
            {r.action === "block" ? (
              <Badge variant="outline" className="font-normal text-destructive">
                Blocked
              </Badge>
            ) : (
              <Select
                value={`${r.action}:${r.weight}`}
                onValueChange={(v) => {
                  const [action, weight] = v.split(":")
                  change.mutate({ update: { id: r.id, action: action as RuleAction, weight: Number(weight) } })
                }}
              >
                <SelectTrigger size="sm" className="w-36" aria-label={`Weight for ${matchText(r, items)}`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(["boost", "demote"] as const).flatMap((action) =>
                    [...new Set([...WEIGHTS[action], ...(r.action === action ? [r.weight] : [])])].map((w) => (
                      <SelectItem key={`${action}:${w}`} value={`${action}:${w}`}>
                        {action === "boost" ? "Boost" : "Demote"} ×{w}
                      </SelectItem>
                    )),
                  )}
                </SelectContent>
              </Select>
            )}
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`Remove the rule for ${matchText(r, items)}`}
              disabled={change.isPending}
              onClick={() => change.mutate({ remove: r.id })}
            >
              <Trash2 />
            </Button>
          </li>
        ))}
      </ul>
    </Panel>
  )
}
