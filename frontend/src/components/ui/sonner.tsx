import { cn } from "@/lib/utils"
import { CircleCheck, CircleX, Info, TriangleAlert, type LucideIcon } from "lucide-react"
import { useTheme } from "next-themes"
import { Toaster as Sonner, type ToasterProps } from "sonner"

import { AsciiSpinner } from "@/components/ascii/ascii-spinner"

// A status icon, coloured with the status tokens; the toast's text stays the
// foreground. aria-hidden: the message says it in words.
function StatusIcon({ icon: Icon, className }: { icon: LucideIcon; className: string }) {
  return <Icon aria-hidden className={cn("size-4", className)} />
}

// Every status shares the one glass surface; the icon carries the status colour. No
// `richColors`: it would only swap in Sonner's own palette.
const surface = {
  bg: "var(--glass-bg-strong)",
  border: "var(--glass-border)",
  text: "var(--popover-foreground)",
}

const Toaster = ({ ...props }: ToasterProps) => {
  // The theme on screen (main.tsx's ThemeProvider); outside one (the extension), the OS's.
  const { forcedTheme, resolvedTheme } = useTheme()
  const theme = forcedTheme ?? resolvedTheme ?? "system"

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      className="toaster group"
      icons={{
        success: <StatusIcon icon={CircleCheck} className="text-success" />,
        info: <StatusIcon icon={Info} className="text-muted-foreground" />,
        warning: <StatusIcon icon={TriangleAlert} className="text-warning" />,
        error: <StatusIcon icon={CircleX} className="text-destructive" />,
        loading: (
          <AsciiSpinner label="" className="text-muted-foreground" />
        ),
      }}
      style={
        {
          "--normal-bg": surface.bg,
          "--normal-bg-hover": surface.bg,
          "--normal-text": surface.text,
          "--normal-border": surface.border,
          "--normal-border-hover": surface.border,
          "--border-radius": "var(--radius)",
        } as React.CSSProperties
      }
      toastOptions={{
        classNames: {
          // The strong glass tier's blur and highlight; Sonner sets the fill, border and
          // position itself (from the variables above).
          toast:
            "cn-toast backdrop-blur-(--glass-blur-md) backdrop-saturate-(--glass-saturate) shadow-[inset_0_1px_0_0_var(--glass-highlight),var(--glass-shadow)]!",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
