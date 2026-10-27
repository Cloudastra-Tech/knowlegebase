import { useState, type ReactNode } from "react"
import { BookOpen, Plus, RefreshCw, UserRound } from "lucide-react"
import { useTheme } from "next-themes"
import { useSearchParams } from "react-router"

import { AppBackdrop } from "@/components/ascii/app-backdrop"
import { ART, type ArtName } from "@/components/ascii/art"
import { AsciiBrain } from "@/components/ascii/ascii-brain"
import { AsciiDivider } from "@/components/ascii/ascii-divider"
import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { AsciiField } from "@/components/ascii/ascii-field"
import { AsciiLoader } from "@/components/ascii/ascii-loader"
import { AsciiProgress } from "@/components/ascii/ascii-progress"
import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { AsciiThinking } from "@/components/ascii/ascii-thinking"
import { AsciiTicker } from "@/components/ascii/ascii-ticker"
import { THINKING_EVENT, type ThinkingDetail } from "@/components/ascii/events"
import { ScrambleText } from "@/components/ascii/scramble-text"
import { GlassPanel } from "@/components/glass/glass-panel"
import { Panel } from "@/components/glass/panel"
import { StatusDot } from "@/components/glass/status-dot"
import { ScreenHeader } from "@/components/screen-header"
import { ThemeMenu } from "@/components/theme-menu"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { getEffects, setEffects } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// /__kit, in development only (routes.tsx): every piece of the design system in one
// place, in either theme, over the app's background field. ?view=backdrop shows just
// the field behind a plain screen (for a performance trace of the field alone).

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
  { tier: "chrome", name: "glass", note: "chrome: header, chat sidebar, composer" },
  { tier: "floating", name: "glass-strong", note: "floating: menus, dialogs, toasts" },
  { tier: "subtle", name: "glass-subtle", note: "panels inside a screen, no blur" },
] as const

const STATUSES = ["Reading the question", "Searching 4 sources", "Ranking 18 passages", "Writing the answer"]

const TICKER = [
  { question: "How many days of leave do I get?", answer: "25 days a year, plus public holidays.", source: "Leave policy §2" },
  { question: "Who approves travel over $2,000?", answer: "Your director, then finance.", source: "Travel policy" },
]

function Section({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <section aria-labelledby={`kit-${id}`} className="space-y-4">
      <AsciiDivider as="h3" id={`kit-${id}`} label={label} />
      {children}
    </section>
  )
}

