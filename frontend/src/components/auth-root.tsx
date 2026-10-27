import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Outlet, useLocation } from "react-router"

import { Skeleton } from "@/components/ui/skeleton"
import {
  api,
  ApiError,
  session,
  setForbiddenHandler,
  setUnauthorizedHandler,
  type Role,
  type User,
} from "@/lib/api"
import { AuthContext, type AuthValue } from "@/lib/auth-context"

// How often to retry restoring a session while the server can't be reached (~1 min).
const RESTORE_ATTEMPTS = 10

// Who is signed in, for every route below it (routes.tsx): restores the session
// after a page refresh, and notices when the server ends it or changes the role.
export function AuthRoot() {
  const location = useLocation()
  const queryClient = useQueryClient()
  const [user, setUser] = useState<User | null>(null)
  // /sso swaps its one-time code for a session itself (routes.tsx).
  const [checking, setChecking] = useState(() => !!session.token && location.pathname !== "/sso")
  const [role, setRoleState] = useState<Role>(() => {
    // Back from a connector's consent screen: the result is on an HR screen.
    const params = new URLSearchParams(location.search)
    if (location.pathname === "/integrations" && (params.has("connected") || params.has("error")))
      session.setRole("hr")
    return session.role
  })
  const [leftOnPurpose, setLeftOnPurpose] = useState(false)
  const current = useRef<User | null>(null)
  useEffect(() => {
    current.current = user
  }, [user])

  const setRole = useCallback((next: Role) => {
    session.setRole(next)
    setRoleState(next)
  }, [])

  const signedIn = useCallback(
    (u: User, token?: string) => {
      if (token) session.setToken(token)
      // Another account (back from SSO while signed in): drop the last one's data.
      if (current.current && current.current.username !== u.username) queryClient.clear()
      setLeftOnPurpose(false)
      setUser(u)
    },
    [queryClient],
  )

  const signedOut = useCallback(
    (onPurpose: boolean) => {
      session.setToken(null)
      queryClient.clear() // the next account never sees this one's data
      setLeftOnPurpose(onPurpose)
      setUser(null)
    },
    [queryClient],
  )

  // An admin can change someone's role while they're signed in. Re-read the
  // account when the server refuses something, and when the tab comes back.
  // (The routes then send them away from screens they may no longer see.)
  useEffect(() => {
    let inFlight = false
    const recheck = () => {
      const token = session.token
      if (!token || inFlight) return               // one check at a time
      inFlight = true
      api
        .me()
        .then((u) => {
          if (session.token !== token) return       // signed out meanwhile
          setUser(u)
        })
        .catch((err) => {
          // Access removed altogether. (/auth/* 401s skip the unauthorized handler.)
          if (err instanceof ApiError && err.status === 401 && session.token === token) {
            signedOut(false)
          }
        })
        .finally(() => {
          inFlight = false
        })
    }
    setForbiddenHandler(recheck)
    const onVisible = () => document.visibilityState === "visible" && recheck()
    document.addEventListener("visibilitychange", onVisible)
    return () => document.removeEventListener("visibilitychange", onVisible)
  }, [signedOut])

  // Restore the session after a page refresh.
  useEffect(() => {
    setUnauthorizedHandler(() => signedOut(false))
    if (!session.token || !checking) return
    // Only the server saying "no" (401) ends the session. If it can't be reached
    // (restarting, a deploy, a network blip), keep it and try again for a while.
    let timer: number | undefined
    const restore = (attempt: number) => {
      api
        .me()
        .then((u) => {
          setUser(u)
          setChecking(false)
        })
        .catch((err) => {
          if (err instanceof ApiError && err.status === 401) {
            session.setToken(null)
            setChecking(false)
          } else if (attempt < RESTORE_ATTEMPTS) {
            timer = window.setTimeout(() => restore(attempt + 1), Math.min(500 * 2 ** attempt, 8000))
          } else {
            setChecking(false)      // still signed in; the next reload tries again
          }
        })
    }
    restore(0)
    return () => window.clearTimeout(timer)
  }, [checking, signedOut]) // `checking` only ever goes from true to false: once, on load

  const value = useMemo<AuthValue>(
    () => ({ user, role, setRole, signedIn, signedOut, leftOnPurpose }),
    [user, role, setRole, signedIn, signedOut, leftOnPurpose],
  )

  if (checking) {
    return (
      <div className="flex min-h-svh items-center justify-center">
        <Skeleton className="h-10 w-48" />
      </div>
    )
  }

  return (
    <AuthContext.Provider value={value}>
      <Outlet />
    </AuthContext.Provider>
  )
}
