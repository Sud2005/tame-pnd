import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // Proxy /api calls to FastAPI during development
      // Direct API calls use http://localhost:8000 in App.jsx
    },
  },
});
