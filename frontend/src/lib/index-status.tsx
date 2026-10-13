import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react"

import { api, type IndexStatus } from "@/lib/api"

// Polls the background re-index status: every second while indexing, every
// 10 seconds otherwise. Components call refresh() right after changing a doc
// so the progress bar appears immediately.
const FAST_MS = 1000
const SLOW_MS = 10000

interface IndexStatusValue {
  status: IndexStatus | null
  // True for a few seconds after an index finishes, to show "Up to date".
  justFinished: boolean
  refresh: () => void
}

const IndexStatusContext = createContext<IndexStatusValue>({
  status: null,
  justFinished: false,
  refresh: () => {},
})

export function IndexStatusProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<IndexStatus | null>(null)
  const [justFinished, setJustFinished] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const wasIndexing = useRef(false)
  const finishedTimer = useRef<number | undefined>(undefined)

  const poll = useCallback(async () => {
    window.clearTimeout(timer.current)
    let next = SLOW_MS
    try {
      const s = await api.indexStatus()
      setStatus(s)
      const indexing = s.status === "indexing"
      if (wasIndexing.current && !indexing) {
        setJustFinished(true)
        window.clearTimeout(finishedTimer.current)
        finishedTimer.current = window.setTimeout(() => setJustFinished(false), 4000)
      }
      wasIndexing.current = indexing
      if (indexing) next = FAST_MS
    } catch {
      /* signed out or offline — try again later */
    }
    timer.current = window.setTimeout(poll, next)
  }, [])

  useEffect(() => {
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

export const useIndexStatus = () => useContext(IndexStatusContext)
