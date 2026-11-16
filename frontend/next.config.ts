import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false, // hide the floating dev badge so it never covers a chart
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
