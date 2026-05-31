/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  experimental: { serverActions: { bodySizeLimit: '2mb' } },
};
export default nextConfig;
