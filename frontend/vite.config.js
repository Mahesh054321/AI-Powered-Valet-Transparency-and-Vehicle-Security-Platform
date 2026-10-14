import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Vite configuration.
 *
 * Two decisions matter for this project:
 *
 *  1. `server.host: '0.0.0.0'` - so the dev server is reachable from other
 *     machines (phones on the same Wi-Fi during a demo, or a sandboxed
 *     preview host).
 *  2. `server.proxy` - browser requests to `/api` are forwarded to Flask. The
 *     SPA therefore never needs to know the backend's origin, which means no
 *     CORS surprises in development and no backend URL baked into the bundle.
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendTarget = env.VITE_PROXY_TARGET || 'http://127.0.0.1:5000'

  return {
    plugins: [react()],
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.js'],
      css: false,
      restoreMocks: true,
      // A single forked worker: jsdom is memory-hungry, and one worker per
      // test file exhausts a small machine. (Vitest 4 moved pool options to the
      // top level; `poolOptions` was removed.)
      pool: 'forks',
      singleFork: true,
      include: ['src/**/*.test.{js,jsx}'],
    },
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    envPrefix: 'VITE_',
    server: {
      host: '0.0.0.0',
      port: Number(env.VITE_PORT || 5173),
      strictPort: false,
      allowedHosts: true,
      proxy: {
        '/api': {
          target: backendTarget,
          changeOrigin: true,
          secure: false,
        },
        '/media': {
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },
    preview: {
      host: '0.0.0.0',
      port: 4173,
      allowedHosts: true,
    },
    build: {
      outDir: 'dist',
      sourcemap: mode !== 'production',
      chunkSizeWarningLimit: 900,
      rollupOptions: {
        output: {
          /**
           * Split the bundle by concern so the first paint does not pay for the
           * map and chart libraries. Vite 8 bundles with rolldown, which
           * requires `manualChunks` to be a function rather than an object map.
           */
          manualChunks(id) {
            if (!id.includes('node_modules')) return undefined
            if (id.includes('leaflet')) return 'map'
            if (id.includes('recharts') || id.includes('d3-')) return 'charts'
            if (id.includes('@tanstack') || id.includes('axios')) return 'query'
            if (id.includes('react')) return 'react'
            return 'vendor'
          },
        },
      },
    },
  }
})
