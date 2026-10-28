import {
  ArrowRight,
  Building2,
  Cloud,
  Database,
  Eye,
  EyeOff,
  Globe,
  KeyRound,
  Layers,
  MessagesSquare,
  Plug,
  Quote,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  Trash2,
  Upload,
  Workflow,
} from "lucide-react"
import {
  siAuth0,
  siBox,
  siConfluence,
  siDropbox,
  siGithub,
  siGoogle,
  siGoogledocs,
  siGoogledrive,
  siGooglesheets,
  siIntercom,
  siJira,
  siKeycloak,
  siLinear,
  siNotion,
  siOkta,
  siOutline,
  siZendesk,
} from "simple-icons"

import { AsciiBrain, AsciiField, AsciiTicker } from "@/components/ascii-art"
import { AsciiDivider } from "@/components/ascii/ascii-divider"
import { BrandIcon, ChatDemo, KnowledgeHub, Marquee, PermissionGate, type DemoScene } from "@/components/landing-visuals"
import { Button } from "@/components/ui/button"
import { useForcedDark } from "@/lib/forced-theme"
import { cn } from "@/lib/utils"

// The page tells one story: see one brain answer from everything the company
// knows, see why it's safe, see that it fits, then start. Every claim is about
// what the product does today; demo content is labelled as an example. Always
// dark (lib/forced-theme.ts), whatever theme the person picked for the app.

const TICKER = [
  { question: "who approves expenses over $500?", answer: "Your manager, then Finance, within 5 working days.", source: "expenses.pdf" },
  { question: "how do I get prod db access?", answer: "Ask in #infra-access; on-call approves 8 hours.", source: "outline/runbooks" },
  { question: "what discount can I offer?", answer: "Up to 15% on annual plans; more needs deal desk.", source: "pricing-2026" },
]

const DEMO: DemoScene[] = [
  {
    id: "hr",
    label: "HR",
    question: "Who approves expenses over $500?",
    answer: "Your manager approves first, then Finance, within 5 working days. Anything under $500 only needs a receipt.",
    docs: [
      { kind: "upload", title: "expenses.pdf", text: "Claims over $500 need your manager and then Finance to approve them.", cited: "your manager and then Finance" },
      { kind: "outline", title: "HR / Expenses", text: "Expenses under $500 only need a receipt, with no sign-off at all.", cited: "only need a receipt" },
      { kind: "drive", title: "Finance handbook", text: "Finance approves within 5 working days of a claim being submitted.", cited: "within 5 working days" },
    ],
  },
  {
    id: "eng",
    label: "Engineering",
    question: "How do I get production database access?",
    answer: "Ask in #infra-access. The on-call lead approves 8 hours of access, through the bastion host only.",
    docs: [
      { kind: "outline", title: "Runbooks / Access", text: "Request it in the #infra-access channel; the on-call lead approves it.", cited: "#infra-access channel" },
      { kind: "notion", title: "Engineering onboarding", text: "Access lasts 8 hours, then it is revoked. All use is logged.", cited: "Access lasts 8 hours" },
      { kind: "upload", title: "security-policy.pdf", text: "Production data never leaves the VPN; use the bastion host to connect.", cited: "use the bastion host" },
    ],
  },
  {
    id: "sales",
    label: "Sales",
    question: "What discount can I offer?",
    answer: "Up to 15% on annual plans and none on monthly. Anything above 15% needs deal desk sign-off before you quote.",
    docs: [
      { kind: "drive", title: "Pricing 2026", text: "Annual plans: up to 15% off list. Monthly plans: no discounts.", cited: "up to 15% off list" },
      { kind: "notion", title: "Deal desk", text: "Over 15% needs deal desk sign-off before you quote; we answer in a day.", cited: "deal desk sign-off before you quote" },
      { kind: "outline", title: "Sales handbook", text: "Never promise a discount by email before it is approved." },
    ],
  },
]

const HOW = [
  { icon: Plug, title: "Connect", body: "Link your wiki, drives and docs, or upload files." },
  { icon: RefreshCw, title: "It keeps up", body: "Every add, edit or delete is re-indexed by itself." },
  { icon: MessagesSquare, title: "Ask", body: "Plain language in, a cited answer out." },
]

const QUALITIES = [
  { icon: Quote, title: "Cited from what it read", body: "Every answer lists its sources, with links back to the original page." },
  { icon: Layers, title: "Many sources, one answer", body: "Your drive, wiki and uploads in a single reply." },
  { icon: Search, title: "Meaning and exact words", body: "Hybrid search finds what you meant and the exact phrase, then reranks." },
  { icon: Workflow, title: "Multi-step questions", body: "An agent chains search, read, calculate and convert for harder ones." },
]

