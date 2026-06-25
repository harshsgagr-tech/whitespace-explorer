import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// host:true binds IPv4 (0.0.0.0) so http://localhost:5173 and http://127.0.0.1:5173
// both work. The proxy target is pinned to 127.0.0.1 so /api always reaches the
// FastAPI shim on IPv4 (uvicorn binds 127.0.0.1 by default).
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8010",
    },
  },
});
