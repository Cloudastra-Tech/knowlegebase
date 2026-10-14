import { useCallback, useEffect, useRef, useState } from "react"

import { api, type IndexStatus } from "@/lib/api"
import { IndexStatusContext } from "@/lib/index-status-context"

// Polls the background re-index status: every second while indexing, every
// 10 seconds otherwise. Components call refresh() right after changing a doc
// so the progress bar appears immediately.
const FAST_MS = 1000
const SLOW_MS = 10000

export function IndexStatusProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<IndexStatus | null>(null)
  const [justFinished, setJustFinished] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const wasIndexing = useRef(false)
  const finishedTimer = useRef<number | undefined>(undefined)
  // The timer calls the poll through this ref: poll can't name itself while
  // it's still being defined.
  const pollRef = useRef<() => void>(() => {})

  const poll = useCallback(() => {
    window.clearTimeout(timer.current)
    return api
      .indexStatus()
      .then((s) => {
        setStatus(s)
        const indexing = s.status === "indexing"
        if (wasIndexing.current && !indexing) {
          setJustFinished(true)
          window.clearTimeout(finishedTimer.current)
          finishedTimer.current = window.setTimeout(() => setJustFinished(false), 4000)
        }
        wasIndexing.current = indexing
        return indexing ? FAST_MS : SLOW_MS
      })
      .catch(() => SLOW_MS) // signed out or offline — try again later
      .then((next) => {
        timer.current = window.setTimeout(() => pollRef.current(), next)
      })
  }, [])

  useEffect(() => {
    pollRef.current = poll
    poll()
    return () => {
      window.clearTimeout(timer.current)
      window.clearTimeout(finishedTimer.current)
    }
  }, [poll])

  return (
    <IndexStatusContext.Provider value={{ status, justFinished, refresh: poll }}>
      {children}
    </IndexStatusContext.Provider>
  )
}
