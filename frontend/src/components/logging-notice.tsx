import { useQuery } from "@tanstack/react-query"

import { queries } from "@/lib/queries"

// The chat's empty state says when questions are logged for the workspace's admins
// (any mode but metadata only), and for how long the text is kept.
export function LoggingNotice() {
  const settings = useQuery(queries.logSettings()).data
  if (!settings?.logged) return null
  return (
    <p className="mt-1 text-xs text-muted-foreground">
      Questions are logged for your admins for {settings.days} days
      {settings.mode === "redacted" ? ", with emails and phone numbers masked" : ""}.
    </p>
  )
}
