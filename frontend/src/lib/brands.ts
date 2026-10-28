import {
  siAsana,
  siBox,
  siClickup,
  siCoda,
  siConfluence,
  siDropbox,
  siGithub,
  siGmail,
  siGooglecalendar,
  siGoogledrive,
  siHubspot,
  siIntercom,
  siJira,
  siLinear,
  siNotion,
  siOutline,
  siZendesk,
} from "simple-icons"

// A brand mark: one or more paths on a 24×24 box, drawn in the current colour.
export interface Brand {
  title: string
  paths: string[]
}

const fromSimpleIcon = (icon: { title: string; path: string }): Brand => ({ title: icon.title, paths: [icon.path] })

// simple-icons has no Slack mark, so it is drawn here: one arm (a nub and a bar),
// repeated four times around the centre (brand-mark.tsx turns it).
export const SLACK_ARM = [
  "M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52z",
  "M6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z",
]

// Brand marks by the connector registry's simple-icons slug (connectors/registry.py).
export const BRANDS: Record<string, Brand> = {
  googledrive: fromSimpleIcon(siGoogledrive),
  notion: fromSimpleIcon(siNotion),
  outline: fromSimpleIcon(siOutline),
  confluence: fromSimpleIcon(siConfluence),
  jira: fromSimpleIcon(siJira),
  github: fromSimpleIcon(siGithub),
  dropbox: fromSimpleIcon(siDropbox),
  zendesk: fromSimpleIcon(siZendesk),
  linear: fromSimpleIcon(siLinear),
  box: fromSimpleIcon(siBox),
  intercom: fromSimpleIcon(siIntercom),
  hubspot: fromSimpleIcon(siHubspot),
  asana: fromSimpleIcon(siAsana),
  clickup: fromSimpleIcon(siClickup),
  coda: fromSimpleIcon(siCoda),
  gmail: fromSimpleIcon(siGmail),
  googlecalendar: fromSimpleIcon(siGooglecalendar),
  slack: { title: "Slack", paths: SLACK_ARM },
}

// Other names a source's provider goes by ("Google Docs" is a Drive file).
const ALIASES: Record<string, string> = {
  googledocs: "googledrive",
  googlesheets: "googledrive",
  googleslides: "googledrive",
  drive: "googledrive",
  slackmessages: "slack",
}

// The brand slug for a source's provider as the API names it ("Outline", "Google
// Drive", "GitHub"), or null when it has no mark (an upload, a website).
export function brandOf(provider: string | undefined): string | null {
  if (!provider) return null
  const key = provider.toLowerCase().replace(/[^a-z0-9]/g, "")
  const slug = ALIASES[key] ?? key
  return slug in BRANDS ? slug : null
}

// The distinct brands among some providers, in the order first seen.
export function brandsOf(providers: Iterable<string | undefined>): string[] {
  const out: string[] = []
  for (const p of providers) {
    const slug = brandOf(p)
    if (slug && !out.includes(slug)) out.push(slug)
  }
  return out
}