const SAFETY = [
  { icon: Server, title: "Runs on your server", body: "One container on your server or private cloud. Documents and their index stay there." },
  { icon: EyeOff, title: "Never used for training", body: "Only the question and a few snippets go to the model, under an API that doesn't train on them." },
  { icon: ShieldCheck, title: "Guardrails on every answer", body: "Prompt-injection checks, sensitive-data filters, and masking of emails, phones and cards." },
  { icon: Eye, title: "Read-only connections", body: "Your tools are only ever read. Your source of truth stays untouched." },
  { icon: Trash2, title: "Delete means gone", body: "Remove a document and it leaves the index, so it can't appear in answers." },
  { icon: Database, title: "No outside vector store", body: "The search index lives with your documents, not in a third-party database." },
]

const letter = (ch: string) => <span className="text-xs font-semibold">{ch}</span>
const MICROSOFT = (
  <svg viewBox="0 0 24 24" aria-hidden className="size-5 fill-current">
    <path d="M1 1h10v10H1zM13 1h10v10H13zM1 13h10v10H1zM13 13h10v10H13z" />
  </svg>
)

// Live = connected today (Docs and Sheets come in through Google Drive). Keep this honest.
const SOURCES = [
  { name: "Outline", glyph: <BrandIcon icon={siOutline} />, live: true, tag: "live" },
  { name: "Google Drive", glyph: <BrandIcon icon={siGoogledrive} />, live: true, tag: "live" },
  { name: "Google Docs", glyph: <BrandIcon icon={siGoogledocs} />, live: true, tag: "via Drive" },
  { name: "Google Sheets", glyph: <BrandIcon icon={siGooglesheets} />, live: true, tag: "via Drive" },
  { name: "Notion", glyph: <BrandIcon icon={siNotion} />, live: true, tag: "live" },
  { name: "PDF & text uploads", glyph: <Upload className="size-5" />, live: true, tag: "live" },
  { name: "Confluence", glyph: <BrandIcon icon={siConfluence} />, tag: "soon" },
  { name: "SharePoint", glyph: letter("S"), tag: "soon" },
  { name: "Slack", glyph: letter("#"), tag: "soon" },
  { name: "Microsoft Teams", glyph: letter("T"), tag: "soon" },
  { name: "Jira", glyph: <BrandIcon icon={siJira} />, tag: "soon" },
  { name: "Linear", glyph: <BrandIcon icon={siLinear} />, tag: "soon" },
  { name: "GitHub", glyph: <BrandIcon icon={siGithub} />, tag: "soon" },
  { name: "Dropbox", glyph: <BrandIcon icon={siDropbox} />, tag: "soon" },
  { name: "Box", glyph: <BrandIcon icon={siBox} />, tag: "soon" },
  { name: "Zendesk", glyph: <BrandIcon icon={siZendesk} />, tag: "soon" },
  { name: "Intercom", glyph: <BrandIcon icon={siIntercom} />, tag: "soon" },
]

const SIGN_IN = [
  { name: "Google Workspace", glyph: <BrandIcon icon={siGoogle} />, live: true },
  { name: "Microsoft Entra ID", glyph: MICROSOFT, live: true },
  { name: "Okta", glyph: <BrandIcon icon={siOkta} />, live: true },
  { name: "Keycloak", glyph: <BrandIcon icon={siKeycloak} />, live: true },
  { name: "Auth0", glyph: <BrandIcon icon={siAuth0} />, live: true },
  { name: "Any SAML provider", glyph: <KeyRound className="size-5" />, live: true },
  { name: "Any OpenID Connect provider", glyph: <KeyRound className="size-5" />, live: true },
]

const RUNS_ON = [
  { icon: Server, title: "Your server", body: "One Docker container on any Linux VPS or on-prem machine." },
  { icon: Cloud, title: "Private cloud", body: "Your own AWS, Azure or GCP account and network." },
  { icon: Globe, title: "In-region model", body: "Optional: Azure OpenAI in your region, or a self-hosted model." },
]

const STEPS = [
  { icon: Building2, title: "Create a workspace", body: "Sign up with your company Google Workspace account; your domain is verified straight away." },
  { icon: Plug, title: "Connect sources and sign-in", body: "Add your wiki, drives or uploads, and your identity provider if you use one." },
  { icon: MessagesSquare, title: "Ask", body: "Your team signs in with their work account and asks in plain language." },
]

const NAV = [
  ["#how", "one brain"],
  ["#ask", "ask anything"],
  ["#safe", "safe by design"],
  ["#stack", "fits your stack"],
]

const accent = (text: string) => <span className="font-serif font-light italic">{text}</span>

