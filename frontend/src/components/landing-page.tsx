import {
  ArrowRight,
  BookOpen,
  FileText,
  Layers,
  Lock,
  MessagesSquare,
  Plug,
  Quote,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Workflow,
} from "lucide-react"
import {
  siBox,
  siConfluence,
  siDropbox,
  siGithub,
  siGoogledocs,
  siGoogledrive,
  siGooglesheets,
  siIntercom,
  siJira,
  siLinear,
  siNotion,
  siOutline,
  siZendesk,
  type SimpleIcon,
} from "simple-icons"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

type Integration = {
  name: string
  kind: string
  icon?: SimpleIcon
  letter?: string
  live?: boolean
}

// Live = connected today. Everything else is on the roadmap — keep this honest.
const INTEGRATIONS: Integration[] = [
  { name: "Outline", kind: "Wiki", icon: siOutline, live: true },
  { name: "PDF & text files", kind: "Uploads", live: true },
  { name: "Google Drive", kind: "Files", icon: siGoogledrive },
  { name: "Google Docs", kind: "Documents", icon: siGoogledocs },
  { name: "Google Sheets", kind: "Spreadsheets", icon: siGooglesheets },
  { name: "Notion", kind: "Wiki", icon: siNotion },
  { name: "Confluence", kind: "Wiki", icon: siConfluence },
  { name: "SharePoint", kind: "Files", letter: "S" },
  { name: "Slack", kind: "Chat", letter: "#" },
  { name: "Microsoft Teams", kind: "Chat", letter: "T" },
  { name: "Jira", kind: "Tickets", icon: siJira },
  { name: "Linear", kind: "Tickets", icon: siLinear },
  { name: "GitHub", kind: "Code & docs", icon: siGithub },
  { name: "Dropbox", kind: "Files", icon: siDropbox },
  { name: "Box", kind: "Files", icon: siBox },
  { name: "Zendesk", kind: "Help center", icon: siZendesk },
  { name: "Intercom", kind: "Help center", icon: siIntercom },
]

const STEPS = [
  {
    icon: Plug,
    title: "Connect your tools",
    body: "Link the wiki, drives and docs your team already uses, or upload files directly.",
  },
  {
    icon: RefreshCw,
    title: "It indexes itself",
    body: "Every add, edit or delete is re-indexed automatically in the background.",
  },
  {
    icon: MessagesSquare,
    title: "Ask in plain language",
    body: "Get a direct answer with the exact documents it came from, cited underneath.",
  },
]

const FEATURES = [
  {
    icon: Quote,
    title: "Every answer is cited",
    body: "Sources are built from what was actually read, with links back to the original page.",
  },
  {
    icon: Lock,
    title: "Respects who can see what",
    body: "Restricted documents never reach the model for people who aren't allowed to see them.",
  },
  {
    icon: Search,
    title: "Finds meaning and keywords",
    body: "Hybrid search combines semantic vectors and exact-word matching, then reranks.",
  },
  {
    icon: Workflow,
    title: "Handles multi-step questions",
    body: "An agent chains tools (search, read, calculate, convert) to answer harder questions.",
  },
  {
    icon: Layers,
    title: "One answer, many sources",
    body: "Combine your uploaded files and your wiki in a single reply.",
  },
  {
    icon: ShieldCheck,
    title: "Guardrails built in",
    body: "Prompt-injection checks, sensitive-data filters and PII masking on every answer.",
  },
]

