import { useState } from "react"
import { toast } from "sonner"

import { SettingsGroup, SettingsRow, SettingsSection } from "@/components/settings/settings-section"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"

// Settings › Security: end every session of this account (a lost laptop, a shared
// computer), this one too, after a confirm.
export function SecuritySettings() {
  const { signedOut } = useAuth()
  const [busy, setBusy] = useState(false)

  async function signOutEverywhere() {
    setBusy(true)
    try {
      await api.logoutEverywhere()
    } catch (err) {
      setBusy(false)
      toast.error(err instanceof Error ? err.message : "Couldn't sign out everywhere. Try again.")
      return
    }
    signedOut(true)
    toast.success("Signed out on every device.")
  }

  return (
    <SettingsSection title="Security" description="Where your account is signed in.">
      <SettingsGroup title="Sessions">
        <SettingsRow
          label="Sign out everywhere"
          description="Ends every session of your account, on every device and browser, this one included."
        >
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button size="sm" variant="outline" loading={busy}>
                Sign out everywhere
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Sign out everywhere?</AlertDialogTitle>
                <AlertDialogDescription>
                  You're signed out on every device and browser where you're signed in, this one
                  included. Use it if you lost a device or signed in on a computer that isn't yours.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={signOutEverywhere}>Sign out everywhere</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </SettingsRow>
      </SettingsGroup>
    </SettingsSection>
  )
}