// A section's heading, under a mono eyebrow on a rule that matches the header's link
// to it ("── one brain ──────").
function Heading({
  eyebrow,
  title,
  body,
  center,
  className,
}: {
  eyebrow?: string
  title: React.ReactNode
  body?: string
  center?: boolean
  className?: string
}) {
  return (
    <div className={cn("max-w-2xl", center && "mx-auto text-center", className)}>
      {eyebrow && (
        <AsciiDivider
          label={eyebrow}
          align={center ? "center" : "start"}
          className={cn("mb-6 text-signal/80 [&>span[aria-hidden]]:text-muted-foreground/40", center && "mx-auto max-w-md")}
        />
      )}
      <h2 className="text-3xl font-medium tracking-tight text-balance sm:text-5xl">{title}</h2>
      {body && <p className="mt-4 text-pretty text-muted-foreground">{body}</p>}
    </div>
  )
}

// An icon in a soft signal tile; it lifts 2px while its card is hovered (only with a
// mouse: Tailwind's hover variants are gated on (hover: hover)).
function IconTile({ icon: Icon, className }: { icon: typeof Server; className?: string }) {
  return (
    <span
      className={cn(
        "flex size-10 shrink-0 items-center justify-center rounded-xl border border-signal/25 bg-signal/10 text-signal transition-transform duration-180 ease-out-strong group-hover:-translate-y-0.5",
        className,
      )}
    >
      <Icon className="size-5" strokeWidth={1.6} />
    </span>
  )
}

