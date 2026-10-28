import { createContext, useContext } from "react"

import type { Role, User } from "@/lib/api"

// Shared by AuthRoot (components/auth-root.tsx) and the screens that read it. Kept
// apart from the provider so Fast Refresh can hot-reload that component file.
export interface AuthValue {
  user: User | null
  // The role the app is viewed as. Only an HR or admin account can view it as "hr".
  role: Role
  setRole: (role: Role) => void
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

// What the app is viewed as: an HR or admin account picks (the navbar's "View as");
// everyone else is public.
export function useActiveRole(): Role {
  const { user, role } = useAuth()
  return user && user.role !== "public" ? role : "public"
}

// Inside the signed-in app (App.tsx), where there always is a user.
export function useUser() {
  const { user } = useAuth()
  if (!user) throw new Error("useUser() while signed out")
  return user
}
