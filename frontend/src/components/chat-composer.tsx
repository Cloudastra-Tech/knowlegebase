import { useEffect, useId, type FormEvent, type KeyboardEvent, type RefObject } from "react"
import { ArrowUp, Square } from "lucide-react"

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
}

// Where a question is typed. Enter sends, Shift+Enter starts a new line, Esc stops
// an answer being written, ↑ in an empty box edits the last question. Grows with its text up to max-h-48. Text is 16px on
// phones (smaller makes iOS zoom in on focus), 14px from md (the Textarea's own sizes).
export function Composer({ value, onChange, onSend, onStop, onEditLast, busy, inputRef }: ComposerProps) {
  const counterId = useId()
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

  return (
    // The bottom padding clears a phone's home indicator (safe area).
    <form
      onSubmit={onSubmit}
      className="pointer-events-auto mx-auto max-w-3xl px-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
    >
      <div
        onClick={() => inputRef.current?.focus()}
        className={cn(
          "flex cursor-text items-end gap-2 rounded-3xl border border-border/70 bg-background py-2 pl-5 pr-2 shadow-[0_2px_20px_-6px_rgba(0,0,0,0.12)] transition-all focus-within:border-foreground/25 focus-within:shadow-[0_4px_28px_-8px_rgba(0,0,0,0.2)]",
          over && "border-destructive/60 focus-within:border-destructive/60",
        )}
      >
        <Textarea
          ref={inputRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
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
      <div className="mt-2 flex items-center justify-center gap-3 text-xs text-muted-foreground">
        <span className="hidden sm:inline">
          {busy ? "Esc to stop" : "Enter to send · Shift + Enter for a new line"}
        </span>
        {value.length >= COUNT_FROM && (
          <span
            id={counterId}
            aria-live="polite"
            className={cn("tabular-nums", over && "font-medium text-destructive")}
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
