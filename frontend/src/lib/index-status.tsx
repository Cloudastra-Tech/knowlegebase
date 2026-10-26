import { useCallback, useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"

import { IndexStatusContext } from "@/lib/index-status-context"
import { queries } from "@/lib/queries"

// Polls the background re-index status: every second while indexing, every
// 10 seconds otherwise (and while the server can't be reached). Components call
// refresh() right after changing a doc so the progress bar appears immediately.
const FAST_MS = 1000
const SLOW_MS = 10000

export function IndexStatusProvider({ children }: { children: React.ReactNode }) {
  // Mounted only while signed in (App.tsx): signing out unmounts it, which stops the polling.
  const { data, refetch } = useQuery({
    ...queries.indexStatus(),
    refetchInterval: (query) => (query.state.data?.status === "indexing" ? FAST_MS : SLOW_MS),
    refetchIntervalInBackground: true,
    retry: false, // offline: the next poll tries again
  })
  const status = data ?? null
  const [justFinished, setJustFinished] = useState(false)
  const wasIndexing = useRef(false)
  const finishedTimer = useRef<number | undefined>(undefined)

  // A run just ended: show "Up to date" for a few seconds.
  const indexing = status?.status === "indexing"
  useEffect(() => {
    if (!status) return
    if (wasIndexing.current && !indexing) {
      setJustFinished(true)
      window.clearTimeout(finishedTimer.current)
      finishedTimer.current = window.setTimeout(() => setJustFinished(false), 4000)
    }
    wasIndexing.current = indexing
  }, [status, indexing])

  useEffect(() => () => window.clearTimeout(finishedTimer.current), [])

  const refresh = useCallback(() => {
    void refetch()
  }, [refetch])

  return (
    <IndexStatusContext.Provider value={{ status, justFinished, refresh }}>
      {children}
    </IndexStatusContext.Provider>
  )
}
