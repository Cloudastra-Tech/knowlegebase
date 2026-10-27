import { useState } from "react"
import { Check, Loader2, PanelRight, TriangleAlert } from "lucide-react"

import { Button } from "@/components/ui/button"
import { extension } from "@/lib/api"
import { useUser } from "@/lib/auth-context"

// Where a browser extension's sign-in comes back to (chrome.identity's redirect URL).
const REDIRECT = /^https:\/\/[a-p]{32}\.chromiumapp\.org\/$/

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong")

// /extension/connect?redirect=https://<id>.chromiumapp.org/ : the browser extension
// opened this (chrome.identity.launchWebAuthFlow) to sign in. Connect hands it a
// one-time code, which it swaps for a key of its own (kb/routers/extension.py).
export function ExtensionConnect({ redirect }: { redirect: string }) {
  const user = useUser()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const valid = REDIRECT.test(redirect)

  async function connect() {
    setBusy(true)
    setProblem(null)
    try {
      const { code } = await extension.connect(redirect)
      window.location.replace(`${redirect}?code=${encodeURIComponent(code)}`)
    } catch (err) {
      setProblem(errorText(err))
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-md flex-col px-4 py-12">
      <div className="rounded-2xl border border-border/70 bg-background p-6">
        <div className="flex size-10 items-center justify-center rounded-xl bg-muted">
          <PanelRight className="size-5" />
        </div>
        <h1 className="mt-4 text-lg font-semibold">Connect the browser extension?</h1>
        {valid ? (
          <>
            <p className="mt-1 text-sm text-muted-foreground">
              It will ask the knowledge base as you, {user.name || user.username}, and see what
              your role can see.
            </p>
            <ul className="mt-4 grid gap-2 text-sm">
              {[
                "Asks questions only: it can't change documents or settings.",
                "Never reads the page; text you select goes along only when you choose.",
                "Lasts 90 days. Disconnect it any time in Integrations or API keys.",
              ].map((line) => (
                <li key={line} className="flex items-start gap-2">
                  <Check className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                  <span>{line}</span>
                </li>
              ))}
            </ul>
            {problem && (
              <p className="mt-4 flex items-start gap-1.5 text-sm text-destructive">
                <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                {problem}
              </p>
            )}
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => window.location.replace(`${redirect}?error=cancelled`)}
              >
                Cancel
              </Button>
              <Button onClick={connect} disabled={busy}>
                {busy && <Loader2 className="size-4 animate-spin" />}
                Connect
              </Button>
            </div>
          </>
        ) : (
          <p className="mt-2 flex items-start gap-1.5 text-sm text-destructive">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            This link didn't come from the browser extension. Open the extension and click Connect
            there.
          </p>
        )}
      </div>
    </div>
  )
}
