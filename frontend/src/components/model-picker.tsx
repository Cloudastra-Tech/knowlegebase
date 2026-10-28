import { Check, ChevronDown } from "lucide-react"

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
import type { ChatModel, ModelRef } from "@/lib/api"
import { sameModel, TIER_LABEL } from "@/lib/chat-models"

// The models grouped by provider, in the order the server lists them (the default's
// provider first).
function byProvider(models: ChatModel[]) {
  const groups = new Map<string, { label: string; models: ChatModel[] }>()
  for (const m of models) {
    const group = groups.get(m.provider) ?? { label: m.provider_label, models: [] }
    group.models.push(m)
    groups.set(m.provider, group)
  }
  return [...groups.entries()]
}

// The composer's model chip (beside the space chip): which of the workspace's enabled
// answer models writes the next answer. Hidden when there's only one to choose from.
// The menu is portalled (DropdownMenuContent), so the composer's glass doesn't clip
// or blur it.
export function ModelPicker({
  models,
  value,
  onChange,
  disabled,
}: {
  models: ChatModel[]
  value: ModelRef | null
  onChange: (model: ModelRef) => void
  disabled?: boolean
}) {
  if (models.length < 2) return null
  const current = models.find((m) => sameModel(m, value)) ?? models[0]
  const groups = byProvider(models)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          aria-label={`Model: ${current.label}. Change model`}
          className="h-7 max-w-44 gap-1 rounded-full px-2.5 text-xs font-normal text-muted-foreground"
        >
          <span className="truncate">{current.label}</span>
          <ChevronDown className="size-3 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        {groups.map(([provider, group], i) => (
          <DropdownMenuGroup key={provider}>
            {i > 0 && <DropdownMenuSeparator />}
            {groups.length > 1 && (
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                {group.label}
              </DropdownMenuLabel>
            )}
            {group.models.map((m) => (
              <DropdownMenuItem
                key={`${m.provider}:${m.model}`}
                onSelect={() => onChange({ provider: m.provider, model: m.model })}
              >
                <span className="min-w-0 flex-1 truncate">{m.label}</span>
                <span className="text-xs text-muted-foreground">{TIER_LABEL[m.tier]}</span>
                <Check className={sameModel(m, current) ? "" : "invisible"} />
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
