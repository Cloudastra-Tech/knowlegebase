import { createCn } from "cn/config"

// Class names, joined and merged (clsx + tailwind-merge, by the `cn` package). It knows
// the two font sizes index.css adds to Tailwind's scale, `text-reading` and
// `text-ascii`: unknown, a `text-*` reads as a colour, and next to a real text colour
// one of them was dropped. Import it from here, never from "cn" itself.
export const cn = createCn({
  extend: { classGroups: { "font-size": [{ text: ["reading", "ascii"] }] } },
})

// "5 min ago" for a Unix time in seconds; "never" for none.
export function timeAgo(epoch?: number | null) {
  if (!epoch) return "never"
  const s = Math.max(0, Date.now() / 1000 - epoch)
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}
