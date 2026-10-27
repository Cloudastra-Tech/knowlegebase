import { cn } from "cn"
import { useTheme } from "next-themes"
import { Toaster as Sonner, type ToasterProps } from "sonner"
import { Loader2Icon } from "lucide-react"

// A status glyph in mono, coloured with the status tokens; the toast's text stays the
// foreground. aria-hidden: the message says it in words.
function Glyph({ char, className }: { char: string; className: string }) {
  return (
    <span aria-hidden className={cn("font-mono text-sm leading-none font-semibold", className)}>
      {char}
    </span>
  )
}

// Every status shares the one glass surface. The rich-colour variables are set too, so
// a `richColors` toaster still gets the tokens rather than Sonner's own palette.
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
        success: <Glyph char="✓" className="text-success" />,
        info: <Glyph char="i" className="text-muted-foreground" />,
        warning: <Glyph char="!" className="text-warning" />,
        error: <Glyph char="×" className="text-destructive" />,
        loading: (
          <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
        ),
      }}
      style={
        {
          "--normal-bg": surface.bg,
          "--normal-bg-hover": surface.bg,
          "--normal-text": surface.text,
          "--normal-border": surface.border,
          "--normal-border-hover": surface.border,
          ...Object.fromEntries(
            ["success", "info", "warning", "error"].flatMap((kind) => [
              [`--${kind}-bg`, surface.bg],
              [`--${kind}-border`, surface.border],
              [`--${kind}-text`, surface.text],
            ])
          ),
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
