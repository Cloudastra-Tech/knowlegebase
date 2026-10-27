// The theme before the first paint (index.html): next-themes (main.tsx) sets the same
// once React runs, from the same localStorage key, but a page that waited for it
// would flash. Dark unless the person chose light, or System on a light OS.
try {
  var theme = localStorage.getItem("theme") || "dark"
  if (theme === "system") theme = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
  if (theme !== "light") theme = "dark"
  var root = document.documentElement
  root.classList.remove("light", "dark")
  root.classList.add(theme)
  root.style.colorScheme = theme
} catch {
  // No storage (private mode, blocked): the default, dark, is already on <html>.
}
