import { Navigate, type RouteObject } from "react-router"

import App from "@/App"
import { AuthRoot } from "@/components/auth-root"
import { SsoTestDone } from "@/components/sso-test-done"
import {
  AccessScreen,
  ApiKeysScreen,
  DocsScreen,
  HrOnly,
  Home,
  InsightsScreen,
  IntegrationsScreen,
  InvitePage,
  KnowledgeScreen,
  Root,
  SharedScreen,
  SignInPage,
  SsoReturnPage,
} from "@/screens"

// Every screen's URL:
//   /                  the landing page (signed in: /chat)
//   /login, /signup    sign in, or create a workspace (?error=... after a failed one)
//   /sso               back from a sign-in provider (see lib/legacy-hash.ts)
//   /sso-test          the window of an admin's "Test sign-in"
//   /chat, /knowledge  everyone signed in
//   /api-keys          their own API keys (the public API and the MCP server)
//   /chat/:id          one of their saved conversations
//   /s/:token          a chat or answer shared by someone in their workspace
//   /documents, /integrations        HR and admins, viewing the app as HR
//                      (not /docs: that is FastAPI's API reference, api.py)
//   /admin/sign-in, /admin/members, /admin/activity, /admin/api   admins (the Access
//                      screen's tabs)
//   /admin/insights, /admin/insights/feedback   HR and admins, viewing the app as HR
//   /invite/:token     accept an invite (signed in or not)
// The old hash addresses (/#login, /#sso?code=..., ...) redirect here (Root).
export const routes: RouteObject[] = [
  {
    path: "/",
    Component: Root,
    children: [
      // The popup of an admin's "Test sign-in" lands here: no app, just "done".
      { path: "sso-test", Component: SsoTestDone },
      {
        Component: AuthRoot,
        children: [
          { index: true, Component: Home },
          // The leaves render nothing themselves (element: null): the page is the parent.
          {
            Component: SignInPage,
            children: [
              { path: "login", element: null },
              { path: "signup", element: null },
            ],
          },
          { path: "sso", Component: SsoReturnPage },
          { path: "invite/:token", Component: InvitePage },
          {
            Component: App,
            children: [
              // The chat stays mounted in App, so it isn't lost on another screen.
              { path: "chat", element: null },
              { path: "chat/:conversationId", element: null }, // a saved conversation
              { path: "s/:token", Component: SharedScreen },
              { path: "knowledge", Component: KnowledgeScreen },
              { path: "api-keys", Component: ApiKeysScreen }, // everyone's own keys
              {
                Component: HrOnly,
                children: [
                  { path: "documents", Component: DocsScreen },
                  { path: "integrations", Component: IntegrationsScreen },
                ],
              },
              {
                path: "admin/insights",
                Component: HrOnly,
                children: [
                  { index: true, element: <InsightsScreen tab="overview" /> },
                  { path: "feedback", element: <InsightsScreen tab="feedback" /> },
                ],
              },
              {
                path: "admin",
                element: <HrOnly admin />,
                children: [
                  { index: true, element: <Navigate to="sign-in" replace /> },
                  { path: "sign-in", element: <AccessScreen tab="sign-in" /> },
                  { path: "members", element: <AccessScreen tab="members" /> },
                  { path: "activity", element: <AccessScreen tab="activity" /> },
                  { path: "api", element: <AccessScreen tab="api" /> },
                ],
              },
            ],
          },
        ],
      },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]
