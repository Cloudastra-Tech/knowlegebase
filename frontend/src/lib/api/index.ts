// The backend client, split by domain. Import from "@/lib/api": `api` and `signIn`
// keep the shape they had when this was one file.

import { apiKeysApi } from "@/lib/api/api-keys"
import { assistantApi } from "@/lib/api/assistant"
import { auditApi } from "@/lib/api/audit"
import { authApi } from "@/lib/api/auth"
import { chatApi } from "@/lib/api/chat"
import { docsApi } from "@/lib/api/docs"
import { insightsApi } from "@/lib/api/insights"
import { integrationsApi } from "@/lib/api/integrations"
import { membersApi } from "@/lib/api/members"
import { signInSettingsApi } from "@/lib/api/sign-in-settings"
import { slackApi } from "@/lib/api/slack"
import { workspaceApi } from "@/lib/api/workspace"

export * from "@/lib/api/types"
export type * from "@/lib/api/insights"
export type * from "@/lib/api/api-keys"
export type * from "@/lib/api/assistant"
export type * from "@/lib/api/slack"
export type * from "@/lib/api/workspace"
export type * from "@/lib/api/operator"
export {
  ApiError,
  session,
  setForbiddenHandler,
  setUnauthorizedHandler,
} from "@/lib/api/client"
export { signupStartUrl, ssoStartUrl } from "@/lib/api/auth"

export const api = { ...authApi, ...chatApi, ...docsApi, ...integrationsApi }

// Sign-in settings, members, invites and the audit log (admin only).
export const signIn = { ...signInSettingsApi, ...membersApi, ...auditApi }

// Insights, the question log's settings, answer feedback and the test set.
export const insights = insightsApi

// API keys for the public API and the MCP server: one's own, and the workspace's (admin).
export const apiKeys = apiKeysApi

// The assistant's content controls: verified answers, source rules, settings, glossary.
export const assistant = assistantApi
// The Slack app: install, its settings (admin), and what HR may see of it.
export const slack = slackApi

// The workspace itself: export, delete, support access, audit retention; and data
// requests about one member (admin).
export const workspace = workspaceApi
export { saveFile } from "@/lib/api/workspace"
