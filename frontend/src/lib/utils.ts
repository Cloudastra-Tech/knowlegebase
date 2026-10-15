export { cn } from "cn"

// The site routes by hash, so redirects carry their parameters after it:
// "#login?error=cancelled" -> URLSearchParams { error: "cancelled" }.
export function hashParams() {
  return new URLSearchParams(window.location.hash.split("?")[1] ?? "")
}
