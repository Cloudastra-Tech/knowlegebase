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
    proxy: { "/api": { target: "http://127.0.0.1:8010", changeOrigin: false } },
  },
})
