import { useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import type { SavedPrompt } from "@/lib/api"
import { fillPrompt } from "@/lib/prompts"

// A saved prompt's {{variables}}, one box each, and what it will say.
export function PromptVariablesDialog({
  prompt,
  onOpenChange,
  onUse,
}: {
  prompt: SavedPrompt | null
  onOpenChange: (open: boolean) => void
  onUse: (text: string) => void
}) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [shown, setShown] = useState<string | null>(null)
  if (prompt && shown !== prompt.id) {
    setShown(prompt.id)
    setValues({})
  }
  const filled = prompt ? fillPrompt(prompt.body, values) : ""
  const ready = prompt?.variables.every((v) => (values[v] ?? "").trim()) ?? false

  function submit(e: FormEvent) {
    e.preventDefault()
    if (ready) onUse(filled)
  }

  return (
    <Dialog open={prompt !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="flex flex-col gap-6">
          <DialogHeader>
            <DialogTitle>{prompt?.title}</DialogTitle>
            <DialogDescription>Fill these in; you can still edit the question after.</DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            {prompt?.variables.map((v, i) => (
              <Field key={v}>
                <FieldLabel htmlFor={`prompt-var-${i}`} className="capitalize">
                  {v}
                </FieldLabel>
                <Input
                  id={`prompt-var-${i}`}
                  autoFocus={i === 0}
                  autoComplete="off"
                  maxLength={200}
                  value={values[v] ?? ""}
                  onChange={(e) => setValues((all) => ({ ...all, [v]: e.target.value }))}
                />
              </Field>
            ))}
          </FieldGroup>
          <p className="rounded-lg bg-muted px-3 py-2 text-sm whitespace-pre-wrap text-muted-foreground">
            {filled}
          </p>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!ready}>
              Use prompt
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
