import { Component, Suspense, type ReactNode } from "react"

import { AsciiEmptyState } from "@/components/ascii/ascii-empty-state"
import { AsciiLoader } from "@/components/ascii/ascii-loader"
import { Button } from "@/components/ui/button"

// A page whose code is downloaded on demand (React.lazy): a mono "loading…" while it
// loads (AsciiLoader keeps data-slot="skeleton", which the e2e specs wait to see
// gone), and a way out if the download fails — e.g. the site was redeployed while
// this tab was open, so the old file is gone.
export function LazyPage({ children, fullScreen = false }: { children: ReactNode; fullScreen?: boolean }) {
  return (
    <PageErrorBoundary fullScreen={fullScreen}>
      <Suspense
        fallback={
          <Centered fullScreen={fullScreen}>
            <AsciiLoader />
          </Centered>
        }
      >
        {children}
      </Suspense>
    </PageErrorBoundary>
  )
}

function Centered({ children, fullScreen }: { children: ReactNode; fullScreen: boolean }) {
  return (
    <div
      className={
        fullScreen
          ? "flex min-h-svh flex-col items-center justify-center gap-3"
          : "flex h-full min-h-40 flex-col items-center justify-center gap-3"
      }
    >
      {children}
    </div>
  )
}

class PageErrorBoundary extends Component<
  { children: ReactNode; fullScreen: boolean },
  { failed: boolean }
> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <Centered fullScreen={this.props.fullScreen}>
        <AsciiEmptyState
          art="broken"
          title="This page couldn't load"
          description="The site may have just been updated."
          action={
            <Button variant="outline" size="sm" onClick={() => window.location.reload()}>
              Reload
            </Button>
          }
        />
      </Centered>
    )
  }
}
