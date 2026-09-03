/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Fuente única de la versión del frontend: `package.json`. Antes había TRES
  // valores vivos a la vez — "V2.1.0_PROD" a pelo en la consola derecha,
  // "v3.0.0" dentro de los mensajes i18n (donde no pinta nada: era idéntico en
  // los dos idiomas, o sea un dato, no una traducción) y "1.0.0" en la API.
  env: {
    NEXT_PUBLIC_APP_VERSION: require("./package.json").version,
  },
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'image.tmdb.org',
        pathname: '/t/p/**',
      },
    ],
  },
  // API Proxy
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: process.env.BACKEND_URL || 'http://backend:8000/api/:path*',
      },
    ];
  },

  // Security headers
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=31536000; includeSubDomains; preload',
          },
          {
            key: 'Permissions-Policy',
            value:
              'camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()',
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
