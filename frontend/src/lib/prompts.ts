// Saved prompts' {{variables}}, as the server reads them (prompts_store.py):
// a letter first, then letters, digits, spaces, _ or -, up to 40 characters.
const VARIABLE = /\{\{\s*([A-Za-z][A-Za-z0-9 _-]{0,39}?)\s*\}\}/g
const VALUE_CHARS = 200

// The names of the {{variables}} in a prompt, each once, in order of first use.
export function variablesOf(body: string): string[] {
  return [...new Set([...body.matchAll(VARIABLE)].map((m) => m[1]))]
}

// The prompt with each variable's value put in (on one line, cut at 200
// characters). A variable without a value stays as it is.
export function fillPrompt(body: string, values: Record<string, string>): string {
  return body.replace(VARIABLE, (whole, name: string) => {
    const value = values[name]
    if (value === undefined) return whole
    return value.split(/\s+/).filter(Boolean).join(" ").slice(0, VALUE_CHARS)
  })
}

// What follows a "/" typed at the very start of the composer, while it's still a
// search for a prompt (no new line yet); null otherwise.
export function slashQuery(text: string): string | null {
  const m = /^\/([^\n]*)$/.exec(text)
  return m ? m[1] : null
}

// A saved prompt is still in the composer while the text it put in is there whole
// (spacing aside): added to before or after, it's still that prompt. Edited into,
// replaced or cleared, it's the person's own question, sent without the prompt.
export function holdsPrompt(text: string, inserted: string): boolean {
  const squash = (s: string) => s.split(/\s+/).filter(Boolean).join(" ")
  const needle = squash(inserted)
  return needle.length > 0 && squash(text).includes(needle)
}
