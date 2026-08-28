import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

const input = process.env.INPUT ?? "widget/operator-console.html";

export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    outDir: "dist/widget",
    emptyOutDir: false,
    cssMinify: true,
    minify: true,
    sourcemap: false,
    rollupOptions: { input },
  },
});
