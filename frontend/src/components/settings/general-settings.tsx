import { useState } from "react"
import { Monitor, Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"

import { SettingsGroup, SettingsRow, SettingsSection } from "@/components/settings/settings-section"
import { Switch } from "@/components/ui/switch"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useUser } from "@/lib/auth-context"
import { getEffects, setEffects } from "@/lib/ascii-loop"
import { ROLE_LABEL } from "@/lib/roles"

const PROVIDERS: Record<string, string> = { google: "Google", microsoft: "Microsoft", password: "Password" }

// Settings › General: who you are here (read-only: names and roles come from sign-in
// and the admins), and how the app looks on this device.
export function GeneralSettings() {
  const user = useUser()
  const email = user.username.includes("@") ? user.username : `@${user.username}`
  return (
    <SettingsSection title="General" description="Your account, and how the app looks on this device.">
      <SettingsGroup title="Profile">
        <SettingsRow label="Name">
          <span className="truncate text-sm text-muted-foreground">{user.name}</span>
        </SettingsRow>
        <SettingsRow label="Email">
          <span className="truncate text-sm text-muted-foreground">{email}</span>
        </SettingsRow>
        <SettingsRow label="Workspace">
          <span className="truncate text-sm text-muted-foreground">{user.workspace}</span>
        </SettingsRow>
        <SettingsRow label="Role" description="Set by your workspace's admins.">
          <span className="text-sm text-muted-foreground">{ROLE_LABEL[user.role]}</span>
        </SettingsRow>
        {user.provider && (
          <SettingsRow label="Signs in with">
            <span className="text-sm text-muted-foreground">{PROVIDERS[user.provider] ?? user.provider}</span>
          </SettingsRow>
        )}
      </SettingsGroup>
      <Appearance />
    </SettingsSection>
  )
}

// The theme (Dark, the default; Light; System) and "Reduce effects", the same choices
// as the account menu's Appearance (theme-menu.tsx), kept on this device.
function Appearance() {
  const { theme = "dark", setTheme } = useTheme()
  const [reduced, setReduced] = useState(() => getEffects() === "reduced")
  return (
    <SettingsGroup title="Appearance">
      <SettingsRow label="Theme" description="Dark is the default.">
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={theme}
          onValueChange={(v) => v && setTheme(v)}
          aria-label="Theme"
        >
          <ToggleGroupItem value="dark">
            <Moon data-icon="inline-start" /> Dark
          </ToggleGroupItem>
          <ToggleGroupItem value="light">
            <Sun data-icon="inline-start" /> Light
          </ToggleGroupItem>
          <ToggleGroupItem value="system">
            <Monitor data-icon="inline-start" /> System
          </ToggleGroupItem>
        </ToggleGroup>
      </SettingsRow>
      <SettingsRow
        label="Reduce effects"
        htmlFor="reduce-effects"
        description="Holds the background still and makes the frosted glass solid."
      >
        <Switch
          id="reduce-effects"
          checked={reduced}
          onCheckedChange={(on) => {
            setReduced(on)
            setEffects(on ? "reduced" : "full")
          }}
        />
      </SettingsRow>
    </SettingsGroup>
  )
}
