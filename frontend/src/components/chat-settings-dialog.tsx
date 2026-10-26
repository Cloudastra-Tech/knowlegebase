import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { api, type ChatSettings } from "@/lib/api"
import { timeAgo } from "@/lib/utils"

const KEEP: { value: string; label: string }[] = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "forever", label: "Until people delete them" },
]

// Admins: how long this workspace keeps chats, and whether they may be shared by link
// (C12). A nightly job deletes chats not used for longer than this.
export function ChatSettingsDialog({
  open,
  onOpenChange,
  settings,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  settings: ChatSettings
}) {
  const queryClient = useQueryClient()
  const [keep, setKeep] = useState(String(settings.retention_days ?? "forever"))
  const [sharing, setSharing] = useState(settings.sharing_enabled)
  const [saving, setSaving] = useState(false)

  async function save() {
    setSaving(true)
    try {
      const saved = await api.setChatSettings(keep === "forever" ? null : Number(keep), sharing)
      queryClient.setQueryData(["chat-settings"], saved)
      toast.success("Chat settings saved.")
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't save the chat settings.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setKeep(String(settings.retention_days ?? "forever"))
          setSharing(settings.sharing_enabled)
        }
        onOpenChange(next)
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Chat history</DialogTitle>
          <DialogDescription>For everyone in this workspace. Everyone sees how long chats are kept.</DialogDescription>
        </DialogHeader>
        <FieldGroup className="gap-4">
          <Field>
            <FieldLabel htmlFor="chat-retention">Keep chats</FieldLabel>
            <Select value={keep} onValueChange={setKeep}>
              <SelectTrigger id="chat-retention" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KEEP.map((k) => (
                  <SelectItem key={k.value} value={k.value}>
                    {k.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <FieldDescription>
              Counted from when a chat was last used. Older chats are deleted every night, with
              their feedback and share links. Answers promoted to the test set are kept.
            </FieldDescription>
          </Field>
          <Field orientation="horizontal">
            <FieldContent>
              <FieldLabel htmlFor="chat-sharing">Allow sharing chats by link</FieldLabel>
              <FieldDescription>Off: every link stops working at once.</FieldDescription>
            </FieldContent>
            <Switch id="chat-sharing" checked={sharing} onCheckedChange={setSharing} />
          </Field>
        </FieldGroup>
        {settings.updated_at && (
          <p className="text-xs text-muted-foreground">
            Last changed by {settings.updated_by} · {timeAgo(settings.updated_at)}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving && <Loader2 className="animate-spin" data-icon="inline-start" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
