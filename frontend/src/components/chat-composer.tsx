import {
  useEffect,
  useId,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react"
import { ArrowUp, Square } from "lucide-react"

import "@/components/chat-motion.css"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

// The server's limit on a question (safety.py MAX_QUESTION_CHARS). The counter shows
// from COUNT_FROM characters, before it's reached.
export const QUESTION_LIMIT = 500
const COUNT_FROM = 400

interface ComposerProps {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  onStop: () => void
  onEditLast?: () => void // ↑ in an empty box: edit the last question
  busy: boolean // an answer is being written: the send button becomes Stop
  inputRef: RefObject<HTMLTextAreaElement | null> // the chat focuses it (Rephrase, New chat)
  top?: ReactNode // inside the box, above the text: the space chip, attached files
  leading?: ReactNode // before the text: the paperclip
  above?: ReactNode // over the box: the "/" menu of saved prompts
  intercept?: (e: KeyboardEvent<HTMLTextAreaElement>) => boolean // true: handled (the "/" menu)
  onFiles?: (files: File[]) => void // dropped or pasted files, to attach
}

// Where a question is typed. Enter sends, Shift+Enter starts a new line, Esc stops
// an answer being written, ↑ in an empty box edits the last question. Grows with its text up to max-h-48. Text is 16px on
// phones (smaller makes iOS zoom in on focus), 14px from md (the Textarea's own sizes).
// The keys are said in a small line under the box only while they help: focused and
// empty ("Enter to send…"), or while an answer is written ("Esc to stop"). The line
// keeps its height either way, so nothing moves when it comes and goes.
export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  onEditLast,
  busy,
  inputRef,
  top,
  leading,
  above,
  intercept,
  onFiles,
}: ComposerProps) {
  const counterId = useId()
  const [focused, setFocused] = useState(false)
  const over = value.length > QUESTION_LIMIT
  const canSend = !busy && !over && value.trim().length > 0

  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${el.scrollHeight}px`
  }, [value, inputRef])

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (canSend) onSend()
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (intercept?.(e)) return
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (canSend) onSend()
    } else if (e.key === "Escape" && busy) {
      e.preventDefault()
      onStop()
    } else if (e.key === "ArrowUp" && !value && onEditLast) {
      e.preventDefault()
      onEditLast()
    }
  }

  // Files dropped on the box, or pasted into it, are attached (when the chat takes them).
  function onDrop(e: DragEvent<HTMLDivElement>) {
    if (!onFiles || !e.dataTransfer.files.length) return
    e.preventDefault()
    onFiles([...e.dataTransfer.files])
  }
  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    if (!onFiles || !e.clipboardData.files.length) return
    e.preventDefault()
    onFiles([...e.clipboardData.files])
  }

  return (
    // The bottom padding clears a phone's home indicator (safe area).
    <form
      onSubmit={onSubmit}
      className="pointer-events-auto relative mx-auto max-w-3xl px-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
    >
      {above}
      {/* Chrome-tier glass with grain, 22px, the chat's one lifted thing: the field (or
          the empty chat's art) blurred behind it. Focus is the signal: its border and a
          soft ring (chat-motion.css, kb-composer), at once, as focus is often from the
          keyboard. kb-frost: a thinner fill and blur, so the art behind it reads as
          motion through the frost. data-drift: the empty chat's art reads where it is. */}
      <div
        onClick={() => inputRef.current?.focus()}
        onDragOver={(e) => onFiles && e.preventDefault()}
        onDrop={onDrop}
        data-drift="composer"
        data-over={over || undefined}
        className={cn(
          "kb-composer kb-frost glass glass-noise cursor-text rounded-3xl py-2 pr-2",
          leading ? "pl-2" : "pl-5",
        )}
      >
        {top && <div className="mb-1 flex flex-wrap items-center gap-1.5 pl-1">{top}</div>}
        <div className="flex items-end gap-2">
          {leading}
          <Textarea
            ref={inputRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder="Ask anything…"
            aria-label="Your question"
            aria-invalid={over || undefined}
            aria-describedby={value.length >= COUNT_FROM ? counterId : undefined}
            rows={1}
            className="max-h-48 min-h-9 flex-1 resize-none rounded-none border-0 bg-transparent px-0 py-1.5 leading-6 shadow-none placeholder:text-muted-foreground/70 focus-visible:ring-0 aria-invalid:ring-0 dark:bg-transparent"
          />
          {busy ? (
            <Button
              type="button"
              size="icon-lg"
              onClick={onStop}
              aria-label="Stop answering"
              className="rounded-full"
            >
              <Square className="size-3.5 fill-current" />
            </Button>
          ) : (
            <Button
              type="submit"
              size="icon-lg"
              disabled={!canSend}
              aria-label="Send"
              className="rounded-full"
            >
              <ArrowUp className="size-4" strokeWidth={2.5} />
            </Button>
          )}
        </div>
      </div>
      <div data-drift="quiet" className="mt-2 flex h-4 items-center justify-between gap-3 px-5 text-xs text-muted-foreground">
        <span data-slot="composer-hint" className="hidden truncate sm:inline">
          {busy
            ? "Esc to stop"
            : focused && !value
              ? "Enter to send · Shift + Enter for a new line · / for saved prompts"
              : null}
        </span>
        {value.length >= COUNT_FROM && (
          <span
            id={counterId}
            aria-live="polite"
            className={cn("ml-auto shrink-0 tabular-nums", over && "font-medium text-destructive")}
          >
            {over
              ? `${value.length - QUESTION_LIMIT} over the ${QUESTION_LIMIT}-character limit`
              : `${value.length} / ${QUESTION_LIMIT}`}
          </span>
        )}
      </div>
    </form>
  )
}
