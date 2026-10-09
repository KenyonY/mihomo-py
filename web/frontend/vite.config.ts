import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [tailwindcss()],
  build: { outDir: "../src/mihomo_py_web/portal", emptyOutDir: true },
});
