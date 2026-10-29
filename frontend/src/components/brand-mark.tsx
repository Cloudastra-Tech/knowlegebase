import { BRANDS } from "@/lib/brands"
import { cn } from "@/lib/utils"

// A platform's mark (Outline, GitHub, Slack...), one colour like the icons beside
// it. Decorative unless given a label.
export function BrandMark({ slug, className, label }: { slug: string; className?: string; label?: string }) {
  const brand = BRANDS[slug]
  if (!brand) return null
  const turns = slug === "slack" ? [0, 90, 180, 270] : [0]
  return (
    <svg
      viewBox="0 0 24 24"
      className={cn("size-5 shrink-0 fill-current", className)}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {turns.map((deg) => (
        <g key={deg} transform={deg ? `rotate(${deg} 12 12)` : undefined}>
          {brand.paths.map((d) => (
            <path key={d} d={d} />
          ))}
        </g>
      ))}
    </svg>
  )
}
