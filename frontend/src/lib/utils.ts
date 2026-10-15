export { cn } from "cn"

// The site routes by hash, so redirects carry their parameters after it:
// "#login?error=cancelled" -> URLSearchParams { error: "cancelled" }.
export function hashParams() {
  return new URLSearchParams(window.location.hash.split("?")[1] ?? "")
}

// "5 min ago" for a Unix time in seconds; "never" for none.
export function timeAgo(epoch?: number | null) {
  if (!epoch) return "never"
  const s = Math.max(0, Date.now() / 1000 - epoch)
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}
