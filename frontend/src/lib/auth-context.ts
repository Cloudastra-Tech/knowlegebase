import { createContext, useContext } from "react"

import type { Role, User } from "@/lib/api"

// Shared by AuthRoot (components/auth-root.tsx) and the screens that read it. Kept
// apart from the provider so Fast Refresh can hot-reload that component file.
export interface AuthValue {
  user: User | null
  // Signed in: with a new token, or (password sign-in) one already stored.
  signedIn: (user: User, token?: string) => void
  // Signed out. onPurpose: they chose to, so they land on the landing page rather
  // than the sign-in page.
  signedOut: (onPurpose: boolean) => void
  leftOnPurpose: boolean
}

export const AuthContext = createContext<AuthValue | null>(null)

export function useAuth() {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error("useAuth() outside AuthRoot")
  return auth
}

// What the signed-in account may read: "hr" for an HR or admin account, else "public".
// Only an admin changes it (Members, SCIM, the sign-in group rules).
export function useRole(): Role {
  const { user } = useAuth()
  return user && user.role !== "public" ? "hr" : "public"
}

// Inside the signed-in app (App.tsx), where there always is a user.
export function useUser() {
  const { user } = useAuth()
  if (!user) throw new Error("useUser() while signed out")
  return user
}