export function KitGallery() {
  const { theme, setTheme } = useTheme()
  const [params] = useSearchParams()
  const [reduced, setReduced] = useState(() => getEffects() === "reduced")
  const [backdrop, setBackdrop] = useState(true)
  const [status, setStatus] = useState(0)
  const [progress, setProgress] = useState(62)

  const controls = (
    <>
      <ToggleGroup type="single" variant="outline" value={theme} onValueChange={(v) => v && setTheme(v)} aria-label="Theme">
        <ToggleGroupItem value="dark">Dark</ToggleGroupItem>
        <ToggleGroupItem value="light">Light</ToggleGroupItem>
        <ToggleGroupItem value="system">System</ToggleGroupItem>
      </ToggleGroup>
      <Label className="gap-2 font-normal">
        <Switch
          checked={reduced}
          onCheckedChange={(on) => {
            setReduced(on)
            setEffects(on ? "reduced" : "full")
          }}
        />
        Reduce effects
      </Label>
      <Label className="gap-2 font-normal">
        <Switch checked={backdrop} onCheckedChange={setBackdrop} />
        Backdrop
      </Label>
    </>
  )

  if (params.get("view") === "backdrop") {
    return (
      <main className="mx-auto max-w-5xl space-y-6 px-4 py-6">
        <AppBackdrop />
        <ScreenHeader title="Knowledge" eyebrow="~/knowledge" description="The app's field behind a plain screen." />
        <div className="grid gap-4 sm:grid-cols-2">
          <Panel title="Sources" eyebrow="12 connected">
            <p className="text-sm text-muted-foreground">Panels inside a screen are subtle glass: no blur.</p>
          </Panel>
          <Panel title="Documents" tone="solid">
            <p className="text-sm text-muted-foreground">Reading surfaces are opaque.</p>
          </Panel>
        </div>
      </main>
    )
  }

  return (
    <main className="mx-auto max-w-5xl space-y-12 px-4 py-6">
      {backdrop && <AppBackdrop />}
      <ScreenHeader title="Signal Glass kit" eyebrow="~/__kit" actions={controls} />

      <Section id="colours" label="colours">
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          {SWATCHES.map((name) => (
            <li key={name} className="space-y-1.5">
              <div className="h-12 rounded-lg border" style={{ background: `var(--${name})` }} />
              <p className="font-mono text-xs">--{name}</p>
            </li>
          ))}
        </ul>
      </Section>

      <Section id="glass" label="glass">
        <div className="relative overflow-hidden rounded-2xl border p-6">
          <AsciiField mode="ambient" tone="field" className="absolute inset-0" />
          <div className="relative grid gap-4 sm:grid-cols-3">
            {TIERS.map((t) => (
              <GlassPanel key={t.name} tier={t.tier} noise className="space-y-1 p-4">
                <p className="font-mono text-sm">.{t.name}</p>
                <p className="text-sm text-muted-foreground">{t.note}</p>
              </GlassPanel>
            ))}
          </div>
        </div>
      </Section>

      <Section id="field" label="field">
        <div className="grid gap-4 md:grid-cols-3">
          <Figure caption='mode="ambient" canvas, 12 fps, mask="radial"'>
            <AsciiField mode="ambient" tone="field" mask="radial" className="h-40 rounded-xl border" />
          </Figure>
          <Figure caption='mode="hero" canvas, 30 fps, reactive'>
            <AsciiField mode="hero" renderer="canvas" tone="field" reactive className="h-40 rounded-xl border" />
          </Figure>
          <Figure caption='mode="still" canvas, mask="bottom-fade"'>
            <AsciiField mode="still" renderer="canvas" tone="field" mask="bottom-fade" className="h-40 rounded-xl border" />
          </Figure>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            window.dispatchEvent(new CustomEvent<ThinkingDetail>(THINKING_EVENT, { detail: { active: true } }))
          }
        >
          Send the thinking ripple
        </Button>
      </Section>

      <Section id="landing" label="landing">
        <div className="grid gap-4 md:grid-cols-2">
          <Figure caption="AsciiBrain framing=&quot;panel&quot;">
            <AsciiBrain framing="panel" className="h-72 rounded-xl border" />
          </Figure>
          <Figure caption="AsciiTicker">
            <AsciiTicker items={TICKER} className="rounded-xl border px-4 py-3" />
          </Figure>
        </div>
      </Section>

      <Section id="working" label="working">
        <div className="grid gap-6 md:grid-cols-2">
          <Panel title="Spinners" eyebrow="AsciiSpinner">
            <div className="space-y-3">
              {(["braille", "dots", "line"] as const).map((variant) => (
                <div key={variant} className="flex items-center gap-4">
                  {(["xs", "sm", "md"] as const).map((size) => (
                    <AsciiSpinner key={size} variant={variant} size={size} />
                  ))}
                  <span className="font-mono text-xs text-muted-foreground">{variant}</span>
                </div>
              ))}
              <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled>
                  <AsciiSpinner label="" /> Saving…
                </Button>
                <Button size="sm" variant="outline" disabled>
                  <AsciiSpinner label="" variant="line" /> Syncing
                </Button>
              </div>
              <AsciiLoader />
            </div>
          </Panel>
          <Panel
            title="Thinking"
            eyebrow="AsciiThinking"
            actions={
              <Button size="sm" variant="outline" onClick={() => setStatus((s) => (s + 1) % STATUSES.length)}>
                <RefreshCw /> Next status
              </Button>
            }
          >
            <div className="space-y-4">
              <AsciiThinking status={STATUSES[status]} />
              <p className="text-sm text-muted-foreground">
                Workspace: <ScrambleText text="Acme Corporation" once className="font-mono text-foreground" />
              </p>
            </div>
          </Panel>
          <Panel
            title="Progress"
            eyebrow="AsciiProgress"
            actions={
              <Button size="sm" variant="outline" onClick={() => setProgress((p) => (p >= 100 ? 0 : p + 9))}>
                <Plus /> Step
              </Button>
            }
          >
            <div className="space-y-2">
              <AsciiProgress value={progress} label="Indexing" />
              <AsciiProgress value={35} cells={20} label="Upload" />
              <AsciiProgress value={100} label="Plan usage" />
            </div>
          </Panel>
          <Panel title="Status" eyebrow="StatusDot">
            <div className="flex flex-wrap gap-4">
              <StatusDot status="live" label="Live" />
              <StatusDot status="syncing" label="Syncing" />
              <StatusDot status="warning" label="Needs attention" />
              <StatusDot status="error" label="Failed" />
              <StatusDot status="off" label="Off" />
            </div>
          </Panel>
        </div>
      </Section>

      <Section id="empty" label="empty states">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(Object.keys(ART) as ArtName[]).map((art, i) => (
            <Panel key={art} tone="subtle" className="p-0">
              <AsciiEmptyState
                art={art}
                size={i % 2 ? "sm" : "md"}
                animate={art === "brain" ? "scan" : art === "inbox" ? "blink" : "none"}
                title={`No ${art} yet`}
                description={`art="${art}"`}
                action={
                  art === "plug" ? (
                    <Button size="sm">
                      <Plus /> Connect a source
                    </Button>
                  ) : undefined
                }
              />
            </Panel>
          ))}
        </div>
      </Section>

      <Section id="dividers" label="dividers">
        <div className="space-y-3">
          <AsciiDivider label="providers" />
          <AsciiDivider label="centred" align="center" char="·" />
          <AsciiDivider label="double" char="═" />
          <AsciiDivider char="╌" />
        </div>
      </Section>

      <Section id="shell" label="screen shell">
        <div className="space-y-4">
          <GlassPanel tier="subtle" className="p-5">
            <ScreenHeader
              title="Members"
              icon={UserRound}
              eyebrow="~/admin/members"
              description="Everyone who can sign in to this workspace."
              actions={
                <Button size="sm">
                  <Plus /> Invite
                </Button>
              }
            />
          </GlassPanel>
          <div className="grid gap-4 md:grid-cols-3">
            <Panel title="Subtle panel" eyebrow='tone="subtle"' corners>
              <p className="text-sm text-muted-foreground">The default: a panel in a screen.</p>
            </Panel>
            <Panel title="Glass panel" eyebrow='tone="glass"' actions={<BookOpen className="size-4 text-muted-foreground" />}>
              <p className="text-sm text-muted-foreground">Blurred, straight on the field.</p>
            </Panel>
            <Panel title="Solid panel" eyebrow='tone="solid"'>
              <p className="text-sm text-muted-foreground">Opaque, for reading and tables.</p>
            </Panel>
          </div>
        </div>
      </Section>

      <Section id="menu" label="theme menu">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              Open the account menu's theme items
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-52">
            <ThemeMenu />
          </DropdownMenuContent>
        </DropdownMenu>
      </Section>
    </main>
  )
}

function Figure({ caption, children, className }: { caption: string; children: ReactNode; className?: string }) {
  return (
    <figure className={cn("space-y-2", className)}>
      {children}
      <figcaption className="font-mono text-xs text-muted-foreground">{caption}</figcaption>
    </figure>
  )
}
