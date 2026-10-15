import { createContext, useContext } from "react"

import type { IndexStatus } from "@/lib/api"

// Shared by IndexStatusProvider (index-status.tsx) and the components that read it.
// Kept apart from the provider so Fast Refresh can hot-reload that component file.
interface IndexStatusValue {
  status: IndexStatus | null
  // True for a few seconds after an index finishes, to show "Up to date".
  justFinished: boolean
  refresh: () => void
}

export const IndexStatusContext = createContext<IndexStatusValue>({
  status: null,
  justFinished: false,
  refresh: () => {},
})

export const useIndexStatus = () => useContext(IndexStatusContext)
