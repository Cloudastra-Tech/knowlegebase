import { useMemo } from "react"
import { Archive, ArrowRight, Cloud, Globe, LifeBuoy, MessagesSquare, Server, Share2, Upload } from "lucide-react"

import { AsciiBrain } from "@/components/ascii-art"
import { BrainOverlay } from "@/components/ascii/brain-overlay"
import { RedactionField, ResolveText } from "@/components/landing-close"
import { HeroLoop, type HeroItem } from "@/components/landing-hero"
import { SourceNetwork, type SourceGroup } from "@/components/landing-network"
import { ModelRoute, type ModelOption } from "@/components/landing-route"
import { AccessDemo, ChatDemo, QuestionLog, type DemoScene } from "@/components/landing-visuals"
import { Button } from "@/components/ui/button"
import { useForcedDark } from "@/lib/forced-theme"
import { cn } from "@/lib/utils"

// The page says four things only this product can say, each shown rather than
// claimed: an answer with its sources, an access rule at work, where the model runs
// and what the log keeps, and the tools it reads. Every claim is what the product
// does today; demo content is labelled as an example. Always dark
// (lib/forced-theme.ts), whatever theme the person picked for the app.
//
// Each section explains itself with one piece of ASCII motion on the shared loop
// (lib/ascii-loop.ts), running only while it's on screen, and still (its finished
// state) with reduced motion or "Reduce effects": the hero's question answered, the
// search reading passages, redaction by person, the route a question takes, the
// sources flowing one way into the index, and the closing line resolving.

// The hero's loop: a question, the two passages it finds, the answer citing them.
const HERO: HeroItem[] = [
  {
    question: "who approves expenses over $500?",
    sources: [
      { label: "expenses.pdf", text: "over $500: your manager, then Finance" },
      { label: "finance", text: "approved within 5 working days" },
    ],
    answer: [{ text: "Your manager, then Finance", cite: 1 }, { text: ", within 5 working days", cite: 2 }, { text: "." }],
  },
  {
    question: "how do I get prod db access?",
    sources: [
      { label: "runbooks", text: "request it in #infra-access" },
      { label: "onboarding", text: "access lasts 8 hours, then revoked" },
    ],
    answer: [{ text: "Ask in #infra-access", cite: 1 }, { text: "; on-call approves 8 hours", cite: 2 }, { text: "." }],
  },
  {
    question: "what discount can I offer?",
    sources: [
      { label: "pricing-2026", text: "annual plans: up to 15% off list" },
      { label: "deal desk", text: "over 15% needs deal desk sign-off" },
    ],
    answer: [{ text: "Up to 15% on annual plans", cite: 1 }, { text: "; more needs deal desk", cite: 2 }, { text: "." }],
  },
]

const DEMO: DemoScene[] = [
  {
    id: "hr",
    label: "HR",
    question: "Who approves expenses over $500?",
    answer: "Your manager approves first, then Finance, within 5 working days. Anything under $500 only needs a receipt.",
    docs: [
      { brand: null, title: "expenses.pdf", text: "Claims over $500 need your manager and then Finance to approve them.", cited: "your manager and then Finance" },
      { brand: "outline", title: "HR / Expenses", text: "Expenses under $500 only need a receipt, with no sign-off at all.", cited: "only need a receipt" },
      { brand: "googledrive", title: "Finance handbook", text: "Finance approves within 5 working days of a claim being submitted.", cited: "within 5 working days" },
    ],
    others: [
      { brand: "notion", title: "Travel policy", text: "Book economy for flights under six hours." },
      { brand: null, title: "Payroll FAQ", text: "Payslips are out on the last working day." },
    ],
  },
  {
    id: "eng",
    label: "Engineering",
    question: "How do I get production database access?",
    answer: "Ask in #infra-access. The on-call lead approves 8 hours of access, through the bastion host only.",
    docs: [
      { brand: "outline", title: "Runbooks / Access", text: "Request it in the #infra-access channel; the on-call lead approves it.", cited: "#infra-access channel" },
      { brand: "notion", title: "Engineering onboarding", text: "Access lasts 8 hours, then it is revoked. All use is logged.", cited: "Access lasts 8 hours" },
      { brand: "confluence", title: "Security policy", text: "Production data never leaves the VPN; use the bastion host to connect.", cited: "use the bastion host" },
    ],
    others: [
      { brand: "github", title: "deploy/README", text: "Deploys run from main after a review." },
      { brand: "linear", title: "Incident 4121", text: "Staging was down for an hour on Monday." },
    ],
  },
  {
    id: "sales",
    label: "Sales",
    question: "What discount can I offer?",
    answer: "Up to 15% on annual plans and none on monthly. Anything above 15% needs deal desk sign-off before you quote.",
    docs: [
      { brand: "googledrive", title: "Pricing 2026", text: "Annual plans: up to 15% off list. Monthly plans: no discounts.", cited: "up to 15% off list" },
      { brand: "notion", title: "Deal desk", text: "Over 15% needs deal desk sign-off before you quote; we answer in a day.", cited: "deal desk sign-off before you quote" },
      { brand: "hubspot", title: "Sales playbook", text: "Never promise a discount by email before it is approved." },
    ],
    others: [
      { brand: "googledrive", title: "Q3 pipeline", text: "Renewals are due by the end of September." },
      { brand: "notion", title: "Partner terms", text: "Partners resell at their own list price." },
    ],
  },
]

