import { useRef } from "react"
import { FileImage, FileSpreadsheet, FileText, Paperclip, ShieldAlert, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { Attachment, AttachmentLimits } from "@/lib/api"
import { fileSize } from "@/lib/spaces"

const ICONS: Record<string, typeof FileText> = {
  xlsx: FileSpreadsheet,
  csv: FileSpreadsheet,
  image: FileImage,
}

// The paperclip: picks files to attach (the types this server reads).
export function AttachButton({
  limits,
  disabled,
  onFiles,
}: {
  limits?: AttachmentLimits
  disabled?: boolean
  onFiles: (files: File[]) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-lg"
            className="shrink-0 rounded-full text-muted-foreground"
            disabled={disabled}
            aria-label="Attach files"
            onClick={(e) => {
              e.stopPropagation()
              input.current?.click()
            }}
          >
            <Paperclip className="size-4" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          Attach files to this chat{limits ? ` (up to ${limits.file_mb} MB each)` : ""}
        </TooltipContent>
      </Tooltip>
      <Input
        ref={input}
        type="file"
        multiple
        accept={limits?.types.join(",")}
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(e) => {
          const files = [...(e.target.files ?? [])]
          e.target.value = ""
          if (files.length) onFiles(files)
        }}
      />
    </>
  )
}

// A chat's attached files above the question: name, size, remove; a placeholder
// while one is being read. Their text answers only this chat, for its owner.
export function AttachmentChips({
  files,
  uploading,
  onRemove,
}: {
  files: Attachment[]
  uploading: string[]
  onRemove: (file: Attachment) => void
}) {
  return (
    <>
      {files.map((f) => {
        const Icon = ICONS[f.kind] ?? FileText
        return (
          <span
            key={f.id}
            className="inline-flex h-7 max-w-56 items-center gap-1.5 rounded-full border border-border/70 bg-muted/40 pr-1 pl-2.5 text-xs"
          >
            <Icon className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate" title={f.filename}>
              {f.filename}
            </span>
            <span className="shrink-0 text-muted-foreground">{fileSize(f.size)}</span>
            {f.flagged && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <ShieldAlert
                    className="size-3.5 shrink-0 text-amber-600"
                    aria-label="Contains instructions"
                  />
                </TooltipTrigger>
                <TooltipContent className="max-w-60">
                  Parts of this file read like instructions to an assistant. They're treated as
                  text to answer from, never followed.
                </TooltipContent>
              </Tooltip>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-5 shrink-0 rounded-full"
              aria-label={`Remove ${f.filename}`}
              onClick={(e) => {
                e.stopPropagation()
                onRemove(f)
              }}
            >
              <X className="size-3" />
            </Button>
          </span>
        )
      })}
      {uploading.map((name) => (
        <span key={name} className="inline-flex h-7 items-center gap-1.5 text-xs text-muted-foreground">
          <Skeleton className="h-7 w-24 rounded-full" />
          <span className="sr-only">Reading {name}</span>
        </span>
      ))}
    </>
  )
}
