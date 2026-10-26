import { ArrowRight } from "lucide-react"

import { AsciiAnswer, AsciiBrain, AsciiField } from "@/components/ascii-art"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type Integration = { name: string; kind: string; live?: boolean }

// Live = connected today. Everything else is on the roadmap — keep this honest.
const INTEGRATIONS: Integration[] = [
  { name: "Outline", kind: "wiki", live: true },
  { name: "PDF & text files", kind: "uploads", live: true },
  { name: "Google Drive", kind: "files" },
  { name: "Google Docs", kind: "documents" },
  { name: "Google Sheets", kind: "spreadsheets" },
  { name: "Notion", kind: "wiki" },
  { name: "Confluence", kind: "wiki" },
  { name: "SharePoint", kind: "files" },
  { name: "Slack", kind: "chat" },
  { name: "Microsoft Teams", kind: "chat" },
  { name: "Jira", kind: "tickets" },
  { name: "Linear", kind: "tickets" },
  { name: "GitHub", kind: "code & docs" },
  { name: "Dropbox", kind: "files" },
  { name: "Box", kind: "files" },
  { name: "Zendesk", kind: "help center" },
  { name: "Intercom", kind: "help center" },
]

const STEPS = [
  ["Connect your tools", "Link the wiki, drives and docs your team already uses, or upload files directly."],
  ["It indexes itself", "Every add, edit or delete is re-indexed automatically in the background."],
  ["Ask in plain language", "Get a direct answer with the exact documents it came from, cited underneath."],
]

const FEATURES = [
  ["Every answer is cited", "Sources are built from what was actually read, with links back to the original page."],
  ["Respects who can see what", "Restricted documents never reach the model for people who aren't allowed to see them."],
  ["Finds meaning and keywords", "Hybrid search combines semantic vectors and exact-word matching, then reranks."],
  ["Handles multi-step questions", "An agent chains tools (search, read, calculate, convert) to answer harder questions."],
  ["One answer, many sources", "Combine your uploaded files and your wiki in a single reply."],
  ["Guardrails built in", "Prompt-injection checks, sensitive-data filters and PII masking on every answer."],
]

const TRUST = [
  ["Self-hosted", "Runs on your own server or private cloud in a single container. No shared, multi-tenant SaaS."],
  ["Your index stays with you", "Documents and their search index (vectors) are stored on your server, not in a third-party database."],
  ["Never used for training", "Answers use the OpenAI API, whose policy is that API data isn't used to train models by default."],
  ["Permission-aware by design", "Restricted documents are filtered out before search, so the AI never receives what a user can't see."],
  ["Read-only connections", "Connected tools are only read, never edited. Your source of truth stays untouched."],
  ["Delete means gone", "Remove a document and it is dropped from the search index automatically, so it can no longer appear in answers."],
  ["Secure sign-in", "Salted, hashed passwords, lockout after failed attempts, and HTTPS on the public site."],
  ["Single sign-on", "Google Workspace, Microsoft Entra ID, Okta or any SAML / OpenID Connect provider, with roles from your groups. Offboard someone once and they lose access here too."],
]

const ALWAYS = [
  "keep your documents and search index on infrastructure you control",
  "send the AI only the snippets needed to answer the current question",
  "show the exact sources behind every answer, so it can be checked",
  "filter restricted content by permission before the AI sees it",
]

const NEVER = [
  "train AI models on your documents or questions",
  "sell, share or reuse your data for anything else",
  "write to or change your connected tools",
  "store your documents in an outside vector-database service",
]

const DEPLOY = [
  ["Your server", "One Docker container on any Linux VPS or on-prem machine."],
  ["Private cloud", "Deploy in your own AWS, Azure or GCP account and network."],
  ["In-region AI model", "Optional: Azure OpenAI in your region, or a self-hosted model, when data must not leave."],
]

const DATA_FLOW = String.raw`┌─ inside your infrastructure ──────────────────────────┐
│                                                       │
│  your documents ──▶ search index ──▶ assistant        │
│                                         │             │
└─────────────────────────────────────────┼─────────────┘
                                          │  only the question
                                          │  + a few snippets
                                          ▼
                                     AI model · keeps nothing`

function Heading({ title, body, className }: { title: React.ReactNode; body?: string; className?: string }) {
  return (
    <div className={cn("max-w-2xl", className)}>
      <h2 className="text-3xl font-medium tracking-tight text-balance sm:text-5xl">{title}</h2>
      {body && <p className="mt-4 text-muted-foreground">{body}</p>}
    </div>
  )
}

