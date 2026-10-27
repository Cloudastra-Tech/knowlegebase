import { EmailInCard } from "@/components/email-in-card"
import { ExtensionCard } from "@/components/extension-card"
import { TeamsAppCard } from "@/components/teams-app-card"

// The other places people ask from, beside Slack on the Integrations screen:
// Microsoft Teams, email-in and the browser extension.
export function SurfaceCards() {
  return (
    <>
      <TeamsAppCard />
      <EmailInCard />
      <ExtensionCard />
    </>
  )
}