function IntegrationTile({ item }: { item: Integration }) {
  return (
    <div className="group flex items-center gap-3 rounded-xl border border-border/70 bg-background p-3.5 transition-colors hover:border-foreground/20">
      <div
        className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground/80 transition-colors"
        style={item.icon ? ({ "--brand": `#${item.icon.hex}` } as React.CSSProperties) : undefined}
      >
        {item.icon ? (
          <svg
            viewBox="0 0 24 24"
            className="size-5 fill-current transition-colors group-hover:text-[var(--brand)]"
            aria-hidden
          >
            <path d={item.icon.path} />
          </svg>
        ) : item.letter ? (
          <span className="text-sm font-semibold">{item.letter}</span>
        ) : (
          <FileText className="size-5" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{item.name}</p>
        <p className="text-xs text-muted-foreground">{item.kind}</p>
      </div>
      {item.live ? (
        <Badge className="shrink-0 gap-1 bg-emerald-600/10 text-emerald-700 hover:bg-emerald-600/10 dark:text-emerald-400">
          <span className="size-1.5 rounded-full bg-emerald-500" /> Live
        </Badge>
      ) : (
        <Badge variant="outline" className="shrink-0 font-normal text-muted-foreground">
          Soon
        </Badge>
      )}
    </div>
  )
}

// A static, styled preview of the chat — what an answer with citations looks like.
function ProductPreview() {
  return (
    <div className="relative mx-auto w-full max-w-xl">
      <div className="absolute -inset-6 -z-10 rounded-[2rem] bg-gradient-to-b from-muted to-transparent blur-2xl" />
      <div className="rounded-2xl border border-border/70 bg-background p-5 shadow-[0_20px_60px_-24px_rgba(0,0,0,0.25)]">
        <div className="flex justify-end">
          <div className="max-w-[80%] rounded-2xl bg-foreground px-4 py-2.5 text-sm text-background">
            What's our refund policy, and what tech stack does DevIQ use?
          </div>
        </div>
        <div className="mt-5 flex gap-3">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background">
            <Sparkles className="size-3.5" />
          </div>
          <div className="min-w-0 flex-1 space-y-3 text-sm leading-relaxed">
            <p>
              Customers can get a <b>full refund within 30 days</b>; after that it's account
              credit only. DevIQ runs a <b>Python data plane</b> (dlt + dbt) on{" "}
              <b>Postgres with RLS</b>, and a <b>TypeScript</b> app, API and agents on top.
            </p>
            <div className="space-y-1.5">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Sources
              </p>
              {[
                { n: 1, title: "handbook.txt", src: "Company documents", Icon: FileText },
                { n: 2, title: "AI Delivery Intelligence Platform", src: "Outline wiki", Icon: BookOpen },
              ].map(({ n, title, src, Icon }) => (
                <div
                  key={n}
                  className="flex items-center gap-2.5 rounded-lg border border-border/70 px-3 py-2 text-xs"
                >
                  <span className="flex size-5 items-center justify-center rounded bg-muted text-[10px] font-medium">
                    {n}
                  </span>
                  <Icon className="size-3.5 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate font-medium">{title}</span>
                  <span className="hidden text-muted-foreground sm:inline">{src}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function Section({
  id,
  eyebrow,
  title,
  body,
  children,
}: {
  id: string
  eyebrow: string
  title: string
  body?: string
  children: React.ReactNode
}) {
  return (
    <section id={id} className="scroll-mt-20 py-20 sm:py-28">
      <div className="mx-auto max-w-6xl px-4">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
            {eyebrow}
          </p>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">{title}</h2>
          {body && <p className="mt-4 text-muted-foreground">{body}</p>}
        </div>
        <div className="mt-12">{children}</div>
      </div>
    </section>
  )
}

export function LandingPage({ onSignIn }: { onSignIn: () => void }) {
  const live = INTEGRATIONS.filter((i) => i.live).length
  return (
    <div className="min-h-svh bg-background text-foreground">
      <header className="sticky top-0 z-20 border-b border-border/60 bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-8 px-4">
          <span className="select-none text-sm font-semibold tracking-tight">
            Knowledge<span className="font-normal text-muted-foreground"> assistant</span>
          </span>
          <nav className="hidden items-center gap-6 text-sm text-muted-foreground md:flex">
            <a href="#integrations" className="transition-colors hover:text-foreground">
              Integrations
            </a>
            <a href="#how" className="transition-colors hover:text-foreground">
              How it works
            </a>
            <a href="#features" className="transition-colors hover:text-foreground">
              Features
            </a>
            <a href="#security" className="transition-colors hover:text-foreground">
              Security
            </a>
          </nav>
          <Button size="sm" className="ml-auto rounded-full px-4" onClick={onSignIn}>
            Sign in
          </Button>
        </div>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(60%_50%_at_50%_0%,var(--color-muted),transparent)]" />
        <div className="mx-auto grid max-w-6xl items-center gap-14 px-4 pb-20 pt-16 sm:pt-24 lg:grid-cols-2">
          <div>
            <Badge variant="outline" className="rounded-full px-3 py-1 font-normal text-muted-foreground">
              <span className="mr-1.5 size-1.5 rounded-full bg-emerald-500" />
              Answers with citations, from your own docs
            </Badge>
            <h1 className="mt-6 text-4xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
              Your company's knowledge,
              <span className="text-muted-foreground"> one question away.</span>
            </h1>
            <p className="mt-6 max-w-lg text-lg text-muted-foreground">
              Connect your wiki, drives and documents. Ask anything in plain language and get a
              straight answer, with the exact sources it came from.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button size="lg" className="rounded-full px-6" onClick={onSignIn}>
                Get started <ArrowRight />
              </Button>
              <Button size="lg" variant="outline" className="rounded-full px-6" asChild>
                <a href="#integrations">See integrations</a>
              </Button>
            </div>
          </div>
          <ProductPreview />
        </div>
      </section>

      {/* Integrations */}
      <div className="border-y border-border/60 bg-muted/30">
        <Section
          id="integrations"
          eyebrow="Integrations"
          title="Works with the tools your team already uses"
          body={`${live} sources are live today, with more on the way. Answers can combine several sources at once.`}
        >
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {INTEGRATIONS.map((item) => (
              <IntegrationTile key={item.name} item={item} />
            ))}
          </div>
          <p className="mt-8 text-center text-sm text-muted-foreground">
            Using something else? Most tools with an API can be connected.
          </p>
        </Section>
      </div>

      {/* How it works */}
      <Section id="how" eyebrow="How it works" title="From scattered docs to answers in three steps">
        <div className="grid gap-6 md:grid-cols-3">
          {STEPS.map((step, i) => (
            <div key={step.title} className="rounded-2xl border border-border/70 p-6">
              <div className="flex items-center justify-between">
                <div className="flex size-10 items-center justify-center rounded-xl bg-foreground text-background">
                  <step.icon className="size-5" />
                </div>
                <span className="text-sm tabular-nums text-muted-foreground">0{i + 1}</span>
              </div>
              <h3 className="mt-5 font-semibold">{step.title}</h3>
              <p className="mt-2 text-sm text-muted-foreground">{step.body}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* Features */}
      <div className="border-y border-border/60 bg-muted/30">
        <Section
          id="features"
          eyebrow="Features"
          title="Built for answers you can trust"
          body="Not a chatbot guessing from memory: it reads your documents first, then answers only from them."
        >
          <div className="grid gap-px overflow-hidden rounded-2xl border border-border/70 bg-border/70 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <div key={f.title} className="bg-background p-6">
                <f.icon className="size-5 text-foreground/80" />
                <h3 className="mt-4 font-semibold">{f.title}</h3>
                <p className="mt-2 text-sm text-muted-foreground">{f.body}</p>
              </div>
            ))}
          </div>
        </Section>
      </div>

      {/* Security */}
      <Section
        id="security"
        eyebrow="Security"
        title="Private by default"
        body="Your documents stay in your workspace. Only signed-in teammates can ask questions, and restricted content stays restricted."
      >
        <div className="mx-auto grid max-w-4xl gap-4 sm:grid-cols-3">
          {[
            ["Sign-in required", "Accounts with salted, hashed passwords and lockout after failed attempts."],
            ["Access-aware search", "HR-only documents are filtered out before the model ever sees them."],
            ["Safety checks", "Every question and answer passes injection and sensitive-data guards."],
          ].map(([title, body]) => (
            <div key={title} className="rounded-2xl border border-border/70 p-5">
              <ShieldCheck className="size-5 text-foreground/80" />
              <h3 className="mt-3 text-sm font-semibold">{title}</h3>
              <p className="mt-1.5 text-sm text-muted-foreground">{body}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* CTA */}
      <section className="px-4 pb-24">
        <div className="mx-auto max-w-6xl rounded-3xl bg-foreground px-6 py-16 text-center text-background sm:py-20">
          <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Stop searching. Start asking.
          </h2>
          <p className="mx-auto mt-4 max-w-md text-background/70">
            Sign in and ask your first question. It takes seconds.
          </p>
          <Button
            size="lg"
            variant="secondary"
            className="mt-8 rounded-full px-6"
            onClick={onSignIn}
          >
            Sign in <ArrowRight />
          </Button>
        </div>
      </section>

      <footer className="border-t border-border/60">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-2 px-4 py-6 text-xs text-muted-foreground sm:flex-row">
          <span>Knowledge assistant · Cloudastra</span>
          <span>Answers from your documents, with sources.</span>
        </div>
      </footer>
    </div>
  )
}
