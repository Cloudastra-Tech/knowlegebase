import { defineConfig, mergeConfig } from "vitest/config"

import viteConfig from "./vite.config.ts"

// `npm test`: the components in a simulated browser (jsdom), no server needed.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
    },
  }),
)
