import { QueryCache, QueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { ApiError } from "@/lib/api"

declare module "@tanstack/react-query" {
  interface Register {
    // silent: a failed load shows no toast (background polling).
    queryMeta: { silent?: boolean }
  }
}

// One cache for the whole app (main.tsx). Cleared on sign-out (lib/auth.tsx), so the
// next account never sees the last one's data.
export function createQueryClient() {
  return new QueryClient({
    queryCache: new QueryCache({
      // A screen that can't load says so, as every screen did before the cache.
      onError: (err, query) => {
        if (query.meta?.silent) return
        // 423: the workspace is being deleted; the banner in App.tsx says so once.
        if (err instanceof ApiError && err.status === 423) return
        toast.error(err instanceof Error ? err.message : "Couldn't load")
      },
    }),
    defaultOptions: {
      queries: {
        // The server said no (4xx, a rate limit): asking again won't change that.
        // Can't reach it, or a 5xx: try twice more.
        retry: (failures, err) =>
          failures < 2 && !(err instanceof ApiError && err.status < 500),
        // Screens refresh when opened and after a change, not on every tab focus.
        refetchOnWindowFocus: false,
      },
    },
  })
}

export const queryClient = createQueryClient()
