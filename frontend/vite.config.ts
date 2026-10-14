import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    // The FastAPI backend (api.py) runs on :8010; the browser only talks to Vite.
    proxy: { "/api": "http://127.0.0.1:8010" },
  },
})
