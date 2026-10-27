import { queryOptions } from "@tanstack/react-query"

import {
  api,
  apiKeys,
  assistant,
  billing,
  digests,
  drafts,
  emailIn,
  extension,
  groups,
  insights,
  onboarding,
  session,
  signIn,
  slack,
  spaces,
  teams,
  webhooks,
  workspace,
} from "@/lib/api"

// Every server read the screens share, cached by TanStack Query (lib/query-client.ts).
// After a change, invalidate the key and the screens showing it load it again.
export const queries = {
  // Polled by IndexStatusProvider (lib/index-status.tsx).
  indexStatus: () =>
    queryOptions({ queryKey: ["index-status"], queryFn: api.indexStatus, meta: { silent: true } }),
  // What's searchable depends on the role the app is viewed as (the X-Role header).
  knowledge: () => queryOptions({ queryKey: ["knowledge", session.role], queryFn: api.knowledge }),
  docs: () => queryOptions({ queryKey: ["docs"], queryFn: api.listDocs }),
  // The first run's suggestions and the checklist depend on the role viewed as.
  onboarding: () =>
    queryOptions({ queryKey: ["onboarding", session.role], queryFn: onboarding.get }),
  // Plan & billing (BILLING=on): { on: false } with it off.
  billing: () => queryOptions({ queryKey: ["billing"], queryFn: billing.get }),
  // Drafts the assistant wrote (HR).
  drafts: () => queryOptions({ queryKey: ["drafts"], queryFn: drafts.list }),
  draft: (id: string) => queryOptions({ queryKey: ["drafts", id], queryFn: () => drafts.get(id) }),
  // My digests, and what I may follow.
  digests: () => queryOptions({ queryKey: ["digests"], queryFn: digests.list }),
  // The workspace's webhooks (admin), and one's deliveries.
  webhooks: () => queryOptions({ queryKey: ["webhooks"], queryFn: webhooks.list }),
  webhookDeliveries: (id: string) =>
    queryOptions({
      queryKey: ["webhooks", id, "deliveries"],
      queryFn: () => webhooks.deliveries(id),
    }),
  // The signed-in person's saved chats. An answer's visibility depends on the role
  // the app is viewed as, so it's part of the key.
  conversations: () =>
    queryOptions({
      queryKey: ["conversations", session.role],
      queryFn: () => api.conversations(),
    }),
  // Searching them: an answer the role can't read now never matches.
  conversationSearch: (q: string) =>
    queryOptions({
      queryKey: ["conversations", session.role, "search", q],
      queryFn: () => api.searchConversations(q),
      enabled: q.trim().length > 0,
      meta: { silent: true },
    }),
  // How long chats are kept, and whether they may be shared (everyone sees it).
  chatSettings: () => queryOptions({ queryKey: ["chat-settings"], queryFn: api.chatSettings }),
  // The links made to one chat that still work.
  shares: (conversationId: string) =>
    queryOptions({
      queryKey: ["shares", conversationId],
      queryFn: () => api.shares(conversationId),
    }),
  // A share link as the viewer may see it; a dead link is shown on the page itself.
  sharedChat: (token: string) =>
    queryOptions({
      queryKey: ["shared", session.role, token],
      queryFn: () => api.openShare(token),
      retry: false,
      meta: { silent: true },
    }),
  conversation: (id: string) =>
    queryOptions({
      queryKey: ["conversations", session.role, id],
      queryFn: () => api.conversation(id),
      retry: false,
    }),
  // A cited passage for the source viewer. Who may read it depends on the role; a
  // 404 (gone from the index, or not for this role) falls back to the saved snippet.
  passage: (chunkId: string) =>
    queryOptions({
      queryKey: ["passage", session.role, chunkId],
      queryFn: () => api.passage(chunkId),
      retry: false,
      meta: { silent: true },
    }),
  integrations: () => queryOptions({ queryKey: ["integrations"], queryFn: api.integrations }),
  myIntegrations: () =>
    queryOptions({ queryKey: ["my-integrations"], queryFn: api.myIntegrations }),
  // One connection's sync history and skipped items (the history sheet).
  integrationRuns: (provider: string) =>
    queryOptions({
      queryKey: ["integrations", provider, "runs"],
      queryFn: () => api.integrationRuns(provider),
    }),
  members: () => queryOptions({ queryKey: ["members"], queryFn: signIn.members }),
  // The workspace's lifecycle, exports, support access and audit retention (admin).
  workspace: () => queryOptions({ queryKey: ["workspace"], queryFn: workspace.settings }),
  invites: () => queryOptions({ queryKey: ["invites"], queryFn: signIn.invites }),
  // Requests to join waiting for an admin (the 'request' joining policy).
  joinRequests: () => queryOptions({ queryKey: ["join-requests"], queryFn: signIn.joinRequests }),
  // Access groups and their members (admins), and who a document can be shared with.
  groups: () => queryOptions({ queryKey: ["groups"], queryFn: groups.groups }),
  principals: () => queryOptions({ queryKey: ["principals"], queryFn: groups.principals }),
  // How questions are logged here (the chat's disclosure, the Insights settings).
  logSettings: () =>
    queryOptions({ queryKey: ["log-settings"], queryFn: insights.logSettings, meta: { silent: true } }),
  // HR sees only what its role may: part of the key.
  insights: (from?: string, to?: string) =>
    queryOptions({
      queryKey: ["insights", session.role, from ?? "", to ?? ""],
      queryFn: () => insights.insights(from, to),
    }),
  ratedAnswers: () =>
    queryOptions({ queryKey: ["rated-answers", session.role], queryFn: () => insights.ratedAnswers(-1) }),
  evalCases: () => queryOptions({ queryKey: ["eval-cases"], queryFn: insights.evalCases }),
  signInSettings: () => queryOptions({ queryKey: ["sign-in-settings"], queryFn: signIn.get }),
  scimSettings: () => queryOptions({ queryKey: ["scim-settings"], queryFn: signIn.scim }),
  // API keys: the signed-in person's own, and (admin) the workspace's API and MCP settings.
  myApiKeys: () => queryOptions({ queryKey: ["api-keys", "mine"], queryFn: apiKeys.myApiKeys }),
  adminApi: () => queryOptions({ queryKey: ["api-keys", "admin"], queryFn: apiKeys.adminApi }),
  // The assistant's name and whether members see the steps panel (everyone).
  assistantInfo: () =>
    queryOptions({ queryKey: ["assistant", "info"], queryFn: assistant.info, meta: { silent: true } }),
  // Its settings and glossary (admins), and the verified answers (HR and admins).
  assistantSettings: () =>
    queryOptions({ queryKey: ["assistant", "settings"], queryFn: assistant.settings }),
  modelProvider: () =>
    queryOptions({ queryKey: ["assistant", "models"], queryFn: assistant.modelProvider }),
  verifiedAnswers: () =>
    queryOptions({ queryKey: ["verified-answers"], queryFn: assistant.verifiedAnswers }),
  slackApp: () => queryOptions({ queryKey: ["slack-app"], queryFn: slack.get }),
  teamsApp: () => queryOptions({ queryKey: ["teams-app"], queryFn: teams.get }),
  emailIn: () => queryOptions({ queryKey: ["email-in"], queryFn: emailIn.get }),
  extension: () => queryOptions({ queryKey: ["extension"], queryFn: extension.get }),
  // Your own space and the team spaces you're in; one of them; what a space can
  // search (depends on what the app is viewed as).
  spaces: () => queryOptions({ queryKey: ["spaces"], queryFn: spaces.spaces }),
  space: (id: string) =>
    queryOptions({ queryKey: ["spaces", session.role, id], queryFn: () => spaces.space(id) }),
  sourceOptions: () =>
    queryOptions({ queryKey: ["source-options", session.role], queryFn: spaces.sourceOptions }),
  // Files attached to a chat, and what may be attached.
  attachments: (conversationId: string) =>
    queryOptions({
      queryKey: ["attachments", conversationId],
      queryFn: () => spaces.attachments(conversationId),
      meta: { silent: true },
    }),
  attachmentLimits: () =>
    queryOptions({ queryKey: ["attachment-limits"], queryFn: spaces.attachmentLimits, staleTime: Infinity }),
  // Saved prompts: the workspace's and your own.
  prompts: () => queryOptions({ queryKey: ["prompts", session.role], queryFn: spaces.prompts }),
  // The sign-in page's options; a failure shows on the page itself, not as a toast.
  authOptions: () =>
    queryOptions({
      queryKey: ["auth-options"],
      queryFn: api.authOptions,
      retry: false,
      meta: { silent: true },
    }),
}
