import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import type { Plugin } from "vite"
import { defineConfig } from "vitest/config"

// The browser extension (../extension, platform.md PL-24), built with this app's
// toolchain: `npm run build:extension` -> extension/dist (load it unpacked), and
// `npm run test:extension` runs its unit tests. It has no node_modules of its own:
// its imports resolve from here, and "@" is this app's src, so the side panel uses
// the same shadcn components.
const EXTENSION = path.resolve(import.meta.dirname, "../extension")
const SRC = path.resolve(import.meta.dirname, "src")

// A bare import ("react", "radix-ui", ...) in an extension file resolves as if it
// were imported from this app's src, i.e. from frontend/node_modules.
function resolveFromFrontend(): Plugin {
  const importer = path.join(SRC, "main.tsx")
  return {
    name: "kb-extension-resolve",
    enforce: "pre",
    async resolveId(source, from, options) {
      if (!from || !from.startsWith(EXTENSION) || !/^[a-z@]/i.test(source)) return null
      if (source.startsWith("@/")) return null
      return this.resolve(source, importer, { ...options, skipSelf: true })
    },
  }
}

export default defineConfig({
  root: EXTENSION,
  base: "./",
  publicDir: path.join(EXTENSION, "public"), // manifest.json and the icons, copied as they are
  plugins: [resolveFromFrontend(), react(), tailwindcss()],
  resolve: {
    alias: { "@": SRC },
    dedupe: ["react", "react-dom"],
  },
  build: {
    outDir: path.join(EXTENSION, "dist"),
    emptyOutDir: true,
    // Extension pages may not load remote code (MV3); everything ships in the folder.
    modulePreload: false,
    rollupOptions: {
      input: {
        sidepanel: path.join(EXTENSION, "sidepanel.html"),
        background: path.join(EXTENSION, "src/background.ts"),
      },
      output: {
        // The manifest names the service worker: it keeps a fixed name.
        entryFileNames: (chunk) =>
          chunk.name === "background" ? "background.js" : "assets/[name]-[hash].js",
      },
    },
  },
  test: {
    root: EXTENSION,
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
})
