// Before it had a router the site routed by hash, and the server still sends people
// back to those addresses (api.py):
//   /#login?error=...         /#signup?error=...      a failed sign-in or sign-up
//   /#sso?code=...&welcome=1  back from Google, Microsoft or a workspace's provider
//   /#sso-test                the window of an admin's "Test sign-in"
//   /#join-requested          a sign-in that asked to join a workspace (no session)
//   /#claim?error=...         a failed claim of a workspace (kb/services/joining.py)
//   /#integrations?connected=google_drive&choose=1, ?error=...   back from a connector
// legacyRedirect() says where each one lives now; routes.tsx redirects there.

// The one-time code from /#sso, carried in the navigation's state rather than the
// URL, so it doesn't end up in the address bar or the history.
export interface SsoReturn {
  code: string
  welcome: boolean // a new workspace was just made: open its Access screen
}

export interface LegacyRedirect {
  to: string
  state?: SsoReturn
}

export function legacyRedirect(pathname: string, hash: string): LegacyRedirect | null {
  if (pathname !== "/") return null
  const match = /^#([a-z-]+)(?:\?(.*))?$/.exec(hash)
  if (!match) return null
  const [, name, query = ""] = match
  const search = query ? `?${query}` : ""
  switch (name) {
    case "login":
    case "signup":
      return { to: `/${name}${search}` }
    case "sso-test":
      return { to: "/sso-test" }
    case "join-requested":
      return { to: "/join-requested" }
    case "claim":
      return { to: `/claim${search}` }
    case "sso": {
      const params = new URLSearchParams(query)
      return {
        to: "/sso",
        state: { code: params.get("code") ?? "", welcome: params.get("welcome") === "1" },
      }
    }
    case "integrations":
      // Plain #integrations is the landing page's own section, not a redirect.
      return query ? { to: `/integrations${search}` } : null
    default:
      return null // #how, #features, ...: the landing page's sections
  }
}
