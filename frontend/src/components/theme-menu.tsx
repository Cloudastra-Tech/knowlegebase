import { useState } from "react"
import { Monitor, Moon, Sparkles, Sun } from "lucide-react"
import { useTheme } from "next-themes"

import {
  DropdownMenuCheckboxItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
} from "@/components/ui/dropdown-menu"
import { getEffects, setEffects } from "@/lib/ascii-loop"
import { cn } from "@/lib/utils"

// The account menu's look: the theme (Dark, the default; Light; System) and "Reduce
// effects", which holds the ASCII still and makes the glass opaque, for anyone who wants
// calm without an OS setting (lib/ascii-loop.ts, setEffects; kept for the next visit).
// Items for a DropdownMenuContent, not a menu of its own.
export function ThemeMenu() {
  const { theme = "dark", setTheme } = useTheme()
  const [reduced, setReduced] = useState(() => getEffects() === "reduced")
  return (
    <>
      <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Theme</DropdownMenuLabel>
      <DropdownMenuRadioGroup value={theme} onValueChange={setTheme}>
        <DropdownMenuRadioItem value="dark">
          <Moon /> Dark
        </DropdownMenuRadioItem>
        <DropdownMenuRadioItem value="light">
          <Sun /> Light
        </DropdownMenuRadioItem>
        <DropdownMenuRadioItem value="system">
          <Monitor /> System
        </DropdownMenuRadioItem>
      </DropdownMenuRadioGroup>
      {/* A switch in looks, a menuitemcheckbox to assistive technology: a real switch
          (a button) can't sit inside a menu item. */}
      <DropdownMenuCheckboxItem
        checked={reduced}
        onCheckedChange={(on) => {
          setReduced(on)
          setEffects(on ? "reduced" : "full")
        }}
        // Stays open, so the change can be seen under the menu.
        onSelect={(e) => e.preventDefault()}
        className="pr-1.5 [&>[data-slot=dropdown-menu-checkbox-item-indicator]]:hidden"
      >
        <Sparkles /> Reduce effects
        <span
          aria-hidden
          data-state={reduced ? "checked" : "unchecked"}
          className={cn(
            "ml-auto inline-flex h-3.5 w-6 shrink-0 items-center rounded-full p-px transition-colors duration-(--dur-hover)",
            reduced ? "bg-primary" : "bg-input",
          )}
        >
          <span
            className={cn(
              "block size-3 rounded-full transition-transform duration-(--dur-hover) ease-out-strong",
              reduced ? "translate-x-2.5 bg-primary-foreground" : "bg-foreground",
            )}
          />
        </span>
      </DropdownMenuCheckboxItem>
    </>
  )
}
