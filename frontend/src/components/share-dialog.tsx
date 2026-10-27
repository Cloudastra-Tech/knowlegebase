import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, Copy, Link2, Lock, X } from "lucide-react"
import { toast } from "sonner"

import { AsciiSpinner } from "@/components/ascii/ascii-spinner"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { api, type ShareLink } from "@/lib/api"
import { duration } from "@/lib/chat-history"
import { queries } from "@/lib/queries"
import { timeAgo } from "@/lib/utils"

const EXPIRY: { value: string; label: string }[] = [
  { value: "1", label: "1 day" },
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "keep", label: "As long as the chat is kept" },
]

function untilText(expires: number | null) {
  if (!expires) return "Doesn't expire"
  return `Expires ${new Date(expires * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`
}

// Share a chat, or one answer, by link with the rest of the workspace (C16). Whoever
// opens it sees only what their role may read: an answer built from documents they
// can't see is withheld, not just its sources.
export function ShareDialog({
  open,
  onOpenChange,
  conversationId,
  messageId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  conversationId: string
  messageId?: string | null // an answer: "This answer" is offered
}) {
  const queryClient = useQueryClient()
  const settings = useQuery({ ...queries.chatSettings(), enabled: open })
  const links = useQuery({ ...queries.shares(conversationId), enabled: open })
  const [what, setWhat] = useState<"answer" | "chat">(messageId ? "answer" : "chat")
  const [expiry, setExpiry] = useState("7")
  const [made, setMade] = useState<ShareLink | null>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const sharingOff = settings.data?.sharing_enabled === false

  function reset(next: boolean) {
    if (next) {
      setWhat(messageId ? "answer" : "chat")
      setMade(null)
      setCopied(false)
    }
    onOpenChange(next)
  }

  async function create() {
    setBusy(true)
    try {
      const link = await api.createShare(
        conversationId,
        what === "answer" ? (messageId ?? null) : null,
        expiry === "keep" ? null : Number(expiry),
      )
      setMade(link)
      queryClient.invalidateQueries({ queryKey: ["shares", conversationId] })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't make a link.")
    } finally {
      setBusy(false)
    }
  }

  const fullUrl = made?.url ? `${window.location.origin}${made.url}` : ""

  async function copy() {
    try {
      await navigator.clipboard.writeText(fullUrl)
      setCopied(true)
      toast.success("Link copied.")
    } catch {
      toast.error("Couldn't copy. Select the link and copy it instead.")
    }
  }

  async function revoke(id: string) {
    try {
      await api.revokeShare(id)
      if (made?.id === id) setMade(null)
      queryClient.invalidateQueries({ queryKey: ["shares", conversationId] })
      toast.success("Link turned off. It doesn't open any more.")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't turn the link off.")
    }
  }

  return (
    <Dialog open={open} onOpenChange={reset}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Share</DialogTitle>
          <DialogDescription>
            Anyone in your workspace with the link can open it. Each person sees only answers
            their role may read.
          </DialogDescription>
        </DialogHeader>

        {sharingOff ? (
          <Alert>
            <Lock />
            <AlertDescription>Your admin has turned off sharing chats.</AlertDescription>
          </Alert>
        ) : made ? (
          <div className="space-y-3">
            <Field>
              <FieldLabel htmlFor="share-link">Link</FieldLabel>
              <div className="flex gap-2">
                <Input id="share-link" value={fullUrl} readOnly onFocus={(e) => e.target.select()} />
                <Button type="button" onClick={copy} aria-label="Copy link">
                  {copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
                  Copy
                </Button>
              </div>
              <FieldDescription>
                {untilText(made.expires_at)}. This is the only time the link is shown: only a
                fingerprint of it is kept.
              </FieldDescription>
            </Field>
            {made.restricted && (
              <Alert>
                <Lock />
                <AlertDescription>
                  Some answers used HR-only documents. Only HR / Admin will be able to read
                  them; everyone else sees the question and is asked to ask it themselves.
                </AlertDescription>
              </Alert>
            )}
          </div>
        ) : (
          <FieldGroup className="gap-4">
            {messageId && (
              <RadioGroup
                value={what}
                onValueChange={(v) => setWhat(v as "answer" | "chat")}
                aria-label="What to share"
              >
                <div className="flex items-center gap-2">
                  <RadioGroupItem value="answer" id="share-answer" />
                  <Label htmlFor="share-answer" className="font-normal">
                    This answer and its question
                  </Label>
                </div>
                <div className="flex items-center gap-2">
                  <RadioGroupItem value="chat" id="share-chat" />
                  <Label htmlFor="share-chat" className="font-normal">
                    The whole chat, as it is now
                  </Label>
                </div>
              </RadioGroup>
            )}
            <Field>
              <FieldLabel htmlFor="share-expiry">Link works for</FieldLabel>
              <Select value={expiry} onValueChange={setExpiry}>
                <SelectTrigger id="share-expiry" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EXPIRY.map((e) => (
                    <SelectItem key={e.value} value={e.value}>
                      {e.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {settings.data && settings.data.retention_days !== null && (
                <FieldDescription>
                  Never longer than chats are kept ({duration(settings.data.retention_days)}).
                </FieldDescription>
              )}
            </Field>
          </FieldGroup>
        )}

        {(links.data?.length ?? 0) > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">Links that work now</p>
            <ul className="space-y-1">
              {links.data!.map((l) => (
                <li key={l.id} className="flex items-center gap-2 text-sm">
                  <Link2 className="size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate">
                    {l.kind === "answer" ? "One answer" : "Whole chat"} · made {timeAgo(l.created_at)}
                    <span className="text-muted-foreground"> · {untilText(l.expires_at)}</span>
                  </span>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Turn this link off"
                    onClick={() => revoke(l.id)}
                    className="pointer-coarse:size-10"
                  >
                    <X />
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <DialogFooter>
          {made || sharingOff ? (
            <Button variant="outline" onClick={() => reset(false)}>
              Done
            </Button>
          ) : (
            <Button onClick={create} disabled={busy || settings.isPending}>
              {busy ? <AsciiSpinner size="xs" label="" /> : <Link2 data-icon="inline-start" />}
              Create link
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
