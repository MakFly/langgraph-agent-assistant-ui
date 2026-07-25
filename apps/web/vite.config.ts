import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:4310";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    // Ports choisis hors des défauts habituels (5173 / 3000 / 3001) pour ne pas
    // entrer en collision avec les autres projets de la machine.
    port: 4311,
    strictPort: true,
    // 0.0.0.0 : sans ça, Vite n'écoute que sur la loopback du conteneur et le
    // port publié ne répond pas.
    host: true,
    // Le front appelle /api/chat en same-origin : pas de CORS à gérer en dev.
    proxy: { "/api": { target: API_TARGET, changeOrigin: true } },
  },
});
