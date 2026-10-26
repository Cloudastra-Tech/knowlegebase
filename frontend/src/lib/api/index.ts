// The backend client, split by domain. Import from "@/lib/api": `api` and `signIn`
// keep the shape they had when this was one file.

import { authApi } from "@/lib/api/auth"
import { chatApi } from "@/lib/api/chat"
import { docsApi } from "@/lib/api/docs"
import { integrationsApi } from "@/lib/api/integrations"
import { membersApi } from "@/lib/api/members"
import { signInSettingsApi } from "@/lib/api/sign-in-settings"

export * from "@/lib/api/types"
export {
  ApiError,
  session,
  setForbiddenHandler,
  setUnauthorizedHandler,
} from "@/lib/api/client"
export { signupStartUrl, ssoStartUrl } from "@/lib/api/auth"

export const api = { ...authApi, ...chatApi, ...docsApi, ...integrationsApi }

// Sign-in settings and members (admin only).
export const signIn = { ...signInSettingsApi, ...membersApi }
