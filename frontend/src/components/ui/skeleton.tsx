import { cn } from "cn"

// A shimmer that sweeps across (a translated gradient: transform only, 1.4s linear),
// not a pulse. The sweep is tw-animate's enter slide from -200% on top of a +100%
// translate, so it runs from -100% to +100%. Reduced motion: a still tint.
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn(
        "relative overflow-hidden rounded-md bg-muted after:absolute after:inset-0 after:translate-x-full after:bg-linear-to-r after:from-transparent after:via-foreground/6 after:to-transparent after:animate-in after:duration-1400 after:ease-linear after:repeat-infinite after:slide-in-from-left-[200%] motion-reduce:after:hidden",
        className
      )}
      {...props}
    />
  )
}

export { Skeleton }
