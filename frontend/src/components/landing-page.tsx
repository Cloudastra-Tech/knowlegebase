import {
  ArrowRight,
  BookOpen,
  Building2,
  Check,
  Cloud,
  Database,
  Eye,
  EyeOff,
  FileText,
  Globe,
  KeyRound,
  Server,
  Trash2,
  X,
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
import { cn } from "@/lib/utils"

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

const TRUST = [
  {
    icon: Server,
    title: "Self-hosted",
    body: "Runs on your own server or private cloud in a single container. No shared, multi-tenant SaaS.",
  },
  {
    icon: Database,
    title: "Your index stays with you",
    body: "Documents and their search index (vectors) are stored on your server, not in a third-party database.",
  },
  {
    icon: EyeOff,
    title: "Never used for training",
    body: "Answers use the OpenAI API, whose policy is that API data isn't used to train models by default.",
  },
  {
    icon: Lock,
    title: "Permission-aware by design",
    body: "Restricted documents are filtered out before search, so the AI never receives what a user can't see.",
  },
  {
    icon: Eye,
    title: "Read-only connections",
    body: "Connected tools are only read, never edited. Your source of truth stays untouched.",
  },
  {
    icon: Trash2,
    title: "Delete means gone",
    body: "Remove a document and it is dropped from the search index automatically, so it can no longer appear in answers.",
  },
  {
    icon: KeyRound,
    title: "Secure sign-in",
    body: "Salted, hashed passwords, lockout after failed attempts, and HTTPS on the public site.",
  },
  {
    icon: ShieldCheck,
    title: "Guardrails on every answer",
    body: "Prompt-injection detection, sensitive-data filters and automatic masking of emails, phones and cards.",
  },
  {
    icon: Building2,
    title: "Single sign-on (SSO)",
    body: "Sign in with Google Workspace or Microsoft Entra ID over SAML / OIDC, so access follows your company directory. Offboard someone once and they lose access here too.",
    tag: "Soon",
    wide: true,
  },
] as { icon: typeof Server; title: string; body: string; tag?: string; wide?: boolean }[]

const ALWAYS = [
  "Keep your documents and search index on infrastructure you control",
  "Send the AI only the snippets needed to answer the current question",
  "Show the exact sources behind every answer, so it can be checked",
  "Filter restricted content by permission before the AI sees it",
]

const NEVER = [
  "Train AI models on your documents or questions",
  "Sell, share or reuse your data for anything else",
  "Write to or change your connected tools",
  "Store your documents in an outside vector-database service",
]

const DEPLOY = [
  { icon: Server, title: "Your server", tag: "", body: "One Docker container on any Linux VPS or on-prem machine." },
  { icon: Cloud, title: "Private cloud", tag: "", body: "Deploy in your own AWS, Azure or GCP account and network." },
  {
    icon: Globe,
    title: "In-region AI model",
    tag: "Option",
    body: "Swap to Azure OpenAI in your region, or a self-hosted model, when data must not leave.",
  },
]

// Where data goes: documents and index stay inside; only snippets reach the model.
function DataFlow() {
  return (
    <div className="mt-8 rounded-2xl border border-border/70 p-5">
      <div className="rounded-xl border border-dashed border-foreground/25 p-4">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Inside your infrastructure
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          {["Your documents", "Search index", "Assistant"].map((label, i) => (
            <div key={label} className="flex items-center gap-2">
              {i > 0 && <ArrowRight className="size-3.5 text-muted-foreground" />}
              <span className="rounded-lg bg-muted px-3 py-1.5 font-medium">{label}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="mt-3 flex items-start gap-3 text-sm">
        <ArrowRight className="mt-0.5 size-3.5 shrink-0 rotate-90 text-muted-foreground" />
        <p className="text-muted-foreground">
          Only the question and the few relevant snippets go to the AI model to write the answer.
          Nothing is kept for training.
        </p>
      </div>
    </div>
  )
}

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

export function LandingPage({
  onSignIn,
  onGetStarted = onSignIn,
}: {
  onSignIn: () => void
  onGetStarted?: () => void // the sign-in page's Create a workspace tab
}) {
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
              <Button size="lg" className="rounded-full px-6" onClick={onGetStarted}>
                Get started <ArrowRight />
              </Button>
              <Button size="lg" variant="outline" className="rounded-full px-6" asChild>
                <a href="#integrations">See integrations</a>
              </Button>
            </div>
            <div className="mt-8 flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
              {["Self-hosted", "Never used for training", "Permission-aware", "Cited answers"].map(
                (t) => (
                  <span key={t} className="flex items-center gap-1.5">
                    <Check className="size-3.5 text-emerald-600" /> {t}
                  </span>
                ),
              )}
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

      {/* Security & data control — the trust USP */}
      <section id="security" className="scroll-mt-20 py-20 sm:py-28">
        <div className="mx-auto max-w-6xl px-4">
          <div className="grid items-start gap-12 lg:grid-cols-[1fr_1.1fr]">
            <div className="lg:sticky lg:top-24">
              <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                Security &amp; data control
              </p>
              <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-5xl">
                Your data.
                <br />
                <span className="text-muted-foreground">Your server. Your rules.</span>
              </h2>
              <p className="mt-5 max-w-md text-muted-foreground">
                The assistant runs inside your own infrastructure. Your documents and their search
                index never leave it, nothing is used to train AI models, and you decide who can see
                what.
              </p>
              <DataFlow />
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              {TRUST.map((t) => (
                <div
                  key={t.title}
                  className={cn(
                    "rounded-2xl border border-border/70 bg-background p-5 transition-colors hover:border-foreground/20",
                    t.wide && "sm:col-span-2",
                  )}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex size-9 items-center justify-center rounded-lg bg-foreground text-background">
                      <t.icon className="size-4" />
                    </div>
                    {t.tag && (
                      <Badge variant="outline" className="font-normal text-muted-foreground">
                        {t.tag}
                      </Badge>
                    )}
                  </div>
                  <h3 className="mt-4 text-sm font-semibold">{t.title}</h3>
                  <p className="mt-1.5 text-sm text-muted-foreground">{t.body}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-16 grid gap-4 md:grid-cols-2">
            <div className="rounded-2xl border border-border/70 p-6">
              <p className="text-sm font-semibold">What we always do</p>
              <ul className="mt-4 space-y-3">
                {ALWAYS.map((x) => (
                  <li key={x} className="flex gap-3 text-sm">
                    <Check className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                    <span>{x}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded-2xl border border-border/70 p-6">
              <p className="text-sm font-semibold">What we never do</p>
              <ul className="mt-4 space-y-3">
                {NEVER.map((x) => (
                  <li key={x} className="flex gap-3 text-sm">
                    <X className="mt-0.5 size-4 shrink-0 text-destructive" />
                    <span>{x}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div className="mt-4 rounded-2xl border border-border/70 bg-muted/30 p-6">
            <p className="text-sm font-semibold">Choose where it runs</p>
            <div className="mt-4 grid gap-5 sm:grid-cols-3">
              {DEPLOY.map((d) => (
                <div key={d.title}>
                  <div className="flex items-center gap-2">
                    <d.icon className="size-4 text-foreground/80" />
                    <p className="text-sm font-medium">{d.title}</p>
                    {d.tag && (
                      <Badge variant="outline" className="font-normal text-muted-foreground">
                        {d.tag}
                      </Badge>
                    )}
                  </div>
                  <p className="mt-1.5 text-sm text-muted-foreground">{d.body}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

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