export function LandingPage({
  onSignIn,
  onGetStarted = onSignIn,
}: {
  onSignIn: () => void
  onGetStarted?: () => void // the sign-in page's Create a workspace tab
}) {
  useForcedDark()
  return (
    <div className="min-h-svh bg-background text-foreground antialiased selection:bg-signal/30">
      {/* The chrome glass tier, as a bar: blurred over the brain, a hairline below. */}
      <header className="sticky top-0 z-20 border-b border-(--glass-border) bg-(--glass-bg) backdrop-blur-(--glass-blur) backdrop-saturate-(--glass-saturate)">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-8 px-4">
          <span className="text-sm font-semibold tracking-tight select-none">
            Knowledge<span className="font-normal text-muted-foreground"> assistant</span>
          </span>
          <nav className="hidden items-center gap-6 font-mono text-xs text-muted-foreground md:flex">
            {NAV.map(([href, label]) => (
              <a key={href} href={href} className="transition-colors duration-(--dur-hover) ease-hover hover:text-foreground">
                {label}
              </a>
            ))}
          </nav>
          <Button size="sm" variant="outline" className="ml-auto rounded-full px-4" onClick={onSignIn}>
            Sign in
          </Button>
        </div>
      </header>

      {/* Hero: the brain, the pitch, a question being answered. */}
      <section className="relative h-[100svh] max-h-[920px] min-h-[680px] overflow-hidden">
        <AsciiBrain className="absolute inset-0" />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-3/5 bg-linear-to-t from-background via-background/90 to-transparent lg:hidden" />
        <div className="relative mx-auto flex h-full max-w-6xl items-end px-4 pb-12 lg:items-center lg:pb-0">
          <div className="max-w-xl">
            <h1 className="text-5xl leading-[1.02] font-medium tracking-tight text-balance sm:text-6xl lg:text-7xl">
              Your company's knowledge, {accent("one question away.")}
            </h1>
            <p className="mt-6 max-w-md text-lg text-pretty text-muted-foreground">
              Ask in plain language. Get a straight answer from your own documents, with the
              exact sources it came from.
            </p>
            <AsciiTicker items={TICKER} className="glass mt-7 max-w-lg rounded-xl px-4 py-3" />
            <div className="mt-7 flex flex-wrap gap-3">
              <Button size="lg" className="h-11 rounded-full px-6" onClick={onGetStarted}>
                Get started <ArrowRight data-icon="inline-end" />
              </Button>
              <Button size="lg" variant="outline" className="h-11 rounded-full px-6" asChild>
                <a href="#how">See how it works</a>
              </Button>
            </div>
          </div>
        </div>
      </section>

      {/* One brain: every source feeds it, every question goes through it. */}
      <section id="how" className="relative scroll-mt-16 overflow-hidden py-24 sm:py-32">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(50%_40%_at_50%_45%,color-mix(in_oklch,var(--signal)_10%,transparent),transparent)]" />
        <div className="relative mx-auto max-w-6xl px-4">
          <Heading
            eyebrow="one brain"
            center
            title={<>One brain for {accent("everything your company knows.")}</>}
            body="Your wiki, drives and documents feed one assistant. Ask it anything; it reads the right sources and tells you which ones it used."
          />
          <KnowledgeHub className="mt-10" />
          <ol className="mx-auto mt-10 grid max-w-4xl gap-6 sm:grid-cols-3">
            {HOW.map(({ icon, title, body }, i) => (
              <li key={title} className="group flex gap-4">
                <IconTile icon={icon} />
                <div>
                  <p className="font-medium">
                    <span className="mr-2 font-mono text-xs text-signal">0{i + 1}</span>
                    {title}
                  </p>
                  <p className="mt-1 text-sm text-muted-foreground">{body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Ask anything: a real-looking conversation, with what it read. */}
      <section id="ask" className="scroll-mt-16 border-t border-border/60 py-24 sm:py-32">
        <div className="mx-auto max-w-6xl px-4">
          <Heading
            eyebrow="ask anything"
            title={<>Ask anything. Get the answer, {accent("and where it came from.")}</>}
            body="It reads your documents first, then answers only from them. Pick a team to see a question it gets every week."
          />
          <ChatDemo scenes={DEMO} className="mt-10" />
          <p className="mt-3 font-mono text-xs text-muted-foreground">Example documents.</p>
          <div className="mt-14 grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
            {QUALITIES.map(({ icon, title, body }) => (
              <div key={title} className="group flex flex-col gap-3">
                <IconTile icon={icon} />
                <p className="font-medium">{title}</p>
                <p className="text-sm text-muted-foreground">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Safe by design: the permission gate, then the rest of the promise. */}
      <section id="safe" className="scroll-mt-16 border-t border-border/60 py-24 sm:py-32">
        <div className="mx-auto max-w-6xl px-4">
          <Heading
            eyebrow="safe by design"
            title={<>Everyone sees only {accent("what they're allowed to.")}</>}
            body="Restricted documents are removed before the search, so the AI never receives what someone can't see. Same question, two people, two answers."
          />
          <PermissionGate className="mt-10" />
          <div className="mt-14 grid gap-px overflow-hidden rounded-2xl border bg-border sm:grid-cols-2 lg:grid-cols-3">
            {SAFETY.map(({ icon, title, body }) => (
              <div key={title} className="group flex flex-col gap-3 bg-background p-6 transition-colors duration-(--dur-hover) ease-hover hover:bg-muted/30">
                <IconTile icon={icon} />
                <p className="font-medium">{title}</p>
                <p className="text-sm text-muted-foreground">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Fits your stack: sources and sign-in scroll by; where it runs. */}
      <section id="stack" className="scroll-mt-16 border-t border-border/60 py-24 sm:py-32">
        <div className="mx-auto max-w-6xl px-4">
          <Heading
            eyebrow="fits your stack"
            center
            title={<>Fits the stack {accent("you already have.")}</>}
            body="Answers from the tools your team already uses. Sign-in through the identity provider you already run."
          />
        </div>
        <div className="mt-12 flex flex-col gap-4">
          <p className="mx-auto font-mono text-xs text-muted-foreground">sources</p>
          <Marquee items={SOURCES} />
          <p className="mx-auto mt-6 font-mono text-xs text-muted-foreground">sign-in</p>
          <Marquee items={SIGN_IN} reverse />
        </div>
        <div className="mx-auto mt-16 grid max-w-6xl gap-4 px-4 sm:grid-cols-3">
          {RUNS_ON.map(({ icon, title, body }) => (
            <div key={title} className="group glass-subtle flex gap-4 rounded-2xl p-5 transition-colors duration-(--dur-hover) ease-hover hover:border-signal/40">
              <IconTile icon={icon} />
              <div>
                <p className="font-medium">{title}</p>
                <p className="mt-1 text-sm text-muted-foreground">{body}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Start: three steps and the way in, over the field. */}
      <section id="start" className="scroll-mt-16 px-4 pb-24">
        <div className="relative mx-auto max-w-6xl overflow-hidden rounded-3xl border px-6 py-20 sm:px-12 sm:py-24">
          <AsciiField className="absolute inset-0 text-foreground/[0.12]" />
          <div className="relative">
            <h2 className="text-center text-4xl font-medium tracking-tight text-balance sm:text-6xl">
              Stop searching. {accent("Start asking.")}
            </h2>
            <ol className="relative mx-auto mt-14 grid max-w-4xl gap-10 sm:grid-cols-3">
              <span aria-hidden className="absolute top-5 right-[16%] left-[16%] hidden border-t border-dashed border-signal/40 sm:block" />
              {STEPS.map(({ icon, title, body }) => (
                <li key={title} className="group relative flex flex-col items-center gap-3 text-center">
                  <IconTile icon={icon} className="bg-background" />
                  <span className="font-medium">{title}</span>
                  <span className="text-sm text-muted-foreground">{body}</span>
                </li>
              ))}
            </ol>
            <div className="mt-14 flex flex-wrap justify-center gap-3">
              <Button size="lg" className="h-11 rounded-full px-6" onClick={onGetStarted}>
                Create a workspace <ArrowRight data-icon="inline-end" />
              </Button>
              <Button size="lg" variant="outline" className="h-11 rounded-full px-6" onClick={onSignIn}>
                Sign in
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