// Where the model runs: the three choices on the Assistant screen (models.py).
const MODELS: ModelOption[] = [
  {
    id: "aws",
    title: "Your AWS account",
    marks: ["bedrock"],
    body: "Chat and search on Bedrock, in your account and region. The server assumes a role you create, so no AWS secret is stored, and nothing goes to OpenAI.",
  },
  {
    id: "key",
    title: "Your own key",
    marks: ["openai", "anthropic"],
    body: "OpenAI or Anthropic, billed to you. The key is sealed when you save it and is never shown again.",
  },
  {
    id: "local",
    title: "This server",
    marks: [],
    icon: Server,
    body: "Search runs on the server's own CPU, so documents are indexed without leaving it. Only the question and the passages it quotes go to the model.",
  },
]

// The live connectors (connectors/registry.py: LIVE and PERSONAL), plus uploads and
// Slack, which is coming. Keep this honest when the registry changes. `sync` is an
// example of what each shows once synced (the page labels it as one), in the units
// each connector reads (its DESCRIPTION).
const SOURCES: SourceGroup[] = [
  {
    group: "Docs and wikis",
    items: [
      { name: "Confluence", brand: "confluence", sync: "1,204 pages · synced 3m ago" },
      { name: "Notion", brand: "notion", sync: "862 pages · synced 5m ago" },
      { name: "Outline", brand: "outline", sync: "319 documents · synced 12m ago" },
      { name: "Google Drive", brand: "googledrive", sync: "2,410 files · synced 4m ago" },
      { name: "SharePoint & OneDrive", icon: Share2, sync: "3,118 files · synced 9m ago" },
      { name: "Coda", brand: "coda", sync: "74 pages · synced 20m ago" },
      { name: "Dropbox", brand: "dropbox", sync: "951 files · synced 7m ago" },
      { name: "Box", brand: "box", sync: "640 files · synced 15m ago" },
      { name: "Amazon S3", icon: Archive, sync: "12,007 files · synced 1h ago" },
      { name: "Websites", icon: Globe, sync: "438 pages · synced 2h ago" },
      { name: "File uploads", icon: Upload, sync: "57 files · uploaded by hand" },
    ],
  },
  {
    group: "Projects and code",
    items: [
      { name: "Jira", brand: "jira", sync: "5,812 issues · synced 6m ago" },
      { name: "Linear", brand: "linear", sync: "1,377 issues · synced 2m ago" },
      { name: "GitHub", brand: "github", sync: "96 READMEs and docs · synced 30m ago" },
      { name: "Asana", brand: "asana", sync: "2,045 tasks · synced 11m ago" },
      { name: "ClickUp", brand: "clickup", sync: "1,630 tasks · synced 14m ago" },
    ],
  },
  {
    group: "Support and sales",
    items: [
      { name: "Zendesk", brand: "zendesk", sync: "412 articles · synced 8m ago" },
      { name: "Intercom", brand: "intercom", sync: "188 articles · synced 10m ago" },
      { name: "Freshdesk", icon: LifeBuoy, sync: "263 articles · synced 25m ago" },
      { name: "HubSpot", brand: "hubspot", sync: "97 articles · synced 40m ago" },
      { name: "Salesforce Knowledge", icon: Cloud, sync: "530 articles · synced 18m ago" },
    ],
  },
  {
    group: "Messages and mail",
    items: [
      { name: "Microsoft Teams", icon: MessagesSquare, sync: "3,904 conversations · synced 5m ago" },
      { name: "Gmail", brand: "gmail", note: "personal", sync: "each person's own mail · synced 9m ago" },
      { name: "Google Calendar", brand: "googlecalendar", note: "personal", sync: "each person's own events · synced 9m ago" },
      { name: "Slack", brand: "slack", note: "soon", sync: "coming soon: nothing synced yet" },
    ],
  },
]

