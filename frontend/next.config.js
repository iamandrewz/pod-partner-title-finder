/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: {
    unoptimized: true,
  },
  // Rewrite API calls to standalone backend on port 5003
  async rewrites() {
    return [
      {
        source: '/api/title-finder/:path*',
        destination: 'http://localhost:5003/api/title-finder/:path*',
      },
      {
        source: '/api/v3/:path*',
        destination: 'http://localhost:5003/api/v3/:path*',
      },
      {
        source: '/api/auth/:path*',
        destination: 'http://localhost:5003/api/auth/:path*',
      },
      {
        source: '/api/users',
        destination: 'http://localhost:5003/api/users',
      },
      {
        source: '/api/health',
        destination: 'http://localhost:5003/api/health',
      },
    ];
  },
};

module.exports = nextConfig;
