import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The Mini App is a single-page app with hash routing, so no history fallback
// is needed: every screen is addressed by ``#screen=...`` and the bot sends
// users straight to ``<MINI_APP_URL>#screen=tutor``.
export default defineConfig({
  plugins: [
    react(),
    /**
     * Log every request the dev server answers.
     *
     * A tunnel in front of Vite makes "did the request even arrive?" the
     * hardest question to answer: the console belongs to the terminal that
     * started Vite, and neither the tunnel nor Telegram reports back. Without
     * this line there is no way to tell a refused URL (Telegram never asks) from
     * a broken page (Telegram asks and the app fails).
     *
     * Dev-only: `apply: "serve"` means it never exists in a production build.
     */
    {
      name: "request-log",
      apply: "serve" as const,
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          const started = Date.now();
          _res.on("finish", () => {
            const ms = Date.now() - started;
            console.log(
              `  ${req.method} ${req.url} -> ${_res.statusCode} ${ms}ms  ` +
                `(host: ${req.headers.host ?? "-"})`,
            );
          });
          next();
        });
      },
    },
  ],
  server: {
    // Deliberately not 5173: another Vite app already holds that port on this
    // machine, and the usual response to "port in use" is to drift to 5174 —
    // where a tunnel still aimed at 5173 keeps serving the other project.
    port: 5180,
    // Fail loudly rather than drifting to another port. A dev server quietly
    // moving is how a Mini App ends up pointing at a stranger's page.
    strictPort: true,
    // Listen on every interface, not just loopback: a tunnel (ngrok, Cloudflare
    // Tunnel, tailscale) connects from another host, and binding to localhost
    // would refuse the connection outright.
    host: true,
    // Vite rejects any Host header it does not recognise. Behind a tunnel the
    // host is the public hostname, not "localhost", so the default whitelist
    // answers 403 — Telegram renders that as "WebView crashed" because the
    // page never arrives.
    allowedHosts: true,
    proxy: {
      // The FastAPI backend. Locally: ``uvicorn app.api.server:app --reload``
      // (see MINIAPP.md).
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{js,ts,jsx,tsx}"],
    css: false,
    // The multi-step walks (a full 26-step application, a 12-step parent
    // request) click through every field, which is not instant on a loaded
    // machine. The default 5s is not enough headroom for them.
    testTimeout: 20000,
    hookTimeout: 20000,
  },
});
