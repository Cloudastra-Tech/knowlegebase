import { useQuery } from "@tanstack/react-query"

import { queries } from "@/lib/queries"

// The chat's empty state says when questions are logged for the workspace's admins
// (any mode but metadata only), and for how long the text is kept. `inline`: as the
// rest of a line, after a dot (the fine print under the composer).
export function LoggingNotice({ inline = false }: { inline?: boolean }) {
  const settings = useQuery(queries.logSettings()).data
  if (!settings?.logged) return null
  const text = (
    <>
      Questions are logged for your admins for {settings.days} days
      {settings.mode === "redacted" ? ", with emails and phone numbers masked" : ""}.
    </>
  )
  if (inline) return <span> · {text}</span>
  return <p className="mt-1 text-xs text-muted-foreground">{text}</p>
}
