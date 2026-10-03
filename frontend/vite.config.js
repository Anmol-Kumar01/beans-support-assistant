import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the UI on :5173 and forwards API calls to the FastAPI server.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/v1": "http://localhost:8001" },
  },
});
