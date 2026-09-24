import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { publicCatalogPlugin } from "./build/public-catalog.mjs";

export default defineConfig(({ mode }) => ({
  base: mode === "warehouse" ? "/xx/" : "/",
  publicDir: mode === "warehouse" ? "warehouse-public" : "public",
  plugins: [react(), publicCatalogPlugin(mode !== "warehouse"), {
    name: "public-or-warehouse-entry",
    transformIndexHtml: {
      order: "pre",
      handler(html) {
        return mode === "warehouse" ? html
          .replace("/src/main.tsx", "/src/warehouse-main.tsx")
          .replace("<title>交流备件知识大全</title>", "<title>RSC个人仓</title><link rel=\"manifest\" href=\"/xx/manifest.webmanifest\" />")
          .replace("交流备件知识大全：查询物料编码、备件名称与适用型号", "RSC个人仓与省级背包库存协同系统") : html;
      },
    },
  }],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    sourcemap: false,
    outDir: mode === "warehouse" ? "dist-warehouse" : "dist",
  },
}));
