import { queryOptions } from "@tanstack/react-query"

import { api, session, signIn } from "@/lib/api"

// Every server read the screens share, cached by TanStack Query (lib/query-client.ts).
// After a change, invalidate the key and the screens showing it load it again.
export const queries = {
  // Polled by IndexStatusProvider (lib/index-status.tsx).
  indexStatus: () =>
    queryOptions({ queryKey: ["index-status"], queryFn: api.indexStatus, meta: { silent: true } }),
  // What's searchable depends on the role the app is viewed as (the X-Role header).
  knowledge: () => queryOptions({ queryKey: ["knowledge", session.role], queryFn: api.knowledge }),
  docs: () => queryOptions({ queryKey: ["docs"], queryFn: api.listDocs }),
  integrations: () => queryOptions({ queryKey: ["integrations"], queryFn: api.integrations }),
  members: () => queryOptions({ queryKey: ["members"], queryFn: signIn.members }),
  signInSettings: () => queryOptions({ queryKey: ["sign-in-settings"], queryFn: signIn.get }),
  // The sign-in page's options; a failure shows on the page itself, not as a toast.
  authOptions: () =>
    queryOptions({
      queryKey: ["auth-options"],
      queryFn: api.authOptions,
      retry: false,
      meta: { silent: true },
    }),
}