const NAV = [
  ["#answer", "answers"],
  ["#access", "access"],
  ["#privacy", "privacy"],
  ["#sources", "sources"],
]

// The one serif phrase on the page (DESIGN.md: one per marketing heading, and the
// hero is the heading that gets it).
const accent = (text: string) => <span className="font-serif font-light italic">{text}</span>

function SectionTitle({ title, body, className }: { title: string; body?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("max-w-2xl", className)}>
      <h2 className="text-3xl font-medium tracking-tight text-balance sm:text-4xl">{title}</h2>
      {body && <p className="mt-4 max-w-[60ch] text-pretty text-muted-foreground">{body}</p>}
    </div>
  )
}

// The brain, the pitch, and a question being answered in ASCII: the card types it, the
// brain takes in the documents, two passages decode and the answer cites them.
function Hero({ onGetStarted }: { onGetStarted: () => void }) {
  const stream = useMemo(() => new BrainOverlay(), [])
  return (
    <section className="relative h-[100svh] max-h-[920px] min-h-[680px] overflow-hidden">
      <AsciiBrain className="absolute inset-0" overlay={stream} />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-3/5 bg-linear-to-t from-background via-background/90 to-transparent lg:hidden" />
      <div className="relative mx-auto flex h-full max-w-6xl items-end px-4 pb-12 lg:items-center lg:pb-0">
        <div className="max-w-xl">
          <h1 className="text-5xl leading-[1.04] font-medium tracking-tight text-balance sm:text-6xl">
            Cited answers {accent("from your own documents.")}
          </h1>
          <p className="mt-6 max-w-md text-lg text-pretty text-muted-foreground">
            Each answer links the passages it used, and each person only gets answers from what they're allowed to open.
          </p>
          <HeroLoop items={HERO} stream={stream} className="glass mt-7 max-w-lg rounded-xl px-4 py-3" />
          <div className="mt-7 flex flex-wrap gap-3">
            <Button size="lg" className="h-11 rounded-full px-6" onClick={onGetStarted}>
              Create a workspace <ArrowRight data-icon="inline-end" />
            </Button>
            <Button size="lg" variant="outline" className="h-11 rounded-full px-6" asChild>
              <a href="#answer">See an answer</a>
            </Button>
          </div>
        </div>
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
  useForcedDark()
  return (
    <div className="min-h-svh bg-background text-foreground antialiased selection:bg-signal/30">
      {/* The chrome glass tier, as a bar: blurred over the brain, a hairline below. */}
      <header className="sticky top-0 z-20 border-b border-(--glass-border) bg-(--glass-bg) backdrop-blur-(--glass-blur) backdrop-saturate-(--glass-saturate)">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-8 px-4">
          <span className="text-sm font-semibold tracking-tight select-none">
            Knowledge<span className="font-normal text-muted-foreground"> assistant</span>
          </span>
          <nav aria-label="Sections" className="hidden items-center gap-6 font-mono text-xs text-muted-foreground md:flex">
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

      <main>
        <Hero onGetStarted={onGetStarted} />

        {/* An answer, with what it read. Full width: the demo is the section. */}
        <section id="answer" className="scroll-mt-16 border-t border-border/60 py-20 sm:py-28">
          <div className="mx-auto max-w-6xl px-4">
            <SectionTitle
              title="Every answer shows where it came from."
              body="It searches your documents first and answers only from what it finds. Each citation opens the passage it came from."
            />
            <ChatDemo scenes={DEMO} className="mt-10" />
            <p className="mt-4 max-w-[68ch] text-sm text-pretty text-muted-foreground">
              When nothing in your documents covers a question, it says so rather than guessing, and the question shows up
              for an admin under Not found, so the gap gets written down. The documents above are examples.
            </p>
          </div>
        </section>

        {/* Access: the rule beside the demo of it. */}
        <section id="access" className="scroll-mt-16 border-t border-border/60 py-20 sm:py-28">
          <div className="mx-auto grid max-w-6xl gap-10 px-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-16">
            <div className="flex flex-col gap-6">
              <SectionTitle
                title="Same question. Different person, different answer."
                body="Each document is open to everyone, to groups or to named people, or follows its sharing in Drive, SharePoint, Confluence and the other tools that have it. What someone can't open is left out before the search, so the model never reads it."
              />
              <p className="max-w-[60ch] text-sm text-pretty text-muted-foreground">
                People sign in with Google Workspace, Microsoft Entra ID, Okta, or any SAML or OpenID Connect provider.
                SCIM keeps members and groups in step with your directory.
              </p>
            </div>
            <AccessDemo />
          </div>
        </section>

        {/* Privacy: where the model runs, then what the log keeps. */}
        <section id="privacy" className="scroll-mt-16 border-t border-border/60 py-20 sm:py-28">
          <div className="mx-auto max-w-6xl px-4">
            <SectionTitle
              title="You choose where the model runs."
              body="An admin picks it for the workspace, and can change it later."
            />
            <ModelRoute options={MODELS} className="mt-10" />

            <div className="mt-16 grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] lg:items-center lg:gap-16">
              <div>
                <h3 className="text-xl font-medium tracking-tight">What the log keeps</h3>
                <ul className="mt-4 flex flex-col gap-3 text-sm text-pretty text-muted-foreground">
                  <li>Emails, phone and card numbers are masked before a question is written down.</li>
                  <li>Question text is cleared after 90 days. Make it shorter, or keep no text at all.</li>
                  <li>Every admin change goes in an audit log that is hash-chained and exports to CSV.</li>
                </ul>
              </div>
              <QuestionLog />
            </div>
          </div>
        </section>

        {/* Sources: the real list, grouped, with the marks, joined to the index. */}
        <section id="sources" className="scroll-mt-16 border-t border-border/60 py-20 sm:py-28">
          <div className="mx-auto max-w-6xl px-4">
            <SectionTitle
              title="Reads the tools you already use. Writes to none of them."
              body="Edits and deletions at the source reach answers on the next sync. Delete a page and it can't be cited again."
            />
            <SourceNetwork groups={SOURCES} className="mt-12" />
            <p className="mt-8 max-w-[68ch] text-sm text-pretty text-muted-foreground">
              Gmail and Google Calendar are personal: each person connects their own, for their own answers. People ask in the
              web app, in Slack or Microsoft Teams, from the browser extension, or from your own tools through the API and MCP.
            </p>
          </div>
        </section>

        {/* The way in, over a still field of redacted records the heading resolves out of. */}
        <section id="start" className="scroll-mt-16 px-4 pb-24">
          <div className="relative mx-auto max-w-6xl overflow-hidden rounded-3xl border px-6 py-16 sm:px-12 sm:py-20">
            <RedactionField className="absolute inset-0" />
            <div className="relative flex flex-col items-start gap-6 sm:items-center sm:text-center">
              <h2 className="text-3xl font-medium tracking-tight text-balance sm:text-4xl">
                <ResolveText text="Start with one source." />
              </h2>
              <p className="max-w-md text-pretty text-muted-foreground">
                Connect a wiki or a drive, ask it something you already know the answer to, and check the page it cites.
              </p>
              <div className="flex flex-wrap gap-3">
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
      </main>

      <footer className="border-t border-border/60">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-6 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
          <span>Knowledge assistant by Cloudastra</span>
          <nav aria-label="Footer" className="flex gap-5 font-mono">
            {NAV.map(([href, label]) => (
              <a key={href} href={href} className="transition-colors duration-(--dur-hover) ease-hover hover:text-foreground">
                {label}
              </a>
            ))}
          </nav>
        </div>
      </footer>
    </div>
  )
}
