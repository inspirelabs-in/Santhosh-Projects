/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  typedRoutes: false,
  devIndicators: false,
  // The local Docker image runs the Next standalone server (the frontend
  // Dockerfile copies .next/standalone + server.js). Standalone output is
  // gated to Docker builds only: the Dockerfile sets DOCKER_BUILD=true, while
  // Vercel leaves it unset — Vercel must NOT use standalone output (it 404s
  // there, which is why it was removed from the config).
  output: process.env.DOCKER_BUILD === "true" ? "standalone" : undefined,
};

export default nextConfig;