// A rule drawn in characters, between sections.
function Rule() {
  return (
    <div aria-hidden className="mx-auto max-w-6xl overflow-hidden px-4 font-mono text-xs text-foreground/20 select-none">
      {"─".repeat(240)}
    </div>
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
    <div className="dark min-h-svh bg-background text-foreground antialiased [--ascii-accent:oklch(0.82_0.15_160)] selection:bg-[var(--ascii-accent)]/30">
      <header className="sticky top-0 z-20 border-b border-border/60 bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-8 px-4">
          <span className="text-sm font-semibold tracking-tight select-none">
            Knowledge<span className="font-normal text-muted-foreground"> assistant</span>
          </span>
          <nav className="hidden items-center gap-6 font-mono text-xs text-muted-foreground md:flex">
            {[
              ["#how", "how it works"],
              ["#integrations", "integrations"],
              ["#features", "features"],
              ["#security", "security"],
            ].map(([href, label]) => (
              <a key={href} href={href} className="transition-colors hover:text-foreground">
                {label}
              </a>
            ))}
          </nav>
          <Button size="sm" className="ml-auto rounded-full px-4" onClick={onSignIn}>
            Sign in
          </Button>
        </div>
      </header>

      {/* Hero: the brain over the field, the pitch on top. */}
      <section className="relative h-[100svh] max-h-[920px] min-h-[640px] overflow-hidden">
        <AsciiBrain className="absolute inset-0" />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-3/5 bg-linear-to-t from-background via-background/90 to-transparent lg:hidden" />
        <div className="relative mx-auto flex h-full max-w-6xl items-end px-4 pb-12 lg:items-center lg:pb-0">
          <div className="max-w-xl">
            <h1 className="text-5xl leading-[1.02] font-medium tracking-tight text-balance sm:text-6xl lg:text-7xl">
              Your company's knowledge,{" "}
              <span className="font-serif font-light italic">one question away.</span>
            </h1>
            <p className="mt-6 max-w-md text-lg text-pretty text-muted-foreground">
              Connect your wiki, drives and documents. Ask anything in plain language and get a
              straight answer, with the exact sources it came from.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button size="lg" className="h-11 rounded-full px-6" onClick={onGetStarted}>
                Get started <ArrowRight data-icon="inline-end" />
              </Button>
              <Button size="lg" variant="outline" className="h-11 rounded-full px-6" asChild>
                <a href="#how">See how it answers</a>
              </Button>
            </div>
            <p className="mt-8 font-mono text-xs text-muted-foreground">
              self-hosted · never used for training · permission-aware · cited answers
            </p>
          </div>
        </div>
      </section>

      {/* How it works: documents become an answer. */}
      <section id="how" className="scroll-mt-16 py-24 sm:py-32">
        <div className="mx-auto grid max-w-6xl items-center gap-14 px-4 lg:grid-cols-[1fr_1.35fr]">
          <div>
            <Heading
              title={
                <>
                  From scattered docs to an answer{" "}
                  <span className="font-serif font-light italic">you can check.</span>
                </>
              }
            />
            <ol className="mt-10 flex flex-col gap-6">
              {STEPS.map(([title, body], i) => (
                <li key={title} className="flex gap-4">
                  <span className="pt-0.5 font-mono text-sm text-[var(--ascii-accent)] tabular-nums">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <div>
                    <p className="font-medium">{title}</p>
                    <p className="mt-1 text-sm text-muted-foreground">{body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
          <div className="rounded-2xl border bg-muted/30 p-4 sm:p-6">
            <AsciiAnswer />
          </div>
        </div>
      </section>

      <Rule />

      {/* Integrations, as a directory listing. */}
      <section id="integrations" className="scroll-mt-16 py-24 sm:py-32">
        <div className="mx-auto max-w-6xl px-4">
          <Heading
            title="Works with the tools your team already uses"
            body={`${live} sources are live today, with more on the way. Answers can combine several sources at once.`}
          />
          <ul className="mt-12 grid gap-x-10 font-mono text-sm sm:grid-cols-2 lg:grid-cols-3">
            {INTEGRATIONS.map((item) => (
              <li key={item.name} className="flex items-baseline gap-2 border-b border-dashed border-border py-3">
                <span className={cn("truncate", !item.live && "text-muted-foreground")}>{item.name}</span>
                <span className="text-xs text-muted-foreground/70">{item.kind}</span>
                <span
                  className={cn(
                    "ml-auto shrink-0 text-xs",
                    item.live ? "text-[var(--ascii-accent)]" : "text-muted-foreground/70",
                  )}
                >
                  {item.live ? "[live]" : "[soon]"}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-8 text-sm text-muted-foreground">
            Using something else? Most tools with an API can be connected.
          </p>
        </div>
      </section>

      <Rule />

      {/* Features. */}
      <section id="features" className="scroll-mt-16 py-24 sm:py-32">
        <div className="mx-auto max-w-6xl px-4">
          <Heading
            title={
              <>
                Built for answers <span className="font-serif font-light italic">you can trust.</span>
              </>
            }
            body="Not a chatbot guessing from memory: it reads your documents first, then answers only from them."
          />
          <dl className="mt-12 grid gap-x-12 sm:grid-cols-2">
            {FEATURES.map(([title, body]) => (
              <div key={title} className="border-t border-border py-6">
                <dt className="flex items-baseline gap-3 font-medium">
                  <span aria-hidden className="font-mono text-[var(--ascii-accent)]">
                    *
                  </span>
                  {title}
                </dt>
                <dd className="mt-2 pl-6 text-sm text-muted-foreground">{body}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <Rule />

      {/* Security and data control. */}
      <section id="security" className="scroll-mt-16 py-24 sm:py-32">
        <div className="mx-auto max-w-6xl px-4">
          <div className="grid items-start gap-12 lg:grid-cols-[1fr_1.1fr]">
            <div className="lg:sticky lg:top-24">
              <Heading
                title={
                  <>
                    Your data. Your server.{" "}
                    <span className="font-serif font-light italic">Your rules.</span>
                  </>
                }
                body="The assistant runs inside your own infrastructure. Your documents and their search index never leave it, nothing is used to train AI models, and you decide who can see what."
              />
              <pre
                aria-label="Your documents, search index and assistant stay inside your infrastructure; only the question and a few snippets go to the AI model, which keeps nothing."
                className="mt-10 overflow-x-auto font-mono text-[10px] leading-[1.35] text-muted-foreground sm:text-xs"
              >
                {DATA_FLOW}
              </pre>
            </div>
            <dl className="flex flex-col">
              {TRUST.map(([title, body]) => (
                <div key={title} className="grid gap-1 border-t border-border py-5 sm:grid-cols-[11rem_1fr] sm:gap-6">
                  <dt className="text-sm font-medium">{title}</dt>
                  <dd className="text-sm text-muted-foreground">{body}</dd>
                </div>
              ))}
            </dl>
          </div>

          {/* What we always / never do, as a diff. */}
          <div className="mt-20 overflow-hidden rounded-2xl border font-mono text-sm">
            <div className="border-b bg-muted/40 px-5 py-3 text-xs text-muted-foreground">
              our-promise.diff
            </div>
            <ul className="flex flex-col py-3">
              {ALWAYS.map((x) => (
                <li key={x} className="flex gap-4 bg-[var(--ascii-accent)]/[0.07] px-5 py-1.5">
                  <span aria-label="We always" className="text-[var(--ascii-accent)]">+</span>
                  <span>{x}</span>
                </li>
              ))}
              {NEVER.map((x) => (
                <li key={x} className="flex gap-4 bg-destructive/[0.08] px-5 py-1.5">
                  <span aria-label="We never" className="text-destructive">-</span>
                  <span className="text-muted-foreground">{x}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="mt-16">
            <p className="font-medium">Choose where it runs</p>
            <dl className="mt-4 grid gap-6 sm:grid-cols-3">
              {DEPLOY.map(([title, body]) => (
                <div key={title} className="border-t border-border pt-4">
                  <dt className="font-mono text-sm">{title}</dt>
                  <dd className="mt-1.5 text-sm text-muted-foreground">{body}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </section>

      {/* Call to action over the field. */}
      <section className="px-4 pb-24">
        <div className="relative mx-auto max-w-6xl overflow-hidden rounded-3xl border px-6 py-20 text-center sm:py-28">
          <AsciiField className="absolute inset-0 text-foreground/[0.14]" />
          <div className="relative">
            <h2 className="text-4xl font-medium tracking-tight text-balance sm:text-6xl">
              Stop searching. <span className="font-serif font-light italic">Start asking.</span>
            </h2>
            <p className="mx-auto mt-4 max-w-md text-muted-foreground">
              Sign in and ask your first question. It takes seconds.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Button size="lg" className="h-11 rounded-full px-6" onClick={onSignIn}>
                Sign in <ArrowRight data-icon="inline-end" />
              </Button>
              <Button size="lg" variant="outline" className="h-11 rounded-full px-6" onClick={onGetStarted}>
                Create a workspace
              </Button>
            </div>
          </div>
        </div>
      </section>

      <footer className="border-t border-border/60">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-2 px-4 py-6 font-mono text-xs text-muted-foreground sm:flex-row">
          <span>knowledge assistant · cloudastra</span>
          <span>answers from your documents, with sources.</span>
        </div>
      </footer>
    </div>
  )
}
