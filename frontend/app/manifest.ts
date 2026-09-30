import type { MetadataRoute } from "next";

// Lets Chrome/Edge/Safari install the dashboard as an app (own window, Dock icon).
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "AutoApply Agent",
    short_name: "AutoApply",
    description: "Tailored resumes, applications and outreach, run by your agent",
    start_url: "/jobs",
    scope: "/",
    display: "standalone",
    background_color: "#fafafa",
    theme_color: "#4f46e5",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
