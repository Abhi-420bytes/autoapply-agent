/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  webpack: (config) => {
    config.resolve.alias.canvas = false; // react-pdf: optional node-canvas dependency
    return config;
  },
};

export default nextConfig;
