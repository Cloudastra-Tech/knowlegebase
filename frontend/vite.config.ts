import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
  server: {
    // The FastAPI backend (api.py) runs on :8010; the browser only talks to Vite.
    // changeOrigin false keeps Host = localhost:5173, so OAuth redirect URIs
    // (e.g. Google Drive) point back at the dev site, not at :8010.
    // KB_API_URL points it at an API on another port (several checkouts side by side).
    // /scim: an identity provider provisioning members (kb/routers/scim.py).
    proxy: {
      "/api": { target: process.env.KB_API_URL || "http://127.0.0.1:8010", changeOrigin: false },
      "/scim": { target: process.env.KB_API_URL || "http://127.0.0.1:8010", changeOrigin: false },
    },
  },
})
