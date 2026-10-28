import { useState } from "react"
import { useQuery } from "@tanstack/react-query"

import { ChatSettingsDialog } from "@/components/chat-settings-dialog"
import {
  SettingsError,
  SettingsGroup,
  SettingsRow,
  SettingsSection,
  SettingsSkeleton,
} from "@/components/settings/settings-section"
import { Button } from "@/components/ui/button"
import { useActiveRole, useUser } from "@/lib/auth-context"
import { keptFor } from "@/lib/chat-history"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

// Settings › Chats: how long this workspace keeps chats, and whether they may be
// shared by link. Everyone sees it; an admin viewing the app as HR changes it
// (ChatSettingsDialog).
export function ChatsSettings() {
  const user = useUser()
  const role = useActiveRole()
  const settings = useQuery(queries.chatSettings())
  const [open, setOpen] = useState(false)
  const canChange = user.role === "admin" && role === "hr"
  const data = settings.data

  return (
    <SettingsSection
      title="Chats"
      description="How long chats are kept, and whether they can be shared by link. The same for everyone in this workspace."
      action={
        canChange && data ? (
          <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
            Change
          </Button>
        ) : undefined
      }
    >
      {settings.isPending ? (
        <SettingsSkeleton rows={2} label="Loading chat settings" />
      ) : !data ? (
        <SettingsError what="the chat settings" onRetry={() => void settings.refetch()} />
      ) : (
        <SettingsGroup title="History">
          <SettingsRow
            label="Chats are kept"
            description={
              data.retention_days === null
                ? "Nothing is deleted on a schedule."
                : "Counted from when a chat was last used. Older ones are deleted every night."
            }
          >
            <span className="text-sm text-muted-foreground">{capitalize(keptFor(data.retention_days))}</span>
          </SettingsRow>
          <SettingsRow
            label="Sharing by link"
            description="A shared link shows a chat, or one answer, to people in this workspace."
          >
            <span className="text-sm text-muted-foreground">{data.sharing_enabled ? "Allowed" : "Off"}</span>
          </SettingsRow>
          {!canChange && (
            <p className="px-4 py-3 text-xs text-muted-foreground">Your workspace's admins set these.</p>
          )}
          {canChange && data.updated_at && (
            <p className="px-4 py-3 text-xs text-muted-foreground">
              Last changed by {data.updated_by} · {timeAgo(data.updated_at)}
            </p>
          )}
        </SettingsGroup>
      )}
      {canChange && data && <ChatSettingsDialog open={open} onOpenChange={setOpen} settings={data} />}
    </SettingsSection>
  )
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
