import { useTheme } from "next-themes"

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { cn } from "@/lib/utils"

// /__kit, in development only (routes.tsx): the design system's pieces in one place, in
// either theme. For now the tokens and the glass tiers; the ASCII and glass components
// join it as they're built (DESIGN.md).

const SWATCHES = [
  "background",
  "card",
  "popover",
  "muted",
  "accent",
  "foreground",
  "muted-foreground",
  "border",
  "signal",
  "success",
  "warning",
  "destructive",
  "chart-1",
  "chart-2",
  "chart-3",
  "chart-4",
  "chart-5",
]

const TIERS = [
  { name: "glass", note: "chrome: header, chat sidebar, composer" },
  { name: "glass-strong", note: "floating: menus, dialogs, toasts" },
  { name: "glass-subtle", note: "panels inside a screen, no blur" },
]

const FIELD = Array.from({ length: 14 }, (_, r) =>
  Array.from({ length: 120 }, (_, c) => " ..··::--~~"[Math.round(5 + 5 * Math.sin(c * 0.21 + r * 0.6))]).join(""),
).join("\n")

export function KitGallery() {
  const { theme, setTheme } = useTheme()
  return (
    <main className="mx-auto max-w-5xl space-y-10 px-4 py-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <p className="font-mono text-xs text-muted-foreground">~/__kit</p>
          <h2 className="text-2xl font-semibold tracking-tight">Signal Glass kit</h2>
        </div>
        <ToggleGroup
          type="single"
          variant="outline"
          value={theme}
          onValueChange={(v) => v && setTheme(v)}
          aria-label="Theme"
        >
          <ToggleGroupItem value="dark">Dark</ToggleGroupItem>
          <ToggleGroupItem value="light">Light</ToggleGroupItem>
          <ToggleGroupItem value="system">System</ToggleGroupItem>
        </ToggleGroup>
      </header>

      <section aria-labelledby="kit-colours" className="space-y-3">
        <h3 id="kit-colours" className="font-mono text-xs text-muted-foreground">── colours</h3>
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          {SWATCHES.map((name) => (
            <li key={name} className="space-y-1.5">
              <div className="h-12 rounded-lg border" style={{ background: `var(--${name})` }} />
              <p className="font-mono text-xs">--{name}</p>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="kit-glass" className="space-y-3">
        <h3 id="kit-glass" className="font-mono text-xs text-muted-foreground">── glass</h3>
        <div className="relative overflow-hidden rounded-2xl border p-6">
          <pre
            aria-hidden
            className="pointer-events-none absolute inset-0 m-0 font-mono text-ascii text-(--ascii-field) select-none"
          >
            {FIELD}
          </pre>
          <div className="relative grid gap-4 sm:grid-cols-3">
            {TIERS.map((tier) => (
              <div key={tier.name} className={cn(tier.name, "glass-noise relative isolate space-y-1 rounded-xl p-4")}>
                <p className="font-mono text-sm">.{tier.name}</p>
                <p className="text-sm text-muted-foreground">{tier.note}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </main>
  )
}
