import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Dev only. In production nginx serves the UI and proxies /convai, so the
    // browser sees one origin and CORS never applies - which is why the API
    // needs no CORSMiddleware and no change at all.
    proxy: {
      "/convai": {
        target: process.env.VITE_API_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: { outDir: "dist" },
});
